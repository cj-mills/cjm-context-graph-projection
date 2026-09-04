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


def test_decide_capture_asserts_capture_state_and_links_the_ridden_item(tmp_path):
    """a3d196c6 shape (a): `decide --capture` journals decide + assert capture_state in one
    invocation; `riding:<prefix>` resolves the item, journals the FULL id in the value and
    a REFERENCES link; a bad spec / --state+--capture together refuse BEFORE minting."""
    db = str(tmp_path / "dev.db")
    journal = str(tmp_path / "writes.jsonl")
    base = ("--graph-db-path", db, "--journal-path", journal)
    r = _run(*base, "decide", "WORK ITEM: host", "--title", "WORK ITEM: host", "--state", "open")
    assert r.returncode == 0, r.stderr or r.stdout
    host = read_journal(journal)[1]["args"]["subject"]
    r = _run(*base, "decide", "CAPTURE: seed", "--capture", "seed")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run(*base, "decide", "CAPTURE: rider", "--capture", f"riding:{host[:8]}")
    assert r.returncode == 0, r.stderr or r.stdout
    ops = read_journal(journal)
    assert [o["verb"] for o in ops] == ["decide", "assert", "decide", "assert", "decide",
                                        "assert", "link"]
    assert ops[3]["args"] == {**ops[3]["args"], "predicate": "capture_state", "value": "seed"}
    assert ops[5]["args"]["value"] == f"riding:{host}"  # full id, never the prefix
    assert ops[6]["args"]["relation"] == "REFERENCES" and ops[6]["args"]["target_id"] == host
    before = len(ops)
    bad = _run(*base, "decide", "CAPTURE: bad", "--capture", "someday")
    assert bad.returncode != 0 and "--capture expects" in bad.stderr
    both = _run(*base, "decide", "X", "--state", "open", "--capture", "seed")
    assert both.returncode == 2 and "OR --capture" in both.stderr
    assert len(read_journal(journal)) == before  # refused specs mint nothing


def test_new_note_born_post_round_trips_with_ingest_notes(tmp_path):
    # a42c0f97: a POST born via `new-note --slug` (profile + emit_root from the notes db's
    # sibling config) lands as <emit_root>/<slug>/index.md with its permalink identity,
    # journals profile + slug, and the emitted tree RE-INGESTS to identical node/edge ids
    # (replay-only projection == ingest-notes projection: the round-trip standard).
    import sqlite3
    emit = tmp_path / "emit"
    journal = str(tmp_path / "notes.writes.jsonl")
    (tmp_path / "graph.config.json").write_text(json.dumps(
        {"notes_profile": "quarto_post", "emit_root": str(emit), "notes_corpus": str(emit)}))
    content = ("---\ntitle: \"Born post\"\ndate: 2026-09-03\ncategories: [notes, graph]\n---\n\n"
               "Lede paragraph.\n\n## First\n\nBody one.\n\n### Nested\n\nBody two.\n")
    r = _run("--graph-db-path", str(tmp_path / "notes.db"), "--journal-path", journal,
             "new-note", "--slug", "series/born-post", "--content", content)
    assert r.returncode == 0, f"new-note dispatch failed: {r.stderr or r.stdout}"
    assert (emit / "series" / "born-post" / "index.md").read_text() == content
    ops = read_journal(journal)
    assert [o["verb"] for o in ops] == ["new-note", "assert"]  # draft at birth (793f025e)
    assert ops[0]["args"]["slug"] == "series/born-post"       # nested permalink pinned
    assert ops[0]["args"]["profile"] == "quarto_post"          # harvest profile rides the op

    def ids(db):
        # CONTENT ids only: the publish_state fact born beside the post (FactSlot +
        # Assertion + ABOUT edges) is enrichment an archive ingest never produces.
        content = "('Note','Section','Topic','Series')"
        con = sqlite3.connect(str(db))
        try:
            return (sorted(r[0] for r in con.execute(f"select id from nodes where label in {content}")),
                    sorted(r[0] for r in con.execute(
                        "select e.id from edges e join nodes s on s.id = e.source_id "
                        f"join nodes t on t.id = e.target_id where s.label in {content} "
                        f"and t.label in {content}")),
                    sorted(r[0] for r in con.execute(f"select label from nodes where label in {content}")))
        finally:
            con.close()

    # Journal-only projection (what a rebuild replays) vs. archive ingest of the emitted tree
    r2 = _run("--graph-db-path", str(tmp_path / "replay.db"), "--journal-path", journal, "replay")
    assert r2.returncode == 0, r2.stderr or r2.stdout
    r3 = _run("--graph-db-path", str(tmp_path / "ingest.db"), "ingest-notes")   # corpus from config
    assert r3.returncode == 0, r3.stderr or r3.stdout
    replayed, ingested = ids(tmp_path / "replay.db"), ids(tmp_path / "ingest.db")
    assert replayed[0] == ingested[0] and replayed[1] == ingested[1]
    assert "Topic" in replayed[2] and replayed[2].count("Section") == 3   # lede + 2 headings
    # Under quarto_post a title-less post is refused (the Quarto minimum)
    r4 = _run("--graph-db-path", str(tmp_path / "notes.db"), "--journal-path", journal,
              "new-note", "--slug", "untitled", "--content", "---\ndate: 2026-09-03\n---\n\nx\n")
    assert r4.returncode != 0 and "title" in (r4.stdout + r4.stderr)
    assert len(read_journal(journal)) == 2   # the refused post journaled nothing


