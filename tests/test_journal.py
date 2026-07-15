"""The write journal: append (with de-dup) + read, the pure half (no graph)."""

import json

from cjm_context_graph_projection.journal import journal_window, touched_node_ids
from cjm_context_graph_primitives.journal import append_write, read_journal
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


def test_journal_sourced_note_paths_matches_any_actor(tmp_path):
    # The authority-flip skip key is the `new-note` OP, not its actor: a MIGRATED note
    # (import:m3-baseline) AND a note BORN on-graph via `new-note` (agent:session) are both
    # journal-sourced, so ingest must skip BOTH .md files. Actor is provenance, not the key.
    from cjm_context_graph_projection.journal import M3_BASELINE_ACTOR, journal_sourced_note_paths
    p = str(tmp_path / "writes.jsonl")
    a = str((tmp_path / "a.md").resolve())
    b = str((tmp_path / "b.md").resolve())
    append_write(p, "new-note", {"path": a, "content": "x\n", "actor": M3_BASELINE_ACTOR})
    append_write(p, "new-note", {"path": b, "content": "y\n", "actor": "agent:session"})
    assert journal_sourced_note_paths(p) == [a, b]  # both flip ingest off their .md


def test_m3_baseline_import_emits_baseline_and_is_idempotent(tmp_path):
    from cjm_context_graph_projection.journal import (M3_BASELINE_ACTOR, journal_sourced_note_paths,
                                                      m3_baseline_import, read_journal)
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
    assert journal_sourced_note_paths(journal) == [str(note.resolve())]

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


def test_append_write_stamps_session_from_env(tmp_path, monkeypatch):
    """The cutover mechanic (DEC 6124d8bf): CJM_SESSION stamps every append TOP-LEVEL;
    absent env = no stamp (pre-cutover shape); dedup stays session-blind."""
    p = str(tmp_path / "j.jsonl")
    monkeypatch.delenv("CJM_SESSION", raising=False)
    assert append_write(p, "link", {"source_id": "a", "target_id": "b", "relation": "R"})
    monkeypatch.setenv("CJM_SESSION", "2026-07-08_10-58-13")
    assert append_write(p, "link", {"source_id": "c", "target_id": "d", "relation": "R"})
    # dedup compares (verb, args) only — the same op in a NEW session is still a duplicate
    assert not append_write(p, "link", {"source_id": "a", "target_id": "b", "relation": "R"})
    ops = read_journal(p)
    assert len(ops) == 2
    assert "session" not in ops[0]
    assert ops[1]["session"] == "2026-07-08_10-58-13"


def test_touched_node_ids_per_verb():
    """The session-lens feed derives TOUCHES per verb: id-shaped args collected,
    natural keys re-derived exactly as the verbs derive them, names omitted."""
    assert touched_node_ids({"verb": "link", "args": {
        "source_id": "aaa111", "target_id": "bbb222", "relation": "R"}}) == ["aaa111", "bbb222"]
    assert touched_node_ids({"verb": "check", "args": {"item_id": "ccc333"}}) == ["ccc333"]
    # an assert on a NAME-shaped subject contributes nothing; id-shaped does
    assert touched_node_ids({"verb": "assert", "args": {"subject": "TaskAdapter"}}) == []
    assert touched_node_ids({"verb": "assert", "args": {"subject": "2026-06-25"}}) == []
    assert touched_node_ids({"verb": "assert", "args": {"subject": "60aae839"}}) == ["60aae839"]
    # a decide derives its deterministic Decision id (+ its session node when tagged)
    got = touched_node_ids({"verb": "decide", "args": {"statement": "S.", "session": "k"}})
    assert len(got) == 2 and all(len(x) == 36 for x in got)
    # a source-journal op maps to its CodeModule
    got = touched_node_ids({"verb": "source", "args": {
        "repo_key": "r", "module_path": "p/m.py", "import_name": "p.m", "text": "x = 1"}})
    assert len(got) == 1 and len(got[0]) == 36
    # a session op maps to its Session node
    assert len(touched_node_ids({"verb": "session", "args": {"key": "2026-07-08_10-58-13"}})) == 1


