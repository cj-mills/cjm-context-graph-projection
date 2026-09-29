"""The Tutorials matrix (designs 8cbdc883 / c450133a): the task and stage vocabulary is graph
data minted by the journaled `entity` op; a tutorial's `teaches_task` / `teaches_stage` facts
name vocabulary keys (checked at write time); the matrix, the cells a cross-task stage leaves
covered, the gaps and the refusals are derived, and a rebuild reproduces the live graph."""

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

from cjm_context_graph_projection.coverage import (coverage_matrix, mint_entity, project_matrix,
                                                   validate_entity)
from cjm_context_graph_projection.purenotes import mint_deliverable_type
from cjm_context_graph_projection.render import render
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.write import assert_value

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()
pytestmark_graph = pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")

_T = [{"key": "det", "name": "Detection", "position": 1}, {"key": "seg", "name": "Segmentation", "position": 2},
      {"key": "general", "name": "General", "position": 8, "cross_task": True},
      {"key": "other", "name": "Other", "position": 9, "off_grid": True}]
_S = [{"key": "setup", "name": "Setup", "position": 0, "cross_task": True},
      {"key": "train", "name": "Training", "position": 1}, {"key": "deploy", "name": "Deployment", "position": 2}]


def _f(tasks=(), stages=()):
    return {P.TEACHES_TASK: list(tasks), P.TEACHES_STAGE: list(stages)}


def test_the_matrix_cells_cross_task_cover_gaps_and_off_grid():
    tuts = {i: {"slug": i, "title": i.upper()} for i in ("a", "b", "env", "art", "track")}
    m = project_matrix(_T, _S, tuts, {
        "a": _f(["det"], ["train", "deploy"]),        # a multi-stage post fills both cells
        "b": _f(["seg"], ["train"]),
        "env": _f(["general"], ["setup"]),             # the cross-task row covers setup for every task
        "art": _f(["other"]),                          # off the grid: no stage needed
        "track": _f(["det", "seg"], ["deploy"]),
    })
    assert m["ok"] and not m["refusals"]
    assert m["cells"]["det|train"] == ["a"] and m["cells"]["det|deploy"] == ["a", "track"]
    assert m["cells"]["seg|deploy"] == ["track"] and m["cells"]["general|setup"] == ["env"]
    assert {(c["task"], c["stage"]) for c in m["covered"]} == {("det", "setup"), ("seg", "setup")}
    # The cross-task row's own empty cells are never gaps; the off-grid task is no row
    assert m["gaps"] == [] and [t["key"] for t in m["tasks"]] == ["det", "seg", "general"]
    assert m["off_grid"] == {"other": ["art"]}


def test_a_cross_task_stage_is_a_gap_while_the_cross_task_row_is_empty():
    m = project_matrix(_T, _S, {"a": {"slug": "a"}}, {"a": _f(["det"], ["train", "deploy"])})
    assert {(g["task"], g["stage"]) for g in m["gaps"]} == {("det", "setup"), ("seg", "setup"),
                                                           ("seg", "train"), ("seg", "deploy")}
    assert m["covered"] == []


def test_the_projection_refuses_what_it_would_otherwise_drop():
    tuts = {i: {"slug": i} for i in ("none", "typo", "nostage", "ok")}
    m = project_matrix(_T, _S, tuts, {"typo": _f(["dett"], ["train"]), "nostage": _f(["det"]),
                                      "ok": _f(["det"], ["train"])})
    assert not m["ok"]
    assert {r["id"]: r["reason"] for r in m["refusals"]} == {"none": "unsurveyed", "typo": "unknown",
                                                             "nostage": "unstaged"}
    assert m["cells"] == {"det|train": ["ok"]}      # a refused post lands in no cell
    out = render("coverage", m)
    assert "Refused (3)" in out and "unsurveyed" in out and "| Detection |" in out


def test_the_ratified_cross_product_flaw_is_explicit():
    # c450133a (3): independent sets imply every pair — two tasks AND two stages fill four cells
    m = project_matrix(_T, _S, {"p": {"slug": "p"}}, {"p": _f(["det", "seg"], ["train", "deploy"])})
    assert sorted(m["cells"]) == ["det|deploy", "det|train", "seg|deploy", "seg|train"]


def test_entity_records_are_validated_per_declared_kind():
    assert validate_entity("stage", "train", "Training", {"position": 1}) is None
    assert "not declared" in validate_entity("hardware", "rtx", "RTX", {})
    assert "needs position" in validate_entity("task", "det", "Detection", {})
    assert "must be int" in validate_entity("stage", "s", "S", {"position": True})
    assert "no field" in validate_entity("stage", "s", "S", {"position": 1, "off_grid": True})
    assert "slug" in validate_entity("task", "a b", "A", {"position": 1})
    assert "name" in validate_entity("task", "a", "", {"position": 1})


def _post(slug: str) -> str:
    return f"---\ntitle: \"{slug}\"\ndate: 2024-01-01\ncategories: [tutorial]\n---\n\n## Overview\n\nBody.\n"


