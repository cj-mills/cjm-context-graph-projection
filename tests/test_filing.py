"""Filing reconciler: pure classification + render (no graph needed)."""

from cjm_context_graph_projection.filing import classify_filing
from cjm_context_graph_projection.render import render


def test_classify_unfiled_item_with_filed_neighbor_gets_vote_proposal():
    out = classify_filing(
        open_items={"item"},
        anchors={"prog"},
        filed_under={"neighbor": {"prog"}},
        neighbors={"item": {"neighbor"}, "neighbor": {"item"}})
    u = out["unfiled"][0]
    assert u["id"] == "item"
    assert u["proposals"][0]["anchor_id"] == "prog"
    assert u["proposals"][0]["evidence"] == [{"kind": "vote", "via": "neighbor"}]
    assert out["counts"] == {"open_items": 1, "anchors": 1, "filed": 0,
                             "unfiled": 1, "with_proposal": 1, "refile": 0}


def test_classify_direct_anchor_reference_outranks_single_vote():
    # The item cites anchor `a` directly (WEIGHT_DIRECT) while one neighbor
    # votes `b` (WEIGHT_VOTE) — the item's own edge wins.
    out = classify_filing(
        open_items={"item"},
        anchors={"a", "b"},
        filed_under={"n1": {"b"}},
        neighbors={"item": {"a", "n1"}, "n1": {"item"}})
    proposals = out["unfiled"][0]["proposals"]
    assert [p["anchor_id"] for p in proposals] == ["a", "b"]
    assert proposals[0]["score"] == 2.0


def test_classify_accumulated_votes_beat_a_direct_reference():
    # Three filed neighbors voting the same anchor outweigh one direct edge —
    # the compounding signal: every confirmed filing sharpens the next proposal.
    out = classify_filing(
        open_items={"item"},
        anchors={"a", "b"},
        filed_under={"n1": {"b"}, "n2": {"b"}, "n3": {"b"}},
        neighbors={"item": {"a", "n1", "n2", "n3"}})
    proposals = out["unfiled"][0]["proposals"]
    assert proposals[0]["anchor_id"] == "b" and proposals[0]["score"] == 3.0


def test_classify_neighbor_citing_anchor_is_weak_bootstrap_evidence():
    # n1 merely cites the anchor without being filed under it — weak evidence,
    # but enough to bootstrap proposals before ANY filing exists.
    out = classify_filing(
        open_items={"item"},
        anchors={"a"},
        filed_under={},
        neighbors={"item": {"n1"}, "n1": {"item", "a"}})
    p = out["unfiled"][0]["proposals"][0]
    assert p["anchor_id"] == "a" and p["score"] == 0.5
    assert p["evidence"] == [{"kind": "cites", "via": "n1"}]


def test_classify_refile_only_when_strictly_better():
    # Filed under `a`; two neighbors vote `b` — a strictly better anchor
    # emerged, so a refile is proposed (late binding is a feature).
    out = classify_filing(
        open_items={"item"},
        anchors={"a", "b"},
        filed_under={"item": {"a"}, "n1": {"b"}, "n2": {"b"}},
        neighbors={"item": {"n1", "n2"}})
    r = out["refile"][0]
    assert r["current"] == ["a"] and r["proposal"]["anchor_id"] == "b"
    # A tie keeps the standing filing — re-filing is supersession, never churn.
    out2 = classify_filing(
        open_items={"item"},
        anchors={"a", "b"},
        filed_under={"item": {"a"}, "n1": {"b"}, "n2": {"a"}},
        neighbors={"item": {"n1", "n2"}})
    assert out2["refile"] == []
    assert out2["counts"]["filed"] == 1


def test_classify_no_anchors_everything_unfiled_without_proposals():
    out = classify_filing(open_items={"i1", "i2"}, anchors=set(),
                          filed_under={}, neighbors={"i1": {"i2"}})
    assert [u["id"] for u in out["unfiled"]] == ["i1", "i2"]
    assert all(u["proposals"] == [] for u in out["unfiled"])
    assert out["counts"]["anchors"] == 0 and out["counts"]["with_proposal"] == 0


def test_render_filing_names_proposals_with_confirm_recipe():
    obj = {"counts": {"open_items": 2, "filed": 1, "unfiled": 1,
                      "with_proposal": 1, "refile": 0},
           "anchors": [{"id": "prog-1", "role": "program", "label": "Transcript Vertical"}],
           "unfiled": [{"id": "item-1", "label": "WORK ITEM: do the thing",
                        "proposals": [{"anchor_id": "prog-1", "label": "Transcript Vertical",
                                       "score": 2.0,
                                       "evidence": [{"kind": "vote", "via": "n1"},
                                                    {"kind": "vote", "via": "n2"}]}]}],
           "refile": []}
    out = render("filing", obj, "human")
    assert "unfiled 1" in out
    assert "Transcript Vertical" in out and "2×vote" in out
    assert "PART_OF" in out  # the confirm recipe is part of the surface


def test_render_filing_without_anchors_says_how_to_activate():
    obj = {"counts": {"open_items": 3, "filed": 0, "unfiled": 3,
                      "with_proposal": 0, "refile": 0},
           "anchors": [], "unfiled": [], "refile": []}
    out = render("filing", obj, "human")
    assert "role=program" in out
