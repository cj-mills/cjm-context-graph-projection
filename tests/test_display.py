"""The display seam: template grammar, first-clause extractor, cascade, rule rendering."""

import asyncio

import pytest

from cjm_context_graph_projection.display import (Displayer, first_clause, node_title,
                                                  parse_template, set_display_rule)
from cjm_context_graph_projection.render import render


# ── Grammar ─────────────────────────────────────────────────────────────────────

def test_parse_template_literals_props_edges():
    parts = parse_template("{predicate} @ {->ABOUT} · {#<-ON_SLOT} ({actor|20})")
    kinds = [p[0] for p in parts]
    assert kinds == ["prop", "lit", "edge", "lit", "edge", "lit", "prop", "lit"]
    # {->ABOUT}: not a count, outgoing, no prop, no trunc.
    assert parts[2] == ("edge", False, "->", "ABOUT", None, None)
    # {#<-ON_SLOT}: a count, incoming.
    assert parts[4] == ("edge", True, "<-", "ON_SLOT", None, None)
    # {actor|20}: a truncated property.
    assert parts[6] == ("prop", "actor", 20)


def test_parse_template_neighbour_prop():
    (p,) = parse_template("{<-ON_SLOT.value|40}")
    assert p == ("edge", False, "<-", "ON_SLOT", "value", 40)


def test_parse_template_rejects_outside_the_frozen_grammar():
    # The boundary principle is structural: multi-hop / logic tokens don't parse.
    for bad in ("{->A->B}", "{a b}", "{#->REL.prop}", "{->}", "{|9}"):
        with pytest.raises(ValueError):
            parse_template(bad)


# ── First-clause extractor (the Decision-title fallback) ───────────────────────

def test_first_clause_short_text_passes_through():
    assert first_clause("KEEP torch-utils as-is") == "KEEP torch-utils as-is"


def test_first_clause_cuts_a_house_style_headline():
    s = ("NEXT ARC LOCKED 2026-06-29 (discussion-first w/ user, planning dialogue): "
         "DOGFOOD THE ON-GRAPH SUBSTRATE before generalizing the membrane to POSTS. " * 3)
    out = first_clause(s)
    assert out == "NEXT ARC LOCKED 2026-06-29 (discussion-first w/ user, planning dialogue)"


def test_first_clause_hard_truncates_when_no_boundary_fits():
    s = "x" * 300
    out = first_clause(s, limit=50)
    assert len(out) == 50 and out.endswith("…")


# ── The cascade (stored/generic tiers) ──────────────────────────────────────────

def test_node_title_display_title_outranks_everything():
    n = {"id": "n1", "properties": {"display_title": "role @ my-note", "title": "other"}}
    assert node_title(n) == "role @ my-note"


def test_node_title_subject_label_rescues_a_factslot():
    n = {"id": "s1", "label": "FactSlot",
         "properties": {"predicate": "rename-disposition", "subject_label": "torch-utils"}}
    # No rule annotation yet: the cascade's subject_label fallback beats the raw id.
    assert node_title(n) == "torch-utils"


def test_node_title_statement_gets_the_first_clause_trim():
    long = "DECIDED THE THING (2026-07-02, discussion-first): " + "detail " * 40
    n = {"id": "d1", "properties": {"statement": long}}
    assert node_title(n) == "DECIDED THE THING (2026-07-02, discussion-first)"


def test_node_title_falls_back_to_id():
    assert node_title({"id": "abc", "properties": {}}) == "abc"


# ── Rule rendering (the interpreter, neighbour lookups faked) ──────────────────

def _one_slot_world():
    """A FactSlot + its subject + two assertions, as wire dicts + a fake index."""
    slot = {"id": "slot1", "label": "FactSlot",
            "properties": {"predicate": "role", "subject_label": "old-name"}}
    subject = {"id": "ent1", "label": "Entity", "properties": {"name": "my-note"}}
    edges = {("->", "ABOUT"): {"slot1": ["ent1"]},
             ("<-", "ON_SLOT"): {"slot1": ["a1", "a2"]}}

    def neighbours(node_id, direction, rel):
        return edges.get((direction, rel), {}).get(node_id, [])

    cache = {"ent1": subject}
    return slot, neighbours, cache


def test_render_composes_props_neighbour_titles_and_counts():
    slot, neighbours, cache = _one_slot_world()
    d = Displayer({})
    parts = parse_template("{predicate} @ {->ABOUT} · {#<-ON_SLOT} assertion(s)")
    out = d._render(slot, "slot1", parts, neighbours, cache)
    assert out == "role @ my-note · 2 assertion(s)"


def test_render_missing_values_collapse_cleanly():
    slot, neighbours, cache = _one_slot_world()
    d = Displayer({})
    parts = parse_template("{predicate} of {->NO_SUCH_REL} end")
    assert d._render(slot, "slot1", parts, neighbours, cache) == "role of end"


def test_annotate_stamps_rule_output_but_never_overwrites_tier_one(monkeypatch):
    slot, _, cache = _one_slot_world()
    stamped = {"id": "slot2", "label": "FactSlot",
               "properties": {"predicate": "role", "display_title": "HAND-CRAFTED"}}
    d = Displayer({"FactSlot": {"title": parse_template("{predicate} @ {->ABOUT}")}})

    async def fake_pairs(gx, rel):
        return [("slot1", "ent1")] if rel == "ABOUT" else []

    async def fake_load_nodes(gx, ids):  # the batched miss-fetch
        return {i: cache[i] for i in ids if i in cache}

    monkeypatch.setattr("cjm_context_graph_projection.display.F.load_edge_pairs", fake_pairs)
    monkeypatch.setattr("cjm_context_graph_projection.display.F.load_nodes", fake_load_nodes)

    gx = type("Gx", (), {"queue": None, "graph_id": "g"})()
    asyncio.run(d.annotate(gx, [slot, stamped]))
    assert slot["properties"]["display_title"] == "role @ my-note"
    assert stamped["properties"]["display_title"] == "HAND-CRAFTED"  # tier 1 wins
    assert node_title(slot) == "role @ my-note"


# ── The write verb's validation gate (no graph touched on a bad rule) ───────────

def test_set_display_rule_rejects_a_malformed_template_before_writing():
    res = asyncio.run(set_display_rule(None, "FactSlot", title_template="{->A->B}"))
    assert res.get("error") and not res.get("written")


def test_set_display_rule_requires_at_least_one_template():
    res = asyncio.run(set_display_rule(None, "FactSlot"))
    assert res.get("error") and not res.get("written")


# ── Render surfaces carry the gloss ─────────────────────────────────────────────

def test_render_list_rows_show_gloss():
    obj = {"mode": "label", "key": "FactSlot",
           "rows": [{"id": "s1", "title": "role @ my-note", "path": None,
                     "gloss": "role of my-note · 2 assertion(s)"}],
           "count": 1, "truncated": False}
    out = render("list", obj, "human")
    assert "role @ my-note" in out and "↳ _role of my-note · 2 assertion(s)_" in out


def test_render_display_rule_result():
    out = render("display-rule", {"rule_id": "r1", "for_label": "FactSlot", "updated": False,
                                  "written": True, "title_template": "{predicate} @ {->ABOUT}",
                                  "gloss_template": None}, "human")
    assert "display-rule authored" in out and "`FactSlot`" in out and "{predicate} @ {->ABOUT}" in out
