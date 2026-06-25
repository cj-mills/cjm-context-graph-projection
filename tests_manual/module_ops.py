#!/usr/bin/env python
"""Module-edit-ops dogfood: create / regroup / rename / delete a module as graph edge ops.

The EXECUTE half of the cohesion oracle ([[true-b-projected-structure-discussion]] N+1) at
direction A (file stays source): the graph knows the membership + the importers, so an
organizational change is an edge op that re-emits the affected files with imports re-derived.
All four ops are structural/import-level — no verbatim body is ever rewritten (symbol
`rename` / Ext-B is a separate increment).

Self-contained: each scenario builds a temp repo on a SCRATCH graph (the real repos are
NEVER written). Run in a core env with the substrate runtime + the libs installed -e:

    conda run -n cjm-transcript-correction-core --no-capture-output python \
        cjm-context-graph-projection/tests_manual/module_ops.py
"""
import ast
import asyncio
import sys
import tempfile
from pathlib import Path

from cjm_context_graph_layer.ops import extend_graph
from cjm_dev_graph_schema.identity import code_module_node_id, code_symbol_node_id

from cjm_context_graph_projection.module_ops import (delete_module, new_module, regroup,
                                                     rename_module)
from cjm_context_graph_projection.refactor_ops import _get
from cjm_context_graph_projection.runtime import open_graph
from cjm_python_decompose_core.extract import decompose_paths
from cjm_python_decompose_core.ingest import corpus_graph_elements

REPO = "demo-modops"


def _check(label, ok):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    return ok


def _parses(p):
    try:
        ast.parse(Path(p).read_text()); return True
    except SyntaxError:
        return False


def _write_repo(root, files):
    for rel, src in files.items():
        f = Path(root) / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(src)


async def _ingest(gx, root):
    """(Re-)ingest every .py currently under root — so new/renamed files are picked up and
    deleted ones drop (a fresh scratch graph each time)."""
    paths = sorted(str(p) for p in Path(root).rglob("*.py"))
    decs = decompose_paths(REPO, paths, root)
    nodes, edges = corpus_graph_elements(decs)
    await extend_graph(gx.queue, gx.graph_id, nodes, edges)


# --- Scenario A: regroup — extract a grab-bag cluster into a NEW module --------------------
A_FILES = {
    "demo/core.py": ('"""Grab-bag core."""\n\n\ndef helper(x):\n    return x * 2\n\n\n'
                     'def alpha(n):\n    return helper(n) + 1\n\n\n'
                     'def beta(n):\n    return helper(n) - 1\n\n\n'
                     'def gamma():\n    return 0\n'),
    "demo/use.py": ('"""Consumer."""\nfrom demo.core import alpha, gamma\n\n\n'
                    'def run():\n    return alpha(1) + gamma()\n'),
}


async def scenario_regroup(root) -> bool:
    ok = True
    _write_repo(root, A_FILES)
    core_id = code_module_node_id(REPO, "demo/core.py")
    grp_id = code_module_node_id(REPO, "demo/grp.py")
    alpha_id = code_symbol_node_id(core_id, "alpha")
    helper_id = code_symbol_node_id(core_id, "helper")
    db = str(Path(root) / "a.db")
    async with open_graph(db) as gx:
        await _ingest(gx, root)

        # dry-run into the not-yet-existing target: previews without mutating the graph
        dry = await regroup(gx, REPO, "demo/grp.py", [alpha_id, helper_id], write=False)
        ok &= _check("regroup dry-run: created_target flagged", dry.get("created_target"))
        ok &= _check("regroup dry-run: not written", not dry.get("written"))
        ok &= _check("regroup dry-run: target not persisted", await _get(gx, grp_id) is None)

        res = await regroup(gx, REPO, "demo/grp.py", [alpha_id, helper_id], write=True)
        ok &= _check("regroup reported no error", not res.get("error"))
        ok &= _check("regroup created the target module", res.get("created_target"))
        ok &= _check("regroup wrote files", res.get("written"))
        ok &= _check("use.py import rewrite recorded", "demo.use" in res.get("caller_imports_rewritten", []))

        core_txt = (Path(root) / "demo/core.py").read_text()
        grp_txt = (Path(root) / "demo/grp.py").read_text()
        use_txt = (Path(root) / "demo/use.py").read_text()
        ok &= _check("alpha+helper left core", "def alpha" not in core_txt and "def helper" not in core_txt)
        ok &= _check("beta+gamma stayed in core", "def beta" in core_txt and "def gamma" in core_txt)
        ok &= _check("alpha+helper landed in grp", "def alpha" in grp_txt and "def helper" in grp_txt)
        # zero-residual both ways: core.beta still uses helper (now cross-module); alpha+helper
        # moved together so within grp helper needs no import.
        ok &= _check("core synthesizes `from demo.grp import helper` (beta still uses it)",
                     "from demo.grp import helper" in core_txt)
        ok &= _check("grp does NOT import helper (alpha+helper co-moved)",
                     "import helper" not in grp_txt)
        ok &= _check("use.py imports alpha from grp now", "from demo.grp import alpha" in use_txt)
        ok &= _check("use.py still imports gamma from core", "from demo.core import gamma" in use_txt)
        ok &= _check("all modules valid Python",
                     all(_parses(Path(root) / p) for p in ("demo/core.py", "demo/grp.py", "demo/use.py")))

        # re-ingest the rewritten tree: alpha now lives under grp and use's CALLS re-resolves.
        db2 = str(Path(root) / "a2.db")
        async with open_graph(db2) as gx2:
            await _ingest(gx2, root)
            from cjm_context_graph_projection.projection import show
            sh = await show(gx2, code_symbol_node_id(grp_id, "alpha"))
            callers = [n for n in sh.get("neighbours", [])
                       if n["relation"] == "CALLS" and n["direction"] == "in"]
            ok &= _check("after re-ingest: moved alpha is called by use (CALLS survives)", bool(callers))
    return ok


