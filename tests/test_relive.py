"""The live half of the code fold (design amendment 2cc81d3b, build B2 of leg B 0e3508fd).

The pure half pins that a fold SEEDED the way the live step seeds it (the journal prefix
advanced, the indexes and the touched module loaded from held state) and then stepped over a
group lands exactly where a fold of the whole journal lands — nodes, times and edges. The
end-to-end half drives a batch of real live code verbs into a db (author, add-symbol, a name
made ambiguous, new-module, move, rename-symbol, rename-module, a flip-module absorb,
delete-module) and, after EVERY op, rebuilds the same source journal into a fresh db and
requires rebuild-diff CLEAN — a later op re-deriving a module must not mask an earlier op's
miss (the first real swap caught exactly that: callers in untouched modules): leg B's
acceptance."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cjm_context_graph_projection.codefold import CodeFold
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS
from cjm_dev_graph_schema.identity import code_module_node_id, code_symbol_node_id

LIB = "cjm-demo-lib"
CORE, UTIL, TST = "cjm_demo_lib/core.py", "cjm_demo_lib/util.py", "tests/test_core.py"


def _src(ts, path, text, op=None):
    rec = {"verb": "source", "ts": ts, "generation": 1,
           "args": {"repo_key": LIB, "module_path": path,
                    "import_name": path[:-3].replace("/", "."), "text": text}}
    if op:
        rec["op"] = op
    return rec


JOURNAL = [
    _src(100.0, UTIL, "def helper(x):\n    return x\n"),
    _src(101.0, CORE, "from cjm_demo_lib.util import helper\n\n\ndef alpha(x):\n"
                      "    return helper(x) + 1\n\n\ndef beta(y):\n    return alpha(y) * 2\n"),
    _src(102.0, TST, "from cjm_demo_lib.core import alpha\n\n\ndef test_alpha():\n"
                     "    assert alpha(1) == 2\n"),
    # a second `helper` makes the bare name ambiguous: alpha's CALLS edge must go
    _src(103.0, CORE, "from cjm_demo_lib.util import helper\n\n\ndef alpha(x):\n"
                      "    return helper(x) + 1\n\n\ndef beta(y):\n    return alpha(y) * 2\n\n\n"
                      "def helper(x):\n    return -x\n"),
    # a keep-identity rename in one group (defining module + importer share the ts)
    _src(104.0, CORE, "from cjm_demo_lib.util import helper\n\n\ndef alpha2(x):\n"
                      "    return helper(x) + 1\n\n\ndef beta(y):\n    return alpha2(y) * 2\n\n\n"
                      "def helper(x):\n    return -x\n",
         op={"op": "rename-symbol", "from": "alpha", "to": "alpha2", "identity": "keep"}),
    _src(104.0, TST, "from cjm_demo_lib.core import alpha2\n\n\ndef test_alpha():\n"
                     "    assert alpha2(1) == 2\n",
         op={"op": "rename-symbol", "from": "alpha", "to": "alpha2", "identity": "keep"}),
]


def _held(fold):
    """A fold's state as the db would hold it: per module, its node wires (times carried)
    and its local edges; plus the index wires the scopes read."""
    held = {}
    for key, d in fold.modules.items():
        nodes = [{**fold.nodes[i]["wire"], "created_at": fold.nodes[i]["created_at"],
                  "updated_at": fold.nodes[i]["updated_at"]} for i in d["nodes"]]
        held[key] = (json.loads(json.dumps(nodes)), json.loads(json.dumps(list(d["local"].values()))))
    return held


def _live_step(prefix, group, tmp_path):
    """The live step's seeding, in memory: a fold of the prefix stands in for the db."""
    db = CodeFold(str(tmp_path), normalize=True)
    for rec in prefix:
        db.apply(rec)
    db.settle()
    held = _held(db)
    live = CodeFold(str(tmp_path), normalize=True)
    for rec in prefix:
        live.advance(rec)
    keys = {(r["args"]["repo_key"], r["args"]["module_path"]) for r in group}
    live.seed_corpus({k: [w for w in held[k][0] if w["label"] in ("CodeModule", "CodeSymbol")]
                      for k in held})
    for k in keys:
        if k in held:
            live.seed_module(k, *held[k])
    for rec in group:
        live.apply(rec)
    live.settle()
    return live


