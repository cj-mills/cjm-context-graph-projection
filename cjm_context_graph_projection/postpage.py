"""The post page's projected parts (the post page of de808eae (2); its rest designed as 39c51c15
under the redesign build 8079ae0f).

The render filter of `derivedblocks` carries them into each post: what the graph decides per
post is computed here, and the words are the site's. The AUTHOR STRIP (39c51c15 (5)) replaces
the about-author callout every archive post includes: its copy is site chrome in the author's
own words, kept under one key of the site config (`author-strip` in `_quarto.yml`), and the
graph decides the variant -- the pitch only on a post that backs an OFFERED claim through a
backing kind (outcome / method / capability; knowledge never carries an offer, 98e99fe5), and
only once the page it points at is published. Until the Work-with-me page (903bc108 (5)) is
on-graph every post takes the no-pitch variant and the plan counts the posts that would pitch.
Every post closes on its COMMENTS block (`comments`, 39c51c15 (1)): the thread its discussion
fact names, else its canonical path; a tutorial's questions line opens it (the questions
callout 32 posts included, now a line derived from the kind). A post that derives from a source
opens its end matter on the SOURCES block (`sources`, 39c51c15 (3), amendment 722a8232)."""

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

from .runtime import GraphHandle

STRIP_KEY = "author-strip"                               # The site-config key holding the strip's copy
STRIP_FIELDS = ("byline", "links", "pitch", "questions")  # Every field the copy must carry
SITE_AUTHOR_KEY = "site-author"            # The site config's one statement of the author (amendment fe6f0fb7)
SITE_AUTHOR_FIELDS = ("name", "role")
SITE_SUMMARY_KEY = "site-summary"          # The site's one sentence of who and what (amendment 5c3c2662 (1))
SITE_HOLDS_KEY = "site-holds"              # What the site holds, the summary's second part (design 8b4f15d0 (3))
SITE_LINKS_KEY = "site-links"              # The site's one statement of its contact links (design ff0c6338 (6))
SITE_LINK_FIELDS = ("icon", "text", "href")
READING_GUIDE_KEY = "reading-guide"        # The site's one statement of how to read it (design ff0c6338 (4))
NAV_FILE = "site-nav.yml"                  # The generated navbar links under _derived/ (a metadata-files entry)
SITE_NAV_KEY = "site-nav"                  # The navbar's own entries, each by its site path (finding c6befeb6)
SITE_NAV_FIELDS = ("text", "path")         # Every field an entry must carry (an icon is optional)
POST_KINDS = ("tutorial", "notes", "log", "work")        # Deliverable kinds a post page carries (not site pages)
TUTORIAL_KIND = "tutorial"
# The header's kind label (39c51c15 (2)): the navigation kind, as a reader reads it; the site's
# title-metadata partial renders the KIND_META value
KIND_LABELS = {"tutorial": "Tutorial", "notes": "Notes", "log": "Log", "work": "Work"}
KIND_META = "post-kind"
# A post's categories as links into the site's category listing (amendment of 0858bbd0): the
# listing is named once in the site config; the site's title-block partial renders CATEGORY_META
CATEGORY_LISTING_KEY = "category-listing"
CATEGORY_META = "category-links"
RELATED_MAX = 4   # Related posts shown at most (39c51c15 (4))
RELATED_FLOOR = 1.5   # The judged score a related post needs (amendment e09e262b; tuned on the review)
# The license vocabulary a page renders (SPDX ids, case-folded as the facts store them)
LICENSES = {
    "cc-by-4.0": ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/"),
    "cc-by-sa-4.0": ("CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0/"),
    "cc-by-nc-4.0": ("CC BY-NC 4.0", "https://creativecommons.org/licenses/by-nc/4.0/"),
    "cc-by-nc-sa-4.0": ("CC BY-NC-SA 4.0", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    "cc-by-nc-nd-4.0": ("CC BY-NC-ND 4.0", "https://creativecommons.org/licenses/by-nc-nd/4.0/"),
    "mit": ("MIT License", "https://opensource.org/licenses/MIT"),
}
FOOTER_FILE = "site-footer.yml"   # The generated footer metadata under _derived/ (a metadata-files entry)


def load_strip_copy(
    website_root: str,  # The site project root
) -> Dict[str, Any]:  # {copy: {field: text}, errors}
    """The strip's copy from the site config: a missing key or field refuses (the build never
    falls back to words the author did not write)."""
    path = Path(website_root) / "_quarto.yml"
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        return {"copy": {}, "errors": [{"kind": "strip-copy", "path": str(path), "why": f"unreadable: {e}"}]}
    copy = cfg.get(STRIP_KEY) or {}
    missing = [f for f in STRIP_FIELDS if not str(copy.get(f) or "").strip()]
    if missing:
        return {"copy": {}, "errors": [{"kind": "strip-copy", "path": str(path), "missing": missing,
                                        "why": f"the site config's `{STRIP_KEY}` lacks copy the strip renders"}]}
    out = {f: str(copy[f]).strip() for f in STRIP_FIELDS}
    # The byline names the author through the site's one statement of them (amendment fe6f0fb7)
    author = load_site_author(website_root)
    if author["errors"]:
        return {"copy": {}, "errors": author["errors"]}
    filled = fill_copy(out["byline"], author["author"])
    if "error" in filled:
        return {"copy": {}, "errors": [{"kind": "strip-copy", "path": str(path), "field": "byline",
                                        "why": f"the byline carries {filled['error']}"}]}
    out["byline"] = filled["text"]
    # The links line names the contact links through the site's one statement of them (design
    # ff0c6338 (6)): a line without {links} would be a second, hand-kept copy
    links = load_site_links(website_root)
    if links["errors"]:
        return {"copy": {}, "errors": links["errors"]}
    if "{links}" not in out["links"]:
        return {"copy": {}, "errors": [{"kind": "strip-copy", "path": str(path), "field": "links",
                                        "why": f"the links line names no {{links}} (the site config's `{SITE_LINKS_KEY}`)"}]}
    try:
        out["links"] = out["links"].format_map({"links": links_line(links["links"])})
    except (KeyError, ValueError, IndexError) as e:
        return {"copy": {}, "errors": [{"kind": "strip-copy", "path": str(path), "field": "links",
                                        "why": f"the links line carries a placeholder other than {{links}} ({e})"}]}
    return {"copy": out, "errors": []}


def load_site_links(
    website_root: str,  # The site project root
) -> Dict[str, Any]:  # {links: [{icon, text, href}], errors}
    """The site's one statement of its contact links (`site-links` in the site config, design
    ff0c6338 (6)): the author strip's links line, the navbar's icons and the About page all render
    it. No links, or a link lacking its icon, text or href, refuses -- never a default."""
    path = Path(website_root) / "_quarto.yml"
    try:
        got = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get(SITE_LINKS_KEY) or []
    except (OSError, yaml.YAMLError) as e:
        return {"links": [], "errors": [{"kind": SITE_LINKS_KEY, "path": str(path), "why": f"unreadable: {e}"}]}
    if not isinstance(got, list) or not got:
        return {"links": [], "errors": [{"kind": SITE_LINKS_KEY, "path": str(path),
                                         "why": f"the site config states no `{SITE_LINKS_KEY}` list"}]}
    out = []
    for i, link in enumerate(got):
        link = link if isinstance(link, dict) else {}
        missing = [f for f in SITE_LINK_FIELDS if not str(link.get(f) or "").strip()]
        if missing:
            return {"links": [], "errors": [{"kind": SITE_LINKS_KEY, "path": str(path), "index": i, "missing": missing,
                                             "why": f"`{SITE_LINKS_KEY}` entry {i + 1} lacks its {', '.join(missing)}"}]}
        out.append({f: str(link[f]).strip() for f in SITE_LINK_FIELDS})
    return {"links": out, "errors": []}


def links_line(
    links: List[Dict[str, str]],  # load_site_links' links
) -> str:  # The links as one markdown line, in their stated order
    return " · ".join(f"[{l['text']}]({l['href']})" for l in links)


def site_nav(
    links: List[Dict[str, str]],                   # load_site_links' links
    pages: Optional[List[Dict[str, str]]] = None,  # nav_entries' items: the navbar's own entries, resolved
) -> Dict[str, Any]:  # The navbar metadata site-build writes under _derived/ (NAV_FILE)
    """The navbar's right side: its own entries in their stated order (finding c6befeb6), then the
    contact icons from the site's links (design ff0c6338 (6)), each labelled by its link's text.
    Quarto appends a metadata file's `navbar.right` after the site config's own, so the site
    config keeps none and this one list holds the order."""
    return {"website": {"navbar": {"right": list(pages or []) + [
        {"icon": l["icon"], "href": l["href"], "aria-label": l["text"]} for l in links]}}}


def load_site_nav(
    website_root: str,  # The site project root
) -> Dict[str, Any]:  # {entries: [{text, path, icon?}], errors} -- [] when the site config states none
    """The navbar's own entries (`site-nav` in the site config, finding c6befeb6): each names its
    target by SITE PATH -- the URL a reader follows -- never by source file, so a path transfer
    moves the link with it. An entry lacking its text or path refuses; an icon is optional."""
    path = Path(website_root) / "_quarto.yml"
    try:
        got = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get(SITE_NAV_KEY) or []
    except (OSError, yaml.YAMLError) as e:
        return {"entries": [], "errors": [{"kind": SITE_NAV_KEY, "path": str(path), "why": f"unreadable: {e}"}]}
    if not isinstance(got, list):
        return {"entries": [], "errors": [{"kind": SITE_NAV_KEY, "path": str(path),
                                           "why": f"the site config's `{SITE_NAV_KEY}` is not a list"}]}
    out = []
    for i, entry in enumerate(got):
        entry = entry if isinstance(entry, dict) else {}
        missing = [f for f in SITE_NAV_FIELDS if not str(entry.get(f) or "").strip()]
        if missing:
            return {"entries": [], "errors": [{"kind": SITE_NAV_KEY, "path": str(path), "index": i, "missing": missing,
                                               "why": f"`{SITE_NAV_KEY}` entry {i + 1} lacks its {', '.join(missing)}"}]}
        out.append({f: str(entry[f]).strip() for f in SITE_NAV_FIELDS + ("icon",) if str(entry.get(f) or "").strip()})
    return {"entries": out, "errors": []}


def nav_entries(
    entries: List[Dict[str, str]],  # load_site_nav's entries
    rendered: Iterable[str],        # The profile's input sources, relative to the site root
) -> Dict[str, Any]:  # {items: [navbar items], errors}
    """Resolve each navbar entry to what Quarto links (finding c6befeb6): a page path to the ONE
    rendered source whose output it names -- a retired source is no input, so it never resolves --
    and any other path (a feed) to itself from the root. A page path no rendered source renders
    refuses, never a dead link; an entry with an icon renders as the icon, labelled by its text."""
    import posixpath
    from .site import output_href
    by_output: Dict[str, List[str]] = {}
    for src in sorted(rendered):
        stem, ext = posixpath.splitext(src)
        if ext in (".qmd", ".md", ".ipynb"):
            by_output.setdefault(stem + ".html", []).append(src)
    items: List[Dict[str, str]] = []
    errors: List[Dict[str, Any]] = []
    for e in entries:
        out = output_href(e["path"])
        href = out
        if out.endswith(".html"):
            srcs = by_output.get(out) or []
            if len(srcs) != 1:
                errors.append({"kind": SITE_NAV_KEY, "path": e["path"], "sources": srcs,
                               "why": "no rendered source renders this navbar path" if not srcs
                                      else "more than one rendered source renders this navbar path"})
                continue
            href = srcs[0]
        items.append({"icon": e["icon"], "href": href, "aria-label": e["text"]} if e.get("icon")
                     else {"text": e["text"], "href": href})
    return {"items": items, "errors": errors}


def load_reading_guide(
    website_root: str,  # The site project root
) -> Dict[str, Any]:  # {text, errors} -- text "" when the site config states none
    """The site's one statement of how to read it (`reading-guide` in the site config, design
    ff0c6338 (4)): llms.txt's intro and the About page both render it, the author named through
    {name} / {role}. Absent = "" (a reader decides whether it may be missing); a placeholder the
    author statement cannot fill refuses."""
    path = Path(website_root) / "_quarto.yml"
    try:
        text = str((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get(READING_GUIDE_KEY) or "")
    except (OSError, yaml.YAMLError) as e:
        return {"text": "", "errors": [{"kind": READING_GUIDE_KEY, "path": str(path), "why": f"unreadable: {e}"}]}
    if not text.strip():
        return {"text": "", "errors": []}
    author = load_site_author(website_root)
    if author["errors"]:
        return {"text": "", "errors": author["errors"]}
    filled = fill_copy(" ".join(text.split()), author["author"])
    if "error" in filled:
        return {"text": "", "errors": [{"kind": READING_GUIDE_KEY, "path": str(path),
                                        "why": f"`{READING_GUIDE_KEY}` carries {filled['error']}"}]}
    return {"text": filled["text"], "errors": []}


def load_site_author(
    website_root: str,  # The site project root
) -> Dict[str, Any]:  # {author: {name, role}, errors}
    """The site's one statement of its author (`site-author` in the site config, amendment
    fe6f0fb7): the byline, llms.txt's intro and the JSON-LD jobTitle all render it. A missing name
    or role refuses, never a default."""
    path = Path(website_root) / "_quarto.yml"
    try:
        got = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get(SITE_AUTHOR_KEY) or {}
    except (OSError, yaml.YAMLError) as e:
        return {"author": {}, "errors": [{"kind": "site-author", "path": str(path), "why": f"unreadable: {e}"}]}
    missing = [f for f in SITE_AUTHOR_FIELDS if not str(got.get(f) or "").strip()]
    if missing:
        return {"author": {}, "errors": [{"kind": "site-author", "path": str(path), "missing": missing,
                                          "why": f"the site config's `{SITE_AUTHOR_KEY}` lacks the author's {', '.join(missing)}"}]}
    return {"author": {f: " ".join(str(got[f]).split()) for f in SITE_AUTHOR_FIELDS}, "errors": []}


def load_site_holds(
    website_root: str,  # The site project root
) -> Dict[str, Any]:  # {text, errors} -- the clause as the summary sentence carries it
    """What the site holds (`site-holds` in the site config, design 8b4f15d0 (3)): the second part
    of the site's one sentence, kept as its own key so the home page and About read the parts
    while llms.txt reads the composed sentence. It is written as the sentence carries it (a clause,
    no closing stop); `holds_line` states it on its own. A missing clause, or a placeholder the
    author statement cannot fill, refuses."""
    path = Path(website_root) / "_quarto.yml"
    try:
        text = str((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get(SITE_HOLDS_KEY) or "")
    except (OSError, yaml.YAMLError) as e:
        return {"text": "", "errors": [{"kind": SITE_HOLDS_KEY, "path": str(path), "why": f"unreadable: {e}"}]}
    if not text.strip():
        return {"text": "", "errors": [{"kind": SITE_HOLDS_KEY, "path": str(path),
                                        "why": f"the site config states no `{SITE_HOLDS_KEY}`"}]}
    author = load_site_author(website_root)
    if author["errors"]:
        return {"text": "", "errors": author["errors"]}
    filled = fill_copy(" ".join(text.split()), author["author"])
    if "error" in filled:
        return {"text": "", "errors": [{"kind": SITE_HOLDS_KEY, "path": str(path),
                                        "why": f"`{SITE_HOLDS_KEY}` carries {filled['error']}"}]}
    return {"text": filled["text"], "errors": []}


def holds_line(
    clause: str,  # load_site_holds' text
) -> str:  # The clause standing as its own sentence: its first letter capitalized, a closing stop
    clause = clause.strip()
    if not clause:
        return ""
    line = clause[0].upper() + clause[1:]
    return line if line[-1] in ".!?" else line + "."


def load_site_summary(
    website_root: str,  # The site project root
) -> Dict[str, Any]:  # {text, errors}
    """The site's one sentence of who and what (`site-summary` in the site config, amendment
    5c3c2662 (1)): llms.txt's intro and the meta descriptions render it, the author named through
    {name} / {role} (fe6f0fb7) and what the site holds through {holds} (`site-holds`, 8b4f15d0
    (3)) -- composed from the parts the home page and About read, never a second copy of them. A
    missing summary, a summary naming no {holds}, or a placeholder neither can fill, refuses --
    never words the author did not write."""
    path = Path(website_root) / "_quarto.yml"
    try:
        text = str((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get(SITE_SUMMARY_KEY) or "")
    except (OSError, yaml.YAMLError) as e:
        return {"text": "", "errors": [{"kind": "site-summary", "path": str(path), "why": f"unreadable: {e}"}]}
    if not text.strip():
        return {"text": "", "errors": [{"kind": "site-summary", "path": str(path),
                                        "why": f"the site config states no `{SITE_SUMMARY_KEY}`"}]}
    if "{holds}" not in text:
        return {"text": "", "errors": [{"kind": "site-summary", "path": str(path),
                                        "why": f"`{SITE_SUMMARY_KEY}` names no {{holds}} (`{SITE_HOLDS_KEY}`): "
                                               "what the site holds would be a second copy"}]}
    author = load_site_author(website_root)
    holds = load_site_holds(website_root)
    if author["errors"] or holds["errors"]:
        return {"text": "", "errors": author["errors"] or holds["errors"]}
    filled = fill_copy(" ".join(text.split()), {**author["author"], "holds": holds["text"]})
    if "error" in filled:
        return {"text": "", "errors": [{"kind": "site-summary", "path": str(path),
                                        "why": f"`{SITE_SUMMARY_KEY}` carries {filled['error']}"}]}
    return {"text": filled["text"], "errors": []}


def fill_copy(
    text: str,                # Copy from the site config
    author: Dict[str, str],   # load_site_author's author
) -> Dict[str, Any]:  # {text} | {error}
    """Copy names the site author through {name} / {role} (the summary its holds clause through
    {holds}); any other placeholder refuses."""
    try:
        return {"text": text.format_map(author)}
    except (KeyError, ValueError, IndexError) as e:
        return {"error": f"a placeholder other than {{name}} / {{role}} ({e})"}


def load_holder(
    website_root: str,  # The site project root
) -> Dict[str, Any]:  # {holder, errors}
    """The copyright holder the footer names (`copyright-holder` in the site config): a missing
    value refuses, never a default name."""
    path = Path(website_root) / "_quarto.yml"
    try:
        holder = str((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("copyright-holder") or "").strip()
    except (OSError, yaml.YAMLError) as e:
        return {"holder": "", "errors": [{"kind": "copyright-holder", "path": str(path), "why": f"unreadable: {e}"}]}
    if not holder:
        return {"holder": "", "errors": [{"kind": "copyright-holder", "path": str(path),
                                          "why": "the site config names no copyright-holder for the footer"}]}
    return {"holder": holder, "errors": []}


def load_category_listing(
    website_root: str,              # The site project root
    projected: Iterable[str] = (),  # Page sources this build projects (relative to the root)
) -> Dict[str, Any]:  # {href, errors} -- href "" when the site config names no category listing
    """The listing a post's categories link into (`category-listing` in the site config: a listing
    page's source path, amendment of 0858bbd0), as the page path the listing opens filtered at by
    `#category=<name>`. Absent = the categories stay labels (a site with no category listing has
    nowhere to link them); a named page the project neither holds nor projects (the category
    listing is a projected page, design ce17606b (3)) refuses."""
    path = Path(website_root) / "_quarto.yml"
    try:
        named = str((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get(CATEGORY_LISTING_KEY) or "").strip()
    except (OSError, yaml.YAMLError) as e:
        return {"href": "", "errors": [{"kind": CATEGORY_LISTING_KEY, "path": str(path), "why": f"unreadable: {e}"}]}
    if not named:
        return {"href": "", "errors": []}
    if not (Path(website_root) / named).is_file() and named not in set(projected):
        return {"href": "", "errors": [{"kind": CATEGORY_LISTING_KEY, "path": named,
                                        "why": "the site config's category listing names no page in the project"}]}
    return {"href": "/" + named.rsplit(".", 1)[0] + ".html", "errors": []}


def category_links(
    categories: Any,    # The post's categories (its chips, categories.load_post_categories; a list, or one string)
    listing_href: str,  # load_category_listing's href ("" = none)
    pages: Optional[Dict[str, str]] = None,  # chip -> its category page's path (categorypages; None = none)
) -> List[Dict[str, str]]:  # [{name, href}] in the post's own order (a category with nowhere to link left out)
    """Each category as a link to its category page where one exists (design a62f2499 (7)), else
    to the category listing filtered to it: Quarto's listing script reads `#category=<URI-encoded
    name>` and decodes it, so a name with a space or an `&` survives."""
    from urllib.parse import quote
    if not categories or not (listing_href or pages):
        return []
    cats = categories if isinstance(categories, list) else [categories]
    out = []
    for c in (str(c) for c in cats if str(c).strip()):
        if pages and c in pages:
            out.append({"name": c, "href": pages[c]})
        elif listing_href:
            out.append({"name": c, "href": f"{listing_href}#category={quote(c, safe='')}"})
    return out


def is_category_link(
    target: str,        # A link target as a page states it (relative or site-absolute, its fragment kept)
    page_dir: str,      # The linking page's dir, relative to the site root ("" = the root)
    listing_href: str,  # load_category_listing's href ("" = none)
) -> bool:  # True = a link into the category listing's filtered view
    """A link to the category listing opened filtered to a category (category_links' form,
    `#category=<name>`), resolved as a browser resolves it against the linking page; the listing
    is named by its page or its markdown layer's file (design 42f30a8b). A bare `#category=` names
    the linking page itself, which only the listing's own in-place filter writes."""
    from urllib.parse import unquote, urljoin, urlsplit
    path, sep, frag = target.partition("#")
    if not listing_href or not sep or not frag.startswith("category=") or not path:
        return False
    resolved = unquote(urlsplit(urljoin("http://site/" + (page_dir + "/" if page_dir else ""), path)).path).lstrip("/")
    listing = listing_href.lstrip("/")[:-len(".html")]
    return any(resolved == listing + suffix for suffix in (".html", ".llms.md"))


def render_end(
    copy: Dict[str, str],                   # load_strip_copy's copy
    *,
    pitch: Optional[Dict[str, str]] = None,  # {claims, href}: the pitch's claim names and its target page
    comments: str = "",                     # The post's comments block (comments.render_comments)
    related: Optional[List[Dict[str, str]]] = None,  # related_posts' result
    reuse: str = "",                        # render_reuse's appendix
    sources: str = "",                      # The post's draws-on block (sources.render_draws)
) -> str:  # The post's end matter (markdown): draws on, related posts, the author strip, the comments, Reuse
    """What the post draws on, related posts, the author strip (with the pitch line when given) and the comments block."""
    lines = [copy["byline"]]
    if pitch:
        lines.append(copy["pitch"].format(claims=pitch["claims"], href=pitch["href"]))
    lines.append(copy["links"])
    # Plain classed divs: a callout a user filter inserts is never converted by Quarto, so its
    # look is the site stylesheet's (.author-strip / .comments-invite)
    out = (([sources] if sources else []) + ([render_related(related)] if related else [])
           + ["::: {.author-strip}\n" + "  \n".join(lines) + "\n:::\n"])
    if comments:
        out.append(comments)
    if reuse:
        out.append(reuse)
    return "\n".join(out)


async def offered_backing(
    gx: GraphHandle,
) -> Dict[str, List[str]]:  # {deliverable id: the offered claims' names it backs, by claim position}
    """The posts that back an OFFERED claim through a backing kind, read through the public
    view (only offered claims, only public supports -- 676bac8e (4))."""
    from cjm_dev_graph_schema import predicates as P
    from .claims import claims_report
    out: Dict[str, List[str]] = {}
    for c in (await claims_report(gx, public=True))["public"]:
        for kind in P.BACKING_KINDS:
            for r in c["supports"].get(kind, []):
                names = out.setdefault(r["id"], [])
                if c["name"] not in names:
                    names.append(c["name"])
    return out


def pitch_target(
    planned_pages: Iterable[Dict[str, Any]],  # page_plan's pages
) -> Optional[Dict[str, str]]:  # {title, href} of the published page the pitch links, or None
    """The page the pitch points at: the Work-with-me page (903bc108 (5)). It is not on-graph
    yet, so no post pitches; its node resolves here when it lands."""
    return None


def end_plan(
    copy: Dict[str, str],                   # load_strip_copy's copy
    notes: Iterable[str],                   # The rendered posts' Note ids
    types: Dict[str, Dict[str, Any]],       # note_types' map
    backing: Dict[str, List[str]],          # offered_backing's map
    target: Optional[Dict[str, str]],       # pitch_target's page
    related: Optional[Dict[str, List[Dict[str, str]]]] = None,  # {note id: related_posts' result}
    licenses: Optional[Dict[str, Dict[str, Any]]] = None,       # {note id: post_licenses' result}
    comments_config: Optional[Dict[str, Any]] = None,           # comments.load_comments_config's config
    page_threads: Optional[Dict[str, Dict[str, Any]]] = None,   # {note id: comments.page_comments' result}
    sources: Optional[Dict[str, str]] = None,                   # {note id: its draws-on block} (sources.draws_plan)
) -> Dict[str, Any]:  # {ends: {note id: markdown}, counts}
    """Every post's end matter. A Note of no post kind (a site page, an untyped Note) has none."""
    from .comments import render_comments
    ends: Dict[str, str] = {}
    counts = {"strips": 0, "pitch": 0, "pitch_pending": 0, "questions": 0, "related": 0,
              "comments_thread": 0, "comments_term": 0, "comments_earlier": 0}
    for nid in sorted(notes):
        kind = (types.get(nid) or {}).get("kind")
        if kind not in POST_KINDS:
            continue
        claims = backing.get(nid) or []
        pitch = {"claims": ", ".join(claims), "href": target["href"]} if claims and target else None
        rel = (related or {}).get(nid) or []
        lic = (licenses or {}).get(nid)
        pc = (page_threads or {}).get(nid)
        block = (render_comments(comments_config, **pc, questions=copy["questions"] if kind == TUTORIAL_KIND else "")
                 if comments_config and pc else "")
        ends[nid] = render_end(copy, pitch=pitch, comments=block, related=rel,
                               reuse=render_reuse(lic) if lic else "", sources=(sources or {}).get(nid, ""))
        if block:
            counts["comments_thread" if pc["number"] is not None else "comments_term"] += 1
            counts["comments_earlier"] += bool(pc["earlier"])
        counts["related"] += bool(rel)
        counts["strips"] += 1
        counts["pitch"] += bool(pitch)
        counts["pitch_pending"] += bool(claims and not target)
        counts["questions"] += kind == TUTORIAL_KIND
    return {"ends": ends, "counts": counts}


async def header_facts(
    gx: GraphHandle,
) -> Dict[str, Dict[str, str]]:  # {Note id: {published, revised}} — ISO dates (UTC) of the facts' times
    """The header's dated facts (39c51c15 (2)): when a deliverable's `published` state was asserted,
    and its latest `revised` assertion. Each date is a fact's own time, never a build or commit time."""
    from datetime import datetime, timezone
    from cjm_dev_graph_schema import predicates as P
    from . import factlayer as F
    slot = [a for a in await F.load_assertions(gx)
            if F.prop(a, "predicate") in (P.PUBLISH_STATE, P.REVISED)]
    out: Dict[str, Dict[str, str]] = {}
    for a in F.active_assertions(slot, await F.load_supersedes(gx)):
        pred, sid = F.prop(a, "predicate"), str(F.prop(a, "subject_id") or "")
        if pred == P.PUBLISH_STATE and F.prop(a, "value") != P.PUBLISH_PUBLISHED:
            continue
        day = datetime.fromtimestamp(float(F.prop(a, "asserted_at") or 0), timezone.utc).date().isoformat()
        key = "published" if pred == P.PUBLISH_STATE else "revised"
        cur = out.setdefault(sid, {}).get(key)
        out[sid][key] = max(cur, day) if cur else day
    return out


def display_date(
    iso: str,  # An ISO day ("2026-09-29")
) -> str:  # The day as the page shows it ("September 29, 2026")
    """Quarto formats a page's dates BEFORE user filters run, so a date the filter sets must
    already be in the site's display format: Quarto's `long` (the site sets no date-format);
    `dcterms.date` still derives from it."""
    from datetime import date
    d = date.fromisoformat(iso)
    return f"{d:%B} {d.day}, {d.year}"


def header_meta(
    kind: str,                      # The deliverable type's kind
    origin: str,                    # The type's origin (archive | born)
    metadata: Dict[str, Any],       # The source's front matter (the Note's metadata)
    facts: Dict[str, str],          # header_facts' entry for this Note
    listing_href: str = "",         # The category listing's page path (load_category_listing; "" = none)
    categories: Optional[List[str]] = None,  # The post's chips (categories.load_post_categories; None = none)
    pages: Optional[Dict[str, str]] = None,  # chip -> its category page's path (categorypages; None = none)
) -> Dict[str, Any]:  # {set: {meta key: value}, unset: [meta keys]} for the render filter
    """The header's projected metadata (39c51c15 (2)): the kind label; a born post's date is its
    publication's (a draft shows none and says so); Updated is the latest revision when it is
    newer than the source's own date-modified (an archive post keeps its front matter); and the
    categories -- the graph's, never the front matter's (design ce17606b (2)) -- as links to each
    category's page, else into the category listing (amendment of 0858bbd0, design a62f2499 (7)).
    A post with no chip shows none, whatever its front matter lists."""
    from .sitepages import parse_date
    label = KIND_LABELS.get(kind, "")
    out: Dict[str, Any] = {"set": {}, "unset": []}
    if origin == "born":
        if facts.get("published"):
            out["set"]["date"] = display_date(facts["published"])
        else:
            out["unset"].append("date")
            label = f"{label} · Draft" if label else "Draft"
    if label:
        out["set"][KIND_META] = label
    own = parse_date(metadata.get("date-modified"))
    if facts.get("revised") and (own is None or facts["revised"] > own.isoformat()):
        out["set"]["date-modified"] = display_date(facts["revised"])
    if categories:
        out["set"]["categories"] = list(categories)
    elif metadata.get("categories"):
        out["unset"].append("categories")
    links = category_links(categories or [], listing_href, pages)
    if links:
        out["set"][CATEGORY_META] = links
    return out


def post_dates(
    origin: str,                    # The type's origin (archive | born)
    metadata: Dict[str, Any],       # The source's front matter (the Note's metadata)
    facts: Dict[str, str],          # header_facts' entry for this Note
) -> Dict[str, str]:  # {published, updated} ISO days ("" both = a draft: not public)
    """A post's public dates (39c51c15 (2)): an archive post's own date, a born post's publication;
    updated = the latest of its revisions, an archive post's date-modified and its publication. The
    footer's years and the JSON-LD dates (amendment 23a49667 (4)) both read them."""
    from .sitepages import parse_date
    own = parse_date(metadata.get("date")) if origin != "born" else None
    published = facts.get("published") or (own.isoformat() if own else "")
    if not published:
        return {"published": "", "updated": ""}
    mod = parse_date(metadata.get("date-modified")) if origin != "born" else None
    return {"published": published,
            "updated": max(v for v in (facts.get("revised") or "", mod.isoformat() if mod else "", published) if v)}


def _day(
    value: Any,  # A date string or None
) -> int:  # The day's ordinal (0 = none): newer posts rank first within a tie
    from .sitepages import parse_date
    d = parse_date(value) if value else None
    return d.toordinal() if d else 0


async def related_context(
    gx: GraphHandle,
) -> Dict[str, Any]:  # {links, judged, series, coverage, stages, stage_names}
    """The relations related posts rank by (39c51c15 (4), amendment e09e262b): post-to-post links
    (REFERENCES between Notes), the stored judgments (JUDGED_RELATED), series membership
    (IN_SERIES), and the tutorials' task + stage facts with the stage order (the adjacent-stage
    reason)."""
    from cjm_dev_graph_schema import predicates as P
    from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations
    from . import factlayer as F
    from .coverage import load_coverage_facts, load_vocab
    from .judging import load_judged
    notes = {str(F.nid(n)) for n in await F.load_label(gx, DevNodeKinds.NOTE)}
    links = {(str(s), str(t)) for s, t in await F.load_edge_pairs(gx, DevRelations.REFERENCES)
             if str(s) in notes and str(t) in notes and s != t}
    series: Dict[str, set] = {}
    for s, t in await F.load_edge_pairs(gx, DevRelations.IN_SERIES):
        series.setdefault(str(s), set()).add(str(t))
    stages = (await load_vocab(gx))[P.ENTITY_STAGE]
    return {"links": links, "judged": await load_judged(gx), "series": series,
            "coverage": await load_coverage_facts(gx), "stages": [s["key"] for s in stages],
            "stage_names": {s["key"]: s.get("name", s["key"]) for s in stages}}


def related_posts(
    nid: str,                               # The post
    candidates: Dict[str, Dict[str, Any]],  # {note id: {title, href, kind, date}} — the posts the profile renders
    ctx: Dict[str, Any],                    # related_context's relations
    limit: int = RELATED_MAX,
) -> List[Dict[str, str]]:  # [{title, href, reason}] in rank order
    """Related posts by relation, each with its reason (39c51c15 (4), amendment e09e262b):
    explicit links in either direction first -- authored intent, kept whatever the judge says,
    ordered by the judged score; then the posts the judge rated at or above RELATED_FLOOR with a
    relation other than `unrelated`, by score, the reason the relation's (a tutorial of the same
    task at the adjacent stage keeps its stage reason). Series-mates are excluded: the navigation
    holds them. Only stored judgments rank -- a post never judged has its links alone, and the
    build reports it stale."""
    from cjm_dev_graph_schema import predicates as P
    from .judging import RELATION_REASONS
    mine_series = ctx["series"].get(nid, set())
    pool = {c: v for c, v in candidates.items()
            if c != nid and not (mine_series & ctx["series"].get(c, set()))}
    judged = ctx.get("judged") or {}

    def score(c):
        return float((judged.get((nid, c)) or {}).get("score") or 0.0)
    ranked: List[tuple] = []   # (tier, sort key, id, reason)
    for c in pool:
        if (nid, c) in ctx["links"]:
            ranked.append((0, -score(c), c, "Linked from this post"))
        elif (c, nid) in ctx["links"]:
            ranked.append((0, -score(c), c, "Links here"))
    seen = {r[2] for r in ranked}
    order = {k: i for i, k in enumerate(ctx["stages"])}
    mine = ctx["coverage"].get(nid, {})
    tasks = set(mine.get(P.TEACHES_TASK, []))
    stages = [s for s in mine.get(P.TEACHES_STAGE, []) if s in order]
    tutorial = candidates.get(nid, {}).get("kind") == TUTORIAL_KIND
    for c in pool:
        j = judged.get((nid, c))
        if c in seen or not j or score(c) < RELATED_FLOOR or j.get("relation") not in RELATION_REASONS:
            continue
        reason = RELATION_REASONS[j["relation"]]
        theirs = ctx["coverage"].get(c, {})
        if tutorial and pool[c].get("kind") == TUTORIAL_KIND and tasks & set(theirs.get(P.TEACHES_TASK, [])):
            steps = [(order[t] - order[s], t) for s in stages for t in theirs.get(P.TEACHES_STAGE, [])
                     if t in order and abs(order[t] - order[s]) == 1]
            if steps:
                d, stage = max(steps)   # a next step outranks a previous one
                reason = f"{'Next step' if d > 0 else 'Previous step'}: {ctx['stage_names'].get(stage, stage)}"
        ranked.append((1, -score(c), c, reason))
    ranked.sort(key=lambda r: (r[0], r[1], -_day(pool[r[2]].get("date")), pool[r[2]].get("title", "")))
    return [{"title": pool[c]["title"], "href": pool[c]["href"], "reason": why} for _, _, c, why in ranked[:limit]]


def render_related(
    items: List[Dict[str, str]],  # related_posts' result
) -> str:  # The related-posts block (markdown), "" when there are none
    """Related posts, each with its reason, as a plain classed div (styled by the site)."""
    if not items:
        return ""
    lines = [f"- [{i['title'].replace('[', '(').replace(']', ')')}]({i['href']}) — {i['reason']}" for i in items]
    return "::: {.related-posts}\n**Related**\n\n" + "\n".join(lines) + "\n:::\n"


async def license_facts(
    gx: GraphHandle,
) -> Dict[str, Any]:  # {by_subject: {id: {predicate: spdx}}, type_ids: {type key: DeliverableType id}}
    """The license facts (39c51c15 (6)): per deliverable class on the DeliverableType, per
    deliverable on the Note (an override); a relicensing is a dated supersession."""
    from cjm_dev_graph_schema import predicates as P
    from cjm_dev_graph_schema.vocab import DevNodeKinds
    from . import factlayer as F
    slot = [a for a in await F.load_assertions(gx)
            if F.prop(a, "predicate") in (P.CONTENT_LICENSE, P.CODE_LICENSE)]
    by: Dict[str, Dict[str, str]] = {}
    for a in F.active_assertions(slot, await F.load_supersedes(gx)):
        by.setdefault(str(F.prop(a, "subject_id")), {})[str(F.prop(a, "predicate"))] = str(F.prop(a, "value"))
    types = {str(F.prop(n, "key")): str(F.nid(n)) for n in await F.load_label(gx, DevNodeKinds.DELIVERABLE_TYPE)}
    return {"by_subject": by, "type_ids": types}


def post_licenses(
    nid: str,                        # The post's Note id
    type_key: str,                   # Its deliverable type's key
    lic: Dict[str, Any],             # license_facts' result
) -> Dict[str, Any]:  # {content, code} as (display, url) pairs, or {error}
    """A post's licenses: its own override, else its class's. A post with none, or with an id
    outside the vocabulary, is an error the build names (every public page states its license)."""
    from cjm_dev_graph_schema import predicates as P
    own = lic["by_subject"].get(nid, {})
    cls = lic["by_subject"].get(lic["type_ids"].get(type_key, ""), {})
    out: Dict[str, Any] = {}
    for key, pred in (("content", P.CONTENT_LICENSE), ("code", P.CODE_LICENSE)):
        spdx = own.get(pred) or cls.get(pred)
        if not spdx:
            return {"error": f"no {pred} fact on the post or its type `{type_key}`"}
        if spdx not in LICENSES:
            return {"error": f"{pred} `{spdx}` is not in the license vocabulary"}
        out[key] = LICENSES[spdx]
    return out


def render_reuse(
    licenses: Dict[str, Any],  # post_licenses' result
) -> str:  # The post's Reuse appendix (markdown): Quarto moves an .appendix div into its appendix
    """The license line, as Quarto's own Reuse section (a filter-set `license` never reaches
    Quarto's appendix, which it builds before user filters run)."""
    (ct, cu), (kt, ku) = licenses["content"], licenses["code"]
    return f"::: {{.appendix}}\n## Reuse\n\nText: [{ct}]({cu}) · Code samples: [{kt}]({ku})\n:::\n"


def site_footer(
    holder: str,                                 # The copyright holder (site config `copyright-holder`)
    dated: List[Dict[str, Any]],                 # [{published, updated}] ISO days of every public post
    licenses: List[Dict[str, Any]],              # post_licenses' results of every public post
) -> Dict[str, Any]:  # The generated site metadata (`website.page-footer`), merged by Quarto's metadata-files
    """The footer, derived (39c51c15 (6)): the copyright years run from the first publication to
    the latest publication or revision among the public posts -- never the build's clock
    (8f6f2343) -- and the license lines state the licenses every public post shares (differing
    ones send the reader to each page's Reuse section)."""
    pub = [d["published"] for d in dated if d.get("published")]
    last = [v for d in dated for v in (d.get("published"), d.get("updated")) if v]
    first_y, last_y = (min(pub)[:4], max(last)[:4]) if pub else ("", "")
    years = first_y if first_y == last_y else f"{first_y}–{last_y}"
    foot: Dict[str, Any] = {"center": [{"text": f"© {years} {holder}".replace("  ", " "), "href": "about.qmd"}]}
    for side, key, what in (("left", "content", "Content"), ("right", "code", "Code samples")):
        vals = {tuple(lic[key]) for lic in licenses if key in lic}
        if len(vals) == 1:
            text, url = vals.pop()
            foot[side] = [{"text": f"{what} licensed under the {text}" if key == "code" else f"{what} licensed under {text}",
                           "href": url}]
        elif vals:
            foot[side] = [{"text": f"{what}: licenses vary, see each page's Reuse section"}]
    return {"website": {"page-footer": foot}}
