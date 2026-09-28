"""In-body site links, RESOLVED after replay (DEC 72d669c5 (1)).

A post's link to a site page (a series page today) names a PATH; what the page is — a
Series, a topic listing — lives in `site_path` facts on the node that holds it, and those
facts are journal content that replays AFTER the ingest. So the harvest keeps each target
VERBATIM on the Note (`site_refs`) and resolution is its own stage: once every fact is on
the graph, each target maps under Quarto's URL equivalences (`site_path_key`) to the one
node holding that path — active or superseded, since an old URL still names its page — and
lands as a `site_link` REFERENCES edge (a cross-reference, never membership; ruling
0f9ee9a8 (4)). The pass RECONCILES: it adds the edges the facts justify and deletes the
`site_link` edges they no longer do, so it is a pure function of source plus journal and a
rebuild reproduces it exactly.

Where it runs: once at the end of every replay (the rebuild stage), and live on each write
that changes an input — a note's links (birth, harvest-on-edit) or a `site_path` fact —
so the live graph never waits for a rebuild. `DEFER_RESOLVE` holds the live hooks off
while a replay or a batch runs; the caller then runs one pass for the whole graph."""

import posixpath
from contextvars import ContextVar
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlsplit

from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.query import EdgeQuery, PropertyPredicate
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.nodes import site_link_edge
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from . import factlayer as F
from .runtime import GraphHandle

# True while a replay or a batch runs: the per-write hooks stand down and the caller runs
# ONE pass at the end (a replay applies ~hundreds of ops; each hook is a whole-graph read).
DEFER_RESOLVE: ContextVar[bool] = ContextVar("defer_site_link_resolve", default=False)


