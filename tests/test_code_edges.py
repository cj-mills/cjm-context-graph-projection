"""Orphaned code-target edges: pure classification + render (no graph needed)."""

from cjm_context_graph_projection.code_edges import classify_orphaned_links
from cjm_context_graph_projection.render import render


def test_classify_resolving_endpoints_are_clean():
    ops = [{"source_id": "dec", "target_id": "sym", "relation": "SHAPES"}]
    assert classify_orphaned_links(ops, resolved_ids={"dec", "sym"}) == []


def test_classify_missing_endpoint_is_orphaned_even_without_label():
    # A legacy op journaled only ids: it still DETECTS (never guesses).
    ops = [{"source_id": "dec", "target_id": "gone", "relation": "SHAPES"}]
    out = classify_orphaned_links(ops, resolved_ids={"dec"})
    assert len(out) == 1
    m = out[0]["missing"][0]
    assert m["side"] == "target" and m["id"] == "gone"
    assert m["label"] is None and "proposal" not in m


def test_classify_labeled_orphan_gets_fuzzy_remap_proposal():
    # The enrichment payoff: the journaled label finds the renamed symbol.
    ops = [{"source_id": "dec", "target_id": "old-id", "relation": "SHAPES",
            "target_label": "classify_readiness"}]
    out = classify_orphaned_links(
        ops, resolved_ids={"dec"},
        code_names={"classify_readiness_v2": "new-id", "unrelated_fn": "other"})
    pr = out[0]["missing"][0]["proposal"]
    assert pr["name"] == "classify_readiness_v2" and pr["id"] == "new-id"
    assert pr["score"] > 0.6


def test_classify_low_similarity_yields_no_proposal():
    ops = [{"source_id": "dec", "target_id": "old-id", "relation": "SHAPES",
            "target_label": "classify_readiness"}]
    out = classify_orphaned_links(ops, resolved_ids={"dec"},
                                  code_names={"totally_different": "x"})
    assert "proposal" not in out[0]["missing"][0]


def test_classify_dedups_identical_ops():
    op = {"source_id": "dec", "target_id": "gone", "relation": "SHAPES"}
    out = classify_orphaned_links([op, dict(op)], resolved_ids=set())
    assert len(out) == 1


def test_render_orphaned_edges_names_proposal_and_context():
    obj = {"orphans": [{"source_id": "dec", "target_id": "old", "relation": "SHAPES",
                        "source_context": "DoD ship decision",
                        "missing": [{"side": "target", "id": "old",
                                     "label": "classify_readiness",
                                     "proposal": {"name": "classify_readiness_v2",
                                                  "id": "new", "score": 0.9}}]}],
           "counts": {"link_ops": 10, "orphaned": 1, "with_proposal": 1}}
    out = render("orphaned-edges", obj, "human")
    assert "link ops 10 · orphaned 1 · with proposal 1" in out
    assert "resolving side: **DoD ship decision**" in out
    assert "journaled label _classify_readiness_" in out
    assert "propose remap to **classify_readiness_v2** `new` (score 0.9)" in out


def test_render_orphaned_edges_clean():
    out = render("orphaned-edges", {"orphans": [], "counts": {"link_ops": 5, "orphaned": 0,
                                                              "with_proposal": 0}}, "human")
    assert "clean" in out and "nothing will drop on replay" in out
