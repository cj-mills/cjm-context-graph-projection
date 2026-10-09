"""The relocation of removed pages (design 3d5ee659 for 489dc0b1): the link map's rules, the
candidate repository, the README's projected block, a copy rendered by Quarto's gfm writer, and
the whole path -- project into a clone, land against the copy GitHub serves (refused while it is
missing or differs), the retirement and RELOCATED_TO journaled, the redirects projected to each
destination, and a rebuild reproducing the live graph."""

import asyncio
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import note_node_id

from cjm_context_graph_projection.relocate import (BLOCK_END, BLOCK_START, ast_targets, copy_dir, copy_targets,
                                                   copy_url, include_paths, land_relocations, merge_readme,
                                                   parse_copy_url, pointer_block, render_copy, repo_candidates,
                                                   resolve_target, strip_chrome, with_lead)
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.site import redirect_plan
from cjm_context_graph_projection.standing import load_standing

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()
_HAVE_QUARTO = shutil.which("quarto") is not None
_CFG = {"retired_repo": "o/retired-posts", "retired_dir": "posts", "companion_dir": "retired-post",
        "chrome_includes": ["/_cta.qmd"]}
_SITE = "https://example.com"


def test_the_copy_lives_under_its_whole_slug():
    assert copy_dir("a/part-1", "o/retired-posts", _CFG) == "posts/a/part-1"
    assert copy_dir("a/part-1", "o/Companion", _CFG) == "retired-post/a/part-1"
    url = copy_url("o/Companion", "main", "retired-post/a/part-1")
    assert url == "https://github.com/o/Companion/blob/main/retired-post/a/part-1/README.md"
    assert parse_copy_url(url) == {"repo": "o/Companion", "branch": "main", "dir": "retired-post/a/part-1"}
    assert copy_url("o/C", "main", "d", raw=True) == "https://raw.githubusercontent.com/o/C/main/d/README.md"
    assert parse_copy_url("https://example.com/x") is None


def test_the_link_map_rules():
    copies = copy_targets({"repo": "o/C", "branch": "main", "dir": "retired-post/s/part-1"}, [
        {"keys": ["/posts/s/part-2"], "repo": "o/C", "branch": "main", "dir": "retired-post/s/part-2"},
        {"keys": ["/posts/other", "/posts/old-name"], "repo": "o/D", "branch": "main", "dir": "retired-post/other"}])
    ctx = {"source_dir": "/posts/s/part-1/", "local": ["images/a.png"], "site_url": _SITE, "copies": copies}
    r = lambda t, image=False: resolve_target(t, image=image, **ctx)
    # kept as written: fragments, other hosts, mail links, the files the copy carries
    assert [r(t) for t in ("#intro", "https://other.org/x", "mailto:a@b.c", "./images/a.png", "images/a.png")] == [None] * 5
    # a relocated page: relative inside one repository, its GitHub page across repositories
    assert r("../part-2/") == "../part-2/README.md"
    assert r("/posts/s/part-2/index.html#setup") == "../part-2/README.md#setup"
    assert r("../part-2/index.md") == "../part-2/README.md"
    assert r("https://example.com/posts/old-name/") == "https://github.com/o/D/blob/main/retired-post/other/README.md"
    # a file under a relocated page: blob for a link, raw for an image
    assert r("/posts/other/images/b.png") == "https://github.com/o/D/blob/main/retired-post/other/images/b.png"
    assert r("/posts/other/images/b.png", image=True) == "https://github.com/o/D/raw/main/retired-post/other/images/b.png"
    # any other site link becomes its absolute URL; a page path names its directory
    assert r("/posts/keep/") == "https://example.com/posts/keep/"
    assert r("../../keep") == "https://example.com/posts/keep/"
    assert r("/series/tutorials/x.html?q=1#a") == "https://example.com/series/tutorials/x.html?q=1#a"
    assert r("../") == "https://example.com/posts/s/"
    assert r("https://example.com/posts/keep/") is None   # already absolute on the site


