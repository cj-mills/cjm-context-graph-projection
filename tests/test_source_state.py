"""N+3 source-state: canonical emit fixpoint + the shadow source journal + soak check
(Phase 1), and the Phase-2 cutover (journal-as-source + the artifact regen gate)."""

import tempfile
from pathlib import Path

from cjm_context_graph_projection.source_state import (absorb_authored_text, append_source,
                                                       canonical_emit, cutover_module,
                                                       emit_source_artifact, flip_module,
                                                       graph_sourced_modules,
                                                       latest_source_ops, source_check)

MODULE = ('"""A tiny module."""\nimport os\n\n\n'
          'def helper(x):\n    return os.path.join(x, "y")\n\n\n'
          'def use(p):\n    return helper(p)\n')


def test_canonical_emit_is_a_fixpoint():
    once = canonical_emit("demo", "demo/m.py", "/tmp/demo/m.py", MODULE)
    twice = canonical_emit("demo", "demo/m.py", "/tmp/demo/m.py", once)
    assert once == twice  # canonical form is stable
    assert "def helper(x):" in once and "import os" in once


def test_append_source_dedups_identical_latest_state():
    with tempfile.TemporaryDirectory() as d:
        j = str(Path(d) / "source.jsonl")
        assert append_source(j, "demo", "demo/m.py", "demo.m", "A\n") is True
        assert append_source(j, "demo", "demo/m.py", "demo.m", "A\n") is False  # identical -> no-op
        assert append_source(j, "demo", "demo/m.py", "demo.m", "B\n") is True   # new state -> append
        latest = latest_source_ops(j)
        assert latest[("demo", "demo/m.py")]["text"] == "B\n"  # last write wins


def test_flip_then_source_check_clean_when_file_canonical():
    with tempfile.TemporaryDirectory() as d:
        repos = Path(d) / "repos"
        f = repos / "demo" / "demo" / "m.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        # Write the file ALREADY in canonical form so the flip is byte-exact.
        canonical = canonical_emit("demo", "demo/m.py", str(f), MODULE)
        f.write_text(canonical)
        j = str(Path(d) / "source.jsonl")

        res = flip_module(j, str(repos), "demo", "demo/m.py")
        assert res["captured"] and res["file_already_canonical"]

        chk = source_check(j, str(repos))
        assert chk["clean"] and chk["count"] == 1
        assert chk["modules"][0]["file_matches_source"] and chk["modules"][0]["roundtrip_fixpoint"]


def test_source_check_flags_out_of_band_file_drift():
    with tempfile.TemporaryDirectory() as d:
        repos = Path(d) / "repos"
        f = repos / "demo" / "demo" / "m.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(canonical_emit("demo", "demo/m.py", str(f), MODULE))
        j = str(Path(d) / "source.jsonl")
        flip_module(j, str(repos), "demo", "demo/m.py")

        f.write_text(f.read_text() + "\n# an out-of-band edit\n")  # the membrane should catch this
        chk = source_check(j, str(repos))
        assert not chk["clean"] and chk["file_drift"] == 1
        assert not chk["modules"][0]["file_matches_source"]


def _shadowed_module(d):
    """A repo with one module flipped to shadow (file already canonical); returns (journal, repos, file)."""
    repos = Path(d) / "repos"
    f = repos / "demo" / "demo" / "m.py"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(canonical_emit("demo", "demo/m.py", str(f), MODULE))
    j = str(Path(d) / "source.jsonl")
    flip_module(j, str(repos), "demo", "demo/m.py")
    return j, str(repos), f


def test_cutover_requires_a_shadow_state():
    with tempfile.TemporaryDirectory() as d:
        j = str(Path(d) / "source.jsonl")
        res = cutover_module(j, d, "demo", "demo/m.py")
        assert not res["cut_over"] and "flip-module" in res["error"]


