"""The code lane's element regime: capture, the source-state fold and the derivation (design abfc2829,
ruling 21b8fcca, design detail 6d35c07d; the tests pilot's leg 2)."""

import json

import pytest

from cjm_context_graph_projection.codefold import CodeFold
from cjm_context_graph_projection.codesource import (absorb_records, capture_records, CodeSourceError,
                                                     ELEMENT, ELEMENT_RETIRE, PLACE, SourceState)
from cjm_context_graph_projection.relive import _touched_keys
from cjm_context_graph_projection.source_state import (append_source, flip_module, IdentityWalk,
                                                       journaled_emit, latest_source_ops)
from cjm_dev_graph_schema.identity import code_module_node_id, code_symbol_node_id
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


# -- the element-grain absorb (abfc2829 (7)) ---------------------------------------------------------
def _captured_state():
    cap, _ = _capture(PREFIX)
    return PREFIX + cap, SourceState.over(PREFIX + cap)


def _id(state, prefix):
    return next(e.element for e in state.elements.elements.values()
                if e.text.startswith(prefix) and not e.retired)


def _absorb(state, text, **kw):
    recs, rep = absorb_records(state, MID, text, **kw)
    return [_rec(r["verb"], 300.0, r["args"]) for r in recs], rep


def test_absorbing_the_held_text_or_another_layout_of_it_lands_nothing():
    _, state = _captured_state()
    assert _absorb(state, TEXT)[0] == []
    shuffled = TEXT.replace("import os\nimport sys\n\nimport pytest\n\nfrom conftest import tmp_store",
                            "from conftest import tmp_store\nimport pytest\nimport sys, os")
    recs, rep = _absorb(state, shuffled)
    assert recs == [] and rep["text"] == TEXT


def test_an_edit_lands_the_changed_element_only(tmp_path):
    prior, state = _captured_state()
    recs, rep = _absorb(state, TEXT.replace("assert tmp_store\n", "assert tmp_store == 1\n"))
    assert [(r["verb"], r["args"]["element"]) for r in recs] == [(ELEMENT, _id(state, "def test_open"))]
    assert rep["changed"] == [_id(state, "def test_open")] and not rep["born"] and not rep["retired"]
    fold, after, _ = _fold(prior + recs, tmp_path)
    assert not fold.failures
    assert "== 1" in after[_id(state, "def test_open")]["properties"]["body"]


def test_a_new_def_is_born_at_its_address_with_the_bindings_it_names(tmp_path):
    prior, state = _captured_state()
    text = TEXT.replace("import os\n", "import json\nimport os\n") + \
        "\n\ndef test_dump():\n    assert json.dumps(1)\n"
    recs, rep = _absorb(state, text)
    born = code_symbol_node_id(MID, "test_dump")
    assert rep["born"] == [born] and not rep["changed"]
    el = next(r["args"] for r in recs if r["verb"] == ELEMENT)
    assert [b["name"] for b in el["bindings"]] == ["json"]
    place = next(r["args"] for r in recs if r["verb"] == PLACE)
    assert place == {"element": born, "module": MID, "parent": None, "after": _id(state, "def test_open")}
    fold, after, _ = _fold(prior + recs, tmp_path)
    assert not fold.failures and after[born]["properties"]["qualname"] == "test_dump"
    assert SourceState.over(prior + recs).latest_ops()[(LIB, TST)]["text"] == rep["text"]


def test_the_binding_check_refuses_a_name_the_write_leaves_unbound():
    prior, state = _captured_state()
    with pytest.raises(CodeSourceError, match="reads json"):
        _absorb(state, TEXT + "\n\ndef test_dump():\n    assert json.dumps(1)\n")
    recs, rep = _absorb(state, TEXT.replace("import os\n", "import json\nimport os\n"))  # the import first
    assert rep["unclaimed"] == ["json", "sys"] and rep["binding_check"] == "clean"
    state2 = SourceState.over(prior + recs)
    recs2, rep2 = absorb_records(state2, MID, TEXT.replace("import os\n", "import json\nimport os\n")
                                 + "\n\ndef test_dump():\n    assert json.dumps(1)\n")
    assert rep2["unclaimed"] == ["sys"]  # the new def claims it


def test_a_removed_def_retires_and_its_follower_is_replaced():
    _, state = _captured_state()
    fake = _id(state, "class FakeDeps")
    text = TEXT.replace(TEXT[TEXT.index("class FakeDeps"):TEXT.index("def test_open")], "")
    recs, rep = _absorb(state, text)
    gone = {r["args"]["element"] for r in recs if r["verb"] == ELEMENT_RETIRE}
    assert fake in gone and len(gone) == 3  # the class, its text member and its method
    moved = [r["args"] for r in recs if r["verb"] == PLACE]
    assert [m["element"] for m in moved] == [_id(state, "def test_open")]
    assert moved[0]["after"] == _id(state, "pytestmark")


def test_a_rename_keeps_the_id_only_when_the_verb_names_it():
    _, state = _captured_state()
    old = _id(state, "def test_open")
    text = TEXT.replace("def test_open(", "def test_opens(")
    recs, rep = _absorb(state, text, renames=[("test_open", "test_opens")])
    assert [r["args"]["element"] for r in recs] == [old] and not rep["born"]
    recs, rep = _absorb(state, text, authored=old)  # author's one edit renamed the def it edited
    assert [r["args"]["element"] for r in recs] == [old] and rep["renamed"] == [["test_open", "test_opens"]]
    recs, rep = _absorb(state, text)  # a file edit names nothing: a birth and a retire
    assert rep["retired"] == [old] and rep["born"] == [code_symbol_node_id(MID, "test_opens")]


