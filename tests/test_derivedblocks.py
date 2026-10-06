"""Derived blocks leave the render; the post navigation replaces them (design 253ac996, 75e7494a)."""

import asyncio
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml

import pytest

from cjm_context_graph_layer.ops import extend_graph
from cjm_dev_graph_schema.identity import (deliverable_type_node_id, note_node_id, series_node_id,
                                          topic_node_id)
from cjm_markdown_decompose_core.extract import note_from_text
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from cjm_context_graph_projection.agentlayer import read_jsonld
from cjm_context_graph_projection.derivedblocks import (DERIVED_DIR, REPORT_FILE, check_end_placement,
                                                        render_nav)
from cjm_context_graph_projection.lens import lens_node_id, set_lens
from cjm_context_graph_projection.purenotes import mint_deliverable_type
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.series import mint_series, set_series_members
from cjm_context_graph_projection.judging import judge_related
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


def test_end_placement_fails_closed_outside_main(tmp_path):
    for slug, html in (("in", '<main><p>Body.</p><div class="author-strip">A</div></main>'),
                       ("out", '<main><p>Body.</p></main></div><div class="author-strip">A</div>')):
        (tmp_path / "posts" / slug).mkdir(parents=True)
        (tmp_path / "posts" / slug / "index.html").write_text(html)
    plan = {"posts": {"posts/in/index.md": {"end": "x"}, "posts/out/index.md": {"end": "x"},
                      "posts/none/index.md": {"end": ""}}}
    res = check_end_placement(str(tmp_path), plan)
    assert res["checked"] == 2
    assert [(e["kind"], e["source"]) for e in res["errors"]] == [("derived-end-placement", "posts/out/index.md")]
    # The comments block sits inside <main> and is the page's only widget (39c51c15 (1))
    block = '<div class="post-comments">C</div>'
    for slug, html in (("one", f'<main><div class="author-strip">A</div>{block}</main>'),
                       ("two", f'<main><div class="author-strip">A</div>{block}</main><script src="https://utteranc.es/client.js"></script>'),
                       ("gone", '<main><div class="author-strip">A</div></main>')):
        (tmp_path / "posts" / slug).mkdir(parents=True)
        (tmp_path / "posts" / slug / "index.html").write_text(html)
    plan = {"posts": {f"posts/{s}/index.md": {"end": "::: {.post-comments}\n:::"} for s in ("one", "two", "gone")}}
    assert sorted((e["kind"], e["source"]) for e in check_end_placement(str(tmp_path), plan)["errors"]) == [
        ("comments-second-widget", "posts/two/index.md"), ("derived-comments-placement", "posts/gone/index.md")]


CALLOUT = ("::: {.callout-tip}\n## This post is part of the following series:\n"
           "* [**CV**](/series/tutorials/cv.html): The parts.\n:::\n\n")
TOC = "* [Overview](#overview)\n* [Details](#details)\n\n-----\n\n"
ABOUT = "\n{{< include /_about-author-cta.qmd >}}\n"
QUESTIONS = "\n{{< include /_tutorial-cta.qmd >}}\n"
STRIP = ('site-author:\n  name: "The Author"\n  role: "a byline"\n'
         'author-strip:\n  byline: "**{name}**, {role}."\n  links: "[About](/about.html) · {links}"\n'
         '  pitch: "Hire me for {claims}: [how]({href})."\n  questions: "Ask in the comments."\n'
         'site-links:\n  - icon: envelope-fill\n    text: Email\n    href: mailto:e@x.org\n'
         'copyright-holder: "The Author"\n'
         'post-comments:\n  repo: o/r\n  repo-id: R_1\n  category: Comments\n  category-id: C_1\n')


def _post(title: str, day: str, body: str) -> str:
    return f"---\ntitle: \"{title}\"\ndate: {day}\ncategories: [notes]\n---\n\n{body}"


