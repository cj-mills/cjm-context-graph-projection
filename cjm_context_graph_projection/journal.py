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
from typing import Any, Dict, List

from .runtime import GraphHandle
from .write import alias, assert_value, decide, link

# The write verbs the journal records + replays (keyed by the op `verb` field).
JOURNAL_VERBS = ("decide", "alias", "assert", "link")


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


async def replay_journal(
    gx: GraphHandle,
    path: str,  # Journal file path (JSONL)
) -> Dict[str, int]:  # Per-verb replay counts
    """Re-apply every journaled write through its core verb (idempotent).

    Run AFTER the projection is built (markdown notes, repo map, seeds, CODE), so an
    `alias`'s canonical note, an `assert`'s seed slot, and a `link`'s endpoints (e.g.
    a decomposed CodeSymbol) already exist to write against. Ops replay in append
    order, so a `link` to a journaled Decision lands after that Decision's `decide`.
    Deterministic ids make re-application a verified no-op."""
    counts = {v: 0 for v in JOURNAL_VERBS}
    counts["skipped"] = 0
    for op in read_journal(path):
        verb, a = op.get("verb"), op.get("args", {})
        if verb == "decide":
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
        else:
            counts["skipped"] += 1
            continue
        counts[verb] += 1
    return counts
