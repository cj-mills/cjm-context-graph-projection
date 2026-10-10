"""The fact layer's batch (finding da6cdab6): a FactBatch reads the assertions and supersessions
once and keeps them current as the batch lands, so a batch of n facts costs n slot lookups --
and lands exactly the graph the same asserts land one at a time: auto-supersession, explicit
supersession, a reinstated value, a back-fill, a contradiction, a multivalued slot, an idempotent
re-assert, and a write-time check whose cached context a landed fact invalidates."""

import asyncio
import sqlite3
from pathlib import Path

import pytest

from cjm_context_graph_layer.ops import extend_graph
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import note_node_id
from cjm_markdown_decompose_core.extract import note_from_text
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from cjm_context_graph_projection.coverage import mint_entity
from cjm_context_graph_projection.purenotes import mint_deliverable_type
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.write import FactBatch, assert_value

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()
pytestmark = pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")

_POST = "---\ntitle: \"{s}\"\ndate: 2024-01-01\n---\n\n## Overview\n\nBody.\n"


def _steps(note):
    """(subject, predicate, value, kwargs) in landing order; every timestamp fixed so two runs compare."""
    t = iter(range(1000, 2000))
    s = [("alpha", "task_state", "open", {}), ("alpha", "task_state", "in_progress", {}),
         ("alpha", "task_state", "done", {}),                                      # ordered: auto-supersede
         ("alpha", "priority", "high", {}), ("alpha", "priority", "low", {}),       # unordered: a contradiction
         ("alpha", "priority", "medium", {"supersede": ["high", "low"]}),          # explicit supersession
         ("beta", P.POINT_ROLE, "meta", {}), ("beta", P.POINT_ROLE, "aside", {"supersede": ["meta"]}),
         ("beta", P.POINT_ROLE, "meta", {"supersede": ["aside"]}),                  # a reinstated value
         ("beta", P.SITE_PATH, "/posts/b/", {}),
         ("beta", P.SITE_PATH, "/old/b/", {"superseded_by": ["/posts/b/"]}),       # a back-fill
         ("beta", "aka", "b-one", {}), ("beta", "aka", "b-two", {}),               # multivalued
         ("beta", "aka", "b-one", {}),                                              # idempotent
         (note, P.ABOUT_TASK, "detection", {}),                                     # a notes post: allowed
         (note, P.DELIVERABLE_TYPE, "archive-tutorial", {"supersede": ["archive-notes"]}),
         (note, P.ABOUT_TASK, "detection", {"actor": "agent:other"})]               # now a tutorial: refused
    return [(a, b, c, {"asserted_at": float(next(t)), **k}) for a, b, c, k in s]


def _rows(db):
    con = sqlite3.connect(str(db))
    try:
        return (sorted(con.execute("select id, label, properties from nodes")),
                sorted(con.execute("select id, relation_type from edges")))
    finally:
        con.close()


def _norm(res):
    keep = ("error", "assertion_id", "superseded", "born_superseded", "conflict", "multi_active", "soft_conflict")
    return {k: res.get(k) for k in keep}


async def _run(db, batched):
    async with open_graph(str(db)) as gx:
        notes = [note_from_text("/c/posts/p/index.md", _POST.format(s="p"), corpus_root="/c/posts", lossless=True)]
        nodes, edges = corpus_graph_elements(notes)
        await extend_graph(gx.queue, gx.graph_id, nodes, edges)
        for k, kind in (("archive-notes", "notes"), ("archive-tutorial", "tutorial")):
            await mint_deliverable_type(gx, k, title=k, kind=kind, origin="archive")
        await mint_entity(gx, "task", "detection", name="Detection", fields={"description": "D", "position": 1})
        note = note_node_id("p")
        await assert_value(gx, note, P.DELIVERABLE_TYPE, "archive-notes", asserted_at=999.0)
        batch = await FactBatch.load(gx) if batched else None
        return [_norm(await assert_value(gx, a, b, c, batch=batch, **k)) for a, b, c, k in _steps(note)]


def test_a_batch_lands_what_single_asserts_land(tmp_path):
    single = asyncio.run(_run(tmp_path / "single.db", False))
    batched = asyncio.run(_run(tmp_path / "batched.db", True))
    assert batched == single
    assert _rows(tmp_path / "batched.db") == _rows(tmp_path / "single.db")
    by = {i: r for i, r in enumerate(single)}
    assert by[2]["superseded"] and by[4]["conflict"] and len(by[5]["superseded"]) == 2
    assert by[10]["born_superseded"] and not by[13].get("error")
    assert not by[14].get("error") and "non-tutorial" in (by[16].get("error") or "")   # the cached type was dropped


def test_the_batch_index_follows_the_writes():
    b = FactBatch([{"id": "a1", "label": "Assertion", "properties": {"slot_id": "s", "predicate": "priority"}}],
                  [("x", "y")])
    assert [n["id"] for n in b.slot("s")] == ["a1"] and b.slot_supersedes("s") == []
    b.cache["k"], b.depends["k"] = 1, {"priority"}
    b.landed([{"id": "a2", "label": "Assertion", "properties": {"slot_id": "s", "predicate": "priority"}}],
             [{"id": "e", "source_id": "a2", "target_id": "a1", "relation_type": "SUPERSEDES"}])
    assert sorted(n["id"] for n in b.slot("s")) == ["a1", "a2"] and b.slot_supersedes("s") == [("a2", "a1")]
    assert "k" not in b.cache                                       # a landed priority fact drops the cached read
    b.dropped([("a2", "a1")])
    assert b.slot_supersedes("s") == []
