"""The projection core: schema / show / relevance / state over a context graph.

Domain-neutral — operates on generic nodes + edges via the graph-storage task
channel, so it serves any cjm-substrate context graph. Every read is bounded and
ranked with provenance to drill into; nothing returns a raw subgraph dump.

Relevance v1 is structural: term-matched seeds, BFS expansion via the store's
`get_context`, ranked by edge-type weight x hop-decay x recency, with superseded
nodes down-weighted. Smarter seed-finding + embeddings are deferred (evidence-
driven), per the arc plan.
"""

import re
import time
from typing import Any, Dict, List, Optional

from cjm_context_graph_layer.ops import graph_task
from cjm_context_graph_primitives.query import EdgeQuery, NodeQuery, PropertyPredicate

from . import factlayer as F
from .display import annotate_display, node_title
from .runtime import GraphHandle

# Edge-type weights for relevance ranking: trust/reasoning/contradiction edges
# pull hard; soft cross-references pull lightly. Unlisted relations default to 1.
EDGE_WEIGHTS = {
    "SUPERSEDES": 3.0, "CONTRADICTS": 3.0, "SUPPORTED_BY": 3.0,
    "DERIVED_FROM": 2.0, "DEPENDS_ON": 2.0, "ABOUT": 2.0, "PRODUCED": 2.0,
    "DECIDED_IN": 1.5, "PRODUCED_IN": 1.5, "EVIDENCED_BY": 1.5, "LANDS_AT": 1.5,
    "REFERENCES": 1.0,
}
_HOP_DECAY = 0.5          # Score multiplier per BFS hop from a seed
_SUPERSEDED_FACTOR = 0.3  # Down-weight for nodes that are the target of a SUPERSEDES edge
# Property fields searched for seed term matches. Includes the fine-tier content
# fields (`statement` on Decisions, `value` on Assertions) so born-on-graph
# decisions/facts are discoverable; and `text` — a Section's body / a CodeText
# region — so memory bodies (M1) and code regions are findable by their CONTENT,
# not just their heading. Seed scoring counts DISTINCT query terms (capped at the
# query's term count), so a long field can't dominate beyond a focused match.
# `source` = notebook Cell sources — without it, notebook-sourced code is invisible
# to the exhaustive literal search while `.py` code (CodeText `text`) is indexed.
_TEXT_FIELDS = ("title", "name", "slug", "key", "description", "statement", "value", "text",
                "source")
# Common words that add noise, not signal, to seed-term matching.
_STOPWORDS = frozenset((
    "the", "and", "for", "with", "this", "that", "from", "into", "are", "was",
    "were", "has", "have", "had", "not", "but", "all", "any", "its", "our",
    "their", "what", "which", "when", "how", "why", "who", "via", "per", "out",
))


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Attribute-or-key access (tolerates typed objects and wire dicts)."""
    if hasattr(obj, key):
        return getattr(obj, key)
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default


def _props(node: Any) -> Dict[str, Any]:
    """A node's properties dict (typed GraphNode or wire dict)."""
    return _get(node, "properties", {}) or {}


def node_summary(node: Any) -> Dict[str, Any]:
    """Compact, provenance-carrying summary of a node (the unit of a bounded read)."""
    p = _props(node)
    out = {"id": _get(node, "id"), "label": _get(node, "label"), "title": node_title(node)}
    if isinstance(p.get("description"), str) and p["description"]:
        out["description"] = p["description"]
    elif isinstance(p.get("text"), str) and p["text"].strip():
        # Fine content nodes (Section / CodeText) carry no `description` — synthesize a
        # short body snippet so a surfaced section is self-describing, not just a heading
        # (the render layer caps it again; the full body is a `read <id>` away).
        snip = " ".join(p["text"].split())
        out["description"] = (snip[:159] + "…") if len(snip) > 160 else snip
    if isinstance(p.get("display_gloss"), str) and p["display_gloss"].strip():
        # The rule-derived orientation line (annotate_display stamps it) — one live
        # line of what the node says/points to; richer detail stays a `read` away.
        out["gloss"] = p["display_gloss"].strip()
    for extra in ("note_type", "entity_kind", "status", "root_kind"):
        if p.get(extra):
            out[extra] = p[extra]
    return out


