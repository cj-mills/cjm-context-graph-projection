"""The post page's comments (design 39c51c15 (1), rulings 98d33f9e (2) + 86f4a34d): a thread is
a discussion fact on its Note, mapped once by the harvest (site_path history, title, or an
authored map; the most comments stands), rendered by number or by the canonical path as a
strict term, and replayed from the journal without asking GitHub."""

import asyncio
import json
from pathlib import Path

import pytest

from cjm_context_graph_layer.ops import extend_graph
from cjm_context_graph_primitives.journal import append_write
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import note_node_id
from cjm_markdown_decompose_core.extract import note_from_text
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from cjm_context_graph_projection import factlayer as F
from cjm_context_graph_projection.comments import (fetch_threads, harvest_discussions, load_comments_config,
                                                   load_threads, map_threads, page_comments, path_key,
                                                   plan_threads, render_comments)
from cjm_context_graph_projection.journal import replay_journal
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.write import assert_value

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()

CONFIG = ("website:\n  title: The Site\n"
          "post-comments:\n  repo: o/r\n  repo-id: R_1\n  category: Comments\n  category-id: C_1\n"
          "  theme:\n    light: light\n    dark: dark_dimmed\n  base-paths: [old-project]\n")


def _t(number, title, comments, kind="issue"):
    return {"number": number, "kind": kind, "title": title, "comments": comments,
            "created": f"2024-01-{number:02d}T00:00:00Z", "last_comment": ""}


def test_the_config_comes_from_the_site_and_refuses_when_missing(tmp_path):
    (tmp_path / "_quarto.yml").write_text(CONFIG)
    got = load_comments_config(str(tmp_path))
    assert not got["errors"] and got["site_title"] == "The Site"
    assert got["config"]["category-id"] == "C_1" and got["config"]["theme"] == {"light": "light", "dark": "dark_dimmed"}
    assert got["config"]["base-paths"] == ["old-project"] and got["config"]["input-position"] == "top"
    (tmp_path / "_quarto.yml").write_text("post-comments:\n  repo: o/r\n  category: Comments\n")
    err = load_comments_config(str(tmp_path))["errors"]
    assert err and err[0]["missing"] == ["repo-id", "category-id"]          # never a guessed id
    assert not load_comments_config(str(tmp_path), required=("repo",))["errors"]   # the harvest needs the repo alone


def test_path_era_titles_name_a_site_path():
    assert path_key("posts/x/") == "/posts/x/"
    assert path_key("posts/x/index") == "/posts/x/" == path_key("posts/x/index.html")   # the index.html split
    assert path_key("/posts/x/") == "/posts/x/"                                         # a giscus term
    assert path_key("old-project/posts/x/", ["old-project"]) == "/posts/x/"            # a former base path
    assert path_key("posts/x/ort-tensorrt-ubuntu") == "/posts/x/ort-tensorrt-ubuntu/"
    assert path_key("about.html") is None and path_key("A Title | The Site") is None    # title era


def test_threads_map_and_the_most_comments_stands():
    paths = {"/posts/a/": ["A"], "/posts/a-old/": ["A"], "/posts/b/": ["B"], "/posts/d/": ["D"]}
    titles = {"Post C": ["C"], "Twin": ["T1", "T2"]}
    threads = [_t(1, "posts/a/", 9), _t(2, "posts/a/index", 3), _t(3, "posts/a-old/", 1),   # one page, three threads
               _t(4, "Post C | The Site", 2), _t(5, "Gone | The Site", 4),                     # title era; a removed page
               _t(6, "Renamed | The Site", 5), _t(7, "Twin | The Site", 1),                    # an authored map; ambiguous
               _t(8, "posts/d/", 2), _t(9, "posts/d/index", 2)]                                # a tie
    mapped = map_threads(threads, paths, titles, {6: "B"}, "The Site", [])
    by = {t["number"]: t for t in mapped}
    assert [by[n]["note"] for n in (1, 2, 3, 4, 6)] == ["A", "A", "A", "C", "B"]
    assert by[4]["via"] == "title" and by[6]["via"] == "map" and by[1]["via"] == "path"
    assert by[5]["note"] is None and "ambiguous" not in by[5]                          # unmapped, reported
    assert by[7]["ambiguous"] == ["T1", "T2"]
    planned = plan_threads(mapped)
    assert planned["plan"]["A"] == {"winner": 1, "earlier": [2, 3]}
    assert planned["plan"]["B"] == {"winner": 6, "earlier": []}
    assert planned["ties"] == [{"note": "D", "numbers": [8, 9]}] and "D" not in planned["plan"]


