"""A live graph against its own rebuild, property for property (design 8f6f2343, finding fbce0173).

Every time field is a function of the durable sources, never of when a projection ran, so a
live db and a rebuild from the same journals must be EQUAL: the same node and edge ids, and on
every shared id the same label, properties, sources and created_at / updated_at. This module is
the standing check of that property. It compares two graphs read through the graph-storage
capability (both opened read-only), with NO exclusion list: a difference is either a live write
path that reads its own clock or an ingest class whose times do not yet come from its source,
and the report names both by class so neither hides.

Paths are the one normalization: a move of a corpus on disk (the website repo onto the SN850X
disk, 19f699d0) changes absolute paths without changing knowledge, so `path_map` rewrites a
prefix in every string value on BOTH sides before comparing. Times compare exactly — a rebuild
takes the journaled float verbatim, so any inequality is real."""

from typing import Any, Dict, Iterable, List, Optional, Tuple

from .runtime import DEFAULT_MANIFESTS, open_graph

SAMPLES_PER_FIELD = 5  # Differing ids shown per (kind, class, field) row


def _wire(
    x: Any,  # A GraphNode / GraphEdge, or its wire dict
) -> Dict[str, Any]:  # The wire dict
    """The wire dict of a typed graph element (task results may arrive typed or as dicts)."""
    return x.to_dict() if hasattr(x, "to_dict") else dict(x)


def _normalize(
    v: Any,                               # Any JSON-shaped value
    path_map: List[Tuple[str, str]],      # (old prefix, new prefix) pairs, applied in order
) -> Any:  # The value with every mapped path prefix rewritten
    """Rewrite mapped path prefixes in every string, recursively (keys stay as they are)."""
    if not path_map:
        return v
    if isinstance(v, str):
        for old, new in path_map:
            if v.startswith(old):
                return new + v[len(old):]
        return v
    if isinstance(v, dict):
        return {k: _normalize(x, path_map) for k, x in v.items()}
    if isinstance(v, list):
        return [_normalize(x, path_map) for x in v]
    return v


def _fields(
    w: Dict[str, Any],       # A normalized node or edge wire dict
    structural: Iterable[str],  # The element's identity-bearing top-level fields
) -> Dict[str, Any]:  # field name -> value, properties flattened one level as `properties.<key>`
    """The compared fields of one element: its structural fields, each property key on its
    own row (so a report says WHICH property drifts), sources whole, both time columns."""
    out: Dict[str, Any] = {f: w.get(f) for f in structural}
    for k, v in (w.get("properties") or {}).items():
        out[f"properties.{k}"] = v
    if "sources" in w:
        out["sources"] = w.get("sources") or []
    out["created_at"] = w.get("created_at")
    out["updated_at"] = w.get("updated_at")
    return out


