"""The instrument's projection half (design detail 681604c1): the CLI's timing event and `perf`.

The readers are pinned on a synthetic ledger (self times, fits, the position trend, the guards,
the report); the end-to-end half runs real CLI invocations with --timings-path and reads the
events they appended."""

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest
from cjm_context_graph_primitives.journal import append_op, read_journal
from cjm_context_graph_projection.perf import (_check_cell, fit, guards, io_row, knee, load_report,
                                               net_written, parse_roles, perf_report, render_perf,
                                               self_times, series_trend, target_standing,
                                               tier_curves, tier_regressions)
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


def _gesture(name, wall, ts, write=False, poll=False, ticks=0, **extra):
    """A synthetic gesture event, the shape the kit's seam appends (design detail 4389a579)."""
    attrs = {"app": name.split(":")[0]}
    if write:
        attrs["write"] = True
    if poll:
        attrs["poll"] = True
    phases = {"queued": {"calls": max(1, ticks), "s": 0.01 * max(1, ticks)}}
    if poll:
        phases["tick"] = {"calls": ticks, "s": wall * ticks}
    ev = {"verb": name, "kind": "gesture", "run_id": f"g{ts}", "ts": ts, "wall_s": wall,
          "outcome": "ok", "phases": phases,
          "storage": {"query_nodes": {"calls": 2 * max(1, ticks), "s": 0.1, "rows_out": 10,
                                      "bytes_in": 100, "bytes_out": 400}},
          "covariates": {"nodes": 1000}, "attrs": attrs}
    ev.update(extra)
    return ev


def test_report_reads_gestures_and_polls_apart_from_verbs(tmp_path):
    """Gestures are their own section (queue wait, round trips, rows, bytes); a poll reads per
    tick; a write gesture's whole-scope read trips the guard a read gesture's never does."""
    ledger = str(tmp_path / "g.timings.jsonl")
    evs = [_event("show", 1.0, 1),
           _gesture("workbench:node", 0.2, 2), _gesture("workbench:node", 0.3, 3),
           _gesture("workbench:decide", 0.5, 4, write=True,
                    whole={"- :: query_nodes": {"calls": 1, "s": 0.4}}),
           _gesture("workbench:search", 0.4, 5, whole={"- :: query_nodes": {"calls": 1, "s": 0.3}}),
           _gesture("workbench:feed", 0.05, 6, poll=True, ticks=30),
           _gesture("workbench:feed", 0.07, 7, poll=True, ticks=10)]
    for e in evs:
        append_op(ledger, e, dedup=False)
    rep = perf_report(ledger)
    assert [v["verb"] for v in rep["verbs"]] == ["show"]
    node = {g["verb"]: g for g in rep["gestures"]}["workbench:node"]
    assert node["runs"] == 2 and node["latest_s"] == 0.3 and node["queued_s"] == 0.01
    assert node["round_trips"] == 2 and node["rows"] == 10 and node["bytes"] == 500
    (feed,) = rep["polls"]
    assert feed["gesture"] == "workbench:feed" and feed["windows"] == 2 and feed["ticks"] == 40
    assert feed["tick_s"] == pytest.approx((0.05 * 30 + 0.07 * 10) / 40)
    assert feed["latest_tick_s"] == pytest.approx(0.07) and feed["round_trips"] == 2
    tripped = {(g["verb"], g["guard"]) for g in rep["guards"]}
    assert ("workbench:decide", "whole-scope-in-write") in tripped
    assert not any(v == "workbench:search" for v, _ in tripped)
    text = render_perf(rep)
    assert "### Gestures" in text and "### Polls (per tick" in text
    assert "Qt-side delivery" in text


def test_a_gesture_writes_when_it_calls_a_store_write():
    ev = _gesture("correction:commit", 0.2, 1, whole={"- :: query_nodes": {"calls": 1, "s": 0.1}})
    assert not guards(ev)
    ev["storage"]["update_node"] = {"calls": 1, "s": 0.01}
    assert [g["guard"] for g in guards(ev)] == ["whole-scope-in-write"]