def test_the_block_loads_the_thread_by_number_else_the_canonical_path_strictly():
    cfg = {"repo": "o/r", "repo-id": "R_1", "category": "Comments", "category-id": "C_1",
           "reactions-enabled": True, "input-position": "top", "language": "en", "loading": "",
           "theme": {"light": "light", "dark": "dark_dimmed"}}
    by_number = render_comments(cfg, number=46, earlier=[53], questions="Ask below.")
    assert by_number.startswith("::: {.post-comments}\nAsk below.\n\n")
    assert "Earlier comments: [#53](https://github.com/o/r/discussions/53)" in by_number
    assert '"mapping": "number", "term": "46"' in by_number and "strict" not in by_number
    assert 'id="giscus-base-theme" value="light"' in by_number and 'id="giscus-alt-theme" value="dark_dimmed"' in by_number
    by_path = render_comments(cfg, term="/posts/x/")
    assert '"mapping": "specific", "term": "/posts/x/", "strict": "1"' in by_path
    assert "Earlier" not in by_path and "Ask" not in by_path
    assert page_comments({"active": [46], "earlier": [53]}, "/p/") == {"number": 46, "term": "/p/", "earlier": [53]}
    assert page_comments({}, "/p/") == {"number": None, "term": "/p/", "earlier": []}
    assert "2 active" in page_comments({"active": [1, 2], "earlier": []}, "/p/")["error"]


def test_fetch_reads_issues_then_the_category_once_discussions_exist():
    pages = {"issue": [{"nodes": [{"number": 2, "title": "posts/b/", "createdAt": "c",
                                   "comments": {"totalCount": 1, "nodes": [{"createdAt": "l"}]}}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "x"}},
                       {"nodes": [{"number": 1, "title": "posts/a/", "createdAt": "c",
                                   "comments": {"totalCount": 0, "nodes": []}}],
                        "pageInfo": {"hasNextPage": False, "endCursor": None}}]}
    calls = []

    def ask(query, variables):
        calls.append(variables)
        if "discussions" in query:
            return {"repository": {"hasDiscussionsEnabled": False}}
        return {"repository": {"issues": pages["issue"][len(calls) - 1]}}
    got = fetch_threads("o/r", "C_1", ask)
    assert [(t["number"], t["comments"], t["last_comment"]) for t in got] == [(1, 0, ""), (2, 1, "l")]
    assert calls[1]["after"] == "x" and calls[2]["category"] == "C_1"
    calls.clear()
    assert len(fetch_threads("o/r", "", ask)) == 2 and all("category" not in c for c in calls)   # no category yet


def _post(title: str) -> str:
    return f"---\ntitle: \"{title}\"\ndate: 2024-01-01\ncategories: [notes]\n---\n\n## Body\n\nText.\n"


async def _seed(gx, root: Path) -> None:
    notes = []
    for slug, title in (("a", "Post A"), ("b", "Post B"), ("c", "Post C")):
        (root / slug).mkdir(parents=True, exist_ok=True)
        (root / slug / "index.md").write_text(_post(title))
        notes.append(note_from_text(str(root / slug / "index.md"), _post(title), corpus_root=str(root), lossless=True))
    nodes, edges = corpus_graph_elements(notes)
    await extend_graph(gx.queue, gx.graph_id, nodes, edges)
    await assert_value(gx, note_node_id("a"), "site_path", "/posts/a/")
    await assert_value(gx, note_node_id("b"), "site_path", "/posts/b-new/")
    await assert_value(gx, note_node_id("b"), "site_path", "/posts/b/", superseded_by=["/posts/b-new/"])


