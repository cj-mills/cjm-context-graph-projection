"""In-body site links, RESOLVED through site_path facts (DEC 72d669c5 (1); the step, 9ee4e346).

A post's link to a site page names a PATH (ONE resolver for every in-body site link, ruling
d31e9ba7); what the page is — a post, a Series, a topic listing, a site page — lives in
`site_path` facts on the node that holds it, and those
facts are journal content that replays AFTER the ingest. So the harvest keeps each target
VERBATIM on the Note (`site_refs`) and resolution is its own stage: once every fact is on
the graph, each target maps under Quarto's URL equivalences (`site_path_key`) to the one
node holding that path — active or superseded, since an old URL still names its page — and
lands as a `site_link` REFERENCES edge (a cross-reference, never membership; ruling
0f9ee9a8 (4)). The pass RECONCILES: it adds the edges the facts justify and deletes the
`site_link` edges they no longer do, so it is a pure function of source plus journal and a
rebuild reproduces it exactly.

Where it runs (design amendment 9ee4e346): as a STEP, once at the close of every write
window that changed one of its inputs, inside that window — a live CLI invocation, or a
replay window (the consecutive ops sharing one journaled ts) — so each edge carries the
time of the invocation that first made it hold, live and on rebuild alike. The window's
writes come from the layer's observer (`observe_writes`); `touches_inputs` reads them. A
whole-graph pass closes every replay as the CHECK: it should change nothing."""

import posixpath
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlsplit

