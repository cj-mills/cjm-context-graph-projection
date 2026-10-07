"""The site's PROJECTED LISTING: one component for every listing (design 0efb5497 + amendment
e66296bd; build 5ad21874 under the redesign build 8079ae0f and the ruling 62f4c8b5 that Quarto is
a replaceable foundation). The Blog, the category pages, the series pages, the logs index and log
pages and the Tutorials page's learning paths are markup the build writes into each page's body;
Quarto renders only the page around it, and no page names a Quarto listing.

- EVERY ITEM IS IN THE HTML (0efb5497 (3c)): the date above the title (e66296bd (3)), the title,
  the chips, the description -- the author's markdown, its links rebased onto the page (the
  Tutorials page's one rebaser) -- and data attributes carrying the item's categories, date and
  series position. Crawlers and the markdown layer read the whole listing; the filter, the order,
  the search and the pagination only hide and reorder.
- THE ORDER is the listing's own: a Lens's view sort applied here (`sort_rows`, the one sorter the
  home page reads an index's hubs through), a series' authored order. The first order button is
  that order, the second its reverse (Newest first / Oldest first; on a series Reading order /
  Reversed, e66296bd (4)).
- THE FILTER'S DATA is derived per listing (e66296bd (1), a62f2499 (5)): the kinds in chip order,
  each kind's categories those the listing's items carry, in chip order, a category page's own
  left out (every item carries it, so it filters nothing). It rides on the listing as JSON for the
  site script, which counts as the filters change.
- A CHIP is the one encoder's link (postpage.category_links): its category's page where one
  exists, else the category listing filtered to it (a62f2499 (7)). On the category listing the
  script toggles a chip in place; its href stays the listing filtered to it, so a crawler and a new
  tab still follow it. With no category listing named a chip is a label.
- THE SITE SCRIPT is generated beside the pages (the theme's fonts' precedent: a git-ignored build
  output under a public path, never `_derived/`): the collapsible panel with the kinds as a column
  of tabs, multi-select (any within a kind, every kind selected from), click-again deselect, the
  bar above the list, the order toggle, live search, pagination with the first and last page
  always shown. The URL contract (0efb5497 (3a)): `#category=<URI-encoded name>` opens the
  listing filtered to that one category, and several categories, the search, the order and the
  page extend the same hash, so a filtered view is a shareable URL and back / forward step
  through it.
- EVERY ELEMENT TAKES A KIT ROLE (898c81d6): chips `kit-chip`, buttons `kit-button`, the panel's
  links `kit-ghost`, the kinds `kit-tab`, the order `kit-seg`, the search Bootstrap's form control;
  their colours are the design system's web rules, the layout the site stylesheet's.

Every value here is a function of its arguments: the page planners read the graph, this module
writes the markup."""

import html
import json
import posixpath
import subprocess
from datetime import date
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from .categorypages import KIND_HEADINGS

SCRIPT_FILE = "js/listing.js"   # The site script, relative to the project root (git-ignored output)
PAGE_SIZE = 25                  # Items per page (Quarto's listing page size, kept)
CHIP_LIMIT = 12                 # A kind with more categories shows its largest this many plus any selected (e66296bd (1))
# What a listing counts, singular and plural, by the kind of page it sits on
NOUNS = {"posts": ["post", "posts"], "parts": ["part", "parts"], "entries": ["entry", "entries"],
         "paths": ["learning path", "learning paths"]}
# The order toggle's two labels: a dated listing's, and a series' (e66296bd (4))
ORDERS = {"dated": ["Newest first", "Oldest first"], "numbered": ["Reading order", "Reversed"]}
# Every word the script shows, stated once here and baked into the generated script
WORDS = {
    "search": "Search", "search_hint": "Titles and descriptions", "order": "Order",
    "categories": "Categories", "show": "Show", "hide": "Hide", "kinds": "Kinds of category",
    "show_all": "Show all {n}", "show_fewer": "Show fewer", "selected": "{n} selected",
    "count": "{n} {noun}", "count_of": "{n} of {total} {noun}", "clear_all": "Clear all",
    "remove": "Remove the filter {name}", "clear_search": "Clear the search",
    "empty": "Nothing matches every filter.", "clear_filters": "Clear the filters",
    "pages": "Pages", "previous": "Previous", "next": "Next", "page": "Page {n}",
    "range": "{a}–{b} of {n}",
}
# A planned row's value for each sort key a listing names
SORT_KEYS = {"title": "title", "date": "date", "date-modified": "updated"}


