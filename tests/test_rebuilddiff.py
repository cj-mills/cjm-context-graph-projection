"""The live-versus-rebuild standing check (design 8f6f2343, amendment efd659a1, finding fbce0173).

What the comparison reports is pinned where it lives, the layer's graphdiff (ruling f8b9c728).
Here: the CLI's path map parsing, and the end-to-end half -- real CLI writes into a live db,
their journal replayed into a fresh db, the two required EQUAL on every property and both time
columns (the property the op clock exists to hold), read through the streamed comparison."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from cjm_context_graph_projection.rebuilddiff import parse_path_map
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS


def test_path_map_refuses_a_pair_without_equals():
    with pytest.raises(ValueError):
        parse_path_map(["/old/site"])


needs_capability = pytest.mark.skipif(
    not (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists(),
    reason=f"graph capability {DEFAULT_GRAPH_ID!r} not installed at {DEFAULT_MANIFESTS}",
)


def _run(*args):
    return subprocess.run([sys.executable, "-m", "cjm_context_graph_projection.cli", *args],
                          capture_output=True, text=True)


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@needs_capability
def test_live_writes_equal_their_rebuild(tmp_path):
    # A batch of live writes across the common verbs — a born note, a multi-op invocation
    # (decide --state open journals decide + assert), a batch (assert-batch), a supersede,
    # an edge — then a replay of their journal into a fresh db: the live db and its rebuild
    # must match on every property and both time columns.
    live, rebuilt = str(tmp_path / "live.db"), str(tmp_path / "rebuilt.db")
    journal = str(tmp_path / "writes.jsonl")
    base = ("--graph-db-path", live, "--journal-path", journal)
    note = tmp_path / "memory" / "clock_demo.md"
    note.parent.mkdir()

    def ok(*args):
        r = _run(*base, *args)
        assert r.returncode == 0, f"{args[0]} failed: {r.stderr or r.stdout}"
        return r.stdout

    ok("new-note", "--path", str(note), "--content",
       "---\nname: clock-demo\ndescription: d\n---\n\nbody\n")
    item = _UUID.search(ok("decide", "WORK ITEM: one clock", "--title", "WORK ITEM: one clock",
                           "--state", "open")).group(0)
    design = _UUID.search(ok("decide", "DESIGN: the op clock", "--title",
                             "DESIGN: the op clock")).group(0)
    batch = tmp_path / "batch.jsonl"
    batch.write_text("".join(json.dumps(x) + "\n" for x in (
        {"subject": item, "predicate": "task_state", "value": "in_progress"},
        {"subject": design, "predicate": "priority", "value": "high"})))
    ok("assert-batch", str(batch))
    ok("assert", item, "task_state", "done")
    ok("link", design, "REFERENCES", item)

    r = _run("--graph-db-path", rebuilt, "--journal-path", journal, "replay")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run("--graph-db-path", live, "rebuild-diff", "--against", rebuilt)
    assert r.returncode == 0, f"live db differs from its rebuild:\n{r.stdout}\n{r.stderr}"
    assert "CLEAN" in r.stdout
