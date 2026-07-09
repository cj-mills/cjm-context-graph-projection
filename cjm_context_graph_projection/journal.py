"""The write journal: the durable, replayable source of truth for born-on-graph writes.

The graph DB is a pure rebuildable PROJECTION of *(markdown corpus + repo map +
seeds + THIS journal)*. The non-re-derivable knowledge — distilled decisions,
confirmed aliases, fact corrections born through the write verbs — lives here as an
append-only log of write operations, NOT inside the binary db. So:

- Live CLI write verbs (`assert`/`alias`/`decide`) append their op here on success.
- `ingest` replays the journal after building the projection, so
  `rm db && ingest` fully reconstructs the graph INCLUDING born-on-graph knowledge.
- The .db is disposable; the small plain-text journal is the precious artifact.

This is the migration discipline the persistent-DB crossover demands — it keeps the
db REBUILDABLE rather than precious (resolving the "never rm the source-of-truth
binary" tension). Replay is idempotent: every write verb has deterministic ids, so
re-applying the log collides into verified no-ops.
"""

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from cjm_context_graph_layer.ops import graph_task
from cjm_dev_graph_schema.identity import (code_module_node_id, note_node_id, section_node_id,
                                           session_node_id)
from cjm_dev_graph_schema.nodes import DecisionNode
from cjm_markdown_decompose_core.extract import note_from_file

from .display import annotate_display, display_rule_node_id, node_title, set_display_rule
from .runtime import GraphHandle
from .structure import add_section, reconstruct_note
from .write import add_check, alias, assert_value, author_section, decide, link, register_session

# The provenance actor stamped on the M3 one-time genesis import (a per-note `new-note` op
# capturing the pre-cutover baseline). The lineage floor every later edit traces back to.
M3_BASELINE_ACTOR = "import:m3-baseline"

# The write verbs the journal records + replays (keyed by the op `verb` field).
# `section` = M2b: a memory section's verbatim `raw` STATE (born-on-graph authoring; the .md
# becomes a projection). Replayed via update_node, so a drifted node's content-hash guard
# (which a re-extend would trip) is sidestepped.
# `new-note` = M3: a whole note's baseline TEXT (frontmatter + body). Replayed via
# `reconstruct_note` (extend_graph, graph-only), it MINTS the Note + Section nodes from the
# journal alone — so `ingest` can stop reading that note's `.md` (the authority flip).
# `add-section` = M2a: a STRUCTURAL add (slug/raw/after) on an already-genesis'd note. Replayed
# graph-only via `add_section` in pass 2 (append order), it re-splices a section born on-graph
# AFTER the note's genesis — the piece that keeps a journal-sourced note's later structure durable.
# `display-rule` = the graph-carried presentation vocabulary (DEC 16bcd96e): a per-kind
# DisplayRule node authored/updated by deterministic id, so the journal's LAST op per kind
# wins on replay (upsert semantics — rules are data, not content).
# `session` = the timestamp-keyed session SPINE (DEC 6124d8bf): a Session node upserted by
# deterministic per-key id (started_at + optional title; last op wins on replay, like
# display-rule). Window END is derived at read time, never stored.
JOURNAL_VERBS = ("decide", "alias", "assert", "link", "section", "new-note", "add-section",
                 "display-rule", "check", "session")


def read_journal(
    path: str,  # Journal file path (JSONL)
) -> List[Dict[str, Any]]:  # The recorded ops, in append order
    """Read every journaled write op (one JSON object per line; missing file = [])."""
    p = Path(path)
    if not p.exists():
        return []
    ops: List[Dict[str, Any]] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            ops.append(json.loads(line))
    return ops


def append_write(
    path: str,        # Journal file path (JSONL)
    verb: str,        # The write verb ("decide" | "alias" | "assert")
    args: Dict[str, Any],  # The resolved arguments applied (replay re-passes these verbatim)
) -> bool:  # True if appended, False if an identical op was already journaled
    """Append one write op (skipping an exact (verb,args) duplicate).

    Args are the RESOLVED inputs to the core verb (e.g. an alias's discovered
    evidence ids), so replay is deterministic and independent of corpus state."""
    for existing in read_journal(path):
        if existing.get("verb") == verb and existing.get("args") == args:
            return False  # already recorded — keep the log tidy (replay is idempotent anyway)
    record = {"verb": verb, "ts": time.time(), "args": args}
    session = current_session()
    if session:
        record["session"] = session
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    return True


