"""Series born on-graph + in-body site links resolved after replay (DEC 72d669c5).

A Series is minted by a journaled op with its page's record; its membership and ORDER are
authored intent (`after` on each IN_SERIES edge, never dates); a post's link to a series or
topic page is a verbatim `site_ref` the post-replay resolve pass maps through site_path
facts to a `site_link` REFERENCES edge. The rebuild standard holds across all of it: a
fresh ingest + replay of the journal reproduces the live graph's node and edge id sets."""
import asyncio
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.query import EdgeQuery
from cjm_dev_graph_schema.identity import note_node_id, series_node_id, topic_node_id
from cjm_dev_graph_schema.predicates import SITE_PATH
from cjm_markdown_decompose_core.extract import note_from_text
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from cjm_context_graph_projection.lens import lens_node_id, set_lens
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.series import (mint_series, order_members, place_in_series,
                                                 series_order, set_series_members)
from cjm_context_graph_projection.sitelinks import (DEFER_RESOLVE, resolve_site_links,
                                                    site_path_key)
from cjm_context_graph_projection.write import assert_value


def test_site_path_key_follows_quartos_url_equivalences():
    assert site_path_key("/series/notes/x.html") == site_path_key("/series/notes/x") == "/series/notes/x"
    assert site_path_key("/posts/p/") == site_path_key("/posts/p/index.html") == "/posts/p"
    assert site_path_key("https://christianjmills.com/series/tutorials/y.html#part") == "/series/tutorials/y"
    assert site_path_key("../../series/a.html", "/posts/p/") == "/series/a"
    assert site_path_key("../../series/a.html") is None          # relative with no base
    assert site_path_key("/Series/X") != site_path_key("/series/x")  # a path keeps its case


def test_order_members_walks_the_chain_and_reports_what_breaks_it():
    assert order_members({"b": "a", "a": "", "c": "b"}) == (["a", "b", "c"], [])
    order, issues = order_members({"a": "", "b": "a", "c": "a", "d": "gone"})
    assert order[:2] == ["a", "b"]
    assert {"kind": "fork", "after": "a", "members": ["b", "c"]} in issues
    assert {"kind": "unreached", "member": "d", "after": "gone"} in issues


