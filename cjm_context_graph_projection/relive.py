"""The LIVE half of the code fold (design amendment 2cc81d3b, build B2 of leg B 0e3508fd).

A live code verb lands its source-journal records first (journal-first, `journaled_emit`),
then applies THE SAME STEP a rebuild folds — `CodeFold.apply` — over exactly those records,
with the state READ FROM THE DB (no second store; the standing rebuild-diff catches any
divergence):

- the journal PREFIX (every record before the verb's) advances the identity walk and the
  inventory (`CodeFold.advance`), so each record decomposes under the map as of itself;
- the resolution indexes are seeded from ONE projected read of every CodeModule and
  CodeSymbol (the properties the scopes key on), and each touched module from its full db
  state, times included;
- the records are applied and the group settled; the delta is committed IN PLACE: removed
  nodes deleted, new and changed nodes written through `import_graph` overwrite (label,
  properties and sources replaced; created_at and every edge kept; updated_at carried — the
  record's ts when the content changed, else unchanged), edges added and removed by id.

Every touched module's own derived edges are also reconciled against the db, so drift from
before this step (the 685da419 class) heals on touch and is reported, never hidden. This
replaces the hand-written db updates the verbs carried (author's slot merge, add-symbol's
node + two edges, `relive_modules`' region diff with USES/CALLS left to a rebuild).
"""

from typing import Any, Dict, List, Optional, Tuple

from cjm_context_graph_layer.ops import graph_task
from cjm_context_graph_primitives.query import EdgeQuery, NodeQuery, PropertyPredicate
from cjm_dev_graph_schema.identity import code_module_node_id
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from . import factlayer as F
from .codefold import CodeFold
from .runtime import GraphHandle
from .seeds import conceptual_key
from .source_state import read_source_journal

# The relations the code fold derives — an edge of one of these OUT of a code node is the
# fold's (the local ABOUT / DEFINES / CONTAINS and the resolved CALLS / USES / IMPORTS / TESTS).
CODE_RELATIONS = (DevRelations.ABOUT, DevRelations.DEFINES, DevRelations.CONTAINS,
                  DevRelations.CALLS, DevRelations.USES, DevRelations.IMPORTS, DevRelations.TESTS)

# What the resolution scopes read off each label (`codefold._scopes`).
_INDEX_PROPS = {
    DevNodeKinds.CODE_SYMBOL: ["qualname", "module_id", "calls", "refs"],
    DevNodeKinds.CODE_MODULE: ["repo_key", "module_path", "import_name", "imports"],
}


def _wire(
    node: Any,  # A GraphNode / GraphEdge (typed) or its wire dict
) -> Dict[str, Any]:  # The wire dict (sources as dicts, times carried)
    """A db read as a plain wire dict."""
    return node.to_dict() if hasattr(node, "to_dict") else dict(node)


def _rows(res: Any) -> List[Dict[str, Any]]:  # Projected rows off a query result
    """The `rows` of a projected query result (typed or wire)."""
    return list(getattr(res, "rows", None) or (res.get("rows") if isinstance(res, dict) else None)
                or [])


def _nodes(res: Any) -> List[Dict[str, Any]]:  # Full node wires off a query result
    """The `nodes` of a full query result (typed or wire), as wire dicts."""
    return [_wire(n) for n in (getattr(res, "nodes", None)
                               or (res.get("nodes") if isinstance(res, dict) else None) or [])]


async def _index_wires(
    gx: GraphHandle,
    label: str,  # CodeSymbol or CodeModule
) -> List[Dict[str, Any]]:  # One wire per node of the label, carrying the index properties
    """Every node of a label, projected to the properties the scopes read — ONE read (one
    consistent snapshot), guarded by the true total so a capped read can never pass for the
    corpus (the 8ac72523 rule)."""
    res = await graph_task(gx.queue, gx.graph_id, "query_nodes",
                           query=NodeQuery(label=label, project=_INDEX_PROPS[label]).to_dict())
    rows = _rows(res)
    total = await F.count_label(gx, label)
    if len(rows) != total:
        raise RuntimeError(f"live code fold: read {len(rows)} {label} row(s) of {total} — "
                           "the index read is incomplete, refusing to derive from it")
    return [{"id": r["id"], "label": label,
             "properties": {k: r.get(k) for k in _INDEX_PROPS[label]}} for r in rows]