def test_cutover_refuses_a_drifted_file():
    with tempfile.TemporaryDirectory() as d:
        j, repos, f = _shadowed_module(d)
        f.write_text(f.read_text() + "\n# out-of-band\n")
        res = cutover_module(j, repos, "demo", "demo/m.py")
        assert not res["cut_over"] and "drifted" in res["error"]
        assert graph_sourced_modules(j) == set()


def test_cutover_flips_and_is_idempotent():
    with tempfile.TemporaryDirectory() as d:
        j, repos, f = _shadowed_module(d)
        res = cutover_module(j, repos, "demo", "demo/m.py")
        assert res["cut_over"] and not res["artifact_written"]
        assert graph_sourced_modules(j) == {("demo", "demo/m.py")}
        again = cutover_module(j, repos, "demo", "demo/m.py")
        assert again["already_graph_sourced"] and not again["cut_over"]
        # A cutover op must NOT clobber the module's latest source STATE.
        assert latest_source_ops(j)[("demo", "demo/m.py")]["text"] == f.read_text()


def test_cutover_regenerates_a_missing_artifact():
    with tempfile.TemporaryDirectory() as d:
        j, repos, f = _shadowed_module(d)
        canonical = f.read_text()
        f.unlink()  # journal is sufficient — the file is a regenerable artifact
        res = cutover_module(j, repos, "demo", "demo/m.py")
        assert res["cut_over"] and res["artifact_written"]
        assert f.read_text() == canonical


def test_source_check_reports_phase_and_the_regen_gate():
    with tempfile.TemporaryDirectory() as d:
        j, repos, f = _shadowed_module(d)
        cutover_module(j, repos, "demo", "demo/m.py")
        chk = source_check(j, repos)
        assert chk["graph_sourced_count"] == 1 and chk["modules"][0]["graph_sourced"]
        assert chk["regen_clean"] and chk["clean"]
        # A post-cutover file edit = a DIVERGED ARTIFACT -> the regen gate fails.
        f.write_text(f.read_text() + "\n# stray edit of a generated file\n")
        chk = source_check(j, repos)
        assert not chk["regen_clean"] and not chk["clean"]


def test_emit_source_artifact_restores_the_file_from_the_journal():
    with tempfile.TemporaryDirectory() as d:
        j, repos, f = _shadowed_module(d)
        cutover_module(j, repos, "demo", "demo/m.py")
        canonical = f.read_text()
        f.write_text(canonical + "\n# stray\n")
        dry = emit_source_artifact(j, repos, "demo", "demo/m.py", write=False)
        assert dry["changed"] and not dry["written"] and f.read_text() != canonical
        res = emit_source_artifact(j, repos, "demo", "demo/m.py")
        assert res["written"] and f.read_text() == canonical
        assert source_check(j, repos)["regen_clean"]


def test_absorb_authored_text_canonicalizes_and_keeps_file_in_sync():
    with tempfile.TemporaryDirectory() as d:
        j, repos, f = _shadowed_module(d)
        cutover_module(j, repos, "demo", "demo/m.py")
        # An author edit that stops using `os` — a verbatim emit would keep the now-dead
        # import; the absorb canonicalizes (prunes it) and rewrites the artifact to match.
        authored = f.read_text().replace('return os.path.join(x, "y")', 'return x + "/y"')
        f.write_text(authored)  # what the author verb wrote
        res = absorb_authored_text(j, "demo", "demo/m.py", str(f), authored)
        assert res["absorbed"] and res["canonicalized"]
        assert "import os" not in f.read_text()
        assert latest_source_ops(j)[("demo", "demo/m.py")]["text"] == f.read_text()
        assert source_check(j, repos)["regen_clean"]


# --- Notebook-sourced modules (the nbdev transition window) ---

import json

from cjm_context_graph_projection.source_state import canonical_emit_notebook

