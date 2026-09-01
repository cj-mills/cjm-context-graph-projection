"""Container routing + the nearest-match assist (build 55a3489e): `author <module|note-id>
--edit` resolves itself to the one child slot holding OLD; refusals point instead of stonewalling."""

import asyncio
from types import SimpleNamespace

import cjm_context_graph_projection.authoring as authoring_mod
import cjm_context_graph_projection.factlayer as factlayer_mod
import cjm_context_graph_projection.projection as projection_mod
from cjm_context_graph_projection.authoring import _nearest_match, author

from test_rename_batch import FakeGraph, GX


def _wire(fake, monkeypatch):
    monkeypatch.setattr(authoring_mod, "graph_task", fake.task)
    monkeypatch.setattr(factlayer_mod, "graph_task", fake.task)
    monkeypatch.setattr(projection_mod, "graph_task", fake.task)


def _module_graph(tmp_path):
    path = tmp_path / "cjm_demo/m.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    mod = {"id": "mod1", "label": "CodeModule",
           "properties": {"repo_key": "cjm-demo", "module_path": "cjm_demo/m.py",
                          "path": str(path), "import_name": "cjm_demo.m",
                          "import_bindings": []}}
    f = {"id": "symf", "label": "CodeSymbol",
         "properties": {"module_id": "mod1", "qualname": "f", "name": "f",
                        "symbol_kind": "function", "order_index": 0,
                        "body": "def f():\n    return 101\n"}}
    g = {"id": "symg", "label": "CodeSymbol",
         "properties": {"module_id": "mod1", "qualname": "g", "name": "g",
                        "symbol_kind": "function", "order_index": 1,
                        "body": "def g():\n    return 202\n"}}
    fake = FakeGraph([mod, f, g])
    path.write_text("def f():\n    return 101\n\n\ndef g():\n    return 202\n")
    return fake, path


def test_module_edit_routes_to_unique_symbol(tmp_path, monkeypatch):
    fake, path = _module_graph(tmp_path)
    _wire(fake, monkeypatch)
    res = asyncio.run(author(GX, "mod1", edit=("return 101", "return 111"), write=True))
    assert not res.get("error"), res.get("error")
    assert res["routed_from"] == "mod1" and res["node_id"] == "symf"
    assert "return 111" in path.read_text() and "return 202" in path.read_text()


def test_module_edit_ambiguous_lists_candidates(tmp_path, monkeypatch):
    fake, path = _module_graph(tmp_path)
    _wire(fake, monkeypatch)
    res = asyncio.run(author(GX, "mod1", edit=("return", "yield"), write=True))
    assert res.get("error") and "matches 2 slots" in res["error"]
    assert "symf" in res["error"] and "symg" in res["error"]
    assert "return 101" in path.read_text()  # untouched


def test_module_edit_no_match_points_at_closest(tmp_path, monkeypatch):
    fake, path = _module_graph(tmp_path)
    _wire(fake, monkeypatch)
    res = asyncio.run(author(GX, "mod1", edit=("def f():\n    return 102\n", "X"), write=True))
    assert res.get("error") and "found in no slot" in res["error"]
    assert "closest is" in res["error"] and "symf" in res["error"]


def test_replace_on_container_still_refused(tmp_path, monkeypatch):
    fake, path = _module_graph(tmp_path)
    _wire(fake, monkeypatch)
    res = asyncio.run(author(GX, "mod1", replace="X = 1\n", write=True))
    assert res.get("error") and "no authorable verbatim slot" in res["error"]


def test_slot_refusal_carries_nearest_match(tmp_path, monkeypatch):
    fake, path = _module_graph(tmp_path)
    _wire(fake, monkeypatch)
    res = asyncio.run(author(GX, "symf", edit=("def f():\n    return  101", "X"), write=True))
    assert res.get("error") and "OLD not found" in res["error"]
    assert "closest slot match" in res["error"] and "return 101" in res["error"]


def test_note_edit_routes_to_section(tmp_path, monkeypatch):
    note_path = tmp_path / "notes/craft.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note = {"id": "note1", "label": "Note",
            "properties": {"slug": "craft", "path": str(note_path), "title": "Craft"}}
    s1 = {"id": "sec1", "label": "Section",
          "properties": {"note_id": "note1", "anchor": "alpha", "order": 0,
                         "raw": "## Alpha\n\nthe alpha body\n"}}
    s2 = {"id": "sec2", "label": "Section",
          "properties": {"note_id": "note1", "anchor": "beta", "order": 1,
                         "raw": "## Beta\n\nthe beta body\n"}}
    fake = FakeGraph([note, s1, s2])
    _wire(fake, monkeypatch)
    res = asyncio.run(author(GX, "note1", edit=("the beta body", "the BETA body"),
                             write=True))
    assert not res.get("error"), res.get("error")
    assert res["routed_from"] == "note1" and res["node_id"] == "sec2"
    assert "the BETA body" in note_path.read_text()
    assert "the alpha body" in note_path.read_text()


def test_nearest_match_unit():
    text = "line one\nline two\nline three\n"
    hit = _nearest_match(text, "line two")
    assert hit is not None and "line two" in hit[1] and hit[0] > 0.8
    assert _nearest_match(text, "zzzz qqqq xxxx") is None
    assert _nearest_match("", "x") is None
