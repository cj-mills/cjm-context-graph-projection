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
from cjm_python_decompose_core.emit import emit_module_from_nodes

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


async def move(
    gx: GraphHandle,
    symbol_id: str,         # The top-level CodeSymbol to relocate
    target_module_id: str,  # The CodeModule to move it into (same repo, v1)
    *,
    write: bool = True,     # Write the affected files to disk (Fork-1(a)); False = dry run
) -> Dict[str, Any]:  # The move result (files, caller rewrites, diagnostic, or error)
    """Relocate a top-level symbol from its module to another, graph-driven.

    Re-emits the source module (without S) + the target module (with S appended), and
    rewrites each importing module's `from A import S` to point at B. Reports the residual
    import work (B's cross-module needs, A's internal use of S) as a diagnostic."""
    node = await _get(gx, symbol_id)
    if node is None:
        return {"error": f"no node `{symbol_id}`", "written": False}
    p = F.props(node)
    if not p.get("body") or p.get("order_index") is None:
        return {"error": "only a TOP-LEVEL symbol (with a verbatim body) can be moved", "written": False}
    src_module_id = p.get("module_id")
    if src_module_id == target_module_id:
        return {"error": "symbol is already in the target module", "written": False}
    A = await _module_node(gx, src_module_id)
    B = await _module_node(gx, target_module_id)
    if B is None:
        return {"error": f"no target module `{target_module_id}`", "written": False}
    if F.prop(A, "repo_key") != F.prop(B, "repo_key"):
        return {"error": "v1 moves are within ONE repo", "written": False}
    qual = p.get("qualname", "")
    a_import, b_import = F.prop(A, "import_name", ""), F.prop(B, "import_name", "")

    a_wires = await _module_region_wires(gx, src_module_id)
    b_wires = await _module_region_wires(gx, target_module_id)
    max_order = max((w["properties"].get("order_index", -1) for w in b_wires), default=-1)
    moved = _symbol_wire(node, target_module_id, max_order + 1)

    files: List[Tuple[str, str]] = [
        (F.prop(A, "path"), emit_module_from_nodes([w for w in a_wires if w["id"] != symbol_id])),
        (F.prop(B, "path"), emit_module_from_nodes(b_wires + [moved])),
    ]

    # Callers: modules importing A; rewrite their `from a_import import S`.
    importers = [src for src, tgt in await F.load_edge_pairs(gx, DevRelations.IMPORTS)
                 if tgt == src_module_id and src != src_module_id]
    caller_hits: List[str] = []
    for mid in dict.fromkeys(importers):
        m = await _module_node(gx, mid)
        text = emit_module_from_nodes(await _module_region_wires(gx, mid))
        new_text, changed = rewrite_symbol_import(text, a_import, b_import, qual)
        if changed:
            files.append((F.prop(m, "path"), new_text))
            caller_hits.append(F.prop(m, "import_name", mid))

    # Diagnostic (computed from the graph; the residual the true-B regenerate-step subsumes):
    #   - B may need imports for what S calls in OTHER modules;
    #   - A still uses S internally (it would need `from B import S`).
    calls = await F.load_edge_pairs(gx, DevRelations.CALLS)
    sym_module = await _symbol_module_map(gx)
    b_needs = sorted({sym_module[t][1] for src, t in calls
                      if src == symbol_id and t in sym_module
                      and sym_module[t][0] not in (src_module_id, target_module_id)})
    a_internal_use = any(t == symbol_id and sym_module.get(src, (None,))[0] == src_module_id
                         for src, t in calls)

    result = {
        "symbol": qual, "from_module": a_import, "to_module": b_import,
        "caller_imports_rewritten": caller_hits,
        "diagnostic": {"target_may_need_imports_for": b_needs,
                       "source_still_uses_symbol": a_internal_use},
        "files": [f for f, _ in files], "written": False,
    }
    if write:
        for path, content in files:
            if path:
                Path(path).write_text(content)
        result["written"] = True
    return result


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