# --- Scenario B: delete — guard, force, and an empty new module ----------------------------
B_FILES = {
    "demo/keep.py": '"""Keep."""\n\n\ndef stay():\n    return 1\n',
    "demo/dead.py": '"""Dead, unreferenced."""\n\n\ndef obsolete():\n    return 2\n',
}


async def scenario_delete(root) -> bool:
    ok = True
    _write_repo(root, B_FILES)
    dead_id = code_module_node_id(REPO, "demo/dead.py")
    fresh_id = code_module_node_id(REPO, "demo/fresh.py")
    db = str(Path(root) / "b.db")
    async with open_graph(db) as gx:
        await _ingest(gx, root)

        guard = await delete_module(gx, dead_id, force=False, write=True)
        ok &= _check("delete guard refuses a module with symbols", bool(guard.get("error")))
        ok &= _check("guard lists the blocking symbols", "obsolete" in guard.get("symbols", []))
        ok &= _check("guard left the file on disk", (Path(root) / "demo/dead.py").exists())

        forced = await delete_module(gx, dead_id, force=True, write=True)
        ok &= _check("forced delete reported written", forced.get("written"))
        ok &= _check("forced delete removed the file", not (Path(root) / "demo/dead.py").exists())
        ok &= _check("forced delete removed the graph node", await _get(gx, dead_id) is None)

        # an empty new module deletes cleanly with NO force (no symbols to block)
        await new_module(gx, REPO, "demo/fresh.py", write=True)
        ok &= _check("new-module added the node", await _get(gx, fresh_id) is not None)
        ok &= _check("new-module wrote no file", not (Path(root) / "demo/fresh.py").exists())
        dup = await new_module(gx, REPO, "demo/fresh.py", write=True)
        ok &= _check("new-module refuses a duplicate", bool(dup.get("error")))
        empty_del = await delete_module(gx, fresh_id, force=False, write=True)
        ok &= _check("empty module deletes without force", empty_del.get("written"))
        ok &= _check("empty module node removed", await _get(gx, fresh_id) is None)
    return ok


# --- Scenario C: rename — re-emit at new path + rewrite importer imports -------------------
C_FILES = {
    "demo/lib.py": '"""Lib."""\n\n\ndef foo():\n    return 7\n',
    "demo/consumer.py": '"""Consumer."""\nfrom demo.lib import foo\n\n\ndef call():\n    return foo()\n',
}


async def scenario_rename(root) -> bool:
    ok = True
    _write_repo(root, C_FILES)
    lib_id = code_module_node_id(REPO, "demo/lib.py")
    renamed_id = code_module_node_id(REPO, "demo/renamed.py")
    db = str(Path(root) / "c.db")
    async with open_graph(db) as gx:
        await _ingest(gx, root)

        res = await rename_module(gx, lib_id, "demo/renamed.py", write=True)
        ok &= _check("rename reported no error", not res.get("error"))
        ok &= _check("rename wrote files", res.get("written"))
        ok &= _check("importer rewrite recorded", "demo.consumer" in res.get("caller_imports_rewritten", []))
        ok &= _check("new file exists with foo",
                     (Path(root) / "demo/renamed.py").exists()
                     and "def foo" in (Path(root) / "demo/renamed.py").read_text())
        ok &= _check("old file removed", not (Path(root) / "demo/lib.py").exists())
        ok &= _check("consumer imports from the new module",
                     "from demo.renamed import foo" in (Path(root) / "demo/consumer.py").read_text())
        ok &= _check("old module node dropped from graph", await _get(gx, lib_id) is None)
        ok &= _check("both files valid Python",
                     _parses(Path(root) / "demo/renamed.py") and _parses(Path(root) / "demo/consumer.py"))

        # re-ingest: the renamed module is re-derived; foo lives under it and is still called.
        db2 = str(Path(root) / "c2.db")
        async with open_graph(db2) as gx2:
            await _ingest(gx2, root)
            ok &= _check("after re-ingest: renamed module present", await _get(gx2, renamed_id) is not None)
            from cjm_context_graph_projection.projection import show
            sh = await show(gx2, code_symbol_node_id(renamed_id, "foo"))
            callers = [n for n in sh.get("neighbours", [])
                       if n["relation"] == "CALLS" and n["direction"] == "in"]
            ok &= _check("after re-ingest: foo still called by consumer", bool(callers))
    return ok


async def main() -> int:
    ok = True
    for name, fn in (("regroup", scenario_regroup), ("delete", scenario_delete),
                     ("rename", scenario_rename)):
        print(f"\n## scenario: {name}")
        with tempfile.TemporaryDirectory() as root:
            ok &= await fn(root)
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