def test_disk_figures_and_the_read_wrote_guard():
    """Design detail 2f5033bf (1): net bytes (a cancelled write never reached the disk), the
    journal bytes appended, the store's share, commits (one per store write call) and the
    amplification; a read writing beyond its own appends trips read-wrote."""
    ev = _event("assert", 1.0, 1, read_only=False,
                io={"write_bytes": 40960, "cancelled_write_bytes": 8192, "read_bytes": 4096},
                appended={"x.writes.jsonl": {"ops": 1, "bytes": 512}},
                storage={"add_nodes": {"calls": 2, "s": 0.1, "io_write_bytes": 16384},
                         "query_nodes": {"calls": 3, "s": 0.1, "io_write_bytes": 4096,
                                         "io_cancelled_write_bytes": 4096}})
    assert io_row(ev) == {"written": 32768, "read": 4096, "appended": 512, "replayed": 0,
                          "store_written": 16384, "commits": 2, "amplification": 64.0}
    assert net_written(None) is None and net_written({"write_bytes": 1, "cancelled_write_bytes": 5}) == 0
    quiet = _event("show", 1.0, 2, io={"write_bytes": 4096}, appended={"r.jsonl": {"ops": 1, "bytes": 300}})
    loud = _event("show", 1.0, 3, io={"write_bytes": 4 << 20})
    assert not [g for g in guards(quiet) if g["guard"] == "read-wrote"]
    assert [g["guard"] for g in guards(loud)] == ["read-wrote"]


def _tier_runs(lane, verb, walls, outcome=0):
    """A canonical verb's harness and CLI events at each tier (its answer grows with the tier)."""
    out = []
    for tier, wall in walls.items():
        rid = f"{lane}-{verb}-{tier}"
        a = {"lane": lane, "tier": tier}
        out.append({"verb": verb, "kind": "bench", "run_id": rid, "ts": float(tier), "wall_s": wall,
                    "outcome": outcome, "peak_rss_kb": 1000, "out_bytes": 100 * tier, "attrs": a})
        out.append({"verb": verb, "kind": "cli", "run_id": rid, "ts": float(tier), "wall_s": wall,
                    "outcome": outcome, "phases": {"verb": {"calls": 1, "s": wall}},
                    "io": {"write_bytes": 0}, "attrs": a})
    return out


def test_target_standing_reads_shape_absolute_and_membership():
    """Design detail 2f5033bf (3): each seat against its class -- a flat read passes, a linear
    by-id read fails, a cost linear in a linear answer passes per answer, a seat in no class is
    NO TARGET, a picker that timed out fails at x32 although it never ran there, and a class
    whose members are loads claims no seat (its load rows are unmeasured with no load run)."""
    tiers = (1, 2, 4, 8, 16, 32)
    events = (_tier_runs("dev", "read", {k: 0.01 for k in tiers})
              + _tier_runs("dev", "show", {k: 0.05 * k for k in tiers})
              + _tier_runs("dev", "locate", {k: 0.02 * k for k in tiers})
              + _tier_runs("dev", "odd", {k: 0.01 for k in tiers})
              + _tier_runs("dev", "picker", {1: 1.0, 2: 3.0, 4: 9.0, 8: 27.0})
              + _tier_runs("dev", "picker", {16: 3600.0}, outcome="timeout"))
    classes = [
        {"key": "class-1", "targets": {"perf_b_max": "0.2", "perf_x32_s": "0.1", "perf_gates": "true"},
         "members": ["read", "show"]},
        {"key": "class-2", "targets": {"perf_b_max": "0.2", "perf_per_answer": "true"}, "members": ["locate"]},
        {"key": "class-3", "targets": {"perf_b_max": "1.1", "perf_x32_s": "1", "perf_gates": "true"},
         "members": ["picker"]},
        {"key": "class-7i", "targets": {"perf_load_p99_ratio": "2", "perf_gates": "true"},
         "members": ["load-L1"]}]
    st = target_standing(tier_curves(events), [], classes, load_defs={"load-L1": {"tier": 32, "lane": "dev"}})
    by = {s["seat"]: s for s in st["seats"]}
    assert by["read"]["class"] == "class-1" and by["read"]["status"] == "pass" and by["read"]["gate"]
    assert by["show"]["status"] == "fail" and not by["show"]["gate"]
    assert by["locate"]["status"] == "pass"
    assert by["odd"]["status"] == "no target"
    assert by["picker"]["status"] == "fail"
    assert any(c["target"] == "x32 s" and c["value"] == math.inf for c in by["picker"]["checks"])
    assert [r["status"] for r in st["loads"]] == ["unmeasured"] and not st["ready"]


