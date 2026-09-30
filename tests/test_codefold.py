"""The code lane as a fold over the source journal (design amendment 2cc81d3b): the journal is
the inventory and the only source, ids follow the identity map as of each record, and every
node / edge time is the ts of the record that produced it."""

from cjm_dev_graph_schema.identity import code_module_node_id, code_symbol_node_id
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from cjm_context_graph_projection.codefold import CodeFold, fold_source_journal
from cjm_context_graph_projection.source_state import cutover_module, flip_module

REPO = "cjm-demo-lib"


def _src(ts, mp, text, op=None, repo=REPO, import_name=None):
    rec = {"verb": "source", "ts": ts, "generation": 1,
           "args": {"repo_key": repo, "module_path": mp, "text": text,
                    "import_name": import_name or mp[:-3].replace("/", ".")}}
    if op:
        rec["op"] = op
    return rec


def _retire(ts, mp, repo=REPO):
    return {"verb": "retire", "ts": ts, "generation": 1,
            "args": {"repo_key": repo, "module_path": mp}}


def _fold(tmp_path, *recs):
    fold = CodeFold(str(tmp_path))
    for r in recs:
        fold.apply(r)
    nodes, edges = fold.elements()
    return fold, {n["id"]: n for n in nodes}, edges


def _sym(nodes, name):
    return next(n for n in nodes.values()
                if n["label"] == DevNodeKinds.CODE_SYMBOL and n["properties"]["qualname"] == name)


def _edges(edges, rel):
    return {(e["source_id"], e["target_id"]): e for e in edges if e["relation_type"] == rel}


def test_the_journal_is_the_only_source(tmp_path):
    # Cut over, then the file is edited out-of-band and finally deleted: the graph never reads it.
    pkg = tmp_path / REPO / "cjm_demo_lib"
    pkg.mkdir(parents=True)
    (pkg / "a.py").write_text('"""A."""\n\n\ndef fa():\n    return 1\n')
    (pkg / "b.py").write_text('"""B."""\n\n\ndef fb():\n    return 2\n')  # never captured
    j = str(tmp_path / "source.jsonl")
    flip_module(j, str(tmp_path), REPO, "cjm_demo_lib/a.py")
    cutover_module(j, str(tmp_path), REPO, "cjm_demo_lib/a.py")
    (pkg / "a.py").write_text('"""A."""\n\n\ndef fa():\n    return 999\n')
    nodes, _ = fold_source_journal(j, str(tmp_path)).elements()
    names = {n["properties"].get("qualname") for n in nodes if n["label"] == DevNodeKinds.CODE_SYMBOL}
    assert names == {"fa"}  # the uncaptured b.py is not on the graph (ingest reports it)
    assert "999" not in next(n for n in nodes if n["properties"].get("qualname") == "fa")["properties"]["body"]
    (pkg / "a.py").unlink()
    nodes, _ = fold_source_journal(j, str(tmp_path)).elements()
    assert any(n["properties"].get("qualname") == "fa" for n in nodes)


def test_times_are_the_records_ts(tmp_path):
    mp = "cjm_demo_lib/a.py"
    fold, nodes, edges = _fold(
        tmp_path,
        _src(100.0, mp, "def fa():\n    return 1\n\n\ndef fb():\n    return 2\n"),
        _src(200.0, mp, "def fa():\n    return 1\n\n\ndef fb():\n    return 3\n"))
    fa, fb = _sym(nodes, "fa"), _sym(nodes, "fb")
    assert (fa["created_at"], fa["updated_at"]) == (100.0, 100.0)  # untouched by the edit below it
    assert (fb["created_at"], fb["updated_at"]) == (100.0, 200.0)  # changed at 200
    mod = nodes[code_module_node_id(REPO, mp)]
    assert mod["created_at"] == 100.0
    defines = _edges(edges, DevRelations.DEFINES)
    assert defines[(mod["id"], fb["id"])]["created_at"] == 100.0


def test_created_at_is_the_start_of_continuous_existence(tmp_path):
    mp = "cjm_demo_lib/a.py"
    _, nodes, _ = _fold(
        tmp_path,
        _src(100.0, mp, "def fa():\n    return 1\n\n\ndef fb():\n    return 2\n"),
        _src(200.0, mp, "def fa():\n    return 1\n"),
        _src(300.0, mp, "def fa():\n    return 1\n\n\ndef fb():\n    return 2\n"))
    fb = _sym(nodes, "fb")
    assert (fb["created_at"], fb["updated_at"]) == (300.0, 300.0)


def test_retire_removes_a_module_and_its_edges(tmp_path):
    a, b = "cjm_demo_lib/a.py", "cjm_demo_lib/b.py"
    _, nodes, edges = _fold(
        tmp_path,
        _src(100.0, a, "def helper():\n    return 1\n"),
        _src(110.0, b, "def use():\n    return helper()\n"),
        _retire(120.0, a))
    assert code_module_node_id(REPO, a) not in nodes
    assert not _edges(edges, DevRelations.CALLS)


