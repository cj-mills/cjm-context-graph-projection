"""Repo-map extraction: pyproject dep parsing + Entity nodes / DEPENDS_ON edges."""

import json

from cjm_dev_graph_schema.identity import code_module_node_id, entity_node_id
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations
from cjm_context_graph_projection.devgraph import (_cjm_dep_keys, notebook_elements,
                                                   repo_map_elements)
from cjm_context_graph_projection.seeds import conceptual_key

PYPROJECT = """\
[project]
name = "cjm-bar"
dependencies = ['cjm-foo>=0.0.1', "cjm-context-graph-layer>=0.0.7", 'numpy>=1.0', 'cjm-baz']
"""


def _make_repo(root, name, deps_toml):
    d = root / name
    d.mkdir()
    (d / "pyproject.toml").write_text(deps_toml)
    return d


def test_cjm_dep_keys_strips_specifiers_and_filters(tmp_path):
    py = tmp_path / "pyproject.toml"
    py.write_text(PYPROJECT)
    keys = _cjm_dep_keys(py)
    assert keys == ["cjm-foo", "cjm-context-graph-layer", "cjm-baz"]  # numpy filtered out


def test_repo_map_elements_entities_and_depends_on(tmp_path):
    _make_repo(tmp_path, "cjm-foo", '[project]\nname = "cjm-foo"\ndependencies = []\n')
    _make_repo(tmp_path, "cjm-bar", PYPROJECT)
    (tmp_path / "not-a-cjm-repo").mkdir()  # ignored
    nodes, edges = repo_map_elements(str(tmp_path))

    assert {n["properties"]["key"] for n in nodes} == {"cjm-foo", "cjm-bar"}
    assert all(n["label"] == DevNodeKinds.ENTITY for n in nodes)
    # cjm-bar DEPENDS_ON cjm-foo (and the layer + baz); self-dep excluded.
    bar_id = entity_node_id("repo", "cjm-bar")
    dep_edges = [e for e in edges if e["source_id"] == bar_id]
    assert all(e["relation_type"] == DevRelations.DEPENDS_ON for e in dep_edges)
    assert entity_node_id("repo", "cjm-foo") in {e["target_id"] for e in dep_edges}


def _make_nbdev_repo(root, name="cjm-foo"):
    """A minimal nbdev-style repo: nbs/00_core.ipynb exporting to <pkg>/core.py."""
    d = root / name
    (d / "nbs").mkdir(parents=True)
    cells = [
        {"cell_type": "code", "id": "c0", "source": "#| default_exp core\n"},
        {"cell_type": "markdown", "id": "c1", "source": "# Core\n\nThe `alpha` helper.\n"},
        {"cell_type": "code", "id": "c2", "source": "#| export\ndef alpha(x):\n    return x + 1\n"},
    ]
    (d / "nbs" / "00_core.ipynb").write_text(
        json.dumps({"cells": cells, "metadata": {}, "nbformat": 4, "nbformat_minor": 5}))
    (d / "nbs" / ".ipynb_checkpoints").mkdir()
    (d / "nbs" / ".ipynb_checkpoints" / "skip.ipynb").write_text("{ not json")  # must be skipped
    return d


def test_notebook_elements_decomposes_repo_notebooks(tmp_path):
    d = _make_nbdev_repo(tmp_path, "cjm-foo")
    nodes, edges = notebook_elements([str(d)])

    labels = {n["label"] for n in nodes}
    assert DevNodeKinds.CODE_MODULE in labels and DevNodeKinds.CELL in labels
    # notebook 00_core.ipynb (default_exp core) -> module cjm_foo/core.py (the export target)
    mod_id = code_module_node_id(conceptual_key("cjm-foo"), "cjm_foo/core.py")
    assert any(n["id"] == mod_id for n in nodes)
    # the export cell yields a CodeSymbol; the checkpoints notebook was skipped (no crash)
    syms = [n for n in nodes if n["label"] == DevNodeKinds.CODE_SYMBOL]
    assert any(n["properties"]["qualname"] == "alpha" for n in syms)
    assert any(e["relation_type"] == DevRelations.CONTAINS for e in edges)