def test_a_write_seat_is_held_to_its_amplification_and_a_read_seat_to_the_mb_ceiling(tmp_path):
    """Ruling 9af0e42e: one class mixes reads and writes -- a write seat (write mode, or journal
    bytes appended or replayed) takes the x32 amplification and never the MB ceiling, a read-only
    seat the ceiling and never the amplification; a replay's logical size is what it replayed;
    the full build is a seat per lane, its build phase's bytes over the db it produced."""
    tiers = (1, 2, 4, 8, 16, 32)
    events = (_tier_runs("dev", "read", {k: 0.01 for k in tiers})
              + _tier_runs("dev", "decide", {k: 0.01 for k in tiers})
              + _tier_runs("dev", "replay-tail", {k: 0.01 for k in tiers}))
    shapes = {"read": ({"read_only": True}, {"write_bytes": 64 * 1024}, {}),
              "decide": ({"read_only": False}, {"write_bytes": 1024 ** 2},  # 1 MB: over the ceiling
                         {"appended": {"w.jsonl": {"ops": 1, "bytes": 64 * 1024}}}),
              "replay-tail": ({"read_only": False}, {"write_bytes": 500_000},
                              {"replayed": {"w.jsonl": {"ops": 100, "bytes": 50_000}}})}
    for e in events:
        if e["kind"] == "cli":
            mode, io, logical = shapes[e["verb"]]
            e.update({"attrs": {**e["attrs"], **mode}, "io": io, **logical})
    assert io_row(events[-1])["amplification"] == pytest.approx(10.0)  # over the ops replayed
    ledger = str(tmp_path / "g.timings.jsonl")
    for e in events:
        append_op(ledger, e, dedup=False)
    append_op(ledger, {"verb": "cg-rebuild", "kind": "rebuild", "run_id": "rb", "ts": 50.0,
                       "wall_s": 9.0, "outcome": 0, "attrs": {"lane": "dev", "mode": "swap"},
                       "phases": {"backup": {"s": 0.1, "write_bytes": 10 * 1024 ** 2},
                                  "build": {"s": 8.0, "write_bytes": 300 * 1024 ** 2}}}, dedup=False)
    append_op(ledger, {"verb": "ingest", "kind": "cli", "run_id": "rb", "ts": 51.0, "wall_s": 8.0,
                       "outcome": 0, "attrs": {"step": "build"},
                       "covariates": {"db_bytes": 100 * 1024 ** 2}}, dedup=False)
    classes = [
        {"key": "class-1", "members": ["read", "decide"],
         "targets": {"perf_x32_write_mb": "0.25", "perf_write_amp_max": "32"}},
        {"key": "class-6", "members": ["replay-tail"], "targets": {"perf_write_amp_max": "32"}},
        {"key": "class-6-verifier", "members": ["cg-rebuild"], "targets": {"perf_write_amp_max": "2"}}]
    res = perf_report(ledger, targets=classes)
    (rb,) = res["rebuilds"]
    assert rb["build_amplification"] == pytest.approx(3.0) and rb["amplification"] == pytest.approx(3.1)
    by = {s["seat"]: s for s in res["standing"]["seats"]}
    names = {k: [c["target"] for c in s["checks"]] for k, s in by.items()}
    assert names["read"] == ["x32 written MB"] and by["read"]["status"] == "pass"
    assert names["decide"] == ["x32 amplification"] and by["decide"]["status"] == "pass"
    assert by["decide"]["checks"][0]["value"] == pytest.approx(16.0)
    assert names["replay-tail"] == ["x32 amplification"] and by["replay-tail"]["checks"][0]["value"] == pytest.approx(10.0)
    assert by["cg-rebuild"]["class"] == "class-6-verifier" and by["cg-rebuild"]["status"] == "fail"
    assert "the build x3.0" in render_perf(res)


