"""The projected series and topic pages (design e240183f): a Series page lists its members in
their AUTHORED order, a Lens page the notes it selects under the Lens's own sort; the page file
is build output at the location its site_path derives, marked, never written over source. Every
chip is the graph's (design ce17606b): a post's confirmed facets, a Series page's what most of
its members carry, and the category listing itself a projected Lens page."""

import asyncio
import re
import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml

from cjm_context_graph_layer.ops import extend_graph
from cjm_context_graph_projection.archive import retire_source
from cjm_dev_graph_schema.identity import (deliverable_type_node_id, note_node_id, series_node_id,
                                          topic_node_id)
from cjm_dev_graph_schema.nodes import series_member_edge
from cjm_dev_graph_schema.vocab import DevNodeKinds
from cjm_markdown_decompose_core.extract import note_from_text
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from cjm_context_graph_projection.lens import lens_node_id, set_lens, validate_lens_spec
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.series import mint_series, set_series_members
from cjm_context_graph_projection.coverage import mint_entity
from cjm_context_graph_projection.facetjudge import judge_facets
from cjm_context_graph_projection.facetreview import review_facets
from cjm_context_graph_projection.judging import judge_related
from cjm_context_graph_projection.site import publish_guard, redirect_plan, site_build
from cjm_context_graph_projection.sitepages import (GENERATED, check_category_listing, group_through_series,
                                                    is_generated, is_public, member_updated, page_plan,
                                                    page_source, parse_date, project_pages, render_page)
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
        "filters:\n  - _derived/derived-blocks.lua\nwebsite:\n  title: t\n  site-url: https://example.org\n"
        # the agent layer's switch and intro (design 39c51c15 (7), amendment 23a49667)
        "  llms-txt: true\nllms-index:\n  summary: S.\n"
        # the author strip's copy (design 39c51c15 (5)): typed posts carry the strip
        'author-strip:\n  byline: "B"\n  links: "L"\n  pitch: "P {claims} {href}"\n  questions: "Q"\n'
        'site-author:\n  name: "N"\n  role: "R"\n'
        'copyright-holder: "The Holder"\n'
        # where every category chip links (amendment of 0858bbd0; design a7224060)
        'category-listing: blog.qmd\n'
        'post-comments:\n  repo: o/r\n  repo-id: R_1\n  category: Comments\n  category-id: C_1\n')
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
    for pred, spdx in (("content_license", "cc-by-4.0"), ("code_license", "mit")):   # 39c51c15 (6)
        await assert_value(gx, deliverable_type_node_id("archive-notes"), pred, spdx)
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
    # The category facets (design ce17606b (1)): a tool and a subject, confirmed through the judge + review
    for kind, key, name in (("tool", "pytorch", "PyTorch"), ("subject", "vision", "Vision")):
        await mint_entity(gx, kind, key, name=name, fields={"description": name, "not_for": "a mention"})
    # The site's category listing is a projected Lens (design ce17606b (3))
    blog = {"selection": [{"verb": "list", "args": {"label": "Note", "deliverable_kind": "notes"}}],
            "view": {"layout": "category-listing", "sort": ["date desc", "title desc"]}}
    await set_lens(gx, "blog", blog, title="Blog", description="Every post.")
    await assert_value(gx, lens_node_id("blog"), "site_path", "/blog.html")
    # ... replacing a hand blog page, retired (the replay form: no git here); the projected page
    # renders at its old path, and the retired Note must not read as rendered there
    hand = note_from_text(str(root / "blog.qmd"), "---\ntitle: Blog\n---\n", corpus_root=str(root), lossless=True)
    nodes, edges = corpus_graph_elements([hand])
    await extend_graph(gx.queue, gx.graph_id, nodes, edges)
    await mint_deliverable_type(gx, "site-page", title="Site page", kind="site", origin="archive")
    await assert_value(gx, note_node_id("blog"), "deliverable_type", "site-page")
    assert (await retire_source(gx, note_node_id("blog"), reason="projected", successor=lens_node_id("blog"),
                                commit="0" * 40, rel_path="blog.qmd"))["written"]


def _unrelated(body):
    """A judge that relates nothing (design e09e262b's verb, without the service)."""
    return {"model": "test", "answers": {
        "relatedness": {"score": 0.1, "confidence": 0.9, "probabilities": {"0": 0.9, "1": 0.1}},
        "relation": {"choice": "unrelated", "confidence": 0.9, "probabilities": {"unrelated": 1.0}}}}


_FACETS = {("Post A", "tool:pytorch"), ("Post B", "tool:pytorch"), ("Post B", "subject:vision")}


