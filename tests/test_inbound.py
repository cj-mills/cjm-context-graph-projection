"""The inbound links (design 7f315830 (6), ruling a3c02fb1): the drill-down reader, the polite fetch,
the hand exports, and a live ingest whose replay reads no file, asks no site and opens no browser."""

import asyncio
import gzip
import io
import json
import os
from pathlib import Path

import pytest
from cjm_context_graph_primitives.journal import append_write
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import reference_node_id
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from cjm_context_graph_projection import factlayer as F
from cjm_context_graph_projection import inbound as I
from cjm_context_graph_projection.evidence import (ingest_evidence, ingested_snapshots, pending_snapshots,
                                                   read_snapshot, web_path_id, write_snapshot)
from cjm_context_graph_projection.journal import replay_journal
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.write import assert_value

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()
HOST = "example.com"
GLYPHS = ""   # the drill-down's row icons, read as private-use glyphs


def test_a_linking_page_keeps_its_query_and_a_site_url_keys_its_web_path():
    # a3c02fb1 (2): scheme and host lowercased, fragment dropped, query kept (the ?tl= variants are pages)
    assert I.normalize_url(f" HTTPS://AE.LinkedIn.com/pulse/x?tl=ar#top{GLYPHS} ") == "https://ae.linkedin.com/pulse/x?tl=ar"
    assert I.normalize_url("https://Example.com") == "https://example.com/"
    assert I.site_key("https://www.example.com/posts/a/index.html#s", HOST) == "/posts/a"
    assert I.site_key("https://example.com", HOST) == "/" and I.site_key("https://other.com/posts/a/", HOST) is None


class FakeBrowser:
    """Serves the drill-down tables a test names; `late` reads return a pending page first."""

    def __init__(self, pages, signin=False, late=1):
        self.pages, self.signin, self.late, self.url, self.reads = pages, signin, late, None, 0

    def goto(self, url):
        self.url, self.reads = url, 0

    def js(self, expr):
        if self.signin:
            return {"signin": "https://accounts.google.com/x"} if "innerText" in expr else "accounts.google.com"
        self.reads += 1
        if self.reads <= self.late:
            return {"pending": True}
        head, rows, total = self.pages[self.url]
        return {"head": head, "rows": rows, "pager": str(len(rows)), "total": str(total), "html": "<table/>",
                "url": self.url}

    def close(self):
        pass


def _drill(target="", site=""):
    return I._drill_url("sc-domain:example.com", target, site)


A, B = "https://example.com/posts/a/", "https://example.com/posts/b.html"
DRILL = {
    _drill(): (["Target page", "Incoming links", "Linking sites"], [[A, "3", "2"], [B, "1", "1"]], 4),
    _drill(A): (["Site", "Links"], [["devtalk.com", "2"], ["github.com", "1"]], 3),
    _drill(B): (["Site", "Links"], [["linkedin.com", "2"]], 1),   # Google says 1 link from 1 site: a mismatch
    _drill(A, "devtalk.com"): (["Linking page", "Target URL (if different)"],
                               [[f"https://devtalk.com/t/one/1{GLYPHS}", f"N/A{GLYPHS}"], ["https://devtalk.com/t/two/2", "N/A"]], 2),
    _drill(A, "github.com"): (["Linking page", "Target URL (if different)"],
                              [["https://github.com/cj-mills/repo", "https://example.com/posts/a"]], 1),
    _drill(B, "linkedin.com"): (["Linking page", "Target URL (if different)"],
                                [["https://ae.linkedin.com/pulse/x?tl=ar", "N/A"]], 1),
}
CONF_SC = {"property": "sc-domain:example.com"}


def test_a_drilldown_is_read_once_its_table_is_stable_and_a_signed_out_profile_refuses():
    b = FakeBrowser(DRILL, late=2)
    got = I.read_drilldown(b, _drill(), settle=5, poll=0)
    assert got["rows"] == DRILL[_drill()][1] and got["pager"] == "2" and b.reads >= 4   # pending twice, then two equal reads
    out = I.read_drilldown(FakeBrowser(DRILL, signin=True), _drill(), settle=5, poll=0)
    assert "not signed in" in out["error"] and "--headed" in out["error"]