def m3_baseline_import(
    memory_dir: str,                  # Dir of memory markdown files
    journal_path: str,                # The write journal to append genesis ops to
    *,
    slugs: Optional[List[str]] = None,  # Restrict to these note slugs (None + all_notes=False -> nothing)
    all_notes: bool = False,            # Import the WHOLE corpus (the slice->corpus widening)
) -> Dict[str, Any]:  # {imported: [{slug, path, bytes}], skipped_existing: [...], unknown: [...]}
    """One-time M3 GENESIS IMPORT: emit a per-note `new-note` baseline op into the journal.

    The authority flip's Fork 1 ([[memory-files-retirement-plan]]): for each selected note,
    capture its EXACT current `.md` bytes as a `new-note` op stamped `import:m3-baseline` — the
    lineage floor every later edit traces back to. After this, `ingest` stops reading that note's
    `.md` (it's reconstructed by replay). Idempotent: `append_write` dedups an identical op, and
    a note already carrying a baseline op is reported as `skipped_existing`, so re-running is safe
    and the slice widens to the corpus mechanically (add slugs / `--all`)."""
    mem = Path(memory_dir)
    by_slug: Dict[str, Path] = {}
    for p in sorted(mem.glob("*.md")):
        if p.name == "MEMORY.md":
            continue
        by_slug[note_from_file(str(p), corpus_root=str(mem), lossless=True).slug] = p

    if all_notes:
        targets = sorted(by_slug)
    else:
        targets = list(slugs or [])
    already = {str(Path(p).resolve()) for p in journal_sourced_note_paths(journal_path)}

    imported: List[Dict[str, Any]] = []
    skipped_existing: List[str] = []
    unknown: List[str] = []
    for slug in targets:
        p = by_slug.get(slug)
        if p is None:
            unknown.append(slug)
            continue
        abspath = str(p.resolve())
        if abspath in already:
            skipped_existing.append(slug)
            continue
        content = p.read_text()
        append_write(journal_path, "new-note",
                     {"path": abspath, "content": content, "actor": M3_BASELINE_ACTOR})
        imported.append({"slug": slug, "path": abspath, "bytes": len(content.encode("utf-8"))})
    return {"imported": imported, "imported_count": len(imported),
            "skipped_existing": skipped_existing, "unknown": unknown,
            "corpus_notes": len(by_slug)}


def journal_sourced_note_paths(
    path: str,  # Journal file path (JSONL)
) -> List[str]:  # Absolute `.md` paths of notes carrying ANY `new-note` genesis op
    """The memory `.md` files `ingest` must NOT read — they're journal-sourced now.

    The per-note authority flip is keyed off the journal itself: a note with a genesis
    `new-note` op is reconstructed by replay, so reading its `.md` during projection would
    double-build it. The skip key is the OP, not the actor — `import:m3-baseline` (migrated)
    and `agent:session` (born on-graph via `new-note`) are uniform here; actor is PROVENANCE
    only. This is the slice->corpus seam: the set grows as notes are imported OR created
    natively, and the flip is complete when it covers the whole dir."""
    out: List[str] = []
    for op in read_journal(path):
        if op.get("verb") == "new-note":
            p = op.get("args", {}).get("path")
            if p:
                out.append(str(Path(p).resolve()))
    return out


