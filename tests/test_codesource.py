"""The code lane's element regime: capture, the source-state fold and the derivation (design abfc2829,
ruling 21b8fcca, design detail 6d35c07d; the tests pilot's leg 2)."""

import json

import pytest
from cjm_context_graph_primitives.journal import op_clock
from cjm_context_graph_projection.codefold import CodeFold
from cjm_context_graph_projection.codesource import (absorb_records, capture_records,
                                                     CodeSourceError, ELEMENT, ELEMENT_RETIRE,
                                                     PLACE, SourceState)
from cjm_context_graph_projection.relive import _touched_keys
from cjm_context_graph_projection.source_state import (birth_form, element_plan, flip_module,
                                                       IdentityWalk, journaled_emit,
                                                       latest_source_ops)
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


def test_a_snapshot_record_after_capture_is_reported_and_never_applied():
    cap, _ = _capture(PREFIX)
    state = SourceState.over(PREFIX + cap + [_src(300.0, TST, TEXT + "\n# edited\n")])
    assert state.latest_ops()[(LIB, TST)]["text"] == TEXT and len(state.conflicts) == 1


def test_a_write_to_a_live_snapshot_module_is_refused_and_journals_nothing(tmp_path):
    # The snapshot regime's write side is retired (design detail 25c1fadc): a journal still holding a
    # live snapshot module takes no write to it.
    journal = tmp_path / "source.jsonl"
    journal.write_text("".join(json.dumps(r) + "\n" for r in PREFIX))
    receipt = journaled_emit(str(journal), emissions=[{"repo_key": LIB, "module_path": TST,
                                                       "import_name": "tests.test_store",
                                                       "text": TEXT + "\n# edited\n"}], op={"op": "author"})
    assert "write side is retired" in receipt["error"]
    assert len(journal.read_text().splitlines()) == len(PREFIX)


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
    caller = journaled_emit(str(journal), emissions=[{"repo_key": LIB, "module_path": TST,
                                                      "import_name": "tests.test_store",
                                                      "text": edited, "path": str(out)}],
                            op={"op": "move", "symbols": ["elsewhere"], "identity": "keep"})
    assert not caller.get("error")  # a module the move only rewrites is an ordinary edit


def test_a_retired_captured_module_takes_its_elements_with_it():
    prior, state = _captured_state()
    gone = SourceState.over(prior + [_rec("retire", 300.0, {"repo_key": LIB, "module_path": TST})])
    assert (LIB, TST) not in gone.latest_ops()
    assert all(e.retired for e in gone.elements.elements.values() if e.module == MID)


def _state(fold):
    nodes = {i: (json.loads(json.dumps(r["wire"])), r["created_at"], r["updated_at"])
             for i, r in fold.nodes.items()}
    return nodes, set(fold.edges)


@pytest.mark.parametrize("step", ["capture", "rename", "absorb", "move"])
def test_a_seeded_step_lands_where_the_whole_fold_lands(tmp_path, step):
    cap, _ = _capture(PREFIX)
    prefix, group = PREFIX, cap
    if step == "move":  # an element and its members re-homed across two captured modules
        prefix = _two_captured()
        plans, _ = element_plan(SourceState.over(prefix),
                                [_emit(TST, TEXT.replace(FAKE, "")),
                                 _emit(OTHER, OTHER_TEXT + "\n\n" + FAKE.rstrip("\n") + "\n")],
                                op={"op": "move", "symbols": ["FakeDeps"], "identity": "keep"})
        group = [_rec(r["verb"], 300.0, r["args"]) for i in (0, 1) for r in plans[i][0]]
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
    assert keys[0] == (LIB, TST) and set(keys) <= {(LIB, TST), (LIB, OTHER)}
    live.seed_corpus({k: [w for w in held[k][0] if w["label"] in ("CodeModule", "CodeSymbol")] for k in held})
    for k in keys:
        live.seed_module(k, *held[k])
    for r in group:
        live.apply(r)
    live.settle()
    w_nodes, w_edges = _state(whole)
    l_nodes, l_edges = _state(live)
    MIDS = {code_module_node_id(*k) for k in keys}  # the modules the step re-derives
    for nid, val in w_nodes.items():
        if nid in MIDS or val[0]["properties"].get("module_id") in MIDS:
            assert l_nodes.get(nid) == val, val[0]["properties"].get("qualname")
    local = ("ABOUT", "DEFINES", "CONTAINS")
    expect = {i for i, r in whole.edges.items()
              if r["wire"]["relation_type"] not in local or r["wire"]["source_id"] in set(l_nodes)}
    assert l_edges == expect


