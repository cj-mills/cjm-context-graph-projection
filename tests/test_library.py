"""The Library's provenance (design 5de7fae9, design leg 4a4ef27e): a work Entity with a form
from its slate; a unit keyed `<work>/<slug>` whose entity op lands its PART_OF; an output_class
vocabulary a DeliverableType names; an ARCHIVE deliverable's one DERIVED_FROM edge to its unit
or work (a born one refused); the survey planned and refused whole; a rebuild reproduces it."""

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
from cjm_dev_graph_schema.vocab import DevRelations
from cjm_markdown_decompose_core.extract import note_from_text
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from cjm_context_graph_projection import factlayer as F
from cjm_context_graph_projection.coverage import mint_entity, validate_entity
from cjm_context_graph_projection.library import (plan_survey, project_library, record_provenance,
                                                  record_work_member)
from cjm_context_graph_projection.purenotes import load_deliverable_type, mint_deliverable_type
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.write import assert_value
from conftest import commit_all

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()
pytestmark_graph = pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")


def test_the_library_records():
    assert validate_entity("work", "fastai-book", "Deep Learning for Coders", {"form": "book"}) is None
    assert "needs form" in validate_entity("work", "fastai-book", "D", {})
    assert "must be one of" in validate_entity("work", "fastai-book", "D", {"form": "podcast"})
    assert "carries no `/`" in validate_entity("work", "a/b", "D", {"form": "book"})
    for good in ("2009", "2010-03", "2023-07-06"):
        assert validate_entity("work", "w", "W", {"form": "book", "published": good}) is None, good
    for bad in ("2020-13", "2023-02-30", "07/06/2023", "2023-7-6"):
        assert "an ISO date" in validate_entity("work", "w", "W", {"form": "book", "published": bad}), bad
    assert validate_entity("work", "w", "W", {"form": "book", "isbn": "9781605982038"}) is None
    for bad in ("9781605982039", "978-1605982038", "1605982038"):
        assert "ISBN-13" in validate_entity("work", "w", "W", {"form": "book", "isbn": bad}), bad
    assert validate_entity("unit", "w/vol-1", "Vol 1", {"position": 1, "isbn": "9781804090107"}) is None
    assert validate_entity("unit", "fastai-book/chapter-1", "Chapter 1", {"position": 1}) is None
    for bad in ("chapter-1", "fastai-book/", "/chapter-1", "a/b/c"):
        assert "a unit key is" in validate_entity("unit", bad, "C", {"position": 1}), bad
    assert "needs position" in validate_entity("unit", "w/u", "U", {})
    assert validate_entity("output_class", "notes", "Notes", {"position": 4}) is None
    assert "needs position" in validate_entity("output_class", "notes", "Notes", {})


def _row(note, work, name, form="book", unit_key="", unit_name="", pos="", **kw):
    return {"note_id": note, "work_key": work, "work_name": name, "form": form, "author": kw.get("author", ""),
            "subtitle": "", "published": kw.get("published", ""), "isbn": kw.get("isbn", ""),
            "unit_key": unit_key, "unit_name": unit_name, "unit_position": pos, "unit_part": kw.get("part", ""),
            "unit_isbn": kw.get("unit_isbn", "")}


def test_the_survey_plan_and_its_refusals():
    ok = plan_survey([_row("n1", "fastai-book", "Fastai", unit_key="chapter-2", unit_name="Ch 2", pos="2"),
                      _row("n2", "fastai-book", "Fastai", unit_key="chapter-1", unit_name="Ch 1", pos="1"),
                      _row("n3", "chip-war", "Chip War", author="Chris Miller", published="2022-10-04",
                           isbn="9781982172008"),
                      _row("n4", "gmm", "The Great Mental Models", unit_key="volume-1", unit_name="Vol 1", pos="1",
                           unit_isbn="9781804090107")])
    assert ok["ok"], ok["errors"]
    assert [w["key"] for w in ok["works"]] == ["chip-war", "fastai-book", "gmm"]
    assert ok["works"][0]["fields"] == {"form": "book", "author": "Chris Miller", "published": "2022-10-04",
                                        "isbn": "9781982172008"}
    assert [u["key"] for u in ok["units"]] == ["fastai-book/chapter-1", "fastai-book/chapter-2", "gmm/volume-1"]
    assert ok["units"][2]["fields"] == {"position": 1, "isbn": "9781804090107"}
    assert {e["deliverable"]: e["source"] for e in ok["edges"]} == {
        "n1": "fastai-book/chapter-2", "n2": "fastai-book/chapter-1", "n3": "chip-war", "n4": "gmm/volume-1"}
    bad = plan_survey([
        _row("m1", "mixed", "M", unit_key="u1", unit_name="U1", pos="1"), _row("m2", "mixed", "M"),
        _row("b1", "bare", "B"), _row("b2", "bare", "B"),
        _row("c1", "clash", "C"), _row("c1", "clash", "C"),
        _row("d1", "drift", "D"), _row("d2", "drift", "D", form="talk", unit_key="x", unit_name="X", pos="1"),
        _row("p1", "pos", "P", unit_key="a", unit_name="A", pos="1"),
        _row("p2", "pos", "P", unit_key="b", unit_name="B", pos="1"),
        _row("y1", "yr", "Y", published="soon"), _row("k1", "keyless", "K", unit_name="No key", pos="1")])
    assert not bad["ok"]
    text = "\n".join(bad["errors"])
    for needle in ("`mixed`: some rows name a unit", "`bare`: 2 deliverables and no units",
                   "appears twice", "`drift` disagrees", "share position 1", "an ISO date", "a unit key is"):
        assert needle in text, needle
    assert "no column(s) form" in plan_survey([{"note_id": "n", "work_key": "w", "work_name": "W"}])["errors"][0]