def _haystack(node: Any) -> str:
    """Lowercased searchable text for a node (its text fields joined)."""
    p = _props(node)
    return " ".join(str(p[f]) for f in _TEXT_FIELDS if p.get(f)).lower()


def _terms(task: str) -> List[str]:
    """Task string -> distinct lowercase search terms (len > 2)."""
    seen: Dict[str, None] = {}
    for w in re.findall(r"[A-Za-z0-9]+", task.lower()):
        if len(w) > 2 and w not in _STOPWORDS:
            seen.setdefault(w, None)
    return list(seen)


async def get_schema(gx: GraphHandle) -> Dict[str, Any]:
    """The graph's ontology: node labels, edge types, per-label counts."""
    return await graph_task(gx.queue, gx.graph_id, "get_schema")


async def graph_overview(
    gx: GraphHandle,
    top_hubs: int = 10,                       # How many hub anchors to surface
    hub_labels: tuple = ("Note",),            # Labels eligible to BE a landmark anchor (the area carriers)
) -> Dict[str, Any]:  # {by_kind:[{kind,count}], hubs:[{id,title,degree}]}
    """The whole-graph orientation view — the facets of the DEFAULT (empty) query.

    Two auto-derived projections that make the onboarding landmark map a PROJECTION
    instead of a hand-seeded list: (1) `by_kind` = the structural coverage (schema
    counts per label — what KINDS of things exist); (2) `hubs` = the most-connected
    nodes by edge degree, restricted to the area-carrying labels (`Note`). The hubs
    empirically recover the curated areas (the Foundational-Picture / arc clusters),
    so connectivity is a usable proxy for 'the landmarks' — no hand-seeding to find
    the territory. Named/grouped areas (community detection) are deferred."""
    schema = await get_schema(gx)
    counts = (schema.get("counts") or {})
    by_kind = [{"kind": k, "count": c} for k, c in
               sorted(counts.items(), key=lambda kv: kv[1], reverse=True)]

    res = await graph_task(gx.queue, gx.graph_id, "query_edges",
                           query=EdgeQuery(project=["source_id", "target_id"],
                                           limit=10_000_000).to_dict())
    rows = _get(res, "rows", None)
    if rows is None:  # non-project fallback (whole edges)
        rows = [{"source_id": _get(e, "source_id"), "target_id": _get(e, "target_id")}
                for e in (_get(res, "edges", []) or [])]
    degree: Dict[str, int] = {}
    for r in rows:
        for end in (r.get("source_id"), r.get("target_id")):
            if end:
                degree[end] = degree.get(end, 0) + 1

    # One id+label scan picks the hub-eligible ids; only the WINNING hubs get
    # their full nodes (was one full find_nodes_by_label round-trip per label —
    # the e128fac6 latency class).
    hub_set = set(hub_labels)
    kind_of: Dict[str, str] = {r["id"]: r["label"]
                               for r in await _scan_rows(gx, ["label"])
                               if r.get("label") in hub_set}
    hub_ids = []
    for nid in sorted(degree, key=lambda i: degree[i], reverse=True):
        if nid in kind_of:
            hub_ids.append(nid)
            if len(hub_ids) >= top_hubs:
                break
    node_of = await F.load_nodes(gx, hub_ids)
    hub_ids = [i for i in hub_ids if i in node_of]
    # Annotate ONLY the selected hubs (annotating every loaded node priced the
    # whole-graph overview at its rule-neighbour fan-out — the 20s serve boot).
    await annotate_display(gx, [node_of[i] for i in hub_ids])
    hubs = [{"id": nid, "title": node_title(node_of[nid]), "degree": degree[nid],
             "kind": kind_of[nid]} for nid in hub_ids]
    return {"by_kind": by_kind, "hubs": hubs}