def test_chrome_includes_and_the_description():
    src = ("---\ntitle: T\ndescription: See [this](/posts/x/).\n---\n\nBody.\n\n{{< include /_cta.qmd >}}\n"
           "{{< include /_warn.qmd >}}\n")
    text, dropped = strip_chrome(src, ["/_cta.qmd"])
    assert dropped == ["/_cta.qmd"] and "_cta" not in text and include_paths(text) == ["/_warn.qmd"]
    led = with_lead(text, "See [this](/posts/x/).")
    assert led.startswith("---\ntitle: T\n") and "\n---\n\n*See [this](/posts/x/).*\n\n" in led
    assert led.index("*See") < led.index("Body.")
    assert with_lead("Body.\n", "") == "Body.\n" and with_lead("Body.\n", "a * b") == "a * b\n\nBody.\n"


def test_the_candidate_is_the_series_repository():
    repos = {"comp": "o/Comp", "other": "o/Other", "site": "o/site"}
    posts = [{"id": "1", "slug": "s/part-1", "text": "https://github.com/o/comp https://github.com/o/Other"},
             {"id": "2", "slug": "s/part-2", "text": "git clone https://github.com/o/comp.git"},
             {"id": "3", "slug": "lone", "text": "https://github.com/o/site/issues https://github.com/o/gone"}]
    c = repo_candidates(posts, repos, "o", exclude=["site"])
    assert c["1"]["candidate"] == c["2"]["candidate"] == "o/Comp" and c["1"]["group"] == "s"
    assert c["1"]["counts"] == {"o/Comp": 1, "o/Other": 1} and c["3"]["candidate"] is None


def test_the_readme_block_is_regenerated_in_place():
    block = pointer_block([{"title": "B", "path": "x/b/README.md", "date": "2021-02-01"},
                           {"title": "A", "path": "x/a/README.md", "date": "2021-01-01"}], dedicated=False,
                          site_url=_SITE)
    assert block.index("[A]") < block.index("[B]") and block.startswith(BLOCK_START) and block.endswith(BLOCK_END)
    once = merge_readme("# Repo\n\nText.\n", block, title="Repo")
    assert once == "# Repo\n\nText.\n\n" + block + "\n"
    newer = pointer_block([{"title": "A", "path": "x/a/README.md", "date": ""}], dedicated=False, site_url=_SITE)
    assert merge_readme(once, newer, title="Repo") == "# Repo\n\nText.\n\n" + newer + "\n"
    fresh = merge_readme(None, block, title="Retired posts", preamble="Intro.")
    assert fresh.startswith("# Retired posts\n\nIntro.\n\n" + BLOCK_START)


def test_the_targets_pandoc_reads():
    ast = {"blocks": [{"t": "Para", "c": [
        {"t": "Link", "c": [["", [], []], [], ["/posts/a/", ""]]},
        {"t": "Image", "c": [["", [], []], [], ["images/x.png", ""]]},
        {"t": "RawInline", "c": ["html", '<a href="../b/" data-src="no">']}]},
        {"t": "RawBlock", "c": ["html", '<img src="/images/y.png">']}]}
    got = ast_targets(ast)
    assert got == {"links": {"/posts/a/"}, "images": {"images/x.png"}, "raw": {"../b/", "/images/y.png"}}


_BODY = """---
title: "Old Post"
date: 2021-03-04
description: "From [the keep post](/posts/keep/)."
---

::: {.callout-tip}
## Part of a series
* [The series](/series/tutorials/s.html)
:::

## Intro

See [the next one](../gone2/) and [another](/posts/keep/#a).

<iframe width="100%" height="480" src="https://www.youtube.com/embed/abc_DEF-123" title="YouTube video player" frameborder="0" allowfullscreen></iframe>

![](./images/a.png){fig-align="center"}

{{< include /_warn.qmd >}}

{{< include /_cta.qmd >}}
"""


@pytest.mark.skipif(not _HAVE_QUARTO, reason="needs quarto")
def test_quarto_renders_the_copy():
    text, _ = strip_chrome(_BODY, ["/_cta.qmd"])
    maps = {"links": {"../gone2/": "../gone2/README.md", "/posts/keep/#a": "https://example.com/posts/keep/#a",
                      "/series/tutorials/s.html": "https://example.com/series/tutorials/s.html"},
            "images": {}, "raw": {}}
    out = render_copy(text, {"images/a.png": b"\x89PNG"}, {"/_warn.qmd": b"**Warning:** workers.\n"}, maps)
    assert "error" not in out, out
    body = out["body"]
    assert "> [!TIP]" in body and "(https://example.com/series/tutorials/s.html)" in body
    assert "[the next one](../gone2/README.md)" in body and "(https://example.com/posts/keep/#a)" in body
    assert "[![Watch on YouTube](https://img.youtube.com/vi/abc_DEF-123/hqdefault.jpg)](https://www.youtube.com/watch?v=abc_DEF-123)" in body
    assert "<iframe" not in body and "./images/a.png" in body and "**Warning:** workers." in body
    assert "Old Post" not in body and "_cta" not in body


