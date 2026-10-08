"""The site's FEEDS, written by the build after the render (design 0efb5497 (3b) under the web
foundation 941f7f13; build 5ad21874): every listing is projected whole, so Quarto's listing feeds
leave with its listings and the build writes them -- blog.xml (the newest 20, full content) and
each category page's index.xml stay at their URLs with equivalent items.

A feed is derived from nodes (941f7f13), but a FULL-CONTENT item is the post's RENDERED main
content, so the step runs after the render, reading each item's page as Quarto 1.10.18's feed
reader read it (readRenderedContents under kFeedOptions):

- the content is `main.content`'s inner HTML with the title block, every <nav>, every
  aria-hidden element and the code copy buttons removed, `role` attributes stripped, in-page
  anchors (`a[href^="#"]`) unwrapped to their text, every image src made absolute against the
  page's folder, each highlighted code span styled inline from the default highlight theme
  (arrow) and each code block given Quarto's panel background, and inline math replaced by the
  rendered-image URL Quarto used;
- the item title is the page's `h1.title` text; link and guid the page's absolute URL (a
  trailing index.html is its folder);
- items sort newest first by date, an undated one last, equal dates in the order given (the
  listing's), and the newest 20 are kept.

EVERY TIME FIELD IS A FUNCTION OF THE DURABLE SOURCES (ruling 8f6f2343): a day is midnight UTC
(Quarto used the build machine's local midnight), an undated item carries no pubDate and a feed
with no dated item no lastBuildDate (Quarto stamped the build's clock on both).

A listed item whose page did not render refuses its feed (fail closed): nothing is written for
it, and the error row says which."""

import json
import posixpath
import re
import struct
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

FEED_ITEMS = 20   # Quarto's kDefaultItems
# Quarto's feed-image bounds (kMaxWidth / kMaxHeight)
IMAGE_MAX_WIDTH, IMAGE_MAX_HEIGHT = 144, 400
CODE_BLOCK_STYLE = "background: #f1f3f5;"
# The default highlight theme Quarto styles feed code with (kDefaultHighlightStyle = "arrow",
# resolved to its light file), as {class abbreviation: [css declarations]} -- generateCssKeyValues
# over the theme's text-styles in key order; "Normal" abbreviates to "" and so styles nothing
HIGHLIGHT_THEME = "arrow-light"
_ABBREVS = {"Keyword": "kw", "DataType": "dt", "DecVal": "dv", "BaseN": "bn", "Float": "fl", "Char": "ch",
            "String": "st", "Comment": "co", "Other": "ot", "Alert": "al", "Function": "fu",
            "RegionMarker": "re", "Error": "er", "Constant": "cn", "SpecialChar": "sc",
            "VerbatimString": "vs", "SpecialString": "ss", "Import": "im", "Documentation": "do",
            "Annotation": "an", "CommentVar": "cv", "Variable": "va", "ControlFlow": "cf", "Operator": "op",
            "BuiltIn": "bu", "Extension": "ex", "Preprocessor": "pp", "Attribute": "at",
            "Information": "in", "Warning": "wa", "Normal": ""}
_IMAGE_TYPES = {".apng": "image/apng", ".avif": "image/avif", ".gif": "image/gif", ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg", ".jfif": "image/jpeg", ".pjpeg": "image/jpeg", ".pjp": "image/jpeg",
                ".png": "image/png", ".svg": "image/svg+xml", ".webp": "image/webp"}
_RSS_HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<rss  xmlns:atom="http://www.w3.org/2005/Atom" \n'
             '      xmlns:media="http://search.yahoo.com/mrss/" \n'
             '      xmlns:content="http://purl.org/rss/1.0/modules/content/" \n'
             '      xmlns:dc="http://purl.org/dc/elements/1.1/" \n'
             '      version="2.0">\n<channel>\n')
_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}
_TAG_RE = re.compile(r"/tag\{(.*)\}")


def escape(
    s: Any,  # Text for an XML element or attribute
) -> str:  # It escaped as the feed templates escape it (lodash's escape: & < > " ')
    return re.sub(r"[&<>\"']", lambda m: _ESCAPES[m.group(0)], str(s or ""))


def absolute_url(
    site_url: str,  # The site's URL (the config's site-url)
    url: str,       # A site path, relative or site-absolute, or a URL
) -> str:  # The absolute URL; a trailing index.html names its folder
    if url.startswith(("http:", "https:")):
        return url
    base = site_url[:-1] if site_url.endswith("/") else site_url
    path = url[1:] if url.startswith("/") else url
    if path.endswith("/index.html"):
        path = posixpath.dirname(path) + "/"
    elif path == "index.html":
        path = ""
    return f"{base}/{path.replace(chr(92), '/')}"