NOTEBOOK = json.dumps({
    "cells": [
        {"cell_type": "code", "id": "c0", "metadata": {}, "execution_count": 1,
         "outputs": [], "source": ["#| default_exp core\n"]},
        {"cell_type": "markdown", "id": "c1", "metadata": {},
         "source": ["# Core\n", "\n", "The `alpha` helper.\n"]},
        {"cell_type": "code", "id": "c2", "metadata": {"tags": ["x"]}, "execution_count": 2,
         "outputs": [{"name": "stdout", "output_type": "stream", "text": ["2\n"]}],
         "source": ["#| export\n", "def alpha(x):\n", "    return x + 1\n"]},
    ],
    "metadata": {"kernelspec": {"display_name": "python3", "language": "python",
                                "name": "python3"}},
    "nbformat": 4, "nbformat_minor": 5}, indent=1) + "\n"


def test_canonical_emit_notebook_is_a_fixpoint_and_strips_derived_state():
    once = canonical_emit_notebook(NOTEBOOK)
    assert canonical_emit_notebook(once) == once  # canonical form is stable
    nb = json.loads(once)
    # Cell sources round-trip verbatim; outputs/exec counts/metadata are derived -> stripped.
    assert "".join(nb["cells"][2]["source"]) == "#| export\ndef alpha(x):\n    return x + 1\n"
    assert nb["cells"][2]["outputs"] == [] and nb["cells"][2]["execution_count"] is None
    assert nb["metadata"] == {} and nb["cells"][2]["metadata"] == {}
    assert [c["id"] for c in nb["cells"]] == ["c0", "c1", "c2"]  # nbformat ids survive


def _notebook_repo(d):
    """A repo with one nbdev notebook at nbs/00_core.ipynb; returns (journal, repos, file)."""
    repos = Path(d) / "repos"
    f = repos / "cjm-demo" / "nbs" / "00_core.ipynb"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(NOTEBOOK)
    return str(Path(d) / "source.jsonl"), str(repos), f


def test_flip_notebook_journals_canonical_cell_state():
    with tempfile.TemporaryDirectory() as d:
        j, repos, f = _notebook_repo(d)
        res = flip_module(j, repos, "cjm-demo", "nbs/00_core.ipynb")
        assert res["captured"] and not res["file_already_canonical"]  # outputs/metadata strip
        assert res["import_name"] == "cjm_demo.core"  # derived from #| default_exp
        a = latest_source_ops(j)[("cjm-demo", "nbs/00_core.ipynb")]
        assert a["text"] == canonical_emit_notebook(NOTEBOOK)


def test_flip_notebook_rejects_malformed_json():
    with tempfile.TemporaryDirectory() as d:
        j, repos, f = _notebook_repo(d)
        f.write_text("{ not a notebook")
        res = flip_module(j, repos, "cjm-demo", "nbs/00_core.ipynb")
        assert not res["captured"] and "cannot decompose" in res["error"]


def test_notebook_walk_flip_emit_cutover_then_regen_gate():
    with tempfile.TemporaryDirectory() as d:
        j, repos, f = _notebook_repo(d)
        flip_module(j, repos, "cjm-demo", "nbs/00_core.ipynb")
        # The file still carries outputs/metadata -> the guarded cutover refuses.
        refused = cutover_module(j, repos, "cjm-demo", "nbs/00_core.ipynb")
        assert not refused["cut_over"] and "drifted" in refused["error"]
        # emit-artifact canonicalizes the file (the one-time canonicalization event) ...
        assert emit_source_artifact(j, repos, "cjm-demo", "nbs/00_core.ipynb")["written"]
        # ... and the cutover goes through; the journal is now the source of truth.
        assert cutover_module(j, repos, "cjm-demo", "nbs/00_core.ipynb")["cut_over"]
        chk = source_check(j, repos)
        assert chk["clean"] and chk["regen_clean"] and chk["graph_sourced_count"] == 1
        assert chk["modules"][0]["module"] == "cjm_demo.core"
        # A post-cutover file edit = a DIVERGED ARTIFACT -> the regen gate fails.
        f.write_text(f.read_text().replace("x + 1", "x + 2"))
        assert not source_check(j, repos)["regen_clean"]
        # Re-flip absorbs the edit (the window editing discipline); the gate is clean again.
        res = flip_module(j, repos, "cjm-demo", "nbs/00_core.ipynb")
        assert res["captured"] and res["file_already_canonical"]
        assert source_check(j, repos)["regen_clean"]