def _diff_elements(
    a: Dict[str, Dict[str, Any]],  # id -> wire dict, graph A
    b: Dict[str, Dict[str, Any]],  # id -> wire dict, graph B
    kind: str,                     # "node" | "edge"
    class_of: str,                 # The wire key naming an element's class (label / relation_type)
    structural: List[str],         # Identity-bearing fields compared besides properties + times
    path_map: List[Tuple[str, str]],
) -> Dict[str, Any]:  # {count_a, count_b, only_a, only_b, differing, rows}
    """Compare two id-keyed element maps: ids on one side only, and per shared id every field."""
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    rows: Dict[Tuple[str, str], Dict[str, Any]] = {}
    differing = 0
    for eid in sorted(set(a) & set(b)):
        wa, wb = _normalize(a[eid], path_map), _normalize(b[eid], path_map)
        fa, fb = _fields(wa, structural), _fields(wb, structural)
        cls = str(wa.get(class_of) or wb.get(class_of) or "?")
        hit = False
        for f in sorted(set(fa) | set(fb)):
            va, vb = fa.get(f), fb.get(f)
            if va == vb:
                continue
            hit = True
            row = rows.setdefault((cls, f), {"kind": kind, "class": cls, "field": f,
                                            "count": 0, "samples": []})
            row["count"] += 1
            if len(row["samples"]) < SAMPLES_PER_FIELD:
                row["samples"].append({"id": eid, "a": va, "b": vb})
        differing += hit

    def _by_class(ids: List[str], side: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for i in ids:
            c = str(side[i].get(class_of) or "?")
            counts[c] = counts.get(c, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    return {"count_a": len(a), "count_b": len(b),
            "only_a": {"count": len(only_a), "by_class": _by_class(only_a, a),
                       "sample": only_a[:SAMPLES_PER_FIELD]},
            "only_b": {"count": len(only_b), "by_class": _by_class(only_b, b),
                       "sample": only_b[:SAMPLES_PER_FIELD]},
            "differing": differing,
            "rows": sorted(rows.values(), key=lambda r: (-r["count"], r["class"], r["field"]))}


def diff_graphs(
    nodes_a: Iterable[Any], edges_a: Iterable[Any],  # Graph A's elements (typed or wire dicts)
    nodes_b: Iterable[Any], edges_b: Iterable[Any],  # Graph B's elements
    path_map: Optional[List[Tuple[str, str]]] = None,  # (old prefix, new prefix) path rewrites
) -> Dict[str, Any]:  # {nodes, edges, clean}
    """The pure comparison: node and edge id sets, then every field on each shared id."""
    pm = list(path_map or [])
    na = {w["id"]: w for w in map(_wire, nodes_a)}
    nb = {w["id"]: w for w in map(_wire, nodes_b)}
    ea = {w["id"]: w for w in map(_wire, edges_a)}
    eb = {w["id"]: w for w in map(_wire, edges_b)}
    nodes = _diff_elements(na, nb, "node", "label", ["label"], pm)
    edges = _diff_elements(ea, eb, "edge", "relation_type",
                           ["source_id", "target_id", "relation_type"], pm)
    clean = all(not d["only_a"]["count"] and not d["only_b"]["count"] and not d["differing"]
                for d in (nodes, edges))
    return {"nodes": nodes, "edges": edges, "clean": clean}


async def _export(
    db_path: str,        # A graph db (opened read-only; must exist)
    manifests_dir: str,  # Dir holding the graph-storage capability manifest
) -> Tuple[List[Any], List[Any]]:  # (nodes, edges)
    """The whole graph through the capability's export, read-only."""
    from cjm_context_graph_layer.ops import graph_task
    async with open_graph(db_path, manifests_dir, readonly=True) as gx:
        ctx = await graph_task(gx.queue, gx.graph_id, "export_graph")
    nodes = ctx.nodes if hasattr(ctx, "nodes") else ctx.get("nodes", [])
    edges = ctx.edges if hasattr(ctx, "edges") else ctx.get("edges", [])
    return list(nodes or []), list(edges or [])


async def rebuild_diff(
    db_a: str,                                   # Graph A (by convention the LIVE db)
    db_b: str,                                   # Graph B (by convention its REBUILD)
    path_map: Optional[List[Tuple[str, str]]] = None,  # (old prefix, new prefix) path rewrites
    manifests_dir: str = DEFAULT_MANIFESTS,      # Dir holding the graph-storage capability manifest
) -> Dict[str, Any]:  # The diff report + the two paths
    """Compare two graph dbs property for property — the live-versus-rebuild standing check."""
    nodes_a, edges_a = await _export(db_a, manifests_dir)
    nodes_b, edges_b = await _export(db_b, manifests_dir)
    res = diff_graphs(nodes_a, edges_a, nodes_b, edges_b, path_map)
    res.update({"a": db_a, "b": db_b, "path_map": [list(p) for p in (path_map or [])]})
    return res


def parse_path_map(
    pairs: Optional[List[str]],  # `OLD=NEW` strings from the CLI
) -> List[Tuple[str, str]]:  # (old prefix, new prefix) pairs
    """Parse `--path-map OLD=NEW` flags; a pair without `=` is refused loudly."""
    out: List[Tuple[str, str]] = []
    for p in pairs or []:
        if "=" not in p:
            raise ValueError(f"--path-map takes OLD=NEW, got {p!r}")
        old, new = p.split("=", 1)
        out.append((old, new))
    return out


def _short(v: Any, width: int = 70) -> str:
    """One value, clipped for a report line."""
    s = repr(v)
    return s if len(s) <= width else s[: width - 1] + "…"


def render_rebuild_diff(
    res: Dict[str, Any],  # The rebuild_diff report
    fmt: str = "human",   # "human" | "agent"
) -> str:  # The rendered report
    """Markdown by class and field (the drift map), or the report as JSON for agents."""
    if fmt == "agent":
        import json
        return json.dumps(res, default=str)
    lines = [f"## Rebuild diff — {'CLEAN' if res['clean'] else 'DIFFERENCES'}",
             f"A `{res['a']}`", f"B `{res['b']}`"]
    for pm in res.get("path_map") or []:
        lines.append(f"path map `{pm[0]}` -> `{pm[1]}`")
    for kind in ("nodes", "edges"):
        d = res[kind]
        lines.append(f"\n### {kind.capitalize()} — A {d['count_a']} · B {d['count_b']} · "
                     f"only in A {d['only_a']['count']} · only in B {d['only_b']['count']} · "
                     f"differing {d['differing']}")
        for side, name in (("only_a", "only in A"), ("only_b", "only in B")):
            if d[side]["count"]:
                cls = " · ".join(f"{c} {n}" for c, n in d[side]["by_class"].items())
                lines.append(f"- {name}: {cls}")
        for r in d["rows"]:
            lines.append(f"- **{r['class']}.{r['field']}** × {r['count']}")
            for s in r["samples"][:2]:
                lines.append(f"    `{s['id'][:8]}` A {_short(s['a'])} · B {_short(s['b'])}")
    return "\n".join(lines)
