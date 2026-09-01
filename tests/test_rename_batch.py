"""The stale-wires guard (finding 889b3025) + the rename batch engine, over a fake
graph-task layer: sequential renames refuse instead of clobbering; a batch lands N
renames from ONE wire snapshot; author's guard allows the convergent 11c981b7 repair."""

import asyncio
from types import SimpleNamespace

import cjm_context_graph_layer.ops as layer_ops_mod
import cjm_context_graph_projection.authoring as authoring_mod
import cjm_context_graph_projection.factlayer as factlayer_mod
import cjm_context_graph_projection.projection as projection_mod
from cjm_context_graph_projection.authoring import _stale_wires_error, author
from cjm_context_graph_projection.rename_ops import rename_symbol, rename_symbols
from cjm_python_decompose_core.emit import emit_module_from_nodes


class FakeGraph:
    """In-memory graph-task stand-in (the ops the rename/authoring verbs use)."""

    def __init__(self, nodes, edges=None):
        self.nodes = {n["id"]: n for n in nodes}
        self.edges = list(edges or [])

    async def task(self, queue, graph_id, op, **kw):
        if op == "get_node":
            return self.nodes.get(kw["node_id"])
        if op == "find_nodes_by_label":
            return [n for n in self.nodes.values() if n["label"] == kw["label"]]
        if op == "query_nodes":
            q = kw["query"]
            rows = list(self.nodes.values())
            if q.get("ids") is not None:
                rows = [n for n in rows if n["id"] in set(q["ids"])]
            if q.get("label"):
                rows = [n for n in rows if n["label"] == q["label"]]
            for p in q.get("where") or []:
                rows = [n for n in rows
                        if (n.get("properties") or {}).get(p["prop"]) == p["value"]]
            return {"nodes": rows}
        if op == "query_edges":
            q = kw["query"]
            rows = [e for e in self.edges
                    if e.get("relation_type") == q.get("relation_type")]
            return SimpleNamespace(rows=rows)
        if op == "update_node":
            self.nodes[kw["node_id"]]["properties"].update(kw["properties"])
            return True
        if op == "add_nodes":
            for n in kw["nodes"]:
                self.nodes[n["id"]] = n
            return SimpleNamespace(nodes_added=len(kw["nodes"]))
        if op == "add_edges":
            self.edges.extend(kw["edges"])
            return SimpleNamespace(edges_added=len(kw["edges"]))
        raise AssertionError(f"unexpected graph op {op}")


GX = SimpleNamespace(queue=None, graph_id="g")


def _wire(fake, monkeypatch):
    monkeypatch.setattr(authoring_mod, "graph_task", fake.task)
    monkeypatch.setattr(factlayer_mod, "graph_task", fake.task)
    monkeypatch.setattr(projection_mod, "graph_task", fake.task)
    monkeypatch.setattr(layer_ops_mod, "graph_task", fake.task)  # refactor_ops._get


def _two_module_graph(tmp_path):
    """cjm_demo/m.py defines f + g; cjm_demo/uses.py imports and calls them."""
    m_path = tmp_path / "cjm_demo/m.py"
    u_path = tmp_path / "cjm_demo/uses.py"
    m_path.parent.mkdir(parents=True, exist_ok=True)
    mod = {"id": "mod1", "label": "CodeModule",
           "properties": {"repo_key": "cjm-demo", "module_path": "cjm_demo/m.py",
                          "path": str(m_path), "import_name": "cjm_demo.m",
                          "import_bindings": []}}
    f = {"id": "symf", "label": "CodeSymbol",
         "properties": {"module_id": "mod1", "qualname": "f", "name": "f",
                        "symbol_kind": "function", "order_index": 0,
                        "body": "def f():\n    return 1"}}
    g = {"id": "symg", "label": "CodeSymbol",
         "properties": {"module_id": "mod1", "qualname": "g", "name": "g",
                        "symbol_kind": "function", "order_index": 1,
                        "body": "def g():\n    return f() + 1"}}
    umod = {"id": "mod2", "label": "CodeModule",
            "properties": {"repo_key": "cjm-demo", "module_path": "cjm_demo/uses.py",
                           "path": str(u_path), "import_name": "cjm_demo.uses",
                           "import_bindings": []}}
    uimp = {"id": "txtu", "label": "CodeText",
            "properties": {"module_id": "mod2", "region_key": "from cjm_demo.m import f, g",
                           "text": "from cjm_demo.m import f, g", "order_index": 0}}
    usym = {"id": "symu", "label": "CodeSymbol",
            "properties": {"module_id": "mod2", "qualname": "h", "name": "h",
                           "symbol_kind": "function", "order_index": 1,
                           "body": "def h():\n    return f() + g()"}}
    fake = FakeGraph([mod, f, g, umod, uimp, usym],
                     edges=[{"source_id": "mod2", "target_id": "mod1",
                             "relation_type": "IMPORTS"}])
    return fake, m_path, u_path


