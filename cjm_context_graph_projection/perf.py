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
import math
import os
import statistics
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from cjm_context_graph_layer.ops import graph_task, store_covariates, WRITE_METHODS
from cjm_context_graph_primitives.journal import journal_segments, read_journal
from cjm_context_graph_primitives.query import NodeQuery, PropertyPredicate
from cjm_context_graph_primitives.timings import (append_event, begin_run, CURRENT_RUN, end_run,
                                                  Run, span)
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.vocab import DevNodeKinds

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
        run.attrs["step"] = step  # a scale-harness run's tier rides from its birth (the Run's own)
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
    instrument must never break the work it measured. The primitives' `append_event`, the one
    append every recorder shares (an app's gestures too, design detail 4389a579)."""
    append_event(path, event)


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
) -> Dict[str, Any]:  # {nodes, edges, db_bytes, kinds, repos, journals}
    """The growth covariates every event carries: the store half (nodes, edges and bytes of the
    store, nodes per kind -- the layer's `store_covariates`, which an app's session measures
    beside its gestures too, design detail 4389a579), the repos on the graph (the code lane's
    distinct repo keys), and each journal family's segments and bytes. Measured with the run
    DISARMED, so the storage table holds the verb's calls alone; its cost is the `covariates`
    phase."""
    run = CURRENT_RUN.get()
    token = CURRENT_RUN.set(None)
    t = time.perf_counter()
    try:
        out = await store_covariates(gx.queue, gx.graph_id)
        if "error" in out:
            return out
        if out["kinds"].get("CodeModule"):
            mods = await graph_task(gx.queue, gx.graph_id, "query_nodes",
                                    query=NodeQuery(label="CodeModule", project=["repo_key"]).to_dict())
            out["repos"] = len({r.get("repo_key") for r in (mods.rows or []) if r.get("repo_key")})
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

FIT_COVARIATES = ("nodes", "edges", "db_bytes", "journal_bytes", "repos")
REGRESSION_RATIO = 1.5    # latest / median of the verb's earlier runs ...
REGRESSION_FLOOR_S = 0.25  # ... and at least this much slower
SLOPE_RATIO = 2.0          # a replay verb's late-bin mean cost over its early-bin mean
GRAPH_SHARE = 0.5          # a write's rows read over the graph's nodes
READ_WROTE_SLACK = 256 * 1024  # bytes a read-only run may write net beyond its own journal appends
PAGE_BYTES = 4096  # a disk write's grain: a written-bytes curve's floor, so a zero is a reading (634fa576)


def _covariate(ev: Dict[str, Any], name: str) -> Optional[float]:  # One covariate of an event
    cov = ev.get("covariates") or {}
    if name == "journal_bytes":
        fams = cov.get("journals") or {}
        return float(sum(f.get("bytes", 0) for f in fams.values())) if fams else None
    v = cov.get(name)
    return float(v) if isinstance(v, (int, float)) else None


def net_written(
    io: Optional[Dict[str, int]],  # An event's I/O counters (`io`), or a harness record's
) -> Optional[int]:  # Net bytes written to the disk, or None when no counters were taken
    """Bytes a run wrote to the disk (design detail 2f5033bf (1)): write_bytes less
    cancelled_write_bytes -- a temp file deleted before writeback is cancelled, never written."""
    if not io or "write_bytes" not in io:
        return None
    return max(0, int(io["write_bytes"]) - int(io.get("cancelled_write_bytes", 0)))


def appended_bytes(
    ev: Dict[str, Any],  # One event
) -> int:  # Journal bytes the run appended: a write's logical size
    """The run's journal appends, every file (design detail 2f5033bf (1))."""
    return sum(int(r.get("bytes", 0)) for r in (ev.get("appended") or {}).values())


def logical_bytes(
    ev: Dict[str, Any],  # One event
) -> int:  # The run's logical change in bytes
    """The denominator of a run's write amplification (ruling 9af0e42e (1) / (3)): the journal
    bytes it appended (a write) plus the journal bytes of the ops it replayed (a replay)."""
    return appended_bytes(ev) + sum(int(r.get("bytes", 0)) for r in (ev.get("replayed") or {}).values())


