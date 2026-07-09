"""The BULK read verb: a node set -> nodes + interconnecting edges, batched (+ its render)."""

import asyncio
from pathlib import Path

import pytest

from cjm_context_graph_layer.grammar import make_edge
from cjm_context_graph_layer.ops import extend_graph

from cjm_context_graph_projection.projection import subgraph_view
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