def _run(*args):
    return subprocess.run([sys.executable, "-m", "cjm_context_graph_projection.cli", *args],
                          capture_output=True, text=True)


def _git(root: Path, *args) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _ids(db):
    con = sqlite3.connect(str(db))
    try:
        return (sorted(r[0] for r in con.execute("select id from nodes")),
                sorted(r[0] for r in con.execute("select id from edges")))
    finally:
        con.close()


def _post(title: str, body: str = "Body.\n") -> str:
    return f"---\ntitle: \"{title}\"\ndate: 2022-01-01\n---\n\n{body}"


@pytest.mark.skipif(not (_HAVE_GRAPH and _HAVE_QUARTO), reason="needs the graph capability and quarto")
def test_project_land_redirect_and_rebuild(tmp_path):
    site = tmp_path / "site"
    for s in ("old", "gone2", "keep", "succ", "oldtimm", "bare"):
        (site / "posts" / s).mkdir(parents=True)
    (site / "posts" / "old" / "images").mkdir()
    (site / "posts" / "old" / "index.md").write_text(_BODY)
    (site / "posts" / "old" / "images" / "a.png").write_bytes(b"\x89PNG")
    (site / "posts" / "gone2" / "index.md").write_text(_post("Gone Two", "Back to [old](../old/).\n"))
    for s in ("keep", "succ", "oldtimm", "bare"):
        (site / "posts" / s / "index.md").write_text(_post(s.title()))
    (site / "_warn.qmd").write_text("**Warning:** workers.\n")
    (site / "_cta.qmd").write_text("Hire me.\n")
    (site / "_quarto.yml").write_text(f"website:\n  site-url: \"{_SITE}\"\npost-comments:\n  repo: o/site\n")
    _git(site, "init", "-q")
    _git(site, "remote", "add", "origin", "https://github.com/o/site.git")
    _git(site, "add", "-A")
    _git(site, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "site")
    journal = str(tmp_path / "writes.jsonl")
    for sub in ("live", "fresh"):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "graph.config.json").write_text(json.dumps(
            {"notes_corpus": str(site / "posts"), "notes_profile": "quarto_post", "website_root": str(site),
             "relocation": {"retired_repo": "o/retired-posts", "chrome_includes": ["/_cta.qmd"]}}))
    live = str(tmp_path / "live" / "g.db")
    base = ["--graph-db-path", live, "--journal-path", journal]
    cmds = [["ingest-notes"],
            ["notes-type", "archive-tutorial", "--title", "T", "--kind", "tutorial", "--origin", "archive"]]
    for s in ("old", "gone2", "keep", "succ", "oldtimm", "bare"):
        cmds += [["assert", note_node_id(s), "deliverable_type", "archive-tutorial"],
                 ["assert", note_node_id(s), "site_path", f"/posts/{s}/"]]
    cmds += [["assert", note_node_id("old"), "site_path", "/posts/old-name.html", "--superseded-by", "/posts/old/"]]
    for s, c in (("old", "removed"), ("gone2", "removed"), ("keep", "current"), ("succ", "current"),
                 ("oldtimm", "removed"), ("bare", "removed")):
        cmds.append(["assert", note_node_id(s), P.CURRENCY, c])
    cmds.append(["relate", "SUPERSEDES", "succ", "oldtimm"])
    for cmd in cmds:
        r = _run(*base, *cmd)
        assert r.returncode == 0, (cmd, r.stdout, r.stderr)
    clone = tmp_path / "clone"
    clone.mkdir()
    _git(clone, "init", "-q", "-b", "main")
    _git(clone, "remote", "add", "origin", "https://github.com/o/retired-posts.git")
    projected = _run(*base, "relocate", "project", "old", "gone2", "oldtimm", "--into", str(clone))
    assert projected.returncode == 1 and "retires to the successor" in projected.stdout, projected.stdout + projected.stderr
    assert "## Projected into o/retired-posts@main — 2 copy(ies)" in projected.stdout
    copy = (clone / "posts" / "old" / "README.md").read_text()
    assert copy.startswith("# Old Post\n\n> [!NOTE]\n") and "first published on 2021-03-04 at <https://example.com/posts/old/>" in copy
    assert "*From [the keep post](https://example.com/posts/keep/).*" in copy
    assert "[the next one](../gone2/README.md)" in copy and "Hire me" not in copy and "**Warning:** workers." in copy
    assert (clone / "posts" / "old" / "images" / "a.png").read_bytes() == b"\x89PNG"
    assert "[old](../old/README.md)" in (clone / "posts" / "gone2" / "README.md").read_text()
    readme = (clone / "README.md").read_text()
    assert readme.startswith("# Retired posts\n") and "[Old Post](posts/old/README.md) (2021-03-04)" in readme

    def fetch(missing=()):
        def go(url):
            rel = url.split("/main/", 1)[1]
            if any(m in rel for m in missing):
                return 404, b""
            return 200, (clone / rel).read_bytes()
        return go

    def land(posts, fetcher, into=str(clone)):
        from cjm_context_graph_primitives.journal import append_write

        async def go():
            async with open_graph(live) as gx:
                return await land_relocations(gx, posts, website_root=str(site), config=_CFG, into=into,
                                              journal_path=journal, journal=lambda v, a: append_write(journal, v, a),
                                              fetch=fetcher)
        return asyncio.run(go())

    ops_before = Path(journal).read_text()
    refused = land(["gone2"], fetch(missing=("gone2",)))
    assert refused["landed"] == [] and "not live on GitHub (HTTP 404)" in refused["errors"][0]["why"]
    stale = land(["gone2"], lambda url: (200, b"an older copy"))
    assert "differs from the projection" in stale["errors"][0]["why"]
    assert Path(journal).read_text() == ops_before    # a refusal leaves nothing behind
    done = land(["old", "gone2", "oldtimm"], fetch())
    assert done["errors"] == [], done["errors"]
    assert [(x["slug"], x["destination"]) for x in done["landed"]] == [
        ("old", "repo"), ("gone2", "repo"), ("oldtimm", "successor")]
    ops = [json.loads(line) for line in Path(journal).read_text().splitlines()]
    assert [o["verb"] for o in ops[-5:]] == ["retire-source", "relocate", "retire-source", "relocate", "retire-source"]
    assert ops[-4]["args"]["url"] == "https://github.com/o/retired-posts/blob/main/posts/old/README.md"
    assert ops[-4]["args"]["observed_hash"].startswith("sha256:")
    # a retired page with no destination refuses the redirect projection
    r = _run(*base, "retire-source", note_node_id("bare"), "--reason", "no destination yet")
    assert r.returncode == 0, r.stdout + r.stderr

    async def read(db):
        async with open_graph(db) as gx:
            return await load_standing(gx), await redirect_plan(gx)
    standing, plan = asyncio.run(read(live))
    by = standing["by_id"]
    assert by[note_node_id("old")]["destination"] == {
        "kind": "repo", "to": ["https://github.com/o/retired-posts/blob/main/posts/old/README.md"]}
    assert by[note_node_id("oldtimm")]["destination"]["kind"] == "successor"
    stubs = {s["stub"]: s["target"] for s in plan["stubs"]}
    assert stubs["posts/old/index.html"] == stubs["posts/old-name.html"] == \
        "https://github.com/o/retired-posts/blob/main/posts/old/README.md"
    assert stubs["posts/oldtimm/index.html"] == "../succ/index.html"
    assert [(e["kind"], e["subject"]) for e in plan["errors"]] == [("retired-destination", note_node_id("bare"))]
    # the sources leave the website clone; a rebuild restores them from git and replays the journal
    _git(site, "rm", "-rq", "posts/old", "posts/gone2", "posts/oldtimm", "posts/bare")
    _git(site, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "relocated")
    fresh = str(tmp_path / "fresh" / "g.db")
    r = _run("--graph-db-path", fresh, "--journal-path", journal, "ingest-notes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _ids(fresh) == _ids(live)
    assert asyncio.run(read(fresh))[1]["stubs"] == plan["stubs"]