async def _scan_rows(gx: GraphHandle, fields: List[str],
                     limit: int = 10_000_000) -> List[Dict[str, Any]]:
    """Every node as a FLAT projected row (`id` + the requested fields), one round-trip.

    The whole-graph scans (seed-finding, grep, prefix resolution, hub picking)
    need a few fields, not whole nodes. This replaced the old label-by-label
    full-node gather twice over (the e128fac6 latency class): the label loop was
    a dozen sequential worker round-trips with a per-label cap that silently
    missed nodes past it (the 6.5k-Segments grep lesson), and even a single
    full-node query ships every code body/section text — projection cuts the
    4.8k-node dev-graph scan 258ms -> 75ms (text fields) / 35ms (id+label).
    Winners get their full nodes AFTERWARD via one batched `F.load_nodes`."""
    q = NodeQuery(limit=limit, project=fields)
    res = await graph_task(gx.queue, gx.graph_id, "query_nodes", query=q.to_dict())
    return list(_get(res, "rows", None) or [])


async def find_seeds(
    gx: GraphHandle,
    task: str,           # The task / query text
    k: int = 6,          # Max seeds
) -> List[Any]:  # Seed nodes, best term-match first
    """Find seed nodes by term overlap with their text fields (accept misses).

    v1 seed-finding: count distinct task terms present in each node's text; the
    top-k by count are the seeds. Caller-provided node ids bypass this."""
    terms = _terms(task)
    if not terms:
        return []
    scored = []
    for row in await _scan_rows(gx, list(_TEXT_FIELDS)):
        hay = " ".join(str(row[f]) for f in _TEXT_FIELDS if row.get(f)).lower()
        hits = sum(1 for t in terms if t in hay)
        if hits:
            scored.append((hits, row["id"]))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = [nid for _, nid in scored[:k]]
    nodes = await F.load_nodes(gx, top)  # full nodes for the winners only
    return [nodes[nid] for nid in top if nid in nodes]


# A ref shaped like (part of) a node id: hex + dashes, at least 6 chars. Names/terms
# don't match, so prefix resolution never hijacks a term lookup.
_ID_PREFIX_RE = re.compile(r"^[0-9a-fA-F][0-9a-fA-F-]{5,35}$")


async def resolve_node_ref(
    gx: GraphHandle,
    ref: str,  # A full node id, or a unique id PREFIX (>= 6 hex chars)
) -> Dict[str, Any]:  # {node} | {candidates:[{id,label,title}]} | {} (no match / not id-shaped)
    """Resolve a node reference: exact id first, then unique id-prefix.

    The human habit this serves: ids get cited by their first chunk (`a85327b1` for the
    full UUID) — every id-taking verb should accept that. An AMBIGUOUS prefix returns the
    candidates instead of guessing; the caller surfaces them."""
    node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=ref)
    if node is not None:
        return {"node": node}
    if not _ID_PREFIX_RE.match(ref):
        return {}
    pref = ref.lower()
    hits = [r["id"] for r in await _scan_rows(gx, ["label"])
            if str(r.get("id", "")).lower().startswith(pref)]
    if not hits:
        return {}
    nodes = await F.load_nodes(gx, hits[:10])  # full nodes for the matches only
    if len(hits) == 1 and hits[0] in nodes:
        return {"node": nodes[hits[0]]}
    return {"candidates": [{"id": nid, "label": _get(nodes[nid], "label"),
                            "title": node_title(nodes[nid])}
                           for nid in hits[:10] if nid in nodes]}


def ambiguity_error(ref: str, candidates: List[Dict[str, Any]]) -> str:
    """One-line error naming the candidates, so the caller's next call can be exact."""
    opts = "; ".join(f"{c['id']} ({c['label']})" for c in candidates)
    return f"ambiguous id prefix `{ref}` — candidates: {opts}"


