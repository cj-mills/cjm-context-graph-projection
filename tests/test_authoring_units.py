"""Pure authoring helpers — slot routing + the apply split (no graph needed)."""

from cjm_python_decompose_core.parse import parse_module

from cjm_context_graph_projection.authoring import (_apply, _available_bindings, _flat_refs,
                                                    _slice_block, _slot_for)


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


def test_slice_block_extracts_nested_defs_verbatim():
    body = (
        "class Widget:\n"
        "    x = 1\n"
        "\n"
        "    @staticmethod\n"
        "    def render(self):\n"
        "        if self.x:\n"
        "            return 'on'\n"
        "        return 'off'\n"
        "\n"
        "    def render_all(self):\n"
        "        return [self.render()]\n"
    )
    # Decorators ride along; the block ends before the next same-indent line.
    assert _slice_block(body, "render") == (
        "    @staticmethod\n"
        "    def render(self):\n"
        "        if self.x:\n"
        "            return 'on'\n"
        "        return 'off'")
    # `render` must not prefix-match `render_all`; async headers match too.
    assert _slice_block(body, "render_all") == (
        "    def render_all(self):\n"
        "        return [self.render()]")
    assert _slice_block("async def go():\n    pass\n", "go") == "async def go():\n    pass"
    assert _slice_block(body, "missing") is None


def test_flat_refs_reach_class_method_bodies():
    # 2b6090dc: a name referenced ONLY inside a method body must reach the class
    # symbol's binding walk (ParsedSymbol.refs alone excludes nested def bodies).
    src = (
        "class Panel:\n"
        "    limit = MAX_ROWS\n"
        "\n"
        "    def render(self):\n"
        "        return shutil.get_terminal_size()\n"
        "\n"
        "    def dump(self):\n"
        "        return json.dumps({})\n"
    )
    ps = parse_module(src).symbols[0]
    flat = _flat_refs(ps)
    assert "shutil" in flat and "json" in flat      # method-body refs surface
    assert "MAX_ROWS" in flat                        # class-surface refs kept
    assert "shutil" not in ps.refs                   # the narrow refs stay narrow


def test_available_bindings_parses_code_text_import_lines():
    # 47b256de/2b6090dc: an import line living only in a CodeText region's verbatim
    # text (author-edited — no binding table of its own) must still be bindable.
    module = {"id": "m1", "label": "CodeModule", "properties": {}}
    wires = [
        {"id": "t1", "label": "CodeText",
         "properties": {"text": "import json\nfrom pathlib import Path\n",
                        "kind": "imports"}},
        {"id": "s1", "label": "CodeSymbol",
         "properties": {"import_bindings": [
             {"name": "os", "kind": "module", "module": "os",
              "imported": "", "alias": "", "level": 0}]}},
    ]
    av = _available_bindings(module, wires)
    assert "json" in av and "Path" in av    # parsed out of the region text
    assert "os" in av                        # frozen per-symbol bindings kept
    # A broken region must not sink the walk (SyntaxError skipped).
    wires.append({"id": "t2", "label": "CodeText",
                  "properties": {"text": "import (broken\n", "kind": "imports"}})
    assert "json" in _available_bindings(module, wires)
