"""The projected series and topic pages (design e240183f): a Series page lists its members in
their AUTHORED order, a Lens page the notes it selects under the Lens's own sort; the page file
is build output at the location its site_path derives, marked, never written over source."""

import asyncio
import re
import shutil
from datetime import date
from pathlib import Path

import pytest

from cjm_context_graph_layer.ops import extend_graph
from cjm_dev_graph_schema.identity import note_node_id, series_node_id, topic_node_id
from cjm_dev_graph_schema.nodes import series_member_edge
from cjm_markdown_decompose_core.extract import note_from_text
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from cjm_context_graph_projection.lens import lens_node_id, set_lens, validate_lens_spec
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.series import mint_series, set_series_members
from cjm_context_graph_projection.site import publish_guard, redirect_plan, site_build
from cjm_context_graph_projection.sitepages import (GENERATED, is_generated, is_public, member_updated,
                                                    page_plan, page_source, parse_date, project_pages,
                                                    render_page)
from cjm_context_graph_projection.purenotes import mint_deliverable_type, note_types
from cjm_context_graph_projection.write import assert_value

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()
_HAVE_QUARTO = shutil.which("quarto") is not None


def test_page_source_dates_and_the_public_rule():
    assert page_source("/series/tutorials/x.html") == "series/tutorials/x.qmd"
    assert page_source("/series/notes/x") == "series/notes/x/index.qmd"   # Quarto's dir form
    assert page_source("/posts/p/") == "posts/p/index.qmd"
    # Every archive date form; last-modified is a file mtime and never counts
    assert parse_date("2023-8-21") == date(2023, 8, 21) == parse_date("8/21/2023") == parse_date("8-21-2023")
    assert parse_date("last-modified") is None and parse_date(None) is None
    assert member_updated({"properties": {"metadata": {"date": "2021-1-2", "date-modified": "last-modified"}}}) \
        == date(2021, 1, 2)
    assert member_updated({"properties": {"metadata": {"date": "2021-1-2", "date-modified": "2022-3-4"}}}) \
        == date(2022, 3, 4)
    # An archive post carries no publish_state; a deliverable is public only when published
    # A publish_state decides; with none, an ARCHIVE type is public as authored (c64e07e7);
    # neither is undecidable (None), never guessed
    archive = {"n": {"type": "archive-notes", "origin": "archive"}}
    assert is_public("n", {}, archive) and is_public("n", {"n": ["published"]}, {})
    assert is_public("n", {"n": ["draft"]}, archive) is False
    assert is_public("n", {"n": ["draft", "published"]}, {}) is False
    assert is_public("n", {}, {}) is None
    assert is_public("n", {}, {"n": {"type": "pure-notes", "origin": "born"}}) is None


def test_render_page_is_marked_and_lens_sort_is_validated(tmp_path):
    text = render_page({"title": "T", "listing": {"contents": ["../a.md"], "sort": False}})
    assert text.startswith(f"---\n{GENERATED}\ntitle: T\n") and text.endswith("---\n")
    (tmp_path / "g.qmd").write_text(text)
    (tmp_path / "h.qmd").write_text("---\ntitle: hand\n---\n")
    assert is_generated(tmp_path / "g.qmd") and not is_generated(tmp_path / "h.qmd")
    sel = [{"verb": "subgraph", "args": {"refs": ["x"]}}]
    assert validate_lens_spec({"selection": sel, "view": {"sort": ["date desc", "title asc"]}})[1] is None
    for bad in ([], ["date sideways"], "date desc", ["date desc extra"]):
        assert validate_lens_spec({"selection": sel, "view": {"sort": bad}})[1]


def _post(title: str, day: str, categories: str = "notes") -> str:
    return f"---\ntitle: \"{title}\"\ndate: {day}\ncategories: [{categories}]\n---\n\n## Overview\n\nBody.\n"


