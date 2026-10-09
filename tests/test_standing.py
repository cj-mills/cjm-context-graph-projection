"""A page's standing (design amendment cbd5f154, amended by 6514869f): currency is the ruling
(current / archived / removed), an archive post without one is unruled, superseded is DERIVED from
a public successor's SUPERSEDES, a leaving page's destination is its successor, else its repo copy,
else the hub; the claims count current support only -- withdrawn from archived, removed and retired
pages, passed on by superseded ones; the relate verb lands the destination relations; a rebuild
reproduces the live graph."""

import asyncio
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from cjm_context_graph_layer.ops import extend_graph
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import note_node_id, reference_node_id
from cjm_dev_graph_schema.nodes import ReferenceNode
from cjm_markdown_decompose_core.extract import note_from_text
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from cjm_context_graph_projection.claims import project_claims, public_view
from cjm_context_graph_projection.paths import record_relation
from cjm_context_graph_projection.purenotes import mint_deliverable_type
from cjm_context_graph_projection.render import render
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.standing import load_standing, project_standing, support_standing
from cjm_context_graph_projection.write import assert_value
from conftest import commit_all

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()
pytestmark_graph = pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")


def _n(slug, origin="archive", kind="tutorial"):
    return {"slug": slug, "title": slug.upper(), "origin": origin, "kind": kind}


def test_the_standing_rules():
    notes = {i: _n(i) for i in ("cur", "arch", "gone", "moved", "old", "waits", "unruled", "both", "zombie",
                                "stale", "stray")}
    notes.update({"new": _n("new", "born", "tutorial"), "draft": _n("draft", "born", "tutorial"),
                  "404": _n("404", kind="site"), "about": _n("about", kind="site")})
    currency = {"cur": ["current"], "arch": ["archived"], "gone": ["removed"], "moved": ["removed"],
                "old": ["removed"], "waits": ["current"], "both": ["current", "archived"],
                "zombie": ["current"], "stale": ["current"], "stray": ["archived"]}
    states = {"moved": ["retired"], "old": ["retired"], "zombie": ["retired"], "new": ["published"],
              "draft": ["draft"], "about": ["retired"]}
    public = {i for i in notes if i not in ("moved", "old", "zombie", "draft", "about")}
    sup = [("new", "old"), ("draft", "waits"), ("new", "stale"), ("lens", "about"), ("x", "y")]
    res = project_standing(notes, currency, states, public, sup,
                           {"moved": ["https://github.com/cj-mills/repo"], "stray": ["https://x/y"]},
                           {"lens": {"slug": "/about", "title": "About"}})
    by = res["by_id"]
    assert [by[i]["standing"] for i in ("cur", "arch", "gone", "moved", "unruled", "both")] == [
        "current", "archived", "removed", "retired", "unruled", "conflict"]
    # superseded is derived: a public successor supersedes; a draft successor leaves the page standing
    assert by["old"]["superseded"] and [s["slug"] for s in by["old"]["superseded_by"]] == ["new"]
    assert not by["waits"]["superseded"] and [s["slug"] for s in by["waits"]["successor_waiting"]] == ["draft"]
    assert [t["slug"] for t in by["new"]["supersedes"]] == ["old", "stale"] and by["new"]["standing"] == ""
    # destinations: successor, else the repo copy, else the hub; a removed page not yet retired is pending
    assert by["old"]["destination"]["kind"] == "successor"
    assert by["moved"]["destination"] == {"kind": "repo", "to": ["https://github.com/cj-mills/repo"]}
    assert by["gone"]["destination"] is None and res["pending"] == ["gone"]   # not yet named
    assert by["zombie"]["destination"] == {"kind": "hub"}   # a retired page with neither falls back to the hub
    assert by["cur"]["destination"] is None
    # site pages are never unruled; a retired one shows, its Lens successor live
    assert "404" not in by and by["about"]["destination"]["to"][0]["slug"] == "/about"
    assert res["unruled"] == ["unruled"] and by["draft"]["standing"] == ""   # a successor shows, unruled never
    assert res["counts"]["superseded"] == 3
    why = {a["slug"]: a["why"] for a in res["anomalies"]}
    assert set(why) == {"both", "zombie", "stale", "stray"}
    assert "retired, yet ruled current" in why["zombie"][0] and "re-rule" in why["stale"][0]
    assert "not removed" in why["stray"][0]
    # what each standing does to claim support
    assert support_standing(by["cur"]) == {"withdrawn": None, "passes_to": []}
    assert support_standing(by["arch"])["withdrawn"] == "archived"
    assert support_standing(by["gone"])["withdrawn"] == "removed"
    assert support_standing(by["moved"])["withdrawn"] == "retired"
    assert support_standing(by["old"])["withdrawn"] == "superseded" and support_standing(None)["withdrawn"] is None
    text = render("standing", {k: v for k, v in res.items() if k != "by_id"})
    assert "pending relocation 1" in text and "⚠ `zombie`" in text and "→ new" in text


