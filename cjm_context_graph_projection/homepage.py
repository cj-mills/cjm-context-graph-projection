"""The HOME PAGE: a projected map of the site under one identity line (design e55201e2, amendment
5c3c2662, under the redesign build 8079ae0f; work item f0ec7156).

A Lens whose view layout is `home` owns the page: its site_path the location (the root) and its
selection the MAP -- one `subgraph` clause naming the index
Lenses in the order the map shows them (the entry order is that list, never template text).
Everything else is derived at build and nothing is stored:

1. the IDENTITY line is the site's one sentence (`site-summary`, filled from `site-author`;
   5c3c2662 (1)), which llms.txt's intro reads too, and the CONTACT line is the author strip's
   links copy (5c3c2662 (2));
2. each MAP ENTRY is an index page the build planned: its title linking it, its Lens's own
   description (the page states it too, 12d98020; 5c3c2662 (4)), and the first `home_hubs` of the
   HUB pages the index states, in the order the index shows them (5c3c2662 (5)) -- never
   re-ranked. An entry whose page lists no content under the profile renders nothing (e55201e2
   (2)); a named node with no planned page, or a page that states no hubs, refuses;
3. RECENT POSTS: the `home_recent` newest by their public dates (postpage.post_dates, the one
   derivation the header, the footer and the JSON-LD read), each with its updated or published
   day, the heading linking the category listing (e55201e2 (3));
4. the WORK-WITH-ME band renders only once its page is published (postpage.pitch_target, the
   author strip's pitch rule; 5c3c2662 (2)).

The page lists no post as a member, so no post names the home page as a collection, and llms.txt
never lists it (it stands in for the site root). There is no most-read list: traffic is not yet a
captured fact (e55201e2 (6))."""

import posixpath
from pathlib import Path
from typing import Any, Dict, List, Optional

from cjm_dev_graph_schema import predicates as P

from . import factlayer as F
from .runtime import GraphHandle

LAYOUT = "home"   # The Lens view layout this page projects
RECENT_HEADING = "Recent posts"
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


def map_entries(
    refs: List[str],                      # The map's index Lens ids, in order
    planned: List[Dict[str, Any]],        # Every other planned page
    descriptions: Dict[str, str],         # {Lens id: its description}
    hubs: int,                            # How many hub pages an entry shows (home_hubs)
    page_src: str,                        # The home page's source, relative to the root
    profile: str,                         # public | staging
) -> Dict[str, Any]:  # {entries: [{title, href, description, hubs: [{title, href}]}], skipped, errors}
    by_subject = {p["subject"]: p for p in planned}

    def rel(src: str) -> str:   # a link relative to the home page's source, as Quarto resolves it
        return posixpath.relpath(src, posixpath.dirname(page_src) or ".")
    out: Dict[str, Any] = {"entries": [], "skipped": [], "errors": []}
    for ref in refs:
        p = by_subject.get(ref)
        if p is None:
            out["errors"].append({"kind": "home-entry", "entry": ref,
                                  "why": "the home page's map names a node with no planned page"})
            continue
        if p.get("hubs") is None:
            out["errors"].append({"kind": "home-entry", "entry": ref, "source": p["source"],
                                  "why": "the home page's map names a page that states no hubs (an index page does)"})
            continue
        if not p.get("members"):
            out["skipped"].append(p["source"])   # nothing public to point at (e55201e2 (2))
            continue
        desc = str(descriptions.get(ref) or "").strip()
        if not desc:
            if profile == "public":
                out["errors"].append({"kind": "home-entry-undescribed", "entry": ref, "source": p["source"],
                                      "why": "a map entry's index Lens has no description for the entry to state"})
                continue
            desc = UNDESCRIBED
        out["entries"].append({"title": p["title"], "href": rel(p["source"]), "description": desc,
                               "hubs": [{"title": h["title"], "href": rel(h["source"])} for h in p["hubs"][:hubs]]})
    return out


def recent_posts(
    posts: List[Dict[str, Any]],  # [{title, href, published, updated}] -- public dates (post_dates)
    n: int,                       # How many (home_recent)
) -> List[Dict[str, Any]]:  # The n newest by updated day, then published, then title
    dated = [p for p in posts if p.get("published")]
    dated = sorted(dated, key=lambda p: str(p.get("title") or "").casefold())
    dated = sorted(dated, key=lambda p: (p.get("updated") or p["published"], p["published"]), reverse=True)
    return dated[:n]