def output_href(
    src: str,       # A listed source, relative to the project root ("posts/x/index.ipynb")
    page_src: str,  # The listing page's source, relative to the root
) -> str:  # The rendered page's href relative to the listing page ("../posts/x/index.html")
    out = posixpath.splitext(src)[0] + ".html"
    return posixpath.relpath(out, posixpath.dirname(page_src) or ".")


def date_text(
    day: str,  # An ISO day ("2025-10-14"; "" = none)
) -> str:  # As the listing shows it ("Oct 14, 2025"; "" for none)
    if not day:
        return ""
    d = date.fromisoformat(day)
    return f"{d:%b} {d.day}, {d.year}"


def sort_rows(
    rows: List[Dict[str, Any]],  # Rows carrying title / date / updated (a missing value sorts last)
    terms: List[str],            # A listing's sort terms ("date desc", "date-modified desc", "title")
) -> Dict[str, Any]:  # {rows} | {error}
    """The rows in the order a listing shows them, each term applied as a stable sort from the last
    to the first; a row with no value for a key follows the rows that have one. A key the build
    plans no value for refuses."""
    from .sitepages import parse_date
    out = list(rows)
    for term in reversed(terms):
        field, _, direction = term.partition(" ")
        key = SORT_KEYS.get(field)
        if key is None:
            return {"error": f"a listing sorts by {field!r}, which the build plans no value for"}

        def value(r: Dict[str, Any]) -> Any:
            v = r.get(key)
            if key == "title":
                return str(v).casefold() if v else None
            return parse_date(v) if v else None
        present = [r for r in out if value(r) is not None]
        out = sorted(present, key=value, reverse=direction == "desc") + [r for r in out if value(r) is None]
    return {"rows": out}


def note_item(
    note: Any,                # A listed Note node
    src: str,                 # Its source, relative to the project root
    page_src: str,            # The listing page's source
    categories: List[str],    # Its chips (the graph's, categories.load_post_categories)
) -> Dict[str, Any]:  # {href, title, description, date, updated, categories}
    """A post as a listing shows it: what its page states (site.stated, the one reader)."""
    from . import factlayer as F
    from .site import stated
    from .sitepages import member_updated, parse_date
    born = parse_date((F.prop(note, "metadata") or {}).get("date"))
    upd = member_updated(note)
    href = output_href(src, page_src)
    return {"source": src, "href": href, "title": stated(note, "title") or str(F.prop(note, "slug") or src),
            "description": stated(note, "description"), "date": born.isoformat() if born else "",
            "updated": upd.isoformat() if upd else "", "categories": list(categories)}


def page_item(
    page: Dict[str, Any],  # A planned collection page (a Series page): {source, title, description?, date?, updated, categories}
    page_src: str,         # The listing page's source
) -> Dict[str, Any]:  # {href, title, description, date, updated, categories}
    """A collection page as a listing shows it (the logs index's series, the learning paths): its
    own date where its node states one, else its newest member's."""
    from .sitepages import parse_date
    born = parse_date(page.get("date")) if page.get("date") else None
    upd = str(page.get("updated") or "")
    return {"source": page["source"], "href": output_href(page["source"], page_src),
            "title": str(page.get("title") or ""),
            "description": str(page.get("description") or ""),
            "date": born.isoformat() if born else upd, "updated": upd,
            "categories": list(page.get("categories") or [])}


