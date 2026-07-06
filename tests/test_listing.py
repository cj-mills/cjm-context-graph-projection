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
    assert "Edges · `GATED_BY` (1 — window; page with --offset)" in out
    assert "**Arc** → **M3 flip**" in out and "`a` → `b`" in out


def test_render_list_error_and_empty():
    assert "⚠" in render("list", {"error": "pass exactly one of --label / --predicate / --relation"}, "human")
    empty = render("list", {"mode": "label", "key": "Ghost", "rows": [], "count": 0, "truncated": False}, "human")
    assert "_(none)_" in empty


def test_parse_where_and_true_total_render():
    # --where parses PROP=VALUE (eq, ANDed); malformed clauses fail loudly.
    from cjm_context_graph_projection.listing import parse_where
    preds, err = parse_where(["note_type=feedback", "payload.kind=x"])
    assert err is None and [(p.prop, p.op, p.value) for p in preds] == [
        ("note_type", "eq", "feedback"), ("payload.kind", "eq", "x")]
    assert parse_where(["oops"])[1] and "PROP=VALUE" in parse_where(["oops"])[1]
    # --where is label-mode only.
    res = asyncio.run(list_graph(None, predicate="task_state", where=["a=b"]))
    assert "label mode only" in res["error"]
    # The render reports the TRUE total ("shown of total") + the active filter badge.
    obj = {"mode": "label", "key": "Note",
           "rows": [{"id": "n1", "title": "A note", "path": None}],
           "count": 1, "total": 40, "truncated": True,
           "where": [{"prop": "note_type", "op": "eq", "value": "feedback"}]}
    out = render("list", obj, "human")
    assert "(1 of 40 — window; page with --offset)" in out
    assert "[note_type=feedback]" in out
