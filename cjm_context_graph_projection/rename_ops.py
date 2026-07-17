"""Symbol `rename` — the Ext-B increment: scoped identifier substitution INTO bodies.

The first time the verbatim/derived line of [[true-b-projected-structure-discussion]] moves
INSIDE a verbatim body. Renaming a top-level symbol changes the def/class name site AND
every reference in OTHER bodies (the `USES`/`CALLS` neighbourhood) AND the importers' import
lines. Unlike `move`/module-edit-ops (which are import-level and never touch a body), this
edits bodies — so it is done as a TARGETED, ROUND-TRIP-SAFE transform (Ext-B's decision
rule: derive a layer only where a deterministic trusted generator exists; a scope-aware
rename qualifies), NOT general AST decomposition (the round-trip trap).

`scoped_rename` is the safe generator: a stdlib-`ast`, LEGB-scope-aware substitution that
rewrites ONLY genuine references to the module-global name — by exact node position, so
strings, comments, attribute accesses (`obj.old`), keyword-argument names (`f(old=…)`), and
shadowed locals are left byte-identical. `rename_symbol` orchestrates it over the graph
(defining module + importers), alias-aware, with an `ast.parse` validity gate before any
write. Persistence is Fork-1(a) (the symbol's id changes with its name, so the graph
re-derives on the next `ingest`); v1 scope = a top-level FREE function or class, same-repo,
`from X import` callers (qualified `import X; X.old` use is reported, not rewritten).
"""

import ast
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from cjm_dev_graph_schema.vocab import DevRelations
from cjm_python_decompose_core.emit import emit_module_from_nodes

from . import factlayer as F
from .authoring import _module_node, _module_region_wires
from .refactor_ops import _emission_for, _get
from .runtime import GraphHandle
from .source_state import journaled_emit

_COMP = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _target_names(target: Any) -> List[str]:
    """The Name ids bound by an assignment/for/with target (recursing tuples/lists/stars)."""
    out: List[str] = []
    if isinstance(target, ast.Name):
        out.append(target.id)
    elif isinstance(target, ast.Starred):
        out += _target_names(target.value)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for e in target.elts:
            out += _target_names(e)
    return out


def _scope_bindings(node: Any) -> Tuple[Set[str], Set[str], Set[str]]:
    """(bound, declared-global, declared-nonlocal) names for ONE scope, NOT descending into
    nested function/class/comprehension scopes (their NAME binds here; their body doesn't).

    This is the per-scope half of LEGB resolution: a name bound here (param, assignment
    anywhere in the scope, for/with/except target, import, nested def/class name, walrus)
    shadows the module global; `global` re-exposes it; `nonlocal` ties it to an enclosing
    function."""
    bound: Set[str] = set()
    g: Set[str] = set()
    nl: Set[str] = set()
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        a = node.args
        for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs]:
            bound.add(arg.arg)
        if a.vararg:
            bound.add(a.vararg.arg)
        if a.kwarg:
            bound.add(a.kwarg.arg)
    if isinstance(node, _COMP):
        for gen in node.generators:
            bound.update(_target_names(gen.target))

    def walk(n: Any) -> None:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(n.name)
            return  # nested scope — its name binds here, its body does not
        if isinstance(n, (ast.Lambda, *_COMP)):
            return  # nested scope
        if isinstance(n, ast.Assign):
            for t in n.targets:
                bound.update(_target_names(t))
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign)):
            bound.update(_target_names(n.target))
        elif isinstance(n, ast.NamedExpr):
            bound.update(_target_names(n.target))
        elif isinstance(n, (ast.For, ast.AsyncFor)):
            bound.update(_target_names(n.target))
        elif isinstance(n, (ast.With, ast.AsyncWith)):
            for it in n.items:
                if it.optional_vars:
                    bound.update(_target_names(it.optional_vars))
        elif isinstance(n, ast.ExceptHandler):
            if n.name:
                bound.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                bound.add(al.asname or al.name.split(".")[0])
        elif isinstance(n, ast.Global):
            g.update(n.names)
        elif isinstance(n, ast.Nonlocal):
            nl.update(n.names)
        for c in ast.iter_child_nodes(n):
            walk(c)

    bodies = []
    if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        bodies = node.body
    for b in bodies:
        walk(b)
    return bound, g, nl