def _facets(body):
    """A facet judge that proposes _FACETS (design eefda2dd's verb, without the service)."""
    t = body["state"]["post"]["title"]
    return {"model": "test", "usage": {"input_tokens": 1},
            "answers": {e: {"type": "noul", "noul": 0.9 if (t, e) in _FACETS else 0.1} for e in body["questions"]}}


@pytest.mark.skipif(not (_HAVE_GRAPH and _HAVE_QUARTO), reason="needs the graph capability and quarto")
def test_series_and_topic_pages_are_projected_and_rendered(tmp_path):
    site = tmp_path / "site"
    _site(site)

    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            await _graph(gx, site)
            # The facets land through the judge and the user's review (design eefda2dd), the
            # document confirmed as proposed
            await judge_facets(gx, ask=_facets)
            await review_facets(gx, apply_text=(await review_facets(gx))["document"])
            # A public build needs every public post judged (amendment e09e262b): a judge that
            # relates nothing, so the pages under test are unchanged
            await judge_related(gx, ask=_unrelated)
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
            assert pub["pages"]["planned"] == 3 and pub["pages"]["removed"] == ["series/notes/old.qmd"]
            assert pub["pages"]["unpaged"] == ["Series:unpaged"]
            cv = (site / "series" / "tutorials" / "cv.qmd").read_text()
            cv_mtime = (site / "series" / "tutorials" / "cv.qmd").stat().st_mtime
            topic = (site / "series" / "notes" / "topic.qmd").read_text()
            blog = (site / "blog.qmd").read_text()
            out = site / "_site"
            series_items = _items((out / "series" / "tutorials" / "cv.html").read_text())
            topic_items = _items((out / "series" / "notes" / "topic.html").read_text())
            # Every projected listing's chips link into the category listing (design a7224060): the
            # site listing template, every href planned by the build; the category listing keeps
            # Quarto's in-place filter
            for page in (out / "series" / "tutorials" / "cv.html", out / "series" / "notes" / "topic.html"):
                html = page.read_text()
                # the chips are the graph's facets (design ce17606b), never the front matter's `notes`
                assert 'class="listing-category" href="' in html and 'blog.html#category=PyTorch"' in html
                assert 'blog.html#category=Vision"' in html and "#category=notes" not in html
                assert '<div class="listing-category"' not in html and "quartoListingCategory('" not in html
            # the projected category listing keeps Quarto's sidebar and in-place filter, and the feed
            blog_html = (out / "blog.html").read_text()
            assert "quartoListingCategory('" in blog_html and _items(blog_html) == ["Post B", "Post C", "Post A"]
            feed = (out / "blog.xml").read_text()
            # a post's header shows its chips; a post with none shows none, whatever its front matter lists
            head_a = (out / "posts" / "a" / "index.html").read_text()
            head_c = (out / "posts" / "c" / "index.html").read_text()
            # every post is on the category listing, so it is no collection a post belongs to, and
            # llms.txt lists it as a site page (design ce17606b (3))
            assert ">Topic</a>" in head_a and ">Blog</a>" not in head_a
            llms_site = (out / "llms.txt").read_text().split("## Site\n")[1].split("\n## ")[0]
            assert llms_site.count("](https://example.org/blog.llms.md)") == 1
            assert "- [Blog](https://example.org/blog.llms.md)\n" in llms_site
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
            return (cv, cv_mtime, topic, series_items, topic_items, staged, again, broken, bad, typed, undecided,
                    guard, blog, feed, head_a, head_c)

    (cv, cv_mtime, topic, series_items, topic_items, staged, again, broken, bad, typed, undecided,
     guard, blog, feed, head_a, head_c) = asyncio.run(go())
    # The page's data is the node's; date-modified is the newest member date; order is authored
    assert "title: CV series\n" in cv and "date: 2020-1-1\n" in cv and "date-modified: '2022-01-01'\n" in cv
    assert "image: ./p.png\n" in cv
    front = yaml.safe_load(cv.split("---\n")[1])
    # the Series page's chips: what MORE THAN HALF its members carry (PyTorch 2 of 3, Vision 1 of 3)
    assert front["categories"] == ["PyTorch"]
    # every item states its chips, an item with none an empty list (design ce17606b (3))
    assert front["listing"]["contents"] == [
        {"path": "../../posts/b/index.md", "categories": ["PyTorch", "Vision"]},
        {"path": "../../posts/a/index.md", "categories": ["PyTorch"]},
        {"path": "../../posts/c/index.md", "categories": []}]
    assert front["listing"]["sort"] is False
    assert front["listing"]["template"] == "../../_derived/listing-default.ejs.md"
    assert front["listing"]["template-params"]["category-links"] == {
        "PyTorch": "/blog.html#category=PyTorch", "Vision": "/blog.html#category=Vision"}
    # The category listing: no title block, the numbered sidebar, the feed, its description the lead
    assert blog.startswith(f"---\n{GENERATED}\npagetitle: Blog\npage-layout: full\ntitle-block-banner: false\n")
    assert "  categories: numbered\n  feed: true\n" in blog and blog.endswith("---\n\nEvery post.\n")
    assert "    in-place: true\n" in blog and "category-links" not in blog
    assert "<category>PyTorch</category>" in feed and "<category>notes</category>" not in feed
    # (the fixture has Quarto's own title block, which renders the chips the filter set as labels)
    assert '<div class="quarto-category">PyTorch</div>' in head_a and ">notes</div>" not in head_a
    assert 'class="quarto-category"' not in head_c
    assert series_items == ["Post B", "Post A", "Post C"]
    # The page file's mtime IS its derived date-modified (Quarto's sitemap lastmod reads it)
    from datetime import datetime, timezone
    assert datetime.fromtimestamp(cv_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M") == "2022-01-01T00:00"
    # The Lens page carries its own date and the Lens's own sort (date desc); the draft only under staging
    assert "date: 2021-12-9\n" in topic and "  sort:\n  - date desc\n" in topic
    assert "  sort-ui:\n  - title\n  - date\n  filter-ui:\n  - date\n  - title\n  - description\n" in topic
    assert topic_items == ["Post B", "Post C", "Post A"]
    assert staged == ["Draft D", "Post B", "Post C", "Post A"]
    assert again["ok"] and again["pages"]["written"] == 1 and again["pages"]["unchanged"] == 2
    assert [e["kind"] for e in broken["errors"]] == ["series-order"]
    assert bad.get("error") and not bad["written"]
    assert typed == {"type": "archive-notes", "kind": "notes", "origin": "archive"}
    # (the forked chain above still refuses the series page beside it)
    assert {(e["kind"], e.get("slug")) for e in undecided["errors"]} == {("member-undecidable", "a"),
                                                                         ("series-order", None)}
    assert [(e["kind"], e["slug"]) for e in guard["errors"]] == [("unstanding", "a")]


def test_an_unlinked_listing_chip_fails_the_build(tmp_path):
    # Design a7224060: with a category listing named, a chip the template rendered as a label (a
    # category the build planned no link for) fails closed
    from cjm_context_graph_projection.sitepages import check_listing_chips
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "a.html").write_text('<a class="listing-category" href="../blog.html#category=x">x</a>')
    (tmp_path / "s" / "b.html").write_text('<div class="listing-category">y</div>')
    got = check_listing_chips(str(tmp_path), ["s/a.qmd", "s/b.qmd", "s/gone.qmd"])
    assert [(e["kind"], e["source"], e["chips"]) for e in got] == [("chip-unlinked", "s/b.qmd", 1)]


