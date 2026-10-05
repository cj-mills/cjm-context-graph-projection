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
out refuses, since every count would read short.

A page is introduced by its entry's `page_description` (amendment e38d403c), never the judge's
criteria: the page's lede, its index line and its meta description all read it. An earned page
with none refuses a public build; staging renders the gap marked. A description defines the
category's SCOPE, never what it holds today, so adding posts never stales it; what it was written
against is the entry's criteria, stamped beside it (`page_description_basis`), and a criteria
change since re-surfaces it -- reported by the build, flagged in the document. `describe-categories`
writes the review document the descriptions are drafted and reviewed in, and lands the edited one
whole."""

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
# What staging shows where an earned page has no page_description (e38d403c (3)); public refuses
UNDESCRIBED = "⚠ No page description yet (page_description; describe-categories)."
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
) -> Dict[str, Any]:  # {pages: [planned entries], stubs, links: {chip: page path}, undescribed, errors}
    """Plan the category index and every category page, and the redirect stub of every entry
    below the threshold. `links` names each earned page by its chip text: every chip on the site
    links it (a62f2499 (7)). `undescribed` names the earned pages with no page_description; under
    the public profile they refuse (e38d403c (3)) -- after the pages are planned, so the review
    document can still read them. `stale_descriptions` names the earned pages whose criteria
    moved since their description was written: reported, never refused."""
    from .facetjudge import load_facet_vocab
    from .lens import LENS_LABEL, apply_lens
    from .sitepages import (LENS_LISTING, _listed, listing_categories, listing_items, page_source,
                            render_page, unlisted_posts, with_category_links)
    out: Dict[str, Any] = {"pages": [], "stubs": [], "links": {}, "undescribed": [], "stale_descriptions": [],
                           "errors": []}
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
            desc = str(v.get("page_description") or "").strip()
            if not desc:
                out["undescribed"].append(f"{v['entity_kind']}:{v['key']}")
            elif description_stale(v):
                out["stale_descriptions"].append(f"{v['entity_kind']}:{v['key']}")
            earned.append({"entry": v, "name": name, "ids": ids, "href": p["active"],
                           "source": page_source(p["active"]), "description": desc or UNDESCRIBED})
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
        front: Dict[str, Any] = {"title": e["name"], "description": e["description"]}
        if got["updated"]:
            front["date-modified"] = got["updated"]
        front["listing"] = listing
        e["updated"] = got["updated"]
        out["pages"].append({"source": csrc, "kind": DevNodeKinds.ENTITY,
                             "key": f"{e['entry']['entity_kind']}:{e['entry']['key']}", "subject": e["entry"]["id"],
                             "categories": [], "members": len(got["contents"]), "updated": got["updated"],
                             "listed": got["ids"], "href": e["href"], "sequence": False, "title": e["name"],
                             "description": e["description"],
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
    if profile == "public" and out["undescribed"]:   # the criteria never reach readers silently
        out["errors"].append({"kind": "category-undescribed", "entries": list(out["undescribed"]),
                              "why": f"{len(out['undescribed'])} earned category page(s) have no page_description "
                                     "-- run describe-categories"})
    return out


def index_body(
    earned: List[Dict[str, Any]],  # The earned categories: {entry, name, ids, source, description}, in chip order
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
            desc = e["description"]
            lines.append(f"- [{e['name']}]({rel}) · {n} post{'' if n == 1 else 's'}" + (f" · {desc}" if desc else ""))
        lines.append("")
    return "\n".join(lines)


# The description review (amendment e38d403c (5)): one document, drafted then reviewed whole
_SECTION_RE = re.compile(r"^## .*<!-- category (\S+) (\S+) -->\s*$")
DESCRIPTION_LABEL = "Page description:"


def entry_record(
    entry: Dict[str, Any],  # A live facet entry (load_facet_vocab's value)
) -> Dict[str, Any]:  # Its WHOLE entity record {kind, key, name, fields}, as entity-batch takes it
    from .coverage import ENTITY_FIELDS
    kind = entry["entity_kind"]
    return {"kind": kind, "key": str(entry["key"]), "name": str(entry.get("name") or entry["key"]),
            "fields": {f: entry[f] for f in ENTITY_FIELDS[kind] if entry.get(f) is not None}}


def description_criteria(
    entry: Dict[str, Any],  # A facet entry's record: {entity_kind, key, name, description, not_for, ...}
) -> str:  # The criteria hash a page_description is written against (the judge's own identity of the entry)
    from .facetjudge import criteria_hash, entry_question
    return criteria_hash(entry_question(entry))


def description_stale(
    entry: Dict[str, Any],  # A live facet entry
) -> bool:  # Has its criteria changed since its page_description was written?
    return bool(entry.get("page_description")) and entry.get("page_description_basis") != description_criteria(entry)


def description_basis(
    entry: Dict[str, Any],  # A live facet entry
) -> str:  # What the document was written against (12 hex): the whole record, page_description too
    from .judgeengine import digest
    return digest(entry_record(entry), 12)


async def description_state(
    gx: GraphHandle,
    website_root: str,                 # The site project root
    drafts_dir: Optional[str] = "drafts",
) -> Dict[str, Any]:  # {entries: [{entry, name, posts: [{title, description}], basis, ...}], errors}
    """Every category page the PUBLIC build earns, in the index's order, with the posts it lists
    (each by what its page states) -- what a description is drafted from. Any planning error but
    the description gap itself refuses."""
    from .facetjudge import load_facet_vocab
    from .site import redirect_plan, stated
    from .sitepages import page_plan
    rp = await redirect_plan(gx)
    if rp["errors"]:
        return {"entries": [], "errors": rp["errors"]}
    plan = await page_plan(gx, website_root, "public", rp["pages"], drafts_dir)
    errors = [e for e in plan["errors"] if e.get("kind") != "category-undescribed"]
    if errors:
        return {"entries": [], "errors": errors}
    by_id = {v["id"]: (e, v) for e, v in (await load_facet_vocab(gx)).items()}
    pages = [p for p in plan["pages"] if p.get("layout") == PAGE_LAYOUT]
    notes = await F.load_nodes(gx, sorted({i for p in pages for i in p["listed"]}))
    entries = []
    for p in pages:
        e, v = by_id[p["subject"]]
        entries.append({"entry": e, "kind": v["entity_kind"], "key": str(v["key"]), "name": p["title"],
                        "criteria": str(v.get("description") or ""), "not_for": str(v.get("not_for") or ""),
                        "page_description": str(v.get("page_description") or ""), "basis": description_basis(v),
                        "stale": description_stale(v),
                        "posts": [{"title": stated(notes.get(i), "title"), "description": stated(notes.get(i), "description")}
                                  for i in p["listed"]]})
    order = list(KIND_HEADINGS)   # the index's order: kinds in the chip order, entries as the vocabulary lists them
    return {"entries": sorted(entries, key=lambda e: order.index(e["kind"])), "errors": []}


def description_document(
    entries: List[Dict[str, Any]],  # description_state's entries
) -> str:  # The markdown the descriptions are drafted and reviewed in, handed back to --apply
    done = sum(1 for e in entries if e["page_description"])
    stale = sum(1 for e in entries if e["stale"])
    out = [f"# Category descriptions · {len(entries)} earned page(s) · {done} described · {len(entries) - done} to write"
           + (f" · {stale} to re-read" if stale else ""), "",
           f"Each section is one category page. Its reader-facing description goes on the `{DESCRIPTION_LABEL}` "
           "line (continuing until the next blank line). A description defines the category's SCOPE -- what belongs "
           "in it, in general terms a reader understands -- never what the page holds today: it names a sub-area only "
           "when the sub-area is part of what the term means, never because a post, series or companion tool happens "
           "to be listed, so adding or reworking posts never stales it. Clear, concise and purely descriptive: no "
           "marketing, no first person, no criteria phrasing (\"the post's own matter\", \"not a mention\", a "
           "sibling entry's name), no claims about the posts' quality (amendment e38d403c (5), (6)). Draft from the "
           "judge's criteria, which already state the scope; the posts are a check that the wording matches how the "
           "term is used here. Neither lands. A description marked ⚠ was written against criteria that have since "
           "changed: re-read it -- leaving it in the file re-confirms it against the current criteria. Then "
           "`cg-write --notes describe-categories --apply <this file>`: every changed or re-confirmed description "
           "lands as one journaled batch, or nothing does if an entry changed since this document was written. A "
           "description left blank stays unwritten. Keep each heading's trailing comment.", ""]
    for e in entries:
        n = len(e["posts"])
        out += [f"## {e['name']} · {e['kind']} `{e['key']}` · {n} post{'' if n == 1 else 's'} "
                f"<!-- category {e['entry']} {e['basis']} -->", "",
                *([("⚠ The criteria changed since this description was written: re-read it against them."), ""]
                  if e["stale"] else []),
                f"{DESCRIPTION_LABEL} {e['page_description']}".rstrip(), "",
                f"_Criteria (the judge's): {e['criteria']}" + (f" · not for: {e['not_for']}" if e["not_for"] else "") + "_",
                "", f"<details><summary>{n} post{'' if n == 1 else 's'}</summary>", ""]
        out += [f"- {p['title']}" + (f" -- {p['description']}" if p["description"] else "") for p in e["posts"]]
        out += ["", "</details>", ""]
    return "\n".join(out)


def parse_descriptions(
    text: str,  # An edited description document
) -> Dict[str, Any]:  # {rows: [{entry, basis, text}], errors}
    """Each section's entry, basis and description (whitespace collapsed to single spaces). A
    section with no description line, or two, or an entry named twice is an error: the file is
    refused whole."""
    rows: List[Dict[str, Any]] = []
    errors: List[str] = []
    cur: Optional[Dict[str, Any]] = None
    collecting = False
    for i, ln in enumerate(text.splitlines(), 1):
        m = _SECTION_RE.match(ln)
        if m:
            cur = {"entry": m.group(1), "basis": m.group(2), "text": None, "line": i}
            if any(r["entry"] == cur["entry"] for r in rows):
                errors.append(f"line {i}: the entry {cur['entry']} appears twice")
            rows.append(cur)
            collecting = False
            continue
        if ln.startswith("## ") or "<!-- category " in ln:
            if "<!-- category " in ln:
                errors.append(f"line {i}: a section heading the document cannot read: {ln.strip()[:120]}")
            cur, collecting = None, False
            continue
        if cur is None:
            continue
        if ln.startswith(DESCRIPTION_LABEL):
            if cur["text"] is not None:
                errors.append(f"line {i}: {cur['entry']} has a second {DESCRIPTION_LABEL!r} line")
            cur["text"], collecting = ln[len(DESCRIPTION_LABEL):], True
        elif collecting and ln.strip():
            cur["text"] += " " + ln
        else:
            collecting = False
    for r in rows:
        if r["text"] is None:
            errors.append(f"line {r['line']}: {r['entry']} has no {DESCRIPTION_LABEL!r} line")
        r["text"] = " ".join(str(r.pop("text") or "").split())
        r.pop("line")
    return {"rows": rows, "errors": errors}


async def plan_descriptions(
    gx: GraphHandle,
    text: str,   # The edited description document
) -> Dict[str, Any]:  # {records: [whole entity records], counts, rows, errors}
    """Check every section against the graph (a live facet entry, its record unchanged since the
    document was written) and plan the batch: each changed description -- or an unchanged one whose
    criteria moved since it was written, re-confirmed by the review -- becomes its entry's whole
    record with the page_description and the criteria hash it now answers to; the judge's fields
    ride unchanged, so nothing re-judges."""
    from .facetjudge import load_facet_vocab
    parsed = parse_descriptions(text)
    errors = list(parsed["errors"])
    vocab = await load_facet_vocab(gx)
    records: List[Dict[str, Any]] = []
    counts = {"changed": 0, "reconfirmed": 0, "unchanged": 0, "blank": 0}
    for r in parsed["rows"]:
        v = vocab.get(r["entry"])
        if v is None:
            errors.append(f"{r['entry']}: no live facet entry")
            continue
        if description_basis(v) != r["basis"]:
            errors.append(f"{r['entry']}: the entry changed since the document was written "
                          "(write a fresh one with describe-categories --out)")
            continue
        if not r["text"]:
            counts["blank"] += 1
        elif r["text"] == str(v.get("page_description") or "") and not description_stale(v):
            counts["unchanged"] += 1
        else:
            counts["changed" if r["text"] != str(v.get("page_description") or "") else "reconfirmed"] += 1
            rec = entry_record(v)
            rec["fields"].update(page_description=r["text"], page_description_basis=description_criteria(v))
            records.append(rec)
    return {"records": records, "counts": counts, "rows": len(parsed["rows"]), "errors": errors}


async def describe_categories(
    gx: GraphHandle,
    *,
    website_root: Optional[str] = None,  # Writing the document: the site project root
    drafts_dir: Optional[str] = "drafts",
    apply_text: Optional[str] = None,    # An edited document to land; None = write a fresh one
    dry_run: bool = False,               # With apply_text: plan and check only
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {document, counts} | {counts, rows, errors, landed: [records], written}
    """The description verb (amendment e38d403c (5)): the document, or one landed review -- the
    changed records go through mint_entities, checked whole, one `entity` op each."""
    from .coverage import mint_entities
    if apply_text is None:
        if not website_root:
            return {"error": "writing the document needs the site project root (--website-root)", "written": False}
        st = await description_state(gx, website_root, drafts_dir)
        if st["errors"]:
            return {"errors": st["errors"], "error": f"{len(st['errors'])} planning error(s)", "written": False}
        es = st["entries"]
        return {"document": description_document(es), "written": False,
                "counts": {"pages": len(es), "described": sum(1 for e in es if e["page_description"])}}
    plan = await plan_descriptions(gx, apply_text)
    out: Dict[str, Any] = {"counts": plan["counts"], "rows": plan["rows"], "errors": plan["errors"],
                           "landed": [], "written": False}
    if plan["errors"]:
        return {**out, "error": f"{len(plan['errors'])} section(s) refused -- nothing written"}
    res = await mint_entities(gx, plan["records"], apply=not dry_run, actor=actor)
    if res["errors"]:
        return {**out, "errors": res["errors"], "error": "the batch was refused -- nothing written"}
    return {**out, "landed": res["landed"], "written": res["written"]}
