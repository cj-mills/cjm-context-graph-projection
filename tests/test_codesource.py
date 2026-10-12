"""The code lane's element regime: capture, the source-state fold and the derivation (design abfc2829,
ruling 21b8fcca, design detail 6d35c07d; the tests pilot's leg 2)."""

import json

import pytest

from cjm_context_graph_projection.codefold import CodeFold
from cjm_context_graph_projection.codesource import (capture_records, CodeSourceError, ELEMENT,
                                                     ELEMENT_RETIRE, PLACE, SourceState)
from cjm_context_graph_projection.relive import _touched_keys
from cjm_context_graph_projection.source_state import (append_source, IdentityWalk, journaled_emit,
                                                       latest_source_ops)
from cjm_dev_graph_schema.identity import code_module_node_id
from cjm_python_decompose_core.elements import compose_module, lift_imports

LIB = "cjm-demo-lib"
TST, CONF = "tests/test_store.py", "tests/conftest.py"
MID = code_module_node_id(LIB, TST)

TEXT = '''"""Tests of the store."""

import os
import sys

import pytest

from conftest import tmp_store

pytestmark = pytest.mark.slow


class FakeDeps:
    name = "fake"

    def path(self):
        def inner():
            return os.getcwd()
        return inner()


def test_open(tmp_store):
    assert tmp_store
'''


def _rec(verb, ts, args, op=None):
    rec = {"verb": verb, "ts": ts, "generation": 1, "args": args}
    if op:
        rec["op"] = op
    return rec


def _src(ts, path, text):
    return _rec("source", ts, {"repo_key": LIB, "module_path": path,
                               "import_name": path[:-3].replace("/", "."), "text": text})


PREFIX = [
    _src(100.0, CONF, "import pytest\n\n\n@pytest.fixture\ndef tmp_store():\n    return 1\n"),
    _src(101.0, TST, TEXT),
    _rec("cutover", 102.0, {"repo_key": LIB, "module_path": TST}),
]


def _capture(prefix, ts=200.0):
    walk = IdentityWalk()
    for r in prefix:
        walk.apply(r)
    walk.flush()
    recs, report = capture_records(LIB, LIB, TST, "tests.test_store", TEXT,
                                   symbol_identity=walk.ident.for_module(LIB, TST),
                                   ever=set(walk.state.elements.ever))
    return [{**r, "ts": ts, "generation": 1} for r in recs], report


def _fold(records, tmp_path):
    fold = CodeFold(str(tmp_path), normalize=True)
    for r in records:
        fold.apply(r)
    nodes, edges = fold.elements()
    return fold, {n["id"]: n for n in nodes}, {e["id"]: e for e in edges}


def _by(nodes, label, key, value):
    """The captured module's nodes of one label holding one property value."""
    return [n for n in nodes.values() if n["label"] == label and n["properties"].get(key) == value
            and (n["properties"].get("module_id") == MID)]


def test_capture_keeps_every_symbol_id_and_composes_the_capture_form(tmp_path):
    cap, report = _capture(PREFIX)
    state = SourceState.over(PREFIX + cap)
    composed = state.latest_ops()[(LIB, TST)]["text"]
    assert composed == compose_module(lift_imports(TEXT).elements, frozenset({"conftest"}))
    assert composed == TEXT  # the author's grouping is the derived one: stdlib, third-party, local
    _, before, _ = _fold(PREFIX, tmp_path)
    fold, after, edges = _fold(PREFIX + cap, tmp_path)
    assert not fold.failures
    sym = lambda ns: {i for i, n in ns.items() if n["label"] == "CodeSymbol"}
    assert sym(before) == sym(after)  # every symbol keeps its id, the closure `inner` included
    first_import_region = _by(before, "CodeText", "region_key", "import os")[0]["id"]
    block = _by(after, "CodeText", "kind", "imports")
    assert [b["id"] for b in block] == [first_import_region]  # one node, the first emptied region's id
    assert block[0]["properties"]["text"] == "import os\nimport sys\n\nimport pytest\n\nfrom conftest import tmp_store"
    assert set(report["leaving"]) == set(before) - set(after)  # the other import groups' ids
    member = [n for n in after.values() if n["properties"].get("parent_id")]
    assert [m["id"] for m in member] == report["born"]  # the class-level text member is born
    assert "order_index" not in member[0]["properties"]  # never read as a top-level region
    fake = _by(after, "CodeSymbol", "qualname", "FakeDeps")[0]["id"]
    contains = {(e["source_id"], e["target_id"]) for e in edges.values() if e["relation_type"] == "CONTAINS"}
    assert (fake, member[0]["id"]) in contains
    test_open = _by(after, "CodeSymbol", "qualname", "test_open")[0]
    assert [b["name"] for b in test_open["properties"]["import_bindings"]] == ["tmp_store"]
    assert report["unclaimed"] == ["sys"]


