"""Structured enumeration: the `list` mode-selection guard + its render (no graph needed)."""

import asyncio

from cjm_context_graph_projection.listing import list_graph
from cjm_context_graph_projection.render import render


def test_list_graph_requires_exactly_one_mode():
    # Zero modes -> error (before any graph call, so a dummy handle is fine).
    assert asyncio.run(list_graph(None)).get("error")
    # Two modes -> error naming what was given.
    res = asyncio.run(list_graph(None, label="Decision", predicate="task_state"))
    assert res.get("error") and set(res["given"]) == {"label", "predicate"}


def test_render_list_label_mode_with_paths():
    obj = {"mode": "label", "key": "CodeModule",
           "rows": [{"id": "m1", "title": "pkg/readiness.py", "path": "/abs/pkg/readiness.py"},
                    {"id": "m2", "title": "pkg/cli.py", "path": None}],
           "count": 2, "truncated": False}
    out = render("list", obj, "human")
    assert "Nodes · `CodeModule` (2)" in out
    assert "pkg/readiness.py" in out and "📄 `/abs/pkg/readiness.py`" in out


def test_render_list_predicate_mode_shows_subject_value_actor():
    obj = {"mode": "predicate", "key": "task_state",
           "rows": [{"subject_id": "d1", "subject": "Ship the spine", "value": "done",
                     "actor": "agent:session"}],
           "count": 1, "truncated": False}
    out = render("list", obj, "human")
    assert "Assertions · `task_state` (1)" in out
    assert "**Ship the spine** = _done_" in out and "agent:session" in out


def test_render_list_relation_mode_shows_src_target():
    obj = {"mode": "relation", "key": "GATED_BY",
           "rows": [{"source_id": "a", "source": "Arc", "target_id": "b", "target": "M3 flip"}],
           "count": 1, "truncated": True}
    out = render("list", obj, "human")
    assert "Edges · `GATED_BY` (1 — truncated)" in out
    assert "**Arc** → **M3 flip**" in out and "`a` → `b`" in out


def test_render_list_error_and_empty():
    assert "⚠" in render("list", {"error": "pass exactly one of --label / --predicate / --relation"}, "human")
    empty = render("list", {"mode": "label", "key": "Ghost", "rows": [], "count": 0, "truncated": False}, "human")
    assert "_(none)_" in empty
