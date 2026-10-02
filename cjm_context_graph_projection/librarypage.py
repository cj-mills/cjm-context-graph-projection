"""The Library's pages: the index a Lens with the `library` view layout projects, and a work page
for every work with units (design 638b7b85 under the redesign build 8079ae0f; designs 5de7fae9
(1)-(2), 4a4ef27e (4); the data of builds 0b95f807 + ed059bda).

The Lens owns the index (its site_path, title, description, date); a work Entity owns its work
page (its site_path fact, its record). Everything else is derived at build from
`library.library_index` and nothing is stored: the form groups, each entry's outputs by output
class in value order, the units by part and position, a pure-notes output's synopsis rolled up
under its unit (ruling a7ca900d (2)), the topic line from the topical Lens pages.

Under the public profile only public outputs count: a unit shows only with a public output, a
work page exists only when one of its units does, and a work with no visible output is left off
the index (5de7fae9 (2)). Staging lists the drafts too, marked, with each unit's source count
(the metabolism signal). A work page is an ORDERED collection (`sequence`): the post navigation,
JSON-LD isPartOf and llms.txt read it as they read a Series page, over the outputs of one class
in unit order (638b7b85 (5)).

REFUSED, never dropped (6752db0a (9)): a Library refusal, a form with no group heading, a work
whose units show with no site_path to hold its page, a work page whose superseded paths would
redirect to a page the profile does not render, a topic page mixing notes with other kinds."""

import posixpath
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from cjm_context_graph_primitives.query import PropertyPredicate
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.vocab import DevNodeKinds

from . import factlayer as F
from .runtime import GraphHandle
from .tutorialspage import _md_text, anchor

LAYOUT = "library"        # The Lens view layout that projects the Library index
WORK_PAGE_KIND = "work"   # A planned work page's `kind` (beside Series and Lens)
NOTES_KIND = "notes"      # The deliverable kind a topic page lists
# The index's group headings, one per work form, in the form slate's order (P.WORK_FORMS)
FORM_GROUPS = {"book": "Books", "course": "Courses", "lecture-series": "Lecture series",
               "talk": "Talks", "video": "Videos", "documentation": "Documentation"}
SYNOPSIS_POLICY = "synopsis"   # A type whose description is its accepted synopsis point (a7ca900d (2))


def year(
    published: Any,  # A work's published date (ISO at the precision known), or None
) -> str:  # The year it renders ("" when unknown)
    return str(published or "")[:4]


def form_name(form: str) -> str:
    """A form key as a reader reads it ("lecture-series" -> "lecture series")."""
    return str(form or "").replace("-", " ")


def _link(title: str, href: str) -> str:
    return f"[{_md_text(title)}]({href})"


def _stage(out: Dict[str, Any], profile: str) -> str:
    """Staging's suffix on an output: its draft mark (a public build never sees one)."""
    return " _(draft)_" if profile == "staging" and not out.get("public") else ""