async def _apply_op(gx: GraphHandle, op: Dict[str, Any]) -> str:
    """Apply one journaled op through its core verb; return the verb ('' = skipped)."""
    verb, a = op.get("verb"), op.get("args", {})
    if verb == "new-note":
        # M3 genesis: MINT a whole note (Note + Section nodes) from journaled baseline text,
        # graph-only (extend_graph), so a note no longer read from its `.md` is reconstructed
        # from the journal alone. Idempotent (deterministic ids -> verified no-op on rebuild).
        await reconstruct_note(gx, a["path"], a["content"])
    elif verb == "decide":
        await decide(gx, a["statement"], actor=a.get("actor", "agent:session"),
                     supports=a.get("supports"), supersedes=a.get("supersedes"),
                     session=a.get("session"), title=a.get("title"))
    elif verb == "alias":
        await alias(gx, a["drifted"], a["canonical"], actor=a.get("actor", "agent:session"),
                    evidence=a.get("evidence"))
    elif verb == "assert":
        await assert_value(gx, a["subject"], a["predicate"], a["value"],
                           actor=a.get("actor", "agent:session"),
                           evidence=a.get("evidence"), supersede=a.get("supersede"))
    elif verb == "link":
        await link(gx, a["source_id"], a["target_id"], a["relation"],
                   actor=a.get("actor", "agent:session"))
    elif verb == "check":
        # DoD gradient: re-attach a check (deterministic (item, text) id -> verified
        # no-op on rebuild). Replaying its `task_state=open` after a later journaled
        # `done` lands born-superseded (ordered predicate), so replay converges.
        await add_check(gx, a["item_id"], a["text"], actor=a.get("actor", "agent:session"))
    elif verb == "section":
        # M2b: re-apply a section's verbatim raw STATE (update_node; idempotent). A missing
        # section is a tolerated no-op. The audit-only `replaces`/ts fields are ignored.
        await author_section(gx, a["slug"], a["anchor"], a["raw"],
                             actor=a.get("actor", "agent:session"))
    elif verb == "add-section":
        # M2a structural add, replayed GRAPH-ONLY (write_md=False): re-splice the section from
        # the journaled slug/raw/after against the CURRENT graph. Runs in pass 2 (append order)
        # so it lands after its note's genesis (pass 1) and composes with interleaved `section`
        # STATE ops in the exact causal order they were journaled. Idempotent: `add_section`
        # no-ops when the target anchor already exists, so a rebuild never duplicates it.
        await add_section(gx, a["slug"], a["raw"], after=a.get("after"),
                          write=True, write_md=False)
    elif verb == "display-rule":
        # Presentation vocabulary: upsert by deterministic per-kind id, so replaying
        # the append-ordered ops converges on the LAST authored rule per kind.
        await set_display_rule(gx, a["for_label"], a.get("title_template"),
                               a.get("gloss_template"), actor=a.get("actor", "agent:session"))
    elif verb == "session":
        # Session spine: upsert the timestamp-keyed Session node (started_at/title are
        # last-op-wins on replay, like display-rule — sessions are data, not content).
        await register_session(gx, a["key"], started_at=a.get("started_at"),
                               title=a.get("title"), actor=a.get("actor", "agent:session"))
    else:
        return ""
    return verb


async def replay_journal(
    gx: GraphHandle,
    path: str,  # Journal file path (JSONL)
) -> Dict[str, int]:  # Per-verb replay counts
    """Re-apply every journaled write through its core verb (idempotent).

    TWO-PASS — STRUCTURE THEN CONTENT. Pass 1 replays the `new-note` GENESIS ops (M3) so all
    journal-sourced Note + Section nodes exist; pass 2 replays everything else in append order.
    This is REQUIRED post-cutover: the genesis ops are appended LAST (the flip is a late event)
    but earlier curation `link`/`assert`/`alias`/`section` ops TARGET those notes/sections — a
    single pass would replay them before the node exists, so `link` would drop the edge and
    `assert` would mint a stray `term` entity. Minting the genesis nodes first lets every later
    op land on the same deterministic id (so curation edges/assertions carry over automatically,
    no re-authoring). Append order WITHIN pass 2 is preserved, so a `link` to a journaled
    Decision still lands after that Decision's `decide`. Deterministic ids make re-application a
    verified no-op; a pre-cutover `section` op for an as-yet-unbuilt section is still tolerated.

    `add-section` is a pass-2 STRUCTURAL op (a section born on-graph AFTER its note's genesis).
    It stays in append order — NOT hoisted to pass 1 like `new-note` — because an add-section is
    always journaled AFTER the note it extends already carries a genesis op, so genesis→add
    ordering holds without hoisting, and append order is exactly what lets an add compose with the
    `section` STATE ops interleaved around it (boundary shift then final content, in causal order).
    Its own idempotency (anchor-exists no-op) covers a note not yet journal-sourced whose backup
    `.md` an ingest still read."""
    counts = {v: 0 for v in JOURNAL_VERBS}
    counts["skipped"] = 0
    ops = read_journal(path)
    genesis = [op for op in ops if op.get("verb") == "new-note"]
    rest = [op for op in ops if op.get("verb") != "new-note"]
    for op in genesis + rest:
        verb = await _apply_op(gx, op)
        if verb:
            counts[verb] += 1
        else:
            counts["skipped"] += 1
    return counts


