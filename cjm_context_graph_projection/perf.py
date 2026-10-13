"""THE INSTRUMENT's projection half: one timing event per CLI invocation, and the `perf` read.

Design detail 681604c1 (work item 85b0d368). The collector lives in the primitives
(`timings`); this module arms it for a CLI invocation -- the launch and import before `main`,
the parse, the graph's open, the verb, the close -- measures the growth covariates while the
graph is open (outside the run's own tables), and appends the event to the timings ledger the
wrapper names (`--timings-path`, `<db stem>.timings.jsonl`: telemetry, prunable, never
replayed; fail-open). A rebuild's wrapper stamps one CJM_RUN_ID on every invocation it makes
(and its step in CJM_RUN_STEP) and appends its own phase event, so a rebuild reads whole.

`perf` reads the ledger: the latest and typical cost per verb, the phases that carry it, each
cost fitted against the covariates (a cost's SHAPE, ruling d7742c47 (b)), regressions against
a verb's own history, and the GUARDS -- a whole-scope storage read inside a write, an answer
cut at its limit and never continued, a replay verb whose cost rises with journal position, a
write that read most of the graph. Nothing it reports is stored.
"""

import json
import os
import statistics
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from cjm_context_graph_layer.ops import graph_task
from cjm_context_graph_primitives.journal import append_op, journal_segments, read_journal
from cjm_context_graph_primitives.timings import begin_run, CURRENT_RUN, end_run, Run, span

from .runtime import GraphHandle

PREFILL = "?"  # A run's name until its verb is parsed


def begin_invocation() -> Run:  # The armed run (named once the verb is parsed)
    """Arm the instrument at the top of `main` -- everything before it is the launch and the
    import; `invocation_parsed` disarms it when no ledger was named."""
    return begin_run("cli", PREFILL)


def invocation_parsed(
    run: Run,                     # The run `begin_invocation` armed
    command: str,                 # The parsed verb
    timings_path: Optional[str],  # The ledger; None = not recorded (the run is disarmed)
    **attrs,                      # Identifying context (db, read_only, ...)
) -> None:
    """Name the run, record the parse, and keep it armed only when a ledger will take it."""
    run.name = command
    run.add("parse", time.perf_counter() - run.t0)
    step = os.environ.get("CJM_RUN_STEP")
    run.attrs.update({k: v for k, v in attrs.items() if v is not None})
    if step:
        run.attrs["step"] = step
    if not timings_path:
        end_run(run)


def run_invocation(
    run: Run,                     # The armed (or disarmed) run
    timings_path: Optional[str],  # The ledger; None = nothing appended
    body: Callable[[], int],      # The invocation (returns its exit code)
) -> int:  # The body's exit code
    """Run the invocation and append its event, whatever its outcome -- a refusal or a crash
    is a measured run too (the outcome names it). The append is fail-open."""
    outcome: Any = None
    try:
        outcome = body()
        return outcome
    except SystemExit as e:
        outcome = e.code
        raise
    except BaseException as e:
        outcome = type(e).__name__
        raise
    finally:
        end_run(run)
        if timings_path:
            append_timing(timings_path, run.event(outcome=outcome))


def append_timing(
    path: str,              # The timings ledger (JSONL)
    event: Dict[str, Any],  # One run's event
) -> None:
    """Append one timing event: O(1), dedup off (every run is one event), fail-open -- the
    instrument must never break the work it measured."""
    try:
        append_op(path, event, dedup=False)
    except Exception as e:
        print(f"warning: timings-ledger append failed (the run itself completed): {e}",
              file=sys.stderr)


def journal_footprint(
    path: Optional[str],  # A journal's live path (the family's tail)
) -> Optional[Dict[str, int]]:  # {segments, bytes} on disk, or None
    """A journal family's size as stored: segments and bytes on disk (a cold segment counts
    its compressed size) -- O(segments), never a read of the history."""
    if not path:
        return None
    segs = journal_segments(path)
    return {"segments": len(segs), "bytes": sum(os.path.getsize(s) for s in segs)}


async def measure_covariates(
    gx: GraphHandle,                      # The open graph
    journals: Dict[str, Optional[str]],  # Journal family name -> live path
) -> Dict[str, Any]:  # {nodes, edges, db_bytes, kinds, journals}
    """The growth covariates every event carries: nodes, edges and bytes of the store, nodes
    per kind, and each journal family's segments and bytes. Measured with the run DISARMED, so
    the storage table holds the verb's calls alone; its cost is the `covariates` phase."""
    run = CURRENT_RUN.get()
    token = CURRENT_RUN.set(None)
    t = time.perf_counter()
    try:
        schema = await graph_task(gx.queue, gx.graph_id, "get_schema")
        kinds = dict(sorted((schema.get("counts") or {}).items()))
        out: Dict[str, Any] = {"nodes": sum(kinds.values()), "edges": schema.get("edge_count"),
                               "db_bytes": schema.get("db_bytes"), "kinds": kinds}
        fams = {name: journal_footprint(p) for name, p in journals.items() if p}
        out["journals"] = {k: v for k, v in fams.items() if v and v["segments"]}
        return out
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    finally:
        CURRENT_RUN.reset(token)
        if run is not None:
            run.add("covariates", time.perf_counter() - t)