from cjm_context_graph_layer.ops import extend_graph, graph_task, observe_writes
from cjm_context_graph_primitives.query import EdgeQuery, PropertyPredicate
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import section_node_id
from cjm_dev_graph_schema.nodes import site_link_edge
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from . import factlayer as F
from .runtime import GraphHandle


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
    the redirect projection serves): the page its supersession chain ends at, so a path
    TRANSFERRED to another holder names the new one (design amendment e916a4b9 (4)). A key two different nodes hold is ambiguous and never
    resolves (the caller reports it)."""
    assertions = await F.load_label_where(
        gx, DevNodeKinds.ASSERTION, [PropertyPredicate("predicate", "eq", P.SITE_PATH)])
    from .archive import path_owners   # function-local: archive reaches this module at call time
    supers = await F.load_supersedes(gx) if assertions else []
    owners, _ = path_owners(assertions, supers)   # an ambiguous value holds nothing; the build reports it
    holders: Dict[str, Set[str]] = {}
    for a in assertions:
        key = site_path_key(str(F.prop(a, "value") or ""))
        subject = owners.get(str(F.nid(a)))
        if key and subject:
            holders.setdefault(key, set()).add(subject)
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
    exclude: Optional[Iterable[str]] = None,  # Notes left out as link SOURCES (a replay's not-yet-born notes)
) -> Dict[str, Any]:  # {notes, links, resolved, added, removed, unresolved: [...], ambiguous: [...], anchors: [...], written}
    """Reconcile the `site_link` REFERENCES edges of the scoped notes against the facts.

    Each note's verbatim `site_refs` target maps through `site_path_key` (a relative target
    against the note's own active site_path) to the ONE node holding that path; the pass
    extends the edges that mapping justifies and deletes the scoped notes' `site_link` edges
    it no longer does. A target nothing holds is `unresolved`, one two nodes hold is
    `ambiguous`; both are reported, neither mints an edge. A link to the note's own page is
    dropped (a self-reference is not a cross-reference).

    ONE resolver for every in-body site link (ruling d31e9ba7): an ANCHORED link lands on the
    Section its anchor names on the page's Note — the id that heading mints, the anchor slug
    being the heading slug — and when that Section does not exist it lands on the page with the
    anchor kept on the edge and is reported in `anchors`: never a dangling edge, never a silent
    drop. A standing edge whose anchor changed is re-minted. An edge's id is its (note, target,
    relation) triple, so a note's several links landing on one node are ONE edge: the first in
    document order gives its properties, and every anchor naming no Section is still reported."""
    if note_ids is None:
        notes = await F.load_label(gx, DevNodeKinds.NOTE)
    else:
        notes = list((await F.load_nodes(gx, list(note_ids))).values())
    if exclude:
        # A replay hoists every genesis op; a note whose journal position the replay has not
        # reached is not born yet, so its links wait for its own window (9ee4e346 (2)).
        left_out = set(exclude)
        notes = [n for n in notes if str(F.nid(n)) not in left_out]
    holders, active = await site_path_holders(gx)
    unresolved: List[Dict[str, Any]] = []
    ambiguous: List[Dict[str, Any]] = []
    placed: List[Tuple[str, str, str, Dict[str, Any]]] = []   # (note, holder, anchor, row)
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
                placed.append((nid, held[0], urlsplit(str(target)).fragment, row))
    # every Section an anchor names, in one read
    named = sorted({section_node_id(h, a) for _, h, a, _ in placed if a})
    present = set(await F.load_nodes(gx, named)) if named else set()
    desired: Dict[str, Dict[str, Any]] = {}
    anchors: List[Dict[str, Any]] = []
    for nid, holder, anchor, row in placed:
        target_id = holder
        if anchor:
            section = section_node_id(holder, anchor)
            if section in present:
                target_id = section
            else:
                anchors.append(dict(row, anchor=anchor))
        e = site_link_edge(nid, target_id, anchor)
        desired.setdefault(e["id"], e)
    standing = await _standing_site_links(gx, [str(F.nid(n)) for n in notes])
    changed = {eid for eid, e in desired.items()
               if eid in standing and (standing[eid].get("properties") or {}) != e["properties"]}
    added = [e for eid, e in desired.items() if eid not in standing or eid in changed]
    removed = [eid for eid in standing if eid not in desired or eid in changed]
    if write and removed:
        await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=sorted(removed))
    if write and added:
        await extend_graph(gx.queue, gx.graph_id, [], added)
    return {"notes": len(notes), "links": links, "resolved": len(desired),
            "added": len(added), "removed": len(removed), "unresolved": unresolved,
            "ambiguous": ambiguous, "anchors": anchors, "written": bool(write and (added or removed))}


def touches_inputs(
    writes: List[Dict[str, Any]],  # One window's writes (the layer's `observe_writes` records)
) -> bool:  # True when the window wrote something the resolve reads
    """Whether a window moved an input of the resolve (design amendment 9ee4e346 (3)).

    The inputs: a Note's `site_refs` (born, harvested, updated or deleted with it), a deleted
    Note (the edges onto it go with it), a Section appearing or disappearing (an anchor's
    target), a `site_path` Assertion, a SUPERSEDES edge (which page a superseded path names),
    and a `site_link` edge written by anything but the step itself."""
    for w in writes:
        deleting = w["method"] == "delete_nodes"
        for n in w["nodes"]:
            props = n.get("properties") or {}
            label = n.get("label")
            if "site_refs" in props or label == DevNodeKinds.SECTION:
                return True
            if deleting and label == DevNodeKinds.NOTE:
                return True
            if label == DevNodeKinds.ASSERTION and props.get("predicate") == P.SITE_PATH:
                return True
        for e in w["edges"]:
            if e.get("relation_type") == DevRelations.SUPERSEDES:
                return True
            if (e.get("properties") or {}).get("site_link"):
                return True
    return False


async def step_site_links(
    gx: GraphHandle,
    writes: List[Dict[str, Any]],  # The window's writes
    *,
    exclude: Optional[Iterable[str]] = None,  # Notes not yet born (a replay's hoisted genesis)
    force: bool = False,           # Run even when no write moved an input (a note born this window)
) -> Optional[Dict[str, Any]]:  # The pass result, or None when the window moved no input
    """THE STEP (design amendment 9ee4e346): one whole-graph resolve at a window's close, run
    only when the window moved an input. Called inside the window, so every edge it adds
    carries the window's ts — the time its justification first held."""
    if not (force or touches_inputs(writes)):
        return None
    return await resolve_site_links(gx, exclude=exclude)


@asynccontextmanager
async def site_link_window(
    gx: GraphHandle,
    report: Optional[Callable[[Dict[str, Any]], None]] = None,  # Called with the step's result when it ran
) -> AsyncIterator[List[Dict[str, Any]]]:  # The window's writes, as the observer records them
    """Observe the block's writes, then run the step once at its close (a live write window —
    the CLI wraps every invocation in one). An exception skips the step: a failed
    invocation's partial writes are for the rebuild's check to report."""
    with observe_writes() as writes:
        yield writes
    got = await step_site_links(gx, writes)
    if got is not None and report is not None:
        report(got)