async def _held_modules(
    gx: GraphHandle,
    keys: List[Tuple[str, str]],  # The touched modules' conceptual keys
) -> Tuple[Dict[Tuple[str, str], List[Dict[str, Any]]], List[Dict[str, Any]]]:  # (key -> held node wires, every held code edge out of them)
    """The touched modules as the db holds them: each module node + every node homed in it
    (times carried), and every code-relation edge leaving any of those nodes."""
    mids = {code_module_node_id(k[0], k[1]): k for k in keys}
    held: Dict[Tuple[str, str], List[Dict[str, Any]]] = {k: [] for k in keys}
    res = await graph_task(gx.queue, gx.graph_id, "query_nodes",
                           query=NodeQuery(ids=sorted(mids), limit=len(mids)).to_dict())
    for n in _nodes(res):
        held[mids[n["id"]]].append(n)
    for label in (DevNodeKinds.CODE_SYMBOL, DevNodeKinds.CODE_TEXT):
        res = await graph_task(gx.queue, gx.graph_id, "query_nodes", query=NodeQuery(
            label=label, where=[PropertyPredicate("module_id", "in", sorted(mids))]).to_dict())
        for n in _nodes(res):
            held[mids[n["properties"]["module_id"]]].append(n)
    ids = [n["id"] for ns in held.values() for n in ns]
    edges: List[Dict[str, Any]] = []
    if ids:
        res = await graph_task(gx.queue, gx.graph_id, "query_edges",
                               query=EdgeQuery(source_ids=ids).to_dict())
        edges = [_wire(e) for e in (getattr(res, "edges", None)
                                    or (res.get("edges") if isinstance(res, dict) else None)
                                    or [])
                 if _wire(e)["relation_type"] in CODE_RELATIONS]
    return held, edges


def _touched_keys(
    records: List[Dict[str, Any]],  # The group's records
) -> List[Tuple[str, str]]:  # Conceptual keys of the .py modules they re-derive, in order
    """The modules a group re-derives (source / retire records of .py keys)."""
    out: Dict[Tuple[str, str], None] = {}
    for rec in records:
        a = rec.get("args", {})
        if rec.get("verb") in ("source", "retire") and str(a.get("module_path", "")).endswith(".py"):
            out[(conceptual_key(a.get("repo_key")), a.get("module_path"))] = None
    return list(out)