def test_the_drilldown_pull_walks_targets_sites_and_pages():
    envs = I.pull_drilldowns({"search_console": CONF_SC}, browser=FakeBrowser(DRILL, late=0), settle=1)
    assert sorted(envs) == ["pages-000-000.json", "pages-000-001.json", "pages-001-000.json", "sites-000.json",
                            "sites-001.json", "targets.json"]
    p = envs["pages-000-000.json"]
    assert (p["shape"], p["target"], p["site"], p["row_count"], p["pager_total"], p["total"]) == \
        ("pages", A, "devtalk.com", 2, 2, 2)


def _pdf_linking(url):
    from pypdf import PdfWriter
    from pypdf.annotations import Link
    w = PdfWriter()
    w.add_blank_page(200, 200)
    w.add_annotation(0, Link(rect=(10, 10, 100, 30), url=url))
    w.add_metadata({"/Title": "A paper"})
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


SITES = {
    "https://devtalk.com/robots.txt": {"status": 200, "body": b"User-agent: *\nAllow: /\n"},
    "https://devtalk.com/t/one/1": {"status": 200, "content_type": "text/html", "body":
        b'<html><head><title>One</title></head><body><p>See <a href="https://example.com/posts/a/">Arc part 2</a>'
        b' and <a href="/local">here</a> and <a href="https://www.example.com/posts/a/index.html#x">again</a></p></body></html>'},
    "https://devtalk.com/t/two/2": {"status": 404, "body": None},
    "https://github.com/robots.txt": {"status": 200, "body": b"User-agent: *\nDisallow: /\n"},
    "https://ae.linkedin.com/robots.txt": {"status": 404, "body": None},
    "https://ae.linkedin.com/pulse/x?tl=ar": {"status": 999, "body": None},
    "https://arxiv.org/robots.txt": {"status": 200, "body": b""},
    "https://arxiv.org/pdf/1": {"status": 200, "content_type": "application/pdf", "body": None},   # set below
}


def _get(url, ua):
    assert "example.com" in ua   # the user agent names the site
    r = dict(SITES.get(url) or {"status": None, "body": None, "reason": "no route"})
    if url == "https://arxiv.org/pdf/1":
        r["body"] = _pdf_linking("https://example.com/posts/b.html")
    return {"final_url": url, "content_type": "", **r}


def test_the_fetch_honors_robots_keeps_every_body_and_names_each_outcome(tmp_path):
    urls = ["https://devtalk.com/t/one/1", "https://devtalk.com/t/two/2", "https://github.com/cj-mills/repo",
            "https://ae.linkedin.com/pulse/x?tl=ar", "https://arxiv.org/pdf/1"]
    asked = []
    envs, blobs = I.pull_fetch({"host": HOST}, urls, get=lambda u, ua: asked.append(u) or _get(u, ua), spacing=0)
    assert "https://github.com/cj-mills/repo" not in asked   # robots.txt disallows it: never asked
    pages = {e["url"]: e for e in envs.values() if e["shape"] == "page"}
    assert pages["https://github.com/cj-mills/repo"]["robots"] == "disallowed"
    assert sorted(e["host"] for e in envs.values() if e["shape"] == "robots") == \
        ["ae.linkedin.com", "arxiv.org", "devtalk.com", "github.com"]   # every robots.txt kept
    assert [I.fetch_outcome(pages[u])[0] for u in urls] == ["ok", "gone", "refused", "refused", "ok"]
    body = gzip.decompress(blobs[pages[urls[0]]["body"]])
    assert b"Arc part 2" in body and pages[urls[0]]["body_sha256"]


def _csv(rows):
    return "﻿" + "\n".join(",".join(r) for r in rows) + "\n"


def test_an_export_is_moved_into_a_dated_snapshot_byte_for_byte(tmp_path):
    root, dl = tmp_path / "evidence", tmp_path / "downloads"
    dl.mkdir()
    f = dl / "example.com-Latest links-2026-10-08.csv"
    f.write_text(_csv([["Linking page", "Last crawled"], ["https://devtalk.com/t/one/1", "2026-09-23"]]))
    os.utime(f, (1791500000, 1791500000))
    conf = {"root": str(root), "host": HOST}
    res = I.import_export(conf, "search-console-export", [str(f)], date="2026-10-08")
    assert res["key"] == "search-console-export/2026-10-08" and not f.exists()
    snap = read_snapshot(str(root), res["key"])
    assert "error" not in snap and list(snap["blobs"]) == [f.name] and snap["envelopes"]["export.json"]["files"][0]["file"] == f.name
    f.write_text("again")
    assert "already exists" in I.import_export(conf, "search-console-export", [str(f)], date="2026-10-08")["error"]
    assert "export source" in I.import_export(conf, "cloudflare", [str(f)])["error"]


