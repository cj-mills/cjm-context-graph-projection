"""Series born on-graph: the node, its membership and its ORDER as journaled intent
(DEC 72d669c5 (2)/(4); ruling 0f9ee9a8).

A Series is minted by the `series` op (the page's title, description, preview image, date
and categories — a whole record, last op wins), never by the harvester: an in-body link to
a series page is a cross-reference (`sitelinks`). Membership is `IN_SERIES` note -> series,
and the order is AUTHORED on each membership edge: `after` names the member it follows
("" = first), so the chain is walked, never sorted by date. Two ops write it —
`series-members` sets the whole ordered list (seeding from a page's listing), and
`place-in-series` splices one member (insert, move, remove) touching only the edges whose
`after` changes. `series_order` walks the chain and reports what breaks it: two members
claiming one predecessor (a fork) or a member no walk reaches (a dangling `after`, a
cycle) — the NEXT-spine rule, applied to authored order."""

from typing import Any, Dict, List, Optional, Tuple

from cjm_context_graph_layer.grammar import make_edge
from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.query import EdgeQuery
from cjm_dev_graph_schema.identity import note_node_id, series_node_id, topic_node_id
from cjm_dev_graph_schema.nodes import series_member_edge, SeriesNode, TopicNode
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations
from cjm_markdown_decompose_core.relations import slugify

from . import factlayer as F
from .runtime import GraphHandle

PAGE_FIELDS = ("description", "image", "date")


async def _edges(
    gx: GraphHandle,
    query: EdgeQuery,  # An unprojected edge query
) -> List[Dict[str, Any]]:  # Whole edges as plain dicts
    res = await graph_task(gx.queue, gx.graph_id, "query_edges", query=query.to_dict())
    raw = getattr(res, "edges", None) or getattr(res, "rows", None) or []
    return [e.to_dict() if hasattr(e, "to_dict") else dict(e) for e in raw]


async def mint_series(
    gx: GraphHandle,
    key: str,                                # The series' durable key (the page's file stem)
    *,
    title: str = "",                         # The page title
    description: str = "",                   # The page's one-line description
    image: str = "",                         # The page's preview image (verbatim)
    date: str = "",                          # The page's own date (verbatim; "" = none)
    categories: Optional[List[str]] = None,  # The page's own categories -> TAGGED edges (None = none)
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {series_id, key, updated, tagged, written} | {error}
    """Mint or update a Series from its page's record (journaled `series`; upsert by key).

    The op carries the WHOLE record, so a re-mint replaces every field (a field left empty
    clears) and the Series' TAGGED edges become exactly its categories — replay of the
    append-ordered ops converges on the last one. The id is key-derived, so a Series the
    harvester once minted keeps its id when it is born here."""
    if not key or not isinstance(key, str):
        return {"error": "a series needs a key", "written": False}
    node = SeriesNode(key=key, title=title, description=description, image=image,
                      date=date).to_graph_node()
    sid = node["id"]
    existing = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=sid)
    if existing is not None:
        props = dict(node["properties"])
        for k in PAGE_FIELDS:
            props.setdefault(k, "")
        await graph_task(gx.queue, gx.graph_id, "update_node", node_id=sid, properties=props)
    else:
        await extend_graph(gx.queue, gx.graph_id, [node], [])
    keys = []
    for c in categories or []:
        k = slugify(str(c))
        if k and k not in keys:
            keys.append(k)
    want = {topic_node_id(k): k for k in keys}
    standing = await _edges(gx, EdgeQuery(source_ids=[sid], relation_type=DevRelations.TAGGED))
    stale = [str(e["id"]) for e in standing if str(e["target_id"]) not in want]
    if stale:
        await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=stale)
    have = {str(e["target_id"]) for e in standing}
    new = {tid: k for tid, k in want.items() if tid not in have}
    if new:  # a Topic already on the graph re-extends as a verified no-op
        await extend_graph(gx.queue, gx.graph_id,
                           [TopicNode(key=k).to_graph_node() for k in new.values()],
                           [make_edge(sid, tid, DevRelations.TAGGED) for tid in new])
    return {"series_id": sid, "key": key, "updated": existing is not None,
            "tagged": sorted(want.values()), "written": True}


async def _members(
    gx: GraphHandle,
    series_id: str,  # The Series node id
) -> Dict[str, Dict[str, Any]]:  # note id -> its standing IN_SERIES edge
    rows = await _edges(gx, EdgeQuery(target_ids=[series_id], relation_type=DevRelations.IN_SERIES))
    return {str(e["source_id"]): e for e in rows}


def order_members(
    after: Dict[str, str],  # member note id -> the member it follows ("" = first)
) -> Tuple[List[str], List[Dict[str, Any]]]:  # (the walked order, contradictions)
    """Walk the `after` chain from the head; report forks and unreached members.

    A fork (two members after one) follows the lowest id so the walk stays deterministic,
    and is reported; a member the walk never reaches (its `after` names a non-member, or a
    cycle) is reported and listed after the walk in id order."""
    children: Dict[str, List[str]] = {}
    for m, a in after.items():
        children.setdefault(a or "", []).append(m)
    order: List[str] = []
    issues: List[Dict[str, Any]] = []
    seen = set()
    cur = ""
    while True:
        nxt = sorted(c for c in children.get(cur, []) if c not in seen)
        if not nxt:
            break
        if len(nxt) > 1:
            issues.append({"kind": "fork", "after": cur, "members": nxt})
        cur = nxt[0]
        seen.add(cur)
        order.append(cur)
    for m in sorted(set(after) - seen):
        issues.append({"kind": "unreached", "member": m, "after": after[m]})
        order.append(m)
    return order, issues