# -- move, regroup, rename-module in the element regime (design detail aba8ec79) -----------------------
OTHER = "tests/test_other.py"
MID2 = code_module_node_id(LIB, OTHER)
OTHER_TEXT = '''"""Other tests."""

import json


def test_json():
    assert json.dumps(1)
'''


def _two_captured():
    """TST and OTHER both captured (the tests phase), as one journal."""
    prefix = PREFIX + [_src(103.0, OTHER, OTHER_TEXT), _rec("cutover", 104.0, {"repo_key": LIB, "module_path": OTHER})]
    walk = IdentityWalk()
    for r in prefix:
        walk.apply(r)
    walk.flush()
    ever, recs = set(walk.state.elements.ever), []
    for path, text in ((TST, TEXT), (OTHER, OTHER_TEXT)):
        rs, _ = capture_records(LIB, LIB, path, path[:-3].replace("/", "."), text,
                                symbol_identity=walk.ident.for_module(LIB, path), ever=ever)
        ever |= {r["args"]["element"] for r in rs if r["args"].get("element")}
        recs += [{**r, "ts": 200.0, "generation": 1} for r in rs]
    return prefix + recs


def _emit(mp, text, **kw):
    return {"repo_key": LIB, "module_path": mp, "import_name": mp[:-3].replace("/", "."), "text": text, **kw}


FAKE = TEXT[TEXT.index("class FakeDeps"):TEXT.index("def test_open")]


def test_a_move_between_captured_modules_keeps_the_ids_and_the_bindings_travel(tmp_path):
    prior = _two_captured()
    state = SourceState.over(prior)
    held = {e.element for e in state.elements.elements.values() if e.module == MID}
    fake = _id(state, "class FakeDeps")
    subtree = {fake, _id(state, "def path"), _id(state, 'name = "fake"')}
    a_text = TEXT.replace(FAKE, "")
    b_text = OTHER_TEXT + "\n\n" + FAKE.rstrip("\n") + "\n"
    plans, _ = element_plan(state, [_emit(TST, a_text), _emit(OTHER, b_text)],
                            op={"op": "move", "symbols": ["FakeDeps"], "identity": "keep"})
    (a_recs, a_rep), (b_recs, b_rep) = plans[0], plans[1]
    assert not a_rep["retired"] and set(a_rep["departed"]) == subtree
    assert set(b_rep["arrived"]) == subtree and not b_rep["born"]
    assert {r["args"]["element"] for r in b_recs if r["verb"] == PLACE} >= subtree
    assert "import os" in b_rep["text"]  # the moved method's binding travelled
    assert "import os" not in a_rep["text"] and "import sys" in a_rep["text"]  # left with its owner; sys stays
    recs = [_rec(r["verb"], 300.0, r["args"]) for r in a_recs + b_recs]
    fold, after, edges = _fold(prior + recs, tmp_path)
    assert not fold.failures
    assert after[fake]["properties"]["module_id"] == MID2
    assert held <= set(after)  # nothing the source held left the graph: the moved ones were re-homed
    inner = [n for n in after.values() if n["properties"].get("qualname") == "FakeDeps.path.inner"]
    assert inner and inner[0]["properties"]["module_id"] == MID2


def test_regroup_into_a_new_test_module_births_it_captured(tmp_path):
    prior = _two_captured()
    state = SourceState.over(prior)
    test_open = _id(state, "def test_open")
    new_path = "tests/test_born.py"
    a_text = TEXT[:TEXT.index("def test_open")].rstrip("\n") + "\n"
    b_text = "def test_open(tmp_store):\n    assert tmp_store\n"
    plans, _ = element_plan(state, [_emit(TST, a_text), _emit(new_path, b_text, born=True)],
                            op={"op": "regroup", "symbols": ["test_open"], "identity": "keep"})
    b_recs, b_rep = plans[1]
    assert b_recs[0]["verb"] == "module" and b_recs[-1]["verb"] == "capture" and b_rep["restated"] == "born"
    assert test_open in b_rep["arrived"] and "from conftest import tmp_store" in b_rep["text"]
    recs = [_rec(r["verb"], 300.0, r["args"]) for r in plans[0][0] + b_recs]
    after_state = SourceState.over(prior + recs)
    assert after_state.latest_ops()[(LIB, new_path)]["text"] == b_rep["text"]
    assert (LIB, new_path) in after_state.sourced
    fold, after, _ = _fold(prior + recs, tmp_path)
    assert not fold.failures and after[test_open]["properties"]["module_id"] == b_recs[0]["args"]["module"]