pytestmark_graph = pytest.mark.skipif(
    not (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists(),
    reason=f"graph capability {DEFAULT_GRAPH_ID!r} not installed at {DEFAULT_MANIFESTS}",
)


def _post(slug: str, body: str = "Body.", categories: str = "notes") -> str:
    return (f"---\ntitle: \"{slug}\"\ndate: 2024-01-01\ncategories: [{categories}]\n---\n\n"
            f"## Overview\n\n{body}\n")


async def _ingest(gx, posts: dict):
    notes = [note_from_text(f"/c/posts/{s}/index.md", t, corpus_root="/c/posts", lossless=True)
             for s, t in posts.items()]
    nodes, edges = corpus_graph_elements(notes)
    await extend_graph(gx.queue, gx.graph_id, nodes, edges)


async def _targets(gx, source_id: str, relation: str) -> dict:
    res = await graph_task(gx.queue, gx.graph_id, "query_edges",
                           query=EdgeQuery(relation_type=relation, source_ids=[source_id]).to_dict())
    raw = getattr(res, "edges", None) or getattr(res, "rows", None) or []
    rows = [e.to_dict() if hasattr(e, "to_dict") else dict(e) for e in raw]
    return {r["target_id"]: r.get("properties") or {} for r in rows}


@pytestmark_graph
def test_series_membership_and_order_are_authored_intent(tmp_path):
    # The user's case (DEC 72d669c5 (4)): tutorials written long after a series began slot in
    # AHEAD of its first post — one splice, two edges, nothing renumbered, nothing sorted by date.
    posts = {s: _post(s) for s in ("part-1", "part-2", "part-3", "dataset-a", "dataset-b")}

    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            await _ingest(gx, posts)
            minted = await mint_series(gx, "cv-series", title="CV", description="d",
                                       image="./p.png", categories=["pytorch", "Tutorial"])
            assert minted["written"] and minted["tagged"] == ["pytorch", "tutorial"]
            assert (await set_series_members(gx, "cv-series", ["part-1", "part-2", "part-3"]))["added"] == 3
            seeded = [m["slug"] for m in (await series_order(gx, "cv-series"))["members"]]
            head = await place_in_series(gx, "cv-series", "dataset-a", first=True)
            second = await place_in_series(gx, "cv-series", "dataset-b", after="dataset-a")
            order = await series_order(gx, "cv-series")
            moved = await place_in_series(gx, "cv-series", "part-3", after="dataset-b")
            after_move = [m["slug"] for m in (await series_order(gx, "cv-series"))["members"]]
            gone = await place_in_series(gx, "cv-series", "dataset-b", remove=True)
            after_remove = [m["slug"] for m in (await series_order(gx, "cv-series"))["members"]]
            refused = [await set_series_members(gx, "cv-series", ["part-1", "part-1"]),
                       await set_series_members(gx, "cv-series", ["nope"]),
                       await set_series_members(gx, "no-series", ["part-1"]),
                       await place_in_series(gx, "cv-series", "part-2", after="dataset-b"),
                       await place_in_series(gx, "cv-series", "part-2")]
            remint = await mint_series(gx, "cv-series", title="CV 2", categories=["pytorch"])
            node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=series_node_id("cv-series"))
            tagged = await _targets(gx, series_node_id("cv-series"), "TAGGED")
            return (seeded, head, second, order, moved, after_move, gone, after_remove, refused,
                    remint, node, tagged)

    (seeded, head, second, order, moved, after_move, gone, after_remove, refused, remint, node,
     tagged) = asyncio.run(go())
    assert seeded == ["part-1", "part-2", "part-3"]
    # Insert at the head: the new member lands, the old head now follows it (one moved edge).
    assert (head["added"], head["moved"], head["removed"]) == (1, 1, 0)
    assert (second["added"], second["moved"]) == (1, 1)
    assert [m["slug"] for m in order["members"]] == ["dataset-a", "dataset-b", "part-1", "part-2", "part-3"]
    assert order["contradictions"] == []
    assert order["members"][0]["after"] == "" and order["members"][2]["after"] == note_node_id("dataset-b")
    assert after_move == ["dataset-a", "dataset-b", "part-3", "part-1", "part-2"]
    assert moved["added"] == 0 and moved["removed"] == 0
    assert after_remove == ["dataset-a", "part-3", "part-1", "part-2"] and gone["removed"] == 1
    assert all(r.get("error") and not r["written"] for r in refused)
    # A re-mint is the WHOLE record: cleared fields clear, the TAGGED set becomes exactly its own.
    assert remint["updated"] and node is not None
    props = node.properties if hasattr(node, "properties") else node["properties"]
    assert props["title"] == "CV 2" and props["description"] == "" and props["image"] == ""
    assert set(tagged) == {topic_node_id("pytorch")}


@pytestmark_graph
def test_site_links_resolve_through_site_path_facts_live_and_report_the_rest(tmp_path):
    posts = {
        "a": _post("a", "Part of [the series](/series/tutorials/cv-series.html)."),
        "b": _post("b", "See [the old URL](/series/tutorials/old-cv) and [topic](/series/notes/edu.html)."),
        "c": _post("c", "A [dead page](/series/notes/nowhere.html)."),
    }
    series, lens = series_node_id("cv-series"), lens_node_id("edu")

    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            await _ingest(gx, posts)
            before = await resolve_site_links(gx, write=False)
            await mint_series(gx, "cv-series", title="CV")
            spec = {"selection": [{"verb": "subgraph", "args": {"refs": [topic_node_id("notes")]}}],
                    "expand": {"hops": 1, "relations": ["TAGGED"]}}
            assert (await set_lens(gx, "edu", spec, title="Education"))["written"]
            # The live hook: each site_path assert re-resolves the links it now answers.
            r1 = await assert_value(gx, series, SITE_PATH, "/series/tutorials/cv-series.html")
            r2 = await assert_value(gx, series, SITE_PATH, "/series/tutorials/old-cv",
                                    superseded_by=["/series/tutorials/cv-series.html"])
            r3 = await assert_value(gx, lens, SITE_PATH, "/series/notes/edu.html")
            a_refs = await _targets(gx, note_node_id("a"), "REFERENCES")
            b_refs = await _targets(gx, note_node_id("b"), "REFERENCES")
            report = await resolve_site_links(gx, write=False)
            # Ambiguity: a second page claiming the same path never resolves either way.
            await mint_series(gx, "twin")
            r4 = await assert_value(gx, series_node_id("twin"), SITE_PATH, "/series/tutorials/cv-series")
            twin = await resolve_site_links(gx)
            a_after = await _targets(gx, note_node_id("a"), "REFERENCES")
            return before, r1, r2, r3, a_refs, b_refs, report, r4, twin, a_after

    before, r1, r2, r3, a_refs, b_refs, report, r4, twin, a_after = asyncio.run(go())
    assert before["links"] == 4 and before["resolved"] == 0 and len(before["unresolved"]) == 4
    assert r1["site_links"]["added"] == 1 and r2["site_links"]["added"] == 1 and r3["site_links"]["added"] == 1
    assert a_refs == {series: {"site_link": True}}
    # The superseded path still names its page; the topic page resolves to its Lens.
    assert b_refs == {series: {"site_link": True}, lens: {"site_link": True}}
    assert report["resolved"] == 3 and report["added"] == report["removed"] == 0
    assert [(r["slug"], r["target"]) for r in report["unresolved"]] == [("c", "/series/notes/nowhere.html")]
    # The twin claims only the CURRENT path: a's link turns ambiguous and its own assert hook
    # retracts that edge; b's link names the old path, which the Series alone still holds.
    assert r4["site_links"]["removed"] == 1 and twin["removed"] == 0
    assert [r["slug"] for r in twin["ambiguous"]] == ["a"] and a_after == {}


