"""Cross-corpus CALLS/IMPORTS resolution + the structural convention audit (pure cores)."""

from cjm_dev_graph_schema.nodes import CodeSymbolNode
from cjm_dev_graph_schema.vocab import DevRelations

from cjm_context_graph_projection.conventions import compute_conventions
from cjm_context_graph_projection.codefold import CodeFold


# --- cross-corpus resolution (the code fold's scopes, 2cc81d3b) ---

def _resolve(tmp_path, modules):
    """Fold one source record per module and return the CALLS / IMPORTS edge pairs by name."""
    fold = CodeFold(str(tmp_path))
    for i, (mp, text) in enumerate(modules):
        fold.apply({"verb": "source", "ts": 100.0 + i,
                    "args": {"repo_key": "r", "module_path": mp, "text": text,
                             "import_name": mp[:-3].replace("/", ".")}})
    nodes, edges = fold.elements()
    name = {n["id"]: n["properties"].get("qualname") or n["properties"].get("import_name")
            for n in nodes}
    return {rel: {(name[e["source_id"]], name[e["target_id"]]) for e in edges
                  if e["relation_type"] == rel}
            for rel in (DevRelations.CALLS, DevRelations.IMPORTS)}


def test_resolve_cross_module_calls_and_imports(tmp_path):
    got = _resolve(tmp_path, [
        ("pkg/a.py", "def foo():\n    return 1\n"),
        # imports a (intra) + os (external); calls foo (cross-module) + open (builtin)
        ("pkg/b.py", "import os\n\nimport pkg.a\n\n\ndef bar():\n    return foo(), open, os\n")])
    assert ("pkg.b", "pkg.a") in got[DevRelations.IMPORTS]
    assert all(t != "os" for _s, t in got[DevRelations.IMPORTS])      # external import not minted
    assert ("bar", "foo") in got[DevRelations.CALLS]
    assert all(s != t for s, t in got[DevRelations.CALLS])


def test_ambiguous_call_name_is_not_resolved(tmp_path):
    # `helper` is defined in BOTH modules -> ambiguous -> a caller's `helper` call is skipped.
    got = _resolve(tmp_path, [
        ("pkg/a.py", "def helper():\n    return 1\n\n\ndef use():\n    return helper()\n"),
        ("pkg/b.py", "def helper():\n    return 2\n")])
    assert got[DevRelations.CALLS] == set()


# --- convention audit (pure) ---

def _nb_sym(module_id, qual, cell, desc=""):
    n = CodeSymbolNode(module_id=module_id, qualname=qual, symbol_kind="function", path="/x",
                       docstring=desc, properties={"cell_key": cell}).to_graph_node()
    return n


def test_conventions_flags_undocumented_no_docstring_and_non_granular():
    m = "mod-1"
    alpha = _nb_sym(m, "alpha", "c1", desc="Alpha.")          # documented (below) + has docstring
    beta = _nb_sym(m, "beta", "c2", desc="")                  # no docstring
    gamma = _nb_sym(m, "gamma", "c2", desc="Gamma.")          # shares cell c2 with beta -> non-granular
    helper = _nb_sym(m, "_helper", "c3", desc="")             # private -> not audited
    documented = {alpha["id"]}                                # only alpha has an incoming DOCUMENTS edge

    res = compute_conventions([alpha, beta, gamma, helper], documented)
    undoc = {u["qualname"] for u in res["undocumented"]}
    assert undoc == {"beta", "gamma"}                          # alpha documented; _helper private
    assert {u["qualname"] for u in res["no_docstring"]} == {"beta"}
    ng = res["non_granular_cells"]
    assert len(ng) == 1 and set(ng[0]["symbols"]) == {"beta", "gamma"}


def test_conventions_ignores_non_notebook_symbols_and_respects_scope():
    plain = CodeSymbolNode(module_id="m", qualname="plain", symbol_kind="function",
                           path="/x").to_graph_node()  # no cell_key -> not notebook-sourced
    nb = _nb_sym("m2", "nbfn", "c0", desc="")
    res = compute_conventions([plain, nb], documented_ids=set())
    assert {u["qualname"] for u in res["undocumented"]} == {"nbfn"}  # plain .py symbol not flagged
    # scope filters to one module
    assert compute_conventions([nb], set(), scope="other")["counts"]["undocumented"] == 0
