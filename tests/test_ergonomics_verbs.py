"""Builds 1b109ddc + a1a48c70 + a6453f70 units: the decide --state validator and the
uncaptured-module audit (the CLI-side wiring is exercised live; these pin the logic)."""

import argparse

import pytest

from cjm_context_graph_projection.cli import _decide_state
from cjm_context_graph_projection.source_state import append_source, uncaptured_modules


def test_decide_state_open_passes():
    assert _decide_state("open") == "open"


def test_decide_state_done_names_the_assert_recipe():
    with pytest.raises(argparse.ArgumentTypeError) as e:
        _decide_state("done")
    msg = str(e.value)
    assert "task_state done" in msg and "assert" in msg and "--evidence" in msg


def test_uncaptured_modules_diffs_tree_against_journal(tmp_path, monkeypatch):
    import cjm_context_graph_projection.seeds as seeds_mod
    monkeypatch.setattr(seeds_mod, "repo_dir_name", lambda k: k)
    repo = tmp_path / "demo-repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "pkg/captured.py").write_text("X = 1\n")
    (repo / "pkg/loose.py").write_text("Y = 2\n")
    (repo / "tests/test_loose.py").write_text("def test():\n    pass\n")
    (repo / "runtime").mkdir()
    (repo / "runtime/junk.py").write_text("Z = 3\n")  # skipped dir
    (repo / "pkg/__pycache__").mkdir()
    (repo / "pkg/__pycache__/c.py").write_text("")     # skipped dir
    jp = str(tmp_path / "source.jsonl")
    append_source(jp, "demo-repo", "pkg/captured.py", "pkg.captured", "X = 1\n")
    rows = uncaptured_modules(jp, ["demo-repo", "absent-repo"], str(tmp_path))
    assert rows == {"demo-repo": ["pkg/loose.py", "tests/test_loose.py"]}
    append_source(jp, "demo-repo", "pkg/loose.py", "pkg.loose", "Y = 2\n")
    append_source(jp, "demo-repo", "tests/test_loose.py", "tests.test_loose", "def test():\n    pass\n")
    assert uncaptured_modules(jp, ["demo-repo"], str(tmp_path)) == {}