def _site(root: Path) -> None:
    for d in ("posts/a", "posts/b", "posts/c", "drafts/posts/d", "series/tutorials", "series/notes"):
        (root / d).mkdir(parents=True)
    (root / "_quarto.yml").write_text(
        "project:\n  type: website\nprofile:\n  default: public\n  group:\n    - [public, staging]\n"
        "filters:\n  - _derived/derived-blocks.lua\nwebsite:\n  title: t\n")
    (root / "_quarto-public.yml").write_text(
        'project:\n  render:\n    - "**/*.qmd"\n    - "**/*.md"\n    - "!drafts/"\n')
    (root / "_quarto-staging.yml").write_text(
        'project:\n  output-dir: _site-staging\n  render:\n    - "**/*.qmd"\n    - "**/*.md"\n')
    (root / "index.md").write_text("---\ntitle: Home\n---\n\nHome.\n")
    posts = {"a": ("Post A", "2020-01-01"), "b": ("Post B", "2022-01-01"), "c": ("Post C", "2021-06-01")}
    for s, (t, d) in posts.items():
        (root / "posts" / s / "index.md").write_text(_post(t, d))
    (root / "drafts" / "posts" / "d" / "index.md").write_text(_post("Draft D", "2023-01-01"))


def _items(html: str) -> list:
    return [re.sub(r"<[^>]+>", "", re.search(r'listing-title">(.*?)</h3>', c, re.S).group(1)).strip()
            for c in re.split(r'<div class="quarto-post', html)[1:]]


async def _graph(gx, root: Path) -> None:
    notes = [note_from_text(str(root / "posts" / s / "index.md"),
                            (root / "posts" / s / "index.md").read_text(),
                            corpus_root=str(root / "posts"), lossless=True) for s in ("a", "b", "c")]
    notes.append(note_from_text(str(root / "drafts" / "posts" / "d" / "index.md"),
                                (root / "drafts" / "posts" / "d" / "index.md").read_text(),
                                corpus_root=str(root / "drafts" / "posts"), lossless=True))
    nodes, edges = corpus_graph_elements(notes)
    await extend_graph(gx.queue, gx.graph_id, nodes, edges)
    await assert_value(gx, note_node_id("d"), "publish_state", "draft")
    # The archive posts' standing is their ARCHIVE type (design amendment c64e07e7)
    assert (await mint_deliverable_type(gx, "archive-notes", title="Archive notes",
                                        kind="notes", origin="archive"))["written"]
    for s in ("a", "b", "c"):
        await assert_value(gx, note_node_id(s), "deliverable_type", "archive-notes")
    # The series' AUTHORED order is b, a, c: neither date order nor its reverse
    await mint_series(gx, "cv", title="CV series", description="Parts.", image="./p.png",
                      date="2020-1-1", categories=["pytorch", "Tutorial"])
    await set_series_members(gx, "cv", ["b", "a", "c"])
    await assert_value(gx, series_node_id("cv"), "site_path", "/series/tutorials/cv.html")
    await mint_series(gx, "unpaged", title="No page")   # no site_path: no page
    spec = {"selection": [{"verb": "subgraph", "args": {"refs": [topic_node_id("notes")]}}],
            "expand": {"hops": 1, "relations": ["TAGGED"]}, "view": {"sort": ["date desc"]}}
    await set_lens(gx, "topic", spec, title="Topic", description="Every note.", date="2021-12-9")
    await assert_value(gx, lens_node_id("topic"), "site_path", "/series/notes/topic.html")


