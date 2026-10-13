"""The off-nbdev step in the element regime (notebook FILE -> a plain .py module born captured, ONE
LOUD VERB; design detail 25c1fadc): retire-op journal semantics, the pure cell->module transform,
and the orchestrator."""

import asyncio
import json
from types import SimpleNamespace

import cjm_context_graph_projection.module_ops as module_ops_mod
from cjm_context_graph_projection.codefold import fold_source_journal
from cjm_context_graph_projection.module_ops import flip_notebook_to_py
from cjm_context_graph_projection.source_state import (append_retire, flip_module,
                                                       latest_source_ops, notebook_to_py_source,
                                                       read_source_journal, source_check)

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

def _born(tmp_path, text="def f():\n    return 1\n"):
    """A module born captured from its file."""
    repos = tmp_path / "repos"
    f = repos / "cjm-demo" / "cjm_demo" / "m.py"
    f.parent.mkdir(parents=True)
    f.write_text(text)
    sj = str(tmp_path / "src.jsonl")
    assert flip_module(sj, str(repos), "cjm-demo", "cjm_demo/m.py")["captured"]
    return sj, repos, f


def test_retire_ends_a_source_key_and_source_check_forgets_it(tmp_path):
    sj, repos, f = _born(tmp_path)
    key = ("cjm-demo", "cjm_demo/m.py")
    assert key in latest_source_ops(sj)
    assert append_retire(sj, "cjm-demo", "cjm_demo/m.py")
    assert key not in latest_source_ops(sj)
    f.unlink()  # the deleted module's file goes...
    assert source_check(sj, str(repos))["count"] == 0  # ...and source-check no longer holds it


def test_retire_noops_on_an_unknown_key(tmp_path):
    sj = str(tmp_path / "src.jsonl")
    assert append_retire(sj, "cjm-demo", "cjm_demo/ghost.py") is False


def test_a_retired_path_is_born_again_captured(tmp_path):
    sj, repos, f = _born(tmp_path)
    append_retire(sj, "cjm-demo", "cjm_demo/m.py")
    assert flip_module(sj, str(repos), "cjm-demo", "cjm_demo/m.py")["captured"]
    assert ("cjm-demo", "cjm_demo/m.py") in latest_source_ops(sj)
    assert [r["verb"] for r in read_source_journal(sj)].count("capture") == 2
    fold = fold_source_journal(sj, str(repos))  # the graph holds the reborn module and its symbol
    nodes, _ = fold.elements()
    assert not fold.failures
    assert [n["properties"]["qualname"] for n in nodes if n["label"] == "CodeSymbol"] == ["f"]


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

def _wire(monkeypatch, stepped=None):
    async def fake_apply_live(gx, source_journal_path, repos_dir, appended):
        # The code fold's step is proven against a real store in test_relive; here the
        # flip must hand it exactly the records it appended.
        if stepped is not None:
            stepped.append(list(appended))
        return {"records": len(appended)}

    monkeypatch.setattr(module_ops_mod, "apply_live", fake_apply_live)


def _notebook(tmp_path, nb_text=DEMO_NB):
    repos = tmp_path / "repos"
    nb_file = repos / "cjm-demo" / "nbs" / "m.ipynb"
    nb_file.parent.mkdir(parents=True)
    nb_file.write_text(nb_text)
    return str(tmp_path / "src.jsonl"), repos, nb_file


def _run(coro):
    return asyncio.run(coro)


def test_flip_to_py_births_the_module_captured_from_the_notebook_file(tmp_path, monkeypatch):
    sj, repos, nb_file = _notebook(tmp_path)
    stepped = []
    _wire(monkeypatch, stepped=stepped)
    res = _run(flip_notebook_to_py(GX, sj, str(repos), "cjm-demo", "nbs/m.ipynb",
                                   docstring="The demo module."))
    assert not res.get("error"), res
    assert res["written"] and res["notebook_deleted"] and not nb_file.exists()
    # the journal: the repo's register, then the module's capture group -- the notebook never entered it
    records = read_source_journal(sj)
    assert records[0]["verb"] == "register" and records[1]["verb"] == "module"
    assert records[-1]["verb"] == "capture"
    assert not any(r["args"].get("module_path", "").endswith(".ipynb") for r in records)
    assert stepped == [records]  # the live step is handed exactly the flip's records
    # the artifact is the composition of its elements
    py = repos / "cjm-demo" / "cjm_demo" / "m.py"
    assert py.read_text() == latest_source_ops(sj)[("cjm-demo", "cjm_demo/m.py")]["text"]
    assert py.read_text().startswith('"""The demo module."""')
    assert "__all__" not in py.read_text() and "def f():" in py.read_text()
    assert "from pathlib import Path" in py.read_text()  # never claimed: kept on the imports element
    # loud report: dropped cells enumerated
    assert [c["cell_key"] for c in res["markdown_cells_dropped"]] == ["c1"]
    assert [c["cell_key"] for c in res["nonexport_code_cells_dropped"]] == ["c4"]
    chk = source_check(sj, str(repos))
    assert chk["clean"] and chk["regen_clean"] and chk["count"] == 1


def test_flip_refuses_a_missing_or_nonexporting_notebook(tmp_path, monkeypatch):
    _wire(monkeypatch)
    sj = str(tmp_path / "src.jsonl")
    res = _run(flip_notebook_to_py(GX, sj, str(tmp_path / "repos"), "cjm-demo", "nbs/m.ipynb"))
    assert "no notebook" in res["error"]
    nb = _nb([("markdown", "c0", "# prose only"), ("code", "c1", "print('scratch')")])
    sj, repos, _ = _notebook(tmp_path, nb_text=nb)
    res = _run(flip_notebook_to_py(GX, sj, str(repos), "cjm-demo", "nbs/m.ipynb"))
    assert "exports nothing" in res["error"]


def test_flip_refuses_a_module_already_on_disk_or_on_the_graph(tmp_path, monkeypatch):
    _wire(monkeypatch)
    sj, repos, nb_file = _notebook(tmp_path)
    py = repos / "cjm-demo" / "cjm_demo" / "m.py"
    py.parent.mkdir(parents=True)
    py.write_text("X = 1\n")
    res = _run(flip_notebook_to_py(GX, sj, str(repos), "cjm-demo", "nbs/m.ipynb"))
    assert "already exists" in res["error"] and nb_file.exists()
    assert flip_module(sj, str(repos), "cjm-demo", "cjm_demo/m.py")["captured"]
    py.unlink()
    res = _run(flip_notebook_to_py(GX, sj, str(repos), "cjm-demo", "nbs/m.ipynb"))
    assert "already holds" in res["error"] and nb_file.exists()


def test_flip_dry_run_touches_nothing(tmp_path, monkeypatch):
    sj, repos, nb_file = _notebook(tmp_path)
    _wire(monkeypatch)
    res = _run(flip_notebook_to_py(GX, sj, str(repos), "cjm-demo", "nbs/m.ipynb", write=False))
    assert not res.get("error") and not res["written"]
    assert nb_file.exists() and read_source_journal(sj) == []
    assert not (repos / "cjm-demo" / "cjm_demo" / "m.py").exists()
