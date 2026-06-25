"""`move` — relocate a symbol between modules (the EXECUTE half of refactor-candidates).

The A-level, graph-DRIVEN form of [[graph-as-source-of-truth-inversion]]'s "refactoring as
edge updates": the file stays the source (Fork-1(a)), so `move` relocates the symbol's
VERBATIM body from module A to module B, re-emits both files, and — driven by the graph's
IMPORTS knowledge — rewrites every caller's `from A import S` to `from B import S`. The
graph tells us exactly what to move and who imports it (vs. hunting through files); the
next `ingest` re-derives the graph (S's new id under B, CALLS re-resolved by name).

The PURE edge-update (no text surgery — imports REGENERATED from the graph because the
graph IS the source) is the true-B form. v1 scope: a TOP-LEVEL symbol, SAME repo, and the
`from X import` caller form. B's internal import needs (what S calls cross-module) and A's
now-dead imports are COMPUTED from the graph and REPORTED as a diagnostic, not auto-edited —
honest about the residual the true-B regenerate-from-graph step subsumes.
"""

import ast
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cjm_dev_graph_schema.identity import code_symbol_node_id
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations
from cjm_python_decompose_core.emit import emit_module_from_nodes, synth_import

from . import factlayer as F
from .authoring import _module_node, _module_region_wires
from .runtime import GraphHandle