def io_row(
    ev: Dict[str, Any],  # One event
) -> Dict[str, Any]:  # {written, read, appended, replayed, store_written, commits, amplification}
    """An event's disk figures (design detail 2f5033bf (1)): net bytes written and bytes read
    from the disk (the process's, its graph worker included once reaped), the journal bytes it
    appended and replayed (the logical size), the store's own share (the worker's per-call
    I/O), its commits (one per store write call: each is one unit of write work in the store)
    and the write amplification -- net bytes written over the logical size."""
    io = ev.get("io") or {}
    written, appended, logical = net_written(io), appended_bytes(ev), logical_bytes(ev)
    storage = ev.get("storage") or {}
    store = None
    if any("io_write_bytes" in r for r in storage.values()):
        store = max(0, sum(int(r.get("io_write_bytes", 0)) - int(r.get("io_cancelled_write_bytes", 0))
                           for r in storage.values()))
    return {"written": written, "read": io.get("read_bytes"), "appended": appended,
            "replayed": logical - appended, "store_written": store,
            "commits": sum(int(r.get("calls", 0)) for m, r in storage.items() if m in WRITE_METHODS),
            "amplification": written / logical if written is not None and logical else None}


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
    saw and where. A gesture (design detail 4389a579) is a write when its session said so (an
    `op_write` method) or when it called a write method of the store."""
    out: List[Dict[str, Any]] = []
    attrs = ev.get("attrs") or {}
    if ev.get("kind") == "gesture":
        writes = bool(attrs.get("write")) or any(m in WRITE_METHODS for m in (ev.get("storage") or {}))
    else:
        writes = not attrs.get("read_only")
    if writes:
        for where, row in (ev.get("whole") or {}).items():
            out.append({"guard": "whole-scope-in-write", "where": where, "calls": row["calls"],
                        "s": row["s"]})
    for where, row in (ev.get("plan_scans") or {}).items():
        # the plan guard (3bd8673d): a typed query whose plan reads a table whole, or a whole
        # kind row by row -- O(graph) or O(kind) whatever it returns, read or write alike
        out.append({"guard": "plan-scan", "where": where, "calls": row["calls"], "s": row["s"]})
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
    written = net_written(ev.get("io"))
    if (ev.get("kind") == "cli" and attrs.get("read_only") and written is not None
            and written - appended_bytes(ev) > READ_WROTE_SLACK):
        # 2f5033bf (1): a read writes nothing but its own ledger appends (ff3ede3d's class)
        out.append({"guard": "read-wrote", "where": ev.get("verb"),
                    "detail": f"{written:,} bytes written net, {appended_bytes(ev):,} appended"})
    nodes = _covariate(ev, "nodes")
    read = sum(row.get("rows_out", 0) for row in (ev.get("storage") or {}).values())
    if writes and nodes and read / nodes >= GRAPH_SHARE and ev.get("kind") in ("cli", "gesture"):
        out.append({"guard": "read-most-of-the-graph", "where": ev.get("verb"),
                    "detail": f"{read:,} rows read over {int(nodes):,} nodes"})
    return out


def gesture_cost(
    ev: Dict[str, Any],  # One gesture event (or a poll's rolling one)
    per: int = 1,        # Divide by this many ticks (a poll's per-tick cost)
) -> Dict[str, float]:  # {queued_s, round_trips, rows, bytes}
    """What a gesture cost the store (design detail 4389a579): its wait on the loop and its
    round trips, rows and wire bytes, read off its storage table -- per tick for a poll."""
    storage = (ev.get("storage") or {}).values()
    n = max(1, per)
    return {"queued_s": ((ev.get("phases") or {}).get("queued") or {}).get("s", 0.0) / n,
            "round_trips": sum(r.get("calls", 0) for r in storage) / n,
            "rows": sum(r.get("rows_in", 0) + r.get("rows_out", 0) for r in storage) / n,
            "bytes": sum(r.get("bytes_in", 0) + r.get("bytes_out", 0) for r in storage) / n}


def poll_rows(
    polls: Dict[str, List[Dict[str, Any]]],  # Poll name -> its rolling events
    runs: int = 50,                          # Windows kept per poll (the newest N)
) -> List[Dict[str, Any]]:  # Per poll: {gesture, windows, ticks, tick_s, failed, ...per-tick cost}
    """A poll's cost per tick (design detail 4389a579): its rolling windows' ticks, the mean work
    per tick and what each tick cost the store; the latest window's tick cost beside the mean."""
    out = []
    for name, evs in sorted(polls.items()):
        evs = sorted(evs, key=lambda e: e.get("ts") or 0)[-runs:]
        ticks = [((e.get("phases") or {}).get("tick") or {}) for e in evs]
        n = sum(t.get("calls", 0) for t in ticks)
        if not n:
            continue
        last = ticks[-1]
        total = {"phases": {"queued": {"s": sum(((e.get("phases") or {}).get("queued") or {})
                                                 .get("s", 0.0) for e in evs)}},
                 "storage": {}}
        for e in evs:
            for m, r in (e.get("storage") or {}).items():
                row = total["storage"].setdefault(m, {})
                for k, v in r.items():
                    row[k] = row.get(k, 0) + v
        out.append({"gesture": name, "windows": len(evs), "ticks": n,
                    "tick_s": sum(t.get("s", 0.0) for t in ticks) / n,
                    "latest_tick_s": last.get("s", 0.0) / last["calls"] if last.get("calls") else None,
                    "failed": sum(t.get("failed", 0) for t in ticks), **gesture_cost(total, n)})
    return out


def growth(
    cells: List[Dict[str, Any]],  # One verb's cells across the tiers ({tier, wall_s, outcome, ...})
    key: str = "wall_s",          # The measure fitted: seconds, or peak RSS, bytes written, answer bytes
    floor: Optional[float] = None,  # A measure whose zero is a reading: its grain (bytes written: a page)
) -> Optional[Dict[str, float]]:  # {b, r2, n}: measure ~ tier^b, or None under three good tiers
    """A measure's growth exponent across the scale tiers (design detail 3bd8673d): least
    squares of log(measure) on log(tier) over the runs that succeeded. b ~ 0 is flat, ~ 1
    linear, ~ 2 quadratic -- the shape d7742c47 (b) reads, against a size the harness controls;
    any measure a cell carries (2f5033bf (1): bytes written beside seconds and RSS). A measure
    given a `floor` keeps its zero cells as data (finding 634fa576): zero at every tier is the
    flattest reading there is (b 0, r2 n/a), and any other curve fits log(measure + floor) --
    never unmeasured, never a slope over the non-zero tiers alone."""
    good = [c for c in cells if c.get("outcome") == 0 and (c.get("tier") or 0) >= 1
            and c.get(key) is not None and (floor is not None or c[key] > 0)]
    if len({c["tier"] for c in good}) < 3:
        return None
    if floor is not None and all(c[key] == 0 for c in good):
        return {"b": 0.0, "r2": None, "n": len(good)}
    pts = [(math.log(c["tier"]), math.log(c[key] + (floor or 0))) for c in good]
    mx, my = statistics.fmean(x for x, _ in pts), statistics.fmean(y for _, y in pts)
    sxx = sum((x - mx) ** 2 for x, _ in pts)
    sxy = sum((x - mx) * (y - my) for x, y in pts)
    syy = sum((y - my) ** 2 for _, y in pts)
    return {"b": sxy / sxx, "r2": (sxy * sxy) / (sxx * syy) if syy else 1.0, "n": len(pts)}


def _keep_latest(
    table: Dict[tuple, Dict[str, Any]],  # (lane, verb, tier) -> the reading kept
    key: tuple,                          # The seat at its tier
    e: Dict[str, Any],                   # A candidate event
) -> None:
    """Keep a seat's reading at a tier (ruling b7b3976f (B)): a WARM run before an unlabelled one
    (a run from before the harness labelled its cache condition), then the latest."""
    def rank(ev: Dict[str, Any]) -> tuple:
        return ((ev.get("attrs") or {}).get("cache") == "warm", ev.get("ts") or 0)
    if key not in table or rank(e) > rank(table[key]):
        table[key] = e