def test_journal_window_time_and_session_filters(tmp_path, monkeypatch):
    """The declarative window (lens invariant 1): time bounds, open end = live,
    session filter spans the stamp AND the pre-cutover decide args fallback."""
    p = str(tmp_path / "j.jsonl")
    monkeypatch.delenv("CJM_SESSION", raising=False)
    append_write(p, "link", {"source_id": "aaa111", "target_id": "bbb222", "relation": "R"})
    append_write(p, "decide", {"statement": "Old.", "session": "old-key"})  # pre-cutover shape
    monkeypatch.setenv("CJM_SESSION", "new-key")
    append_write(p, "check", {"item_id": "ccc333", "text": "t"})
    ops = read_journal(p)

    win = journal_window([p])  # open both ends = everything
    assert win["entries"] == 3
    refs = {t["ref"] for t in win["touched"]}
    assert {"aaa111", "bbb222", "ccc333"} <= refs

    win = journal_window([p], end=ops[0]["ts"])  # closed end excludes later ops
    assert win["entries"] == 1

    win = journal_window([p], start=ops[-1]["ts"])  # open end = live tail
    assert win["entries"] == 1 and win["touched"][0]["ref"] == "ccc333"

    # session filter: the top-level stamp AND a decide's args.session both match
    assert journal_window([p], session="new-key")["entries"] == 1
    assert journal_window([p], session="old-key")["entries"] == 1
    assert journal_window([p], session="nope")["entries"] == 0

    # WINDOW resolution (DEC 6124d8bf leg 2): a registered timestamp-keyed session
    # matches HISTORICAL (untagged) ops by [started_at, next start) — and the last
    # registered session's window is open (in progress)
    monkeypatch.delenv("CJM_SESSION", raising=False)
    t0, mid = ops[0]["ts"], (ops[1]["ts"] + ops[2]["ts"]) / 2
    append_write(p, "session", {"key": "w1", "started_at": t0 - 1.0})
    append_write(p, "session", {"key": "w2", "started_at": mid})
    w1 = journal_window([p], session="w1")
    assert w1["entries"] == 2  # ops 0+1 fall in [t0-1, mid), tagged or not
    assert w1["session_window"] == {"start": t0 - 1.0, "end": mid}
    w2 = journal_window([p], session="w2")
    assert w2["session_window"]["end"] is None  # last registered = open, in progress
    assert w2["entries"] == 3  # op 2 + the two session registrations themselves

    # touch aggregation: repeat touches accumulate per verb, last_ts advances
    append_write(p, "check", {"item_id": "ccc333", "text": "t2"})
    rec = next(t for t in journal_window([p])["touched"] if t["ref"] == "ccc333")
    assert rec["touches"] == 2 and rec["verbs"] == {"check": 2}
    assert rec["last_ts"] >= rec["first_ts"]


def test_unlink_verb_is_journaled_and_replay_converges(tmp_path):
    """Retraction is a durable compensating op (finding 2f1d9382): the verb is
    journaled like link, and the orphan classifier's caller drops retracted
    triples so a retracted edge is never proposed for remap."""
    from cjm_context_graph_projection.journal import JOURNAL_VERBS, append_write, read_journal
    assert "unlink" in JOURNAL_VERBS
    p = str(tmp_path / "writes.jsonl")
    args = {"source_id": "dec-1", "target_id": "sym-1", "relation": "SHAPES", "actor": "a"}
    assert append_write(p, "link", args) is True
    assert append_write(p, "unlink", args) is True   # compensating op appends after its link
    assert [o["verb"] for o in read_journal(p)] == ["link", "unlink"]


def test_unlink_full_id_triple_retracts_despite_missing_endpoints(monkeypatch):
    """Orphan retraction (finding 78cff95f): a FULL-id triple may retract even when
    an endpoint no longer resolves — the stale link op would otherwise poison the
    orphan audit forever (and resurrect the edge on re-resolution). Prefix refs
    still refuse loudly: a prefix cannot derive the deterministic edge id."""
    import asyncio
    from cjm_context_graph_projection import write as write_mod

    async def fake_resolve(gx, ref):
        return {}  # neither endpoint resolves — both vanished from the graph

    calls = []

    async def fake_graph_task(queue, graph_id, op, **kw):
        calls.append(op)
        return 0  # nothing deleted — the db edge is already gone

    monkeypatch.setattr(write_mod, "resolve_node_ref", fake_resolve)
    monkeypatch.setattr(write_mod, "graph_task", fake_graph_task)
    gx = type("GX", (), {"queue": None, "graph_id": "g"})()
    full_src = "d899e643-fab2-5863-a1e9-c00eff077389"
    full_tgt = "8d7d91d4-9011-5517-81a8-6938a126f23a"
    res = asyncio.run(write_mod.unlink(gx, full_src, full_tgt, "SHAPES"))
    assert res["written"] is True and res["deleted"] == 0
    assert calls == ["delete_edges"]
    res2 = asyncio.run(write_mod.unlink(gx, "d899e643", full_tgt, "SHAPES"))
    assert res2["written"] is False and "missing node" in res2["error"]
