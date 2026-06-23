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
from cjm_context_graph_primitives.query import EdgeQuery
from cjm_dev_graph_schema.aliases import build_alias_index
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from .runtime import GraphHandle

_LABEL_LIMIT = 10000  # Generous per-label cap (the dev graph is small; raise if needed)


def nid(node: Any) -> Optional[str]:
    """A node's id (typed GraphNode or wire dict)."""
    if isinstance(node, dict):
        return node.get("id")
    return getattr(node, "id", None)


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
    label: str,        # Node label to load
    limit: int = _LABEL_LIMIT,
) -> List[Any]:  # GraphNodes carrying that label
    """All nodes of a label (bounded by `limit`)."""
    res = await graph_task(gx.queue, gx.graph_id, "find_nodes_by_label", label=label, limit=limit)
    return list(res or [])


async def load_edge_pairs(
    gx: GraphHandle,
    relation: str,  # Edge relation type
) -> List[Tuple[str, str]]:  # (source_id, target_id) pairs
    """All (source, target) pairs for an edge relation type."""
    res = await graph_task(gx.queue, gx.graph_id, "query_edges",
                           query=EdgeQuery(relation_type=relation,
                                           project=["source_id", "target_id"]).to_dict())
    return [(r["source_id"], r["target_id"]) for r in (res.rows or [])]


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
