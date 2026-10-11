"""session-cost (work item e526672a W6): the cost of a session's graph work, derived from
its transcript + the journals — invocations by verb, refusals, wall, .py file edits."""

import json

from cjm_context_graph_projection.sessioncost import (cg_invocations, is_refusal,
                                                      session_cost, transcript_cost)


def _record(kind, ts, blocks):
    return {"type": kind, "timestamp": ts, "isSidechain": False,
            "message": {"role": kind, "content": blocks}}


def _transcript(path, uuid="u-1"):
    rows = [
        _record("assistant", "2026-10-11T10:00:00Z", [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "export CJM_SESSION=k && .cjm/bin/cg-write author abc --edit a b"}}]),
        _record("user", "2026-10-11T10:00:02Z", [
            {"type": "tool_result", "tool_use_id": "t1", "is_error": False,
             "content": "⚠ edit OLD not found in the slot"}]),
        _record("assistant", "2026-10-11T10:01:00Z", [
            {"type": "tool_use", "id": "t2", "name": "Bash",
             "input": {"command": "for i in a b; do .cjm/bin/cg-read show $i; done; sed -n 1p .cjm/bin/cg-backup"}}]),
        _record("user", "2026-10-11T10:01:01Z", [
            {"type": "tool_result", "tool_use_id": "t2", "is_error": False,
             "content": "## a\n  ⚠ an indented advisory\n⚠ **possible duplicates**"}]),
        _record("assistant", "2026-10-11T10:02:00Z", [
            {"type": "tool_use", "id": "t3", "name": "Edit",
             "input": {"file_path": "/repos/lib/pkg/m.py", "old_string": "a", "new_string": "b"}}]),
        _record("user", "2026-10-11T10:03:00Z", [
            {"type": "tool_result", "tool_use_id": "t3", "is_error": False, "content": "ok"}]),
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def test_invocations_are_commands_in_command_position_only():
    assert cg_invocations("CJM_SESSION=k .cjm/bin/cg-read --notes show x | head") == [
        {"tool": "cg-read", "verb": "show"}]
    assert cg_invocations("sed -n 1,30p .cjm/bin/cg-backup") == []
    assert cg_invocations("X=$(.cjm/bin/cg-read locate y) && .cjm/bin/cg-rebuild") == [
        {"tool": "cg-read", "verb": "locate"}, {"tool": "cg-rebuild", "verb": ""}]


def test_refusal_is_the_error_form_never_an_advisory():
    assert is_refusal("⚠ edit OLD not found in the slot", False)
    assert is_refusal("cg-backup: REFUSING -- the close gate failed", False)
    assert is_refusal("all good", True)
    assert not is_refusal("⚠ **possible duplicates among OPEN items**", False)
    assert not is_refusal("  ⚠ a one-time canonicalization — the change:", False)
    assert not is_refusal('echo "cg-backup: REFUSING — $X"', False)


def test_transcript_cost_counts_calls_refusals_wall_and_file_edits(tmp_path):
    cost = transcript_cost(str(_transcript(tmp_path / "t.jsonl")), code_root="/repos")
    assert cost["writes"] == {"author": 1} and cost["reads"] == {"show": 1}
    assert cost["backups"] == 0 and cost["cg_calls"] == 2
    assert cost["refusals"] == 1 and cost["refused_by_verb"] == {"author": 1}
    assert cost["refusal_examples"] == ["author: ⚠ edit OLD not found in the slot"]
    assert cost["cg_wall_seconds"] == 3.0 and cost["wall_seconds"] == 180.0
    assert cost["py_file_edits"] == 1


def test_session_cost_finds_the_transcript_through_the_pull_op(tmp_path):
    projects = tmp_path / "projects" / "-repo"
    projects.mkdir(parents=True)
    _transcript(projects / "u-1.jsonl")
    writes = tmp_path / "writes.jsonl"
    writes.write_text(json.dumps({"verb": "session", "ts": 1.0, "session": "2026-10-11_10-00-00",
                                  "args": {"key": "2026-10-11_10-00-00", "started_at": 1.0}}) + "\n"
                      + json.dumps({"verb": "pull-transcript", "ts": 2.0, "session": "2026-10-11_10-00-00",
                                    "args": {"session_key": "2026-10-11_10-00-00",
                                             "cc_session_uuid": "u-1", "messages": []}}) + "\n")
    cost = session_cost("2026-10-11_10-00-00", [str(writes)], [str(tmp_path / "projects")],
                        code_root="/repos")
    assert not cost.get("error"), cost.get("error")
    assert cost["transcript_path"].endswith("u-1.jsonl")
    assert cost["journaled"]["entries"] == 2
    assert cost["journaled"]["by_verb"] == {"session": 1, "pull-transcript": 1}
    missing = session_cost("2026-10-11_11-00-00", [str(writes)], [str(tmp_path / "projects")])
    assert "no transcript found" in missing["error"]
