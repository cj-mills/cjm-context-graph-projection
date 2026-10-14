"""The scale harness (design detail 3bd8673d): re-keyed copies, tiers, the path guard, the runs.

The tier half is pinned on a synthetic graph (the store's own tables and indexes) with a journal
family of one cold segment and a live tail; the run half drives the real CLI through the harness
against a tier and reads the ledger the way `perf` does."""

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest
from cjm_context_graph_primitives.journal import journal_segments, read_journal, write_segment
from cjm_context_graph_projection.bench import (_client_argv, _integrity, _normalized, bench_config,
                                                BenchRefusal, build, DROP_KEYS, extract_workload,
                                                inside, invert_request, lane_dir, load, rekeyer,
                                                run, status, tree_rss_kb, UUID_RE)
from cjm_context_graph_projection.perf import growth, perf_report, render_perf, tier_curves
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS

A, B, C = ("11111111-1111-5111-8111-111111111111", "22222222-2222-5222-8222-222222222222",
           "33333333-3333-5333-8333-333333333333")

SCHEMA = [
    "CREATE TABLE nodes (id TEXT PRIMARY KEY, label TEXT NOT NULL, properties JSON, "
    "sources JSON, created_at REAL, updated_at REAL)",
    "CREATE TABLE edges (id TEXT PRIMARY KEY, source_id TEXT NOT NULL, target_id TEXT NOT NULL, "
    "relation_type TEXT NOT NULL, properties JSON, created_at REAL, updated_at REAL, "
    "FOREIGN KEY(source_id) REFERENCES nodes(id) ON DELETE CASCADE, "
    "FOREIGN KEY(target_id) REFERENCES nodes(id) ON DELETE CASCADE)",
    "CREATE TABLE ingest_sources (source TEXT PRIMARY KEY, head TEXT NOT NULL)",
    "CREATE INDEX idx_nodes_label_id ON nodes(label, id)",
    "CREATE INDEX idx_edges_source ON edges(source_id)",
    "CREATE INDEX idx_edges_target ON edges(target_id)",
]


def _graph(tmp_path):
    """A small graph whose properties carry ids (a module_id, a statement citing a node) and a
    writes family of one cold compressed segment plus a live tail."""
    src = tmp_path / "live"
    src.mkdir()
    db = src / "g.db"
    con = sqlite3.connect(db)
    for ddl in SCHEMA:
        con.execute(ddl)
    con.executemany("INSERT INTO nodes VALUES (?,?,?,?,?,?)", [
        (A, "CodeModule", json.dumps({"name": "widgets_module"}), "[]", 1.0, 1.0),
        (B, "CodeSymbol", json.dumps({"name": "render_widget", "module_id": A}), "[]", 2.0, 2.0),
        (C, "Decision", json.dumps({"statement": f"see {B} for the widgets"}), "[]", 3.0, 3.0)])
    con.executemany("INSERT INTO edges VALUES (?,?,?,?,?,?,?)", [
        ("e1000000-0000-5000-8000-000000000001", A, B, "CONTAINS", "{}", 1.0, 1.0),
        ("e2000000-0000-5000-8000-000000000002", C, B, "REFERENCES", "{}", 3.0, 3.0),
        ("e3000000-0000-5000-8000-000000000003", C, A, "REFERENCES", "{}", 3.0, 3.0)])
    con.execute("INSERT INTO ingest_sources VALUES ('repo:x', 'abc')")
    con.commit()
    con.close()
    live = src / "g.writes.jsonl"
    write_segment(str(src / "g.writes.0001.jsonl.xz"),
                  json.dumps({"verb": "decide", "args": {"id": C}}) + "\n")
    live.write_text(json.dumps({"verb": "link", "args": {"source_id": C, "target_id": A}}) + "\n"
                    + json.dumps({"verb": "assert", "args": {"subject": B}}) + "\n")
    return db, live


def _lane(db, live, **settings):
    return {"name": "g", "kind": "projection", "db": str(db), "journals": {"writes": str(live)},
            "manifests_dir": DEFAULT_MANIFESTS, "settings": settings}