def test_rename_module_keeps_the_module_id_and_moves_the_key(tmp_path):
    journal = tmp_path / "source.jsonl"
    prior = _two_captured()
    journal.write_text("".join(json.dumps(r) + "\n" for r in prior))
    new_path = "tests/test_store_renamed.py"
    receipt = journaled_emit(str(journal), emissions=[_emit(new_path, TEXT, path=str(tmp_path / "out.py"))],
                             retires=[{"repo_key": LIB, "module_path": TST, "superseded_by": new_path}],
                             op={"op": "rename-module", "from": TST, "to": new_path, "identity": "keep"})
    assert not receipt.get("error")
    assert [e["verb"] for e in receipt["events"]] == ["elements"] and receipt["events"][0]["restated"] == "path"
    latest = latest_source_ops(str(journal))
    assert (LIB, TST) not in latest and latest[(LIB, new_path)]["text"] == TEXT
    records = [json.loads(ln) for ln in journal.read_text().splitlines()]
    sourced = SourceState.over(records).sourced
    assert (LIB, new_path) in sourced and (LIB, TST) not in sourced
    fold, after, _ = _fold(records, tmp_path)
    assert not fold.failures
    assert after[MID]["properties"]["module_path"] == new_path  # the module kept its id
    assert all(n["properties"].get("module_id") in (None, MID, MID2, code_module_node_id(LIB, CONF))
               for n in after.values())


MOVE = lambda *names: {"op": "move", "symbols": list(names), "identity": "keep"}


def _journal(tmp_path, records):
    journal = tmp_path / "source.jsonl"
    journal.write_text("".join(json.dumps(r) + "\n" for r in records))
    return str(journal)


def _records(journal):
    return [json.loads(ln) for ln in open(journal).read().splitlines()]


def test_a_def_born_after_its_module_was_renamed_carries_its_closure_from_its_own_birth(tmp_path):
    journal = _journal(tmp_path, _two_captured())
    new_path = "tests/test_store_renamed.py"
    with op_clock():
        journaled_emit(journal, emissions=[_emit(new_path, TEXT)],
                       retires=[{"repo_key": LIB, "module_path": TST, "superseded_by": new_path}],
                       op={"op": "rename-module", "from": TST, "to": new_path, "identity": "keep"})
    with op_clock():
        receipt = journaled_emit(journal, emissions=[_emit(new_path, TEXT + "\n\ndef test_new():\n    def inner():\n"
                                                           "        return 1\n    assert inner()\n")],
                                 op={"op": "author"})
    assert not receipt.get("error"), receipt["error"]
    fold, after, _ = _fold(_records(journal), tmp_path)
    assert not fold.failures
    born = code_symbol_node_id(MID, "test_new")  # born in the module that kept its id
    assert born in after and code_symbol_node_id(MID, "test_new.inner") in after


def test_a_symbol_the_write_moves_that_no_module_gives_up_is_refused(tmp_path):
    journal = _journal(tmp_path, _two_captured())
    receipt = journaled_emit(journal, emissions=[_emit(TST, TEXT + "\n\ndef test_x():\n    pass\n")],
                             op=MOVE("test_x"))
    assert "which no module in the write gives up" in receipt["error"]
    receipt = journaled_emit(journal, emissions=[_emit(TST, TEXT.replace(FAKE, ""))], op=MOVE("FakeDeps"))
    assert "for no module in the write" in receipt["error"]
    assert len(_records(journal)) == len(_two_captured())


def test_the_live_step_re_derives_the_module_a_move_leaves():
    prior = _two_captured()
    state = SourceState.over(prior)
    fake = _id(state, "class FakeDeps")
    keys = _touched_keys([_rec(PLACE, 300.0, {"element": fake, "module": MID2, "parent": None, "after": None})], state)
    assert set(keys) == {(LIB, TST), (LIB, OTHER)}


# -- every module is born captured (the ruling bef8e6b8; design detail 25c1fadc) -----------------------
UNSORTED = "tests/test_unsorted.py"
UNSORTED_TEXT = "import sys\nimport os\n\n\ndef test_paths():\n    assert os and sys\n"


def _files(tmp_path, *modules):
    """The repos root holding each (path, text) module's file."""
    repos = tmp_path / "repos"
    for mp, text in modules:
        (repos / LIB / mp).parent.mkdir(parents=True, exist_ok=True)
        (repos / LIB / mp).write_text(text)
    return repos


