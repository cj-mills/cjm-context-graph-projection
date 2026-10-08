"""The evidence (design 7f315830; build fbf7fc0e): raw snapshots hash-checked, months derived from
days at the finest resolution, traffic landed on web_path nodes with the snapshots as EVIDENCED_BY
edges, a page's share derived through the path ownership, and replay reading no file."""

import asyncio
import datetime as dt
import json
import math
from pathlib import Path

import pytest
from cjm_context_graph_primitives.journal import append_write
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from cjm_context_graph_projection import factlayer as F
from cjm_context_graph_projection.evidence import (cloudflare_measures, evidence_timer, ingest_evidence,
                                                   pending_snapshots, pull_cloudflare, pull_evidence,
                                                   pull_search_console, read_snapshot, search_console_measures,
                                                   snapshot_id, traffic_report, web_path_id, write_snapshot,
                                                   _run_systemd)
from cjm_context_graph_projection.journal import replay_journal
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.write import assert_value

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()
HOST = "example.com"


def _cf_row(day, path, count, visits, interval, bot=0, host=HOST):
    return {"count": count, "sum": {"visits": visits}, "avg": {"sampleInterval": interval},
            "dimensions": {"date": day, "requestHost": host, "requestPath": path, "bot": bot}}


def _cf_env(window, rows, pulled_at):
    return {"shape": "daily-path", "window": window, "pulled_at": pulled_at, "row_count": len(rows),
            "response": {"data": {"viewer": {"accounts": [{"rows": rows}]}}}}


def _cf_snap(key, window, rows, pulled_at):
    return {"key": key, "source": "cloudflare", "pulled_at": pulled_at, "window": window,
            "envelopes": {"daily-path.json": _cf_env(window, rows, pulled_at)}}


def test_a_day_is_read_at_its_finest_resolution_and_a_month_sums_its_days():
    old = _cf_snap("cloudflare/2026-10-01", ["2026-09-01", "2026-09-30"], [
        _cf_row("2026-09-01", "/posts/a/", 20, 10, 10), _cf_row("2026-09-29", "/posts/a/", 30, 30, 10),
        _cf_row("2026-09-29", "/posts/a/", 50, 50, 10, bot=1),                 # a bot: never counted
        _cf_row("2026-09-01", "/posts/a.html", 10, 10, 10, host="evil.test"),  # another host: reported
    ], "2026-10-01T00:00:00+00:00")
    new = _cf_snap("cloudflare/2026-10-06", ["2026-09-29", "2026-10-05"], [
        _cf_row("2026-09-29", "/posts/a/index.html", 27, 25, 1),   # the same day at interval 1 wins
        _cf_row("2026-10-02", "/posts/a.html", 4, 4, 1),
    ], "2026-10-06T00:00:00+00:00")
    got = cloudflare_measures([old, new], HOST)
    assert got["day_source"]["2026-09-29"] == "cloudflare/2026-10-06" and got["day_source"]["2026-09-01"] == "cloudflare/2026-10-01"
    sep = got["measures"][("/posts/a", "2026-09")]
    assert sep["snapshots"] == ["cloudflare/2026-10-01", "cloudflare/2026-10-06"]
    m = sep["measure"]
    assert m["pageloads"]["estimate"] == 47 and m["visits"]["estimate"] == 35 and m["days"] == 2
    var = (10 - 1) * 20 + (1 - 1) * 27                       # (interval - 1) x count: a full-resolution day is exact
    assert m["pageloads"]["upper"] == round(47 + 1.959964 * math.sqrt(var), 1)
    assert m["pageloads"]["sample_size"] == round(20 / 10 + 27 / 1)   # the true sample
    assert m["complete"] is True                             # September covered, ended before the pull
    assert got["measures"][("/posts/a", "2026-10")]["measure"]["complete"] is False
    assert got["foreign"] == {"evil.test": 10}