# A pytest module whose conftest import is consumed ONLY as a fixture PARAMETER name
# (a side-effect fixture: the body never loads the name, so no ref sees it) —
# pytest wires it by string match, the stage-2 false-prune case.
FIXTURE_TEST_MODULE = ('"""Fixture-import survival."""\n'
                       'from conftest import my_fixture\n\n\n'
                       'def test_uses_fixture(my_fixture):\n'
                       '    assert True\n')

# A script-shaped manual test: the closure lives inside a `try:` block, so the symbol
# walk never extracts it and its `uuid4` ref is invisible — the LIVE false-prune case.
CLOSURE_TEST_MODULE = ('"""Block-nested closure import survival."""\n'
                       'from uuid import uuid4\n\n\n'
                       'def main():\n'
                       '    try:\n'
                       '        def make_id():\n'
                       '            return str(uuid4())\n'
                       '        return make_id()\n'
                       '    except Exception:\n'
                       '        return ""\n')


def test_test_module_canonical_emit_keeps_fixture_import_verbatim():
    # Under tests/: the import block is VERBATIM — canonical emit is byte-identity.
    out = canonical_emit("demo", "tests/test_fx.py", "/tmp/demo/tests/test_fx.py",
                         FIXTURE_TEST_MODULE)
    assert out == FIXTURE_TEST_MODULE
    # The SAME text on a package path derives its imports — the fixture import would
    # false-prune (pins that the dispatch, not the walk, is what protects tests).
    pruned = canonical_emit("demo", "demo/fx.py", "/tmp/demo/demo/fx.py", FIXTURE_TEST_MODULE)
    assert "from conftest import my_fixture" not in pruned


def test_test_module_block_nested_closure_import_survives():
    out = canonical_emit("demo", "tests_manual/drill.py", "/tmp/demo/tests_manual/drill.py",
                         CLOSURE_TEST_MODULE)
    assert out == CLOSURE_TEST_MODULE
    assert "from uuid import uuid4" in out


def test_test_module_flip_cutover_roundtrip_is_byte_clean():
    with tempfile.TemporaryDirectory() as d:
        repos = Path(d) / "repos"
        f = repos / "demo" / "tests" / "test_fx.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(FIXTURE_TEST_MODULE)
        j = str(Path(d) / "source.jsonl")
        res = flip_module(j, str(repos), "demo", "tests/test_fx.py")
        assert res["captured"] and res["file_already_canonical"]  # verbatim -> zero-diff flip
        assert cutover_module(j, str(repos), "demo", "tests/test_fx.py")["cut_over"]
        chk = source_check(j, str(repos))
        assert chk["clean"] and chk["regen_clean"] and chk["graph_sourced_count"] == 1


def test_no_write_previews_journal_nothing():
    with tempfile.TemporaryDirectory() as d:
        j, repos, f = _shadowed_module(d)
        before = Path(j).read_text()
        res = cutover_module(j, repos, "demo", "demo/m.py", write=False)
        assert res["previewed"] and not res["cut_over"]
        assert graph_sourced_modules(j) == set()
        assert Path(j).read_text() == before
        res = flip_module(j, repos, "demo", "demo/m.py", write=False)
        assert res["previewed"] and not res["captured"]
        assert Path(j).read_text() == before
        # The preview costs nothing: the real cutover still lands after it.
        assert cutover_module(j, repos, "demo", "demo/m.py")["cut_over"]
