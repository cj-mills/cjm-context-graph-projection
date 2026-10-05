"""The About page (design ff0c6338, amendment 2364f215): a Lens projects the author's identity,
background and reading guide; the background is a born Note rendered under its approval."""

import asyncio
import json

import pytest

from cjm_context_graph_projection.aboutpage import about_body, background_ref, body_of
from cjm_context_graph_projection.agentlayer import profile_jsonld

from test_sitepages import _HAVE_GRAPH, _HAVE_QUARTO, _graph, _site, _unrelated, _facets

LINKS = [{"icon": "envelope-fill", "text": "Email", "href": "mailto:e@x.org"},
         {"icon": "github", "text": "GitHub", "href": "https://github.com/x"}]


def test_the_background_is_one_named_note():
    assert background_ref([{"verb": "subgraph", "args": {"refs": ["n1"]}}]) == {"ref": "n1"}
    for sel in ([{"verb": "subgraph", "args": {"refs": ["n1", "n2"]}}],
                [{"verb": "list", "args": {"label": "Note"}}],
                [{"verb": "subgraph", "args": {"refs": ["n1"]}}, {"verb": "subgraph", "args": {"refs": ["n2"]}}]):
        assert "error" in background_ref(sel)


def test_the_body_reads_the_note_after_its_front_matter():
    assert body_of("---\ntitle: T\n---\n\nI started.\n\nThen more.\n") == "I started.\n\nThen more."
    assert body_of("No front matter.\n") == "No front matter."
    assert body_of("---\ntitle: T\n---\n") == ""


def test_the_body_opens_on_the_site_sentence_and_closes_on_the_guide():
    body = about_body("N, R: the site.", "I started.", "Read the dates.")
    assert body.split("\n") == ["N, R: the site.", "", "I started.", "", "## How to read this site", "",
                                "Read the dates.", ""]
    marked = about_body("S", "B", "G", marker="::: {.callout-warning}\nDraft\n:::\n")
    assert marked.index("Draft") < marked.index("B")
    assert "## [Work with me](work.qmd)" in about_body("S", "B", "G", band={"title": "Work with me", "href": "work.qmd"})


def test_the_profile_names_the_author_never_an_offer():
    obj = profile_jsonld({"name": "N", "role": "R"}, "https://x.org/about.html", LINKS,
                         "https://x.org/me.jpg", "A University")
    assert obj == {"@context": "https://schema.org", "@type": "ProfilePage", "url": "https://x.org/about.html",
                   "mainEntity": {"@type": "Person", "name": "N", "jobTitle": "R", "url": "https://x.org/about.html",
                                  "image": "https://x.org/me.jpg", "sameAs": ["https://github.com/x"],
                                  "alumniOf": {"@type": "CollegeOrUniversity", "name": "A University"}}}
    bare = profile_jsonld({"name": "N", "role": "R"}, "u", LINKS[:1])
    assert set(bare["mainEntity"]) == {"@type", "name", "jobTitle", "url"}   # a mailto is never sameAs


