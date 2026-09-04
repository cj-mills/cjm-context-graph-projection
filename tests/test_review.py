"""The review frontier (730e077e): pure derivation + render, then the CLI chain end to end."""

import json
import subprocess
import sys

from cjm_context_graph_primitives.journal import append_op, read_journal
from cjm_context_graph_projection.render import render
from cjm_context_graph_projection.review import (approvals_of, change_key, classify_text_change,
                                                 walk_upstream)


def _run(*args):
    return subprocess.run([sys.executable, "-m", "cjm_context_graph_projection.cli", *args],
                          capture_output=True, text=True)


def _assertion(aid, subject, predicate, value, ts, h=None, slot=None):
    return {"id": aid, "label": "Assertion",
            "properties": {"slot_id": slot or f"slot-{subject}-{predicate}", "subject_id": subject,
                           "predicate": predicate, "value": value, "asserted_at": ts,
                           "subject_content_hash": h, "actor": "user:test"}}


def test_approvals_are_the_active_approval_class_only():
    # draft is a birth, not an approval; a superseded published is not active.
    a = [_assertion("a1", "post", "publish_state", "draft", 10.0, "sha256:aa"),
         _assertion("a2", "post", "publish_state", "published", 20.0, "sha256:bb"),
         _assertion("a3", "post", "publish_state", "published", 30.0, "sha256:cc"),
         _assertion("a4", "item", "task_state", "done", 5.0)]
    got = approvals_of(a, [("a2", "a1"), ("a3", "a2")])
    assert [(r["assertion_id"], r["bound_hash"], r["asserted_at"]) for r in got] == [("a3", "sha256:cc", 30.0)]


def test_walk_upstream_bounded_with_chain_paths_and_component_hops():
    adj = {"post": [("REFERENCES", "sym")], "sec": [("DERIVED_FROM", "seg")],
           "sym": [("DEPENDS_ON", "lib")], "lib": [("DEPENDS_ON", "deep")],
           "seg": [("REFERENCES", "post")]}  # a back-edge to the root is never upstream
    paths = walk_upstream("post", ["post", "sec"], adj, depth=2)
    assert paths["sym"] == [{"relation": "REFERENCES", "id": "sym"}]
    assert paths["seg"] == [{"relation": "HAS_SECTION", "id": "sec"}, {"relation": "DERIVED_FROM", "id": "seg"}]
    assert paths["lib"][-1]["id"] == "lib" and "deep" not in paths and "post" not in paths and "sec" not in paths


def test_classify_text_change_is_revert_aware_and_grades_cosmetic():
    assert classify_text_change("x = 1\n", "x = 1\n") is None
    assert classify_text_change("x  =  1\n", "x = 1\n") == "cosmetic"
    assert classify_text_change("x = 2\n", "x = 1\n") == "content"
    assert classify_text_change("x = 2\n", None) == "content"
    # containment: a symbol body against the module snapshot it lived in
    snap = "import os\n\ndef f():\n    return 1\n"
    assert classify_text_change("def f():\n    return 1\n", snap, containment=True) is None
    assert classify_text_change("def f():\n  return 1\n", snap, containment=True) == "cosmetic"
    assert classify_text_change("def f():\n    return 2\n", snap, containment=True) == "content"


def test_change_key_is_short_and_hash_bound():
    assert change_key("abcdef12-3456", "sha256:0123456789abcdef0123") == "abcdef12@0123456789ab"
    assert change_key("abcdef12-3456", "assertion:9f9f9f9f") == "abcdef12@assertion:9f9f9f9f"
    assert change_key("abcdef12-3456", "edge:REFERENCES") == "abcdef12@edge:REFERENCES"


