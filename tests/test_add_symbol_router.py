"""add-symbol (the authoring CREATE leg) + the nested-symbol edit router, over a fake
graph-task layer (get_node / find_nodes_by_label / add_nodes / add_edges / update_node)."""

import asyncio
import json
from types import SimpleNamespace

import cjm_context_graph_projection.authoring as authoring_mod
import cjm_context_graph_projection.factlayer as factlayer_mod
import cjm_context_graph_projection.projection as projection_mod
from cjm_context_graph_projection.authoring import add_symbol, author


class FakeGraph:
    """In-memory stand-in for the graph capability (the ops the authoring verbs use)."""

    def __init__(self, nodes):
        self.nodes = {n["id"]: n for n in nodes}
        self.edges = []

    async def task(self, queue, graph_id, op, **kw):
        if op == "get_node":
            return self.nodes.get(kw["node_id"])
        if op == "find_nodes_by_label":
            return [n for n in self.nodes.values() if n["label"] == kw["label"]]
        if op == "add_nodes":
            for n in kw["nodes"]:
                self.nodes[n["id"]] = n
            return SimpleNamespace(nodes_added=len(kw["nodes"]))
        if op == "add_edges":
            self.edges.extend(kw["edges"])
            return SimpleNamespace(edges_added=len(kw["edges"]))
        if op == "update_node":
            self.nodes[kw["node_id"]]["properties"].update(kw["properties"])
            return True
        raise AssertionError(f"unexpected graph op {op}")


GX = SimpleNamespace(queue=None, graph_id="g")
_OS_BINDING = {"name": "os", "kind": "import", "module": "os",
               "imported": "", "alias": "", "level": 0}


def _wire(fake, monkeypatch):
    monkeypatch.setattr(authoring_mod, "graph_task", fake.task)
    monkeypatch.setattr(factlayer_mod, "graph_task", fake.task)
    monkeypatch.setattr(projection_mod, "graph_task", fake.task)


def _module_node(tmp_path, module_path="cjm_demo/m.py"):
    path = tmp_path / module_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return {"id": "mod1", "label": "CodeModule",
            "properties": {"repo_key": "cjm-demo", "module_path": module_path,
                           "path": str(path), "import_name": "cjm_demo.m",
                           "import_bindings": []}}


def _py_module_graph(tmp_path, module_path="cjm_demo/m.py"):
    """A one-module graph: an import CodeText region + one function symbol."""
    mod = _module_node(tmp_path, module_path)
    text = {"id": "txt1", "label": "CodeText",
            "properties": {"module_id": "mod1", "region_key": "import os",
                           "text": "import os", "order_index": 0}}
    sym = {"id": "sym1", "label": "CodeSymbol",
           "properties": {"module_id": "mod1", "qualname": "f", "name": "f",
                          "symbol_kind": "function", "order_index": 1,
                          "body": "def f():\n    return os.getcwd()",
                          "import_bindings": [dict(_OS_BINDING)]}}
    return FakeGraph([mod, text, sym])


def test_add_symbol_appends_emits_and_links(tmp_path, monkeypatch):
    fake = _py_module_graph(tmp_path)
    _wire(fake, monkeypatch)
    res = asyncio.run(add_symbol(GX, "mod1", "def g(x):\n    return f(x)\n"))
    assert not res.get("error") and res["written"]
    assert res["qualname"] == "g" and res["order_index"] == 2
    emitted = (tmp_path / "cjm_demo/m.py").read_text()
    assert emitted.index("def f():") < emitted.index("def g(x):")
    assert "import os" in emitted  # the existing symbol's binding survives the derive
    # The node landed with ingest's identity + both structural edges.
    assert res["symbol_id"] in fake.nodes
    rels = {(e.get("source_id"), e.get("relation_type")) for e in fake.edges}
    assert ("mod1", "DEFINES") in rels and ("mod1", "CONTAINS") in rels
    # The new symbol's refs bound against the module's available imports.
    assert res["repo_key"] == "cjm-demo" and res["artifact"] == "module"