def _state(fold):
    nodes = {i: (json.loads(json.dumps(r["wire"])), r["created_at"], r["updated_at"])
             for i, r in fold.nodes.items()}
    return nodes, set(fold.edges)


@pytest.mark.parametrize("prefix,group", [
    (2, 1),  # the test module arrives: TESTS + the test corpus's CALLS / IMPORTS
    (3, 1),  # a second `helper`: the name turns ambiguous corpus-wide
    (4, 2),  # a keep-identity rename across two modules in one group
])
def test_a_seeded_step_lands_where_the_whole_fold_lands(tmp_path, prefix, group):
    whole = CodeFold(str(tmp_path), normalize=True)
    for rec in JOURNAL[:prefix + group]:
        whole.apply(rec)
    whole.settle()
    live = _live_step(JOURNAL[:prefix], JOURNAL[prefix:prefix + group], tmp_path)
    w_nodes, w_edges = _state(whole)
    l_nodes, l_edges = _state(live)
    touched = {code_module_node_id(LIB, r["args"]["module_path"])
               for r in JOURNAL[prefix:prefix + group]}
    compared = 0
    for nid, (wire, c, u) in w_nodes.items():
        if nid in touched or wire["properties"].get("module_id") in touched:
            assert l_nodes.get(nid) == (wire, c, u), wire["properties"].get("qualname")
            compared += 1
    assert compared and set(l_nodes) <= set(w_nodes)
    # every resolved edge corpus-wide, and the local edges of the touched modules (an
    # untouched module's DEFINES / CONTAINS / ABOUT are never loaded — nothing moves them)
    local = ("ABOUT", "DEFINES", "CONTAINS")
    held_nodes = set(l_nodes)
    expect = {i for i, r in whole.edges.items()
              if r["wire"]["relation_type"] not in local or r["wire"]["source_id"] in held_nodes}
    assert l_edges == expect


def test_the_ambiguity_step_drops_the_calls_edge_and_keeps_the_rest(tmp_path):
    live = _live_step(JOURNAL[:3], JOURNAL[3:4], tmp_path)
    core = code_module_node_id(LIB, CORE)
    alpha = code_symbol_node_id(core, "alpha")
    calls = {(r["wire"]["source_id"], r["wire"]["target_id"]) for r in live.edges.values()
             if r["wire"]["relation_type"] == "CALLS"}
    assert not any(s == alpha for s, _ in calls)  # `helper` is ambiguous now
    assert (code_symbol_node_id(core, "beta"), alpha) in calls
    # alpha's content did not change (only its whole-file locator moved): its times hold
    assert (live.nodes[alpha]["created_at"], live.nodes[alpha]["updated_at"]) == (101.0, 101.0)


