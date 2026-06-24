"""Refactoring-candidate identification (pure compute over code-graph slices)."""

from cjm_context_graph_projection.refactor import compute_refactor_candidates


def _mod(mid, repo, path):
    return {"id": mid, "label": "CodeModule", "properties": {"repo_key": repo, "module_path": path}}


def _sym(sid, mid, qual, kind="function", cell_key=None):
    p = {"module_id": mid, "qualname": qual, "symbol_kind": kind}
    if cell_key is not None:
        p["cell_key"] = cell_key
    return {"id": sid, "label": "CodeSymbol", "properties": p}


def test_single_consumer_cross_repo_is_relocation_not_cycle():
    mods = [_mod("MA", "lib-a", "a/x.py"), _mod("MB", "lib-b", "b/y.py")]
    syms = [_sym("S1", "MA", "helper"), _sym("S2", "MB", "caller")]
    calls = [("S2", "S1")]                      # b.caller -> a.helper (b depends on a)
    imports = [("MB", "MA")]                    # b imports a (the normal direction)
    out = compute_refactor_candidates(syms, mods, calls, imports)
    assert out["counts"]["relocation"] == 1 and out["counts"]["relocation_cycles"] == 0
    assert out["relocation"][0]["qualname"] == "helper" and out["relocation"][0]["cycle"] is False


def test_mutual_import_marks_relocation_cycle():
    mods = [_mod("MA", "lib-a", "a/x.py"), _mod("MB", "lib-b", "b/y.py")]
    syms = [_sym("S1", "MA", "helper"), _sym("S2", "MB", "caller")]
    calls = [("S2", "S1")]                      # b.caller -> a.helper
    imports = [("MB", "MA"), ("MA", "MB")]      # AND a imports b -> a<->b cycle around helper
    out = compute_refactor_candidates(syms, mods, calls, imports)
    assert out["counts"]["relocation_cycles"] == 1 and out["relocation"][0]["cycle"] is True


def test_within_repo_caller_is_not_relocation():
    mods = [_mod("MA", "lib-a", "a/x.py")]
    syms = [_sym("S1", "MA", "helper"), _sym("S2", "MA", "caller")]
    out = compute_refactor_candidates(syms, mods, [("S2", "S1")], [])
    assert out["counts"]["relocation"] == 0


def test_dead_code_flags_uncalled_public_excludes_dunder_private_init():
    mods = [_mod("MA", "lib-a", "a/x.py"), _mod("IN", "lib-a", "a/__init__.py")]
    syms = [_sym("S1", "MA", "orphan"),                       # no callers -> dead
            _sym("S2", "MA", "__init__", kind="method"),      # dunder/nested -> skip
            _sym("S3", "MA", "_private"),                     # private -> skip
            _sym("S4", "IN", "reexport"),                     # in __init__.py -> skip
            _sym("S5", "MA", "used")]
    calls = [("S1", "S5")]                                    # S5 has a caller
    out = compute_refactor_candidates(syms, mods, calls, [])
    names = {d["qualname"] for d in out["dead_code"]}
    assert names == {"orphan"}


def test_consolidation_same_name_across_repos():
    mods = [_mod("MA", "lib-a", "a/x.py"), _mod("MB", "lib-b", "b/y.py"), _mod("MC", "lib-a", "a/z.py")]
    syms = [_sym("S1", "MA", "corpus_graph_elements"),
            _sym("S2", "MB", "corpus_graph_elements"),        # same name, different repo -> consolidate
            _sym("S3", "MC", "unique_one")]
    out = compute_refactor_candidates(syms, mods, [], [])
    assert out["counts"]["consolidation"] == 1
    g = out["consolidation"][0]
    assert g["name"] == "corpus_graph_elements" and g["repos"] == ["lib-a", "lib-b"]


def test_same_name_within_one_repo_is_not_consolidation():
    mods = [_mod("MA", "lib-a", "a/x.py"), _mod("MB", "lib-a", "a/y.py")]
    syms = [_sym("S1", "MA", "helper"), _sym("S2", "MB", "helper")]  # same repo -> not a candidate
    out = compute_refactor_candidates(syms, mods, [], [])
    assert out["counts"]["consolidation"] == 0


def test_split_divergent_vs_shared_neighborhood():
    mods = [_mod("MA", "lib-a", "a/core.py")]
    # two public symbols sharing one cell; plus their neighbors.
    syms = [_sym("S1", "MA", "alpha", cell_key="c0"), _sym("S2", "MA", "beta", cell_key="c0"),
            _sym("NA", "MA", "na"), _sym("NB", "MA", "nb")]
    # divergent: alpha->na, beta->nb (no shared neighbor) -> split candidate
    out = compute_refactor_candidates(syms, mods, [("S1", "NA"), ("S2", "NB")], [])
    assert out["counts"]["split"] == 1
    # shared: both call the SAME neighbor -> a concept pair, not a split
    out2 = compute_refactor_candidates(syms, mods, [("S1", "NA"), ("S2", "NA")], [])
    assert out2["counts"]["split"] == 0


def test_scope_restricts_to_one_repo():
    mods = [_mod("MA", "lib-a", "a/x.py"), _mod("MB", "lib-b", "b/y.py")]
    syms = [_sym("S1", "MA", "a_orphan"), _sym("S2", "MB", "b_orphan")]
    out = compute_refactor_candidates(syms, mods, [], [], scope="lib-a")
    assert {d["qualname"] for d in out["dead_code"]} == {"a_orphan"}