def _cold_cell(
    e: Optional[Dict[str, Any]],               # The seat's cold reading at the tier, or None
    cli: Dict[Optional[str], Dict[str, Any]],  # The CLI events by run id
) -> Optional[Dict[str, Any]]:  # {wall_s, verb_s, block_read, outcome}, or None
    """A tier's COLD reading, shown beside the warm one and gating nothing (ruling b7b3976f (B)):
    its seconds and the bytes its run read from the block layer -- the evidence it ran cold."""
    if e is None:
        return None
    c = cli.get(e.get("run_id")) or (e if e.get("kind") == "gesture" else {})
    return {"wall_s": e.get("wall_s"), "outcome": e.get("outcome"),
            "verb_s": ((c.get("phases") or {}).get("verb") or {}).get("s"),
            "block_read": (c.get("io") or {}).get("read_bytes")}


def tier_curves(
    events: List[Dict[str, Any]],  # The ledger's tier-tagged events (the harness's, the CLI's, the gestures')
    verb: Optional[str] = None,    # Only this canonical verb or gesture
) -> List[Dict[str, Any]]:  # Per lane: {lane, tiers, verbs: [{verb, cells, growth}], builds}
    """The scale harness's read (design detail 3bd8673d): per lane and canonical verb, the
    latest run at each tier -- the harness's wall and peak RSS, joined by run id with the CLI's
    own event for its heaviest phases -- and the growth exponent across the tiers; beside them
    the latest build wall per tier. Each canonical GESTURE is a row too (design detail
    4389a579): its own wall (a poll's per tick), the peak RSS of the driver process that ran
    it (joined by run id), its heaviest phases."""
    cli = {e.get("run_id"): e for e in events if e.get("kind") == "cli" and e.get("run_id")}
    harness = {e.get("run_id"): e for e in events if e.get("kind") == "bench" and e.get("run_id")}
    latest: Dict[tuple, Dict[str, Any]] = {}
    gestures: Dict[tuple, Dict[str, Any]] = {}
    builds: Dict[tuple, Dict[str, Any]] = {}
    colds: Dict[tuple, Dict[str, Any]] = {}  # b7b3976f (B): each seat's cold reading, beside the warm
    for e in events:
        a = e.get("attrs") or {}
        if e.get("kind") == "gesture":
            if verb and e.get("verb") != verb:
                continue
            _keep_latest(colds if a.get("cache") == "cold" else gestures,
                         (a.get("lane"), e.get("verb"), a.get("tier")), e)
            continue
        if e.get("kind") != "bench":
            continue
        if e.get("verb") == "bench-build":
            key = (a.get("lane"), a.get("tier"))
            if key not in builds or (e.get("ts") or 0) > (builds[key].get("ts") or 0):
                builds[key] = e
            continue
        if not e.get("run_id") or (verb and e.get("verb") != verb):
            continue
        _keep_latest(colds if a.get("cache") == "cold" else latest,
                     (a.get("lane"), e.get("verb"), a.get("tier")), e)
    lanes: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for (lane, name, tier), e in latest.items():
        c = cli.get(e.get("run_id"))
        selfs = sorted(self_times(c.get("phases") or {}).items(), key=lambda kv: -kv[1]) if c else []
        lanes.setdefault(lane, {}).setdefault(name, []).append(
            {"tier": tier, "wall_s": e.get("wall_s"), "peak_rss_kb": e.get("peak_rss_kb"),
             "outcome": e.get("outcome"), "nodes": _covariate(c, "nodes") if c else None,
             # 2f5033bf: the verb's own seconds (the launch floor excluded, until A), its net
             # bytes written (the CLI's own counters, its worker included), its answer's size
             "verb_s": (((c.get("phases") or {}).get("verb") or {}).get("s") if c else None),
             "written": net_written(c.get("io")) if c else None, "out_bytes": e.get("out_bytes"),
             # 9af0e42e: a write seat is held to its amplification, a read-only one to the MB ceiling
             "read_only": (c.get("attrs") or {}).get("read_only") if c else None,
             "logical": logical_bytes(c) if c else None,
             "amplification": io_row(c)["amplification"] if c else None,
             "cold": _cold_cell(colds.get((lane, name, tier)), cli),
             "top_phases": [{"phase": p, "self_s": s} for p, s in selfs[:3]]})
    for (lane, name, tier), e in gestures.items():
        h = harness.get(e.get("run_id")) or {}
        phases = e.get("phases") or {}
        tick = phases.get("tick") or {}
        poll = (e.get("attrs") or {}).get("poll")
        wall = tick.get("s", 0.0) / tick["calls"] if poll and tick.get("calls") else e.get("wall_s")
        selfs = sorted(self_times(phases).items(), key=lambda kv: -kv[1])
        lanes.setdefault(lane, {}).setdefault(name, []).append(
            {"tier": tier, "wall_s": wall, "peak_rss_kb": h.get("peak_rss_kb"),
             "outcome": 0 if e.get("outcome") == "ok" else e.get("outcome"),
             "nodes": _covariate(e, "nodes"), "gesture": True, "verb_s": wall,
             "written": net_written(e.get("io")),  # process-wide: an upper bound (2f5033bf (1))
             "read_only": None, "logical": logical_bytes(e),  # a gesture writes when it appends
             "amplification": io_row(e)["amplification"],
             "cold": _cold_cell(colds.get((lane, name, tier)), cli),
             "top_phases": [{"phase": p, "self_s": s} for p, s in selfs[:3]]})
    out: List[Dict[str, Any]] = []
    for lane in sorted(set(lanes) | {l for l, _ in builds}, key=str):
        verbs = []
        for name, cells in sorted((lanes.get(lane) or {}).items()):
            cells.sort(key=lambda c: c["tier"])
            verbs.append({"verb": name, "cells": cells, "growth": growth(cells),
                          "growth_verb": growth(cells, "verb_s"),
                          "growth_rss": growth(cells, "peak_rss_kb"),
                          "growth_written": growth(cells, "written", floor=PAGE_BYTES),
                          "growth_answer": growth(cells, "out_bytes")})
        built = {t: e.get("wall_s") for (l, t), e in builds.items() if l == lane}
        tiers = sorted({c["tier"] for v in verbs for c in v["cells"]} | set(built))
        out.append({"lane": lane, "tiers": tiers, "verbs": verbs, "builds": built})
    return out