def test_the_category_listing_lists_every_public_post():
    # Design ce17606b (3): one category-listing page, at the page the site config names, listing
    # every public post under the public profile -- a post it left out is reachable from no chip
    types = {"a": {"kind": "notes", "origin": "archive"}, "t": {"kind": "tutorial", "origin": "archive"},
             "s": {"kind": "site", "origin": "archive"}, "r": {"kind": "notes", "origin": "archive"},
             "d": {"kind": "notes", "origin": "born"}}
    states = {"r": ["retired"], "d": ["draft"]}
    page = {"layout": "category-listing", "subject": "blog", "href": "/blog.html", "listed": ["a"]}
    got = check_category_listing([page], "/blog.html", "public", states, types)
    assert [(e["kind"], e.get("missing")) for e in got] == [("category-listing-incomplete", ["t"])]
    assert check_category_listing([page], "/blog.html", "staging", states, types) == []
    off = check_category_listing([page, {**page, "subject": "b2", "href": "/b2.html"}],
                                 "/blog.html", "staging", states, types)
    assert [e["kind"] for e in off] == ["category-listing", "category-listing"]


def test_a_lens_grouped_by_series_lists_each_series_page_once():
    # Design 7657c4a5 (1): a member in a paged Series is listed through that page, in the place of
    # its first member; a member in none as itself; a member two paged Series list refuses
    planned = [{"kind": DevNodeKinds.SERIES, "sequence": True, "source": "logs/arc/index.qmd", "listed": ["a1", "a2"]},
               {"kind": "Lens", "sequence": False, "source": "series/notes/topic.qmd", "listed": ["a1", "s1"]}]
    got = group_through_series(["s1", "a2", "a1", "s2"], ["../posts/s1/index.md", "../posts/a2/index.md",
                                                          "../posts/a1/index.md", "../posts/s2/index.md"],
                               planned, "logs/index.qmd", "lens")
    assert got == {"contents": ["../posts/s1/index.md", "arc/index.qmd", "../posts/s2/index.md"], "errors": []}
    planned.append({"kind": DevNodeKinds.SERIES, "sequence": True, "source": "logs/other/index.qmd", "listed": ["a1"]})
    got = group_through_series(["a1"], ["../posts/a1/index.md"], planned, "logs/index.qmd", "lens")
    assert got["contents"] == [] and [e["kind"] for e in got["errors"]] == ["lens-group"]


