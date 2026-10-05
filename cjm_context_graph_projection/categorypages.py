"""CATEGORY PAGES (design a62f2499 under the category model 0f7fcdcb (5), work item 779c7a79).

Every category has a URL: a `site_path` fact on its facet vocabulary Entity, `/categories/<slug>/`
(one flat namespace, the slug drawn from the display name once, when the path is asserted), so a
rename is a supersession whose redirect the build projects like every other page's. Whether the
URL serves a PAGE is derived at each build, never stored: an entry whose posts under the profile
number at least the index Lens's `category_page_min` gets a page, an entry below it a redirect to
the category listing filtered to it, so a category that falls below the threshold never leaves a
broken link. A retired entry's URL redirects to the category index.

A category page is projected from its Entity through the Lens page machinery -- no Lens node is
minted per entry, since the selection is a pure function of the entry: the posts the index Lens
selects whose chips (categories.py, the one reader) carry the entry's display name, listed with
the index Lens's sort, the sort and filter UI and a feed. The index (a Lens whose view layout is
`category-index`) lists every entry that earns a page, grouped by kind in the chip order, each
with its post count and description; under the public profile a public post its selection leaves
out refuses, since every count would read short."""

import posixpath
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.vocab import DevNodeKinds

from . import factlayer as F
from .runtime import GraphHandle

CATEGORY_ROOT = "/categories/"   # The namespace a new entry's path is drawn in (a62f2499 (2))
PAGE_LAYOUT = "category"         # A planned category page's layout
INDEX_LAYOUT = "category-index"  # The Lens view layout that projects the category index
# The index's kind headings, in the chip order (a62f2499 (5))
KIND_HEADINGS = {P.ENTITY_TASK: "Tasks", P.ENTITY_STAGE: "Stages", P.ENTITY_MODEL: "Models",
                 P.ENTITY_TOOL: "Tools", P.ENTITY_SUBJECT: "Subjects"}
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def category_slug(
    name: str,  # An entry's display name ("conda / Mamba")
) -> str:  # Its path slug ("conda-mamba"); "" when the name has no letter or digit
    return _SLUG_RE.sub("-", name.lower()).strip("-")


async def plan_category_paths(
    gx: GraphHandle,
    pages: Dict[str, Dict[str, Any]],   # redirect_plan's {subject: {active, superseded}}
) -> Dict[str, Any]:  # {assert: [{subject, entry, name, value, supersede?}], held, errors}
    """The `site_path` each live facet entry should hold (a62f2499 (1)): `/categories/<slug>/`,
    the slug drawn from its display name. An entry with none takes it; an entry whose name changed
    since its path was drawn takes the new one, superseding the old (a RENAME -- its redirect is
    then projected). A path outside the namespace is left alone (never drawn by this plan). A name
    with no slug, or a path another node holds (active or superseded) or another planned entry
    takes, refuses the whole plan -- nothing is guessed."""
    from .facetjudge import load_facet_vocab
    from .sitelinks import site_path_key
    held: Dict[str, str] = {}
    for subject, p in pages.items():
        for v in [p["active"], *p.get("superseded", [])]:
            held.setdefault(site_path_key(v), subject)
    plan: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    have = 0
    for entry, v in (await load_facet_vocab(gx)).items():
        name = str(v.get("name") or v["key"])
        slug = category_slug(name)
        if not slug:
            errors.append({"kind": "category-slug", "entry": entry, "name": name,
                           "why": "the display name gives no path slug"})
            continue
        value = f"{CATEGORY_ROOT}{slug}/"
        current = (pages.get(v["id"]) or {}).get("active")
        if current is not None and (current == value or not current.startswith(CATEGORY_ROOT)):
            have += 1
            continue
        owner = held.get(site_path_key(value))
        if owner is not None and owner != v["id"]:
            errors.append({"kind": "category-path-taken", "entry": entry, "path": value, "holder": owner,
                           "why": "another node already holds the path this entry would take"})
            continue
        held[site_path_key(value)] = v["id"]
        plan.append({"subject": v["id"], "entry": entry, "name": name, "value": value,
                     **({"supersede": [current]} if current else {})})
    return {"assert": [] if errors else plan, "held": have, "errors": errors}