async def subgraph_view(
    gx: GraphHandle,
    refs: List[str],  # Node ids / unique id prefixes — the SET to materialize
    *,
    hops: int = 0,                          # Neighbourhood expansion depth (0 = the given set only)
    relations: Optional[List[str]] = None,  # Expansion relation filter (None = every relation)
    cap: int = 500,                         # Expansion node budget; the given refs are never dropped
) -> Dict[str, Any]:  # {nodes, edges, resolved, missing, ambiguous, seed_count, expanded_count, truncated}
    """The BULK read verb: a node SET -> nodes + interconnecting edges, batched.

    One `query_nodes` for the exact ids, one shared label scan for every prefix,
    two edge queries per expansion hop, one interconnect edge query, one display
    pass — so a canvas/lens application costs a HANDFUL of worker round-trips
    instead of ~N sequential `get_node`s (per-node round-trips serialize through
    the worker queue at ~7.7ms/op; the same class the display batching collapsed
    from 25s to 1.6s). Consumers: `journal_window_view`, the future lens apply,
    the explorer canvas.

    Read-parity: an unresolvable ref stays visible in `missing`, an ambiguous
    prefix in `ambiguous` — the verb never silently narrows what it was asked for."""
    seen: set = set()
    ordered = [r for r in refs if not (r in seen or seen.add(r))]
    exact = [r for r in ordered if len(r) == 36]
    prefixes = [r for r in ordered if len(r) != 36]

    nodes_by_id: Dict[str, Any] = dict(await F.load_nodes(gx, exact))
    resolved: Dict[str, str] = {r: r for r in exact if r in nodes_by_id}
    missing = [r for r in exact if r not in nodes_by_id]
    ambiguous: List[Dict[str, Any]] = []

    if prefixes:
        # One shared id+label scan resolves every prefix (resolve_node_ref
        # semantics, batched); the winners join ONE full-node batch load.
        universe = await _scan_rows(gx, ["label"])
        for r in prefixes:
            if not _ID_PREFIX_RE.match(r):
                missing.append(r)
                continue
            pref = r.lower()
            hits = [row for row in universe
                    if str(row.get("id", "")).lower().startswith(pref)]
            if len(hits) == 1:
                resolved[r] = hits[0]["id"]
            elif hits:
                ambiguous.append({"ref": r, "candidates": [
                    {"id": row["id"], "label": row.get("label")} for row in hits[:10]]})
            else:
                missing.append(r)
        need = [i for i in resolved.values() if i not in nodes_by_id]
        if need:
            nodes_by_id.update(await F.load_nodes(gx, need))

    seed_ids: List[str] = []
    for r in ordered:  # ref order, deduped (a prefix + its full id resolve to ONE node)
        i = resolved.get(r)
        if i is not None and i not in seed_ids:
            seed_ids.append(i)
    known = set(seed_ids)
    frontier = set(seed_ids)
    truncated = False
    proj = ["source_id", "target_id", "relation_type"]
    for _ in range(max(0, hops)):
        if not frontier or truncated:
            break
        fr = sorted(frontier)
        rows: List[Dict[str, Any]] = []
        for q in (EdgeQuery(source_ids=fr, project=proj),
                  EdgeQuery(target_ids=fr, project=proj)):
            res = await graph_task(gx.queue, gx.graph_id, "query_edges", query=q.to_dict())
            rows.extend(_get(res, "rows", None) or [])
        new_ids: List[str] = []
        for row in rows:
            if relations and row.get("relation_type") not in relations:
                continue
            for end in (row.get("source_id"), row.get("target_id")):
                if not end or end in known:
                    continue
                if len(known) - len(seed_ids) >= cap:
                    truncated = True
                    break
                known.add(end)
                new_ids.append(end)
        if new_ids:
            nodes_by_id.update(await F.load_nodes(gx, new_ids))
        frontier = set(new_ids)

    edges: List[Dict[str, Any]] = []
    if nodes_by_id:
        ids = sorted(nodes_by_id)
        res = await graph_task(gx.queue, gx.graph_id, "query_edges",
                               query=EdgeQuery(source_ids=ids, target_ids=ids,
                                               project=proj).to_dict())
        edges = list(_get(res, "rows", None) or [])

    node_objs = list(nodes_by_id.values())
    await annotate_display(gx, node_objs)
    seed_set = set(seed_ids)
    out_nodes: List[Dict[str, Any]] = []
    for nid in seed_ids + [i for i in nodes_by_id if i not in seed_set]:
        n = nodes_by_id.get(nid)
        if n is None:
            continue  # a dangling expansion endpoint (edge outlives its node)
        out_nodes.append({"id": nid, "label": _get(n, "label"),
                          "title": node_title(n), "properties": dict(_props(n)),
                          "expanded": nid not in seed_set})
    return {"refs": len(ordered), "nodes": out_nodes, "edges": edges,
            "resolved": resolved, "missing": missing, "ambiguous": ambiguous,
            "seed_count": len(seed_ids),
            "expanded_count": len(out_nodes) - len(seed_ids),
            "truncated": truncated}