def home_body(
    summary: str,                       # The site's one sentence
    contact: str,                       # The author strip's links copy
    entries: List[Dict[str, Any]],      # map_entries' entries
    recent: List[Dict[str, Any]],       # recent_posts' rows, each with its href
    listing: str,                       # The category listing's href relative to the page ("" = none)
    band: Optional[Dict[str, str]] = None,  # The Work-with-me page {title, href}, once published
) -> str:  # The page's body
    from .postpage import display_date
    lines = [summary, "", contact, "", "::: {.grid}", ""]
    for e in entries:
        lines += ["::: {.g-col-12 .g-col-md-6}", "", f"## [{e['title']}]({e['href']})", "", e["description"], ""]
        lines += [f"- [{h['title']}]({h['href']})" for h in e["hubs"]]
        lines += ["", ":::", ""]
    lines += [":::", ""]
    if recent:
        lines += [f"## [{RECENT_HEADING}]({listing})" if listing else f"## {RECENT_HEADING}", ""]
        for r in recent:
            updated = r.get("updated") and r["updated"] != r["published"]
            day = display_date(r["updated"] if updated else r["published"])
            lines.append(f"- [{r['title']}]({r['href']}) · {'Updated ' if updated else ''}{day}")
        lines.append("")
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
    listing_href: str = "",                  # The category listing's page path ("" = none)
) -> Dict[str, Any]:  # {page: planned entry} | {errors}
    """Plan the home page: the identity and contact lines, the map, the recent posts, the band."""
    from .lens import lens_count
    from .postpage import POST_KINDS, header_facts, load_site_summary, load_strip_copy, pitch_target, post_dates
    from .site import stated
    from .sitepages import _listed, page_source, render_page
    sid, key, src = str(F.nid(node)), str(F.prop(node, "key") or ""), page["source"]
    errors: List[Dict[str, Any]] = []
    summary = load_site_summary(website_root)
    strip = load_strip_copy(website_root)
    errors += summary["errors"] + strip["errors"]
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
    lenses = await F.load_nodes(gx, refs["refs"])
    descriptions = {r: str(F.prop(n, "description") or "") for r, n in lenses.items() if n is not None}
    mapped = map_entries(refs["refs"], planned, descriptions, counts[P.HOME_HUBS], src, profile)
    errors += mapped["errors"]
    # Every post the profile renders, with its public dates
    post_ids = sorted(n for n, t in types.items() if t.get("kind") in POST_KINDS)
    notes = await F.load_nodes(gx, post_ids)
    members = [notes[i] for i in post_ids if notes.get(i) is not None]
    listed = _listed(members, src, root, profile, states, types, drafts, sid)
    errors += listed["errors"]
    if errors:
        return {"errors": errors}
    facts = await header_facts(gx)
    posts = []
    for nid, href in zip(listed["ids"], listed["contents"]):
        t = types.get(nid) or {}
        dates = post_dates(t.get("origin", ""), F.prop(notes[nid], "metadata") or {}, facts.get(nid, {}))
        posts.append({"title": stated(notes[nid], "title"), "href": href, **dates})
    recent = recent_posts(posts, counts[P.HOME_RECENT])
    listing = page_source(listing_href) if listing_href else None
    band = pitch_target(planned)
    if band:
        band = {"title": band["title"], "href": posixpath.relpath(page_source(band["href"]) or band["href"],
                                                                  posixpath.dirname(src) or ".")}
    body = home_body(summary["text"], strip["copy"]["links"], mapped["entries"], recent,
                     posixpath.relpath(listing, posixpath.dirname(src) or ".") if listing else "", band)
    updated = max((r.get("updated") or r["published"] for r in recent), default=None)
    # No title block: the identity line opens the page. No page title (the site root's tab is the
    # site's own title), the summary only as the meta description, and no dates (the title block
    # would show them; the sitemap's lastmod reads the file time, stamped from `updated`). The
    # site's title partials refuse `title-block-style: none`, so the block is left empty instead.
    front: Dict[str, Any] = {"description-meta": summary["text"], "page-layout": "full"}
    return {"page": {"source": src, "kind": "Lens", "key": key, "subject": sid, "categories": [],
                     "members": len(mapped["entries"]), "updated": updated, "listed": [],
                     "href": page["href"], "title": str(F.prop(node, "title") or key), "layout": LAYOUT,
                     "entries": [e["title"] for e in mapped["entries"]], "skipped": mapped["skipped"],
                     "recent": [r["href"] for r in recent], "text": render_page(front, body=body)}}