def test_a_tier_reads_its_warm_run_and_shows_the_cold_one_beside(tmp_path):
    """Ruling b7b3976f (B): the harness labels each run cold or warm; a seat's reading at a tier
    is its warm run (an unlabelled older run stands in until one exists), the cold one rides
    beside it with its block-layer reads, and a cold run is never a tier regression."""
    def runs(cache, wall, ts, read_bytes):
        rid = f"{cache}-{ts}"
        a = {"lane": "dev", "tier": 8, **({"cache": cache} if cache else {})}
        return [{"verb": "locate", "kind": "bench", "run_id": rid, "ts": ts, "wall_s": wall,
                 "outcome": 0, "attrs": a},
                {"verb": "locate", "kind": "cli", "run_id": rid, "ts": ts, "wall_s": wall,
                 "outcome": 0, "phases": {"verb": {"calls": 1, "s": wall}},
                 "io": {"write_bytes": 0, "read_bytes": read_bytes}, "attrs": a}]
    old = runs(None, 25.0, 1.0, 0)
    (lane,) = tier_curves(old)
    assert lane["verbs"][0]["cells"][0]["wall_s"] == 25.0 and lane["verbs"][0]["cells"][0]["cold"] is None
    events = old + runs("cold", 74.0, 2.0, 6 << 30) + runs("warm", 22.0, 3.0, 0)
    (cell,) = tier_curves(events)[0]["verbs"][0]["cells"]
    assert cell["wall_s"] == 22.0
    assert cell["cold"]["wall_s"] == 74.0 and cell["cold"]["block_read"] == 6 << 30
    assert tier_regressions(events) == []          # 74 s cold vs a 25 s best: shown, not compared
    ledger = str(tmp_path / "g.timings.jsonl")
    for e in events:
        append_op(ledger, e, dedup=False)
    assert "cold 74.0 s" in render_perf(perf_report(ledger))


def test_tier_regressions_and_the_load_read():
    """Design detail 2f5033bf (2) / (3): a tier seat's latest reading against its best there;
    a load run read per verb (p50 / p99 of the harness's seconds, the CLI's own beside them),
    its throughput, failures by outcome, its clients' bytes and the load event's summary."""
    def bench(rid, ts, wall):
        return {"verb": "show", "kind": "bench", "run_id": rid, "ts": ts, "wall_s": wall,
                "outcome": 0, "attrs": {"lane": "dev", "tier": 8}}
    assert tier_regressions([bench("a", 1, 2.0), bench("b", 2, 1.0), bench("c", 3, 3.0)]) == [
        {"lane": "dev", "verb": "show", "tier": 8, "latest_s": 3.0, "best_s": 1.0, "ratio": 3.0}]
    events = []
    for i, (verb, wall, outcome, ro) in enumerate([("show", 1.0, 0, True), ("show", 3.0, 0, True),
                                                   ("decide", 2.0, 0, False), ("show", 0.5, 1, True)]):
        a = {"lane": "dev", "tier": 1, "load": "L", "clients": 2, "client": i % 2}
        events.append({"verb": verb, "kind": "bench", "run_id": f"L{i}", "ts": 10.0 + i,
                       "wall_s": wall, "outcome": outcome, "attrs": a})
        events.append({"verb": verb, "kind": "cli", "run_id": f"L{i}", "ts": 10.0 + i, "wall_s": wall,
                       "outcome": outcome, "phases": {"verb": {"calls": 1, "s": wall / 2}},
                       "io": {"write_bytes": 1000}, "attrs": {**a, "read_only": ro}})
    events.append({"verb": "bench-load", "kind": "bench", "ts": 20.0, "wall_s": 5.0,
                   "peak_rss_kb": 2048, "integrity": {"ok": True},
                   "attrs": {"lane": "dev", "tier": 1, "load": "L", "clients": 2}})
    (r,) = load_report(events)
    assert (r["clients"], r["ops"], r["failures"], r["window_s"]) == (2, 4, {"1": 1}, 4.0)
    show = next(v for v in r["verbs"] if v["verb"] == "show")
    assert (show["n"], show["p50_s"], show["p99_s"], show["verb_p99_s"]) == (2, 1.0, 3.0, 1.5)
    assert r["written"] == 3000 and r["peak_rss_kb"] == 2048 and r["integrity"] == {"ok": True}
    assert r["writes_per_s"] == 0.25 and r["ops_per_s"] == 0.75


