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
