"""Repo-map extraction: pyproject dep parsing + Entity nodes / DEPENDS_ON edges."""

import json

from cjm_dev_graph_schema.identity import (code_module_node_id, entity_node_id,
                                           note_node_id, series_node_id, topic_node_id)
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations
from cjm_context_graph_projection.devgraph import (_cjm_dep_keys, notebook_elements,
                                                   notes_corpus_elements, repo_map_elements)
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


def _post(root, slug, body, categories=None, series_link=None):
    d = root / slug
    d.mkdir()
    fm = ["---", f'title: "{slug}"', "date: 2024-1-1"]
    if categories:
        fm.append(f"categories: [{', '.join(categories)}]")
    fm.append("---")
    lines = list(fm) + ["# Overview", "", body]  # a heading -> one Section node
    if series_link:
        lines.append(f"Part of the [series]({series_link}).")
    (d / "index.md").write_text("\n".join(lines) + "\n")


def test_notes_corpus_elements_permalink_identity_and_facets(tmp_path):
    posts = tmp_path / "posts"
    posts.mkdir()
    _post(posts, "the-learning-game-book-notes", "Body.", categories=["education", "history"],
          series_link="/series/notes/education-notes.html")
    _post(posts, "dumbing-us-down-book-notes",
          "See [other](/posts/the-learning-game-book-notes/).",
          categories=["education", "history"], series_link="/series/notes/education-notes.html")

    nodes, edges = notes_corpus_elements(str(posts))
    labels = [n["label"] for n in nodes]
    # Permalink identity: each post is its own Note (no `index` collision).
    note_ids = {n["id"] for n in nodes if n["label"] == DevNodeKinds.NOTE}
    assert note_node_id("the-learning-game-book-notes") in note_ids
    assert note_node_id("dumbing-us-down-book-notes") in note_ids
    # Shared facets deduped: 2 Topics + 1 Series across the two posts.
    assert labels.count(DevNodeKinds.TOPIC) == 2
    assert labels.count(DevNodeKinds.SERIES) == 1
    # Both converge on the education Topic + the series; cross-post REFERENCES present.
    in_series = [e for e in edges if e["relation_type"] == DevRelations.IN_SERIES]
    assert {e["target_id"] for e in in_series} == {series_node_id("education-notes")}
    assert any(e["relation_type"] == DevRelations.TAGGED
               and e["target_id"] == topic_node_id("education") for e in edges)
    assert any(e["relation_type"] == DevRelations.REFERENCES
               and e["properties"].get("cross_post") for e in edges)
    # The notes corpus decomposes bodies into Section nodes (opt-in, posts only).
    assert labels.count(DevNodeKinds.SECTION) >= 1
    assert any(e["relation_type"] == DevRelations.HAS_SECTION for e in edges)


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


def test_code_elements_ingests_graph_sourced_modules_from_the_journal(tmp_path):
    """N+3 Phase 2: a cut-over module's text comes from the SOURCE journal, not the file
    (the authority flip — the code analogue of skip_memory_paths); a missing artifact
    file still ingests (the journal is sufficient)."""
    from cjm_context_graph_projection.devgraph import code_elements
    from cjm_context_graph_projection.source_state import cutover_module, flip_module

    repo = tmp_path / "cjm-demo-lib"
    pkg = repo / "cjm_demo_lib"
    pkg.mkdir(parents=True)
    (pkg / "a.py").write_text('"""A."""\n\n\ndef fa():\n    return 1\n')
    (pkg / "b.py").write_text('"""B."""\n\n\ndef fb():\n    return 2\n')
    j = str(tmp_path / "source.jsonl")
    key = conceptual_key(repo.name)
    flip_module(j, str(tmp_path), key, "cjm_demo_lib/a.py")
    cutover_module(j, str(tmp_path), key, "cjm_demo_lib/a.py")

    # Post-cutover, an out-of-band file edit must NOT reach the graph — journal wins.
    (pkg / "a.py").write_text('"""A."""\n\n\ndef fa():\n    return 999  # stray\n')
    nodes, _ = code_elements([str(repo)], source_journal_path=j)
    fa = next(n for n in nodes if n["label"] == "CodeSymbol" and n["properties"]["name"] == "fa")
    assert "999" not in fa["properties"]["body"]
    fb = next(n for n in nodes if n["label"] == "CodeSymbol" and n["properties"]["name"] == "fb")
    assert "return 2" in fb["properties"]["body"]  # un-flipped modules read from disk

    # The artifact file deleted entirely: the module still ingests from the journal.
    (pkg / "a.py").unlink()
    nodes, _ = code_elements([str(repo)], source_journal_path=j)
    assert any(n["label"] == "CodeSymbol" and n["properties"]["name"] == "fa" for n in nodes)


def test_notebook_elements_scans_only_nbs_when_present(tmp_path):
    """quarto's `_proc` copies (and `dist/` etc.) share export targets with the real
    notebooks — scanning them ingests duplicate module identities. `nbs/` is the source."""
    d = _make_nbdev_repo(tmp_path, "cjm-foo")
    (d / "_proc").mkdir()
    (d / "_proc" / "00_core.ipynb").write_text((d / "nbs" / "00_core.ipynb").read_text())
    nodes, _ = notebook_elements([str(d)])
    mods = [n for n in nodes if n["label"] == DevNodeKinds.CODE_MODULE]
    assert len(mods) == 1  # the _proc duplicate was not scanned
    assert mods[0]["properties"]["path"].endswith("nbs/00_core.ipynb")


def test_notebook_elements_ingests_graph_sourced_notebooks_from_the_journal(tmp_path):
    """The notebook authority flip: a cut-over notebook's cells come from the SOURCE
    journal, not the file; a missing artifact file still ingests."""
    from cjm_context_graph_projection.source_state import (cutover_module,
                                                           emit_source_artifact, flip_module)

    d = _make_nbdev_repo(tmp_path, "cjm-foo")
    j = str(tmp_path / "source.jsonl")
    key = conceptual_key("cjm-foo")
    flip_module(j, str(tmp_path), key, "nbs/00_core.ipynb")
    emit_source_artifact(j, str(tmp_path), key, "nbs/00_core.ipynb")  # canonicalize the file
    assert cutover_module(j, str(tmp_path), key, "nbs/00_core.ipynb")["cut_over"]

    # Post-cutover, an out-of-band file edit must NOT reach the graph — journal wins.
    nb_file = d / "nbs" / "00_core.ipynb"
    nb_file.write_text(nb_file.read_text().replace("x + 1", "x + 999"))
    nodes, _ = notebook_elements([str(d)], source_journal_path=j)
    cells = [n for n in nodes if n["label"] == DevNodeKinds.CELL]
    assert cells and not any("999" in n["properties"]["source"] for n in cells)

    # The artifact file deleted entirely: the notebook still ingests from the journal.
    nb_file.unlink()
    nodes, _ = notebook_elements([str(d)], source_journal_path=j)
    assert any(n["label"] == DevNodeKinds.CELL for n in nodes)
    mod_id = code_module_node_id(key, "cjm_foo/core.py")
    assert any(n["id"] == mod_id for n in nodes)
