"""The readiness frontier: pure derivation (classify) + its render (no graph needed)."""

from cjm_context_graph_projection.readiness import classify_readiness
from cjm_context_graph_projection.render import render


def test_classify_open_with_done_gate_is_ready():
    # The seed case: an open item whose only gate is done is READY (derived, not marked).
    parts = classify_readiness(
        task_state={"arc": "open", "m3": "done"},
        gates={"arc": ["m3"]})
    assert [r["id"] for r in parts["ready"]] == ["arc"]
    assert parts["blocked"] == []
    assert [d["id"] for d in parts["done"]] == ["m3"]


def test_classify_open_with_unmet_gate_is_blocked():
    parts = classify_readiness(
        task_state={"arc": "open", "m3": "open"},
        gates={"arc": ["m3"]})
    # m3 is open but ungated -> itself ready; arc is blocked on the still-open m3.
    assert [r["id"] for r in parts["ready"]] == ["m3"]
    assert parts["blocked"][0] == {"id": "arc", "blocked_by": ["m3"]}


def test_classify_ungated_open_item_is_ready():
    parts = classify_readiness(task_state={"a": "open"}, gates={})
    assert [r["id"] for r in parts["ready"]] == ["a"] and parts["ready"][0]["gates"] == []


def test_classify_gate_without_task_state_counts_as_unmet():
    # A gate pointing at a node with no `done` task_state is NOT silently satisfied —
    # absence reads as an unmet prerequisite (surfaces a mis-wired/standing block).
    parts = classify_readiness(task_state={"a": "open"}, gates={"a": ["ghost"]})
    assert parts["blocked"][0]["blocked_by"] == ["ghost"] and parts["ready"] == []


def test_classify_transitive_unlock_is_pure_recompute():
    # Nothing "fires": marking the deepest prerequisite done flips readiness on the
    # next computation with no cascade write — a -> b -> c chain.
    gates = {"a": ["b"], "b": ["c"]}
    blocked = classify_readiness({"a": "open", "b": "open", "c": "open"}, gates)
    # c has no gate entry -> ready; a,b blocked on a still-open prerequisite.
    assert [r["id"] for r in blocked["ready"]] == ["c"]
    assert {b["id"] for b in blocked["blocked"]} == {"a", "b"}
    # Close c -> b becomes ready (a still blocked on b); pure recompute, no stored unlock.
    step = classify_readiness({"a": "open", "b": "open", "c": "done"}, gates)
    assert [r["id"] for r in step["ready"]] == ["b"]
    assert [b["id"] for b in step["blocked"]] == ["a"]


def test_render_readiness_human_groups_and_flags_derived():
    obj = {"ready": [{"id": "arc", "label": "Dogfood arc",
                      "gates": [{"id": "m3", "label": "M3 flip"}]}],
           "blocked": [{"id": "viz", "label": "Viz",
                        "blocked_by": [{"id": "proj", "label": "Projector"}]}],
           "done": [{"id": "m3", "label": "M3 flip"}],
           "counts": {"ready": 1, "blocked": 1, "done": 1}}
    out = render("readiness", obj, "human")
    assert "Readiness frontier" in out and "DERIVED, never stored" in out
    assert "✅ **Dogfood arc**" in out and "all done" in out
    assert "⛔ **Viz**" in out and "needs _Projector_" in out
    assert "ready 1 · blocked 1 · done 1" in out


def test_render_readiness_caps_giant_decision_labels():
    # Work-items are often Decisions whose title is their whole statement; the line
    # must stay bounded (the house bounded-by-construction invariant).
    huge = "Z" * 4000
    obj = {"ready": [{"id": "d", "label": huge, "gates": [{"id": "g", "label": huge}]}],
           "blocked": [{"id": "b", "label": huge, "blocked_by": [{"id": "g2", "label": huge}]}],
           "done": [{"id": "e", "label": huge}],
           "counts": {"ready": 1, "blocked": 1, "done": 1}}
    out = render("readiness", obj, "human")
    assert max(len(line) for line in out.splitlines()) < 220  # no line blows the budget


def test_render_readiness_empty():
    out = render("readiness", {"ready": [], "blocked": [], "done": [],
                               "counts": {"ready": 0, "blocked": 0, "done": 0}}, "human")
    assert "no work-items" in out