async def show(
    gx: GraphHandle,
    node_id: str,   # Node to expand (full id, or a unique id prefix)
    depth: int = 1, # Neighbourhood depth
) -> Dict[str, Any]:  # {node, neighbours:[{node, relation, direction}]}
    """One node in full, with its immediate neighbours + the relation to each."""
    res = await resolve_node_ref(gx, node_id)
    if "candidates" in res:
        return {"node": None, "neighbours": [], "candidates": res["candidates"],
                "error": ambiguity_error(node_id, res["candidates"])}
    node = res.get("node")
    if node is None:
        return {"node": None, "neighbours": [], "error": f"no node {node_id}"}
    node_id = _get(node, "id")
    ctx = await graph_task(gx.queue, gx.graph_id, "get_context", node_id=node_id, depth=depth)
    by_id = {_get(n, "id"): n for n in (_get(ctx, "nodes", []) or [])}
    await annotate_display(gx, [node, *by_id.values()])  # rule titles before summarizing
    neighbours = []
    for e in (_get(ctx, "edges", []) or []):
        src, tgt, rel = _get(e, "source_id"), _get(e, "target_id"), _get(e, "relation_type")
        if src == node_id and tgt in by_id:
            neighbours.append({"node": node_summary(by_id[tgt]), "relation": rel, "direction": "out"})
        elif tgt == node_id and src in by_id:
            neighbours.append({"node": node_summary(by_id[src]), "relation": rel, "direction": "in"})
    return {"node": node_summary(node), "properties": _props(node), "neighbours": neighbours}


def _recency_factor(node: Any) -> float:
    """Mild recency boost in [1.0, 1.5] from a node's timestamp (1.0 if none)."""
    ts = _get(node, "updated_at") or _get(node, "created_at")
    if not ts:
        return 1.0
    age_days = max(0.0, (time.time() - float(ts)) / 86400.0)
    return 1.0 + 0.5 * (1.0 / (1.0 + age_days / 30.0))  # ~1.5 fresh -> ~1.0 old


async def _score_task(
    gx: GraphHandle,
    task: str,        # The task / query text
    depth: int = 2,   # BFS expansion depth from each seed
) -> "tuple":  # (scores, nodes_by_id, why, seed_of, seeds)
    """Score every node reachable from the task's seeds — the FULL relevance set.

    The shared scorer behind `relevant` (top-k view) and `explore` (faceted
    descent): seeds by term match; expand each via `get_context(depth)`; score
    each reached node by edge-type weight x hop-decay x recency, down-weighting
    superseded nodes. `seed_of[nid]` records which seed gave the winning score —
    the seed-neighbourhood facet (which cluster a hit belongs to)."""
    seeds = await find_seeds(gx, task)
    scores: Dict[str, float] = {}
    nodes_by_id: Dict[str, Any] = {}
    why: Dict[str, str] = {}
    seed_of: Dict[str, str] = {}

    for rank, seed in enumerate(seeds):
        sid = _get(seed, "id")
        seed_weight = float(len(seeds) - rank)  # earlier (better-matching) seeds weigh more
        ctx = await graph_task(gx.queue, gx.graph_id, "get_context", node_id=sid, depth=depth)
        cnodes = {_get(n, "id"): n for n in (_get(ctx, "nodes", []) or [])}
        cnodes[sid] = seed
        nodes_by_id.update(cnodes)
        cedges = _get(ctx, "edges", []) or []
        superseded = {_get(e, "target_id") for e in cedges if _get(e, "relation_type") == "SUPERSEDES"}

        # BFS hop-distances from the seed over the returned edges.
        adj: Dict[str, List[tuple]] = {}
        for e in cedges:
            s, t, r = _get(e, "source_id"), _get(e, "target_id"), _get(e, "relation_type")
            adj.setdefault(s, []).append((t, r))
            adj.setdefault(t, []).append((s, r))
        dist = {sid: 0}
        best_rel = {sid: None}
        frontier = [sid]
        while frontier:
            nxt = []
            for u in frontier:
                for v, r in adj.get(u, []):
                    if v not in dist:
                        dist[v] = dist[u] + 1
                        best_rel[v] = r
                        nxt.append(v)
            frontier = nxt

        for nid, d in dist.items():
            rel = best_rel.get(nid)
            w = EDGE_WEIGHTS.get(rel, 1.0) if rel else 3.0  # the seed itself scores high
            score = seed_weight * (_HOP_DECAY ** d) * w * _recency_factor(cnodes.get(nid, seed))
            if nid in superseded:
                score *= _SUPERSEDED_FACTOR
            if score > scores.get(nid, 0.0):
                scores[nid] = score
                seed_of[nid] = sid
                why[nid] = (f"matches task" if nid == sid
                            else f"{rel or 'linked'} {('->' if d == 1 else f'{d} hops from')} "
                                 f"“{node_title(seed)}”")
    return scores, nodes_by_id, why, seed_of, seeds