def _rows(db, sql):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_rekeyer_is_the_identity_for_copy_zero_and_consistent_per_copy():
    assert rekeyer(0)(f"x {A}") == f"x {A}"
    one, two = rekeyer(1), rekeyer(2)
    a1 = one(A)
    assert UUID_RE.fullmatch(a1) and a1 != A
    assert one(f'{{"module_id": "{A}"}}') == f'{{"module_id": "{a1}"}}'  # one map per copy
    assert two(A) not in (A, a1)                                        # copies never collide
    assert one("no ids here") == "no ids here" and one(3.5) == 3.5


def test_the_path_guard_and_a_missing_bench_dir_refuse(tmp_path):
    bench = tmp_path / "bench"
    assert inside(bench / "x2" / "g.db", bench) == (bench / "x2" / "g.db").resolve()
    with pytest.raises(BenchRefusal):
        inside(tmp_path / "elsewhere", bench)
    db, live = _graph(tmp_path)
    with pytest.raises(BenchRefusal, match="bench_dir"):
        bench_config(str(db))
    (db.parent / "graph.config.json").write_text(json.dumps({"bench": {"bench_dir": str(bench)}}))
    assert bench_config(str(db))["tiers"] == [2, 4, 8, 16, 32]
    with pytest.raises(BenchRefusal, match="inside the bench"):  # a tier never copies a tier
        lane_dir({"bench_dir": str(db.parent)}, _lane(db, live))


@pytest.mark.skipif(not Path("/proc/self/statm").exists(), reason="reads /proc (Linux)")
def test_the_tree_rss_counts_a_child_process():
    # The graph worker is the invocation's child: the sample must reach it
    child = subprocess.Popen([sys.executable, "-c",
                              "import time; b = bytearray(64 * 1024 * 1024); time.sleep(5)"])
    try:
        alone = tree_rss_kb(os.getpid())
        deadline = time.time() + 5
        while tree_rss_kb(os.getpid()) - alone < 60 * 1024 and time.time() < deadline:
            time.sleep(0.05)
        assert tree_rss_kb(os.getpid()) - alone >= 60 * 1024
    finally:
        child.kill()
        child.wait()


