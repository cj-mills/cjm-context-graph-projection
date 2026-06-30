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
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from cjm_markdown_decompose_core.extract import note_from_file

from .runtime import GraphHandle
from .structure import reconstruct_note
from .write import alias, assert_value, author_section, decide, link

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
JOURNAL_VERBS = ("decide", "alias", "assert", "link", "section", "new-note")


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
                     session=a.get("session"))
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
    elif verb == "section":
        # M2b: re-apply a section's verbatim raw STATE (update_node; idempotent). A missing
        # section is a tolerated no-op. The audit-only `replaces`/ts fields are ignored.
        await author_section(gx, a["slug"], a["anchor"], a["raw"],
                             actor=a.get("actor", "agent:session"))
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
    verified no-op; a pre-cutover `section` op for an as-yet-unbuilt section is still tolerated."""
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