def test_a_class_rename_carries_its_members():
    _, state = _captured_state()
    path = _id(state, "def path")
    recs, rep = _absorb(state, TEXT.replace("class FakeDeps:", "class FakeDep:"),
                        renames=[("FakeDeps", "FakeDep")])
    assert [r["verb"] for r in recs] == [ELEMENT] and not rep["born"] and not rep["retired"]
    assert path not in {r["args"]["element"] for r in recs}  # a member's own text never changed


def test_a_text_element_keeps_its_id_through_an_edit_and_an_ambiguous_gap_is_refused():
    _, state = _captured_state()
    mark = _id(state, "pytestmark")
    recs, _ = _absorb(state, TEXT.replace("pytest.mark.slow", "pytest.mark.fast"))
    assert [(r["verb"], r["args"]["element"]) for r in recs] == [(ELEMENT, mark)]
    split = TEXT.replace("pytestmark = pytest.mark.slow", "A = 1\n\nB = 2")
    with pytest.raises(CodeSourceError, match="ambiguous"):
        _absorb(state, split.replace("A = 1\n\nB = 2", "A = 1\n\nB = 2\n\nC = 3")
                .replace("class FakeDeps:\n    name", "X = 0\n\n\nclass FakeDeps:\n    name"))
    recs, rep = _absorb(state, TEXT.replace("pytestmark = pytest.mark.slow",
                                            "pytestmark = pytest.mark.slow\n\nLIMIT = 3"))
    assert not rep["changed"] and len(rep["born"]) == 1  # the region kept by its leading line, one born
    doc = _id(state, '"""Tests of the store')
    recs, rep = _absorb(state, TEXT.replace('"""Tests of the store."""', '"""The store, tested."""')
                        .replace("pytestmark =", "LIMIT = 3\n\npytestmark ="))
    assert rep["changed"] == [doc] and len(rep["born"]) == 1  # the module docstring continues itself


def test_a_reborn_name_never_reuses_a_retired_id():
    prior, state = _captured_state()
    old = _id(state, "def test_open")
    without = TEXT[:TEXT.index("def test_open")].rstrip("\n") + "\n"
    recs, _ = _absorb(state, without)
    state2 = SourceState.over(prior + recs)
    recs2, rep = absorb_records(state2, MID, TEXT)
    assert rep["born"] == [code_symbol_node_id(MID, "test_open", 1)] != [old]


def test_journaled_emit_and_flip_module_land_element_records(tmp_path):
    journal = tmp_path / "source.jsonl"
    prior, _ = _captured_state()
    journal.write_text("".join(json.dumps(r) + "\n" for r in prior))
    edited = TEXT.replace("assert tmp_store\n", "assert tmp_store is not None\n")
    out = tmp_path / "repos" / LIB / TST
    receipt = journaled_emit(str(journal), emissions=[{"repo_key": LIB, "module_path": TST,
                                                       "import_name": "tests.test_store",
                                                       "text": edited, "path": str(out)}],
                             op={"op": "author"})
    assert not receipt.get("error") and receipt["events"][0]["verb"] == "elements"
    assert out.read_text() == edited == latest_source_ops(str(journal))[(LIB, TST)]["text"]
    tail = [json.loads(ln) for ln in journal.read_text().splitlines()][len(prior):]
    assert [r["verb"] for r in tail] == [ELEMENT] and len({r["ts"] for r in tail}) == 1
    out.write_text(edited.replace("is not None", "!= 0"))
    res = flip_module(str(journal), str(tmp_path / "repos"), LIB, TST)
    assert res["captured"] and res["element_grain"]["changed"] and not res["element_grain"]["born"]
    assert "!= 0" in latest_source_ops(str(journal))[(LIB, TST)]["text"]
    refused = journaled_emit(str(journal), emissions=[{"repo_key": LIB, "module_path": TST,
                                                       "import_name": "tests.test_store",
                                                       "text": edited, "path": str(out)}],
                             op={"op": "move", "symbols": ["test_open"], "identity": "keep"})
    assert "does not carry" in refused["error"] and "!= 0" in out.read_text()


def test_a_retired_captured_module_takes_its_elements_with_it():
    prior, state = _captured_state()
    gone = SourceState.over(prior + [_rec("retire", 300.0, {"repo_key": LIB, "module_path": TST})])
    assert (LIB, TST) not in gone.latest_ops()
    assert all(e.retired for e in gone.elements.elements.values() if e.module == MID)


def _state(fold):
    nodes = {i: (json.loads(json.dumps(r["wire"])), r["created_at"], r["updated_at"])
             for i, r in fold.nodes.items()}
    return nodes, set(fold.edges)


@pytest.mark.parametrize("step", ["capture", "rename", "absorb"])
def test_a_seeded_step_lands_where_the_whole_fold_lands(tmp_path, step):
    cap, _ = _capture(PREFIX)
    prefix, group = PREFIX, cap
    if step == "absorb":  # a birth, a retire and a re-placed follower in one write
        prefix = PREFIX + cap
        text = TEXT.replace(TEXT[TEXT.index("    def path"):TEXT.index("def test_open")], "\n\n") + \
            "\n\ndef test_two():\n    assert os.sep\n"
        recs, _ = absorb_records(SourceState.over(prefix), MID, text)
        group = [_rec(r["verb"], 300.0, r["args"]) for r in recs]
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
