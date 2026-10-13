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

The graph-mutation posture (design amendment 2cc81d3b, build B2 of leg B): every op lands
its source-journal records, then the code fold's step (`relive.apply_live`) applies exactly
those records to the db — a module exists on the graph because the journal holds it, so
`new_module` births an EMPTY module captured (graph-sourced from its first record), `rename_module` re-keys the module while its symbols keep their ids, and
`delete_module`'s retire takes the whole subtree with it.
"""

import ast
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cjm_context_graph_primitives.provenance import SourceRef
from cjm_context_graph_primitives.query import PropertyPredicate
from cjm_dev_graph_schema.identity import code_module_node_id
from cjm_dev_graph_schema.nodes import CodeModuleNode
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations
from cjm_python_decompose_core.emit import emit_module_from_nodes

from . import factlayer as F
from .authoring import _label_of, _module_node, _module_region_wires, _resolve_node
from .codesource import CodeSourceError
from .refactor_ops import _emission_for, _get, _relocate
from .relive import apply_live
from .runtime import GraphHandle
from .seeds import repo_dir_name
from .source_state import (birth_form, canonicalization_report, collect_appends, derive_import_name,
                           is_test_module_path, journaled_emit, notebook_to_py_source)


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
    """A module + every node homed in it (top-level + nested symbols, code-text elements) —
    the ids to drop so a rename/delete leaves no stale, resurrectable node."""
    ids = [module_id]
    for label in (DevNodeKinds.CODE_SYMBOL, DevNodeKinds.CODE_TEXT):
        for n in await F.load_label_where(
                gx, label, [PropertyPredicate("module_id", "eq", module_id)]):
            ids.append(F.nid(n))
    return ids


async def add_module(
    gx: GraphHandle,
    repo_key: str,            # The repo's durable conceptual slug (the rename-stable Entity key)
    module_path: str,         # Repo-relative path of the new module (e.g. "pkg/sub.py", "tests/test_x.py")
    body: str,                # The module's whole text ("" = an empty module, the new-module birth)
    *,
    import_name: Optional[str] = None,  # Dotted import name (derived from module_path if omitted)
    repo_root: Optional[str] = None,    # Absolute repo root — anchors the FIRST module of a fresh repo (no sibling to derive from)
    write: bool = True,       # Journal + write + derive (else dry run)
    source_journal_path: Optional[str] = None,  # The source journal (the module's capture group and the repo REGISTER land here)
    repos_dir: Optional[str] = None,  # The repos root (the live fold step derives node paths under it)
    allow_reorder: bool = False,      # Birth the canonical form even when it carries a hazard
) -> Dict[str, Any]:  # The add-module result (or error)
    """Birth a module WHOLE in ONE journaled op, BORN CAPTURED (work item 177dcb07, the ruling
    bef8e6b8): the body's elements are journaled as the module's capture group (with the repo's
    REGISTER), the file is written as their composition, and the code fold's step derives the
    CodeModule, every element and symbol, and their edges — no add-text / add-symbol per region,
    no flip-module after. Nothing exists before the op, so nothing re-keys: a module the graph
    already holds, or a path with a file already on disk (a file-born module is captured with
    flip-module), REFUSES. The capture form is shown as a diff and a HAZARD refuses unless
    `allow_reorder` (e526672a W4). An empty body is the empty-module birth `new_module`
    delegates to."""
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
    path = root + module_path
    if body and Path(path).exists():
        return {"error": f"a file already exists at {path} — a file-born module is captured "
                         "with flip-module, never re-born", "written": False}
    imp = import_name or derive_import_name(module_path)
    report = {"diff": "", "hazards": []}
    try:
        # Born captured (bef8e6b8): its text is the composition of its element records, the form
        # shown and guarded here
        text = birth_form(source_journal_path, repo_dir_name(repo_key), module_path, imp, body)
    except (SyntaxError, ValueError, CodeSourceError) as e:
        return {"error": f"body does not decompose: {e}", "written": False}
    if body:
        report = canonicalization_report(body, text, module_path)
    result = {"module_id": mid, "repo_key": repo_key, "module_path": module_path,
              "import_name": imp, "path": path, "written": False,
              "body_bytes": len(text.encode("utf-8")),
              "file_already_canonical": text == body,
              "canonicalization_diff": report["diff"], "hazards": report["hazards"],
              "born_captured": True,
              "note": ("born captured: its element records are its source, journaled as one group "
                       "(the ruling bef8e6b8)" if body else
                       "born captured: the empty module is journaled as one group — populate it "
                       "with add-text / add-symbol (no flip-module follows)")}
    if report["hazards"] and not allow_reorder:
        return {**result, "error": f"the canonical form of {module_path} can change its "
                                   "behaviour — " + "; ".join(report["hazards"])
                                   + " (restructure the body, or pass --allow-reorder)"}
    if write and not source_journal_path:
        return {**result, "error": "add-module needs the source journal — a module is on the "
                                   "graph because the journal holds it (2cc81d3b)"}
    # The module's capture group, and the repo REGISTER event (DEC c47912f6).
    with collect_appends() as appended:
        rec = journaled_emit(source_journal_path,
                             emissions=[{"repo_key": repo_dir_name(repo_key),
                                         "module_path": module_path, "import_name": imp,
                                         "text": text, "path": path, "born": True}],
                             registers=[{"repo_key": repo_dir_name(repo_key),
                                         "repo_root": root.rstrip("/"),
                                         "source_kind": "code"}],
                             op={"op": "add-module" if body else "new-module",
                                 "module_path": module_path}, write=write)
    if rec.get("error"):
        return {**result, "error": rec["error"]}
    result["journal"] = rec
    if write:
        result["live"] = await apply_live(gx, source_journal_path, repos_dir, appended)
        result["written"] = True
    return result


async def new_module(
    gx: GraphHandle,
    repo_key: str,            # The repo's durable conceptual slug (the rename-stable Entity key)
    module_path: str,         # Repo-relative path of the new module (e.g. "pkg/sub.py")
    *,
    import_name: Optional[str] = None,  # Dotted import name (derived from module_path if omitted)
    repo_root: Optional[str] = None,    # Absolute repo root — anchors the FIRST module of a fresh repo (no sibling to derive from)
    write: bool = True,       # Journal + write + derive (else dry run)
    source_journal_path: Optional[str] = None,  # The source journal (the module's capture group and the repo REGISTER land here)
    repos_dir: Optional[str] = None,  # The repos root (the live fold step derives node paths under it)
) -> Dict[str, Any]:  # The new-module result (or error)
    """Mint an EMPTY module, graph-sourced from birth (the target add-text / add-symbol /
    `regroup` / `move` populate).

    Under the code fold (2cc81d3b) a module is on the graph because the journal holds it: the
    verb journals the empty module's capture group (and the repo's REGISTER), writes the empty
    file, and the code fold's step derives the CodeModule node — so no flip-module follows the
    birth, and the module survives every rebuild. A
    repo with no modules yet (born-on-graph greenfield) has no sibling to derive the on-disk
    root from — pass `repo_root` explicitly there; an existing sibling-derived root always
    wins. The empty-body case of `add_module` (177dcb07): one birth path, two verbs."""
    return await add_module(gx, repo_key, module_path, "", import_name=import_name,
                            repo_root=repo_root, write=write,
                            source_journal_path=source_journal_path, repos_dir=repos_dir)


async def regroup(
    gx: GraphHandle,
    repo_key: str,                  # The repo the symbols + target live in (same-repo, v1)
    target_module_path: str,        # Repo-relative path of the module to gather them into (created if absent)
    symbol_ids: List[str],          # The top-level CodeSymbols to relocate
    *,
    import_name: Optional[str] = None,  # Target's dotted import name (derived if omitted)
    write: bool = True,             # Execute (else dry-run preview)
    source_journal_path: Optional[str] = None,  # The source journal (events land BEFORE files)
    repos_dir: Optional[str] = None,  # The repos root (the live fold step derives node paths under it)
) -> Dict[str, Any]:  # The regroup result (created_target + the relocation outcome, or error)
    """Gather symbols into a module — the EXECUTE verb for an `under_split` (extract a
    grab-bag into a cohesive module) or `over_split` (consolidate a scattered helper)
    finding. Creates the target module if it doesn't exist, then batch-relocates every
    symbol in ONE emit pass via `_relocate` (re-emitting each affected source module + the
    target + the importers, imports re-derived). A NOT-yet-existing target is synthesized
    in memory and born captured by the relocation's own records, so the code fold's step
    derives it with everything else."""
    target_id = code_module_node_id(repo_key, target_module_path)
    existing = await _get(gx, target_id)
    target_node: Any = existing
    created = False
    if existing is None:
        root = await _repo_root(gx, repo_key)
        if root is None:
            return {"error": f"no existing module in repo `{repo_key}` to anchor the path",
                    "written": False}
        imp = import_name or derive_import_name(target_module_path)
        node = CodeModuleNode(repo_key=repo_key, module_path=target_module_path,
                              path=root + target_module_path,
                              content_hash=SourceRef.compute_hash(b""), import_name=imp)
        target_node = node.to_graph_node()  # synthesized; born by the relocation's records
        created = True
    res = await _relocate(gx, symbol_ids, target_id, write=write, target_node=target_node,
                          source_journal_path=source_journal_path, op_name="regroup",
                          repos_dir=repos_dir)
    res["created_target"] = created
    res["target_module"] = import_name or derive_import_name(target_module_path)
    return res


async def rename_module(
    gx: GraphHandle,
    module_id: str,                 # The CodeModule to rename
    new_module_path: str,           # Its new repo-relative path
    *,
    new_import_name: Optional[str] = None,  # New dotted import name (derived if omitted)
    write: bool = True,             # Execute (else dry-run preview)
    source_journal_path: Optional[str] = None,  # The source journal (events land BEFORE files)
    repos_dir: Optional[str] = None,  # The repos root (the live fold step derives node paths under it)
) -> Dict[str, Any]:  # The rename result (importer rewrites, files, or error)
    """Rename a `.py` module — re-emit its content at the new path, drop the old file, and
    rewrite every importer's `from old import …` / `import old` to the new name. Purely
    import-level (no body touched). The MODULE re-keys (its id embeds the path — the
    federation anchor) but its SYMBOLS keep their ids (36f649d3: the op rides the journal
    with `identity: keep`, and the identity map re-registers every name from the retired
    path): the code fold's step derives the new module, re-homes the symbols in place and
    drops the old module node + its code-text regions — no rebuild needed. A CAPTURED module
    (the element regime) keeps its module id too: its write re-states the path under the
    journaled id (design detail aba8ec79 (4))."""
    M = await _module_node(gx, module_id)
    if M is None:
        return {"error": f"no module `{module_id}`", "written": False}
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
    new_import = new_import_name or derive_import_name(new_module_path)

    text = emit_module_from_nodes(await _module_region_wires(gx, module_id),
                                  module_node=M, derive_imports=not is_test_module_path(old_mp))
    files: List[Tuple[str, str]] = [(new_path, text)]
    dir_key = repo_dir_name(repo_key)
    renamed_emission: Dict[str, Any] = {"repo_key": dir_key, "module_path": new_module_path,
                                        "import_name": new_import, "text": text,
                                        "path": new_path}
    emissions: List[Optional[Dict[str, Any]]] = [renamed_emission]
    import_pairs = await F.load_edge_pairs(gx, DevRelations.IMPORTS)
    importers = [s for s, t in import_pairs if t == module_id and s != module_id]
    caller_hits: List[str] = []
    for mid in dict.fromkeys(importers):
        m = await _module_node(gx, mid)
        itext = emit_module_from_nodes(await _module_region_wires(gx, mid))
        new_itext, changed = rewrite_module_import(itext, old_import, new_import)
        if changed:
            files.append((F.prop(m, "path"), new_itext))
            emissions.append(_emission_for(m, new_itext))
            caller_hits.append(F.prop(m, "import_name", mid))

    result = {"from_module": old_import, "to_module": new_import,
              "from_path": old_mp, "to_path": new_module_path,
              "caller_imports_rewritten": sorted(dict.fromkeys(caller_hits)),
              "files": [f for f, _ in files], "written": False,
              "note": "the module re-keys (path identity); its symbols keep their ids "
                      "(36f649d3) and are re-homed live — no rebuild needed"}
    if any(e is None for e in emissions):
        return {**result, "error": "cannot derive a source-journal key for an affected "
                "module (notebook-backed caller?) — refusing to write unjournaled"}
    with collect_appends() as appended:
        rec = journaled_emit(source_journal_path, emissions=emissions,
                             retires=[{"repo_key": dir_key, "module_path": old_mp,
                                       "superseded_by": new_module_path}],
                             deletes=[old_path] if (old_path and Path(old_path) != Path(new_path))
                                     else [],
                             op={"op": "rename-module", "from": old_mp, "to": new_module_path,
                                 "identity": "keep"},
                             write=write)
    if rec.get("error"):
        return {**result, "error": rec["error"]}
    result["journal"] = rec
    if write:
        # The code fold's step: the new key derives (symbols keep their ids, the code-text
        # regions re-key), the retired key's module node + regions go.
        result["live"] = await apply_live(gx, source_journal_path, repos_dir, appended)
        kept = any(ev.get("restated") == "path" for ev in rec.get("events", []))
        result["module_id"] = module_id if kept else new_id  # a captured module keeps its id (aba8ec79 (4))
        result["written"] = True
    return result


async def delete_module(
    gx: GraphHandle,
    module_id: str,        # The CodeModule to delete
    *,
    force: bool = False,   # Delete even if it still defines top-level symbols (a confirmed-dead module)
    write: bool = True,    # Execute (else dry-run preview)
    source_journal_path: Optional[str] = None,  # The source journal (the retire event leads)
    repos_dir: Optional[str] = None,  # The repos root (the live fold step derives node paths under it)
) -> Dict[str, Any]:  # The delete result (or error/guard)
    """Delete a module — retire its journal key, drop its file, and let the code fold's step
    take its whole subtree off the graph (and re-resolve every name it held). Guarded: refuses
    while it still DEFINES top-level symbols (move them out first), unless `force` (a
    confirmed-dead module)."""
    M, amb = await _resolve_node(gx, module_id)
    if amb:
        return {"error": amb, "written": False}
    if M is None:
        return {"error": f"no module `{module_id}`", "written": False}
    if _label_of(M) != DevNodeKinds.CODE_MODULE:
        return {"error": f"delete-module targets a CodeModule (got {_label_of(M)})",
                "written": False}
    module_id = F.nid(M)
    region_wires = await _module_region_wires(gx, module_id)
    top_syms = [w for w in region_wires
                if w["label"] == DevNodeKinds.CODE_SYMBOL and w["properties"].get("body")]
    if top_syms and not force:
        return {"error": f"module still defines {len(top_syms)} top-level symbol(s); "
                         f"move them out first or pass force",
                "symbols": [w["properties"].get("qualname", "") for w in top_syms],
                "written": False}
    path = F.prop(M, "path")
    if str(path or "").endswith(".ipynb"):
        return {"error": "delete-module handles .py modules", "written": False}
    ids = await _module_subtree_ids(gx, module_id)
    result = {"module_id": module_id, "import_name": F.prop(M, "import_name", ""),
              "path": path, "node_count": len(ids), "forced": force, "written": False}
    # The retire event leads; the unlink follows (journal-first — a deleted file whose
    # key stayed live would hold source-check red forever, finding ff8522fa's cousin).
    with collect_appends() as appended:
        rec = journaled_emit(source_journal_path,
                             retires=[{"repo_key": repo_dir_name(F.prop(M, "repo_key", "")),
                                       "module_path": str(F.prop(M, "module_path") or "")}],
                             deletes=[path] if path else [],
                             op={"op": "delete-module",
                                 "import_name": F.prop(M, "import_name", "")},
                             write=write)
    if rec.get("error"):
        return {**result, "error": rec["error"]}
    result["journal"] = rec
    if write:
        result["live"] = await apply_live(gx, source_journal_path, repos_dir, appended)
        result["written"] = True
    return result


async def flip_notebook_to_py(
    gx: GraphHandle,
    source_journal_path: str,   # The source journal (the module's capture group lands here)
    repos_dir: str,             # The repos root
    repo_key: str,              # The repo's durable conceptual slug (its directory under the repos root)
    notebook_path: str,         # Repo-relative .ipynb path (the file converted, then deleted)
    *,
    docstring: Optional[str] = None,  # Module docstring (the prose-triage fold), verbatim
    write: bool = True,               # Execute (else dry-run preview, nothing touched)
) -> Dict[str, Any]:  # The flip report (or the guard that refused it)
    """The off-nbdev step, ONE LOUD VERB, in the element regime (design detail 25c1fadc, ruling N):
    a notebook FILE -> a plain `.py` module BORN CAPTURED.

    Builds the arc-lib-shaped module from the notebook's EXPORT cells (`notebook_to_py_source`:
    `#|` directives stripped, `__all__` dropped, every other cell reported -- never silently
    discarded), then in one journaled op births it captured (its capture group, and the repo's
    REGISTER), writes the `.py` as the composition of its elements, and deletes the notebook file.
    The notebook never enters the code journal: its text stays in the repo's git history, the
    derivation's provenance. A notebook that exports nothing, a module the journal already holds
    and a `.py` file already on disk (a file-born module is captured with flip-module) REFUSE."""
    nb_file = Path(repos_dir) / repo_key / notebook_path
    if not notebook_path.endswith(".ipynb") or not nb_file.exists():
        return {"error": f"no notebook at {nb_file}", "written": False}
    try:
        built = notebook_to_py_source(nb_file.read_text(), docstring=docstring)
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
    import_name = derive_import_name(module_path)
    file_path = str(Path(repos_dir) / repo_key / module_path)
    if Path(file_path).exists():
        return {"error": f"a file already exists at {file_path} — capture it with flip-module "
                         "and retire the notebook by hand", "written": False}
    try:
        text = birth_form(source_journal_path, repo_key, module_path, import_name, built["text"])
    except (SyntaxError, ValueError, CodeSourceError) as e:
        return {"error": f"the built module does not decompose: {e}", "written": False}
    result = {"repo_key": repo_key, "notebook_path": notebook_path,
              "module_path": module_path, "import_name": import_name,
              "file_path": file_path, "module_id": code_module_node_id(repo_key, module_path),
              "export_cells": built["export_cells"],
              "markdown_cells_dropped": built["markdown_cells"],
              "nonexport_code_cells_dropped": built["nonexport_code_cells"],
              "dropped_all_dunder": built["dropped_all_dunder"],
              "born_bytes": len(text.encode("utf-8")), "written": False}
    if not write:
        result["note"] = "dry run — nothing journaled, written, or deleted"
        return result
    # ONE seam call (journal-first): the capture group lands BEFORE the .py write and the
    # notebook unlink.
    with collect_appends() as appended:
        rec = journaled_emit(source_journal_path,
                             emissions=[{"repo_key": repo_key, "module_path": module_path,
                                         "import_name": import_name, "text": text,
                                         "path": file_path, "born": True}],
                             registers=[{"repo_key": repo_key,
                                         "repo_root": str(Path(repos_dir) / repo_key),
                                         "source_kind": "code"}],
                             deletes=[str(nb_file)],
                             op={"op": "flip-to-py", "from": notebook_path, "to": module_path})
    if rec.get("error"):
        return {"error": f"flip refused at the seam: {rec['error']}", **result}
    result["journal"] = rec
    result.update({"written": True, "notebook_deleted": not nb_file.exists(),
                   "live": await apply_live(gx, source_journal_path, repos_dir, appended),
                   "note": "BORN CAPTURED as plain .py; the notebook file is deleted (its text stays "
                           "in the repo's git history)"})
    return result
