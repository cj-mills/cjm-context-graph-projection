"""The claims (design amendment 98e99fe5 under the coverage model de808eae (1)): a claim is an
Entity with a `claim_state` fact; a deliverable backs it with one SUPPORTS edge per pair, the
kind on the edge; the report derives the backing and refuses a stateless, conflicting or
unbacked-offered claim; the public view shows only offered claims and public supports; a
rebuild reproduces the live graph."""

import asyncio
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from cjm_context_graph_layer.ops import extend_graph
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import entity_node_id, note_node_id
from cjm_markdown_decompose_core.extract import note_from_text
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from cjm_context_graph_projection.claims import (claims_report, load_supports, project_claims,
                                                 public_view, record_support)
from cjm_context_graph_projection.coverage import mint_entity, validate_entity
from cjm_context_graph_projection.purenotes import mint_deliverable_type, public_deliverables
from cjm_context_graph_projection.render import render
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.write import assert_value

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()
pytestmark_graph = pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")

_C = [{"key": "cv", "id": "c1", "name": "CV", "statement": "CV work", "position": 1},
      {"key": "llm", "id": "c2", "name": "LLM", "statement": "LLM work", "position": 2},
      {"key": "gpu", "id": "c3", "name": "GPU", "statement": "GPU work", "position": 3},
      {"key": "qt", "id": "c4", "name": "Qt", "statement": "Qt apps", "position": 4},
      {"key": "x", "id": "c5", "name": "X", "statement": "X", "position": 5}]


def _e(src, claim, kind):
    return {"source_id": src, "target_id": claim, "properties": {"kind": kind, "note": ""}}


def test_the_floor_the_refusals_and_the_public_view():
    dl = {"tut": {"slug": "tut", "title": "T", "public": True},
          "notes": {"slug": "notes", "title": "N", "public": True},
          "draft": {"slug": "draft", "title": "D", "public": False}}
    r = project_claims(_C, {"c1": ["offered"], "c2": ["offered"], "c3": ["building"],
                            "c4": ["building", "offered"]},
                       [_e("tut", "c1", "capability"), _e("notes", "c1", "knowledge"),
                        _e("notes", "c2", "knowledge"), _e("draft", "c2", "capability"),
                        _e("tut", "c3", "capability")], dl)
    by = {c["key"]: c for c in r["claims"]}
    # cv: a public capability carries the offer; llm: knowledge alone and a draft's capability do not
    assert by["cv"]["backed"] and not by["llm"]["backed"]
    # gpu meets the floor but stays building (the floor never promotes)
    assert by["gpu"]["backed"] and by["gpu"]["state"] == "building"
    assert {(x["claim"], x["reason"]) for x in r["refusals"]} == {
        ("llm", "unbacked"), ("qt", "conflict"), ("x", "stateless")}
    assert not r["ok"] and by["qt"]["state"] is None and by["qt"]["conflict"] == ["building", "offered"]
    # The public view: offered claims only, public supports only
    pub = public_view(r)
    assert [c["key"] for c in pub] == ["cv", "llm"]
    assert [x["id"] for x in pub[1]["supports"]["capability"]] == [] and pub[1]["supports"]["knowledge"]


def test_a_claim_record_needs_its_statement_and_position():
    assert validate_entity("claim", "cv", "CV", {"statement": "s", "position": 1}) is None
    assert "needs statement" in validate_entity("claim", "cv", "CV", {"position": 1})
    assert "no field(s) device_class" in validate_entity("claim", "cv", "CV", {"statement": "s", "position": 1,
                                                                             "device_class": "gpu"})


def _post(slug: str) -> str:
    return f"---\ntitle: \"{slug}\"\ndate: 2024-01-01\ncategories: [tutorial]\n---\n\n## Overview\n\nBody.\n"