def test_a_complete_day_outranks_a_partial_reading_at_any_resolution():
    # the pull day read mid-day at interval 1, then read complete a week later, downsampled to 10
    early = _cf_snap("cloudflare/2026-10-08", ["2026-10-01", "2026-10-08"], [
        _cf_row("2026-10-08", "/posts/a/", 50, 40, 1)], "2026-10-08T20:22:00+00:00")
    late = _cf_snap("cloudflare/2026-10-15", ["2026-10-07", "2026-10-15"], [
        _cf_row("2026-10-08", "/posts/a/", 70, 60, 10)], "2026-10-15T20:00:00+00:00")
    got = cloudflare_measures([early, late], HOST)
    assert got["day_source"]["2026-10-08"] == "cloudflare/2026-10-15"
    assert got["measures"][("/posts/a", "2026-10")]["measure"]["pageloads"]["estimate"] == 70
    # inside the settle hour a reading is still partial; among complete readings the finer wins
    settling = _cf_snap("cloudflare/2026-10-09", ["2026-10-07", "2026-10-09"], [
        _cf_row("2026-10-08", "/posts/a/", 60, 50, 1)], "2026-10-09T00:30:00+00:00")
    complete = _cf_snap("cloudflare/2026-10-10", ["2026-10-08", "2026-10-10"], [
        _cf_row("2026-10-08", "/posts/a/", 66, 55, 1.02)], "2026-10-10T08:00:00+00:00")
    got = cloudflare_measures([early, settling, complete, late], HOST)
    assert got["day_source"]["2026-10-08"] == "cloudflare/2026-10-10"


def _sc_snap(key, window, rows, pulled_at):
    return {"key": key, "source": "search-console", "pulled_at": pulled_at, "window": window,
            "envelopes": {"page-date.json": {"shape": "page-date", "window": window, "pulled_at": pulled_at,
                                             "pages": [{"request": {}, "response": {"rows": rows}}]}}}


def test_search_console_takes_each_day_from_the_latest_pull_and_weights_position():
    def r(url, day, clicks, impr, pos):
        return {"keys": [url, day], "clicks": clicks, "impressions": impr, "position": pos}
    a = _sc_snap("search-console/2026-10-03", ["2026-09-01", "2026-10-02"], [
        r(f"https://{HOST}/posts/a/", "2026-10-01", 1, 10, 4.0), r(f"https://{HOST}/posts/a/", "2026-10-02", 0, 10, 8.0),
        r("https://other.test/x/", "2026-10-01", 0, 3, 1.0)], "2026-10-03T00:00:00+00:00")
    b = _sc_snap("search-console/2026-10-08", ["2026-10-02", "2026-10-07"], [
        r(f"https://{HOST}/posts/a/index.html", "2026-10-02", 2, 30, 6.0)], "2026-10-08T00:00:00+00:00")
    got = search_console_measures([a, b], HOST)
    m = got["measures"][("/posts/a", "2026-10")]["measure"]
    assert (m["clicks"], m["impressions"], m["days"]) == (3, 40, 2)           # 10-02 revised by the later pull
    assert m["position"] == round((4.0 * 10 + 6.0 * 30) / 40, 2)
    assert got["foreign"] == {"other.test": 3}


def test_a_snapshot_is_its_hashes(tmp_path):
    write_snapshot(str(tmp_path), "cloudflare", "2026-10-08", {"daily-path-2026-09-01.json": _cf_env(
        ["2026-09-01", "2026-09-30"], [], "2026-10-08T00:00:00+00:00")})
    with pytest.raises(FileExistsError):                     # a pull never overwrites the source it is
        write_snapshot(str(tmp_path), "cloudflare", "2026-10-08", {})
    assert read_snapshot(str(tmp_path), "cloudflare/2026-10-08")["window"] == ["2026-09-01", "2026-09-30"]
    f = tmp_path / "cloudflare" / "2026-10-08" / "daily-path-2026-09-01.json"
    f.write_text(f.read_text().replace("2026-09-30", "2026-09-29"))
    assert "does not match its manifest hash" in read_snapshot(str(tmp_path), "cloudflare/2026-10-08")["error"]
    assert "a snapshot key" in read_snapshot(str(tmp_path), "ga/2026-10-08")["error"]