def test_an_element_rename_keeps_the_id_and_the_closure_follows(tmp_path):
    cap, _ = _capture(PREFIX)
    state = SourceState.over(PREFIX + cap)
    path_id = next(e.element for e in state.elements.elements.values() if e.text.startswith("def path"))
    renamed = state.elements.elements[path_id].text.replace("def path(", "def where(")
    edit = [_rec(ELEMENT, 300.0, {"element": path_id, "kind": "symbol", "text": renamed,
                                  "bindings": state.elements.elements[path_id].bindings})]
    _, before, _ = _fold(PREFIX + cap, tmp_path)
    fold, after, _ = _fold(PREFIX + cap + edit, tmp_path)
    assert not fold.failures
    assert after[path_id]["properties"]["qualname"] == "FakeDeps.where"
    inner = _by(before, "CodeSymbol", "qualname", "FakeDeps.path.inner")[0]["id"]
    assert after[inner]["properties"]["qualname"] == "FakeDeps.where.inner"  # the closure kept its id


def test_an_element_retire_removes_its_node(tmp_path):
    cap, _ = _capture(PREFIX)
    state = SourceState.over(PREFIX + cap)
    test_open = next(e.element for e in state.elements.elements.values() if e.text.startswith("def test_open"))
    fold, after, _ = _fold(PREFIX + cap + [_rec(ELEMENT_RETIRE, 300.0, {"element": test_open})], tmp_path)
    assert not fold.failures and test_open not in after
    assert "tmp_store" not in SourceState.over(PREFIX + cap + [
        _rec(ELEMENT_RETIRE, 300.0, {"element": test_open})]).latest_ops()[(LIB, TST)]["text"]


def test_a_broken_placement_chain_is_reported_and_the_derivation_stands(tmp_path):
    cap, _ = _capture(PREFIX)
    state = SourceState.over(PREFIX + cap)
    a, b = [e for e in state.elements.elements.values() if e.parent is None and e.kind != "imports"][-2:]
    fork = [_rec(PLACE, 300.0, {"element": b.element, "module": MID, "parent": None, "after": a.after})]
    _, good, _ = _fold(PREFIX + cap, tmp_path)
    fold, after, _ = _fold(PREFIX + cap + fork, tmp_path)
    assert fold.failures and "follow" in fold.failures[-1]["error"]
    assert set(after) == set(good)


def test_a_snapshot_write_to_a_captured_module_is_refused(tmp_path):
    journal = tmp_path / "source.jsonl"
    cap, _ = _capture(PREFIX)
    journal.write_text("".join(json.dumps(r) + "\n" for r in PREFIX + cap))
    assert latest_source_ops(str(journal))[(LIB, TST)]["text"] == TEXT
    with pytest.raises(CodeSourceError):
        append_source(str(journal), LIB, TST, "tests.test_store", TEXT + "\n# edited\n")
    receipt = journaled_emit(str(journal), emissions=[{"repo_key": LIB, "module_path": TST,
                                                       "import_name": "tests.test_store",
                                                       "text": TEXT, "path": str(tmp_path / "x.py")}])
    assert "captured" in receipt["error"] and not (tmp_path / "x.py").exists()


def _state(fold):
    nodes = {i: (json.loads(json.dumps(r["wire"])), r["created_at"], r["updated_at"])
             for i, r in fold.nodes.items()}
    return nodes, set(fold.edges)


@pytest.mark.parametrize("step", ["capture", "rename"])
def test_a_seeded_step_lands_where_the_whole_fold_lands(tmp_path, step):
    cap, _ = _capture(PREFIX)
    prefix, group = PREFIX, cap
    if step == "rename":
        state = SourceState.over(PREFIX + cap)
        pid = next(e.element for e in state.elements.elements.values() if e.text.startswith("def path"))
        prefix = PREFIX + cap
        group = [_rec(ELEMENT, 300.0, {"element": pid, "kind": "symbol",
                                       "text": state.elements.elements[pid].text.replace("def path(", "def where("),
                                       "bindings": state.elements.elements[pid].bindings})]
    whole = CodeFold(str(tmp_path), normalize=True)
    for r in prefix + group:
        whole.apply(r)
    whole.settle()
    db = CodeFold(str(tmp_path), normalize=True)
    for r in prefix:
        db.apply(r)
    db.settle()
    held = {k: ([{**db.nodes[i]["wire"], "created_at": db.nodes[i]["created_at"],
                  "updated_at": db.nodes[i]["updated_at"]} for i in d["nodes"]], list(d["local"].values()))
            for k, d in db.modules.items()}
    held = json.loads(json.dumps({json.dumps(k): v for k, v in held.items()}))
    held = {tuple(json.loads(k)): v for k, v in held.items()}
    live = CodeFold(str(tmp_path), normalize=True)
    for r in prefix:
        live.advance(r)
    live.walk.flush()
    keys = _touched_keys(group, live.walk.state)
    assert keys == [(LIB, TST)]
    live.seed_corpus({k: [w for w in held[k][0] if w["label"] in ("CodeModule", "CodeSymbol")] for k in held})
    for k in keys:
        live.seed_module(k, *held[k])
    for r in group:
        live.apply(r)
    live.settle()
    w_nodes, w_edges = _state(whole)
    l_nodes, l_edges = _state(live)
    for nid, val in w_nodes.items():
        if nid == MID or val[0]["properties"].get("module_id") == MID:
            assert l_nodes.get(nid) == val, val[0]["properties"].get("qualname")
    local = ("ABOUT", "DEFINES", "CONTAINS")
    expect = {i for i, r in whole.edges.items()
              if r["wire"]["relation_type"] not in local or r["wire"]["source_id"] in set(l_nodes)}
    assert l_edges == expect