def _facet_axis_value(node_id: str, axis: str, nodes_by_id: Dict[str, Any],
                      seed_of: Dict[str, str]) -> Any:
    """A node's value on a facet axis: its label (kind) or its seed-cluster id."""
    if axis == "kind":
        return _get(nodes_by_id.get(node_id), "label", "?")
    if axis == "seed":
        return seed_of.get(node_id)
    return None


def _facet_breakdown(node_ids: List[str], axis: str, task: str, filters: List[Dict[str, Any]],
                     nodes_by_id: Dict[str, Any], seed_of: Dict[str, str],
                     seeds: List[Any]) -> List[Dict[str, Any]]:
    """Count `node_ids` by `axis`, biggest first, each with a re-runnable descent HANDLE.

    A handle is a `(task, filters)` spec — a replayable query, not an in-memory
    offset — so a sub-agent can re-derive exactly this cluster on its own (facets
    are the divide-and-conquer work-partition)."""
    counts: Dict[Any, int] = {}
    for nid in node_ids:
        v = _facet_axis_value(nid, axis, nodes_by_id, seed_of)
        counts[v] = counts.get(v, 0) + 1
    seed_title = {_get(s, "id"): node_title(s) for s in seeds}
    out = []
    for value, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
        entry = {"axis": axis, "value": value, "count": count,
                 "handle": {"task": task, "filters": filters + [{"axis": axis, "value": value}]}}
        if axis == "seed":
            entry["title"] = seed_title.get(value, str(value))
        out.append(entry)
    return out


async def relevant(
    gx: GraphHandle,
    task: str,        # The task / query text
    depth: int = 2,   # BFS expansion depth from each seed
    k: int = 12,      # Max ranked results in the teaser
) -> Dict[str, Any]:  # {task, total_hits, seeds, facets, results}
    """The bounded level-0 pull: the full reached set's SHAPE + a top-k teaser.

    `relevant` already scores the WHOLE reached set and (previously) discarded all
    but the top-k — so the agent couldn't see the other hits existed (invisible
    truncation). This now also returns the set's `total_hits` + facet breakdowns
    (by kind, by seed-cluster), each carrying a re-runnable `explore` handle, so
    the shape is visible in fixed-budget form and any cluster can be descended in
    full. The `results` teaser preserves the old ranked view (back-compat)."""
    scores, nodes_by_id, why, seed_of, seeds = await _score_task(gx, task, depth)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    await annotate_display(gx, [nodes_by_id[nid] for nid, _ in ranked[:k]
                                if nid in nodes_by_id] + list(seeds))
    results = [{**node_summary(nodes_by_id[nid]), "score": round(sc, 3), "why": why.get(nid, "")}
               for nid, sc in ranked[:k] if nid in nodes_by_id]
    all_ids = list(scores)
    facets = {
        "by_kind": _facet_breakdown(all_ids, "kind", task, [], nodes_by_id, seed_of, seeds),
        "by_seed": _facet_breakdown(all_ids, "seed", task, [], nodes_by_id, seed_of, seeds),
    }
    return {"task": task, "total_hits": len(scores),
            "seeds": [node_summary(s) for s in seeds], "facets": facets, "results": results}