@asynccontextmanager
async def invocation_graph(
    graph_cm,                             # `open_graph(...)`: its own spans measure the open / close
    journals: Dict[str, Optional[str]],  # Journal family name -> live path, for the covariates
) -> AsyncIterator[GraphHandle]:  # The open graph
    """The invocation's graph with its phases: the body runs as `verb`, and the covariates are
    measured after it, before the graph closes (only when a run is armed)."""
    async with graph_cm as gx:
        with span("verb"):
            yield gx
        run = CURRENT_RUN.get()
        if run is not None:
            run.covariates = await measure_covariates(gx, journals)


# ---------------------------------------------------------------------------------------------
# The read: `perf`
# ---------------------------------------------------------------------------------------------

FIT_COVARIATES = ("nodes", "edges", "db_bytes", "journal_bytes")
REGRESSION_RATIO = 1.5    # latest / median of the verb's earlier runs ...
REGRESSION_FLOOR_S = 0.25  # ... and at least this much slower
SLOPE_RATIO = 2.0          # a replay verb's late-bin mean cost over its early-bin mean
GRAPH_SHARE = 0.5          # a write's rows read over the graph's nodes


def _covariate(ev: Dict[str, Any], name: str) -> Optional[float]:  # One covariate of an event
    cov = ev.get("covariates") or {}
    if name == "journal_bytes":
        fams = cov.get("journals") or {}
        return float(sum(f.get("bytes", 0) for f in fams.values())) if fams else None
    v = cov.get(name)
    return float(v) if isinstance(v, (int, float)) else None


def self_times(
    phases: Dict[str, Dict[str, float]],  # An event's phase table (path -> {calls, s, ...})
) -> Dict[str, float]:  # path -> seconds spent in the phase itself, its child phases excluded
    """A phase's SELF time: its inclusive seconds less its direct children's."""
    out = {p: row.get("s", 0.0) for p, row in phases.items()}
    for p, row in phases.items():
        parent = p.rsplit("/", 1)[0] if "/" in p else None
        if parent in out:
            out[parent] -= row.get("s", 0.0)
    return out


def end_to_end(
    ev: Dict[str, Any],  # One CLI event
) -> float:  # Seconds from the wrapper's clock to the run's end
    """An invocation's whole cost as its caller waits for it: the in-process wall plus the
    launch (conda run + interpreter start) and the import measured before `main`."""
    phases = ev.get("phases") or {}
    return ev.get("wall_s", 0.0) + sum((phases.get(p) or {}).get("s", 0.0)
                                       for p in ("launch", "import"))


def fit(
    xs: List[float],  # Covariate values
    ys: List[float],  # Costs
) -> Optional[Dict[str, float]]:  # {slope, intercept, r2, n}, or None when x does not vary
    """Least squares of cost on one covariate -- the shape d7742c47 (b) reads. None when the
    covariate barely moved (under 5 %): a slope over a constant is noise."""
    if len(xs) < 4 or min(xs) <= 0 or max(xs) / min(xs) < 1.05:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    syy = sum((y - my) ** 2 for y in ys)
    slope = sxy / sxx
    r2 = (sxy * sxy) / (sxx * syy) if syy else 0.0
    return {"slope": slope, "intercept": my - slope * mx, "r2": r2, "n": len(xs)}