def tier_regressions(
    events: List[Dict[str, Any]],  # The ledger's tier-tagged events (load runs excluded)
) -> List[Dict[str, Any]]:  # [{lane, verb, tier, latest_s, best_s, ratio}]
    """A seat's latest reading at a tier against its best there (design detail 2f5033bf (3)):
    the ratchet's regression -- the latest successful run at least REGRESSION_RATIO times the
    best earlier one and REGRESSION_FLOOR_S slower. A tier holds a fixed size, so its own
    history is the baseline (a live verb's moves with the graph). A canonical verb's reading is
    the harness's wall, a gesture's its own (a poll's ride their driver)."""
    runs: Dict[tuple, List[Dict[str, Any]]] = {}
    for e in events:
        a = e.get("attrs") or {}
        if a.get("cache") == "cold":
            continue  # b7b3976f (B): a cold reading is shown, never compared
        if e.get("kind") == "bench" and e.get("run_id") and e.get("outcome") == 0:
            pass
        elif e.get("kind") == "gesture" and e.get("outcome") == "ok" and not a.get("poll"):
            pass
        else:
            continue
        runs.setdefault((str(a.get("lane")), str(e.get("verb")), a.get("tier")), []).append(e)
    out = []
    for (lane, verb, tier), evs in sorted(runs.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or 0)):
        evs.sort(key=lambda e: e.get("ts") or 0)
        if len(evs) < 2:
            continue
        best = min(e.get("wall_s") or 0 for e in evs[:-1])
        latest = evs[-1].get("wall_s") or 0
        if best > 0 and latest >= best * REGRESSION_RATIO and latest - best >= REGRESSION_FLOOR_S:
            out.append({"lane": lane, "verb": verb, "tier": tier, "latest_s": latest,
                        "best_s": best, "ratio": latest / best})
    return out


def _quantile(
    xs: List[float],  # Values, sorted ascending
    q: float,         # The quantile (0 - 1)
) -> Optional[float]:  # The nearest-rank quantile, or None when empty
    if not xs:
        return None
    return xs[min(len(xs) - 1, max(0, math.ceil(q * len(xs)) - 1))]


def load_report(
    events: List[Dict[str, Any]],  # The ledger's load-run events (attrs.load set)
) -> List[Dict[str, Any]]:  # The latest load run per (lane, tier, clients)
    """The concurrent replay mode's read (design detail 2f5033bf (2)): per load run -- its lane,
    tier and client count -- each verb's end-to-end seconds as the harness saw them (p50 / p99)
    with the CLI's own verb seconds beside them, the run's throughput (ops and writes per second
    over its window), its failures by outcome, the net bytes its clients wrote (their own
    counters), and from the load's own event the clients' summed peak RSS and the integrity
    check. The latest run per (lane, tier, clients)."""
    cli = {e.get("run_id"): e for e in events if e.get("kind") == "cli" and e.get("run_id")}
    runs: Dict[str, Dict[str, Any]] = {}
    for e in events:
        a = e.get("attrs") or {}
        if not a.get("load") or e.get("kind") != "bench":
            continue
        r = runs.setdefault(a["load"], {"load": a["load"], "lane": a.get("lane"), "tier": a.get("tier"),
                                        "clients": a.get("clients"), "calls": [], "summary": {}})
        if e.get("verb") == "bench-load":
            r["summary"] = e
        elif e.get("run_id"):
            r["calls"].append(e)
    latest: Dict[tuple, Dict[str, Any]] = {}
    for r in runs.values():
        if not r["calls"]:
            continue
        r["ts"] = max(c.get("ts") or 0 for c in r["calls"])
        key = (str(r["lane"]), r["tier"] or 0, r["clients"] or 0)
        if key not in latest or r["ts"] > latest[key]["ts"]:
            latest[key] = r
    out = []
    for _, r in sorted(latest.items()):
        calls = r["calls"]
        t0 = min(c.get("ts") or 0 for c in calls)
        window = max(max((c.get("ts") or 0) + (c.get("wall_s") or 0) for c in calls) - t0, 1e-9)
        ok = [c for c in calls if c.get("outcome") == 0]
        mine = [cli.get(c["run_id"]) or {} for c in ok]
        writes = sum(1 for c in mine if c and not (c.get("attrs") or {}).get("read_only"))
        fails: Dict[str, int] = {}
        for c in calls:
            if c.get("outcome") != 0:
                fails[str(c.get("outcome"))] = fails.get(str(c.get("outcome")), 0) + 1
        by_verb: Dict[str, List[Dict[str, Any]]] = {}
        for c in ok:
            by_verb.setdefault(str(c.get("verb")), []).append(c)
        verbs = []
        for v, cs in sorted(by_verb.items()):
            walls = sorted(c.get("wall_s") or 0.0 for c in cs)
            own = sorted(s for s in ((((cli.get(c["run_id"]) or {}).get("phases") or {}).get("verb") or {})
                                     .get("s") for c in cs) if s is not None)
            verbs.append({"verb": v, "n": len(cs), "p50_s": _quantile(walls, 0.5),
                          "p99_s": _quantile(walls, 0.99), "verb_p50_s": _quantile(own, 0.5),
                          "verb_p99_s": _quantile(own, 0.99)})
        written = [net_written(c.get("io")) for c in mine if c]
        s = r["summary"]
        out.append({"load": r["load"], "lane": r["lane"], "tier": r["tier"], "clients": r["clients"],
                    "ops": len(calls), "window_s": window, "ops_per_s": len(ok) / window,
                    "writes_per_s": writes / window, "failures": fails, "verbs": verbs,
                    "written": sum(w for w in written if w is not None) if written else None,
                    "peak_rss_kb": s.get("peak_rss_kb"), "integrity": s.get("integrity"),
                    "aborted": s.get("aborted")})  # the machine guard's reading, when it fired
    return out


def _target_number(v: Any) -> Optional[float]:  # A target fact's value as a number, or None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _target_check(
    name: str,                # The target's name as reported
    value: Optional[float],   # The reading (None = no reading; inf = the run failed)
    limit: Any,               # The target fact's value (None = not set: no check)
    kind: str,                # "shape" | "absolute" | "load"
) -> Optional[Dict[str, Any]]:  # {target, value, limit, kind, ok (None = unmeasured)}, or None when unset
    lim = _target_number(limit)
    if lim is None:
        return None
    return {"target": name, "value": value, "limit": lim, "kind": kind,
            "ok": None if value is None else value <= lim}


