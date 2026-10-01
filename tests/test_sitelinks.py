"""One resolver for every in-body site link (ruling d31e9ba7): relative targets, anchors onto
Sections or reported, never a dangling edge."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.query import EdgeQuery
from cjm_dev_graph_schema.identity import note_node_id, section_node_id
from cjm_dev_graph_schema.predicates import SITE_PATH
from cjm_markdown_decompose_core.extract import note_from_text
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.sitelinks import resolve_site_links
from cjm_context_graph_projection.write import assert_value
from conftest import commit_all

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()

FM = "---\ntitle: T\ndate: 2024-01-01\ncategories: [x]\n---\n\n"
PART_2 = FM + ("See [part one](../part-1/), [its setup](../part-1/#setup), [a gone heading](../part-1/#gone), "
               "[nowhere](/posts/nowhere/), [myself](./#more) and [the same place again](/posts/s/part-1/).\n\n"
               "## More\n\nText.\n")
PART_1 = FM + "## Setup\n\nSteps.\n"


@pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")
def test_one_resolver_places_post_links_and_reports_what_it_cannot(tmp_path):
    root = tmp_path / "posts"

    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            notes = [note_from_text(str(root / "s" / p / "index.md"), text, corpus_root=str(root),
                                    profile="quarto_post", lossless=True)
                     for p, text in (("part-2", PART_2), ("part-1", PART_1))]
            nodes, edges = corpus_graph_elements(notes)
            await extend_graph(gx.queue, gx.graph_id, nodes, edges)
            for p in ("part-1", "part-2"):
                await assert_value(gx, note_node_id(f"s/{p}"), SITE_PATH, f"/posts/s/{p}/")
            first = await resolve_site_links(gx)
            again = await resolve_site_links(gx)
            return first, again

    first, again = asyncio.run(go())
    part1, part2 = note_node_id("s/part-1"), note_node_id("s/part-2")
    # relative targets resolve against the linking page's own path; the plain link, its rooted
    # twin and the link whose anchor names no Section all land on the page: ONE edge (the id is
    # the triple; the first link in document order gives its properties); the anchored link
    # lands on the Section (no write window ran the step, so this pass places both)
    assert first["links"] == 6 and first["resolved"] == 2 and first["added"] == 2
    assert [r["target"] for r in first["unresolved"]] == ["/posts/nowhere/"]
    # an anchor naming no Section: the edge stays on the page, the anchor kept, and reported
    assert [(r["target"], r["anchor"]) for r in first["anchors"]] == [("../part-1/#gone", "gone")]
    # the self-link is no cross-reference; a second pass is a no-op
    assert not first["ambiguous"] and again["added"] == 0 and again["removed"] == 0
    assert section_node_id(part1, "setup") != part1


@pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")
def test_a_changed_anchor_re_mints_the_standing_edge(tmp_path):
    root = tmp_path / "posts"

    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            src = FM + "[a](../part-1/#gone)\n"
            notes = [note_from_text(str(root / "s" / p / "index.md"), text, corpus_root=str(root),
                                    profile="quarto_post", lossless=True)
                     for p, text in (("part-2", src), ("part-1", PART_1))]
            nodes, edges = corpus_graph_elements(notes)
            await extend_graph(gx.queue, gx.graph_id, nodes, edges)
            for p in ("part-1", "part-2"):
                await assert_value(gx, note_node_id(f"s/{p}"), SITE_PATH, f"/posts/s/{p}/")
            first = await resolve_site_links(gx)
            # the link now names another missing anchor: same page, same edge id, new anchor
            await graph_task(gx.queue, gx.graph_id, "update_node", node_id=note_node_id("s/part-2"),
                             properties={"site_refs": ["../part-1/#also-gone"]})
            second = await resolve_site_links(gx)
            return first, second

    first, second = asyncio.run(go())
    assert first["resolved"] == 1 and [r["anchor"] for r in first["anchors"]] == ["gone"]
    assert second["added"] == 1 and second["removed"] == 1
    assert [r["anchor"] for r in second["anchors"]] == ["also-gone"]


def _cli(*args):
    return subprocess.run([sys.executable, "-m", "cjm_context_graph_projection.cli", *args],
                          capture_output=True, text=True)


def _post(title, body):
    return f'---\ntitle: "{title}"\ndate: 2026-09-30\ncategories: [x]\n---\n\n{body}\n'


@pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")
def test_live_site_links_equal_their_rebuild_after_every_window(tmp_path):
    """Design amendment 9ee4e346: the resolve is a step at the op-clock window grain, so a
    site_link edge carries the time of the invocation that first made it hold — live and on
    rebuild alike. An archive post rides along: its elements take their times from the
    clone's git history (design amendment 19edbe97), so they match on every rebuild too — and
    its Topic, shared with the born posts, keeps the archive's time."""
    corpus, emit = tmp_path / "archive", tmp_path / "drafts"
    (corpus / "old").mkdir(parents=True)
    (corpus / "old" / "index.md").write_text(_post("Old", "## Notes\n\nArchive body."))
    commit_all(corpus)
    emit.mkdir()
    journal = str(tmp_path / "writes.jsonl")
    live = str(tmp_path / "live.db")
    base = ["--graph-db-path", live, "--journal-path", journal]
    checks = []

    def ok(*args):
        r = _cli(*base, *args)
        assert r.returncode == 0, f"{args[0]} failed: {r.stderr or r.stdout}"
        return r

    def equals_its_rebuild(after):
        rebuilt = str(tmp_path / f"rebuilt{len(checks)}.db")
        r = _cli("--graph-db-path", rebuilt, "--journal-path", journal, "ingest-notes",
                 "--notes-corpus", str(corpus), "--profile", "quarto_post", "--emit-root", str(emit))
        assert r.returncode == 0, r.stderr or r.stdout
        assert "'site_links_drift': 0" in r.stdout, r.stdout
        r = _cli("--graph-db-path", live, "rebuild-diff", "--against", rebuilt)
        assert r.returncode == 0 and "CLEAN" in r.stdout, (
            f"after {after}: the live db differs from its rebuild:\n{r.stdout}\n{r.stderr}")
        checks.append(after)

    def step(*args):
        r = ok(*args)
        equals_its_rebuild(args[0])
        return r

    def born(slug, title, body):
        f = tmp_path / f"{slug.replace('/', '_')}.md"
        f.write_text(_post(title, body))
        return step("new-note", "--slug", slug, "--profile", "quarto_post", "--emit-root", str(emit),
                    "--content-file", str(f))

    assert _cli(*base, "ingest-notes", "--notes-corpus", str(corpus), "--profile", "quarto_post",
                "--emit-root", str(emit)).returncode == 0
    # a is born linking to part-1's #setup (no such Section yet) and to c's page; part-1 links back
    born("s/part-2", "Part 2", "Read [the setup](../part-1/#setup) and [c](/posts/s/c/).")
    born("s/part-1", "Part 1", "## Intro\n\nText and [next](../part-2/).")
    born("s/c", "C", "Plain.")
    born("s/d", "D", "Plain.")
    part1, part2 = note_node_id("s/part-1"), note_node_id("s/part-2")
    c, d = note_node_id("s/c"), note_node_id("s/d")
    # one path alone: part-1's relative link still has no holder, part-2 no base -> no edge
    step("assert", part1, SITE_PATH, "/posts/s/part-1/")
    # a batch: three edges justified inside ONE window (the invocation's close)
    batch = tmp_path / "paths.jsonl"
    batch.write_text("\n".join(json.dumps(x) for x in [
        {"subject": part2, "predicate": SITE_PATH, "value": "/posts/s/part-2/"},
        {"subject": c, "predicate": SITE_PATH, "value": "/posts/s/c/"}]) + "\n")
    r = step("assert-batch", str(batch))
    assert "+3 -0" in r.stderr, r.stderr
    # a Section appears on the HOLDER: part-2's anchored link moves from the page to it
    step("add-section", "s/part-1", "--content", "## Setup\n\nSteps.")
    # the path moves between holders: part-2's link follows it
    step("transfer-path", c, d)
    # a note born AFTER the facts it resolves through: its edge takes its own birth window, not
    # an earlier fact's (the replay hoists its genesis; the step leaves it out until its position)
    born("s/e", "E", "Back to [part 1](/posts/s/part-1/).")

    async def refs():
        async with open_graph(live) as gx:
            res = await graph_task(gx.queue, gx.graph_id, "query_edges",
                                   query=EdgeQuery(relation_type="REFERENCES").to_dict())
            return {(e.source_id, e.target_id) for e in res.edges
                    if (e.properties or {}).get("site_link")}

    assert asyncio.run(refs()) == {(part2, section_node_id(part1, "setup")), (part2, d),
                                   (part1, part2), (note_node_id("s/e"), part1)}
    assert len(checks) == 9
