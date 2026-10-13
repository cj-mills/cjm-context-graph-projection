"""The code source journal's write side in the element regime (design detail 25c1fadc): a module is
born captured, a write lands element records journal-first, source-check holds every file to its
composed text; the identity map still reads the snapshot history the journal keeps."""

import json
import tempfile
from pathlib import Path

from cjm_context_graph_projection.source_state import (canonicalization_report,
                                                       emit_source_artifact, flip_module,
                                                       journaled_emit, latest_source_ops,
                                                       read_source_journal, source_check)

MODULE = ('"""A tiny module."""\n\nimport os\n\n\n'
          'def helper(x):\n    return os.path.join(x, "y")\n\n\n'
          'def use(p):\n    return helper(p)\n')


def _born_module(d, text=MODULE, path="demo/m.py"):
    """A file flipped into the journal: born captured. Returns (journal, repos root, file)."""
    repos = Path(d) / "repos"
    f = repos / "demo" / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    j = str(Path(d) / "source.jsonl")
    assert flip_module(j, str(repos), "demo", path)["captured"]
    return j, str(repos), f


def _snapshot(j, module_path, text, op=None):
    """A historical snapshot-regime `source` record -- the history the fold still reads."""
    rec = {"verb": "source", "ts": float(len(read_source_journal(j)) + 1), "generation": 1,
           "args": {"repo_key": "demo", "module_path": module_path,
                    "import_name": module_path[:-3].replace("/", "."), "text": text}}
    if op:
        rec["op"] = op
    with open(j, "a") as fh:
        fh.write(json.dumps(rec) + "\n")


def test_a_born_module_source_checks_clean_when_the_file_is_canonical():
    with tempfile.TemporaryDirectory() as d:
        j, repos, f = _born_module(d)
        assert latest_source_ops(j)[("demo", "demo/m.py")]["text"] == MODULE == f.read_text()
        chk = source_check(j, repos)
        assert chk["clean"] and chk["regen_clean"] and chk["count"] == 1
        assert chk["graph_sourced_count"] == 1 and chk["modules"][0]["roundtrip_fixpoint"]


def test_source_check_flags_drift_and_emit_artifact_restores_the_file():
    with tempfile.TemporaryDirectory() as d:
        j, repos, f = _born_module(d)
        f.write_text(MODULE + "\n# an out-of-band edit\n")
        chk = source_check(j, repos)
        assert chk["file_drift"] == 1 and not chk["clean"] and not chk["regen_clean"]
        assert not chk["modules"][0]["file_matches_source"]
        dry = emit_source_artifact(j, repos, "demo", "demo/m.py", write=False)
        assert dry["changed"] and not dry["written"] and "an out-of-band edit" in f.read_text()
        res = emit_source_artifact(j, repos, "demo", "demo/m.py")
        assert res["written"] and f.read_text() == MODULE
        assert source_check(j, repos)["regen_clean"]
        f.unlink()
        assert not source_check(j, repos)["regen_clean"]  # a missing artifact fails the gate
        assert emit_source_artifact(j, repos, "demo", "demo/m.py")["written"] and f.exists()


def test_a_live_snapshot_module_fails_the_regen_gate():
    # A journal still holding a live snapshot key (the retired regime) is never held clean.
    with tempfile.TemporaryDirectory() as d:
        j = str(Path(d) / "source.jsonl")
        _snapshot(j, "demo/m.py", MODULE)
        f = Path(d) / "repos" / "demo" / "demo" / "m.py"
        f.parent.mkdir(parents=True)
        f.write_text(MODULE)
        chk = source_check(j, str(Path(d) / "repos"))
        assert chk["graph_sourced_count"] == 0 and not chk["regen_clean"]


def test_canonicalization_report_names_the_hazards_and_shows_the_diff():
    # e526672a W4: an import hoisted above the setup it followed, and a docstring that
    # stops being the first statement, are hazards; a plain import reorder is only a diff.
    setup = 'import sys\nsys.path.insert(0, "x")\nimport foo  # noqa: E402\n'
    hoisted = 'import foo\nimport sys\n\nsys.path.insert(0, "x")\n'
    rep = canonicalization_report(setup, hoisted, "pkg/m.py")
    assert rep["hazards"] == ["import of foo moves above `sys.path.insert(0, 'x')` (line 2 of the file)"]
    assert rep["diff"].startswith("--- file\n+++ canonical\n")
    doc = canonicalization_report('"""Doc."""\nimport os\n', 'import os\n"""Doc."""\n', "pkg/m.py")
    assert doc["hazards"] == ["the first statement changes kind: docstring -> import"]
    plain = canonicalization_report("import b\nimport a\nX = 1\n", "import a\nimport b\nX = 1\n", "pkg/m.py")
    assert plain["hazards"] == [] and "+import a" in plain["diff"]
    assert canonicalization_report("X = 1\n", "X = 1\n", "pkg/m.py") == {"diff": "", "hazards": []}