def render_index(
    groups: List[Dict[str, Any]],   # [{form, heading, works: [entry]}] in slate order, each work entry {name, author, year, page, classes: [{name, outputs: [{title, href, public}]}]}
    topics: List[Dict[str, str]],   # [{title, href}] the topical Lens pages, title order
    profile: str,                   # public | staging
) -> str:  # The index body (markdown)
    """The topic line, then one section per form: each work's name (linked to its work page when
    it has one), author and year, then its outputs by class -- a paged work as a count linking
    its page, a unitless work as the links themselves. Pure."""
    out: List[str] = []
    if topics:
        out += ["**By topic:** " + " · ".join(_link(t["title"], t["href"]) for t in topics), ""]
    for g in groups:
        out += [f"## {g['heading']} {{#{anchor(g['form'])}}}", ""]
        for w in g["works"]:
            name = f"**{_link(w['name'], w['page'])}**" if w.get("page") else f"**{_md_text(w['name'])}**"
            head = " · ".join([name] + [_md_text(b) for b in (w.get("author"), w.get("year")) if b])
            parts = []
            for c in w["classes"]:
                if w.get("page"):
                    n = len(c["outputs"])
                    parts.append(f"{c['name']}: [{n} page{'s' if n != 1 else ''}]({w['page']})")
                else:
                    parts.append(f"{c['name']}: " + " · ".join(_link(o["title"], o["href"]) + _stage(o, profile)
                                                             for o in c["outputs"]))
            out.append(f"- {head} — " + "; ".join(parts))
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def render_work(
    work: Dict[str, Any],           # {form, author?, year?, isbn?, locator?}
    units: List[Dict[str, Any]],    # [{name, part?, sources, outputs: [{title, href, public, class_name, synopsis}]}] in position order, visible ones only
    whole: List[Dict[str, Any]],    # The outputs on the whole work (same shape)
    profile: str,                   # public | staging
) -> str:  # The work page body (markdown)
    """The card, the units grouped by part, and the outputs on the whole work. A unit with one
    output links its name to it; a pure-notes output's synopsis follows it (the executive
    summary by construction, a7ca900d (2)); the class is named only when the page shows more
    than one. Staging marks the drafts and counts each unit's sources. Pure."""
    card = [form_name(work.get("form", "")).capitalize()]
    card += [_md_text(b) for b in (work.get("author"), work.get("year")) if b]
    if work.get("isbn"):
        card.append(f"ISBN {work['isbn']}")
    if work.get("locator"):
        card.append(f"[Link]({work['locator']})")
    out = ["::: {.library-work-card}", " · ".join(card), ":::", ""]
    classes = {o["class_name"] for u in units for o in u["outputs"]} | {o["class_name"] for o in whole}
    named = len(classes) > 1

    def tail(o: Dict[str, Any], src: str = "") -> str:   # the synopsis always ends the row
        return ((f" · *{o['class_name']}*" if named else "") + _stage(o, profile) + src
                + (f" — {_md_text(o['synopsis'])}" if o.get("synopsis") else ""))

    if units:
        out += ["## Contents", ""]
        # A part is a heading only when it is one run in the work's order (a book's parts); parts
        # that interleave (a course's workshops and office hours) keep the order, labelled per row
        runs = [u.get("part") for i, u in enumerate(units) if i == 0 or u.get("part") != units[i - 1].get("part")]
        headed = len(runs) == len(set(runs))
        part = None
        for u in units:
            if headed and u.get("part") and u["part"] != part:
                out += ["", f"### {_md_text(u['part'])}", ""]
                part = u["part"]
            label = f"*{_md_text(u['part'])}* · " if u.get("part") and not headed else ""
            n = len(u.get("sources") or [])
            src = f" _({n} source{'s' if n != 1 else ''})_" if profile == "staging" and n else ""
            if len(u["outputs"]) == 1:
                o = u["outputs"][0]
                out.append(f"- {label}{_link(u['name'], o['href'])}{tail(o, src)}")
            else:
                out.append(f"- {label}{_md_text(u['name'])}{src} — "
                           + " · ".join(_link(o["title"], o["href"]) + tail(o) for o in u["outputs"]))
        out.append("")
    if whole:
        out += ["## On the whole work", ""]
        out += [f"- {_link(o['title'], o['href'])}{tail(o)}" for o in whole]
        out.append("")
    return "\n".join(ln for i, ln in enumerate(out) if not (ln == "" and i and out[i - 1] == "")).rstrip("\n") + "\n"


def work_description(
    work: Dict[str, Any],   # {name, form, author?}
    classes: List[str],     # The output class names the page shows, in value order
) -> str:  # The work page's description, derived from its record
    what = " and ".join([", ".join(classes[:-1]), classes[-1]] if len(classes) > 1 else classes).capitalize()
    by = f" by {work['author']}" if work.get("author") else ""
    return f"{what} from *{work['name']}*, a {form_name(work.get('form', ''))}{by}, in the work's own order."


def topic_pages(
    planned: List[Dict[str, Any]],           # page_plan's other planned pages
    types: Dict[str, Dict[str, Any]],        # note_types' map
    page_src: str,                           # The index's source (relative to the root)
) -> Dict[str, Any]:  # {topics: [{title, href}], errors}
    """The topical Lens pages: a Lens page whose listed members are all notes; one mixing notes
    with other kinds refuses (the build never guesses whether it is a topic of the Library)."""
    topics, errors = [], []
    for p in sorted(planned, key=lambda p: str(p.get("title") or "")):
        if p.get("kind") != "Lens" or p.get("sequence") or p.get("layout"):
            continue
        # Typed members decide: an untyped Note (a staging fixture) is no deliverable of any kind
        kinds = {(types.get(i) or {}).get("kind") for i in p["listed"]} - {None}
        if NOTES_KIND not in kinds:
            continue
        if kinds != {NOTES_KIND}:
            errors.append({"kind": "topic-mixed", "subject": p["subject"], "source": p["source"],
                           "kinds": sorted(str(k) for k in kinds),
                           "why": "a topic page mixes notes with other kinds; the build never guesses "
                                  "whether it is a topic of the Library"})
            continue
        topics.append({"title": p["title"], "href": posixpath.relpath(p["source"], posixpath.dirname(page_src) or ".")})
    return {"topics": topics, "errors": errors}


