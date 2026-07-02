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
_TEXT_FIELDS = ("title", "name", "slug", "key", "description", "statement", "value", "text")
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


def node_title(node: Any) -> str:
    """Best display label for a node (first non-empty text field, else its id)."""
    p = _props(node)
    for f in ("title", "name", "slug", "key", "statement", "value"):
        v = p.get(f)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return _get(node, "id", "?")


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

    title_of: Dict[str, str] = {}
    kind_of: Dict[str, str] = {}
    for label in hub_labels:
        nodes = await graph_task(gx.queue, gx.graph_id, "find_nodes_by_label",
                                 label=label, limit=5000)
        for n in (nodes or []):
            title_of[_get(n, "id")] = node_title(n)
            kind_of[_get(n, "id")] = label
    hubs = []
    for nid in sorted(degree, key=lambda i: degree[i], reverse=True):
        if nid in title_of:
            hubs.append({"id": nid, "title": title_of[nid], "degree": degree[nid],
                         "kind": kind_of[nid]})
            if len(hubs) >= top_hubs:
                break
    return {"by_kind": by_kind, "hubs": hubs}


async def _all_nodes(gx: GraphHandle, per_label: int = 1000) -> List[Any]:
    """Every node, gathered label by label (bounded per label)."""
    schema = await get_schema(gx)
    nodes: List[Any] = []
    for label in schema.get("node_labels", []):
        res = await graph_task(gx.queue, gx.graph_id, "find_nodes_by_label",
                               label=label, limit=per_label)
        nodes.extend(res or [])
    return nodes


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
    for n in await _all_nodes(gx):
        hay = _haystack(n)
        hits = sum(1 for t in terms if t in hay)
        if hits:
            scored.append((hits, n))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [n for _, n in scored[:k]]


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
    hits = [n for n in await _all_nodes(gx, per_label=1_000_000)
            if str(_get(n, "id", "")).lower().startswith(pref)]
    if len(hits) == 1:
        return {"node": hits[0]}
    if hits:
        return {"candidates": [{"id": _get(n, "id"), "label": _get(n, "label"),
                                "title": node_title(n)} for n in hits[:10]]}
    return {}


def ambiguity_error(ref: str, candidates: List[Dict[str, Any]]) -> str:
    """One-line error naming the candidates, so the caller's next call can be exact."""
    opts = "; ".join(f"{c['id']} ({c['label']})" for c in candidates)
    return f"ambiguous id prefix `{ref}` — candidates: {opts}"


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
    (`_all_nodes`' default per-label cap would silently miss nodes past 1000: found on the
    capability graph's 6.5k Segments, where grep hits came and went with load order)."""
    needle = term.lower()
    if not needle:
        return {"term": term, "matches": [], "count": 0, "truncated": False}
    rows = []
    for n in await _all_nodes(gx, per_label=1_000_000):
        p = _props(n)
        for f in _TEXT_FIELDS:
            v = p.get(f)
            if not v:
                continue
            s = str(v)
            i = s.lower().find(needle)
            if i < 0:
                continue
            start, end = max(0, i - context), min(len(s), i + len(term) + context)
            snippet = (("…" if start else "") + " ".join(s[start:end].split())
                       + ("…" if end < len(s) else ""))
            rows.append({"id": _get(n, "id"), "label": _get(n, "label"),
                         "title": node_title(n), "field": f, "snippet": snippet})
            break  # one hit per node — the match list stays a node list
    rows.sort(key=lambda r: (r["label"] or "", r["title"] or ""))
    return {"term": term, "matches": rows[:limit], "count": len(rows),
            "truncated": len(rows) > limit}