def test_the_element_regime_never_hoists_and_emit_refuses_a_hazardous_overwrite():
    # An import after a statement stays where it is (the setup it may depend on), so a birth carries
    # no hazard; overwriting a file that holds such a setup with a journal that does not is refused.
    with tempfile.TemporaryDirectory() as d:
        text = ('import sys\n\nsys.path.insert(0, "x")\nimport json\n\n\n'
                'def f():\n    return json.dumps(sys.argv)\n')
        j, repos, f = _born_module(d, text)
        assert latest_source_ops(j)[("demo", "demo/m.py")]["text"] == text
    with tempfile.TemporaryDirectory() as d:
        hoisted = ('import json\nimport sys\n\nsys.path.insert(0, "x")\n\n\n'
                   'def f():\n    return json.dumps(sys.argv)\n')
        j, repos, f = _born_module(d, hoisted)
        setup = ('import sys\n\nsys.path.insert(0, "x")\nimport json\n\n\n'
                 'def f():\n    return json.dumps(sys.argv)\n')
        f.write_text(setup)
        emit = emit_source_artifact(j, repos, "demo", "demo/m.py")
        assert emit["error"] and emit["hazards"] and not emit["written"] and f.read_text() == setup
        forced = emit_source_artifact(j, repos, "demo", "demo/m.py", allow_reorder=True)
        assert forced["written"] and f.read_text() == hoisted


def test_a_born_module_keeps_a_fixture_import_and_a_block_nested_closure_import():
    # A fixture is requested by parameter name and a closure inside a `try:` reads its import from
    # inside the element: both bindings are claimed, in a test module and a package module alike.
    fixture = ('"""Fixture-import survival."""\n\nfrom conftest import my_fixture\n\n\n'
               'def test_uses_fixture(my_fixture):\n    assert True\n')
    closure = ('"""Block-nested closure import survival."""\n\nfrom uuid import uuid4\n\n\n'
               'def main():\n    try:\n        def make_id():\n            return str(uuid4())\n'
               '        return make_id()\n    except Exception:\n        return ""\n')
    for path in ("tests/test_fx.py", "demo/fx.py"):
        for text in (fixture, closure):
            with tempfile.TemporaryDirectory() as d:
                j, repos, f = _born_module(d, text, path)
                assert latest_source_ops(j)[("demo", path)]["text"] == text == f.read_text()
                assert source_check(j, repos)["regen_clean"]


def test_no_write_previews_journal_nothing():
    with tempfile.TemporaryDirectory() as d:
        repos = Path(d) / "repos"
        f = repos / "demo" / "demo" / "m.py"
        f.parent.mkdir(parents=True)
        f.write_text(MODULE)
        j = str(Path(d) / "source.jsonl")
        res = flip_module(j, str(repos), "demo", "demo/m.py", write=False)
        assert res["previewed"] and not res["captured"] and not Path(j).exists()
        assert flip_module(j, str(repos), "demo", "demo/m.py")["captured"]  # the preview costs nothing
        before = Path(j).read_text()
        f.write_text(MODULE + "\n\nX = 1\n")
        res = flip_module(j, str(repos), "demo", "demo/m.py", write=False)
        assert res["previewed"] and not res["captured"] and Path(j).read_text() == before