def test_the_named_loads_read_7i_7b_7v_and_a_composed_load_never_gates():
    """Design detail 2c4bad75 (5): a named load's latest full run at its tier -- 7i the interactive
    clients' p99 on class (1)-(2) verbs over the solo pass's, 7b its own write floor, no failure,
    no integrity break and the summed RSS's growth over the scale sweep with the knee, 7v the held
    reader's p99; the largest op and the roles are reported; a composed load is a projection
    whose gate never counts toward promotion."""
    assert parse_roles("main=1 ingest=1, sub=4") == {"main": 1, "ingest": 1, "sub": 4}
    with pytest.raises(ValueError):
        parse_roles("main=one")

    def run(load_id, perf_load, clients, calls, t0, solo=None, scale=1.0, composed=False, aborted=None,
            floor=None):
        base = {"lane": "dev", "tier": 32, "load": load_id, "clients": clients, "perf_load": perf_load,
                "scale": scale, "composed": composed, **({"solo": solo} if solo else {})}
        evs = []
        for i, (client, role, inter, verb, wall, appended) in enumerate(calls):
            a = {**base, "client": client, "role": role, "interactive": inter}
            rid = f"{load_id}-{i}"
            evs.append({"verb": verb, "kind": "bench", "run_id": rid, "ts": t0 + i + wall,
                        "wall_s": wall, "outcome": 0, "attrs": a})
            evs.append({"verb": verb, "kind": "cli", "run_id": rid, "ts": t0 + i + wall, "wall_s": wall,
                        "outcome": 0, "attrs": {**a, "read_only": not appended},
                        "appended": {"w.jsonl": {"ops": 1, "bytes": appended}} if appended else {}})
        evs.append({"verb": "bench-load", "kind": "bench", "ts": t0 + 100, "wall_s": 10.0,
                    "peak_rss_kb": 1000, "integrity": {"ok": True}, "attrs": base, "aborted": aborted,
                    "visibility": {"n": 2, "p50_s": 0.01, "p99_s": 0.05, "max_s": 0.05, "unseen": 0},
                    **({"floor": {"writes_per_s": floor}} if floor is not None else {})})
        return evs
    l1 = [(0, "main", True, "show", 0.4, 0), (0, "main", True, "show", 0.6, 0),
          (1, "sub", True, "show", 0.5, 0), (2, "ingest", False, "pull-transcript", 2.0, 5000)]
    # 6a2b88c5 (2): a seed call is read apart -- never in the span's ops, failures or quantiles
    seed = {"verb": "decide", "kind": "bench", "run_id": "a-seed", "ts": 0.5, "wall_s": 0.5, "outcome": 1,
            "attrs": {"lane": "dev", "tier": 32, "load": "a", "clients": 3, "perf_load": "load-L1",
                      "scale": 1.0, "composed": False, "client": 0, "role": "main", "phase": "seed"}}
    events = [seed] + (run("a", "load-L1", 3, l1, 0.0, floor=0.1)
              + run("s", "load-L1", 1, [(0, "main", True, "show", 0.4, 0)], 200.0, solo="main")
              + run("b", "load-L1", 6, l1 * 2, 300.0, scale=2.0)
              + run("c", "load-L1", 12, l1 * 4, 500.0, scale=4.0)
              + run("p", "load-S4", 16, l1, 700.0, composed=True)
              + run("g", "load-L1", 24, l1, 900.0, scale=8.0, aborted="the clients' summed RSS passed the ceiling"))
    loads = load_report(events)
    full = next(r for r in loads if r["perf_load"] == "load-L1" and r["scale"] == 1.0 and not r["solo"])
    assert full["largest_op"]["verb"] == "pull-transcript" and full["largest_op"]["bytes"] == 5000
    assert [x["role"] for x in full["roles"]] == ["ingest", "main", "sub"]
    assert [v["verb"] for v in full["interactive"]] == ["show"] and full["visibility"]["p99_s"] == 0.05
    assert full["seed"] == {"calls": 1, "failed": 1} and full["ops"] == 4 and not full["failures"]
    classes = [{"key": "class-1", "position": 1, "targets": {"perf_b_max": "0.2"}, "members": ["show"]},
               {"key": "class-7i", "position": 7, "targets": {"perf_load_p99_ratio": "2", "perf_gates": "true"},
                "members": ["load-L1"]},
               {"key": "class-7b", "position": 7, "targets": {"perf_load_rss_b_max": "0.2", "perf_gates": "false"},
                "members": ["load-L1", "load-S4"]},
               {"key": "class-7v", "position": 7, "targets": {"perf_load_visibility_p99_s": "1", "perf_gates": "true"},
                "members": ["load-L1"]}]
    defs = {"load-L1": {"tier": 32, "lane": "dev", "writes_min": 0.1},
            "load-S4": {"tier": 32, "lane": "dev", "writes_min": None}}
    st = target_standing([], loads, classes, load_defs=defs)
    rows = {(r["class"], r["load"]): r for r in st["loads"]}
    seven_i = rows[("class-7i", "load-L1")]
    assert seven_i["status"] == "pass" and seven_i["checks"][0]["value"] == pytest.approx(0.6 / 0.4)
    assert [c["target"] for c in seven_i["checks"]] == ["interactive p99 ratio"]  # the load's floor is 7b's
    seven_b = rows[("class-7b", "load-L1")]
    assert {c["target"] for c in seven_b["checks"]} == {"load writes/s", "load RSS b", "failures",
                                                         "integrity breaks"}
    # the aborted run is the guard's reading: never in the knee or the RSS growth; 7b's class does
    # not gate (6f7a4013 (4)): reported, never counted toward promotion
    assert seven_b["status"] == "reported: pass" and seven_b["gate"] is None
    assert seven_b["knee"]["clients"] in (3, 6, 12) and seven_b["guard_at"] == 24
    assert rows[("class-7i", "load-L1")]["gate"] is True
    # 6a2b88c5 (3): a floor that is not the one the run's windows derive judges nothing
    stale = target_standing([], loads, classes, load_defs={**defs, "load-L1": {**defs["load-L1"], "writes_min": 0.5}})
    floor = next(c for r in stale["loads"] if r["class"] == "class-7b" and r["load"] == "load-L1"
                 for c in r["checks"] if c["target"] == "load writes/s")
    assert floor["stale"] and floor["ok"] is None and floor["derived"] == 0.1
    assert "STALE floor" in _check_cell(floor)
    assert knee([{"clients": 1, "ops_per_s": 1.0}, {"clients": 2, "ops_per_s": 1.9},
                 {"clients": 4, "ops_per_s": 2.0}]) == {"clients": 2, "ops_per_s": 1.9, "beyond": False}
    assert knee([{"clients": 1, "ops_per_s": 1.0}, {"clients": 2, "ops_per_s": 2.0}])["beyond"]
    assert knee([{"clients": 1, "ops_per_s": 1.0}]) is None
    assert rows[("class-7v", "load-L1")]["status"] == "pass"
    s4 = rows[("class-7b", "load-S4")]
    assert s4["composed"] and s4["status"].startswith("projection") and s4["gate"] is None
    assert all(s["seat"] != "show" or s["class"] == "class-1" for s in st["seats"])
    assert "load-S4 x32" in render_perf({"ledger": "g.timings.jsonl", "events": 0, "verbs": [], "rebuilds": [],
                                          "load": loads, "standing": st, "regressions": [], "fits": [],
                                          "guards": [], "unmeasured": []})
