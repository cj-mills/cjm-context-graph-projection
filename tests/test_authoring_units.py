"""Pure authoring helpers — slot routing + the apply split (no graph needed)."""

from cjm_context_graph_projection.authoring import _apply, _slot_for


def _node(label, **props):
    return {"id": "n1", "label": label, "properties": props}


def test_section_routes_to_raw_note_slot_by_label():
    slot, artifact, label = _slot_for(_node("Section", raw="## A\n\nbody\n", note_id="note:x",
                                            anchor="a"))
    assert (slot, artifact, label) == ("raw", "note", "Section")


def test_section_routes_by_inference_without_a_surfaced_label():
    # A node carrying note_id+anchor but no label still routes to the section slot.
    assert _slot_for(_node(None, note_id="note:x", anchor="a", raw="")) == ("raw", "note", "Section")


def test_code_and_cell_slots_still_route():
    assert _slot_for(_node("CodeSymbol", body="def f(): ...", module_id="m"))[:2] == ("body", "module")
    assert _slot_for(_node("Cell", source="x", cell_type="code", module_id="m"))[:2] == ("source", "notebook")


def test_non_authorable_node_returns_none():
    assert _slot_for(_node("Decision", statement="x")) is None
    assert _slot_for(_node("CodeSymbol", module_id="m")) is None  # nested: no body slot


def test_apply_replace_and_targeted_edit_on_a_section_span():
    raw = "## Alpha\n\nAlpha body.\n\n"
    assert _apply(raw, "## Alpha\n\nNew.\n\n", None) == ("## Alpha\n\nNew.\n\n", None)
    assert _apply(raw, None, ("Alpha body.", "Edited."))[0] == "## Alpha\n\nEdited.\n\n"
    assert _apply(raw, None, ("nope", "x"))[1] and "not found" in _apply(raw, None, ("nope", "x"))[1]
