"""Triage proposals (bb015d12): the pure drafter, then the propose/confirm chain end to end.

A stale code-referencing deliverable (a post whose fenced code block carries a symbol
whose body changed after approval) gets an agent-drafted Proposal BESIDE its approved
content; the frontier row points at it; a re-run mints nothing; `confirm-proposal`
applies the draft as a journaled section op and re-asserts the approval on the new
hash so the frontier clears; a journal-only replay carries the proposal, the applied
section and the re-approval.
"""
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cjm_context_graph_layer.grammar import make_edge
from cjm_context_graph_layer.ops import extend_graph
from cjm_context_graph_primitives.journal import append_op, read_journal
from cjm_dev_graph_schema.identity import code_module_node_id, note_node_id, section_node_id

from cjm_context_graph_projection.propose import draft_code_block_update
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph

OLD = "def f():\n    return 1\n"
NEW = "def f():\n    return 2\n"
RAW = "## Use\n\nCall it:\n\n```python\nimport os\n\ndef f():\n    return 1\n```\n\nAfter.\n"


def test_draft_replaces_only_the_verbatim_span_and_marks_once():
    out = draft_code_block_update(RAW, OLD, NEW, "f", "abcdef12@0123")
    assert out is not None
    assert "import os\n\ndef f():\n    return 2\n```" in out          # the surrounding line stayed
    assert "<!-- proposal abcdef12@0123: code block re-rendered from `f` (live body) -->\n\nAfter." in out
    assert draft_code_block_update(out, OLD, NEW, "f", "abcdef12@0123") is None   # already re-rendered, no double mark


def test_draft_cosmetic_drift_rerenders_whole_block_and_no_baseline_matches_header():
    drifted = RAW.replace("    return 1", "  return 1")
    out = draft_code_block_update(drifted, OLD, NEW, "f", "k")
    assert out is not None and "```python\ndef f():\n    return 2\n```" in out
    out2 = draft_code_block_update(RAW, None, NEW, "f", "k")
    assert out2 is not None and "def f():\n    return 2" in out2
    assert draft_code_block_update(RAW, None, NEW, "g", "k") is None            # no block opens `g`
    assert draft_code_block_update("## X\n\nprose only\n", OLD, NEW, "f", "k") is None


def _run(*args):
    return subprocess.run([sys.executable, "-m", "cjm_context_graph_projection.cli", *args],
                          capture_output=True, text=True)


