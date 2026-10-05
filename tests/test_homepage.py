"""The home page (design e55201e2, amendment 5c3c2662): a projected map of the site under one
identity line -- the site's one sentence, the author strip's contact links, one entry per index
page the home Lens names (its title, its Lens's description, its hub pages in the index's own
order), the recent posts by their public dates, and a Work-with-me band only once that page is
published."""

import asyncio

import pytest

from cjm_context_graph_projection.agentlayer import load_index_copy
from cjm_context_graph_projection.homepage import (UNDESCRIBED, entry_refs, home_body, map_entries,
                                                   recent_posts)
from cjm_context_graph_projection.postpage import load_site_summary
from cjm_context_graph_projection.sitepages import GENERATED, hub_order

from test_sitepages import _HAVE_GRAPH, _HAVE_QUARTO, _facets, _graph, _site, _unrelated

WHO = 'site-author:\n  name: "N"\n  role: "R"\n'


def test_the_summary_is_the_sites_one_sentence(tmp_path):
    # Amendment 5c3c2662 (1): `site-summary`, filled from site-author, read by llms.txt and the home
    # page alike; a summary still kept under llms-index is a second copy and refuses
    (tmp_path / "_quarto.yml").write_text("site-summary: |\n  {name},   {role}: the site.\nllms-index:\n  details: d\n" + WHO)
    assert load_site_summary(str(tmp_path)) == {"text": "N, R: the site.", "errors": []}
    assert load_index_copy(str(tmp_path)) == {"copy": {"summary": "N, R: the site.", "details": "d"}, "errors": []}
    (tmp_path / "_quarto.yml").write_text("site-summary: S\nllms-index:\n  summary: S\n" + WHO)
    assert "second copy" in load_index_copy(str(tmp_path))["errors"][0]["why"]
    (tmp_path / "_quarto.yml").write_text("llms-index:\n  details: d\n" + WHO)
    assert load_site_summary(str(tmp_path))["errors"][0]["kind"] == "site-summary"
    assert load_index_copy(str(tmp_path))["errors"][0]["kind"] == "site-summary"
    (tmp_path / "_quarto.yml").write_text("site-summary: '{nope}'\n" + WHO)   # an unknown placeholder refuses
    assert load_site_summary(str(tmp_path))["errors"][0]["kind"] == "site-summary"
    (tmp_path / "_quarto.yml").write_text("site-summary: S\n")
    assert load_site_summary(str(tmp_path))["errors"][0]["kind"] == "site-author"


def test_the_map_is_an_ordered_subgraph_list():
    sel = [{"verb": "subgraph", "args": {"refs": ["t", "l"]}}, {"verb": "subgraph", "args": {"refs": ["c", "t"]}}]
    assert entry_refs(sel) == {"refs": ["t", "l", "c"]}
    assert "error" in entry_refs([{"verb": "list", "args": {"label": "Lens"}}])


def test_hubs_follow_the_listing_that_shows_them():
    # Amendment 5c3c2662 (5): the order a listing shows its pages, never a re-rank; a page with no
    # value for a key follows those with one; a key the build plans nothing for refuses
    pages = [{"title": "b", "source": "b.qmd", "updated": "2021-01-01", "date": "2020-1-1"},
             {"title": "A", "source": "a.qmd", "updated": "2022-01-01"},
             {"title": "c", "source": "c.qmd", "updated": None, "date": "2019-5-5"},
             {"title": "d", "source": "d.qmd", "updated": "2021-01-01"}]

    def srcs(got):
        return [h["source"] for h in got["hubs"]]
    assert srcs(hub_order(pages, ["date-modified desc"])) == ["a.qmd", "b.qmd", "d.qmd", "c.qmd"]
    assert srcs(hub_order(pages, ["date-modified desc", "title desc"])) == ["a.qmd", "d.qmd", "b.qmd", "c.qmd"]
    assert srcs(hub_order(pages, ["date asc"])) == ["c.qmd", "b.qmd", "a.qmd", "d.qmd"]
    assert srcs(hub_order(pages, ["title"])) == ["a.qmd", "b.qmd", "c.qmd", "d.qmd"]
    assert srcs(hub_order(pages, [])) == ["b.qmd", "a.qmd", "c.qmd", "d.qmd"]
    assert "error" in hub_order(pages, ["reading-time"])