def test_the_library_projection_and_its_refusals():
    w = lambda k, n: {"key": k, "id": f"W:{k}", "name": n, "form": "book"}
    u = lambda k, n, pos: {"key": k, "id": f"U:{k}", "name": n, "position": pos}
    ents = {"work": {"fastai": w("fastai", "Fastai"), "chip-war": w("chip-war", "Chip War")},
            "unit": {"fastai/ch-2": u("fastai/ch-2", "Ch 2", 2), "fastai/ch-1": u("fastai/ch-1", "Ch 1", 1)},
            "output_class": {"notes": {"key": "notes", "id": "C:n", "name": "Notes", "position": 4},
                             "resource": {"key": "resource", "id": "C:r", "name": "Resources", "position": 3}}}
    ents["work"]["fastai"]["id"] = entity_node_id("work", "fastai")
    ents["work"]["chip-war"]["id"] = entity_node_id("work", "chip-war")
    note = lambda slug, cls, origin="archive", kind="notes", public=True: {
        "slug": slug, "title": slug.upper(), "type": "t", "kind": kind, "origin": origin, "output_class": cls,
        "public": public}
    notes = {"a1": note("a1", "notes"), "a2": note("a2", "notes"), "b1": note("b1", "resource", "born", public=False),
             "b2": note("b2", "notes", "born"), "lost": note("lost", "notes"), "essay": note("essay", "", kind="work"),
             "tut": note("tut", "tutorial", kind="tutorial")}
    r = project_library(ents, [("a1", "U:fastai/ch-1"), ("a2", entity_node_id("work", "chip-war")),
                               ("essay", "U:fastai/ch-2"), ("x", "not-an-entity")],
                        {"R:src1": "U:fastai/ch-1", "R:col": entity_node_id("work", "fastai")},
                        {"b1": ["R:src1"], "b2": ["R:nowhere"]}, notes)
    assert [x["key"] for x in r["works"]] == ["chip-war", "fastai"]
    fastai = r["works"][1]
    assert [x["key"] for x in fastai["units"]] == ["fastai/ch-1", "fastai/ch-2"]
    ch1 = fastai["units"][0]
    # the resource class outranks notes; the born draft shows, marked not public
    assert [(o["slug"], o["public"]) for o in ch1["outputs"]] == [("b1", False), ("a1", True)]
    assert ch1["sources"] == ["R:src1"] and fastai["sources"] == ["R:col"]
    assert [o["slug"] for o in r["works"][0]["outputs"]] == ["a2"]
    assert {(x["deliverable"], x["reason"]) for x in r["refusals"]} == {
        ("b2", "source-unplaced"), ("lost", "unplaced"), ("essay", "classless")}
    assert not r["ok"] and [c["key"] for c in r["classes"]] == ["resource", "notes"]


def _post(slug: str) -> str:
    return f"---\ntitle: \"{slug}\"\ndate: 2024-01-01\ncategories: [notes]\n---\n\n## Overview\n\nBody.\n"