def _e(src, kind, claim="c1"):
    return {"source_id": src, "target_id": claim, "properties": {"kind": kind, "note": src}}


def test_claims_count_current_support_only():
    claims = [{"key": "eng", "id": "c1", "name": "Engines", "statement": "s", "position": 1}]
    dl = {i: {"slug": i, "title": i, "public": i != "draft"} for i in ("cur", "arch", "gone", "old", "older",
                                                                       "new", "draft")}
    ss = {"arch": {"withdrawn": "archived", "passes_to": []}, "gone": {"withdrawn": "removed", "passes_to": []},
          "old": {"withdrawn": "superseded", "passes_to": [{"id": "new", "slug": "new"}]},
          "older": {"withdrawn": "superseded", "passes_to": [{"id": "new", "slug": "new"}]}}
    sup = [_e("cur", "capability"), _e("arch", "capability"), _e("gone", "method"), _e("old", "outcome"),
           _e("older", "outcome"), _e("old", "capability"), _e("new", "capability")]
    r = project_claims(claims, {"c1": ["offered"]}, sup, dl, ss)
    c = r["claims"][0]
    # the successor's own capability wins; the two superseded outcomes pass on as one row
    assert [(x["id"], x.get("via")) for x in c["supports"]["capability"]] == [("cur", None), ("new", None)]
    assert [(x["id"], x["via"]) for x in c["supports"]["outcome"]] == [("new", ["old", "older"])]
    assert c["supports"]["method"] == [] and c["backed"]
    assert [(x["id"], x["withdrawn"]) for x in c["withdrawn"]] == [
        ("arch", "archived"), ("gone", "removed"), ("old", "superseded"), ("old", "superseded"),
        ("older", "superseded")]
    assert "withdrawn" not in public_view(r)[0]
    # with everything withdrawn the claim falls below the floor
    only = project_claims(claims, {"c1": ["offered"]}, [_e("arch", "capability")], dl, ss)
    assert not only["claims"][0]["backed"] and only["refusals"][0]["reason"] == "unbacked"
    assert "withdrawn 1: archived 1" in render("claims", only)


def _post(slug: str) -> str:
    return f"---\ntitle: \"{slug}\"\ndate: 2024-01-01\ncategories: [tutorial]\n---\n\n## Overview\n\nBody.\n"


@pytestmark_graph
def test_the_writes_check_the_slate_and_the_endpoints(tmp_path):
    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            notes = [note_from_text(f"/c/posts/{s}/index.md", _post(s), corpus_root="/c/posts", lossless=True)
                     for s in ("old", "new", "draft", "gone")]
            nodes, edges = corpus_graph_elements(notes)
            url = "https://github.com/cj-mills/retired-posts/blob/main/gone/README.md"
            ref = ReferenceNode(graph=ReferenceNode.WEB, foreign_id=url, foreign_label="web page", title=url)
            await extend_graph(gx.queue, gx.graph_id, nodes + [ref.to_graph_node()], edges)
            await mint_deliverable_type(gx, "archive-tutorial", title="A", kind="tutorial", origin="archive")
            await mint_deliverable_type(gx, "born-page", title="B", kind="tutorial", origin="born")
            for s in ("old", "new", "gone"):
                await assert_value(gx, note_node_id(s), "deliverable_type", "archive-tutorial")
            await assert_value(gx, note_node_id("draft"), "deliverable_type", "born-page")
            await assert_value(gx, note_node_id("draft"), P.PUBLISH_STATE, "draft")
            out = {
                "bad": await assert_value(gx, note_node_id("old"), P.CURRENCY, "superseded"),
                "not_note": await assert_value(gx, ref.id, P.CURRENCY, "current"),
                "ok": await assert_value(gx, note_node_id("old"), P.CURRENCY, "removed"),
                "waits": await record_relation(gx, "SUPERSEDES", "draft", "old"),
                "sup": await record_relation(gx, "SUPERSEDES", "new", "old", note="the new series"),
                "to_ref": await record_relation(gx, "RELOCATED_TO", "gone", ref.id),
                "bad_end": await record_relation(gx, "RELOCATED_TO", "gone", "new"),
            }
            await assert_value(gx, note_node_id("gone"), P.CURRENCY, "removed")
            await assert_value(gx, note_node_id("new"), P.CURRENCY, "current")
            out["standing"] = await load_standing(gx)
            return out
    out = asyncio.run(go())
    assert "superseded is derived" in out["bad"]["error"] and "deliverable Note" in out["not_note"]["error"]
    assert not out["ok"].get("error")
    assert "once it is public" in out["waits"]["error"] and out["sup"]["written"]
    assert out["to_ref"]["written"] and "never to a deliverable" in out["bad_end"]["error"]
    by = out["standing"]["by_id"]
    assert by[note_node_id("old")]["superseded"] and by[note_node_id("old")]["pending"]
    assert by[note_node_id("gone")]["destination"]["to"] == [
        "https://github.com/cj-mills/retired-posts/blob/main/gone/README.md"]
    assert out["standing"]["anomalies"] == [] and out["standing"]["unruled"] == []
    assert out["to_ref"]["target_id"] == reference_node_id(ReferenceNode.WEB,
                                                           "https://github.com/cj-mills/retired-posts/blob/main/gone/README.md")