def _write_all(root):
    """An export, a drill-down pull and a fetch pull, as the verbs write them."""
    conf = {"root": str(root), "host": HOST, "search_console": CONF_SC}
    dl = root.parent / "dl"
    dl.mkdir(exist_ok=True)
    (dl / "Latest links.csv").write_text(_csv([["Linking page", "Last crawled"], ["https://devtalk.com/t/one/1", "2026-09-23"],
                                                ["https://old.blog/post", "2025-01-02"]]))
    (dl / "Top target pages.csv").write_text(_csv([["Target page", "Incoming links", "Linking sites"], [A, "3", "2"], [B, "1", "1"]]))
    assert "key" in I.import_export(conf, "search-console-export", [str(dl / "Latest links.csv"), str(dl / "Top target pages.csv")],
                                    date="2026-10-08")
    envs = I.pull_drilldowns({"search_console": CONF_SC}, browser=FakeBrowser(DRILL, late=0), settle=1)
    write_snapshot(str(root), "search-console-links", "2026-10-08", envs)
    urls = I.fetch_list(str(root))
    assert "https://old.blog/post" in urls and "https://ae.linkedin.com/pulse/x?tl=ar" in urls
    fenvs, blobs = I.pull_fetch(conf, [u for u in urls if u != "https://old.blog/post"] + ["https://arxiv.org/pdf/1"],
                                get=_get, spacing=0)
    write_snapshot(str(root), "links-fetch", "2026-10-09", fenvs, blobs=blobs)
    return conf


async def _seed(gx):
    await assert_value(gx, "page-a", P.SITE_PATH, "/posts/a/", actor="agent:test")
    await assert_value(gx, "page-b", P.SITE_PATH, "/posts/b.html", actor="agent:test")


async def _ids(gx):
    nodes, edges = [], []
    for label in (DevNodeKinds.ENTITY, DevNodeKinds.ASSERTION, "FactSlot", DevNodeKinds.REFERENCE):
        nodes += [(str(F.nid(n)), json.dumps({k: v for k, v in F.props(n).items() if k != "asserted_at"},
                                             sort_keys=True, default=str))   # the seed's clock is the live one
                  for n in await F.load_label(gx, label)]
    for rel in (DevRelations.EVIDENCED_BY, DevRelations.ABOUT):
        edges += await F.load_edge_pairs(gx, rel)
    inbound = sorted((e["id"], json.dumps(e["properties"], sort_keys=True)) for e in await I._inbound_edges(gx))
    return sorted(nodes), sorted(edges), inbound


@pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")
def test_links_ingest_lands_two_observers_and_a_replay_reads_no_file(tmp_path):
    root, jp = tmp_path / "evidence", str(tmp_path / "writes.jsonl")
    conf = _write_all(root)

    async def live():
        async with open_graph(str(tmp_path / "live.db")) as gx:
            await _seed(gx)
            pending = pending_snapshots(conf["root"], await ingested_snapshots(gx))
            assert pending == ["search-console-export/2026-10-08", "search-console-links/2026-10-08", "links-fetch/2026-10-09"]
            results = []
            for key in pending:
                res = await ingest_evidence(gx, conf, key, actor="agent:test")
                assert res["written"], res
                append_write(jp, "ingest-evidence", {"run": res["run"], "actor": "agent:test"})
                results.append(res)
            again = await ingest_evidence(gx, conf, "search-console-links/2026-10-08", actor="agent:test")
            return results, again, await I.inbound_report(gx), await _ids(gx)

    async def replayed():
        async with open_graph(str(tmp_path / "fresh.db")) as gx:
            await _seed(gx)
            await replay_journal(gx, jp)
            return await I.inbound_report(gx), await _ids(gx)

    (export, drill, fetch), again, report, ids = asyncio.run(live())
    assert export["report"] == {"listed": 2, "targets": 2, "unkeyed": []}
    assert drill["report"]["mismatches"] == [   # the fixture's B is inconsistent twice over
        {"target": B, "site": "linkedin.com", "total": 1, "site_links": 2},
        {"target": B, "links": 1, "sites_summed": 2, "sites": 1, "sites_listed": 1}]
    assert fetch["report"]["outcomes"] == {"gone": 1, "ok": 2, "refused": 2}
    assert again["applied"]["nodes_added"] == 0 and again["applied"]["edges_added"] == 0   # a re-ingest lands nothing
    by_path = {h["path"]: h for h in report["holders"]}
    a = by_path["/posts/a/"]
    assert a["google_links"] == 3 and a["sites"] == ["devtalk.com", "github.com"] and a["verified"] == 1
    one = next(p for p in a["pages"] if p["url"] == "https://devtalk.com/t/one/1")
    assert one["google"] == "2026-10-08" and one["fetched"] == "2026-10-09" and one["anchors"] == ["Arc part 2", "again"]
    gh = next(p for p in a["pages"] if p["site"] == "github.com")
    assert gh["google"] and gh["fetch"]["outcome"] == "refused" and "robots" in gh["fetch"]["reason"]   # Google's edge stays
    b = by_path["/posts/b.html"]
    assert {p["url"] for p in b["pages"]} == {"https://ae.linkedin.com/pulse/x?tl=ar", "https://arxiv.org/pdf/1"}
    assert [u["url"] for u in report["untargeted"]] == ["https://old.blog/post"]   # listed only: kept, never dropped
    ref = reference_node_id("web", "https://arxiv.org/pdf/1")
    assert any(n == ref and '"title": "A paper @ web"' in p for n, p in ids[0])   # the fetch observed the page
    fresh_report, fresh_ids = asyncio.run(replayed())
    assert fresh_ids == ids and fresh_report == report   # no file, no site, no browser


@pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")
def test_a_refetch_refreshes_the_reference_and_a_later_listing_never_blanks_it(tmp_path):
    root = tmp_path / "evidence"
    conf = {"root": str(root), "host": HOST}
    page = "https://devtalk.com/t/one/1"

    def fetch(day, html):
        envs, blobs = I.pull_fetch(conf, [page], spacing=0, get=lambda u, ua: (
            {"status": 200, "body": b"", "final_url": u, "content_type": ""} if u.endswith("robots.txt")
            else {"status": 200, "body": html, "final_url": u, "content_type": "text/html"}))
        write_snapshot(str(root), "links-fetch", day, envs, blobs=blobs)

    fetch("2026-10-09", b'<title>One</title><a href="https://example.com/posts/a/">x</a>')
    fetch("2026-11-09", b'<title>One, edited</title><p>the link is gone</p>')

    async def run():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            await _seed(gx)
            for key in ("links-fetch/2026-10-09", "links-fetch/2026-11-09"):
                await ingest_evidence(gx, conf, key, actor="agent:test")
            node = (await F.load_nodes(gx, [reference_node_id("web", page)]))[reference_node_id("web", page)]
            return node, await I.inbound_report(gx)

    node, report = asyncio.run(run())
    assert F.prop(node, "title") == "One, edited @ web"
    page_a = report["holders"][0]["pages"][0]
    assert page_a["fetched"] == "2026-10-09" and page_a["fetch"]["date"] == "2026-11-09" and page_a["fetch"]["targets"] == 0


def test_a_drilldown_total_reads_past_its_label_icon_and_help_text():
    # the kept region as Search Console renders it (2026-10-08): label, icon glyph, help sentence, figure
    html = ('<div><span>Linking site</span><span>reddit.com</span><div>Total links<i></i>'
            '<div role="tooltip">The number of unique links from this root domain to your page.</div></div>'
            '<div>1,029</div><table><tr><td>https://www.reddit.com/r/x/</td></tr></table></div>')
    assert I.html_total(html) == 1029 and I.html_total("<table></table>") is None


def test_a_page_that_cannot_be_asked_is_an_observation_never_a_crash():
    # 2026-10-08: a search page's URL carried a raw space; http.client refused it and the whole pull died
    def get(url, ua):
        if url.endswith("robots.txt"):
            return {"status": 404, "body": None, "final_url": url, "content_type": ""}
        raise RuntimeError("boom")
    envs, _ = I.pull_fetch({"host": HOST}, ["https://a.test/x", "https://b.test/y"], get=get, spacing=0)
    pages = [e for e in envs.values() if e["shape"] == "page"]
    assert len(pages) == 2 and all(I.fetch_outcome(e) == ("error", "RuntimeError: boom") for e in pages)
    got = I._get("http://127.0.0.1:9/search?text=Christian Mills", "ua")   # quoted for the wire; refused port
    assert got["status"] is None and got["reason"] and got["final_url"].endswith("Christian Mills")
