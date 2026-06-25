#!/usr/bin/env python
"""Authoring-on-graph dogfood: the B write surface (the make-or-break increment).

Proves you can edit a function body / notebook cell through GRAPH operations and get
faithful `.py` / `.ipynb` back — the make-or-break primitive of
[[graph-as-source-of-truth-inversion]]. End-to-end against SCRATCH databases (the real
persistent dev-graph + real repo files are NEVER written):

  A. ROUND-TRIP from the graph (real corpus, read-only): ingest an arc lib's code, then
     `emit_artifact` a real module FROM THE GRAPH and assert it is byte-identical to the
     file on disk — the "graph is a sufficient source" proof.
  B. AUTHOR a real symbol body (real corpus, DRY-RUN, no disk write): a targeted `--edit`
     splice reflects in the emitted module, stays valid Python, leaves sibling symbols
     byte-stable; non-unique / absent OLD are refused (the NotebookEdit-pain ergonomics).
  C. AUTHOR + EMIT TO DISK (temp `.py`): a self-contained temp module; `replace` and
     targeted `edit` with write=True land on disk, valid Python, and round-trip.
  D. NOTEBOOK-CELL authoring (temp `.ipynb`): the SAME `author` verb on a `Cell` node —
     the unification (a verbatim-text slot, whatever the node kind); the `.ipynb` reflects
     the edit and the cell round-trips.

Run in a core env with the substrate runtime + the libs installed -e:

    conda run -n cjm-transcript-correction-core --no-capture-output python \
        cjm-context-graph-projection/tests_manual/authoring.py
"""
import argparse
import ast
import asyncio
import json
import sys
import tempfile
from pathlib import Path

from cjm_context_graph_layer.ops import extend_graph
from cjm_dev_graph_schema.identity import (cell_node_id, code_module_node_id,
                                           code_symbol_node_id, code_text_node_id)

from cjm_context_graph_projection.authoring import author, emit_artifact
from cjm_context_graph_projection.devgraph import build_dev_graph_elements
from cjm_context_graph_projection.runtime import open_graph
from cjm_context_graph_projection.seeds import conceptual_key

from cjm_python_decompose_core.extract import decompose_file
from cjm_python_decompose_core.ingest import corpus_graph_elements
from cjm_notebook_decompose_core.compose import (decompose_notebook_file,
                                                 module_path_for_notebook)
from cjm_notebook_decompose_core.ingest import notebook_graph_elements

REPOS = "/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills"
MEMORY = ("/home/innom-dt/.claude/projects/"
          "-mnt-SN850X-8TB-EXT4-Projects-GitHub-cj-mills-cjm-substrate/memory")
LIB = "cjm-python-decompose-core"          # the arc lib whose real code we round-trip / author (dry-run)
PKG = "cjm_python_decompose_core"


def _check(label, ok):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    return ok


async def part_a_b(gx, repos_dir) -> bool:
    ok = True
    repo_key = conceptual_key(LIB)
    mod_id = code_module_node_id(repo_key, f"{PKG}/parse.py")
    disk = Path(repos_dir) / LIB / PKG / "parse.py"

    # A. round-trip from the graph (imports DERIVED): bodies byte-exact, imports canonical.
    # parse.py's imports are already in canonical order, so the whole file is byte-exact here
    # even though emit_artifact now regenerates the import block (imports-as-projection).
    em = await emit_artifact(gx, mod_id, write=False)
    ok &= _check("emit_artifact reproduces a real module from the graph (imports derived, bodies byte-exact)",
                 not em.get("error") and _parses(em["text"]) and em["text"] == disk.read_text())

    # B. author a real top-level symbol (DRY-RUN): targeted edit, no disk write.
    sym_id = code_symbol_node_id(mod_id, "parse_module")
    res = await author(gx, sym_id, edit=("tree = ast.parse(text)",
                                         "tree = ast.parse(text)  # authored-on-graph"),
                       write=False)
    ok &= _check("author --edit (dry-run) did not touch disk", not res.get("written"))
    emitted = res.get("emitted_text", "")
    ok &= _check("authored edit appears in the emitted module",
                 "# authored-on-graph" in emitted)
    ok &= _check("emitted module is still valid Python", _parses(emitted))
    # sibling stability: removing exactly the inserted text restores the ORIGINAL file.
    ok &= _check("sibling symbols are byte-stable (only the target region changed)",
                 emitted.replace("  # authored-on-graph", "") == disk.read_text())

    # B. ergonomics: non-unique / absent OLD are refused (targeted-edit safety).
    dup = await author(gx, sym_id, edit=("text", "TEXT"), write=False)  # "text" repeats in the body
    ok &= _check("non-unique OLD is refused", dup.get("error") and "unique" in dup["error"])
    miss = await author(gx, sym_id, edit=("zzz-nope", "x"), write=False)
    ok &= _check("absent OLD is refused", miss.get("error") and "not found" in miss["error"])

    # B. a nested method (no verbatim body) is NOT authorable in v1 (coarse cut).
    nested = code_symbol_node_id(mod_id, "parse_regions.slice_text")  # a nested fn in parse.py
    nres = await author(gx, nested, replace="x", write=False)
    ok &= _check("a nested symbol (no body slot) is refused as non-authorable",
                 nres.get("error") is not None)
    return ok