async def category_page_min(
    gx: GraphHandle,
    lens_id: str,  # The category index's Lens id
) -> Dict[str, Any]:  # {min} | {error}
    """The index Lens's active `category_page_min` (a62f2499 (3)): one positive integer."""
    slot = [a for a in await F.load_assertions(gx)
            if F.prop(a, "predicate") == P.CATEGORY_PAGE_MIN and str(F.prop(a, "subject_id")) == lens_id]
    vals = [str(F.prop(a, "value") or "") for a in F.active_assertions(slot, await F.load_supersedes(gx))]
    if len(vals) != 1:
        return {"error": f"the category index needs exactly one active {P.CATEGORY_PAGE_MIN} (has {len(vals)})"}
    if not vals[0].strip().isdigit() or int(vals[0]) < 1:
        return {"error": f"{P.CATEGORY_PAGE_MIN} must be a positive integer (is {vals[0]!r})"}
    return {"min": int(vals[0])}


def _stub(
    active: str,   # The entry's active site_path
    target: str,   # The page path it redirects to ("/blog.html"), its fragment kept
    subject: str,  # The entry's id
) -> Dict[str, Any]:  # A redirect stub in redirect_plan's shape
    from .site import output_href
    stub = output_href(active)
    path, sep, frag = target.partition("#")
    rel = posixpath.relpath(output_href(path), posixpath.dirname(stub) or ".")
    return {"stub": stub, "alias": active, "subject": subject, "target": rel + sep + frag}