def day_time(
    day: str,  # An ISO day ("2025-10-14"; "" = none)
) -> Optional[datetime]:  # Its midnight UTC, or None
    try:
        return datetime.strptime(str(day or "")[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def rss_date(
    when: datetime,  # A UTC time
) -> str:  # Its RFC 822 form, as Date.toUTCString writes it ("Tue, 14 Oct 2025 00:00:00 GMT")
    return format_datetime(when, usegmt=True)


def png_size(
    path: Path,  # An image file
) -> Optional[Dict[str, int]]:  # {height, width} for a PNG (Quarto sizes PNGs only), else None
    if path.suffix != ".png" or not path.is_file():
        return None
    head = path.read_bytes()[:24]
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width, height = struct.unpack(">II", head[16:24])
    return {"height": height, "width": width}


def feed_image_size(
    height: int,  # An image's height
    width: int,   # Its width
) -> List[int]:  # [height, width] scaled into the feed-image bounds (feedImageSize)
    hs, ws = IMAGE_MAX_HEIGHT / height, IMAGE_MAX_WIDTH / width
    if hs >= 1 and ws >= 1:
        return [height, width]
    f = min(hs, ws)
    return [_js_round(height * f), _js_round(width * f)]


def _js_round(x: float) -> int:
    """Math.round: halves round up (Python's round is banker's)."""
    import math
    return int(math.floor(x + 0.5))


def highlight_styles(
    theme_dir: Optional[str] = None,  # Quarto's pandoc/highlight-styles dir (None = beside the quarto on PATH)
) -> Dict[str, str]:  # {class abbreviation: inline style}, the declarations joined by newlines
    """Quarto's defaultSyntaxHighlightingClassMap: each text style of the default theme as the
    inline style its code spans carry in a feed (`null` colours kept, as Quarto writes them)."""
    path = Path(theme_dir) / f"{HIGHLIGHT_THEME}.theme" if theme_dir else _quarto_theme()
    if path is None or not path.is_file():
        return {}
    styles = (json.loads(path.read_text(encoding="utf-8")).get("text-styles") or {})
    out: Dict[str, str] = {}
    for name, values in styles.items():
        abbr = _ABBREVS.get(name)
        if abbr is None:
            continue
        lines: List[str] = []
        for attr, v in values.items():
            if attr == "text-color":
                lines.append(f"color: {_js_str(v)};")
            elif attr == "background-color":
                lines.append(f"background-color: {_js_str(v)};")
            elif attr == "bold" and v:
                lines.append("font-weight: bold;")
            elif attr == "italic":
                lines.append("font-style: italic;" if v else "font-style: inherit;")
            elif attr == "underline" and v:
                lines.append("text-decoration: underline;")
        out[abbr] = "\n".join(lines)
    return out


def _js_str(v: Any) -> str:
    return "null" if v is None else str(v)


def _quarto_theme() -> Optional[Path]:
    import shutil
    exe = shutil.which("quarto")
    if not exe:
        return None
    return Path(exe).resolve().parent.parent / "share" / "pandoc" / "highlight-styles" / f"{HIGHLIGHT_THEME}.theme"


def math_image_url(
    math: str,  # Inline math's TeX
) -> str:  # The rendered-image URL Quarto's feed puts in its place (kWebTexUrl)
    return "https://latex.codecogs.com/png.latex?" + quote(math, safe="!*'();/?:@&=+$,#")


def rendered_contents(
    html_path: Path,           # A rendered page
    rel_path: str,             # Its path relative to the output dir
    site_url: str,             # The site's URL
    styles: Dict[str, str],    # highlight_styles' map
) -> Dict[str, Any]:  # {title, authors, contents}: the page's title text, its authors, its feed content (inner HTML)
    """readRenderedContents under kFeedOptions, on the page as rendered. The authors are the
    page's `<meta name="author">` tags: a post's author is directory metadata (the posts tree's
    _metadata.yml), which no Note carries, so the rendered page is where it is stated."""
    from bs4 import BeautifulSoup
    from bs4.dammit import EntitySubstitution
    from bs4.formatter import HTMLFormatter
    doc = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    folder = posixpath.dirname(rel_path)
    authors = [str(m.get("content")) for m in doc.find_all("meta", attrs={"name": "author"}) if m.get("content")]
    main = doc.select_one("main.content")
    title_el = doc.find(id="title-block-header")
    h1 = title_el.select_one("h1.title") if title_el else None
    title = h1.get_text() if h1 else ""
    if title_el:
        title_el.decompose()
    for nav in doc.find_all("nav"):
        nav.decompose()
    for img in doc.find_all("img"):
        src = img.get("src")
        if src:
            if not src.startswith("/") and not src.startswith(("http:", "https:")):
                src = posixpath.normpath(posixpath.join(folder, src))
            img["src"] = absolute_url(site_url, src)
    for sel in ('*[aria-hidden="true"]', "button.code-copy-button"):
        for el in doc.select(sel):
            el.decompose()
    for el in doc.find_all(attrs={"role": True}):
        del el["role"]
    for a in doc.select('a[href^="#"]'):
        a.unwrap()
    for span in doc.select("code span"):
        for cls in span.get("class") or []:
            if cls in styles:
                span["style"] = styles[cls]
                break
    for block in doc.select("div.sourceCode"):
        block["style"] = CODE_BLOCK_STYLE
    for span in doc.select("span.math"):
        tex = span.get_text()
        if len(tex) > 4 and tex.startswith(("\\[", "\\(")):
            tex = tex[2:-2]
        img = doc.new_tag("img", src=math_image_url(tex))
        span.replace_with(img)
    if main is None:
        return {"title": title, "authors": authors, "contents": ""}
    # text and attribute values escaped (& < >), void elements unclosed, as a DOM serializes them
    inner = main.decode_contents(formatter=HTMLFormatter(entity_substitution=EntitySubstitution.substitute_xml,
                                                         void_element_close_prefix=None))
    return {"title": title, "authors": authors, "contents": _TAG_RE.sub(lambda m: "\\qquad{" + m.group(1) + "}", inner)}


def item_image(
    image: Any,   # A post's front-matter image as written ("/images/x.gif", "./images/x.png", "../y.png"; "" = none)
    output: str,  # The post's rendered page, relative to the output dir
) -> str:  # The image as a site path relative to the root ("" = none)
    """A site-absolute image stands; a relative one is relative to the post's folder."""
    img = str(image or "").strip()
    if not img or img.startswith(("http:", "https:")):
        return img
    if img.startswith("/"):
        return img.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(output), img))