def _run(*args):
    return subprocess.run([sys.executable, "-m", "cjm_context_graph_projection.cli", *args],
                          capture_output=True, text=True)


def _ids(db):
    con = sqlite3.connect(str(db))
    try:
        return (sorted(r[0] for r in con.execute("select id from nodes")),
                sorted(r[0] for r in con.execute("select id from edges")))
    finally:
        con.close()


@pytestmark_graph
def test_a_rebuild_reproduces_the_standing(tmp_path):
    corpus = tmp_path / "posts"
    for s in ("a", "b", "c", "d"):
        (corpus / s).mkdir(parents=True)
        (corpus / s / "index.md").write_text(_post(s))
    commit_all(corpus)
    journal = str(tmp_path / "writes.jsonl")
    for sub in ("live", "fresh"):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "graph.config.json").write_text(json.dumps(
            {"notes_corpus": str(corpus), "notes_profile": "quarto_post"}))
    live = str(tmp_path / "live" / "g.db")
    base = ["--graph-db-path", live, "--journal-path", journal]
    assert _run(*base, "ingest-notes").returncode == 0
    from cjm_dev_graph_schema.identity import entity_node_id
    for cmd in (["notes-type", "archive-tutorial", "--title", "Archive tutorial", "--kind", "tutorial",
                 "--origin", "archive"],
                *(["assert", note_node_id(s), "deliverable_type", "archive-tutorial"] for s in "abcd"),
                ["entity", "claim", "eng", "--name", "Engines", "--statement", "Engine work", "--position", "1"],
                ["assert", entity_node_id("claim", "eng"), P.CLAIM_STATE, "building"],
                ["supports", "a", "eng", "--kind", "capability"],
                ["supports", "b", "eng", "--kind", "capability"],
                ["supports", "c", "eng", "--kind", "method"],
                ["assert", note_node_id("a"), P.CURRENCY, "current"],
                ["assert", note_node_id("b"), P.CURRENCY, "archived"],
                ["assert", note_node_id("c"), P.CURRENCY, "removed"],
                ["relate", "SUPERSEDES", "a", "c", "--note", "a succeeds c"]):
        r = _run(*base, *cmd)
        assert r.returncode == 0, (cmd, r.stdout, r.stderr)
    refused = _run(*base, "assert", note_node_id("d"), P.CURRENCY, "gone")
    assert refused.returncode != 0
    report = _run("--graph-db-path", live, "standing", "--bare")
    assert report.returncode == 0, report.stdout + report.stderr
    assert "current 1 · archived 1 · removed 1 · unruled 1 · superseded 1" in report.stdout
    claims = _run("--graph-db-path", live, "claims")
    assert "withdrawn 2: archived 1 · superseded 1" in claims.stdout and "← passed on by" in claims.stdout
    fresh = str(tmp_path / "fresh" / "g.db")
    r = _run("--graph-db-path", fresh, "--journal-path", journal, "ingest-notes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _ids(fresh) == _ids(live)
    assert _run("--graph-db-path", fresh, "standing", "--bare").stdout == report.stdout
    assert _run("--graph-db-path", fresh, "claims").stdout == claims.stdout
