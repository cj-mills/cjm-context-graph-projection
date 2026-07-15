"""Import rewriting for the `move` op (the caller `from A import S` -> `from B import S`)."""

from cjm_context_graph_projection.refactor_ops import rewrite_symbol_import


def test_rewrite_sole_import():
    text = "from pkg.a import S\n\n\nx = S()\n"
    out, changed = rewrite_symbol_import(text, "pkg.a", "pkg.b", "S")
    assert changed and "from pkg.b import S\n" in out and "from pkg.a import S" not in out


def test_rewrite_splits_multi_name_import():
    text = "from pkg.a import S, T, U\n\nx = S()\n"
    out, changed = rewrite_symbol_import(text, "pkg.a", "pkg.b", "S")
    assert changed
    assert "from pkg.a import T, U\n" in out and "from pkg.b import S\n" in out


def test_rewrite_preserves_alias():
    text = "from pkg.a import S as Sym, T\n"
    out, changed = rewrite_symbol_import(text, "pkg.a", "pkg.b", "S")
    assert "from pkg.a import T\n" in out and "from pkg.b import S as Sym\n" in out


def test_rewrite_parenthesized_import():
    text = "from pkg.a import (\n    S,\n    T,\n)\n\nx=1\n"
    out, changed = rewrite_symbol_import(text, "pkg.a", "pkg.b", "S")
    assert changed and "from pkg.b import S\n" in out and "T" in out


def test_no_match_unchanged():
    text = "from pkg.a import T\nimport os\n"
    out, changed = rewrite_symbol_import(text, "pkg.a", "pkg.b", "S")
    assert not changed and out == text


def test_other_module_import_untouched():
    text = "from pkg.c import S\n"  # same symbol name, different module -> not ours
    out, changed = rewrite_symbol_import(text, "pkg.a", "pkg.b", "S")
    assert not changed


def test_rewrite_relative_import_cross_repo():
    text = "from .journal import append_write, read_journal\n"
    out, changed = rewrite_symbol_import(text, "pkg_a.journal", "pkg_b.journal", "append_write",
                                         caller_package="pkg_a")
    assert changed
    assert "from .journal import read_journal\n" in out  # remaining names keep their relative form
    assert "from pkg_b.journal import append_write\n" in out


def test_relative_import_ignored_without_caller_package():
    text = "from .journal import append_write\n"
    out, changed = rewrite_symbol_import(text, "pkg_a.journal", "pkg_b.journal", "append_write")
    assert not changed and out == text


def test_rewrite_deeper_relative_level():
    text = "from ..core.journal import append_write\n"
    out, changed = rewrite_symbol_import(text, "pkg.core.journal", "pkg_b.journal", "append_write",
                                         caller_package="pkg.sub")
    assert changed and "from pkg_b.journal import append_write\n" in out
