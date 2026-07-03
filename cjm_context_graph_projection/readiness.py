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

DoD-as-graph-objects rides the same machinery on the CLOSING side: a `Check`
node hangs off its work item via `CHECKS` (never `GATED_BY` — a DoD gates closing
an item, not starting it; checks are satisfied BY doing the work) and carries its
own `task_state`. Derived, never stored:

    closable ≡ the item is `open` AND it has checks AND every check is `done`
    drift    ≡ the item is `done` AND >=1 of its checks is still `open`

`done` itself stays human-authored — checks VERIFY the judgment (drift) and
surface when it is due (closable); they never auto-close. A `GATED_BY` edge MAY
target a check (partial dependency on one aspect of another item) — checks count
toward gate satisfaction but never appear as frontier work-items themselves.

Same family as `contradictions` / `worklist` / the version oracle: a derived view
over authored facts + edges.
"""

from typing import Any, Dict, List, Optional, Set, Tuple

from cjm_context_graph_layer.ops import graph_task
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.vocab import DevRelations

from . import factlayer as F
from .display import annotate_display, node_title
from .runtime import GraphHandle


def classify_readiness(
    task_state: Dict[str, str],   # work-item id -> its ACTIVE task_state (open | done)
    gates: Dict[str, List[str]],  # work-item id -> the prerequisite ids it is GATED_BY
    hidden: Optional[Set[str]] = None,  # ids to EXCLUDE from the partitions (Check nodes) — they still satisfy gates
) -> Dict[str, List[Dict[str, Any]]]:  # {done, ready, blocked} partitions
    """Pure: partition work-items into done / ready / blocked from authored ground truth.

    A `done` item is done. An `open` item is READY when every gate is done, else
    BLOCKED (naming the unmet gates). A gate id with no `done` task_state counts as
    NOT done — an unmet prerequisite (absence is never silently treated as
    satisfied, so a gate pointing at a non-work-item surfaces as a standing block).
    `hidden` ids (Check nodes — they carry `task_state` too) are never partitioned
    as work-items but DO count toward gate satisfaction, so a `GATED_BY` targeting
    one check of another item works (partial dependency).
    `ready`/`blocked` are computed here and NEVER written back (the derived-field rule)."""
    done_ids: Set[str] = {i for i, s in task_state.items() if s == P.TASK_DONE}
    done, ready, blocked = [], [], []
    for item, state in sorted(task_state.items()):
        if hidden and item in hidden:
            continue
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


def summarize_checks(
    task_state: Dict[str, str],       # node id -> ACTIVE task_state (items AND checks)
    checks_of: Dict[str, List[str]],  # work-item id -> its Check ids (CHECKS edges)
) -> Dict[str, Dict[str, Any]]:  # item id -> {total, done, open: [check ids]}
    """Pure: per-item DoD summary from the checks' own task_states.

    A check with no `done` task_state is open (absence is never satisfied — the
    same absence rule as gates)."""
    out: Dict[str, Dict[str, Any]] = {}
    for item, cids in checks_of.items():
        open_ids = [c for c in sorted(cids) if task_state.get(c) != P.TASK_DONE]
        out[item] = {"total": len(cids), "done": len(cids) - len(open_ids),
                     "open": open_ids}
    return out


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
    nodes = await F.load_nodes(gx, list(ids))
    await annotate_display(gx, list(nodes.values()))
    return {nid: (node_title(nodes[nid]) if nid in nodes else nid) for nid in ids}


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

    # DoD checks: fold into closable/drift, never into the work-item partitions.
    check_pairs = await F.load_edge_pairs(gx, DevRelations.CHECKS)
    checks_of: Dict[str, List[str]] = {}
    for chk, item in check_pairs:
        checks_of.setdefault(item, []).append(chk)
    check_ids: Set[str] = {chk for chk, _ in check_pairs}

    parts = classify_readiness(task_state, gates, hidden=check_ids)
    dod = summarize_checks(task_state, checks_of)

    closable_ids = sorted(
        e["id"] for bucket in (parts["ready"], parts["blocked"]) for e in bucket
        if e["id"] in dod and dod[e["id"]]["open"] == [] )
    drift_pairs = [(e["id"], dod[e["id"]]["open"]) for e in parts["done"]
                   if e["id"] in dod and dod[e["id"]]["open"]]

    ids: Set[str] = {e["id"] for bucket in parts.values() for e in bucket}
    for b in parts["blocked"]:
        ids.update(b["blocked_by"])
    for _, open_checks in drift_pairs:
        ids.update(open_checks)
    labels = await _resolve_labels(gx, ids)

    scope_l = scope.lower() if scope else None

    def _label(nid: str) -> str:
        return labels.get(nid, nid)

    def _keep(nid: str) -> bool:
        return scope_l is None or scope_l in _label(nid).lower()

    def _checks(nid: str) -> Dict[str, Any]:
        d = dod.get(nid)
        return {"checks": {"done": d["done"], "total": d["total"]}} if d else {}

    ready = [{"id": e["id"], "label": _label(e["id"]),
              "gates": [{"id": g, "label": _label(g)} for g in e["gates"]],
              **_checks(e["id"])}
             for e in parts["ready"] if _keep(e["id"])]
    blocked = [{"id": e["id"], "label": _label(e["id"]),
                "blocked_by": [{"id": g, "label": _label(g)} for g in e["blocked_by"]],
                **_checks(e["id"])}
               for e in parts["blocked"] if _keep(e["id"])]
    done = [{"id": e["id"], "label": _label(e["id"]), **_checks(e["id"])}
            for e in parts["done"] if _keep(e["id"])]
    closable = [{"id": i, "label": _label(i), **_checks(i)}
                for i in closable_ids if _keep(i)]
    drift = [{"id": i, "label": _label(i),
              "open_checks": [{"id": c, "label": _label(c)} for c in open_checks]}
             for i, open_checks in drift_pairs if _keep(i)]

    return {"ready": ready, "blocked": blocked, "done": done,
            "closable": closable, "drift": drift,
            "counts": {"ready": len(ready), "blocked": len(blocked), "done": len(done),
                       "closable": len(closable), "drift": len(drift)}}