def _emit_files(fake, m_path, u_path):
    """Seed both files as their plain live emissions (a healthy ingested state)."""
    m_wires = [n for n in fake.nodes.values()
               if (n.get("properties") or {}).get("module_id") == "mod1"]
    u_wires = [n for n in fake.nodes.values()
               if (n.get("properties") or {}).get("module_id") == "mod2"]
    m_path.write_text(emit_module_from_nodes(m_wires))
    u_path.write_text(emit_module_from_nodes(u_wires))


def test_batch_renames_land_from_one_snapshot(tmp_path, monkeypatch):
    fake, m_path, u_path = _two_module_graph(tmp_path)
    _wire(fake, monkeypatch)
    _emit_files(fake, m_path, u_path)
    jp = str(tmp_path / "source.jsonl")
    res = asyncio.run(rename_symbols(GX, [("symf", "f2"), ("symg", "g2")],
                                     write=True, source_journal_path=jp))
    assert not res.get("error"), res.get("error")
    m_out = m_path.read_text()
    assert "def f2():" in m_out and "def g2():" in m_out
    assert "return f2() + 1" in m_out  # g's internal call renamed too
    u_out = u_path.read_text()
    assert "from cjm_demo.m import f2, g2" in u_out
    assert "return f2() + g2()" in u_out
    assert len(res["renames"]) == 2 and res["written"]


def test_sequential_second_rename_refused_as_stale(tmp_path, monkeypatch):
    """THE 889b3025 regression: rename #2 on a module whose wires predate rename #1's
    file rewrite must refuse — the old behavior silently reverted rename #1."""
    fake, m_path, u_path = _two_module_graph(tmp_path)
    _wire(fake, monkeypatch)
    _emit_files(fake, m_path, u_path)
    jp = str(tmp_path / "source.jsonl")
    res1 = asyncio.run(rename_symbol(GX, "symf", "f2", write=True, source_journal_path=jp))
    assert not res1.get("error") and "def f2():" in m_path.read_text()
    # No ingest ran: the graph still holds pre-rename bodies. Rename #2 must refuse.
    res2 = asyncio.run(rename_symbol(GX, "symg", "g2", write=True, source_journal_path=jp))
    assert res2.get("error") and "889b3025" in res2["error"]
    assert "def f2():" in m_path.read_text()  # rename #1 NOT reverted
    assert "def g2():" not in m_path.read_text()


def test_chained_batch_refused(tmp_path, monkeypatch):
    fake, m_path, u_path = _two_module_graph(tmp_path)
    _wire(fake, monkeypatch)
    _emit_files(fake, m_path, u_path)
    res = asyncio.run(rename_symbols(GX, [("symf", "g"), ("symg", "g2")], write=False))
    assert res.get("error") and "chained" in res["error"]


def test_author_convergent_repair_allowed(tmp_path, monkeypatch):
    """The 11c981b7 recipe survives the guard: the file already carries the fix by
    hand; authoring the same OLD->NEW re-lands identical text (journal sync)."""
    fake, m_path, u_path = _two_module_graph(tmp_path)
    _wire(fake, monkeypatch)
    _emit_files(fake, m_path, u_path)
    fixed = m_path.read_text().replace("return 1", "return 42")
    m_path.write_text(fixed)  # the hand repair
    res = asyncio.run(author(GX, "symf", edit=("return 1", "return 42"), write=True))
    assert not res.get("error"), res.get("error")
    assert m_path.read_text() == fixed


def test_author_refused_on_stale_wires(tmp_path, monkeypatch):
    """A non-convergent divergence (here: a rename landed on disk but not in the
    graph) refuses the author edit instead of silently reverting the file."""
    fake, m_path, u_path = _two_module_graph(tmp_path)
    _wire(fake, monkeypatch)
    _emit_files(fake, m_path, u_path)
    m_path.write_text(m_path.read_text().replace("def f():", "def f_renamed():"))
    res = asyncio.run(author(GX, "symg", edit=("return f() + 1", "return f() + 2"),
                             write=True))
    assert res.get("error") and "889b3025" in res["error"]
    assert "def f_renamed():" in m_path.read_text()  # untouched


def test_stale_wires_error_unit(tmp_path):
    p = tmp_path / "m.py"
    mod = {"id": "m", "label": "CodeModule",
           "properties": {"repo_key": "cjm-demo", "module_path": "m.py",
                          "path": str(p), "import_name": "m"}}
    live = "def f():\n    return 1\n"
    p.write_text(live)
    assert _stale_wires_error(mod, live, "t") is None                      # match
    other = "def f():\n    return 2\n"
    assert _stale_wires_error(mod, other, "t")                             # mismatch
    assert _stale_wires_error(mod, other, "t", converged_text=live) is None  # convergent
    p.unlink()
    assert _stale_wires_error(mod, other, "t") is None                     # birth