def series_trend(
    bins: Dict[str, Dict[str, float]],  # A series: bin start -> {calls, s}
    min_calls: int = 5,                 # Bins with fewer calls are left out (too noisy)
) -> Optional[Dict[str, float]]:  # {early, late, ratio, bins}, or None under three usable bins
    """A positioned cost's trend: the mean cost per item in the last third of the bins over the
    first third. Healthy is ~1 (flat in position); a rising ratio is history-shaped."""
    rows = sorted(((int(k), v) for k, v in bins.items() if v.get("calls", 0) >= min_calls),
                  key=lambda kv: kv[0])
    if len(rows) < 3:
        return None
    third = max(1, len(rows) // 3)

    def mean(part):
        return sum(v["s"] for _, v in part) / sum(v["calls"] for _, v in part)
    early, late = mean(rows[:third]), mean(rows[-third:])
    return {"early": early, "late": late, "ratio": late / early if early else 0.0,
            "bins": len(rows)}


def guards(ev: Dict[str, Any]) -> List[Dict[str, Any]]:  # The guard findings on one event
    """The design's guards, read off one event (681604c1): each row names the guard, what it
    saw and where."""
    out: List[Dict[str, Any]] = []
    attrs = ev.get("attrs") or {}
    writes = not attrs.get("read_only")
    if writes:
        for where, row in (ev.get("whole") or {}).items():
            out.append({"guard": "whole-scope-in-write", "where": where, "calls": row["calls"],
                        "s": row["s"]})
    for method, row in (ev.get("storage") or {}).items():
        cut, paged = row.get("at_limit", 0), row.get("paged", 0)
        if cut > paged:
            out.append({"guard": "cut-answer", "where": method,
                        "detail": f"{cut} answer(s) at their limit, {paged} continued"})
    for name, bins in (ev.get("series") or {}).items():
        trend = series_trend(bins)
        if trend and trend["ratio"] >= SLOPE_RATIO:
            out.append({"guard": "rising-with-position", "where": name,
                        "detail": f"late {trend['late'] * 1000:.2f} ms vs early "
                                  f"{trend['early'] * 1000:.2f} ms per op (x{trend['ratio']:.1f}, "
                                  f"{trend['bins']} bins)"})
    nodes = _covariate(ev, "nodes")
    read = sum(row.get("rows_out", 0) for row in (ev.get("storage") or {}).values())
    if writes and nodes and read / nodes >= GRAPH_SHARE and ev.get("kind") == "cli":
        out.append({"guard": "read-most-of-the-graph", "where": ev.get("verb"),
                    "detail": f"{read:,} rows read over {int(nodes):,} nodes"})
    return out


def perf_report(
    path: str,                   # The timings ledger
    verb: Optional[str] = None,  # Only this verb
    runs: int = 50,              # History kept per verb (the newest N)
    since: Optional[float] = None,  # Only events at or after this unix time
) -> Dict[str, Any]:  # {ledger, events, verbs, fits, regressions, guards, rebuilds, unmeasured}
    """The ledger read: per verb the latest / median / p90 END-TO-END seconds (launch + import
    + wall), the latest run's heaviest phases by self time and storage by method; fits of that
    cost against each covariate; regressions; the guards on each verb's latest run; the latest
    rebuild per lane."""
    events = [e for e in read_journal(path) if not since or (e.get("ts") or 0) >= since]
    by_verb: Dict[str, List[Dict[str, Any]]] = {}
    for e in events:
        if e.get("kind") == "cli" and (verb is None or e.get("verb") == verb):
            by_verb.setdefault(e.get("verb") or "?", []).append(e)
    verbs, fits, regressions, found = [], [], [], []
    guarded = set()  # (run_id, verb, ts) of events whose guards are listed: each event once
    for name, evs in sorted(by_verb.items()):
        evs = sorted(evs, key=lambda e: e.get("ts") or 0)[-runs:]
        walls = [end_to_end(e) for e in evs]
        latest = evs[-1]
        selfs = sorted(self_times(latest.get("phases") or {}).items(), key=lambda kv: -kv[1])
        q = statistics.quantiles(walls, n=10)[-1] if len(walls) >= 2 else walls[-1]
        verbs.append({"verb": name, "runs": len(evs), "latest_s": walls[-1],
                      "median_s": statistics.median(walls), "p90_s": q,
                      "top_phases": [{"phase": p, "self_s": s} for p, s in selfs[:4]],
                      "storage": {m: {"calls": r["calls"], "s": r["s"]}
                                  for m, r in sorted((latest.get("storage") or {}).items(),
                                                     key=lambda kv: -kv[1]["s"])[:4]}})
        for cov in FIT_COVARIATES:
            pts = [(_covariate(e, cov), end_to_end(e)) for e in evs]
            pts = [(x, y) for x, y in pts if x is not None]
            f = fit([x for x, _ in pts], [y for _, y in pts])
            if f:
                fits.append({"verb": name, "covariate": cov, **f})
        if len(walls) >= 4:
            base = statistics.median(walls[:-1])
            if walls[-1] >= base * REGRESSION_RATIO and walls[-1] - base >= REGRESSION_FLOOR_S:
                regressions.append({"verb": name, "latest_s": walls[-1], "median_s": base,
                                    "ratio": walls[-1] / base})
        found += [{"verb": name, **g} for g in guards(latest)]
        guarded.add((latest.get("run_id"), latest.get("verb"), latest.get("ts")))
    rebuilds: Dict[str, Dict[str, Any]] = {}
    for e in events:
        if e.get("kind") == "rebuild":
            lane = (e.get("attrs") or {}).get("lane", "?")
            if lane not in rebuilds or (e.get("ts") or 0) > (rebuilds[lane].get("ts") or 0):
                rebuilds[lane] = e
    rebuild_rows = []
    for lane, e in sorted(rebuilds.items()):
        steps = [s for s in events if s.get("run_id") == e.get("run_id") and s.get("kind") == "cli"]
        step_rows = []
        for s in sorted(steps, key=lambda s: s.get("ts") or 0):
            selfs = sorted(self_times(s.get("phases") or {}).items(), key=lambda kv: -kv[1])
            step_rows.append({"step": (s.get("attrs") or {}).get("step") or s.get("verb"),
                              "verb": s.get("verb"), "wall_s": s.get("wall_s"),
                              "top_phases": [{"phase": p, "self_s": t} for p, t in selfs[:3]]})
            if (s.get("run_id"), s.get("verb"), s.get("ts")) not in guarded:
                found += [{"verb": f"{s.get('verb')} (rebuild {lane})", **g} for g in guards(s)]
        rebuild_rows.append({"lane": lane, "ts": e.get("ts"), "wall_s": e.get("wall_s"),
                             "outcome": e.get("outcome"), "phases": e.get("phases"),
                             "steps": step_rows})
    return {"ledger": path, "events": len(events), "verbs": verbs, "fits": fits,
            "regressions": regressions, "guards": found, "rebuilds": rebuild_rows,
            "unmeasured": ["the query-plan SCAN guard (needs a plan read through the storage seam)",
                           "journal op counts per family (B's journal index)",
                           "repos on the graph as a covariate",
                           "app gestures (the Qt kit's loop-thread seam)",
                           "the scale harness's synthetic tiers"]}


def _s(v: Optional[float]) -> str:  # Seconds, compact
    return "-" if v is None else (f"{v:.3f}" if v < 10 else f"{v:.1f}")


def render_perf(
    res: Dict[str, Any],  # `perf_report`'s result
    fmt: str = "human",   # "human" | "agent" (JSON)
) -> str:  # The rendered read
    """Render the perf read: tables for a person, the result as JSON for an agent."""
    if fmt == "agent":
        return json.dumps(res, indent=1, default=str)
    out = [f"## Perf — {Path(res['ledger']).name} · {res['events']} event(s)", ""]
    if not res["verbs"] and not res["rebuilds"]:
        return "\n".join(out + ["(no events yet -- the wrappers record one per invocation)"])
    out += ["### Verbs (seconds end to end: launch + import + the run)", "",
            "| verb | runs | latest | median | p90 | heaviest phases, latest run (self s) |",
            "|---|---:|---:|---:|---:|---|"]
    for v in res["verbs"]:
        phases = " · ".join(f"{p['phase']} {_s(p['self_s'])}" for p in v["top_phases"])
        out.append(f"| {v['verb']} | {v['runs']} | {_s(v['latest_s'])} | {_s(v['median_s'])} | "
                   f"{_s(v['p90_s'])} | {phases} |")
    if res["regressions"]:
        out += ["", "### Regressions", ""]
        out += [f"- **{r['verb']}** {_s(r['latest_s'])} s vs median {_s(r['median_s'])} s "
                f"(x{r['ratio']:.1f})" for r in res["regressions"]]
    if res["fits"]:
        out += ["", "### Cost shape (end-to-end seconds on each covariate)", "",
                "| verb | covariate | seconds per unit | r² | runs |", "|---|---|---:|---:|---:|"]
        out += [f"| {f['verb']} | {f['covariate']} | {f['slope']:.3g} | {f['r2']:.2f} | {f['n']} |"
                for f in sorted(res["fits"], key=lambda f: (f["verb"], -f["r2"]))]
    out += ["", "### Guards", ""]
    out += ([f"- `{g['guard']}` · {g['verb']} · {g['where']}"
             + (f" — {g['detail']}" if g.get("detail") else f" — {g['calls']} call(s), {_s(g['s'])} s")
             for g in res["guards"]] or ["- none tripped"])
    for r in res["rebuilds"]:
        phases = " · ".join(f"{k} {_s(v.get('s'))}" for k, v in (r.get("phases") or {}).items())
        out += ["", f"### Rebuild — {r['lane']} lane · {_s(r['wall_s'])} s · outcome {r['outcome']}",
                "", f"- phases: {phases}"]
        for s in r["steps"]:
            top = " · ".join(f"{p['phase']} {_s(p['self_s'])}" for p in s["top_phases"])
            out.append(f"- step `{s['step']}` ({s['verb']}) {_s(s['wall_s'])} s — {top}")
    out += ["", "_Not yet measured: " + "; ".join(res["unmeasured"]) + "._"]
    return "\n".join(out)
