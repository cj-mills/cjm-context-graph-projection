"""The standing dedup query: slots whose ACTIVE assertions disagree.

A contradiction is mechanical for typed value-spaces (the high-pain cases): a
slot with >=2 active (non-superseded) assertions whose values are incompatible
AND not related by a known ordering. Ordered predicates (version) never
contradict — the newer just supersedes. Untyped predicates produce SOFT signals
(routed to the worklist), never hard contradictions.

This is the failure mode the arc exists to make structurally visible: the
torch/hf-utils "keep vs rename" case lands here as a single query result instead
of three drifting doc copies a human has to reconcile by hand.
"""

from typing import Any, Dict, List, Optional

from cjm_dev_graph_schema import predicates as P

from . import factlayer as F
from .display import node_title
from .runtime import GraphHandle


def _subject_label(slot_assertions: List[Any], entities_by_id: Dict[str, Any]) -> str:
    """Best display label for a slot's subject (from the entity, else the carried id).

    Folded onto the one display seam (`node_title` — its cascade covers the old
    name/key fallbacks), so a subject renders the same here as everywhere else."""
    if not slot_assertions:
        return "?"
    subj = F.prop(slot_assertions[0], "subject_id")
    ent = entities_by_id.get(subj)
    if ent is not None:
        return node_title(ent)
    return subj or "?"


async def contradictions(
    gx: GraphHandle,
    scope: Optional[str] = None,  # Restrict to subjects/predicates matching this term (substring)
) -> Dict[str, Any]:  # {contradictions: [...], count}
    """All slots whose active assertions form a hard contradiction (optionally scoped)."""
    assertions = await F.load_assertions(gx)
    supers = await F.load_supersedes(gx)
    _, entities_by_id = await F.alias_index(gx)
    by_slot = F.group_by_slot(assertions)

    scope_l = scope.lower() if scope else None
    out: List[Dict[str, Any]] = []
    for slot_id, slot_assertions in by_slot.items():
        active = F.active_assertions(slot_assertions, supers)
        if len(active) < 2:
            continue
        predicate = F.prop(active[0], "predicate", "")
        values = [F.prop(a, "value", "") for a in active]
        if not P.active_contradiction(predicate, values):
            continue
        subject_label = _subject_label(active, entities_by_id)
        if scope_l and scope_l not in subject_label.lower() and scope_l not in predicate.lower():
            continue
        out.append({
            "slot_id": slot_id,
            "subject_id": F.prop(active[0], "subject_id"),
            "subject": subject_label,
            "predicate": predicate,
            "assertions": [{"assertion_id": F.nid(a), "value": F.prop(a, "value"),
                            "actor": F.prop(a, "actor")} for a in active],
        })
    out.sort(key=lambda c: (c["subject"], c["predicate"]))
    return {"contradictions": out, "count": len(out)}