async def apply_live(
    gx: GraphHandle,
    source_journal_path: Optional[str],  # The source journal the records were appended to
    repos_dir: Optional[str],            # The repos root (a node's provenance path derives under it — as ingest's)
    appended: List[Dict[str, Any]],      # The records this invocation appended (`collect_appends`), in order
) -> Dict[str, Any]:  # The receipt: modules, node/edge counts, healed drift, collisions
    """Apply the code fold's step to the db for the records a live verb just appended."""
    receipt: Dict[str, Any] = {"records": len(appended), "modules": [], "nodes_added": 0,
                               "nodes_updated": 0, "nodes_removed": 0, "edges_added": 0,
                               "edges_removed": 0, "edges_skipped": 0, "healed_edges": 0,
                               "collisions": []}
    keys = _touched_keys(appended)
    if not keys:
        return receipt
    if not (source_journal_path and repos_dir):
        raise RuntimeError("live code fold: needs the source journal and the repos root")
    records = read_source_journal(source_journal_path)
    k = len(appended)
    if records[-k:] != appended:
        raise RuntimeError("live code fold: the journal's tail is not this op's records "
                           "(a concurrent writer?) — the db is left for the next rebuild")
    fold = CodeFold(repos_dir, normalize=True)
    for rec in records[:-k]:
        fold.advance(rec)

    # The state, read from the db: the resolution indexes over the whole corpus, then the
    # touched modules in full (their index wires replaced by the full ones).
    held, held_edges = await _held_modules(gx, keys)
    mods = await _index_wires(gx, DevNodeKinds.CODE_MODULE)
    key_of = {m["id"]: (m["properties"]["repo_key"], m["properties"]["module_path"]) for m in mods}
    corpus: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for m in mods:
        corpus.setdefault(key_of[m["id"]], []).append(m)
    for s in await _index_wires(gx, DevNodeKinds.CODE_SYMBOL):
        key = key_of.get(s["properties"]["module_id"])
        if key is not None:
            corpus.setdefault(key, []).append(s)
    for key in keys:
        corpus[key] = [n for n in held[key]
                       if n["label"] in (DevNodeKinds.CODE_MODULE, DevNodeKinds.CODE_SYMBOL)]
    fold.seed_corpus(corpus)
    local = (DevRelations.ABOUT, DevRelations.DEFINES, DevRelations.CONTAINS)
    by_key = {n["id"]: key for key, ns in held.items() for n in ns}
    for key in keys:
        fold.seed_module(key, held[key], [e for e in held_edges if e["relation_type"] in local
                                          and by_key.get(e["source_id"]) == key])
    before_nodes = {nid: dict(r) for nid, r in fold.nodes.items()}
    before_edges = set(fold.edges)

    for rec in appended:
        fold.apply(rec)
    fold.settle()

    # The node delta (every node the fold holds belongs to a touched module).
    removed = sorted(set(before_nodes) - set(fold.nodes))
    write: List[Dict[str, Any]] = []
    fresh: List[str] = []
    for nid, r in fold.nodes.items():
        b = before_nodes.get(nid)
        if b is None:
            fresh.append(nid)
        elif b["wire"] == r["wire"] and b["updated_at"] == r["updated_at"]:
            continue
        else:
            receipt["nodes_updated"] += 1
        write.append({**r["wire"], "created_at": r["created_at"], "updated_at": r["updated_at"]})
    if fresh:
        present = await F.load_nodes(gx, fresh)
        receipt["collisions"] = sorted(present)  # a fresh id the db already held: overwritten, reported
    receipt["nodes_added"] = len(fresh)
    receipt["nodes_removed"] = len(removed)

    # The edge delta: what the fold's step changed, plus the touched nodes' own derived edges
    # reconciled against the db (drift from before this step heals on touch, reported).
    touched = set(before_nodes) | set(fold.nodes)
    desired = {eid for eid, r in fold.edges.items() if r["wire"]["source_id"] in touched}
    actual = {e["id"] for e in held_edges}
    gone = (before_edges - set(fold.edges)) | (actual - desired)
    new = (set(fold.edges) - before_edges) | (desired - actual)
    receipt["healed_edges"] = len((actual - desired) - before_edges) + len(
        (desired - actual) - (set(fold.edges) - before_edges))
    ts = appended[-1].get("ts")
    add_edges = [{**fold.edges[eid]["wire"],
                  "created_at": fold.edges[eid]["created_at"] or ts,
                  "updated_at": fold.edges[eid]["updated_at"] or ts} for eid in sorted(new)]
    receipt["edges_added"], receipt["edges_removed"] = len(add_edges), len(gone)

    if gone:
        await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=sorted(gone))
    if removed:
        await graph_task(gx.queue, gx.graph_id, "delete_nodes", node_ids=removed, cascade=True)
    if write or add_edges:
        res = await graph_task(gx.queue, gx.graph_id, "import_graph",
                               graph_data={"nodes": write, "edges": add_edges, "metadata": {}},
                               merge_strategy="overwrite")
        # An edge onto an absent endpoint (a module's ABOUT before its repo Entity exists) is
        # skipped by the store, exactly as a rebuild skips it — counted, never claimed.
        receipt["edges_skipped"] = len(add_edges) - int((res or {}).get("edges_created", 0))
    receipt["modules"] = [f"{k[0]}/{k[1]}" for k in keys]
    receipt["failures"] = fold.failures
    return receipt
