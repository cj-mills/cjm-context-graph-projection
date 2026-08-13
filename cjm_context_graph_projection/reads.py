"""The content-access READS ledger: which nodes each read delivered into context.

The write journals are truth; this stream is TELEMETRY (item b93eb12f, DEC
45df767d): session-stamped read events — verb, delivered node ids, identifying
request params, actor — appended to a SEPARATE `*.reads.jsonl`, prunable and
never replayed. Dedup is OFF by design: a repeat read is signal (attribution
wants every delivery). Delivered != absorbed — an event proves delivery into
context, not use. Recording is OPT-IN (`--reads-path`; the `cg-read` wrapper
bakes it): with no path configured every call is a no-op, and `record_read`
is FAIL-OPEN — telemetry must never break a read.
"""

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

from cjm_context_graph_primitives.journal import append_op

_UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
_IDS_CAP = 500  # ids kept per event; `n` always records the true count
# Process-level arm state: one CLI invocation is one verb, so the reads path and
# the identifying request params are process constants (set once in main()).
_CONFIG: Dict[str, Any] = {"path": None, "request": None}


def configure_reads(
    path: Optional[str],                        # Reads-ledger path (JSONL); None/empty disarms
    request: Optional[Dict[str, Any]] = None,   # Identifying request params, stamped on every event
) -> None:
    """Arm (or disarm, path=None) read recording for this process."""
    _CONFIG["path"] = path or None
    _CONFIG["request"] = request or None


def delivered_ids(data: Any) -> List[str]:  # Uuid-shaped strings, first-seen order, deduped
    """Node ids a rendered result delivered into the consumer's context.

    The rendered view IS the delivery boundary: an id in the view entered
    context (a neighbour row no less than a read body), so extraction is
    verb-agnostic — uuid-shaped strings over the serialized result — and
    stays correct as result shapes evolve."""
    blob = data if isinstance(data, str) else json.dumps(data, default=str)
    out: List[str] = []
    seen = set()
    for m in _UUID_RE.findall(blob):
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def append_read(
    path: str,               # Reads-ledger path (JSONL)
    event: Dict[str, Any],   # The event record — requires `verb`
) -> None:
    """Append one read event; `ts`/`session` stamping rides `append_op`.

    Dedup OFF: every delivery is one event (the ledger is telemetry — the
    O(n) rescan dedup buys for write journals is pure cost here)."""
    append_op(path, event, dedup=False)


def record_read(
    verb: str,   # The read verb whose result is being delivered
    data: Any,   # The result dict handed to the renderer
) -> None:
    """The render-boundary tap: no-op unarmed, FAIL-OPEN armed."""
    path = _CONFIG.get("path")
    if not path:
        return
    try:
        ids = delivered_ids(data)
        event: Dict[str, Any] = {"verb": verb, "ids": ids[:_IDS_CAP], "n": len(ids)}
        if _CONFIG.get("request"):
            event["request"] = _CONFIG["request"]
        actor = os.environ.get("CJM_ACTOR")
        if actor:
            event["actor"] = actor
        append_read(path, event)
    except Exception as e:  # telemetry must never break a read
        print(f"warning: reads-ledger append failed (read delivered anyway): {e}",
              file=sys.stderr)