@pytest.mark.skipif(not (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists(),
                    reason=f"graph capability {DEFAULT_GRAPH_ID!r} not installed at {DEFAULT_MANIFESTS} (CI)")
def test_cli_propose_confirm_chain_and_replay(tmp_path):
    db, journal = str(tmp_path / "notes.db"), str(tmp_path / "writes.jsonl")
    src = str(tmp_path / "source.jsonl")
    emit = tmp_path / "staging"
    (tmp_path / "graph.config.json").write_text(json.dumps(
        {"notes_profile": "quarto_post", "emit_root": str(emit), "notes_corpus": str(emit)}))
    base = ("--graph-db-path", db, "--journal-path", journal, "--source-journal-path", src)
    # The referenced code: a CodeModule + CodeSymbol whose LIVE body is already the NEW one.
    module_id = code_module_node_id("r", "m.py")
    sym_id = "5e5e5e5e-0000-5000-8000-000000000001"

    async def build_code():
        async with open_graph(db) as gx:
            await extend_graph(gx.queue, gx.graph_id, [
                {"id": module_id, "label": "CodeModule", "sources": [],
                 "properties": {"title": "m.py", "repo_key": "r", "module_path": "m.py", "path": "m.py"}},
                {"id": sym_id, "label": "CodeSymbol", "sources": [],
                 "properties": {"title": "f", "name": "f", "qualname": "f", "module_id": module_id,
                                "body": NEW, "symbol_kind": "function"}},
            ], [make_edge(module_id, sym_id, "DEFINES")])
    asyncio.run(build_code())
    # The deliverable: a born post whose section carries the OLD body in a fenced block.
    post = ("---\ntitle: \"Tutorial\"\ndate: 2026-09-04\ncategories: [notes]\n---\n\nLede.\n\n" + RAW)
    r = _run(*base, "new-note", "--slug", "tutorial", "--content", post)
    assert r.returncode == 0, r.stderr or r.stdout
    post_id, use_id = note_node_id("tutorial"), section_node_id(note_node_id("tutorial"), "use")
    r = _run(*base, "link", use_id, "REFERENCES", sym_id)
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run(*base, "assert", post_id, "publish_state", "published")
    assert r.returncode == 0, r.stderr or r.stdout
    approved_at = time.time()
    # Source snapshots: the OLD body before approval, the NEW body after it.
    # (the NEW snapshot sits just past the approval — and BEFORE the confirm below re-approves)
    for i, (ts, text) in enumerate([(approved_at - 100, "import os\n\n" + OLD), (approved_at + 0.01, "import os\n\n" + NEW)]):
        append_op(src, {"verb": "source", "ts": ts, "args": {"repo_key": "r", "module_path": "m.py", "text": text},
                        **({"op": {"node_id": sym_id, "op": "author", "slot": "body"}} if i else {})}, dedup=False)
    # The frontier names the change; nothing is proposed yet.
    r = _run(*base, "review-frontier")
    assert r.returncode == 0 and "stale 1" in r.stdout and "[content]" in r.stdout and "📝" not in r.stdout, r.stdout
    # Dry run mints nothing; the real run drafts exactly one proposal and journals it.
    r = _run(*base, "propose", "--no-write")
    assert r.returncode == 0 and "dry run" in r.stdout and "(dry)" in r.stdout, r.stderr or r.stdout
    assert [o["verb"] for o in read_journal(journal)] == ["new-note", "assert", "link", "assert"]
    r = _run(*base, "propose")
    assert r.returncode == 0 and "proposed 1" in r.stdout and "confirm-proposal" in r.stdout, r.stderr or r.stdout
    ops = read_journal(journal)
    assert ops[-1]["verb"] == "propose"
    args = ops[-1]["args"]
    assert args["deliverable_id"] == post_id and args["section_id"] == use_id and args["anchor"] == "use"
    assert "def f():\n    return 2" in args["raw"] and "<!-- proposal " in args["raw"]
    assert "import os\n\ndef f():\n    return 2" in args["raw"]     # the block's other line survived
    assert args["approval"] == {"predicate": "publish_state", "value": "published"}
    r = _run("--graph-db-path", db, "--format", "agent", "list", "--label", "Proposal")
    assert r.returncode == 0, r.stderr or r.stdout
    rows = json.loads(r.stdout)["rows"]
    assert len(rows) == 1
    proposal_id = rows[0]["id"]
    # The proposal is READABLE and the frontier row now points at it; a re-run mints nothing.
    r = _run("--graph-db-path", db, "read", proposal_id)
    assert r.returncode == 0 and "return 2" in r.stdout, r.stderr or r.stdout
    r = _run(*base, "review-frontier")
    assert f"📝 proposal `{proposal_id[:8]}`" in r.stdout and f"confirm-proposal {proposal_id[:8]}" in r.stdout
    r = _run(*base, "propose")
    assert r.returncode == 0 and "proposed 0" in r.stdout and "already proposed" in r.stdout, r.stdout
    assert [o["verb"] for o in read_journal(journal)].count("propose") == 1
    # The approved content is untouched until confirmation.
    assert "return 1" in (emit / "tutorial" / "index.md").read_text()
    # CONFIRM: the draft lands as a section op + the re-approval on the new hash; frontier clears.
    r = _run(*base, "confirm-proposal", proposal_id[:8], "--actor", "user:test")
    assert r.returncode == 0 and "**confirmed**" in r.stdout and "re-approved" in r.stdout, r.stderr or r.stdout
    verbs = [o["verb"] for o in read_journal(journal)]
    assert verbs[-2:] == ["section", "assert"]
    assert read_journal(journal)[-1]["args"]["evidence"] == [proposal_id]
    assert "return 2" in (emit / "tutorial" / "index.md").read_text()     # the .md refreshed
    r = _run(*base, "review-frontier")
    assert r.returncode == 0 and "nothing stale" in r.stdout, r.stdout
    r = _run(*base, "propose")
    assert "proposed 0" in r.stdout and "nothing to propose" in r.stdout
    # Journal-only replay carries the proposal, the applied section and the re-approval.
    r = _run("--graph-db-path", str(tmp_path / "replay.db"), "--journal-path", journal, "replay")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run("--graph-db-path", str(tmp_path / "replay.db"), "--format", "agent", "list", "--label", "Proposal")
    assert [row["id"] for row in json.loads(r.stdout)["rows"]] == [proposal_id]
    r = _run("--graph-db-path", str(tmp_path / "replay.db"), "read", use_id)
    assert "return 2" in r.stdout and "<!-- proposal " in r.stdout
    r = _run("--graph-db-path", str(tmp_path / "replay.db"), "--journal-path", journal,
             "--source-journal-path", src, "review-frontier")
    assert r.returncode == 0 and "nothing stale" in r.stdout, r.stdout