async def explore(
    gx: GraphHandle,
    task: str,                        # The original query text (re-scored deterministically)
    filters: List[Dict[str, Any]],    # [{axis, value}] — a node must match ALL (compound = recursive re-facet)
    depth: int = 2,                   # BFS expansion depth (must match the level-0 query)
    budget: int = 15,                 # Max members enumerated before re-faceting instead of dumping
) -> Dict[str, Any]:  # {task, filters, total, complete, members, subfacets?}
    """Descend into one cluster of a query: its members, BOUNDED, re-faceting if large.

    Filters compose (kind=X AND seed=Y), so descent is recursive: when a cluster
    still exceeds `budget`, rather than dump it we return the top-`budget` members
    PLUS a `subfacets` breakdown on an axis not yet filtered — each a compound
    handle to descend further. So no `explore` output ever exceeds a fixed budget,
    at any corpus size (the bounded-by-construction invariant)."""
    scores, nodes_by_id, why, seed_of, seeds = await _score_task(gx, task, depth)
    used = {f["axis"] for f in filters}

    def passes(nid: str) -> bool:
        return all(_facet_axis_value(nid, f["axis"], nodes_by_id, seed_of) == f["value"]
                   for f in filters)

    selected = sorted(((nid, sc) for nid, sc in scores.items() if passes(nid)),
                      key=lambda kv: kv[1], reverse=True)
    total = len(selected)
    await annotate_display(gx, [nodes_by_id[nid] for nid, _ in selected[:max(budget, 15)]
                                if nid in nodes_by_id])
    if total <= budget:
        members = [{**node_summary(nodes_by_id[nid]), "score": round(sc, 3), "why": why.get(nid, "")}
                   for nid, sc in selected]
        return {"task": task, "filters": filters, "total": total, "complete": True, "members": members}

    members = [{**node_summary(nodes_by_id[nid]), "score": round(sc, 3)} for nid, sc in selected[:budget]]
    next_axis = next((a for a in ("seed", "kind") if a not in used), None)
    subfacets = (_facet_breakdown([nid for nid, _ in selected], next_axis, task, filters,
                                  nodes_by_id, seed_of, seeds) if next_axis else [])
    return {"task": task, "filters": filters, "total": total, "complete": False,
            "shown": len(members), "members": members, "subfacets": subfacets}


async def state(
    gx: GraphHandle,
    subject: Optional[str] = None,  # A node id or subject term; None = graph overview
) -> Dict[str, Any]:  # Overview, or the subject's effective view
    """Graph overview (no subject) or a subject's effective view (`show`).

    A subject is resolved as a node id first, then as a term match (first seed)."""
    if subject:
        res = await resolve_node_ref(gx, subject)
        if "candidates" in res:
            return {"subject": subject, "candidates": res["candidates"],
                    "error": ambiguity_error(subject, res["candidates"])}
        if res.get("node") is not None:
            subject = _get(res["node"], "id")
        else:
            seeds = await find_seeds(gx, subject, k=1)
            if not seeds:
                return {"subject": subject, "resolved": None, "note": "no matching node"}
            subject = _get(seeds[0], "id")
        return {"subject": subject, **await show(gx, subject)}
    schema = await get_schema(gx)
    return {"overview": schema,
            "hint": "run `relevant <task>` for task-scoped context, or `show <id>` to drill in"}


# The identifying properties a human handle might name (matched case-insensitively,
# substring). `path`/`module_path` make 'where does this FILE live' a lookup; the
# rest cover symbol/note/entity names + slugs/keys. NOT a content search (that's
# `relevant`) — this resolves a HANDLE to a node + its on-disk location.
_LOCATE_PROPS = ("name", "title", "slug", "key", "module_path", "import_name", "path")


def _locate_row(node: Any) -> Dict[str, Any]:  # {id, label, title, path}
    """The lookup view of a node: id + label + display title + on-disk path (if any)."""
    return {"id": _get(node, "id"), "label": _get(node, "label"),
            "title": node_title(node), "path": _props(node).get("path")}


