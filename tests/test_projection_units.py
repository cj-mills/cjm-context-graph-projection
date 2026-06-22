"""Pure projection helpers + rendering (no graph needed)."""

import json

from cjm_context_graph_projection.projection import (
    _terms, node_summary, node_title,
)
from cjm_context_graph_projection.render import render


def _node(**props):
    return {"id": "n1", "label": "Note", "properties": props}


def test_terms_distinct_lowercase_len_gt_2():
    assert _terms("Self-hosting GRAPH arc; the arc!") == ["self", "hosting", "graph", "arc"]


def test_node_title_prefers_title_then_name_then_slug_then_id():
    assert node_title(_node(title="T", name="N")) == "T"
    assert node_title(_node(name="N", slug="s")) == "N"
    assert node_title(_node(slug="s")) == "s"
    assert node_title({"id": "n1", "label": "X", "properties": {}}) == "n1"


def test_node_summary_carries_description_and_kind():
    s = node_summary(_node(title="T", description="d", note_type="project"))
    assert s == {"id": "n1", "label": "Note", "title": "T",
                 "description": "d", "note_type": "project"}


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