def test_add_symbol_binds_refs_to_available_imports(tmp_path, monkeypatch):
    fake = _py_module_graph(tmp_path)
    _wire(fake, monkeypatch)
    res = asyncio.run(add_symbol(GX, "mod1", "def h():\n    return os.sep\n"))
    assert not res.get("error")
    node = fake.nodes[res["symbol_id"]]
    assert any(b.get("name") == "os" for b in node["properties"]["import_bindings"])
    assert "import os" in (tmp_path / "cjm_demo/m.py").read_text()


def test_add_symbol_keeps_test_module_imports_verbatim(tmp_path, monkeypatch):
    # On a tests/ path the emit must NOT derive (DEC 67a5efda): the import region
    # survives verbatim even though no symbol's bindings reference it.
    mod = _module_node(tmp_path, "tests/test_m.py")
    text = {"id": "txt1", "label": "CodeText",
            "properties": {"module_id": "mod1", "region_key": "from conftest import fx",
                           "text": "from conftest import fx", "order_index": 0}}
    fake = FakeGraph([mod, text])
    _wire(fake, monkeypatch)
    res = asyncio.run(add_symbol(GX, "mod1", "def test_x(fx):\n    assert True\n"))
    assert not res.get("error")
    assert "from conftest import fx" in (tmp_path / "tests/test_m.py").read_text()


def test_add_symbol_refuses_bad_bodies_and_duplicates(tmp_path, monkeypatch):
    fake = _py_module_graph(tmp_path)
    _wire(fake, monkeypatch)
    dup = asyncio.run(add_symbol(GX, "mod1", "def f():\n    pass\n"))
    assert "already exists" in dup["error"] and "sym1" in dup["error"]
    multi = asyncio.run(add_symbol(GX, "mod1", "x = 1\n\n\ndef g():\n    pass\n"))
    assert "exactly ONE top-level def/class" in multi["error"]
    bad = asyncio.run(add_symbol(GX, "mod1", "def g(:\n"))
    assert "does not parse" in bad["error"]
    missing = asyncio.run(add_symbol(GX, "nope", "def g():\n    pass\n"))
    assert "no module" in missing["error"]


def test_add_symbol_refuses_notebook_modules(tmp_path, monkeypatch):
    mod = _module_node(tmp_path)
    cell = {"id": "cell1", "label": "Cell",
            "properties": {"module_id": "mod1", "cell_type": "code",
                           "source": "def f():\n    pass", "cell_key": "c0"}}
    fake = FakeGraph([mod, cell])
    _wire(fake, monkeypatch)
    res = asyncio.run(add_symbol(GX, "mod1", "def g():\n    pass\n"))
    assert ".py modules only" in res["error"]


def _class_module_graph(tmp_path):
    """A module holding a class with a method — the method is a NESTED symbol (no body)."""
    mod = _module_node(tmp_path)
    cls = {"id": "cls1", "label": "CodeSymbol",
           "properties": {"module_id": "mod1", "qualname": "K", "name": "K",
                          "symbol_kind": "class", "order_index": 0,
                          "body": "class K:\n    def m(self):\n        return 1",
                          "import_bindings": []}}
    nested = {"id": "meth1", "label": "CodeSymbol",
              "properties": {"module_id": "mod1", "qualname": "K.m", "name": "K.m",
                             "symbol_kind": "method"}}
    return FakeGraph([mod, cls, nested])


def test_nested_symbol_edit_routes_through_owning_class(tmp_path, monkeypatch):
    fake = _class_module_graph(tmp_path)
    _wire(fake, monkeypatch)
    res = asyncio.run(author(GX, "meth1", edit=("return 1", "return 2")))
    assert not res.get("error"), res
    assert res["routed_from"] == "meth1" and res["node_id"] == "cls1"
    assert "return 2" in fake.nodes["cls1"]["properties"]["body"]
    assert "return 2" in (tmp_path / "cjm_demo/m.py").read_text()


def test_nested_symbol_replace_is_refused_with_guidance(tmp_path, monkeypatch):
    fake = _class_module_graph(tmp_path)
    _wire(fake, monkeypatch)
    res = asyncio.run(author(GX, "meth1", replace="def m(self):\n    return 3\n"))
    assert "--edit" in res["error"] and "cls1" in res["error"]


