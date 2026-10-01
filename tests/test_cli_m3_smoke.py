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
from conftest import commit_all

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
    commit_all(emit)   # the archive is HEAD (19edbe97)
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
    # APPROVAL BINDS TO CONTENT (design 40622922): the published op journaled the hash it
    # approved; an edit after publication demotes BY DERIVATION (nothing written) and the
    # emit refuses until a person re-asserts published — which supersedes the stale approval.
    pub_ops = [o for o in read_journal(journal) if o["verb"] == "assert"
               and o["args"]["value"] == "published"]
    assert len(pub_ops) == 1 and pub_ops[0]["args"]["subject_content_hash"]
    r = _run("--graph-db-path", db, "--journal-path", journal, "add-section", "gate-post",
             "--content", "## Later\n\nAdded after publication.\n")
    assert r.returncode == 0, r.stderr or r.stdout
    landed.unlink()
    r = run_emit()                                                # edited -> refused
    assert r.returncode != 0 and "changed since approval" in (r.stdout + r.stderr)
    assert not landed.exists()
    r = _run("--graph-db-path", db, "--journal-path", journal,
             "assert", note_id, "publish_state", "published")   # re-approval of NEW content
    assert r.returncode == 0, r.stderr or r.stdout
    r = run_emit()                                                # re-published -> lands
    assert r.returncode == 0, r.stderr or r.stdout
    assert "## Later" in landed.read_text() and landed.read_text() == (emit / "gate-post" / "index.md").read_text()
    pub_ops = [o for o in read_journal(journal) if o["verb"] == "assert"
               and o["args"]["value"] == "published"]
    assert len(pub_ops) == 2 and pub_ops[0]["args"]["subject_content_hash"] != pub_ops[1]["args"]["subject_content_hash"]
    # The publish chain is journaled: a replay-only projection still emits
    r = _run("--graph-db-path", str(tmp_path / "replay.db"), "--journal-path", journal, "replay")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run("--graph-db-path", str(tmp_path / "replay.db"), "emit-post", note_id, "--no-write")
    assert r.returncode == 0 and "gate-post" in r.stdout, r.stderr or r.stdout   # dry-run verdict


def test_assert_batch_cli_validates_first_then_journals_one_assert_per_line(tmp_path):
    # assert-batch (the site_path pass, ruling 96aff70e): a malformed file refuses before ANY
    # write; a good one journals one ORDINARY assert op per line, superseded_by riding only
    # on the lines that set it, so replay needs no new op kind.
    db, journal = str(tmp_path / "dev.db"), str(tmp_path / "writes.jsonl")
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"subject": "fixture-page", "predicate": "site_path", "value": "/posts/x/"}\n'
                   '{"subject": "fixture-page"}\n')
    r = _run("--graph-db-path", db, "--journal-path", journal, "assert-batch", str(bad))
    assert r.returncode == 2 and "line 2 lacks predicate, value" in r.stderr
    assert not Path(journal).exists() or read_journal(journal) == []

    good = tmp_path / "good.jsonl"
    lines = [{"subject": "fixture-page", "predicate": "site_path", "value": "/posts/x/"},
             {"subject": "fixture-page", "predicate": "site_path", "value": "/Notes-on-X/",
              "superseded_by": ["/posts/x/"]}]
    good.write_text("".join(json.dumps(line) + "\n" for line in lines))
    r = _run("--graph-db-path", db, "--journal-path", journal, "assert-batch", str(good))
    assert r.returncode == 0 and "2 of 2 landed" in r.stdout, r.stderr or r.stdout
    ops = read_journal(journal)
    assert [o["verb"] for o in ops] == ["assert", "assert"]
    assert "superseded_by" not in ops[0]["args"]
    assert ops[1]["args"]["superseded_by"] == ["/posts/x/"]


def test_born_post_location_derives_from_emit_root_on_replay(tmp_path):
    # DEC 98293e72 (1): a born post journals its slug, never a machine path, and replay
    # DERIVES the file location from the config's emit root — so moving the drafts tree is a
    # config change: the replayed Note records the NEW location, its ids unchanged. A legacy
    # op still carrying an absolute path is ignored the same way; with no emit root the post
    # projects UNPLACED (same ids, no location) rather than onto the journaled tree.
    import sqlite3
    old, new = tmp_path / "old" / "posts", tmp_path / "site" / "drafts" / "posts"
    journal = str(tmp_path / "notes.writes.jsonl")
    cfg = tmp_path / "graph.config.json"
    cfg.write_text(json.dumps({"notes_profile": "quarto_post", "emit_root": str(old)}))
    content = "---\ntitle: \"Moved post\"\ndate: 2026-09-28\n---\n\nLede.\n\n## One\n\nBody.\n"
    r = _run("--graph-db-path", str(tmp_path / "notes.db"), "--journal-path", journal,
             "new-note", "--slug", "work/moved-post", "--content", content)
    assert r.returncode == 0, r.stderr or r.stdout
    op = read_journal(journal)[0]["args"]
    assert op["slug"] == "work/moved-post" and "path" not in op   # no machine path journaled

    def note(db):
        con = sqlite3.connect(str(db))
        try:
            rows = con.execute("select id, properties from nodes where label = 'Note'").fetchall()
            ids = sorted(r[0] for r in con.execute("select id from nodes"))
        finally:
            con.close()
        assert len(rows) == 1
        return rows[0][0], json.loads(rows[0][1])["path"], ids

    nid, path, ids = note(tmp_path / "notes.db")
    assert path == str((old / "work" / "moved-post" / "index.md").resolve())
    # The tree moves: the config names the new root, the journal is untouched
    cfg.write_text(json.dumps({"notes_profile": "quarto_post", "emit_root": str(new)}))
    r2 = _run("--graph-db-path", str(tmp_path / "replay.db"), "--journal-path", journal, "replay")
    assert r2.returncode == 0, r2.stderr or r2.stdout
    nid2, path2, ids2 = note(tmp_path / "replay.db")
    assert nid2 == nid and ids2 == ids
    assert path2 == str((new / "work" / "moved-post" / "index.md").resolve())
    # A legacy op (absolute path journaled beside the slug) derives the same way
    legacy = str(tmp_path / "legacy.writes.jsonl")
    Path(legacy).write_text(json.dumps({"verb": "new-note", "args": dict(
        op, path=str(old / "work" / "moved-post" / "index.md"))}) + "\n")
    r3 = _run("--graph-db-path", str(tmp_path / "legacy.db"), "--journal-path", legacy, "replay")
    assert r3.returncode == 0, r3.stderr or r3.stdout
    nid3, path3, ids3 = note(tmp_path / "legacy.db")
    assert nid3 == nid and path3 == path2
    # No emit root: the born post projects unplaced — the same ids, no file location
    cfg.write_text(json.dumps({"notes_profile": "quarto_post"}))
    r4 = _run("--graph-db-path", str(tmp_path / "none.db"), "--journal-path", legacy, "replay")
    assert r4.returncode == 0, r4.stderr or r4.stdout
    nid4, path4, ids4 = note(tmp_path / "none.db")
    assert nid4 == nid and ids4 == ids3 and not path4
