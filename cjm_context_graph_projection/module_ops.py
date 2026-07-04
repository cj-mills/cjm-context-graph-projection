"""Module-edit ops — create / rename / delete / regroup a module as graph edge ops.

The EXECUTE half of the cohesion oracle ([[true-b-projected-structure-discussion]] N+1):
`cohesion` proposes organizational changes (a grab-bag to split, a scattered helper, a
dead module); these verbs carry them out. They ride the `move` machinery (the shared
`_relocate` engine + imports-as-projection) so an under_split finding becomes `regroup`
(extract symbols into a new module), an over_split/dead finding becomes `move` + `delete`.

Persistence is Fork-1(a) (the file stays the source; `ingest` re-derives) — the same line
`move`/`author` hold until the deliberate N+3 flip. All four ops are STRUCTURAL/import-level
(safe at the verbatim-body line): they never rewrite a body. Symbol `rename` — which would
push scoped identifier substitution INTO bodies (Ext-B) — is its own deliberate increment.

The graph-mutation posture, per op (mirrors `move`'s "drive files from graph knowledge,
let `ingest` re-derive", deviating only where a footgun forces it):
- `new_module` ADDS the node (so a same-session `regroup`/`move` can target it; additive,
  no id cascade).
- `delete_module` / `rename_module` DROP the old module's graph subtree (a CodeModule id
  embeds `module_path`, so a rename changes the id of the module AND every symbol — the
  heavy cascade `move` defers to re-ingest; but a stale node whose file is gone would let a
  later `emit` resurrect the deleted file, so the old subtree is removed). The NEW module is
  re-derived by the next `ingest` — `rename_module` reports that.
"""

import ast
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cjm_context_graph_layer.ops import graph_task
from cjm_context_graph_primitives.provenance import SourceRef
from cjm_dev_graph_schema.identity import code_module_node_id
from cjm_dev_graph_schema.nodes import CodeModuleNode
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations
from cjm_python_decompose_core.emit import emit_module_from_nodes

from . import factlayer as F
from .authoring import _module_node, _module_region_wires, _notebook_cell_wires
from .refactor_ops import _get, _relocate
from .runtime import GraphHandle


def _derive_import_name(module_path: str) -> str:  # repo-relative path -> dotted import name
    """Derive a module's dotted import name from its repo-relative path
    ("pkg/sub.py" -> "pkg.sub"). Overridable where the package layout doesn't map directly."""
    stem = module_path[:-3] if module_path.endswith(".py") else module_path
    return stem.replace("/", ".")


