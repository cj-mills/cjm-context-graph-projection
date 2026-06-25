"""N+3 Phase 1 source-state: canonical emit fixpoint + the shadow source journal + soak check."""

import tempfile
from pathlib import Path

from cjm_context_graph_projection.source_state import (append_source, canonical_emit,
                                                       flip_module, latest_source_ops,
                                                       source_check)

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