async def part_c(gx) -> bool:
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "demo.py"
        original = ('"""Demo."""\n'
                    "import os\n\n\n"
                    "def greet(name):\n"
                    '    return f"hi {name}"\n\n\n'
                    "def total(xs):\n"
                    "    return sum(xs)\n")
        f.write_text(original)
        d = decompose_file("demo-temp", str(f), tmp)
        nodes, edges = corpus_graph_elements([d])
        await extend_graph(gx.queue, gx.graph_id, nodes, edges)

        mod_id = code_module_node_id("demo-temp", "demo.py")
        greet_id = code_symbol_node_id(mod_id, "greet")
        total_id = code_symbol_node_id(mod_id, "total")

        # replace a whole symbol body, written to disk.
        r1 = await author(gx, greet_id, replace='def greet(name):\n    return f"hello {name}!"',
                          write=True)
        ok &= _check("author --replace wrote the temp .py", r1.get("written"))
        after1 = f.read_text()
        ok &= _check("replaced body landed on disk + valid Python",
                     'return f"hello {name}!"' in after1 and _parses(after1))
        ok &= _check("untouched sibling `total` is byte-stable after replace",
                     "def total(xs):\n    return sum(xs)" in after1)

        # targeted edit on the OTHER symbol (re-emits the whole file from the graph state).
        r2 = await author(gx, total_id, edit=("sum(xs)", "sum(xs) + 0"), write=True)
        ok &= _check("author --edit wrote the temp .py", r2.get("written"))
        after2 = f.read_text()
        ok &= _check("targeted edit landed + replace edit persisted (graph carries both)",
                     "sum(xs) + 0" in after2 and 'return f"hello {name}!"' in after2
                     and _parses(after2))

        # round-trip: re-decompose the authored file -> emit -> stable (idempotent).
        d2 = decompose_file("demo-temp", str(f), tmp)
        from cjm_python_decompose_core.emit import emit_module_from_nodes
        ok &= _check("authored file round-trips (re-decompose -> emit == file)",
                     emit_module_from_nodes(list(d2.symbols) + list(d2.texts)) == after2)
    return ok


async def part_d(gx) -> bool:
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        nb_path = Path(tmp) / "demo.ipynb"
        nb = {
            "cells": [
                {"cell_type": "markdown", "id": "m0", "metadata": {},
                 "source": ["# Demo\n", "A greeter."]},
                {"cell_type": "code", "id": "c0", "metadata": {},
                 "source": ["#| export\n", "def greet(name):\n", "    return f'hi {name}'\n"],
                 "outputs": [], "execution_count": None},
            ],
            "metadata": {}, "nbformat": 4, "nbformat_minor": 5,
        }
        nb_path.write_text(json.dumps(nb, indent=1) + "\n")
        d = decompose_notebook_file("demo-temp", str(nb_path), tmp, package="demo")
        nodes, edges = notebook_graph_elements([d])
        await extend_graph(gx.queue, gx.graph_id, nodes, edges)

        cell_id = cell_node_id(d.module.id, "c0")
        # author the CODE cell's verbatim source via the SAME verb (Cell.source slot).
        r = await author(gx, cell_id, edit=("return f'hi {name}'", "return f'hello {name}!'"),
                         write=True)
        ok &= _check("author wrote the temp .ipynb (same verb, Cell node)", r.get("written"))
        ok &= _check("artifact routed as a notebook", r.get("artifact") == "notebook")

        reloaded = json.loads(nb_path.read_text())
        c0 = next(c for c in reloaded["cells"] if c.get("id") == "c0")
        src = "".join(c0["source"])
        ok &= _check("the .ipynb cell source reflects the edit",
                     "return f'hello {name}!'" in src)
        ok &= _check("the markdown cell is untouched (verbatim)",
                     "".join(reloaded["cells"][0]["source"]) == "# Demo\nA greeter.")
        # round-trip: re-decomposing the authored notebook reproduces the edited cell.
        d2 = decompose_notebook_file("demo-temp", str(nb_path), tmp, package="demo")
        c0_cell = next(c for c in d2.cells if c.cell_key == "c0")
        ok &= _check("authored notebook round-trips (re-decompose sees the edit)",
                     "return f'hello {name}!'" in c0_cell.source)
    return ok


def _parses(text: str) -> bool:
    try:
        ast.parse(text)
        return True
    except SyntaxError:
        return False


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos-dir", default=REPOS)
    ap.add_argument("--memory-dir", default=MEMORY)
    args = ap.parse_args()

    code_repos = [str(Path(args.repos_dir) / LIB)]
    nodes, edges = build_dev_graph_elements(args.memory_dir, args.repos_dir, seed=False,
                                            code_repos=code_repos)
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "authoring.db")
        async with open_graph(db) as gx:
            res = await extend_graph(gx.queue, gx.graph_id, nodes, edges)
            print(f"ingested {LIB}: {res.nodes_added} nodes / {res.edges_added} edges\n")
            print("A/B — round-trip + author a real symbol (dry-run):")
            ok &= await part_a_b(gx, args.repos_dir)
            print("C — author + emit to disk (temp .py):")
            ok &= await part_c(gx)
            print("D — notebook-cell authoring (temp .ipynb, same verb):")
            ok &= await part_d(gx)

    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