def test_map_entries_state_each_index_or_nothing():
    planned = [
        {"subject": "t", "source": "series/tutorials/index.qmd", "title": "Tutorials", "members": 3,
         "hubs": [{"title": f"P{i}", "source": f"series/tutorials/p{i}.qmd"} for i in range(5)]},
        {"subject": "p", "source": "projects/index.qmd", "title": "Projects", "members": 0, "hubs": []},
        {"subject": "s", "source": "series/tutorials/p0.qmd", "title": "P0", "members": 2},
        {"subject": "u", "source": "logs/index.qmd", "title": "Logs", "members": 1, "hubs": []}]
    desc = {"t": "Hands-on tutorials.", "p": "Projects.", "s": "A series."}
    got = map_entries(["t", "p", "u"], planned, desc, 3, "index.qmd", "staging")
    # an index with nothing public renders nothing (e55201e2 (2)); staging marks a missing description
    assert got["errors"] == [] and got["skipped"] == ["projects/index.qmd"]
    assert got["entries"] == [
        {"title": "Tutorials", "href": "series/tutorials/index.qmd", "description": "Hands-on tutorials.",
         "hubs": [{"title": f"P{i}", "href": f"series/tutorials/p{i}.qmd"} for i in range(3)]},
        {"title": "Logs", "href": "logs/index.qmd", "description": UNDESCRIBED, "hubs": []}]
    pub = map_entries(["t", "u", "s", "gone"], planned, desc, 3, "index.qmd", "public")
    assert [(e["kind"], e["entry"]) for e in pub["errors"]] == [
        ("home-entry-undescribed", "u"), ("home-entry", "s"), ("home-entry", "gone")]


def test_recent_posts_by_their_public_dates():
    posts = [{"title": "Old", "href": "o", "published": "2020-01-01", "updated": "2020-01-01"},
             {"title": "Revised", "href": "r", "published": "2019-01-01", "updated": "2024-02-02"},
             {"title": "New", "href": "n", "published": "2024-03-03", "updated": "2024-03-03"},
             {"title": "Draft", "href": "d", "published": "", "updated": ""}]
    assert [p["title"] for p in recent_posts(posts, 2)] == ["New", "Revised"]
    assert [p["title"] for p in recent_posts(posts, 9)] == ["New", "Revised", "Old"]


def test_the_body_maps_the_site_under_one_line():
    entries = [{"title": "Tutorials", "href": "series/tutorials/index.qmd", "description": "Hands-on.",
                "hubs": [{"title": "P", "href": "series/tutorials/p.qmd"}]}]
    recent = [{"title": "New", "href": "posts/n/index.md", "published": "2024-03-03", "updated": "2024-03-03"},
              {"title": "Revised", "href": "posts/r/index.md", "published": "2019-01-01", "updated": "2024-02-02"}]
    body = home_body("N, R: the site.", "[About](/about.html)", entries, recent, "blog.qmd")
    assert body.split("\n") == [
        "N, R: the site.", "", "[About](/about.html)", "", "::: {.grid}", "",
        "::: {.g-col-12 .g-col-md-6}", "", "## [Tutorials](series/tutorials/index.qmd)", "", "Hands-on.", "",
        "- [P](series/tutorials/p.qmd)", "", ":::", "", ":::", "",
        "## [Recent posts](blog.qmd)", "", "- [New](posts/n/index.md) · March 3, 2024",
        "- [Revised](posts/r/index.md) · Updated February 2, 2024", ""]
    # the Work-with-me band renders once its page is published, never before (5c3c2662 (2))
    assert "## [Work with me](work/index.qmd)" in home_body("S", "L", [], [], "", band={"title": "Work with me",
                                                                                         "href": "work/index.qmd"})
    assert "##" not in home_body("S", "L", [], [], "")