def listing_kinds(
    items: List[Dict[str, Any]],  # The listing's items
    kinds: Dict[str, str],        # Chip -> its kind (categories.load_post_categories' kinds)
    rank: Dict[str, int],         # Chip -> its place in the chip order
    exclude: Optional[List[str]] = None,  # Categories left out (a category page's own)
) -> List[Dict[str, Any]]:  # [{key, label, cats}] in chip order; a kind with no category left out
    """The filter's kinds (e66296bd (1)): only the categories this listing's items carry, so a
    small listing offers no dead filters."""
    carried = {c for it in items for c in it["categories"]} - set(exclude or [])
    out = []
    for kind, label in KIND_HEADINGS.items():
        cats = sorted((c for c in carried if kinds.get(c) == kind), key=lambda c: rank.get(c, len(rank)))
        if cats:
            out.append({"key": kind, "label": label, "cats": cats})
    return out


def chip_hrefs(
    categories: List[str],    # Every category the listing's items carry
    listing_href: str,        # The category listing's page path ("" = none)
    pages: Optional[Dict[str, str]] = None,  # chip -> its category page's path
    in_place: bool = False,   # The page IS the category listing: every chip its own filtered view
) -> Dict[str, str]:  # chip -> href (a chip with nowhere to link absent: a label)
    """Every chip's href from the one encoder (postpage.category_links); on the category listing a
    chip names the listing filtered to it, which the script toggles in place."""
    from .postpage import category_links
    if in_place:
        return {c: f"{listing_href}#category={quote(c, safe='')}" for c in categories} if listing_href else {}
    return {c["name"]: c["href"] for c in category_links(categories, listing_href, pages)}


def _attr(v: Any) -> str:
    return html.escape(str(v), quote=True)


_TITLES: Dict[str, str] = {}   # title -> its HTML, rendered once per build process


def title_html(
    titles: List[str],  # Titles as the pages state them (the author's inline markdown)
) -> Dict[str, str]:  # title -> its HTML as Pandoc renders it
    """A title is the author's markdown: Quarto rendered a listed title through Pandoc (its smart
    quotes, dashes and emphasis), so the build renders it through the same Pandoc (`quarto pandoc`),
    every uncached title in one call. A title Pandoc reads as anything but one paragraph (a leading
    `#` or `1.`) is escaped as written."""
    todo = sorted({t for t in titles if t and t not in _TITLES and "\n" not in t})
    if todo:
        r = subprocess.run(["quarto", "pandoc", "--from", "markdown", "--to", "html", "--wrap=none"],
                           input="\n\n".join(todo) + "\n", capture_output=True, text=True, check=True)
        paras = [ln for ln in r.stdout.splitlines() if ln.strip()]
        if len(paras) != len(todo):   # a title read as a block: one call each
            paras = [subprocess.run(["quarto", "pandoc", "--from", "markdown", "--to", "html", "--wrap=none"],
                                    input=t + "\n", capture_output=True, text=True, check=True).stdout.strip()
                     for t in todo]
        for t, p in zip(todo, paras):
            _TITLES[t] = p[3:-4] if p.startswith("<p>") and p.endswith("</p>") and "\n" not in p else html.escape(t)
    return {t: _TITLES.get(t, html.escape(t)) for t in titles}


