"""Pure projection helpers + rendering (no graph needed)."""

import json

from cjm_context_graph_projection.projection import (
    _TEXT_FIELDS, _facet_axis_value, _facet_breakdown, _haystack, _terms,
    node_summary, node_title,
)
from cjm_context_graph_projection.onboarding import (
    SUBSTRATE_HOW_TO_QUERY, _load_seeds, _render_coverage,
)
from cjm_context_graph_projection.render import _short, render


def _node(**props):
    return {"id": "n1", "label": "Note", "properties": props}


def test_terms_distinct_lowercase_len_gt_2():
    assert _terms("Self-hosting GRAPH arc; the arc!") == ["self", "hosting", "graph", "arc"]


def test_node_title_prefers_title_then_name_then_slug_then_id():
    assert node_title(_node(title="T", name="N")) == "T"
    assert node_title(_node(name="N", slug="s")) == "N"
    assert node_title(_node(slug="s")) == "s"
    assert node_title({"id": "n1", "label": "X", "properties": {}}) == "n1"


def test_fine_tier_content_is_titled_and_searchable():
    # Born-on-graph Decisions/Assertions carry content in `statement`/`value`, not
    # the coarse text fields — they must still be titled + seed-matchable (the gap
    # Inc-4 dogfooding surfaced: `relevant` must find what a session decided).
    assert "statement" in _TEXT_FIELDS and "value" in _TEXT_FIELDS
    dec = {"id": "d1", "label": "Decision", "properties": {"statement": "alias persists on-graph"}}
    assert node_title(dec) == "alias persists on-graph"
    assert "alias persists on-graph" in _haystack(dec)
    a = {"id": "a1", "label": "Assertion", "properties": {"value": "rename:cjm-x"}}
    assert node_title(a) == "rename:cjm-x" and "rename:cjm-x" in _haystack(a)


def test_node_summary_carries_description_and_kind():
    s = node_summary(_node(title="T", description="d", note_type="project"))
    assert s == {"id": "n1", "label": "Note", "title": "T",
                 "description": "d", "note_type": "project"}


def test_section_body_is_searchable_and_summarised():
    # M1 sections must be findable by CONTENT (the `text` body), not just their heading,
    # and a surfaced section without a `description` synthesises a snippet from its body.
    assert "text" in _TEXT_FIELDS
    sec = {"id": "s1", "label": "Section",
           "properties": {"title": "Heading", "text": "preamble lede about lossless round-trip " * 10}}
    assert "lossless round-trip" in _haystack(sec)  # body is in the match haystack
    summ = node_summary(sec)
    assert summ["title"] == "Heading" and summ["description"].startswith("preamble lede")
    assert len(summ["description"]) <= 160 and summ["description"].endswith("…")  # snippet, bounded
    # A real description still wins over the body snippet.
    assert node_summary({"id": "s2", "label": "Section",
                         "properties": {"name": "x", "description": "real", "text": "body"}})["description"] == "real"


def test_render_schema_human_and_agent():
    obj = {"node_labels": ["Note", "Entity"], "edge_types": ["REFERENCES"],
           "counts": {"Note": 65, "Entity": 40}}
    human = render("schema", obj, "human")
    assert "Node labels" in human and "Note (65)" in human
    agent = render("schema", obj, "agent")
    assert json.loads(agent)["counts"]["Note"] == 65


def test_render_relevant_human_lists_results():
    obj = {"task": "graph arc",
           "seeds": [{"title": "Arc", "label": "Note", "id": "a"}],
           "results": [{"id": "a", "label": "Note", "title": "Arc",
                        "description": "d", "score": 9.0, "why": "matches task"}]}
    out = render("relevant", obj, "human")
    assert "Relevant to: graph arc" in out and "Arc" in out and "matches task" in out


# --- Bounded faceted pull protocol -------------------------------------------

def _scored(*pairs):
    """Build (nodes_by_id, seed_of) from (id, label, seed) triples."""
    nodes_by_id = {i: {"id": i, "label": lab, "properties": {}} for i, lab, _ in pairs}
    seed_of = {i: s for i, _, s in pairs}
    return nodes_by_id, seed_of


def test_facet_axis_value_kind_and_seed():
    nodes_by_id, seed_of = _scored(("n1", "Section", "s1"))
    assert _facet_axis_value("n1", "kind", nodes_by_id, seed_of) == "Section"
    assert _facet_axis_value("n1", "seed", nodes_by_id, seed_of) == "s1"


def test_facet_breakdown_counts_sorted_with_compound_handles():
    nodes_by_id, seed_of = _scored(
        ("n1", "Section", "s1"), ("n2", "Section", "s1"),
        ("n3", "Decision", "s2"))
    seeds = [{"id": "s1", "properties": {"title": "Cluster One"}},
             {"id": "s2", "properties": {"title": "Cluster Two"}}]
    facets = _facet_breakdown(["n1", "n2", "n3"], "kind", "task X",
                              [{"axis": "seed", "value": "s1"}], nodes_by_id, seed_of, seeds)
    # Biggest bucket first; handle COMPOSES onto the existing filter (recursive descent).
    assert [(f["value"], f["count"]) for f in facets] == [("Section", 2), ("Decision", 1)]
    assert facets[0]["handle"] == {"task": "task X",
                                   "filters": [{"axis": "seed", "value": "s1"},
                                               {"axis": "kind", "value": "Section"}]}
    # A seed-axis breakdown attaches the seed's display title.
    sf = _facet_breakdown(["n1", "n2", "n3"], "seed", "task X", [], nodes_by_id, seed_of, seeds)
    assert sf[0]["value"] == "s1" and sf[0]["title"] == "Cluster One"


