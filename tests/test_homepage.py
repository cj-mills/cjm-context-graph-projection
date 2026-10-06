"""The home page (design e55201e2, amendments 5c3c2662 and 8b4f15d0): a projected map of the site
under the author's role and what the site holds -- the role heading, the holds line, the contact
links as the kit's buttons, then one entry per page the home Lens names (a plain heading, an All-N
label linking the page, its Lens's description, its items: an index's hub pages in its own order,
the category index's as chips with their counts, the category listing's newest posts by their
public dates), jump links to the entries, and a Work-with-me band only once that page is
published."""

import asyncio

import pytest

from cjm_context_graph_projection.agentlayer import load_index_copy
from cjm_context_graph_projection.homepage import (UNDESCRIBED, entry_anchor, entry_refs, home_body,
                                                   map_entries, recent_posts)
from cjm_context_graph_projection.postpage import holds_line, load_site_holds, load_site_summary
from cjm_context_graph_projection.sitepages import GENERATED, hub_order

from test_sitepages import _HAVE_GRAPH, _HAVE_QUARTO, _facets, _graph, _site, _unrelated

WHO = 'site-author:\n  name: "N"\n  role: "R"\n'


def test_the_summary_is_composed_from_its_parts(tmp_path):
    # Amendment 5c3c2662 (1) + design 8b4f15d0 (3): `site-summary` names the author through
    # site-author and what the site holds through site-holds, so llms.txt reads the one sentence
    # and the home page the parts; a summary still kept under llms-index is a second copy and refuses
    cfg = tmp_path / "_quarto.yml"
    cfg.write_text("site-summary: |\n  {name},   {role}: {holds}.\nsite-holds: the   site\nreading-guide: d\n" + WHO)
    assert load_site_summary(str(tmp_path)) == {"text": "N, R: the site.", "errors": []}
    assert load_site_holds(str(tmp_path)) == {"text": "the site", "errors": []}
    assert load_index_copy(str(tmp_path)) == {"copy": {"summary": "N, R: the site.", "details": "d"}, "errors": []}
    cfg.write_text("site-summary: '{holds}'\nsite-holds: S\nllms-index:\n  summary: S\n" + WHO)
    assert "second copy" in load_index_copy(str(tmp_path))["errors"][0]["why"]
    # a summary naming no {holds} keeps a second copy of what the site holds: refused
    cfg.write_text("site-summary: '{name}: the site.'\nsite-holds: the site\n" + WHO)
    assert "{holds}" in load_site_summary(str(tmp_path))["errors"][0]["why"]
    cfg.write_text("site-summary: '{holds}'\n" + WHO)   # no holds clause
    assert load_site_summary(str(tmp_path))["errors"][0]["kind"] == "site-holds"
    cfg.write_text("reading-guide: d\n" + WHO)
    assert load_site_summary(str(tmp_path))["errors"][0]["kind"] == "site-summary"
    assert load_index_copy(str(tmp_path))["errors"][0]["kind"] == "site-summary"
    cfg.write_text("site-summary: '{holds} {nope}'\nsite-holds: S\n" + WHO)   # an unknown placeholder refuses
    assert load_site_summary(str(tmp_path))["errors"][0]["kind"] == "site-summary"
    cfg.write_text("site-summary: '{holds}'\nsite-holds: S\n")
    assert load_site_summary(str(tmp_path))["errors"][0]["kind"] == "site-author"


def test_the_holds_clause_stands_as_its_own_sentence():
    assert holds_line("tutorials and notes since 2020") == "Tutorials and notes since 2020."
    assert holds_line("Notes.") == "Notes." and holds_line("why?") == "Why?" and holds_line("  ") == ""


def test_the_map_is_an_ordered_subgraph_list():
    sel = [{"verb": "subgraph", "args": {"refs": ["t", "l"]}}, {"verb": "subgraph", "args": {"refs": ["c", "t"]}}]
    assert entry_refs(sel) == {"refs": ["t", "l", "c"]}
    assert "error" in entry_refs([{"verb": "list", "args": {"label": "Lens"}}])
    assert entry_anchor("Project Logs") == "home-project-logs" and entry_anchor("") == "home-entry"


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


