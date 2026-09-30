"""Retiring an archive source and moving a page's path (design amendment e916a4b9 under the
Tutorials page design 7f200ecb): the retirement is a journaled fact that records where the
source lived, the ingest restores the retired node from git before replay once its file has
left the tree, and a transferred path reads as the new holder's history -- a rebuild
reproduces the live graph id-for-id."""

import asyncio
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from cjm_dev_graph_schema.identity import note_node_id

from cjm_context_graph_projection.archive import path_owners
from cjm_context_graph_projection.lens import lens_node_id
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.site import redirect_plan
from cjm_context_graph_projection.sitelinks import site_path_holders, site_path_key

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()


def _a(aid: str, subject: str, value: str) -> dict:
    return {"id": aid, "label": "Assertion", "properties": {"subject_id": subject, "value": value}}


def test_path_owners_follow_the_chain_to_the_active_holder():
    asserts = [_a("p1", "page", "/old.html"), _a("p2", "page", "/tut/"),     # the page's own history
               _a("l1", "lens", "/tut/"),                                   # the transfer's assertion
               _a("x1", "x", "/x/"), _a("y1", "y", "/y/"), _a("z1", "z", "/z/")]
    supers = [("p2", "p1"),            # in-slot: the page's prior path
              ("l1", "p2"),            # across slots: the page's path moved to the lens
              ("x1", "z1"), ("y1", "z1")]   # a value reaching two pages is ambiguous
    owners, errors = path_owners(asserts, supers)
    assert owners == {"p1": "lens", "p2": "lens", "l1": "lens", "x1": "x", "y1": "y"}
    assert [(e["kind"], e["owners"]) for e in errors] == [("path-owner", ["x", "y"])]


def _run(*args, cwd=None):
    return subprocess.run([sys.executable, "-m", "cjm_context_graph_projection.cli", *args],
                          capture_output=True, text=True, cwd=cwd)


def _git(root: Path, *args) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _rows(db):
    con = sqlite3.connect(str(db))
    try:
        return (sorted(con.execute("select id, label, properties from nodes")),
                sorted(con.execute("select id, source_id, target_id, relation_type, properties from edges")))
    finally:
        con.close()


_POST = "---\ntitle: \"{t}\"\ndate: 2024-01-01\ncategories: [tutorial]\n---\n\n## Overview\n\nBody.\n"
_PAGE = "---\ntitle: \"Tutorials\"\nlisting:\n  contents: ./\n---\n"


@pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")
def test_retire_restore_and_transfer_survive_a_rebuild(tmp_path):
    site = tmp_path / "site"
    for d in ("posts/a", "series/tutorials"):
        (site / d).mkdir(parents=True)
    (site / "posts" / "a" / "index.md").write_text(_POST.format(t="Post A"))
    (site / "series" / "tutorials" / "index.md").write_text(_PAGE)
    _git(site, "init", "-q")
    _git(site, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "x", "--allow-empty")
    _git(site, "add", "-A")
    _git(site, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "site")
    journal = str(tmp_path / "writes.jsonl")
    for sub in ("live", "fresh", "bad"):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "graph.config.json").write_text(json.dumps(
            {"notes_corpus": str(site / "posts"), "notes_profile": "quarto_post", "website_root": str(site),
             "site_pages": ["series/tutorials/index.md"]}))
    live = str(tmp_path / "live" / "g.db")
    base = ["--graph-db-path", live, "--journal-path", journal]
    page, lens = note_node_id("series/tutorials"), lens_node_id("tutorials")
    spec = json.dumps({"selection": [{"verb": "list", "args": {"label": "Note", "deliverable_kind": "tutorial"}}],
                       "view": {"layout": "coverage-matrix"}})
    for cmd in (["ingest-notes"],
                ["notes-type", "site-page", "--title", "Site page", "--kind", "site", "--origin", "archive"],
                ["notes-type", "archive-tutorial", "--title", "T", "--kind", "tutorial", "--origin", "archive"],
                ["assert", page, "deliverable_type", "site-page"],
                ["assert", note_node_id("a"), "deliverable_type", "archive-tutorial"],
                ["assert", note_node_id("a"), "site_path", "/posts/a/"],
                ["assert", page, "site_path", "/series/tutorials/"],
                ["assert", page, "site_path", "/tutorials.html", "--superseded-by", "/series/tutorials/"],
                ["set-lens", "tutorials", "--spec", spec, "--title", "Tutorials"]):
        r = _run(*base, *cmd)
        assert r.returncode == 0, (cmd, r.stdout, r.stderr)
    # Only a Note retires this way; a source that differs from HEAD is refused
    assert "no Note" in _run(*base, "retire-source", lens, "--reason", "x").stdout
    # HEAD is what a rebuild restores, so a committed change the node does not carry refuses
    (site / "series" / "tutorials" / "index.md").write_text(_PAGE + "\nEdited.\n")
    _git(site, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "edit")
    dirty = _run(*base, "retire-source", page, "--reason", "x")
    assert dirty.returncode == 1 and "differs from the ingested node" in dirty.stdout
    (site / "series" / "tutorials" / "index.md").write_text(_PAGE)
    _git(site, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "revert")
    retired = _run(*base, "retire-source", page[:8], "--reason", "the Tutorials Lens projects this page",
                   "--successor", lens[:8])
    assert retired.returncode == 0, retired.stdout + retired.stderr
    moved = _run(*base, "transfer-path", page[:8], lens[:8])
    assert moved.returncode == 0, moved.stdout + moved.stderr
    # The source holds no active path now, and a target that holds one is refused
    again = _run(*base, "transfer-path", page, lens)
    assert again.returncode == 1 and "holds 0 active" in again.stdout
    taken = _run(*base, "transfer-path", lens, note_node_id("a"))
    assert taken.returncode == 1 and "already holds /posts/a/" in taken.stdout
    ops = [json.loads(line) for line in Path(journal).read_text().splitlines()]
    rop = next(o["args"] for o in ops if o["verb"] == "retire-source")
    assert rop["note"] == page and rop["path"] == "series/tutorials/index.md" and rop["successor"] == lens
    assert rop["commit"] == _git(site, "rev-parse", "HEAD")
    assert next(o["args"] for o in ops if o["verb"] == "transfer-path") == {"from": page, "to": lens,
                                                                             "actor": "agent:session"}
    # The file leaves the tree (its site_pages entry may linger: the restore owns the identity)
    (site / "series" / "tutorials" / "index.md").unlink()
    _git(site, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "retire the hand page")

    async def reads(db):
        async with open_graph(db) as gx:
            plan = await redirect_plan(gx)
            holders, active = await site_path_holders(gx)
            return plan, holders, active
    plan, holders, active = asyncio.run(reads(live))
    assert plan["errors"] == []
    assert plan["pages"][lens] == {"active": "/series/tutorials/", "superseded": ["/tutorials.html"]}
    assert page not in plan["pages"]
    assert [(s["alias"], s["subject"]) for s in plan["stubs"]] == [("/tutorials.html", lens)]
    assert holders[site_path_key("/series/tutorials/")] == {lens} and active[lens] == "/series/tutorials/"
    assert page not in active and holders[site_path_key("/tutorials.html")] == {lens}

    fresh = str(tmp_path / "fresh" / "g.db")
    r = _run("--graph-db-path", fresh, "--journal-path", journal, "ingest-notes")
    assert r.returncode == 0, r.stdout + r.stderr
    live_rows, fresh_rows = _rows(live), _rows(fresh)
    assert [n[0] for n in fresh_rows[0]] == [n[0] for n in live_rows[0]]
    assert [e[0] for e in fresh_rows[1]] == [e[0] for e in live_rows[1]]
    # The restored node is the ingested one, property for property
    assert next(n for n in fresh_rows[0] if n[0] == page) == next(n for n in live_rows[0] if n[0] == page)
    assert asyncio.run(reads(fresh))[0]["pages"] == plan["pages"]

    # A commit the clone does not hold (a shallow clone, a rewritten history) refuses loudly
    bad_journal = tmp_path / "bad.jsonl"
    bad_journal.write_text(Path(journal).read_text().replace(rop["commit"], "0" * 40))
    bad = _run("--graph-db-path", str(tmp_path / "bad" / "g.db"), "--journal-path", str(bad_journal),
               "ingest-notes")
    assert bad.returncode != 0 and "cannot restore it" in (bad.stdout + bad.stderr)