def test_nested_symbol_routes_to_owning_notebook_cell(tmp_path, monkeypatch):
    nb_path = tmp_path / "nbs" / "00_m.ipynb"
    nb_path.parent.mkdir(parents=True, exist_ok=True)
    mod = {"id": "mod1", "label": "CodeModule",
           "properties": {"repo_key": "cjm-demo", "module_path": "cjm_demo/m.py",
                          "path": str(nb_path), "import_name": "cjm_demo.m"}}
    cell = {"id": "cell1", "label": "Cell",
            "properties": {"module_id": "mod1", "cell_type": "code", "cell_key": "c0",
                           "source": "class K:\n    def m(self):\n        return 1",
                           "order_index": 0}}
    nested = {"id": "meth1", "label": "CodeSymbol",
              "properties": {"module_id": "mod1", "qualname": "K.m", "name": "K.m",
                             "symbol_kind": "method"}}
    fake = FakeGraph([mod, cell, nested])
    _wire(fake, monkeypatch)
    res = asyncio.run(author(GX, "meth1", edit=("return 1", "return 2")))
    assert not res.get("error"), res
    assert res["routed_from"] == "meth1" and res["node_id"] == "cell1"
    assert res["artifact"] == "notebook"
    nb = json.loads(nb_path.read_text())
    assert any("return 2" in "".join(c.get("source", [])) for c in nb["cells"])


def test_add_symbol_dry_run_touches_nothing(tmp_path, monkeypatch):
    # write=False (--no-write): the artifact is previewed; graph and disk stay untouched.
    fake = _py_module_graph(tmp_path)
    _wire(fake, monkeypatch)
    res = asyncio.run(add_symbol(GX, "mod1", "def g():\n    return 1\n", write=False))
    assert not res.get("error") and not res["written"]
    assert "def g():" in res["emitted_text"]
    assert res["symbol_id"] not in fake.nodes and not fake.edges
    assert not (tmp_path / "cjm_demo/m.py").exists()


def test_class_method_refs_survive_add_symbol_emit(tmp_path, monkeypatch):
    # 2b6090dc regression (3rd bite of the 47b256de class): an import author-added
    # to a region and referenced ONLY inside class-method bodies must survive the
    # next add-symbol's canonical emit (which derives the import block from the
    # graph's binding tables, not the file).
    mod = _module_node(tmp_path)
    text = {"id": "txt1", "label": "CodeText",
            "properties": {"module_id": "mod1", "region_key": "import os",
                           "text": "import os", "order_index": 0, "kind": "imports"}}
    cls = {"id": "cls1", "label": "CodeSymbol",
           "properties": {"module_id": "mod1", "qualname": "Panel", "name": "Panel",
                          "symbol_kind": "class", "order_index": 1,
                          "body": ("class Panel:\n"
                                   "    def dump(self):\n"
                                   "        return os.getcwd()"),
                          "import_bindings": [dict(_OS_BINDING)]}}
    fake = FakeGraph([mod, text, cls])
    _wire(fake, monkeypatch)
    # Leg 1: the import line lands in the region's verbatim text — and mints a
    # module-level binding (the add-text pattern, now on author too).
    res = asyncio.run(author(GX, "txt1", edit=("import os", "import os\nimport json")))
    assert not res.get("error") and res.get("new_import_bindings") == 2  # os + json (module node started bare)
    assert "json" in [b["name"] for b in fake.nodes["mod1"]["properties"]["import_bindings"]]
    # Leg 2: a METHOD body starts referencing it — the rebind reaches class bodies
    # (frozen bindings + a narrow _direct_refs walk would both miss it).
    res = asyncio.run(author(GX, "cls1", edit=("return os.getcwd()",
                                               "return json.dumps(os.getcwd())")))
    assert not res.get("error")
    names = [b["name"] for b in fake.nodes["cls1"]["properties"]["import_bindings"]]
    assert "json" in names and "os" in names
    # The bite itself: the NEXT add-symbol triggers a derive-imports canonical emit.
    res = asyncio.run(add_symbol(GX, "mod1", "def g():\n    return Panel()\n"))
    assert not res.get("error")
    emitted = (tmp_path / "cjm_demo/m.py").read_text()
    assert "import json" in emitted and "import os" in emitted