def _target_verdict(checks: List[Dict[str, Any]], gates: bool) -> Dict[str, Any]:  # {status, gate}
    """A seat's status (fail / unmeasured / pass, or no target set) and whether its gate holds:
    every shape check passed and, where its class gates, every absolute one."""
    if not checks:
        return {"status": "no target set", "gate": False}
    status = ("fail" if any(c["ok"] is False for c in checks) else
              "unmeasured" if any(c["ok"] is None for c in checks) else "pass")
    gating = [c for c in checks if c["kind"] != "absolute" or gates]
    return {"status": status, "gate": all(c["ok"] is True for c in gating)}


def target_standing(
    tiers: List[Dict[str, Any]],    # `tier_curves`'s result
    loads: List[Dict[str, Any]],    # `load_report`'s result
    classes: List[Dict[str, Any]],  # `load_targets`'s result: [{key, name, position, targets, members}]
    rebuilds: Optional[List[Dict[str, Any]]] = None,  # `perf_report`'s rebuild rows (the full build's seat)
) -> Dict[str, Any]:  # {seats, loads, counts, ready}
    """Each seat against its class's targets (ruling dba17ca8, design detail 2f5033bf (3)).
    SHAPE targets read the growth exponents across the tiers -- the cost's (the verb's own
    seconds; per answer when the class says so: b of the cost less b of the answer), the peak
    RSS's, the net bytes written's; ABSOLUTE targets read the x32 cell -- the verb's own seconds
    (the launch floor excluded until A), peak RSS, net bytes written; a failed x32 run fails
    them. LOAD targets read the load runs: each member verb's p99 at the most clients over its
    p99 at one, the summed RSS's growth in N, write throughput at the most clients. A target not
    set is not checked; a set one with no reading is UNMEASURED. A seat in no class is NO TARGET
    -- shown, never silently passed (6752db0a (9)). Promotion reads every gate holding.
    Ruling 9af0e42e: a WRITE seat (a run in write mode, or one that appended or replayed journal
    bytes) is held to its x32 write amplification and never to the MB ceiling, which holds the
    read-only seats; the full build (`cg-rebuild`, a seat per lane) to its build phase's bytes
    over the db it produced."""
    load_preds = (P.PERF_LOAD_P99_RATIO, P.PERF_LOAD_RSS_B_MAX, P.PERF_LOAD_WRITES_MIN)
    by_seat: Dict[str, Dict[str, Any]] = {}
    for c in classes:  # a class carrying only load targets names the verbs its load rows read
        if any(v is not None for p, v in c["targets"].items() if p not in load_preds + (P.PERF_GATES,)):
            for m in c["members"]:
                by_seat[m] = c
    seats = []
    for lane in tiers:
        for v in lane["verbs"]:
            name = v["verb"]
            cls = by_seat.get(f"{lane['lane']}/{name}") or by_seat.get(name)
            row: Dict[str, Any] = {"lane": lane["lane"], "seat": name, "class": cls and cls["key"]}
            if cls is None:
                seats.append({**row, "checks": [], "status": "no target", "gate": False})
                continue
            t = cls["targets"]
            g = v.get("growth_verb") or v.get("growth")
            shape = g["b"] if g else None
            if shape is not None and str(t.get(P.PERF_PER_ANSWER, "")).lower() == "true":
                shape = shape - v["growth_answer"]["b"] if v.get("growth_answer") else None
            x32 = next((c for c in v["cells"] if c.get("tier") == 32), None)
            # a failed x32 run fails the absolute targets -- and so does a missing one where a
            # lower tier failed or the lane reached x32 (a driver that died never reports a gesture)
            failed = (x32.get("outcome") != 0 if x32 is not None
                      else any(c.get("outcome") != 0 for c in v["cells"]) or 32 in lane["tiers"])

            def at32(key: str, scale: float = 1.0) -> Optional[float]:
                if failed:
                    return math.inf
                if x32 is None:
                    return None
                val = x32.get(key)
                return None if val is None else val / scale
            writes = any(c.get("read_only") is False or (c.get("logical") or 0) > 0 for c in v["cells"])
            checks = [c for c in (
                _target_check("b", shape, t.get(P.PERF_B_MAX), "shape"),
                _target_check("RSS b", (v.get("growth_rss") or {}).get("b"), t.get(P.PERF_RSS_B_MAX), "shape"),
                _target_check("written b", (v.get("growth_written") or {}).get("b"), t.get(P.PERF_WRITE_B_MAX), "shape"),
                _target_check("x32 s", at32("verb_s"), t.get(P.PERF_X32_S), "absolute"),
                _target_check("x32 RSS MB", at32("peak_rss_kb", 1024.0), t.get(P.PERF_X32_RSS_MB), "absolute"),
                None if writes else _target_check("x32 written MB", at32("written", 1024.0 ** 2),
                                                  t.get(P.PERF_X32_WRITE_MB), "absolute"),
                _target_check("x32 amplification", at32("amplification"), t.get(P.PERF_WRITE_AMP_MAX),
                              "absolute") if writes else None,
            ) if c]
            seats.append({**row, "checks": checks,
                          **_target_verdict(checks, str(t.get(P.PERF_GATES, "")).lower() == "true")})
    for r in rebuilds or []:  # the full build: its build phase's bytes over the db it produced
        row = {"lane": r["lane"], "seat": "cg-rebuild"}
        cls = by_seat.get(f"{r['lane']}/cg-rebuild") or by_seat.get("cg-rebuild")
        if cls is None:
            seats.append({**row, "class": None, "checks": [], "status": "no target", "gate": False})
            continue
        t = cls["targets"]
        amp = r.get("build_amplification") if r.get("outcome") == 0 else math.inf
        checks = [c for c in (
            _target_check("build amplification", amp, t.get(P.PERF_WRITE_AMP_MAX), "absolute"),) if c]
        seats.append({**row, "class": cls["key"], "checks": checks,
                      **_target_verdict(checks, str(t.get(P.PERF_GATES, "")).lower() == "true")})
    load_rows = []
    for cls in classes:
        t = cls["targets"]
        if not any(t.get(p) is not None for p in (P.PERF_LOAD_P99_RATIO, P.PERF_LOAD_RSS_B_MAX,
                                                    P.PERF_LOAD_WRITES_MIN)):
            continue
        groups: Dict[tuple, List[Dict[str, Any]]] = {}
        for r in loads:
            groups.setdefault((str(r["lane"]), r["tier"]), []).append(r)
        if not groups:
            load_rows.append({"class": cls["key"], "lane": None, "tier": None, "checks": [],
                              "status": "unmeasured", "gate": False})
        for (lane, tier), rs in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)):
            rs = sorted(rs, key=lambda r: r["clients"] or 0)
            one = next((r for r in rs if r["clients"] == 1), None)
            most = rs[-1]
            ratios = []
            for v in (most["verbs"] if one is not None and most is not one else []):
                if v["verb"] not in cls["members"]:
                    continue
                base = next((b for b in one["verbs"] if b["verb"] == v["verb"]), None)
                if base and base["p99_s"]:
                    ratios.append(v["p99_s"] / base["p99_s"])
            rss = growth([{"tier": r["clients"], "outcome": 0, "peak_rss_kb": r["peak_rss_kb"]}
                          for r in rs if r["clients"]], "peak_rss_kb")
            checks = [c for c in (
                _target_check("load p99 ratio", max(ratios) if ratios else None, t.get(P.PERF_LOAD_P99_RATIO), "load"),
                _target_check("load RSS b", rss["b"] if rss else None, t.get(P.PERF_LOAD_RSS_B_MAX), "load"),
            ) if c]
            floor = _target_number(t.get(P.PERF_LOAD_WRITES_MIN))
            if floor is not None:  # a floor: the reading must reach it
                checks.append({"target": "load writes/s", "value": most["writes_per_s"], "limit": floor,
                               "kind": "load", "ok": most["writes_per_s"] >= floor})
            load_rows.append({"class": cls["key"], "lane": lane, "tier": tier,
                              "clients": most["clients"], "checks": checks, **_target_verdict(checks, True)})
    counts: Dict[str, int] = {}
    for s in seats:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    return {"seats": seats, "loads": load_rows, "counts": counts,
            "ready": bool(seats) and all(s["gate"] for s in seats)
                     and all(r["gate"] for r in load_rows)}