THREADS = [_t(1, "posts/a/", 9), _t(2, "posts/a/index", 3), _t(3, "posts/b/", 2),     # b: by its prior path
           _t(4, "Post C | The Site", 1), _t(5, "Gone | The Site", 4)]


def _ask(threads):
    def ask(query, variables):
        if "discussions" in query:
            return {"repository": {"hasDiscussionsEnabled": False}}
        return {"repository": {"issues": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [
            {"number": t["number"], "title": t["title"], "createdAt": t["created"],
             "comments": {"totalCount": t["comments"], "nodes": []}} for t in threads]}}}
    return ask


async def _slot(gx):
    slot = [a for a in await F.load_assertions(gx) if F.prop(a, "predicate") == P.DISCUSSION]
    ids = {F.nid(a) for a in slot}
    supers = sorted(p for p in await F.load_supersedes(gx) if p[0] in ids and p[1] in ids)
    return sorted((str(F.nid(a)), str(F.prop(a, "subject_id")), str(F.prop(a, "value"))) for a in slot), supers


@pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")
def test_the_harvest_lands_the_facts_and_a_replay_lands_them_again(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "_quarto.yml").write_text(CONFIG)
    jp = str(tmp_path / "writes.jsonl")

    async def live():
        async with open_graph(str(tmp_path / "live.db")) as gx:
            await _seed(gx, tmp_path / "posts")
            dry = await harvest_discussions(gx, str(site), dry_run=True, ask=_ask(THREADS))
            assert not dry["written"] and not (await load_threads(gx))
            res = await harvest_discussions(gx, str(site), ask=_ask(THREADS), actor="agent:test")
            assert res["written"], res
            append_write(jp, "harvest-discussions", {"run": res["run"], "actor": "agent:test"})
            again = await harvest_discussions(gx, str(site), ask=_ask(THREADS))
            # #2 grows past #1: the most comments now stands, #1 joins the earlier threads
            moved = await harvest_discussions(gx, str(site), ask=_ask([{**THREADS[1], "comments": 12}, *THREADS[:1],
                                                                        *THREADS[2:]]), actor="agent:test")
            append_write(jp, "harvest-discussions", {"run": moved["run"], "actor": "agent:test"})
            tie = await harvest_discussions(gx, str(site), ask=_ask([*THREADS, _t(6, "posts/a/index.html", 9)]))
            bad = await harvest_discussions(gx, str(site), maps={9: "nothing"}, ask=_ask(THREADS))
            return dry, res, again, moved, tie, bad, await load_threads(gx), await _slot(gx)

    async def replayed():
        async with open_graph(str(tmp_path / "fresh.db")) as gx:
            await _seed(gx, tmp_path / "posts2")
            await replay_journal(gx, jp)
            return await load_threads(gx), await _slot(gx)

    dry, res, again, moved, tie, bad, threads, slot = asyncio.run(live())
    a, b, c = note_node_id("a"), note_node_id("b"), note_node_id("c")
    assert dry["unmapped"] == [5] and len(dry["changes"]) == 3
    assert res["applied"] == {"asserted": 3, "backfilled": 1}
    assert again["changes"] == [] and not again["written"]                       # nothing new, nothing written
    assert moved["applied"] == {"asserted": 1, "backfilled": 0}
    assert threads == {a: {"active": [2], "earlier": [1]}, b: {"active": [3], "earlier": []},
                       c: {"active": [4], "earlier": []}}
    assert "1 tie(s)" in tie["error"] and not tie["written"]                       # all or nothing
    assert "resolve to no single Note" in bad["error"]
    fresh_threads, fresh_slot = asyncio.run(replayed())
    assert fresh_threads == threads and fresh_slot == slot                        # replay asks no one
    ops = [json.loads(line) for line in Path(jp).read_text().splitlines()]
    assert ops[0]["args"]["run"]["threads"][4]["title"] == "Gone | The Site"     # the observation rides the op
