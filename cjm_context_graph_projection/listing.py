"""Structured enumeration: every node of a LABEL / assertion of a PREDICATE / edge of a RELATION.

The class-level read dual of `locate` (which resolves a SINGLE handle): `list` answers
'show me every X of this kind'. This is the query that forced direct SQL this arc —
inspecting the readiness ground truth (the `task_state` assertions, the `GATED_BY`
edges) and whole-graph note enumeration (the prior gap `3343bdb9`, generalized here).
Read-only, bounded by `limit`, agent-formattable. Content search stays with `relevant`;
single-handle resolution stays with `locate`.
"""

from typing import Any, Dict, List, Optional, Set, Tuple

from cjm_context_graph_primitives.query import PropertyPredicate

from . import factlayer as F
from .display import annotate_display, node_title
from .runtime import GraphHandle


def parse_where(
    clauses: Optional[List[str]],  # Raw `PROP=VALUE` clauses (repeatable, ANDed)
) -> Tuple[List[PropertyPredicate], Optional[str]]:  # (predicates, error)
    """Parse `--where PROP=VALUE` clauses into property predicates (op `eq`, AND).

    Dotted PROP paths descend nested property JSON (the predicate's own contract).
    v1 is equality-only — the op-carrying form waits for a felt demand."""
    preds: List[PropertyPredicate] = []
    for c in clauses or []:
        if "=" not in c:
            return [], f"--where expects PROP=VALUE (got {c!r})"
        prop, value = c.split("=", 1)
        if not prop:
            return [], f"--where expects PROP=VALUE (got {c!r})"
        preds.append(PropertyPredicate(prop=prop, op="eq", value=value))
    return preds, None


async def _labels_for(
    gx: GraphHandle,
    ids: Set[str],  # Node ids to resolve to display titles
) -> Dict[str, str]:  # id -> best display title (id itself when unresolved)
    """Best display title per id (bounded — only the ids in the shown page)."""
    nodes = await F.load_nodes(gx, list(ids))
    await annotate_display(gx, list(nodes.values()))
    return {nid: (node_title(nodes[nid]) if nid in nodes else nid) for nid in ids}


async def _list_label(gx: GraphHandle, label: str, limit: int, offset: int = 0,
                      contains: Optional[str] = None,
                      where: Optional[List[PropertyPredicate]] = None) -> Dict[str, Any]:
    """Nodes carrying `label`, windowed by `offset`+`limit`, filtered by property
    predicates (`where`, server-side) and/or title substring (`contains`, client-side).

    The window is what keeps a thousands-strong kind browsable at a fixed budget:
    page with `offset`, narrow with `--where prop=value` (the query machinery the
    primitives always had, exposed) or `contains` (case-insensitive title substring —
    that filter scans the whole label, so the window is over the MATCHES). `total` is
    the TRUE class/match size, never the page size."""
    preds = list(where or [])
    if contains:
        # Title filtering needs display annotation, so load all matches client-side;
        # the true total is the match count.
        nodes = (await F.load_label_where(gx, label, preds, limit=1_000_000) if preds
                 else await F.load_label(gx, label, limit=1_000_000))
        await annotate_display(gx, nodes)
        c = contains.lower()
        nodes = [n for n in nodes if c in node_title(n).lower()]
        total = len(nodes)
        window = nodes[offset:offset + limit]
    else:
        # Server-side page + a count query for the true total (two bounded round-trips).
        total = await F.count_label(gx, label, preds)
        window = (await F.load_label_where(gx, label, preds, limit=limit, offset=offset)
                  if preds else
                  (await F.load_label(gx, label, limit=offset + limit))[offset:offset + limit])
        await annotate_display(gx, window)
    # `key` rides along where the node carries one (Session keys, Lens slugs):
    # a picker/consumer must bind the DURABLE key, never the display title —
    # titles are presentation and may change under it (the session-picker lesson).
    rows = [{"id": F.nid(n), "title": node_title(n), "path": F.prop(n, "path"),
             **({"key": F.prop(n, "key")} if F.prop(n, "key") is not None else {}),
             **({"gloss": F.prop(n, "display_gloss")} if F.prop(n, "display_gloss") else {})}
            for n in window]
    return {"mode": "label", "key": label, "rows": rows, "count": len(rows),
            "total": total, "offset": offset, "contains": contains,
            "where": [p.to_dict() for p in preds],
            "truncated": total > offset + len(rows)}


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
    total = len(hits)
    hits = hits[:limit]
    labels = await _labels_for(gx, {h["subject_id"] for h in hits})
    rows = [{**h, "subject": labels.get(h["subject_id"], h["subject_id"])} for h in hits]
    rows.sort(key=lambda r: (r["subject"], str(r["value"])))
    return {"mode": "predicate", "key": predicate, "rows": rows,
            "count": len(rows), "total": total, "truncated": total > limit}


async def _list_relation(gx: GraphHandle, relation: str, limit: int) -> Dict[str, Any]:
    """Every edge of `relation` (source -> target, both labelled), bounded by `limit`.

    The structure read: `list --relation GATED_BY` shows the dependency edges the
    readiness projector derives over."""
    pairs = await F.load_edge_pairs(gx, relation)
    total = len(pairs)
    pairs = pairs[:limit]
    labels = await _labels_for(gx, {p for pair in pairs for p in pair})
    rows = [{"source_id": s, "source": labels.get(s, s),
             "target_id": t, "target": labels.get(t, t)} for s, t in pairs]
    rows.sort(key=lambda r: (r["source"], r["target"]))
    return {"mode": "relation", "key": relation, "rows": rows,
            "count": len(rows), "total": total, "truncated": total > limit}


async def list_graph(
    gx: GraphHandle,
    *,
    label: Optional[str] = None,      # Enumerate nodes of this label
    predicate: Optional[str] = None,  # Enumerate active assertions of this predicate
    relation: Optional[str] = None,   # Enumerate edges of this relation type
    limit: int = 50,                  # Cap the row list (the window size for label mode)
    offset: int = 0,                  # Label mode: window start (page through big kinds)
    contains: Optional[str] = None,   # Label mode: title substring filter (case-insensitive)
    where: Optional[List[str]] = None,  # Label mode: `PROP=VALUE` property filters (repeatable, ANDed, server-side)
) -> Dict[str, Any]:  # {mode, key, rows, count, total, truncated} or {error}
    """Enumerate one CLASS of the graph: nodes by label / assertions by predicate / edges
    by relation. Exactly one of `label`/`predicate`/`relation` selects the mode; `total`
    always reports the TRUE class/match size (never the page size)."""
    chosen = [(k, v) for k, v in (("label", label), ("predicate", predicate),
                                  ("relation", relation)) if v]
    if len(chosen) != 1:
        return {"error": "pass exactly one of --label / --predicate / --relation",
                "given": [k for k, _ in chosen]}
    mode, key = chosen[0]
    preds, err = parse_where(where)
    if err:
        return {"error": err}
    if preds and mode != "label":
        return {"error": "--where filters node properties — label mode only"}
    if mode == "label":
        return await _list_label(gx, key, limit, offset=offset, contains=contains,
                                 where=preds)
    if mode == "predicate":
        return await _list_predicate(gx, key, limit)
    return await _list_relation(gx, key, limit)
