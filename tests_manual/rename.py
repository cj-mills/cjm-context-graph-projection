#!/usr/bin/env python
"""Symbol-`rename` dogfood: the Ext-B increment — scoped identifier substitution INTO bodies.

Renaming a top-level free function/class as a graph-driven refactor (direction A, file stays
source): the graph knows the defining module + the importers, so `rename_symbol` rewrites the
def/class site, every reference in OTHER bodies (scope-aware — strings/comments/attributes/
shadowed locals untouched), and each importer's import line (alias-aware). The next `ingest`
re-derives the graph (the symbol's id changes with its name) and the callers' CALLS re-resolve.

Self-contained: each scenario builds a temp repo on a SCRATCH graph (the real repos are NEVER
written). Run in a core env with the substrate runtime + the libs installed -e:

    conda run -n cjm-transcript-correction-core --no-capture-output python \
        cjm-context-graph-projection/tests_manual/rename.py
"""
import ast
import asyncio
import sys
import tempfile
from pathlib import Path

from cjm_context_graph_layer.ops import extend_graph
from cjm_dev_graph_schema.identity import code_module_node_id, code_symbol_node_id

from cjm_context_graph_projection.projection import show
from cjm_context_graph_projection.rename_ops import rename_symbol
from cjm_context_graph_projection.runtime import open_graph
from cjm_python_decompose_core.extract import decompose_paths
from cjm_python_decompose_core.ingest import corpus_graph_elements

REPO = "demo-rename"


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
    paths = sorted(str(p) for p in Path(root).rglob("*.py"))
    decs = decompose_paths(REPO, paths, root)
    nodes, edges = corpus_graph_elements(decs)
    await extend_graph(gx.queue, gx.graph_id, nodes, edges)


# core defines `parse` (free fn) + `Loader` (class). use_plain imports both unaliased and has
# a local var also named `parse` (must stay). use_alias imports `parse as p` (import line
# changes, body keeps `p`). unrelated has its own `parse` method on a class (must stay).
FILES = {
    "demo/core.py": (
        '"""Core."""\n\n\n'
        'def parse(text):\n    # parse the text\n    return text.strip()\n\n\n'
        'def reparse(text):\n    return parse(text) + "!"\n\n\n'        # internal caller
        'class Loader:\n    def run(self, t):\n        return parse(t)\n'  # method body ref
    ),
    "demo/use_plain.py": (
        '"""Plain consumer."""\nfrom demo.core import parse, Loader\n\n\n'
        'def go(t):\n    parse_count = 1            # local var, must NOT rename\n'
        '    return parse(t) + str(parse_count) + Loader().run(t)\n'
    ),
    "demo/use_alias.py": (
        '"""Aliased consumer."""\nfrom demo.core import parse as p\n\n\n'
        'def go(t):\n    return p(t)\n'
    ),
    "demo/unrelated.py": (
        '"""Unrelated — has its own parse method + a string \'parse\'."""\n\n\n'
        'class Other:\n    def parse(self, x):       # attribute/method, must NOT rename\n'
        '        return x\n\n\n'
        'def f(o):\n    return o.parse("parse")      # attr access + string, must NOT rename\n'
    ),
}