@pytest.mark.skipif(not (_HAVE_GRAPH and _HAVE_QUARTO), reason="needs the graph capability and quarto")
def test_the_about_page_is_projected_under_its_backgrounds_approval(tmp_path):
    from cjm_context_graph_projection.facetjudge import judge_facets
    from cjm_context_graph_projection.facetreview import review_facets
    from cjm_context_graph_projection.judging import judge_related
    from cjm_context_graph_projection.lens import lens_node_id, set_lens
    from cjm_context_graph_projection.purenotes import mint_deliverable_type
    from cjm_context_graph_projection.runtime import open_graph
    from cjm_context_graph_projection.site import site_build
    from cjm_context_graph_layer.ops import extend_graph
    from cjm_context_graph_projection.authoring import emit_post
    from cjm_context_graph_projection.write import assert_value
    from cjm_dev_graph_schema.identity import note_node_id
    from cjm_markdown_decompose_core.extract import note_from_text
    from cjm_markdown_decompose_core.ingest import corpus_graph_elements
    site = tmp_path / "site"
    _site(site)
    cfg = site / "_quarto.yml"
    cfg.write_text(cfg.read_text() + "reading-guide: Read the dates, {name}.\n")
    cfg.write_text(cfg.read_text().replace('site-author:\n  name: "N"\n  role: "R"\n',
                                           'site-author:\n  name: "N"\n  role: "R"\n  image: images/me.jpg\n'
                                           '  alumni-of: A University\n'))
    (site / "images").mkdir()
    (site / "images" / "me.jpg").write_bytes(b"\xff\xd8\xff")
    (site / "custom-marquee.ejs").write_text('<div class="quarto-about-marquee"><%= about.title %> <%= about.body %></div>\n')
    bg = site / "drafts" / "about-background" / "index.md"
    bg.parent.mkdir(parents=True)
    bg.write_text("---\ntitle: About background\n---\n\nI started in 2016.\n")

    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            await _graph(gx, site)
            await judge_facets(gx, ask=_facets)
            await review_facets(gx, apply_text=(await review_facets(gx))["document"])
            await judge_related(gx, ask=_unrelated)
            note = note_from_text(str(bg), bg.read_text(), corpus_root=str(site / "drafts"), lossless=True)
            nodes, edges = corpus_graph_elements([note])
            await extend_graph(gx.queue, gx.graph_id, nodes, edges)
            nid = note_node_id(note.slug)
            await mint_deliverable_type(gx, "page-content", title="Page content", kind="site", origin="born")
            await assert_value(gx, nid, "deliverable_type", "page-content")
            await assert_value(gx, nid, "publish_state", "draft")
            about = {"selection": [{"verb": "subgraph", "args": {"refs": [nid]}}], "view": {"layout": "about"}}
            await set_lens(gx, "about", about, title="About", description="Who writes this site.")
            await assert_value(gx, lens_node_id("about"), "site_path", "/about.html")
            draft_pub = await site_build(gx, str(site), "public")
            staged = await site_build(gx, str(site), "staging")
            staged_text = (site / "about.qmd").read_text()
            emitted = await emit_post(gx, nid, str(site), write=False)
            await assert_value(gx, nid, "publish_state", "published")
            pub = await site_build(gx, str(site), "public")
            text = (site / "about.qmd").read_text()
            html = (site / "_site" / "about.html").read_text()
            md = (site / "_site" / "about.llms.md").read_text()
            llms = (site / "_site" / "llms.txt").read_text()
            return draft_pub, staged, staged_text, emitted, pub, text, html, md, llms

    draft_pub, staged, staged_text, emitted, pub, text, html, md, llms = asyncio.run(go())
    # a draft background refuses the public page and is marked in staging
    assert not draft_pub["ok"] and "about-unapproved" in {e["kind"] for e in draft_pub["errors"]}
    assert staged["ok"], staged
    assert "## Draft background" in staged_text and "I started in 2016." in staged_text
    # page content is never emitted as a post
    assert "page content" in emitted["error"]
    assert pub["ok"], pub
    body = text.split("---\n", 2)[2]
    assert body.split("\n")[1:4] == ["S.", "", "I started in 2016."]
    assert "## How to read this site\n\nRead the dates, N.\n" in body and "Draft" not in body
    assert "template: custom-marquee.ejs" in text and "image: images/me.jpg" in text
    assert "href: mailto:e@x.org" in text
    ld = json.loads(html.split('<script type="application/ld+json">', 1)[1].split("</script>", 1)[0])
    assert ld["@type"] == "ProfilePage" and ld["mainEntity"]["jobTitle"] == "R"
    assert ld["mainEntity"]["alumniOf"]["name"] == "A University" and "sameAs" not in ld["mainEntity"]
    assert "I started in 2016." in md
    assert "[About](https://example.org/about.llms.md): Who writes this site." in llms
