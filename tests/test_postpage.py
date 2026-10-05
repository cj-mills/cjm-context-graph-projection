"""The post page's projected parts (design 39c51c15): the author strip and the end matter."""

from cjm_context_graph_projection.postpage import (display_date, end_plan, header_meta, links_line, load_holder,
                                                   load_reading_guide, load_site_links, load_strip_copy,
                                                   post_licenses, related_posts, render_end, render_related,
                                                   render_reuse, site_footer, site_nav)

COPY = {"byline": "**A** — a byline.", "links": "[About](/about.html)",
        "pitch": "Hire me for {claims}: [how]({href}).", "questions": "Ask below."}
TYPES = {"t": {"kind": "tutorial"}, "n": {"kind": "notes"}, "p": {"kind": "site"}, "w": {"kind": "work"}}

LINKS = ('site-links:\n  - icon: envelope-fill\n    text: Email\n    href: mailto:e@x.org\n'
         '  - icon: github\n    text: GitHub\n    href: https://github.com/x\n')


def test_the_copy_comes_from_the_site_config_and_refuses_when_missing(tmp_path):
    who = 'site-author:\n  name: "N"\n  role: "R"\n'
    (tmp_path / "_quarto.yml").write_text(
        'author-strip:\n  byline: "**{name}** — {role}."\n  links: "L · {links}"\n  pitch: "P {claims} {href}"\n  questions: "Q"\n'
        + who + LINKS)
    # The byline names the author through the site's one statement of them (amendment fe6f0fb7),
    # the links line the site's contact links (design ff0c6338 (6)); the pitch keeps its own
    # placeholders for the render
    assert load_strip_copy(str(tmp_path)) == {"copy": {"byline": "**N** — R.",
                                                       "links": "L · [Email](mailto:e@x.org) · [GitHub](https://github.com/x)",
                                                       "pitch": "P {claims} {href}", "questions": "Q"}, "errors": []}
    (tmp_path / "_quarto.yml").write_text('author-strip:\n  byline: "B"\n  links: ""\n' + who + LINKS)
    err = load_strip_copy(str(tmp_path))["errors"]
    assert err and err[0]["missing"] == ["links", "pitch", "questions"]   # never a fallback text
    # No site author, or a placeholder it does not name, refuses
    (tmp_path / "_quarto.yml").write_text('author-strip:\n  byline: "B"\n  links: "{links}"\n  pitch: "P"\n  questions: "Q"\n'
                                          'site-author:\n  name: "N"\n' + LINKS)
    assert load_strip_copy(str(tmp_path))["errors"][0] == {**load_strip_copy(str(tmp_path))["errors"][0],
                                                           "kind": "site-author", "missing": ["role"]}
    (tmp_path / "_quarto.yml").write_text('author-strip:\n  byline: "{who}"\n  links: "{links}"\n  pitch: "P"\n  questions: "Q"\n'
                                          + who + LINKS)
    assert load_strip_copy(str(tmp_path))["errors"][0]["field"] == "byline"
    # A links line typing its own links (no {links}) is a second copy; another placeholder refuses;
    # no site links refuses
    for line in ('"[Email](mailto:e@x.org)"', '"{links} {who}"'):
        (tmp_path / "_quarto.yml").write_text(f'author-strip:\n  byline: "B"\n  links: {line}\n  pitch: "P"\n  questions: "Q"\n'
                                              + who + LINKS)
        assert load_strip_copy(str(tmp_path))["errors"][0]["field"] == "links"
    (tmp_path / "_quarto.yml").write_text('author-strip:\n  byline: "B"\n  links: "{links}"\n  pitch: "P"\n  questions: "Q"\n' + who)
    assert load_strip_copy(str(tmp_path))["errors"][0]["kind"] == "site-links"