def _site(root: Path) -> None:
    for d in ("posts/a", "posts/b", "posts/c", "series/tutorials", "series/notes"):
        (root / d).mkdir(parents=True)
    (root / "_quarto.yml").write_text(
        "project:\n  type: website\nprofile:\n  default: public\n  group:\n    - [public, staging]\n"
        "filters:\n  - _derived/derived-blocks.lua\nwebsite:\n  title: t\n  site-url: https://example.org\n"
        "  llms-txt: true\nsite-summary: The test site.\n" + STRIP)
    # The posts' author, as Quarto merges it from the directory (the JSON-LD's, 23a49667 (4))
    (root / "posts" / "_metadata.yml").write_text("author: The Author\n")
    (root / "_about-author-cta.qmd").write_text(
        '---\n\n::: {.callout-tip title="About Me:"}\nI\'m the author. [More](/about.html)\n:::\n')
    (root / "_tutorial-cta.qmd").write_text('::: {.callout-tip title="Questions:"}\n- Ask below.\n:::\n')
    (root / "_quarto-public.yml").write_text('project:\n  render:\n    - "**/*.qmd"\n    - "**/*.md"\n')
    (root / "_quarto-staging.yml").write_text(
        'project:\n  output-dir: _site-staging\n  render:\n    - "**/*.qmd"\n    - "**/*.md"\n')
    (root / "index.md").write_text("---\ntitle: Home\n---\n\nHome.\n")
    # The page the strip's and the include's About links name (the rendered-link gate, c6befeb6)
    (root / "about.md").write_text("---\ntitle: About\n---\n\nAbout.\n")
    body_a = (CALLOUT + TOC + "## Overview\n\nPart one.\n\n## Details\n\nMore.\n\n### Next: [Part B](../b/)\n\n"
              "Thanks.\n" + ABOUT)
    body_b = CALLOUT + "## Overview\n\nPart two, and a [real link](/series/tutorials/cv.html).\n" + QUESTIONS + ABOUT
    (root / "posts" / "a" / "index.md").write_text(_post("Post A", "2020-01-01", body_a))
    (root / "posts" / "b" / "index.md").write_text(_post("Post B", "2021-01-01", body_b))
    # c: in no series, but the topic Lens lists it: a collections line only; the CRLF source
    # carries a hand TOC the classifier must still see
    (root / "posts" / "c" / "index.md").write_bytes(
        _post("Post C", "2022-01-01", TOC + "## Overview\n\nThree.\n\n## Details\n\nFour.\n"
              # raw HTML left open (a cell output's, in the archive): Pandoc nests the rest of the
              # body, the include's callout with it, inside a Div
              + '\n<div class="output">\n\nInside.\n' + ABOUT)
        .replace("\n", "\r\n").encode())


async def _graph(gx, root: Path) -> None:
    notes = [note_from_text(str(root / "posts" / s / "index.md"),
                            (root / "posts" / s / "index.md").read_bytes().decode(),
                            corpus_root=str(root / "posts"), lossless=True) for s in ("a", "b", "c")]
    nodes, edges = corpus_graph_elements(notes)
    await extend_graph(gx.queue, gx.graph_id, nodes, edges)
    assert (await mint_deliverable_type(gx, "archive-notes", kind="notes", origin="archive"))["written"]
    assert (await mint_deliverable_type(gx, "archive-tutorial", kind="tutorial", origin="archive"))["written"]
    for s in ("a", "c"):
        await assert_value(gx, note_node_id(s), "deliverable_type", "archive-notes")
    await assert_value(gx, note_node_id("b"), "deliverable_type", "archive-tutorial")
    for t in ("archive-notes", "archive-tutorial"):   # the class licenses (39c51c15 (6))
        await assert_value(gx, deliverable_type_node_id(t), "content_license", "cc-by-nc-sa-4.0")
        await assert_value(gx, deliverable_type_node_id(t), "code_license", "mit")
    await assert_value(gx, note_node_id("c"), "content_license", "cc-by-4.0")   # one post's override
    await assert_value(gx, note_node_id("b"), "revised", "a new section")   # the header's Updated (39c51c15 (2))
    # b's comment thread, and an earlier one that stands superseded (39c51c15 (1), 86f4a34d)
    await assert_value(gx, note_node_id("b"), "discussion", "7")
    await assert_value(gx, note_node_id("b"), "discussion", "3", superseded_by=["7"])
    await mint_series(gx, "cv", title="CV series")
    await set_series_members(gx, "cv", ["a", "b"])
    await assert_value(gx, series_node_id("cv"), "site_path", "/series/tutorials/cv.html")
    spec = {"selection": [{"verb": "subgraph", "args": {"refs": [topic_node_id("notes")]}}],
            "expand": {"hops": 1, "relations": ["TAGGED"]}, "view": {"sort": ["date desc"]}}
    await set_lens(gx, "topic", spec, title="Topic", description="Every note.")
    await assert_value(gx, lens_node_id("topic"), "site_path", "/series/notes/topic.html")


