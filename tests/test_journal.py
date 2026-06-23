"""The write journal: append (with de-dup) + read, the pure half (no graph)."""

import json

from cjm_context_graph_projection.journal import append_write, read_journal


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
