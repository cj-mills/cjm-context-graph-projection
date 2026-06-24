"""Cross-corpus CALLS/IMPORTS resolution + the structural convention audit (pure cores)."""

from cjm_dev_graph_schema.nodes import CodeModuleNode, CodeSymbolNode
from cjm_dev_graph_schema.vocab import DevRelations

from cjm_context_graph_projection.conventions import compute_conventions
from cjm_context_graph_projection.devgraph import resolve_corpus_code_edges


# --- cross-corpus resolution ---

def _mod(repo, path, import_name, imports=None):
    return CodeModuleNode(repo_key=repo, module_path=path, path="/" + path, content_hash="h",
                          import_name=import_name, imports=imports or []).to_graph_node()


def _sym(module_id, qual, calls=None):
    return CodeSymbolNode(module_id=module_id, qualname=qual, symbol_kind="function",
                          path="/x", calls=calls or []).to_graph_node()


def test_resolve_cross_module_calls_and_imports():
    a = _mod("r", "pkg/a.py", "pkg.a")
    b = _mod("r", "pkg/b.py", "pkg.b", imports=["pkg.a", "os"])  # imports a (intra) + os (external)
    foo = _sym(a["id"], "foo")
    bar = _sym(b["id"], "bar", calls=["foo", "open"])            # calls foo (cross-module) + open (builtin)
    edges = resolve_corpus_code_edges([a, b, foo, bar])

    imports = [(e["source_id"], e["target_id"]) for e in edges if e["relation_type"] == DevRelations.IMPORTS]
    calls = [(e["source_id"], e["target_id"]) for e in edges if e["relation_type"] == DevRelations.CALLS]
    assert (b["id"], a["id"]) in imports          # pkg.b imports pkg.a (cross-module)
    assert all(t != "os" for _s, t in imports)    # external import not minted
    assert (bar["id"], foo["id"]) in calls        # bar calls foo across modules
    assert all(s != t for s, t in calls)          # no self-loops


def test_ambiguous_call_name_is_not_resolved():
    a = _mod("r", "pkg/a.py", "pkg.a")
    b = _mod("r", "pkg/b.py", "pkg.b")
    # `helper` is defined in BOTH modules -> ambiguous -> a caller's `helper` call is skipped.
    h1, h2 = _sym(a["id"], "helper"), _sym(b["id"], "helper")
    caller = _sym(a["id"], "use", calls=["helper"])
    edges = resolve_corpus_code_edges([a, b, h1, h2, caller])
    assert [e for e in edges if e["relation_type"] == DevRelations.CALLS] == []


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
