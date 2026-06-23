"""The write surface: `assert` a slot value, `decide` a conclusion.

Both are idempotent (deterministic ids + `extend_graph`): re-asserting the same
value (same actor) is a verified no-op; a different value mints a new Assertion =
the potential conflict. Subjects resolve through the rename-stable alias machinery
(old names + variant slugs all land on one entity); an unresolved subject mints a
lightweight `term` Entity rather than failing.

Write-time conflict UX = WARN-RECORD-FLAG, never block (the arc-plan lock): the
assertion is written, a detected hard conflict is RECORDED as CONTRADICTS edges
and RETURNED in the result, and the caller is forced to see it without a hard stop.
Ordered predicates (version) auto-supersede older values, so a healthy bump is
never a conflict; unordered predicates (rename-disposition) flag genuine
disagreement; untyped predicates report a SOFT signal (worklist, not a hard edge).
"""

import re
from typing import Any, Dict, List, Optional

from cjm_context_graph_layer.grammar import make_edge
from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.aliases import resolve_subject_id
from cjm_dev_graph_schema.nodes import (AssertionNode, DecisionNode, EntityNode,
                                        FactSlotNode, SessionNode)
from cjm_dev_graph_schema.vocab import DevRelations

from . import factlayer as F
from .runtime import GraphHandle


def _term_slug(text: str) -> str:
    """A stable conceptual slug for an unresolved subject (mint a `term` entity)."""
    s = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return s or "unnamed"


async def resolve_subject(
    gx: GraphHandle,
    subject: str,  # A node id, an entity key/name/alias, or a free term
) -> Dict[str, Any]:  # {subject_id, subject_label, created_node|None}
    """Resolve a subject to an entity id (rename-stable), minting a `term` entity
    if it resolves to nothing — so `assert` always has a real subject node."""
    # 1. Already a node id?
    existing = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=subject)
    if existing is not None:
        label = (F.prop(existing, "name") or F.prop(existing, "title")
                 or F.prop(existing, "key") or subject)
        return {"subject_id": subject, "subject_label": label, "created_node": None}
    # 2. Resolve via the alias index (key / current name / prior name / variant slug).
    index, _ = await F.alias_index(gx)
    rid = resolve_subject_id(index, subject)
    if rid is not None:
        return {"subject_id": rid, "subject_label": subject, "created_node": None}
    # 3. Unresolved -> mint a term entity (don't fail; don't guess an existing one).
    ent = EntityNode(kind="term", key=_term_slug(subject), name=subject)
    return {"subject_id": ent.id, "subject_label": subject, "created_node": ent.to_graph_node()}


def _match_supersede_targets(
    targets: List[str],            # Caller-named ids OR values to supersede
    slot_assertions: List[Any],    # Existing assertions in the slot
    predicate: str,
) -> List[str]:
    """Resolve `--supersede` tokens (assertion ids OR values) to assertion ids."""
    by_id = {F.nid(a): a for a in slot_assertions}
    by_canon: Dict[str, str] = {}
    for a in slot_assertions:
        by_canon.setdefault(P.canonical_value(predicate, F.prop(a, "value", "")), F.nid(a))
    out: List[str] = []
    for t in targets:
        if t in by_id:
            out.append(t)
        else:
            cid = by_canon.get(P.canonical_value(predicate, t))
            if cid:
                out.append(cid)
    return out


