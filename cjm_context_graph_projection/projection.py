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
# Property fields searched for seed term matches + used as a display title. Includes
# the fine-tier content fields (`statement` on Decisions, `value` on Assertions) so
# born-on-graph decisions/facts are discoverable by `relevant`, not just coarse Notes.
_TEXT_FIELDS = ("title", "name", "slug", "key", "description", "statement", "value")
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


async def show(
    gx: GraphHandle,
    node_id: str,   # Node to expand
    depth: int = 1, # Neighbourhood depth
) -> Dict[str, Any]:  # {node, neighbours:[{node, relation, direction}]}
    """One node in full, with its immediate neighbours + the relation to each."""
    node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=node_id)
    if node is None:
        return {"node": None, "neighbours": [], "error": f"no node {node_id}"}
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


async def relevant(
    gx: GraphHandle,
    task: str,        # The task / query text
    depth: int = 2,   # BFS expansion depth from each seed
    k: int = 12,      # Max ranked results
) -> Dict[str, Any]:  # {task, seeds, results:[{...,score,why}]}
    """Structurally nearest nodes to a task, ranked (the relevance read).

    Seeds by term match; expand each via `get_context(depth)`; score every
    reached node by edge-type weight x hop-decay x recency, down-weighting
    superseded nodes; return the bounded top-k with a one-line `why`."""
    seeds = await find_seeds(gx, task)
    scores: Dict[str, float] = {}
    nodes_by_id: Dict[str, Any] = {}
    why: Dict[str, str] = {}

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
                why[nid] = (f"matches task" if nid == sid
                            else f"{rel or 'linked'} {('->' if d == 1 else f'{d} hops from')} "
                                 f"“{node_title(seed)}”")

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k]
    results = [{**node_summary(nodes_by_id[nid]), "score": round(sc, 3), "why": why.get(nid, "")}
               for nid, sc in ranked if nid in nodes_by_id]
    return {"task": task, "seeds": [node_summary(s) for s in seeds], "results": results}


async def state(
    gx: GraphHandle,
    subject: Optional[str] = None,  # A node id or subject term; None = graph overview
) -> Dict[str, Any]:  # Overview, or the subject's effective view
    """Graph overview (no subject) or a subject's effective view (`show`).

    A subject is resolved as a node id first, then as a term match (first seed)."""
    if subject:
        node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=subject)
        if node is None:
            seeds = await find_seeds(gx, subject, k=1)
            if not seeds:
                return {"subject": subject, "resolved": None, "note": "no matching node"}
            subject = _get(seeds[0], "id")
        return {"subject": subject, **await show(gx, subject)}
    schema = await get_schema(gx)
    return {"overview": schema,
            "hint": "run `relevant <task>` for task-scoped context, or `show <id>` to drill in"}
