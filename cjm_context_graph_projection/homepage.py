"""The HOME PAGE: a projected map of the site under its role and what it holds (design e55201e2,
amendments 5c3c2662 and 8b4f15d0 + erratum 2e2abe66, under the redesign build 8079ae0f; work
items f0ec7156 and b5385504).

A Lens whose view layout is `home` owns the page: its site_path the location (the root) and its
selection the MAP -- one `subgraph` clause naming the pages the map shows, in the order it shows
them (the entry order is that list, never template text). Everything else is derived at build and
nothing is stored:

1. the IDENTITY: a heading stating the author's role (`site-author`), a line under it stating
   what the site holds (`site-holds`, the summary's second part), and the contact links as
   buttons (`site-links` in order, the first the primary; 8b4f15d0 (3) / (8)); the composed
   sentence (`site-summary`) is the meta description;
2. each MAP ENTRY is a page the build planned: a plain heading (its title), an All-N label
   linking the page (N counted at build from the full list the entry's items head -- an index's
   hub pages, else the posts it lists -- never stored; 8b4f15d0 (6)), its Lens's own description (12d98020; 8b4f15d0 (2)) and its items --
   - an INDEX page: the first `home_hubs` of the hub pages it states, in its own order (5c3c2662
     (5)); the category index's EVERY page as the site's category chips, each with its post
     count (8b4f15d0 (7)), its entry spanning the map's full width (the review of b5385504);
   - the category LISTING: its `home_recent` newest posts by their public dates
     (postpage.post_dates, the one derivation the header, the footer and the JSON-LD read), each
     with its updated or published day on a line of its own (8b4f15d0 (1) / (5), erratum
     2e2abe66; the review of b5385504);
   an entry whose page lists no content under the profile renders nothing (e55201e2 (2)); a
   named node with no planned page, or a page that is neither, refuses;
3. JUMP LINKS to the entries, from the same entry list, open the page below a narrow width
   (8b4f15d0 (9)); the entries lay out as columns at the full width (8b4f15d0 (10));
4. the WORK-WITH-ME band renders only once its page is published (postpage.pitch_target, the
   author strip's pitch rule; 5c3c2662 (2)).

The page is Quarto markdown -- fenced divs with home classes, the buttons and chips in the kit's
roles (`kit-button`, `kit-chip`), styled by rules reading token roles only (8b4f15d0 (11),
898c81d6). It lists no post as a member, so no post names the home page as a collection, and
llms.txt never lists it (it stands in for the site root). There is no most-read list: traffic is
not yet a captured fact (e55201e2 (6))."""

import posixpath
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from cjm_dev_graph_schema import predicates as P

from . import factlayer as F
from .runtime import GraphHandle

LAYOUT = "home"   # The Lens view layout this page projects
ALL_LABEL = "All {n}"   # An entry's link to its page, N counted at build (8b4f15d0 (6))
# What staging shows where a map entry's index Lens has no description; public refuses
UNDESCRIBED = "⚠ No description yet (the index Lens's description)."


def entry_refs(
    selection: List[Dict[str, Any]],  # The home Lens's selection clauses
) -> Dict[str, Any]:  # {refs} | {error}
    """The map's entries in their order: the refs of the selection's `subgraph` clauses, in turn.
    Any other verb selects with no stated order, so it refuses."""
    refs: List[str] = []
    for c in selection:
        if c.get("verb") != "subgraph":
            return {"error": f"the home Lens selects with {c.get('verb')!r}; its map is an ordered "
                             "`subgraph` list of the index Lenses"}
        refs += [str(r) for r in (c.get("args") or {}).get("refs") or [] if str(r) not in refs]
    return {"refs": refs}


def entry_anchor(
    key: str,  # The entry page's key
) -> str:  # The entry's element id, from its key (never a node id)
    return "home-" + (re.sub(r"[^a-z0-9]+", "-", key.casefold()).strip("-") or "entry")


