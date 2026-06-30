"""Structured enumeration: every node of a LABEL / assertion of a PREDICATE / edge of a RELATION.

The class-level read dual of `locate` (which resolves a SINGLE handle): `list` answers
'show me every X of this kind'. This is the query that forced direct SQL this arc —
inspecting the readiness ground truth (the `task_state` assertions, the `GATED_BY`
edges) and whole-graph note enumeration (the prior gap `3343bdb9`, generalized here).
Read-only, bounded by `limit`, agent-formattable. Content search stays with `relevant`;
single-handle resolution stays with `locate`.
"""

from typing import Any, Dict, List, Optional, Set

from cjm_context_graph_layer.ops import graph_task

from . import factlayer as F
from .projection import node_title
from .runtime import GraphHandle


async def _labels_for(
    gx: GraphHandle,
    ids: Set[str],  # Node ids to resolve to display titles
) -> Dict[str, str]:  # id -> best display title (id itself when unresolved)
    """Best display title per id (bounded — only the ids in the shown page)."""
    out: Dict[str, str] = {}
    for nid in ids:
        node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=nid)
        out[nid] = node_title(node) if node is not None else nid
    return out


async def _list_label(gx: GraphHandle, label: str, limit: int) -> Dict[str, Any]:
    """Every node carrying `label` (id + title + on-disk path), bounded by `limit`."""
    nodes = await F.load_label(gx, label, limit=limit + 1)
    rows = [{"id": F.nid(n), "title": node_title(n), "path": F.prop(n, "path")}
            for n in nodes[:limit]]
    return {"mode": "label", "key": label, "rows": rows,
            "count": len(rows), "truncated": len(nodes) > limit}


async def _list_predicate(gx: GraphHandle, predicate: str, limit: int) -> Dict[str, Any]:
    """Every ACTIVE assertion of `predicate` (subject + value + actor), across all slots.

    The readiness-ground-truth read: `list --predicate task_state` shows each work-item's
    current state. Only non-superseded assertions are reported (the slot's live value)."""
    assertions = await F.load_assertions(gx)
    supers = await F.load_supersedes(gx)
    hits: List[Dict[str, Any]] = []
    for slot_assertions in F.group_by_slot(assertions).values():
        active = F.active_assertions(slot_assertions, supers)
        if active and F.prop(active[0], "predicate") == predicate:
            for a in active:
                hits.append({"subject_id": F.prop(a, "subject_id"),
                             "value": F.prop(a, "value"), "actor": F.prop(a, "actor")})
    truncated = len(hits) > limit
    hits = hits[:limit]
    labels = await _labels_for(gx, {h["subject_id"] for h in hits})
    rows = [{**h, "subject": labels.get(h["subject_id"], h["subject_id"])} for h in hits]
    rows.sort(key=lambda r: (r["subject"], str(r["value"])))
    return {"mode": "predicate", "key": predicate, "rows": rows,
            "count": len(rows), "truncated": truncated}


async def _list_relation(gx: GraphHandle, relation: str, limit: int) -> Dict[str, Any]:
    """Every edge of `relation` (source -> target, both labelled), bounded by `limit`.

    The structure read: `list --relation GATED_BY` shows the dependency edges the
    readiness projector derives over."""
    pairs = await F.load_edge_pairs(gx, relation)
    truncated = len(pairs) > limit
    pairs = pairs[:limit]
    labels = await _labels_for(gx, {p for pair in pairs for p in pair})
    rows = [{"source_id": s, "source": labels.get(s, s),
             "target_id": t, "target": labels.get(t, t)} for s, t in pairs]
    rows.sort(key=lambda r: (r["source"], r["target"]))
    return {"mode": "relation", "key": relation, "rows": rows,
            "count": len(rows), "truncated": truncated}


async def list_graph(
    gx: GraphHandle,
    *,
    label: Optional[str] = None,      # Enumerate nodes of this label
    predicate: Optional[str] = None,  # Enumerate active assertions of this predicate
    relation: Optional[str] = None,   # Enumerate edges of this relation type
    limit: int = 50,                  # Cap the row list
) -> Dict[str, Any]:  # {mode, key, rows, count, truncated} or {error}
    """Enumerate one CLASS of the graph: nodes by label / assertions by predicate / edges
    by relation. Exactly one of `label`/`predicate`/`relation` selects the mode."""
    chosen = [(k, v) for k, v in (("label", label), ("predicate", predicate),
                                  ("relation", relation)) if v]
    if len(chosen) != 1:
        return {"error": "pass exactly one of --label / --predicate / --relation",
                "given": [k for k, _ in chosen]}
    mode, key = chosen[0]
    if mode == "label":
        return await _list_label(gx, key, limit)
    if mode == "predicate":
        return await _list_predicate(gx, key, limit)
    return await _list_relation(gx, key, limit)
