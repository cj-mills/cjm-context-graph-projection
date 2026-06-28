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