async def _synopsis_types(gx: GraphHandle) -> set:
    """The deliverable types whose description is the accepted synopsis point."""
    out = set()
    for t in await F.load_label(gx, DevNodeKinds.DELIVERABLE_TYPE):
        pol = (F.prop(t, "presentation_policy") or {}).get("frontmatter") or {}
        if pol.get("description") == SYNOPSIS_POLICY:
            out.add(str(F.prop(t, "key")))
    return out


async def _locators(gx: GraphHandle) -> Dict[str, str]:
    """{Reference id: its active locator} -- the observed public URL of a source."""
    rows = await F.load_label_where(gx, DevNodeKinds.ASSERTION, [PropertyPredicate("predicate", "eq", P.LOCATOR)])
    if not rows:
        return {}
    return {str(F.prop(a, "subject_id")): str(F.prop(a, "value") or "")
            for a in F.active_assertions(rows, await F.load_supersedes(gx))}


def _front(
    title: str,
    fields: Dict[str, Any],          # description, subtitle, date -- each only when present
    updated: Optional[str],          # The derived date-modified
) -> str:
    from .sitepages import GENERATED
    front: Dict[str, Any] = {"title": title}
    for k in ("subtitle", "description", "date"):
        if fields.get(k):
            front[k] = fields[k]
    if updated:
        front["date-modified"] = updated
    head = yaml.safe_dump(front, sort_keys=False, allow_unicode=True, width=10_000)
    return f"---\n{GENERATED}\n{head}---\n\n"


