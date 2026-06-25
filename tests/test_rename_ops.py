"""Scope-aware identifier rename (the Ext-B crux) + the importer-rewrite helper.

The contract: rewrite ONLY genuine references to the module-global name, by exact position;
leave shadowed locals, attributes, kwarg names, strings, and comments byte-identical."""

import ast

from cjm_context_graph_projection.rename_ops import rewrite_import_for_rename, scoped_rename


def _r(text, old="old", new="new"):
    out, n = scoped_rename(text, old, new)
    assert ast.parse(out) is not None  # always valid Python
    return out, n


def test_renames_def_site_and_callers():
    out, n = _r("def old(x):\n    return x\n\n\ndef caller():\n    return old(1)\n")
    assert "def new(x):" in out and "return new(1)" in out and "old" not in out
    assert n == 2


def test_renames_class_site_and_base_and_annotation():
    out, _ = _r("class old:\n    pass\n\n\nclass Sub(old):\n    pass\n\n\n"
                "def f(x: old) -> old:\n    return x\n")
    assert "class new:" in out and "class Sub(new):" in out
    assert "def f(x: new) -> new:" in out


def test_skips_attribute_access():
    out, n = _r("import m\n\n\ndef f(o):\n    return o.old + m.old\n")
    assert out == "import m\n\n\ndef f(o):\n    return o.old + m.old\n" and n == 0


def test_skips_keyword_argument_name_but_renames_value():
    out, _ = _r("def old():\n    return 1\n\n\ndef g(f):\n    return f(old=old())\n")
    assert "f(old=new())" in out  # the kwarg NAME `old=` stays; the value `old()` renames


def test_skips_local_shadow_param_and_assignment():
    src = ("def old():\n    return 1\n\n\n"
           "def shadows(old):\n    return old\n\n\n"
           "def assigns():\n    old = 5\n    return old\n")
    out, _ = _r(src)
    assert "def new():" in out
    assert "def shadows(old):\n    return old" in out   # param shadows -> untouched
    assert "    old = 5\n    return old" in out          # local assign shadows -> untouched


def test_skips_comprehension_target_shadow():
    src = "def old():\n    return 1\n\n\ndef c():\n    return [old for old in range(3)]\n"
    out, _ = _r(src)
    assert "[old for old in range(3)]" in out  # comprehension var shadows -> untouched


def test_global_declaration_reaches_module_binding():
    src = "old = 0\n\n\ndef bump():\n    global old\n    old = old + 1\n"
    out, n = _r(src)
    assert "new = 0" in out and "global new" in out and "new = new + 1" in out and n == 4


def test_strings_and_comments_untouched():
    src = 'def old():\n    return 1\n\n\ndef f():\n    # old stays\n    return "old" + str(old())\n'
    out, _ = _r(src)
    assert "# old stays" in out and '"old"' in out and "str(new())" in out


def test_decorator_reference_renamed():
    out, _ = _r("def old(f):\n    return f\n\n\n@old\ndef g():\n    pass\n")
    assert "@new" in out and "def new(f):" in out


def test_self_recursion_renamed():
    out, _ = _r("def old(n):\n    return 1 if n <= 0 else old(n - 1)\n")
    assert "def new(n):" in out and "else new(n - 1)" in out and "old" not in out


def test_unrelated_name_untouched_byte_exact():
    src = "def keep():\n    older = 1\n    boldly = 2\n    return older + boldly\n"
    out, n = _r(src)
    assert out == src and n == 0  # substring matches (older/boldly) never touched


def test_method_named_old_not_treated_as_module_global():
    # `old` as a method is reached via attribute; a class-body method def named `old`
    # is a different binding. Renaming module-global `old` must not touch the method def.
    src = ("def old():\n    return 1\n\n\n"
           "class C:\n    def old(self):\n        return 2\n")
    out, _ = _r(src)
    assert "def new():" in out
    assert "    def old(self):" in out  # the method def name is class-scoped, untouched


def test_nonascii_elsewhere_on_line_keeps_alignment():
    src = 'def old():\n    return 1\n\n\ndef f():\n    x = "café"  # noqa\n    return old() + len(x)\n'
    out, _ = _r(src)
    assert '"café"' in out and "return new() + len(x)" in out


# --- importer rewrite -----------------------------------------------------------------------
def test_import_rewrite_unaliased_reports_local_name_old():
    out, local, qual = rewrite_import_for_rename("from pkg.m import old, other\n", "pkg.m", "old", "new")
    assert "from pkg.m import new, other\n" in out and local == "old" and not qual


def test_import_rewrite_aliased_keeps_alias_and_signals_no_body_edit():
    out, local, qual = rewrite_import_for_rename("from pkg.m import old as o\n", "pkg.m", "old", "new")
    assert "from pkg.m import new as o\n" in out and local == "o" and not qual


def test_import_rewrite_flags_qualified_use():
    out, local, qual = rewrite_import_for_rename("import pkg.m\n\nx = pkg.m.old()\n", "pkg.m", "old", "new")
    assert out == "import pkg.m\n\nx = pkg.m.old()\n" and local is None and qual