@pytestmark_graph
def test_vocabulary_is_data_and_facts_name_live_entries(tmp_path):
    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            notes = [note_from_text(f"/c/posts/{s}/index.md", _post(s), corpus_root="/c/posts", lossless=True)
                     for s in ("a", "b")]
            nodes, edges = corpus_graph_elements(notes)
            await extend_graph(gx.queue, gx.graph_id, nodes, edges)
            await mint_deliverable_type(gx, "archive-tutorial", title="Archive tutorial",
                                        kind="tutorial", origin="archive")
            for s in ("a", "b"):
                await assert_value(gx, note_node_id(s), "deliverable_type", "archive-tutorial")
            for t in _T:
                f = {k: v for k, v in t.items() if k not in ("key", "name")}
                assert (await mint_entity(gx, "task", t["key"], name=t["name"], fields=f))["written"]
            for s in _S:
                f = {k: v for k, v in s.items() if k not in ("key", "name")}
                await mint_entity(gx, "stage", s["key"], name=s["name"], fields=f)
            # A typo'd key never lands
            bad = await assert_value(gx, note_node_id("a"), P.TEACHES_TASK, "dett")
            # Two values on one multivalued slot coexist
            await assert_value(gx, note_node_id("a"), P.TEACHES_TASK, "det")
            both = await assert_value(gx, note_node_id("a"), P.TEACHES_TASK, "seg")
            await assert_value(gx, note_node_id("a"), P.TEACHES_STAGE, "train")
            first = await coverage_matrix(gx)
            # A rename is a re-mint (the id and every fact stay); a retired entry leaves the axis
            # and refuses new facts; the whole record clears what a re-mint leaves out
            await mint_entity(gx, "task", "seg", name="Instance segmentation", fields={"position": 2})
            await mint_entity(gx, "stage", "deploy", name="Deployment", fields={"position": 2, "retired": True})
            retired = await assert_value(gx, note_node_id("b"), P.TEACHES_STAGE, "deploy")
            await mint_entity(gx, "task", "general", name="General", fields={"position": 8})
            second = await coverage_matrix(gx)
            return bad, both, first, retired, second
    bad, both, first, retired, second = asyncio.run(go())
    assert "no task in the vocabulary" in bad["error"] and not bad.get("written")
    assert not both.get("conflict")
    assert first["cells"] == {"det|train": [note_node_id("a")], "seg|train": [note_node_id("a")]}
    assert [r["reason"] for r in first["refusals"]] == ["unsurveyed"]          # b has no facts yet
    assert "retired" in retired["error"]
    assert [s["key"] for s in second["stages"]] == ["setup", "train"]
    assert next(t for t in second["tasks"] if t["key"] == "seg")["name"] == "Instance segmentation"
    assert not next(t for t in second["tasks"] if t["key"] == "general").get("cross_task")
    assert entity_node_id("task", "seg") == next(t for t in second["tasks"] if t["key"] == "seg")["id"]


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
def test_a_rebuild_reproduces_the_vocabulary_and_the_matrix(tmp_path):
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
    batch = tmp_path / "facts.jsonl"
    batch.write_text("\n".join(json.dumps(x) for x in [
        {"subject": note_node_id("a"), "predicate": "deliverable_type", "value": "archive-tutorial"},
        {"subject": note_node_id("b"), "predicate": "deliverable_type", "value": "archive-tutorial"},
        {"subject": note_node_id("a"), "predicate": P.TEACHES_TASK, "value": "det"},
        {"subject": note_node_id("a"), "predicate": P.TEACHES_STAGE, "value": "train"},
        {"subject": note_node_id("a"), "predicate": P.TEACHES_STAGE, "value": "deploy"},
        {"subject": note_node_id("b"), "predicate": P.TEACHES_TASK, "value": "general"},
        {"subject": note_node_id("b"), "predicate": P.TEACHES_STAGE, "value": "setup"}]) + "\n")
    for cmd in (["notes-type", "archive-tutorial", "--title", "Archive tutorial", "--kind", "tutorial",
                 "--origin", "archive"],
                ["entity", "task", "det", "--name", "Detection", "--position", "1"],
                ["entity", "task", "general", "--name", "General", "--position", "8", "--cross-task"],
                ["entity", "stage", "setup", "--name", "Setup", "--position", "0", "--cross-task"],
                ["entity", "stage", "train", "--name", "Training", "--position", "1"],
                ["entity", "stage", "deploy", "--name", "Deploy", "--position", "2"],
                ["entity", "stage", "deploy", "--name", "Deployment", "--position", "2"],
                ["assert-batch", str(batch)]):
        r = _run(*base, *cmd)
        assert r.returncode == 0, (cmd, r.stdout, r.stderr)
    refused = _run(*base, "entity", "stage", "x", "--name", "X")          # no position: refused
    assert refused.returncode == 1 and "needs position" in refused.stdout
    verbs = [json.loads(line)["verb"] for line in Path(journal).read_text().splitlines()]
    assert verbs.count("entity") == 6 and verbs.count("assert") == 7      # the refusal journaled nothing
    matrix = _run("--graph-db-path", live, "coverage")
    assert matrix.returncode == 0, matrix.stdout + matrix.stderr
    assert "| Detection | ~ | 1 | 1 |" in matrix.stdout and "| Deployment |" in matrix.stdout

    fresh = str(tmp_path / "fresh" / "g.db")
    r = _run("--graph-db-path", fresh, "--journal-path", journal, "ingest-notes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _ids(fresh) == _ids(live)
    again = _run("--graph-db-path", fresh, "coverage")
    assert again.stdout == matrix.stdout