@pytest.mark.skipif(not (_HAVE_GRAPH and _HAVE_QUARTO), reason="needs the graph capability and quarto")
def test_category_pages_earn_a_page_or_redirect(tmp_path):
    # Design a62f2499: every facet entry's URL is a path fact; at or above the index Lens's
    # category_page_min it serves a page, below it a redirect into the filtered category listing;
    # every chip links the category's page where one exists; a topic Lens merges into a page
    from cjm_dev_graph_schema.identity import entity_node_id
    from cjm_context_graph_projection.archive import transfer_site_path
    from cjm_context_graph_projection.categorypages import (UNDESCRIBED, describe_categories, description_criteria,
                                                           entry_record, plan_category_paths)
    from cjm_context_graph_projection.facetjudge import load_facet_vocab
    from cjm_context_graph_projection.facetreview import review_state
    site = tmp_path / "site"
    _site(site)

    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            await _graph(gx, site)
            await judge_facets(gx, ask=_facets)
            await review_facets(gx, apply_text=(await review_facets(gx))["document"])
            await judge_related(gx, ask=_unrelated)
            idx = {"selection": [{"verb": "list", "args": {"label": "Note", "deliverable_kind": "notes"}}],
                   "view": {"layout": "category-index", "sort": ["date desc", "title desc"]}}
            await set_lens(gx, "categories", idx, title="Categories", description="Every category with a page.")
            await assert_value(gx, lens_node_id("categories"), "site_path", "/categories/")
            no_min = await page_plan(gx, str(site), "public", (await redirect_plan(gx))["pages"])
            await assert_value(gx, lens_node_id("categories"), "category_page_min", "2")
            unpathed = await page_plan(gx, str(site), "public", (await redirect_plan(gx))["pages"])
            plan = await plan_category_paths(gx, (await redirect_plan(gx))["pages"])
            for r in plan["assert"]:
                await assert_value(gx, r["subject"], "site_path", r["value"])
            merged = await transfer_site_path(gx, lens_node_id("topic"), entity_node_id("tool", "pytorch"), merge=True)
            # Amendment e38d403c: an earned page with no page_description refuses a public build (its
            # criteria never reach readers); staging renders the gap marked
            gap = await page_plan(gx, str(site), "public", (await redirect_plan(gx))["pages"])
            marked = await page_plan(gx, str(site), "staging", (await redirect_plan(gx))["pages"])
            # ... described through the review document: drafted, edited, landed whole
            doc = (await describe_categories(gx, website_root=str(site)))["document"]
            edited = doc.replace("\nPage description:\n", "\nPage description: Posts that build\nwith PyTorch.\n")
            dry = await describe_categories(gx, apply_text=edited, dry_run=True)
            described = await describe_categories(gx, apply_text=edited)
            stale = (await review_state(gx))["stale"]   # a description re-judges nothing
            again = await describe_categories(gx, apply_text=edited)   # its basis moved: refused
            pub = await site_build(gx, str(site), "public")
            out = site / "_site"
            got = {k: (out / k).read_text() for k in ("categories/vision/index.html", "series/notes/topic.html",
                                                       "series/tutorials/cv.html")}
            page = (site / "categories" / "pytorch" / "index.qmd").read_text()
            index = (site / "categories" / "index.qmd").read_text()
            items = _items((out / "categories" / "pytorch" / "index.html").read_text())
            llms = (out / "categories" / "pytorch" / "index.llms.md").exists()
            llms_txt = (out / "llms.txt").read_text()
            # a criteria change since the description was written re-surfaces it: reported, never
            # refused (the facet gate re-judges the entry apart), the document flags it, and the review re-confirms it unchanged
            live = (await load_facet_vocab(gx))["tool:pytorch"]
            rec = entry_record(live)
            await mint_entity(gx, "tool", "pytorch", name="PyTorch",
                              fields={**rec["fields"], "description": "PyTorch as the framework"})
            moved = await page_plan(gx, str(site), "public", (await redirect_plan(gx))["pages"])
            flagged = (await describe_categories(gx, website_root=str(site)))["document"]
            reconfirmed = await describe_categories(gx, apply_text=flagged)
            settled = await page_plan(gx, str(site), "public", (await redirect_plan(gx))["pages"])
            # a rename re-draws the entry's path, superseding the old one
            await mint_entity(gx, "subject", "vision", name="Computer vision",
                              fields={"description": "Vision", "not_for": "a mention"})
            renamed = await plan_category_paths(gx, (await redirect_plan(gx))["pages"])
            return (plan, unpathed, no_min, merged, pub, got, page, index, items, llms, renamed, gap, marked, doc,
                    dry, described, stale, again, llms_txt, moved, flagged, reconfirmed, settled)

    (plan, unpathed, no_min, merged, pub, got, page, index, items, llms, renamed, gap, marked, doc,
     dry, described, stale, again, llms_txt, moved, flagged, reconfirmed, settled) = asyncio.run(go())
    assert [r["value"] for r in plan["assert"]] == ["/categories/pytorch/", "/categories/vision/"]
    assert {e["kind"] for e in unpathed["errors"]} == {"category-path"}
    assert [e["kind"] for e in no_min["errors"]] == ["category-index"]
    assert "category_page_min" in no_min["errors"][0]["why"]
    assert merged["written"]
    assert [(e["kind"], e.get("entries")) for e in gap["errors"]] == [("category-undescribed", ["tool:pytorch"])]
    assert not marked["errors"] and marked["undescribed"] == ["tool:pytorch"]
    lede = [p for p in marked["pages"] if p.get("layout") == "category"][0]
    assert lede["description"] == UNDESCRIBED and "PyTorch as" not in lede["text"]
    assert "## PyTorch · tool `pytorch` · 2 posts <!-- category tool:pytorch " in doc
    assert "\nPage description:\n" in doc and "- Post A\n- Post B\n" in doc and "Vision ·" not in doc
    assert dry["counts"] == {"changed": 1, "reconfirmed": 0, "unchanged": 0, "blank": 0} and not dry["written"]
    fields = described["landed"][0]["fields"]
    assert described["written"] and fields == {
        "description": "PyTorch", "not_for": "a mention", "page_description": "Posts that build with PyTorch.",
        "page_description_basis": description_criteria({"entity_kind": "tool", "key": "pytorch", "name": "PyTorch",
                                                         "description": "PyTorch", "not_for": "a mention"})}
    assert stale == {}
    assert again.get("error") and "changed since" in again["errors"][0] and not again["written"]
    assert not moved["errors"] and moved["stale_descriptions"] == ["tool:pytorch"]
    assert "⚠ The criteria changed since this description was written" in flagged and "1 to re-read" in flagged
    assert reconfirmed["counts"] == {"changed": 0, "reconfirmed": 1, "unchanged": 0, "blank": 0}
    assert reconfirmed["written"] and settled["stale_descriptions"] == []
    assert pub["ok"], pub
    assert pub["redirects"]["category_stubs"] == 1
    # PyTorch (2 posts) earns its page; Vision (1) redirects into the filtered category listing
    assert '"":"../../blog.html#category=Vision"' in got["categories/vision/index.html"]
    # the merged topic Lens's URL redirects to the category page it merged into
    assert '"":"../../categories/pytorch/index.html"' in got["series/notes/topic.html"]
    # the page's lede, its index line and the agent layer all read the page_description (e38d403c (4))
    assert page.startswith(f"---\n{GENERATED}\ntitle: PyTorch\ndescription: Posts that build with PyTorch.\n")
    assert "  feed: true\n" in page and items == ["Post B", "Post A"]
    assert "[PyTorch](pytorch/index.qmd) · 2 posts · Posts that build with PyTorch." in index and "Vision" not in index
    assert "Posts that build with PyTorch." in llms_txt
    # every chip links its page where one exists, else the filtered listing
    cv = got["series/tutorials/cv.html"]
    assert 'href="../../categories/pytorch/"' in cv and 'href="../../blog.html#category=Vision"' in cv
    assert llms
    assert renamed["assert"] == [{"subject": entity_node_id("subject", "vision"), "entry": "subject:vision",
                                  "name": "Computer vision", "value": "/categories/computer-vision/",
                                  "supersede": ["/categories/vision/"]}]