async def plan_library_pages(
    gx: GraphHandle,
    node: Any,                               # The Lens node
    page: Dict[str, Any],                    # {source, href}: the index's page
    pages: Dict[str, Dict[str, Any]],        # redirect_plan's {subject: {active, superseded}}
    root: Path,                              # The site project root (resolved)
    profile: str,                            # public | staging
    states: Dict[str, List[str]],
    types: Dict[str, Dict[str, Any]],
    drafts: Optional[Path],
    planned: List[Dict[str, Any]],           # The other planned pages (the topic candidates)
) -> Dict[str, Any]:  # {pages: [planned entries], errors}
    """Plan the Library index and every work page it links: the Library under the profile, a
    work page per work whose units show, the index over every work with a visible output."""
    from .library import library_index
    from .sitepages import _listed, member_updated, page_source, parse_date
    sid, key, src = str(F.nid(node)), str(F.prop(node, "key") or ""), page["source"]
    lib = await library_index(gx)
    errors: List[Dict[str, Any]] = [
        {"kind": "library-refusal", "subject": sid, "deliverable": r["deliverable"], "reason": r["reason"],
         "why": f"the Library refuses a deliverable ({r['reason']}: {r['detail']})"} for r in lib["refusals"]]
    errors += [{"kind": "work-form", "subject": sid, "work": w["key"], "form": w.get("form"),
                "why": "a work's form has no group heading on the index"}
               for w in lib["works"] if w.get("form") not in FORM_GROUPS]
    topics = topic_pages(planned, types, src)
    errors += topics["errors"]
    if errors:
        return {"errors": errors}
    class_names = {c["key"]: c["name"] for c in lib["classes"]}
    out_ids = sorted({o["id"] for w in lib["works"] for o in w["outputs"] + [o for u in w["units"] for o in u["outputs"]]})
    nodes = await F.load_nodes(gx, out_ids)
    synopsis_types, locators = await _synopsis_types(gx), await _locators(gx)
    # The accepted synopsis POINT of each typed deliverable (a7ca900d (2)); none yet = the link alone
    from .purenotes import born_notes_by_unit
    synopses = {r["note_id"]: r["synopsis"] for rows in (await born_notes_by_unit(gx)).values() for r in rows}

    def visible(outputs: List[Dict[str, Any]], page_src: str, subject: str) -> List[Dict[str, Any]]:
        """The outputs the profile lists, each with its href relative to the page."""
        members = [nodes[o["id"]] for o in outputs if o["id"] in nodes]
        got = _listed(members, page_src, root, profile, states, types, drafts, subject)
        errors.extend(got["errors"])
        href = dict(zip(got["ids"], got["contents"]))
        res = []
        for o in outputs:
            if o["id"] not in href:
                continue
            n = nodes[o["id"]]
            md = F.prop(n, "metadata") or {}
            born = parse_date(md.get("date")) or member_updated(n)
            syn = synopses.get(o["id"], "") if (types.get(o["id"]) or {}).get("type") in synopsis_types else ""
            res.append({**o, "href": href[o["id"]], "class_name": class_names.get(o["output_class"], o["output_class"]),
                        "date": born.isoformat() if born else "", "synopsis": syn,
                        "updated": (member_updated(n) or born or None)})
        return res

    planned_out: List[Dict[str, Any]] = []
    entries: Dict[str, List[Dict[str, Any]]] = {f: [] for f in FORM_GROUPS}
    all_listed: List[str] = []
    dates: List[str] = []
    for w in lib["works"]:
        wpage = pages.get(w["id"])
        wsrc = page_source(wpage["active"]) if wpage else None
        if wpage and wsrc is None:
            errors.append({"kind": "page-path", "subject": w["id"], "path": wpage["active"],
                           "why": "the work's site_path names no page file"})
            continue
        units = []
        for u in w["units"]:
            vis = visible(u["outputs"], wsrc or src, w["id"])
            if vis:
                units.append({**u, "outputs": vis})
        whole = visible(w["outputs"], wsrc or src, w["id"])
        has_page = bool(units)
        if has_page and wpage is None:
            errors.append({"kind": "work-unpaged", "subject": w["id"], "work": w["key"],
                           "why": "a work whose units show has no site_path to hold its page"})
            continue
        if not has_page and wpage and wpage["superseded"]:
            errors.append({"kind": "work-page-hidden", "subject": w["id"], "work": w["key"],
                           "superseded": wpage["superseded"],
                           "why": "the work's superseded paths would redirect to a page this profile does not render"})
            continue
        shown = [o for u in units for o in u["outputs"]] + whole
        if not shown:
            continue
        rec = {"name": w["name"], "form": w.get("form", ""), "author": w.get("author", ""),
               "year": year(w.get("published")), "isbn": w.get("isbn", ""),
               "locator": next((locators[r] for r in w.get("sources") or [] if locators.get(r)), "") or w.get("locator", "")}
        order = [c["key"] for c in lib["classes"]]
        by_class: Dict[str, List[Dict[str, Any]]] = {}
        for o in sorted(shown, key=lambda o: (order.index(o["output_class"]) if o["output_class"] in order else len(order))):
            by_class.setdefault(o["output_class"], []).append(o)
        classes = [{"name": class_names.get(k, k), "outputs": v} for k, v in by_class.items()]
        if has_page:
            # The work page lists its outputs in unit order, then the whole work's (the sequence
            # the post navigation walks, one class at a time)
            listed = [o for u in units for o in u["outputs"]] + whole
            upd = max((o["updated"] for o in listed if o["updated"]), default=None)
            upd = upd.isoformat() if upd else None
            desc = work_description(rec, [c["name"] for c in classes])
            text = _front(w["name"], {"subtitle": w.get("subtitle"), "description": desc}, upd)
            text += render_work(rec, units, whole, profile)
            planned_out.append({"source": wsrc, "kind": WORK_PAGE_KIND, "key": w["key"], "subject": w["id"],
                                "members": len(listed), "updated": upd, "listed": [o["id"] for o in listed],
                                "groups": {o["id"]: o["output_class"] for o in listed}, "sequence": True,
                                "href": wpage["active"], "title": w["name"], "description": desc, "text": text})
            href = posixpath.relpath(wsrc, posixpath.dirname(src) or ".")
        else:
            href = None
        # The index's own hrefs are relative to the index, not to the work page
        idx = {o["id"]: o for o in visible(shown, src, sid)}
        for c in classes:
            c["outputs"] = [{**o, "href": idx[o["id"]]["href"]} for o in c["outputs"] if o["id"] in idx]
        newest = max((o["date"] for o in shown if o["date"]), default="")
        entries[rec["form"]].append({**rec, "page": href, "classes": classes, "newest": newest})
        all_listed += [o["id"] for o in shown if o["id"] not in all_listed]
        dates += [o["updated"].isoformat() for o in shown if o["updated"]]
    if errors:
        # one row per distinct error: the index and a work page may list the same output
        return {"errors": list({repr(sorted(e.items())): e for e in errors}.values())}
    groups = []
    for form in P.WORK_FORMS:
        ws = sorted(entries.get(form) or [], key=lambda e: str(e["name"]).casefold())
        ws = sorted(ws, key=lambda e: e["newest"], reverse=True)
        if ws:
            groups.append({"form": form, "heading": FORM_GROUPS[form], "works": ws})
    updated = max(dates) if dates else None
    text = _front(str(F.prop(node, "title") or key),
                  {k: F.prop(node, k) for k in ("description", "date")}, updated)
    text += render_index(groups, topics["topics"], profile)
    index = {"source": src, "kind": "Lens", "key": key, "subject": sid, "members": len(all_listed),
             "updated": updated, "listed": all_listed, "href": page["href"],
             "title": str(F.prop(node, "title") or key), "layout": LAYOUT,
             "works": sum(len(g["works"]) for g in groups), "text": text}
    return {"pages": [index] + planned_out, "errors": []}