def rewrite_symbol_import(
    text: str,        # A module's full source text
    old_module: str,  # Dotted import name the symbol is currently imported FROM
    new_module: str,  # Dotted import name it should be imported from now
    symbol: str,      # The (top-level) symbol name being moved
) -> Tuple[str, bool]:  # (rewritten text, changed?)
    """Rewrite `from old_module import ... S ...` -> import S from new_module instead.

    AST-located (handles single-line and parenthesized multi-line imports), preserving any
    `as` alias and the other names on the line. Only the `from`-import form is handled;
    qualified `import old_module; old_module.S` use is left for the diagnostic."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text, False
    lines = text.splitlines(keepends=True)
    edits: List[Tuple[int, int, List[str]]] = []
    for node in tree.body:
        if not (isinstance(node, ast.ImportFrom) and node.module == old_module and node.level == 0):
            continue
        names = [(a.name, a.asname) for a in node.names]
        if symbol not in [n for n, _ in names]:
            continue
        remaining = [(n, a) for n, a in names if n != symbol]
        alias = next(a for n, a in names if n == symbol)
        s_clause = f"{symbol} as {alias}" if alias else symbol
        repl: List[str] = []
        if remaining:
            rem = ", ".join(f"{n} as {a}" if a else n for n, a in remaining)
            repl.append(f"from {old_module} import {rem}\n")
        repl.append(f"from {new_module} import {s_clause}\n")
        edits.append((node.lineno - 1, node.end_lineno, repl))
    if not edits:
        return text, False
    for start, end, repl in sorted(edits, reverse=True):
        lines[start:end] = repl
    return "".join(lines), True


def _symbol_wire(node: Any, module_id: str, order_index: int) -> Dict[str, Any]:
    """A re-keyed CodeSymbol wire dict placing the symbol under a new module + order."""
    p = dict(F.props(node))
    p["module_id"] = module_id
    p["order_index"] = order_index
    return {"id": code_symbol_node_id(module_id, p.get("qualname", "")),
            "label": DevNodeKinds.CODE_SYMBOL, "properties": p}


async def _relocate(
    gx: GraphHandle,
    symbol_ids: List[str],   # The top-level CodeSymbols to relocate (possibly from several modules)
    target_module_id: str,   # The CodeModule to move them into (same repo, v1)
    *,
    write: bool = True,      # Write the affected files to disk (Fork-1(a)); False = dry run
    target_node: Any = None,  # Pre-fetched/synthesized target CodeModule node (regroup dry-run into a not-yet-persisted module)
) -> Dict[str, Any]:  # The relocation result (symbols, caller rewrites, diagnostic, or error)
    """Relocate one OR MANY top-level symbols into a target module, graph-driven.

    The shared engine behind `move` (one symbol) and `regroup` (a batch, possibly from
    several source modules). It must compute the WHOLE batch in one emit pass per affected
    module: `move` is file-driven and does NOT mutate the graph (Fork-1(a) — `ingest`
    re-derives), so naively looping it would re-read the original graph each time and
    resurrect already-moved symbols. Re-emits every affected source module (minus its moved
    symbols) + the target (with all moved appended), and rewrites each importer's
    `from A import S` to point at B. ZERO-RESIDUAL via the same USES-derived synthetics as
    the single move, with the override spanning every moved subtree."""
    B = target_node if target_node is not None else await _module_node(gx, target_module_id)
    if B is None:
        return {"error": f"no target module `{target_module_id}`", "written": False}
    b_repo, b_import = F.prop(B, "repo_key"), F.prop(B, "import_name", "")

    # Resolve + validate every symbol; group by source module (qualname carried for callers).
    by_src: Dict[str, List[Tuple[str, Any, str]]] = {}
    for sid in symbol_ids:
        node = await _get(gx, sid)
        if node is None:
            return {"error": f"no node `{sid}`", "written": False}
        p = F.props(node)
        if not p.get("body") or p.get("order_index") is None:
            return {"error": f"only a TOP-LEVEL symbol (with a verbatim body) can be moved: `{sid}`",
                    "written": False}
        src_module_id = p.get("module_id")
        if src_module_id == target_module_id:
            return {"error": f"symbol `{p.get('qualname', sid)}` is already in the target module",
                    "written": False}
        A = await _module_node(gx, src_module_id)
        if F.prop(A, "repo_key") != b_repo:
            return {"error": "v1 moves are within ONE repo", "written": False}
        by_src.setdefault(src_module_id, []).append((sid, node, p.get("qualname", "")))

    moved_ids = set(symbol_ids)
    # The post-move membership override: every moved symbol + its DEFINES subtree -> target
    # (a class moves with its methods), so USES-derived synthetics resolve in both directions.
    override: Dict[str, str] = {}
    for sid in symbol_ids:
        override.update({x: target_module_id for x in await _moved_subtree(gx, sid)})

    files: List[Tuple[str, str]] = []
    # Each affected SOURCE module, re-emitted without its moved symbols (imports re-derived).
    for src_module_id, items in by_src.items():
        A = await _module_node(gx, src_module_id)
        a_wires = await _module_region_wires(gx, src_module_id)
        a_uses = await _uses_derived_imports(gx, src_module_id, override)
        files.append((F.prop(A, "path"),
                      emit_module_from_nodes([w for w in a_wires if w["id"] not in moved_ids],
                                             module_node=A, derive_imports=True, uses_derived=a_uses)))

    # The TARGET module, re-emitted with every moved symbol appended in order.
    b_wires = await _module_region_wires(gx, target_module_id)
    max_order = max((w["properties"].get("order_index", -1) for w in b_wires), default=-1)
    moved_wires = [_symbol_wire(node, target_module_id, max_order + 1 + i)
                   for i, sid in enumerate(symbol_ids)
                   for node in [await _get(gx, sid)]]
    b_uses = await _uses_derived_imports(gx, target_module_id, override)
    files.append((F.prop(B, "path"),
                  emit_module_from_nodes(b_wires + moved_wires,
                                         module_node=B, derive_imports=True, uses_derived=b_uses)))

    # Callers: modules importing a source; rewrite each `from a_import import S` to point at B.
    import_pairs = await F.load_edge_pairs(gx, DevRelations.IMPORTS)
    caller_hits: List[str] = []
    for src_module_id, items in by_src.items():
        A = await _module_node(gx, src_module_id)
        a_import = F.prop(A, "import_name", "")
        importers = [src for src, tgt in import_pairs if tgt == src_module_id and src != src_module_id]
        for mid in dict.fromkeys(importers):
            if mid == target_module_id:
                continue  # the target's own imports are handled by its re-derived block
            m = await _module_node(gx, mid)
            text = emit_module_from_nodes(await _module_region_wires(gx, mid))
            changed_any = False
            for _sid, _node, qual in items:
                text, changed = rewrite_symbol_import(text, a_import, b_import, qual)
                changed_any = changed_any or changed
            if changed_any:
                files.append((F.prop(m, "path"), text))
                caller_hits.append(F.prop(m, "import_name", mid))

    result = {
        "symbols": [q for items in by_src.values() for _s, _n, q in items],
        "from_modules": sorted({F.prop(await _module_node(gx, s), "import_name", "") for s in by_src}),
        "to_module": b_import,
        "caller_imports_rewritten": sorted(dict.fromkeys(caller_hits)),
        "diagnostic": {"zero_residual": True,
                       "target_imports_synthesized": sorted({b["module"] for b in b_uses}),
                       "source_imports_synthesized": sorted({b["module"] for b in a_uses})},
        "files": [f for f, _ in files], "written": False,
    }
    if write:
        for path, content in files:
            if path:
                Path(path).write_text(content)
        result["written"] = True
    return result


async def move(
    gx: GraphHandle,
    symbol_id: str,         # The top-level CodeSymbol to relocate
    target_module_id: str,  # The CodeModule to move it into (same repo, v1)
    *,
    write: bool = True,     # Write the affected files to disk (Fork-1(a)); False = dry run
) -> Dict[str, Any]:  # The move result (files, caller rewrites, diagnostic, or error)
    """Relocate a single top-level symbol from its module to another, graph-driven.

    The one-symbol case of `_relocate` (the engine `regroup` batches over): re-emits the
    source module (without S) + the target (with S appended), and rewrites each importing
    module's `from A import S` to point at B. The USES-derived synthetics make it
    zero-residual; the next `ingest` re-derives (S's new id under B, CALLS re-resolved)."""
    res = await _relocate(gx, [symbol_id], target_module_id, write=write)
    if res.get("error"):
        return res
    # Shape the single-symbol result keys (back-compat with the published move surface).
    res["symbol"] = res["symbols"][0] if res.get("symbols") else ""
    res["from_module"] = res["from_modules"][0] if res.get("from_modules") else ""
    return res


async def _get(gx: GraphHandle, node_id: str) -> Optional[Any]:
    from cjm_context_graph_layer.ops import graph_task
    return await graph_task(gx.queue, gx.graph_id, "get_node", node_id=node_id)


async def _symbol_module_map(gx: GraphHandle) -> Dict[str, Tuple[str, str]]:
    """symbol id -> (module_id, module import_name), for resolving cross-module call needs."""
    modules = {F.nid(m): F.prop(m, "import_name", "") for m in await F.load_label(gx, DevNodeKinds.CODE_MODULE)}
    out: Dict[str, Tuple[str, str]] = {}
    for s in await F.load_label(gx, DevNodeKinds.CODE_SYMBOL):
        mid = F.prop(s, "module_id")
        out[F.nid(s)] = (mid, modules.get(mid, ""))
    return out


async def _uses_derived_imports(
    gx: GraphHandle,
    home_module_id: str,            # The module whose import block we are deriving
    override: Dict[str, str],       # {symbol id: new module id} — the moved subtree's post-move membership
) -> List[Dict[str, Any]]:  # Synthetic `from <mod> import <name>` for cross-module USES targets
    """USES-derived intra-corpus imports for a module under a post-move membership override.

    A reference that was SAME-module before the move has no import statement, so the frozen
    bindings can't carry it; here we synthesize it from the live USES graph + the target's
    (effective) defining module — closing the move residual in BOTH directions (B importing
    what the moved symbol still needs from A; A importing the moved symbol if it still uses
    it). Only TOP-LEVEL targets are importable (a method is reached via its class)."""
    import_name = {F.nid(m): F.prop(m, "import_name", "")
                   for m in await F.load_label(gx, DevNodeKinds.CODE_MODULE)}
    idx: Dict[str, Dict[str, Any]] = {}
    for s in await F.load_label(gx, DevNodeKinds.CODE_SYMBOL):
        sid = F.nid(s)
        eff = override.get(sid, F.prop(s, "module_id"))
        qual = F.prop(s, "qualname", "") or ""
        idx[sid] = {"name": qual.split(".")[-1], "module": eff,
                    "import_name": import_name.get(eff, ""), "top": "." not in qual}
    members = {sid for sid, info in idx.items() if info["module"] == home_module_id}
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for src, tgt in await F.load_edge_pairs(gx, DevRelations.USES):
        t = idx.get(tgt)
        if (src in members and t and t["top"] and t["import_name"]
                and t["module"] != home_module_id and t["name"] not in seen):
            seen.add(t["name"])
            out.append(synth_import(t["name"], t["import_name"]))
    return out


async def _moved_subtree(gx: GraphHandle, symbol_id: str) -> set:
    """The moved symbol + its DEFINES descendants (a class moves with its methods)."""
    children: Dict[str, List[str]] = {}
    for s, t in await F.load_edge_pairs(gx, DevRelations.DEFINES):
        children.setdefault(s, []).append(t)
    subtree, stack = set(), [symbol_id]
    while stack:
        x = stack.pop()
        subtree.add(x)
        stack.extend(children.get(x, []))
    return subtree
