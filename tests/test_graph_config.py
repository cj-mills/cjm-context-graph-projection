"""Graph-sibling config discovery + arg overlay (a1d965b0): the DEFAULT_*
hardcodes are fallback only — the config beside the addressed graph db names its
paths, explicit flags always win, and a corrupt config refuses loudly instead of
silently falling back. No key names a code inventory: that is the source journal's
(2cc81d3b, finding 7a2d54ae)."""
import argparse
import json

import pytest

from cjm_context_graph_projection.cli import (_apply_graph_config, DEFAULT_MEMORY,
                                              DEFAULT_REPOS)
from cjm_context_graph_projection.config import load_graph_config


def _args(db, **kw):
    ns = argparse.Namespace(graph_db_path=str(db))
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_absent_config_is_empty_fallback(tmp_path):
    assert load_graph_config(tmp_path / "g.db") == {}


def test_corrupt_config_refuses_loudly(tmp_path):
    (tmp_path / "graph.config.json").write_text("{not json")
    with pytest.raises(SystemExit, match="unreadable"):
        load_graph_config(tmp_path / "g.db")
    (tmp_path / "graph.config.json").write_text("[1, 2]")
    with pytest.raises(SystemExit, match="JSON object"):
        load_graph_config(tmp_path / "g.db")


def test_overlay_replaces_baked_defaults_only(tmp_path):
    (tmp_path / "graph.config.json").write_text(json.dumps(
        {"code_libs": ["repo-a", "repo-b"], "memory_dir": "/cfg/memory",
         "repos_dir": "/cfg/repos"}))
    db = tmp_path / "g.db"
    # Values still at their baked defaults are replaced by the config
    a = _args(db, code_lib=None, memory_dir=DEFAULT_MEMORY, repos_dir=DEFAULT_REPOS)
    _apply_graph_config(a)
    assert a.memory_dir == "/cfg/memory" and a.repos_dir == "/cfg/repos"
    assert a.code_lib is None  # a stale code_libs key overlays nothing: the journal is the inventory
    # Explicit flags win over the config
    b = _args(db, memory_dir="/explicit", repos_dir=DEFAULT_REPOS)
    _apply_graph_config(b)
    assert b.memory_dir == "/explicit" and b.repos_dir == "/cfg/repos"
    # Verbs without the attribute are untouched (no spurious attrs minted)
    c = _args(db)
    _apply_graph_config(c)
    assert not hasattr(c, "memory_dir")


def test_config_keys_absent_leave_args_alone(tmp_path):
    (tmp_path / "graph.config.json").write_text(json.dumps({"repos_dir": "/x"}))
    a = _args(tmp_path / "g.db", memory_dir=DEFAULT_MEMORY)
    _apply_graph_config(a)
    assert a.memory_dir == DEFAULT_MEMORY


def test_notes_corpus_and_profile_overlay(tmp_path):
    # 81a02642: the notes graph's corpus root + harvest profile are DATA in its own
    # sibling config, so `ingest-notes` runs with no --notes-corpus on a rebuild.
    (tmp_path / "graph.config.json").write_text(json.dumps(
        {"notes_corpus": "/cfg/posts", "notes_profile": "cfg_profile"}))
    a = _args(tmp_path / "g.db", notes_corpus=None, profile=None, emit_root=None)
    _apply_graph_config(a)
    assert a.notes_corpus == "/cfg/posts" and a.profile == "cfg_profile"
    assert a.emit_root is None                      # key absent -> untouched
    # Explicit flags win
    b = _args(tmp_path / "g.db", notes_corpus="/explicit", profile="mine")
    _apply_graph_config(b)
    assert b.notes_corpus == "/explicit" and b.profile == "mine"
    # The dev graph's config (no notes keys) leaves the attrs alone
    (tmp_path / "graph.config.json").write_text(json.dumps({"repos_dir": "/x"}))
    c = _args(tmp_path / "g.db", notes_corpus=None, profile="quarto_post")
    _apply_graph_config(c)
    assert c.notes_corpus is None and c.profile == "quarto_post"