needs_capability = pytest.mark.skipif(
    not (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists(),
    reason=f"graph capability {DEFAULT_GRAPH_ID!r} not installed at {DEFAULT_MANIFESTS}",
)


def _cli(*args):
    return subprocess.run([sys.executable, "-m", "cjm_context_graph_projection.cli", *args],
                          capture_output=True, text=True)


FILES = {
    "cjm_demo_lib/__init__.py": "",
    UTIL: "def helper(x):\n    return x\n",
    CORE: ('"""Core."""\n\nfrom cjm_demo_lib.util import helper\n\n\ndef alpha(x):\n'
           "    return helper(x) + 1\n\n\ndef beta(y):\n    return alpha(y) * 2\n"),
    TST: "from cjm_demo_lib.core import alpha\n\n\ndef test_alpha():\n    assert alpha(1) == 2\n",
}


@needs_capability
def test_live_code_verbs_equal_their_rebuild(tmp_path):
    repos = tmp_path / "repos"
    for rel, text in FILES.items():
        (repos / LIB / rel).parent.mkdir(parents=True, exist_ok=True)
        (repos / LIB / rel).write_text(text)
    live = str(tmp_path / "live.db")
    sj = str(tmp_path / "source.jsonl")
    R = ("--repos-dir", str(repos))

    memory = tmp_path / "memory"
    memory.mkdir()
    checks = []

    def ok(*args):
        r = _cli("--graph-db-path", live, "--source-journal-path", sj, *args)
        assert r.returncode == 0, f"{args[0]} failed: {r.stderr or r.stdout}"
        return r.stdout

    def equals_its_rebuild(after):
        # The standing check after one op: the same source journal folded into a fresh db.
        rebuilt = str(tmp_path / f"rebuilt{len(checks)}.db")
        r = _cli("--graph-db-path", rebuilt, "--source-journal-path", sj, "ingest",
                 "--memory-dir", str(memory), "--no-repo-map", "--no-seed", *R)
        assert r.returncode == 0, r.stderr or r.stdout
        r = _cli("--graph-db-path", live, "rebuild-diff", "--against", rebuilt)
        assert r.returncode == 0 and "CLEAN" in r.stdout, (
            f"after {after}: the live db differs from its rebuild:\n{r.stdout}\n{r.stderr}")
        checks.append(after)

    def step(*args):
        ok(*args)
        equals_its_rebuild(args[0])

    # Capture: every flip is a source record the live step derives into the db.
    for rel in FILES:
        ok("flip-module", LIB, rel, *R)
        ok("emit-artifact", LIB, rel, *R)  # regenerate the file if the flip canonicalized
        ok("cutover", LIB, rel, *R)
    equals_its_rebuild("capture")
    core, util = code_module_node_id(LIB, CORE), code_module_node_id(LIB, UTIL)
    extra = code_module_node_id(LIB, "cjm_demo_lib/extra.py")
    # The batch: a body edit, new symbols, a name turned ambiguous corpus-wide, a module born
    # on-graph, a keep-identity move / rename / module rename, an absorbed plain edit, a delete.
    step("author", code_symbol_node_id(core, "alpha"), "--edit", "helper(x) + 1", "helper(x) + 2", *R)
    step("add-symbol", util, "--body", "def gamma(z):\n    return helper(z)", *R)
    step("add-symbol", core, "--body", "def helper(x):\n    return -x", *R)
    step("new-module", LIB, "cjm_demo_lib/extra.py", *R)
    step("add-symbol", extra, "--body", "def delta():\n    return 1", *R)
    step("move", code_symbol_node_id(util, "gamma"), extra, *R)
    step("rename-symbol", code_symbol_node_id(core, "beta"), "beta2", *R)
    step("rename-module", util, "cjm_demo_lib/tools.py", *R)
    tst = repos / LIB / TST
    tst.write_text("from cjm_demo_lib.core import alpha, beta2\n\n\ndef test_alpha():\n"
                   "    assert alpha(1) == 3\n\n\ndef test_beta():\n    assert beta2(1) == 6\n")
    step("flip-module", LIB, TST, *R)
    step("delete-module", extra, "--force", *R)

    assert len(checks) == 11
    # ... and not vacuously: the live db holds what the batch made, derived edges included.
    import sqlite3
    con = sqlite3.connect(live)
    q = lambda sql, *a: con.execute(sql, a).fetchall()
    paths = {p for (p,) in q("select json_extract(properties,'$.module_path') from nodes "
                             "where label='CodeModule'")}
    assert paths == {"cjm_demo_lib/__init__.py", CORE, "cjm_demo_lib/tools.py", TST}
    beta = code_symbol_node_id(core, "beta")  # the rename kept its birth id
    assert q("select json_extract(properties,'$.qualname') from nodes where id=?", beta) == [("beta2",)]
    rel = lambda s, r: {t for (t,) in q("select target_id from edges where source_id=? and "
                                         "relation_type=?", s, r)}
    alpha = code_symbol_node_id(core, "alpha")
    assert rel(beta, "CALLS") == {alpha}
    assert rel(alpha, "CALLS") == set()  # `helper` is defined twice: ambiguous, no edge
    test_beta = code_symbol_node_id(code_module_node_id(LIB, TST), "test_beta")
    assert beta in rel(test_beta, "TESTS")
    assert rel(code_module_node_id(LIB, TST), "IMPORTS") == {core}