def test_the_site_links_are_stated_once(tmp_path):
    # Design ff0c6338 (6): each link needs its icon, text and href, never a default
    (tmp_path / "_quarto.yml").write_text(LINKS)
    got = load_site_links(str(tmp_path))
    assert got["errors"] == [] and [l["text"] for l in got["links"]] == ["Email", "GitHub"]
    assert links_line(got["links"]) == "[Email](mailto:e@x.org) · [GitHub](https://github.com/x)"
    # the navbar's icons follow the hand-kept pages, each labelled by its text
    assert site_nav(got["links"]) == {"website": {"navbar": {"right": [
        {"icon": "envelope-fill", "href": "mailto:e@x.org", "aria-label": "Email"},
        {"icon": "github", "href": "https://github.com/x", "aria-label": "GitHub"}]}}}
    (tmp_path / "_quarto.yml").write_text("site-links:\n  - icon: github\n    text: GitHub\n")
    assert load_site_links(str(tmp_path))["errors"][0]["missing"] == ["href"]
    (tmp_path / "_quarto.yml").write_text("site-links: []\n")
    assert load_site_links(str(tmp_path))["errors"][0]["kind"] == "site-links"


def test_the_reading_guide_is_stated_once(tmp_path):
    # Design ff0c6338 (4): llms.txt and About read one key; absent is "" (the reader decides)
    who = 'site-author:\n  name: "N"\n  role: "R"\n'
    (tmp_path / "_quarto.yml").write_text(who)
    assert load_reading_guide(str(tmp_path)) == {"text": "", "errors": []}
    (tmp_path / "_quarto.yml").write_text("reading-guide: |\n  Ask  {name}.\n" + who)
    assert load_reading_guide(str(tmp_path)) == {"text": "Ask N.", "errors": []}
    (tmp_path / "_quarto.yml").write_text("reading-guide: '{who}'\n" + who)
    assert load_reading_guide(str(tmp_path))["errors"][0]["kind"] == "reading-guide"


def test_render_end_variants():
    plain = render_end(COPY)
    assert plain.startswith("::: {.author-strip}\n")
    assert "**A** — a byline.  \n[About](/about.html)" in plain and "Hire" not in plain and "Ask" not in plain
    pitched = render_end(COPY, pitch={"claims": "CV, Data", "href": "/work/"},
                         comments="::: {.post-comments}\nAsk below.\n:::\n")
    assert "Hire me for CV, Data: [how](/work/)." in pitched
    assert pitched.index("Hire") < pitched.index("[About]") < pitched.index("::: {.post-comments}")
    # the sources open the end matter, before the related posts (722a8232)
    sourced = render_end(COPY, sources="::: {.post-sources}\n**Source**\n\n- S\n:::\n",
                         related=[{"title": "R", "href": "/r/", "reason": "linked"}])
    assert sourced.index("post-sources") < sourced.index("related-posts") < sourced.index("author-strip")


def test_end_plan_pitches_only_with_a_published_target():
    backing = {"t": ["CV"], "n": []}
    pending = end_plan(COPY, ["t", "n", "p", "w", "x"], TYPES, backing, None)
    assert sorted(pending["ends"]) == ["n", "t", "w"]            # site pages and untyped Notes carry none
    assert pending["counts"] == {"strips": 3, "pitch": 0, "pitch_pending": 1, "questions": 1, "related": 0,
                                 "comments_thread": 0, "comments_term": 0, "comments_earlier": 0}
    assert "Hire" not in "".join(pending["ends"].values())       # no target page: no pitch anywhere
    live = end_plan(COPY, ["t", "n"], TYPES, backing, {"title": "Work with me", "href": "/work/"})
    assert "Hire me for CV: [how](/work/)." in live["ends"]["t"] and "Hire" not in live["ends"]["n"]
    assert live["counts"]["pitch"] == 1 and live["counts"]["pitch_pending"] == 0
    # The comments block closes every post; the questions line opens a tutorial's (39c51c15 (1))
    cfg = {"repo": "o/r", "repo-id": "R", "category": "C", "category-id": "CI", "theme": {"light": "l", "dark": "d"}}
    threads = {"t": {"number": 4, "term": "/t/", "earlier": [2]}, "n": {"number": None, "term": "/n/", "earlier": []}}
    com = end_plan(COPY, ["t", "n"], TYPES, {}, None, comments_config=cfg, page_threads=threads)
    t, n = com["ends"]["t"], com["ends"]["n"]
    assert t.index("[About]") < t.index("::: {.post-comments}\nAsk below.") and "Ask below." not in n
    assert '"term": "4"' in t and "Earlier comments: [#2]" in t and '"term": "/n/", "strict": "1"' in n
    assert {k: com["counts"][k] for k in ("comments_thread", "comments_term", "comments_earlier")} == \
        {"comments_thread": 1, "comments_term": 1, "comments_earlier": 1}


