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

from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.provenance import SourceRef
from cjm_dev_graph_schema.identity import code_module_node_id
from cjm_dev_graph_schema.nodes import CodeModuleNode
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations
from cjm_python_decompose_core.emit import emit_module_from_nodes
from cjm_python_decompose_core.extract import decompose_text
from cjm_python_decompose_core.ingest import corpus_graph_elements

from . import factlayer as F
from .authoring import _module_node, _module_region_wires, _notebook_cell_wires
from .journal import append_write, read_journal
from .refactor_ops import _get, _relocate
from .runtime import GraphHandle
from .source_state import (append_retire, append_source, canonical_emit, cutover_module,
                           graph_sourced_modules, is_test_module_path, latest_source_ops,
                           notebook_to_py_source)
from .write import link


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
    repo_root: Optional[str] = None,    # Absolute repo root — anchors the FIRST module of a fresh repo (no sibling to derive from)
    write: bool = True,       # Add the node to the graph (else dry run)
) -> Dict[str, Any]:  # The new-module result (or error)
    """Mint an empty CodeModule node (the target a `regroup`/`move` populates).

    Node-only: no `.py` is written until the first symbol lands (a module is EMITTED from
    its regions, and an empty module has none). The node is added so a same-session
    relocation can target it by id; the next `ingest` re-derives it from whatever file the
    relocation writes (or drops it harmlessly if it stays empty). A repo with no modules
    yet (born-on-graph greenfield) has no sibling to derive the on-disk root from — pass
    `repo_root` explicitly there; an existing sibling-derived root always wins."""
    mid = code_module_node_id(repo_key, module_path)
    if await _get(gx, mid) is not None:
        return {"error": f"module `{module_path}` already exists in {repo_key}",
                "module_id": mid, "written": False}
    root = await _repo_root(gx, repo_key)
    if root is None and repo_root:
        root = repo_root if repo_root.endswith("/") else repo_root + "/"
    if root is None:
        return {"error": f"no existing module in repo `{repo_key}` to anchor the path "
                         "— pass --repo-root for a fresh repo's first module",
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
                                  module_node=M, derive_imports=not is_test_module_path(old_mp))
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


def _import_clauses(text: str) -> set:
    """The top-level import CLAUSES a module carries (the prune-report universe).

    Statement-clause granularity, not bound-name granularity: `import urllib.request`
    and `import urllib.error` both bind the name `urllib`, so a name-keyed universe
    cannot see one of them being dropped (the flip-time import-dedupe blind spot) —
    per-clause identity makes every dropped import statement REPORTABLE."""
    clauses = set()
    for node in ast.parse(text).body:
        if isinstance(node, ast.Import):
            for a in node.names:
                clauses.add(f"{a.name} as {a.asname}" if a.asname else a.name)
        elif isinstance(node, ast.ImportFrom):
            mod = "." * (node.level or 0) + (node.module or "")
            for a in node.names:
                leaf = f"{a.name} as {a.asname}" if a.asname else a.name
                sep = "" if mod.endswith(".") else "."
                clauses.add(f"{mod}{sep}{leaf}" if mod else leaf)
    return clauses


def _cell_id_refs(
    ops: List[Dict[str, Any]],  # journaled write ops
    cell_ids: set,              # the flipping module's Cell node ids
) -> List[Dict[str, Any]]:  # one entry per (op, path) referencing a Cell id
    """Deep-scan journaled op args for references to the retiring Cell ids.

    ALL verbs, ALL arg positions (not just link endpoints) — an assert subject or a
    decide-supports entry pointing at a Cell would orphan just as silently."""
    hits: List[Dict[str, Any]] = []

    def walk(x: Any, path: str, op_index: int, verb: str):
        if isinstance(x, str) and x in cell_ids:
            hits.append({"op_index": op_index, "verb": verb, "arg_path": path, "cell_id": x})
        elif isinstance(x, dict):
            for k, v in x.items():
                walk(v, f"{path}.{k}" if path else k, op_index, verb)
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, f"{path}[{i}]", op_index, verb)

    for i, op in enumerate(ops):
        walk(op.get("args", {}), "", i, op.get("verb", ""))
    return hits