async def _apply_chain(
    gx: GraphHandle,
    series_id: str,
    standing: Dict[str, Dict[str, Any]],  # note id -> standing edge
    chain: Dict[str, str],                # note id -> after, the WHOLE desired membership
) -> Dict[str, int]:  # {added, moved, removed}
    """Land a desired membership: delete + extend only the edges whose `after` changed."""
    drop: List[str] = []
    land: List[Dict[str, Any]] = []
    moved = added = 0
    for m, e in standing.items():
        if m not in chain:
            drop.append(str(e["id"]))
        elif (e.get("properties") or {}).get("after") != chain[m]:
            drop.append(str(e["id"]))
            land.append(series_member_edge(m, series_id, chain[m]))
            moved += 1
    for m, a in chain.items():
        if m not in standing:
            land.append(series_member_edge(m, series_id, a))
            added += 1
    if drop:
        await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=sorted(drop))
    if land:
        await extend_graph(gx.queue, gx.graph_id, [], land)
    return {"added": added, "moved": moved, "removed": len(drop) - moved}


async def set_series_members(
    gx: GraphHandle,
    key: str,          # The Series key
    slugs: List[str],  # The member notes' slugs, IN ORDER
    *,
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {series_id, members, added, moved, removed, written} | {error}
    """Set a series' WHOLE ordered membership (journaled `series-members`).

    Seeds a series from its page's listing, or re-states it: each slug follows the one
    before it, the first follows "". Refused whole (nothing written) when the series is
    unknown, a slug repeats, or a slug names no Note."""
    sid = series_node_id(key)
    if await graph_task(gx.queue, gx.graph_id, "get_node", node_id=sid) is None:
        return {"error": f"no series `{key}` (mint it with `series` first)", "written": False}
    if len(set(slugs)) != len(slugs):
        dup = sorted({s for s in slugs if slugs.count(s) > 1})
        return {"error": f"a member repeats: {', '.join(dup)}", "written": False}
    ids = [note_node_id(s) for s in slugs]
    found = await F.load_nodes(gx, ids)
    missing = [s for s, i in zip(slugs, ids) if i not in found]
    if missing:
        return {"error": f"no note for: {', '.join(missing)}", "written": False}
    chain = {i: (ids[n - 1] if n else "") for n, i in enumerate(ids)}
    res = await _apply_chain(gx, sid, await _members(gx, sid), chain)
    return {"series_id": sid, "key": key, "members": len(ids), **res, "written": True}


async def place_in_series(
    gx: GraphHandle,
    key: str,                     # The Series key
    slug: str,                    # The member to place
    *,
    after: Optional[str] = None,  # Place it after this member's slug
    first: bool = False,          # Place it at the head
    remove: bool = False,         # Take it out of the series
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {series_id, note_id, added, moved, removed, written} | {error}
    """Splice ONE member (journaled `place-in-series`): insert, move, or remove.

    Exactly one of `after` / `first` / `remove`. The member first leaves its place (its
    follower now follows its predecessor), then enters the new one (the member that followed
    the new predecessor now follows it) — so an insert touches two edges, a move at most
    three, and nothing renumbers."""
    if sum([after is not None, bool(first), bool(remove)]) != 1:
        return {"error": "pass exactly one of --after SLUG / --first / --remove", "written": False}
    sid = series_node_id(key)
    if await graph_task(gx.queue, gx.graph_id, "get_node", node_id=sid) is None:
        return {"error": f"no series `{key}`", "written": False}
    nid = note_node_id(slug)
    if await graph_task(gx.queue, gx.graph_id, "get_node", node_id=nid) is None:
        return {"error": f"no note `{slug}`", "written": False}
    standing = await _members(gx, sid)
    chain = {m: str((e.get("properties") or {}).get("after") or "") for m, e in standing.items()}
    if remove and nid not in chain:
        return {"error": f"`{slug}` is not in series `{key}`", "written": False}
    if nid in chain:  # leave the current place
        prev = chain.pop(nid)
        for m, a in chain.items():
            if a == nid:
                chain[m] = prev
    if not remove:
        anchor = ""
        if after is not None:
            anchor = note_node_id(after)
            if anchor == nid:
                return {"error": "a member cannot follow itself", "written": False}
            if anchor not in chain:
                return {"error": f"`{after}` is not in series `{key}`", "written": False}
        for m, a in chain.items():
            if a == anchor:
                chain[m] = nid
        chain[nid] = anchor
    res = await _apply_chain(gx, sid, standing, chain)
    return {"series_id": sid, "key": key, "note_id": nid, **res, "written": True}


async def series_order(
    gx: GraphHandle,
    key: str,  # The Series key
) -> Dict[str, Any]:  # {series_id, key, title, members: [{id, slug, title, after}], contradictions} | {error}
    """The series in its AUTHORED order, with whatever breaks the chain (the read verb)."""
    sid = series_node_id(key)
    node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=sid)
    if node is None:
        return {"error": f"no series `{key}`"}
    standing = await _members(gx, sid)
    after = {m: str((e.get("properties") or {}).get("after") or "") for m, e in standing.items()}
    order, issues = order_members(after)
    notes = await F.load_nodes(gx, order)
    members = [{"id": m, "slug": F.prop(notes.get(m), "slug"), "title": F.prop(notes.get(m), "title"),
                "after": after[m]} for m in order]
    return {"series_id": sid, "key": key, "title": F.prop(node, "title"),
            "label": F.label(node) or DevNodeKinds.SERIES, "members": members,
            "contradictions": issues}