def site_path_key(
    target: str,                 # A link target or a site_path value (rooted, relative, or an own-site URL)
    base: Optional[str] = None,  # The linking page's own path, for a relative target (None = unknown)
) -> Optional[str]:  # The equivalence key, or None when a relative target has no base
    """The key two URLs of one page share under Quarto's URL rules (the facts stay verbatim).

    The path only (scheme, host, query and #anchor dropped — the harvester already kept only
    this site's links); `x.html` = `x`, `dir/` = `dir/index.html` = `dir`; `.` and `..`
    collapse. Case is kept: a path is case-sensitive on the host, and a site_path is never
    normalized (c350f7d1). A relative target resolves against `base`."""
    parts = urlsplit(target)
    path = parts.path
    if not parts.scheme and not parts.netloc and not path.startswith("/"):
        if not base:
            return None
        path = urlsplit(urljoin(base, target)).path
    if not path:
        return None
    path = posixpath.normpath(path)
    for suffix in ("/index.html", "/index.htm"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    else:
        for ext in (".html", ".htm"):
            if path.endswith(ext):
                path = path[: -len(ext)]
                break
    return path.rstrip("/") or "/"


async def site_path_holders(
    gx: GraphHandle,
) -> Tuple[Dict[str, Set[str]], Dict[str, str]]:  # ({key: holder ids}, {subject id: active path})
    """Every site_path value on the graph, keyed for resolution, plus each page's ACTIVE path.

    Superseded values count as holders too — an old URL still names its page (that is what
    the redirect projection serves). A key two different nodes hold is ambiguous and never
    resolves (the caller reports it)."""
    assertions = await F.load_label_where(
        gx, DevNodeKinds.ASSERTION, [PropertyPredicate("predicate", "eq", P.SITE_PATH)])
    holders: Dict[str, Set[str]] = {}
    for a in assertions:
        key = site_path_key(str(F.prop(a, "value") or ""))
        subject = F.prop(a, "subject_id")
        if key and subject:
            holders.setdefault(key, set()).add(str(subject))
    supers = await F.load_supersedes(gx) if assertions else []
    active: Dict[str, str] = {}
    for group in F.group_by_slot(assertions).values():
        standing = F.active_assertions(group, supers)
        if len(standing) == 1:
            active[str(F.prop(standing[0], "subject_id"))] = str(F.prop(standing[0], "value"))
    return holders, active


async def _standing_site_links(
    gx: GraphHandle,
    note_ids: List[str],  # The notes whose outgoing site-link edges to read
) -> Dict[str, Dict[str, Any]]:  # edge id -> edge dict (only `site_link`-marked REFERENCES)
    if not note_ids:
        return {}
    q = EdgeQuery(source_ids=sorted(note_ids), relation_type=DevRelations.REFERENCES)
    res = await graph_task(gx.queue, gx.graph_id, "query_edges", query=q.to_dict())
    raw = getattr(res, "edges", None) or getattr(res, "rows", None) or []
    out: Dict[str, Dict[str, Any]] = {}
    for e in raw:
        d = e.to_dict() if hasattr(e, "to_dict") else dict(e)
        if (d.get("properties") or {}).get("site_link"):
            out[str(d["id"])] = d
    return out


async def resolve_site_links(
    gx: GraphHandle,
    note_ids: Optional[List[str]] = None,  # Scope to these notes (None = every Note on the graph)
    *,
    write: bool = True,                    # Apply the edge diff (False = report only)
) -> Dict[str, Any]:  # {notes, links, resolved, added, removed, unresolved: [...], ambiguous: [...], written}
    """Reconcile the `site_link` REFERENCES edges of the scoped notes against the facts.

    Each note's verbatim `site_refs` target maps through `site_path_key` (a relative target
    against the note's own active site_path) to the ONE node holding that path; the pass
    extends the edges that mapping justifies and deletes the scoped notes' `site_link` edges
    it no longer does. A target nothing holds is `unresolved`, one two nodes hold is
    `ambiguous`; both are reported, neither mints an edge. A link to the note's own page is
    dropped (a self-reference is not a cross-reference)."""
    if note_ids is None:
        notes = await F.load_label(gx, DevNodeKinds.NOTE)
    else:
        notes = list((await F.load_nodes(gx, list(note_ids))).values())
    holders, active = await site_path_holders(gx)
    desired: Dict[str, Dict[str, Any]] = {}
    unresolved: List[Dict[str, Any]] = []
    ambiguous: List[Dict[str, Any]] = []
    links = 0
    for n in notes:
        nid = str(F.nid(n))
        for target in F.prop(n, "site_refs") or []:
            links += 1
            key = site_path_key(str(target), active.get(nid))
            held = sorted(holders.get(key, ())) if key else []
            row = {"note_id": nid, "slug": F.prop(n, "slug"), "target": target, "key": key}
            if len(held) > 1:
                ambiguous.append(dict(row, holders=held))
            elif not held:
                unresolved.append(row)
            elif held[0] != nid:
                e = site_link_edge(nid, held[0])
                desired[e["id"]] = e
    standing = await _standing_site_links(gx, [str(F.nid(n)) for n in notes])
    added = [e for eid, e in desired.items() if eid not in standing]
    removed = [eid for eid in standing if eid not in desired]
    if write and removed:
        await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=sorted(removed))
    if write and added:
        await extend_graph(gx.queue, gx.graph_id, [], added)
    return {"notes": len(notes), "links": links, "resolved": len(desired),
            "added": len(added), "removed": len(removed), "unresolved": unresolved,
            "ambiguous": ambiguous, "written": bool(write and (added or removed))}


async def resolve_after_write(
    gx: GraphHandle,
    note_ids: Optional[List[str]] = None,  # The notes a write touched (None = the whole graph, e.g. a site_path fact changed)
) -> Optional[Dict[str, Any]]:  # The pass result, or None while a replay / batch defers
    """The live hook: re-resolve after a write that changed an input, unless deferred."""
    if DEFER_RESOLVE.get():
        return None
    return await resolve_site_links(gx, note_ids)
