"""session-cost: what one session's graph work cost, DERIVED — never stored (work item
e526672a W6; ruling 865ba564; the transcript source ruled by the user 2026-10-11).

Two durable sources, read together. The SESSION'S TRANSCRIPT (the harness's lossless
both-sides record, kept past its upstream prune by cc-archive) carries every command and
its result: each `cg-read` / `cg-write` / `cg-rebuild` / `cg-backup` invocation by verb,
the calls that were REFUSED (a write that refuses journals nothing, so only the
transcript can count it), wall time per call, and the file-tool edits of `.py` source —
the measure finding e9afcae5 asked for (graph-native edits against file-born ones). The
JOURNALS carry what landed: the session's ops per verb (`journal_window`'s tally). The
transcript is found through the session's own `pull-transcript` op (its
`cc_session_uuid`), then by the boot-prompt match; the archive copy and the live copy
are both looked for and the fuller one read.

A refusal is a call whose exit was non-zero, or whose output carries the verbs' error
form at the start of a line — `⚠ ` (an advisory is bold, `⚠ **`, or indented, and does
not count), `⛔ `, `error:`, or `<tool>: REFUSING`. Commands chained in one call each count; a refusal is charged
to the call's write verbs (else its read verbs)."""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from cjm_context_graph_primitives.journal import journal_segments, read_segment

from .journal import journal_window

CG_TOOLS = ("cg-read", "cg-write", "cg-rebuild", "cg-backup")
_CG_CALL = re.compile(r"(?:(?<=\s)|(?<=^)|(?<=[;&|(`]))(?:\S*/)?(cg-read|cg-write|cg-rebuild|cg-backup)(?=\s|$)([^;&|\n`)]*)",
                      re.M)
_SEGMENT_BREAK = re.compile(r"[;&|(`\n]|\$\(")
_PREFIX_WORD = re.compile(r"^(?:\w+=\S*|time|exec|command|nohup|timeout|\d+[smh]?|do|then|else|if|while|until|!|\{)$")
_VERB = re.compile(r"^[a-z][a-z0-9-]*$")
_REFUSAL_LINE = re.compile(r"^(?:⚠ (?!\*\*)|⛔ |error:|\S*: REFUSING)")


def cg_invocations(
    command: str,  # One Bash call's command text
) -> List[Dict[str, str]]:  # [{tool, verb}] in order ("" verb for cg-rebuild / cg-backup)
    """Every graph-tool invocation in a command: the tool, and for `cg-read` / `cg-write`
    the verb — the first argument that is not a flag (global flags like `--notes`
    precede it). A tool named as an ARGUMENT (a path read by sed / grep) is not one."""
    out: List[Dict[str, str]] = []
    for m in _CG_CALL.finditer(command):
        # Command position only: what precedes the tool in its simple command may be
        # environment assignments (and time / timeout / exec), never an argument — a
        # `sed … .cjm/bin/cg-backup` names the file, it does not run it.
        before = _SEGMENT_BREAK.split(command[:m.start()])[-1].split()
        if not all(_PREFIX_WORD.match(w) for w in before):
            continue
        tool, rest = m.group(1), m.group(2).split()
        verb = ""
        if tool in ("cg-read", "cg-write"):
            verb = next((t for t in rest if not t.startswith("-") and _VERB.match(t)), "")
        out.append({"tool": tool, "verb": verb})
    return out


def is_refusal(
    output: str,      # A tool result's text
    is_error: bool,   # The harness's error flag (a non-zero exit)
) -> bool:  # Whether the call refused / failed
    """A call refused when it exited non-zero or printed the verbs' error form (see the
    module docstring; a bold `⚠ **` advisory is not a refusal)."""
    return bool(is_error) or any(_REFUSAL_LINE.match(line) for line in output.splitlines())