async def load_targets(
    gx: GraphHandle,  # The open dev graph (the planning graph holds the targets)
) -> List[Dict[str, Any]]:  # [{key, name, position, targets: {predicate: value}, members: [seat]}]
    """The target classes (design detail 2f5033bf (3)): every perf_class Entity and its ACTIVE
    facts, supersession applied -- the classes by kind, then their assertions by subject in one
    batch read (da6cdab6). Ordered by position."""
    from . import factlayer as F
    ents = await F.load_label_where(gx, DevNodeKinds.ENTITY,
                                    [PropertyPredicate("entity_kind", "eq", P.ENTITY_PERF_CLASS)])
    if not ents:
        return []
    ids = [F.nid(e) for e in ents]
    facts = await F.load_label_where(gx, DevNodeKinds.ASSERTION,
                                     [PropertyPredicate("subject_id", "in", ids)])
    supers = await F.load_supersedes(gx)
    active = [a for slot in F.group_by_slot(facts).values() for a in F.active_assertions(slot, supers)]
    out = []
    for e in ents:
        mine = [a for a in active if F.prop(a, "subject_id") == F.nid(e)]
        out.append({"key": F.prop(e, "key"), "name": F.prop(e, "name"),
                    "position": F.prop(e, "position"),
                    "targets": {F.prop(a, "predicate"): F.prop(a, "value") for a in mine
                                if F.prop(a, "predicate") in P.PERF_TARGETS},
                    "members": sorted(str(F.prop(a, "value")) for a in mine
                                      if F.prop(a, "predicate") == P.PERF_MEMBER)})
    return sorted(out, key=lambda c: (c["position"] if isinstance(c["position"], int) else 1 << 30,
                                      str(c["key"])))