@pytestmark_graph
def test_claim_state_supports_and_the_public_rule(tmp_path):
    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            notes = [note_from_text(f"/c/posts/{s}/index.md", _post(s), corpus_root="/c/posts", lossless=True)
                     for s in ("arch", "born", "loose")]
            nodes, edges = corpus_graph_elements(notes)
            await extend_graph(gx.queue, gx.graph_id, nodes, edges)
            await mint_deliverable_type(gx, "archive-tutorial", title="A", kind="tutorial", origin="archive")
            await mint_deliverable_type(gx, "born-page", title="B", kind="work", origin="born")
            await assert_value(gx, note_node_id("arch"), "deliverable_type", "archive-tutorial")
            await assert_value(gx, note_node_id("born"), "deliverable_type", "born-page")
            await assert_value(gx, note_node_id("born"), P.PUBLISH_STATE, "draft")
            for key, pos in (("cv", 1), ("graph", 2)):
                assert (await mint_entity(gx, "claim", key, name=key.upper(),
                                          fields={"statement": f"{key} work", "position": pos}))["written"]
            cv, graph = entity_node_id("claim", "cv"), entity_node_id("claim", "graph")
            bad_value = await assert_value(gx, cv, P.CLAIM_STATE, "maybe")
            bad_subject = await assert_value(gx, note_node_id("arch"), P.CLAIM_STATE, "offered")
            await assert_value(gx, cv, P.CLAIM_STATE, "building")
            promoted = await assert_value(gx, cv, P.CLAIM_STATE, "offered", supersede=["building"])
            await assert_value(gx, graph, P.CLAIM_STATE, "offered")
            first = await record_support(gx, "arch", "cv", kind="knowledge", note="first read")
            restated = await record_support(gx, note_node_id("arch"), "cv", kind="capability", note="trains")
            born = await record_support(gx, "born", "graph", kind="capability")
            bad_kind = await record_support(gx, "arch", "graph", kind="proof")
            no_claim = await record_support(gx, "arch", "nope", kind="method")
            no_note = await record_support(gx, "missing", "cv", kind="method")
            await record_support(gx, "loose", "cv", kind="method")
            gone = await record_support(gx, "loose", "cv", retract=True)
            pub = await public_deliverables(gx)
            report = await claims_report(gx, public=True)
            edges = await load_supports(gx)
            # Publishing the born page lifts the graph claim over the floor
            await assert_value(gx, note_node_id("born"), P.PUBLISH_STATE, "published", supersede=["draft"])
            after = await claims_report(gx)
            return (bad_value, bad_subject, promoted, first, restated, born, bad_kind, no_claim, no_note,
                    gone, pub, report, edges, after)
    (bad_value, bad_subject, promoted, first, restated, born, bad_kind, no_claim, no_note,
     gone, pub, report, edges, after) = asyncio.run(go())
    assert "no claim state" in bad_value["error"] and "claim Entity" in bad_subject["error"]
    assert not promoted.get("conflict")
    assert restated["replaced"] and restated["edge_id"] == first["edge_id"]
    assert "kind must be one of" in bad_kind["error"] and "no claim `nope`" in no_claim["error"]
    assert "no deliverable Note" in no_note["error"] and gone["retracted"]
    # The public rule: an archive post is public as authored; a born draft and an untyped Note are not
    assert pub == {note_node_id("arch")}
    assert len(edges) == 2
    by = {c["key"]: c for c in report["claims"]}
    assert by["cv"]["state"] == "offered" and by["cv"]["backed"]
    assert [x["note"] for x in by["cv"]["supports"]["capability"]] == ["trains"]
    assert [(x["claim"], x["reason"]) for x in report["refusals"]] == [("graph", "unbacked")]
    assert [x["public"] for x in by["graph"]["supports"]["capability"]] == [False]
    assert [c["key"] for c in report["public"]] == ["cv", "graph"]
    assert after["ok"] and all(c["backed"] for c in after["claims"])
    full = render("claims", {k: v for k, v in report.items() if k != "public"})
    assert "⚠ **unbacked** `graph`" in full and "(not public)" in full
    # The public view drops the draft's support from the page, never the refusal
    public = render("claims", report)
    assert "the public view" in public and "(not public)" not in public and "⚠ **unbacked**" in public


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
def test_a_rebuild_reproduces_the_claims(tmp_path):
    corpus = tmp_path / "posts"
    for s in ("a", "b"):
        (corpus / s).mkdir(parents=True)
        (corpus / s / "index.md").write_text(_post(s))
    journal = str(tmp_path / "writes.jsonl")
    for sub in ("live", "fresh"):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "graph.config.json").write_text(json.dumps(
            {"notes_corpus": str(corpus), "notes_profile": "quarto_post"}))
    live = str(tmp_path / "live" / "g.db")
    base = ["--graph-db-path", live, "--journal-path", journal]
    assert _run(*base, "ingest-notes").returncode == 0
    cv = entity_node_id("claim", "cv")
    for cmd in (["notes-type", "archive-tutorial", "--title", "Archive tutorial", "--kind", "tutorial",
                 "--origin", "archive"],
                ["assert", note_node_id("a"), "deliverable_type", "archive-tutorial"],
                ["assert", note_node_id("b"), "deliverable_type", "archive-tutorial"],
                ["entity", "claim", "cv", "--name", "Computer vision", "--statement", "CV work", "--position", "1"],
                ["entity", "claim", "gpu", "--name", "GPU", "--statement", "GPU work", "--position", "2"],
                ["assert", cv, P.CLAIM_STATE, "building"],
                ["assert", cv, P.CLAIM_STATE, "offered", "--supersede", "building"],
                ["assert", entity_node_id("claim", "gpu"), P.CLAIM_STATE, "building"],
                ["supports", "a", "cv", "--kind", "knowledge"],
                ["supports", "a", "cv", "--kind", "capability", "--note", "trains a detector"],
                ["supports", "b", "gpu", "--kind", "knowledge"],
                ["supports", "b", "cv", "--kind", "method"],
                ["supports", "b", "cv", "--retract"]):
        r = _run(*base, *cmd)
        assert r.returncode == 0, (cmd, r.stdout, r.stderr)
    refused = _run(*base, "supports", "a", "cv", "--kind", "proof")
    assert refused.returncode == 1 and "kind must be one of" in refused.stdout
    verbs = [json.loads(line)["verb"] for line in Path(journal).read_text().splitlines()]
    assert verbs.count("supports") == 5                                   # the refusal journaled nothing
    report = _run("--graph-db-path", live, "claims")
    assert report.returncode == 0, report.stdout + report.stderr
    assert "`cv` · _offered_ · backed" in report.stdout and "trains a detector" in report.stdout

    fresh = str(tmp_path / "fresh" / "g.db")
    r = _run("--graph-db-path", fresh, "--journal-path", journal, "ingest-notes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _ids(fresh) == _ids(live)
    assert _run("--graph-db-path", fresh, "claims").stdout == report.stdout
    public = [_run("--graph-db-path", db, "claims", "--public").stdout for db in (live, fresh)]
    assert public[0] == public[1] and "the public view — 1 claim(s)" in public[0] and "`gpu`" not in public[0]