@pytest.mark.skipif(not (_HAVE_GRAPH and _HAVE_QUARTO), reason="needs the graph capability and quarto")
def test_the_home_page_is_projected_and_rendered(tmp_path):
    from cjm_context_graph_projection.judging import judge_related
    from cjm_context_graph_projection.facetjudge import judge_facets
    from cjm_context_graph_projection.facetreview import review_facets
    from cjm_context_graph_projection.lens import lens_node_id, set_lens
    from cjm_context_graph_projection.runtime import open_graph
    from cjm_context_graph_projection.site import redirect_plan, site_build
    from cjm_context_graph_projection.sitepages import page_plan
    from cjm_context_graph_projection.write import assert_value
    site = tmp_path / "site"
    _site(site)
    (site / "index.md").unlink()   # the hand home page the projection replaces
    cfg = site / "_quarto.yml"
    cfg.write_text(cfg.read_text().replace("site-summary: S.\n", "site-summary: The site in one sentence.\n"))

    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            await _graph(gx, site)
            await judge_facets(gx, ask=_facets)
            await review_facets(gx, apply_text=(await review_facets(gx))["document"])
            await judge_related(gx, ask=_unrelated)
            # an index grouped by series: its hub is the series page; an index of logs holds none
            grouped = {"selection": [{"verb": "list", "args": {"label": "Note", "deliverable_kind": "notes"}}],
                       "view": {"group_by": "series", "sort": ["date desc"]}}
            await set_lens(gx, "shelf", grouped, title="Shelf", description="Every series.")
            await assert_value(gx, lens_node_id("shelf"), "site_path", "/shelf/")
            logs = {"selection": [{"verb": "list", "args": {"label": "Note", "deliverable_kind": "log"}}]}
            await set_lens(gx, "logs", logs, title="Logs", description="Logs.")
            await assert_value(gx, lens_node_id("logs"), "site_path", "/logs/")
            refs = [lens_node_id(k) for k in ("shelf", "logs", "topic")]
            home = {"selection": [{"verb": "subgraph", "args": {"refs": refs}}], "view": {"layout": "home"}}
            await set_lens(gx, "home", home, title="The Site")
            await assert_value(gx, lens_node_id("home"), "site_path", "/")
            uncounted = await page_plan(gx, str(site), "public", (await redirect_plan(gx))["pages"])
            await assert_value(gx, lens_node_id("home"), "home_hubs", "2")
            await assert_value(gx, lens_node_id("home"), "home_recent", "2")
            pub = await site_build(gx, str(site), "public")
            text = (site / "index.qmd").read_text()
            out = site / "_site"
            html = (out / "index.html").read_text()
            md = (out / "index.llms.md").read_text() if (out / "index.llms.md").exists() else ""
            llms = (out / "llms.txt").read_text()
            return uncounted, pub, text, html, md, llms

    uncounted, pub, text, html, md, llms = asyncio.run(go())
    assert {e["kind"] for e in uncounted["errors"]} == {"home-count"}
    assert pub["ok"], pub
    body = text.split("---\n", 2)[2]
    assert text.startswith(f"---\n{GENERATED}\ndescription-meta: The site in one sentence.\n"
                           "page-layout: full\ntitle-block-banner: false\n---\n")
    assert "<title>t</title>" in html   # the site root's tab is the site's own title
    assert '<meta name="description" content="The site in one sentence.">' in html
    # the identity line, the contact line, the map in the Lens's order (the empty logs index renders
    # nothing), each hub in its index's order, then the recent posts
    assert body.split("\n")[1:4] == ["The site in one sentence.", "", "L"]
    assert "## [Shelf](shelf/index.qmd)\n\nEvery series.\n\n- [CV series](series/tutorials/cv.qmd)\n" in body
    assert "## [Topic](series/notes/topic.qmd)\n\nEvery note.\n\n\n:::" in body and "Logs" not in body
    assert body.index("[Shelf]") < body.index("[Topic]") < body.index("Recent posts")
    assert "## [Recent posts](blog.qmd)\n\n- [Post B](posts/b/index.md) · January 1, 2022\n" \
           "- [Post C](posts/c/index.md) · June 1, 2021\n" in body
    assert 'href="./series/tutorials/cv.html"' in html or 'href="series/tutorials/cv.html"' in html
    # the agent layer: the home page's own markdown states the map; llms.txt never lists it
    assert md.startswith("The site in one sentence.\n\nL\n\n## ") and "Modified" not in md
    assert "CV series" in md and "https://example.org/index.llms.md" not in llms
