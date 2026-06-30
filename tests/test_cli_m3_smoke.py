"""CLI dispatch smoke for `m3-baseline` — guards the import wiring the unit tests can't see.

A pure-unit test imports `m3_baseline_import` from `journal` directly, so it stays green even
if `cli` forgets to import the name into its own namespace (which broke the real `m3-baseline`
run with a NameError at dispatch). This drives the actual CLI end-to-end in a subprocess, so the
dispatch path's imports are exercised for real.
"""
import json
import subprocess
import sys

from cjm_context_graph_projection.journal import read_journal


def _run(*args):
    return subprocess.run([sys.executable, "-m", "cjm_context_graph_projection.cli", *args],
                          capture_output=True, text=True)


def test_m3_baseline_cli_dispatches_and_journals(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    (mem / "feedback_demo.md").write_text("---\nname: demo-note\ndescription: d\n---\n\nbody\n")
    db = str(tmp_path / "dev.db")
    journal = str(tmp_path / "writes.jsonl")

    r = _run("--graph-db-path", db, "--journal-path", journal,
             "m3-baseline", "--memory-dir", str(mem), "--slug", "demo-note")
    assert r.returncode == 0, f"m3-baseline dispatch failed: {r.stderr or r.stdout}"
    ops = read_journal(journal)
    assert [o["verb"] for o in ops] == ["new-note"]
    assert ops[0]["args"]["actor"] == "import:m3-baseline"
    assert ops[0]["args"]["content"] == (mem / "feedback_demo.md").read_text()


def test_m3_baseline_cli_requires_journal(tmp_path):
    db = str(tmp_path / "dev.db")
    r = _run("--graph-db-path", db, "m3-baseline", "--slug", "x")
    assert r.returncode != 0 and "journal" in (r.stderr + r.stdout).lower()
