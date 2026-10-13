"""Shared fine-tier reads over the fact-layering schema (slots + assertions).

The write surface, the `contradictions` query, the version oracle, and the
worklist all need the same handful of reads: load all assertions, load the
SUPERSEDES/CONTRADICTS edge sets, group assertions by their slot, and resolve the
ACTIVE (non-superseded) set in a slot. They live here once.

Supersession resolution reuses the layer's `resolve_active` (the one thing worth
importing from the otherwise transcript-flavored `edits` module — pure SUPERSEDES
resolution, no text specifics). This module is dev-schema-aware (it knows the
Assertion/FactSlot labels) but carries no value-space policy — that stays in
`cjm_dev_graph_schema.predicates`.
"""

from typing import Any, Dict, List, Optional, Tuple

from cjm_context_graph_layer.edits import resolve_active
from cjm_context_graph_layer.ops import graph_task
from cjm_context_graph_primitives.query import EdgeQuery, NodeQuery, OrderBy, PropertyPredicate
from cjm_dev_graph_schema.aliases import build_alias_index
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from .runtime import GraphHandle

_LABEL_PAGE = 5000  # Keyset page of a whole-label read: a transport bound, never a cap (a107e2f3)


def nid(node: Any) -> Optional[str]:
    """A node's id (typed GraphNode or wire dict)."""
    if isinstance(node, dict):
        return node.get("id")
    return getattr(node, "id", None)


def label(node: Any) -> Optional[str]:
    """A node's label / kind (typed GraphNode or wire dict) — the sibling of `nid`/`props`."""
    if isinstance(node, dict):
        return node.get("label")
    return getattr(node, "label", None)


def props(node: Any) -> Dict[str, Any]:
    """A node's properties dict (typed GraphNode or wire dict)."""
    p = getattr(node, "properties", None)
    if p is None and isinstance(node, dict):
        p = node.get("properties")
    return p or {}


def prop(node: Any, key: str, default: Any = None) -> Any:
    """One property value off a node."""
    return props(node).get(key, default)


async def load_label(
    gx: GraphHandle,
    label: str,  # Node label to load
) -> List[Any]:  # EVERY GraphNode carrying that label, in id order
    """All nodes of a label — every row, never a capped subset (finding a107e2f3); the
    keyset pages are `load_label_where`'s."""
    return await load_label_where(gx, label, [])


async def load_label_where(
    gx: GraphHandle,
    label: str,                          # Node label to load
    where: List[PropertyPredicate],      # Property predicates (AND) — the server-side filter
    limit: Optional[int] = None,         # Window size; None = EVERY match
    offset: int = 0,                     # Window start (with a limit)
    order_by: Optional[OrderBy] = None,  # Ordering (default: id)
) -> List[Any]:  # GraphNodes matching label + predicates
    """Nodes of a label filtered by property predicates, SERVER-SIDE (`NodeQuery.where`).

    The read-surface exposure of the query machinery the primitives always had —
    a property filter runs in the worker, not over a full-label client scan. With no
    `limit` it returns EVERY match (finding a107e2f3: a default cap once returned an
    arbitrary subset past it, silently), read in keyset pages on the id — each page a
    range scan over the (label, id) index — unless an ordering asks for one ordered read.
    A window (`limit` / `offset`) is ordered in SQL before it is cut — by `order_by`, the
    id breaking ties, else by the id — so it is never an arbitrary subset either."""
    def _nodes(res: Any) -> List[Any]:
        return list(getattr(res, "nodes", None)
                    or (res.get("nodes") if isinstance(res, dict) else None) or [])

    if limit is not None or offset or order_by is not None:
        q = NodeQuery(label=label, where=list(where), limit=limit, offset=offset,
                      order_by=order_by)
        return _nodes(await graph_task(gx.queue, gx.graph_id, "query_nodes", query=q.to_dict()))
    out: List[Any] = []
    after: Optional[str] = None
    while True:
        q = NodeQuery(label=label, where=list(where), limit=_LABEL_PAGE, after=after)
        page = _nodes(await graph_task(gx.queue, gx.graph_id, "query_nodes", query=q.to_dict()))
        out.extend(page)
        if len(page) < _LABEL_PAGE:
            return out
        after = nid(page[-1])


async def count_label(
    gx: GraphHandle,
    label: str,                                       # Node label to count
    where: Optional[List[PropertyPredicate]] = None,  # Optional property predicates (AND)
) -> int:  # The TRUE total (independent of any page limit)
    """Count nodes of a label (optionally predicate-filtered) — `NodeQuery(count=True)`.

    The true-total read a windowed list pairs with, so `count == rows returned`
    can never masquerade as the class size (the 8ac72523 CLI-gap finding)."""
    q = NodeQuery(label=label, where=list(where or []), count=True)
    res = await graph_task(gx.queue, gx.graph_id, "query_nodes", query=q.to_dict())
    c = getattr(res, "count", None)
    if c is None and isinstance(res, dict):
        c = res.get("count")
    return int(c or 0)