def map_entries(
    refs: List[str],                      # The map's page ids, in order
    planned: List[Dict[str, Any]],        # Every other planned page
    descriptions: Dict[str, str],         # {Lens id: its description}
    hubs: int,                            # How many hub pages an index entry shows (home_hubs)
    recent: int,                          # How many posts the listing entry shows (home_recent)
    posts: Dict[str, Dict[str, Any]],     # {post id: {title, href, published, updated}} -- hrefs from the home page
    page_src: str,                        # The home page's source, relative to the root
    profile: str,                         # public | staging
) -> Dict[str, Any]:  # {entries: [{title, href, anchor, description, count, form, items}], skipped, errors}
    """Each named page as a map entry (the module docstring's (2)). An entry's `form` names its
    items: `hubs` (an index page's hub pages), `chips` (the category index's, each with its post
    count, every one) or `posts` (the category listing's newest)."""
    from .categorypages import INDEX_LAYOUT as CATEGORY_INDEX
    from .sitepages import CATEGORY_LAYOUT
    by_subject = {p["subject"]: p for p in planned}
    by_source = {p["source"]: p for p in planned}

    def rel(src: str) -> str:   # a link relative to the home page's source, as Quarto resolves it
        return posixpath.relpath(src, posixpath.dirname(page_src) or ".")
    out: Dict[str, Any] = {"entries": [], "skipped": [], "errors": []}
    for ref in refs:
        p = by_subject.get(ref)
        if p is None:
            out["errors"].append({"kind": "home-entry", "entry": ref,
                                  "why": "the home page's map names a node with no planned page"})
            continue
        listing = p.get("layout") == CATEGORY_LAYOUT
        if p.get("hubs") is None and not listing:
            out["errors"].append({"kind": "home-entry", "entry": ref, "source": p["source"],
                                  "why": "the home page's map names a page that is neither an index (it states "
                                         "hubs) nor the category listing"})
            continue
        if not p.get("members"):
            out["skipped"].append(p["source"])   # nothing public to point at (e55201e2 (2))
            continue
        desc = str(descriptions.get(ref) or "").strip()
        if not desc:
            if profile == "public":
                out["errors"].append({"kind": "home-entry-undescribed", "entry": ref, "source": p["source"],
                                      "why": "a map entry's Lens has no description for the entry to state"})
                continue
            desc = UNDESCRIBED
        entry = {"title": p["title"], "href": rel(p["source"]), "anchor": entry_anchor(str(p.get("key") or ref)),
                 "description": desc}
        if listing:   # the listing's newest posts, counted from everything it lists
            rows = [posts[i] for i in p.get("listed") or [] if i in posts]
            entry.update(form="posts", count=len(rows), items=recent_posts(rows, recent))
        elif p.get("layout") == CATEGORY_INDEX:   # every category page as a chip, each with its count
            entry.update(form="chips", count=len(p["hubs"]),
                         items=[{"title": h["title"], "href": rel(h["source"]),
                                 "count": int((by_source.get(h["source"]) or {}).get("members") or 0)}
                                for h in p["hubs"]])
        else:   # an index grouped into hub pages lists them; one that states none lists its posts
            entry.update(form="hubs", count=len(p["hubs"]) or int(p["members"]),
                         items=[{"title": h["title"], "href": rel(h["source"])} for h in p["hubs"][:hubs]])
        out["entries"].append(entry)
    return out


def recent_posts(
    posts: List[Dict[str, Any]],  # [{title, href, published, updated}] -- public dates (post_dates)
    n: int,                       # How many (home_recent)
) -> List[Dict[str, Any]]:  # The n newest by updated day, then published, then title
    dated = [p for p in posts if p.get("published")]
    dated = sorted(dated, key=lambda p: str(p.get("title") or "").casefold())
    dated = sorted(dated, key=lambda p: (p.get("updated") or p["published"], p["published"]), reverse=True)
    return dated[:n]


def post_line(
    post: Dict[str, Any],  # A recent_posts row
) -> str:  # The post as a list item: its title linking it, then its updated or published day (`home-day`)
    from .postpage import display_date
    updated = post.get("updated") and post["updated"] != post["published"]
    day = display_date(post["updated"] if updated else post["published"])
    return f"- [{post['title']}]({post['href']}) [{'Updated ' if updated else ''}{day}]{{.home-day}}"


def home_body(
    role: str,                          # The author's role (the page heading)
    holds: str,                         # What the site holds, as its own sentence (postpage.holds_line)
    links: List[Dict[str, str]],        # The site's contact links, in order (postpage.load_site_links)
    entries: List[Dict[str, Any]],      # map_entries' entries
    band: Optional[Dict[str, str]] = None,  # The Work-with-me page {title, href}, once published
) -> str:  # The page's body
    buttons = [f"[{l['text']}]({l['href']}){{.kit-button{' .kit-primary' if i == 0 else ''}}}"
               for i, l in enumerate(links)]
    lines = ["::: {.home-identity}", "", f"# {role}", "", holds, ""]
    if buttons:
        lines += ["::: {.home-contact}", "", " ".join(buttons), "", ":::", ""]
    lines += [":::", ""]
    if entries:
        lines += ["::: {.home-jump}", "", " · ".join(f"[{e['title']}](#{e['anchor']})" for e in entries), "",
                  ":::", "", "::: {.home-map}", ""]
    for e in entries:
        wide = " .home-wide" if e["form"] == "chips" else ""   # the chips span the map's width
        lines += [f"::: {{#{e['anchor']} .home-entry{wide}}}", "", "::: {.home-entry-head}", "", f"## {e['title']}", "",
                  f"[{ALL_LABEL.format(n=e['count'])}]({e['href']}){{.home-all}}", "", ":::", "",
                  e["description"], ""]
        if e["form"] == "chips":
            lines += ["::: {.home-chips}", ""]
            lines += [f"[{c['title']} [{c['count']}]{{.home-count}}]({c['href']}){{.kit-chip}}" for c in e["items"]]
            lines += ["", ":::", ""]
        elif e["form"] == "posts":
            lines += [post_line(r) for r in e["items"]] + [""]
        else:
            lines += [f"- [{h['title']}]({h['href']})" for h in e["items"]] + [""]
        lines += [":::", ""]
    if entries:
        lines += [":::", ""]
    if band:
        lines += [f"## [{band['title']}]({band['href']})", ""]
    return "\n".join(lines)