def render_listing(
    items: List[Dict[str, Any]],   # The items in the listing's own order
    page_src: str,                 # The listing page's source (where the script is named from)
    *,
    kinds: List[Dict[str, Any]],   # listing_kinds' result
    hrefs: Dict[str, str],         # chip_hrefs' result
    in_place: bool = False,        # Chips toggle the filter in place (the category listing)
    numbered: bool = False,        # A series: each item's part number, Reading order / Reversed
    noun: str = "posts",           # A NOUNS key
    titles: Optional[Dict[str, str]] = None,  # title -> its HTML (title_html; None = each escaped as written)
) -> str:  # The listing as page markup (markdown with raw HTML), the site script named last
    """The listing's markup: a container carrying the filter's data, an ordered list of the items,
    and the site script. Pure: every value comes from the arguments."""
    from .tutorialspage import _md_inline
    cfg = {"kinds": kinds, "noun": NOUNS[noun], "orders": ORDERS["numbered" if numbered else "dated"],
           "chips": "filter" if in_place else "link", "pageSize": PAGE_SIZE, "chipLimit": CHIP_LIMIT}
    out = ["```{=html}",
           f'<div class="site-listing" data-listing="{_attr(json.dumps(cfg, ensure_ascii=False))}">',
           '<ol class="listing-items">', "```", ""]
    for n, it in enumerate(items, 1):
        day = date_text(it.get("date") or "")
        meta = f"Part {n}" + (f" · {day}" if day else "") if numbered else day
        attrs = [f'data-date="{_attr(it.get("date") or "")}"', f'data-position="{n}"',
                 f'data-categories="{_attr(json.dumps(it["categories"], ensure_ascii=False))}"']
        chips = []
        for c in it["categories"]:
            if c in hrefs:
                chips.append(f'<a class="listing-category kit-chip" href="{_attr(hrefs[c])}">{html.escape(c)}</a>')
            else:
                chips.append(f'<span class="listing-category kit-chip">{html.escape(c)}</span>')
        block = ["```{=html}", f'<li class="listing-item" {" ".join(attrs)}>']
        if meta:
            block.append(f'<p class="listing-date">{html.escape(meta)}</p>')
        block.append(f'<h3 class="no-anchor listing-title"><a href="{_attr(it["href"])}">'
                     f'{(titles or {}).get(it["title"]) or html.escape(it["title"])}</a></h3>')
        if chips:
            block.append('<div class="listing-categories">' + " ".join(chips) + "</div>")
        desc = _md_inline(it.get("description") or "", it["href"])
        if desc:
            out += block + ['<div class="listing-description">', "```", "", desc, "",
                            "```{=html}", "</div>", "</li>", "```", ""]
        else:
            out += block + ["</li>", "```", ""]
    script = posixpath.relpath(SCRIPT_FILE, posixpath.dirname(page_src) or ".")
    out += ["```{=html}", "</ol>", "</div>", f'<script src="{_attr(script)}" defer></script>', "```"]
    return "\n".join(out) + "\n"