def test_header_meta_kind_dates_and_drafts():
    # an archive tutorial: the label only; its own date-modified stands against an older revision
    arch = header_meta("tutorial", "archive", {"date-modified": "2024-05-01"}, {"revised": "2023-01-01"})
    assert arch == {"set": {"post-kind": "Tutorial"}, "unset": []}
    newer = header_meta("notes", "archive", {}, {"revised": "2026-09-29"})
    assert newer["set"] == {"post-kind": "Notes", "date-modified": "September 29, 2026"}
    # a born post: its date is its publication's; a draft shows none and says so
    pub = header_meta("notes", "born", {"date": "2026-09-10"}, {"published": "2026-10-02"})
    assert pub["set"]["date"] == "October 2, 2026" and pub["unset"] == []
    draft = header_meta("notes", "born", {"date": "2026-09-10"}, {})
    assert draft == {"set": {"post-kind": "Notes · Draft"}, "unset": ["date"]}
    assert display_date("2026-01-05") == "January 5, 2026"


def test_categories_link_into_the_listing_the_site_config_names(tmp_path):
    """A post's categories become links to the category listing filtered to each (amendment of
    0858bbd0): named once in the site config, URI-encoded so a space or an `&` survives the hash;
    no listing named = the categories stay labels; a listing the project does not hold refuses."""
    from cjm_context_graph_projection.postpage import CATEGORY_META, category_links, load_category_listing
    (tmp_path / "_quarto.yml").write_text("project:\n  type: website\n")
    assert load_category_listing(str(tmp_path)) == {"href": "", "errors": []}
    (tmp_path / "_quarto.yml").write_text("category-listing: blog.qmd\n")
    assert load_category_listing(str(tmp_path))["errors"][0]["kind"] == "category-listing"
    # a page this build projects holds it too (the category listing is a Lens page, design ce17606b (3))
    assert load_category_listing(str(tmp_path), projected=["blog.qmd"]) == {"href": "/blog.html", "errors": []}
    (tmp_path / "blog.qmd").write_text("---\ntitle: Blog\n---\n")
    href = load_category_listing(str(tmp_path))["href"]
    assert href == "/blog.html"
    assert category_links(["pytorch", "image classification", "a&b"], href) == [
        {"name": "pytorch", "href": "/blog.html#category=pytorch"},
        {"name": "image classification", "href": "/blog.html#category=image%20classification"},
        {"name": "a&b", "href": "/blog.html#category=a%26b"}]
    assert category_links("solo", href) == [{"name": "solo", "href": "/blog.html#category=solo"}]
    assert category_links(["x"], "") == [] and category_links(None, href) == []
    # The header's categories are the graph's chips, never the front matter's (design ce17606b (2))
    meta = header_meta("tutorial", "archive", {"categories": ["old"]}, {}, href, categories=["PyTorch"])
    assert meta["set"]["categories"] == ["PyTorch"]
    assert meta["set"][CATEGORY_META] == [{"name": "PyTorch", "href": "/blog.html#category=PyTorch"}]
    assert CATEGORY_META not in header_meta("tutorial", "archive", {}, {}, categories=["PyTorch"])["set"]
    bare = header_meta("tutorial", "archive", {"categories": ["old"]}, {}, href)
    assert bare["unset"] == ["categories"] and "categories" not in bare["set"] and CATEGORY_META not in bare["set"]