async def plan_home_page(
    gx: GraphHandle,
    node: Any,                               # The home Lens node
    page: Dict[str, Any],                    # {source, href}
    website_root: str,                       # The site project root (the site config's copy)
    root: Path,                              # The site project root (resolved)
    profile: str,                            # public | staging
    states: Dict[str, List[str]],
    types: Dict[str, Dict[str, Any]],
    drafts: Optional[Path],
    planned: List[Dict[str, Any]],           # Every other planned page (the map reads them)
) -> Dict[str, Any]:  # {page: planned entry} | {errors}
    """Plan the home page: the identity, the map, the jump links, the band."""
    from .lens import lens_count
    from .postpage import (POST_KINDS, header_facts, holds_line, load_site_author, load_site_holds,
                           load_site_links, load_site_summary, pitch_target, post_dates)
    from .site import stated
    from .sitepages import _listed, page_source, render_page
    sid, key, src = str(F.nid(node)), str(F.prop(node, "key") or ""), page["source"]
    errors: List[Dict[str, Any]] = []
    summary = load_site_summary(website_root)
    author = load_site_author(website_root)
    holds = load_site_holds(website_root)
    links = load_site_links(website_root)
    errors += summary["errors"] + author["errors"] + holds["errors"] + links["errors"]
    counts = {}
    for pred in (P.HOME_HUBS, P.HOME_RECENT):
        got = await lens_count(gx, sid, pred, "the home page")
        if got.get("error"):
            errors.append({"kind": "home-count", "subject": sid, "why": got["error"]})
        counts[pred] = got.get("value")
    refs = entry_refs(list(F.prop(node, "selection") or []))
    if refs.get("error"):
        errors.append({"kind": "home-selection", "subject": sid, "why": refs["error"]})
    if errors:
        return {"errors": errors}
    # Every post the profile renders, with its public dates and its href from the home page
    post_ids = sorted(n for n, t in types.items() if t.get("kind") in POST_KINDS)
    notes = await F.load_nodes(gx, post_ids)
    members = [notes[i] for i in post_ids if notes.get(i) is not None]
    listed = _listed(members, src, root, profile, states, types, drafts, sid)
    errors += listed["errors"]
    if errors:
        return {"errors": errors}
    facts = await header_facts(gx)
    posts = {}
    for nid, href in zip(listed["ids"], listed["contents"]):
        t = types.get(nid) or {}
        dates = post_dates(t.get("origin", ""), F.prop(notes[nid], "metadata") or {}, facts.get(nid, {}))
        posts[nid] = {"title": stated(notes[nid], "title"), "href": href, **dates}
    lenses = await F.load_nodes(gx, refs["refs"])
    descriptions = {r: str(F.prop(n, "description") or "") for r, n in lenses.items() if n is not None}
    mapped = map_entries(refs["refs"], planned, descriptions, counts[P.HOME_HUBS], counts[P.HOME_RECENT],
                         posts, src, profile)
    errors += mapped["errors"]
    if errors:
        return {"errors": errors}
    band = pitch_target(planned)
    if band:
        band = {"title": band["title"], "href": posixpath.relpath(page_source(band["href"]) or band["href"],
                                                                  posixpath.dirname(src) or ".")}
    body = home_body(author["author"]["role"], holds_line(holds["text"]), links["links"], mapped["entries"], band)
    recent = [r for e in mapped["entries"] if e["form"] == "posts" for r in e["items"]]
    updated = max((r.get("updated") or r["published"] for r in recent), default=None)
    # No title block: the role heading opens the page. No page title (the site root's tab is the
    # site's own title), the composed summary only as the meta description, and no dates (the
    # title block would show them; the sitemap's lastmod reads the file time, stamped from
    # `updated`). The site's title partials refuse `title-block-style: none`, so the block is left
    # empty instead.
    front: Dict[str, Any] = {"description-meta": summary["text"], "page-layout": "full"}
    return {"page": {"source": src, "kind": "Lens", "key": key, "subject": sid, "categories": [],
                     "members": len(mapped["entries"]), "updated": updated, "listed": [],
                     "href": page["href"], "title": str(F.prop(node, "title") or key), "layout": LAYOUT,
                     "entries": [e["title"] for e in mapped["entries"]], "skipped": mapped["skipped"],
                     "recent": [r["href"] for r in recent], "text": render_page(front, body=body)}}
