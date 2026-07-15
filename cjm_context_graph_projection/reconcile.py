"""M2b shadow-phase RECONCILE — surface + (explicitly) absorb out-of-band `.md` edits.

The memory analogue of `source_state`'s source-journal soak, at SECTION grain. Under the
shadow phase the `.md` stays the ingest source while the private write journal SHADOWS
deliberate section authoring; a human may still hand-edit a `.md`, drifting it from the
graph/journal. This is the soak instrument: REPORT that drift (dry-run, the default), and
only under an explicit absorb FOLD a hand-edit into the journal as a `section` op
(file-wins-by-absorption — "surfaced, never silently overridden").

Absorption ops are SELF-DESCRIBING — actor `reconcile:absorb` + the prior `raw` they
`replaces` — so each machine decision is auditable and reversible (undo = a compensating
author, or a journal line-drop + rebuild, since the db is a projection). Absorbing also
SNAPSHOTS the `.md` first (the files aren't git-committed). NO cutover here: the `.md`
remains the ingest source; clean soaks across sessions gate the eventual M3 flip.

Only CHANGED sections are absorbable in phase 1 — added/removed sections need new-section
authoring (the deferred M2a gradient), so they are reported, not absorbed.
"""

import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from cjm_context_graph_primitives.journal import append_write
from cjm_dev_graph_schema.vocab import DevNodeKinds

from . import factlayer as F
from .authoring import file_section_raws, graph_section_raws, section_divergence
from .runtime import GraphHandle
from .write import author_section

ABSORB_ACTOR = "reconcile:absorb"


def _preview(s: str, n: int = 80) -> str:
    """A one-line, length-capped preview of a section span (for the dry-run diff)."""
    s = " ".join(s.split())
    return s[:n] + ("…" if len(s) > n else "")


async def reconcile_memory(
    gx: GraphHandle,
    *,
    note_slug: Optional[str] = None,             # Restrict to one note (by slug); else the whole corpus
    absorb_anchors: Optional[List[str]] = None,  # Absorb these changed anchors (within scope)
    absorb_all: bool = False,                    # Absorb ALL changed sections in scope
    journal_path: Optional[str] = None,          # Required to absorb (the private write journal)
    backup_dir: Optional[str] = None,            # Snapshot affected .md here (default: alongside the file)
) -> Dict[str, Any]:  # {notes_with_drift, drift, absorbed, clean} or {error}
    """Report `.md`<->graph section drift across the corpus; optionally absorb hand-edits.

    Dry-run by default (absorb only with `absorb_all` / `absorb_anchors`). Absorption needs
    `journal_path`: it snapshots each affected file, applies the file's raw via
    `author_section`, and appends a self-describing `section` op (actor `reconcile:absorb`
    + the replaced raw) — file-wins-by-absorption, auditable and reversible."""
    absorbing = absorb_all or bool(absorb_anchors)
    if absorbing and not journal_path:
        return {"error": "absorb needs --journal-path (the private write journal)"}

    notes = await F.load_label(gx, DevNodeKinds.NOTE)
    drift: List[Dict[str, Any]] = []
    absorbed: List[Dict[str, Any]] = []

    for note in notes:
        slug = F.prop(note, "slug")
        if note_slug and slug != note_slug:
            continue
        note_id = F.nid(note)
        div = await section_divergence(gx, note_id)
        if div.get("error") or div.get("in_sync"):
            continue
        path = div["path"]
        graph_raws = await graph_section_raws(gx, note_id)
        file_raws = file_section_raws(path)
        drift.append({
            "slug": slug, "path": path, "added": div["added"], "removed": div["removed"],
            "changed": [{"anchor": a, "graph": _preview(graph_raws.get(a, "")),
                         "file": _preview(file_raws.get(a, ""))} for a in div["changed"]],
        })

        if not absorbing:
            continue
        targets = (div["changed"] if absorb_all
                   else [a for a in div["changed"] if a in (absorb_anchors or [])])
        if not targets:
            continue
        # Snapshot the file ONCE before changing its source-of-truth relationship.
        dst = Path(backup_dir or Path(path).parent) / f"{Path(path).name}.{int(time.time())}.bak"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)
        for anchor in targets:
            prior, new = graph_raws.get(anchor, ""), file_raws.get(anchor, "")
            await author_section(gx, slug, anchor, new, actor=ABSORB_ACTOR)  # apply to graph now
            append_write(journal_path, "section",
                         {"slug": slug, "anchor": anchor, "raw": new,
                          "actor": ABSORB_ACTOR, "replaces": prior})  # durable, self-describing
            absorbed.append({"slug": slug, "anchor": anchor, "backup": str(dst),
                             "prior_bytes": len(prior.encode("utf-8")),
                             "new_bytes": len(new.encode("utf-8"))})

    return {"notes_with_drift": len(drift), "drift": drift,
            "absorbed": absorbed, "absorbed_count": len(absorbed),
            "clean": len(drift) == 0}