def test_a_module_is_born_captured_whatever_its_phase(tmp_path):
    journal = _journal(tmp_path, [])
    born, empty, pkg = "tests/test_born.py", "tests/test_empty.py", "cjm_demo_lib/born.py"
    form = birth_form(journal, LIB, born, "tests.test_born", UNSORTED_TEXT)
    assert form == "import os\nimport sys\n\n\ndef test_paths():\n    assert os and sys\n"
    assert birth_form(journal, LIB, pkg, "cjm_demo_lib.born", UNSORTED_TEXT) == form  # a package too
    repos = _files(tmp_path)
    for mp, text in ((born, UNSORTED_TEXT), (empty, ""), (pkg, UNSORTED_TEXT)):
        with op_clock():
            receipt = journaled_emit(journal, emissions=[_emit(mp, text, born=True,
                                                               path=str(repos / LIB / mp))],
                                     op={"op": "add-module" if text else "new-module"})
        assert not receipt.get("error"), receipt["error"]
        assert receipt["events"][0]["verb"] == "elements" and receipt["events"][0]["restated"] == "born"
    records = _records(journal)
    assert "source" not in {r["verb"] for r in records} and [r["verb"] for r in records].count("capture") == 3
    composed = latest_source_ops(journal)
    assert composed[(LIB, born)]["text"] == form == (repos / LIB / born).read_text()
    assert composed[(LIB, empty)]["text"] == "" == (repos / LIB / empty).read_text()
    with pytest.raises(CodeSourceError, match="already holds"):
        birth_form(journal, LIB, born, "tests.test_born", UNSORTED_TEXT)
    with pytest.raises(CodeSourceError, match="element form"):
        birth_form(journal, LIB, "nbs/core.ipynb", "", "{}")
    fold, _, _ = _fold(records, tmp_path)
    assert not fold.failures


def test_flip_module_births_a_file_born_module_captured_and_refuses_a_notebook(tmp_path):
    journal = _journal(tmp_path, [])
    filed, pkg = "tests/test_filed.py", "cjm_demo_lib/filed.py"
    repos = _files(tmp_path, (filed, UNSORTED_TEXT), (pkg, UNSORTED_TEXT), ("nbs/core.ipynb", "{}"))
    for mp in (filed, pkg):
        n = len(_records(journal))
        res = flip_module(journal, str(repos), LIB, mp)
        assert res["captured"] and res["graph_sourced"] and "BORN CAPTURED" in res["note"]
        tail = _records(journal)[n:]
        assert tail[0]["verb"] == "module" and tail[-1]["verb"] == "capture"
        assert latest_source_ops(journal)[(LIB, mp)]["text"].startswith("import os\nimport sys\n")
    assert "source" not in {r["verb"] for r in _records(journal)}
    (repos / LIB / pkg).write_text(UNSORTED_TEXT.replace("os and sys", "sys and os"))
    res = flip_module(journal, str(repos), LIB, pkg)  # the out-of-band edit absorbed at element grain
    assert res["captured"] and res["element_grain"]["changed"] and "CAPTURED" in res["note"]
    res = flip_module(journal, str(repos), LIB, "nbs/core.ipynb")
    assert "flip-to-py" in res["error"] and not res["captured"]


def test_a_born_module_captures_a_property_accessor_chain_as_one_symbol(tmp_path):
    fake = "tests/test_fake.py"
    text = ("class Fake:\n    @property\n    def row(self):\n        return self._row\n\n"
            "    @row.setter\n    def row(self, i):\n        self._row = i\n\n\n"
            "def test_row():\n    f = Fake()\n    f.row = 2\n    assert f.row == 2\n")
    journal = _journal(tmp_path, [])
    repos = _files(tmp_path, (fake, text))
    assert flip_module(journal, str(repos), LIB, fake)["captured"]
    assert latest_source_ops(journal)[(LIB, fake)]["text"] == text  # the chain composes back as written
    elements = [r["args"] for r in _records(journal) if r["verb"] == "element"]
    assert [e["text"].split("\n")[0] for e in elements if "def row" in e["text"]] == ["@property"]
    fold, nodes, _ = _fold(_records(journal), tmp_path)
    assert not fold.failures
    rows = [n for n in nodes.values() if n["label"] == "CodeSymbol"
            and n["properties"]["qualname"] == "Fake.row"]
    assert len(rows) == 1 and rows[0]["properties"]["decorators"] == ["property", "setter"]
    assert rows[0]["properties"]["lineno"] == 3  # the getter's: the attribute's first def


def test_a_birth_with_a_duplicate_qualname_is_refused_by_name_and_journals_nothing(tmp_path):
    dup = "tests/test_dup.py"
    text = "def test_a():\n    assert 1\n\n\ndef test_a():\n    assert 2\n"  # pytest runs only the second
    journal = _journal(tmp_path, [])
    repos = _files(tmp_path, (dup, text))
    res = flip_module(journal, str(repos), LIB, dup)
    assert "two definitions of test_a" in res["error"] and not res["captured"]
    assert _records(journal) == []