async def plan_category_pages(
    gx: GraphHandle,
    index_lenses: List[Any],            # The Lens nodes whose view layout is category-index
    pages: Dict[str, Dict[str, Any]],   # redirect_plan's {subject: {active, superseded}}
    root: Path,                         # The site project root (resolved)
    profile: str,                       # public | staging
    states: Dict[str, List[str]],
    types: Dict[str, Dict[str, Any]],
    drafts: Optional[Path],
    chips: Dict[str, Any],              # categories.load_post_categories' result
    listing_href: str,                  # The category listing's page path ("" = none)
) -> Dict[str, Any]:  # {pages: [planned entries], stubs, links: {chip: page path}, errors}
    """Plan the category index and every category page, and the redirect stub of every entry
    below the threshold. `links` names each earned page by its chip text: every chip on the site
    links it (a62f2499 (7))."""
    from .facetjudge import load_facet_vocab
    from .lens import LENS_LABEL, apply_lens
    from .sitepages import (LENS_LISTING, _listed, listing_categories, listing_items, page_source,
                            render_page, unlisted_posts, with_category_links)
    out: Dict[str, Any] = {"pages": [], "stubs": [], "links": {}, "errors": []}
    vocab = await load_facet_vocab(gx)
    pathed = [v for v in vocab.values() if v["id"] in pages]
    if not index_lenses:
        if pathed:
            out["errors"].append({"kind": "category-index", "entries": len(pathed),
                                  "why": "facet entries carry site_paths but no Lens projects the category index"})
        return out
    if len(index_lenses) > 1:
        out["errors"].append({"kind": "category-index", "subjects": [str(F.nid(n)) for n in index_lenses],
                              "why": "two Lenses project the category index"})
        return out
    node = index_lenses[0]
    sid, key = str(F.nid(node)), str(F.prop(node, "key") or "")
    page = pages.get(sid)
    src = page_source(page["active"]) if page else None
    if src is None:
        out["errors"].append({"kind": "category-index", "subject": sid,
                              "why": "the category index's Lens has no site_path naming a page file"})
        return out
    threshold = await category_page_min(gx, sid)
    applied = await apply_lens(gx, key)
    if threshold.get("error") or applied.get("error") or applied.get("truncated"):
        out["errors"].append({"kind": "category-index", "subject": sid,
                              "why": threshold.get("error") or applied.get("error")
                              or "the Lens selection was truncated; every count would read short"})
        return out
    members = [n for n in applied["nodes"] if n.get("label") == DevNodeKinds.NOTE]
    listed = _listed(members, src, root, profile, states, types, drafts, sid, post_cats=chips["posts"])
    out["errors"] += listed["errors"]
    if profile == "public":
        missing = unlisted_posts(set(listed["ids"]), states, types)
        if missing:
            out["errors"].append({"kind": "category-index-incomplete", "subject": sid, "missing": missing,
                                  "why": "the category index's selection leaves out public posts; its counts would read short"})
    if out["errors"]:
        return out
    by_id = {str(F.nid(n)): n for n in members}
    view = applied.get("view") or {}
    sort = list(view.get("sort") or [])
    # The posts carrying each chip, in the selection's order; an entry with no path is no page
    carrying: Dict[str, List[str]] = {}
    for i in listed["ids"]:
        for c in chips["posts"].get(i, []):
            carrying.setdefault(c, []).append(i)
    earned: List[Dict[str, Any]] = []
    for v in vocab.values():
        name = str(v.get("name") or v["key"])
        p = pages.get(v["id"])
        if p is None:
            out["errors"].append({"kind": "category-path", "entry": f"{v['entity_kind']}:{v['key']}", "name": name,
                                  "why": "a facet entry has no site_path -- run category-paths"})
            continue
        ids = carrying.get(name, [])
        if len(ids) >= threshold["min"]:
            earned.append({"entry": v, "name": name, "ids": ids, "href": p["active"],
                           "source": page_source(p["active"])})
            out["links"][name] = p["active"]
        elif listing_href:   # below the threshold: the category listing filtered to it (a62f2499 (3))
            out["stubs"].append(_stub(p["active"], f"{listing_href}#category={quote(name, safe='')}", v["id"]))
        else:
            out["errors"].append({"kind": "category-stub", "name": name,
                                  "why": "a category below the threshold redirects into the category listing, and the site names none"})
    # A retired entry's URL redirects to the index (a62f2499 (3): no link breaks)
    live = {v["id"] for v in vocab.values()}
    kinds = set(KIND_HEADINGS)
    retired = await F.load_nodes(gx, sorted(s for s in pages if s not in live))
    for s, n in sorted(retired.items()):
        if n is not None and F.label(n) == DevNodeKinds.ENTITY and F.prop(n, "entity_kind") in kinds:
            out["stubs"].append(_stub(pages[s]["active"], page["active"], s))
    for e in earned:
        if e["source"] is None:
            out["errors"].append({"kind": "page-path", "subject": e["entry"]["id"], "path": e["href"],
                                  "why": "the category's site_path names no page file"})
    if out["errors"]:
        return out
    links = out["links"]
    for e in earned:
        csrc = e["source"]
        got = _listed([by_id[i] for i in e["ids"]], csrc, root, profile, states, types, drafts, e["entry"]["id"],
                      post_cats=chips["posts"])
        listing = {**({"sort": sort} if sort else {}), **LENS_LISTING, "feed": True}
        listing = with_category_links({"contents": listing_items(got["contents"], csrc, got["cats"]), **listing}, csrc,
                                      listing_categories(got["contents"], csrc, got["cats"]), listing_href, pages=links)
        front: Dict[str, Any] = {"title": e["name"]}
        if e["entry"].get("description"):
            front["description"] = str(e["entry"]["description"])
        if got["updated"]:
            front["date-modified"] = got["updated"]
        front["listing"] = listing
        e["updated"] = got["updated"]
        out["pages"].append({"source": csrc, "kind": DevNodeKinds.ENTITY,
                             "key": f"{e['entry']['entity_kind']}:{e['entry']['key']}", "subject": e["entry"]["id"],
                             "categories": [], "members": len(got["contents"]), "updated": got["updated"],
                             "listed": got["ids"], "href": e["href"], "sequence": False, "title": e["name"],
                             "description": str(e["entry"].get("description") or ""),
                             "layout": PAGE_LAYOUT, "entity_kind": e["entry"]["entity_kind"],
                             "text": render_page(front)})
    dates = [e["updated"] for e in earned if e["updated"]]
    front = {"title": F.prop(node, "title") or key}
    if F.prop(node, "description"):
        front["description"] = F.prop(node, "description")
    if dates:
        front["date-modified"] = max(dates)
    out["pages"].append({"source": src, "kind": LENS_LABEL,
                         "key": key, "subject": sid, "categories": [], "members": len(earned),
                         "updated": max(dates) if dates else None, "listed": [], "href": page["active"],
                         "sequence": False, "title": str(F.prop(node, "title") or key), "layout": INDEX_LAYOUT,
                         "links": dict(links), "text": render_page(front, body=index_body(earned, src))})
    return out


def index_body(
    earned: List[Dict[str, Any]],  # The earned categories: {entry, name, ids, source}, in chip order
    page_src: str,                 # The index's source, relative to the root
) -> str:  # The index's body: a section per kind, each category linked with its count and description
    lines: List[str] = []
    for kind, heading in KIND_HEADINGS.items():
        rows = [e for e in earned if e["entry"]["entity_kind"] == kind]
        if not rows:
            continue
        lines += [f"## {heading}", ""]
        for e in rows:
            rel = posixpath.relpath(e["source"], posixpath.dirname(page_src) or ".")
            n = len(e["ids"])
            desc = str(e["entry"].get("description") or "").strip()
            lines.append(f"- [{e['name']}]({rel}) · {n} post{'' if n == 1 else 's'}" + (f" · {desc}" if desc else ""))
        lines.append("")
    return "\n".join(lines)