def test_born_post_is_draft_at_birth_and_emit_post_gates_on_published(tmp_path):
    # Ruling 793f025e + item 6eba8815: a born post carries publish_state=draft from the
    # SAME invocation that minted it (journaled beside the new-note op); emit-post refuses
    # draft and reviewed; a published post lands at <website_root>/posts/<slug>/index.md
    # byte-identical to the staging file; the fact chain survives a journal replay.
    emit, site = tmp_path / "staging" / "posts", tmp_path / "site"
    db, journal = str(tmp_path / "notes.db"), str(tmp_path / "notes.writes.jsonl")
    (tmp_path / "graph.config.json").write_text(json.dumps(
        {"notes_profile": "quarto_post", "emit_root": str(emit), "website_root": str(site)}))
    content = ("---\ntitle: \"Gate\"\ndate: 2026-09-03\ncategories: [notes]\n---\n\n"
               "Lede.\n\n## Body\n\nText.\n")
    r = _run("--graph-db-path", db, "--journal-path", journal,
             "new-note", "--slug", "gate-post", "--content", content)
    assert r.returncode == 0, r.stderr or r.stdout
    ops = read_journal(journal)
    assert [o["verb"] for o in ops] == ["new-note", "assert"]
    assert ops[1]["args"]["predicate"] == "publish_state" and ops[1]["args"]["value"] == "draft"
    note_id = ops[1]["args"]["subject"]
    landed = site / "posts" / "gate-post" / "index.md"

    def run_emit():
        return _run("--graph-db-path", db, "--journal-path", journal, "emit-post", note_id)

    r = run_emit()                                                # draft -> refused
    assert r.returncode != 0 and "not published" in (r.stdout + r.stderr) and not landed.exists()
    r = _run("--graph-db-path", db, "--journal-path", journal,
             "assert", note_id, "publish_state", "reviewed")
    assert r.returncode == 0, r.stderr or r.stdout
    r = run_emit()                                                # reviewed -> refused
    assert r.returncode != 0 and not landed.exists()
    r = _run("--graph-db-path", db, "--journal-path", journal,
             "assert", note_id, "publish_state", "published")   # ordered: auto-supersedes
    assert r.returncode == 0, r.stderr or r.stdout
    r = run_emit()                                                # published -> lands
    assert r.returncode == 0, r.stderr or r.stdout
    assert landed.read_text() == content == (emit / "gate-post" / "index.md").read_text()
    # The publish chain is journaled: a replay-only projection still emits
    r = _run("--graph-db-path", str(tmp_path / "replay.db"), "--journal-path", journal, "replay")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run("--graph-db-path", str(tmp_path / "replay.db"), "emit-post", note_id, "--no-write")
    assert r.returncode == 0 and "gate-post" in r.stdout, r.stderr or r.stdout   # dry-run verdict