@pytestmark_graph
def test_works_units_provenance_and_the_output_class(tmp_path):
    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            notes = [note_from_text(f"/c/posts/{s}/index.md", _post(s), corpus_root="/c/posts", lossless=True)
                     for s in ("arch", "born")]
            nodes, edges = corpus_graph_elements(notes)
            await extend_graph(gx.queue, gx.graph_id, nodes, edges)
            no_class = await mint_deliverable_type(gx, "archive-notes", title="Archive notes", kind="notes",
                                                   origin="archive", output_class="notes")
            await mint_deliverable_type(gx, "archive-notes", title="Archive notes", kind="notes", origin="archive",
                                        presentation_policy={"kinds": {"k": "gloss"}})
            await mint_deliverable_type(gx, "lecture-notes", title="L", kind="notes", origin="born")
            await assert_value(gx, note_node_id("arch"), "deliverable_type", "archive-notes")
            await assert_value(gx, note_node_id("born"), "deliverable_type", "lecture-notes")
            await mint_entity(gx, "output_class", "notes", name="Notes", fields={"position": 4})
            classed = await mint_deliverable_type(gx, "archive-notes", output_class="notes")   # field-only
            typ = await load_deliverable_type(gx, "archive-notes")
            orphan = await mint_entity(gx, "unit", "fastai-book/chapter-1", name="Ch 1", fields={"position": 1})
            await mint_entity(gx, "work", "fastai-book", name="Fastai", fields={"form": "book"})
            unit = await mint_entity(gx, "unit", "fastai-book/chapter-1", name="Ch 1", fields={"position": 1})
            again = await mint_entity(gx, "unit", "fastai-book/chapter-1", name="Chapter 1", fields={"position": 1})
            part_of = [p for p in await F.load_edge_pairs(gx, "PART_OF") if p[0] == unit["entity_id"]]
            first = await record_provenance(gx, "arch", "fastai-book")
            same = await record_provenance(gx, "arch", "fastai-book")
            moved = await record_provenance(gx, note_node_id("arch"), "fastai-book/chapter-1")
            born = await record_provenance(gx, "born", "fastai-book")
            nowork = await record_provenance(gx, "arch", "nope")
            derived = [p for p in await F.load_edge_pairs(gx, DevRelations.DERIVED_FROM)
                       if p[0] == note_node_id("arch")]
            gone = await record_provenance(gx, "arch", retract=True, source="")
            twice = await record_provenance(gx, "arch", retract=True, source="")
            return (no_class, classed, typ, orphan, unit, again, part_of, first, same, moved, born, nowork,
                    derived, gone, twice)
    (no_class, classed, typ, orphan, unit, again, part_of, first, same, moved, born, nowork,
     derived, gone, twice) = asyncio.run(go())
    assert "no output class `notes`" in no_class["error"]
    # A field-only upsert keeps the profile it does not name
    assert classed["written"] and typ["output_class"] == "notes"
    assert typ["presentation_policy"] == {"kinds": {"k": "gloss"}} and typ["title"] == "Archive notes"
    assert typ["kind"] == "notes" and typ["origin"] == "archive"
    assert "no work `fastai-book`" in orphan["error"]
    assert again["updated"] and part_of == [(unit["entity_id"], entity_node_id("work", "fastai-book"))]
    assert first["written"] and not first["replaced"]
    assert same.get("unchanged") and not same["written"]
    assert moved["replaced"] and moved["edge_id"] == first["edge_id"]
    assert derived == [(note_node_id("arch"), entity_node_id("unit", "fastai-book/chapter-1"))]
    assert "not an archive deliverable" in born["error"] and "rollup" in born["error"]
    assert "no work `nope`" in nowork["error"]
    assert gone["retracted"] and "no provenance edge" in twice["error"]


