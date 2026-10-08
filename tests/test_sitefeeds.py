"""The site's feeds, written by the build after the render (design 0efb5497 (3b); build 5ad21874):
Quarto 1.10's feed reader reproduced on the rendered page, midnight-UTC days, the newest 20,
a missing page or a missing highlight theme refusing its feed, a file written only when its text
changed."""

import json
import struct
import zlib

from cjm_context_graph_projection.sitefeeds import (HIGHLIGHT_THEME, absolute_url, feed_image_size, feed_order,
                                                    item_image, math_image_url, write_feeds)

SITE = {"url": "https://example.com", "title": "Ex & Co", "description": "The site's own words.",
        "image": "images/logo.png"}

PAGE = """<!DOCTYPE html><html><head><meta name="author" content="A. Author"></head><body>
<nav id="TOC">toc</nav>
<main class="content" id="quarto-document-content">
<header id="title-block-header"><h1 class="title">Hello <em>world</em></h1></header>
<section id="s1"><h2 class="anchored" data-anchor-id="s1">S1<a class="anchorjs-link" aria-hidden="true" href="#s1">#</a></h2>
<p>See <a href="#s1">the section</a> and <a href="../other/">another</a>.</p>
<p><img src="images/pic.png"> <img src="/images/abs.png"> <img src="https://cdn.example.org/x.png"></p>
<div class="sourceCode" id="cb1"><pre class="sourceCode python"><code class="sourceCode python"><span id="cb1-1"><span class="im">import</span> x <span class="co"># &lt;tag&gt;</span></span></code></pre><button class="code-copy-button" title="Copy">c</button></div>
<div role="note" class="callout">note</div>
<p>Math <span class="math inline">\\(a+b\\)</span>.</p>
<nav class="page-nav">prev / next</nav>
</section>
</main></body></html>"""


def _png(path, w, h):
    raw = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw + struct.pack(">I", zlib.crc32(raw[12:]) & 0xFFFFFFFF))


def _site(tmp_path, n=1):
    for i in range(n):
        page = tmp_path / "posts" / f"p{i}" / "index.html"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(PAGE)
    _png(tmp_path / "images" / "logo.png", 300, 300)
    return tmp_path


def _theme(tmp_path):
    """The highlight theme a feed's code spans are styled from -- the one style the tests read, so a
    test never depends on the machine's Quarto (CI has none)."""
    d = tmp_path / "highlight-styles"
    d.mkdir(exist_ok=True)
    (d / f"{HIGHLIGHT_THEME}.theme").write_text(json.dumps({"text-styles": {"Comment": {
        "text-color": "#5E5E5E", "background-color": None, "bold": False, "italic": False, "underline": False}}}))
    return str(d)


def _feed(items, xml="blog.xml", page="blog.html", description=""):
    return {"page": page, "xml": xml, "description": description, "items": items}


def test_the_feed_reader_reproduces_quartos_transforms(tmp_path):
    site = _site(tmp_path)
    rep = write_feeds(str(site), [_feed([{"output": "posts/p0/index.html", "date": "2025-10-14",
                                          "categories": ["A & B"], "image": "./images/pic.png"}])],
                      SITE, "gen", theme_dir=_theme(tmp_path))
    assert rep["errors"] == [] and rep["written"] == ["blog.xml"] and rep["items"] == 1
    xml = (site / "blog.xml").read_text()
    # the channel: the site's title escaped, the page's folder link, the scaled site image, the fallback description
    assert "<title>Ex &amp; Co</title>" in xml and "<link>https://example.com/blog.html</link>" in xml
    assert '<atom:link href="https://example.com/blog.xml"' in xml
    assert "<description>The site&#39;s own words.</description>" in xml
    assert "<height>144</height>\n<width>144</width>" in xml
    assert "<lastBuildDate>Tue, 14 Oct 2025 00:00:00 GMT</lastBuildDate>" in xml
    # the item: the title block's text, the page's author meta, its folder URL, a midnight-UTC day
    assert "<title>Hello world</title>" in xml and "<dc:creator>A. Author</dc:creator>" in xml
    assert "<link>https://example.com/posts/p0/</link>" in xml and "<guid>https://example.com/posts/p0/</guid>" in xml
    assert "<category>A &amp; B</category>" in xml
    assert "<pubDate>Tue, 14 Oct 2025 00:00:00 GMT</pubDate>" in xml
    # a relative image resolves against the post's folder
    assert '<media:content url="https://example.com/posts/p0/images/pic.png" medium="image" type="image/png"/>' in xml
    body = xml.split("<![CDATA[ ", 1)[1].split(" ]]>", 1)[0]
    assert "title-block-header" not in body and "<nav" not in body and "aria-hidden" not in body
    assert "code-copy-button" not in body and 'role="note"' not in body
    assert "See the section and" in body and 'href="../other/"' in body   # in-page anchors unwrapped, others kept
    assert 'src="https://example.com/posts/p0/images/pic.png"' in body
    assert 'src="https://example.com/images/abs.png"' in body and 'src="https://cdn.example.org/x.png"' in body
    assert '<div class="sourceCode" id="cb1" style="background: #f1f3f5;">' in body
    assert '<span class="co" style="color: #5E5E5E;\nbackground-color: null;\nfont-style: inherit;"># &lt;tag&gt;</span>' in body
    assert f'<img src="{math_image_url("a+b")}">' in body and 'span class="math' not in body


