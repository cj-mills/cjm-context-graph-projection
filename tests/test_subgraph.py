"""The BULK read verbs: node set -> subgraph, and the whole-graph export (+ renders)."""

import asyncio
from pathlib import Path

import pytest

from cjm_context_graph_layer.grammar import make_edge
from cjm_context_graph_layer.ops import extend_graph

from cjm_context_graph_projection.projection import full_graph_view, subgraph_view
from cjm_context_graph_projection.render import render
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph

# These drive the real graph-storage worker capability via open_graph().
# Skip wherever its manifest isn't discoverable (e.g. CI).
pytestmark = pytest.mark.skipif(
    not (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists(),
    reason=f"graph capability {DEFAULT_GRAPH_ID!r} not installed at {DEFAULT_MANIFESTS}",
)

A = "aaaaaaaa-0000-5000-8000-000000000001"
B = "bbbbbbbb-0000-5000-8000-000000000002"
C = "cccccccc-0000-5000-8000-000000000003"
D = "dddddddd-0000-5000-8000-000000000004"
ABSENT = "eeeeeeee-0000-5000-8000-000000000005"


def _node(nid, title):
    return {"id": nid, "label": "Decision",
            "properties": {"title": title, "root_kind": "asserted"}, "sources": []}


async def _build(db):
    nodes = [_node(A, "Alpha"), _node(B, "Beta"), _node(C, "Gamma"), _node(D, "Delta")]
    edges = [make_edge(A, B, "REFERENCES"), make_edge(B, C, "GATED_BY"),
             make_edge(C, D, "ABOUT")]
    async with open_graph(db) as gx:
        await extend_graph(gx.queue, gx.graph_id, nodes, edges)


def test_subgraph_set_interconnect_missing_and_prefix(tmp_path):
    db = str(tmp_path / "g.db")

    async def go():
        await _build(db)
        async with open_graph(db) as gx:
            # A cited by unique prefix, B/C exact, one absent full id.
            return await subgraph_view(gx, ["aaaaaaaa", B, C, ABSENT])

    res = asyncio.run(go())
    assert {n["id"] for n in res["nodes"]} == {A, B, C}
    assert res["seed_count"] == 3 and res["expanded_count"] == 0
    # Interconnecting edges ONLY (C->D leaves the set, so it must not appear).
    assert {(e["source_id"], e["target_id"], e["relation_type"]) for e in res["edges"]} \
        == {(A, B, "REFERENCES"), (B, C, "GATED_BY")}
    assert res["missing"] == [ABSENT]              # read-parity: never silently dropped
    assert res["resolved"]["aaaaaaaa"] == A        # prefix -> id map for join consumers
    assert [n["title"] for n in res["nodes"]][:1] == ["Alpha"]


def test_subgraph_hops_expansion_and_relation_filter(tmp_path):
    db = str(tmp_path / "g.db")

    async def go():
        await _build(db)
        async with open_graph(db) as gx:
            full = await subgraph_view(gx, [C], hops=1)
            only_about = await subgraph_view(gx, [C], hops=1, relations=["ABOUT"])
            return full, only_about

    full, only_about = asyncio.run(go())
    # One hop from C reaches B (incoming GATED_BY) and D (outgoing ABOUT).
    assert {n["id"] for n in full["nodes"]} == {C, B, D}
    assert full["seed_count"] == 1 and full["expanded_count"] == 2
    assert {n["id"] for n in full["nodes"] if n["expanded"]} == {B, D}
    assert {(e["source_id"], e["target_id"]) for e in full["edges"]} == {(B, C), (C, D)}
    # The relation filter narrows the EXPANSION, not the interconnect join.
    assert {n["id"] for n in only_about["nodes"]} == {C, D}


def test_subgraph_expansion_cap_truncates_loudly(tmp_path):
    db = str(tmp_path / "g.db")

    async def go():
        await _build(db)
        async with open_graph(db) as gx:
            return await subgraph_view(gx, [C], hops=1, cap=1)

    res = asyncio.run(go())
    assert res["truncated"] is True
    assert res["seed_count"] == 1 and res["expanded_count"] == 1  # budget respected


def test_journal_window_view_joins_via_bulk_read(tmp_path, monkeypatch):
    from cjm_context_graph_projection.journal import append_write, journal_window_view

    db = str(tmp_path / "g.db")
    jp = str(tmp_path / "writes.jsonl")
    monkeypatch.setenv("CJM_SESSION", "w1")
    append_write(jp, "link", {"source_id": A, "target_id": B, "relation": "REFERENCES"})
    append_write(jp, "assert", {"subject": ABSENT, "predicate": "task_state", "value": "done"})

    async def go():
        await _build(db)
        async with open_graph(db) as gx:
            return await journal_window_view(gx, [jp], session="w1")

    res = asyncio.run(go())
    by_ref = {t["ref"]: t for t in res["touched"]}
    assert by_ref[A]["title"] == "Alpha" and by_ref[A]["label"] == "Decision"
    assert by_ref[ABSENT].get("missing") is True   # the audit surface survives the refactor
    assert res["missing"] == 1


def test_full_graph_view_every_node_every_edge_cheap_titles(tmp_path):
    db = str(tmp_path / "g.db")
    long_stmt = ("DECISION RECORDED: the canvas holds the whole graph. "
                 "Everything after the first clause is trimmed for the cheap-title tier "
                 "because a statement runs to a whole paragraph in house style " + "x" * 120)

    async def go():
        await _build(db)
        async with open_graph(db) as gx:
            # A statement-only node exercises the first-clause trim; a bare node
            # (no title-cascade property at all) falls back to its id.
            stmt = {"id": ABSENT, "label": "Decision",
                    "properties": {"statement": long_stmt, "root_kind": "asserted"},
                    "sources": []}
            bare = {"id": "ffffffff-0000-5000-8000-000000000006", "label": "Entity",
                    "properties": {"root_kind": "asserted"}, "sources": []}
            # A body-only node (a transcription graph's Segment shape): no name-ish
            # property at all — the `text` fallback titles it, clause-trimmed.
            seg = {"id": "99999999-0000-5000-8000-000000000007", "label": "Segment",
                   "properties": {"text": "First sentence of the segment. " + "y" * 200,
                                  "root_kind": "ingested"}, "sources": []}
            await extend_graph(gx.queue, gx.graph_id, [stmt, bare, seg], [])
            return await full_graph_view(gx)

    res = asyncio.run(go())
    assert res["node_count"] == 7 and len(res["nodes"]) == 7
    assert res["edge_count"] == 3
    by_id = {n["id"]: n for n in res["nodes"]}
    assert by_id[A]["title"] == "Alpha" and by_id[A]["label"] == "Decision"
    # statement takes the first-clause trim (never the whole paragraph)…
    assert by_id[ABSENT]["title"].startswith("DECISION RECORDED")
    assert len(by_id[ABSENT]["title"]) < len(long_stmt)
    # …and a node with NO cascade property still renders (id fallback, read-parity).
    assert by_id["ffffffff-0000-5000-8000-000000000006"]["title"] \
        == "ffffffff-0000-5000-8000-000000000006"
    # A body-only node titles from `text`, clause-trimmed.
    seg_title = by_id["99999999-0000-5000-8000-000000000007"]["title"]
    assert seg_title.startswith("First sentence of the segment") and len(seg_title) < 130
    # Edge shape matches subgraph_view (one canvas ingest path).
    assert {(e["source_id"], e["target_id"], e["relation_type"]) for e in res["edges"]} \
        == {(A, B, "REFERENCES"), (B, C, "GATED_BY"), (C, D, "ABOUT")}


def test_render_export_human_is_shape_summary_not_dump():
    obj = {"nodes": [{"id": A, "label": "Decision", "title": "Alpha"},
                     {"id": B, "label": "Decision", "title": "Beta"},
                     {"id": C, "label": "Note", "title": "Gamma"}],
           "edges": [{"source_id": A, "target_id": B, "relation_type": "REFERENCES"}],
           "node_count": 3, "edge_count": 1}
    out = render("export", obj, "human")
    assert "## Export — 3 node(s) · 1 edge(s)" in out
    assert "**Decision** ×2" in out and "**Note** ×1" in out
    assert "Alpha" not in out  # the human view is the shape, never the node dump


def test_render_subgraph_human():
    obj = {"nodes": [{"id": A, "label": "Decision", "title": "Alpha", "expanded": False},
                     {"id": D, "label": "Decision", "title": "Delta", "expanded": True}],
           "edges": [{"source_id": A, "target_id": D, "relation_type": "ABOUT"}],
           "resolved": {A: A}, "missing": ["deadbee"],
           "ambiguous": [{"ref": "abc123", "candidates": [{"id": B, "label": "Decision"}]}],
           "seed_count": 1, "expanded_count": 1, "truncated": True}
    out = render("subgraph", obj, "human")
    assert "## Subgraph" in out and "1 seed + 1 expanded" in out
    assert "MISSING `deadbee`" in out and "AMBIGUOUS `abc123`" in out
    assert "TRUNCATED" in out
    assert "**Alpha**" in out and "↳ **Delta**" in out
    assert "—ABOUT→" in out