def feed_order(
    items: List[Dict[str, Any]],  # A feed's items ({output, date, ...}), in the listing's order
) -> List[Dict[str, Any]]:  # The ones the feed carries: unique by page, newest first, the first FEED_ITEMS
    """prepareItems: an undated item sorts last, equal dates keep the order given."""
    seen, unique = set(), []
    for it in items:
        if it["output"] not in seen:
            seen.add(it["output"])
            unique.append(it)
    def key(it: Dict[str, Any]) -> float:
        t = day_time(it.get("date") or "")
        return -(t.timestamp() * 1000) if t else 1.0
    return sorted(unique, key=key)[:FEED_ITEMS]


def render_feed(
    feed: Dict[str, Any],          # {page, xml, description, items}
    rows: List[Dict[str, Any]],    # feed_order's items, each with its rendered {title, contents}
    site: Dict[str, Any],          # {url, title, description, image}
    generator: str,                # The <generator> text
    image_size: Optional[Dict[str, int]] = None,  # The site image's {height, width} (PNG only)
    item_sizes: Optional[Dict[str, Dict[str, int]]] = None,  # Item image -> its {height, width}
) -> str:  # The feed's XML, in the shape Quarto's feed templates wrote
    url, title = str(site.get("url") or ""), str(site.get("title") or "")
    link = absolute_url(url, feed["page"])
    out = [_RSS_HEAD, f"<title>{escape(title)}</title>\n", f"<link>{link}</link>\n",
           f'<atom:link href="{absolute_url(url, feed["xml"])}" rel="self" type="application/rss+xml"/>\n',
           f"<description>{escape(feed.get('description') or site.get('description') or '')}</description>\n"]
    if site.get("image"):
        out += ["<image>\n", f"<url>{absolute_url(url, str(site['image']))}</url>\n",
                f"<title>{escape(title)}</title>\n", f"<link>{link}</link>\n"]
        if image_size:
            h, w = feed_image_size(image_size["height"], image_size["width"])
            out += [f"<height>{h}</height>\n", f"<width>{w}</width>\n"]
        out.append("</image>")
        out.append("\n")
    out.append(f"<generator>{escape(generator)}</generator>\n")
    dated = [t for t in (day_time(r.get("date") or "") for r in rows) if t]
    if dated:
        out.append(f"<lastBuildDate>{rss_date(max(dated))}</lastBuildDate>\n")
    for r in rows:
        href = absolute_url(url, r["output"])
        out += ["<item>\n", f"  <title>{escape(r['title'])}</title>\n"]
        out += [f"  <dc:creator>{escape(a)}</dc:creator>\n" for a in r.get("authors") or []]
        body = str(r["contents"]).replace("]]>", "]]]]><![CDATA[>")
        out += [f"  <link>{href}</link>\n", f"  <description><![CDATA[ {body} ]]></description>\n"]
        out += [f"  <category>{escape(c)}</category>\n" for c in r.get("categories") or []]
        out.append(f"  <guid>{href}</guid>\n")
        t = day_time(r.get("date") or "")
        if t:
            out.append(f"  <pubDate>{rss_date(t)}</pubDate>\n")
        if r.get("image"):
            img = str(r["image"])
            kind = _IMAGE_TYPES.get(posixpath.splitext(img)[1])
            size = (item_sizes or {}).get(img)
            attrs = (f' type="{kind}"' if kind else "")
            if size:
                h, w = feed_image_size(size["height"], size["width"])
                attrs += f' height="{h}" width="{w}"'
            out.append(f'  <media:content url="{absolute_url(url, img)}" medium="image"{attrs}/>\n')
        out.append("</item>\n")
    out.append("</channel>\n</rss>\n")
    return "".join(out)


