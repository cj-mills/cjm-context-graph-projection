"""The golden-reference flip (notebook -> plain .py, ONE LOUD VERB): retire-op journal
semantics, the pure cell->module transform, and the orchestrator over a fake graph."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import cjm_context_graph_projection.factlayer as factlayer_mod
import cjm_context_graph_projection.module_ops as module_ops_mod
from cjm_context_graph_projection.journal import read_journal
from cjm_context_graph_projection.module_ops import flip_notebook_to_py
from cjm_context_graph_projection.source_state import (append_retire, cutover_module, flip_module,
                                                       graph_sourced_modules, latest_source_ops,
                                                       notebook_to_py_source, source_check)
from cjm_dev_graph_schema.identity import code_module_node_id

GX = SimpleNamespace(queue=None, graph_id="g")


def _nb(cells) -> str:
    """A minimal nbformat notebook from (cell_type, id, source) triples."""
    return json.dumps({"cells": [{"cell_type": t, "id": k, "metadata": {},
                                  **({"outputs": [], "execution_count": None}
                                     if t == "code" else {}),
                                  "source": s.splitlines(keepends=True)}
                                 for t, k, s in cells],
                      "metadata": {}, "nbformat": 4, "nbformat_minor": 5})


DEMO_NB = _nb([
    ("code", "c0", "#| default_exp m"),
    ("markdown", "c1", "# The demo module\n\nProse the triage disposes of."),
    ("code", "c2", "#| export\nimport os\nfrom pathlib import Path"),
    ("code", "c3", "#| export\ndef f():\n    return os.name"),
    ("code", "c4", "assert f() is not None"),
    ("code", "c5", "#| export\n__all__ = ['f']\nX = 1\n#| eval: false"),
])


# ---------------------------------------------------------------- retire semantics

def _journal_a_notebook(tmp_path, nb_text=DEMO_NB):
    """A graph-sourced notebook state (source + cutover), keystone-style."""
    repos = tmp_path / "repos"
    nb_file = repos / "cjm-demo" / "nbs" / "m.ipynb"
    nb_file.parent.mkdir(parents=True)
    nb_file.write_text(nb_text)
    sj = str(tmp_path / "src.jsonl")
    r = flip_module(sj, str(repos), "cjm-demo", "nbs/m.ipynb")
    assert r["captured"], r
    # flip_module journals the CANONICAL notebook; align the file so cutover's guard passes
    nb_file.write_text(latest_source_ops(sj)[("cjm-demo", "nbs/m.ipynb")]["text"])
    c = cutover_module(sj, str(repos), "cjm-demo", "nbs/m.ipynb")
    assert c["cut_over"], c
    return sj, repos, nb_file


def test_retire_ends_a_source_key_and_source_check_forgets_it(tmp_path):
    sj, repos, nb_file = _journal_a_notebook(tmp_path)
    key = ("cjm-demo", "nbs/m.ipynb")
    assert key in latest_source_ops(sj) and key in graph_sourced_modules(sj)
    assert append_retire(sj, "cjm-demo", "nbs/m.ipynb", superseded_by="cjm_demo/m.py")
    assert key not in latest_source_ops(sj)
    assert key not in graph_sourced_modules(sj)
    nb_file.unlink()  # the flipped-away notebook is deleted...
    chk = source_check(sj, str(repos))
    assert chk["count"] == 0  # ...and source-check no longer holds the deleted file


def test_retire_noops_on_an_unknown_key(tmp_path):
    sj = str(tmp_path / "src.jsonl")
    assert append_retire(sj, "cjm-demo", "nbs/ghost.ipynb") is False


def test_a_later_source_op_revives_a_retired_key(tmp_path):
    sj, repos, nb_file = _journal_a_notebook(tmp_path)
    append_retire(sj, "cjm-demo", "nbs/m.ipynb")
    r = flip_module(sj, str(repos), "cjm-demo", "nbs/m.ipynb")  # generic supersession
    assert r["captured"]
    assert ("cjm-demo", "nbs/m.ipynb") in latest_source_ops(sj)


# ---------------------------------------------------------------- the pure transform

def test_notebook_to_py_source_keeps_exports_and_reports_the_rest():
    built = notebook_to_py_source(DEMO_NB, docstring="The demo module.")
    text = built["text"]
    assert text.startswith('"""The demo module."""\n\n')
    assert "def f():" in text and "import os" in text
    assert "#|" not in text                       # directives stripped ANYWHERE (mid-cell too)
    assert "__all__" not in text
    assert built["dropped_all_dunder"] == 1
    assert "X = 1" in text                        # the __all__ cell's other code survives
    assert built["default_exp"] == "m"
    assert built["export_cells"] == 3
    assert [c["cell_key"] for c in built["markdown_cells"]] == ["c1"]
    assert [c["cell_key"] for c in built["nonexport_code_cells"]] == ["c4"]


def test_notebook_to_py_source_reports_a_nonexporting_notebook():
    built = notebook_to_py_source(_nb([("markdown", "c0", "# prose only")]))
    assert built["default_exp"] is None and built["text"] == ""


# ---------------------------------------------------------------- the orchestrator

class FakeGraph:
    """In-memory stand-in for the graph ops the flip verb touches."""

    def __init__(self, nodes):
        self.nodes = {n["id"]: n for n in nodes}
        self.edges = []
        self.deleted = []

    async def task(self, queue, graph_id, op, **kw):
        if op == "find_nodes_by_label":
            return [n for n in self.nodes.values() if n["label"] == kw["label"]]
        if op == "get_node":
            return self.nodes.get(kw["node_id"])
        if op == "delete_nodes":
            self.deleted.extend(kw["node_ids"])
            for i in kw["node_ids"]:
                self.nodes.pop(i, None)
            return True
        raise AssertionError(f"unexpected graph op {op}")


MID = code_module_node_id("cjm-demo", "cjm_demo/m.py")


def _notebook_graph():
    """The pre-flip graph shape: module + Cells + a body-less symbol keyed to its cell."""
    return FakeGraph([
        {"id": MID, "label": "CodeModule",
         "properties": {"repo_key": "cjm-demo", "module_path": "cjm_demo/m.py",
                        "import_name": "cjm_demo.m"}},
        {"id": "cell-c1", "label": "Cell",
         "properties": {"module_id": MID, "cell_key": "c1", "cell_type": "markdown"}},
        {"id": "cell-c3", "label": "Cell",
         "properties": {"module_id": MID, "cell_key": "c3", "cell_type": "code"}},
        {"id": "cell-c4", "label": "Cell",
         "properties": {"module_id": MID, "cell_key": "c4", "cell_type": "code"}},
        {"id": "sym-f", "label": "CodeSymbol",
         "properties": {"module_id": MID, "cell_key": "c3", "qualname": "f", "name": "f"}},
    ])


def _wire(fake, monkeypatch, link_calls=None, extended=None):
    monkeypatch.setattr(module_ops_mod, "graph_task", fake.task)
    monkeypatch.setattr(factlayer_mod, "graph_task", fake.task)

    async def fake_extend(queue, graph_id, nodes, edges):
        if extended is not None:
            extended.append((nodes, edges))
        for n in nodes:
            fake.nodes[n["id"]] = n
        fake.edges.extend(edges)
        return SimpleNamespace(nodes_added=len(nodes), edges_added=len(edges))

    async def fake_link(gx, source_id, target_id, relation, actor="agent:session"):
        if link_calls is not None:
            link_calls.append({"source_id": source_id, "target_id": target_id,
                               "relation": relation, "actor": actor})
        return {"source_id": source_id, "target_id": target_id, "relation": relation,
                "written": True, "source_label": "S", "target_label": "T"}

    monkeypatch.setattr(module_ops_mod, "extend_graph", fake_extend)
    monkeypatch.setattr(module_ops_mod, "link", fake_link)


def _run(coro):
    return asyncio.run(coro)


def test_flip_to_py_end_to_end(tmp_path, monkeypatch):
    sj, repos, nb_file = _journal_a_notebook(tmp_path)
    wj = str(tmp_path / "writes.jsonl")
    fake = _notebook_graph()
    extended = []
    _wire(fake, monkeypatch, extended=extended)

    res = _run(flip_notebook_to_py(GX, sj, wj, str(repos), "cjm-demo", "nbs/m.ipynb",
                                   docstring="The demo module."))
    assert not res.get("error"), res
    assert res["written"] and res["cut_over"] and res["retired"]
    # journal: the .py key is graph-sourced, the .ipynb key is GONE
    assert ("cjm-demo", "cjm_demo/m.py") in graph_sourced_modules(sj)
    assert ("cjm-demo", "nbs/m.ipynb") not in latest_source_ops(sj)
    # artifact == journaled canonical state; the notebook file is deleted
    py = repos / "cjm-demo" / "cjm_demo" / "m.py"
    assert py.read_text() == latest_source_ops(sj)[("cjm-demo", "cjm_demo/m.py")]["text"]
    assert py.read_text().startswith('"""The demo module."""')
    assert "__all__" not in py.read_text()
    assert not nb_file.exists()
    assert res["notebook_deleted"]
    # loud report: dropped cells enumerated, dead import pruned
    assert [c["cell_key"] for c in res["markdown_cells_dropped"]] == ["c1"]
    assert [c["cell_key"] for c in res["nonexport_code_cells_dropped"]] == ["c4"]
    assert res["pruned_imports"] == ["Path"]  # imported, referenced nowhere
    # graph swap: Cells out, plain regions in (same module id)
    assert "cell-c3" in fake.deleted and MID in fake.deleted
    assert extended and any(n["label"] == "CodeSymbol" for n in extended[0][0])
    # the whole walk soaks clean, regen gate included
    chk = source_check(sj, str(repos))
    assert chk["clean"] and chk["regen_clean"] and chk["count"] == 1


def test_flip_refuses_an_unsourced_notebook(tmp_path, monkeypatch):
    repos = tmp_path / "repos"
    (repos / "cjm-demo" / "nbs").mkdir(parents=True)
    sj, wj = str(tmp_path / "src.jsonl"), str(tmp_path / "writes.jsonl")
    _wire(_notebook_graph(), monkeypatch)
    res = _run(flip_notebook_to_py(GX, sj, wj, str(repos), "cjm-demo", "nbs/m.ipynb"))
    assert "no live journaled source state" in res["error"]


def test_flip_refuses_a_nonexporting_notebook(tmp_path, monkeypatch):
    nb = _nb([("markdown", "c0", "# prose only"), ("code", "c1", "print('scratch')")])
    sj, repos, _ = _journal_a_notebook(tmp_path, nb_text=nb)
    wj = str(tmp_path / "writes.jsonl")
    _wire(_notebook_graph(), monkeypatch)
    res = _run(flip_notebook_to_py(GX, sj, wj, str(repos), "cjm-demo", "nbs/m.ipynb"))
    assert "exports nothing" in res["error"]


def test_flip_dry_run_touches_nothing(tmp_path, monkeypatch):
    sj, repos, nb_file = _journal_a_notebook(tmp_path)
    wj = str(tmp_path / "writes.jsonl")
    fake = _notebook_graph()
    _wire(fake, monkeypatch)
    res = _run(flip_notebook_to_py(GX, sj, wj, str(repos), "cjm-demo", "nbs/m.ipynb",
                                   write=False))
    assert not res.get("error") and not res["written"]
    assert nb_file.exists() and not fake.deleted
    assert ("cjm-demo", "nbs/m.ipynb") in graph_sourced_modules(sj)
    assert not (repos / "cjm-demo" / "cjm_demo" / "m.py").exists()


def test_flip_blocks_on_an_unretargetable_cell_ref_and_force_drops_loudly(
        tmp_path, monkeypatch):
    sj, repos, nb_file = _journal_a_notebook(tmp_path)
    wj = str(tmp_path / "writes.jsonl")
    # a journaled assert SUBJECT on a markdown cell — no surviving symbol, no retarget
    Path(wj).write_text(json.dumps(
        {"verb": "assert", "ts": 0,
         "args": {"subject": "cell-c1", "predicate": "p", "value": "v"}}) + "\n")
    fake = _notebook_graph()
    _wire(fake, monkeypatch)
    res = _run(flip_notebook_to_py(GX, sj, wj, str(repos), "cjm-demo", "nbs/m.ipynb"))
    assert "re-home them first" in res["error"]
    assert res["cell_ref_blockers"][0]["cell_id"] == "cell-c1"
    assert nb_file.exists()  # refused = untouched

    res = _run(flip_notebook_to_py(GX, sj, wj, str(repos), "cjm-demo", "nbs/m.ipynb",
                                   force_drop_cell_refs=True))
    assert not res.get("error"), res
    assert res["cell_refs_dropped"][0]["cell_id"] == "cell-c1"
    assert not nb_file.exists()


def test_flip_retargets_a_link_onto_the_surviving_symbol(tmp_path, monkeypatch):
    sj, repos, _ = _journal_a_notebook(tmp_path)
    wj = str(tmp_path / "writes.jsonl")
    # a Decision SHAPES edge onto the def cell — content survives as symbol `f`
    Path(wj).write_text(json.dumps(
        {"verb": "link", "ts": 0,
         "args": {"source_id": "dec-1", "target_id": "cell-c3",
                  "relation": "SHAPES", "actor": "human"}}) + "\n")
    fake = _notebook_graph()
    link_calls = []
    _wire(fake, monkeypatch, link_calls=link_calls)
    res = _run(flip_notebook_to_py(GX, sj, wj, str(repos), "cjm-demo", "nbs/m.ipynb"))
    assert not res.get("error"), res
    assert res["cell_refs_retargeted"][0]["surviving_symbol"] == "f"
    assert link_calls == [{"source_id": "dec-1", "target_id": "sym-f",
                           "relation": "SHAPES", "actor": "human"}]
    ops = read_journal(wj)
    assert ops[-1]["verb"] == "link" and ops[-1]["args"]["target_id"] == "sym-f"
