"""Live re-derivation of a module's region nodes from its new text (36f649d3).

The refactor verbs (rename-symbol / move / regroup / rename-module) used to be
file-driven: they emitted the files, journaled the source ops and left the graph to
the next `cg-rebuild` — every symbol re-keyed under its new address, the live
projection went stale, and the 889b3025 guard refused further authoring on the touched
modules until the rebuild (~350 s) landed. Container-independent identity lets the
graph be updated IN PLACE instead: re-decompose each affected module's new text under
the journal-derived identity hook (a symbol keeps the id it was born with), then diff
the module's region nodes — drop what vanished, update what stayed, add what is new,
swap the structural DEFINES/CONTAINS edges — and the projection reproduces the file
the moment the verb returns. USES/CALLS overlays stay a rebuild's job (the same
contract as `author`).
"""

from typing import Any, Dict, List, Optional, Tuple

from cjm_context_graph_layer.grammar import make_edge
from cjm_context_graph_layer.ops import graph_task
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations
from cjm_python_decompose_core.extract import decompose_text

from . import factlayer as F
from .authoring import _module_region_wires
from .runtime import GraphHandle
from .seeds import conceptual_key
from .source_state import symbol_identity_map


def _structural_edge_ids(
    module_id: str,                 # The module the regions hang from
    wires: List[Dict[str, Any]],    # Its region wires (top-level + nested symbols, code-text)
) -> set:  # Deterministic ids of the module's structural edges (module DEFINES/CONTAINS region; class DEFINES method)
    """The structural edges a module's regions carry, recomputed from the triples —
    `make_edge` ids are deterministic, so dropping them needs no edge query."""
    ids: set = set()
    by_qual = {w["properties"].get("qualname"): w["id"]
               for w in wires if w["label"] == DevNodeKinds.CODE_SYMBOL}
    for w in wires:
        ids.add(make_edge(module_id, w["id"], DevRelations.CONTAINS)["id"])
        ids.add(make_edge(module_id, w["id"], DevRelations.DEFINES)["id"])
        qual = str(w["properties"].get("qualname") or "")
        if w["label"] == DevNodeKinds.CODE_SYMBOL and "." in qual:
            parent = by_qual.get(qual.rsplit(".", 1)[0])
            if parent:
                ids.add(make_edge(parent, w["id"], DevRelations.DEFINES)["id"])
    return ids


async def relive_modules(
    gx: GraphHandle,
    items: List[Tuple[Any, str]],   # (CodeModule node, its NEW canonical text) per affected module
    *,
    source_journal_path: Optional[str] = None,  # The source journal — the identity map derives from it, so call AFTER the op's events landed
    retired_module_ids: Optional[List[str]] = None,  # Modules whose regions are being re-homed away (a rename-module's old key): their wires count as "before"
) -> Dict[str, Any]:  # {modules, dropped, updated, added, edges_dropped, edges_added}
    """Re-derive several modules' region nodes LIVE in one pass — the batch form a move
    needs: a symbol leaving A and arriving in B is ONE node updated, never dropped-then-added.

    Before = every listed module's current region wires (plus the retired modules');
    after = the decomposition of each new text under the identity hook. Ids in before-only
    are dropped (cascade takes their edges — a code-text region whose lead line changed
    churns, the 4d3279eb class); ids in both are updated in place (props merged: a moved
    symbol's `module_id`/`order_index`/`path`/body and its birth fields land here); ids in
    after-only are added — unless the node already exists elsewhere in the graph, then it
    is updated (a symbol arriving from a module this batch did not list). Structural edges
    are diffed by their deterministic ids the same way; the ABOUT edge is left alone; each
    module node's own props (imports, bindings, hash) are refreshed."""
    ident = (symbol_identity_map(source_journal_path, normalize=conceptual_key)
             if source_journal_path else None)
    before: Dict[str, Dict[str, Any]] = {}
    before_edges: set = set()
    for mid in [F.nid(m) for m, _ in items] + list(retired_module_ids or []):
        wires = await _module_region_wires(gx, mid)
        before.update({w["id"]: w for w in wires})
        before_edges |= _structural_edge_ids(mid, wires)
    after: Dict[str, Dict[str, Any]] = {}
    after_edges: Dict[str, Dict[str, Any]] = {}
    module_props: List[Tuple[str, Dict[str, Any]]] = []
    for module, text in items:
        p = F.props(module)
        repo_key, module_path = str(p.get("repo_key") or ""), str(p.get("module_path") or "")
        dm = decompose_text(repo_key, module_path, str(p.get("path") or ""), text,
                            import_name=p.get("import_name"),
                            symbol_identity=ident.for_module(repo_key, module_path) if ident else None)
        for n in [s.to_graph_node() for s in dm.symbols] + [t.to_graph_node() for t in dm.texts]:
            after[n["id"]] = n
        for e in dm.local_edges:
            if e["relation_type"] != DevRelations.ABOUT:
                after_edges[e["id"]] = e
        module_props.append((F.nid(module), dm.module.to_graph_node()["properties"]))
    drop = sorted(set(before) - set(after))
    stay = [i for i in after if i in before]
    fresh = [i for i in after if i not in before]
    existing = await F.load_nodes(gx, fresh) if fresh else {}
    add = [after[i] for i in fresh if i not in existing]
    update = stay + [i for i in fresh if i in existing]
    edges_drop = sorted(before_edges - set(after_edges))
    edges_add = [e for i, e in after_edges.items() if i not in before_edges]
    if drop:
        await graph_task(gx.queue, gx.graph_id, "delete_nodes", node_ids=drop, cascade=True)
    if edges_drop:
        await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=edges_drop)
    for i in update:
        await graph_task(gx.queue, gx.graph_id, "update_node", node_id=i,
                         properties=after[i]["properties"])
    if add:
        await graph_task(gx.queue, gx.graph_id, "add_nodes", nodes=add)
    if edges_add:
        await graph_task(gx.queue, gx.graph_id, "add_edges", edges=edges_add)
    for mid, props in module_props:
        await graph_task(gx.queue, gx.graph_id, "update_node", node_id=mid, properties=props)
    return {"modules": [mid for mid, _ in module_props], "dropped": drop, "updated": update,
            "added": [n["id"] for n in add], "edges_dropped": len(edges_drop),
            "edges_added": len(edges_add)}


async def relive_module(
    gx: GraphHandle,
    module: Any,   # The CodeModule node
    text: str,     # Its NEW canonical text
    *,
    source_journal_path: Optional[str] = None,  # The source journal (see `relive_modules`)
) -> Dict[str, Any]:  # The `relive_modules` receipt
    """The single-module form of `relive_modules`."""
    return await relive_modules(gx, [(module, text)], source_journal_path=source_journal_path)