def perf_report(
    path: str,                   # The timings ledger
    verb: Optional[str] = None,  # Only this verb (or app:gesture)
    runs: int = 50,              # History kept per verb (the newest N)
    since: Optional[float] = None,  # Only events at or after this unix time
    targets: Optional[List[Dict[str, Any]]] = None,  # `load_targets`'s classes: the standing is read when given
) -> Dict[str, Any]:  # {ledger, events, verbs, gestures, polls, fits, regressions, guards, rebuilds, tiers, tier_regressions, load, standing, unmeasured}
    """The ledger read: per verb the latest / median / p90 END-TO-END seconds (launch + import
    + wall), the latest run's heaviest phases by self time and storage by method; per app
    gesture the same over its wall, with its queue wait, round trips, rows and bytes, and per
    poll its cost per tick (design detail 4389a579); fits of each cost against each covariate;
    regressions; the guards on each verb's and gesture's latest run; the latest rebuild per
    lane; the scale harness's curves per lane (`tier_curves`). Design detail 2f5033bf: each
    verb's and rebuild's disk figures, a seat's regression against its best at a tier, the
    concurrent replay mode's load runs, and -- given the target classes -- every seat's standing."""
    events = [e for e in read_journal(path) if not since or (e.get("ts") or 0) >= since]
    # A scale-harness run (design detail 3bd8673d) carries its tier: its cost belongs to the
    # tier curves, never to a live verb's history, fits, regressions or guards
    tiered = [e for e in events if (e.get("attrs") or {}).get("tier") is not None]
    loaded = [e for e in tiered if (e.get("attrs") or {}).get("load")]  # 2f5033bf (2): apart from the curves
    tiered = [e for e in tiered if not (e.get("attrs") or {}).get("load")]
    events = [e for e in events if (e.get("attrs") or {}).get("tier") is None]
    by_verb: Dict[str, List[Dict[str, Any]]] = {}
    polls: Dict[str, List[Dict[str, Any]]] = {}
    for e in events:
        if verb is not None and e.get("verb") != verb:
            continue
        if e.get("kind") == "gesture" and (e.get("attrs") or {}).get("poll"):
            polls.setdefault(e.get("verb") or "?", []).append(e)
        elif e.get("kind") in ("cli", "gesture"):
            by_verb.setdefault(e.get("verb") or "?", []).append(e)
    verbs, gestures, fits, regressions, found = [], [], [], [], []
    guarded = set()  # (run_id, verb, ts) of events whose guards are listed: each event once
    for name, evs in sorted(by_verb.items()):
        evs = sorted(evs, key=lambda e: e.get("ts") or 0)[-runs:]
        walls = [end_to_end(e) for e in evs]
        latest = evs[-1]
        selfs = sorted(self_times(latest.get("phases") or {}).items(), key=lambda kv: -kv[1])
        q = statistics.quantiles(walls, n=10)[-1] if len(walls) >= 2 else walls[-1]
        row = {"verb": name, "runs": len(evs), "latest_s": walls[-1],
               "median_s": statistics.median(walls), "p90_s": q,
               "top_phases": [{"phase": p, "self_s": s} for p, s in selfs[:4]],
               "storage": {m: {"calls": r["calls"], "s": r["s"]}
                           for m, r in sorted((latest.get("storage") or {}).items(),
                                              key=lambda kv: -kv[1]["s"])[:4]},
               **io_row(latest)}
        if latest.get("kind") == "gesture":
            gestures.append({**row, "outcome": latest.get("outcome"), **gesture_cost(latest)})
        else:
            verbs.append(row)
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
    for name, evs in sorted(polls.items()):
        latest = max(evs, key=lambda e: e.get("ts") or 0)
        found += [{"verb": name, **g} for g in guards(latest)]
    rebuilds: Dict[str, Dict[str, Any]] = {}
    for e in events:
        if e.get("kind") == "rebuild":
            lane = (e.get("attrs") or {}).get("lane", "?")
            if lane not in rebuilds or (e.get("ts") or 0) > (rebuilds[lane].get("ts") or 0):
                rebuilds[lane] = e
    curves, loads = tier_curves(tiered, verb=verb), load_report(loaded)
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
        # 2f5033bf (1): the wrapper's phases carry the bytes of every step beneath them (a reaped
        # child's I/O accrues to the shell); the amplification is over the db the build produced
        written = [net_written(ph) for ph in (e.get("phases") or {}).values()]
        written = sum(w for w in written if w is not None) if any(w is not None for w in written) else None
        db_bytes = max([_covariate(s, "db_bytes") or 0 for s in steps] or [0]) or None
        built = net_written((e.get("phases") or {}).get("build"))  # the full build (9af0e42e (3))
        rebuild_rows.append({"lane": lane, "ts": e.get("ts"), "wall_s": e.get("wall_s"),
                             "outcome": e.get("outcome"), "phases": e.get("phases"),
                             "steps": step_rows, "written": written, "db_bytes": db_bytes,
                             "amplification": written / db_bytes if written and db_bytes else None,
                             "build_written": built,
                             "build_amplification": built / db_bytes if built is not None and db_bytes else None})
    return {"ledger": path, "events": len(events), "verbs": verbs, "gestures": gestures,
            "polls": poll_rows(polls, runs=runs), "fits": fits,
            "regressions": regressions, "guards": found, "rebuilds": rebuild_rows,
            "tiers": curves, "tier_regressions": tier_regressions(tiered), "load": loads,
            "standing": (target_standing(curves, loads, targets, rebuild_rows)
                         if targets is not None else None),
            "unmeasured": ["journal op counts per family (B's journal index)",
                           "Qt-side delivery and paint (the shell's, after its migration; "
                           "design detail 4389a579)",
                           "code writes at scale (the sandbox repo tier, work item 29318333)",
                           "the synthetic replay tier (deferred, design detail 3bd8673d)"]}


def _s(v: Optional[float]) -> str:  # Seconds, compact
    return "-" if v is None else (f"{v:.3f}" if v < 10 else f"{v:.1f}")


def _bytes_text(n: Optional[float]) -> str:  # Bytes, compact
    if n is None:
        return "-"
    for unit, k in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= k:
            return f"{n / k:.1f} {unit}"
    return f"{int(n)} B"


def _tier_cell(c: Optional[Dict[str, Any]]) -> str:  # One tier's cell: seconds, peak RSS, bytes written, or the failure
    if not c:
        return "-"
    if c.get("outcome") != 0:
        return f"**{c.get('outcome')}**"
    rss = c.get("peak_rss_kb")
    cold = c.get("cold") or {}
    return (f"{_s(c.get('wall_s'))} s" + (f" · {rss / 1024 ** 2:.2f} GB" if rss else "")
            + (f" · w {_bytes_text(c['written'])}" if c.get("written") is not None else "")
            + (f" · cold {_s(cold['wall_s'])} s" if cold.get("wall_s") is not None else ""))


def _check_cell(c: Dict[str, Any]) -> str:  # One target check: reading / target, a miss marked
    v = c["value"]
    shown = "-" if v is None else ("failed" if v == math.inf else f"{v:.3g}")
    return f"{c['target']} {shown} / {c['limit']:g}" + (" ✗" if c["ok"] is False else "")


def _growth_text(g: Optional[Dict[str, float]]) -> str:  # A growth exponent, compact
    return f"{g['b']:.2f}" if g else "-"


