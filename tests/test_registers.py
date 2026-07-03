"""Register drift-check: pure reconciliation (classify) + its render (no graph needed)."""

from cjm_context_graph_projection.registers import classify_register_drift
from cjm_context_graph_projection.render import render


def test_classify_in_sync_register_reports_no_drift():
    out = classify_register_drift(
        active_members={"application": {"a", "b"}},
        historic_members={"application": {"a", "b"}},
        hub_refs={"application": {"a", "b", "strategy-note"}},
        hubs={"application": "hub-1"})
    r = out["registers"][0]
    assert r["members"] == 2 and r["cached"] == 2
    assert r["missing_cache"] == [] and r["stale_cache"] == []


def test_classify_active_member_without_cache_edge_is_missing():
    out = classify_register_drift(
        active_members={"rule": {"a", "b"}},
        historic_members={"rule": {"a", "b"}},
        hub_refs={"rule": {"a"}},
        hubs={"rule": "hub-1"})
    assert out["registers"][0]["missing_cache"] == ["b"]


def test_classify_superseded_member_still_cached_is_stale():
    # `b` once carried the role (historic) but no longer does — its lingering
    # cache link is genuine rot.
    out = classify_register_drift(
        active_members={"rule": {"a"}},
        historic_members={"rule": {"a", "b"}},
        hub_refs={"rule": {"a", "b"}},
        hubs={"rule": "hub-1"})
    assert out["registers"][0]["stale_cache"] == ["b"]


def test_classify_contextual_reference_is_not_drift():
    # The hub cites strategy prose that never carried the role — deliberately ignored.
    out = classify_register_drift(
        active_members={"rule": {"a"}},
        historic_members={"rule": {"a"}},
        hub_refs={"rule": {"a", "strategy-note"}},
        hubs={"rule": "hub-1"})
    r = out["registers"][0]
    assert r["stale_cache"] == [] and r["missing_cache"] == []


def test_classify_role_value_without_hub_is_hubless_counts_only():
    out = classify_register_drift(
        active_members={"north-star": {"a", "b", "c"}},
        historic_members={"north-star": {"a", "b", "c"}},
        hub_refs={}, hubs={})
    assert out["registers"] == []
    assert out["hubless"] == [{"value": "north-star", "members": 3}]


def test_render_register_drift_marks_sync_and_names_drift():
    obj = {"registers": [
               {"value": "application", "hub_id": "h1", "hub_label": "Application Register",
                "members": 5, "cached": 5, "missing_cache": [], "stale_cache": []},
               {"value": "rule", "hub_id": "h2", "hub_label": "Rule Register",
                "members": 3, "cached": 2,
                "missing_cache": [{"id": "m1", "label": "A New Rule"}],
                "stale_cache": [{"id": "s1", "label": "A Retired Rule"}]}],
           "hubless": [{"value": "north-star", "members": 9}],
           "counts": {"registers": 2, "in_sync": 1, "drifting": 1, "hubless": 1}}
    out = render("register-drift", obj, "human")
    assert "registers 2 · in-sync 1 · drifting 1 · hubless 1" in out
    assert "✓ **Application Register**" in out
    assert "✗ **Rule Register**" in out
    assert "missing from cache: _A New Rule_ `m1`" in out
    assert "stale cache link: _A Retired Rule_ `s1`" in out
    assert "◌ `role=north-star`: 9 member(s)" in out


def test_render_register_drift_empty():
    out = render("register-drift", {"registers": [], "hubless": [], "counts": {}}, "human")
    assert "nothing to reconcile" in out
