"""Derived blocks leave the render; the post navigation replaces them (design 253ac996, 75e7494a)."""

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from cjm_context_graph_layer.ops import extend_graph
from cjm_dev_graph_schema.identity import note_node_id, series_node_id, topic_node_id
from cjm_markdown_decompose_core.extract import note_from_text
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from cjm_context_graph_projection.derivedblocks import DERIVED_DIR, REPORT_FILE, render_nav
from cjm_context_graph_projection.lens import lens_node_id, set_lens
from cjm_context_graph_projection.purenotes import mint_deliverable_type
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.series import mint_series, set_series_members
from cjm_context_graph_projection.site import site_build
from cjm_context_graph_projection.write import assert_value

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()
_HAVE_QUARTO = shutil.which("quarto") is not None


def test_render_nav_shapes():
    a, b = {"title": "A", "href": "/posts/a/"}, {"title": "B [x]", "href": "/posts/b/"}
    first = render_nav({"title": "S", "href": "/series/s.html", "part": 1, "total": 2, "prev": None, "next": b}, [])
    assert "**Part 1 of 2** in [S](/series/s.html)" in first and "Previous" not in first
    assert "Next: [B (x)](/posts/b/)" in first          # a bracket in a title never breaks the link
    last = render_nav({"title": "S", "href": "/series/s.html", "part": 2, "total": 2, "prev": a, "next": None}, [])
    assert "Previous: [A](/posts/a/)" in last and "Next" not in last
    one = render_nav(None, [{"title": "Education", "href": "/series/notes/education.html"}])
    assert one.startswith("::: {.callout-note .derived-nav") and "In the collection: [Education]" in one
    two = render_nav(None, [a, b])
    assert "In the collections: [A](/posts/a/), [B (x)](/posts/b/)" in two
    assert render_nav(None, []) == ""


CALLOUT = ("::: {.callout-tip}\n## This post is part of the following series:\n"
           "* [**CV**](/series/tutorials/cv.html): The parts.\n:::\n\n")
TOC = "* [Overview](#overview)\n* [Details](#details)\n\n-----\n\n"


def _post(title: str, day: str, body: str) -> str:
    return f"---\ntitle: \"{title}\"\ndate: {day}\ncategories: [notes]\n---\n\n{body}"


def _site(root: Path) -> None:
    for d in ("posts/a", "posts/b", "posts/c", "series/tutorials", "series/notes"):
        (root / d).mkdir(parents=True)
    (root / "_quarto.yml").write_text(
        "project:\n  type: website\nprofile:\n  default: public\n  group:\n    - [public, staging]\n"
        "filters:\n  - _derived/derived-blocks.lua\nwebsite:\n  title: t\n")
    (root / "_quarto-public.yml").write_text('project:\n  render:\n    - "**/*.qmd"\n    - "**/*.md"\n')
    (root / "_quarto-staging.yml").write_text(
        'project:\n  output-dir: _site-staging\n  render:\n    - "**/*.qmd"\n    - "**/*.md"\n')
    (root / "index.md").write_text("---\ntitle: Home\n---\n\nHome.\n")
    body_a = CALLOUT + TOC + "## Overview\n\nPart one.\n\n## Details\n\nMore.\n\n### Next: [Part B](../b/)\n\nThanks.\n"
    body_b = CALLOUT + "## Overview\n\nPart two, and a [real link](/series/tutorials/cv.html).\n"
    (root / "posts" / "a" / "index.md").write_text(_post("Post A", "2020-01-01", body_a))
    (root / "posts" / "b" / "index.md").write_text(_post("Post B", "2021-01-01", body_b))
    # c: in no series, but the topic Lens lists it: a collections line only; the CRLF source
    # carries a hand TOC the classifier must still see
    (root / "posts" / "c" / "index.md").write_bytes(
        _post("Post C", "2022-01-01", TOC + "## Overview\n\nThree.\n\n## Details\n\nFour.\n")
        .replace("\n", "\r\n").encode())


