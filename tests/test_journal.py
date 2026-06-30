"""The write journal: append (with de-dup) + read, the pure half (no graph)."""

import json

from cjm_context_graph_projection.journal import append_write, read_journal
from cjm_context_graph_projection.render import render


def test_read_missing_journal_is_empty(tmp_path):
    assert read_journal(str(tmp_path / "nope.jsonl")) == []


def test_append_and_read_roundtrip(tmp_path):
    p = str(tmp_path / "writes.jsonl")
    assert append_write(p, "decide", {"statement": "x", "actor": "a"}) is True
    assert append_write(p, "alias", {"drifted": "d", "canonical": "c"}) is True
    ops = read_journal(p)
    assert [o["verb"] for o in ops] == ["decide", "alias"]
    assert ops[0]["args"] == {"statement": "x", "actor": "a"}
    assert all("ts" in o for o in ops)  # each op is timestamped


def test_append_skips_exact_duplicate(tmp_path):
    p = str(tmp_path / "writes.jsonl")
    args = {"drifted": "d", "canonical": "c", "actor": "a"}
    assert append_write(p, "alias", args) is True
    assert append_write(p, "alias", args) is False   # identical -> not re-appended
    assert append_write(p, "alias", {**args, "actor": "b"}) is True  # different -> appended
    assert len(read_journal(p)) == 2


def test_journal_lines_are_valid_jsonl(tmp_path):
    p = str(tmp_path / "writes.jsonl")
    append_write(p, "assert", {"subject": "s", "predicate": "version", "value": "0.0.1"})
    lines = (tmp_path / "writes.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["verb"] == "assert"


def test_link_verb_is_journaled():
    from cjm_context_graph_projection.journal import JOURNAL_VERBS
    assert "link" in JOURNAL_VERBS  # link writes are durable/replayable, like decide/alias/assert


def test_link_append_roundtrip(tmp_path):
    p = str(tmp_path / "writes.jsonl")
    args = {"source_id": "dec-1", "target_id": "sym-1", "relation": "IMPLEMENTED_BY",
            "actor": "human"}
    assert append_write(p, "link", args) is True
    ops = read_journal(p)
    assert ops[0]["verb"] == "link" and ops[0]["args"] == args


def test_render_link_human_and_error():
    ok = {"source_id": "dec-1", "target_id": "sym-1", "relation": "IMPLEMENTED_BY",
          "actor": "human", "edge_id": "e-1", "written": True}
    out = render("link", ok, "human")
    assert "IMPLEMENTED_BY" in out and "dec-1" in out and "sym-1" in out
    err = render("link", {"error": "missing node(s): ['x']", "written": False}, "human")
    assert "missing node(s)" in err
    assert render("link", ok, "agent").strip().startswith("{")  # agent form = JSON


def test_section_verb_is_journaled():
    from cjm_context_graph_projection.journal import JOURNAL_VERBS
    assert "section" in JOURNAL_VERBS  # M2b: memory section raw STATE is durable/replayable


def test_section_append_roundtrip_carries_replaces(tmp_path):
    p = str(tmp_path / "writes.jsonl")
    # a deliberate author op + a self-describing reconcile:absorb op (carries `replaces`).
    assert append_write(p, "section", {"slug": "n", "anchor": "beta",
                                       "raw": "## Beta\n\nnew\n", "actor": "agent:session"}) is True
    assert append_write(p, "section", {"slug": "n", "anchor": "beta", "raw": "## Beta\n\nfile\n",
                                       "actor": "reconcile:absorb",
                                       "replaces": "## Beta\n\nnew\n"}) is True
    ops = read_journal(p)
    assert [o["verb"] for o in ops] == ["section", "section"]
    assert ops[1]["args"]["actor"] == "reconcile:absorb"
    assert ops[1]["args"]["replaces"] == "## Beta\n\nnew\n"  # prior state recorded for undo


def test_new_note_verb_is_journaled():
    from cjm_context_graph_projection.journal import JOURNAL_VERBS
    assert "new-note" in JOURNAL_VERBS  # M3: a whole note's baseline text is durable/replayable


def test_m3_baseline_paths_filters_by_actor(tmp_path):
    from cjm_context_graph_projection.journal import M3_BASELINE_ACTOR, m3_baseline_paths
    p = str(tmp_path / "writes.jsonl")
    a = str((tmp_path / "a.md").resolve())
    b = str((tmp_path / "b.md").resolve())
    append_write(p, "new-note", {"path": a, "content": "x\n", "actor": M3_BASELINE_ACTOR})
    # a non-genesis new-note op (a later born-on-graph note) is NOT a flip target.
    append_write(p, "new-note", {"path": b, "content": "y\n", "actor": "agent:session"})
    assert m3_baseline_paths(p) == [a]  # only the import:m3-baseline op flips ingest off the .md


def test_m3_baseline_import_emits_baseline_and_is_idempotent(tmp_path):
    from cjm_context_graph_projection.journal import (M3_BASELINE_ACTOR, m3_baseline_import,
                                                      m3_baseline_paths, read_journal)
    mem = tmp_path / "memory"
    mem.mkdir()
    note = mem / "feedback_demo.md"
    note.write_text("---\nname: demo-note\ndescription: d\n---\n\nbody\n")
    journal = str(tmp_path / "writes.jsonl")

    r1 = m3_baseline_import(str(mem), journal, slugs=["demo-note"])
    assert r1["imported_count"] == 1 and not r1["unknown"]
    ops = read_journal(journal)
    assert ops[0]["verb"] == "new-note"
    assert ops[0]["args"]["actor"] == M3_BASELINE_ACTOR
    assert ops[0]["args"]["content"] == note.read_text()  # EXACT baseline bytes captured
    assert m3_baseline_paths(journal) == [str(note.resolve())]

    # Re-running is a no-op (already has a baseline op); an unknown slug is reported, not raised.
    r2 = m3_baseline_import(str(mem), journal, slugs=["demo-note", "ghost"])
    assert r2["imported_count"] == 0 and r2["skipped_existing"] == ["demo-note"]
    assert r2["unknown"] == ["ghost"]
    assert len(read_journal(journal)) == 1  # journal unchanged


def test_render_structure_human():
    nn = render("structure", {"slug": "n", "path": "/n.md", "sections": 3, "written": True}, "human")
    assert "created note" in nn and "n" in nn
    add = render("structure", {"slug": "n", "added": ["gamma"], "updated": ["beta"],
                               "removed": [], "frontmatter_changed": False, "written": True}, "human")
    assert "gamma" in add and "beta" in add
    err = render("structure", {"error": "no note `x`", "written": False}, "human")
    assert "no note" in err


def test_render_reconcile_memory_human():
    clean = render("reconcile-memory", {"clean": True, "notes_with_drift": 0,
                                        "absorbed_count": 0, "drift": [], "absorbed": []}, "human")
    assert "clean" in clean
    drifty = render("reconcile-memory", {"clean": False, "notes_with_drift": 1, "absorbed_count": 1,
        "drift": [{"slug": "n", "path": "/n.md", "added": [], "removed": [],
                   "changed": [{"anchor": "beta", "graph": "old", "file": "new"}]}],
        "absorbed": [{"slug": "n", "anchor": "beta", "backup": "/n.md.bak",
                      "prior_bytes": 3, "new_bytes": 3}]}, "human")
    assert "beta" in drifty and "absorbed" in drifty