def rewrite_module_import(
    text: str,        # The importer module's full source text
    old_module: str,  # The renamed module's old dotted import name
    new_module: str,  # Its new dotted import name
) -> Tuple[str, bool]:  # (rewritten text, changed?)
    """Rewrite a module-RENAME across an importer: every `from old import …` and
    `import old [as x]` for the exact module name is re-pointed at `new` (names + aliases
    preserved). AST-located (single-line + parenthesized forms). Exact-match only — a
    submodule like `from old.sub import …` is left alone (v1; reported nowhere, a known gap)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text, False
    lines = text.splitlines(keepends=True)
    edits: List[Tuple[int, int, List[str]]] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == old_module and node.level == 0:
            names = ", ".join(f"{a.name} as {a.asname}" if a.asname else a.name for a in node.names)
            edits.append((node.lineno - 1, node.end_lineno, [f"from {new_module} import {names}\n"]))
        elif isinstance(node, ast.Import):
            hit = False
            rendered: List[str] = []
            for a in node.names:
                name = new_module if a.name == old_module else a.name
                hit = hit or a.name == old_module
                rendered.append(f"{name} as {a.asname}" if a.asname else name)
            if hit:
                edits.append((node.lineno - 1, node.end_lineno, [f"import {', '.join(rendered)}\n"]))
    if not edits:
        return text, False
    for start, end, repl in sorted(edits, reverse=True):
        lines[start:end] = repl
    return "".join(lines), True


async def _repo_root(gx: GraphHandle, repo_key: str) -> Optional[str]:
    """The repo's filesystem root (the prefix `path` minus `module_path`), read off an
    existing module of the same repo — so a new/renamed module's absolute path is anchored
    to the real checkout without the caller having to supply it."""
    for m in await F.load_label(gx, DevNodeKinds.CODE_MODULE):
        if F.prop(m, "repo_key") == repo_key:
            path, mp = F.prop(m, "path"), F.prop(m, "module_path")
            if path and mp and path.endswith(mp):
                return path[: -len(mp)]
    return None


async def _module_subtree_ids(gx: GraphHandle, module_id: str) -> List[str]:
    """A module + every node homed in it (top-level + nested symbols, code-text regions,
    notebook cells) — the ids to drop so a rename/delete leaves no stale, resurrectable node."""
    ids = [module_id]
    for label in (DevNodeKinds.CODE_SYMBOL, DevNodeKinds.CODE_TEXT, DevNodeKinds.CELL):
        for n in await F.load_label(gx, label):
            if F.prop(n, "module_id") == module_id:
                ids.append(F.nid(n))
    return ids


async def new_module(
    gx: GraphHandle,
    repo_key: str,            # The repo's durable conceptual slug (the rename-stable Entity key)
    module_path: str,         # Repo-relative path of the new module (e.g. "pkg/sub.py")
    *,
    import_name: Optional[str] = None,  # Dotted import name (derived from module_path if omitted)
    write: bool = True,       # Add the node to the graph (else dry run)
) -> Dict[str, Any]:  # The new-module result (or error)
    """Mint an empty CodeModule node (the target a `regroup`/`move` populates).

    Node-only: no `.py` is written until the first symbol lands (a module is EMITTED from
    its regions, and an empty module has none). The node is added so a same-session
    relocation can target it by id; the next `ingest` re-derives it from whatever file the
    relocation writes (or drops it harmlessly if it stays empty)."""
    mid = code_module_node_id(repo_key, module_path)
    if await _get(gx, mid) is not None:
        return {"error": f"module `{module_path}` already exists in {repo_key}",
                "module_id": mid, "written": False}
    root = await _repo_root(gx, repo_key)
    if root is None:
        return {"error": f"no existing module in repo `{repo_key}` to anchor the path",
                "written": False}
    imp = import_name or _derive_import_name(module_path)
    node = CodeModuleNode(repo_key=repo_key, module_path=module_path, path=root + module_path,
                          content_hash=SourceRef.compute_hash(b""), import_name=imp)
    result = {"module_id": mid, "repo_key": repo_key, "module_path": module_path,
              "import_name": imp, "path": root + module_path, "written": False,
              "note": "node only — the .py file is emitted when the first symbol is moved in"}
    if write:
        await graph_task(gx.queue, gx.graph_id, "add_nodes", nodes=[node.to_graph_node()])
        await graph_task(gx.queue, gx.graph_id, "add_edges", edges=[node.about_edge()])
        result["written"] = True
    return result


async def regroup(
    gx: GraphHandle,
    repo_key: str,                  # The repo the symbols + target live in (same-repo, v1)
    target_module_path: str,        # Repo-relative path of the module to gather them into (created if absent)
    symbol_ids: List[str],          # The top-level CodeSymbols to relocate
    *,
    import_name: Optional[str] = None,  # Target's dotted import name (derived if omitted)
    write: bool = True,             # Execute (else dry-run preview)
) -> Dict[str, Any]:  # The regroup result (created_target + the relocation outcome, or error)
    """Gather symbols into a module — the EXECUTE verb for an `under_split` (extract a
    grab-bag into a cohesive module) or `over_split` (consolidate a scattered helper)
    finding. Creates the target module if it doesn't exist, then batch-relocates every
    symbol in ONE emit pass via `_relocate` (re-emitting each affected source module + the
    target + the importers, imports re-derived). On dry-run into a NOT-yet-existing target,
    the target node is synthesized in-memory so the file preview is still computed without
    mutating the graph."""
    target_id = code_module_node_id(repo_key, target_module_path)
    existing = await _get(gx, target_id)
    target_node: Any = existing
    created = False
    if existing is None:
        root = await _repo_root(gx, repo_key)
        if root is None:
            return {"error": f"no existing module in repo `{repo_key}` to anchor the path",
                    "written": False}
        imp = import_name or _derive_import_name(target_module_path)
        node = CodeModuleNode(repo_key=repo_key, module_path=target_module_path,
                              path=root + target_module_path,
                              content_hash=SourceRef.compute_hash(b""), import_name=imp)
        target_node = node.to_graph_node()  # synthesized; used as B even on dry run
        created = True
        if write:
            await graph_task(gx.queue, gx.graph_id, "add_nodes", nodes=[target_node])
            await graph_task(gx.queue, gx.graph_id, "add_edges", edges=[node.about_edge()])
    res = await _relocate(gx, symbol_ids, target_id, write=write, target_node=target_node)
    res["created_target"] = created
    res["target_module"] = import_name or _derive_import_name(target_module_path)
    return res


async def rename_module(
    gx: GraphHandle,
    module_id: str,                 # The CodeModule to rename
    new_module_path: str,           # Its new repo-relative path
    *,
    new_import_name: Optional[str] = None,  # New dotted import name (derived if omitted)
    write: bool = True,             # Execute (else dry-run preview)
) -> Dict[str, Any]:  # The rename result (importer rewrites, files, or error)
    """Rename a `.py` module — re-emit its content at the new path, drop the old file, and
    rewrite every importer's `from old import …` / `import old` to the new name. Purely
    import-level (no body touched). The graph's old subtree is dropped (the id embeds the
    path, so the rename changes every contained symbol's id — the cascade `move` defers to
    re-ingest); the renamed module is re-derived on the next `ingest`."""
    M = await _module_node(gx, module_id)
    if M is None:
        return {"error": f"no module `{module_id}`", "written": False}
    if await _notebook_cell_wires(gx, module_id):
        return {"error": "v1 renames .py modules only (this module has notebook cells)",
                "written": False}
    repo_key = F.prop(M, "repo_key")
    old_path, old_mp = F.prop(M, "path"), F.prop(M, "module_path")
    old_import = F.prop(M, "import_name", "")
    new_id = code_module_node_id(repo_key, new_module_path)
    if new_id == module_id:
        return {"error": "new module_path is the same as the current one", "written": False}
    if await _get(gx, new_id) is not None:
        return {"error": f"target module `{new_module_path}` already exists", "written": False}
    if not (old_path and old_mp and old_path.endswith(old_mp)):
        return {"error": "cannot resolve the repo root from the module path", "written": False}
    root = old_path[: -len(old_mp)]
    new_path = root + new_module_path
    new_import = new_import_name or _derive_import_name(new_module_path)

    text = emit_module_from_nodes(await _module_region_wires(gx, module_id),
                                  module_node=M, derive_imports=True)
    files: List[Tuple[str, str]] = [(new_path, text)]
    import_pairs = await F.load_edge_pairs(gx, DevRelations.IMPORTS)
    importers = [s for s, t in import_pairs if t == module_id and s != module_id]
    caller_hits: List[str] = []
    for mid in dict.fromkeys(importers):
        m = await _module_node(gx, mid)
        itext = emit_module_from_nodes(await _module_region_wires(gx, mid))
        new_itext, changed = rewrite_module_import(itext, old_import, new_import)
        if changed:
            files.append((F.prop(m, "path"), new_itext))
            caller_hits.append(F.prop(m, "import_name", mid))

    result = {"from_module": old_import, "to_module": new_import,
              "from_path": old_mp, "to_path": new_module_path,
              "caller_imports_rewritten": sorted(dict.fromkeys(caller_hits)),
              "files": [f for f, _ in files], "written": False,
              "note": "graph subtree dropped; re-ingest to re-derive the renamed module"}
    if write:
        for path, content in files:
            if path:
                Path(path).write_text(content)
        if old_path and Path(old_path) != Path(new_path) and Path(old_path).exists():
            Path(old_path).unlink()
        ids = await _module_subtree_ids(gx, module_id)
        await graph_task(gx.queue, gx.graph_id, "delete_nodes", node_ids=ids, cascade=True)
        result["written"] = True
    return result


async def delete_module(
    gx: GraphHandle,
    module_id: str,        # The CodeModule to delete
    *,
    force: bool = False,   # Delete even if it still defines top-level symbols (a confirmed-dead module)
    write: bool = True,    # Execute (else dry-run preview)
) -> Dict[str, Any]:  # The delete result (or error/guard)
    """Delete a module — drop its file and its whole graph subtree. Guarded: refuses while
    it still DEFINES top-level symbols (move them out first), unless `force` (a confirmed-dead
    module). Cleans the graph (vs. deferring to re-ingest) precisely so a later `emit` can't
    resurrect the just-deleted file from a lingering node."""
    M = await _module_node(gx, module_id)
    if M is None:
        return {"error": f"no module `{module_id}`", "written": False}
    region_wires = await _module_region_wires(gx, module_id)
    top_syms = [w for w in region_wires
                if w["label"] == DevNodeKinds.CODE_SYMBOL and w["properties"].get("body")]
    if top_syms and not force:
        return {"error": f"module still defines {len(top_syms)} top-level symbol(s); "
                         f"move them out first or pass force",
                "symbols": [w["properties"].get("qualname", "") for w in top_syms],
                "written": False}
    path = F.prop(M, "path")
    ids = await _module_subtree_ids(gx, module_id)
    result = {"module_id": module_id, "import_name": F.prop(M, "import_name", ""),
              "path": path, "node_count": len(ids), "forced": force, "written": False}
    if write:
        if path and Path(path).exists():
            Path(path).unlink()
        await graph_task(gx.queue, gx.graph_id, "delete_nodes", node_ids=ids, cascade=True)
        result["written"] = True
    return result
