"""The readiness frontier: which work-items are READY vs BLOCKED — derived, never stored.

The spine of the dogfood-arc roadmap (DEC-REPR `a7ca00d1`): author the MINIMAL
ground truth, DERIVE the rest. A work-item carries an authored `task_state`
(`open` -> `done`) and dedicated `GATED_BY` edges to its prerequisites. `ready`
vs `blocked` is **never stored** — it is COMPUTED on read here:

    ready  ≡ the item is `open` AND every GATED_BY prerequisite is `done`
    blocked ≡ the item is `open` AND >=1 prerequisite is not yet `done`

Nothing "fires": mark one prerequisite `done` and every transitive dependent is
ALREADY on the next frontier query — no unlock event, no cascade write, no stale
state. A pure read can't corrupt (strictly safer than the file-mutating refactor
ops). This is the never-hand-maintain-a-derived-field RULE (DEC-RULE `eb25ea0d`)
satisfied at layer 1: there is simply no write path for ready/blocked.

Same family as `contradictions` / `worklist` / the version oracle: a derived view
over authored facts + edges.
"""

from typing import Any, Dict, List, Optional, Set, Tuple

from cjm_context_graph_layer.ops import graph_task
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.vocab import DevRelations

from . import factlayer as F
from .projection import node_title
from .runtime import GraphHandle


def classify_readiness(
    task_state: Dict[str, str],   # work-item id -> its ACTIVE task_state (open | done)
    gates: Dict[str, List[str]],  # work-item id -> the prerequisite ids it is GATED_BY
) -> Dict[str, List[Dict[str, Any]]]:  # {done, ready, blocked} partitions
    """Pure: partition work-items into done / ready / blocked from authored ground truth.

    A `done` item is done. An `open` item is READY when every gate is done, else
    BLOCKED (naming the unmet gates). A gate id with no `done` task_state counts as
    NOT done — an unmet prerequisite (absence is never silently treated as
    satisfied, so a gate pointing at a non-work-item surfaces as a standing block).
    `ready`/`blocked` are computed here and NEVER written back (the derived-field rule)."""
    done_ids: Set[str] = {i for i, s in task_state.items() if s == P.TASK_DONE}
    done, ready, blocked = [], [], []
    for item, state in sorted(task_state.items()):
        if state == P.TASK_DONE:
            done.append({"id": item})
            continue
        item_gates = gates.get(item, [])
        unmet = [g for g in item_gates if g not in done_ids]
        if unmet:
            blocked.append({"id": item, "blocked_by": unmet})
        else:
            ready.append({"id": item, "gates": item_gates})
    return {"done": done, "ready": ready, "blocked": blocked}


def _active_task_states(
    assertions: List[Any],                      # All Assertion nodes
    supersedes: List[Tuple[str, str]],          # All SUPERSEDES (superseder, superseded) pairs
) -> Dict[str, str]:  # subject_id -> active task_state value
    """Each work-item's ACTIVE `task_state` (resolving supersession; `done` won a bump)."""
    out: Dict[str, str] = {}
    for slot_assertions in F.group_by_slot(assertions).values():
        active = F.active_assertions(slot_assertions, supersedes)
        if active and F.prop(active[0], "predicate") == P.TASK_STATE:
            out[F.prop(active[0], "subject_id")] = F.prop(active[0], "value", "")
    return out


async def _resolve_labels(
    gx: GraphHandle,
    ids: Set[str],  # Node ids to label
) -> Dict[str, str]:  # id -> best display title (id itself when unresolved)
    """Best display title for each id (the work-items + their gate targets)."""
    out: Dict[str, str] = {}
    for nid in ids:
        node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=nid)
        out[nid] = node_title(node) if node is not None else nid
    return out


async def readiness(
    gx: GraphHandle,
    scope: Optional[str] = None,  # Restrict to work-items whose label matches this term (substring)
) -> Dict[str, Any]:  # {ready, blocked, done, counts}
    """The derived ready/blocked/done frontier over authored `task_state` + `GATED_BY` edges.

    Pure read: loads the active task states and the gate edges (`GATED_BY` plus its
    reserved synonym `BLOCKED_BY`), classifies, then decorates each entry with a
    display label. `scope` narrows to work-items whose label matches (the frontier
    is small, but the dependency forest will grow)."""
    assertions = await F.load_assertions(gx)
    supers = await F.load_supersedes(gx)
    task_state = _active_task_states(assertions, supers)

    gate_pairs = (await F.load_edge_pairs(gx, DevRelations.GATED_BY)
                  + await F.load_edge_pairs(gx, DevRelations.BLOCKED_BY))
    gates: Dict[str, List[str]] = {}
    for src, tgt in gate_pairs:
        gates.setdefault(src, []).append(tgt)

    parts = classify_readiness(task_state, gates)

    ids: Set[str] = {e["id"] for bucket in parts.values() for e in bucket}
    for b in parts["blocked"]:
        ids.update(b["blocked_by"])
    labels = await _resolve_labels(gx, ids)

    scope_l = scope.lower() if scope else None

    def _label(nid: str) -> str:
        return labels.get(nid, nid)

    def _keep(nid: str) -> bool:
        return scope_l is None or scope_l in _label(nid).lower()

    ready = [{"id": e["id"], "label": _label(e["id"]),
              "gates": [{"id": g, "label": _label(g)} for g in e["gates"]]}
             for e in parts["ready"] if _keep(e["id"])]
    blocked = [{"id": e["id"], "label": _label(e["id"]),
                "blocked_by": [{"id": g, "label": _label(g)} for g in e["blocked_by"]]}
               for e in parts["blocked"] if _keep(e["id"])]
    done = [{"id": e["id"], "label": _label(e["id"])}
            for e in parts["done"] if _keep(e["id"])]

    return {"ready": ready, "blocked": blocked, "done": done,
            "counts": {"ready": len(ready), "blocked": len(blocked), "done": len(done)}}