async def scenario_rename_function(root) -> bool:
    ok = True
    _write_repo(root, FILES)
    core_id = code_module_node_id(REPO, "demo/core.py")
    parse_id = code_symbol_node_id(core_id, "parse")
    before = {p: (Path(root) / p).read_text() for p in FILES}
    db = str(Path(root) / "r.db")
    async with open_graph(db) as gx:
        await _ingest(gx, root)

        dry = await rename_symbol(gx, parse_id, "parse_text", write=False)
        ok &= _check("dry-run reported no error", not dry.get("error"))
        ok &= _check("dry-run did not write", not dry.get("written")
                     and all((Path(root) / p).read_text() == before[p] for p in FILES))

        res = await rename_symbol(gx, parse_id, "parse_text", write=True)
        ok &= _check("rename reported no error", not res.get("error"))
        ok &= _check("rename wrote files", res.get("written"))
        ok &= _check("both importers updated",
                     set(res.get("modules_updated", [])) == {"demo.use_plain", "demo.use_alias"})

        core = (Path(root) / "demo/core.py").read_text()
        plain = (Path(root) / "demo/use_plain.py").read_text()
        alias = (Path(root) / "demo/use_alias.py").read_text()
        unrel = (Path(root) / "demo/unrelated.py").read_text()

        ok &= _check("def site renamed", "def parse_text(text):" in core)
        ok &= _check("internal caller (reparse) renamed", "return parse_text(text)" in core)
        ok &= _check("method-body ref renamed", "return parse_text(t)" in core)
        ok &= _check("the '# parse the text' comment is untouched", "# parse the text" in core)
        ok &= _check("plain importer: import re-pointed",
                     "from demo.core import parse_text, Loader" in plain)
        ok &= _check("plain importer: body ref renamed", "return parse_text(t) +" in plain)
        ok &= _check("plain importer: local var parse_count UNtouched", "parse_count = 1" in plain)
        ok &= _check("aliased importer: import imported-name changed, alias kept",
                     "from demo.core import parse_text as p" in alias)
        ok &= _check("aliased importer: body still uses the alias p", "return p(t)" in alias)
        ok &= _check("unrelated module BYTE-EXACT (own method + string + attr)", unrel == before["demo/unrelated.py"])
        ok &= _check("all modules valid Python", all(_parses(Path(root) / p) for p in FILES))

        # re-ingest: parse_text now lives under core; callers' CALLS re-resolve to it.
        db2 = str(Path(root) / "r2.db")
        async with open_graph(db2) as gx2:
            await _ingest(gx2, root)
            sh = await show(gx2, code_symbol_node_id(core_id, "parse_text"))
            callers = [n for n in sh.get("neighbours", [])
                       if n["relation"] == "CALLS" and n["direction"] == "in"]
            ok &= _check("after re-ingest: renamed symbol exists + is called (CALLS survive)",
                         bool(sh.get("node")) and bool(callers))
            gone = await show(gx2, parse_id)
            ok &= _check("after re-ingest: old symbol id is gone", not gone.get("node"))
    return ok


# A class rename: def site + a subclass base + an annotation + an importer.
CLS_FILES = {
    "demo/m.py": '"""M."""\n\n\nclass Widget:\n    pass\n\n\nclass Big(Widget):\n    pass\n',
    "demo/c.py": ('"""C."""\nfrom demo.m import Widget\n\n\n'
                  'def make(w: Widget) -> Widget:\n    return w\n'),
}


async def scenario_rename_class(root) -> bool:
    ok = True
    _write_repo(root, CLS_FILES)
    m_id = code_module_node_id(REPO, "demo/m.py")
    widget_id = code_symbol_node_id(m_id, "Widget")
    db = str(Path(root) / "cls.db")
    async with open_graph(db) as gx:
        await _ingest(gx, root)
        res = await rename_symbol(gx, widget_id, "Gadget", write=True)
        ok &= _check("class rename no error", not res.get("error"))
        m = (Path(root) / "demo/m.py").read_text()
        c = (Path(root) / "demo/c.py").read_text()
        ok &= _check("class def renamed", "class Gadget:" in m)
        ok &= _check("subclass base renamed", "class Big(Gadget):" in m)
        ok &= _check("importer import renamed", "from demo.m import Gadget" in c)
        ok &= _check("annotation refs renamed", "def make(w: Gadget) -> Gadget:" in c)
        ok &= _check("both valid Python", _parses(Path(root) / "demo/m.py") and _parses(Path(root) / "demo/c.py"))
    return ok


async def main() -> int:
    ok = True
    for name, fn in (("rename function", scenario_rename_function),
                     ("rename class", scenario_rename_class)):
        print(f"\n## scenario: {name}")
        with tempfile.TemporaryDirectory() as root:
            ok &= await fn(root)
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