def test_items_run_newest_first_and_the_feed_keeps_twenty(tmp_path):
    site = _site(tmp_path, 23)
    items = [{"output": f"posts/p{i}/index.html", "date": f"2024-01-{i + 1:02d}", "categories": []} for i in range(22)]
    items.append({"output": "posts/p22/index.html", "date": "", "categories": []})   # undated: last, cut
    items.append(dict(items[0]))   # a duplicate page counts once
    got = feed_order(items)
    assert len(got) == 20 and got[0]["output"] == "posts/p21/index.html" and got[-1]["output"] == "posts/p2/index.html"
    # equal dates keep the order given (the listing's)
    tie = [{"output": "b", "date": "2024-01-01"}, {"output": "a", "date": "2024-01-01"}]
    assert [i["output"] for i in feed_order(tie)] == ["b", "a"]
    write_feeds(str(site), [_feed(items)], SITE, "gen", theme_dir=_theme(tmp_path))
    xml = (site / "blog.xml").read_text()
    assert xml.count("<item>") == 20 and "<lastBuildDate>Mon, 22 Jan 2024 00:00:00 GMT</lastBuildDate>" in xml


def test_an_undated_feed_carries_no_build_clock(tmp_path):
    site = _site(tmp_path)
    write_feeds(str(site), [_feed([{"output": "posts/p0/index.html", "date": "", "categories": []}])], SITE, "gen", theme_dir=_theme(tmp_path))
    xml = (site / "blog.xml").read_text()
    assert "<lastBuildDate>" not in xml and "<pubDate>" not in xml


def test_a_page_that_did_not_render_refuses_its_feed(tmp_path):
    site = _site(tmp_path)
    rep = write_feeds(str(site), [_feed([{"output": "posts/p0/index.html", "date": "2024-01-01", "categories": []},
                                         {"output": "posts/gone/index.html", "date": "2024-01-02", "categories": []}]),
                                  _feed([{"output": "posts/p0/index.html", "date": "2024-01-01", "categories": []}],
                                        xml="categories/a/index.xml", page="categories/a/index.html", description="A.")],
                      SITE, "gen", theme_dir=_theme(tmp_path))
    assert [(e["kind"], e["feed"], e["items"]) for e in rep["errors"]] == [
        ("feed-item-missing", "blog.xml", ["posts/gone/index.html"])]
    assert not (site / "blog.xml").exists() and rep["written"] == ["categories/a/index.xml"]
    xml = (site / "categories/a/index.xml").read_text()
    assert "<link>https://example.com/categories/a/</link>" in xml and "<description>A.</description>" in xml


def test_a_feed_is_written_only_when_its_text_changed(tmp_path):
    site = _site(tmp_path)
    feed = _feed([{"output": "posts/p0/index.html", "date": "2024-01-01", "categories": [], "authors": ["Given"]}])
    assert write_feeds(str(site), [feed], SITE, "gen", theme_dir=_theme(tmp_path))["written"] == ["blog.xml"]
    assert "<dc:creator>Given</dc:creator>" in (site / "blog.xml").read_text()   # supplied authors stand
    again = write_feeds(str(site), [feed], SITE, "gen", theme_dir=_theme(tmp_path))
    assert again["written"] == [] and again["unchanged"] == 1
    assert write_feeds(str(site), [feed], SITE, "gen2", theme_dir=_theme(tmp_path))["written"] == ["blog.xml"]


def test_urls_images_and_sizes_as_quarto_derives_them(tmp_path):
    assert absolute_url("https://x.com/", "/a/index.html") == "https://x.com/a/"
    assert absolute_url("https://x.com", "index.html") == "https://x.com/"
    assert absolute_url("https://x.com", "https://y.org/z") == "https://y.org/z"
    assert item_image("/images/empty.gif", "posts/a/b/index.html") == "images/empty.gif"
    assert item_image("../social-media/cover.png", "posts/a/b/index.html") == "posts/a/social-media/cover.png"
    assert item_image("", "posts/a/index.html") == ""
    assert feed_image_size(300, 300) == [144, 144] and feed_image_size(100, 100) == [100, 100]
    assert feed_image_size(1013, 1800) == [81, 144]
    _png(tmp_path / "images" / "logo.png", 300, 300)
    write_feeds(str(tmp_path), [], {**SITE, "url": ""}, "gen")   # no site url: nothing, an error row
    assert write_feeds(str(tmp_path), [], {**SITE, "url": ""}, "gen")["errors"][0]["kind"] == "feed-site-url"


def test_no_highlight_theme_refuses_every_feed(tmp_path):
    site = _site(tmp_path)
    rep = write_feeds(str(site), [_feed([{"output": "posts/p0/index.html", "date": "2024-01-01", "categories": []}])],
                      SITE, "gen", theme_dir=str(tmp_path / "nowhere"))
    assert [e["kind"] for e in rep["errors"]] == ["feed-highlight-theme"] and rep["written"] == []
    assert not (site / "blog.xml").exists()