def test_the_pulls_go_month_by_month_and_never_truncate(tmp_path):
    asked = []

    def cf(query, variables):
        asked.append((variables["s"], variables["e"]))
        return {"data": {"viewer": {"accounts": [{"rows": [_cf_row(variables["s"], "/", 10, 10, 10)]}]}}}
    out = pull_cloudflare({"account": "acct", "site_tag": "tag"}, dt.date(2026, 9, 20), dt.date(2026, 10, 3), ask=cf)
    assert sorted({a for a in asked}) == [("2026-09-20", "2026-09-30"), ("2026-10-01", "2026-10-03")]
    assert len(out) == 6 and all("a" not in e["variables"] for e in out.values())   # the account stays out of the file
    with pytest.raises(RuntimeError, match="reached the limit"):
        pull_cloudflare({"account": "a", "site_tag": "t"}, dt.date(2026, 9, 1), dt.date(2026, 9, 2),
                        ask=lambda q, v: {"data": {"viewer": {"accounts": [{"rows": [{}] * 10000}]}}})
    starts = []

    def sc(body):
        starts.append(body["startRow"])
        return {"rows": [{"keys": ["u", "d"]}] * (25000 if body["startRow"] == 0 else 3)}
    env = pull_search_console({"property": "sc-domain:x"}, dt.date(2026, 10, 1), dt.date(2026, 10, 2), ask=sc)
    assert env["page-date-2026-10-01.json"]["row_count"] == 25003 and starts[:2] == [0, 25000]
    conf = {"root": str(tmp_path), "cloudflare": {"account": "a", "site_tag": "t"}}
    first = pull_evidence(conf, "cloudflare", today=dt.date(2026, 10, 8), ask=cf)
    assert first["window"] == ["2026-04-08", "2026-10-08"]                  # a first pull reaches the retention
    n = len(asked)
    assert pull_evidence(conf, "cloudflare", today=dt.date(2026, 10, 8), ask=cf)["skipped"] == "already pulled today"
    assert len(asked) == n                                                   # skipped without asking the source
    again = pull_evidence(conf, "cloudflare", today=dt.date(2026, 10, 15), ask=cf)
    assert again["window"] == ["2026-10-07", "2026-10-15"]                   # from the last end, the overlap re-read


