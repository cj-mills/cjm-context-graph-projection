"""CLI dispatch smoke for `m3-baseline` — guards the import wiring the unit tests can't see.

A pure-unit test imports `m3_baseline_import` from `journal` directly, so it stays green even
if `cli` forgets to import the name into its own namespace (which broke the real `m3-baseline`
run with a NameError at dispatch). This drives the actual CLI end-to-end in a subprocess, so the
dispatch path's imports are exercised for real.
"""
import json
import subprocess
import sys

from pathlib import Path

import pytest

from cjm_context_graph_primitives.journal import read_journal
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS

# Integration smoke: drives the real CLI, which needs the graph-storage worker
# capability installed. Skip wherever its manifest isn't discoverable (e.g. CI).
pytestmark = pytest.mark.skipif(
    not (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists(),
    reason=f"graph capability {DEFAULT_GRAPH_ID!r} not installed at {DEFAULT_MANIFESTS}",
)


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


def test_new_note_cli_journals_natively(tmp_path):
    # A note BORN on-graph via `new-note` journals its OWN genesis op (actor agent:session,
    # not m3-baseline) so it is journal-sourced from birth — no post-hoc m3-baseline needed.
    mem = tmp_path / "memory"
    mem.mkdir()
    note = mem / "born_demo.md"
    db = str(tmp_path / "dev.db")
    journal = str(tmp_path / "writes.jsonl")
    content = "---\nname: born-demo\ndescription: d\n---\n\nbody\n"

    r = _run("--graph-db-path", db, "--journal-path", journal,
             "new-note", "--path", str(note), "--content", content)
    assert r.returncode == 0, f"new-note dispatch failed: {r.stderr or r.stdout}"
    ops = read_journal(journal)
    assert [o["verb"] for o in ops] == ["new-note"]
    assert ops[0]["args"]["actor"] == "agent:session"      # born on-graph, NOT m3-baseline
    assert ops[0]["args"]["content"] == note.read_text()    # exact written bytes captured


def test_m3_baseline_cli_requires_journal(tmp_path):
    db = str(tmp_path / "dev.db")
    r = _run("--graph-db-path", db, "m3-baseline", "--slug", "x")
    assert r.returncode != 0 and "journal" in (r.stderr + r.stdout).lower()


def test_decide_state_open_mints_and_asserts_in_one_invocation(tmp_path):
    # The frontier-visibility enforcement: a work item minted with --state open journals
    # BOTH ops (decide + assert task_state=open), so it is never invisible to readiness.
    db = str(tmp_path / "dev.db")
    journal = str(tmp_path / "writes.jsonl")
    r = _run("--graph-db-path", db, "--journal-path", journal,
             "decide", "WORK ITEM: smoke", "--title", "WORK ITEM: smoke", "--state", "open")
    assert r.returncode == 0, f"decide --state dispatch failed: {r.stderr or r.stdout}"
    ops = read_journal(journal)
    assert [o["verb"] for o in ops] == ["decide", "assert"]
    assert ops[1]["args"]["predicate"] == "task_state"
    assert ops[1]["args"]["value"] == "open"
    assert ops[1]["args"]["subject"]  # the freshly minted decision id


def test_link_resolves_id_prefixes_and_journals_resolved_ids(tmp_path):
    # The 66fffba6 asymmetry fix: link accepts unique id PREFIXES like every read verb,
    # and the journal records the RESOLVED full ids (replay must not depend on a prefix).
    db = str(tmp_path / "dev.db")
    journal = str(tmp_path / "writes.jsonl")
    base = ("--graph-db-path", db, "--journal-path", journal, "--format", "agent")
    a = json.loads(_run(*base, "decide", "alpha decision").stdout)["decision_id"]
    b = json.loads(_run(*base, "decide", "beta decision").stdout)["decision_id"]
    r = _run(*base, "link", a[:8], "REFERENCES", b[:8])
    assert r.returncode == 0, f"prefix link failed: {r.stderr or r.stdout}"
    op = [o for o in read_journal(journal) if o["verb"] == "link"][-1]
    assert op["args"]["source_id"] == a and op["args"]["target_id"] == b
    # A prefix matching nothing stays a loud miss (never a guess, never journaled).
    miss = _run(*base, "link", "deadbeef", "REFERENCES", b)
    assert miss.returncode != 0
    assert len([o for o in read_journal(journal) if o["verb"] == "link"]) == 1