def _result_text(block: Dict[str, Any]) -> str:
    """A tool_result block's text (a string, or a list of text parts)."""
    c = block.get("content")
    if isinstance(c, list):
        return "\n".join(str(p.get("text", "")) for p in c if isinstance(p, dict))
    return str(c or "")


def _ts(record: Dict[str, Any]) -> Optional[float]:
    """A transcript record's timestamp as unix seconds."""
    t = record.get("timestamp")
    try:
        return datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp() if t else None
    except ValueError:
        return None


def transcript_cost(
    path: str,                       # The session's transcript (.jsonl)
    code_root: Optional[str] = None,  # The checkouts root: a file-tool edit of a .py under it counts
) -> Dict[str, Any]:  # The transcript half of the cost (see session_cost)
    """Walk a transcript's main chain: pair each tool call with its result, classify the
    graph-tool invocations, charge refusals, time each call, count file-tool edits of
    `.py` source under the code root."""
    uses: Dict[str, Dict[str, Any]] = {}
    tools: Dict[str, int] = {}
    first = last = None
    results: Dict[str, Dict[str, Any]] = {}
    for line in Path(path).read_text(errors="replace").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("isSidechain"):
            continue
        ts = _ts(r)
        if ts is not None:
            first = ts if first is None else min(first, ts)
            last = ts if last is None else max(last, ts)
        content = (r.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                tools[b.get("name", "")] = tools.get(b.get("name", ""), 0) + 1
                uses[b.get("id", "")] = {"name": b.get("name"), "input": b.get("input") or {}, "ts": ts}
            elif b.get("type") == "tool_result":
                results[b.get("tool_use_id", "")] = {"text": _result_text(b),
                                                    "is_error": bool(b.get("is_error")), "ts": ts}
    reads: Dict[str, int] = {}
    writes: Dict[str, int] = {}
    refused: Dict[str, int] = {}
    examples: List[str] = []
    rebuilds = backups = cg_calls = refusals = py_edits = 0
    cg_wall = 0.0
    root = str(Path(code_root)) + "/" if code_root else None
    for uid, u in uses.items():
        inp = u["input"]
        if u["name"] in ("Edit", "Write", "NotebookEdit") and root:
            fp = str(inp.get("file_path") or inp.get("notebook_path") or "")
            if fp.startswith(root) and fp.endswith((".py", ".ipynb")):
                py_edits += 1
        if u["name"] != "Bash":
            continue
        calls = cg_invocations(str(inp.get("command") or ""))
        if not calls:
            continue
        cg_calls += 1
        for c in calls:
            if c["tool"] == "cg-read":
                reads[c["verb"]] = reads.get(c["verb"], 0) + 1
            elif c["tool"] == "cg-write":
                writes[c["verb"]] = writes.get(c["verb"], 0) + 1
            elif c["tool"] == "cg-rebuild":
                rebuilds += 1
            else:
                backups += 1
        res = results.get(uid)
        if res is None:
            continue
        if u["ts"] is not None and res["ts"] is not None:
            cg_wall += max(0.0, res["ts"] - u["ts"])
        if is_refusal(res["text"], res["is_error"]):
            refusals += 1
            charged = ([c["verb"] for c in calls if c["tool"] == "cg-write"]
                       or [c["verb"] or c["tool"] for c in calls])
            for v in dict.fromkeys(charged):
                refused[v] = refused.get(v, 0) + 1
            line = next((ln.strip() for ln in res["text"].splitlines()
                         if _REFUSAL_LINE.match(ln)), res["text"].strip().splitlines()[0]
                        if res["text"].strip() else "(no output)")
            if len(examples) < 8:
                examples.append(f"{'/'.join(dict.fromkeys(charged))}: {line[:160]}")
    return {"transcript_path": str(path), "first_ts": first, "last_ts": last,
            "wall_seconds": (last - first) if first is not None and last is not None else None,
            "tool_calls": dict(sorted(tools.items(), key=lambda kv: -kv[1])),
            "cg_calls": cg_calls, "cg_wall_seconds": round(cg_wall, 1),
            "reads": dict(sorted(reads.items(), key=lambda kv: -kv[1])),
            "writes": dict(sorted(writes.items(), key=lambda kv: -kv[1])),
            "rebuilds": rebuilds, "backups": backups,
            "refusals": refusals, "refused_by_verb": dict(sorted(refused.items(), key=lambda kv: -kv[1])),
            "refusal_examples": examples, "py_file_edits": py_edits}


def find_session_transcript(
    session_key: str,            # The session spine key
    journal_paths: List[str],    # The writes + source journals (the pull op names the transcript)
    transcript_dirs: List[str],  # Where transcripts live: the harness projects dir, the cc-archive copy
) -> Optional[str]:  # The fullest transcript file for the session, or None
    """The session's transcript: its `pull-transcript` op's `cc_session_uuid` first (the
    pairing the graph already asserts), else the boot-prompt match; among the copies the
    dirs hold (archive and live), the one with the most lines."""
    uuid = _uuid_from_journal(session_key, journal_paths)
    candidates: List[Path] = []
    for d in transcript_dirs:
        base = Path(d).expanduser()
        if not base.is_dir():
            continue
        if uuid:
            candidates += list(base.glob(f"*/{uuid}.jsonl")) + list(base.glob(f"{uuid}.jsonl"))
    if not candidates and uuid is None:
        try:
            from cjm_harness_transcripts.mapping import find_transcripts_for_key
        except ModuleNotFoundError:
            return None
        for d in transcript_dirs:
            for sub in [p for p in Path(d).expanduser().glob("*") if p.is_dir()] + [Path(d).expanduser()]:
                matches = find_transcripts_for_key(sub, session_key, require_signal=False)
                candidates += [m.path for m in matches[:1]]
    if not candidates:
        return None
    return str(max(candidates, key=lambda p: sum(1 for _ in open(p, "rb"))))


def _uuid_from_journal(
    session_key: str,          # The session spine key
    journal_paths: List[str],  # The writes journal(s)
) -> Optional[str]:  # The cc_session_uuid a pull-transcript op for the session carries
    """Scan the raw journal records for the session's pull-transcript op (the touch-row
    cache does not carry op args)."""
    for path in journal_paths:
        if not path:
            continue
        for seg in journal_segments(path):     # the family, compressed cold segments included
            for line in read_segment(seg).splitlines():
                if '"pull-transcript"' not in line or session_key not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                args = rec.get("args") or {}
                if rec.get("verb") == "pull-transcript" and args.get("cc_session_uuid"):
                    if args.get("session_key", session_key) == session_key:
                        return str(args["cc_session_uuid"])
    return None


def session_cost(
    session_key: str,            # The session spine key
    journal_paths: List[str],    # The writes + source journals
    transcript_dirs: List[str],  # The harness projects dir + the cc-archive copy
    code_root: Optional[str] = None,  # The checkouts root (the .py file-edit count)
    transcript: Optional[str] = None,  # An explicit transcript path (overrides the lookup)
) -> Dict[str, Any]:  # {session_key, journaled: {entries, by_verb}, ...transcript_cost} or {error}
    """The cost instrument: the transcript half (commands, refusals, wall, file edits) and
    the journal half (ops per verb) for one session, derived on every call."""
    jw = journal_window(journal_paths, session=session_key)
    out: Dict[str, Any] = {"session_key": session_key,
                           "journaled": {"entries": jw.get("entries", 0),
                                         "by_verb": dict(sorted((jw.get("by_verb") or {}).items(),
                                                                key=lambda kv: -kv[1]))}}
    path = transcript or find_session_transcript(session_key, journal_paths, transcript_dirs)
    if path is None:
        return {**out, "error": f"no transcript found for session {session_key} in "
                                f"{', '.join(transcript_dirs)}"}
    return {**out, **transcript_cost(path, code_root)}
