"""Pure-helper tests for the module-edit ops (rewrite_module_import + import-name derivation)."""

from cjm_context_graph_projection.module_ops import _derive_import_name, rewrite_module_import


def test_derive_import_name_strips_py_and_dots_path():
    assert _derive_import_name("pkg/sub.py") == "pkg.sub"
    assert _derive_import_name("pkg/__init__.py") == "pkg.__init__"
    assert _derive_import_name("top.py") == "top"


def test_rewrite_from_import_repoints_module_keeping_names():
    text = "from pkg.old import S, T\n\nx = S()\n"
    out, changed = rewrite_module_import(text, "pkg.old", "pkg.new")
    assert changed and "from pkg.new import S, T\n" in out and "pkg.old" not in out


def test_rewrite_from_import_preserves_aliases():
    text = "from pkg.old import S as Sym, T\n"
    out, changed = rewrite_module_import(text, "pkg.old", "pkg.new")
    assert "from pkg.new import S as Sym, T\n" in out


def test_rewrite_parenthesized_from_import():
    text = "from pkg.old import (\n    S,\n    T,\n)\n\ny = 1\n"
    out, changed = rewrite_module_import(text, "pkg.old", "pkg.new")
    assert changed and "from pkg.new import S, T\n" in out


def test_rewrite_plain_import_and_alias():
    text = "import pkg.old\nimport pkg.old as po\nimport other\n"
    out, changed = rewrite_module_import(text, "pkg.old", "pkg.new")
    assert changed
    assert "import pkg.new\n" in out and "import pkg.new as po\n" in out and "import other\n" in out


def test_rewrite_leaves_submodule_and_other_modules_alone():
    text = "from pkg.old.sub import S\nfrom pkg.other import T\n"  # exact-match only (v1)
    out, changed = rewrite_module_import(text, "pkg.old", "pkg.new")
    assert not changed and out == text


def test_rewrite_invalid_python_is_a_noop():
    text = "from pkg.old import (\n"  # unparseable
    out, changed = rewrite_module_import(text, "pkg.old", "pkg.new")
    assert not changed and out == text