def current_session() -> Optional[str]:  # The active session key, or None
    """The session key stamped on journal appends (provenance, not replay input).

    Read from `CJM_SESSION` — the `cg-write` wrapper exports it from
    `.cjm/current-session`, one start-time timestamp key generated per session
    (DEC 6124d8bf: discipline problems become infrastructure — historical
    per-verb `--session` coverage was 9% for exactly this reason). Stamped
    TOP-LEVEL on the record so dedup (verb+args) and replay stay session-blind."""
    return os.environ.get("CJM_SESSION") or None


def _id_shaped(ref: str) -> bool:  # True if `ref` looks like a node id / unique id prefix
    """Heuristic for id-shaped journal args (an `assert` subject may be a NAME).

    Hex + dashes, >=6 chars, with at least one [a-f] — the letter requirement
    keeps date-shaped values (e.g. "2026-06-25") out of the touched set."""
    if not ref or len(ref) < 6 or len(ref) > 36:
        return False
    return bool(re.fullmatch(r"[0-9a-f-]+", ref)) and bool(re.search(r"[a-f]", ref))


def _note_slug(path: Optional[str], content: Optional[str]) -> Optional[str]:
    """A `new-note` op's note slug: frontmatter `name:` first, else the filename stem
    (mirrors `note_from_file`'s identity rule without re-parsing the whole note)."""
    m = re.search(r"^name:\s*(\S+)\s*$", content or "", re.MULTILINE)
    if m:
        return m.group(1)
    return Path(path).stem if path else None


def touched_node_ids(
    op: Dict[str, Any],  # One journaled write op ({verb, args, ...} — writes OR source journal)
) -> List[str]:  # Node refs the op touched (full ids, or unique id prefixes to resolve db-side)
    """Best-effort node refs a journaled op touched — the session-lens feed (2f51ff5d).

    TOUCHES, not creations: an `author`/`assert` on an old node is invisible to
    `created_at` queries but present here. Ids are DERIVED from each verb's natural
    key exactly as the verb derives them (deterministic ids), so the mapping holds
    for the WHOLE historical journal, not just post-cutover entries. Source-journal
    ops (`source`/`cutover`/`retire`) map to their CodeModule. Refs that aren't
    resolvable journal-side (an `alias`'s drifted term, a name-shaped assert
    subject) are simply omitted — the lens is best-effort, never wrong-effort."""
    verb, a = op.get("verb"), op.get("args") or {}
    out: List[str] = []
    if verb == "decide":
        if a.get("statement"):
            out.append(DecisionNode(statement=a["statement"]).id)
        out.extend(a.get("supports") or [])
        out.extend(a.get("supersedes") or [])
        if a.get("session"):
            out.append(session_node_id(a["session"]))
    elif verb == "assert":
        if a.get("subject") and _id_shaped(a["subject"]):
            out.append(a["subject"])
    elif verb == "link":
        out.extend(r for r in (a.get("source_id"), a.get("target_id")) if r)
    elif verb == "check":
        if a.get("item_id"):
            out.append(a["item_id"])
    elif verb == "alias":
        if a.get("canonical"):
            out.append(note_node_id(a["canonical"]))
    elif verb == "new-note":
        slug = _note_slug(a.get("path"), a.get("content"))
        if slug:
            out.append(note_node_id(slug))
    elif verb in ("section", "add-section"):
        if a.get("slug"):
            nid = note_node_id(a["slug"])
            out.append(nid)
            if verb == "section" and a.get("anchor"):
                out.append(section_node_id(nid, a["anchor"]))
    elif verb == "display-rule":
        if a.get("for_label"):
            out.append(display_rule_node_id(a["for_label"]))
    elif verb == "session":
        if a.get("key"):
            out.append(session_node_id(a["key"]))
    elif a.get("repo_key") and a.get("module_path"):
        out.append(code_module_node_id(a["repo_key"], a["module_path"]))
    return out