PLANNED = [
    {"subject": "t", "key": "tutorials", "source": "series/tutorials/index.qmd", "title": "Tutorials", "members": 3,
     "hubs": [{"title": f"P{i}", "source": f"series/tutorials/p{i}.qmd"} for i in range(5)]},
    {"subject": "p", "key": "projects", "source": "projects/index.qmd", "title": "Projects", "members": 0, "hubs": []},
    {"subject": "s", "key": "s", "source": "series/tutorials/p0.qmd", "title": "P0", "members": 2},
    {"subject": "u", "key": "logs", "source": "logs/index.qmd", "title": "Logs", "members": 1, "hubs": []},
    {"subject": "b", "key": "blog", "source": "blog.qmd", "title": "Blog", "members": 3, "layout": "category-listing",
     "listed": ["x", "y", "z"]},
    {"subject": "c", "key": "categories", "source": "categories/index.qmd", "title": "Categories", "members": 2,
     "layout": "category-index", "hubs": [{"title": "Vision", "source": "categories/vision/index.qmd"},
                                          {"title": "Audio", "source": "categories/audio/index.qmd"}]},
    {"subject": "cv", "key": "subject:vision", "source": "categories/vision/index.qmd", "title": "Vision",
     "members": 4, "layout": "category"},
    {"subject": "ca", "key": "subject:audio", "source": "categories/audio/index.qmd", "title": "Audio",
     "members": 1, "layout": "category"}]
POSTS = {"x": {"title": "X", "href": "posts/x/index.md", "published": "2020-01-01", "updated": "2020-01-01"},
         "y": {"title": "Y", "href": "posts/y/index.md", "published": "2019-01-01", "updated": "2024-02-02"},
         "z": {"title": "Z", "href": "posts/z/index.md", "published": "2024-03-03", "updated": "2024-03-03"},
         "w": {"title": "W", "href": "posts/w/index.md", "published": "2025-01-01", "updated": "2025-01-01"}}


def test_map_entries_state_each_page_or_nothing():
    desc = {"t": "Hands-on tutorials.", "p": "Projects.", "s": "A series.", "b": "Every post.", "c": "Every category."}
    got = map_entries(["b", "t", "p", "c", "u"], PLANNED, desc, 3, 2, POSTS, "index.qmd", "staging")
    # an index with nothing public renders nothing (e55201e2 (2)); staging marks a missing description
    assert got["errors"] == [] and got["skipped"] == ["projects/index.qmd"]
    assert got["entries"] == [
        # the listing's newest posts by their public dates, counted from what it lists (w is not listed)
        {"title": "Blog", "href": "blog.qmd", "anchor": "home-blog", "description": "Every post.", "form": "posts",
         "count": 3, "items": [POSTS["z"], POSTS["y"]]},
        # an index's first hubs in its own order, counted from every hub it states
        {"title": "Tutorials", "href": "series/tutorials/index.qmd", "anchor": "home-tutorials",
         "description": "Hands-on tutorials.", "form": "hubs", "count": 5,
         "items": [{"title": f"P{i}", "href": f"series/tutorials/p{i}.qmd"} for i in range(3)]},
        # the category index's every page as a chip, each with the count its own page lists
        {"title": "Categories", "href": "categories/index.qmd", "anchor": "home-categories",
         "description": "Every category.", "form": "chips", "count": 2,
         "items": [{"title": "Vision", "href": "categories/vision/index.qmd", "count": 4},
                   {"title": "Audio", "href": "categories/audio/index.qmd", "count": 1}]},
        {"title": "Logs", "href": "logs/index.qmd", "anchor": "home-logs", "description": UNDESCRIBED,
         "form": "hubs", "count": 1, "items": []}]
    pub = map_entries(["t", "u", "s", "gone"], PLANNED, desc, 3, 2, POSTS, "index.qmd", "public")
    assert [(e["kind"], e["entry"]) for e in pub["errors"]] == [
        ("home-entry-undescribed", "u"), ("home-entry", "s"), ("home-entry", "gone")]


def test_recent_posts_by_their_public_dates():
    posts = [{"title": "Old", "href": "o", "published": "2020-01-01", "updated": "2020-01-01"},
             {"title": "Revised", "href": "r", "published": "2019-01-01", "updated": "2024-02-02"},
             {"title": "New", "href": "n", "published": "2024-03-03", "updated": "2024-03-03"},
             {"title": "Draft", "href": "d", "published": "", "updated": ""}]
    assert [p["title"] for p in recent_posts(posts, 2)] == ["New", "Revised"]
    assert [p["title"] for p in recent_posts(posts, 9)] == ["New", "Revised", "Old"]