def test_short_caps_long_text_and_collapses_whitespace():
    assert _short("a\n  b   c") == "a b c"
    capped = _short("x" * 500, 100)
    assert len(capped) == 100 and capped.endswith("…")
    assert _short("short", 100) == "short"


def test_render_relevant_facets_bounded_even_with_giant_content():
    # A node whose title/why are enormous must NOT produce an unbounded line
    # (the bounded-by-construction invariant applies per-line).
    huge = "Z" * 5000
    obj = {"task": "t", "total_hits": 200,
           "seeds": [], "results": [{"id": "a", "label": "Decision", "title": huge,
                                     "description": huge, "score": 9.0, "why": huge}],
           "facets": {"by_kind": [{"axis": "kind", "value": "Section", "count": 122,
                                   "handle": {"task": "t", "filters": [{"axis": "kind", "value": "Section"}]}}],
                      "by_seed": [{"axis": "seed", "value": "s1", "title": huge, "count": 99,
                                   "handle": {"task": "t", "filters": [{"axis": "seed", "value": "s1"}]}}]}}
    out = render("relevant", obj, "human")
    assert "200 hits across 1 kinds / 1 seed-clusters" in out
    assert "explore \"t\" --facet kind=Section" in out  # a re-runnable descent handle
    assert max(len(line) for line in out.splitlines()) < 400  # no line blows the budget


def test_render_coverage_by_kind_and_hub_handles():
    # The auto landmark view = facets of the default query: by-kind coverage + hub
    # anchors, each a re-runnable `relevant` handle. Augments, never enumerates.
    overview = {"by_kind": [{"kind": "Section", "count": 498}, {"kind": "Note", "count": 85}],
                "hubs": [{"id": "h1", "title": "Substrate Foundational Picture", "degree": 100},
                         {"id": "h2", "title": "Current Arc Status", "degree": 59}]}
    out = _render_coverage(overview)
    assert "Section×498 · Note×85" in out
    assert '- **Substrate Foundational Picture** ×100 → `relevant "Substrate Foundational Picture"`' in out
    assert max(len(line) for line in out.splitlines()) < 200  # bounded lines
    # No hubs -> just the by-kind line (e.g. a fresh/empty graph).
    assert "Hub anchors" not in _render_coverage({"by_kind": [{"kind": "Note", "count": 1}], "hubs": []})


def test_load_seeds_how_to_query_overrides_per_key(tmp_path):
    # The substrate 'how to query' prose is now a config-overridable seed (data,
    # not code) — the same default-in-code / override-in-JSON contract as arc_lead.
    # No config -> falls back to the in-code default.
    *_, how_to_query, _ = _load_seeds(None)
    assert how_to_query == SUBSTRATE_HOW_TO_QUERY
    # Config supplying `how_to_query` -> that prose wins.
    cfg = tmp_path / "onboarding.config.json"
    cfg.write_text(json.dumps({"how_to_query": "## Q\n- use `cg-write`"}))
    *_, how_to_query, _ = _load_seeds(str(cfg))
    assert how_to_query == "## Q\n- use `cg-write`"
    # Config WITHOUT the key -> per-key fallback (override one seed, keep the rest).
    cfg.write_text(json.dumps({"arc_lead": "LEAD"}))
    _push, _landmarks, arc_lead, how_to_query, _hooks = _load_seeds(str(cfg))
    assert arc_lead == "LEAD" and how_to_query == SUBSTRATE_HOW_TO_QUERY


def test_render_explore_complete_vs_refacet():
    complete = render("explore", {"task": "t", "filters": [{"axis": "kind", "value": "Decision"}],
                                  "total": 2, "complete": True,
                                  "members": [{"id": "d", "label": "Decision", "title": "D", "score": 5.0}]}, "human")
    assert "all shown" in complete
    refacet = render("explore", {"task": "t", "filters": [{"axis": "kind", "value": "Section"}],
                                 "total": 122, "complete": False, "shown": 15,
                                 "members": [{"id": "s", "label": "Section", "title": "S", "score": 5.0}],
                                 "subfacets": [{"axis": "seed", "value": "s1", "title": "Cluster",
                                                "count": 81, "handle": {"task": "t",
                                                "filters": [{"axis": "kind", "value": "Section"},
                                                            {"axis": "seed", "value": "s1"}]}}]}, "human")
    assert "re-facet below" in refacet and "Refine (by seed)" in refacet
    assert "--facet kind=Section --facet seed=s1" in refacet  # compound descent handle