def test_symbol_identity_map_keeps_ids_across_rename_move_and_module_rename():
    # The container-independent identity map (36f649d3), derived from the snapshot history's op
    # provenance alone.
    from cjm_context_graph_projection.source_state import symbol_identity_map
    with tempfile.TemporaryDirectory() as d:
        j = str(Path(d) / "source.jsonl")
        a0 = "def alpha():\n    return 1\n\n\nclass K:\n    def m(self):\n        return 2\n"
        _snapshot(j, "demo/a.py", a0)
        # rename alpha -> beta (keep identity); the importer record carries the SAME op but
        # defines neither name, so it registers nothing.
        keep = {"op": "rename-symbol", "from": "alpha", "to": "beta", "identity": "keep"}
        _snapshot(j, "demo/a.py", a0.replace("alpha", "beta"), op=keep)
        _snapshot(j, "demo/uses.py", "from demo.a import beta\n\n\ndef use():\n    return beta()\n", op=keep)
        ident = symbol_identity_map(j)
        assert ident.birth("demo", "demo/a.py", "beta") == {
            "birth_repo_key": "demo", "birth_module_path": "demo/a.py",
            "birth_qualname": "alpha", "generation": 0}
        assert ident.birth("demo", "demo/uses.py", "use") is None
        assert ident.birth("demo", "demo/a.py", "alpha") == {"generation": 1}  # a newcomer at the vacated name
        # move K (with its method) into demo/b.py: the source record lands first, then the target.
        mv = {"op": "move", "symbols": ["K"], "to_module": "demo.b", "identity": "keep"}
        _snapshot(j, "demo/a.py", "def beta():\n    return 1\n", op=mv)
        _snapshot(j, "demo/b.py", "class K:\n    def m(self):\n        return 2\n", op=mv)
        ident = symbol_identity_map(j)
        assert ident.birth("demo", "demo/b.py", "K")["birth_module_path"] == "demo/a.py"
        assert ident.birth("demo", "demo/b.py", "K.m") == {
            "birth_repo_key": "demo", "birth_module_path": "demo/a.py",
            "birth_qualname": "K.m", "generation": 0}
        assert ident.birth("demo", "demo/a.py", "K.m") == {"generation": 1}  # a new K in a.py: its methods too
        # rename-module b.py -> c.py re-registers every name from the retired path.
        _snapshot(j, "demo/c.py", "class K:\n    def m(self):\n        return 2\n",
                  op={"op": "rename-module", "from": "demo/b.py", "to": "demo/c.py", "identity": "keep"})
        ident = symbol_identity_map(j)
        assert ident.birth("demo", "demo/c.py", "K")["birth_module_path"] == "demo/a.py"
        # a pre-scheme op (no marker) registers nothing: the symbol is born at its new name.
        _snapshot(j, "demo/c.py", "class Q:\n    def m(self):\n        return 2\n",
                  op={"op": "rename-symbol", "from": "K", "to": "Q"})
        assert symbol_identity_map(j).birth("demo", "demo/c.py", "Q") is None
        # the normalizer maps the journal's dir-name keys into the graph's key space.
        norm = symbol_identity_map(j, normalize=lambda k: "X-" + k)
        assert norm.birth("X-demo", "demo/c.py", "K")["birth_repo_key"] == "X-demo"
        # back home: renaming beta -> alpha again returns the plain derivation.
        _snapshot(j, "demo/a.py", "def alpha():\n    return 1\n",
                  op={"op": "rename-symbol", "from": "beta", "to": "alpha", "identity": "keep"})
        ident = symbol_identity_map(j)
        assert ident.birth("demo", "demo/a.py", "alpha") is None
        assert ident.birth("demo", "demo/a.py", "beta") == {"generation": 1}


def _emission(f, text, **kw):
    return {"repo_key": "demo", "module_path": "demo/m.py", "import_name": "demo.m", "text": text,
            "path": str(f), **kw}


def test_journaled_emit_events_before_files_and_composed_landing():
    with tempfile.TemporaryDirectory() as d:
        j, repos, f = _born_module(d)
        n = len(read_source_journal(j))
        new_text = f.read_text().replace('os.path.join(x, "y")', 'os.path.join(x, "z")')
        rec = journaled_emit(j, emissions=[_emission(f, new_text)], op={"op": "author", "node_id": "sym1"})
        assert not rec.get("error") and rec["written"]
        assert [e["verb"] for e in rec["events"]] == ["elements"] and rec["events"][0]["appended"]
        assert rec["events"][0]["changed"] and rec["files_written"] == [str(f)] and not rec["unjournaled_files"]
        # The journal leads: the composed text == the file bytes (the regen gate green by construction).
        assert latest_source_ops(j)[("demo", "demo/m.py")]["text"] == f.read_text()
        tail = read_source_journal(j)[n:]
        assert [r["verb"] for r in tail] == ["element"]  # what changed only
        assert tail[0]["op"]["op"] == "author" and tail[0]["generation"] == 1  # provenance, replay-ignored


def test_journaled_emit_refuses_without_journal_and_previews_clean():
    with tempfile.TemporaryDirectory() as d:
        j, repos, f = _born_module(d)
        before_journal, before_file = Path(j).read_text(), f.read_text()
        emission = _emission(f, before_file + "\n\nX = 1\n")
        # No journal path + write=True -> outright refusal, nothing touched.
        rec = journaled_emit(None, emissions=[emission])
        assert "no source-journal path" in rec["error"] and f.read_text() == before_file
        # Preview -> zero side effects, events listed with appended=False.
        rec = journaled_emit(j, emissions=[emission], write=False)
        assert not rec.get("error") and not rec["written"]
        assert rec["events"][0]["verb"] == "elements" and not rec["events"][0]["appended"]
        assert Path(j).read_text() == before_journal and f.read_text() == before_file