def test_the_timer_pulls_files_only_and_ingest_reads_what_is_pending(tmp_path):
    root = tmp_path / "evidence"
    for key in ("cloudflare/2026-10-08", "cloudflare/2026-10-12", "search-console/2026-10-12"):
        source, day = key.split("/")
        write_snapshot(str(root), source, day, {"x.json": {"window": [day, day]}})
    (root / "liveness" / "2026-10-08").mkdir(parents=True)                # not a traffic source: never pending
    assert pending_snapshots(str(root), ["cloudflare/2026-10-08"]) == ["cloudflare/2026-10-12", "search-console/2026-10-12"]
    exe = tmp_path / "bin" / "cjm-context-graph"
    exe.parent.mkdir()
    exe.write_text("")
    calls = []

    def run(argv):
        calls.append(argv)
        return 0, ("ActiveState=active\nResult=success" if argv[2:3] == ["show"] else "")
    conf = {"root": str(root), "host": HOST, "schedule": "Mon,Thu *-*-* 09:30:00",
            "cloudflare": {"account": "a"}, "search_console": {"property": "p"}}
    units, db = tmp_path / "units", str(tmp_path / "notes 100%.db")
    kw = {"db_path": db, "exe": str(exe), "unit_dir": str(units), "today": dt.date(2026, 10, 19)}
    res = evidence_timer(conf, "install", run=run, **kw)
    service = (units / "cjm-evidence-pull.service").read_text()
    timer = (units / "cjm-evidence-pull.timer").read_text()
    assert "pull-evidence cloudflare search-console\n" in service and "ingest" not in service   # files only
    assert 'notes 100%%.db"' in service                                    # quoted, the specifier sign doubled
    assert "OnCalendar=Mon,Thu *-*-* 09:30:00" in timer and "Persistent=true" in timer
    assert calls[0][:2] == ["systemd-analyze", "calendar"]                  # the schedule checked before any write
    assert ["systemctl", "--user", "enable", "--now", "cjm-evidence-pull.timer"] in calls
    assert res["installed"] and res["stale"] == [] and res["units"]["cjm-evidence-pull.timer"]["ActiveState"] == "active"
    assert res["latest"]["cloudflare"] == {"key": "cloudflare/2026-10-12", "through": "2026-10-12"}
    assert any("pull now" in w for w in res["warnings"])                   # 7 days since the last covered day
    moved = evidence_timer({**conf, "schedule": "Tue *-*-* 09:30:00"}, "status", run=run, **kw)
    assert moved["stale"] == ["cjm-evidence-pull.timer"]                   # the config moved on: reinstall
    bad = evidence_timer({**conf, "schedule": "fortnightly"}, "install",
                         run=lambda argv: (1, "bad") if argv[1] == "calendar" else (0, ""), **kw)
    assert "not a systemd calendar expression" in bad["error"] and timer == (units / "cjm-evidence-pull.timer").read_text()
    gone = evidence_timer(conf, "remove", run=run, **kw)
    assert not gone["installed"] and not list(units.iterdir())
    import sys
    assert _run_systemd([sys.executable, "-c", "print('ok')"]) == (0, "ok")   # the default runner itself
    assert _run_systemd(["no-such-systemd-tool"])[0] == 127                 # no systemd: reported, never raised


def test_pull_evidence_never_opens_the_graph(tmp_path, monkeypatch, capsys):
    import sys
    from cjm_context_graph_projection import cli, evidence
    (tmp_path / "graph.config.json").write_text(json.dumps({"evidence": {"root": str(tmp_path / "ev"), "host": HOST}}))

    def no_graph(*a, **k):
        raise AssertionError("pull-evidence opened the graph")
    monkeypatch.setattr(cli, "open_graph", no_graph)
    monkeypatch.setattr(evidence, "pull_evidence",
                        lambda conf, source, **k: {"key": f"{source}/2026-10-12", "skipped": "already pulled today"})
    monkeypatch.setattr(sys, "argv", ["cjm-context-graph", "--graph-db-path", str(tmp_path / "g.db"),
                                      "pull-evidence", "cloudflare", "search-console"])
    assert cli.main() == 0
    assert capsys.readouterr().out.count("**skipped**") == 2


def _write_cf(root, pulled, window, rows):
    write_snapshot(str(root), "cloudflare", pulled, {f"daily-path-{window[0]}.json": _cf_env(
        window, rows, f"{pulled}T12:00:00+00:00")})


async def _seed(gx):
    # one page holding /posts/a now and /posts/a-old before (a back-filled prior path)
    await assert_value(gx, "page-a", P.SITE_PATH, "/posts/a/", actor="agent:test")
    await assert_value(gx, "page-a", P.SITE_PATH, "/posts/a-old/", actor="agent:test", superseded_by=["/posts/a/"])


async def _ids(gx):
    nodes, edges = [], []
    for label in (DevNodeKinds.ENTITY, DevNodeKinds.ASSERTION, "FactSlot"):
        nodes += [str(F.nid(n)) for n in await F.load_label(gx, label)]
    for rel in (DevRelations.EVIDENCED_BY, DevRelations.SUPERSEDES, DevRelations.ABOUT):
        edges += await F.load_edge_pairs(gx, rel)
    return sorted(nodes), sorted(edges)


@pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")
def test_ingest_lands_what_changed_and_a_replay_reads_no_file(tmp_path):
    root, jp = tmp_path / "evidence", str(tmp_path / "writes.jsonl")
    conf = {"root": str(root), "host": HOST}
    _write_cf(root, "2026-10-01", ["2026-09-01", "2026-10-01"], [
        _cf_row("2026-09-02", "/posts/a/", 100, 90, 10), _cf_row("2026-09-03", "/posts/a-old.html", 20, 20, 10),
        _cf_row("2026-09-03", "/gone/", 10, 10, 10), _cf_row("2026-10-01", "/posts/a/", 10, 10, 10)])
    _write_cf(root, "2026-10-08", ["2026-10-01", "2026-10-08"], [   # October re-read at full resolution
        _cf_row("2026-10-01", "/posts/a/", 12, 11, 1), _cf_row("2026-10-07", "/posts/a/", 5, 5, 1)])

    async def live():
        async with open_graph(str(tmp_path / "live.db")) as gx:
            await _seed(gx)
            dry = await ingest_evidence(gx, conf, "cloudflare/2026-10-01", dry_run=True)
            assert not dry["written"] and not await F.load_label_where(
                gx, DevNodeKinds.ENTITY, [F.PropertyPredicate("entity_kind", "eq", P.ENTITY_WEB_PATH)])
            one = await ingest_evidence(gx, conf, "cloudflare/2026-10-01", actor="agent:test")
            append_write(jp, "ingest-evidence", {"run": one["run"], "actor": "agent:test"})
            two = await ingest_evidence(gx, conf, "cloudflare/2026-10-08", actor="agent:test")
            append_write(jp, "ingest-evidence", {"run": two["run"], "actor": "agent:test"})
            again = await ingest_evidence(gx, conf, "cloudflare/2026-10-08", actor="agent:test")
            report = await traffic_report(gx)
            return dry, one, two, again, report, await _ids(gx)

    async def replayed():
        async with open_graph(str(tmp_path / "fresh.db")) as gx:
            await _seed(gx)
            await replay_journal(gx, jp)
            return await traffic_report(gx), await _ids(gx)

    dry, one, two, again, report, ids = asyncio.run(live())
    assert dry["changes"] == 4 and one["applied"]["landed"] == 4            # /posts/a x2 months, /posts/a-old, /gone
    assert two["applied"] == {"snapshot": "cloudflare/2026-10-08", "web_paths": 1, "landed": 1, "superseded": 1,
                              "corroborated": 0, "reinstated": 0}         # only October changed
    assert again["changes"] == 0                                           # nothing new, nothing lands
    page = report["holders"][0]
    assert page["paths"] == ["/posts/a", "/posts/a-old"]                   # the old path's traffic is the page's
    assert page["cloudflare"]["visits"]["estimate"] == 90 + 20 + 16 and page["cloudflare"]["incomplete"] == ["2026-10"]
    assert [u["key"] for u in report["unresolved"]] == ["/gone"]            # held by no page: reported, never dropped
    oct_wp = web_path_id("/posts/a")
    assert snapshot_id("cloudflare/2026-10-08") in {t for s, t in ids[1] if s} and oct_wp in ids[0]
    fresh_report, fresh_ids = asyncio.run(replayed())
    assert fresh_ids == ids and fresh_report == report                     # replay reads no file
    (root / "cloudflare" / "2026-10-01" / "manifest.json").unlink()
    assert "no readable manifest" in asyncio.run(_reingest(tmp_path, conf))["error"]


async def _reingest(tmp_path, conf):
    async with open_graph(str(tmp_path / "third.db")) as gx:
        return await ingest_evidence(gx, conf, "cloudflare/2026-10-01")