def render_perf(
    res: Dict[str, Any],  # `perf_report`'s result
    fmt: str = "human",   # "human" | "agent" (JSON)
) -> str:  # The rendered read
    """Render the perf read: tables for a person, the result as JSON for an agent."""
    if fmt == "agent":
        return json.dumps(res, indent=1, default=str)
    out = [f"## Perf — {Path(res['ledger']).name} · {res['events']} event(s)", ""]
    if not (res["verbs"] or res.get("gestures") or res.get("polls") or res["rebuilds"]
            or res.get("tiers") or res.get("load")):
        return "\n".join(out + ["(no events yet -- the wrappers record one per invocation, "
                                "an app one per gesture)"])
    if res["verbs"]:
        out += ["### Verbs (seconds end to end: launch + import + the run)", "",
                "| verb | runs | latest | median | p90 | written | read | appended | x | heaviest phases, latest run (self s) |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for v in res["verbs"]:
        phases = " · ".join(f"{p['phase']} {_s(p['self_s'])}" for p in v["top_phases"])
        amp = f"{v['amplification']:.0f}" if v.get("amplification") is not None else "-"
        out.append(f"| {v['verb']} | {v['runs']} | {_s(v['latest_s'])} | {_s(v['median_s'])} | "
                   f"{_s(v['p90_s'])} | {_bytes_text(v.get('written'))} | {_bytes_text(v.get('read'))} | "
                   f"{_bytes_text(v.get('appended'))} | {amp} | {phases} |")
    if res["verbs"]:
        out += ["", "_written / read = net bytes to / from the disk (the run's process, its worker "
                    "included); appended = journal bytes appended; x = written over the logical size "
                    "(journal bytes appended + replayed)._"]
    if res.get("gestures"):
        out += ["", "### Gestures (seconds in the session's loop, queued + work; latest run's "
                    "round trips = storage calls, rows and wire bytes)", "",
                "| gesture | runs | latest | median | p90 | queued | round trips | rows | bytes | "
                "heaviest phases, latest run (self s) |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
        for g in res["gestures"]:
            phases = " · ".join(f"{p['phase']} {_s(p['self_s'])}" for p in g["top_phases"])
            failed = "" if g.get("outcome") in (None, "ok") else f" **{g['outcome']}**"
            out.append(f"| {g['verb']}{failed} | {g['runs']} | {_s(g['latest_s'])} | "
                       f"{_s(g['median_s'])} | {_s(g['p90_s'])} | {_s(g['queued_s'])} | "
                       f"{g['round_trips']:,.0f} | {g['rows']:,.0f} | {g['bytes']:,.0f} | {phases} |")
    if res.get("polls"):
        out += ["", "### Polls (per tick; one rolling event per window)", "",
                "| poll | windows | ticks | per tick | latest tick | queued | round trips | rows | "
                "bytes | failed |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for p in res["polls"]:
            out.append(f"| {p['gesture']} | {p['windows']} | {p['ticks']} | {_s(p['tick_s'])} | "
                       f"{_s(p['latest_tick_s'])} | {_s(p['queued_s'])} | {p['round_trips']:,.1f} | "
                       f"{p['rows']:,.0f} | {p['bytes']:,.0f} | {p['failed']} |")
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
        phases = " · ".join(f"{k} {_s(v.get('s'))}"
                            + (f" (w {_bytes_text(net_written(v))})" if net_written(v) is not None else "")
                            for k, v in (r.get("phases") or {}).items())
        disk = ""
        if r.get("written") is not None:
            disk = f" · wrote {_bytes_text(r['written'])}" + (f" over a {_bytes_text(r['db_bytes'])} db (x{r['amplification']:.1f})"
                                                    if r.get("amplification") else "")
            if r.get("build_amplification") is not None:
                disk += f" · the build x{r['build_amplification']:.1f}"
        out += ["", f"### Rebuild — {r['lane']} lane · {_s(r['wall_s'])} s · outcome {r['outcome']}{disk}",
                "", f"- phases: {phases}"]
        for s in r["steps"]:
            top = " · ".join(f"{p['phase']} {_s(p['self_s'])}" for p in s["top_phases"])
            out.append(f"- step `{s['step']}` ({s['verb']}) {_s(s['wall_s'])} s — {top}")
    for lane in res.get("tiers") or []:
        ks = lane["tiers"]
        out += ["", f"### Scale tiers — {lane['lane']} (end to end s · peak RSS; growth = b in "
                    "cost ~ size^b over the runs that succeeded)", "",
                "| verb | " + " | ".join(f"x{k}" for k in ks) + " | growth | b verb · RSS · written · answer |",
                "|---|" + "---:|" * len(ks) + "---|---|"]
        for v in lane["verbs"]:
            cells = {c["tier"]: c for c in v["cells"]}
            g = v.get("growth")
            out.append(f"| {v['verb']} | " + " | ".join(_tier_cell(cells.get(k)) for k in ks)
                       + " | " + (f"b {g['b']:.2f} (r² {g['r2']:.2f})" if g else "-") + " | "
                       + " · ".join(_growth_text(v.get(k)) for k in ("growth_verb", "growth_rss",
                                                            "growth_written", "growth_answer")) + " |")
        if lane["builds"]:
            out.append("| _(build)_ | " + " | ".join(
                f"{_s(lane['builds'][k])} s" if k in lane["builds"] else "-" for k in ks) + " | |")
    if res.get("tier_regressions"):
        out += ["", "### Tier regressions (the latest reading against the best at its tier)", ""]
        out += [f"- **{r['lane']} · {r['verb']} · x{r['tier']}** {_s(r['latest_s'])} s vs best "
                f"{_s(r['best_s'])} s (x{r['ratio']:.1f})" for r in res["tier_regressions"]]
    for r in res.get("load") or []:
        fails = ", ".join(f"{k} x{n}" for k, n in sorted(r["failures"].items())) or "none"
        rss = r.get("peak_rss_kb")
        out += ["", f"### Load — {r['lane']} · x{r['tier']} · {r['clients']} client(s) · "
                    f"{r['ops']} ops in {_s(r['window_s'])} s · {r['ops_per_s']:.2f} ops/s · "
                    f"{r['writes_per_s']:.2f} writes/s", "",
                f"- clients' summed peak RSS {_bytes_text(rss * 1024) if rss else '-'} · written "
                f"{_bytes_text(r.get('written'))} · failures {fails} · integrity "
                f"{(r.get('integrity') or {}).get('ok', '-')}"
                + (f" · **ABORTED: {r['aborted']}**" if r.get("aborted") else ""), "",
                "| verb | n | p50 | p99 | verb p50 | verb p99 |", "|---|---:|---:|---:|---:|---:|"]
        out += [f"| {v['verb']} | {v['n']} | {_s(v['p50_s'])} | {_s(v['p99_s'])} | "
                f"{_s(v['verb_p50_s'])} | {_s(v['verb_p99_s'])} |" for v in r["verbs"]]
    st = res.get("standing")
    if st is not None:
        counts = " · ".join(f"{k} {n}" for k, n in sorted(st["counts"].items())) or "no seats"
        out += ["", f"### Targets — {counts} · promotion gate "
                    f"{'HOLDS' if st['ready'] else 'not yet'}", "",
                "| lane | seat | class | status | gate | checks (value / target) |",
                "|---|---|---|---|---|---|"]
        for s in st["seats"]:
            checks = " · ".join(_check_cell(c) for c in s["checks"])
            out.append(f"| {s['lane']} | {s['seat']} | {s['class'] or '-'} | {s['status']} | "
                       f"{'✓' if s['gate'] else '-'} | {checks} |")
        for r in st["loads"]:
            checks = " · ".join(_check_cell(c) for c in r["checks"])
            out.append(f"| {r['lane'] or '-'} | load x{r['tier'] or '-'} | {r['class']} | {r['status']} | "
                       f"{'✓' if r['gate'] else '-'} | {checks} |")
    out += ["", "_Not yet measured: " + "; ".join(res["unmeasured"]) + "._"]
    return "\n".join(out)
