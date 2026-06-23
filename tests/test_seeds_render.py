"""Pure pieces of the Inc 3 surface: rename-stable keys, seed shape, render."""

from cjm_context_graph_projection import seeds
from cjm_context_graph_projection.render import render
from cjm_context_graph_projection.write import _term_slug
from cjm_dev_graph_schema.aliases import build_alias_index, resolve_subject_id


def test_conceptual_key_is_rename_stable():
    # Both the current AND prior repo names map to the durable conceptual key.
    assert seeds.conceptual_key("cjm-substrate-torch-utils") == "torch-utils"
    assert seeds.conceptual_key("cjm-torch-plugin-utils") == "torch-utils"
    # An un-renamed repo defaults to its (stable) name as key.
    assert seeds.conceptual_key("cjm-substrate") == "cjm-substrate"
    assert seeds.aliases_for("cjm-substrate-torch-utils") == ["cjm-torch-plugin-utils"]
    assert seeds.aliases_for("cjm-substrate") == []


def test_seed_has_both_rename_contradictions_with_two_active_claims():
    nodes, edges = seeds.rename_contradiction_elements()
    assertions = [n for n in nodes if n["label"] == "Assertion"]
    slots = [n for n in nodes if n["label"] == "FactSlot"]
    assert len(slots) == 2 and len(assertions) == 4  # 2 libs × (keep + rename), none superseded
    # The keep claim carries provenance to all three drifting source notes (dedup win).
    evidenced = [e for e in edges if e["relation_type"] == "EVIDENCED_BY"]
    assert len(evidenced) == 2 * (2 + 1)  # per lib: keep->2 notes, rename->1 note


def test_term_slug():
    assert _term_slug("Some New Subject!") == "some-new-subject"
    assert _term_slug("   ") == "unnamed"


def test_render_contradictions_human():
    obj = {"count": 1, "contradictions": [
        {"subject": "cjm-substrate-torch-utils", "predicate": "rename-disposition",
         "slot_id": "s1", "assertions": [
             {"value": "keep", "actor": "human", "assertion_id": "a1"},
             {"value": "rename:cjm-substrate-torch-utils", "actor": "human", "assertion_id": "a2"}]}]}
    out = render("contradictions", obj, "human")
    assert "Contradictions (1)" in out and "rename-disposition" in out and "keep" in out


def test_render_assert_conflict_warns():
    obj = {"subject": "x", "predicate": "rename-disposition", "value": "rename:y",
           "actor": "agent", "slot_id": "s", "assertion_id": "a",
           "superseded": [], "born_superseded": False,
           "conflict": [{"value": "keep", "actor": "human", "assertion_id": "a0"}]}
    out = render("assert", obj, "human")
    assert "CONFLICT" in out and "keep" in out