def test_render_review_frontier_empty_and_rows():
    empty = render("review-frontier", {"stale": [], "counts": {"approvals": 2}}, "human")
    assert "Review frontier" in empty and "nothing stale" in empty and "DERIVED, never stored" in empty
    obj = {"stale": [{"deliverable": {"id": "d1234567-x", "label": "Post", "kind": "Note"},
                      "approval": {"predicate": "publish_state", "value": "published", "actor": "u",
                                   "asserted_at": 0.0, "bound_hash": "sha256:abc"},
                      "changes": [{"upstream": {"id": "u7654321-y", "label": "helper", "kind": "CodeSymbol"},
                                   "path": [{"relation": "REFERENCES", "id": "u7654321-y", "label": "helper"}],
                                   "class": "content", "detail": "author after approval", "at": 1.0,
                                   "key": "u7654321@deadbeef0000", "acknowledged": False}],
                      "acknowledged": 1}],
           "counts": {"approvals": 1, "stale": 1, "changes": 2, "acknowledged": 1, "unverifiable": 0},
           "view": {"all": False}}
    out = render("review-frontier", obj, "human")
    assert "⚠ **Post** `d1234567`" in out and "[content] **helper**" in out
    assert "chain: `d1234567` → REFERENCES → `u7654321`" in out
    assert "assert d1234567 review_verdict u7654321@deadbeef0000" in out
    assert "1 acknowledged change(s) hidden" in out


def test_cli_review_frontier_chain_empty_then_exact_then_silent_after_ack(tmp_path):
    # Acceptance (730e077e): EMPTY when nothing upstream changed; names EXACTLY the touched
    # chain when one upstream section changes; SILENT after the acknowledgment; and the
    # deliverable's own edit after approval is the `self` class (re-approval, not ack).
    db, journal = str(tmp_path / "dev.db"), str(tmp_path / "writes.jsonl")
    (tmp_path / "graph.config.json").write_text(json.dumps({"emit_root": str(tmp_path / "staging")}))
    # author mutates source state -> the source journal rides every call (cg-write bakes it)
    base = ("--graph-db-path", db, "--journal-path", journal,
            "--source-journal-path", str(tmp_path / "source.jsonl"))
    up = "---\nname: upstream-note\ndescription: u\n---\n\nIntro.\n\n## Facts\n\nOne.\n"
    r = _run(*base, "new-note", "--slug", "upstream-note", "--content", up)
    assert r.returncode == 0, r.stderr or r.stdout
    post = ("---\ntitle: \"Post\"\ndate: 2026-09-04\ncategories: [notes]\n---\n\n"
            "Draws on [[upstream-note]].\n\n## Body\n\nText.\n")
    r = _run(*base, "new-note", "--slug", "post", "--content", post)
    assert r.returncode == 0, r.stderr or r.stdout
    ops = read_journal(journal)
    post_id = [o for o in ops if o["verb"] == "assert"][-1]["args"]["subject"]
    up_id = [o for o in ops if o["verb"] == "new-note"][0]
    # the wiki-link harvest minted post -REFERENCES-> upstream at birth; re-linking is idempotent
    r = _run("--graph-db-path", db, "--format", "agent", "locate", "upstream-note")
    assert r.returncode == 0, r.stderr or r.stdout
    up_node = [m for m in json.loads(r.stdout)["matches"] if m.get("label") == "Note"][0]["id"]
    r = _run(*base, "link", post_id, "REFERENCES", up_node)
    assert r.returncode == 0, r.stderr or r.stdout
    # draft -> no approval -> empty frontier (a draft is a birth, not an approval)
    r = _run(*base, "review-frontier")
    assert r.returncode == 0 and "nothing stale" in r.stdout, r.stderr or r.stdout
    r = _run(*base, "assert", post_id, "publish_state", "published")
    assert r.returncode == 0, r.stderr or r.stdout
    # approved, nothing upstream changed since -> EMPTY
    r = _run(*base, "review-frontier")
    assert r.returncode == 0 and "nothing stale" in r.stdout, r.stderr or r.stdout
    # one upstream section edited (a fidelity change) -> exactly that chain
    r = _run(*base, "author", up_node, "--edit", "One.", "Two, corrected.")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run(*base, "review-frontier")
    assert r.returncode == 0, r.stderr or r.stdout
    assert "stale 1" in r.stdout and "[content]" in r.stdout and "Upstream Note" in r.stdout
    assert f"chain: `{post_id[:8]}` → REFERENCES → `{up_node[:8]}`" in r.stdout
    key = [tok for tok in r.stdout.split() if tok.startswith(up_node[:8] + "@")][0].rstrip("`")
    # a whitespace-only edit of the same section stays classed cosmetic (baseline-aware)
    r = _run(*base, "author", up_node, "--edit", "Two, corrected.", "Two,  corrected.")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run(*base, "review-frontier")
    assert "[content]" in r.stdout  # vs the APPROVAL-time baseline it is still a content change
    # acknowledge the change at its current hash -> silent
    r = _run(*base, "review-frontier")
    key = [tok for tok in r.stdout.split() if tok.startswith(up_node[:8] + "@")][0].rstrip("`")
    r = _run(*base, "assert", post_id, "review_verdict", key)
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run(*base, "review-frontier")
    assert r.returncode == 0 and "nothing stale" in r.stdout and "acknowledged 1" in r.stdout, r.stdout
    # the upstream changes AGAIN -> a new key, the old verdict no longer silences it
    r = _run(*base, "author", up_node, "--edit", "Two,  corrected.", "Three.")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run(*base, "review-frontier")
    assert "stale 1" in r.stdout and "[content]" in r.stdout
    # the deliverable's OWN edit after approval -> `self` class with the re-approval recipe
    r = _run(*base, "add-section", "post", "--content", "## Later\n\nMore.\n")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run(*base, "review-frontier", "--all")
    assert "[self]" in r.stdout and f"assert {post_id[:8]} publish_state published" in r.stdout
    # replay-only projection agrees (the verdict + approval hashes ride the journal)
    r = _run("--graph-db-path", str(tmp_path / "replay.db"), "--journal-path", journal, "replay")
    assert r.returncode == 0, r.stderr or r.stdout
    r2 = _run("--graph-db-path", str(tmp_path / "replay.db"), "--journal-path", journal, "review-frontier", "--all")
    assert r2.returncode == 0 and "[self]" in r2.stdout and "stale 1" in r2.stdout, r2.stderr or r2.stdout