def write_feeds(
    output_dir: str,              # The profile's output dir (the rendered site)
    feeds: List[Dict[str, Any]],  # One per feed: {page, xml, description, items}
    site: Dict[str, Any],         # {url, title, description, image} -- the site config's website keys (image site-relative, "" = none)
    generator: str,               # The <generator> text (e.g. "cjm-context-graph-projection 0.0.78")
    *,
    source_root: Optional[str] = None,  # The site project root: an image's size is read from its SOURCE, as Quarto read it (None = the output dir)
    theme_dir: Optional[str] = None,    # Quarto's highlight-styles dir (None = beside the quarto on PATH; none found = an error row, nothing written)
) -> Dict[str, Any]:  # {written, unchanged, items, errors}
    """Write every feed whose text changed. An item is {output, date, categories, image, authors?}:
    its rendered page relative to the output dir, its ISO day, its chips, its front-matter image as
    written (site-absolute, or relative to the post's folder), and optionally its authors (absent =
    the page's author meta tags). A feed one of whose items did not render is not written (an
    error row)."""
    out = Path(output_dir)
    sizes_root = Path(source_root) if source_root else out
    styles = highlight_styles(theme_dir)
    cache: Dict[str, Dict[str, Any]] = {}
    rep: Dict[str, Any] = {"written": [], "unchanged": 0, "items": 0, "errors": []}
    url = str(site.get("url") or "")
    if not url:
        rep["errors"].append({"kind": "feed-site-url", "why": "the site config names no site-url; a feed's links would be relative"})
        return rep
    if not styles:   # fail closed: a feed written without the theme silently loses Quarto's code styles
        rep["errors"].append({"kind": "feed-highlight-theme",
                              "why": f"no {HIGHLIGHT_THEME} highlight theme (theme_dir, or beside the quarto on PATH); "
                                     "no feed is written"})
        return rep
    for feed in feeds:
        rows: List[Dict[str, Any]] = []
        missing = []
        for it in feed_order(list(feed.get("items") or [])):
            page = out / it["output"]
            if not page.is_file():
                missing.append(it["output"])
                continue
            if it["output"] not in cache:
                cache[it["output"]] = rendered_contents(page, it["output"], url, styles)
            got = cache[it["output"]]
            rows.append({**it, "title": got["title"], "contents": got["contents"],
                         "authors": it["authors"] if it.get("authors") is not None else got["authors"],
                         "image": item_image(it.get("image"), it["output"])})
        if missing:
            rep["errors"].append({"kind": "feed-item-missing", "feed": feed["xml"], "items": missing,
                                  "why": "a feed lists a page that did not render; the feed is not written"})
            continue
        image_size = png_size(sizes_root / str(site["image"]).lstrip("/")) if site.get("image") else None
        item_sizes = {str(r["image"]): s for r in rows if r.get("image")
                      for s in [png_size(sizes_root / str(r["image"]))] if s}
        text = render_feed(feed, rows, site, generator, image_size, item_sizes)
        dest = out / feed["xml"]
        rep["items"] += len(rows)
        if dest.is_file() and dest.read_text(encoding="utf-8") == text:
            rep["unchanged"] += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        rep["written"].append(feed["xml"])
    return rep