async def _graph(gx, root: Path) -> None:
    notes = [note_from_text(str(root / "posts" / s / "index.md"),
                            (root / "posts" / s / "index.md").read_bytes().decode(),
                            corpus_root=str(root / "posts"), lossless=True) for s in ("a", "b", "c")]
    nodes, edges = corpus_graph_elements(notes)
    await extend_graph(gx.queue, gx.graph_id, nodes, edges)
    assert (await mint_deliverable_type(gx, "archive-notes", kind="notes", origin="archive"))["written"]
    for s in ("a", "b", "c"):
        await assert_value(gx, note_node_id(s), "deliverable_type", "archive-notes")
    await mint_series(gx, "cv", title="CV series")
    await set_series_members(gx, "cv", ["a", "b"])
    await assert_value(gx, series_node_id("cv"), "site_path", "/series/tutorials/cv.html")
    spec = {"selection": [{"verb": "subgraph", "args": {"refs": [topic_node_id("notes")]}}],
            "expand": {"hops": 1, "relations": ["TAGGED"]}, "view": {"sort": ["date desc"]}}
    await set_lens(gx, "topic", spec, title="Topic", description="Every note.")
    await assert_value(gx, lens_node_id("topic"), "site_path", "/series/notes/topic.html")


@pytest.mark.skipif(not (_HAVE_GRAPH and _HAVE_QUARTO), reason="needs the graph capability and quarto")
def test_derived_blocks_leave_the_render_and_the_navigation_replaces_them(tmp_path):
    site = tmp_path / "site"
    _site(site)
    sources = {s: (site / "posts" / s / "index.md").read_bytes() for s in ("a", "b", "c")}

    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            await _graph(gx, site)
            pub = await site_build(gx, str(site), "public")
            html = {s: (site / "_site" / "posts" / s / "index.html").read_text() for s in ("a", "b", "c")}
            report = (site / DERIVED_DIR / REPORT_FILE).read_text()
            # The source drifts from the graph (a TOC item added, no re-ingest): the fingerprint
            # no longer names the block, and the build fails closed, naming the post
            p = site / "posts" / "a" / "index.md"
            p.write_text(p.read_text().replace("* [Details](#details)", "* [Details](#details)\n* [Extra](#extra)"))
            drift = await site_build(gx, str(site), "public")
            return pub, html, report, drift

    pub, html, report, drift = asyncio.run(go())
    assert pub["ok"], pub
    assert pub["derived"] == {"posts": 3, "series_nav": 2, "collections": 3, "series_callout": 2,
                              "hand_toc": 2, "series_nav_line": 1, "reported": 3}
    # The sources never change: the blocks leave the render only
    assert all(sources[s] == (site / "posts" / s / "index.md").read_bytes() for s in ("b", "c"))
    a, b, c = html["a"], html["b"], html["c"]
    assert "This post is part of the following series" not in a + b
    assert 'href="#overview"' not in a and 'href="#overview"' not in c   # the hand TOCs left
    assert "Next:</a>" not in a and "Part B</a>" not in a.split("Part 1 of 2")[0]
    assert "<hr" not in a.split("Part one.")[0]                         # the closing rule left with the TOC
    assert "Part 1 of 2" in a and "Part 2 of 2" in b and "Part " not in c
    assert "In the collection" in a and "In the collection" in c
    assert "real link" in b and "Thanks." in a                          # content stays
    rows = [json.loads(l) for l in report.splitlines()]
    assert {r["input"] for r in rows} == {"posts/a/index.md", "posts/b/index.md", "posts/c/index.md"}
    assert all(d["count"] == 1 for r in rows for d in r["dropped"])
    assert not drift["ok"]
    assert [(e["kind"], e["source"], e.get("role")) for e in drift["errors"]] == [
        ("derived-drop", "posts/a/index.md", "hand_toc")]
