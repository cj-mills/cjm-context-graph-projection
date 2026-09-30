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
A tutorial's end matter invites questions into the comments (the questions callout 32 posts
included, now a line derived from the kind)."""

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

from .runtime import GraphHandle

STRIP_KEY = "author-strip"                               # The site-config key holding the strip's copy
STRIP_FIELDS = ("byline", "links", "pitch", "questions")  # Every field the copy must carry
POST_KINDS = ("tutorial", "notes", "log", "work")        # Deliverable kinds a post page carries (not site pages)
TUTORIAL_KIND = "tutorial"
# The header's kind label (39c51c15 (2)): the navigation kind, as a reader reads it; the site's
# title-metadata partial renders the KIND_META value
KIND_LABELS = {"tutorial": "Tutorial", "notes": "Notes", "log": "Log", "work": "Work"}
KIND_META = "post-kind"


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
    return {"copy": {f: str(copy[f]).strip() for f in STRIP_FIELDS}, "errors": []}


def render_end(
    copy: Dict[str, str],                   # load_strip_copy's copy
    *,
    pitch: Optional[Dict[str, str]] = None,  # {claims, href}: the pitch's claim names and its target page
    questions: bool = False,                # A tutorial: the questions line closes the page
) -> str:  # The post's end matter (markdown): the author strip, then the questions line
    """The author strip (with the pitch line when given) and a tutorial's questions line."""
    lines = [copy["byline"]]
    if pitch:
        lines.append(copy["pitch"].format(claims=pitch["claims"], href=pitch["href"]))
    lines.append(copy["links"])
    # Plain classed divs: a callout a user filter inserts is never converted by Quarto, so its
    # look is the site stylesheet's (.author-strip / .comments-invite)
    out = ["::: {.author-strip}\n" + "  \n".join(lines) + "\n:::\n"]
    if questions:
        out.append("::: {.comments-invite}\n" + copy["questions"] + "\n:::\n")
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
) -> Dict[str, Any]:  # {ends: {note id: markdown}, counts}
    """Every post's end matter. A Note of no post kind (a site page, an untyped Note) has none."""
    ends: Dict[str, str] = {}
    counts = {"strips": 0, "pitch": 0, "pitch_pending": 0, "questions": 0}
    for nid in sorted(notes):
        kind = (types.get(nid) or {}).get("kind")
        if kind not in POST_KINDS:
            continue
        claims = backing.get(nid) or []
        pitch = {"claims": ", ".join(claims), "href": target["href"]} if claims and target else None
        ends[nid] = render_end(copy, pitch=pitch, questions=kind == TUTORIAL_KIND)
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
) -> Dict[str, Any]:  # {set: {meta key: value}, unset: [meta keys]} for the render filter
    """The header's projected metadata (39c51c15 (2)): the kind label; a born post's date is its
    publication's (a draft shows none and says so); Updated is the latest revision when it is
    newer than the source's own date-modified (an archive post keeps its front matter)."""
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
    return out