async def locate(
    gx: GraphHandle,
    term: str,         # A node id, OR a name/title/slug/key/module-path/file-path substring (case-insensitive)
    limit: int = 25,   # Cap the match list
) -> Dict[str, Any]:  # {term, matches:[{id, label, title, path}], count, truncated}
    """Resolve a human HANDLE to node(s) + their on-disk path — the inverse of `show`.

    The file-archeology killer (a soak gap: you rarely know a node's deterministic id
    up front, but you DO know its name — `classify_readiness` — its file —
    `readiness.py` — or its slug). A full node id resolves directly; otherwise `term`
    is matched (case-insensitive substring) across the identifying properties and each
    hit is reported with its `path`, so 'what's the id of X' and 'where does X live'
    are one read instead of a SQL expedition. Content search stays with `relevant`."""
    res = await resolve_node_ref(gx, term)
    if res.get("node") is not None:
        await annotate_display(gx, [res["node"]])
        return {"term": term, "matches": [_locate_row(res["node"])], "count": 1, "truncated": False}
    if "candidates" in res:  # ambiguous id prefix: the candidates ARE the lookup result
        rows = [{"id": c["id"], "label": c["label"], "title": c["title"], "path": None}
                for c in res["candidates"]]
        return {"term": term, "matches": rows, "count": len(rows), "truncated": False}
    # Union of per-property `contains` matches (NodeQuery `where` is AND-only, so OR
    # across properties = one query per property, deduped by id).
    seen: Dict[str, Any] = {}
    for prop in _LOCATE_PROPS:
        q = NodeQuery(where=[PropertyPredicate(prop=prop, op="contains", value=term)],
                      limit=limit + 1)
        res = await graph_task(gx.queue, gx.graph_id, "query_nodes", query=q.to_dict())
        for n in (res.nodes or []):
            nid = _get(n, "id")
            if nid is not None:
                seen.setdefault(nid, n)
    await annotate_display(gx, list(seen.values()))
    rows = sorted((_locate_row(n) for n in seen.values()),
                  key=lambda r: (r["label"] or "", r["title"] or ""))
    return {"term": term, "matches": rows[:limit], "count": len(rows),
            "truncated": len(rows) > limit}


async def grep(
    gx: GraphHandle,
    term: str,          # Exact substring / phrase to find (case-insensitive)
    limit: int = 25,    # Cap the match list
    context: int = 60,  # Snippet context chars either side of the hit
) -> Dict[str, Any]:  # {term, matches:[{id, label, title, field, snippet}], count, truncated}
    """Exact-substring CONTENT search over every node's text fields — the literal third leg.

    Fills the gap between `locate` (identifying-property lookup — misses body content) and
    `relevant` (term-overlap seed ranking — common words seed elsewhere, so an exact phrase
    the corpus contains can still rank out of sight; found driving the explorer, 2026-07-01).
    One hit per node (its first matching field), with a whitespace-normalized snippet around
    the hit so the match is judgeable without a `read`. Content search stays `relevant`'s
    business for RANKING; this is for WHEN YOU KNOW THE WORDS — so the scan is EXHAUSTIVE
    (a bounded scan silently missed nodes past its cap: found on the capability graph's
    6.5k Segments, where grep hits came and went with load order — `_scan_rows` is
    uncapped by default and projects just the text fields)."""
    needle = term.lower()
    if not needle:
        return {"term": term, "matches": [], "count": 0, "truncated": False}
    hits: List[tuple] = []
    for row in await _scan_rows(gx, list(_TEXT_FIELDS)):
        for f in _TEXT_FIELDS:
            v = row.get(f)
            if not v:
                continue
            s = str(v)
            i = s.lower().find(needle)
            if i < 0:
                continue
            start, end = max(0, i - context), min(len(s), i + len(term) + context)
            snippet = (("…" if start else "") + " ".join(s[start:end].split())
                       + ("…" if end < len(s) else ""))
            hits.append((row["id"], f, snippet))
            break  # one hit per node — the match list stays a node list
    # Full nodes for the MATCHES only (title cascade + display rules).
    nodes = await F.load_nodes(gx, [nid for nid, _, _ in hits])
    await annotate_display(gx, list(nodes.values()))
    rows = [{"id": nid, "label": _get(nodes.get(nid), "label"),
             "title": node_title(nodes[nid]) if nid in nodes else nid,
             "field": f, "snippet": snippet}
            for nid, f, snippet in hits]
    rows.sort(key=lambda r: (r["label"] or "", r["title"] or ""))
    return {"term": term, "matches": rows[:limit], "count": len(rows),
            "truncated": len(rows) > limit}