def _visit_args_header(args: ast.arguments, scopes: List[Tuple], visit) -> None:
    """Visit a function's default values + parameter annotations — they evaluate in the
    ENCLOSING scope, not the function's own (so a default/annotation referencing the renamed
    symbol resolves correctly)."""
    for d in args.defaults:
        visit(d, scopes)
    for d in args.kw_defaults:
        if d is not None:
            visit(d, scopes)
    for a in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
        if a is not None and a.annotation is not None:
            visit(a.annotation, scopes)


def scoped_rename(
    text: str,  # The module's full source
    old: str,   # The module-global name to rename
    new: str,   # Its new name
) -> Tuple[str, int]:  # (rewritten source, number of identifier occurrences rewritten)
    """Rename references to the module-global `old` -> `new`, scope-aware, by exact position.

    Only genuine references to the MODULE-LEVEL binding are rewritten (the def/class name
    site, self-references, and every free Load/Store of the name); a shadowing local/param/
    comprehension var, an attribute access (`obj.old`), a keyword-argument name (`f(old=…)`),
    and anything inside a string or comment are left BYTE-IDENTICAL. Assumes ASCII identifiers
    (true for the corpus); `ast` col offsets are UTF-8 byte offsets, so splicing is done in
    byte space to stay correct under non-ASCII content elsewhere on a line."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text, 0
    lines = text.splitlines(keepends=True)
    edits: Set[Tuple[int, int, int]] = set()  # (lineno, byte-col, byte-end-col)

    def emit_name(n: ast.Name) -> None:
        edits.add((n.lineno, n.col_offset, n.end_col_offset))

    def emit_defname(node: Any) -> None:
        line = lines[node.lineno - 1]
        m = re.search(r"(?:async\s+def|def|class)\s+(" + re.escape(old) + r")\b", line)
        if m:
            # Regex offsets are char offsets; convert to byte offsets for splicing.
            edits.add((node.lineno, len(line[: m.start(1)].encode("utf-8")),
                       len(line[: m.end(1)].encode("utf-8"))))

    def emit_global_name(node: Any) -> None:
        # `global a, old, b` — the name is a bare string (no Name node). Rewrite within the
        # statement's own column span (so a trailing `# old` comment is never touched).
        line = lines[node.lineno - 1]
        seg = line[node.col_offset: node.end_col_offset]
        for m in re.finditer(r"\b" + re.escape(old) + r"\b", seg):
            s, e = node.col_offset + m.start(), node.col_offset + m.end()
            edits.add((node.lineno, len(line[:s].encode("utf-8")), len(line[:e].encode("utf-8"))))

    def resolve(n: ast.Name, scopes: List[Tuple]) -> None:
        if n.id != old:
            return
        for i in range(len(scopes) - 1, 0, -1):  # innermost -> outermost, excluding module (0)
            kind, bound, g, nl = scopes[i]
            if kind == "class" and i != len(scopes) - 1:
                continue  # a class scope is invisible to nested (method) scopes
            if old in g:
                emit_name(n)
                return  # `global old` -> the module binding
            if old in nl or old in bound:
                return  # nonlocal/local/free -> not the module global; leave it
        emit_name(n)  # reached module scope -> the module global -> rename

    def visit(n: Any, scopes: List[Tuple]) -> None:
        if isinstance(n, ast.Name):
            resolve(n, scopes)
            return
        if isinstance(n, ast.Attribute):
            visit(n.value, scopes)  # rewrite the value, never the `.attr` name
            return
        if isinstance(n, ast.keyword):
            visit(n.value, scopes)  # rewrite the value, never the kwarg `arg` name
            return
        if isinstance(n, ast.Global):
            if old in n.names:
                emit_global_name(n)  # `global old` ties to the module binding -> rename it too
            return
        if isinstance(n, ast.Nonlocal):
            return  # `nonlocal old` refers to an enclosing function var, not the module global
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in n.decorator_list:
                visit(d, scopes)
            _visit_args_header(n.args, scopes, visit)
            if n.returns:
                visit(n.returns, scopes)
            if scopes[-1][0] == "module" and n.name == old:
                emit_defname(n)
            inner = scopes + [("function", *_scope_bindings(n))]
            for s in n.body:
                visit(s, inner)
            return
        if isinstance(n, ast.Lambda):
            _visit_args_header(n.args, scopes, visit)
            visit(n.body, scopes + [("function", *_scope_bindings(n))])
            return
        if isinstance(n, ast.ClassDef):
            for d in n.decorator_list:
                visit(d, scopes)
            for b in n.bases:
                visit(b, scopes)
            for k in n.keywords:
                visit(k, scopes)
            if scopes[-1][0] == "module" and n.name == old:
                emit_defname(n)
            inner = scopes + [("class", *_scope_bindings(n))]
            for s in n.body:
                visit(s, inner)
            return
        if isinstance(n, _COMP):
            inner = scopes + [("function", *_scope_bindings(n))]
            for c in ast.iter_child_nodes(n):
                visit(c, inner)
            return
        for c in ast.iter_child_nodes(n):
            visit(c, scopes)

    module_scope = ("module", *_scope_bindings(tree))
    for s in tree.body:
        visit(s, [module_scope])

    if not edits:
        return text, 0
    by_line: Dict[int, List[Tuple[int, int]]] = {}
    for (ln, col, end) in edits:
        by_line.setdefault(ln, []).append((col, end))
    nb = new.encode("utf-8")
    for ln, spans in by_line.items():
        b = lines[ln - 1].encode("utf-8")
        for col, end in sorted(spans, reverse=True):
            b = b[:col] + nb + b[end:]
        lines[ln - 1] = b.decode("utf-8")
    return "".join(lines), len(edits)


def rewrite_import_for_rename(
    text: str,        # The importer module's full source
    src_module: str,  # The renamed symbol's defining module (dotted import name)
    old: str,         # The symbol's old name
    new: str,         # Its new name
) -> Tuple[str, Optional[str], bool]:  # (rewritten text, local name the symbol is known by, qualified-use?)
    """Re-point an importer's `from src_module import old [as a]` at the new name.

    The IMPORTED name changes (`old` -> `new`); any `as` alias is preserved (so an aliased
    importer needs NO body edits — it refers to the symbol by the alias). Returns the LOCAL
    name the symbol is bound to here (the alias, or `old` if unaliased — the signal for whether
    the caller must also rename body references), and whether the module uses a qualified
    `import src_module` (whose `src_module.old` attribute access this v1 does not rewrite)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text, None, False
    lines = text.splitlines(keepends=True)
    local_name: Optional[str] = None
    has_qualified = False
    edits: List[Tuple[int, int, List[str]]] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == src_module and node.level == 0:
            if old not in [a.name for a in node.names]:
                continue
            local_name = next(a.asname or old for a in node.names if a.name == old)
            names = [f"{(new if a.name == old else a.name)} as {a.asname}" if a.asname
                     else (new if a.name == old else a.name) for a in node.names]
            edits.append((node.lineno - 1, node.end_lineno,
                          [f"from {src_module} import {', '.join(names)}\n"]))
        elif isinstance(node, ast.Import):
            if any(a.name == src_module for a in node.names):
                has_qualified = True
    for start, end, repl in sorted(edits, reverse=True):
        lines[start:end] = repl
    return "".join(lines), local_name, has_qualified