def test_cross_module_calls_and_imports_resolve(tmp_path):
    a, b = "pkg/a.py", "pkg/b.py"
    _, nodes, edges = _fold(
        tmp_path,
        _src(100.0, a, "def foo():\n    return 1\n"),
        _src(110.0, b, "import os\n\nimport pkg.a\n\n\ndef bar():\n    return foo() + len(open('x').read())\n"))
    ma, mb = code_module_node_id(REPO, a), code_module_node_id(REPO, b)
    assert (mb, ma) in _edges(edges, DevRelations.IMPORTS)            # external `os` never minted
    calls = _edges(edges, DevRelations.CALLS)
    assert (_sym(nodes, "bar")["id"], _sym(nodes, "foo")["id"]) in calls
    assert calls[(_sym(nodes, "bar")["id"], _sym(nodes, "foo")["id"])]["created_at"] == 110.0


def test_a_calls_edge_is_born_when_its_name_becomes_unambiguous(tmp_path):
    a, b, c = "pkg/a.py", "pkg/b.py", "pkg/c.py"
    recs = [_src(100.0, a, "def helper():\n    return 1\n"),
            _src(110.0, b, "def helper():\n    return 2\n"),
            _src(120.0, c, "def use():\n    return helper()\n")]
    _, nodes, edges = _fold(tmp_path, *recs)
    assert not _edges(edges, DevRelations.CALLS)                      # ambiguous: never guessed
    _, nodes, edges = _fold(tmp_path, *recs, _retire(130.0, b))
    call = _edges(edges, DevRelations.CALLS)[(_sym(nodes, "use")["id"], _sym(nodes, "helper")["id"])]
    assert call["created_at"] == 130.0                                # when its justification began


def test_tests_edges_and_the_test_corpus(tmp_path):
    core, tst = "cjm_demo_lib/core.py", "tests/test_core.py"
    _, nodes, edges = _fold(
        tmp_path,
        _src(100.0, core, "def alpha(x):\n    return x + 1\n"),
        _src(110.0, tst, "from cjm_demo_lib.core import alpha\n\n\ndef test_alpha():\n    assert alpha(1) == 2\n"))
    tmod = nodes[code_module_node_id(REPO, tst)]
    assert tmod["properties"]["module_path"] == tst
    tests = _edges(edges, DevRelations.TESTS)
    assert (_sym(nodes, "test_alpha")["id"], _sym(nodes, "alpha")["id"]) in tests


def test_identity_follows_the_map_as_of_each_record(tmp_path):
    # 36f649d3 under the fold: a keep-identity rename keeps the born id (and its created_at),
    # a newcomer at the vacated name is generation 1, a pre-scheme rename re-keys.
    mp = "cjm_demo_lib/a.py"
    mid = code_module_node_id(REPO, mp)
    born = code_symbol_node_id(mid, "fa")
    recs = [_src(100.0, mp, "def fa():\n    return 1\n"),
            _src(200.0, mp, "def fa2():\n    return 1\n",
                 op={"op": "rename-symbol", "from": "fa", "to": "fa2", "identity": "keep"}),
            _src(300.0, mp, "def fa2():\n    return 1\n\n\ndef fa():\n    return 7\n",
                 op={"op": "add-symbol", "qualname": "fa"})]
    _, nodes, edges = _fold(tmp_path, *recs)
    fa2 = _sym(nodes, "fa2")
    assert fa2["id"] == born and fa2["created_at"] == 100.0 and fa2["updated_at"] == 200.0
    assert _sym(nodes, "fa")["id"] == code_symbol_node_id(mid, "fa", 1)
    assert (mid, born) in _edges(edges, DevRelations.DEFINES)
    _, nodes, _ = _fold(tmp_path, *recs, _src(400.0, mp, "def fa3():\n    return 1\n\n\ndef fa():\n    return 7\n",
                                           op={"op": "rename-symbol", "from": "fa2", "to": "fa3"}))
    assert _sym(nodes, "fa3")["id"] == code_symbol_node_id(mid, "fa3")


def test_a_keep_identity_move_keeps_created_at_across_its_records(tmp_path):
    # One refactor = one group: the symbol leaves one module and arrives in the other; the
    # removal settles at the group's end, so its created_at survives (pre-clock records
    # carry distinct ts but share the op envelope).
    a, b = "cjm_demo_lib/a.py", "cjm_demo_lib/b.py"
    move = {"op": "move", "symbols": ["fm"], "identity": "keep"}
    _, nodes, _ = _fold(
        tmp_path,
        _src(100.0, a, "def fm():\n    return 1\n\n\ndef keep():\n    return 2\n"),
        _src(110.0, b, "def other():\n    return 3\n"),
        _src(200.0, a, "def keep():\n    return 2\n", op=move),
        _src(200.5, b, "def other():\n    return 3\n\n\ndef fm():\n    return 1\n", op=move))
    fm = _sym(nodes, "fm")
    assert fm["id"] == code_symbol_node_id(code_module_node_id(REPO, a), "fm")  # its birth id
    assert fm["created_at"] == 100.0 and fm["updated_at"] == 200.5


def test_what_the_fold_cannot_project_is_reported(tmp_path):
    mp = "cjm_demo_lib/a.py"
    fold, nodes, _ = _fold(
        tmp_path,
        _src(100.0, mp, "def fa():\n    return 1\n"),
        _src(200.0, mp, "def fa(:\n"),                                 # does not parse
        _src(210.0, "nbs/00_core.ipynb", "{}"))
    assert [f["ts"] for f in fold.failures] == [200.0]
    assert _sym(nodes, "fa")["updated_at"] == 100.0                     # the previous state stands
    assert fold.live_notebooks() == [(REPO, "nbs/00_core.ipynb")]
