"""The readiness frontier: pure derivation (classify) + its render (no graph needed)."""

from cjm_context_graph_projection.readiness import classify_readiness, summarize_checks
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


def test_classify_hidden_check_satisfies_gate_but_never_partitions():
    # A Check carries task_state too: it must count toward gate satisfaction
    # (partial dependency on one aspect of another item) yet NEVER appear as a
    # frontier work-item itself.
    parts = classify_readiness(
        task_state={"arc": "open", "chk": "done"},
        gates={"arc": ["chk"]},
        hidden={"chk"})
    assert [r["id"] for r in parts["ready"]] == ["arc"]
    all_ids = {e["id"] for bucket in parts.values() for e in bucket}
    assert "chk" not in all_ids


def test_classify_hidden_open_check_still_blocks_a_gate():
    parts = classify_readiness(
        task_state={"arc": "open", "chk": "open"},
        gates={"arc": ["chk"]},
        hidden={"chk"})
    assert parts["blocked"][0] == {"id": "arc", "blocked_by": ["chk"]}
    assert parts["ready"] == []


def test_summarize_checks_counts_and_absence_is_open():
    # A check with no `done` task_state is open — absence is never satisfied
    # (the same absence rule as gates).
    dod = summarize_checks(
        task_state={"c1": "done", "c2": "open"},
        checks_of={"item": ["c1", "c2", "c3"]})
    assert dod["item"] == {"total": 3, "done": 1, "open": ["c2", "c3"]}


def test_summarize_checks_all_done_is_closable_shape():
    dod = summarize_checks(task_state={"c1": "done"}, checks_of={"item": ["c1"]})
    assert dod["item"]["open"] == [] and dod["item"]["done"] == dod["item"]["total"] == 1


def test_render_readiness_marks_closable_and_dod_progress():
    obj = {"ready": [{"id": "a", "label": "Item A", "gates": [],
                      "checks": {"done": 2, "total": 2}},
                     {"id": "b", "label": "Item B", "gates": [],
                      "checks": {"done": 1, "total": 3}}],
           "blocked": [], "done": [],
           "closable": [{"id": "a", "label": "Item A", "checks": {"done": 2, "total": 2}}],
           "drift": [],
           "counts": {"ready": 2, "blocked": 0, "done": 0, "closable": 1, "drift": 0}}
    out = render("readiness", obj, "human")
    assert "closable 1" in out
    assert "🏁 _DoD 2/2 met — closable_" in out
    assert "_[DoD 1/3]_" in out


def test_render_readiness_drift_section_names_open_checks():
    obj = {"ready": [], "blocked": [],
           "done": [{"id": "d", "label": "Item D", "checks": {"done": 1, "total": 2}}],
           "closable": [],
           "drift": [{"id": "d", "label": "Item D",
                      "open_checks": [{"id": "c2", "label": "replay stays byte-clean"}]}],
           "counts": {"ready": 0, "blocked": 0, "done": 1, "closable": 0, "drift": 1}}
    out = render("readiness", obj, "human")
    assert "DoD-drift 1" in out
    assert "DoD drift (marked done, checks still open):" in out
    assert "⚠ **Item D**" in out and "open check _replay stays byte-clean_ `c2`" in out


def test_render_readiness_without_checks_is_unchanged_shape():
    # No checks anywhere -> no DoD noise in the classic frontier.
    obj = {"ready": [{"id": "a", "label": "A", "gates": []}], "blocked": [], "done": [],
           "closable": [], "drift": [],
           "counts": {"ready": 1, "blocked": 0, "done": 0, "closable": 0, "drift": 0}}
    out = render("readiness", obj, "human")
    assert "DoD" not in out and "closable" not in out


def test_render_readiness_bounded_default_view():
    # 707327ea: the default view caps ready to a top-K, never enumerates Done
    # (counts stay TRUE totals), and points at the descend facets.
    obj = {
        "ready": [{"id": "r1", "label": "Item one", "gates": []},
                  {"id": "r2", "label": "Item two", "gates": []}],
        "blocked": [], "done": [], "closable": [], "drift": [],
        "counts": {"ready": 30, "blocked": 0, "done": 110, "closable": 0, "drift": 0},
        "view": {"state": "default", "limit": 2, "offset": 0, "shown_ready": 2},
    }
    out = render("readiness", obj, "human")
    assert "top 2 of 30 by last touch" in out
    assert "Done (110) not enumerated" in out and "--state" in out
    assert "Item one" in out and "Item two" in out
    # A paged --state done view labels the window against the true total.
    obj_done = {
        "ready": [], "blocked": [], "closable": [], "drift": [],
        "done": [{"id": "d1", "label": "Closed thing"}],
        "counts": {"ready": 30, "blocked": 0, "done": 110, "closable": 0, "drift": 0},
        "view": {"state": "done", "limit": 1, "offset": 5},
    }
    out = render("readiness", obj_done, "human")
    assert "Done (6–6 of 110" in out and "Closed thing" in out
