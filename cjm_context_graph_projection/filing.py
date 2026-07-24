"""Filing reconciler: propose PART_OF program anchors for unfiled work items.

PART_OF filing (DEC-RATIF `f9ce3f22`, rides bounded readiness `707327ea`): work
items/findings link to a SMALL set of program anchors (role-asserted nodes —
north stars / arcs / portfolio objects) via `PART_OF`, ONE primary link per item
as discipline. Filing is NOT required at mint — capture stays frictionless, and
forced links are worse than missing links — so the nudge lives in three
mechanisms instead of a gate: the readiness view's visible `unfiled` group,
THIS reconciler, and filing joining the session-end ritual.

The propose/confirm instrument (register-drift / orphaned-edges mold):

    unfiled  = an OPEN work-item with no PART_OF edge onto any anchor
    proposal = an anchor scored from the item's REFERENCES/SHAPES neighborhood
    refile   = a FILED item whose neighborhood now scores a DIFFERENT anchor
               strictly higher than every anchor it currently carries (late
               binding is a feature: the proper program may not exist at mint
               and may change over time)

Scoring is evidence-counted, never guessed: a filed neighbor votes for its own
anchor (the compounding signal — every confirmed filing sharpens the next
proposal), a direct REFERENCES/SHAPES edge onto an anchor outranks a single
vote, and a neighbor merely citing an anchor is weak bootstrap evidence.
Confirming a proposal means minting the edge yourself:

    link <item-id> PART_OF <anchor-id>

Same family as `register-drift` / `readiness`: a derived view over authored
facts + edges; there is no write path here.
"""

from typing import Any, Dict, List, Set, Tuple

from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.vocab import DevRelations

from . import factlayer as F
from .display import annotate_display, node_title
from .readiness import _active_task_states, _last_touch
from .registers import ROLE_PREDICATE
from .runtime import GraphHandle

ANCHOR_ROLES = ("program", "arc", "north-star")
PART_OF = "PART_OF"
SHAPES = "SHAPES"

WEIGHT_DIRECT = 2.0  # the item's own neighborhood contains the anchor
WEIGHT_VOTE = 1.0    # a neighbor FILED under the anchor votes for it
WEIGHT_CITES = 0.5   # a neighbor cites the anchor without being filed under it


def derive_anchors(
    assertions: List[Any],              # All Assertion nodes
    supersedes: List[Tuple[str, str]],  # All SUPERSEDES (superseder, superseded) pairs
) -> Tuple[Set[str], Dict[str, str]]:  # (anchor ids, anchor id -> role value)
    """The program-anchor set: subjects whose ACTIVE `role` is one of ANCHOR_ROLES.

    Shared with `readiness` (which lazily imports it to annotate the frontier's
    program grouping) — the anchor vocabulary lives here, with the reconciler."""
    anchors: Set[str] = set()
    roles: Dict[str, str] = {}
    for slot_assertions in F.group_by_slot(assertions).values():
        if F.prop(slot_assertions[0], "predicate") != ROLE_PREDICATE:
            continue
        for a in F.active_assertions(slot_assertions, supersedes):
            value = F.prop(a, "value", "")
            if value in ANCHOR_ROLES:
                subject = F.prop(a, "subject_id")
                if subject:
                    anchors.add(subject)
                    roles[subject] = value
    return anchors, roles


