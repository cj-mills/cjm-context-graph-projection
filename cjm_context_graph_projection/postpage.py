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
    return {"copy": {f: str(copy[f]).strip() for f in STRIP_FIELDS}, "errors": []}


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


def render_end(
    copy: Dict[str, str],                   # load_strip_copy's copy
    *,
    pitch: Optional[Dict[str, str]] = None,  # {claims, href}: the pitch's claim names and its target page
    questions: bool = False,                # A tutorial: the questions line closes the page
    related: Optional[List[Dict[str, str]]] = None,  # related_posts' result
    reuse: str = "",                        # render_reuse's appendix
) -> str:  # The post's end matter (markdown): related posts, the author strip, the questions line, Reuse
    """Related posts, the author strip (with the pitch line when given) and a tutorial's
    questions line."""
    lines = [copy["byline"]]
    if pitch:
        lines.append(copy["pitch"].format(claims=pitch["claims"], href=pitch["href"]))
    lines.append(copy["links"])
    # Plain classed divs: a callout a user filter inserts is never converted by Quarto, so its
    # look is the site stylesheet's (.author-strip / .comments-invite)
    out = ([render_related(related)] if related else []) + ["::: {.author-strip}\n" + "  \n".join(lines) + "\n:::\n"]
    if questions:
        out.append("::: {.comments-invite}\n" + copy["questions"] + "\n:::\n")
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
) -> Dict[str, Any]:  # {ends: {note id: markdown}, counts}
    """Every post's end matter. A Note of no post kind (a site page, an untyped Note) has none."""
    ends: Dict[str, str] = {}
    counts = {"strips": 0, "pitch": 0, "pitch_pending": 0, "questions": 0, "related": 0}
    for nid in sorted(notes):
        kind = (types.get(nid) or {}).get("kind")
        if kind not in POST_KINDS:
            continue
        claims = backing.get(nid) or []
        pitch = {"claims": ", ".join(claims), "href": target["href"]} if claims and target else None
        rel = (related or {}).get(nid) or []
        lic = (licenses or {}).get(nid)
        ends[nid] = render_end(copy, pitch=pitch, questions=kind == TUTORIAL_KIND, related=rel,
                               reuse=render_reuse(lic) if lic else "")
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