def _unrelated(body):
    """A judge that relates nothing (design e09e262b's verb, without the service)."""
    return {"model": "test", "answers": {
        "relatedness": {"score": 0.1, "confidence": 0.9, "probabilities": {"0": 0.9, "1": 0.1}},
        "relation": {"choice": "unrelated", "confidence": 0.9, "probabilities": {"unrelated": 1.0}}}}


@pytest.mark.skipif(not (_HAVE_GRAPH and _HAVE_QUARTO), reason="needs the graph capability and quarto")
def test_derived_blocks_leave_the_render_and_the_navigation_replaces_them(tmp_path):
    site = tmp_path / "site"
    _site(site)
    sources = {s: (site / "posts" / s / "index.md").read_bytes() for s in ("a", "b", "c")}

    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            await _graph(gx, site)
            # A public build needs every public post judged (amendment e09e262b): a judge that
            # relates nothing, so the posts under test are unchanged
            judged = await judge_related(gx, ask=_unrelated)
            assert judged["written"], judged
            pub = await site_build(gx, str(site), "public")
            html = {s: (site / "_site" / "posts" / s / "index.html").read_text() for s in ("a", "b", "c")}
            report = (site / DERIVED_DIR / REPORT_FILE).read_text()
            agent = {"txt": (site / "_site" / "llms.txt").read_text(),
                     "a_md": (site / "_site" / "posts" / "a" / "index.llms.md").read_text()}
            # The source drifts from the graph (a TOC item added, no re-ingest): the fingerprint
            # no longer names the block, and the build fails closed, naming the post
            p = site / "posts" / "a" / "index.md"
            p.write_text(p.read_text().replace("* [Details](#details)", "* [Details](#details)\n* [Extra](#extra)"))
            drift = await site_build(gx, str(site), "public")
            return pub, html, report, drift, agent

    pub, html, report, drift, agent = asyncio.run(go())
    assert pub["ok"], pub
    # The footer's years run from the first publication to the latest revision (b's, asserted today)
    this_year = datetime.now(timezone.utc).year
    derived = dict(pub["derived"])
    assert derived.pop("footer_years") == f"© 2020–{this_year} The Author"
    foot = yaml.safe_load((site / DERIVED_DIR / "site-footer.yml").read_text())["website"]["page-footer"]
    assert foot["right"][0]["text"] == "Code samples licensed under the MIT License"
    assert "licenses vary" in foot["left"][0]["text"]            # c's override differs from the class
    assert derived == {"posts": 3, "series_nav": 2, "collections": 3, "related_stale": 0, "strips": 3,
                       "pitch": 0, "headers": 3, "category_links": 0, "comments_unrendered": 0, "comments_thread": 1,
                       "comments_term": 2, "comments_earlier": 1,
                       "draws_on": 0, "draws_lines": 0, "draws_located": 0, "draws_unplaced": 0,
                       "sources_missing": 0,
                              "pitch_pending": 0, "questions": 1, "related": 0, "series_callout": 2, "hand_toc": 2,
                              "series_nav_line": 1, "chrome_include": 4, "reported": 3, "end_placed": 3,
                       "jsonld": 3, "jsonld_checked": 3}
    # The agent layer (39c51c15 (7), amendment 23a49667): one JSON-LD object per post, as planned
    ld = {s: read_jsonld(h) for s, h in html.items()}
    assert all(len(v) == 1 for v in ld.values())
    (la,), (lb,), (lc,) = ld["a"], ld["b"], ld["c"]
    assert lb["@type"] == "TechArticle" and la["@type"] == lc["@type"] == "BlogPosting"
    assert la["url"] == "https://example.org/posts/a/" and la["author"]["name"] == "The Author"
    assert la["author"]["jobTitle"] == "a byline"   # the site author's role (amendment fe6f0fb7)
    assert la["datePublished"] == "2020-01-01" and lb["dateModified"] == datetime.now(timezone.utc).date().isoformat()
    assert [s["@type"] for s in la["isPartOf"]] == ["CreativeWorkSeries"] and "isPartOf" not in lc
    assert lc["license"] == "https://creativecommons.org/licenses/by/4.0/"
    # Links stay in the markdown layer; llms.txt is the build's, from the graph's structure
    assert pub["agent"]["links_rewritten"] > 0 and "](../b/index.llms.md)" in agent["a_md"]
    # The page's ids carried into its markdown (design b82d2a98), never into its HTML
    assert pub["agent"]["anchors"] > 0 and '## <a id="overview"></a>Overview' in agent["a_md"]
    assert pub["agent"]["fragments_dead"] == 0 and "cjm-anchor" not in html["a"]
    txt = agent["txt"]
    assert txt.startswith("# t\n\n> The test site.\n\n")
    assert all(txt.count(f"https://example.org/posts/{s}/index.llms.md") == 1 for s in ("a", "b", "c"))
    assert "## Notes" in txt and "## Optional" not in txt
    # The sources never change: the blocks leave the render only
    assert all(sources[s] == (site / "posts" / s / "index.md").read_bytes() for s in ("b", "c"))
    a, b, c = html["a"], html["b"], html["c"]
    # every post states its licenses in Quarto's Reuse appendix: the class's, or its own override
    assert "Reuse" in a and "CC BY-NC-SA 4.0</a>" in a and "CC BY 4.0</a>" in c and "MIT License</a>" in c
    assert "This post is part of the following series" not in a + b
    assert 'href="#overview"' not in a and 'href="#overview"' not in c   # the hand TOCs left
    assert "Next:</a>" not in a and "Part B</a>" not in a.split("Part 1 of 2")[0]
    assert "<hr" not in a.split("Part one.")[0]                         # the closing rule left with the TOC
    assert "Part 1 of 2" in a and "Part 2 of 2" in b and "Part " not in c
    assert "In the collection" in a and "In the collection" in c
    assert "real link" in b and "Thanks." in a                          # content stays
    # The chrome includes left (the about-author partial's opening rule with them); the author
    # strip closes every post, the questions line only the tutorial (39c51c15 (5))
    assert "About Me" not in a + b + c and "Questions:" not in b and "Ask below" not in b
    assert "Inside." in c and "derived-drop" not in a + b + c          # a nested include leaves too
    assert all("author-strip" in h and "The Author" in h for h in (a, b, c))
    tail = a[a.index("Thanks."):a.index("author-strip")]
    assert "<hr" not in tail
    # Every post closes on its comments block: b's thread by number with its earlier one linked,
    # a and c by their canonical paths as strict terms; the questions line opens the tutorial's
    assert all("post-comments" in h for h in (a, b, c)) and "utterances" not in a + b + c
    assert "Ask in the comments." in b and "Ask in the comments." not in a + c
    assert b.index("real link") < b.index("author-strip") < b.index("post-comments") < b.index("Ask in the comments.")
    assert '"mapping": "number", "term": "7"' in b and "o/r/discussions/3" in b
    assert '"mapping": "specific", "term": "/posts/a/", "strict": "1"' in a
    # the revision dates b's Updated; a and c keep their sources' own (none)
    assert '<p class="date-modified">' in b and '<p class="date-modified">' not in a + c
    rows = [json.loads(l) for l in report.splitlines()]
    assert {r["input"] for r in rows} == {"posts/a/index.md", "posts/b/index.md", "posts/c/index.md"}
    assert all(d["count"] == 1 for r in rows for d in r["dropped"])
    assert not drift["ok"]
    assert [(e["kind"], e["source"], e.get("role")) for e in drift["errors"]] == [
        ("derived-drop", "posts/a/index.md", "hand_toc")]