def load_site_title(
    website_root: str,  # The site project root
) -> str:  # The site config's website title ("" = none)
    from pathlib import Path
    import yaml
    try:
        cfg = yaml.safe_load((Path(website_root) / "_quarto.yml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return ""
    return str((cfg.get("website") or {}).get("title") or "")


def feed_plan(
    page_src: str,     # The listing page's source, relative to the root
    description: str,  # The page's own description ("" = the site's)
    posts: List[Any],  # (Note, source relative to the root, its chips) for every post the page lists
) -> Dict[str, Any]:  # {page, xml, description, items} -- sitefeeds.write_feeds' feed
    """A listing's feed (design 0efb5497 (3b)): the page's output and the feed beside it (the page's
    stem with .xml, Quarto's name), and every listed post with its day, its chips and the image its
    front matter states; the writer keeps the newest twenty after the render."""
    from .site import stated
    from .sitepages import parse_date
    from . import factlayer as F
    stem = posixpath.splitext(page_src)[0]
    items = []
    for note, src, cats in posts:
        born = parse_date((F.prop(note, "metadata") or {}).get("date"))
        items.append({"output": posixpath.splitext(src)[0] + ".html", "date": born.isoformat() if born else "",
                      "categories": list(cats), "image": stated(note, "image")})
    return {"page": stem + ".html", "xml": stem + ".xml", "description": description, "items": items}


def feed_header(
    xml: str,   # The feed's file name beside the page ("blog.xml", "index.xml")
    title: str, # The feed's title (the site title)
) -> Dict[str, Any]:  # Front matter naming the feed in the page head, as Quarto's listing did
    return {"include-in-header": {"text": f'<link rel="alternate" type="application/rss+xml" '
                                          f'title="{_attr(title)}" href="{_attr(xml)}" data-external="1">'}}


def check_listing_chips(
    output_dir: str,     # The profile's output dir
    sources: List[str],  # The projected pages' sources (relative)
) -> List[Dict[str, Any]]:  # Error rows: a chip the build planned no link for
    """After the render, with a category listing named: every chip on a projected page is a link
    (a category with no planned href renders as a label span) -- fails closed."""
    from pathlib import Path
    out = Path(output_dir)
    errors = []
    for s in sources:
        page = out / (s[:-len(".qmd")] + ".html")
        n = page.read_text(encoding="utf-8").count('<span class="listing-category') if page.exists() else 0
        if n:
            errors.append({"kind": "chip-unlinked", "source": s, "chips": n,
                           "why": "a listing chip the build planned no category link for"})
    return errors


def listing_script() -> str:
    """The site script, its words baked in from WORDS."""
    return LISTING_SCRIPT.replace("__WORDS__", json.dumps(WORDS, ensure_ascii=False, sort_keys=True))


LISTING_SCRIPT = r'''// GENERATED by site-build from the notes graph (design 0efb5497, amendment e66296bd): do not edit, it is overwritten.
// The projected listing's behaviour: the category filter, the bar, the order, the search and the
// pagination over the items the page already holds, and the URL hash that names the view.
(function () {
  "use strict";
  var WORDS = __WORDS__;

  function fill(text, vals) {
    return text.replace(/\{(\w+)\}/g, function (m, k) { return k in vals ? String(vals[k]) : m; });
  }

  function make(tag, attrs, kids) {
    var e = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k === "text") e.textContent = v;
      else if (k.slice(0, 2) === "on") e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? "" : String(v));
    });
    (kids || []).forEach(function (c) { if (c) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }

  // The hash: category=<name> (repeated), q=<search>, order=reversed, page=<n>. A name is
  // URI-encoded; Quarto may hand a fragment over already decoded, so decoding is tolerant and the
  // parts split only before a key this script writes.
  function parseHash(hash) {
    var st = { sel: [], q: "", order: 0, page: 1 };
    var h = (hash || "").replace(/^#/, "");
    if (!h) return st;
    h.split(/&(?=(?:category|q|order|page)=)/).forEach(function (part) {
      var i = part.indexOf("=");
      if (i < 0) return;
      var k = part.slice(0, i), v = part.slice(i + 1);
      try { v = decodeURIComponent(v); } catch (e) { /* already decoded */ }
      if (k === "category" && v && st.sel.indexOf(v) < 0) st.sel.push(v);
      else if (k === "q") st.q = v;
      else if (k === "order") st.order = v === "reversed" ? 1 : 0;
      else if (k === "page") st.page = Math.max(1, parseInt(v, 10) || 1);
    });
    return st;
  }

  function writeHash(st) {
    var parts = st.sel.map(function (c) { return "category=" + encodeURIComponent(c); });
    if (st.q.trim()) parts.push("q=" + encodeURIComponent(st.q.trim()));
    if (st.order) parts.push("order=reversed");
    if (st.page > 1) parts.push("page=" + st.page);
    return parts.length ? "#" + parts.join("&") : "";
  }

  function init(root, uid) {
    var cfg = JSON.parse(root.getAttribute("data-listing"));
    var list = root.querySelector("ol.listing-items");
    var items = Array.prototype.map.call(list.children, function (li, i) {
      var title = li.querySelector(".listing-title"), desc = li.querySelector(".listing-description");
      return { li: li, i: i, cats: JSON.parse(li.getAttribute("data-categories") || "[]"),
               text: ((title ? title.textContent : "") + " " + (desc ? desc.textContent : "")).toLowerCase() };
    });
    var kindOf = {};
    cfg.kinds.forEach(function (k) { k.cats.forEach(function (c) { kindOf[c] = k.key; }); });
    var st = { sel: [], q: "", order: 0, page: 1, tab: cfg.kinds.length ? cfg.kinds[0].key : null,
               open: true, more: {} };

    function fromHash() {
      var h = parseHash(location.hash);
      st.sel = h.sel.filter(function (c) { return kindOf[c]; });
      st.q = h.q; st.order = h.order; st.page = h.page;
      if (st.sel.length && !st.sel.some(function (c) { return kindOf[c] === st.tab; })) st.tab = kindOf[st.sel[0]];
    }

    function set(patch, how) {
      Object.keys(patch).forEach(function (k) { st[k] = patch[k]; });
      render();
      if (!how) return;
      var h = writeHash(st);
      if (h !== (location.hash === "#" ? "" : location.hash)) {
        history[how === "replace" ? "replaceState" : "pushState"](null, "", h || location.pathname + location.search);
      }
    }

    function toggle(c) {
      var sel = st.sel.indexOf(c) >= 0 ? st.sel.filter(function (x) { return x !== c; }) : st.sel.concat([c]);
      set({ sel: sel, page: 1 }, "push");
    }

    // The toolbar: the search and the order
    var sid = uid + "-search", oid = uid + "-order", pid = uid + "-categories";
    var search = make("input", { id: sid, type: "search", "class": "form-control listing-search-input",
                                 placeholder: WORDS.search_hint, autocomplete: "off",
                                 oninput: function () { set({ q: search.value, page: 1 }, "replace"); } });
    var orderBtns = cfg.orders.map(function (label, n) {
      return make("button", { type: "button", "class": "kit-seg-button", text: label,
                              onclick: function () { set({ order: n, page: 1 }, "push"); } });
    });
    var toolbar = make("div", { "class": "listing-toolbar" }, [
      make("div", { "class": "listing-search" }, [make("label", { "for": sid, "class": "listing-label", text: WORDS.search }), search]),
      make("div", { "class": "listing-order", role: "group", "aria-labelledby": oid }, [
        make("span", { id: oid, "class": "listing-label", text: WORDS.order }),
        make("div", { "class": "kit-seg" }, orderBtns)])]);

    // The panel: Show / Hide, the kinds as a column of tabs, one kind's categories at a time
    var panelBody = make("div", { "class": "listing-panel-body", id: pid + "-body" });
    var panelBtn = make("button", { type: "button", "class": "kit-ghost", "aria-controls": pid + "-body",
                                    onclick: function () { set({ open: !st.open }); } });
    var panel = cfg.kinds.length ? make("section", { "class": "listing-panel", "aria-labelledby": pid }, [
      make("div", { "class": "listing-panel-head" }, [make("h2", { id: pid, text: WORDS.categories }), panelBtn]),
      panelBody]) : null;

    var bar = make("div", { "class": "listing-bar" });
    var empty = make("p", { "class": "listing-empty", hidden: true });
    var pager = make("nav", { "class": "listing-pages", "aria-label": WORDS.pages });
    root.insertBefore(toolbar, list);
    if (panel) root.insertBefore(panel, list);
    root.insertBefore(bar, list);
    root.insertBefore(empty, list);
    root.appendChild(pager);

    function render() {
      var focus = document.activeElement && document.activeElement.getAttribute("data-focus");
      var q = st.q.trim().toLowerCase();
      var selBy = {};
      st.sel.forEach(function (c) { (selBy[kindOf[c]] = selBy[kindOf[c]] || []).push(c); });
      function hit(it, skip) {
        if (q && it.text.indexOf(q) < 0) return false;
        return Object.keys(selBy).every(function (k) {
          return k === skip || selBy[k].some(function (c) { return it.cats.indexOf(c) >= 0; });
        });
      }
      var res = items.filter(function (it) { return hit(it, null); });
      if (st.order) res.reverse();
      var pages = Math.max(1, Math.ceil(res.length / cfg.pageSize));
      st.page = Math.min(Math.max(1, st.page), pages);
      var start = (st.page - 1) * cfg.pageSize, shown = res.slice(start, start + cfg.pageSize);

      // The items: the page's slice in the chosen order, the rest hidden
      var inSlice = new Set(shown);
      shown.concat(items.filter(function (it) { return !inSlice.has(it); })).forEach(function (it) {
        it.li.hidden = !inSlice.has(it);
        list.appendChild(it.li);
      });
      items.forEach(function (it) {
        Array.prototype.forEach.call(it.li.querySelectorAll(".listing-category"), function (a) {
          if (cfg.chips === "filter") a.setAttribute("aria-pressed", st.sel.indexOf(a.textContent) >= 0 ? "true" : "false");
        });
      });

      // The toolbar's state
      if (search.value !== st.q) search.value = st.q;
      orderBtns.forEach(function (b, n) { b.setAttribute("aria-pressed", n === st.order ? "true" : "false"); });

      // The panel: each kind's categories counted over what the other filters leave
      if (panel) {
        panelBtn.textContent = st.open ? WORDS.hide : WORDS.show;
        panelBtn.setAttribute("aria-expanded", st.open ? "true" : "false");
        panelBody.hidden = !st.open;
        panelBody.textContent = "";
        var tabs = make("div", { "class": "listing-kinds", role: "tablist", "aria-orientation": "vertical",
                                 "aria-label": WORDS.kinds });
        var cur = cfg.kinds.filter(function (k) { return k.key === st.tab; })[0] || cfg.kinds[0];
        var chipsId = pid + "-chips";
        cfg.kinds.forEach(function (k, n) {
          var nSel = k.cats.filter(function (c) { return st.sel.indexOf(c) >= 0; }).length;
          var on = k === cur;
          var tab = make("button", { type: "button", role: "tab", "class": "kit-tab", "aria-selected": on ? "true" : "false",
                                     "aria-controls": chipsId, tabindex: on ? "0" : "-1", "data-focus": "tab:" + k.key,
                                     onclick: function () { set({ tab: k.key }); },
                                     onkeydown: function (e) {
                                       var d = e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : 0;
                                       if (!d) return;
                                       e.preventDefault();
                                       var next = cfg.kinds[(n + d + cfg.kinds.length) % cfg.kinds.length];
                                       set({ tab: next.key });
                                       var t = panelBody.querySelector('[data-focus="tab:' + next.key + '"]');
                                       if (t) t.focus();
                                     } }, [
            make("span", { "class": "listing-kind-label", text: k.label }),
            nSel ? make("span", { "class": "listing-kind-selected", "aria-label": fill(WORDS.selected, { n: nSel }), text: String(nSel) }) : null,
            make("span", { "class": "listing-kind-count", text: String(k.cats.length) })]);
          tabs.appendChild(tab);
        });
        var base = items.filter(function (it) { return hit(it, cur.key); });
        var counted = cur.cats.map(function (c) {
          return { name: c, on: st.sel.indexOf(c) >= 0,
                   count: base.filter(function (it) { return it.cats.indexOf(c) >= 0; }).length };
        });
        var open = !!st.more[cur.key], shownCats = counted;
        if (!open && counted.length > cfg.chipLimit) {
          var top = counted.slice().sort(function (a, b) { return b.count - a.count; }).slice(0, cfg.chipLimit)
            .map(function (c) { return c.name; });
          shownCats = counted.filter(function (c) { return c.on || top.indexOf(c.name) >= 0; });
        }
        var chips = make("div", { "class": "listing-chips", role: "tabpanel", id: chipsId }, shownCats.map(function (c) {
          return make("button", { type: "button", "class": "kit-chip", "aria-pressed": c.on ? "true" : "false",
                                  disabled: !c.on && c.count === 0, "data-focus": "cat:" + c.name,
                                  onclick: function () { toggle(c.name); } },
                      [c.name, make("span", { "class": "listing-chip-count", text: String(c.count) })]);
        }));
        if (counted.length > cfg.chipLimit) {
          chips.appendChild(make("button", { type: "button", "class": "kit-ghost", "data-focus": "more",
                                             text: open ? WORDS.show_fewer : fill(WORDS.show_all, { n: counted.length }),
                                             onclick: function () {
                                               var more = Object.assign({}, st.more); more[cur.key] = !open; set({ more: more });
                                             } }));
        }
        panelBody.appendChild(tabs);
        panelBody.appendChild(chips);
      }

      // The bar: the count, each active filter and the search removable, Clear all
      var noun = cfg.noun[items.length === 1 ? 0 : 1];
      bar.textContent = "";
      bar.appendChild(make("p", { role: "status", "class": "listing-count",
                                  text: st.sel.length || q ? fill(WORDS.count_of, { n: res.length, total: items.length, noun: noun })
                                                           : fill(WORDS.count, { n: items.length, noun: noun }) }));
      function removable(label, aria, key, fn) {
        return make("button", { type: "button", "class": "kit-chip listing-active", "aria-label": aria, "data-focus": key, onclick: fn },
                    [label, make("span", { "class": "listing-remove", "aria-hidden": "true", text: "×" })]);
      }
      if (q) bar.appendChild(removable("“" + st.q.trim() + "”", WORDS.clear_search, "q",
                                       function () { set({ q: "", page: 1 }, "push"); }));
      st.sel.forEach(function (c) {
        bar.appendChild(removable(c, fill(WORDS.remove, { name: c }), "sel:" + c, function () { toggle(c); }));
      });
      if (st.sel.length || q) {
        bar.appendChild(make("button", { type: "button", "class": "kit-ghost", "data-focus": "clear", text: WORDS.clear_all,
                                         onclick: function () { set({ sel: [], q: "", page: 1 }, "push"); } }));
      }

      empty.hidden = res.length > 0;
      empty.textContent = "";
      if (!res.length) {
        empty.appendChild(document.createTextNode(WORDS.empty + " "));
        empty.appendChild(make("button", { type: "button", "class": "kit-ghost", text: WORDS.clear_filters,
                                           onclick: function () { set({ sel: [], q: "", page: 1 }, "push"); } }));
      }

      // The pagination: Previous, the first and the last page always, the current page's
      // neighbours between, Next, and the range
      pager.textContent = "";
      pager.hidden = pages < 2;
      if (pages > 1) {
        var nums = [];
        if (pages <= 7) { for (var i = 1; i <= pages; i++) nums.push(i); }
        else {
          var lo = Math.max(2, st.page - 1), hi = Math.min(pages - 1, st.page + 1);
          nums.push(1);
          if (lo > 2) nums.push(0);
          for (var j = lo; j <= hi; j++) nums.push(j);
          if (hi < pages - 1) nums.push(0);
          nums.push(pages);
        }
        var go = function (p) { return function () { set({ page: p }, "push"); root.scrollIntoView({ block: "start" }); }; };
        var btns = [make("button", { type: "button", "class": "kit-button", disabled: st.page === 1, text: WORDS.previous,
                                     "data-focus": "prev", onclick: go(st.page - 1) })];
        nums.forEach(function (p) {
          btns.push(p === 0 ? make("span", { "class": "listing-gap", "aria-hidden": "true", text: "…" })
            : make("button", { type: "button", "class": "kit-button", "aria-current": p === st.page ? "page" : null,
                               "aria-label": fill(WORDS.page, { n: p }), text: String(p), "data-focus": "page:" + p,
                               onclick: go(p) }));
        });
        btns.push(make("button", { type: "button", "class": "kit-button", disabled: st.page === pages, text: WORDS.next,
                                   "data-focus": "next", onclick: go(st.page + 1) }));
        pager.appendChild(make("p", { "class": "listing-range",
                                      text: fill(WORDS.range, { a: start + 1, b: start + shown.length, n: res.length }) }));
        pager.appendChild(make("div", { "class": "listing-page-buttons" }, btns));
      }

      if (focus) {
        var back = root.querySelector('[data-focus="' + focus.replace(/"/g, '\\"') + '"]');
        if (back && back !== document.activeElement) back.focus();
      }
    }

    // A chip on the category listing toggles the filter in place; its href stays for a new tab
    if (cfg.chips === "filter") {
      list.addEventListener("click", function (e) {
        var a = e.target.closest && e.target.closest("a.listing-category");
        if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0 || !kindOf[a.textContent]) return;
        e.preventDefault();
        toggle(a.textContent);
      });
    }
    window.addEventListener("popstate", function () { fromHash(); render(); });
    window.addEventListener("hashchange", function () { fromHash(); render(); });
    fromHash();
    root.classList.add("is-live");
    render();
  }

  function start() {
    Array.prototype.forEach.call(document.querySelectorAll(".site-listing"), function (root, n) {
      init(root, "listing" + (n ? "-" + n : ""));
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
'''