@pytestmark_graph
def test_a_metabolized_source_takes_its_place_in_the_work(tmp_path):
    from cjm_dev_graph_schema.nodes import ReferenceNode

    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            src = ReferenceNode(graph="transcription", foreign_id="s-1", foreign_label="Source", title="Ch 1")
            col = ReferenceNode(graph="transcription", foreign_id="c-1", foreign_label="Collection", title="Book")
            seg = ReferenceNode(graph="transcription", foreign_id="g-1", foreign_label="Segment", title="seg")
            await extend_graph(gx.queue, gx.graph_id, [r.to_graph_node() for r in (src, col, seg)], [])
            await mint_entity(gx, "work", "book", name="Book", fields={"form": "book"})
            for n in (1, 2):
                await mint_entity(gx, "unit", f"book/ch-{n}", name=f"Ch {n}", fields={"position": n})
            seg_refused = await record_work_member(gx, seg.id, "book/ch-1")
            col_unit = await record_work_member(gx, col.id, "book/ch-1")
            col_work = await record_work_member(gx, col.id, "book")
            placed = await record_work_member(gx, src.id, "book/ch-1")
            same = await record_work_member(gx, src.id, "book/ch-1")
            moved = await record_work_member(gx, src.id, "book/ch-2")
            nounit = await record_work_member(gx, src.id, "book/ch-9")
            pairs = sorted(p for p in await F.load_edge_pairs(gx, "PART_OF") if p[0] in (src.id, col.id))
            gone = await record_work_member(gx, col.id, retract=True)
            return seg_refused, col_unit, col_work, placed, same, moved, nounit, pairs, gone, src, col
    seg_refused, col_unit, col_work, placed, same, moved, nounit, pairs, gone, src, col = asyncio.run(go())
    assert "no Source or Collection Reference" in seg_refused["error"]
    assert "never a unit" in col_unit["error"] and col_work["written"]
    assert placed["written"] and same.get("unchanged") and moved["replaced"]
    assert moved["edge_id"] == placed["edge_id"] and "no unit `book/ch-9`" in nounit["error"]
    assert pairs == sorted([(src.id, entity_node_id("unit", "book/ch-2")), (col.id, entity_node_id("work", "book"))])
    assert gone["retracted"]


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
def test_a_rebuild_reproduces_the_library(tmp_path):
    corpus = tmp_path / "posts"
    for s in ("a", "b", "c"):
        (corpus / s).mkdir(parents=True)
        (corpus / s / "index.md").write_text(_post(s))
    commit_all(corpus)
    journal = str(tmp_path / "writes.jsonl")
    for sub in ("live", "fresh"):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "graph.config.json").write_text(json.dumps(
            {"notes_corpus": str(corpus), "notes_profile": "quarto_post"}))
    live = str(tmp_path / "live" / "g.db")
    base = ["--graph-db-path", live, "--journal-path", journal]
    assert _run(*base, "ingest-notes").returncode == 0
    header = "\t".join(("note_id", "work_key", "work_name", "form", "author", "subtitle", "published",
                        "unit_key", "unit_name", "unit_position", "unit_part"))
    table = tmp_path / "survey.tsv"
    table.write_text("\n".join([
        header,
        f"{note_node_id('a')}\tgpu-mode\tGPU MODE\tlecture-series\t\t\t\tlecture-1\tLecture 1\t1\t",
        f"{note_node_id('b')}\tgpu-mode\tGPU MODE\tlecture-series\t\t\t\tlecture-2\tLecture 2\t2\t",
        f"{note_node_id('c')}\tchip-war\tChip War\tbook\tChris Miller\t\t2022\t\t\t\t"]) + "\n")
    for cmd in (["notes-type", "archive-notes", "--title", "Archive notes", "--kind", "notes",
                 "--origin", "archive"],
                ["entity", "output_class", "notes", "--name", "Notes", "--position", "4"],
                ["notes-type", "archive-notes", "--output-class", "notes"],
                ["assert", note_node_id("a"), "deliverable_type", "archive-notes"],
                ["assert", note_node_id("b"), "deliverable_type", "archive-notes"]):
        r = _run(*base, *cmd)
        assert r.returncode == 0, (cmd, r.stdout, r.stderr)
    # c is untyped: the whole batch refuses, nothing written
    refused = _run(*base, "library-survey", str(table), "--apply")
    assert refused.returncode == 1 and "REFUSED" in refused.stdout and "not an archive deliverable" in refused.stderr
    assert _run(*base, "assert", note_node_id("c"), "deliverable_type", "archive-notes").returncode == 0
    dry = _run(*base, "library-survey", str(table))
    assert dry.returncode == 0 and "2 work(s) · 2 unit(s) · 3 provenance edge(s)" in dry.stdout
    assert "dry run" in dry.stdout
    verbs = [json.loads(line)["verb"] for line in Path(journal).read_text().splitlines()]
    assert "entity" not in verbs[2:] and "derived-from" not in verbs     # dry runs and refusals journal nothing
    applied = _run(*base, "library-survey", str(table), "--apply")
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert "4 entity op(s) · 3 provenance edge(s) · 0 unchanged" in applied.stdout
    rerun = _run(*base, "library-survey", str(table), "--apply")
    assert "0 provenance edge(s) · 3 unchanged" in rerun.stdout
    for cmd in (["derived-from", "c", "gpu-mode/lecture-1"], ["derived-from", "c", "chip-war"],
                ["derived-from", "b", "--retract"]):
        r = _run(*base, *cmd)
        assert r.returncode == 0, (cmd, r.stdout, r.stderr)
    assert _run(*base, "derived-from", "a", "nope").returncode == 1

    fresh = str(tmp_path / "fresh" / "g.db")
    r = _run("--graph-db-path", fresh, "--journal-path", journal, "ingest-notes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _ids(fresh) == _ids(live)
    con = sqlite3.connect(fresh)
    try:
        props = [json.loads(p) for (p,) in con.execute(
            "select properties from nodes where label = 'DeliverableType'")]
    finally:
        con.close()
    assert [p.get("output_class") for p in props] == ["notes"]