def test_code_symbol_change_is_verified_against_the_source_snapshot(tmp_path):
    # The first mechanical slice (730e077e (e)): a deliverable REFERENCES a CodeSymbol whose
    # body changed after approval — verified against the module snapshot at approval time
    # (a touch that edits the body back reads as UNCHANGED).
    from cjm_context_graph_projection.review import _content_change
    seg = tmp_path / "src.jsonl"
    module_id = "mod-1"
    snap_old = "import os\n\ndef f():\n    return 1\n"
    snap_new = "import os\n\ndef f():\n    return 2\n"
    rows = []
    for i, (ts, text, nid) in enumerate([(10.0, snap_old, None), (30.0, snap_new, "sym-1")]):
        append_op(str(seg), {"verb": "source", "ts": ts, "args": {"repo_key": "r", "module_path": "m.py", "text": text},
                             **({"op": {"node_id": nid, "op": "author", "slot": "body"}} if nid else {})}, dedup=False)
        rows.append({"verb": "source", "ts": ts, "refs": [module_id], "seg": str(seg), "line": i,
                     "source_op": ({"node_id": nid, "op": "author", "slot": "body"} if nid else None)})
    by_ref = {module_id: rows}
    sym = {"id": "sym-1", "label": "CodeSymbol", "properties": {"module_id": module_id, "body": "def f():\n    return 2\n"}}
    cls, detail, unver = _content_change(sym, "CodeSymbol", 20.0, by_ref, {}, True)
    assert cls == "content" and "author after approval" in detail and not unver
    # approved AFTER the edit -> nothing after T -> unchanged, no fetch
    assert _content_change(sym, "CodeSymbol", 40.0, by_ref, {}, True) == (None, "", False)
    # edited back to the approved body -> revert-aware: unchanged
    back = {"id": "sym-1", "label": "CodeSymbol", "properties": {"module_id": module_id, "body": "def f():\n    return 1\n"}}
    assert _content_change(back, "CodeSymbol", 20.0, by_ref, {}, True)[0] is None
    # a module with no snapshots at all is UNVERIFIABLE (counted, never reported as a change)
    ghost = {"id": "sym-2", "label": "CodeSymbol", "properties": {"module_id": "mod-ghost", "body": "x"}}
    assert _content_change(ghost, "CodeSymbol", 20.0, by_ref, {}, True) == (None, "", True)