def journal_window(
    paths: List[str],               # Journal files to scan (the writes + source journals together)
    start: Optional[float] = None,  # Window start (unix ts; None = journal dawn)
    end: Optional[float] = None,    # Window end (unix ts; None = OPEN — live mode)
    session: Optional[str] = None,  # Session key filter (stamped, or a decide's args.session)
) -> Dict[str, Any]:  # {window, entries, touched: [{ref, verbs, touches, first_ts, last_ts}]}
    """The journal-window projection: which nodes a window/session touched, when, how.

    THE session lens's data path (DEC f1b02b95 invariant 1: declarative and
    re-evaluatable — an OPEN end is live mode, re-evaluate on append; evaluate the
    same window at T for time-travel). Session matching prefers the top-level
    provenance stamp and falls back to a `decide` op's `args.session` (the sparse
    pre-cutover tagging), so one filter spans both eras."""
    # DEC 6124d8bf resolution order: post-cutover ops match by TAG; HISTORICAL ops
    # (no stamp) match by the session's WINDOW [started_at, next session's start).
    # The windows are journal-derived too — the retrofit/live `session` ops carry
    # started_at — so a keyed lens stays pure (no graph read, no stored end).
    win_start: Optional[float] = None
    win_end: Optional[float] = None
    if session is not None:
        starts: Dict[str, float] = {}
        for path in paths:
            if not path:
                continue
            for op in read_journal(path):
                a = op.get("args") or {}
                if op.get("verb") == "session" and a.get("key") and a.get("started_at") is not None:
                    starts[a["key"]] = float(a["started_at"])  # upsert: last op wins
        if session in starts:
            win_start = starts[session]
            later = [t for t in starts.values() if t > win_start]
            win_end = min(later) if later else None  # last session = open (in-progress)

    per: Dict[str, Dict[str, Any]] = {}
    entries = 0
    for path in paths:
        if not path:
            continue
        for op in read_journal(path):
            ts = float(op.get("ts") or 0.0)
            if start is not None and ts < start:
                continue
            if end is not None and ts > end:
                continue
            if session is not None:
                tag = op.get("session") or (op.get("args") or {}).get("session")
                in_window = (win_start is not None and ts >= win_start
                             and (win_end is None or ts < win_end))
                if tag != session and not in_window:
                    continue
            entries += 1
            verb = op.get("verb") or "?"
            for ref in touched_node_ids(op):
                rec = per.setdefault(ref, {"ref": ref, "verbs": {}, "touches": 0,
                                           "first_ts": ts, "last_ts": ts})
                rec["touches"] += 1
                rec["verbs"][verb] = rec["verbs"].get(verb, 0) + 1
                rec["first_ts"] = min(rec["first_ts"], ts)
                rec["last_ts"] = max(rec["last_ts"], ts)
    touched = sorted(per.values(), key=lambda r: r["last_ts"], reverse=True)
    out: Dict[str, Any] = {"window": {"start": start, "end": end, "session": session},
                           "entries": entries, "touched": touched}
    if win_start is not None:
        out["session_window"] = {"start": win_start, "end": win_end}
    return out


async def journal_window_view(
    gx: GraphHandle,
    journal_paths: List[str],       # The journals to scan (writes + source, together)
    *,
    start: Optional[float] = None,  # Window start (unix ts)
    end: Optional[float] = None,    # Window end (unix ts; None = OPEN — live mode)
    session: Optional[str] = None,  # Session key filter
) -> Dict[str, Any]:  # journal_window result, refs joined to live nodes
    """The SESSION LENS read verb: `journal_window` + graph join (title/label per ref).

    Prefix refs resolve db-side (the id-prefix convention); a ref whose node no
    longer exists stays listed with `missing: True` — this is an AUDIT surface
    (read-parity, DEC 60aae839 theme 4): it must not silently drop what the
    journal says was touched."""
    # Function-local: `.projection` imports `.journal` via `.write` — a module-level
    # import here would cycle (write.py -> projection.py is the existing edge).
    from .projection import resolve_node_ref

    base = journal_window(journal_paths, start=start, end=end, session=session)
    out: List[Dict[str, Any]] = []
    display_nodes: List[Any] = []
    for rec in base["touched"]:
        ref = rec["ref"]
        if len(ref) == 36:
            node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=ref)
        else:
            res = await resolve_node_ref(gx, ref)
            node = res.get("node") if "candidates" not in res else None
        item = dict(rec)
        if node is None:
            item["missing"] = True
        else:
            item["id"] = (node.get("id") if isinstance(node, dict)
                          else getattr(node, "id", ref))
            item["label"] = (node.get("label") if isinstance(node, dict)
                             else getattr(node, "label", None))
            item["_node"] = node
            display_nodes.append(node)
        out.append(item)
    await annotate_display(gx, display_nodes)
    for item in out:
        node = item.pop("_node", None)
        if node is not None:
            item["title"] = node_title(node)
    base["touched"] = out
    base["missing"] = sum(1 for i in out if i.get("missing"))
    return base
