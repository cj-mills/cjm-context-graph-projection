"""Module cohesion audit (pure compute over code-graph slices)."""

from cjm_context_graph_projection.cohesion import compute_cohesion, _tokens


def _mod(mid, repo, path):
    return {"id": mid, "label": "CodeModule", "properties": {"repo_key": repo, "module_path": path}}


def _sym(sid, mid, qual, kind="function"):
    return {"id": sid, "label": "CodeSymbol",
            "properties": {"module_id": mid, "qualname": qual, "symbol_kind": kind}}


def test_tokens_snake_and_camel():
    assert _tokens("CapabilityError") == {"capability", "error"}
    assert _tokens("is_apple_silicon") == {"is", "apple", "silicon"}
    assert _tokens("set_config") == {"config"}            # generic `set` dropped as a stop-token
    assert _tokens("get_torch_device") == {"torch", "device"}


def test_under_split_flags_disconnected_grabbag():
    # one module, 4 public symbols, two disjoint call-clusters, no shared name token.
    mods = [_mod("M", "lib-a", "a/core.py")]
    syms = [_sym("A1", "M", "pil_to_tensor"), _sym("A2", "M", "tensor_to_pil"),
            _sym("B1", "M", "get_device"), _sym("B2", "M", "move_to_device")]
    # cluster A: A1<->A2 ; cluster B: B1<->B2 ; A and B never touch -> 2 components.
    calls = [("A1", "A2"), ("B1", "B2")]
    out = compute_cohesion(syms, mods, calls)
    assert out["counts"]["under_split"] == 1
    u = out["under_split"][0]
    assert u["module_path"] == "a/core.py" and u["num_components"] == 2


def test_name_family_is_not_flagged():
    # 4 predicates that never call each other but share the `is` token -> one cluster.
    mods = [_mod("M", "lib-a", "a/platform.py")]
    syms = [_sym("S1", "M", "is_apple_silicon"), _sym("S2", "M", "is_linux"),
            _sym("S3", "M", "is_macos"), _sym("S4", "M", "is_windows")]
    out = compute_cohesion(syms, mods, [])      # no calls, but `is` token couples them
    assert out["counts"]["under_split"] == 0


def test_dominant_core_plus_satellites_is_damped():
    # 4 symbols in one call-cluster + 1 unrelated satellite -> dominant core, not a grab-bag.
    mods = [_mod("M", "lib-a", "a/queue.py")]
    syms = [_sym("S1", "M", "submit"), _sym("S2", "M", "start"), _sym("S3", "M", "drain"),
            _sym("S4", "M", "stop"), _sym("X", "M", "zzz_unrelated")]
    calls = [("S1", "S2"), ("S2", "S3"), ("S3", "S4")]   # core cluster of 4 + 1 isolated
    out = compute_cohesion(syms, mods, calls)
    assert out["counts"]["under_split"] == 0 and out["counts"]["dominant_damped"] == 1


def test_cohesive_module_not_flagged():
    # 4 symbols, all in one connected call-cluster -> cohesive, no finding.
    mods = [_mod("M", "lib-a", "a/queue.py")]
    syms = [_sym("S1", "M", "submit"), _sym("S2", "M", "start"),
            _sym("S3", "M", "drain"), _sym("S4", "M", "stop")]
    calls = [("S1", "S2"), ("S2", "S3"), ("S3", "S4")]   # a chain -> 1 component
    out = compute_cohesion(syms, mods, calls)
    assert out["counts"]["under_split"] == 0


def test_under_split_respects_min_symbols():
    mods = [_mod("M", "lib-a", "a/small.py")]
    syms = [_sym("S1", "M", "alpha"), _sym("S2", "M", "beta")]  # 2 < min 4
    out = compute_cohesion(syms, mods, [])
    assert out["counts"]["under_split"] == 0


def test_over_split_helper_used_only_by_one_other_module():
    mods = [_mod("MA", "lib-a", "a/helpers.py"), _mod("MB", "lib-a", "a/pipeline.py")]
    syms = [_sym("H", "MA", "build_thing"),               # the helper
            _sym("C1", "MB", "run"), _sym("C2", "MB", "step")]
    calls = [("C1", "H"), ("C2", "H")]                   # both callers live in pipeline.py
    out = compute_cohesion(syms, mods, calls)
    assert out["counts"]["over_split"] == 1
    o = out["over_split"][0]
    assert o["qualname"] == "build_thing" and o["consumer_module"] == "a/pipeline.py"


def test_over_split_damps_driver_consumer():
    # a CLI that dispatches to 4 command modules is a driver; its helpers are expected layering.
    mods = ([_mod("CLI", "lib-a", "a/cli.py")]
            + [_mod(f"M{i}", "lib-a", f"a/cmd{i}.py") for i in range(4)])
    syms = ([_sym("D", "CLI", "main")]
            + [_sym(f"C{i}", f"M{i}", f"cmd{i}") for i in range(4)])
    calls = [("D", f"C{i}") for i in range(4)]   # cli.main -> 4 distinct command modules
    out = compute_cohesion(syms, mods, calls)
    assert out["counts"]["over_split"] == 0 and out["counts"]["over_split_driver_damped"] == 4


def test_over_split_excludes_multi_module_and_cross_repo_callers():
    mods = [_mod("MA", "lib-a", "a/h.py"), _mod("MB", "lib-a", "a/p.py"),
            _mod("MC", "lib-a", "a/q.py"), _mod("MX", "lib-b", "b/y.py")]
    syms = [_sym("H", "MA", "shared"), _sym("C1", "MB", "u1"), _sym("C2", "MC", "u2"),
            _sym("H2", "MA", "xrepo"), _sym("CX", "MX", "ux")]
    calls = [("C1", "H"), ("C2", "H"),    # H used by two modules -> not over_split
             ("CX", "H2")]                # H2 used by another REPO -> relocation, not over_split
    out = compute_cohesion(syms, mods, calls)
    assert out["counts"]["over_split"] == 0


def test_scope_restricts_to_one_repo():
    mods = [_mod("MA", "lib-a", "a/h.py"), _mod("MB", "lib-a", "a/p.py"),
            _mod("MC", "lib-b", "b/h.py"), _mod("MD", "lib-b", "b/p.py")]
    syms = [_sym("HA", "MA", "ha"), _sym("CA", "MB", "ca"),
            _sym("HB", "MC", "hb"), _sym("CB", "MD", "cb")]
    calls = [("CA", "HA"), ("CB", "HB")]
    out = compute_cohesion(syms, mods, calls, scope="lib-a")
    assert out["counts"]["over_split"] == 1 and out["over_split"][0]["repo"] == "lib-a"
