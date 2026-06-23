"""The propose/confirm worklist: candidate fixes that need a human decision.

The graph makes rot queryable that the flat files hid; the worklist is where that
rot is triaged WITHOUT auto-guessing (the substrate dialect: surface a candidate,
let a human confirm; never silently normalize). Minimal in the cut, three sources:

- **dangling references** — `[[wiki-links]]` whose slug resolves to no note, with a
  fuzzy "probably means Y" suggestion (the corpus-findings finding 1 triage).
  Confirming one writes an alias / fixes the link; it is never applied here.
- **soft conflicts** — UNTYPED slots whose active assertions disagree. Can't be
  adjudicated mechanically (no value-space), so they propose rather than contradict.
- **untyped predicates in use** — predicates carrying real assertions but not yet
  typed: candidates to pull into the typed registry (a real conflict types it).
"""

import difflib
from pathlib import Path
from typing import Any, Dict, List, Optional

from cjm_dev_graph_schema import predicates as P
from cjm_markdown_decompose_core.extract import note_from_file

from . import factlayer as F
from .runtime import GraphHandle


def dangling_reference_proposals(
    memory_dir: str,    # Dir of memory markdown files
    cutoff: float = 0.6,  # difflib similarity cutoff for a suggestion
) -> List[Dict[str, Any]]:  # [{from, missing, suggestion, score}]
    """Referenced `[[slugs]]` with no note, each with a fuzzy suggestion (no auto-fix).

    Reads the declared-slug universe from the CORPUS on disk, not from Note nodes
    in the graph — deliberately: the store SILENTLY DROPS edges to absent targets
    on ingest, so a dangling `[[link]]` leaves no queryable trace in the graph. The
    corpus is the only place that still knows a link was attempted. (When dangling
    links become stub nodes, this can move on-graph.)"""
    mem = Path(memory_dir)
    files = sorted(p for p in mem.glob("*.md") if p.name != "MEMORY.md")
    notes = [note_from_file(str(p), corpus_root=str(mem)) for p in files]
    declared = {n.slug for n in notes}
    out: List[Dict[str, Any]] = []
    for n in notes:
        for ref in n.references:
            if ref in declared:
                continue
            match = difflib.get_close_matches(ref, declared, n=1, cutoff=cutoff)
            score = round(difflib.SequenceMatcher(None, ref, match[0]).ratio(), 3) if match else 0.0
            out.append({"from": n.slug, "missing": ref,
                        "suggestion": match[0] if match else None, "score": score})
    out.sort(key=lambda d: d["score"], reverse=True)
    return out


async def _slot_soft_signals(gx: GraphHandle) -> Dict[str, List[Dict[str, Any]]]:
    """Untyped-slot soft conflicts + the set of untyped predicates carrying assertions."""
    assertions = await F.load_assertions(gx)
    supers = await F.load_supersedes(gx)
    by_slot = F.group_by_slot(assertions)
    soft: List[Dict[str, Any]] = []
    untyped: Dict[str, int] = {}
    for slot_id, slot_assertions in by_slot.items():
        active = F.active_assertions(slot_assertions, supers)
        predicate = F.prop(active[0], "predicate", "") if active else ""
        if predicate and not P.is_typed(predicate):
            untyped[predicate] = untyped.get(predicate, 0) + 1
            values = [F.prop(a, "value", "") for a in active]
            if P.soft_conflict(predicate, values):
                soft.append({"slot_id": slot_id, "subject_id": F.prop(active[0], "subject_id"),
                             "predicate": predicate,
                             "values": sorted({F.prop(a, "value") for a in active})})
    return {"soft": soft,
            "untyped": [{"predicate": k, "slots": v} for k, v in sorted(untyped.items())]}


async def worklist(
    gx: GraphHandle,
    memory_dir: Optional[str] = None,  # Corpus dir for the dangling-reference triage (None = skip it)
) -> Dict[str, Any]:  # {dangling_references, soft_conflicts, untyped_predicates}
    """Assemble the propose/confirm worklist (graph signals + optional corpus triage)."""
    signals = await _slot_soft_signals(gx)
    dangling = dangling_reference_proposals(memory_dir) if memory_dir else []
    return {
        "dangling_references": dangling,
        "soft_conflicts": signals["soft"],
        "untyped_predicates": signals["untyped"],
        "counts": {"dangling_references": len(dangling),
                   "soft_conflicts": len(signals["soft"]),
                   "untyped_predicates": len(signals["untyped"])},
    }