async def flip_notebook_to_py(
    gx: GraphHandle,
    source_journal_path: str,   # The source journal (both the old .ipynb and new .py keys)
    writes_journal_path: str,   # The write journal (scanned for Cell-id refs; re-target ops land here)
    repos_dir: str,             # The repos root
    repo_key: str,              # The repo's durable conceptual slug
    notebook_path: str,         # Repo-relative .ipynb path (the retiring source-journal key)
    *,
    docstring: Optional[str] = None,       # Module docstring (the prose-triage fold), verbatim
    force_drop_cell_refs: bool = False,    # Proceed past un-retargetable Cell-id refs (they orphan on rebuild)
    write: bool = True,                    # Execute (else dry-run preview, nothing touched)
) -> Dict[str, Any]:  # The flip report (or the guard that refused it)
    """The golden-reference flip, ONE LOUD VERB (DEC b2c5363d): notebook -> plain `.py`.

    Per-module endpoint pass: build the arc-lib-shaped module from the journaled
    notebook's EXPORT cells (`notebook_to_py_source` — `#|` directives stripped,
    `__all__` dropped, non-kept cells reported), canonicalize, then in one pass:

    - journal the `.py` state + cut it over (the new key is GRAPH-SOURCED from birth),
    - RETIRE the `.ipynb` key (source-check stops holding the deleted file),
    - write the `.py` artifact, delete the notebook file,
    - swap the graph subtree (Cell nodes out, plain-module decomposition in — the
      CodeModule/CodeSymbol ids are IDENTICAL before/after: module identity is the
      export-target path, so decision SHAPES edges onto symbols survive untouched),
    - EDGE CONTINUITY for the Cell ids that do vanish: journaled write ops referencing
      them are deep-scanned; a `link` endpoint whose cell content survives as exactly
      one CodeSymbol is re-linked to it (live + journaled); anything else REFUSES the
      flip unless `force_drop_cell_refs` (loud, never silent — the b2c5363d rule).

    Packaged as one verb because the steps that can individually skip are the bug
    class (the journaling-gap lesson): a flip that journals but doesn't retire leaves
    source-check red forever; one that retires but doesn't re-link orphans provenance."""
    latest = latest_source_ops(source_journal_path)
    sourced = graph_sourced_modules(source_journal_path)
    a = latest.get((repo_key, notebook_path))
    if a is None:
        return {"error": f"no live journaled source state for {repo_key}/{notebook_path} "
                         "(never flipped into the journal, or already retired)", "written": False}
    if (repo_key, notebook_path) not in sourced:
        return {"error": f"{repo_key}/{notebook_path} is not GRAPH-SOURCED — this verb flips "
                         "post-cutover notebooks; run flip-module + cutover first", "written": False}

    try:
        built = notebook_to_py_source(a.get("text", ""), docstring=docstring)
    except (SyntaxError, ValueError) as e:
        return {"error": f"cannot build .py from {notebook_path}: {e}", "written": False}
    if not built["default_exp"]:
        return {"error": f"{notebook_path} exports nothing (`#| default_exp` absent) — "
                         "a non-exporting notebook is a retirement-time disposition, not a flip",
                "written": False}
    if not built["text"].strip():
        return {"error": f"{notebook_path} has no export-cell content to flip", "written": False}

    module_path = (repo_key.replace("-", "_") + "/"
                   + built["default_exp"].replace(".", "/") + ".py")
    if (repo_key, module_path) in sourced:
        return {"error": f"{repo_key}/{module_path} is already graph-sourced — re-flip the "
                         "notebook key only after retiring the .py (not a supported walk)",
                "written": False}
    import_name = _derive_import_name(module_path)
    file_path = str(Path(repos_dir) / repo_key / module_path)
    nb_file = Path(repos_dir) / repo_key / notebook_path
    try:
        canonical = canonical_emit(repo_key, module_path, file_path, built["text"],
                                   import_name=import_name)
    except (SyntaxError, ValueError) as e:
        return {"error": f"canonical emit failed for the built module: {e}", "written": False}
    pruned = sorted(_import_clauses(built["text"]) - _import_clauses(canonical))

    # Edge continuity: what journaled knowledge points at the Cells about to vanish?
    module_id = code_module_node_id(repo_key, module_path)
    cells = await _notebook_cell_wires(gx, module_id)
    cell_ids = {w["id"] for w in cells}
    syms_by_cellkey: Dict[str, List[Dict[str, Any]]] = {}
    for w in await _module_region_wires(gx, module_id):
        ck = w["properties"].get("cell_key")
        if w["label"] == DevNodeKinds.CODE_SYMBOL and ck:
            syms_by_cellkey.setdefault(str(ck), []).append(w)
    cellkey_by_id = {w["id"]: str(w["properties"].get("cell_key", "")) for w in cells}

    ops = read_journal(writes_journal_path)
    refs = _cell_id_refs(ops, cell_ids)
    retargets: List[Dict[str, Any]] = []
    blockers: List[Dict[str, Any]] = []
    for r in refs:
        op = ops[r["op_index"]]
        survivors = syms_by_cellkey.get(cellkey_by_id.get(r["cell_id"], ""), [])
        if (op.get("verb") == "link" and r["arg_path"] in ("source_id", "target_id")
                and len(survivors) == 1):
            oa = op.get("args", {})
            new_id = survivors[0]["id"]
            retargets.append({"relation": oa.get("relation"),
                              "source_id": new_id if r["arg_path"] == "source_id"
                                           else oa.get("source_id"),
                              "target_id": new_id if r["arg_path"] == "target_id"
                                           else oa.get("target_id"),
                              "actor": oa.get("actor", "agent:session"),
                              "replaces_cell": r["cell_id"],
                              "surviving_symbol": survivors[0]["properties"].get("qualname")})
        else:
            blockers.append({**r, "surviving_symbols":
                             [s["properties"].get("qualname") for s in survivors]})
    if blockers and not force_drop_cell_refs:
        return {"error": f"{len(blockers)} journaled write op(s) reference Cell ids this flip "
                         "would orphan and no unambiguous symbol re-target exists — re-home "
                         "them first, or pass force_drop_cell_refs to drop them LOUDLY",
                "cell_ref_blockers": blockers, "written": False}

    result = {"repo_key": repo_key, "notebook_path": notebook_path,
              "module_path": module_path, "import_name": import_name,
              "file_path": file_path, "module_id": module_id,
              "export_cells": built["export_cells"],
              "markdown_cells_dropped": built["markdown_cells"],
              "nonexport_code_cells_dropped": built["nonexport_code_cells"],
              "dropped_all_dunder": built["dropped_all_dunder"],
              "pruned_imports": pruned,
              "canonical_bytes": len(canonical.encode("utf-8")),
              "cell_refs_retargeted": retargets, "cell_refs_dropped": blockers,
              "written": False}
    if not write:
        result["note"] = "dry run — nothing journaled, written, or deleted"
        return result

    append_source(source_journal_path, repo_key, module_path, import_name, canonical)
    Path(file_path).parent.mkdir(parents=True, exist_ok=True)
    Path(file_path).write_text(canonical)
    co = cutover_module(source_journal_path, repos_dir, repo_key, module_path)
    if not co.get("cut_over"):
        return {"error": f"cutover refused after journaling the .py state: "
                         f"{co.get('error', co)} — the flip is INCOMPLETE (shadow state "
                         "journaled, artifact written; notebook untouched)", **result}
    append_retire(source_journal_path, repo_key, notebook_path, superseded_by=module_path)
    notebook_deleted = False
    if nb_file.exists():
        nb_file.unlink()
        notebook_deleted = True

    # Subtree swap: Cells (and the notebook-shaped module node) out, plain decomposition in.
    old_ids = await _module_subtree_ids(gx, module_id)
    await graph_task(gx.queue, gx.graph_id, "delete_nodes", node_ids=old_ids, cascade=True)
    dm = decompose_text(repo_key, module_path, file_path, canonical, import_name=import_name)
    nodes, edges = corpus_graph_elements([dm])
    res = await extend_graph(gx.queue, gx.graph_id, nodes, edges)

    # Re-link AFTER the swap — the cascade delete just severed every live edge on the
    # module's nodes, including ones whose endpoints SURVIVE under identical ids (a
    # note's REFERENCES onto a symbol). Rebuild replay would heal them, but the live
    # graph must not silently lose curation edges between rebuilds.
    seen_edges = set()
    for rt in retargets:
        rres = await link(gx, rt["source_id"], rt["target_id"], rt["relation"],
                          actor=rt["actor"])
        if rres.get("error"):
            return {"error": f"re-target link failed ({rt['relation']} -> "
                             f"{rt['surviving_symbol']}): {rres['error']}", **result}
        seen_edges.add((rres["source_id"], rt["relation"], rres["target_id"]))
        append_write(writes_journal_path, "link",
                     {"source_id": rres["source_id"], "target_id": rres["target_id"],
                      "relation": rt["relation"], "actor": rt["actor"],
                      "source_label": rres.get("source_label"),
                      "target_label": rres.get("target_label")})
    new_ids = {n["id"] for n in nodes}
    replayed = 0
    for op in read_journal(writes_journal_path):
        if op.get("verb") != "link":
            continue
        oa = op.get("args", {})
        key = (oa.get("source_id"), oa.get("relation"), oa.get("target_id"))
        if key in seen_edges or not (set(key[::2]) & new_ids):
            continue
        seen_edges.add(key)
        rres = await link(gx, oa["source_id"], oa["target_id"], oa["relation"],
                          actor=oa.get("actor", "agent:session"))
        if not rres.get("error"):  # a dead endpoint (e.g. a dropped Cell) is already reported
            replayed += 1

    result.update({"written": True, "cut_over": True, "retired": True,
                   "notebook_deleted": notebook_deleted,
                   "graph": {"dropped_nodes": len(old_ids), "added_nodes": res.nodes_added,
                             "added_edges": res.edges_added,
                             "curation_links_replayed": replayed},
                   "note": "GRAPH-SOURCED as plain .py; the .ipynb key is retired and the "
                           "notebook file deleted; cross-module CALLS/IMPORTS re-derive on "
                           "the next rebuild"})
    return result
