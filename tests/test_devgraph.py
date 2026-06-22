"""Repo-map extraction: pyproject dep parsing + Entity nodes / DEPENDS_ON edges."""

from cjm_dev_graph_schema.identity import entity_node_id
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations
from cjm_context_graph_projection.devgraph import _cjm_dep_keys, repo_map_elements

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