def test_related_posts_tiers_reasons_and_exclusions():
    cands = {k: {"title": k.upper(), "href": f"/posts/{k}/", "kind": kind, "date": day}
             for k, kind, day in (("me", "tutorial", "2024-01-01"), ("out", "notes", "2020-01-01"),
                                  ("in", "notes", "2021-01-01"), ("mate", "tutorial", "2022-01-01"),
                                  ("next", "tutorial", "2023-01-01"), ("far", "tutorial", "2023-01-01"),
                                  ("topical", "notes", "2022-06-01"), ("thin", "notes", "2022-06-01"),
                                  ("odd", "notes", "2022-06-01"), ("unjudged", "notes", "2022-06-01"))}
    j = lambda score, rel: {"score": score, "relation": rel}
    ctx = {"links": {("me", "out"), ("in", "me"), ("me", "mate")},
           "judged": {("me", "out"): j(1.2, "unrelated"), ("me", "in"): j(2.5, "prerequisite"),
                      ("me", "next"): j(2.8, "follow_up"), ("me", "far"): j(1.9, "same_technique"),
                      ("me", "topical"): j(1.8, "same_subject"), ("me", "thin"): j(1.4, "same_tool"),
                      ("me", "odd"): j(2.0, "unrelated"), ("me", "mate"): j(3.0, "follow_up"),
                      ("far", "me"): j(3.0, "same_technique")},
           "series": {"me": {"s"}, "mate": {"s"}},
           "coverage": {"me": {"teaches_task": ["det"], "teaches_stage": ["training"]},
                        "next": {"teaches_task": ["det"], "teaches_stage": ["export"]},
                        "far": {"teaches_task": ["det"], "teaches_stage": ["deployment"]}},
           "stages": ["training", "export", "deployment"],
           "stage_names": {"export": "Export"}}
    got = related_posts("me", cands, ctx)
    # links first whatever the judge says, ordered by the judged score; then judged pairs by score,
    # an adjacent-stage tutorial keeping its stage reason; the cap (4) drops TOPICAL
    assert [(g["title"], g["reason"]) for g in got] == [
        ("IN", "Links here"), ("OUT", "Linked from this post"), ("NEXT", "Next step: Export"),
        ("FAR", "Same technique")]
    assert all(g["title"] != "MATE" for g in got)    # a series-mate is the navigation's
    every = {g["title"]: g["reason"] for g in related_posts("me", cands, ctx, limit=10)}
    assert every["TOPICAL"] == "Same subject"
    # below the floor, judged unrelated, or never judged: no relation
    assert not {"THIN", "ODD", "UNJUDGED"} & set(every)
    block = render_related(got)
    assert block.startswith("::: {.related-posts}\n**Related**") and "- [OUT](/posts/out/) — Linked from this post" in block
    assert render_related([]) == ""


def test_licenses_resolve_override_then_class_and_fail_closed():
    lic = {"by_subject": {"T": {"content_license": "cc-by-nc-sa-4.0", "code_license": "mit"},
                          "p2": {"content_license": "cc-by-4.0"}, "p3": {"content_license": "wtfpl"}},
           "type_ids": {"archive-notes": "T"}}
    assert post_licenses("p1", "archive-notes", lic)["content"][0] == "CC BY-NC-SA 4.0"
    got = post_licenses("p2", "archive-notes", lic)
    assert got["content"][0] == "CC BY 4.0" and got["code"][0] == "MIT License"   # the override wins
    assert "vocabulary" in post_licenses("p3", "archive-notes", lic)["error"]
    assert "no content_license" in post_licenses("p4", "untyped", lic)["error"]
    assert render_reuse(got).startswith("::: {.appendix}\n## Reuse\n\nText: [CC BY 4.0](")


def test_the_footer_derives_years_and_shared_licenses(tmp_path):
    same = [{"content": ("CC BY-NC-SA 4.0", "u1"), "code": ("MIT License", "u2")}] * 2
    foot = site_footer("A. Holder", [{"published": "2020-03-01", "updated": "2020-03-01"},
                                     {"published": "2023-01-01", "updated": "2025-06-01"}], same)["website"]["page-footer"]
    assert foot["center"] == [{"text": "© 2020–2025 A. Holder", "href": "about.qmd"}]   # never the build's clock
    assert foot["left"] == [{"text": "Content licensed under CC BY-NC-SA 4.0", "href": "u1"}]
    assert foot["right"] == [{"text": "Code samples licensed under the MIT License", "href": "u2"}]
    one = site_footer("A", [{"published": "2024-01-01", "updated": "2024-02-01"}], same)
    assert one["website"]["page-footer"]["center"][0]["text"] == "© 2024 A"
    (tmp_path / "_quarto.yml").write_text("website:\n  title: t\n")
    assert load_holder(str(tmp_path))["errors"] and not load_holder(str(tmp_path))["holder"]
