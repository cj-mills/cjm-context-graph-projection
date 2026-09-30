"""The live-versus-rebuild standing check (design 8f6f2343, amendment efd659a1, finding fbce0173).

The pure half pins what `diff_graphs` reports (a time drift, a property drift, a one-sided id, a
path moved on disk). The end-to-end half drives real CLI writes into a live db, replays their
journal into a fresh db, and requires the two to be EQUAL on every property and both time
columns — the property the op clock exists to hold."""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from cjm_context_graph_projection.rebuilddiff import diff_graphs, parse_path_map
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS


def _node(nid, t=1.0, **props):
    return {"id": nid, "label": "Decision", "properties": props, "sources": [],
            "created_at": t, "updated_at": t}


def test_identical_graphs_are_clean():
    a = [_node("n1", statement="x")]
    assert diff_graphs(a, [], [dict(a[0])], [])["clean"]


def test_time_and_property_drift_are_named_by_class_and_field():
    res = diff_graphs([_node("n1", 1.0, statement="x")], [],
                      [_node("n1", 1.08, statement="y")], [])
    assert not res["clean"] and res["nodes"]["differing"] == 1
    fields = {r["field"] for r in res["nodes"]["rows"]}
    assert fields == {"created_at", "updated_at", "properties.statement"}
    assert all(r["class"] == "Decision" for r in res["nodes"]["rows"])


def test_one_sided_ids_are_counted_by_class():
    res = diff_graphs([_node("n1"), _node("n2")], [], [_node("n1")], [])
    assert res["nodes"]["only_a"] == {"count": 1, "by_class": {"Decision": 1}, "sample": ["n2"]}
    assert not res["clean"]


def test_path_map_normalizes_a_corpus_moved_on_disk():
    a = [_node("n1", path="/old/site/posts/a.md")]
    b = [_node("n1", path="/new/site/posts/a.md")]
    assert not diff_graphs(a, [], b, [])["clean"]
    assert diff_graphs(a, [], b, [], parse_path_map(["/old/site=/new/site"]))["clean"]


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