def classify_filing(
    open_items: Set[str],              # OPEN work-item/finding ids (Check nodes excluded)
    anchors: Set[str],                 # role-asserted program-anchor node ids
    filed_under: Dict[str, Set[str]],  # node id -> the anchors it is PART_OF (any node, not just items)
    neighbors: Dict[str, Set[str]],    # node id -> its REFERENCES/SHAPES neighborhood (undirected)
    top_k: int = 3,                    # proposals kept per item
) -> Dict[str, Any]:  # {unfiled: [...], refile: [...], counts}
    """Pure: partition open items into filed/unfiled and score anchor proposals.

    Evidence per anchor accumulates: WEIGHT_DIRECT when the item's own
    neighborhood contains the anchor, WEIGHT_VOTE per neighbor filed under it,
    WEIGHT_CITES per neighbor that merely cites it. Proposes only — confirming
    mints the PART_OF edge elsewhere. A refile is proposed when a filed item's
    best anchor STRICTLY outscores every anchor it currently carries (ties keep
    the standing filing — re-filing is supersession, never churn)."""
    unfiled: List[Dict[str, Any]] = []
    refile: List[Dict[str, Any]] = []
    for item in sorted(open_items):
        current = filed_under.get(item, set()) & anchors
        hood = neighbors.get(item, set()) - {item}
        scores: Dict[str, float] = {}
        evidence: Dict[str, List[Dict[str, Any]]] = {}

        def _add(anchor: str, kind: str, weight: float, via: str = "") -> None:
            scores[anchor] = scores.get(anchor, 0.0) + weight
            evidence.setdefault(anchor, []).append(
                {"kind": kind, **({"via": via} if via else {})})

        for a in sorted(hood & anchors):
            _add(a, "direct", WEIGHT_DIRECT)
        for n in sorted(hood - anchors):
            n_filed = filed_under.get(n, set()) & anchors
            for a in sorted(n_filed):
                _add(a, "vote", WEIGHT_VOTE, via=n)
            for a in sorted((neighbors.get(n, set()) & anchors) - n_filed):
                _add(a, "cites", WEIGHT_CITES, via=n)

        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        proposals = [{"anchor_id": a, "score": round(s, 2), "evidence": evidence[a]}
                     for a, s in ranked[:top_k]]
        if not current:
            unfiled.append({"id": item, "proposals": proposals})
            continue
        if ranked:
            best_id, best_score = ranked[0]
            if best_id not in current and all(
                    best_score > scores.get(c, 0.0) for c in current):
                refile.append({
                    "id": item, "current": sorted(current),
                    "proposal": {"anchor_id": best_id, "score": round(best_score, 2),
                                 "evidence": evidence[best_id]}})
    counts = {"open_items": len(open_items), "anchors": len(anchors),
              "filed": len(open_items) - len(unfiled), "unfiled": len(unfiled),
              "with_proposal": sum(1 for u in unfiled if u["proposals"]),
              "refile": len(refile)}
    return {"unfiled": unfiled, "refile": refile, "counts": counts}


async def filing(
    gx: GraphHandle,
    top_k: int = 3,  # Proposals kept per unfiled item
) -> Dict[str, Any]:  # {unfiled, refile, anchors, counts}
    """The derived filing report over task_state subjects + PART_OF/REFERENCES/SHAPES edges.

    Pure read: derives the OPEN work-item population exactly as `readiness` does
    (active `task_state`, Check nodes excluded — done items are history, not
    filing debt), gathers the role-asserted anchor set, the current PART_OF
    filings and the REFERENCES/SHAPES neighborhoods, classifies, then decorates
    every id with a display label. Unfiled items come back newest-touch first —
    the session-end ritual files the session's new items, which are exactly the
    recently touched ones."""
    assertions = await F.load_assertions(gx)
    supers = await F.load_supersedes(gx)
    task_state = _active_task_states(assertions, supers)
    check_ids = {chk for chk, _ in await F.load_edge_pairs(gx, DevRelations.CHECKS)}
    open_items = {i for i, s in task_state.items()
                  if s != P.TASK_DONE} - check_ids

    anchors, anchor_roles = derive_anchors(assertions, supers)

    filed_under: Dict[str, Set[str]] = {}
    for src, tgt in await F.load_edge_pairs(gx, PART_OF):
        if tgt in anchors:
            filed_under.setdefault(src, set()).add(tgt)

    neighbors: Dict[str, Set[str]] = {}
    for rel in (DevRelations.REFERENCES, SHAPES):
        for src, tgt in await F.load_edge_pairs(gx, rel):
            neighbors.setdefault(src, set()).add(tgt)
            neighbors.setdefault(tgt, set()).add(src)

    report = classify_filing(open_items, anchors, filed_under, neighbors, top_k=top_k)
    touch = _last_touch(assertions)
    report["unfiled"].sort(key=lambda u: touch.get(u["id"], 0.0), reverse=True)

    ids: Set[str] = set(anchors)
    for u in report["unfiled"]:
        ids.add(u["id"])
        ids.update(p["anchor_id"] for p in u["proposals"])
    for r in report["refile"]:
        ids.add(r["id"])
        ids.update(r["current"])
        ids.add(r["proposal"]["anchor_id"])
    nodes = await F.load_nodes(gx, list(ids))
    await annotate_display(gx, list(nodes.values()))
    labels = {i: (node_title(nodes[i]) if i in nodes else i) for i in ids}

    for u in report["unfiled"]:
        u["label"] = labels.get(u["id"], u["id"])
        for p in u["proposals"]:
            p["label"] = labels.get(p["anchor_id"], p["anchor_id"])
    for r in report["refile"]:
        r["label"] = labels.get(r["id"], r["id"])
        r["current"] = [{"id": c, "label": labels.get(c, c)} for c in r["current"]]
        r["proposal"]["label"] = labels.get(r["proposal"]["anchor_id"],
                                            r["proposal"]["anchor_id"])
    report["anchors"] = sorted(
        ({"id": a, "role": anchor_roles.get(a, ""), "label": labels.get(a, a)}
         for a in anchors),
        key=lambda e: (e["role"], e["label"]))
    return report
