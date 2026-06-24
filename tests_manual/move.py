#!/usr/bin/env python
"""`move` dogfood: relocate a symbol across modules as a graph-driven refactor.

The EXECUTE half of refactor-candidates ([[graph-as-source-of-truth-inversion]] "refactoring
as edge updates"), at direction A (file stays source): the graph knows what to move + who
imports it, so `move` relocates the verbatim body A->B, re-emits both files, and rewrites
each caller's `from A import S` -> `from B import S`. Re-ingest then re-derives the graph
(S's new id under B; CALLS re-resolved by name) — the "references survive the move" proof.

Self-contained: builds a temp 3-module repo on a SCRATCH graph (the real repos are NEVER
written). Run in a core env with the substrate runtime + the libs installed -e:

    conda run -n cjm-transcript-correction-core --no-capture-output python \
        cjm-context-graph-projection/tests_manual/move.py
"""
import ast
import asyncio
import sys
import tempfile
from pathlib import Path

from cjm_context_graph_layer.ops import extend_graph
from cjm_dev_graph_schema.identity import code_module_node_id, code_symbol_node_id

from cjm_context_graph_projection.refactor_ops import move
from cjm_context_graph_projection.runtime import open_graph
from cjm_python_decompose_core.extract import decompose_paths
from cjm_python_decompose_core.ingest import corpus_graph_elements

REPO = "demo-move"
PKG = "demo"
# a = home of S (helper); b = the move target; c = a caller that imports S from a.
FILES = {
    "demo/a.py": '"""Module a."""\n\n\ndef helper(x):\n    return x * 2\n\n\ndef shared(n):\n    return n + 1\n',
    "demo/b.py": '"""Module b."""\n\n\ndef other():\n    return 0\n',
    "demo/c.py": '"""Module c."""\nfrom demo.a import shared, helper\n\n\ndef use():\n    return shared(helper(3))\n',
}


def _check(label, ok):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    return ok


async def _ingest(gx, root):
    paths = [str(Path(root) / p) for p in FILES]
    decs = decompose_paths(REPO, paths, root)
    nodes, edges = corpus_graph_elements(decs)
    await extend_graph(gx.queue, gx.graph_id, nodes, edges)


def _parses(p):
    try:
        ast.parse(Path(p).read_text()); return True
    except SyntaxError:
        return False


async def main() -> int:
    ok = True
    with tempfile.TemporaryDirectory() as root:
        for rel, src in FILES.items():
            f = Path(root) / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(src)
        a_id = code_module_node_id(REPO, "demo/a.py")
        b_id = code_module_node_id(REPO, "demo/b.py")
        shared_id = code_symbol_node_id(a_id, "shared")

        db = str(Path(root) / "move.db")
        async with open_graph(db) as gx:
            await _ingest(gx, root)

            res = await move(gx, shared_id, b_id, write=True)
            ok &= _check("move reported no error", not res.get("error"))
            ok &= _check("move wrote the files", res.get("written"))
            ok &= _check("caller import rewrite recorded", "demo.c" in res.get("caller_imports_rewritten", []))

            a_txt = (Path(root) / "demo/a.py").read_text()
            b_txt = (Path(root) / "demo/b.py").read_text()
            c_txt = (Path(root) / "demo/c.py").read_text()
            ok &= _check("S removed from module a", "def shared" not in a_txt and "def helper" in a_txt)
            ok &= _check("S appended to module b", "def shared" in b_txt and "def other" in b_txt)
            ok &= _check("caller c imports S from b now", "from demo.b import shared" in c_txt)
            ok &= _check("caller c still imports helper from a", "from demo.a import helper" in c_txt)
            ok &= _check("all three modules are valid Python",
                         all(_parses(Path(root) / p) for p in FILES))

            # re-ingest from the moved files: S now lives under b, and the caller's CALLS
            # re-resolves to it (references survive the move).
            db2 = str(Path(root) / "move2.db")
            async with open_graph(db2) as gx2:
                await _ingest(gx2, root)
                moved_id = code_symbol_node_id(b_id, "shared")
                from cjm_context_graph_projection.projection import show
                sh = await show(gx2, moved_id)
                callers = [n for n in sh.get("neighbours", [])
                           if n["relation"] == "CALLS" and n["direction"] == "in"]
                ok &= _check("after re-ingest: moved S is called by the caller (CALLS survives)",
                             bool(callers))

            # dry-run leaves disk untouched
            res2 = await move(gx, shared_id, b_id, write=False)
            ok &= _check("dry-run does not write", not res2.get("written"))

    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