@pytestmark_graph
def test_replay_defers_the_hooks_to_one_closing_pass():
    async def go():
        token = DEFER_RESOLVE.set(True)
        try:
            from cjm_context_graph_projection.sitelinks import resolve_after_write
            return await resolve_after_write(None)
        finally:
            DEFER_RESOLVE.reset(token)

    assert asyncio.run(go()) is None


def _run(*args):
    return subprocess.run([sys.executable, "-m", "cjm_context_graph_projection.cli", *args],
                          capture_output=True, text=True)


def _ids(db):
    con = sqlite3.connect(str(db))
    try:
        return (sorted(r[0] for r in con.execute("select id from nodes")),
                sorted(r[0] for r in con.execute("select id from edges")))
    finally:
        con.close()


@pytestmark_graph
def test_a_rebuild_reproduces_series_order_and_site_links_from_source_plus_journal(tmp_path):
    corpus = tmp_path / "posts"
    for slug, body in {"p1": "Part of [the series](/series/tutorials/s.html).", "p2": "Two.",
                       "p0": "See [topic](/series/notes/t.html)."}.items():
        (corpus / slug).mkdir(parents=True)
        (corpus / slug / "index.md").write_text(_post(slug, body))
    journal = str(tmp_path / "writes.jsonl")
    for sub in ("live", "fresh"):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "graph.config.json").write_text(json.dumps(
            {"notes_corpus": str(corpus), "notes_profile": "quarto_post"}))
    live = str(tmp_path / "live" / "g.db")
    base = ["--graph-db-path", live, "--journal-path", journal]
    assert _run(*base, "ingest-notes").returncode == 0
    spec = tmp_path / "lens.json"
    spec.write_text(json.dumps({"selection": [{"verb": "subgraph", "args": {"refs": [topic_node_id("notes")]}}],
                                "expand": {"hops": 1, "relations": ["TAGGED"]}}))
    batch = tmp_path / "paths.jsonl"
    batch.write_text("\n".join(json.dumps(x) for x in [
        {"subject": series_node_id("s"), "predicate": SITE_PATH, "value": "/series/tutorials/s.html"},
        {"subject": series_node_id("s"), "predicate": SITE_PATH, "value": "/series/tutorials/s-old",
         "superseded_by": ["/series/tutorials/s.html"]},
        {"subject": lens_node_id("t"), "predicate": SITE_PATH, "value": "/series/notes/t.html"}]) + "\n")
    for cmd in (["series", "s", "--title", "S", "--category", "tutorial"],
                ["series-members", "s", "p1", "p2"],
                ["place-in-series", "s", "p0", "--first"],
                ["set-lens", "t", "--spec-file", str(spec), "--title", "T"],
                ["assert-batch", str(batch)]):
        r = _run(*base, *cmd)
        assert r.returncode == 0, (cmd, r.stdout, r.stderr)
    assert "site links: 2 resolved" in r.stdout          # the batch's ONE closing pass
    order = _run("--graph-db-path", live, "series-order", "s")
    assert order.returncode == 0 and order.stdout.index("p0") < order.stdout.index("p1") < order.stdout.index("p2")
    assert _run("--graph-db-path", live, "site-links").returncode == 0
    verbs = [json.loads(line)["verb"] for line in Path(journal).read_text().splitlines()]
    assert verbs == ["series", "series-members", "place-in-series", "set-lens", "assert", "assert", "assert"]

    fresh = str(tmp_path / "fresh" / "g.db")
    r = _run("--graph-db-path", fresh, "--journal-path", journal, "ingest-notes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "'site_links_resolved': 2" in r.stdout
    assert _ids(fresh) == _ids(live)