@pytest.mark.skipif(not (_HAVE_GRAPH and _HAVE_QUARTO), reason="needs the graph capability and quarto")
def test_series_and_topic_pages_are_projected_and_rendered(tmp_path):
    site = tmp_path / "site"
    _site(site)

    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            await _graph(gx, site)
            # A hand file where a projected page lands refuses the build before anything renders
            (site / "series" / "notes" / "topic.qmd").write_text("---\ntitle: hand\n---\n")
            refused = await site_build(gx, str(site), "public")
            assert not refused["ok"] and "render" not in refused
            assert [e["kind"] for e in refused["errors"]] == ["page-over-source"]
            (site / "series" / "notes" / "topic.qmd").unlink()
            # A stale generated page (a page that moved away) is removed by the next projection
            (site / "series" / "notes" / "old.qmd").write_text(render_page({"title": "Old"}))
            pub = await site_build(gx, str(site), "public")
            assert pub["ok"], pub
            assert pub["pages"]["planned"] == 2 and pub["pages"]["removed"] == ["series/notes/old.qmd"]
            assert pub["pages"]["unpaged"] == ["Series:unpaged"]
            cv = (site / "series" / "tutorials" / "cv.qmd").read_text()
            cv_mtime = (site / "series" / "tutorials" / "cv.qmd").stat().st_mtime
            topic = (site / "series" / "notes" / "topic.qmd").read_text()
            out = site / "_site"
            series_items = _items((out / "series" / "tutorials" / "cv.html").read_text())
            topic_items = _items((out / "series" / "notes" / "topic.html").read_text())
            stg = await site_build(gx, str(site), "staging")
            assert stg["ok"], stg
            staged = _items((site / "_site-staging" / "series" / "notes" / "topic.html").read_text())
            # A second public build rewrites the profile's own version and nothing else
            again = await site_build(gx, str(site), "public", render=False)
            # A broken chain (two members after one) refuses: the build never guesses an order
            await extend_graph(gx.queue, gx.graph_id, [],
                               [series_member_edge(note_node_id("d"), series_node_id("cv"), note_node_id("b"))])
            broken = await page_plan(gx, str(site), "public", (await redirect_plan(gx))["pages"])
            # A kind outside the vocabulary refuses; the kind and origin read through the type
            bad = await mint_deliverable_type(gx, "x", kind="gallery")
            typed = (await note_types(gx))[note_node_id("a")]
            # An archive post that loses its standing (typed by a BORN type, no publish_state):
            # the Lens page refuses to guess, and the guard refuses its public page
            await mint_deliverable_type(gx, "pure-notes")
            await assert_value(gx, note_node_id("a"), "deliverable_type", "pure-notes",
                               supersede=["archive-notes"])
            undecided = await page_plan(gx, str(site), "public", (await redirect_plan(gx))["pages"])
            guard = await publish_guard(gx, str(out), "drafts")
            return cv, cv_mtime, topic, series_items, topic_items, staged, again, broken, bad, typed, undecided, guard

    (cv, cv_mtime, topic, series_items, topic_items, staged, again, broken, bad, typed, undecided,
     guard) = asyncio.run(go())
    # The page's data is the node's; date-modified is the newest member date; order is authored
    assert "title: CV series\n" in cv and "date: 2020-1-1\n" in cv and "date-modified: '2022-01-01'\n" in cv
    assert "categories:\n- pytorch\n- tutorial\n" in cv and "image: ./p.png\n" in cv
    assert "  - ../../posts/b/index.md\n  - ../../posts/a/index.md\n  - ../../posts/c/index.md\n  sort: false\n" in cv
    assert series_items == ["Post B", "Post A", "Post C"]
    # The page file's mtime IS its derived date-modified (Quarto's sitemap lastmod reads it)
    from datetime import datetime, timezone
    assert datetime.fromtimestamp(cv_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M") == "2022-01-01T00:00"
    # The Lens page carries its own date and the Lens's own sort (date desc); the draft only under staging
    assert "date: 2021-12-9\n" in topic and "  sort:\n  - date desc\n" in topic and "sort-ui: true" in topic
    assert topic_items == ["Post B", "Post C", "Post A"]
    assert staged == ["Draft D", "Post B", "Post C", "Post A"]
    assert again["ok"] and again["pages"]["written"] == 1 and again["pages"]["unchanged"] == 1
    assert [e["kind"] for e in broken["errors"]] == ["series-order"]
    assert bad.get("error") and not bad["written"]
    assert typed == {"type": "archive-notes", "kind": "notes", "origin": "archive"}
    # (the forked chain above still refuses the series page beside it)
    assert {(e["kind"], e.get("slug")) for e in undecided["errors"]} == {("member-undecidable", "a"),
                                                                         ("series-order", None)}
    assert [(e["kind"], e["slug"]) for e in guard["errors"]] == [("unstanding", "a")]