def test_a_tier_is_k_self_consistent_copies_and_its_journals(tmp_path):
    db, live = _graph(tmp_path)
    bench = tmp_path / "bench"
    lane = _lane(db, live)
    res = build(lane, {"bench_dir": str(bench), "tiers": [2, 4], "workers": 1})
    assert [t["k"] for t in res["tiers"]] == [1, 2, 4] and len(res["copies"]) == 3
    for k in (1, 2, 4):
        tdb = bench / "g" / f"x{k}" / "g.db"
        assert _rows(tdb, "SELECT COUNT(*) FROM nodes") == [(3 * k,)]
        assert _rows(tdb, "SELECT COUNT(*) FROM edges") == [(3 * k,)]
        assert _rows(tdb, "SELECT COUNT(*) FROM ingest_sources") == [(1,)]  # copy 0's alone
        # every edge's ends are nodes of the tier: each copy is a whole graph
        assert _rows(tdb, "SELECT COUNT(*) FROM edges WHERE source_id NOT IN (SELECT id FROM "
                          "nodes) OR target_id NOT IN (SELECT id FROM nodes)") == [(0,)]
        # an id inside a property follows its copy's map
        mods = dict(_rows(tdb, "SELECT id, json_extract(properties, '$.module_id') FROM nodes "
                               "WHERE label = 'CodeSymbol'"))
        assert set(mods.values()) == set(r[0] for r in _rows(tdb, "SELECT id FROM nodes WHERE "
                                                                   "label = 'CodeModule'"))
        names = {r[0] for r in _rows(tdb, "SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {"idx_nodes_label_id", "idx_edges_source", "idx_edges_target"} <= names
        assert _rows(tdb, "SELECT COUNT(*) FROM sqlite_stat1") != [(0,)]
        tier_live = bench / "g" / f"x{k}" / "journals" / "g.writes.jsonl"
        ops = read_journal(str(tier_live))
        assert len(ops) == 3 * k
        cited = {o["args"].get("id") or o["args"].get("source_id") or o["args"].get("subject")
                 for o in ops}
        assert len(cited & {r[0] for r in _rows(tdb, "SELECT id FROM nodes")}) == len(cited)
        cfg = json.loads((bench / "g" / f"x{k}" / "graph.config.json").read_text())
        for key in ("repos_dir", "memory_dir"):
            assert Path(cfg[key]).resolve().is_relative_to(bench.resolve())
        assert cfg["sibling_graphs"] == {}
    # copy 0 is the live graph; a tier's cold segments are links to the staged copies: copy 0's
    # one cold segment, then each further copy's two (its cold segment and the tail, stored cold)
    assert {r[0] for r in _rows(bench / "g" / "x1" / "g.db", "SELECT id FROM nodes")} == {A, B, C}
    cold = [s for s in journal_segments(str(bench / "g" / "x4" / "journals" / "g.writes.jsonl"))
            if s.endswith(".xz")]
    assert len(cold) == 1 + 3 * 2 and all(os.stat(s).st_nlink >= 2 for s in cold)
    # a second build reuses every tier of the same snapshot; status reads them current
    again = build(lane, {"bench_dir": str(bench), "tiers": [2, 4], "workers": 1})
    assert all(t["reused"] for t in again["tiers"]) and not again["copies"]
    st = status(lane, {"bench_dir": str(bench)})
    assert [t["k"] for t in st["tiers"]] == [1, 2, 4] and all(t["current"] for t in st["tiers"])


def test_growth_reads_the_exponent_and_tier_curves_join_the_cli_event():
    linear = [{"tier": k, "wall_s": 0.5 * k, "outcome": 0} for k in (1, 2, 4, 8)]
    assert growth(linear)["b"] == pytest.approx(1.0)
    flat = [{"tier": k, "wall_s": 0.7, "outcome": 0} for k in (1, 2, 4)]
    assert growth(flat)["b"] == pytest.approx(0.0)
    assert growth(linear[:2]) is None                                  # under three tiers
    assert growth([*linear[:2], {"tier": 4, "wall_s": 9, "outcome": "timeout"}]) is None
    events = []
    for k in (1, 2, 4):
        events.append({"verb": "show", "kind": "bench", "run_id": f"r{k}", "ts": k,
                       "wall_s": 0.4 * k, "outcome": 0, "peak_rss_kb": 1000 * k,
                       "attrs": {"lane": "g", "tier": k}})
        events.append({"verb": "show", "kind": "cli", "run_id": f"r{k}", "ts": k, "wall_s": 0.3 * k,
                       "phases": {"verb": {"calls": 1, "s": 0.2 * k}},
                       "covariates": {"nodes": 100 * k}, "attrs": {"lane": "g", "tier": k}})
    events.append({"verb": "bench-build", "kind": "bench", "ts": 9, "wall_s": 3.0,
                   "attrs": {"lane": "g", "tier": 4}})
    (lane,) = tier_curves(events)
    assert lane["tiers"] == [1, 2, 4] and lane["builds"] == {4: 3.0}
    (show,) = lane["verbs"]
    assert [c["nodes"] for c in show["cells"]] == [100, 200, 400]
    assert show["cells"][0]["top_phases"][0]["phase"] == "verb"
    assert show["growth"]["b"] == pytest.approx(1.0)


needs_capability = pytest.mark.skipif(
    not (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists(),
    reason=f"graph capability {DEFAULT_GRAPH_ID!r} not installed at {DEFAULT_MANIFESTS}",
)


@needs_capability
def test_a_run_measures_each_verb_at_each_tier_apart_from_the_live_history(tmp_path):
    # A real graph built through the CLI, two tiers, one read and one write per tier: each run
    # appends the CLI's event (tagged with its tier) and the harness's (wall, peak RSS); the
    # perf read keeps them out of the live verbs and shows them as curves.
    src = tmp_path / "live"
    src.mkdir()
    db, journal = str(src / "g.db"), str(src / "g.writes.jsonl")
    ledger = str(tmp_path / "g.timings.jsonl")
    base = [sys.executable, "-m", "cjm_context_graph_projection.cli", "--graph-db-path", db,
            "--journal-path", journal]
    for s in ("the widgets render lazily", "the widgets cache their layout"):
        assert subprocess.run([*base, "decide", s], capture_output=True).returncode == 0
    assert subprocess.run([*base, "--timings-path", ledger, "--read-only", "state"],
                          capture_output=True).returncode == 0  # one live event
    settings = {"bench_dir": str(tmp_path / "bench"), "tiers": [2], "workers": 1,
                "memory_limit_gb": 16, "timeout_s": 300, "replay_tail": 10}
    # a graph with no edges has no degree hub: the lane names its own (placeholders are DATA)
    hub = _rows(db, "SELECT id FROM nodes WHERE label = 'Decision' ORDER BY id LIMIT 1")[0][0]
    lane = _lane(db, journal, placeholders={"hub": hub}, verbs=[
        {"name": "locate", "argv": ["locate", "widgets"], "read": True},
        {"name": "decide", "argv": ["decide", "bench probe {stamp}"], "capture": "new"},
        {"name": "link", "argv": ["link", "{new}", "REFERENCES", "{hub}"]},
        {"name": "nothing", "argv": ["show", "{no_such_placeholder}"], "read": True}])
    build(lane, settings, ledger=ledger)
    res = run(lane, settings, ledger)
    assert {(r["tier"], r["verb"]) for r in res["runs"]} == {
        (k, v) for k in (1, 2) for v in ("locate", "decide", "link")}
    assert all(r["outcome"] == 0 and r["peak_rss_kb"] for r in res["runs"]), res["runs"]
    assert {s["verb"] for s in res["skipped"]} == {"nothing"}
    # the writes landed in the tier's journal, never the live one
    assert len(read_journal(journal)) == 2
    assert len(read_journal(str(tmp_path / "bench" / "g" / "x2" / "journals" / "g.writes.jsonl"))) == 6
    events = read_journal(ledger)
    cli = [e for e in events if e["kind"] == "cli" and (e.get("attrs") or {}).get("tier")]
    assert {(e["attrs"]["tier"], e["verb"]) for e in cli} == {
        (k, v) for k in (1, 2) for v in ("locate", "decide", "link")}
    rep = perf_report(ledger)
    assert [v["verb"] for v in rep["verbs"]] == ["state"]  # the live history alone
    (curves,) = rep["tiers"]
    assert curves["lane"] == "g" and curves["tiers"] == [1, 2]
    assert {v["verb"] for v in curves["verbs"]} == {"locate", "decide", "link"}
    assert "### Scale tiers — g" in render_perf(rep)


def test_tier_curves_carry_each_gesture_with_its_driver_peak():
    """A canonical gesture is a row of its own (design detail 4389a579): its wall (a poll's per
    tick), the peak RSS of the driver process that ran it, joined by run id."""
    events = []
    for k in (1, 2, 4):
        events.append({"verb": "gestures:app", "kind": "bench", "run_id": f"d{k}", "ts": k,
                       "wall_s": 9.0, "outcome": 0, "peak_rss_kb": 500 * k,
                       "attrs": {"lane": "g", "tier": k}})
        events.append({"verb": "app:node", "kind": "gesture", "run_id": f"d{k}", "ts": k,
                       "wall_s": 0.1 * k, "outcome": "ok", "phases": {"queued": {"calls": 1, "s": 0.0}},
                       "covariates": {"nodes": 100 * k}, "attrs": {"lane": "g", "tier": k}})
        events.append({"verb": "app:feed", "kind": "gesture", "run_id": f"d{k}", "ts": k,
                       "wall_s": 60.0, "outcome": "ok",
                       "phases": {"tick": {"calls": 2, "s": 0.4 * k}},
                       "attrs": {"lane": "g", "tier": k, "poll": True}})
    (lane,) = tier_curves(events)
    rows = {v["verb"]: v for v in lane["verbs"]}
    assert set(rows) == {"gestures:app", "app:node", "app:feed"}
    node = rows["app:node"]
    assert [c["peak_rss_kb"] for c in node["cells"]] == [500, 1000, 2000]
    assert [c["nodes"] for c in node["cells"]] == [100, 200, 400]
    assert node["growth"]["b"] == pytest.approx(1.0)
    assert [c["wall_s"] for c in rows["app:feed"]["cells"]] == pytest.approx([0.2, 0.4, 0.8])


@pytest.mark.skipif(importlib.util.find_spec("cjm_substrate_qt_kit") is None,
                    reason="the gesture runner lives in cjm-substrate-qt-kit, not a projection dependency")
def test_a_driver_spec_runs_an_apps_gestures_at_each_tier(tmp_path, monkeypatch):
    """A spec naming a driver runs the kit's gesture runner (design detail 4389a579): each
    gesture lands in the lane's ledger tagged with its tier and joined to the harness's event;
    a missing env refuses loudly."""
    db, live = _graph(tmp_path)
    drivers = tmp_path / "drivers"
    drivers.mkdir()
    (drivers / "probe_driver.py").write_text(DRIVER)
    monkeypatch.setenv("PYTHONPATH", str(drivers))
    ledger = str(tmp_path / "g.timings.jsonl")
    settings = {"bench_dir": str(tmp_path / "bench"), "tiers": [2], "workers": 1,
                "memory_limit_gb": 16, "timeout_s": 300, "replay_tail": 10}
    lane = _lane(db, live, placeholders={"hub": A}, verbs=[
        {"name": "gestures:probe", "driver": "probe_driver:drive", "params": {"hub": "{hub}"}},
        {"name": "gestures:none", "driver": "probe_driver:drive", "params": {"hub": "{nope}"}}])
    build(lane, settings, ledger=ledger)
    res = run(lane, settings, ledger)
    assert [(r["tier"], r["verb"], r["outcome"]) for r in res["runs"]] == [
        (1, "gestures:probe", 0), (2, "gestures:probe", 0)], res
    assert {s["verb"] for s in res["skipped"]} == {"gestures:none"}
    events = read_journal(ledger)
    gest = [e for e in events if e["kind"] == "gesture"]
    assert {(e["attrs"]["tier"], e["verb"]) for e in gest} == {
        (k, v) for k in (1, 2) for v in ("probe:open", "probe:node", "probe:close")}
    harness = {e["run_id"] for e in events if e["kind"] == "bench" and e.get("driver")}
    assert {e["run_id"] for e in gest} == harness
    assert all(e["covariates"]["nodes"] == 3 for e in gest)
    bad = {**lane, "settings": {**lane["settings"], "verbs": [
        {"name": "x", "driver": "probe_driver:drive", "env": "no-such-env-anywhere",
         "params": {"hub": "{hub}"}}]}}
    with pytest.raises(BenchRefusal):
        run(bad, settings, ledger)


def test_a_request_inverts_by_the_verbs_own_parser():
    """Design detail 2f5033bf (2): positionals in order, options by flag, a JSON-dumped list
    decoded, a write op's envelope dropped and reported, a missing positional refused."""
    from cjm_context_graph_projection.cli import build_parser
    p = build_parser()
    inv = invert_request(p, "link", {"source_id": A, "relation": "REFINES", "target_id": B,
                                     "source_label": "X", "actor": "agent:session"}, DROP_KEYS)
    assert inv["argv"] == ["link", A, "REFINES", B] and inv["dropped"] == ["actor", "source_label"]
    assert invert_request(p, "decide", {"statement": "S", "title": "T", "supersedes": None})["argv"] == [
        "decide", "S", "--title", "T"]
    assert invert_request(p, "read", {"node_id": _normalized(f'["{A}", "{B}"]')})["argv"] == ["read", A, B]
    assert invert_request(p, "journal-window", {"session": "s1"})["argv"] == ["journal-window", "--session", "s1"]
    assert invert_request(p, "show", {})["argv"] is None
    assert invert_request(p, "no-such-verb", {"x": 1})["argv"] is None
    assert _normalized("[not json") == "[not json"


def test_a_client_rekeys_marks_and_follows_its_own_mints():
    """Design detail 2f5033bf (2): a client's ids re-keyed into its copy, its free text marked,
    and a later step naming the trace's decision swapped for the one this client minted."""
    from cjm_context_graph_projection.cli import build_parser
    from cjm_dev_graph_schema.nodes import DecisionNode
    p = build_parser()
    orig = DecisionNode(statement="probe").id
    decide = {"kind": "write", "verb": "decide", "request": {"statement": "probe"},
              "drop": ["actor", "session"], "mints": orig}
    link = {"kind": "write", "verb": "link", "drop": ["actor", "session"],
            "request": {"source_id": orig, "relation": "REFERENCES", "target_id": A}}
    rekey, minted, marker = rekeyer(2), {}, "[bench load L client 2]"
    made = _client_argv(p, decide, rekey, minted, marker)
    assert made["argv"] == ["decide", f"probe {marker}"]
    assert made["mints"] == DecisionNode(statement=f"probe {marker}").id
    assert _client_argv(p, link, rekey, minted, marker)["argv"] == [
        "link", made["mints"], "REFERENCES", rekey(A)]


def test_load_integrity_finds_torn_lines_lost_and_duplicated_ops(tmp_path):
    """Design detail 2f5033bf (2): every line parses, each marked decide appears once in the
    journal with its node in the db, no op of the load twice -- else the check fails."""
    db = tmp_path / "g.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE nodes (id TEXT PRIMARY KEY)")
    con.execute("INSERT INTO nodes VALUES ('d1')")
    con.commit()
    con.close()
    live = tmp_path / "g.writes.jsonl"
    lines = [json.dumps({"verb": "decide", "args": {"statement": "s [bench load L client 0]"}}),
             json.dumps({"verb": "link", "args": {"source_id": "d1", "note": "[bench load L client 0]"}})]
    live.write_text("\n".join(lines) + "\n")
    tm = {"db": str(db), "journals": {"writes": {"live": str(live), "cold": []}}}
    ok = _integrity(tm, "L", {0: ["d1"]})
    assert ok["ok"] and (ok["decides"], ok["in_db"], ok["duplicated"], ok["bad_lines"]) == (1, 1, 0, 0)
    live.write_text("\n".join(lines + [lines[1], '{"verb": "deci']) + "\n")
    bad = _integrity(tm, "L", {0: ["d1", "d2"]})
    assert not bad["ok"] and (bad["bad_lines"], bad["duplicated"], bad["in_db"]) == (1, 1, 1)


@needs_capability
def test_a_recorded_session_replays_as_a_concurrent_load(tmp_path):
    """Design detail 2f5033bf (2), end to end: a session recorded through the CLI becomes a
    workload; two clients replay it at two tiers as real invocations, their writes marked and
    landing in the tier; the integrity check holds and perf reads each load run."""
    src = tmp_path / "live"
    src.mkdir()
    db, journal = str(src / "g.db"), str(src / "g.writes.jsonl")
    ledger = str(tmp_path / "g.timings.jsonl")
    base = [sys.executable, "-m", "cjm_context_graph_projection.cli", "--graph-db-path", db,
            "--journal-path", journal]
    env = {**os.environ, "CJM_SESSION": "s1"}
    for s in ("the widgets render lazily", "the widgets cache their layout"):
        assert subprocess.run([*base, "decide", s], capture_output=True, env=env).returncode == 0
    (src / "g.reads.jsonl").write_text(json.dumps(
        {"verb": "locate", "request": {"term": "widgets"}, "session": "s1", "ts": time.time()}) + "\n")
    settings = {"bench_dir": str(tmp_path / "bench"), "tiers": [2], "workers": 1,
                "memory_limit_gb": 16, "timeout_s": 300, "replay_tail": 10}
    lane = _lane(db, journal)
    build(lane, settings, ledger=ledger)
    w = extract_workload(lane, settings, "s1")
    assert (w["reads"], w["writes"]) == (1, 2) and all(s.get("mints") for s in w["steps"] if s["verb"] == "decide")
    res = load(lane, settings, ledger, tiers=[1, 2], clients=[2])
    assert [(r["tier"], r["clients"], r["steps"], r["failed"]) for r in res["loads"]] == [(1, 2, 6, 0), (2, 2, 6, 0)]
    assert all(r["integrity"]["ok"] and r["integrity"]["decides"] == 4 for r in res["loads"]), res["loads"]
    assert len(read_journal(journal)) == 2  # the live journal untouched
    rep = perf_report(ledger)
    assert sorted((r["tier"], r["clients"]) for r in rep["load"]) == [(1, 2), (2, 2)]
    assert not rep["tiers"] or all(v["verb"] != "decide" for t in rep["tiers"] for v in t["verbs"])
    assert "### Load — g" in render_perf(rep)


# The gesture leg's fixture driver (design detail 4389a579): a session that measures one gesture
DRIVER = '''
from cjm_substrate_qt_kit.loopthread import LoopThreadSession


class Session(LoopThreadSession):
    app = "probe"

    def __init__(self, db):
        super().__init__(timeout=30.0)
        self.db = db

    async def on_open(self):
        await self.measure_into(self.db)

    async def measure_covariates(self):
        return {"nodes": 3}

    async def node(self, ref):
        return ref


def drive(params):
    s = Session(params["db"])
    s.start()
    try:
        s.call(s.node(params["hub"]))
    finally:
        s.close()
    assert params["journals"]["writes"].endswith("g.writes.jsonl"), params
    return ["node"]
'''