async def rename_symbol(
    gx: GraphHandle,
    symbol_id: str,    # The top-level CodeSymbol to rename
    new_name: str,     # Its new bare name
    *,
    write: bool = True,  # Write the affected files (Fork-1(a)); False = dry run
    source_journal_path: Optional[str] = None,  # The source journal (events land BEFORE files)
) -> Dict[str, Any]:  # The rename result (modules updated, diagnostics, or error)
    """Rename a top-level free function/class everywhere it is referenced, graph-driven.

    Scoped-renames the defining module (def site + internal refs), then each importer:
    re-points the import and — when the symbol is imported unaliased — scoped-renames the
    body references too. An `ast.parse` gate refuses to write if any emitted module would be
    invalid. The symbol's id changes with its name, so the graph re-derives on the next
    `ingest` (Fork-1(a))."""
    node = await _get(gx, symbol_id)
    if node is None:
        return {"error": f"no node `{symbol_id}`", "written": False}
    p = F.props(node)
    if not p.get("body") or p.get("order_index") is None:
        return {"error": "only a TOP-LEVEL symbol (with a verbatim body) can be renamed",
                "written": False}
    qual, kind = p.get("qualname", ""), p.get("symbol_kind", "")
    if "." in qual:
        return {"error": "v1 renames top-level free functions/classes, not methods",
                "written": False}
    if kind not in ("function", "class"):
        return {"error": f"v1 renames functions/classes (not {kind})", "written": False}
    if not new_name.isidentifier():
        return {"error": f"`{new_name}` is not a valid identifier", "written": False}
    if qual == new_name:
        return {"error": "new name equals the current name", "written": False}
    old = qual
    def_module_id = p.get("module_id")
    D = await _module_node(gx, def_module_id)
    d_import = F.prop(D, "import_name", "")

    d_text = emit_module_from_nodes(await _module_region_wires(gx, def_module_id))
    d_new, n_def = scoped_rename(d_text, old, new_name)
    files: List[Tuple[str, str]] = [(F.prop(D, "path"), d_new)]
    emissions: List[Optional[Dict[str, Any]]] = [_emission_for(D, d_new)]
    modules_updated: List[str] = []
    diagnostics: List[str] = []

    import_pairs = await F.load_edge_pairs(gx, DevRelations.IMPORTS)
    importers = [s for s, t in import_pairs if t == def_module_id and s != def_module_id]
    for mid in dict.fromkeys(importers):
        M = await _module_node(gx, mid)
        m_text = emit_module_from_nodes(await _module_region_wires(gx, mid))
        new_text, local_name, has_qualified = rewrite_import_for_rename(m_text, d_import, old, new_name)
        if local_name == old:  # imported unaliased -> body references use `old` too
            new_text, _ = scoped_rename(new_text, old, new_name)
        if new_text != m_text:
            files.append((F.prop(M, "path"), new_text))
            emissions.append(_emission_for(M, new_text))
            modules_updated.append(F.prop(M, "import_name", mid))
        if has_qualified:
            diagnostics.append(f"{F.prop(M, 'import_name', mid)}: qualified "
                               f"`{d_import}.{old}` usage not rewritten (v1)")

    for path, content in files:
        try:
            ast.parse(content)
        except SyntaxError as e:
            return {"error": f"rename would produce invalid Python in {path}: {e}",
                    "written": False}

    result = {"old_name": old, "new_name": new_name, "symbol_kind": kind, "module": d_import,
              "def_site_edits": n_def, "modules_updated": sorted(dict.fromkeys(modules_updated)),
              "diagnostics": diagnostics, "files": [f for f, _ in files], "written": False,
              "note": "the symbol id changes with its name — re-ingest to re-derive the graph"}
    if any(e is None for e in emissions):
        return {**result, "error": "cannot derive a source-journal key for an affected "
                "module (notebook-backed importer?) — refusing to write unjournaled"}
    rec = journaled_emit(source_journal_path, emissions=emissions,
                         op={"op": "rename-symbol", "from": old, "to": new_name},
                         write=write)
    if rec.get("error"):
        return {**result, "error": rec["error"]}
    result["journal"] = rec
    result["written"] = bool(write)
    return result
