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

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .config import load_graph_config
from .runtime import DEFAULT_MANIFESTS, open_graph
from .sourcemoves import attribute_moved_sources

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
    moved: Optional[Set[str]] = None,  # Ids a moved source accounts for (DEC a9176261)
) -> Dict[str, Any]:  # {count_a, count_b, only_a, only_b, differing, rows, moved: {only_a, only_b, differing, rows}}
    """Compare two id-keyed element maps: ids on one side only, and per shared id every field.
    A difference on an id in `moved` is reported under `moved`, apart from drift — still
    printed and counted, never dropped."""
    moved = moved or set()
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    diffs: List[Tuple[str, str, List[Tuple[str, Any, Any]]]] = []
    for eid in sorted(set(a) & set(b)):
        wa, wb = _normalize(a[eid], path_map), _normalize(b[eid], path_map)
        fa, fb = _fields(wa, structural), _fields(wb, structural)
        cls = str(wa.get(class_of) or wb.get(class_of) or "?")
        fields = [(f, fa.get(f), fb.get(f)) for f in sorted(set(fa) | set(fb)) if fa.get(f) != fb.get(f)]
        if fields:
            diffs.append((eid, cls, fields))

    def _bucket(keep) -> Dict[str, Any]:  # The report of the differences whose id `keep` admits
        rows: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for eid, cls, fields in diffs:
            if not keep(eid):
                continue
            for f, va, vb in fields:
                row = rows.setdefault((cls, f), {"kind": kind, "class": cls, "field": f,
                                                "count": 0, "samples": []})
                row["count"] += 1
                if len(row["samples"]) < SAMPLES_PER_FIELD:
                    row["samples"].append({"id": eid, "a": va, "b": vb})
        return {"only_a": _by_class([i for i in only_a if keep(i)], a),
                "only_b": _by_class([i for i in only_b if keep(i)], b),
                "differing": sum(1 for eid, _, _ in diffs if keep(eid)),
                "rows": sorted(rows.values(), key=lambda r: (-r["count"], r["class"], r["field"]))}

    def _by_class(ids: List[str], side: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        for i in ids:
            c = str(side[i].get(class_of) or "?")
            counts[c] = counts.get(c, 0) + 1
        return {"count": len(ids), "by_class": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
                "sample": ids[:SAMPLES_PER_FIELD]}

    return {"count_a": len(a), "count_b": len(b), **_bucket(lambda i: i not in moved),
            "moved": _bucket(lambda i: i in moved)}


def diff_graphs(
    nodes_a: Iterable[Any], edges_a: Iterable[Any],  # Graph A's elements (typed or wire dicts)
    nodes_b: Iterable[Any], edges_b: Iterable[Any],  # Graph B's elements
    path_map: Optional[List[Tuple[str, str]]] = None,  # (old prefix, new prefix) path rewrites
    moved: Optional[Set[str]] = None,  # Element ids a moved source accounts for (DEC a9176261)
) -> Dict[str, Any]:  # {nodes, edges, clean, moved}
    """The pure comparison: node and edge id sets, then every field on each shared id.

    `moved` ids — the elements a moved source's changed paths decompose to — are attributed,
    and so is every edge they ORIGINATE on either side (a site link resolved from a moved
    post's body is that post's row too). `clean` = no unattributed difference; `moved` counts
    the attributed ones."""
    pm = list(path_map or [])
    na = {w["id"]: w for w in map(_wire, nodes_a)}
    nb = {w["id"]: w for w in map(_wire, nodes_b)}
    ea = {w["id"]: w for w in map(_wire, edges_a)}
    eb = {w["id"]: w for w in map(_wire, edges_b)}
    moved = set(moved or ())
    if moved:
        moved |= {i for side in (ea, eb) for i, w in side.items() if w.get("source_id") in moved}
    nodes = _diff_elements(na, nb, "node", "label", ["label"], pm, moved)
    edges = _diff_elements(ea, eb, "edge", "relation_type",
                           ["source_id", "target_id", "relation_type"], pm, moved)
    clean = all(not d["only_a"]["count"] and not d["only_b"]["count"] and not d["differing"]
                for d in (nodes, edges))
    attributed = sum(d["moved"]["only_a"]["count"] + d["moved"]["only_b"]["count"] + d["moved"]["differing"]
                     for d in (nodes, edges))
    return {"nodes": nodes, "edges": edges, "clean": clean, "moved": attributed}


async def _export(
    db_path: str,        # A graph db (opened read-only; must exist)
    manifests_dir: str,  # Dir holding the graph-storage capability manifest
) -> Tuple[List[Any], List[Any], Dict[str, str]]:  # (nodes, edges, the ingest record)
    """The whole graph through the capability's export, and the ingest record beside it
    (DEC a9176261; {} for a db built before the record existed), read-only."""
    from cjm_context_graph_layer.ops import graph_task
    async with open_graph(db_path, manifests_dir, readonly=True) as gx:
        ctx = await graph_task(gx.queue, gx.graph_id, "export_graph")
        record = await graph_task(gx.queue, gx.graph_id, "ingest_sources")
    nodes = ctx.nodes if hasattr(ctx, "nodes") else ctx.get("nodes", [])
    edges = ctx.edges if hasattr(ctx, "edges") else ctx.get("edges", [])
    return list(nodes or []), list(edges or []), dict(record or {})


async def rebuild_diff(
    db_a: str,                                   # Graph A (by convention the LIVE db)
    db_b: str,                                   # Graph B (by convention its REBUILD)
    path_map: Optional[List[Tuple[str, str]]] = None,  # (old prefix, new prefix) path rewrites
    manifests_dir: str = DEFAULT_MANIFESTS,      # Dir holding the graph-storage capability manifest
    config: Optional[Dict[str, Any]] = None,     # The lane's graph config (None: beside db_b, else beside db_a)
) -> Dict[str, Any]:  # The diff report + the two paths + the ingest records' sources
    """Compare two graph dbs property for property — the live-versus-rebuild standing check.

    Each db's ingest record names the source HEADs it was built from; a source whose HEAD
    differs between the two moved between the ingests, and the rows its changed paths account
    for are attributed to it (DEC a9176261) — listed apart from drift, never dropped."""
    nodes_a, edges_a, rec_a = await _export(db_a, manifests_dir)
    nodes_b, edges_b, rec_b = await _export(db_b, manifests_dir)
    if config is None:
        config = load_graph_config(db_b) or load_graph_config(db_a)
    moves = attribute_moved_sources(rec_a, rec_b, path_map, config)
    res = diff_graphs(nodes_a, edges_a, nodes_b, edges_b, path_map, moved=moves["ids"])
    res.update({"a": db_a, "b": db_b, "path_map": [list(p) for p in (path_map or [])],
                "record": moves["record"], "sources": moves["sources"]})
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
    """Markdown by class and field (the drift map), or the report as JSON for agents. Rows a
    moved source accounts for print under each kind as `↪ source moved`, apart from drift."""
    if fmt == "agent":
        import json
        return json.dumps(res, default=str)
    head = f"## Rebuild diff — {'CLEAN' if res['clean'] else 'DIFFERENCES'}"
    if res.get("moved"):
        head += f" · {res['moved']} row(s) attributed to moved sources"
    lines = [head, f"A `{res['a']}`", f"B `{res['b']}`"]
    for pm in res.get("path_map") or []:
        lines.append(f"path map `{pm[0]}` -> `{pm[1]}`")
    rec, sources = res.get("record"), res.get("sources") or []
    if rec is not None and (sources or bool(rec["a"]) != bool(rec["b"])):
        lines.append(f"\n### Sources — ingest record A {rec['a']} · B {rec['b']}")
        if bool(rec["a"]) != bool(rec["b"]):
            lines.append("- ⚠ one db holds no ingest record (built before DEC a9176261): nothing is attributed")
        for s in sources:
            span = f"{(s['a'] or '—')[:12]} -> {(s['b'] or '—')[:12]}"
            if s["status"] == "unattributed":
                lines.append(f"- ⚠ `{s['source']}` {span}: NOT attributed — {s['reason']}")
            else:
                lines.append(f"- source {s['status']} `{s['source']}` {span}: "
                             f"{len(s['paths'])} path(s) · {s['elements']} element(s)")
                for p in s["paths"][:SAMPLES_PER_FIELD]:
                    lines.append(f"    {p}")
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
        m = d.get("moved") or {}
        if m and (m["only_a"]["count"] or m["only_b"]["count"] or m["differing"]):
            lines.append(f"- ↪ source moved: only in A {m['only_a']['count']} · "
                         f"only in B {m['only_b']['count']} · differing {m['differing']}")
            for r in m["rows"]:
                lines.append(f"    ↪ {r['class']}.{r['field']} × {r['count']}")
    return "\n".join(lines)
