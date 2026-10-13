"""The instrument's projection half (design detail 681604c1): the CLI's timing event and `perf`.

The readers are pinned on a synthetic ledger (self times, fits, the position trend, the guards,
the report); the end-to-end half runs real CLI invocations with --timings-path and reads the
events they appended."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from cjm_context_graph_primitives.journal import append_op, read_journal
from cjm_context_graph_projection.perf import (fit, guards, perf_report, render_perf, self_times,
                                               series_trend)
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS


def _event(verb, wall, ts, nodes=1000, read_only=True, **extra):
    """A synthetic CLI event, the shape `Run.event` writes."""
    ev = {"verb": verb, "kind": "cli", "run_id": f"r{ts}", "ts": ts, "wall_s": wall,
          "outcome": 0, "phases": {"verb": {"calls": 1, "s": wall * 0.8}},
          "storage": {}, "covariates": {"nodes": nodes, "edges": nodes * 3},
          "attrs": {"read_only": read_only}}
    ev.update(extra)
    return ev


def test_self_time_excludes_direct_children():
    phases = {"verb": {"calls": 1, "s": 1.0}, "verb/a": {"calls": 2, "s": 0.3},
              "verb/a/b": {"calls": 1, "s": 0.1}, "open_graph": {"calls": 1, "s": 0.2}}
    selfs = self_times(phases)
    assert selfs["verb"] == pytest.approx(0.7) and selfs["verb/a"] == pytest.approx(0.2)
    assert selfs["verb/a/b"] == pytest.approx(0.1) and selfs["open_graph"] == pytest.approx(0.2)


def test_fit_reads_a_linear_cost_and_refuses_a_constant_covariate():
    xs = [1000.0, 2000.0, 4000.0, 8000.0]
    f = fit(xs, [0.5 + x * 1e-4 for x in xs])
    assert f["slope"] == pytest.approx(1e-4) and f["r2"] == pytest.approx(1.0)
    assert fit([1000.0] * 4, [1.0, 2.0, 3.0, 4.0]) is None  # the covariate never moved
    assert fit(xs[:3], [1.0, 2.0, 3.0]) is None             # too few runs


def test_series_trend_flat_versus_rising():
    flat = {str(i * 1000): {"calls": 10, "s": 0.1} for i in range(6)}
    rising = {str(i * 1000): {"calls": 10, "s": 0.1 * (i + 1)} for i in range(6)}
    assert series_trend(flat)["ratio"] == pytest.approx(1.0)
    assert series_trend(rising)["ratio"] == pytest.approx(11 / 3)  # (0.5 + 0.6) / (0.1 + 0.2)
    assert series_trend({"0": {"calls": 10, "s": 1.0}}) is None  # under three bins


def test_guards_read_one_event():
    ev = _event("assert", 1.0, 1.0, nodes=100, read_only=False,
                whole={"verb/assert_value :: query_nodes": {"calls": 2, "s": 0.4}},
                storage={"query_nodes": {"calls": 3, "s": 0.5, "rows_out": 80, "at_limit": 1}},
                series={"replay:assert": {str(i * 1000): {"calls": 10, "s": 0.1 * (i + 1)}
                                          for i in range(6)}})
    tripped = {g["guard"] for g in guards(ev)}
    assert tripped == {"whole-scope-in-write", "cut-answer", "rising-with-position",
                       "read-most-of-the-graph"}
    # a read may read whole kinds and most of the graph: those two guards are for writes
    ev["attrs"]["read_only"] = True
    assert {g["guard"] for g in guards(ev)} == {"cut-answer", "rising-with-position"}


def test_the_plan_guard_reads_whole_table_plans_on_reads_and_writes():
    # 3bd8673d: a plan that reads a table whole is reported wherever it ran -- a read included,
    # unlike the whole-scope guard, which is about writes
    ev = _event("locate", 0.5, 1.0, read_only=True,
                plan_scans={"verb :: query_nodes :: nodes": {"calls": 1, "s": 0.29}})
    (g,) = guards(ev)
    assert g["guard"] == "plan-scan" and g["where"] == "verb :: query_nodes :: nodes"
    assert g["calls"] == 1


def test_report_per_verb_regression_fit_and_rebuild(tmp_path):
    ledger = str(tmp_path / "dev-graph.timings.jsonl")
    for i, (wall, nodes) in enumerate([(1.0, 1000), (1.1, 2000), (1.2, 4000), (3.0, 8000)]):
        append_op(ledger, _event("show", wall, float(i), nodes=nodes), dedup=False)
    append_op(ledger, _event("locate", 0.6, 9.0), dedup=False)
    append_op(ledger, {"verb": "cg-rebuild", "kind": "rebuild", "run_id": "rb", "ts": 10.0,
                       "wall_s": 30.0, "outcome": 0, "attrs": {"lane": "dev"},
                       "phases": {"build": {"calls": 1, "s": 25.0}}}, dedup=False)
    append_op(ledger, {**_event("ingest", 25.0, 10.5, read_only=False), "run_id": "rb",
                       "attrs": {"read_only": False, "step": "build"}}, dedup=False)
    res = perf_report(ledger)
    show = next(v for v in res["verbs"] if v["verb"] == "show")
    assert show["runs"] == 4 and show["latest_s"] == 3.0 and show["median_s"] == pytest.approx(1.15)
    assert [r["verb"] for r in res["regressions"]] == ["show"]
    assert {f["covariate"] for f in res["fits"] if f["verb"] == "show"} == {"nodes", "edges"}
    (rb,) = res["rebuilds"]
    assert rb["lane"] == "dev" and [s["step"] for s in rb["steps"]] == ["build"]
    assert perf_report(ledger, verb="locate")["verbs"][0]["verb"] == "locate"
    text = render_perf(res)
    assert "| show | 4 |" in text and "### Regressions" in text and "Rebuild — dev lane" in text
    assert json.loads(render_perf(res, "agent"))["events"] == 7


needs_capability = pytest.mark.skipif(
    not (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists(),
    reason=f"graph capability {DEFAULT_GRAPH_ID!r} not installed at {DEFAULT_MANIFESTS}",
)


def _run(*args, env=None):
    return subprocess.run([sys.executable, "-m", "cjm_context_graph_projection.cli", *args],
                          capture_output=True, text=True, env=env)


@needs_capability
def test_an_invocation_appends_one_event(tmp_path):
    # A write and a read, each with --timings-path: one event per invocation, carrying the
    # phases, the storage table and the covariates; with no path, nothing is appended.
    db, journal = str(tmp_path / "g.db"), str(tmp_path / "writes.jsonl")
    ledger = str(tmp_path / "g.timings.jsonl")
    base = ["--graph-db-path", db, "--journal-path", journal, "--timings-path", ledger]
    r = _run(*base, "decide", "the instrument measures this decide")
    assert r.returncode == 0, r.stderr
    r = _run(*base, "--read-only", "state")
    assert r.returncode == 0, r.stderr
    assert _run("--graph-db-path", db, "--read-only", "state").returncode == 0  # unrecorded
    events = read_journal(ledger)
    assert [e["verb"] for e in events] == ["decide", "state"]
    decide, state = events
    assert decide["attrs"]["read_only"] is False and state["attrs"]["read_only"] is True
    for ev in events:
        assert ev["kind"] == "cli" and ev["outcome"] == 0
        assert {"parse", "open_graph", "verb", "close_graph", "covariates"} <= set(ev["phases"])
        assert ev["covariates"]["nodes"] >= 1 and "edges" in ev["covariates"]
    assert "add_nodes" in decide["storage"] or "import_graph" in decide["storage"]
    assert decide["covariates"]["journals"]["writes"]["segments"] == 1
    out = _run("--graph-db-path", db, "--timings-path", ledger, "--read-only", "perf")
    assert out.returncode == 0 and "| decide | 1 |" in out.stdout