async def load_nodes(
    gx: GraphHandle,
    ids: List[str],  # Node ids to fetch
) -> Dict[str, Any]:  # id -> node (absent ids omitted)
    """Batch-fetch nodes by id in ONE worker round-trip (`NodeQuery.ids`).

    Per-id `get_node` calls serialize through the worker task queue (~100ms each),
    so any page-of-ids read (list subjects, readiness labels, display neighbours)
    must batch or it prices a page at seconds."""
    if not ids:
        return {}
    q = NodeQuery(ids=sorted(set(ids)), limit=len(set(ids)))
    res = await graph_task(gx.queue, gx.graph_id, "query_nodes", query=q.to_dict())
    out: Dict[str, Any] = {}
    for n in (getattr(res, "nodes", None) or (res.get("nodes") if isinstance(res, dict) else None) or []):
        out[nid(n)] = n
    return out


async def load_edge_pairs(
    gx: GraphHandle,
    relation: str,  # Edge relation type
) -> List[Tuple[str, str]]:  # (source_id, target_id) pairs
    """All (source, target) pairs for an edge relation type."""
    res = await graph_task(gx.queue, gx.graph_id, "query_edges",
                           query=EdgeQuery(relation_type=relation,
                                           project=["source_id", "target_id"]).to_dict())
    return [(r["source_id"], r["target_id"]) for r in (res.rows or [])]


async def load_edges(
    gx: GraphHandle,
    query: EdgeQuery,  # An unprojected edge query (endpoint filters, a relation type)
) -> List[Dict[str, Any]]:  # Whole edges as plain dicts (id, source_id, target_id, relation_type, properties)
    """The edges an `EdgeQuery` matches, whole -- for a caller that diffs edge ids."""
    res = await graph_task(gx.queue, gx.graph_id, "query_edges", query=query.to_dict())
    raw = getattr(res, "edges", None) or getattr(res, "rows", None) or []
    return [e.to_dict() if hasattr(e, "to_dict") else dict(e) for e in raw]


async def load_supersedes(gx: GraphHandle) -> List[Tuple[str, str]]:
    """All SUPERSEDES (superseder, superseded) pairs (the resolve_active input)."""
    return await load_edge_pairs(gx, DevRelations.SUPERSEDES)


async def load_contradicts(gx: GraphHandle) -> List[Tuple[str, str]]:
    """All CONTRADICTS pairs already recorded (for write idempotency / reporting)."""
    return await load_edge_pairs(gx, DevRelations.CONTRADICTS)


async def alias_index(
    gx: GraphHandle,
) -> Tuple[Dict[str, str], Dict[str, Any]]:  # (canon-name -> entity id, entity id -> node)
    """Build the entity alias index + an id->entity lookup (rename-stable subjects)."""
    entities = await load_label(gx, DevNodeKinds.ENTITY)
    return build_alias_index(entities), {nid(e): e for e in entities}


async def load_assertions(gx: GraphHandle) -> List[Any]:
    """All Assertion nodes."""
    return await load_label(gx, DevNodeKinds.ASSERTION)


async def note_alias_map(gx: GraphHandle) -> Dict[str, str]:
    """Confirmed note aliases as a {drifted-slug: canonical-slug} map.

    Reads the active `aka` assertions (the propose/confirm worklist's output): each
    claims a drifted link slug (the value) FOR a canonical note (the subject). The
    canonical slug is read off the subject Note node. This is the index ingest uses
    to heal drifted references and the worklist uses to drop confirmed refs."""
    assertions = await load_assertions(gx)
    supers = await load_supersedes(gx)
    aka = [a for a in assertions if prop(a, "predicate") == "aka"]
    if not aka:
        return {}
    active = active_assertions(aka, supers)
    notes = await load_label(gx, DevNodeKinds.NOTE)
    slug_by_id = {nid(n): prop(n, "slug") for n in notes}
    out: Dict[str, str] = {}
    for a in active:
        canonical = slug_by_id.get(prop(a, "subject_id"))
        drifted = prop(a, "value")
        if canonical and drifted:
            out[str(drifted)] = str(canonical)
    return out


def group_by_slot(assertions: List[Any]) -> Dict[str, List[Any]]:
    """Group assertion nodes by their `slot_id` property."""
    out: Dict[str, List[Any]] = {}
    for a in assertions:
        sid = prop(a, "slot_id")
        if sid:
            out.setdefault(sid, []).append(a)
    return out


def active_assertions(
    slot_assertions: List[Any],            # Assertion nodes in ONE slot
    supersedes_pairs: List[Tuple[str, str]],  # All SUPERSEDES (superseder, superseded) pairs
) -> List[Any]:  # The active (non-superseded) assertions, input order preserved
    """The active assertions in a slot under append-only supersession."""
    ids = [nid(a) for a in slot_assertions]
    active = resolve_active(ids, supersedes_pairs)
    return [a for a in slot_assertions if nid(a) in active]
