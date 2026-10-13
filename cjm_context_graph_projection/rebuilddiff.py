"""A live graph against its own rebuild, property for property (design 8f6f2343, finding fbce0173).

Every time field is a function of the durable sources, never of when a projection ran, so a
live db and a rebuild from the same journals must be EQUAL: the same node and edge ids, and on
every shared id the same label, properties, sources and created_at / updated_at. This module is
the dev lane's standing check of that property. The comparison itself is the layer's, one for
every lane (`cjm_context_graph_layer.graphdiff`, ruling f8b9c728): both graphs read through the
graph-storage capability read-only, streamed side by side in id-ordered keyset pages, with NO
exclusion list: a difference is either a live write path that reads its own clock or an ingest
class whose times do not yet come from its source, and the report names both by class so
neither hides.

What this lane adds is its known cause: a git source that moved between the two ingests (DEC
a9176261) accounts for the rows its changed paths decompose to, attributed as `source moved`,
apart from drift. Paths are the one normalization: `path_map` rewrites a moved corpus's prefix
in every string value on BOTH sides before comparing (19f699d0)."""

from typing import Any, Dict, Iterable, List, Optional, Tuple

from cjm_context_graph_layer.graphdiff import (diff_stores, IdAttribution, render_head,
                                               render_kinds, SAMPLES_PER_FIELD, STREAM_HISTORY)

from .config import load_graph_config
from .runtime import DEFAULT_MANIFESTS, open_graph
from .sourcemoves import attribute_moved_sources

SOURCE_MOVED = "source moved"  # The dev lane's attribution class (DEC a9176261)


def moved_sources(
    ids: Iterable[str],  # Element ids a moved source accounts for
) -> IdAttribution:  # The dev lane's cause: those rows and every edge they originate
    """The `source moved` attribution: the rows a moved source's changed paths decompose to,
    and every edge they ORIGINATE on either side (a site link resolved from a moved post's
    body is that post's row too)."""
    return IdAttribution(SOURCE_MOVED, frozenset(ids))


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
    from cjm_context_graph_layer.ops import graph_task
    async with open_graph(db_a, manifests_dir, readonly=True, max_history=STREAM_HISTORY) as ga, \
            open_graph(db_b, manifests_dir, readonly=True, max_history=STREAM_HISTORY) as gb:
        rec_a = dict(await graph_task(ga.queue, ga.graph_id, "ingest_sources") or {})
        rec_b = dict(await graph_task(gb.queue, gb.graph_id, "ingest_sources") or {})
        if config is None:
            config = load_graph_config(db_b) or load_graph_config(db_a)
        moves = attribute_moved_sources(rec_a, rec_b, path_map, config)
        res = await diff_stores(ga, gb, path_map, attribution=[moved_sources(moves["ids"])])
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


def render_rebuild_diff(
    res: Dict[str, Any],  # The rebuild_diff report
    fmt: str = "human",   # "human" | "agent"
) -> str:  # The rendered report
    """Markdown by class and field (the drift map, the layer's), with this lane's Sources
    section, or the report as JSON for agents. Rows a moved source accounts for print under
    each kind as `↪ source moved`, apart from drift."""
    if fmt == "agent":
        import json
        return json.dumps(res, default=str)
    lines = [render_head(res), f"A `{res['a']}`", f"B `{res['b']}`"]
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
    return "\n".join(lines + render_kinds(res))