async def assert_value(
    gx: GraphHandle,
    subject: str,        # Subject (node id / entity key / name / alias / term)
    predicate: str,      # Predicate slug
    value: str,          # The claimed value
    *,
    actor: str = "agent:session",       # Who is claiming it
    evidence: Optional[List[str]] = None,  # Source-note/session/evidence node ids supporting the claim
    supersede: Optional[List[str]] = None, # Prior assertion ids OR values this claim supersedes
    asserted_at: Optional[float] = None,   # Override the timestamp (oracle uses last_verified semantics)
    method: Optional[str] = None,          # Derivation method (oracle/programmatic)
) -> Dict[str, Any]:  # The write result (incl. any conflict, warn-record-flag)
    """Write one value to a `(subject, predicate)` slot, recording any conflict.

    Auto-supersedes older values on ordered predicates; records CONTRADICTS +
    returns the conflict on unordered disagreement; reports a soft signal on
    untyped disagreement. Idempotent on re-assertion of the same value+actor."""
    r = await resolve_subject(gx, subject)
    subject_id, subject_label, created = r["subject_id"], r["subject_label"], r["created_node"]

    slot = FactSlotNode(subject_id=subject_id, predicate=predicate, subject_label=subject_label)
    assertion = AssertionNode(slot_id=slot.id, value=value, actor=actor, predicate=predicate,
                              subject_id=subject_id, asserted_at=asserted_at, method=method,
                              last_verified=(asserted_at if method else None))

    # Existing state of the slot BEFORE this write.
    all_assertions = await F.load_assertions(gx)
    supers = await F.load_supersedes(gx)
    slot_existing = [a for a in all_assertions if F.prop(a, "slot_id") == slot.id]
    active_existing = F.active_assertions(slot_existing, supers)

    nodes: List[Dict[str, Any]] = []
    if created:
        nodes.append(created)
    nodes.append(slot.to_graph_node())
    nodes.append(assertion.to_graph_node())

    edges: List[Dict[str, Any]] = [slot.about_edge(), assertion.on_slot_edge()]
    edges += assertion.evidenced_by_edges(evidence or [])

    superseded_ids: List[str] = []
    born_superseded = False

    # Ordered predicate: newer auto-supersedes older active values (healthy evolution).
    if P.is_ordered(predicate):
        for old in active_existing:
            old_id, old_val = F.nid(old), F.prop(old, "value", "")
            if old_id == assertion.id:
                continue
            verdict = P.ordering_supersedes(predicate, value, old_val)
            if verdict is True:
                edges.append(assertion.supersedes_edge(old_id))
                superseded_ids.append(old_id)
            elif verdict is False:
                # New value is older than an existing active one -> born superseded.
                edges.append(make_edge(old_id, assertion.id, DevRelations.SUPERSEDES))
                born_superseded = True

    # Explicit supersede targets (ids or values).
    for tid in _match_supersede_targets(supersede or [], slot_existing, predicate):
        if tid != assertion.id and tid not in superseded_ids:
            edges.append(assertion.supersedes_edge(tid))
            superseded_ids.append(tid)

    res = await extend_graph(gx.queue, gx.graph_id, nodes, edges)

    # Write-time conflict check (warn-record-flag): recompute the active set.
    all_after = await F.load_assertions(gx)
    supers_after = await F.load_supersedes(gx)
    slot_after = [a for a in all_after if F.prop(a, "slot_id") == slot.id]
    active_after = F.active_assertions(slot_after, supers_after)

    conflict: List[Dict[str, Any]] = []
    soft = False
    if not born_superseded:
        active_vals = [F.prop(a, "value", "") for a in active_after]
        if P.active_contradiction(predicate, active_vals):
            my_canon = assertion.canonical
            conflict_edges: List[Dict[str, Any]] = []
            for a in active_after:
                if F.nid(a) == assertion.id:
                    continue
                if P.canonical_value(predicate, F.prop(a, "value", "")) != my_canon:
                    conflict.append({"assertion_id": F.nid(a), "value": F.prop(a, "value"),
                                     "actor": F.prop(a, "actor")})
                    conflict_edges.append(assertion.contradicts_edge(F.nid(a)))
            if conflict_edges:
                await extend_graph(gx.queue, gx.graph_id, [], conflict_edges)
        elif P.soft_conflict(predicate, active_vals):
            soft = True

    return {
        "subject": subject, "subject_id": subject_id, "slot_id": slot.id,
        "predicate": predicate, "value": value, "actor": actor,
        "assertion_id": assertion.id, "created_subject": created is not None,
        "nodes_added": res.nodes_added, "edges_added": res.edges_added,
        "superseded": superseded_ids, "born_superseded": born_superseded,
        "conflict": conflict, "soft_conflict": soft,
    }


async def decide(
    gx: GraphHandle,
    statement: str,   # The decision statement
    *,
    actor: str = "agent:session",
    supports: Optional[List[str]] = None,    # Premise Assertion ids the decision rests on
    supersedes: Optional[List[str]] = None,  # Prior Decision ids this one replaces
    session: Optional[str] = None,           # Session key this was decided in
) -> Dict[str, Any]:  # The write result
    """Record a Decision + its `SUPPORTED_BY` premise edges (reasoning substrate).

    Minimal in the cut: bank the premise edges now; the premise-drift checker is
    deferred. Idempotent on the canonical statement."""
    decision = DecisionNode(statement=statement, actor=actor)
    nodes: List[Dict[str, Any]] = [decision.to_graph_node()]
    edges: List[Dict[str, Any]] = decision.supported_by_edges(supports or [])
    edges += [decision.supersedes_edge(s) for s in (supersedes or [])]
    if session:
        sess = SessionNode(key=session)
        nodes.append(sess.to_graph_node())
        edges.append(decision.decided_in_edge(sess.id))
    res = await extend_graph(gx.queue, gx.graph_id, nodes, edges)
    return {"decision_id": decision.id, "statement": statement, "actor": actor,
            "supports": supports or [], "supersedes": supersedes or [],
            "session": session, "nodes_added": res.nodes_added, "edges_added": res.edges_added}