def test_the_body_maps_the_site_under_the_role():
    desc = {"t": "Hands-on.", "b": "Every post.", "c": "Every category."}
    entries = map_entries(["b", "t", "c"], PLANNED, desc, 1, 2, POSTS, "index.qmd", "public")["entries"]
    links = [{"icon": "e", "text": "Email", "href": "mailto:e@x.org"}, {"icon": "g", "text": "GitHub", "href": "https://g"}]
    body = home_body("R", "The site.", links, entries)
    assert body.split("\n") == [
        # the identity: the role heading, the holds line, the links as buttons (the first primary)
        "::: {.home-identity}", "", "# R", "", "The site.", "",
        "::: {.home-contact}", "", "[Email](mailto:e@x.org){.kit-button .kit-primary} [GitHub](https://g){.kit-button}",
        "", ":::", "", ":::", "",
        # the jump links from the same entry list
        "::: {.home-jump}", "", "[Blog](#home-blog) · [Tutorials](#home-tutorials) · [Categories](#home-categories)",
        "", ":::", "", "::: {.home-map}", "",
        "::: {#home-blog .home-entry}", "", "::: {.home-entry-head}", "", "## Blog", "", "[All 3](blog.qmd){.home-all}",
        "", ":::", "", "Every post.", "",
        "- [Z](posts/z/index.md) [March 3, 2024]{.home-day}", "- [Y](posts/y/index.md) [Updated February 2, 2024]{.home-day}", "", ":::", "",
        "::: {#home-tutorials .home-entry}", "", "::: {.home-entry-head}", "", "## Tutorials", "",
        "[All 5](series/tutorials/index.qmd){.home-all}", "", ":::", "", "Hands-on.", "",
        "- [P0](series/tutorials/p0.qmd)", "", ":::", "",
        "::: {#home-categories .home-entry .home-wide}", "", "::: {.home-entry-head}", "", "## Categories", "",
        "[All 2](categories/index.qmd){.home-all}", "", ":::", "", "Every category.", "",
        "::: {.home-chips}", "", "[Vision [4]{.home-count}](categories/vision/index.qmd){.kit-chip}",
        "[Audio [1]{.home-count}](categories/audio/index.qmd){.kit-chip}", "", ":::", "",
        ":::", "", ":::", ""]
    # the Work-with-me band renders once its page is published, never before (5c3c2662 (2))
    assert "## [Work with me](work/index.qmd)" in home_body("R", "S.", [], [], band={"title": "Work with me",
                                                                                  "href": "work/index.qmd"})
    assert "##" not in home_body("R", "S.", [], [])
    assert "home-contact" not in home_body("R", "S.", [], []) and "home-map" not in home_body("R", "S.", [], [])


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
    cfg.write_text(cfg.read_text().replace("site-holds: S.\n", "site-holds: the site in one clause\n"))

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
            refs = [lens_node_id(k) for k in ("blog", "shelf", "logs", "topic")]
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
    assert text.startswith(f"---\n{GENERATED}\ndescription-meta: the site in one clause\n"
                           "page-layout: full\n---\n")
    assert "<title>t</title>" in html   # the site root's tab is the site's own title
    assert '<meta name="description" content="the site in one clause">' in html
    # the identity: the role heading, the holds line, the contact buttons
    assert body.split("\n")[1:8] == ["::: {.home-identity}", "", "# R", "", "The site in one clause.", "",
                                     "::: {.home-contact}"]
    assert "[Email](mailto:e@x.org){.kit-button .kit-primary}" in body
    assert 'class="kit-button kit-primary"' in html and '<h1' in html and 'class="title"' not in html
    # the map in the Lens's order (the empty logs index renders nothing), each with its All-N label:
    # the listing's newest posts, the index's hubs in its own order
    assert "## Blog\n\n[All 3](blog.qmd){.home-all}\n\n:::\n\nEvery post.\n\n" \
           "- [Post B](posts/b/index.md) [January 1, 2022]{.home-day}\n" \
           "- [Post C](posts/c/index.md) [June 1, 2021]{.home-day}\n" in body
    assert "## Shelf\n\n[All 1](shelf/index.qmd){.home-all}\n\n:::\n\nEvery series.\n\n" \
           "- [CV series](series/tutorials/cv.qmd)\n" in body
    assert "## Topic\n\n[All 3](series/notes/topic.qmd){.home-all}\n\n:::\n\nEvery note.\n\n\n:::" in body
    assert "Logs" not in body and body.index("## Blog") < body.index("## Shelf") < body.index("## Topic")
    assert "[Blog](#home-blog) · [Shelf](#home-shelf) · [Topic](#home-topic)" in body
    assert 'id="home-blog"' in html and 'href="./series/tutorials/cv.html"' in html or 'href="series/tutorials/cv.html"' in html
    # the agent layer: the home page's own markdown states the map; llms.txt never lists it
    assert md.startswith('# <a id="r"></a>R\n\nThe site in one clause.\n\n') and "Modified" not in md
    assert '<a id="home-blog"></a>' in md and "[All 3](blog.llms.md)" in md
    assert "CV series" in md and "https://example.org/index.llms.md" not in llms