def test_journaled_emit_unjournaled_module_is_loud_and_retire_delete_order():
    with tempfile.TemporaryDirectory() as d:
        j, repos, f = _born_module(d)
        stray = Path(repos) / "demo" / "demo" / "stray.py"
        rec = journaled_emit(j, emissions=[{"repo_key": "demo", "module_path": "demo/stray.py",
                                            "import_name": "demo.stray", "text": "Y = 2\n",
                                            "path": str(stray)}])
        # No live key and not born -> plain write, loudly reported; no event minted.
        assert rec["unjournaled_files"] == [str(stray)] and stray.read_text() == "Y = 2\n"
        assert not rec["events"]
        # Retire + delete: the retire event lands and the file goes.
        rec = journaled_emit(j, retires=[{"repo_key": "demo", "module_path": "demo/m.py"}],
                             deletes=[str(f)], op={"op": "delete-module"})
        assert rec["events"][0]["verb"] == "retire" and rec["events"][0]["appended"]
        assert not f.exists() and ("demo", "demo/m.py") not in latest_source_ops(j)


def test_journaled_emit_births_a_captured_module_and_registers():
    with tempfile.TemporaryDirectory() as d:
        repos = Path(d) / "repos"
        f = repos / "demo" / "demo" / "born.py"
        j = str(Path(d) / "source.jsonl")
        birth = {"repo_key": "demo", "module_path": "demo/born.py", "import_name": "demo.born",
                 "text": "def g():\n    return 1\n", "path": str(f), "born": True}
        reg = [{"repo_key": "demo", "repo_root": str(repos / "demo")}]
        rec = journaled_emit(j, emissions=[birth], registers=reg, op={"op": "add-module"})
        assert [e["verb"] for e in rec["events"]] == ["register", "elements"]
        assert rec["events"][1]["restated"] == "born" and f.exists()
        assert [r["verb"] for r in read_source_journal(j)][-1] == "capture"
        # Idempotent retry: an identical batch re-appends nothing.
        again = journaled_emit(j, emissions=[{**birth}], registers=reg)
        assert not any(e["appended"] for e in again["events"])


def test_journaled_emit_refuses_whole_batch_on_one_bad_emission():
    with tempfile.TemporaryDirectory() as d:
        j, repos, f = _born_module(d)
        before_journal, before_file = Path(j).read_text(), f.read_text()
        good = _emission(f, before_file + "\n\nZ = 3\n")
        bad = {"repo_key": "demo", "module_path": "demo/other.py", "import_name": "demo.other",
               "text": "def broken(:\n", "path": str(Path(repos) / "demo" / "demo" / "other.py"),
               "born": True}
        rec = journaled_emit(j, emissions=[good, bad], op={"op": "author"})
        assert "does not parse" in rec["error"] and "NOTHING journaled" in rec["error"]
        # All-or-nothing: the GOOD emission journaled/wrote nothing either.
        assert Path(j).read_text() == before_journal and f.read_text() == before_file


def test_source_journal_appends_route_through_one_choke_point():
    """Finding 160466f7 (P-48): every source-journal append lands via `_append_record`.

    Guards the choke point structurally — no raw append-mode open may exist in
    source_state.py outside the helper — and behaviorally: each event verb lands
    session/actor-stamped through the same path."""
    import inspect
    import os
    import re
    from cjm_context_graph_projection import source_state
    src = inspect.getsource(source_state)
    helper_src = inspect.getsource(source_state._append_record)
    raw_appends = len(re.findall(r'\.open\("a"\)', src))
    assert raw_appends == len(re.findall(r'\.open\("a"\)', helper_src)) == 1
    with tempfile.TemporaryDirectory() as d:
        j = str(Path(d) / "nested" / "source.jsonl")  # parent dir created by the helper
        os.environ["CJM_ACTOR"] = "agent:test-choke"
        try:
            assert source_state.append_register(j, "demo", repo_root=d) is True
            recs, _ = source_state.element_plan(source_state.SourceState(), [_emission(
                Path(d) / "m.py", "X = 1\n", born=True)])[0][0]
            assert source_state.append_elements(j, recs) == len(recs)
            assert source_state.append_retire(j, "demo", "demo/m.py", superseded_by=None) is True
        finally:
            del os.environ["CJM_ACTOR"]
        recs = list(read_source_journal(j))
        assert [r["verb"] for r in recs][0] == "register" and recs[-1]["verb"] == "retire"
        assert "capture" in [r["verb"] for r in recs]
        assert all(r.get("actor") == "agent:test-choke" for r in recs)  # stamped by the one path
        assert all("ts" in r and r.get("generation") == 1 for r in recs)
