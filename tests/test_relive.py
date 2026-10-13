"""The live half of the code fold (design amendment 2cc81d3b, build B2 of leg B 0e3508fd).

The pure half pins that a fold SEEDED the way the live step seeds it (the journal prefix
advanced, the indexes and the touched module loaded from held state) and then stepped over a
group lands exactly where a fold of the whole journal lands — nodes, times and edges. The
end-to-end half births a repo captured and drives a batch of real live code verbs into a db
(author, add-symbol, new-module, move, a name made ambiguous, rename-symbol, rename-module, a
flip-module absorb, add-module, delete-module) and, after EVERY op, rebuilds the same source journal into a fresh db and
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


FORM = "cjm_demo_lib/form.py"
FILES = {
    "cjm_demo_lib/__init__.py": "",
    UTIL: "def helper(x):\n    return x\n",
    CORE: ('"""Core."""\n\nfrom cjm_demo_lib.util import helper\n\n\ndef alpha(x):\n'
           "    return helper(x) + 1\n\n\ndef beta(y):\n    return alpha(y) * 2\n"),
    TST: "from cjm_demo_lib.core import alpha\n\n\ndef test_alpha():\n    assert alpha(1) == 2\n",
    "tests/test_util.py": ("import sys\nfrom cjm_demo_lib.util import helper\nimport os\n\n\n"
                           "def test_helper():\n    assert helper(os.sep) and sys\n"),
    FORM: ("class Form:\n    @property\n    def row(self):\n        return self._row\n\n"
           "    @row.setter\n    def row(self, i):\n        self._row = i\n"),
}


def _born_repo(tmp_path):
    """The demo repo on disk, each module born captured from its file (flip-module), the file
    regenerated where its capture form differs (emit-artifact)."""
    repos = tmp_path / "repos"
    for rel, text in FILES.items():
        (repos / LIB / rel).parent.mkdir(parents=True, exist_ok=True)
        (repos / LIB / rel).write_text(text)
    live, sj = str(tmp_path / "live.db"), str(tmp_path / "source.jsonl")
    R = ("--repos-dir", str(repos))
    for rel in FILES:
        for verb in ("flip-module", "emit-artifact"):
            r = _cli("--graph-db-path", live, "--source-journal-path", sj, verb, LIB, rel, *R)
            assert r.returncode == 0, r.stderr or r.stdout
    return repos, live, sj, R


@needs_capability
def test_live_code_verbs_equal_their_rebuild(tmp_path):
    repos, live, sj, R = _born_repo(tmp_path)
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
        out = ok(*args)
        equals_its_rebuild(args[0])
        return out

    equals_its_rebuild("birth")
    assert (repos / LIB / "tests/test_util.py").read_text().startswith(
        "import os\nimport sys\n\nfrom cjm_demo_lib.util import helper\n")  # the derived layout
    core, util = code_module_node_id(LIB, CORE), code_module_node_id(LIB, UTIL)
    extra = code_module_node_id(LIB, "cjm_demo_lib/extra.py")
    tst, fm = code_module_node_id(LIB, TST), code_module_node_id(LIB, FORM)
    # The batch: a body edit (an accessor chain's member too), new symbols, a module born on-graph,
    # a keep-identity move, a name turned ambiguous corpus-wide (after the move: before it, the move
    # is refused -- finding 73714120), a keep-identity rename / module rename, absorbed plain edits,
    # births whole and empty, a delete -- on package and test modules alike.
    step("author", code_symbol_node_id(core, "alpha"), "--edit", "helper(x) + 1", "helper(x) + 2", *R)
    step("author", code_symbol_node_id(fm, "Form.row"), "--edit", "self._row = i", "self._row = int(i)", *R)
    assert "@row.setter\n    def row(self, i):\n        self._row = int(i)\n" in (repos / LIB / FORM).read_text()
    step("add-symbol", util, "--body", "def gamma(z):\n    return helper(z)", *R)
    step("new-module", LIB, "cjm_demo_lib/extra.py", *R)
    step("add-symbol", extra, "--body", "def delta():\n    return 1", *R)
    step("move", code_symbol_node_id(util, "gamma"), extra, *R)
    step("add-symbol", core, "--body", "def helper(x):\n    return -x", *R)
    step("rename-symbol", code_symbol_node_id(core, "beta"), "beta2", *R)
    step("rename-module", util, "cjm_demo_lib/tools.py", *R)
    (repos / LIB / CORE).write_text((repos / LIB / CORE).read_text() + "\n\ndef omega():\n    return 0\n")
    step("flip-module", LIB, CORE, *R)
    step("author", code_symbol_node_id(tst, "test_alpha"), "--edit", "== 2", "== 3", *R)
    step("add-symbol", tst, "--body", "def test_beta():\n    assert beta2(1) == 6", "--import",
         "from cjm_demo_lib.core import beta2", *R)
    (repos / LIB / TST).write_text((repos / LIB / TST).read_text() + "\n\ndef test_three():\n    assert alpha(3)\n")
    step("flip-module", LIB, TST, *R)
    body = tmp_path / "born.py"
    body.write_text('"""Born."""\n\nfrom cjm_demo_lib.core import alpha\n\n\ndef twice(x):\n'
                    "    return alpha(x) * 2\n")
    assert "born captured" in step("add-module", LIB, "cjm_demo_lib/born.py", "--body-file", str(body), *R)
    tbody = tmp_path / "test_born.py"
    tbody.write_text("import sys\nimport os\n\n\ndef test_born():\n    assert os and sys\n")
    out = step("add-module", LIB, "tests/test_born.py", "--body-file", str(tbody), *R)
    assert "born captured" in out and "+import os" in out  # the birth shows the form it lands
    step("new-module", LIB, "tests/test_empty.py", *R)
    step("add-symbol", code_module_node_id(LIB, "tests/test_empty.py"), "--body",
         "def test_empty():\n    assert True", *R)
    step("delete-module", extra, "--force", *R)

    assert len(checks) == 19
    assert "source" not in {json.loads(ln)["verb"] for ln in open(sj)}  # element records only
    # ... and not vacuously: the live db holds what the batch made, derived edges included.
    import sqlite3
    con = sqlite3.connect(live)
    q = lambda sql, *a: con.execute(sql, a).fetchall()
    paths = {p for (p,) in q("select json_extract(properties,'$.module_path') from nodes "
                             "where label='CodeModule'")}
    assert paths == {"cjm_demo_lib/__init__.py", CORE, "cjm_demo_lib/tools.py", TST, FORM,
                     "tests/test_util.py", "cjm_demo_lib/born.py", "tests/test_born.py",
                     "tests/test_empty.py"}
    beta = code_symbol_node_id(core, "beta")  # the rename kept its birth id
    assert q("select json_extract(properties,'$.qualname') from nodes where id=?", beta) == [("beta2",)]
    rel = lambda s, r: {t for (t,) in q("select target_id from edges where source_id=? and "
                                         "relation_type=?", s, r)}
    alpha = code_symbol_node_id(core, "alpha")
    assert rel(beta, "CALLS") == {alpha}
    assert rel(alpha, "CALLS") == set()  # `helper` is defined twice: ambiguous, no edge
    test_beta = code_symbol_node_id(tst, "test_beta")
    assert beta in rel(test_beta, "TESTS")
    assert rel(tst, "IMPORTS") == {core}


@needs_capability
def test_add_module_births_a_whole_module_equal_to_its_rebuild(tmp_path):
    # 177dcb07: one add-module journals a whole module born captured (its elements, one group) and
    # the live db equals the same journal folded fresh; a test module too;
    # a re-birth on a held path and a birth over a file already on disk both refuse.
    repos, live, sj, R = _born_repo(tmp_path)
    memory = tmp_path / "memory"
    memory.mkdir()

    def run(*args):
        return _cli("--graph-db-path", live, "--source-journal-path", sj, *args)

    born, born_test = "cjm_demo_lib/born.py", "tests/test_born.py"
    body = tmp_path / "born.py"
    body.write_text('"""Born whole."""\n\nfrom cjm_demo_lib.core import alpha\n\n\n'
                    "def twice(x):\n    return alpha(x) * 2\n\n\nclass Pair:\n"
                    "    def left(self):\n        return twice(1)\n")
    tbody = tmp_path / "test_born.py"
    tbody.write_text("from cjm_demo_lib.born import twice\n\n\ndef test_twice():\n"
                     "    assert twice(1) == 4\n")
    r = run("add-module", LIB, born, "--body-file", str(body), *R)
    assert r.returncode == 0, r.stderr or r.stdout
    r = run("add-module", LIB, born_test, "--body-file", str(tbody), *R)
    assert r.returncode == 0, r.stderr or r.stdout
    assert (repos / LIB / born).read_text() == body.read_text()  # already canonical: born byte-identical
    assert (repos / LIB / born_test).read_text() == tbody.read_text()
    rebuilt = str(tmp_path / "rebuilt.db")
    r = _cli("--graph-db-path", rebuilt, "--source-journal-path", sj, "ingest",
             "--memory-dir", str(memory), "--no-repo-map", "--no-seed", *R)
    assert r.returncode == 0, r.stderr or r.stdout
    r = _cli("--graph-db-path", live, "rebuild-diff", "--against", rebuilt)
    assert r.returncode == 0 and "CLEAN" in r.stdout, r.stdout + r.stderr
    import sqlite3
    con = sqlite3.connect(live)
    mid = code_module_node_id(LIB, born)
    names = {n for (n,) in con.execute(
        "select json_extract(properties,'$.qualname') from nodes where label='CodeSymbol' "
        "and json_extract(properties,'$.module_id')=?", (mid,))}
    assert {"twice", "Pair", "Pair.left"} <= names
    again = run("add-module", LIB, born, "--body-file", str(body), *R)
    assert again.returncode == 1 and "already exists" in again.stdout
    (repos / LIB / "cjm_demo_lib/filed.py").write_text("X = 1\n")
    over = run("add-module", LIB, "cjm_demo_lib/filed.py", "--body-file", str(body), *R)
    assert over.returncode == 1 and "captured with flip-module" in over.stdout
