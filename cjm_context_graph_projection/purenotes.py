"""The pure-notes lane (ruling a7262fe7; work item fdafeed9): a typed deliverable whose
SUBSTANCE is Points and whose body Sections are RENDERED, never authored.

The structural inversion: the fixtures authored Sections as text and hung DERIVED_FROM
on the Note. Here the deliverable Note keeps its publish state and its authored
frontmatter/preamble, but everything below the preamble is a deterministic rendering of
Point nodes — telegraphic statements of one kind, each derived from a named segment run in
the transcription graph (cross-graph References via the 47c60a16 seam). Fidelity is by
construction (a Point exists only as derived); coverage and duplication are graph QUERIES
(`point_coverage` / `point_overlap`); presentation is a rendering policy over substance
(`render_points`: OUTLINE for scanning, EXPANDED for reference, a stable anchor per point).

The lane, in the filter lane's propose/accept shape:

    notes-type    -> the type profile as graph DATA (DeliverableType node, journaled upsert)
    notes-pack    -> read one source UNIT from the sibling graph per the type's stratum query,
                     write the proposer's pack (json + markdown brief)
    notes-ingest  -> validate a proposer's rows against the pack, write a PROPOSAL SET (no graph write)
    notes-accept  -> mint Points (+ References + HAS_POINT + DERIVED_FROM) — one journaled op per point
    notes-coverage / notes-overlap / notes-check / notes-retract   -> the review verbs
    notes-edit    -> edit an accepted point IN PLACE (text / lead / parent; journaled `edit-point`) —
                     the per-point repair (ruling 5625b74e), never a re-propose for one row
    notes-render  -> derive the body from the Points, apply as Sections, write the staging file

Journal shape: `deliverable-type` upserts (last op wins), `accept-point` carries the point AND
its segment observations (replay never opens the sibling), `retract-point` is the compensating
op, `edit-point` re-applies a field set (text / lead / parent_key) to the standing point, and
`render-notes` replays graph-only and re-derives the same Sections from the same Points.
"""

import hashlib
import json
import os
import re
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml
from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.query import (EdgeQuery, NodeQuery, PropertyPredicate,
                                                RelationPredicate)
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import note_node_id, point_node_id
from cjm_dev_graph_schema.nodes import (DeliverableTypeNode, POINT_KIND_GLOSSES, PointNode,
                                        PointSetNode, ReferenceNode)
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from . import factlayer as F
from .runtime import DEFAULT_MANIFESTS, GraphHandle, open_graph

PURE_NOTES_KEY = "pure-notes"

NOTES_PACK_FORMAT = "cjm-context-graph-projection/notes-pack"
NOTES_PACK_VERSION = "0.2.0"   # 0.2.0: stratum roles — spans, line notes, speakers, the clean read (ruling e1e096fa)
NOTES_PROPSET_FORMAT = "cjm-context-graph-projection/notes-proposal-set"
NOTES_PROPSET_VERSION = "0.1.0"

RENDERINGS = ("outline", "expanded")   # the two renderings one substance carries (ruling a7262fe7 (4))
HEADER_MAX_WORDS = 15                  # a structure (apparatus) run longer than this is boilerplate, not a heading
# What the notes pack DOES with a stratum class (ruling e1e096fa; finding 6735f8f1). header: the run leaves
# the content and renders as a heading · span: the lines stay content and the pack carries the span ·
# annotate: the lines stay content and carry the class as a margin note · quote: a quote span the drafter
# carries as a `quotation` point · exclude: the lines are gone · content: plain content, no note.
STRATUM_ROLES = ("header", "span", "annotate", "quote", "exclude", "content")
DEFAULT_STRATUM_ROLE = "annotate"      # a class the policy never named REACHES the drafter (6752db0a (9)), never vanishes
UNNAMED_CLASS_ROLES = ("annotate", "content")   # the only roles a default may take: neither can hide a line
LEGACY_STRATUM_KEYS = (("include_strata", "quote"), ("structure_strata", "header"), ("exclude_strata", "exclude"))


# --------------------------------------------------------------------------------------
# The type profile
# --------------------------------------------------------------------------------------


def pure_notes_type(
    actor: str = "agent:session",  # Who mints the profile
) -> DeliverableTypeNode:  # The pure-notes DeliverableType (the ruling a7262fe7 defaults)
    """The pure-notes profile as data: information policy = a stratum query, presentation
    policy = the two renderings + the starter kind slate, production procedure = the lane.
    A convenience factory — the node exists only once `notes-type` journals it."""
    return DeliverableTypeNode(
        key=PURE_NOTES_KEY,
        title="Pure notes",
        description=("What the source says, telegraphically, addressable back to it: one Point per "
                     "statement, in source order, no interpretation, no research enrichment."),
        information_policy={
            "include_unclassified": True,                 # absence of a stratum IS main topic
            # A ROLE per stratum class (ruling e1e096fa; the vocabulary = STRATUM_ROLES). The book profile:
            # 2047cf1d ruling (2026-09-09): a cross-reference or a transition is never a heading and never
            # content; apparatus (credits, legal, boilerplate) is excluded outright, no longer a header source.
            # 353394c8 / c4a0c744 (2026-09-17): `filler` (a wholly elidable line) is what the clean read
            # excludes — `disfluency` marks a run that CONTAINS disfluencies and its content stays content
            "stratum_roles": {
                "quotation": "quote",                     # verbatim units, carried as `quotation` points
                "section-header": "header",               # read-aloud section titles -> the heading hierarchy (never content)
                "tangent": "exclude", "sponsor": "exclude", "filler": "exclude", "apparatus": "exclude",
                "cross-reference": "exclude", "transition": "exclude",
                "disfluency": "content",
            },
            "default_role": "annotate",                   # an unnamed class reaches the drafter with a note, never vanishes
            "stratum_glosses": {},                        # class -> how the drafter carries it (the brief prints these)
            "never_carry": ["research-mark", "tool-mention", "asr-error"],  # things to DO, not things the source says
        },
        presentation_policy={
            "renderings": {
                "outline": {"role": "review + work page (never the public post — ruling e1fd4d64 (E))",
                            "line": "one per point", "headings": "derived",
                            "emphasis": "lead-term-only", "timestamps": False},
                "expanded": {"role": "the public post (reference)", "quotations": "block",
                             "timestamps": "when-addressable",   # e1fd4d64 (D): only a Source with a public time-addressable URL renders spans, as LINKS
                             "anchor": "pt-<key8> + visible permalink glyph (.pt-anchor)",
                             "emphasis": "lead-in-place (definition: Term — text)",
                             "nesting": 2,                        # second-read ruling (4): depth two; a sequence's events are child points
                             "comparison": "table", "step": "ordered-list",
                             "sequence": "ordered-list of its `event` children (support nests under each event)",
                             "quotation_in_items": "block (a `>` block inside the item — ruling (5))"},
            },
            "public": "expanded",                     # what emit-post carries
            "frontmatter": {"title": "work-unit-notes",   # second-read ruling (1): the SHORT shape — "<work>, Ch. n notes"
                            "description": "synopsis"},   # ruling (2): the accepted `synopsis` point; derived headings only as fallback
            "source_card": True,                      # ruling (1): a derived callout under the title — work, author, unit, the paraphrase rule
            "kinds": dict(POINT_KIND_GLOSSES),
            "emphasis": "lead-in-place",
            "section_length_target": None,
            "tone": "formal; fragments by default; compressed, never the transcript's sentences; no hedging the "
                    "source did not hedge; no first person; no meta-commentary; digits and symbols",
            "structure": "source order (the addressing layer); headings from the structure map + apparatus strata; "
                         "the first apparatus header restating the unit's own identity is the title, not a section",
            "unit": "one emitted post per chapter unit; title = the unit title; the work page is the series link",
        },
        production_procedure=[
            "notes-pack: read the unit's effective spine + strata per the information policy; write the pack",
            "proposer: draft Points with segment runs (kind/from_i/to_i/text/lead/parent) from the pack — compressed, nested up to two levels, one synopsis last",
            "notes-ingest: validate rows against the pack; write the proposal set",
            "notes-accept: human confirms per point; each accept = Point + References + edges, journaled",
            "notes-coverage / notes-overlap / notes-check: the graph-read review; notes-retract undoes",
            "notes-render: derive the EXPANDED body (the public post; OUTLINE = review) + the type-owned frontmatter from the Points; Sections + staging file follow",
        ],
        actor=actor,
    )


async def mint_deliverable_type(
    gx: GraphHandle,
    key: str,                                   # The type slug
    *,
    title: str = "",
    description: str = "",
    information_policy: Optional[Dict[str, Any]] = None,
    presentation_policy: Optional[Dict[str, Any]] = None,
    production_procedure: Optional[List[str]] = None,
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {type_id, key, created|updated, args, written}
    """UPSERT a DeliverableType by slug (the display-rule pattern: last journaled op wins).
    An absent policy falls back to the type's code-carried defaults for the two built-in
    profiles: `pure-notes` (a7262fe7) and `work-page` (ebb77107)."""
    base = (pure_notes_type(actor) if key == PURE_NOTES_KEY
            else work_page_type(actor) if key == WORK_PAGE_KEY
            else DeliverableTypeNode(key=key, actor=actor))
    node = DeliverableTypeNode(
        key=key, title=title or base.title, description=description or base.description,
        information_policy=information_policy if information_policy is not None else base.information_policy,
        presentation_policy=presentation_policy if presentation_policy is not None else base.presentation_policy,
        production_procedure=production_procedure if production_procedure is not None else base.production_procedure,
        actor=actor)
    wire = node.to_graph_node()
    existing = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=node.id)
    if existing is None:
        await extend_graph(gx.queue, gx.graph_id, [wire], [])
        state = "created"
    else:
        await graph_task(gx.queue, gx.graph_id, "update_node", node_id=node.id, properties=wire["properties"])
        state = "updated"
    args = {"key": key, "title": node.title, "description": node.description,
            "information_policy": node.information_policy, "presentation_policy": node.presentation_policy,
            "production_procedure": node.production_procedure, "actor": actor}
    return {"type_id": node.id, "key": key, "state": state, "args": args, "written": True}


async def load_deliverable_type(
    gx: GraphHandle,
    key: str,  # The type slug
) -> Optional[Dict[str, Any]]:  # The type node's properties, or None when not minted
    """Read a DeliverableType profile off the graph (None = `notes-type <key>` first)."""
    node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=DeliverableTypeNode(key=key).id)
    return dict(F.props(node)) if node is not None else None


async def note_deliverable_type(
    gx: GraphHandle,
    note_id: str,  # The deliverable Note
) -> Optional[str]:  # The active `deliverable_type` value, or None
    """The Note's bound type slug (the active `deliverable_type` fact)."""
    slot = [a for a in await F.load_assertions(gx)
            if F.prop(a, "subject_id") == note_id and F.prop(a, "predicate") == "deliverable_type"]
    active = F.active_assertions(slot, await F.load_supersedes(gx))
    return str(F.prop(active[0], "value")) if active else None


# --------------------------------------------------------------------------------------
# Sibling reads: one source UNIT of the transcription graph (effective spine + strata)
# --------------------------------------------------------------------------------------


def choose_spine(
    groups: Dict[Optional[str], int],   # skeleton_hash (None = legacy) -> segment count
    selector: Optional[str] = None,     # "legacy" | a skeleton-hash (prefix ok) | None = auto
) -> Tuple[Optional[str], bool]:  # (chosen skeleton_hash, is_legacy)
    """Pick the SKELETON spine to read (the correction core's `spine_where_for` rule, pure):
    auto takes a sole spine and refuses loudly when several coexist; "legacy" = the
    pre-split spine (no hash); anything else is a unique hash / hex-tail prefix."""
    def _label(h: Optional[str]) -> str:
        return "legacy" if h is None else h.split(":")[-1][:8]
    if selector is None:
        if len(groups) <= 1:
            h = next(iter(groups), None)
            return h, h is None
        raise ValueError(f"{len(groups)} spines coexist under this source "
                         f"({', '.join(_label(h) for h in groups)}); pass --skeleton "
                         "('legacy' or a hash prefix) to choose one")
    sel = selector.strip().lower()
    if sel == "legacy":
        if None not in groups:
            raise ValueError(f"no legacy spine here (available: {[_label(h) for h in groups]})")
        return None, True
    hits = [h for h in groups if h and (h.lower().startswith(sel) or h.split(':')[-1].lower().startswith(sel))]
    if len(hits) != 1:
        raise ValueError(f"--skeleton {selector!r} matches {len(hits)} spine(s) "
                         f"(available: {[_label(h) for h in groups]})")
    return hits[0], False


async def resolve_sibling_source(
    sg: GraphHandle,
    needle: str,  # A Source id / unique prefix, or a title substring
) -> Dict[str, Any]:  # {source_id, title, properties} | {error}
    """Resolve a Source in the sibling graph: id prefix first (the shared seam), then a
    case-insensitive title substring (exactly one match, else loud)."""
    from .projection import ambiguity_error, resolve_node_ref
    if len(needle) >= 6 and all(c in "0123456789abcdef-" for c in needle.lower()):
        r = await resolve_node_ref(sg, needle)
        if "candidates" in r:
            return {"error": ambiguity_error(needle, r["candidates"])}
        node = r.get("node")
        if node is not None and F.label(node) == "Source":
            return {"source_id": F.nid(node), "title": str(F.prop(node, "title") or ""),
                    "properties": dict(F.props(node))}
    q = NodeQuery(label="Source", project=["title"])
    res = await graph_task(sg.queue, sg.graph_id, "query_nodes", query=q.to_dict())
    hits = [r for r in (res.rows or []) if needle.lower() in str(r.get("title") or "").lower()]
    if len(hits) != 1:
        return {"error": f"source {needle!r} matches {len(hits)} Source(s) in the sibling"
                         + (f": {[h.get('title') for h in hits][:6]}" if hits else "")}
    node = await graph_task(sg.queue, sg.graph_id, "get_node", node_id=hits[0]["id"])
    return {"source_id": hits[0]["id"], "title": str(hits[0].get("title") or ""),
            "properties": dict(F.props(node)) if node is not None else {}}


async def read_source_unit(
    sg: GraphHandle,
    source_id: str,                     # The Source node id in the sibling graph
    *,
    skeleton: Optional[str] = None,     # Spine selector ("legacy" | a skeleton-hash prefix; None = auto)
) -> Dict[str, Any]:  # {source, skeleton_hash, segments: [{id,index,text,start,end}], strata, speakers?, read?} | {error}
    """Read one source unit: the EFFECTIVE spine (layer-0 + applied corrections, via the
    correction core's spine read + projection — imported lazily, the pull-transcript
    pattern), the live strata over it, the speaker on each line once the assign lane has
    touched the source, and — when the source carries accepted speech overlays — the lines
    as L1, the clean read (ruling e1e096fa (5))."""
    src = await graph_task(sg.queue, sg.graph_id, "get_node", node_id=source_id)
    if src is None:
        return {"error": f"no Source `{source_id}` in the sibling graph"}
    try:
        from cjm_transcript_correction_core.cleanread import ELISION_MARKER, clean_read, clean_read_summary
        from cjm_transcript_correction_core.graph import (active_speaker_assignments, active_speech_overlays,
                                                          list_source_spines, load_source_segments,
                                                          project_effective_spine, skeleton_hash_for)
    except ModuleNotFoundError:
        return {"error": "cjm-transcript-correction-core (>= 0.0.22, the clean read) is not installed in this "
                         "env — it owns the spine read, the effective-spine projection and the clean read "
                         "the notes pack reads"}
    # The spine is the CORE's read, never a re-implementation: it picks the rendition chain,
    # then the skeleton, and keeps only the LIVE view — a chunk respine leaves its replaced
    # segments on the graph stamped `superseded_by` (ruling 0b4d5cfa (4)). A by-source-id read
    # mixed 1,649 replaced segments into the Bonus lecture's 1,637-line spine (finding, 2026-09-20).
    try:
        segs = await load_source_segments(sg.queue, sg.graph_id, source_id, skeleton_selector=skeleton)
        spines = await list_source_spines(sg.queue, sg.graph_id, source_id)
        chosen = skeleton_hash_for(spines, skeleton)
    except ValueError as e:
        return {"error": str(e)}
    groups: Dict[Optional[str], int] = {sp.get("skeleton_hash"): int(sp.get("segments") or 0) for sp in spines}
    # Corrections for the source (append-only; supersession from SUPERSEDES edges).
    cq = NodeQuery(label="Correction", where=[PropertyPredicate("payload.source_id", "eq", source_id)],
                   limit=200000)
    cres = await graph_task(sg.queue, sg.graph_id, "query_nodes", query=cq.to_dict())
    corrections: List[Dict[str, Any]] = []
    for n in (getattr(cres, "nodes", None) or []):
        d = n.to_dict() if hasattr(n, "to_dict") else dict(n)
        props = dict(d.get("properties") or {})
        props["id"] = d["id"]
        corrections.append(props)
    superseded: set = set()
    ids = [c["id"] for c in corrections]
    for i in range(0, len(ids), 500):
        eq = EdgeQuery(relation_type="SUPERSEDES", target_ids=ids[i:i + 500], project=[])
        eres = await graph_task(sg.queue, sg.graph_id, "query_edges", query=eq.to_dict())
        superseded.update(r["target_id"] for r in (eres.rows or []))
    active = [c for c in corrections if c["id"] not in superseded and c.get("status") != "proposed"]
    strata = sorted([c for c in active if c.get("correction_type") == "stratum"],
                    key=lambda c: float((c.get("payload") or {}).get("start_time") or 0.0))
    eff = project_effective_spine(segs, active)
    segments = [{"id": s.id, "index": s.index, "text": s.text,
                 "start": (float(s.start_time) if s.start_time is not None else None),
                 "end": (float(s.end_time) if s.end_time is not None else None)}
                for s in eff if (s.text or "").strip()]
    # L1 (ruling e1e096fa (5)): the accepted speech overlays are subtracted from the lines a
    # drafter reads — the clean read, as `filter-pack --read clean` reads it. Only the
    # SUB-LINE subtraction happens here: which stratum classes leave the read is the TYPE's
    # call (its exclude roles), made in `build_notes_pack`. A source with no overlays (a
    # book) keeps its L0 lines untouched.
    read: Optional[Dict[str, Any]] = None
    overlays = active_speech_overlays(active, superseded)
    if overlays:
        clean = clean_read(eff, [], overlays, exclude_strata=())
        kept = {ln["id"]: ln["text"] for ln in clean}
        emptied = [s["id"] for s in segments if s["id"] not in kept]
        segments = [{**s, "text": kept[s["id"]]} for s in segments if s["id"] in kept]
        read = {"layer": "clean", "marker": ELISION_MARKER, "spans_cut": clean_read_summary(clean)["spans_cut"],
                "lines_emptied": len(emptied)}
    # Speakers ride every line once the assign lane has touched the source: the entity's
    # canonical name, else the diarization cluster the assignment was made over.
    speakers: Optional[Dict[str, Optional[str]]] = None
    roster: Dict[str, Dict[str, str]] = {}
    assigned = active_speaker_assignments(active, superseded)
    if assigned:
        eres = await graph_task(sg.queue, sg.graph_id, "query_nodes",
                                query=NodeQuery(label="Entity", limit=100000).to_dict())
        names: Dict[str, Optional[str]] = {}
        for n in (getattr(eres, "nodes", None) or []):
            d = n.to_dict() if hasattr(n, "to_dict") else dict(n)
            names[d["id"]] = (d.get("properties") or {}).get("canonical_name")
        speakers = {}
        for s in segments:
            a = assigned.get(s["id"]) or {}
            name = names.get(a.get("entity_id"))
            who = name or a.get("cluster") or None
            speakers[s["id"]] = who
            # The ROSTER (ruling bc62c727 (B)): every voice once, in first-appearance order over the
            # WHOLE unit (a windowed pack numbers its anonymous voices the same way), with what a
            # label may print — the name, else the per-source role, else 'Speaker N'. `role` stays
            # empty until the per-source speaker-role fact exists on the transcript graph.
            if who and who not in roster:
                roster[who] = {"speaker": who, "name": str(name or ""), "role": ""}
    sp = dict(F.props(src))
    # A public, time-addressable URL (YouTube / a podcast player) makes the source ADDRESSABLE:
    # only then does the public rendering carry timestamps, as links (ruling e1fd4d64 (D)).
    public_url = str(sp.get("public_url") or sp.get("url") or "").strip()
    # Human-added resource links ride the unit snapshot too (item ae103970) — the pack brief
    # shows them and a render with no sibling at hand falls back to this snapshot. So do the
    # source FACTS a lecture's title and card read (series, public title, dates — baa640e8);
    # `pack_digest` leaves them out like the roster, so a pack digests as it did before them.
    references = await read_source_references(sg, source_id)
    facts = {k: v for k, v in (await read_source_facts(sg, source_id)).items() if k in SOURCE_FACT_KEYS}
    return {"source": {"source_id": source_id, "title": str(sp.get("title") or ""),
                       "work_structure": sp.get("work_structure"), "skeleton_hash": chosen,
                       **({"public_url": public_url} if public_url else {}),
                       **facts,
                       **({"references": references} if references else {}),
                       **({"speaker_roster": list(roster.values())} if roster else {})},
            "skeleton_hash": chosen, "segments": segments, "strata": strata,
            **({"speakers": speakers} if speakers is not None else {}),
            **({"read": read} if read else {}),
            "spines": {(h or "legacy"): n for h, n in groups.items()}}


async def read_source_references(
    sg: GraphHandle,   # The sibling (transcription) graph
    source_id: str,    # The Source whose human-added links to read
) -> List[Dict[str, Any]]:  # [{id, label, url, notes_slug, role}] — role, then label order
    """The Source's human-added resource links (`Reference` nodes minted by the transcription
    core's `add-reference`; ruling a7ca900d (3), item ae103970) — read LIVE from the sibling
    so every rendering of the unit carries the current set, never a body-authored copy."""
    q = NodeQuery(label="Reference", where=[PropertyPredicate("source_id", "eq", source_id)], limit=10000)
    res = await graph_task(sg.queue, sg.graph_id, "query_nodes", query=q.to_dict())
    out: List[Dict[str, Any]] = []
    for n in (getattr(res, "nodes", None) or []):
        d = n.to_dict() if hasattr(n, "to_dict") else dict(n)
        p = dict(d.get("properties") or {})
        out.append({"id": d.get("id"), "label": str(p.get("label") or ""), "url": str(p.get("url") or ""),
                    "notes_slug": str(p.get("notes_slug") or ""), "role": str(p.get("role") or "")})
    out.sort(key=lambda r: (r["role"], r["label"]))
    return out


async def read_source_facts(
    sg: GraphHandle,   # The sibling (transcription) graph
    source_id: str,    # The Source whose card-level facts to read
) -> Dict[str, Any]:  # {series: [titles], lecture_title?, public_url?, published_at?, recorded_at?, recorded_at_precision?} — empties omitted
    """The Source-level facts a rendering's title and card read LIVE from the sibling (finding
    baa640e8; ruling de9c4cda (H7)): the SERIES = the titles of the confirmed Collections
    holding the Source (PART_OF, inbound; a retired collection is not a series), the
    lecture's PUBLIC title (the playlist row the URL binding matched — the on-disk Source
    title carries the characters a filename cannot spell), the public URL, and the dates
    `bind-source-dates` landed: `published_at` (exact) and `recorded_at` with its precision.
    Read live like the references (ae103970) so an accepted point's frozen unit snapshot
    never hides a fact bound after the accept (the b542896b class); the render op journals
    what it observed and replay renders from that."""
    src = await graph_task(sg.queue, sg.graph_id, "get_node", node_id=source_id)
    if src is None:
        return {}
    sp = dict(F.props(src))
    out: Dict[str, Any] = {}
    cq = NodeQuery(label="Collection", related=RelationPredicate("PART_OF", direction="in", node_id=source_id),
                   project=["title", "status"])
    res = await graph_task(sg.queue, sg.graph_id, "query_nodes", query=cq.to_dict())
    series = sorted({str(r.get("title") or "").strip() for r in (getattr(res, "rows", None) or [])
                     if str(r.get("status") or "confirmed") != "retired" and str(r.get("title") or "").strip()})
    if series:
        out["series"] = series
    ev = sp.get("public_url_evidence") if isinstance(sp.get("public_url_evidence"), dict) else {}
    lecture_title = str((ev or {}).get("playlist_title") or "").strip()
    if lecture_title:
        out["lecture_title"] = lecture_title
    for k in ("public_url", "published_at", "recorded_at", "recorded_at_precision"):
        v = str(sp.get(k) or "").strip()
        if v:
            out[k] = v
    return out


async def resolve_references(
    gx: GraphHandle,
    references: List[Dict[str, Any]],  # Raw Reference rows [{label, url, notes_slug, role}] (read_source_references / a journaled render op)
) -> List[Dict[str, Any]]:  # [{label, href, role, notes_slug, resolved}] — href = the born page's permalink, else the URL fallback, else ""
    """Resolve human-added links for rendering (ae103970): a cross-work link naming a
    notes-graph slug points at the BORN page (`/posts/<slug>/`) when that Note exists on
    this graph and falls back to its URL until then — finding 962866ae's shape: a forward
    reference to a not-yet-born note heals on the next render, never dangles as an empty
    link. Plain links keep their URL."""
    out: List[Dict[str, Any]] = []
    for r in references or []:
        slug = str(r.get("notes_slug") or "").strip()
        href = ""
        if slug:
            node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=note_node_id(slug))
            if node is not None:
                href = f"/posts/{slug}/"
        resolved = bool(href)
        if not href:
            href = str(r.get("url") or "").strip()
        out.append({"label": str(r.get("label") or "").strip(), "href": href, "role": str(r.get("role") or ""),
                    "notes_slug": slug, "resolved": resolved})
    return out


# --------------------------------------------------------------------------------------
# The pack (what a proposer reads)
# --------------------------------------------------------------------------------------


def _fmt_ts(seconds: Optional[float]) -> str:  # mm:ss for rendered lines
    if seconds is None:
        return "--:--"
    m, s = divmod(max(0.0, float(seconds)), 60.0)
    return f"{int(m):02d}:{int(s):02d}"


def pack_digest(pack: Dict[str, Any]) -> str:  # "sha256:<hex>" over the read content
    """Digest the READ content (source binding + numbered lines + headers) — what a proposal
    set records so the read-trace is verifiable, independent of pack id / timestamps. The
    role fields (spans, line notes, speakers — ruling e1e096fa) join the digest only when a
    pack carries them, so a pack without them digests exactly as it did before the ruling.
    The source's `speaker_roster` stays OUT: the per-line speakers already ride the margin,
    and the roster is how a label PRINTS, not what was read. A window pack's read-only
    `context` was read too, so it joins the digest when the pack carries one."""
    source = pack.get("source")
    if isinstance(source, dict):
        # the roster and the source FACTS (series, public title, dates — baa640e8) are how a page
        # PRINTS, not what was read: a pack digests as it did before either existed
        source = {k: v for k, v in source.items() if k != "speaker_roster" and k not in SOURCE_FACT_KEYS}
    body = {"source": source,
            "headers": [[h["i_before"], h["text"]] for h in pack.get("headers") or []],
            "segments": [[r["i"], r["id"], r["start"], r["end"], r["text"]] for r in pack.get("segments") or []]}
    margin = [[r["i"], r.get("speaker"), r.get("notes") or []] for r in pack.get("segments") or []
              if r.get("speaker") or r.get("notes")]
    if margin:
        body["margin"] = margin
    if pack.get("spans"):
        body["spans"] = [[s["class"], s["from_i"], s["to_i"]] for s in pack["spans"]]
    if pack.get("context"):
        body["context"] = {side: [[r["id"], r.get("speaker"), r["text"]] for r in pack["context"].get(side) or []]
                           for side in ("before", "after")}
    if pack.get("index"):
        body["index"] = [[e.get("key"), e.get("proposal_id"), e.get("text")] for e in pack["index"]]
    return "sha256:" + hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def stratum_role_policy(
    info: Dict[str, Any],  # A type's `information_policy`
) -> Tuple[Dict[str, str], str]:  # (class -> role, the role of a class the policy does not name)
    """Read a type's stratum policy as ROLES (ruling e1e096fa). `stratum_roles` is the one
    vocabulary; the three book-shaped lists (include / structure / exclude) still read, as
    quote / header / exclude, so a policy minted before the ruling behaves exactly as it
    did — including its unnamed classes, which stay plain content. Under `stratum_roles` an
    unnamed class takes `default_role` (annotate unless the policy says content): it
    reaches the drafter with a note instead of vanishing. `never_carry` classes are things
    to DO, not things the source says: plain content unless the policy names them."""
    roles: Dict[str, str] = {}
    for key, role in LEGACY_STRATUM_KEYS:
        for c in info.get(key) or []:
            roles[str(c)] = role
    named = info.get("stratum_roles")
    for c, role in dict(named or {}).items():
        if role not in STRATUM_ROLES:
            raise ValueError(f"stratum_roles: `{c}` names the role {role!r} — roles are {', '.join(STRATUM_ROLES)}")
        roles[str(c)] = str(role)
    default = str(info.get("default_role") or (DEFAULT_STRATUM_ROLE if named is not None else "content"))
    if default not in UNNAMED_CLASS_ROLES:
        raise ValueError(f"default_role {default!r}: a class the policy never named may only be "
                         f"{' or '.join(UNNAMED_CLASS_ROLES)} — any other default can hide content")
    for c in info.get("never_carry") or []:
        roles.setdefault(str(c), "content")
    return roles, default


def build_notes_pack(
    unit: Dict[str, Any],           # `read_source_unit` output (source / segments / strata / speakers / read)
    type_props: Dict[str, Any],     # The DeliverableType node's properties (the policies)
    *,
    window: Optional[Tuple[float, Optional[float]]] = None,  # (start, end) source seconds; None = whole unit
    margin: int = 0,                # Content lines of READ-ONLY context either side of the window
) -> Dict[str, Any]:  # The pack (JSON-serializable)
    """Apply the type's INFORMATION POLICY (a stratum query read as ROLES — ruling e1e096fa)
    to the unit and number what a proposer reads: content lines 0..n-1 (unclassified lines
    plus every class whose role keeps its lines), the header runs as HEADERS between lines
    (never content), quote spans and structure SPANS over the numbered lines (a span's lines
    stay content: a qa block is read, never removed), the annotate classes as per-line
    `notes`, the speaker on every line when the unit carries speakers, the kind slate with
    glosses, and the output contract. `margin` keeps the neighbouring content lines as
    UN-NUMBERED `context` a window drafter reads but cannot draft over (design 6752db0a (5),
    the filter pack's shape): the same role policy applies, so an excluded line stays out
    of the margin too. Raises ValueError on a policy naming an unknown role."""
    info = dict(type_props.get("information_policy") or {})
    roles, default_role = stratum_role_policy(info)
    speakers = unit.get("speakers")   # segment id -> display name; None = the unit carries no speakers
    by_seg: Dict[str, List[Tuple[str, str]]] = {}
    for c in unit.get("strata") or []:
        p = c.get("payload") or {}
        for sid in p.get("segment_ids") or []:
            by_seg.setdefault(sid, []).append((str(p.get("category") or ""), str(c.get("id") or "")))
    w0 = float(window[0]) if window and window[0] is not None else None
    w1 = float(window[1]) if window and window[1] is not None else None
    rows: List[Dict[str, Any]] = []
    headers: List[Dict[str, Any]] = []
    pending_header: List[str] = []
    pending_header_id: Optional[str] = None
    quote_runs: Dict[str, List[int]] = {}
    span_runs: Dict[str, Tuple[str, List[int]]] = {}
    margin = max(0, int(margin or 0))
    before: List[Dict[str, Any]] = []
    after: List[Dict[str, Any]] = []
    for s in unit.get("segments") or []:
        side: Optional[List[Dict[str, Any]]] = None
        if w0 is not None and s["end"] is not None and s["end"] <= w0:
            side = before
        elif w1 is not None and s["start"] is not None and s["start"] >= w1:
            side = after
        if side is not None and not margin:
            continue
        by_role: Dict[str, List[Tuple[str, str]]] = {}
        for c, cid in by_seg.get(s["id"], []):
            by_role.setdefault(roles.get(c, default_role), []).append((c, cid))
        if "exclude" in by_role:
            continue
        if side is not None:
            # a margin line: what the drafter would have read as content next door — un-numbered,
            # so no row can name it (a header run is structure, never context)
            if "header" not in by_role:
                ctx = {"id": s["id"], "start": s["start"], "end": s["end"], "text": s["text"]}
                if speakers is not None:
                    ctx["speaker"] = speakers.get(s["id"])
                side.append(ctx)
            continue
        if "header" in by_role:
            # a read-aloud header: accumulate its run, flush as one header before the next content line
            sid = by_role["header"][0][1]
            if pending_header and sid != pending_header_id:
                headers.append({"i_before": len(rows), "text": " ".join(pending_header).strip(),
                                "stratum_id": pending_header_id})
                pending_header = []
            pending_header.append(s["text"].strip())
            pending_header_id = sid
            continue
        if pending_header:
            headers.append({"i_before": len(rows), "text": " ".join(pending_header).strip(),
                            "stratum_id": pending_header_id})
            pending_header, pending_header_id = [], None
        i = len(rows)
        row = {"i": i, "id": s["id"], "index": s["index"], "start": s["start"], "end": s["end"],
               "text": s["text"], "h": len(headers)}   # h = count of headers before this line
        if speakers is not None:
            row["speaker"] = speakers.get(s["id"])
        notes = list(dict.fromkeys(c for c, _ in by_role.get("annotate", [])))
        if notes:
            row["notes"] = notes
        rows.append(row)
        for _, cid in by_role.get("quote", []):
            quote_runs.setdefault(cid, []).append(i)
        for c, cid in by_role.get("span", []):
            span_runs.setdefault(cid, (c, []))[1].append(i)
    if pending_header:
        headers.append({"i_before": len(rows), "text": " ".join(pending_header).strip(),
                        "stratum_id": pending_header_id})
    # A structure stratum is a HEADER only when it reads like one: a long apparatus run (a
    # closing segue, credits, a dedication) is boilerplate the notes neither head nor carry
    # (live sighting: chapter 1's closing paragraph is an apparatus stratum).
    headers = [h for h in headers if len(h["text"].split()) <= HEADER_MAX_WORDS]
    for k, r in enumerate(rows):
        r["h"] = sum(1 for h in headers if h["i_before"] <= r["i"])
    quote_spans = [{"stratum_id": cid, "from_i": min(v), "to_i": max(v)} for cid, v in quote_runs.items()]
    quote_spans.sort(key=lambda q: q["from_i"])
    spans = [{"class": c, "stratum_id": cid, "from_i": min(v), "to_i": max(v)} for cid, (c, v) in span_runs.items()]
    spans.sort(key=lambda q: (q["from_i"], q["to_i"]))
    kinds = dict((type_props.get("presentation_policy") or {}).get("kinds") or POINT_KIND_GLOSSES)
    # The row fields this TYPE adds to the contract (ruling ba341c72 (3)): contract text is type
    # data, so a brief prints the fields of the kinds its type actually has.
    kind_fields = {str(k): str(v) for k, v in
                   dict((type_props.get("presentation_policy") or {}).get("kind_fields") or {}).items()}
    carried = {sp["class"] for sp in spans} | {c for r in rows for c in r.get("notes") or []}
    glosses = {str(c): str(g) for c, g in dict(info.get("stratum_glosses") or {}).items() if c in carried}
    pack = {
        "format": NOTES_PACK_FORMAT, "version": NOTES_PACK_VERSION,
        "pack_id": f"npack_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}",
        "created_at": time.time(),
        "type": str(type_props.get("key") or PURE_NOTES_KEY),
        "source": dict(unit.get("source") or {}),
        "window": {"start": w0, "end": w1},
        "kinds": [{"kind": k, "gloss": g} for k, g in kinds.items()],
        "headers": headers,
        "quote_spans": quote_spans,
        "spans": spans,
        "stratum_glosses": glosses,
        **({"kind_fields": kind_fields} if kind_fields else {}),
        **({"read": dict(unit["read"])} if unit.get("read") else {}),
        "segments": rows,
    }
    if margin and (before or after):
        pack["context"] = {"before": before[-margin:], "after": after[:margin]}
    pack["digest"] = pack_digest(pack)
    return pack


def plan_notes_windows(
    pack: Dict[str, Any],   # The WHOLE-unit pack (build_notes_pack with no window)
    count: int,             # How many windows to cut the unit into
    *,
    slack: float = 0.2,     # Search radius around each even cut, as a fraction of one window
) -> List[Dict[str, Any]]:  # `count` windows {k, start, end, from_i, to_i, seam}; the last end is None
    """Cut a whole-unit pack into `count` windows of near-equal line count at MECHANICAL
    seams (design 6752db0a (5); the filter lane's `plan_pack_windows` read over pack lines):
    within `slack` of each even cut a header boundary wins, else the speaker turn nearest the
    even cut, else the longest silence; no model chooses a seam. A seam NEVER falls inside a
    span or a quote span — a qa block is drafted whole (work item 3a2c94eb (1)) — so when
    the whole search radius sits inside one, the nearest legal seam outside it is taken
    instead. A cut sits in the gap between two lines, so `build_notes_pack(window=...)` over
    the returned (start, end) pairs tiles the unit; `from_i`/`to_i` are the whole-pack lines
    each window is expected to hold and `seam` names what opened it."""
    rows = pack.get("segments") or []
    n = int(count)
    if n < 1:
        raise ValueError("count must be >= 1")
    if n > max(1, len(rows)):
        raise ValueError(f"cannot cut {len(rows)} lines into {n} windows")
    runs = [(int(s["from_i"]), int(s["to_i"]))
            for s in list(pack.get("spans") or []) + list(pack.get("quote_spans") or [])]

    def _legal(j: int) -> bool:  # may a cut sit between line j-1 and line j?
        a, b = rows[j - 1], rows[j]
        if a.get("end") is None or b.get("start") is None or float(b["start"]) < float(a["end"]):
            return False   # untimed or overlapping neighbours: no gap to cut in
        return not any(f < j <= t for f, t in runs)

    def _gap(j: int) -> float:
        return float(rows[j]["start"]) - float(rows[j - 1]["end"])

    per = len(rows) / n
    radius = max(1, int(per * float(slack)))
    seams: List[Tuple[int, str]] = []
    lo_bound = 1
    for k in range(1, n):
        target = int(round(k * per))
        cands = [j for j in range(max(lo_bound, target - radius), min(len(rows) - 1, target + radius) + 1)
                 if _legal(j)]
        if not cands:
            # the radius sits inside a span (or among overlapping lines): the nearest legal seam wins
            legal = [j for j in range(lo_bound, len(rows)) if _legal(j)]
            if not legal:
                raise ValueError(f"no legal seam for cut {k} of {n - 1} — fewer windows, or a span covers the rest of the unit")
            cands = [min(legal, key=lambda j: abs(j - target))]
        heads = [j for j in cands if rows[j].get("h") != rows[j - 1].get("h")]
        turns = [j for j in cands if "speaker" in rows[j] and rows[j].get("speaker") != rows[j - 1].get("speaker")]
        if heads:
            j, seam = min(heads, key=lambda j: abs(j - target)), "header"
        elif turns:
            j, seam = min(turns, key=lambda j: (abs(j - target), -_gap(j))), "turn"
        else:
            j, seam = max(cands, key=lambda j: (_gap(j), -abs(j - target))), "silence"
        seams.append((j, seam))
        lo_bound = j + 1
    out: List[Dict[str, Any]] = []
    edges = [(0, "start")] + seams
    for k, (j, seam) in enumerate(edges):
        nxt = edges[k + 1][0] if k + 1 < len(edges) else None
        start = 0.0 if k == 0 else round((float(rows[j - 1]["end"]) + float(rows[j]["start"])) / 2.0, 4)
        end = (None if nxt is None
               else round((float(rows[nxt - 1]["end"]) + float(rows[nxt]["start"])) / 2.0, 4))
        out.append({"k": k, "start": start, "end": end, "from_i": j,
                    "to_i": (len(rows) - 1 if nxt is None else nxt - 1), "seam": seam})
    return out


OUTPUT_CONTRACT = """\
## Output contract

Write ONE JSON object per line (JSONL). Each row proposes ONE point over a run of
consecutive pack lines:

    {"kind": "<kind>", "from_i": <int>, "to_i": <int>, "text": "<telegraphic statement>",
     "lead": "<optional: a term the text CONTAINS — definition: the term defined, which the text does NOT repeat>",
     "parent": <optional: the 0-based ROW NUMBER of the point this one elaborates>,
     "attribution": "<quotation only: who is quoted>",
     "data": {<comparison: "columns": [...], "rows": [[...], ...] | sequence: "items": [{"when": "...", "what": "..."}]}}

* You are COMPRESSING, not transcribing. Every point keeps the lines it derives from and is
  checked beside them afterwards, so a tight rewrite cannot drift — copying the
  transcript's sentences is the failure mode. Write the statement a reader returning to
  these notes would want to find: usually SEVERAL lines compressed into one telegraphic
  fragment, never one point per transcript line, never the speaker's rhetorical setup.
* Register: fragments by default; drop articles, connectives, hedges the source did not
  make, and framing ("what if…", "the thing is…", "I want to talk about…"); keep the
  source's own terms and its precision (digits, symbols, units); no first person, no
  meta-commentary, no interpretation, no restating one fact under two kinds.
  `quotation` text is VERBATIM. A PURPOSE clause keeps its verb — compress "so that they
  would never suffer such a defeat again" to "so as never to suffer such a defeat again",
  never to "never such a defeat again", which reads as an outcome instead of an intent.

  Before / after:
    lines: "So the reason the bridge failed, and this is the part most people get wrong, is
            that the engineers assumed the wind load would be static. It wasn't. It oscillated."
    weak:  "The reason the bridge failed is that the engineers assumed the wind load would be
            static, but it oscillated"                      (the transcript's sentence, trimmed)
    good:  "Bridge failure — wind load assumed static; it oscillated"
    lines: "I want to talk about three things today. First, budgets. A budget is really just a
            plan for money you haven't spent yet. Most people think of it as a restriction."
    weak:  three rows, one per line, including the announcement of three things
    good:  ONE row: {"kind": "definition", "lead": "Budget",
                     "text": "A plan for money not yet spent — not a restriction"}
* `kind`: a kind from the slate above, or a NEW kebab-case kind when none fits (say so in
  the text's lead). Use the STRUCTURED kinds whenever the content has that shape — a table
  or a numbered series is the reader's scan aid, a row of prose is not:
    comparison  {"kind": "comparison", "from_i": 40, "to_i": 44, "text": "Static vs dynamic load models",
                 "data": {"columns": ["", "Static model", "Dynamic model"],
                          "rows": [["Assumes", "constant wind pressure", "oscillating pressure"],
                                   ["Predicts", "a stable deck", "resonance"]]}}
                A comparison's children ADD what the table does not hold; an example that
                fits a row belongs IN the table, never restated as a bullet beneath it.
    sequence    a PARENT row naming the series, then one `event` row per item with
                `parent` pointing at it and `data.when`; an event's own support nests
                beneath the event (that is what the second level is for):
                {"kind": "sequence", "from_i": 3, "to_i": 9, "text": "The bridge's last year"}
                {"kind": "event", "from_i": 3, "to_i": 4, "text": "opened to traffic", "parent": 0, "data": {"when": "July"}}
                {"kind": "event", "from_i": 5, "to_i": 9, "text": "collapse in a 40 mph wind", "parent": 0, "data": {"when": "November"}}
                {"kind": "claim", "from_i": 7, "to_i": 9, "text": "Deck had oscillated for weeks", "parent": 2}
    step        one row per step of a procedure the source lays out (consecutive rows)
* `parent` (nesting, at most TWO levels deep): when a point elaborates, exemplifies,
  qualifies, or gives the consequence of an EARLIER point in the same section, name that
  point's row number (0-based, counting the rows you have written so far); it renders as
  a sub-item under the parent. A child may itself have children (an event's support, an
  example's detail); a grandchild may not. Use it — a claim with its consequences and
  its example beneath it reads as a tree, not a wall of equal bullets.
* `synopsis` — write ONE such row LAST, after everything else, spanning the whole unit
  (`from_i` 0 to the last line; the only row allowed to cross headers): one or two
  sentences, under 30 words, on what the unit ARGUES — its claim and its move, in the
  work's own terms. Never a list of the section headings. It becomes the post's
  description, so a reader decides from it whether to open the notes.
* `from_i`/`to_i`: inclusive pack line numbers (the `[i]` prefixes) the point derives from —
  the smallest run that contains the statement. Two points may share lines; one point
  never spans a header (except the synopsis).
* `lead`: the term the reader's eye keys on (a name, a concept). It MUST appear in the
  text — it is bolded IN PLACE, never prefixed. Only for `definition` does the lead stand
  apart as the term defined: its text is the gloss ALONE (the row renders as
  `**Term** — gloss`), so never open a definition's text with the term itself.
* Names: where the source speaks as "I", name the author by surname (the Work line
  above); never "the author". Symbols, a small fixed palette a general reader parses
  without decoding: `→` ONLY where the source asserts cause and effect, at most ONE per
  point and never chained — a trigger and its response, or one thing becoming another,
  is said in words; `=` where the source equates two things; `vs` for contrast; `≈` and
  `≠` only where the source makes that relation. Nothing else.

Rows only — no prose before or after, no code fences.
"""


def render_notes_pack(pack: Dict[str, Any]) -> str:  # The proposer brief (markdown)
    """Render a pack as the brief a proposer reads: the unit, the kind slate, the headers
    the notes will use, the quote spans, the structure spans and the margin (line notes,
    speakers, the clean read — each section only when the pack carries it, so a pack
    without role fields renders exactly as before ruling e1e096fa), the output contract,
    then the numbered lines with `[H]` header rows interleaved. A WINDOW pack (design
    6752db0a (5)) says so after the contract — no synopsis, no section rows: both belong to
    the whole source — and prints its read-only `context` as un-numbered `[·]` lines either
    side of the numbered ones. Deterministic for a given pack."""
    src = pack.get("source") or {}
    ws = src.get("work_structure") or {}
    unit_bits = [f"{k}: {ws[k]}" for k in ("kind", "part", "part_title", "chapter", "title") if ws.get(k)]
    work = dict(ws.get("work") or {})
    work_line = (f"Work: **{work.get('title')}**" + (f" by {work.get('author')}" if work.get("author") else "")
                 + (f" (narrated by {work.get('narrator')})" if work.get("narrator") and work.get("narrator") != work.get("author") else "")
                 + " — name the author by surname where the source says \"I\"; never write \"the author\".")
    rows = pack.get("segments") or []
    spans = pack.get("spans") or []
    glosses = dict(pack.get("stratum_glosses") or {})
    note_classes = list(dict.fromkeys(c for r in rows for c in r.get("notes") or []))
    roster = list(dict.fromkeys(r["speaker"] for r in rows if r.get("speaker")))
    has_speakers = any("speaker" in r for r in rows)
    read = dict(pack.get("read") or {})
    lines: List[str] = [
        f"# Notes pack `{pack.get('pack_id')}` — type `{pack.get('type')}`", "",
        f"Source: **{src.get('title') or src.get('source_id')}**  (`{src.get('source_id')}`; "
        f"spine `{(src.get('skeleton_hash') or 'legacy')[-12:]}`)",
        *([work_line] if work.get("title") else []),
        ("Unit: " + " · ".join(unit_bits)) if unit_bits else "Unit: (no structure map on this source)",
        f"{len(rows)} content lines · {len(pack.get('headers') or [])} headers · "
        f"{len(pack.get('quote_spans') or [])} quote spans"
        + (f" · {len(spans)} spans" if spans else "")
        + f" · digest `{pack.get('digest', '')[-12:]}`", "",
        "## Task", "",
        "Read the numbered lines below and propose POINTS: what the source says, COMPRESSED into",
        "telegraphic statements a reader would scan for, in source order, each over the run of lines",
        "it derives from. Everything below is main-topic content already (excluded strata are gone);",
        "`[H]` rows are the section headers the notes will render under — never make a point of a",
        "header. Nest with `parent` where a point elaborates an earlier one; use the structured kinds",
        "where the content has that shape.", "",
        "## Kinds", "",
    ]
    for k in pack.get("kinds") or []:
        lines.append(f"- `{k['kind']}` — {k.get('gloss') or ''}".rstrip(" —"))
    qs = pack.get("quote_spans") or []
    lines += ["", "## Quote spans (verbatim units the source quotes — propose as `quotation`)", ""]
    lines += [f"- lines {q['from_i']}–{q['to_i']}" for q in qs] or ["- (none)"]
    if spans:
        lines += ["", "## Spans (structure over the lines — the lines inside a span ARE content: read them, draft them)", ""]
        lines += [f"- `{s['class']}` lines {s['from_i']}–{s['to_i']}" for s in spans]
        for c in dict.fromkeys(s["class"] for s in spans):
            if glosses.get(c):
                lines.append(f"  - `{c}`: {glosses[c]}")
    if note_classes or has_speakers or read.get("layer") == "clean":
        lines += ["", "## The margin", ""]
        if note_classes:
            lines.append("* A `{class}` note before a line's text is the source's own stratum over that line. The line "
                         "is content; the note tells you how to carry it:")
            lines += [f"  - `{{{c}}}` — {glosses.get(c) or 'a class the type policy does not describe: keep the content, judge by the lines'}"
                      for c in note_classes]
        if has_speakers:
            lines.append("* A `— name —` rule marks where the speaker changes; every line below it is that speaker's until "
                         "the next rule" + (f" (speakers: {', '.join(roster)})" if roster else "") + ". A rule reading "
                         "`— ? —` is a line nobody has been assigned to yet.")
        if read.get("layer") == "clean":
            lines.append(f"* The lines are the CLEAN read: `{read.get('marker') or '[…]'}` stands where spoken disfluency "
                         "(hesitations, repeats, false starts) was elided. Nothing of substance is behind it; never "
                         "carry the marker into a point.")
    lines += ["", OUTPUT_CONTRACT]
    kind_fields = dict(pack.get("kind_fields") or {})
    if kind_fields:
        lines += ["## This type's row fields", "",
                  "Everything above holds for every row. This deliverable type adds:", ""]
        lines += [f"* `{k}` — {v}" for k, v in kind_fields.items()]
        lines.append("")
    win = dict(pack.get("window") or {})
    ctx = dict(pack.get("context") or {})
    if win.get("start") is not None or win.get("end") is not None:
        lines += ["## This window", "",
                  f"This pack is ONE WINDOW of a longer source ({_fmt_ts(win.get('start') or 0.0)} to "
                  f"{_fmt_ts(win['end']) if win.get('end') is not None else 'the end'}). The other windows are "
                  "drafted separately and the rows are merged afterwards, so:", "",
                  "* Write NO `synopsis` row and NO `section` row — both are proposed over the WHOLE source once "
                  "the windows are merged. This overrides the contract above."]
        if ctx.get("before") or ctx.get("after"):
            lines.append("* `[·]` lines are the neighbouring lines, READ-ONLY: read them to see what the window "
                         "opens on and closes into; never draft a point over them (they carry no line number, "
                         "so no row can name them).")
        index = list(pack.get("index") or [])
        lines.append("* A point of yours may elaborate or lean on something said BEFORE this window (the speaker "
                     "returns to it, an answer goes back to a slide). You cannot give a row number for a point of "
                     "another window, so say it on the row as an OPEN REFERENCE — `\"open_refs\": [{\"role\": "
                     "\"refers_to\", " + ("\"point\": \"p017\"}]` naming the earlier point by its key from "
                     "\"Points so far\" below" if index else "\"hint\": \"<what the earlier point said, in the "
                     "source's own terms>\"}]` — it is matched against the other windows' points afterwards") +
                     "; role `parent` when your row would NEST under that point (one parent only, and then give "
                     "no `parent` row number)." + (" Where the index does not list what you mean, give a `hint` "
                     "(what the earlier point said, in the source's own terms) in place of `point`." if index else "")
                     + " Inside this window keep using `parent` / `refers_to` row numbers. Only when the lines "
                     "really do reach back — never to decorate.")
        if index:
            lines += ["", "### Points so far", "",
                      "The points ALREADY drafted from the earlier windows, in source order (children indented). Do "
                      "not redraft them; write in the same register and at the same grain; name one by its key "
                      "when your row leans on it or nests under it.", ""]
            lines += render_points_index(index)
        lines.append("")
    lines += ["## Transcript", ""]

    def _context(title: str, ctx_rows: List[Dict[str, Any]]) -> List[str]:  # un-numbered read-only lines
        out: List[str] = [f"### {title}", ""]
        prev: Any = object()
        for c in ctx_rows:
            if "speaker" in c and c.get("speaker") != prev:
                prev = c.get("speaker")
                out.append(f"— {prev or '?'} —")
            out.append(f"[·] {_fmt_ts(c['start'])}–{_fmt_ts(c['end'])}  {c['text']}")
        return out + [""]

    if ctx.get("before"):
        lines += _context("Context before (read-only)", ctx["before"])
    if ctx.get("before") or ctx.get("after"):
        lines += ["### This window's lines", ""]
    headers = pack.get("headers") or []
    hi = 0
    prev_speaker: Any = object()
    for r in rows:
        while hi < len(headers) and headers[hi]["i_before"] <= r["i"]:
            lines.append(f"[H] {headers[hi]['text']}")
            hi += 1
        if "speaker" in r and r.get("speaker") != prev_speaker:
            prev_speaker = r.get("speaker")
            lines.append(f"— {prev_speaker or '?'} —")
        chips = "".join(f"{{{c}}} " for c in r.get("notes") or [])
        lines.append(f"[{r['i']}] {_fmt_ts(r['start'])}–{_fmt_ts(r['end'])}  {chips}{r['text']}")
    while hi < len(headers):
        lines.append(f"[H] {headers[hi]['text']}")
        hi += 1
    if ctx.get("after"):
        lines += [""] + _context("Context after (read-only)", ctx["after"])[:-1]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------
# Ingest: proposer rows -> a proposal set (durable inference output; no graph writes)
# --------------------------------------------------------------------------------------


def _is_kind_token(value: str) -> bool:
    v = (value or "").strip()
    return bool(v) and v[:1].isalnum() and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", v) is not None


def validate_point_rows(
    rows: List[Dict[str, Any]],  # Raw proposer output rows (parsed JSONL)
    pack: Dict[str, Any],        # The pack the rows reference
    *,
    lenient_leads: bool = False, # Drop (never refuse) a lead the text does not contain; the CLI's --lenient
) -> List[Dict[str, Any]]:  # Normalized rows (kind/from_i/to_i/text/lead/parent/attribution/data)
    """Validate + normalize proposer rows against their pack — loud on the first bad row.
    Enforces the contract: kind token, in-range inclusive run, non-empty text, a run that
    never crosses a header, `comparison` carries columns+rows, `sequence` carries items,
    a `lead` the text CONTAINS (bolded in place — ruling e1fd4d64 (I); the term-then-gloss
    kinds of LEAD_PREFIX_KINDS exempt), and `parent` = an EARLIER row under the same header
    that is itself top-level (ONE level of nesting — e1fd4d64 (H)). A `section` row (ruling
    bc62c727 (A)) is an ANCHOR, never a run: its text is the title and it carries nothing
    else; no row nests under it or refers to it. A `question` may say it was `relayed` and
    by whom it was asked (`asker`) — bc62c727 (B3). A link that crosses a WINDOW cut rides
    the row as an OPEN REFERENCE (work item 3a2c94eb (3)): `open_refs` = [{role, hint}] to be
    closed over the merged Points, or [{role, point}] naming a key of the pack's running
    `index` of earlier windows' points (closed at ingest)."""
    segs = pack.get("segments") or []
    n = len(segs)
    out: List[Dict[str, Any]] = []
    for k, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"row {k}: not an object")
        kind = str(raw.get("kind") or "").strip()
        if not _is_kind_token(kind):
            raise ValueError(f"row {k}: kind must be a kebab-case token, got {kind!r}")
        try:
            fi, ti = int(raw.get("from_i")), int(raw.get("to_i"))
        except (TypeError, ValueError):
            raise ValueError(f"row {k}: from_i/to_i must be integers")
        if not (0 <= fi <= ti < n):
            raise ValueError(f"row {k}: run {fi}..{ti} outside the pack (0..{n - 1}) or inverted")
        if kind == "synopsis":
            # The ONE unit-spanning row (second-read ruling (2)): exempt from the header rule.
            if (fi, ti) != (0, n - 1):
                raise ValueError(f"row {k}: a synopsis spans the whole unit (from_i 0, to_i {n - 1})")
            if any(r["kind"] == "synopsis" for r in out):
                raise ValueError(f"row {k}: a second synopsis — one per unit")
        elif segs[fi]["h"] != segs[ti]["h"]:
            raise ValueError(f"row {k}: run {fi}..{ti} crosses a header (a point never spans sections)")
        if kind == SECTION_KIND:
            # A synthesized section (ruling bc62c727 (A1)): an anchor at the first line of the first
            # point it covers — the title is its text, and a title carries nothing else.
            if fi != ti:
                raise ValueError(f"row {k}: a section is an anchor, not a run — from_i = to_i = the first line "
                                 f"of the first point it covers")
            extra = [f for f in ("lead", "parent", "refers_to", "attribution", "data") if raw.get(f) not in (None, "", [], {})]
            if extra:
                raise ValueError(f"row {k}: a section carries its title as `text` and nothing else (got {', '.join(extra)})")
        text = str(raw.get("text") or "").strip()
        if not text:
            raise ValueError(f"row {k}: text is empty")
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        if kind == "comparison" and not (data.get("columns") and data.get("rows")):
            raise ValueError(f"row {k}: a comparison needs data.columns and data.rows")
        lead = str(raw.get("lead") or "").strip()
        if kind == "question":
            lead = ""   # the questioner is DERIVED from the asking lines' speaker, never drafted (ruling ba341c72 (1))
        if lead and kind not in LEAD_PREFIX_KINDS and lead.lower() not in text.lower():
            if not lenient_leads:
                raise ValueError(f"row {k}: lead {lead!r} does not appear in the text (a lead is bolded IN PLACE; "
                                 f"only {' / '.join(LEAD_PREFIX_KINDS)} may carry a lead the text lacks) — fix the row or ingest --lenient")
            lead = ""
        if text.count("→") > 1:
            # Ruling 5625b74e (2): one arrow per point, cause-and-effect only — the ch. 2 page carried
            # 12 arrows doing three jobs and chaining inside rows; the rest is said in words.
            raise ValueError(f"row {k}: {text.count('→')} arrows — at most ONE `→` per point (cause and effect "
                             f"only); say a trigger-and-response or a becoming in words")
        parent: Optional[int] = None
        if raw.get("parent") is not None:
            try:
                parent = int(raw.get("parent"))
            except (TypeError, ValueError):
                raise ValueError(f"row {k}: parent must be a 0-based row number")
            if not (0 <= parent < k - 1):
                raise ValueError(f"row {k}: parent {parent} is not an EARLIER row (0..{k - 2})")
            if kind == "synopsis":
                raise ValueError(f"row {k}: a synopsis is never nested")
            if out[parent]["kind"] == SECTION_KIND:
                raise ValueError(f"row {k}: parent row {parent} is a section — a section is a heading, nothing nests under it")
            gp = out[parent].get("parent")
            if gp is not None and out[gp].get("parent") is not None:
                raise ValueError(f"row {k}: parent row {parent} is already a grandchild — two levels at most")
            if segs[out[parent]["from_i"]]["h"] != segs[fi]["h"]:
                raise ValueError(f"row {k}: parent row {parent} is under another header")
        if kind == "event":
            # An event is one item of a sequence, as a CHILD point (second-read ruling (4)).
            if parent is None or out[parent]["kind"] != "sequence":
                raise ValueError(f"row {k}: an event's parent must be a `sequence` row")
            if not str(data.get("when") or "").strip():
                raise ValueError(f"row {k}: an event needs data.when")
        if kind == "sequence" and not data.get("items"):
            data = dict(data)   # items arrive as `event` children now; legacy data.items still renders
        # The optional per-kind fields a type's contract may name (ruling ba341c72 (2)): `refers_to` =
        # earlier rows this one leans on (a Q&A answer's back-links; they become point keys), and the
        # kind-specific `asr_form` (glossary/code: the transcript's surface form when it differed) and
        # `unverified` (code: an identifier heard, not seen), which ride the point's `data`.
        refers: List[int] = []
        if raw.get("refers_to") is not None:
            if kind == "synopsis":
                raise ValueError(f"row {k}: a synopsis never refers to other rows")
            vals = raw.get("refers_to") if isinstance(raw.get("refers_to"), list) else [raw.get("refers_to")]
            for v in vals:
                try:
                    t = int(v)
                except (TypeError, ValueError):
                    raise ValueError(f"row {k}: refers_to takes 0-based ROW NUMBERS of earlier rows, got {v!r}")
                if not (0 <= t < k - 1):
                    raise ValueError(f"row {k}: refers_to {t} is not an EARLIER row (0..{k - 2})")
                if out[t]["kind"] in STRUCTURE_KINDS:
                    raise ValueError(f"row {k}: refers_to {t} is the {out[t]['kind']} — name the point it leans on")
                if t not in refers:
                    refers.append(t)
        # OPEN REFERENCES (work item 3a2c94eb (3)): a window drafter cannot number a row of ANOTHER
        # window, so a link across the cut rides the row — as a `hint` (what the earlier point said;
        # closed later over the merged Points, never guessed) or, when the pack carries a running
        # index of the earlier windows' points, as that `point`'s key (closed at ingest).
        open_refs: List[Dict[str, str]] = []
        index_depth = {str(e.get("key")): int(e.get("depth") or 0) for e in pack.get("index") or []}
        if raw.get("open_refs") not in (None, "", []):
            if not isinstance(raw.get("open_refs"), list):
                raise ValueError(f"row {k}: open_refs takes a LIST of {{role, hint}} / {{role, point}} objects")
            if kind in STRUCTURE_KINDS:
                raise ValueError(f"row {k}: a {kind} never refers to other points")
            for o in raw.get("open_refs"):
                if not isinstance(o, dict):
                    raise ValueError(f"row {k}: an open reference is an object {{role, hint}} or {{role, point}}")
                role = str(o.get("role") or "refers_to").strip()
                if role not in OPEN_REF_ROLES:
                    raise ValueError(f"row {k}: open reference role {role!r} — one of {' | '.join(OPEN_REF_ROLES)}")
                hint, point = str(o.get("hint") or "").strip(), str(o.get("point") or "").strip()
                if bool(hint) == bool(point):
                    raise ValueError(f"row {k}: an open reference carries exactly ONE of `hint` / `point`")
                if point and point not in index_depth:
                    raise ValueError(f"row {k}: open reference names point {point!r}, which this pack's index does not list")
                if role == "parent":
                    if parent is not None or any(x["role"] == "parent" for x in open_refs):
                        raise ValueError(f"row {k}: one parent only — a `parent` row number OR one open `parent` reference")
                    if point and index_depth[point] >= 2:
                        raise ValueError(f"row {k}: point {point} is already a grandchild — two levels at most")
                open_refs.append({"role": role, **({"point": point} if point else {"hint": hint})})
        asr_form = str(raw.get("asr_form") or "").strip()
        if asr_form or raw.get("unverified") is not None:
            data = dict(data)
            if asr_form:
                data["asr_form"] = asr_form
            if raw.get("unverified") is not None:
                data["unverified"] = bool(raw.get("unverified"))
        # A RELAYED question (ruling bc62c727 (B3)): that the host read it out is content of the lines,
        # so the row may say so — `relayed` = true, or the channel it came through (a SPEAKER_ROLES
        # token: chat, audience member) — and name the `asker` when the host did. Both ride `data`;
        # the derived speaker (the host) is untouched.
        asker = str(raw.get("asker") or "").strip()
        relayed = raw.get("relayed")
        if asker or relayed not in (None, False, ""):
            if kind != "question":
                raise ValueError(f"row {k}: `relayed` / `asker` belong to a `question` row, not a {kind}")
            if isinstance(relayed, str):
                relayed = relayed.strip().lower()
                if relayed not in SPEAKER_ROLES:
                    raise ValueError(f"row {k}: relayed takes true or the channel the question came through "
                                     f"({' | '.join(SPEAKER_ROLES)}), got {relayed!r}")
            else:
                relayed = True   # an asker the host named IS a relayed question
            data = dict(data)
            data["relayed"] = relayed
            if asker:
                data["asker"] = asker
        out.append({"kind": kind, "from_i": fi, "to_i": ti, "text": text, "lead": lead, "parent": parent,
                    "attribution": str(raw.get("attribution") or "").strip(),
                    "data": data, "refers_to": refers, "open_refs": open_refs})
    return out


def proposals_from_point_rows(
    rows: List[Dict[str, Any]],  # validate_point_rows output
    pack: Dict[str, Any],        # The pack the rows reference
) -> List[Dict[str, Any]]:  # Proposal rows, source order, pack positions resolved to segment identity
    """Resolve validated rows to proposal rows: a minted proposal id (the point's future
    key), the segment ids + times of the run, the header the run falls under, and the
    read-trace (pack id + run). An open reference naming a `point` of the pack's running
    index closes HERE (its proposal id joins `refers_to`, or becomes the `parent_key` — a
    link into ANOTHER set, which only the merge resolves); a `hint` stays open on the row."""
    segs = pack.get("segments") or []
    headers = pack.get("headers") or []
    out: List[Dict[str, Any]] = []
    by_key = {str(e.get("key")): str(e.get("proposal_id")) for e in pack.get("index") or []}
    for r in rows:
        keyed = [o for o in r.get("open_refs") or [] if o.get("point")]
        hints = [o for o in r.get("open_refs") or [] if o.get("hint")]
        run = segs[r["from_i"]:r["to_i"] + 1]
        starts = [s["start"] for s in run if s.get("start") is not None]
        ends = [s["end"] for s in run if s.get("end") is not None]
        h = int(run[0]["h"])
        parent_row = r.get("parent")
        # Who says it is READ OFF THE LINES, never drafted (ruling ba341c72 (1)): the first line's
        # speaker; every speaker in order when the run crosses a turn.
        voices = list(dict.fromkeys(s["speaker"] for s in run if s.get("speaker")))
        if r["kind"] == SECTION_KIND:
            voices = []   # a synthesized title is nobody's line (ruling bc62c727 (A)): its anchor line only places it
        out.append({
            "proposal_id": str(uuid.uuid4()),
            "kind": r["kind"], "text": r["text"], "lead": r["lead"],
            "speaker": (voices[0] if voices else ""), "speakers": (voices if len(voices) > 1 else []),
            # rows -> proposal ids: a proposal id IS the point's future key, so these are point keys at accept
            "refers_to": ([out[t]["proposal_id"] for t in (r.get("refers_to") or [])]
                          + [by_key[o["point"]] for o in keyed if o["role"] == "refers_to"]),
            "attribution": r["attribution"], "data": r["data"],
            "from_i": r["from_i"], "to_i": r["to_i"],
            "segment_ids": [s["id"] for s in run],
            "start_time": (round(min(starts), 3) if starts else None),
            "end_time": (round(max(ends), 3) if ends else None),
            "heading": (headers[h - 1]["text"] if h > 0 and h - 1 < len(headers) else ""),
            "heading_index": h,
            # ONE level of nesting (e1fd4d64 (H)): the parent's proposal id becomes the point's
            # `parent_key` at accept (validated as an earlier, top-level, same-header row).
            "parent_key": (out[parent_row]["proposal_id"] if parent_row is not None
                           else next((by_key[o["point"]] for o in keyed if o["role"] == "parent"), "")),
            **({"open_refs": hints} if hints else {}),
            "evidence": {"pack_id": pack.get("pack_id"), "digest": pack.get("digest"),
                         "from_i": r["from_i"], "to_i": r["to_i"]},
        })
    # Source order, with every descendant DIRECTLY after its ancestors so an in-order accept
    # never meets a child before its parent; the synopsis (unit-spanning) sorts last.
    pos = {p["proposal_id"]: i for i, p in enumerate(out)}   # row position breaks ties between tops on the same lines
    by_id = {p["proposal_id"]: p for p in out}

    def _path(p: Dict[str, Any]) -> List[int]:  # row positions root -> … -> p
        chain: List[int] = []
        cur: Optional[Dict[str, Any]] = p
        while cur is not None and len(chain) < 8:
            chain.append(pos[cur["proposal_id"]])
            cur = by_id.get(cur["parent_key"]) if cur["parent_key"] else None
        return chain[::-1]

    rows_in_order = list(out)   # list.sort empties the list while it runs — index the snapshot

    def _key(p: Dict[str, Any]) -> Tuple[int, int, int, List[int]]:
        path = _path(p)
        root = rows_in_order[path[0]]
        return (1 if p["kind"] == "synopsis" else 0, root["from_i"], root["to_i"], path)
    out.sort(key=_key)
    return out


def write_notes_propset(
    pack: Dict[str, Any],             # The pack the proposals came from
    proposals: List[Dict[str, Any]],  # proposals_from_point_rows output
    *,
    out_root: Path,                   # Proposal-set root
    proposer: Dict[str, Any],         # Provenance: {"kind": ..., "name": ..., "model": ...}
    arm: Optional[str] = None,        # The experiment arm this set belongs to (work item 3a2c94eb (4))
    extra: Optional[Dict[str, Any]] = None,  # Further manifest fields (a merge's `merged_from`, a close's `closed_from`)
) -> Dict[str, Any]:  # {set_id, set_dir, manifest_path, counts}
    """Write one notes proposal set: `<out_root>/<set_id>/manifest.json` + `proposals.jsonl`
    — the durable half of the propose/accept contract (the filter lane's shape)."""
    started = time.time()
    set_id = f"npropset_{time.strftime('%Y%m%d_%H%M%S', time.localtime(started))}_{uuid.uuid4().hex[:8]}"
    set_dir = Path(out_root) / set_id
    set_dir.mkdir(parents=True)
    with open(set_dir / "proposals.jsonl", "w") as f:
        for p in proposals:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    counts: Dict[str, int] = {}
    for p in proposals:
        counts[p["kind"]] = counts.get(p["kind"], 0) + 1
    manifest = {"format": NOTES_PROPSET_FORMAT, "version": NOTES_PROPSET_VERSION,
                "proposal_set_id": set_id, "created_at": started, "type": pack.get("type"),
                "model": dict(proposer),
                "pack": {"pack_id": pack.get("pack_id"), "digest": pack.get("digest"),
                         "segments": len(pack.get("segments") or [])},
                "source": dict(pack.get("source") or {}), "window": dict(pack.get("window") or {}),
                "files": {"proposals": "proposals.jsonl"}, "counts": counts,
                **({"arm": str(arm)} if arm else {}), **dict(extra or {})}
    if (pack.get("plan") or {}).get("whole_pack_id"):
        manifest["plan"] = dict(pack["plan"])   # which window of which whole pack this set drafted
    (set_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    return {"set_id": set_id, "set_dir": str(set_dir), "manifest_path": str(set_dir / "manifest.json"),
            "counts": counts, "proposals": len(proposals)}


def load_notes_propsets(
    root: Path,                          # Proposal-set root
    source_id: Optional[str] = None,     # Restrict to one source (None = all)
) -> List[Dict[str, Any]]:  # [{manifest, path, proposals}] newest first
    """Every notes proposal set under `root` (optionally for one source), newest first."""
    root = Path(root)
    if not root.is_dir():
        return []
    found: List[Dict[str, Any]] = []
    for mp in sorted(root.glob("*/manifest.json")):
        try:
            m = json.loads(mp.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if m.get("format") != NOTES_PROPSET_FORMAT:
            continue
        if source_id and (m.get("source") or {}).get("source_id") != source_id:
            continue
        pf = mp.parent / str((m.get("files") or {}).get("proposals") or "proposals.jsonl")
        try:
            proposals = [json.loads(l) for l in pf.read_text().splitlines() if l.strip()]
        except (OSError, json.JSONDecodeError):
            continue
        found.append({"manifest": m, "path": str(mp), "proposals": proposals})
    found.sort(key=lambda d: float(d["manifest"].get("created_at") or 0.0), reverse=True)
    return found


def pick_propset(
    sets: List[Dict[str, Any]],    # load_notes_propsets output (newest first)
    selector: Optional[str],       # A set id / prefix; None = newest
) -> Optional[Dict[str, Any]]:  # The chosen set, or None
    """Choose a proposal set: newest by default, else the unique id/prefix match."""
    if not sets:
        return None
    if not selector:
        return sets[0]
    hits = [s for s in sets if str(s["manifest"].get("proposal_set_id") or "").startswith(selector)]
    return hits[0] if len(hits) == 1 else None


def points_index(
    proposals: List[Dict[str, Any]],  # Proposal rows of ONE lineage (one arm's earlier windows, or a merged set), any order
) -> List[Dict[str, Any]]:  # [{key, proposal_id, kind, lead, text, start_time, depth, parent}] in source order
    """Key a set of proposal rows for a reader that cannot see their lines: `p001`… in source
    order (structure kinds left out — a synopsis or a section is nobody's back-link target),
    each with its nesting depth so a later row never nests under a grandchild. One index
    serves both readers of work item 3a2c94eb: the SEQUENTIAL drafter's running view of the
    earlier windows' points (arm (4)) and the reconciler that closes hinted references (3).
    A block merge's pending EXTRAS are not points yet (`extra_list` keys them apart)."""
    rows = [p for p in proposals if p.get("kind") not in STRUCTURE_KINDS and not p.get("extra")]
    by_id = {p["proposal_id"]: p for p in rows}

    def _depth(p: Dict[str, Any]) -> int:
        d, cur = 0, p
        while cur.get("parent_key") and cur["parent_key"] in by_id and d < 8:
            d, cur = d + 1, by_id[cur["parent_key"]]
        return d

    def _root_start(p: Dict[str, Any]) -> float:
        cur, hops = p, 0
        while cur.get("parent_key") and cur["parent_key"] in by_id and hops < 8:
            cur, hops = by_id[cur["parent_key"]], hops + 1
        return float(cur.get("start_time") or 0.0)

    rows.sort(key=lambda p: (_root_start(p), _depth(p) > 0, float(p.get("start_time") or 0.0)))
    # children directly after their parent, in the order the sort left them
    ordered: List[Dict[str, Any]] = []

    def _emit(p: Dict[str, Any]) -> None:
        ordered.append(p)
        for c in rows:
            if c.get("parent_key") == p["proposal_id"]:
                _emit(c)
    for p in rows:
        if not (p.get("parent_key") and p["parent_key"] in by_id):
            _emit(p)
    width = max(3, len(str(len(ordered))))
    keys = {p["proposal_id"]: f"p{n:0{width}d}" for n, p in enumerate(ordered, start=1)}
    return [{"key": keys[p["proposal_id"]], "proposal_id": p["proposal_id"], "kind": p.get("kind"),
             "lead": p.get("lead") or "", "text": p.get("text") or "", "start_time": p.get("start_time"),
             "speaker": p.get("speaker") or "", "depth": _depth(p),
             "parent": keys.get(p.get("parent_key") or "", "")} for p in ordered]


def render_points_index(
    index: List[Dict[str, Any]],  # points_index output
) -> List[str]:  # One markdown line per point, children indented under their parent
    """The index as a reader sees it: `p017 [claim] 12:03  **lead** — text`, nested by depth."""
    lines: List[str] = []
    for e in index:
        lead = f"**{e['lead']}** — " if e.get("lead") else ""
        who = f" ({e['speaker']})" if e.get("speaker") else ""
        lines.append(f"{'  ' * int(e.get('depth') or 0)}- `{e['key']}` [{e.get('kind')}] {_fmt_ts(e.get('start_time'))}{who}  {lead}{e.get('text')}")
    return lines


def with_points_index(
    pack: Dict[str, Any],             # A WINDOW pack (build_notes_pack with a window)
    proposals: List[Dict[str, Any]],  # The earlier windows' proposal rows, one lineage
) -> Dict[str, Any]:  # A NEW pack (own id + digest) whose brief opens on the points so far
    """The SEQUENTIAL arm's pack (design 6752db0a (11)): the window pack plus a running index
    of the points the earlier windows produced, so the drafter names an earlier point by KEY
    (`open_refs` … `point`) instead of describing it. What was read changed, so the pack is a
    new one — new id, the index inside the digest."""
    out = dict(pack)
    out["index"] = points_index(proposals)
    out["pack_id"] = f"npack_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    out["indexed_from"] = pack.get("pack_id")
    out["created_at"] = time.time()
    out["digest"] = pack_digest(out)
    return out


def _line_iou(a: Tuple[int, int], b: Tuple[int, int]) -> float:  # intersection over union of two inclusive line runs
    """Whole-pack line agreement of two runs: 0.0 when they share no line."""
    inter = min(a[1], b[1]) - max(a[0], b[0]) + 1
    if inter <= 0:
        return 0.0
    return inter / float((a[1] - a[0] + 1) + (b[1] - b[0] + 1) - inter)


def _resolve_cell_rows(
    sets: List[Dict[str, Any]],  # load_notes_propsets-shaped entries ({"manifest", "proposals"}): window sets and whole sets, any arms
    pack: Dict[str, Any],        # The WHOLE-unit pack every row re-resolves against
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:  # ([{p, run, cell, origin}] in line order, the seed stats)
    """The merges' shared first step: every drafted row of every set re-resolved by SEGMENT ID
    into the whole pack's line numbers — loud when a segment is not in the pack (the spine
    moved or the pack is the wrong one) and when a set drafted another source — so a
    window-relative run, heading and speaker are all re-read from the whole unit. Structure
    kinds are left out and counted (a synopsis and the sections are proposed over the whole
    source after the merge, ruling bc62c727). A CELL is one (arm, model); the `origin` names
    what the fold keeps of the row (set, proposal, cell, window, run, kind, wording)."""
    segs = pack.get("segments") or []
    pos = {r["id"]: r["i"] for r in segs}
    src_id = (pack.get("source") or {}).get("source_id")
    flat: List[Dict[str, Any]] = []
    stats: Dict[str, Any] = {"sets": len(sets), "cells": {}, "structure_dropped": 0}
    for entry in sets:
        m = entry.get("manifest") or {}
        if (m.get("source") or {}).get("source_id") != src_id:
            raise ValueError(f"set {m.get('proposal_set_id')}: a different source than the target pack")
        model = str((m.get("model") or {}).get("model") or (m.get("model") or {}).get("name") or "?")
        arm = str(m.get("arm") or "?")
        cell = f"{arm}/{model}"
        for p in entry.get("proposals") or []:
            if p.get("kind") in STRUCTURE_KINDS:
                stats["structure_dropped"] += 1
                continue
            ids = list(p.get("segment_ids") or [])
            missing = [i for i in ids if i not in pos]
            if not ids or missing:
                raise ValueError(f"set {m.get('proposal_set_id')} proposal {p.get('proposal_id')}: "
                                 f"{len(missing) or 'all'} segment id(s) not in the target pack")
            run = (min(pos[i] for i in ids), max(pos[i] for i in ids))
            stats["cells"][cell] = stats["cells"].get(cell, 0) + 1
            flat.append({"p": p, "run": run, "cell": cell,
                         "origin": {"set_id": m.get("proposal_set_id"), "proposal_id": p.get("proposal_id"),
                                    "arm": arm, "model": model, "cell": cell,
                                    "window": (m.get("plan") or {}).get("k"),
                                    "from_i": run[0], "to_i": run[1], "kind": p.get("kind"),
                                    "lead": p.get("lead") or "", "text": p.get("text") or ""}})
    stats["inputs"] = len(flat)
    flat.sort(key=lambda f: (f["run"][0], f["run"][1], f["cell"], str(f["origin"]["set_id"]), str(f["origin"]["proposal_id"])))
    return flat, stats


def _merged_row(
    rep: Dict[str, Any],             # The member whose wording is SHOWN ({p, run, cell, origin}, optionally `how`)
    members: List[Dict[str, Any]],   # Every member of the row, rep included — each becomes an origin, in this order
    pack: Dict[str, Any],            # The whole-unit pack (its lines and headers)
    new_id: str,                     # The merged row's proposal id (the point's future key)
    **more: Any,                     # Further fields on the row (a block merge's `block`, `extra`)
) -> Dict[str, Any]:  # One merged row — links still member ids, `_parent_hints` / `_ref_hints` pending `_fold_links`
    """One merged row from its members: the shown member's kind, text, lead, data and nesting
    (its text was written for that parent); the run, times, speaker and heading re-read from
    the whole pack; back-links the UNION of the members' (a link any arm found is a candidate
    the human sees); every member an origin with `how` it joined — `shown` for the wording
    read, `matched` for a row that agreed on the lines, else what the caller set."""
    segs = pack.get("segments") or []
    headers = pack.get("headers") or []
    p, (fi, ti) = rep["p"], rep["run"]
    run = segs[fi:ti + 1]
    starts = [s["start"] for s in run if s.get("start") is not None]
    ends = [s["end"] for s in run if s.get("end") is not None]
    voices = list(dict.fromkeys(s["speaker"] for s in run if s.get("speaker")))
    h = int(run[0].get("h") or 0)
    ordered = [rep] + [f for f in members if f is not rep]
    return {
        "proposal_id": new_id, "kind": p.get("kind"), "text": p.get("text"), "lead": p.get("lead") or "",
        "speaker": (voices[0] if voices else ""), "speakers": (voices if len(voices) > 1 else []),
        "refers_to": list(dict.fromkeys(t for f in ordered for t in (f["p"].get("refers_to") or []))),
        "attribution": p.get("attribution") or "",
        "data": p.get("data") or {}, "from_i": fi, "to_i": ti,
        "segment_ids": [s["id"] for s in run],
        "start_time": (round(min(starts), 3) if starts else None),
        "end_time": (round(max(ends), 3) if ends else None),
        "heading": (headers[h - 1]["text"] if h > 0 and h - 1 < len(headers) else ""), "heading_index": h,
        "parent_key": str(p.get("parent_key") or ""),
        "_parent_hints": [o for o in p.get("open_refs") or [] if o.get("role") == "parent"],
        "_ref_hints": [o for f in ordered for o in f["p"].get("open_refs") or [] if o.get("role") != "parent"],
        "evidence": {"pack_id": pack.get("pack_id"), "digest": pack.get("digest"), "from_i": fi, "to_i": ti},
        "shown": rep["cell"],
        "origins": [dict(f["origin"], how=(f.get("how") or ("shown" if f is rep else "matched"))) for f in ordered],
        **more,
    }


def _fold_links(
    rows: List[Dict[str, Any]],  # Merged rows (`_merged_row` output) whose links still name MEMBER ids
    id_map: Dict[str, str],      # Member proposal id -> the merged row it became part of
) -> Dict[str, int]:  # {"detached_parents", "dropped_refs"} — the links the fold could not honour
    """The merges' shared link fold: `parent_key` and `refers_to` follow the collapse (a member's
    id maps to its merged row, across sets — a sequential drafter's keyed reference lands here).
    A hinted `refers_to` survives only on a row no member closed a back-link for; a parent hint
    only where no parent landed. A link the merge cannot honour — target gone, collapsed into
    the row itself, another header, a cycle, a third level — is DETACHED and counted, never
    bent. Rows are updated in place; the pending hint fields are consumed."""
    by_id = {r["proposal_id"]: r for r in rows}
    detached = dropped_refs = 0
    for r in rows:   # member ids -> merged ids
        refs = [id_map.get(t) for t in r["refers_to"]]
        kept = list(dict.fromkeys(t for t in refs if t and t != r["proposal_id"]))
        # LOST links only (target left out, or collapsed into this very row) — members agreeing on one target is a fold, not a loss
        dropped_refs += sum(1 for t in refs if not t or t == r["proposal_id"])
        r["refers_to"] = kept
        if r["parent_key"]:
            t = id_map.get(r["parent_key"]) or ""
            if not t or t == r["proposal_id"] or by_id[t]["heading_index"] != r["heading_index"]:
                detached += 1
                t = ""
            r["parent_key"] = t
        parent_hints, ref_hints = r.pop("_parent_hints", []), r.pop("_ref_hints", [])
        still_open = (([] if r["parent_key"] else parent_hints[:1])
                      + ([] if kept else list({o["hint"]: o for o in ref_hints}.values())))
        if still_open:
            r["open_refs"] = still_open
    for r in rows:   # a cycle or a third level: detach the link that made it
        seen, cur, depth = {r["proposal_id"]}, r, 0
        while cur["parent_key"]:
            nxt = by_id[cur["parent_key"]]
            depth += 1
            if nxt["proposal_id"] in seen or depth > 2:
                r["parent_key"] = ""
                detached += 1
                break
            seen.add(nxt["proposal_id"])
            cur = nxt
    return {"detached_parents": detached, "dropped_refs": dropped_refs}


def _accept_order(
    rows: List[Dict[str, Any]],  # Merged rows with their links folded (or a note's Points in proposal shape — `ordinal` stands in for the run)
) -> List[Dict[str, Any]]:  # The rows in accept order
    """Accept order: source order (first line, last line, id), every descendant DIRECTLY after
    its ancestors so an in-order accept never meets a child before its parent."""
    ordered = sorted(rows, key=lambda r: (int(r.get("from_i", r.get("ordinal", 0)) or 0), int(r.get("to_i", 0) or 0), r["proposal_id"]))
    kids: Dict[str, List[Dict[str, Any]]] = {}
    for r in ordered:
        if r.get("parent_key"):
            kids.setdefault(r["parent_key"], []).append(r)
    out: List[Dict[str, Any]] = []

    def _emit(r: Dict[str, Any]) -> None:
        out.append(r)
        for k in kids.get(r["proposal_id"], []):
            _emit(k)
    for r in ordered:
        if not r.get("parent_key"):
            _emit(r)
    return out


def merge_point_proposals(
    sets: List[Dict[str, Any]],  # load_notes_propsets-shaped entries ({"manifest", "proposals"}): window sets and whole sets, any arms
    pack: Dict[str, Any],        # The WHOLE-unit pack every row re-resolves against
    *,
    iou: float = 0.5,            # Whole-pack line IoU at/above which two rows of different cells are ONE point
    same_kind: bool = True,      # Agreement also needs the same kind
) -> Dict[str, Any]:  # {"proposals": merged rows in accept order, "stats": {...}}
    """The ROW-LEVEL merge (work item 3a2c94eb (2); the filter lane's `merge_filter_proposals`
    read over points) — kept as the sweep's measure and the first experiment's evidence; the
    lane's merge is `merge_point_blocks` (ruling 1798a796: a row is not a comparable unit
    across drafters, a block of source is). Each row re-resolves by SEGMENT ID into the whole
    pack's line numbers (`_resolve_cell_rows`). A CELL is one (arm, model); rows of DIFFERENT
    cells whose runs agree collapse into one row whose `origins` name every contributing (set,
    proposal, arm, model, window, run, kind, text) — never two rows of one cell, which drafted
    them as distinct points. The text SHOWN for a collapsed row rotates across the agreeing
    cells (the cell shown least so far wins, user ruling 2026-09-21), and `shown` records
    whose wording the human read, so an edit at accept is attributable. Structure kinds are
    left out. Links follow the collapse (`_fold_links`): nesting is the SHOWN row's, back-links
    the union of the members', a link the merge cannot honour detached and counted."""
    flat, stats = _resolve_cell_rows(sets, pack)
    clusters: List[List[Dict[str, Any]]] = []
    for f in flat:
        best, best_v = None, 0.0
        for c in clusters:
            if c[0]["run"][1] < f["run"][0] or c[0]["run"][0] > f["run"][1]:
                continue   # no shared line
            if f["cell"] in {x["cell"] for x in c} or (same_kind and c[0]["p"].get("kind") != f["p"].get("kind")):
                continue
            v = _line_iou(c[0]["run"], f["run"])
            if v >= float(iou) and v > best_v:
                best, best_v = c, v
        if best is None:
            clusters.append([f])
        else:
            best.append(f)
    # the wording SHOWN for a collapsed row rotates: the agreeing cell shown least so far (ties by name)
    shown: Dict[str, int] = {c: 0 for c in stats["cells"]}
    id_map: Dict[str, str] = {}
    merged: List[Dict[str, Any]] = []
    for c in clusters:
        rep = c[0] if len(c) == 1 else min(c, key=lambda f: (shown[f["cell"]], f["cell"]))
        if len(c) > 1:
            shown[rep["cell"]] += 1
        new_id = str(uuid.uuid4())
        for f in c:
            id_map[str(f["p"].get("proposal_id"))] = new_id
        merged.append(_merged_row(rep, c, pack, new_id))
    links = _fold_links(merged, id_map)
    out = _accept_order(merged)
    sizes: Dict[str, int] = {}
    alone: Dict[str, int] = {c: 0 for c in stats["cells"]}
    for r in out:
        sizes[str(len(r["origins"]))] = sizes.get(str(len(r["origins"])), 0) + 1
        if len(r["origins"]) == 1:
            alone[r["shown"]] += 1
    stats.update({"merged": len(out), "by_agreement": dict(sorted(sizes.items())), "alone_by_cell": alone,
                  "shown_by_cell": shown, **links,
                  "open_refs": sum(len(r.get("open_refs") or []) for r in out),
                  "iou": float(iou), "same_kind": bool(same_kind)})
    return {"proposals": out, "stats": stats}


def plan_notes_blocks(
    extents: Dict[str, List[Tuple[int, int]]],  # Per cell: every root SUBTREE's full line extent (from_i, to_i), whole-pack lines
    n_lines: int,                                # Lines in the whole pack
    *,
    max_lines: int = 40,                         # A longer block splits at the seam the fewest cells span
) -> List[Dict[str, Any]]:  # [{k, from_i, to_i, lines, seam, spanned}] tiling the pack in order
    """Cut the whole pack into BLOCKS (ruling 1798a796, fork 1): a block runs between COMMON
    SEAMS — line boundaries no cell's root subtree (root + descendants, full extent) spans — so
    one cell's rows can be shown WHOLE per block with their nesting intact. Root runs alone
    will not do: a child's run usually sits outside its parent's, so blocks cut on root runs
    degenerate to one line each (the pre-build datum in the ruling). A block longer than
    `max_lines` splits at the internal boundary the FEWEST cells span (ties: nearest its
    middle) and both halves are checked again; `seam` records how the block opened (`common`,
    or `split:<cells spanning>`) and `spanned` names the cells whose subtree crosses its
    opening — the rows the rotation shows across a cut."""
    if n_lines <= 0:
        return []
    spanned: List[set] = [set() for _ in range(n_lines + 1)]   # boundary j sits between line j-1 and line j
    for cell, exts in extents.items():
        for a, b in exts:
            for j in range(a + 1, b + 1):
                spanned[j].add(cell)
    blocks: List[Tuple[int, int, str]] = []
    start = 0
    for j in [j for j in range(1, n_lines) if not spanned[j]] + [n_lines]:
        blocks.append((start, j - 1, "common"))
        start = j
    out: List[Tuple[int, int, str]] = []
    stack = blocks[::-1]
    while stack:
        a, b, seam = stack.pop()
        if b - a + 1 <= int(max_lines) or b <= a:
            out.append((a, b, seam))
            continue
        mid = (a + b + 1) / 2.0
        j = min(range(a + 1, b + 1), key=lambda j: (len(spanned[j]), abs(j - mid), j))
        stack.append((j, b, f"split:{len(spanned[j])}"))
        stack.append((a, j - 1, seam))
    return [{"k": k, "from_i": a, "to_i": b, "lines": b - a + 1, "seam": s,
             "spanned": (sorted(spanned[a]) if a > 0 else [])} for k, (a, b, s) in enumerate(out)]


def merge_point_blocks(
    sets: List[Dict[str, Any]],  # load_notes_propsets-shaped entries ({"manifest", "proposals"}): window sets and whole sets, any arms
    pack: Dict[str, Any],        # The WHOLE-unit pack every row re-resolves against
    *,
    max_lines: int = 40,         # A block longer than this splits at the seam the fewest cells span
    iou: float = 0.5,            # Whole-pack line IoU at/above which another cell's row MATCHES a shown row
    same_kind: bool = True,      # A match also needs the same kind
) -> Dict[str, Any]:  # {"proposals": shown rows + EXTRAS in accept order, "blocks": the plan, "stats": {...}}
    """The BLOCK merge (ruling 1798a796, fork 1; work item 1561551e): the unit of agreement is
    a stretch of source, not a row — grain differs across drafters, so no row-to-row overlap
    threshold reconciles one row over a stretch with another's two (finding dfc75128). The
    pack is cut into blocks at the seams no cell's root subtree spans (`plan_notes_blocks`; a
    subtree sits in the block its first line falls in). Per block ONE cell's rows are shown
    WHOLE (a parent with its children, one author, so nesting stays coherent), the shown cell
    rotating to the one with the fewest rows shown so far (ties by name). Every other cell's
    row then MATCHES the shown row its lines agree with (best IoU at or above the threshold,
    the kind rule, at most one row per cell per shown row) and becomes an origin of it — or,
    unmatched, an EXTRA: a row flagged `extra` in its block, for the judge
    (`render_judge_brief` / `apply_judgements`) to classify as a grain variant (credited, not
    shown) or a genuinely different point (added). Attribution is then complete: every
    drafted row is shown, matched, or an extra awaiting its verdict (`stats.accounting`).
    merge_point_proposals's contract holds: rows re-resolve by segment id, structure kinds are
    left out, keyed cross-window links follow the fold, links the merge cannot honour are
    detached and counted."""
    flat, stats = _resolve_cell_rows(sets, pack)
    n = len(pack.get("segments") or [])
    by_pid = {str(f["p"]["proposal_id"]): f for f in flat}
    root_of: Dict[str, str] = {}
    for f in flat:
        cur, hops = f, 0
        while str(cur["p"].get("parent_key") or "") in by_pid and hops < 8:   # climb to the in-cell root
            cur, hops = by_pid[str(cur["p"]["parent_key"])], hops + 1
        root_of[str(f["p"]["proposal_id"])] = str(cur["p"]["proposal_id"])
    extent: Dict[str, Tuple[int, int]] = {}
    for f in flat:
        r = root_of[str(f["p"]["proposal_id"])]
        a, b = extent.get(r, (n, -1))
        extent[r] = (min(a, f["run"][0]), max(b, f["run"][1]))   # a SUBTREE's full extent
    per_cell: Dict[str, List[Tuple[int, int]]] = {}
    for r, ext in extent.items():
        per_cell.setdefault(by_pid[r]["cell"], []).append(ext)
    blocks = plan_notes_blocks(per_cell, n, max_lines=max_lines)
    block_of_line: List[int] = [0] * n
    for b in blocks:
        for i in range(b["from_i"], b["to_i"] + 1):
            block_of_line[i] = b["k"]
    in_block: Dict[int, Dict[str, int]] = {}
    for f in flat:
        f["block"] = block_of_line[extent[root_of[str(f["p"]["proposal_id"])]][0]]
        in_block.setdefault(f["block"], {})
        in_block[f["block"]][f["cell"]] = in_block[f["block"]].get(f["cell"], 0) + 1
    # rotation: per block the cell with the fewest rows shown so far (ties by name)
    shown_by_cell: Dict[str, int] = {c: 0 for c in stats["cells"]}
    shown_cell: Dict[int, str] = {}
    for b in blocks:
        present = in_block.get(b["k"]) or {}
        b["cells"] = dict(sorted(present.items()))
        if not present:
            continue
        c = min(present, key=lambda c: (shown_by_cell[c], c))
        shown_cell[b["k"]] = b["shown"] = c
        shown_by_cell[c] += present[c]
    shown = [f for f in flat if shown_cell.get(f["block"]) == f["cell"]]
    others = [f for f in flat if shown_cell.get(f["block"]) != f["cell"]]
    # matching: greedy by IoU over every (other row, shown row) sharing a line — one row per cell per shown row
    cand: List[Tuple[float, int, int]] = []
    for oi, o in enumerate(others):
        for si, s in enumerate(shown):
            if s["run"][1] < o["run"][0] or s["run"][0] > o["run"][1]:
                continue
            if same_kind and s["p"].get("kind") != o["p"].get("kind"):
                continue
            v = _line_iou(s["run"], o["run"])
            if v >= float(iou):
                cand.append((v, oi, si))
    cand.sort(key=lambda t: (-t[0], t[1], t[2]))
    taken: set = set()
    matched: Dict[int, int] = {}
    for v, oi, si in cand:
        if oi in matched or (si, others[oi]["cell"]) in taken:
            continue
        matched[oi] = si
        taken.add((si, others[oi]["cell"]))
    members: Dict[int, List[Dict[str, Any]]] = {si: [s] for si, s in enumerate(shown)}
    for oi, si in matched.items():
        members[si].append(others[oi])
    id_map: Dict[str, str] = {}
    rows: List[Dict[str, Any]] = []
    for si, s in enumerate(shown):
        new_id = str(uuid.uuid4())
        for f in members[si]:
            id_map[str(f["p"]["proposal_id"])] = new_id
        rows.append(_merged_row(s, members[si], pack, new_id, block=s["block"]))
    for oi, o in enumerate(others):
        if oi in matched:
            continue
        new_id = str(uuid.uuid4())
        id_map[str(o["p"]["proposal_id"])] = new_id
        o["how"] = "extra"
        rows.append(_merged_row(o, [o], pack, new_id, block=o["block"], extra=True))
    links = _fold_links(rows, id_map)
    out = _accept_order(rows)
    acct: Dict[str, Dict[str, int]] = {c: {"rows": stats["cells"][c], "shown": 0, "matched": 0, "extra": 0}
                                       for c in stats["cells"]}
    sizes: Dict[str, int] = {}
    for r in out:
        for o in r["origins"]:
            acct[o["cell"]][o["how"]] += 1
        if not r.get("extra"):
            sizes[str(len(r["origins"]))] = sizes.get(str(len(r["origins"])), 0) + 1
    stats.update({"merged": sum(1 for r in out if not r.get("extra")), "extras": sum(1 for r in out if r.get("extra")),
                  "blocks": len(blocks), "split_blocks": sum(1 for b in blocks if str(b["seam"]).startswith("split")),
                  "longest_block": max((b["lines"] for b in blocks), default=0),
                  "by_agreement": dict(sorted(sizes.items())), "shown_by_cell": shown_by_cell, "accounting": acct,
                  **links, "open_refs": sum(len(r.get("open_refs") or []) for r in out),
                  "iou": float(iou), "same_kind": bool(same_kind), "max_lines": int(max_lines)})
    return {"proposals": out, "blocks": blocks, "stats": stats}


def extra_list(
    proposals: List[Dict[str, Any]],  # A block-merged set's rows (`merge_point_blocks` output)
) -> List[Dict[str, Any]]:  # [{key, proposal_id, block, cell, kind, lead, text, start_time, speaker, parent}] in set order — `x001`… are stable for the set
    """Every EXTRA still pending in a set, keyed `x001`… in the set's order — the ids the judge
    answers by and `apply_judgements` applies. `parent` is the key of the row the extra nests
    under (a shown point's `p` key, or another extra's `x` key), "" at top level."""
    rows = [p for p in proposals if p.get("extra")]
    width = max(3, len(str(len(rows))))
    xkeys = {str(p["proposal_id"]): f"x{n:0{width}d}" for n, p in enumerate(rows, start=1)}
    pkeys = {e["proposal_id"]: e["key"] for e in points_index(proposals)}
    return [{"key": xkeys[str(p["proposal_id"])], "proposal_id": p["proposal_id"], "block": p.get("block"),
             "cell": p.get("shown") or "", "kind": p.get("kind"), "lead": p.get("lead") or "", "text": p.get("text") or "",
             "start_time": p.get("start_time"), "speaker": p.get("speaker") or "",
             "parent": pkeys.get(p.get("parent_key") or "") or xkeys.get(p.get("parent_key") or "") or ""} for p in rows]


def unjudged_pairs(
    proposals: List[Dict[str, Any]],  # A set's rows (a merged set), or a note's Points as `points_as_proposals` shapes them
) -> List[Dict[str, Any]]:  # [{key, a: {key, proposal_id, …}, b: {…}, shared}] in index order — `q001`… are stable for the rows
    """STANDING DETECTION (ruling 1798a796 (1)): the pairs of points that leave a draft
    UNCLEAN — two rows deriving from shared segments, from different origins (no drafter cell
    contributed to both; a row with no origins counts as unknown, so it flags), with no
    judgement recorded on the pair. Legitimate relations do not flag: a parent and its child,
    and a pair either row's `judged` names (`different` / `related` — recorded by
    `apply_judgements`, carried onto the Points at accept). Structure rows and pending extras
    are not points yet. Holds for any draft at any time — a re-draft after a spine correction,
    a second source on one topic — not only for the merge that produced it."""
    index = points_index(proposals)
    by_id = {p["proposal_id"]: p for p in proposals}
    rows = [by_id[e["proposal_id"]] for e in index]
    keys = {e["proposal_id"]: e["key"] for e in index}

    def _cells(p: Dict[str, Any]) -> set:
        # the cells that AUTHORED the point (shown / matched / added), never a fold-in (`same` / `contains`): a
        # compound folded into several finer survivors copies its origins onto each of them, and those copies must
        # not make two other drafters' rows look like one drafter's (the nine-cell Bonus run, 2026-09-22)
        return {str(o.get("cell") or "") for o in (p.get("origins") or [])
                if o.get("cell") and str(o.get("how") or "shown") not in ("same", "contains")}

    def _judged(p: Dict[str, Any], other: str) -> bool:
        return any(str(j.get("key")) == other for j in (p.get("judged") or []))

    def _entry(p: Dict[str, Any]) -> Dict[str, Any]:
        return {"key": keys[p["proposal_id"]], "proposal_id": p["proposal_id"], "kind": p.get("kind"),
                "lead": p.get("lead") or "", "text": p.get("text") or "", "start_time": p.get("start_time"),
                "speaker": p.get("speaker") or "", "cells": sorted(_cells(p))}
    out: List[Dict[str, Any]] = []
    for i, a in enumerate(rows):
        sa = set(a.get("segment_ids") or [])
        for b in rows[i + 1:]:
            shared = sorted(sa & set(b.get("segment_ids") or []))
            if not shared:
                continue
            if a.get("parent_key") == b["proposal_id"] or b.get("parent_key") == a["proposal_id"]:
                continue
            ca, cb = _cells(a), _cells(b)
            if ca and cb and ca & cb:
                continue   # one drafter wrote both as distinct points
            if _judged(a, b["proposal_id"]) or _judged(b, a["proposal_id"]):
                continue
            out.append({"a": _entry(a), "b": _entry(b), "shared": shared, "same_kind": a.get("kind") == b.get("kind")})
    width = max(3, len(str(len(out))))
    for n, q in enumerate(out, start=1):
        q["key"] = f"q{n:0{width}d}"
    return out


def points_as_proposals(
    points: List[Dict[str, Any]],  # load_points output (a note's accepted Points)
) -> List[Dict[str, Any]]:  # The same rows in proposal shape: `proposal_id` = the point's key (what `parent_key` / `refers_to` / `judged` name)
    """An accepted draft read as a proposal set — a point's key IS its accepted proposal id, so
    the set-level judge (`unjudged_pairs`, `render_pairs_brief`, `apply_judgements`) works
    over a note's Points unchanged; `id` rides along for the graph write that follows."""
    return [{**p, "proposal_id": str(p.get("key") or "")} for p in points]


def render_judge_brief(
    proposals: List[Dict[str, Any]],  # A block-merged set's rows (`merge_point_blocks` output)
    blocks: List[Dict[str, Any]],     # The set's block plan (the merge's `blocks`, carried on the manifest)
    *,
    set_id: str = "",                 # The set the brief is about (printed only)
    source: str = "",                 # The source's title (printed only)
) -> str:  # The duplicate judge's brief over the EXTRAS (markdown)
    """The brief of the bounded JUDGEMENT (ruling 1798a796 (2)): ONE whole-source reader works
    from the keyed Points, never the spine (design 6752db0a (6)), and answers each EXTRA of
    the block merge beside the shown rows of its block — same statement / one contains the
    other / different points on shared lines / legitimately related. When one row contains
    two, the FINER rows win by default where each stands as a complete statement (fork 2);
    the judge may say otherwise, and never edits text. Blocks without extras are left out."""
    index = points_index(proposals)
    extras = extra_list(proposals)
    by_id = {p["proposal_id"]: p for p in proposals}
    shown_in: Dict[Any, List[Dict[str, Any]]] = {}
    for e in index:
        shown_in.setdefault(by_id[e["proposal_id"]].get("block"), []).append(e)
    extras_in: Dict[Any, List[Dict[str, Any]]] = {}
    for x in extras:
        extras_in.setdefault(x["block"], []).append(x)
    with_x = [b for b in blocks if extras_in.get(b["k"])]
    lines = [f"# Duplicate judge — set `{set_id}`", "", f"Source: **{source}**", "",
             f"The merge cut this source into {len(blocks)} blocks and per block showed ONE drafter's rows whole; the other "
             "drafters' rows that agree with a shown row on its lines were folded into it as origins. What remains are the "
             f"EXTRAS — {len(extras)} rows that matched nothing — listed under the shown rows of their block. Say what each "
             "extra is, judging by what the rows SAY on the lines they cite. Never rewrite anything.", "",
             "## Output contract", "",
             "ONE JSON object per line, one per extra, in the order listed:", "",
             '    {"extra": "x012", "verdict": "same", "of": "p034"}                        it states what p034 states — a rewording, or the same statement at another grain',
             '    {"extra": "x012", "verdict": "contains", "of": ["p034", "p035"], "keep": "shown"}   one contains the other: the extra is a compound of the rows named, or a finer statement inside ONE of them',
             '    {"extra": "x012", "verdict": "different", "of": ["p034"]}                 a different point that happens to share lines with the rows named ([] when it shares lines with none)',
             '    {"extra": "x012", "verdict": "related", "of": ["p034"]}                   legitimately beside them — a term beside a claim, a question beside its answer, a detail beside a summary that does not repeat it', "",
             "* `same`: the extra folds into the row named — its drafter is credited as an origin; the shown wording stays.",
             "* `contains`: `keep` names what stands. The FINER rows win by default where each stands as a complete statement — "
             "a merged compound is harder to check against its lines. So `\"keep\": \"shown\"` when the extra is the compound of "
             "the shown rows named; `\"keep\": \"extra\"` when the extra is the finer statement inside a shown compound AND the "
             "extra with its neighbours covers what the compound says. Over fragments that are not complete statements keep the "
             "complete row. The row that does not stand folds into the one that does.",
             "* `different` and `related`: the extra is ADDED as a point of its own and its pairing with the rows named is recorded as "
             "judged — so name in `of` EVERY shown row of the block that speaks about the same stretch (by time and content); a pair "
             "left unnamed comes back as an open pair.",
             "* When in doubt between `same` and `different`, a shared statement on shared lines is `same`.",
             "* Rows only — no prose, no code fences.", "",
             f"## Blocks ({len(with_x)} of {len(blocks)} carry extras)", ""]
    for b in with_x:
        k = b["k"]
        rows_in = [by_id[e["proposal_id"]] for e in shown_in.get(k, [])] + [by_id[x["proposal_id"]] for x in extras_in[k]]
        starts = [r["start_time"] for r in rows_in if r.get("start_time") is not None]
        ends = [r["end_time"] for r in rows_in if r.get("end_time") is not None]
        span = f" · {_fmt_ts(min(starts))}–{_fmt_ts(max(ends))}" if starts and ends else ""
        lines += [f"### Block {k} — lines {b['from_i']}–{b['to_i']}{span} · shown: {b.get('shown') or '—'}", "", "Shown:", ""]
        lines += render_points_index(shown_in.get(k, [])) or ["- (none)"]
        lines += ["", "Extras:", ""]
        for x in extras_in[k]:
            lead = f"**{x['lead']}** — " if x["lead"] else ""
            who = f" ({x['speaker']})" if x["speaker"] else ""
            under = f" · under {x['parent']}" if x["parent"] else ""
            lines.append(f"- `{x['key']}` [{x['kind']}] {_fmt_ts(x['start_time'])}{who} · {x['cell']}{under}  {lead}{x['text']}")
        lines.append("")
    return "\n".join(lines) + "\n"


def render_pairs_brief(
    proposals: List[Dict[str, Any]],  # A set's rows, or a note's Points as `points_as_proposals` shapes them
    *,
    set_id: str = "",                 # The set (or note) the brief is about (printed only)
    source: str = "",                 # The source's title (printed only)
) -> str:  # The overlap judge's brief over the UNJUDGED PAIRS (markdown)
    """The brief of the judgement over what standing detection flags (ruling 1798a796 (1)-(2)):
    every cross-origin pair of points sharing segments with no judgement yet, answered by ONE
    whole-source reader from the keyed Points — same statement / one contains the other /
    different points on shared lines / legitimately related. Serves a merged set before accept
    and an accepted draft alike (a re-draft after a spine correction, a second source on one
    topic); the full point list follows the pairs for context."""
    pairs = unjudged_pairs(proposals)
    index = points_index(proposals)
    lines = [f"# Overlap judge — `{set_id}`", "", f"Source: **{source}**", "",
             f"{len(pairs)} pair(s) of points derive from shared lines, come from different drafters, and carry no judgement — "
             "a draft with such a pair is not clean. A parent and its child never appear here. Judge each pair by what the "
             "two rows SAY on the lines they share; never rewrite anything. The full point list follows for context.", "",
             "## Output contract", "",
             "ONE JSON object per line, one per pair, in the order listed:", "",
             '    {"pair": "q003", "verdict": "same", "keep": "a"}        one statement twice (a rewording, or the same statement at another grain): `keep` names the row that stands; the other folds into it, credited as an origin',
             '    {"pair": "q003", "verdict": "contains", "keep": "b"}    one contains the other: `keep` names the row that stands — the FINER one by default when it is a complete statement (a compound is harder to check against its lines); the compound when the finer row is a fragment',
             '    {"pair": "q003", "verdict": "different"}                different points that happen to share lines — both stand',
             '    {"pair": "q003", "verdict": "related"}                  legitimately related — a term beside a claim, a question beside its answer, a detail beside a summary that does not repeat it; both stand', "",
             "* When in doubt between `same` and `different`, a shared statement on shared lines is `same`.",
             "* Rows only — no prose, no code fences.", "",
             f"## Pairs ({len(pairs)})", ""]
    for q in pairs:
        lines.append(f"- `{q['key']}` · {len(q['shared'])} shared line(s)")
        for side in ("a", "b"):
            e = q[side]
            lead = f"**{e['lead']}** — " if e.get("lead") else ""
            who = f" ({e['speaker']})" if e.get("speaker") else ""
            cells = f" · {', '.join(e['cells'])}" if e.get("cells") else ""
            lines.append(f"  - {side}: `{e['key']}` [{e.get('kind')}] {_fmt_ts(e.get('start_time'))}{who}{cells}  {lead}{e.get('text')}")
    if not pairs:
        lines.append("- (none — the draft is clean)")
    lines += ["", "## Points", ""] + render_points_index(index)
    return "\n".join(lines) + "\n"


def apply_judgements(
    proposals: List[Dict[str, Any]],  # A set's rows (a block-merged set, judged or not), or a note's Points as `points_as_proposals` shapes them
    answers: List[Dict[str, Any]],    # The judge's rows: {"extra": "x012", "verdict", "of", "keep"} | {"pair": "q003", "verdict", "keep"}
) -> Dict[str, Any]:  # {"proposals": rows after the fold (survivors keep their ids), "folded": [{loser, into, verdict}], "stats": {...}}
    """THE FOLD (ruling 1798a796 (3)): apply a judge's answers mechanically, loud on the first
    bad row — a known extra or pair answered once, `of` naming shown points of this set, a
    verdict from the four. A folded row is never deleted: it becomes an ORIGIN of every
    survivor it folds into (`how` = the verdict), so per-arm and per-model credit survives;
    its back-links join the survivor's and every link that named it now names the survivor
    (a child of a folded row nests under the survivor — or, when the survivor was that child,
    steps up to the folded row's parent). `same` folds the extra into the row named; on a
    pair `keep` names the survivor. `contains` folds the row that does not stand into the one
    that does (`keep`: shown / extra on an extra, a / b on a pair). `different` and `related`
    ADD the extra as a point and record the verdict on BOTH rows of each pairing, so standing
    detection never re-opens it. Nesting is re-checked after the fold: another header or a
    cycle detaches, a third level LIFTS the row beside its parent, both counted. An extra
    never answered stays an extra; a pair never answered stays unjudged — the stats say so."""
    verdicts = ("same", "contains", "different", "related")
    pkey = {e["key"]: e["proposal_id"] for e in points_index(proposals)}
    xkey = {x["key"]: x["proposal_id"] for x in extra_list(proposals)}
    qkey = {q["key"]: (q["a"]["proposal_id"], q["b"]["proposal_id"]) for q in unjudged_pairs(proposals)}
    out = [json.loads(json.dumps(p)) for p in proposals]
    by_id = {p["proposal_id"]: p for p in out}
    seen: set = set()
    folds: List[Tuple[str, str, str]] = []   # (loser, survivor, verdict)
    pairs: List[Tuple[str, str, str]] = []   # (a, b, verdict) — recorded on both
    added: List[str] = []
    for n, a in enumerate(answers, start=1):
        if not isinstance(a, dict):
            raise ValueError(f"judgement {n}: not an object")
        verdict = str(a.get("verdict") or "")
        if verdict not in verdicts:
            raise ValueError(f"judgement {n}: verdict {verdict!r} is not one of {' / '.join(verdicts)}")
        if a.get("extra"):
            xk = str(a["extra"])
            if xk not in xkey:
                raise ValueError(f"judgement {n}: unknown extra {xk!r}")
            if xk in seen:
                raise ValueError(f"judgement {n}: {xk} answered twice")
            seen.add(xk)
            xid = xkey[xk]
            of = a.get("of")
            of = [of] if isinstance(of, str) else list(of or [])
            bad = [k for k in of if k not in pkey]
            if bad:
                raise ValueError(f"judgement {n}: `of` {bad[0]!r} is not a shown point of this set")
            targets = list(dict.fromkeys(pkey[k] for k in of))
            if verdict == "same":
                if len(targets) != 1:
                    raise ValueError(f"judgement {n}: `same` names exactly ONE shown point in `of`")
                folds.append((xid, targets[0], "same"))
            elif verdict == "contains":
                if not targets:
                    raise ValueError(f"judgement {n}: `contains` names the shown point(s) involved in `of`")
                keep = str(a.get("keep") or "shown")
                if keep not in ("shown", "extra"):
                    raise ValueError(f"judgement {n}: `keep` is shown or extra")
                if keep == "shown":
                    folds += [(xid, t, "contains") for t in targets]
                else:
                    added.append(xid)
                    folds += [(t, xid, "contains") for t in targets]
            else:
                added.append(xid)
                pairs += [(xid, t, verdict) for t in targets]
        elif a.get("pair"):
            qk = str(a["pair"])
            if qk not in qkey:
                raise ValueError(f"judgement {n}: unknown pair {qk!r}")
            if qk in seen:
                raise ValueError(f"judgement {n}: {qk} answered twice")
            seen.add(qk)
            pa, pb = qkey[qk]
            if verdict in ("same", "contains"):
                keep = str(a.get("keep") or "")
                if keep not in ("a", "b"):
                    raise ValueError(f"judgement {n}: `{verdict}` on a pair names `keep` (a or b)")
                loser, survivor = (pb, pa) if keep == "a" else (pa, pb)
                folds.append((loser, survivor, verdict))
            else:
                pairs.append((pa, pb, verdict))
        else:
            raise ValueError(f"judgement {n}: names neither `extra` nor `pair`")
    # the fold: a loser's origins join EACH survivor; its id maps to the first (chains follow, a cycle refuses)
    gone: Dict[str, List[Tuple[str, str]]] = {}
    for loser, survivor, verdict in folds:
        if loser == survivor:
            raise ValueError(f"a row cannot fold into itself ({loser[:8]})")
        gone.setdefault(loser, []).append((survivor, verdict))

    def _final(x: str) -> str:
        path: set = set()
        while x in gone:
            if x in path:
                raise ValueError(f"a fold cycle through {x[:8]} — the judgements fold rows into each other")
            path.add(x)
            x = gone[x][0][0]
        return x
    for loser, survivors in gone.items():
        L = by_id[loser]
        for s_id, verdict in survivors:
            S = by_id[_final(s_id)]
            have = {(o.get("set_id"), o.get("proposal_id")) for o in S.get("origins") or []}
            S["origins"] = list(S.get("origins") or []) + [dict(o, how=verdict) for o in L.get("origins") or []
                                                            if (o.get("set_id"), o.get("proposal_id")) not in have]
            S["refers_to"] = list(dict.fromkeys(list(S.get("refers_to") or []) + list(L.get("refers_to") or [])))
            S["judged"] = list(S.get("judged") or []) + [dict(j) for j in L.get("judged") or []]
    id_map = {loser: _final(loser) for loser in gone}
    orphan_parent = {loser: str(by_id[loser].get("parent_key") or "") for loser in gone}
    out = [r for r in out if r["proposal_id"] not in gone]
    by_id = {r["proposal_id"]: r for r in out}

    def _map(x: str) -> str:   # a link's target after the fold; "" when it left the set
        hops = 0
        while x and x in id_map and hops < 16:
            x, hops = id_map[x], hops + 1
        return x if x in by_id else ""
    lifted = detached = 0
    for r in out:
        pk = str(r.get("parent_key") or "")
        if pk and pk not in by_id:
            t = _map(pk)
            if t == r["proposal_id"]:              # the survivor was the folded row's own child: step up to that row's parent
                t = _map(orphan_parent.get(pk, ""))
                if t == r["proposal_id"]:
                    t = ""
            r["parent_key"] = t
        refs = [_map(t) for t in r.get("refers_to") or []]
        r["refers_to"] = list(dict.fromkeys(t for t in refs if t and t != r["proposal_id"]))
        seen_j: Dict[str, Dict[str, Any]] = {}
        for j in r.get("judged") or []:
            k = _map(str(j.get("key") or ""))
            if k and k != r["proposal_id"] and k not in seen_j:
                seen_j[k] = {"key": k, "verdict": str(j.get("verdict") or "")}
        if seen_j:
            r["judged"] = list(seen_j.values())
        else:
            r.pop("judged", None)
    for a_id, b_id, verdict in pairs:   # a judgement is recorded on BOTH rows of the pairing
        A, B = by_id.get(_map(a_id)), by_id.get(_map(b_id))
        if A is None or B is None or A is B:
            continue
        for X, Y in ((A, B), (B, A)):
            if not any(str(j.get("key")) == Y["proposal_id"] for j in X.get("judged") or []):
                X["judged"] = list(X.get("judged") or []) + [{"key": Y["proposal_id"], "verdict": verdict}]
    for xid in added:   # an extra the judge kept is a point now
        r = by_id.get(xid)
        if r is None:
            continue
        r.pop("extra", None)
        for o in r.get("origins") or []:
            if o.get("how") == "extra":
                o["how"] = "added"
    for r in out:   # nesting after the fold: same header, no cycle, two levels at most
        if r.get("parent_key") and by_id[r["parent_key"]].get("heading_index") != r.get("heading_index"):
            r["parent_key"] = ""
            detached += 1
    changed, rounds = True, 0
    while changed and rounds < 8:
        changed, rounds = False, rounds + 1
        for r in out:
            pk = str(r.get("parent_key") or "")
            gp = str(by_id[pk].get("parent_key") or "") if pk else ""
            if not gp:
                continue
            if gp == r["proposal_id"]:
                r["parent_key"] = ""
                detached += 1
            else:
                r["parent_key"] = gp   # a third level lifts the row beside its parent
                lifted += 1
            changed = True
    out = _accept_order(out)
    answered_x = sum(1 for k in seen if k in xkey)
    answered_q = sum(1 for k in seen if k in qkey)
    stats = {"extras": len(xkey), "answered": answered_x, "pending_extras": len(xkey) - answered_x,
             "folded": len(gone), "added": len(added), "pairs": len(qkey), "pairs_answered": answered_q,
             "recorded": len(pairs), "lifted": lifted, "detached": detached,
             "points": len(points_index(out)), "unjudged_pairs": len(unjudged_pairs(out))}
    return {"proposals": out, "stats": stats,
            "folded": [{"loser": l, "into": list(dict.fromkeys(_final(s) for s, _ in v)), "verdict": v[0][1]} for l, v in gone.items()]}


def open_reference_list(
    proposals: List[Dict[str, Any]],  # One proposal set's rows (normally a merged set)
) -> List[Dict[str, Any]]:  # [{ref, row, proposal_id, role, hint}] in index order — `r01`… are stable for the set
    """Every hinted reference still open in a set, keyed `r01`… in the order of `points_index`
    — the ids a reconciler answers by and `close_open_refs` applies (work item 3a2c94eb (3))."""
    index = points_index(proposals)
    by_id = {p["proposal_id"]: p for p in proposals}
    found = [(e, o) for e in index for o in by_id[e["proposal_id"]].get("open_refs") or [] if o.get("hint")]
    width = max(2, len(str(len(found))))
    return [{"ref": f"r{n:0{width}d}", "row": e["key"], "proposal_id": e["proposal_id"],
             "role": o.get("role") or "refers_to", "hint": o["hint"]} for n, (e, o) in enumerate(found, start=1)]


def render_reconcile_brief(
    proposals: List[Dict[str, Any]],  # One proposal set's rows (normally a merged set)
    *,
    set_id: str = "",                 # The set the brief is about (printed only)
) -> str:  # The reconciler's brief (markdown)
    """The brief of the pass that closes hinted references (work item 3a2c94eb (3)): a
    WHOLE-SOURCE reader works from the Points, never the spine (design 6752db0a (6)), so it
    reads the keyed index and answers each open reference with the key of the EARLIER point
    the hint describes — or null. An unresolved reference stays open; it is never guessed."""
    refs = open_reference_list(proposals)
    lines = [f"# Open references — set `{set_id}`", "",
             "Window drafters could not number a point of ANOTHER window, so where a point leans on or elaborates "
             "something said earlier they left a HINT. Close each hint against the points below.", "",
             "## Output contract", "",
             "ONE JSON object per line, one per reference, in the order listed:", "",
             '    {"ref": "r01", "target": "p017"}     the EARLIER point the hint describes',
             '    {"ref": "r02", "target": null}       nothing below is what the hint describes', "",
             "* The target sits EARLIER than the referring row (a smaller key). Never the row itself.",
             "* `parent` role: the target is the point the row would NEST under — a top-level point or a child, "
             "never a grandchild (two-space indents below show depth).",
             "* Judge by what the points SAY. A hint that fits two points takes the more specific one; a hint that "
             "fits none takes null — a wrong back-link is worse than an open one.",
             "* Rows only — no prose, no code fences.", "",
             f"## References ({len(refs)})", ""]
    lines += [f"- `{r['ref']}` from `{r['row']}` ({r['role']}): {r['hint']}" for r in refs] or ["- (none)"]
    lines += ["", "## Points", ""] + render_points_index(points_index(proposals))
    return "\n".join(lines) + "\n"


def close_open_refs(
    proposals: List[Dict[str, Any]],  # One proposal set's rows (normally a merged set)
    closures: List[Dict[str, Any]],   # The reconciler's rows: {"ref": "r01", "target": "p017" | null}
) -> Dict[str, Any]:  # {"proposals": rows with closed references applied, "stats": {...}}
    """Apply a reconciler's answers to a set's hinted references — mechanically checked, loud
    on the first bad row: a known `ref` answered once, a `target` key of this set's index
    that sits EARLIER than the referring row and is not the row itself; a `parent` closure
    needs a row with no parent yet, a target under the same header, and no third level. A
    closed `refers_to` joins the row's `refers_to`; a closed `parent` becomes its
    `parent_key`. A null target — and any reference never answered — stays open on the row.
    Proposal ids are KEPT: they are the points' future keys."""
    index = points_index(proposals)
    order = {e["key"]: n for n, e in enumerate(index)}
    entry = {e["key"]: e for e in index}
    refs = {r["ref"]: r for r in open_reference_list(proposals)}
    out = [dict(p) for p in proposals]
    by_id = {p["proposal_id"]: p for p in out}
    has_kids = {p["parent_key"] for p in out if p.get("parent_key")}
    seen: set = set()
    closed = left_open = 0
    for n, c in enumerate(closures, start=1):
        ref = str((c or {}).get("ref") or "")
        if ref not in refs:
            raise ValueError(f"closure {n}: unknown reference {ref!r}")
        if ref in seen:
            raise ValueError(f"closure {n}: reference {ref} answered twice")
        seen.add(ref)
        r = refs[ref]
        if c.get("target") in (None, "", "null"):
            left_open += 1
            continue
        target = str(c.get("target"))
        if target not in order:
            raise ValueError(f"closure {n}: target {target!r} is not a point of this set")
        if order[target] >= order[r["row"]]:
            raise ValueError(f"closure {n}: target {target} is not EARLIER than {r['row']}")
        row, tgt = by_id[r["proposal_id"]], by_id[entry[target]["proposal_id"]]
        if r["role"] == "parent":
            if row.get("parent_key"):
                raise ValueError(f"closure {n}: {r['row']} already has a parent")
            if tgt.get("heading_index") != row.get("heading_index"):
                raise ValueError(f"closure {n}: parent {target} is under another header")
            if int(entry[target]["depth"]) + 1 + (1 if row["proposal_id"] in has_kids else 0) > 2:
                raise ValueError(f"closure {n}: nesting {r['row']} under {target} makes a third level")
            row["parent_key"] = tgt["proposal_id"]
        elif tgt["proposal_id"] not in (row.get("refers_to") or []):
            row["refers_to"] = list(row.get("refers_to") or []) + [tgt["proposal_id"]]
        left = [o for o in row.get("open_refs") or [] if not (o.get("hint") == r["hint"] and (o.get("role") or "refers_to") == r["role"])]
        if left:
            row["open_refs"] = left
        else:
            row.pop("open_refs", None)
        closed += 1
    return {"proposals": out, "stats": {"references": len(refs), "closed": closed, "answered_open": left_open,
                                        "unanswered": len(refs) - len(seen)}}


def render_outline_brief(
    proposals: List[Dict[str, Any]],  # One proposal set's rows (normally the merged set)
    pack: Dict[str, Any],             # The whole-unit pack (its source line heads the brief)
    *,
    set_id: str = "",                 # The set the brief is about (printed only)
) -> str:  # The outline pass's brief (markdown)
    """The brief of the whole-source OUTLINE PASS (ruling bc62c727 (A)): after the window merge,
    one reader proposes the SECTIONS — each a title anchored at the first point it covers —
    and the unit's synopsis. It reads the keyed Points, never the spine (design 6752db0a (6));
    slide titles are not the basis (the ruling), the points' own content is."""
    src = pack.get("source") or {}
    index = points_index(proposals)
    tops = sum(1 for e in index if not e["depth"])
    lines = [f"# Outline pass — set `{set_id}`", "",
             f"Source: **{src.get('title') or src.get('source_id')}**", "",
             f"Below are the {len(index)} points drafted from this source ({tops} top-level; children indented), in source "
             "order, each with a key, its kind, the time it starts and who says it. They will render as ONE page of "
             "notes. Propose the page's SECTIONS and its SYNOPSIS.", "",
             "## Output contract", "",
             "ONE JSON object per line: the sections in source order, then the synopsis LAST.", "",
             '    {"section": "<title>", "first": "p017"}',
             '    {"synopsis": "<one or two sentences>"}', "",
             "* A section is a stretch of the source a returning reader would JUMP to: one topic, one demonstration, "
             "one question-and-answer block. `first` is the key of the FIRST point it covers — a TOP-LEVEL point (never "
             "an indented child); the section runs until the next section's `first`. The first section's `first` is "
             "the first top-level point, so every point falls under a heading.",
             "* Cut where the SUBJECT changes, judged by what the points say — never at even intervals, never one "
             "section per speaker turn. A run of questions on one subject is one section; a long topic with a clear "
             "internal turn is two. Sections of very different lengths are fine when the source is like that.",
             "* Title: a short noun phrase in the source's own terms, naming what the section is ABOUT (\"Replacing "
             "raw pointers with mdspan\"), never a generic label (\"Introduction\", \"Part 2\", \"Discussion\"), never a "
             "sentence, no trailing period, no numbering. A Q&A section's title names the subject asked about.",
             "* `synopsis`: one or two sentences, under 30 words, on what the source ARGUES or SHOWS — its claim and its "
             "move, in its own terms; never a list of the section titles. It becomes the page's description.",
             "* Rows only — no prose, no code fences.", "",
             "## Points", ""]
    return "\n".join(lines + render_points_index(index)) + "\n"


def apply_outline(
    proposals: List[Dict[str, Any]],  # One proposal set's rows (normally the merged set)
    answers: List[Dict[str, Any]],    # The outline pass's rows: {"section", "first"}… then {"synopsis"}
    pack: Dict[str, Any],             # The whole-unit pack the set's rows are numbered in
) -> Dict[str, Any]:  # {"proposals": rows + the section and synopsis rows in accept order, "stats": {...}}
    """Turn the outline pass's answers into STRUCTURE rows on the set (ruling bc62c727 (A)) —
    mechanically checked, loud on the first bad row: each `first` a TOP-LEVEL point of this
    set's index, the sections in strictly rising order, the first one at the first top-level
    point, one synopsis. A section becomes a `section` row anchored at the first line of its
    first point (validated by the same contract a drafter's row meets), placed directly
    before that point; the synopsis spans the unit and goes last. A set that already carries
    structure rows is refused — the outline is proposed once, over points."""
    if any(p.get("kind") in STRUCTURE_KINDS for p in proposals):
        raise ValueError("the set already carries section / synopsis rows — outline a set of points")
    pending = sum(1 for p in proposals if p.get("extra"))
    if pending:
        raise ValueError(f"the set still carries {pending} unjudged extra(s) — `notes-judge` first, then outline the judged set")
    index = points_index(proposals)
    order = {e["key"]: n for n, e in enumerate(index)}
    entry = {e["key"]: e for e in index}
    by_id = {p["proposal_id"]: p for p in proposals}
    tops = [e["key"] for e in index if not e["depth"]]
    rows: List[Dict[str, Any]] = []
    firsts: List[str] = []
    synopsis = ""
    for n, a in enumerate(answers, start=1):
        if not isinstance(a, dict):
            raise ValueError(f"outline row {n}: not an object")
        if a.get("synopsis"):
            if synopsis:
                raise ValueError(f"outline row {n}: a second synopsis — one per unit")
            synopsis = str(a["synopsis"]).strip()
            continue
        title, first = str(a.get("section") or "").strip(), str(a.get("first") or "").strip()
        if not title or not first:
            raise ValueError(f"outline row {n}: a section row carries `section` (the title) and `first` (a point key)")
        if synopsis:
            raise ValueError(f"outline row {n}: the synopsis goes LAST")
        if first not in order:
            raise ValueError(f"outline row {n}: `first` {first!r} is not a point of this set")
        if entry[first]["depth"]:
            raise ValueError(f"outline row {n}: `first` {first} is a child point — a section opens on a TOP-LEVEL point")
        if firsts and order[first] <= order[firsts[-1]]:
            raise ValueError(f"outline row {n}: sections run in source order — {first} does not follow {firsts[-1]}")
        firsts.append(first)
        line = int(by_id[entry[first]["proposal_id"]]["from_i"])
        rows.append({"kind": SECTION_KIND, "from_i": line, "to_i": line, "text": title.rstrip(".")})
    if not firsts:
        raise ValueError("the outline proposes no section")
    if tops and firsts[0] != tops[0]:
        raise ValueError(f"the first section opens on {firsts[0]}, not the first top-level point {tops[0]} — points would fall under no heading")
    if not synopsis:
        raise ValueError("the outline carries no synopsis")
    rows.append({"kind": "synopsis", "from_i": 0, "to_i": len(pack.get("segments") or []) - 1, "text": synopsis})
    made = {(p["kind"], p["from_i"], p["text"]): p for p in proposals_from_point_rows(validate_point_rows(rows, pack), pack)}
    before = {entry[k]["proposal_id"]: made[(SECTION_KIND, r["from_i"], r["text"])] for k, r in zip(firsts, rows)}
    out: List[Dict[str, Any]] = []
    for p in proposals:
        if p["proposal_id"] in before:
            out.append(before[p["proposal_id"]])
        out.append(p)
    out.append(made[("synopsis", 0, synopsis)])
    sizes = [(order[firsts[k + 1]] if k + 1 < len(firsts) else len(index)) - order[firsts[k]] for k in range(len(firsts))]
    return {"proposals": out, "stats": {"sections": len(firsts), "points": len(index),
                                        "smallest": min(sizes), "largest": max(sizes), "synopsis_words": len(synopsis.split())}}


def point_from_args(
    owner_id: str,          # The node that OWNS the point (ruling 96be1528 (P)): the (Source, unit)'s PointSet for substance, the deliverable Note for its own (`section`, `research`)
    p: Dict[str, Any],      # The journaled point args (key/kind/text/…)
    actor: str = "agent:session",
) -> PointNode:  # The PointNode the accept op describes
    """The op-args -> PointNode mapping the live accept, the replay AND the re-home share."""
    return PointNode(owner_id=owner_id, key=str(p["key"]), kind=str(p["kind"]), text=str(p["text"]),
                     ordinal=int(p.get("ordinal") or 0), lead=str(p.get("lead") or ""),
                     heading=str(p.get("heading") or ""), heading_index=int(p.get("heading_index") or 0),
                     segment_ids=list(p.get("segment_ids") or []),
                     start_time=p.get("start_time"), end_time=p.get("end_time"),
                     attribution=str(p.get("attribution") or ""), data=dict(p.get("data") or {}),
                     unit=dict(p.get("unit") or {}), parent_key=str(p.get("parent_key") or ""),
                     speaker=str(p.get("speaker") or ""), speakers=list(p.get("speakers") or []),
                     refers_to=list(p.get("refers_to") or []),
                     origins=[dict(o) for o in (p.get("origins") or [])], judged=[dict(j) for j in (p.get("judged") or [])],
                     provenance=str(p.get("provenance") or "source"),
                     citations=[dict(c) for c in (p.get("citations") or [])], expands=str(p.get("expands") or ""),
                     actor=actor)


def deliverable_owns(
    point: Dict[str, Any],  # Point args or node props (`kind`, `provenance`)
) -> bool:  # True when the DELIVERABLE owns the point; False when the PointSet of its (Source, unit) does
    """Ruling 96be1528 (P): a source's points are the source's points — a SUBSTANCE point is
    owned by the PointSet of its (Source, unit) and shared by every deliverable that renders
    the set; a deliverable owns only its OWN points: a `section` (the synthesized outline,
    bc62c727 (A)) and a `research` point (provenance = research, 96be1528 (4)). The synopsis
    is substance — what the source argues — so a sibling deliverable shares it."""
    return (str(point.get("kind") or "") == SECTION_KIND
            or str(point.get("provenance") or "source") == "research")


def point_set_of(
    unit: Dict[str, Any],           # A point's unit snapshot ({graph, source_id, title, …}), merged with an op's `point_set` ({graph, source_id, unit}) when one rides it
    actor: str = "agent:session",   # Who mints the set (the first accept into it)
) -> Optional[PointSetNode]:  # The set the unit addresses; None when the snapshot names no Source
    """The PointSet a unit snapshot addresses (ruling 96be1528 (P)): identity = (sibling graph
    key, Source id, unit key). Every Source the lane reads today is ONE unit — a book chapter
    is its own Source, a lecture is one video — so the unit key is "" unless the snapshot
    carries `unit`; the address is the Source's, never the deliverable's, so a re-draft, a
    re-accept and a second deliverable type converge on the same set. The set keeps the
    unit's ADDRESS for display (source, unit, title, the work structure), not the whole
    snapshot — the substance rides the points."""
    graph, sid = str(unit.get("graph") or ""), str(unit.get("source_id") or "")
    if not graph or not sid:
        return None
    address = {k: unit[k] for k in ("source_id", "unit", "title", "work_structure") if unit.get(k)}
    return PointSetNode(graph=graph, source_id=sid, unit=str(unit.get("unit") or ""),
                        title=str(unit.get("title") or ""), unit_address=address, actor=actor)


async def _edge_rows(
    gx: GraphHandle,
    query: EdgeQuery,  # An UNPROJECTED edge query (the rows come back as whole edges)
) -> List[Dict[str, Any]]:  # [{id, source_id, target_id, relation_type, properties}] — the matching edges as plain dicts
    """Whole edges as dicts, whichever shape the store hands back (edge objects or rows)."""
    res = await graph_task(gx.queue, gx.graph_id, "query_edges", query=query.to_dict())
    raw = getattr(res, "edges", None) or getattr(res, "rows", None) or []
    return [e.to_dict() if hasattr(e, "to_dict") else dict(e) for e in raw]


async def rendered_sets(
    gx: GraphHandle,
    note_id: str,  # The deliverable Note
) -> List[str]:  # The PointSet ids the Note RENDERS from (today at most one)
    """The Note's RENDERS edges (ruling 96be1528 (P)) — where its substance lives. A node that
    is not a Note (a set, a point) renders nothing and gets an empty list."""
    rows = await _edge_rows(gx, EdgeQuery(source_ids=[note_id], relation_type=DevRelations.RENDERS))
    return sorted(str(r["target_id"]) for r in rows if r.get("target_id"))


async def renderers_of(
    gx: GraphHandle,
    set_id: str,  # A PointSet
) -> List[str]:  # The Note ids that RENDER from the set
    """The inverse of `rendered_sets`: every deliverable sharing the set's substance — what a
    retract of the set's points would reach beyond the Note at hand."""
    rows = await _edge_rows(gx, EdgeQuery(target_ids=[set_id], relation_type=DevRelations.RENDERS))
    return sorted(str(r["source_id"]) for r in rows if r.get("source_id"))


async def ensure_point_set(
    gx: GraphHandle,
    note_id: str,                 # The deliverable Note that renders the set
    unit: Dict[str, Any],         # The unit snapshot (merged with the op's `point_set`) the set is addressed by
    *,
    actor: str = "agent:session",
) -> Optional[str]:  # The set's id — minted if absent, the RENDERS edge landed; None when the unit names no Source
    """Mint the (Source, unit)'s PointSet on first use and assert the Note RENDERS it — both
    idempotent (deterministic ids: a present set is left as minted; a present edge is a
    verified no-op), so every accept and the re-home call it without a presence check."""
    ps = point_set_of(unit, actor=actor)
    if ps is None:
        return None
    have = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=ps.id)
    await extend_graph(gx.queue, gx.graph_id, [] if have is not None else [ps.to_graph_node()],
                       [ps.renders_edge(note_id)])
    return ps.id


async def key_owner(
    gx: GraphHandle,
    owner_id: str,  # The owner of the point whose cross-point KEYS are being resolved
) -> str:  # The owner `refers_to` / `expands` keys resolve under
    """Cross-point keys name SUBSTANCE points: for a set-owned point that is its own set; for
    a deliverable-owned point (a research point leaning on the lecture, 96be1528 (4)) it is
    the set the deliverable RENDERS — the `target_owner_id` the schema's edge builders take.
    A Note that renders no set yet (a deliverable before its re-home) resolves under itself."""
    sets = await rendered_sets(gx, owner_id)
    return sets[0] if sets else owner_id


async def load_owned_points(
    gx: GraphHandle,
    owner_id: str,  # A PointSet or a deliverable Note
) -> List[Dict[str, Any]]:  # Point property dicts (+ id), source order
    """The points ONE owner holds (HAS_POINT owner -> point), in source order — the owner's
    loader every Note-level read goes through (ruling 61624f40)."""
    out: List[Dict[str, Any]] = []
    for n in await F.load_label_where(gx, DevNodeKinds.POINT, [PropertyPredicate("owner_id", "eq", owner_id)]):
        d = dict(F.props(n))
        d["id"] = F.nid(n)
        out.append(d)
    out.sort(key=_sort_key)
    return out


async def observe_segments(
    graph_key: str,                 # The sibling graph's config key
    segment_ids: Sequence[str],     # Segment ids to observe
    siblings: Dict[str, str],       # {graph key: db path}
    manifests_dir: str = DEFAULT_MANIFESTS,
    handle: Optional[GraphHandle] = None,   # An already-open sibling handle (batch accepts open it once)
) -> Dict[str, Any]:  # {observations: [obs dicts, segment order]} | {error}
    """Observe each segment in the sibling READ-ONLY (label + properties hash + title) — the
    Reference stand-ins a Point's DERIVED_FROM edges land on. Opens the sibling once."""
    async def _observe(sg: GraphHandle) -> Dict[str, Any]:
        out: List[Dict[str, Any]] = []
        for sid in segment_ids:
            node = await graph_task(sg.queue, sg.graph_id, "get_node", node_id=sid)
            if node is None:
                return {"error": f"no Segment `{sid}` in sibling graph `{graph_key}`"}
            wire = node if isinstance(node, dict) else {
                "id": getattr(node, "id", sid), "label": getattr(node, "label", ""),
                "properties": getattr(node, "properties", {}) or {}}
            out.append(ReferenceNode.observe(graph_key, wire).observation())
        return {"observations": out}
    if handle is not None:
        return await _observe(handle)
    path = siblings.get(graph_key)
    if not path:
        return {"error": f"no sibling graph `{graph_key}` in this graph's config "
                         f"(`sibling_graphs` keys: {sorted(siblings) or 'none'})"}
    try:
        async with open_graph(path, manifests_dir, readonly=True) as sg:
            return await _observe(sg)
    except RuntimeError as e:
        return {"error": f"sibling graph `{graph_key}` unavailable: {e}"}


async def accept_point(
    gx: GraphHandle,
    slug: str,                              # The deliverable Note's slug
    point: Dict[str, Any],                  # Point args: key/kind/text/lead/attribution/data/ordinal/heading/heading_index/segment_ids/start_time/end_time/unit
    *,
    observations: List[Dict[str, Any]],     # One journaled observation per segment id (segment order)
    actor: str = "user:cli",                # Who confirmed (the human — the accept IS the confirmation)
    proposal_set_id: str = "",              # Provenance: the set the point came from
    point_set: Optional[Dict[str, Any]] = None,  # The (Source, unit) the substance belongs to ({graph, source_id, unit}); None = the Note owns it (an op journaled before the re-home)
) -> Dict[str, Any]:  # {point_id, owner_id, note_id, set_id, existing, args, written} | {error}
    """Land ONE accepted point: the Point node, its segment References (from observations —
    live accept observed them a moment ago, replay carries them), `HAS_POINT` from its OWNER,
    `DERIVED_FROM` to each Reference. THE OWNER (ruling 96be1528 (P)): a SUBSTANCE point is
    owned by the PointSet of its (Source, unit) — `point_set` names it, the set is minted on
    first use and the Note asserts RENDERS -> set — while a `section` or a `research` point
    is the deliverable's own. The op CARRIES `point_set`, so an op journaled before the
    re-home replays under the Note exactly as it landed live and the journaled `rehome-points`
    op moves it. Idempotent: deterministic ids make a re-accept a verified no-op; a moved
    observation refreshes the stand-in in place (as `link` does)."""
    note_id = note_node_id(slug)
    note = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=note_id)
    if note is None:
        return {"error": f"no note `{slug}` — birth the deliverable first (new-note --slug {slug} …)",
                "slug": slug, "written": False}
    if len(observations) != len(point.get("segment_ids") or []):
        return {"error": "observations must match segment_ids one-to-one", "slug": slug, "written": False}
    set_id: Optional[str] = None
    if point_set:
        set_id = await ensure_point_set(gx, note_id, {**dict(point.get("unit") or {}), **dict(point_set)}, actor=actor)
        if set_id is None:
            return {"error": "point_set names no Source (it needs `graph` + `source_id`)", "slug": slug, "written": False}
    owner = note_id if deliverable_owns(point) or set_id is None else set_id
    ref_owner = set_id or await key_owner(gx, note_id)   # cross-point keys name substance: the set once one stands
    node = point_from_args(owner, point, actor=str(point.get("actor") or actor))
    if node.parent_key:
        # The parent must already stand under the same owner (accept order = the set's order,
        # children after their parent); a missing parent is a SKIP the caller reports, not a
        # dangling edge. Depth two is the notes TYPES' presentation policy for SUBSTANCE
        # (776c13d3 (b)); a `section` nests as deep as the outline has parents.
        parent = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=node.parent_id)
        if parent is None:
            return {"error": f"parent point `{node.parent_key[:8]}` is not accepted yet (accept it first)",
                    "skippable": True, "slug": slug, "written": False}
        gp_key = str(F.prop(parent, "parent_key") or "")
        if gp_key and node.kind != SECTION_KIND:
            gp = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=point_node_id(owner, gp_key))
            if gp is not None and str(F.prop(gp, "parent_key") or ""):
                return {"error": f"parent point `{node.parent_key[:8]}` is already a grandchild — two levels at most",
                        "skippable": True, "slug": slug, "written": False}
    existing = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=node.id)
    nodes: List[Dict[str, Any]] = [] if existing is not None else [node.to_graph_node()]
    changed = False
    if existing is not None:
        new_props = node.to_graph_node()["properties"]
        changed = any(F.prop(existing, k) != new_props.get(k)
                      for k in ("text", "kind", "lead", "attribution", "heading", "data", "parent_key",
                                "speaker", "speakers", "refers_to", "origins", "judged",
                                "provenance", "citations", "expands"))
        if changed:
            # A re-accept with edited content (the human's edit-on-accept) lands as a property
            # update — same id, the journal carries the new state, last op wins on replay.
            await graph_task(gx.queue, gx.graph_id, "update_node", node_id=node.id, properties=new_props)
    ref_ids: List[str] = []
    for obs in observations:
        ref = ReferenceNode.from_observation(obs)
        ref_ids.append(ref.id)
        wire = ref.to_graph_node()
        have = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=ref.id)
        if have is None:
            nodes.append(wire)
        elif F.prop(have, "observed_hash") != ref.observed_hash:
            await graph_task(gx.queue, gx.graph_id, "update_node", node_id=ref.id, properties=wire["properties"])
    edges = [node.has_point_edge()] + node.derived_from_edges(ref_ids)
    nest = node.elaborates_edge()
    if nest is not None:
        edges.append(nest)
    # Back-links (ruling ba341c72 (2)): an edge to every referred Point that already stands. A
    # target not accepted (yet, or ever) is REPORTED, never a dangling edge — the key stays on
    # the point and a re-accept lands the edge once the target stands. The same for the
    # source point a `research` point expands (96be1528 (4)).
    standing: List[str] = []
    for rk in node.refers_to:
        if await graph_task(gx.queue, gx.graph_id, "get_node", node_id=point_node_id(ref_owner, rk)) is not None:
            standing.append(rk)
    edges += node.refers_to_edges(standing, target_owner_id=ref_owner)
    refers_missing = [rk for rk in node.refers_to if rk not in standing]
    expands_missing = False
    if node.expands:
        if await graph_task(gx.queue, gx.graph_id, "get_node", node_id=point_node_id(ref_owner, node.expands)) is not None:
            edges.append(node.expands_edge(target_owner_id=ref_owner))
        else:
            expands_missing = True
    res = await extend_graph(gx.queue, gx.graph_id, nodes, edges)
    args = {"slug": slug, "point": {**point, "key": node.key}, "observations": list(observations),
            "actor": actor, "proposal_set_id": proposal_set_id,
            **({"point_set": dict(point_set)} if point_set else {})}
    return {"point_id": node.id, "owner_id": owner, "note_id": note_id, "set_id": set_id, "key": node.key,
            "kind": node.kind, "text": node.text, "existing": existing is not None, "changed": changed,
            "references": ref_ids, "nodes_added": res.nodes_added, "edges_added": res.edges_added,
            **({"refers_to_missing": refers_missing} if refers_missing else {}),
            **({"expands_missing": node.expands} if expands_missing else {}),
            "args": args, "written": True}


async def retract_point(
    gx: GraphHandle,
    point_ref: str,                 # The Point id (or unique prefix)
    *,
    actor: str = "user:cli",
) -> Dict[str, Any]:  # {point_id, owner_id, key, deleted, args, written} | {error}
    """Retract a point: delete the node (its edges cascade). The compensating op of accept —
    journaled, replayed in append order after the accept it undoes; a missing point is a
    tolerated no-op so a rebuild converges with the point absent. A set-owned point is the
    SOURCE's (96be1528 (P)): retracting it reaches every deliverable rendering the set."""
    from .projection import ambiguity_error, resolve_node_ref
    r = await resolve_node_ref(gx, point_ref)
    if "candidates" in r:
        return {"error": ambiguity_error(point_ref, r["candidates"]), "written": False}
    node = r.get("node")
    if node is None:
        return {"point_id": point_ref, "deleted": False, "written": True,
                "args": {"point_id": point_ref, "actor": actor}}
    if F.label(node) != DevNodeKinds.POINT:
        return {"error": f"`{point_ref}` is a {F.label(node)}, not a Point", "written": False}
    pid, owner_id, key = F.nid(node), str(F.prop(node, "owner_id") or ""), str(F.prop(node, "key") or "")
    await graph_task(gx.queue, gx.graph_id, "delete_nodes", node_ids=[pid], cascade=True)
    return {"point_id": pid, "owner_id": owner_id, "key": key, "deleted": True, "written": True,
            "args": {"point_id": pid, "slug": "", "key": key, "actor": actor}}


async def retract_note_points(
    gx: GraphHandle,
    slug: str,                      # The deliverable Note's slug
    *,
    actor: str = "user:cli",
) -> Dict[str, Any]:  # {slug, retracted: [retract results], written} | {error}
    """Retract EVERY point a Note renders (the re-drive's clean slate — ruling e1fd4d64 (5)):
    its own AND the substance of the set it renders — the slate is the SOURCE's (96be1528
    (P)), so a set another deliverable also renders is REFUSED, naming the sharers (the case
    that needs a shared re-drive names its own verb). One `retract_point` per point, children
    before parents so no ELABORATES edge ever dangles; the caller journals one `retract-point`
    op per result (replay-identical to singles)."""
    note_id = note_node_id(slug)
    for sid in await rendered_sets(gx, note_id):
        others = [n for n in await renderers_of(gx, sid) if n != note_id]
        if others:
            return {"error": f"the set `{sid[:8]}` is also rendered by {len(others)} other deliverable(s) "
                             f"({', '.join(o[:8] for o in others)}) — its points are the source's, not this Note's to retract",
                    "slug": slug, "retracted": [], "written": False}
    points = await load_points(gx, note_id)
    keys = {str(p.get("key")): p for p in points}

    def _depth(p: Dict[str, Any]) -> int:
        d, cur = 0, p
        while cur is not None and str(cur.get("parent_key") or "") in keys and d < 8:
            cur = keys[str(cur.get("parent_key"))]
            d += 1
        return d
    order = sorted(points, key=lambda p: (-_depth(p), _sort_key(p)))   # deepest first
    out: List[Dict[str, Any]] = []
    for p in order:
        r = await retract_point(gx, p["id"], actor=actor)
        if r.get("error"):
            return {"error": r["error"], "slug": slug, "retracted": out, "written": bool(out)}
        out.append(r)
    return {"slug": slug, "retracted": out, "written": bool(out)}


async def rehome_points(
    gx: GraphHandle,
    slug: str,                      # The deliverable Note's slug
    *,
    actor: str = "user:cli",
) -> Dict[str, Any]:  # {slug, note_id, set_id, moved, kept, args, written} | {error}
    """THE RE-HOME (ruling 96be1528 (P); the migration of a deliverable born before PointSets):
    every SUBSTANCE point the Note owns moves to the PointSet of its (Source, unit) — the set
    minted if absent, RENDERS asserted — with its key, text and every cross-point field
    unchanged: a new node under the new owner (the id changes with the owner, nothing else),
    HAS_POINT from the set, the same DERIVED_FROM References in the same order, ELABORATES
    and the `refers_to` REFERENCES re-derived under the set, then the old node deleted (its
    edges cascade). The deliverable's OWN points (sections, research) stay, their back-links
    and expansions re-targeted to the set. Journaled as `rehome-points` and replayed in
    append order after the accepts it moves, so a journal of pre-re-home accepts converges
    on the same graph as the live one; idempotent — a Note with nothing left to move is a
    no-op that journals nothing."""
    note_id = note_node_id(slug)
    note = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=note_id)
    if note is None:
        return {"error": f"no note `{slug}`", "slug": slug, "written": False}
    own = await load_owned_points(gx, note_id)
    moving = [p for p in own if not deliverable_owns(p)]
    kept = [p for p in own if deliverable_owns(p)]
    args = {"slug": slug, "actor": actor}
    if not moving:
        sets = await rendered_sets(gx, note_id)
        return {"slug": slug, "note_id": note_id, "set_id": sets[0] if sets else None, "moved": 0,
                "kept": len(kept), "args": args, "written": False}
    units = {(str((p.get("unit") or {}).get("graph") or ""), str((p.get("unit") or {}).get("source_id") or ""))
             for p in moving}
    if len(units) != 1 or not all(next(iter(units))):
        return {"error": f"the Note's points name {len(units)} source unit(s) — a Note renders ONE set per "
                         f"(Source, unit), and every point must carry its unit's graph + source_id",
                "slug": slug, "written": False}
    set_id = await ensure_point_set(gx, note_id, dict(moving[0].get("unit") or {}), actor=actor)
    moved_keys = {str(p.get("key")) for p in moving}
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    for p in moving:
        pn = point_from_args(set_id, {k: v for k, v in p.items() if k != "id"}, actor=str(p.get("actor") or actor))
        nodes.append(pn.to_graph_node())
        edges.append(pn.has_point_edge())
        # the same References in the same order — what the accept observed, never re-observed
        rows = await _edge_rows(gx, EdgeQuery(source_ids=[p["id"]], relation_type=DevRelations.DERIVED_FROM))
        rows.sort(key=lambda e: int((e.get("properties") or {}).get("order") or 0))
        edges += pn.derived_from_edges([str(e["target_id"]) for e in rows if e.get("target_id")])
        nest = pn.elaborates_edge()
        if nest is not None and pn.parent_key in moved_keys:
            edges.append(nest)
        edges += pn.refers_to_edges([k for k in pn.refers_to if k in moved_keys])
    for p in kept:
        # a deliverable-owned point leaning on the moved substance re-targets the set
        pn = point_from_args(note_id, {k: v for k, v in p.items() if k != "id"}, actor=str(p.get("actor") or actor))
        if not (pn.refers_to or pn.expands):
            continue
        rows = await _edge_rows(gx, EdgeQuery(source_ids=[p["id"]], relation_type=DevRelations.REFERENCES))
        stale = [str(e["id"]) for e in rows if e.get("id")]
        if stale:
            await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=stale)
        edges += pn.refers_to_edges([k for k in pn.refers_to if k in moved_keys], target_owner_id=set_id)
        if pn.expands in moved_keys:
            edges.append(pn.expands_edge(target_owner_id=set_id))
    res = await extend_graph(gx.queue, gx.graph_id, nodes, edges)
    await graph_task(gx.queue, gx.graph_id, "delete_nodes", node_ids=[p["id"] for p in moving], cascade=True)
    return {"slug": slug, "note_id": note_id, "set_id": set_id, "moved": len(moving), "kept": len(kept),
            "nodes_added": res.nodes_added, "edges_added": res.edges_added, "args": args, "written": True}


async def edit_point(
    gx: GraphHandle,
    point_ref: str,                     # The Point id (or unique prefix)
    *,
    text: Optional[str] = None,         # New statement text (None = keep)
    lead: Optional[str] = None,         # New lead term ("" clears; None = keep)
    parent: Optional[str] = None,       # New parent: a Point key, id or prefix under the same OWNER; "" = top level; None = keep
    heading: Optional[str] = None,      # Re-derived section heading (the rehead pass; None = keep)
    heading_index: Optional[int] = None,  # Its order within the unit (None = keep)
    refers_to: Optional[List[str]] = None,   # New back-link keys (the judge's fold; None = keep) — REFERENCES edges re-derived to the standing targets
    origins: Optional[List[Dict[str, Any]]] = None,  # New provenance list (the judge's fold; None = keep)
    judged: Optional[List[Dict[str, Any]]] = None,   # New overlap judgements (the judge; None = keep)
    actor: str = "user:cli",
) -> Dict[str, Any]:  # {point_id, owner_id, key, changed: {field: [old, new]}, args, written} | {error}
    """Edit an accepted point IN PLACE — the per-point repair the ch. 2 staging read demanded
    (ruling 5625b74e; the lane gap named in b542896b (b) and 5fdeb80c): text, lead, parent —
    and, for the overlap judge over an accepted draft (ruling 1798a796), the back-links,
    origins and judgements. Identity is (owner, key), so the node, its `pt-` anchor and its
    References all stand; a parent change rewires the ELABORATES edge under the accept-time
    rules (the same owner, an accepted parent, depth two at most for substance — a point
    with children cannot become a grandchild — never a descendant of the point itself; a
    `section` nests as deep as the outline has parents, 776c13d3); a back-link change
    re-derives the REFERENCES edges to the targets that stand under the key owner (a missing
    target keeps its key, as at accept). The lead and arrow contracts of ingest hold on the
    edited text. Journaled as `edit-point` with the FIELD SET applied; replay re-applies it
    after the accept it edits (a missing point is a tolerated no-op — the accept may have
    been retracted later in the journal). Unchanged fields land nothing."""
    from .projection import ambiguity_error, resolve_node_ref
    r = await resolve_node_ref(gx, point_ref)
    if "candidates" in r:
        return {"error": ambiguity_error(point_ref, r["candidates"]), "written": False}
    node = r.get("node")
    if node is None:
        return {"point_id": point_ref, "missing": True, "changed": {}, "written": False,
                "args": {"point_id": point_ref, "fields": {}, "actor": actor}}
    if F.label(node) != DevNodeKinds.POINT:
        return {"error": f"`{point_ref}` is a {F.label(node)}, not a Point", "written": False}
    pid, owner_id, key = F.nid(node), str(F.prop(node, "owner_id") or ""), str(F.prop(node, "key") or "")
    cur = {k: F.prop(node, k) for k in ("text", "kind", "lead", "attribution", "heading", "heading_index",
                                        "segment_ids", "start_time", "end_time", "data", "unit",
                                        "parent_key", "ordinal", "actor", "speaker", "speakers", "refers_to",
                                        "origins", "judged", "provenance", "citations", "expands")}
    capped = str(cur.get("kind")) != SECTION_KIND   # the depth cap is substance policy, never a section's
    fields: Dict[str, Any] = {}
    if text is not None:
        if not text.strip():
            return {"error": "text may not be empty (retract the point instead)", "written": False}
        if text.strip() != str(cur.get("text") or ""):
            fields["text"] = text.strip()
    if lead is not None and lead.strip() != str(cur.get("lead") or ""):
        fields["lead"] = lead.strip()
    if heading is not None and heading.strip() != str(cur.get("heading") or ""):
        fields["heading"] = heading.strip()
    if heading_index is not None and int(heading_index) != int(cur.get("heading_index") or 0):
        fields["heading_index"] = int(heading_index)
    if refers_to is not None and [str(k) for k in refers_to] != list(cur.get("refers_to") or []):
        fields["refers_to"] = [str(k) for k in refers_to]
    if origins is not None and [dict(o) for o in origins] != list(cur.get("origins") or []):
        fields["origins"] = [dict(o) for o in origins]
    if judged is not None and [dict(j) for j in judged] != list(cur.get("judged") or []):
        fields["judged"] = [dict(j) for j in judged]
    new_text = str(fields.get("text", cur.get("text") or ""))
    new_lead = str(fields.get("lead", cur.get("lead") or ""))
    if "text" in fields or "lead" in fields:
        # The ingest contracts hold on what this edit TOUCHES; a heading-only edit (the rehead
        # pass) leaves a pre-ruling row's text as it stands — its arrows are that row's debt.
        if new_lead and str(cur.get("kind")) != "definition" and new_lead.lower() not in new_text.lower():
            return {"error": f"lead {new_lead!r} does not appear in the text (a lead is bolded IN PLACE — "
                             f"e1fd4d64 (I); only `definition` may carry a lead the text lacks)", "written": False}
        if new_text.count("→") > 1:
            return {"error": f"{new_text.count('→')} arrows — at most ONE `→` per point (ruling 5625b74e); "
                             f"say the rest in words", "written": False}
    old_parent_key = str(cur.get("parent_key") or "")
    if parent is not None:
        new_parent_key = ""
        if parent != "":
            # A key first (what the journal carries), then an id / prefix (what a reader holds).
            pnode = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=point_node_id(owner_id, parent))
            if pnode is None:
                pr = await resolve_node_ref(gx, parent)
                if "candidates" in pr:
                    return {"error": ambiguity_error(parent, pr["candidates"]), "written": False}
                pnode = pr.get("node")
            if pnode is None or F.label(pnode) != DevNodeKinds.POINT:
                return {"error": f"parent `{parent}` is not an accepted Point", "written": False}
            if str(F.prop(pnode, "owner_id") or "") != owner_id:
                return {"error": f"parent `{parent[:8]}` belongs to another owner (a point nests under its own "
                                 f"set's points; a section under its own deliverable's sections)", "written": False}
            if F.nid(pnode) == pid:
                return {"error": "a point cannot elaborate itself", "written": False}
            new_parent_key = str(F.prop(pnode, "key") or "")
            # Depth: the parent's ancestry decides the point's depth; its own children ride along.
            gp_key = str(F.prop(pnode, "parent_key") or "")
            if gp_key:
                gp = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=point_node_id(owner_id, gp_key))
                if capped and gp is not None and str(F.prop(gp, "parent_key") or ""):
                    return {"error": f"parent `{new_parent_key[:8]}` is already a grandchild — two levels at most",
                            "written": False}
                if gp_key == key:
                    return {"error": f"parent `{new_parent_key[:8]}` is this point's own child (cycle)", "written": False}
            siblings = await load_owned_points(gx, owner_id)
            children = [p for p in siblings if str(p.get("parent_key") or "") == key]
            if capped and gp_key and children:
                return {"error": f"`{key[:8]}` has {len(children)} child point(s) — under `{new_parent_key[:8]}` they would "
                                 f"sit at depth three; re-parent them first", "written": False}
            if any(str(p.get("parent_key") or "") == key and str(p.get("key")) == new_parent_key for p in siblings):
                return {"error": f"parent `{new_parent_key[:8]}` is this point's own child (cycle)", "written": False}
        if new_parent_key != old_parent_key:
            fields["parent_key"] = new_parent_key
    if not fields:
        return {"point_id": pid, "owner_id": owner_id, "key": key, "changed": {}, "written": False,
                "args": {"point_id": pid, "key": key, "fields": {}, "actor": actor}}
    merged = {**{k: v for k, v in cur.items() if v is not None}, **fields, "key": key}
    pn = point_from_args(owner_id, merged, actor=str(cur.get("actor") or actor))
    props = pn.to_graph_node()["properties"]
    props.update(fields)   # update_node MERGES: a cleared lead / parent_key / list lands explicitly, never by absence
    await graph_task(gx.queue, gx.graph_id, "update_node", node_id=pid, properties=props)
    if "parent_key" in fields:
        q = EdgeQuery(source_ids=[pid], relation_type=DevRelations.ELABORATES, project=["id"])
        res = await graph_task(gx.queue, gx.graph_id, "query_edges", query=q.to_dict())
        old_edges = [row["id"] for row in (res.rows or []) if row.get("id")]
        if old_edges:
            await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=old_edges)
        nest = pn.elaborates_edge()
        if nest is not None:
            await extend_graph(gx.queue, gx.graph_id, [], [nest])
    if "refers_to" in fields:
        ref_owner = await key_owner(gx, owner_id)
        rows = await _edge_rows(gx, EdgeQuery(source_ids=[pid], relation_type=DevRelations.REFERENCES))
        old_edges = [str(e["id"]) for e in rows if e.get("id") and (e.get("properties") or {}).get("role") == "refers_to"]
        if old_edges:
            await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=old_edges)
        standing = [rk for rk in pn.refers_to
                    if await graph_task(gx.queue, gx.graph_id, "get_node", node_id=point_node_id(ref_owner, rk)) is not None]
        if standing:
            await extend_graph(gx.queue, gx.graph_id, [], pn.refers_to_edges(standing, target_owner_id=ref_owner))
    changed = {k: [cur.get(k) if cur.get(k) is not None else "", v] for k, v in fields.items()}
    return {"point_id": pid, "owner_id": owner_id, "key": key, "changed": changed, "text": pn.text, "written": True,
            "args": {"point_id": pid, "key": key, "fields": dict(fields), "actor": actor}}


async def rehead_points(
    gx: GraphHandle,
    slug: str,                      # The deliverable Note's slug
    pack: Dict[str, Any],           # A FRESH notes pack over the same source unit (its headers are the new structure)
    *,
    actor: str = "user:cli",
) -> Dict[str, Any]:  # {slug, points, changed: [{point_id, key, old, new}], unmapped, results, written}
    """Re-derive every Point's captured heading / heading_index from its segment run against a
    fresh pack — the per-point re-derivation a header RECLASSIFICATION upstream demands (ruling
    b398d73f; the b542896b (b) gap): a title that stops being a header (a cross-reference, a
    transition) leaves every Point under it carrying a stale heading, and the renderer groups
    by that heading. The rule is the pack's own (`heading_index` = the count of headers at or
    before the run's first line; `heading` = the last of them). Each changed Point lands as an
    `edit_point` field set — the caller journals one edit-point per result — so every anchor
    stands. A Point whose segments the pack no longer numbers (excluded upstream since) is
    reported, never touched; the synopsis spans the unit and is skipped."""
    note_id = note_node_id(slug)
    points = await load_points(gx, note_id)
    by_seg = {str(r.get("id")): int(r.get("i") or 0) for r in (pack.get("segments") or [])}
    headers = list(pack.get("headers") or [])
    changed: List[Dict[str, Any]] = []
    unmapped: List[Dict[str, Any]] = []
    results: List[Dict[str, Any]] = []
    for p in points:
        if str(p.get("kind")) == "synopsis":
            continue
        idx = [by_seg[s] for s in (p.get("segment_ids") or []) if s in by_seg]
        if not idx:
            unmapped.append({"point_id": p["id"], "key": p.get("key"), "text": p.get("text")})
            continue
        first = min(idx)
        before = [h for h in headers if int(h.get("i_before") or 0) <= first]
        heading = str(before[-1].get("text") or "") if before else ""
        h_index = len(before)
        if heading == str(p.get("heading") or "") and h_index == int(p.get("heading_index") or 0):
            continue
        r = await edit_point(gx, p["id"], heading=heading, heading_index=h_index, actor=actor)
        if r.get("error"):
            return {"error": r["error"], "slug": slug, "points": len(points), "changed": changed,
                    "unmapped": unmapped, "results": results, "written": bool(results)}
        results.append(r)
        changed.append({"point_id": p["id"], "key": p.get("key"),
                        "old": str(p.get("heading") or ""), "new": heading,
                        "old_index": int(p.get("heading_index") or 0), "new_index": h_index})
    return {"slug": slug, "points": len(points), "changed": changed, "unmapped": unmapped,
            "results": results, "written": bool(results)}


async def judge_points(
    gx: GraphHandle,
    slug: str,                          # The deliverable Note's slug
    answers: List[Dict[str, Any]],      # The overlap judge's rows over the draft's pairs brief ({"pair", "verdict", "keep"})
    *,
    actor: str = "user:cli",
) -> Dict[str, Any]:  # {slug, stats, folded, edited: [edit results], retracted: [retract results], written} | {error}
    """Apply an overlap judge's answers to an ACCEPTED draft (ruling 1798a796; work item
    1561551e (4)): the same mechanical fold as a set (`apply_judgements` over the Points in
    proposal shape), landed as the lane's journaled ops — every survivor whose parent,
    back-links, origins or judgements changed is an `edit_point` (the caller journals one
    `edit-point` per result), every folded point a `retract_point` after the edits that moved
    its children (the caller journals `retract-point`). A folded point is never lost: it is an
    origin of its survivor. Loud on the first bad answer, before any write."""
    note_id = note_node_id(slug)
    points = await load_points(gx, note_id)
    rows = points_as_proposals(points)
    before = {r["proposal_id"]: r for r in rows}
    res = apply_judgements(rows, answers)
    after = {r["proposal_id"]: r for r in res["proposals"]}
    edited: List[Dict[str, Any]] = []
    retracted: List[Dict[str, Any]] = []
    for key, r in after.items():
        old = before[key]
        fields: Dict[str, Any] = {}
        if str(r.get("parent_key") or "") != str(old.get("parent_key") or ""):
            fields["parent"] = str(r.get("parent_key") or "")
        if list(r.get("refers_to") or []) != list(old.get("refers_to") or []):
            fields["refers_to"] = list(r.get("refers_to") or [])
        if list(r.get("origins") or []) != list(old.get("origins") or []):
            fields["origins"] = list(r.get("origins") or [])
        if list(r.get("judged") or []) != list(old.get("judged") or []):
            fields["judged"] = list(r.get("judged") or [])
        if not fields:
            continue
        e = await edit_point(gx, old["id"], actor=actor, **fields)
        if e.get("error"):
            return {"error": e["error"], "slug": slug, "edited": edited, "retracted": retracted, "written": bool(edited)}
        edited.append(e)
    for key in before:
        if key in after:
            continue
        d = await retract_point(gx, before[key]["id"], actor=actor)
        if d.get("error"):
            return {"error": d["error"], "slug": slug, "edited": edited, "retracted": retracted, "written": bool(edited or retracted)}
        retracted.append(d)
    return {"slug": slug, "stats": res["stats"], "folded": res["folded"], "edited": edited, "retracted": retracted,
            "written": bool(edited or retracted)}


# --------------------------------------------------------------------------------------
# Reads: the review verbs
# --------------------------------------------------------------------------------------


def _sort_key(p: Dict[str, Any]) -> Tuple[float, int, int, str]:
    """Source order: start time, then pack ordinal, then key. A synthesized `section` is
    anchored at the first line of the first point it covers, so it sorts BEFORE every point
    that starts where it does (ruling bc62c727 (A1))."""
    st = p.get("start_time")
    return (float(st) if st is not None else float("inf"), 0 if str(p.get("kind")) == "section" else 1,
            int(p.get("ordinal") or 0), str(p.get("key") or ""))


async def load_points(
    gx: GraphHandle,
    note_id: str,  # The deliverable Note
) -> List[Dict[str, Any]]:  # Point property dicts (+ id), source order
    """What a Note RENDERS (ruling 96be1528 (P)): its OWN points (sections, research) plus the
    substance points of every PointSet it RENDERS from, one list in source order (start time,
    then pack ordinal, then key). Which of the set's points a type SHOWS is the render's
    call (the `point_role` facts + the type's role map), never the loader's."""
    out = await load_owned_points(gx, note_id)
    for sid in await rendered_sets(gx, note_id):
        out += await load_owned_points(gx, sid)
    out.sort(key=_sort_key)
    return out


def overlapping_points(
    points: List[Dict[str, Any]],  # load_points output
) -> List[Dict[str, Any]]:  # [{a, b, shared: [segment ids], same_kind, nested, cross_origin, judged, flagged}] — pairs whose segment runs intersect
    """Pure: the duplication candidates — two points deriving from a shared segment — and
    STANDING DETECTION over them (ruling 1798a796 (1)): a pair is FLAGGED when it is
    cross-origin (no drafter cell contributed to both — a point with no origins counts as
    unknown), not a parent and its child, and neither point's `judged` names the other. A
    draft with a flagged pair is unclean, whatever produced it. A `quotation` beside a `claim`
    over the same run is expected (`same_kind` False); a recorded verdict rides on `judged`."""
    out: List[Dict[str, Any]] = []
    # the unit-spanning synopsis overlaps everything by design, and a section shares its anchor line with the point it opens
    points = [p for p in points if str(p.get("kind")) not in STRUCTURE_KINDS]

    def _cells(p: Dict[str, Any]) -> set:
        # authoring cells only (shown / matched / added) — a fold-in origin (`same` / `contains`) is judgement
        # provenance copied onto every survivor, not a drafter writing both points (see unjudged_pairs)
        return {str(o.get("cell") or "") for o in (p.get("origins") or [])
                if o.get("cell") and str(o.get("how") or "shown") not in ("same", "contains")}

    def _verdict(p: Dict[str, Any], other: str) -> str:
        return next((str(j.get("verdict") or "") for j in (p.get("judged") or []) if str(j.get("key")) == other), "")
    for i, a in enumerate(points):
        sa = set(a.get("segment_ids") or [])
        for b in points[i + 1:]:
            shared = sorted(sa & set(b.get("segment_ids") or []))
            if shared:
                ka, kb = str(a.get("key") or ""), str(b.get("key") or "")
                nested = str(a.get("parent_key") or "") == kb or str(b.get("parent_key") or "") == ka
                ca, cb = _cells(a), _cells(b)
                cross = not (ca and cb and ca & cb)
                verdict = _verdict(a, kb) or _verdict(b, ka)
                out.append({"a": {"id": a["id"], "key": ka, "kind": a.get("kind"), "text": a.get("text")},
                            "b": {"id": b["id"], "key": kb, "kind": b.get("kind"), "text": b.get("text")},
                            "shared": shared,
                            "same_kind": a.get("kind") == b.get("kind"),
                            "nested": nested, "cross_origin": cross, "judged": verdict,
                            "flagged": bool(cross and not nested and not verdict)})
    return out


def coverage_gaps(
    pack_rows: List[Dict[str, Any]],   # The unit's content lines (build_notes_pack `segments`)
    points: List[Dict[str, Any]],      # load_points output
) -> List[Dict[str, Any]]:  # [{from_i, to_i, start, end, text, lines}] — runs of content lines no point derives from
    """Pure: the unreferenced-lines query — every content line the type includes that no
    accepted point derives from, grouped into consecutive runs (the 2025 audit prompt,
    as a graph read)."""
    covered: set = set()
    for p in points:
        if str(p.get("kind")) in STRUCTURE_KINDS:
            continue   # the synopsis spans the unit by design and a section only anchors a title: neither is coverage
        covered.update(p.get("segment_ids") or [])
    gaps: List[Dict[str, Any]] = []
    run: List[Dict[str, Any]] = []

    def _flush() -> None:
        if run:
            gaps.append({"from_i": run[0]["i"], "to_i": run[-1]["i"], "start": run[0]["start"],
                         "end": run[-1]["end"], "lines": len(run),
                         "text": " ".join(r["text"].strip() for r in run)})
    for r in pack_rows:
        if r["id"] in covered:
            _flush()
            run = []
        else:
            run.append(r)
    _flush()
    return gaps


async def point_coverage(
    gx: GraphHandle,
    slug: str,                              # The deliverable Note's slug
    *,
    siblings: Dict[str, str],               # {graph key: db path}
    graph_key: Optional[str] = None,        # Sibling key (default: the points' unit / the sole key)
    skeleton: Optional[str] = None,         # Spine selector (default: the points' unit skeleton)
    manifests_dir: str = DEFAULT_MANIFESTS,
) -> Dict[str, Any]:  # {slug, points, covered_lines, total_lines, gaps: [...]} | {error}
    """The COVERAGE review: re-read the unit per the Note's type policy and list the content
    runs no accepted point derives from."""
    note_id = note_node_id(slug)
    points = await load_points(gx, note_id)
    if not points:
        return {"error": f"note `{slug}` has no points yet", "slug": slug}
    unit = dict(points[0].get("unit") or {})
    key = graph_key or unit.get("graph") or (next(iter(siblings)) if len(siblings) == 1 else None)
    if not key or key not in siblings:
        return {"error": f"which sibling? pass --sibling (keys: {sorted(siblings) or 'none'})", "slug": slug}
    tkey = await note_deliverable_type(gx, note_id) or PURE_NOTES_KEY
    tprops = await load_deliverable_type(gx, tkey)
    if tprops is None:
        return {"error": f"deliverable type `{tkey}` is not minted — run `notes-type {tkey}` first", "slug": slug}
    try:
        async with open_graph(siblings[key], manifests_dir, readonly=True) as sg:
            read = await read_source_unit(sg, str(unit.get("source_id") or ""),
                                          skeleton=skeleton or unit.get("skeleton_hash"))
    except RuntimeError as e:
        return {"error": f"sibling graph `{key}` unavailable: {e}", "slug": slug}
    if read.get("error"):
        return {"error": read["error"], "slug": slug}
    try:
        pack = build_notes_pack(read, tprops)
    except ValueError as e:   # a type policy naming an unknown role (ruling e1e096fa)
        return {"error": f"deliverable type `{tkey}`: {e}", "slug": slug}
    gaps = coverage_gaps(pack["segments"], points)
    body_points = [p for p in points if str(p.get("kind")) != "synopsis"]
    covered = sum(1 for r in pack["segments"] if any(r["id"] in (p.get("segment_ids") or []) for p in body_points))
    return {"slug": slug, "note_id": note_id, "type": tkey, "points": len(points),
            "covered_lines": covered, "total_lines": len(pack["segments"]), "gaps": gaps}


async def point_check(
    gx: GraphHandle,
    point_ref: str,                         # The Point id (or unique prefix)
    *,
    siblings: Dict[str, str],
    manifests_dir: str = DEFAULT_MANIFESTS,
) -> Dict[str, Any]:  # {point, segments: [{id, text, start, end, live_hash, observed_hash, moved}]} | {error}
    """The CHECK review: one point beside its segments' LIVE text from the sibling — the
    fidelity spot-check — with each Reference's observed-vs-live verdict."""
    from cjm_dev_graph_schema.nodes import foreign_content_hash
    from .projection import ambiguity_error, resolve_node_ref
    r = await resolve_node_ref(gx, point_ref)
    if "candidates" in r:
        return {"error": ambiguity_error(point_ref, r["candidates"])}
    node = r.get("node")
    if node is None or F.label(node) != DevNodeKinds.POINT:
        return {"error": f"no Point `{point_ref}`"}
    p = dict(F.props(node))
    p["id"] = F.nid(node)
    unit = dict(p.get("unit") or {})
    key = unit.get("graph") or (next(iter(siblings)) if len(siblings) == 1 else None)
    if not key or key not in siblings:
        return {"error": f"which sibling? (keys: {sorted(siblings) or 'none'})", "point": p}
    refs = {str(F.prop(n, "foreign_id")): n for n in await F.load_label_where(
        gx, DevNodeKinds.REFERENCE, [PropertyPredicate("graph", "eq", key)])} if p.get("segment_ids") else {}
    rows: List[Dict[str, Any]] = []
    try:
        async with open_graph(siblings[key], manifests_dir, readonly=True) as sg:
            for sid in p.get("segment_ids") or []:
                seg = await graph_task(sg.queue, sg.graph_id, "get_node", node_id=sid)
                if seg is None:
                    rows.append({"id": sid, "gone": True})
                    continue
                wire = seg if isinstance(seg, dict) else {"id": sid, "label": getattr(seg, "label", ""),
                                                          "properties": getattr(seg, "properties", {}) or {}}
                live = foreign_content_hash(wire)
                obs = str(F.prop(refs.get(sid), "observed_hash") or "") if refs.get(sid) is not None else ""
                sp = F.props(seg)
                rows.append({"id": sid, "text": str(sp.get("text") or ""), "start": sp.get("start_time"),
                             "end": sp.get("end_time"), "live_hash": live, "observed_hash": obs,
                             "moved": bool(obs) and obs != live})
    except RuntimeError as e:
        return {"error": f"sibling graph `{key}` unavailable: {e}", "point": p}
    return {"point": p, "segments": rows}


# --------------------------------------------------------------------------------------
# Render: the body as a deterministic function of the Points
# --------------------------------------------------------------------------------------


def _anchor(p: Dict[str, Any]) -> str:  # the stable per-point anchor id
    return "pt-" + str(p.get("key") or "")[:8]


ANCHOR_GLYPH = "§"   # the visible permalink mark a reader copies (ruling e1fd4d64 (F)); styled by .pt-anchor
BODY_MARKER = "<!-- pure-notes: rendered body follows; everything above is authored -->"   # invisible in HTML + Typora


def _anchor_link(p: Dict[str, Any]) -> str:  # "[§](#pt-x){#pt-x .pt-anchor}" — the id AND a visible permalink
    a = _anchor(p)
    return f"[{ANCHOR_GLYPH}](#{a}){{#{a} .pt-anchor}}"


def _time_link(url: str, start: float) -> str:  # a public URL addressed at `start` seconds
    """YouTube takes `t=<s>s` (watch: as a query param; youtu.be: `?t=`); anything else gets
    the media-fragment `#t=<s>` the audio/video elements honour."""
    s = int(max(0.0, float(start)))
    if "youtube.com/" in url:
        return f"{url}{'&' if '?' in url else '?'}t={s}s"
    if "youtu.be/" in url:
        return f"{url}{'&' if '?' in url else '?'}t={s}"
    return f"{url}#t={s}"


def _span(p: Dict[str, Any], timestamps: str = "addressable", ctx: Optional[Dict[str, Any]] = None) -> str:  # " (mm:ss–mm:ss)" | " [(mm:ss–mm:ss)](url){.src-ref}" | " [mm:ss](url){.src-ref}" | ""
    """The source span: `always` = plain; `addressable` = ONLY when the unit carries a public
    time-addressable URL, rendered as a LINK into it (ruling e1fd4d64 (D) — an audiobook span
    resolves against nobody else's file split); `never` = none. A linked span carries the
    `src-ref` class (read 9d301b5a: the site styles provenance quietly and can hide it — the
    default link blue drew the eye off the content). Style `span: start` (ruling de9c4cda for
    the standalone lecture page) prints the start time alone: the link leaves the site, so
    the range served only the walk; the range stays on the point for a video-centred
    surface. Review verbs show spans regardless — this governs the rendered post."""
    if p.get("start_time") is None or timestamps == "never":
        return ""
    start_only = bool(ctx) and str((ctx.get("style") or {}).get("span")) == "start"
    text = _fmt_ts(p.get("start_time")) if start_only else f"({_fmt_ts(p.get('start_time'))}–{_fmt_ts(p.get('end_time'))})"
    if timestamps == "always":
        return " " + text
    url = str((p.get("unit") or {}).get("public_url") or "").strip()
    if not url:
        return ""
    return f" [{text}]({_time_link(url, float(p.get('start_time') or 0.0))}){{.src-ref}}"


def _bold_in_place(text: str, lead: str) -> str:  # bold the FIRST occurrence of `lead` inside `text` (case-insensitive)
    i = text.lower().find(lead.lower())
    if i < 0:
        return ""
    return text[:i] + "**" + text[i:i + len(lead)] + "**" + text[i + len(lead):]


def _lead_text(p: Dict[str, Any]) -> str:  # definition: "**Term** — text"; else the lead bolded IN PLACE
    """Ruling e1fd4d64 (I): a prefixed lead that restates a term already in the text adds
    nothing — bold the term where it occurs. `definition` keeps the glossary shape (the term,
    then what it means) when its text is the gloss alone — a definition whose text already
    carries the term is bolded in place like every other kind, never prefixed on top of it
    (finding 06fa8cb5: the ch. 2 proposer's self-check moved the term into the text and the
    page read "**Term** — Term — gloss"). A lead the text lacks (legacy rows) falls back to
    the prefix."""
    lead, text = str(p.get("lead") or "").strip(), str(p.get("text") or "").strip()
    if not lead:
        return text
    return _bold_in_place(text, lead) or f"**{lead}** — {text}"


OUTLINE_QUOTE_WORDS = 12   # a quotation's outline line = attribution + its first words (the scan view abbreviates)


def _outline_text(p: Dict[str, Any]) -> str:  # the OUTLINE line for a point (scan view)
    """The scan line: a quotation shows who is quoted + its opening words (the full verbatim text
    lives in the expanded rendering); every other kind shows its lead + text."""
    if str(p.get("kind")) != "quotation":
        return _lead_text(p)
    words = str(p.get("text") or "").split()
    head = " ".join(words[:OUTLINE_QUOTE_WORDS]) + ("…" if len(words) > OUTLINE_QUOTE_WORDS else "")
    who = str(p.get("attribution") or "").strip()
    return (f"**{who}**: “{head}”" if who else f"“{head}”")


def _heading_text(h: str) -> str:  # a read-aloud header as a heading (no trailing period)
    return h.strip().rstrip(".").strip()


def _norm(s: str) -> str:  # heading comparison form: lowercase, no trailing period, collapsed spaces
    return " ".join(str(s or "").lower().replace("’", "'").split()).rstrip(".").strip()


def unit_title_header(heading: str, unit: Dict[str, Any]) -> bool:  # does this header restate the unit's own identity?
    """Ruling e1fd4d64 (C): the first read-aloud header of a chapter file is the chapter's own
    title ('Part 1. School. Chapter 1. …'), one level UP the structure map — it is the unit's
    TITLE, not a section. True when the header contains the structure map's unit title (or
    the Source title's tail after its file prefix)."""
    h = _norm(heading)
    if not h:
        return False
    ws = dict((unit or {}).get("work_structure") or {})
    title = _norm(ws.get("title") or "")
    if title and title in h:
        return True
    src_title = _norm((unit or {}).get("title") or "")
    # "04 - 1. Seven Dangerous Lessons…" -> "seven dangerous lessons…"
    tail = re.sub(r"^[\d\s\-–—.:]+", "", src_title).strip()
    return bool(tail) and tail in h


def _render_table(data: Dict[str, Any], indent: str = "") -> List[str]:
    cols = [str(c) for c in (data.get("columns") or [])]
    rows = data.get("rows") or []
    if not cols:
        return []
    out = [indent + "| " + " | ".join(cols) + " |", indent + "|" + "|".join(["---"] * len(cols)) + "|"]
    for r in rows:
        cells = [str(c) for c in (r if isinstance(r, (list, tuple)) else [r])]
        cells += [""] * (len(cols) - len(cells))
        out.append(indent + "| " + " | ".join(cells[:len(cols)]) + " |")
    return out


def _sequence_items(p: Dict[str, Any], indent: str) -> List[str]:  # the ordered items of a `sequence`
    out: List[str] = []
    for n, it in enumerate((p.get("data") or {}).get("items") or [], start=1):
        when = str((it or {}).get("when") or "").strip() if isinstance(it, dict) else ""
        what = str((it or {}).get("what") or it or "").strip() if isinstance(it, dict) else str(it)
        out.append(f"{indent}{n}. " + (f"**{when}** — {what}" if when else what))
    return out


def build_point_tree(
    points: List[Dict[str, Any]],  # load_points output (source order)
) -> List[Dict[str, Any]]:  # [{"p": point, "kids": [{"p", "kids"}…]}] — roots in source order, kids in source order
    """Nest by `parent_key` (second-read ruling (4): depth TWO in practice, the tree is generic).
    A point whose parent is absent (retracted) renders as a root, never disappears; a cycle
    cannot form (a parent is always an earlier accepted point) but is guarded anyway."""
    keys = {str(p.get("key")): p for p in points}
    kids: Dict[str, List[Dict[str, Any]]] = {}
    roots: List[Dict[str, Any]] = []
    for p in points:
        pk = str(p.get("parent_key") or "")
        if pk and pk in keys and pk != str(p.get("key")):
            kids.setdefault(pk, []).append(p)
        else:
            roots.append(p)

    def _node(p: Dict[str, Any], seen: set) -> Dict[str, Any]:
        k = str(p.get("key"))
        seen = seen | {k}
        return {"p": p, "kids": [_node(c, seen) for c in kids.get(k, []) if str(c.get("key")) not in seen]}
    return [_node(r, set()) for r in roots]


def nest_points(
    points: List[Dict[str, Any]],  # load_points output (source order)
) -> List[Tuple[Dict[str, Any], List[Dict[str, Any]]]]:  # [(root point, [its direct children])] — the one-level view
    """The one-level view of `build_point_tree` (kept for callers that only need parent -> children)."""
    return [(n["p"], [c["p"] for c in n["kids"]]) for n in build_point_tree(points)]


def _tail(p: Dict[str, Any], timestamps: str, ctx: Optional[Dict[str, Any]] = None) -> str:  # " (span) [§](#pt-x){…}"
    return f"{_span(p, timestamps, ctx)} {_anchor_link(p)}"


def _item(
    p: Dict[str, Any],
    text: str,                          # the point's line text (`_point_text` output)
    timestamps: str,
    ctx: Optional[Dict[str, Any]],      # _render_ctx output (None = the book path's old shape: text + tail)
) -> str:  # the list item's content: text + span + permalink, in the style's order
    """One item's content in the type's ANCHOR style. `tail` (the default, the book shapes):
    `text (span) §` — the glyph at the end. `head` (ruling de9c4cda for the standalone lecture
    page; read 9d301b5a): `§ text (span)` — the permalink, which IS the item's anchor id,
    sits at the head of the item, so a link to a point that wraps lands the reader on its
    first line instead of below it, and the glyph reads as the bullet's own mark."""
    if ctx is not None and str((ctx.get("style") or {}).get("anchor")) == "head":
        return f"{_anchor_link(p)} {text}{_span(p, timestamps, ctx)}"
    return f"{text}{_tail(p, timestamps, ctx)}"


def speaker_labels(
    roster: Optional[List[Dict[str, Any]]],  # The unit's speaker roster, first-appearance order: [{speaker, name?, role?}]
) -> Dict[str, str]:  # line speaker string -> the label a reader sees
    """Ruling bc62c727 (B): a speaker reads as its NAME, else its ROLE in the role's own words
    ('Audience member'), else a neutral anonymous label — 'Speaker N', numbered by first
    appearance among the source's unnamed, role-less voices. A diarization cluster id is
    never printed. A speaker the roster does not list keeps its own string (a point accepted
    before rosters existed carries the name itself)."""
    out: Dict[str, str] = {}
    n = 0
    for r in roster or []:
        key = str((r or {}).get("speaker") or "")
        if not key or key in out:
            continue
        name, role = str(r.get("name") or "").strip(), str(r.get("role") or "").strip()
        if name:
            out[key] = name
        elif role:
            out[key] = role[:1].upper() + role[1:]
        else:
            n += 1
            out[key] = f"Speaker {n}"
    return out


def _render_ctx(
    pts: List[Dict[str, Any]],   # every point the page renders (no synopsis)
    unit: Dict[str, Any],        # the points' unit snapshot (carries `speaker_roster` when the source has speakers)
    style: Optional[Dict[str, Any]] = None,   # the type's render_style: {"span": "range" | "start", "anchor": "tail" | "head"}
) -> Dict[str, Any]:  # {labels, prev, by_key, style, section_of} — one per rendering pass (the outline and the body each track their own speaker)
    """The state one rendering pass threads through its points: the reader-facing speaker
    labels, the speaker of the previous rendered point (a label prints only where it
    changes), the STANDING points a back-link may target (a section is a heading, never a
    target), the type's RENDER STYLE (ruling de9c4cda / read 9d301b5a: a lecture page shows a
    point's START time and puts the permalink at the HEAD of the item; the book shapes keep
    the range and the tail — the defaults), and `section_of` (point key -> section index),
    filled by `render_points` once the sections are known so a back-link inside its own
    section can be left out."""
    return {"labels": speaker_labels(unit.get("speaker_roster")), "prev": "",
            "by_key": {str(p.get("key")): p for p in pts if str(p.get("kind")) != SECTION_KIND},
            "style": {"span": "range", "anchor": "tail", **{k: v for k, v in (style or {}).items() if v}},
            "section_of": {}}


def _question_lead(
    p: Dict[str, Any],     # a `question` point
    who: str,              # the label of the point's derived speaker ("" when the source carries no speakers)
) -> str:  # "**Q** (*who*):" | "**Q** (chat, relayed by *host*):" | "**Q** (*asker*, relayed by *host*):" | "**Q**:"
    """A question LEADS with who asked it (work item e370e5db (2)). The derived speaker of a
    RELAYED question is the host who read it out (ruling ba341c72 (1) stands), so the point's
    `data.relayed` — true, or the channel it came through — and `data.asker` — the name the
    host gave — say so (ruling bc62c727 (B3)): the asker (else the channel) leads, the host
    is named as the relay."""
    data = dict(p.get("data") or {})
    asker = str(data.get("asker") or "").strip()
    relayed = data.get("relayed")
    if relayed or asker:
        via = relayed.strip() if isinstance(relayed, str) else ""
        bits = [f"*{asker}*" if asker else via, f"relayed by *{who}*" if who else "relayed"]
    else:
        bits = [f"*{who}*" if who else ""]
    inner = ", ".join(b for b in bits if b)
    return "**Q**" + (f" ({inner})" if inner else "") + ":"


def _code_text(p: Dict[str, Any]) -> str:  # a `code` point: the identifier VERBATIM in inline code, an unverified mark when heard-not-seen
    """Work item e370e5db (4): the lead is an identifier, so it is code-formatted where the
    text names it (case-sensitive — an identifier is verbatim), prefixed when the text lacks
    it, and left alone when the drafter already fenced it. `data.unverified` (the audio alone
    could not confirm the identifier) prints a mark the reader can act on."""
    lead, text = str(p.get("lead") or "").strip(), str(p.get("text") or "").strip()
    body = text
    if lead and f"`{lead}`" not in text:
        i = text.find(lead)
        body = (text[:i] + f"`{lead}`" + text[i + len(lead):]) if i >= 0 else f"`{lead}` — {text}"
    if dict(p.get("data") or {}).get("unverified"):
        body += " *(unverified)*"
    return body


def _back_links(
    p: Dict[str, Any],
    ctx: Dict[str, Any],   # _render_ctx output
    timestamps: str,
) -> str:  # " (see [§ label](#pt-x), …)" | "" — only the referred points that STAND, outside this point's own section
    """A point's `refers_to` as anchors into the page (ruling ba341c72 (2); work item e370e5db
    (2)): a referred point that was never accepted, or was retracted since, renders nothing —
    never a dangling anchor. A target INSIDE the point's own section renders nothing either
    (read 9d301b5a: 36 of the Bonus page's 163 cross-references pointed a few lines up; the
    section already holds both ends, so the link cost space and gave no route) — `section_of`
    is filled by `render_points` once the sections are known; a page with no sections keeps
    every standing link. The link reads as the target's lead term, else its start time where
    the page renders spans at all, else its opening words — never a bare glyph a reader
    cannot tell from the next one."""
    links: List[str] = []
    section_of = ctx.get("section_of") or {}
    own = section_of.get(str(p.get("key")))
    for rk in p.get("refers_to") or []:
        t = ctx["by_key"].get(str(rk))
        if t is None or str(t.get("key")) == str(p.get("key")):
            continue
        if own is not None and section_of.get(str(t.get("key"))) == own:
            continue
        label = str(t.get("lead") or "").strip() or (_fmt_ts(t.get("start_time")) if _span(t, timestamps, ctx) else "")
        if not label:
            words = str(t.get("text") or "").split()
            label = " ".join(words[:BACK_LINK_WORDS]).rstrip(".,;:") + ("…" if len(words) > BACK_LINK_WORDS else "")
        links.append(f"[{ANCHOR_GLYPH}{' ' + label if label else ''}](#{_anchor(t)})")
    return f" (see {', '.join(links)})" if links else ""


def _point_text(
    p: Dict[str, Any],
    ctx: Optional[Dict[str, Any]],   # _render_ctx output; None = no speakers, no back-links (the book path's old shape)
    timestamps: str = "addressable",
    *,
    outline: bool = False,           # the scan line: the outline text, no back-links
) -> str:  # the point's line text: who says it (only where that changes) + the kind's shape + its back-links
    """One point's text as the page prints it. SPEAKER (work item e370e5db (1); ruling
    bc62c727 (B)): an italic lead label only where the speaker differs from the previous
    rendered point — dense text, never a per-line column — read from the Point's derived
    `speaker`; a `question` always leads with who asked. A point with no speaker (a book)
    prints no label and leaves the running speaker alone."""
    kind = str(p.get("kind") or "claim")
    body = _outline_text(p) if outline else (_code_text(p) if kind == "code" else _lead_text(p))
    if ctx is None:
        return body
    sp = str(p.get("speaker") or "")
    who = ctx["labels"].get(sp, sp)
    changed = bool(sp) and sp != ctx["prev"]
    if sp:
        ctx["prev"] = sp
    if kind == "question":
        body = f"{_question_lead(p, who)} {body}"
    elif changed:
        body = f"*{who}:* {body}"
    return body if outline else body + _back_links(p, ctx, timestamps)


def group_points(
    pts: List[Dict[str, Any]],   # the body's points, source order (no synopsis, no glossary)
    unit: Dict[str, Any],        # the points' unit snapshot (its title suppresses a header that restates it)
) -> List[Tuple[str, List[Dict[str, Any]]]]:  # [(heading, build_point_tree roots)] in page order; "" = no heading
    """The page's sections. SYNTHESIZED (ruling bc62c727 (A)): when the points include
    `section` points the structure is theirs — a section sorts immediately before the first
    point it covers, and membership is DERIVED: every root point from one section's anchor
    to the next, children following their parent wherever they fall. A section left with no
    points (its points were retracted, or the next section starts where it does) renders
    nothing, so retracting a section drops its points into the previous one. CAPTURED (the
    book path, unchanged): consecutive points sharing the read-aloud header captured at
    propose time; a header that restates the unit's own title is suppressed (e1fd4d64 (C))."""
    marks = [p for p in pts if str(p.get("kind")) == SECTION_KIND]
    if marks:
        groups: List[Tuple[str, List[Dict[str, Any]]]] = [("", [])]
        k = 0
        for node in build_point_tree([p for p in pts if str(p.get("kind")) != SECTION_KIND]):
            while k < len(marks) and _sort_key(marks[k]) <= _sort_key(node["p"]):
                groups.append((str(marks[k].get("text") or ""), []))
                k += 1
            groups[-1][1].append(node)
        return [(h, tree) for h, tree in groups if tree]
    runs: List[Tuple[str, List[Dict[str, Any]]]] = []
    for p in pts:
        h = str(p.get("heading") or "")
        if unit_title_header(h, unit):
            h = ""   # the unit's own title, not a section — wherever it occurs, so its points stay ONE group
        if runs and runs[-1][0] == h:
            runs[-1][1].append(p)
        else:
            runs.append((h, [p]))
    return [(h, build_point_tree(ps)) for h, ps in runs]


def _glossary_lines(
    gloss: List[Dict[str, Any]],   # the `glossary` points
    timestamps: str,
    *,
    outline: bool = False,         # the scan view: no spans, no anchors of its own
    link: bool = False,            # outline lines link to the body's anchors (rendering "both")
    ctx: Optional[Dict[str, Any]] = None,   # _render_ctx output: the render style (span + anchor placement)
) -> List[str]:  # the closer's list lines, alphabetical by term
    """The DERIVED closing section (work item e370e5db (3)): every glossary point, by term —
    `**Term** — the source's usage` — with the transcript's surface form kept beside the term
    when it differed (nickel -> NCCL: the pair is the fidelity chain's evidence, f9d0fd93). A
    text that opens with its own term never prints it twice. The span and the permalink
    follow the type's render style like every other item (`_item`)."""
    out: List[str] = []
    for p in sorted(gloss, key=lambda q: (str(q.get("lead") or q.get("text") or "").casefold(), _sort_key(q))):
        term, text = str(p.get("lead") or "").strip(), str(p.get("text") or "").strip()
        asr = str(dict(p.get("data") or {}).get("asr_form") or "").strip()
        heard = f" (heard as “{asr}”)" if asr and asr.casefold() != term.casefold() else ""
        rest = text[len(term):].lstrip(" —–-:,") if term and text.casefold().startswith(term.casefold()) else text
        line = (f"**{term}**{heard}" + (f" — {rest}" if rest else "")) if term else text
        if outline:
            out.append(f"- [{line}](#{_anchor(p)})" if link else f"- {line}")
        else:
            out.append(f"- {_item(p, line, timestamps, ctx)}")
    return out


def _render_node(
    node: Dict[str, Any],   # {"p", "kids"} from build_point_tree
    indent: str,            # the item's own indent ("" at top level); children indent to the item's CONTENT column
    timestamps: str,
    ctx: Optional[Dict[str, Any]] = None,   # _render_ctx output: speaker labels + back-link targets + render style (None = neither)
) -> List[str]:  # markdown lines for the point and its subtree
    """One point as a list item at `indent`, its children beneath it. Block kinds keep their
    shapes at every depth: a `quotation` is a `>` block (blank-line-separated, indented to
    the item's content column — ruling (5)); a `sequence` lists its `event` children as an
    ordered list (legacy `data.items` still renders); a `comparison` carries its table inside
    the item; everything else is a bullet whose lead is bolded in place. Indents are the
    CONTENT column of the enclosing item ("- " = 2, "1. " = 3), the only way Pandoc keeps a
    nested block inside the item. With a `ctx` the line text is `_point_text` — the speaker
    label where it changes, a question's lead, a code point's identifier, the back-links; a
    quotation nobody attributed takes a changed speaker as its attribution. The span and the
    permalink sit where the type's render style puts them (`_item`): at the tail (books) or
    the permalink at the head (the lecture page); a quotation in the head style carries its
    permalink at the head of its first line and its span on the attribution line."""
    p, kids = node["p"], node["kids"]
    kind = str(p.get("kind") or "claim")
    sub = indent + "  "          # content column of a "- " item
    head = ctx is not None and str((ctx.get("style") or {}).get("anchor")) == "head"
    out: List[str] = []
    if kind == "quotation":
        who = str(p.get("attribution") or "").strip()
        text = str(p.get("text") or "").strip()
        sp = str(p.get("speaker") or "")
        if ctx is not None and sp:
            if not who and sp != ctx["prev"]:
                who = ctx["labels"].get(sp, sp)
            ctx["prev"] = sp
        if indent:
            out.append("")
        out.append(f"{indent}> {_anchor_link(p)} {text}" if head else f"{indent}> {text}")
        out.append(f"{indent}>" + (f" — {who}" if who else "") + (_back_links(p, ctx, timestamps) if ctx is not None else "")
                   + (_span(p, timestamps, ctx) if head else _tail(p, timestamps, ctx)))
        out.append("")
        for k in kids:
            out += _render_node(k, indent, timestamps, ctx)   # a quotation's support sits at the quotation's own indent
        return out
    if kind == "sequence":
        out.append(f"{indent}- {_item(p, _point_text(p, ctx, timestamps), timestamps, ctx)}")
        events = [k for k in kids if str(k["p"].get("kind")) == "event"]
        others = [k for k in kids if str(k["p"].get("kind")) != "event"]
        n = 1
        for ev in events:
            q = ev["p"]
            when = str((q.get("data") or {}).get("when") or "").strip()
            out.append(f"{sub}{n}. " + _item(q, (f"**{when}** — " if when else "") + _point_text(q, ctx, timestamps), timestamps, ctx))
            for g in ev["kids"]:
                out += _render_node(g, sub + "   ", timestamps, ctx)   # the ordered item's content column
            n += 1
        if not events:
            out += _sequence_items(p, sub)
        for k in others:
            out += _render_node(k, sub, timestamps, ctx)
        return out
    if kind == "comparison":
        out.append(f"{indent}- {_item(p, _point_text(p, ctx, timestamps), timestamps, ctx)}")
        out.append("")
        out += _render_table(dict(p.get("data") or {}), sub)
        out.append("")
        for k in kids:
            out += _render_node(k, sub, timestamps, ctx)
        return out
    if kind == "event":
        when = str((p.get("data") or {}).get("when") or "").strip()
        out.append(f"{indent}- " + _item(p, (f"**{when}** — " if when else "") + _point_text(p, ctx, timestamps), timestamps, ctx))
    else:
        out.append(f"{indent}- {_item(p, _point_text(p, ctx, timestamps), timestamps, ctx)}")
    for k in kids:
        out += _render_node(k, sub, timestamps, ctx)
    return out


def render_points(
    points: List[Dict[str, Any]],   # load_points output (source order)
    *,
    rendering: str = "expanded",    # "outline" | "expanded" | "both"
    timestamps: str = "addressable",  # "always" | "addressable" | "never" (see _span)
    outline_title: str = "At a glance",
    style: Optional[Dict[str, Any]] = None,   # the type's render_style: {"span": "range" | "start", "anchor": "tail" | "head"} (None = the book defaults)
) -> str:  # The body markdown (after the preamble)
    """Render the body from the Points — deterministic, so a replayed `render-notes` derives
    the same Sections. EXPANDED (the public post): under the derived headings (`group_points`:
    the SYNTHESIZED section points when the points carry them — ruling bc62c727 (A) — else the
    captured read-aloud headers) each root point with its permalink glyph and its subtree
    (depth two in practice), spans only when the source is addressable; block kinds keep
    their shapes at every depth; consecutive top-level `step`s are ONE ordered list; the
    `synopsis` never renders in the body (it is the description). OUTLINE (review / the work
    page): one line per point, same headings. THE LECTURE SHAPES (work item e370e5db): a
    speaker label only where the speaker changes — and again at each heading, so a reader
    who lands on a section knows who is speaking — a `question` led by who asked it with its
    answers nested beneath, `refers_to` as back-link anchors to the points that stand OUTSIDE
    the point's own section (read 9d301b5a), a `code` point's identifier in inline code, and
    every `glossary` point in ONE derived closing section, alphabetically, never in the body.
    THE RENDER STYLE (ruling de9c4cda; type data `presentation_policy.render_style`): the span
    as a range or the start time alone, the permalink at the item's tail or its head — the
    book shapes keep the defaults byte for byte. Every input is a Point field or type data
    (the derived speaker, the back-link keys, the unit's speaker roster, the style), so the
    labels and the anchors replay from the accept ops and the type alone."""
    pts = [p for p in sorted(points, key=_sort_key) if str(p.get("kind")) != "synopsis"]
    unit = dict((pts[0].get("unit") or {}) if pts else {})
    gloss = [p for p in pts if str(p.get("kind")) == GLOSSARY_KIND]
    groups = group_points([p for p in pts if str(p.get("kind")) != GLOSSARY_KIND], unit)
    # point key -> section index: a back-link whose target sits in the same SYNTHESIZED section renders
    # nothing (read 9d301b5a); a captured-heading page (the book shapes) keeps every standing link
    section_of: Dict[str, int] = {}

    def _index(node: Dict[str, Any], gi: int) -> None:
        section_of[str(node["p"].get("key"))] = gi
        for k in node["kids"]:
            _index(k, gi)
    if any(str(p.get("kind")) == SECTION_KIND for p in pts):
        for gi, (_h, tree) in enumerate(groups):
            for n in tree:
                _index(n, gi)
    lines: List[str] = []
    want_outline = rendering in ("outline", "both")
    want_expanded = rendering in ("expanded", "both")

    if want_outline:
        octx = _render_ctx(pts, unit, style)
        octx["section_of"] = section_of
        lines += [f"## {outline_title}", ""]
        for h, tree in groups:
            if h:
                lines += [f"**{_heading_text(h)}**", ""]
            octx["prev"] = ""

            def _walk(node: Dict[str, Any], depth: int) -> None:
                q = node["p"]
                ind = "  " * depth
                text = _point_text(q, octx, timestamps, outline=True)
                lines.append(f"{ind}- [{text}](#{_anchor(q)})" if want_expanded else f"{ind}- {text}")
                for k in node["kids"]:
                    _walk(k, depth + 1)
            for n in tree:
                _walk(n, 0)
            lines.append("")
        if gloss:
            lines += [f"**{GLOSSARY_HEADING}**", ""] + _glossary_lines(gloss, timestamps, outline=True, link=want_expanded, ctx=octx) + [""]

    if want_expanded:
        ctx = _render_ctx(pts, unit, style)
        ctx["section_of"] = section_of
        block_kinds = ("step", "quotation")
        for h, tree in groups:
            if h:
                lines += [f"## {_heading_text(h)}", ""]
            # a unit with no section headers renders its points with no heading at all (a lone
            # "## Notes" says nothing to a reader; the source card already names the unit)
            ctx["prev"] = ""
            i = 0
            while i < len(tree):
                node = tree[i]
                kind = str(node["p"].get("kind") or "claim")
                if kind == "step":
                    n = 1
                    while i < len(tree) and str(tree[i]["p"].get("kind")) == "step":
                        q = tree[i]["p"]
                        lines.append(f"{n}. {_item(q, _point_text(q, ctx, timestamps), timestamps, ctx)}")
                        for k in tree[i]["kids"]:
                            lines += _render_node(k, "   ", timestamps, ctx)   # the "1. " content column
                        n += 1
                        i += 1
                    lines.append("")
                    continue
                lines += _render_node(node, "", timestamps, ctx)
                nxt = str(tree[i + 1]["p"].get("kind")) if i + 1 < len(tree) else None
                if kind != "quotation" and (nxt is None or nxt in block_kinds):
                    lines.append("")   # close the bullet run before a block or the section end
                i += 1
        if gloss:
            if lines and lines[-1] != "":
                lines.append("")
            lines += [f"## {GLOSSARY_HEADING}", ""] + _glossary_lines(gloss, timestamps, ctx=ctx) + [""]
    text = "\n".join(lines).rstrip("\n") + "\n"
    return text if text.strip() else ""


def synopsis_of(
    points: List[Dict[str, Any]],  # load_points output
) -> str:  # The accepted `synopsis` point's text ("" when none)
    for p in sorted(points, key=_sort_key):
        if str(p.get("kind")) == "synopsis":
            return str(p.get("text") or "").strip()
    return ""


def unit_label(unit: Dict[str, Any]) -> str:  # "Ch. 1" | "Part 2" | the unit title | ""
    """The short unit handle the title carries (second-read ruling (1): the short shape)."""
    ws = dict((unit or {}).get("work_structure") or {})
    if ws.get("kind") == "chapter" and ws.get("chapter") is not None:
        return f"Ch. {ws.get('chapter')}"
    return str(ws.get("title") or "").strip()


def lecture_title(unit: Dict[str, Any]) -> str:  # the lecture's public title: the playlist row's, else the Source title with its on-disk substitutes undone
    """The title a reader knows the lecture by. The URL binding kept the playlist row's title as
    evidence (`lecture_title` on the unit's facts); without it the Source title — a file name —
    is folded through NFKC (a fullwidth '：' becomes ':') with the slash substitutes undone and
    whitespace collapsed, the inverse of the fold the binding joined on."""
    t = str((unit or {}).get("lecture_title") or "").strip()
    if t:
        return t
    t = unicodedata.normalize("NFKC", str((unit or {}).get("title") or "")).replace("⧸", "/").replace("／", "/")
    return " ".join(t.split()).strip()


def date_phrase(iso: str, precision: str = "day") -> str:  # "Apr 27, 2024" | "around Apr 27, 2024" | "Apr 2024" | "2024" | "" (malformed)
    """A date as the card prints it, at the precision it is KNOWN to (ruling de9c4cda (H7)): `day`
    the full day; `around` the day with the hedge said out loud (a re-uploaded live stream is
    known to within days); `month` and `year` drop what is not known. Locale-free."""
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(iso or "").strip())
    if not m:
        return ""
    y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    if not 1 <= mo <= 12:
        return ""
    p = str(precision or "day").strip()
    if p == "year":
        return y
    if p == "month":
        return f"{months[mo - 1]} {y}"
    full = f"{months[mo - 1]} {d}, {y}"
    return f"around {full}" if p == "around" else full


def render_source_card(
    unit: Dict[str, Any],  # A point's `unit` (carries work_structure incl. `work`), merged with the source facts for a lecture (series, lecture_title, dates, public_url, speaker_roster)
    references: Optional[List[Dict[str, Any]]] = None,  # RESOLVED human-added links [{label, href}] (ae103970); none = no Resources line
) -> str:  # A Quarto callout naming the work, author, and unit — or the lecture, its series, dates and speakers; "" without either
    """The reader-facing provenance (second-read ruling (1)): a derived one-line callout under
    the title so the post is never mistaken for the source. THE WORK SHAPE (books): the work,
    its author, the part/chapter, and the paraphrase/verbatim rule. THE LECTURE SHAPE (finding
    baa640e8; rulings de9c4cda (H2) (H7)): the lecture's public title, the series it belongs to
    (the Collection), when it was recorded and published — at the precision each is known to,
    because a standalone page's claims describe the world as of that date — the speakers by
    the labels the page uses (names and roles; an anonymous voice is not card material), the
    paraphrase rule reworded for a talk, and the WATCH link first among the resources when
    the source is addressable. Nothing of the lane's vocabulary. The Source's human-added
    resource links (ruling a7ca900d (3)) render as a `Resources:` line INSIDE the card —
    derived from Reference nodes, never authored into the body; a link with no resolvable
    target renders as its label alone (never an empty link)."""
    u = dict(unit or {})
    ws = dict(u.get("work_structure") or {})
    work = dict(ws.get("work") or {}) if isinstance(ws.get("work"), dict) else {}
    refs = [r for r in (references or []) if str(r.get("label") or "").strip()]
    parts = [(f"[{str(r['label']).strip()}]({r['href']})" if str(r.get("href") or "").strip()
              else str(r["label"]).strip()) for r in refs]
    if work.get("title"):
        who = f" by {work['author']}" if work.get("author") else ""
        where_bits: List[str] = []
        if ws.get("part") is not None:
            where_bits.append(f"Part {ws['part']}" + (f" ({ws['part_title']})" if ws.get("part_title") else ""))
        if ws.get("kind") == "chapter" and ws.get("chapter") is not None:
            where_bits.append(f"Chapter {ws['chapter']}")
        where = " · ".join(where_bits)
        title = str(ws.get("title") or "").strip()
        place = (f" — {where}" if where else "") + (f", *{title}*" if title else "")
        resources = ("\n\nResources: " + " · ".join(parts)) if parts else ""
        return ("::: {.callout-note appearance=\"simple\" icon=false}\n"
                f"Notes on **{work['title']}**{who}{place}. The points paraphrase the {ws.get('kind') or 'source'} "
                "in its own order; only the quotations are verbatim." + resources + "\n:::\n")
    series = [str(s).strip() for s in (u.get("series") or []) if str(s).strip()]
    if not series and not str(u.get("lecture_title") or "").strip():
        return ""
    title = lecture_title(u)
    of = f", a *{' / '.join(series)}* lecture" if series else ""
    rec = date_phrase(str(u.get("recorded_at") or ""), str(u.get("recorded_at_precision") or "day"))
    pub = date_phrase(str(u.get("published_at") or ""))
    if rec and pub:
        when = (f"recorded and published {pub}" if rec == pub else f"recorded {rec}, published {pub}")
    else:
        when = f"recorded {rec}" if rec else (f"published {pub}" if pub else "")
    labels = speaker_labels(u.get("speaker_roster"))
    speakers = [labels[str(r.get("speaker"))] for r in (u.get("speaker_roster") or [])
                if str(r.get("speaker") or "") in labels and (str(r.get("name") or "").strip() or str(r.get("role") or "").strip())]
    spoken = (" Speakers: " + ", ".join(dict.fromkeys(speakers)) + ".") if speakers else ""
    url = str(u.get("public_url") or "").strip()
    watch = ([f"[Watch on YouTube]({url})"] if "youtube.com/" in url or "youtu.be/" in url else [f"[Watch]({url})"]) if url else []
    resources = ("\n\nResources: " + " · ".join(watch + parts)) if (watch or parts) else ""
    return ("::: {.callout-note appearance=\"simple\" icon=false}\n"
            f"Notes on **{title}**{of}" + (f" — {when}" if when else "") + f".{spoken} The points paraphrase the talk "
            "in the order it was given; only the quotations are verbatim." + resources + "\n:::\n")


def derived_description(
    points: List[Dict[str, Any]],  # load_points output
) -> str:  # The frontmatter description a reader can use (e1fd4d64 (A)), "" when nothing to derive
    """What the unit CONTAINS, from data the rendering already uses: the work (when the
    structure map names it), the unit title, its section headings in order, and the type's
    one distinguishing clause. Never the lane's vocabulary (strata, graph, file numbers)."""
    pts = sorted(points, key=_sort_key)
    if not pts:
        return ""
    unit = dict(pts[0].get("unit") or {})
    ws = dict(unit.get("work_structure") or {})
    title = str(ws.get("title") or "").strip()
    kind = str(ws.get("kind") or "source").strip() or "source"
    heads: List[str] = []
    for p in pts:
        # a SYNTHESIZED section's title is its own text (ruling bc62c727 (A)); every other point names its captured header
        h = _heading_text(str((p.get("text") if str(p.get("kind")) == SECTION_KIND else p.get("heading")) or ""))
        if h and h not in heads and not (not heads and unit_title_header(h, unit)):
            heads.append(h)
    work = str((ws.get("work") or {}).get("title") or "").strip() if isinstance(ws.get("work"), dict) else ""
    lead = (f"{work} — " if work else "") + (title or "Notes")
    body = (": " + " · ".join(heads)) if heads else ""
    return f"{lead}{body}. What the {kind} says, in its own order, without added commentary."


def derive_frontmatter(
    fm_raw: str,                    # The authored frontmatter block ("---\\n…\\n---\\n")
    points: List[Dict[str, Any]],   # load_points output
    policy: Dict[str, Any],         # presentation_policy["frontmatter"] ({"title": "work-unit-notes" | "unit-title" | "series-lecture-notes" | "lecture-label-leads", "description": "synopsis" | "derived"})
    *,
    synopsis: str = "",             # The accepted synopsis point's text (policy "synopsis"; derived headings as fallback)
    unit: Optional[Dict[str, Any]] = None,   # The unit the title reads (default: the first point's snapshot); a render passes the snapshot MERGED with the live source facts
) -> str:  # The frontmatter with the policy-owned lines replaced (or inserted after title)
    """The type may OWN the title and the description — the rest of the authored frontmatter
    (date, categories, …) stays. Title `work-unit-notes` (second-read ruling (1), the short
    shape) = "<work>, Ch. n notes", falling back to the unit title without work metadata;
    `unit-title` = the unit title alone; `series-lecture-notes` (finding baa640e8) = the
    series and the lecture's public title with `notes` after the lecture's own label —
    "GPU MODE Bonus Lecture notes: CUDA C++ llm.cpp" when the title splits at a colon, else
    "<series> <title> notes" — the companion-post naming; `lecture-label-leads` (ruling
    96be1528 (9), the STANDALONE lecture resource) = the lecture's OWN label as the title —
    "CUDA C++ llm.cpp" — with the series and the lecture's slot moved to a `subtitle` line
    ("Notes on the GPU MODE Bonus Lecture") and the card, because a standalone resource is a
    different situation from the chapters of one book, where the work leads. Description
    `synopsis` = the accepted synopsis point (ruling (2)), falling back to the derived
    headings; `derived` = the headings. Idempotent: a re-derive over derived lines yields the
    same bytes."""
    if not fm_raw.startswith("---") or not points:
        return fm_raw
    pts = sorted(points, key=_sort_key)
    unit = dict(unit if unit is not None else (pts[0].get("unit") or {}))
    ws = dict(unit.get("work_structure") or {})
    work = dict(ws.get("work") or {}) if isinstance(ws.get("work"), dict) else {}
    want: Dict[str, str] = {}
    tpol = policy.get("title")
    if tpol == "work-unit-notes" and str(work.get("title") or "").strip():
        lab = unit_label(unit)
        want["title"] = f"{work['title']}, {lab} notes" if lab else f"{work['title']} notes"
    elif tpol in ("unit-title", "work-unit-notes") and str(ws.get("title") or "").strip():
        want["title"] = str(ws.get("title")).strip()
    elif tpol in ("series-lecture-notes", "lecture-label-leads"):
        lec = lecture_title(unit)
        series = " / ".join(str(s).strip() for s in (unit.get("series") or []) if str(s).strip())
        if lec:
            head, sep, rest = lec.partition(": ")
            split = bool(sep and head.strip() and rest.strip())
            if tpol == "series-lecture-notes":
                want["title"] = (f"{series} {head.strip()} notes: {rest.strip()}" if split else f"{series} {lec} notes").strip()
            else:
                want["title"] = rest.strip() if split else lec.strip()
                label = head.strip() if split else ""
                # a lecture whose own label already names the series ("GPU MODE Lecture 12") is not doubled
                slot = label if series and label.lower().startswith(series.lower()) else " ".join(s for s in (series, label) if s)
                want["subtitle"] = f"Notes on the {slot}" if slot else "Lecture notes"
    dpol = policy.get("description")
    if dpol == "synopsis" and synopsis.strip():
        want["description"] = synopsis.strip()
    elif dpol in ("derived", "synopsis"):
        d = derived_description(points)
        if d:
            want["description"] = d
    return _replace_frontmatter_lines(fm_raw, want)


async def render_notes(
    gx: GraphHandle,
    slug: str,                          # The deliverable Note's slug
    *,
    rendering: str = "expanded",        # "outline" | "expanded" | "both" (the public post = expanded)
    timestamps: str = "addressable",    # "always" | "addressable" | "never"
    write_md: bool = True,              # Write the staging `.md` (replay passes False)
    actor: str = "agent:session",
    siblings: Optional[Dict[str, str]] = None,        # {graph key: db path} — read the unit's LIVE Reference nodes + source facts from the sibling (ae103970, baa640e8)
    graph_key: Optional[str] = None,                  # Sibling key (default: the points' unit graph, else the sole key)
    manifests_dir: Optional[str] = None,              # Capability manifests dir
    references: Optional[List[Dict[str, Any]]] = None,  # Raw Reference rows to render (REPLAY passes the journaled ones; None = read live, else the unit snapshot)
    facts: Optional[Dict[str, Any]] = None,             # The source facts to render (REPLAY passes the journaled ones; None = read live, else the unit snapshot)
) -> Dict[str, Any]:  # {slug, points, added, updated, removed, written, text} | {error}
    """Derive the Note's body from its Points and APPLY it: the authored preamble stays, the
    frontmatter's type-owned lines (title / description) are re-derived per the type's
    presentation policy, everything after is re-derived, the diff lands as Section
    adds/updates, and Sections the rendering no longer produces are deleted (render owns the
    body). The staging file is rewritten from the same text. Idempotent: the same Points
    render the same bytes. The source card carries the unit's human-added links (ruling
    a7ca900d (3)): read LIVE from the sibling when one is at hand, else the points' unit
    snapshot; the op JOURNALS the rows it observed so replay renders the same card with no
    sibling open (the accept-point observations pattern), and the substance digest covers
    the RESOLVED hrefs so a re-render after a cross-work target is born lands a new op. A
    type that asks for the SOURCE FACTS (finding baa640e8: `frontmatter.title =
    series-lecture-notes` or `source_card = "lecture"`) reads them the same way — series,
    public title, public URL, dates — merges them over the unit snapshot for the title and
    the card, journals them, and digests them; a book type never asks, so its ops and
    digests are the ones it always had. The type's `render_style` reaches the body the same
    way and rides the digest when set."""
    from .authoring import _note_section_wires
    from .structure import _apply_note_text
    note_id = note_node_id(slug)
    note = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=note_id)
    if note is None:
        return {"error": f"no note `{slug}`", "slug": slug, "written": False}
    if rendering not in ("outline", "expanded", "both"):
        return {"error": f"rendering must be outline | expanded | both, got {rendering!r}", "written": False}
    if timestamps not in ("always", "addressable", "never"):
        return {"error": f"timestamps must be always | addressable | never, got {timestamps!r}", "written": False}
    points = await load_points(gx, note_id)
    wires = await _note_section_wires(gx, note_id)
    pre = ""
    for w in wires:
        if str(F.props(w).get("anchor")) == "_preamble":
            pre = str(F.props(w).get("raw") or "")
    # Heading-less opening points (the unit's title header is suppressed — e1fd4d64 (C)) decompose
    # into the SAME `_preamble` Section as the authored preamble; the marker is the boundary a
    # re-render splits at, so the authored part never accretes rendered lines.
    pre = pre.split(BODY_MARKER, 1)[0]
    tkey = await note_deliverable_type(gx, note_id) or PURE_NOTES_KEY
    tprops = await load_deliverable_type(gx, tkey) or {}
    ppol = dict(tprops.get("presentation_policy") or {})
    fm_policy = dict(ppol.get("frontmatter") or {})
    style = {k: v for k, v in dict(ppol.get("render_style") or {}).items() if v}
    syn = synopsis_of(points)
    unit0 = dict(sorted(points, key=_sort_key)[0].get("unit") or {}) if points else {}
    want_card = bool(ppol.get("source_card", True)) and bool(points)
    wants_facts = bool(points) and (fm_policy.get("title") == "series-lecture-notes" or ppol.get("source_card") == "lecture")
    # The sibling is opened ONCE for whatever the type reads live: explicit rows (replay) > the
    # sibling's LIVE nodes > the unit snapshot.
    live_refs: Optional[List[Dict[str, Any]]] = None
    live_facts: Optional[Dict[str, Any]] = None
    need_refs, need_facts = want_card and references is None, wants_facts and facts is None
    if (need_refs or need_facts) and unit0.get("source_id"):
        key = graph_key or str(unit0.get("graph") or "")
        if siblings and (key in siblings or len(siblings) == 1):
            key = key if key in siblings else next(iter(siblings))
            try:
                async with open_graph(siblings[key], manifests_dir or DEFAULT_MANIFESTS, readonly=True) as sg:
                    if need_refs:
                        live_refs = await read_source_references(sg, str(unit0["source_id"]))
                    if need_facts:
                        live_facts = await read_source_facts(sg, str(unit0["source_id"]))
            except RuntimeError:
                live_refs, live_facts = None, None   # sibling unavailable -> the snapshot below
    if want_card and references is None:
        references = live_refs if live_refs is not None else list(unit0.get("references") or [])
    if wants_facts and facts is None:
        facts = live_facts if live_facts is not None else {k: unit0[k] for k in SOURCE_FACT_KEYS + ("public_url",) if unit0.get(k)}
    facts = dict(facts or {})
    unit_card = {**unit0, **facts}
    fm = derive_frontmatter(str(F.prop(note, "frontmatter_raw") or ""), points, fm_policy, synopsis=syn, unit=unit_card)
    card = ""
    resolved: List[Dict[str, Any]] = []
    if want_card:
        # The reader-facing provenance (second-read ruling (1)) — derived from the unit's work
        # metadata or the lecture's facts, rendered above the body, never authored.
        resolved = await resolve_references(gx, references or [])
        card = render_source_card(unit_card, resolved)
    body = render_points(points, rendering=rendering, timestamps=timestamps, style=style)
    if pre and not pre.endswith("\n\n"):
        pre = pre.rstrip("\n") + "\n\n"
    new_text = fm + pre + BODY_MARKER + "\n\n" + (card + "\n" if card else "") + body
    path = str(F.prop(note, "path") or "")
    res = await _apply_note_text(gx, note, slug, new_text, path, write=True, write_md=write_md)
    removed = list(res.get("removed") or [])
    if removed:
        from cjm_dev_graph_schema.identity import section_node_id
        await graph_task(gx.queue, gx.graph_id, "delete_nodes",
                         node_ids=[section_node_id(note_id, a) for a in removed], cascade=True)
        res["removed_applied"] = removed
    # The journaled op carries a SUBSTANCE digest: replay ignores it, but `append_write` dedups
    # identical ops — without it a re-render after an accept/retract would be dropped as a
    # duplicate of the first render and a rebuild would stop at the stale body. A true no-op
    # re-render (same points) still dedups.
    # The RESOLVED hrefs ride the digest too (ae103970): the same rows resolve differently once a
    # cross-work target is born, and that re-render must land as a new op, not dedup away.
    # What the LECTURE shapes print rides it as well (work item e370e5db (6)): a point's derived
    # speaker and its back-link keys, and the unit's speaker roster — appended only where a point
    # carries them, so a book's digest is the one it always had. The source FACTS and the render
    # STYLE join the same way (baa640e8): only when the type asked for them.
    roster0 = list(unit0.get("speaker_roster") or []) if points else []
    digest = hashlib.sha256(json.dumps(
        [[[p.get("key"), p.get("kind"), p.get("text"), p.get("lead"), p.get("heading"), p.get("data"),
           p.get("parent_key")] + ([p.get("speaker"), list(p.get("refers_to") or [])]
                                   if p.get("speaker") or p.get("refers_to") else []) for p in points],
         [[r.get("label"), r.get("href")] for r in resolved]] + ([roster0] if roster0 else [])
        + ([facts] if facts else []) + ([style] if style else []) + ([fm_policy] if wants_facts else []),
        sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
    res.update(points=len(points), rendering=rendering, timestamps=timestamps, text=new_text,
               references=resolved, facts=facts,
               args={"slug": slug, "rendering": rendering, "timestamps": timestamps, "actor": actor,
                     "substance": f"sha256:{digest}",
                     **({"references": [dict(r) for r in references]} if references else {}),
                     **({"facts": facts} if facts else {})})
    return res


def _frontmatter_fields(
    fm_raw: str,  # The Note's authored frontmatter block ("---\n…\n---\n")
) -> Dict[str, Any]:  # {title, date, description, categories} as plain strings / string lists; {} when absent or unparsable
    """Pure: the listing fields a staging index needs, parsed from a note's frontmatter with
    yaml (a date value becomes its ISO string so the projection stays plain data)."""
    if not fm_raw.startswith("---"):
        return {}
    lines = fm_raw.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}
    try:
        data = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, Any] = {}
    for k in ("title", "date", "description", "categories"):
        v = data.get(k)
        if v is None:
            continue
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        out[k] = [str(x) for x in v] if isinstance(v, list) else str(v)
    return out


async def note_publish_states(
    gx: GraphHandle,
) -> Dict[str, List[str]]:  # {Note id: sorted ACTIVE publish_state values} — deliverables only (the Notes carrying the fact)
    """The publish_state facts as a map: every deliverable's active values. One value is the
    healthy case; several is a supersession gap the staging index SHOWS (a `multi-active`
    list), never hides. The fixture / draft / reviewed / published / retired vocabulary is
    schema data (`predicates.PUBLISH_*`, item 140981e9)."""
    slot = [a for a in await F.load_assertions(gx) if F.prop(a, "predicate") == P.PUBLISH_STATE]
    active = F.active_assertions(slot, await F.load_supersedes(gx))
    out: Dict[str, List[str]] = {}
    for a in active:
        sid = str(F.prop(a, "subject_id") or "")
        if sid:
            out.setdefault(sid, []).append(str(F.prop(a, "value") or ""))
    return {k: sorted(set(v)) for k, v in out.items()}


async def work_of_note(
    gx: GraphHandle,
    note_id: str,  # The deliverable Note id
) -> str:  # The work title the note's Points derive from ("" for a page with no typed points)
    """Which WORK a typed deliverable belongs to — read from its Points' unit (the structure
    map rides every accepted point); a WORK PAGE has no Points and names its work by the
    Collection its DERIVED_FROM edge observes (`work_reference_of_note`). An essay or fixture
    page with neither has no work and the promotion condition never applies to it."""
    for p in await load_points(gx, note_id):
        ws = dict((p.get("unit") or {}).get("work_structure") or {})
        work = ws.get("work")
        if isinstance(work, dict) and str(work.get("title") or "").strip():
            return str(work["title"]).strip()
    ref = await work_reference_of_note(gx, note_id)
    return ref["title"] if ref else ""


async def work_promotion_status(
    gx: GraphHandle,
    *,
    siblings: Dict[str, str],               # {graph key: db path} — the transcription sibling holding the structure map
    graph_key: Optional[str] = None,        # Sibling key (default: the sole key)
    manifests_dir: Optional[str] = None,    # Capability manifests dir for opening the sibling
    work_title: Optional[str] = None,       # Restrict to one work (default: every work in the sibling's structure map)
) -> Dict[str, Any]:  # {sibling, works: [{work, chapters_total, chapters_born, condition_met, missing: [{source_id, chapter, title, file}], notes: [{note_id, slug, states, born, chapter, unit_title}]}]} | {error}
    """The WORK-PAGE promotion condition (ruling a7ca900d (1)/(4); item 140981e9 (c)) as a
    graph query: for each work in the sibling's structure map, its chapter units (Sources
    whose work_structure.kind is "chapter") and which of them carry a born deliverable at
    draft or better on THIS graph — a fixture, a retired page, or a Note with no
    publish_state does not count. A work is promotable only when EVERY chapter is born: the
    whole work replaces the pre-graph notes at once. Derived on read, never stored."""
    key = graph_key
    if key is None:
        if len(siblings) != 1:
            return {"error": f"pass a sibling key (config `sibling_graphs` keys: {sorted(siblings) or 'none'})"}
        key = next(iter(siblings))
    if key not in siblings:
        return {"error": f"no sibling graph `{key}` (keys: {sorted(siblings) or 'none'})"}
    # The works and their chapter units, from the sibling's structure map.
    try:
        async with open_graph(siblings[key], manifests_dir or DEFAULT_MANIFESTS, readonly=True) as sg:
            # Full nodes, not a projected row set: the structure map is a NESTED property and
            # only the node form carries it whole (the `read_source_unit` corrections pattern).
            q = NodeQuery(label="Source", limit=200000)
            res = await graph_task(sg.queue, sg.graph_id, "query_nodes", query=q.to_dict())
            rows = []
            for n in (getattr(res, "nodes", None) or []):
                d = n.to_dict() if hasattr(n, "to_dict") else dict(n)
                rows.append({"id": d.get("id"), **dict(d.get("properties") or {})})
    except RuntimeError as e:
        return {"error": f"sibling graph `{key}` unavailable: {e}"}
    chapters: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        ws = r.get("work_structure") or {}
        if not isinstance(ws, dict) or ws.get("kind") != "chapter":
            continue
        work = ws.get("work") if isinstance(ws.get("work"), dict) else {}
        title = str((work or {}).get("title") or "").strip()
        if not title or (work_title and title != work_title):
            continue
        chapters.setdefault(title, []).append({"source_id": str(r.get("id") or ""), "chapter": ws.get("chapter"),
                                               "title": str(ws.get("title") or "").strip(), "file": ws.get("file")})
    # Which chapter units carry a born deliverable at draft or better on THIS graph (a Note's
    # unit rides its Points; its state is the fact) — one pass, shared with the work page.
    notes_by_source = await born_notes_by_unit(gx)
    pages = await work_page_notes(gx)   # the work PAGES (ebb77107), by the work their Collection edge names
    works: List[Dict[str, Any]] = []
    for title in sorted(chapters):
        units = sorted(chapters[title], key=lambda u: (u.get("chapter") is None, u.get("chapter") or 0, u.get("file") or 0))
        missing: List[Dict[str, Any]] = []
        notes: List[Dict[str, Any]] = []
        for u in units:
            ns = notes_by_source.get(u["source_id"]) or []
            notes.extend({**n, "chapter": u.get("chapter"), "unit_title": u.get("title")} for n in ns)
            if not any(n["born"] for n in ns):
                missing.append(u)
        works.append({"work": title, "chapters_total": len(units), "chapters_born": len(units) - len(missing),
                      "condition_met": bool(units) and not missing, "missing": missing, "notes": notes,
                      "units": units, "work_page": list(pages.get(title) or [])})
    if work_title and not works:
        return {"error": f"no chapter units for work {work_title!r} in sibling `{key}`'s structure map",
                "sibling": key, "works": []}
    return {"sibling": key, "works": works}


def render_works_table(
    works: List[Dict[str, Any]],  # work_promotion_status output rows
) -> str:  # A markdown table: work · chapters born · condition · missing
    """Pure: the per-work promotion condition as the staging site's works table (item
    140981e9 (c)) — the reader sees why a work's pages are held before any page is."""
    if not works:
        return "_No works with chapter units in the sibling's structure map._\n"
    lines = ["| Work | Chapters born | Condition | Missing |", "|---|---|---|---|"]
    for w in works:
        miss = ", ".join((f"ch. {m.get('chapter')}" if m.get("chapter") is not None else str(m.get("title") or "?"))
                         for m in (w.get("missing") or []))
        cond = "READY — every chapter born" if w.get("condition_met") else "HELD"
        lines.append(f"| {w.get('work')} | {w.get('chapters_born')} of {w.get('chapters_total')} | {cond} | {miss or '—'} |")
    return "\n".join(lines) + "\n"


async def staging_index(
    gx: GraphHandle,
    *,
    project_root: str,                          # The staging Quarto project root (index.qmd lives here)
    siblings: Optional[Dict[str, str]] = None,  # For the works table (skipped when absent)
    graph_key: Optional[str] = None,            # Sibling key (default: the sole key)
    manifests_dir: Optional[str] = None,        # Capability manifests dir
    write: bool = True,                         # Write <project_root>/_lists/<state>.yml + works.md (else report only)
) -> Dict[str, Any]:  # {project_root, lists: {state: [items]}, counts, works, works_error?, written: [paths]}
    """Project the staging site's LISTINGS from the publish_state facts (item 140981e9 (b)):
    one Quarto listing file per state under <project_root>/lists/<state>.yml — fixtures,
    drafts, reviewed, published and retired pages as SEPARATE lists, so a reader of staging
    never mistakes a fixture for a candidate — plus lists/_works.md, the per-work promotion
    condition table (item 140981e9 (c)). Deterministic from graph state and never journaled:
    the facts are the truth, this is their rendering. A page whose slot holds several active
    values lands in `multi-active.yml` (a supersession gap, shown). Quarto mechanics fixed
    by the first live render: the directory carries NO underscore (Quarto skips `_dirs` when
    resolving listing contents), item paths are RELATIVE TO THE LISTING FILE, and the works
    table is an underscore file (includable via the include shortcode, never rendered as a
    page of its own)."""
    root = Path(project_root)
    lists_dir = root / "lists"
    pred = P.get_predicate(P.PUBLISH_STATE)
    order = list(pred.order_values or ()) + list(pred.terminal_values or ())
    lists: Dict[str, List[Dict[str, Any]]] = {s: [] for s in order}
    lists["multi-active"] = []
    for nid_, vals in (await note_publish_states(gx)).items():
        node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=nid_)
        if node is None or F.label(node) != DevNodeKinds.NOTE:
            continue
        fm = _frontmatter_fields(str(F.prop(node, "frontmatter_raw") or ""))
        path = str(F.prop(node, "path") or "")
        try:
            rel = os.path.relpath(Path(path).resolve(), lists_dir.resolve()) if path else ""
        except ValueError:
            rel = path
        item: Dict[str, Any] = {"path": rel, "title": fm.get("title") or str(F.prop(node, "title") or "")}
        for k in ("date", "description", "categories"):
            if fm.get(k):
                item[k] = fm[k]
        item["publish_state"] = "/".join(vals)
        item["note_id"] = nid_
        lists.setdefault(vals[0] if len(vals) == 1 else "multi-active", []).append(item)
    for items in lists.values():
        items.sort(key=lambda it: (str(it.get("date") or ""), str(it.get("title") or "")), reverse=True)
    out: Dict[str, Any] = {"project_root": str(root), "lists": lists,
                           "counts": {s: len(v) for s, v in lists.items()}, "works": [], "written": []}
    if siblings:
        st = await work_promotion_status(gx, siblings=siblings, graph_key=graph_key, manifests_dir=manifests_dir)
        if st.get("error"):
            out["works_error"] = st["error"]
        out["works"] = list(st.get("works") or [])
    if write:
        lists_dir.mkdir(parents=True, exist_ok=True)
        for state, items in lists.items():
            p = lists_dir / f"{state}.yml"
            p.write_text(yaml.safe_dump(items, allow_unicode=True, sort_keys=False, default_flow_style=False)
                         if items else "[]\n")
            out["written"].append(str(p))
        wp = lists_dir / "_works.md"
        wp.write_text(render_works_table(out["works"]))
        out["written"].append(str(wp))
    return out


def work_page_type(
    actor: str = "agent:session",  # Who mints the profile
) -> DeliverableTypeNode:  # The work-page DeliverableType (ruling a7ca900d (2); item ebb77107)
    """The WORK PAGE profile as data: one page per Source work, the directory index above its
    chapter pages. Its substance is not Points but the work itself — the structure map (units
    in source order), the born chapter notes and their synopsis points, and the work's
    human-added links (Reference nodes on the work's Collection). The body is DERIVED on every
    render: the work card, then the chapters per part — each linked once its page is born,
    followed by its synopsis — the executive summary by construction (no authored summary
    prose). Identity: the Note is linked DERIVED_FROM the work's Collection on the sibling."""
    return DeliverableTypeNode(
        key=WORK_PAGE_KEY,
        title="Work page",
        description=("One page per Source work: the work card (title, subtitle, author, narrator, shape, "
                     "resources) and its chapters per part in source order — each linked once born, with its "
                     "synopsis — the derived executive summary; never authored."),
        information_policy={
            "reads": ["the sibling's structure map for the work (work_structure on its Sources)",
                      "the born chapter notes on this graph and their accepted `synopsis` points",
                      "the work's human-added links: Reference nodes on the work's Collection (sibling)"],
            "identity": ("the Note is linked DERIVED_FROM the work's Collection node on the sibling "
                         "(`link <note> DERIVED_FROM <key>:<collection-id>`); the Collection title is the work title"),
        },
        presentation_policy={
            "renderings": {
                "expanded": {"role": "the public work page (the directory index above the chapter pages)",
                             "card": "work, subtitle, author, narrator, chapters/parts/files, the paraphrase rule, Resources",
                             "chapters": ("one heading per part (front matter first, back matter last); a chapter = its "
                                          "number, its title linked to /posts/<slug>/ once born, an em dash, its synopsis"),
                             "unborn": "the title alone, no link (a public emit never sees one: the page is gated on every chapter)"},
            },
            "public": "expanded",
            "frontmatter": {"title": "notes-on-work",          # "Notes on *<work>*" — the site's series convention
                            "description": "work-summary"},    # derived from the work's subtitle + author
            "source_card": True,
            "matter": "born-only",                             # front/back matter (credits, acknowledgments) lists only once it has a page; "all" lists every unit
            "structure": "front matter · parts in order · back matter, from the structure map's file order",
            "tone": "formal; no first person; nothing of the lane's vocabulary (no states, no counts of what is held)",
            "unit": "one page per Source work; its chapter pages are its members and never repeat the work-level content",
        },
        production_procedure=[
            "new-note --slug <work-slug>: bear the page (draft at birth) with its authored frontmatter + preamble",
            "link <note> DERIVED_FROM <sibling>:<collection-id>: bind the page to its work (the Collection's title = the work title)",
            "assert <note> deliverable_type work-page",
            "add-reference <collection-id> … (transcription core): the work's links live on the Collection, never in the body",
            "notes-render --slug <work-slug>: derive the card + chapters; re-run after any chapter is born or re-rendered",
            "emit-post <note>: gated on publish_state=published AND every chapter page published (the whole work at once)",
        ],
        actor=actor,
    )


async def work_reference_of_note(
    gx: GraphHandle,
    note_id: str,  # The work-page Note id
) -> Optional[Dict[str, Any]]:  # {graph, foreign_id, title, reference_id} for the linked Collection, or None
    """The work a WORK PAGE stands for, read off its edges: the page is linked DERIVED_FROM a
    local Reference observing the work's Collection node on the sibling (ruling 2f8073bb —
    references are edges, never a copied title). The observed display handle IS the work
    title (the transcription core names a Collection by the work). None = not yet linked."""
    for s, t in await F.load_edge_pairs(gx, DevRelations.DERIVED_FROM):
        if s != note_id:
            continue
        node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=t)
        if node is None or F.label(node) != DevNodeKinds.REFERENCE:
            continue
        p = F.props(node)
        if str(p.get("foreign_label") or "") != "Collection":
            continue
        return {"graph": str(p.get("graph") or ""), "foreign_id": str(p.get("foreign_id") or ""),
                "title": str(p.get("name") or "").strip(), "reference_id": t}
    return None


async def read_work_structure(
    sg: GraphHandle,          # The sibling (transcription) graph
    work_title: str,          # The work (the structure map's work.title == the Collection title)
    collection_id: str = "",  # The work's Collection node (carries the work's Reference links)
) -> Dict[str, Any]:  # {work, units: [{source_id, file, kind, chapter, part, part_title, unit, title}], collections: [titles], references: [rows]} | {error}
    """The WORK as the sibling holds it: every Source whose structure map names the work, in
    file order (front matter, the parts' chapters, back matter), the work metadata off the
    first, the Collections those Sources are PART_OF (a book's collection is the work; a
    lecture's is its series), and the work's human-added links (Reference nodes on the
    Collection, `read_source_references`). Read live; the render journals what it observed."""
    q = NodeQuery(label="Source", limit=200000)
    res = await graph_task(sg.queue, sg.graph_id, "query_nodes", query=q.to_dict())
    units: List[Dict[str, Any]] = []
    work: Dict[str, Any] = {}
    for n in (getattr(res, "nodes", None) or []):
        d = n.to_dict() if hasattr(n, "to_dict") else dict(n)
        p = dict(d.get("properties") or {})
        ws = p.get("work_structure") or {}
        w = ws.get("work") if isinstance(ws, dict) and isinstance(ws.get("work"), dict) else {}
        if str((w or {}).get("title") or "").strip() != work_title:
            continue
        for k in ("title", "subtitle", "author", "narrator"):
            if w.get(k) and not work.get(k):
                work[k] = str(w[k]).strip()
        units.append({"source_id": str(d.get("id") or ""), "file": ws.get("file"), "kind": str(ws.get("kind") or ""),
                      "chapter": ws.get("chapter"), "part": ws.get("part"), "part_title": str(ws.get("part_title") or ""),
                      "unit": str(ws.get("unit") or ""), "title": str(ws.get("title") or "").strip()})
    if not units:
        return {"error": f"no Source in the sibling names the work {work_title!r} in its structure map"}
    units.sort(key=lambda u: (u["file"] is None, u["file"] or 0, u["chapter"] is None, u["chapter"] or 0, u["title"]))
    collections: List[str] = []
    eq = EdgeQuery(relation_type="PART_OF", source_ids=[u["source_id"] for u in units], project=["source_id", "target_id"])
    eres = await graph_task(sg.queue, sg.graph_id, "query_edges", query=eq.to_dict())
    for tid in sorted({str(r["target_id"]) for r in (getattr(eres, "rows", None) or [])}):
        node = await graph_task(sg.queue, sg.graph_id, "get_node", node_id=tid)
        title = str(F.prop(node, "title") or "").strip() if node is not None else ""
        if title and title not in collections:
            collections.append(title)
    references = await read_source_references(sg, collection_id) if collection_id else []
    return {"work": work, "units": units, "collections": collections, "references": references}


async def born_notes_by_unit(
    gx: GraphHandle,
) -> Dict[str, List[Dict[str, Any]]]:  # {source id: [{note_id, slug, states, born, synopsis}]} — every typed deliverable, keyed by the unit its Points derive from
    """Which source UNITS carry a born deliverable on this graph, with its state and synopsis:
    a Note's unit rides the points it RENDERS (a set's points count for every Note rendering
    the set; a Note's own for the Note — ruling 96be1528 (P)), its state is the publish_state
    fact — born = draft or better (a fixture, a retired page, or a Note without the fact does
    not count) — and its synopsis is the accepted `synopsis` point's text. One pass over the
    Points; the promotion condition and the work page both read it."""
    live = {P.PUBLISH_DRAFT, P.PUBLISH_REVIEWED, P.PUBLISH_PUBLISHED}
    states = await note_publish_states(gx)
    renders: Dict[str, List[str]] = {}   # set id -> the Notes that RENDER it
    for e in await _edge_rows(gx, EdgeQuery(relation_type=DevRelations.RENDERS)):
        if e.get("source_id") and e.get("target_id"):
            renders.setdefault(str(e["target_id"]), []).append(str(e["source_id"]))
    unit_of_note: Dict[str, Dict[str, Any]] = {}
    synopsis_of_note: Dict[str, str] = {}
    for n in await F.load_label(gx, DevNodeKinds.POINT):
        pr = F.props(n)
        owner = str(pr.get("owner_id") or "")
        if not owner:
            continue
        unit = dict(pr.get("unit") or {})
        for nid_ in renders.get(owner) or [owner]:
            if nid_ not in unit_of_note and unit.get("source_id"):
                unit_of_note[nid_] = unit
            if str(pr.get("kind")) == "synopsis" and nid_ not in synopsis_of_note:
                synopsis_of_note[nid_] = str(pr.get("text") or "").strip()
    out: Dict[str, List[Dict[str, Any]]] = {}
    for nid_, unit in unit_of_note.items():
        st = states.get(nid_) or []
        node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=nid_)
        slug = str(F.prop(node, "slug") or "") if node is not None else ""
        out.setdefault(str(unit["source_id"]), []).append(
            {"note_id": nid_, "slug": slug, "states": st, "born": any(s in live for s in st),
             "synopsis": synopsis_of_note.get(nid_, "")})
    for rows in out.values():
        rows.sort(key=lambda r: (not r["born"], r["slug"]))
    return out


async def work_page_notes(
    gx: GraphHandle,
) -> Dict[str, List[Dict[str, Any]]]:  # {work title: [{note_id, slug, states}]} — the work-page Notes on this graph, by the work their Collection edge names
    """The WORK PAGES on this graph: every Note bound to the work-page type, keyed by the work
    its DERIVED_FROM Collection reference names (`work_reference_of_note`); an unbound page
    keys under "" so the readout shows it."""
    slot = [a for a in await F.load_assertions(gx) if F.prop(a, "predicate") == "deliverable_type"]
    active = F.active_assertions(slot, await F.load_supersedes(gx))
    states = await note_publish_states(gx)
    out: Dict[str, List[Dict[str, Any]]] = {}
    for a in active:
        if str(F.prop(a, "value") or "") != WORK_PAGE_KEY:
            continue
        nid_ = str(F.prop(a, "subject_id") or "")
        ref = await work_reference_of_note(gx, nid_)
        node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=nid_)
        slug = str(F.prop(node, "slug") or "") if node is not None else ""
        out.setdefault(ref["title"] if ref else "", []).append(
            {"note_id": nid_, "slug": slug, "states": states.get(nid_) or []})
    return out


def render_work_card(
    work: Dict[str, Any],                               # {title, subtitle?, author?, narrator?}
    units: List[Dict[str, Any]],                        # read_work_structure units
    collections: Optional[List[str]] = None,            # Collection titles the units are PART_OF
    references: Optional[List[Dict[str, Any]]] = None,  # RESOLVED links [{label, href}]
) -> str:  # A Quarto callout: the work, author/narrator, its shape, the paraphrase rule, Resources; "" without a title
    """The work page's reader-facing card (check 2d01fe1e): the work-level content — never
    repeated on a chapter page. A collection is named only when it is not the work itself (a
    book's collection is the book; a lecture names its series). The Resources line renders
    the work's Reference nodes exactly as a chapter card does — a link with no resolvable
    target is its label alone."""
    title = str(work.get("title") or "").strip()
    if not title:
        return ""
    head = f"Notes on **{title}**"
    if work.get("subtitle"):
        head += f": *{str(work['subtitle']).strip()}*"
    author = str(work.get("author") or "").strip()
    narrator = str(work.get("narrator") or "").strip()
    if author:
        head += f" by {author}"
    if narrator and narrator == author:
        head += " (read by the author)"
    elif narrator:
        head += f", narrated by {narrator}"
    chapters = [u for u in units if u.get("kind") == "chapter"]
    parts = sorted({u.get("part") for u in chapters if u.get("part") is not None}, key=str)
    shape: List[str] = []
    if chapters:
        shape.append(f"{len(chapters)} chapter{'s' if len(chapters) != 1 else ''}"
                     + (f" in {len(parts)} parts" if len(parts) > 1 else ""))
    if units:
        shape.append(f"{len(units)} file{'s' if len(units) != 1 else ''}")
    first = head + (f" — {', '.join(shape)}" if shape else "") + "."
    series = [c.strip() for c in (collections or []) if c.strip() and c.strip() != title]
    if series:
        first += " Part of " + ", ".join(f"*{c}*" for c in series) + "."
    text = ("::: {.callout-note appearance=\"simple\" icon=false}\n" + first
            + " One page per chapter: what the chapter says, in its own order; only the quotations are "
              "verbatim. Each chapter's synopsis is rolled up below.")
    refs = [r for r in (references or []) if str(r.get("label") or "").strip()]
    if refs:
        parts_ = [(f"[{str(r['label']).strip()}]({r['href']})" if str(r.get("href") or "").strip()
                   else str(r["label"]).strip()) for r in refs]
        text += "\n\nResources: " + " · ".join(parts_)
    return text + "\n:::\n"


def _work_group(u: Dict[str, Any]) -> str:  # "Front matter" | "Part n — title" | "Chapters" | "Back matter"
    kind = str(u.get("kind") or "")
    if kind == "front-matter":
        return "Front matter"
    if kind == "back-matter":
        return "Back matter"
    if u.get("part") is not None:
        return f"Part {u['part']}" + (f" — {u['part_title']}" if u.get("part_title") else "")
    return "Chapters"


def render_work_chapters(
    units: List[Dict[str, Any]],       # read_work_structure units, in source order
    born: Dict[str, Dict[str, Any]],   # {source id: {slug, synopsis}} for units with a born page
    *,
    matter: str = "born-only",         # Front/back matter: "born-only" (a credits/acknowledgments unit lists only once it has a page) | "all"
) -> str:  # The `## Chapters` section: one heading per group, a line per unit (linked + synopsis once born); "" without units
    """The TOC that is also the executive summary (checks 2d01fe1e + 34f73e46): the units in
    source order under their part, each chapter numbered, linked to `/posts/<slug>/` once its
    page is born and followed by that page's synopsis; an unborn CHAPTER is its title alone
    (the public emit never sees one — the page is gated on every chapter), while an unborn
    front/back-matter unit (opening credits, acknowledgments, end credits) is apparatus the
    reader has no page for and is left out under the default `matter` policy. Nothing of the
    lane's vocabulary reaches the reader."""
    if matter == "born-only":
        units = [u for u in units if u.get("kind") not in ("front-matter", "back-matter")
                 or str(u.get("source_id") or "") in born]
    if not units:
        return ""
    lines: List[str] = ["## Chapters"]
    group = None
    for u in units:
        g = _work_group(u)
        if g != group:
            lines += ["", f"### {g}", ""]
            group = g
        title = str(u.get("title") or "").strip() or str(u.get("unit") or "").strip() or "Untitled"
        b = born.get(str(u.get("source_id") or ""))
        label = f"[{title}](/posts/{b['slug']}/)" if b and b.get("slug") else title
        syn = f" — {str(b['synopsis']).strip()}" if b and str(b.get("synopsis") or "").strip() else ""
        num = f"{u['chapter']}. " if u.get("kind") == "chapter" and u.get("chapter") is not None else "- "
        lines.append(f"{num}{label}{syn}")
    return "\n".join(lines).rstrip("\n") + "\n"


def _replace_frontmatter_lines(
    fm_raw: str,             # The authored frontmatter block ("---\n…\n---\n")
    want: Dict[str, str],    # {key: value} — the policy-owned lines to replace (or insert in title order)
) -> str:  # The frontmatter with those lines replaced; unchanged when the block is malformed or nothing is wanted
    """Pure: the line surgery shared by every type's frontmatter derivation — replace a top-level
    key's line in place; insert a missing `title` first, a missing `subtitle` after the title
    and a missing `description` after those; keep everything else (date, categories, aliases,
    …) verbatim. Idempotent."""
    if not want or not fm_raw.startswith("---"):
        return fm_raw
    lines = fm_raw.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return fm_raw
    seen: set = set()
    out: List[str] = [lines[0]]
    for ln in lines[1:end]:
        key = ln.split(":", 1)[0].strip() if ":" in ln and not ln.startswith((" ", "\t", "-")) else ""
        if key in want:
            out.append(f"{key}: {json.dumps(want[key], ensure_ascii=False)}")
            seen.add(key)
        else:
            out.append(ln)
    order = ("title", "subtitle", "description")
    for key in order:
        if key in want and key not in seen:
            before = order[:order.index(key)]
            at = max([i + 1 for i, l in enumerate(out) if l.split(":", 1)[0].strip() in before] or [1])
            out.insert(at, f"{key}: {json.dumps(want[key], ensure_ascii=False)}")
    return "\n".join(out + lines[end:])


def derive_work_frontmatter(
    fm_raw: str,                # The authored frontmatter block
    work: Dict[str, Any],       # {title, subtitle?, author?}
    policy: Dict[str, Any],     # presentation_policy["frontmatter"] ({"title": "notes-on-work", "description": "work-summary"})
) -> str:  # The frontmatter with the policy-owned lines replaced
    """The work-page type OWNS the title and description: `notes-on-work` = "Notes on *<work>*"
    (the site's series convention); `work-summary` = one derived sentence from the work's
    subtitle and author. The rest of the authored block (date, categories, aliases) stays."""
    title = str(work.get("title") or "").strip()
    if not title:
        return fm_raw
    want: Dict[str, str] = {}
    if policy.get("title") == "notes-on-work":
        want["title"] = f"Notes on *{title}*"
    if policy.get("description") == "work-summary":
        full = title + (f": {str(work['subtitle']).strip()}" if work.get("subtitle") else "")
        who = f" by {str(work['author']).strip()}" if work.get("author") else ""
        want["description"] = (f"Chapter-by-chapter notes on *{full}*{who} — what each chapter says, in its own "
                               "order, with every chapter's synopsis rolled up on this page.")
    return _replace_frontmatter_lines(fm_raw, want)


async def render_work_page(
    gx: GraphHandle,
    slug: str,                          # The work-page Note's slug
    *,
    write_md: bool = True,              # Write the staging `.md` (replay passes False)
    actor: str = "agent:session",
    siblings: Optional[Dict[str, str]] = None,   # {graph key: db path} — the structure map + the work's links live in the sibling
    manifests_dir: Optional[str] = None,         # Capability manifests dir
    observed: Optional[Dict[str, Any]] = None,   # REPLAY: the journaled sibling observation {work, units, collections, references}; None = read live
) -> Dict[str, Any]:  # {slug, work, units, chapters, chapters_born, born, added, updated, removed, written, text, args} | {error}
    """Derive the WORK PAGE's body and APPLY it (item ebb77107; ruling a7ca900d (2)): the
    authored frontmatter + preamble stay (title/description re-derived per the type), then the
    work card and the chapters — from the sibling's structure map (observed live and JOURNALED
    so replay renders the same page with no sibling open), the born chapter notes and their
    synopsis points on THIS graph (read live, on replay too — they land earlier in the journal),
    and the work's Reference links resolved like a chapter's. The substance digest covers the
    units, the born slugs + synopses and the resolved hrefs, so a re-render after a chapter is
    born lands a new op instead of dedup-ing away. Idempotent: the same state renders the same
    bytes."""
    from .authoring import _note_section_wires
    from .structure import _apply_note_text
    note_id = note_node_id(slug)
    note = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=note_id)
    if note is None:
        return {"error": f"no note `{slug}`", "slug": slug, "written": False}
    ref = await work_reference_of_note(gx, note_id)
    if ref is None:
        return {"error": f"work page `{slug}` is not bound to a work — link it to the work's Collection on the "
                         "sibling first: `link <note> DERIVED_FROM <key>:<collection-id>`", "slug": slug, "written": False}
    if observed is None:
        sibs = dict(siblings or {})
        key = ref["graph"] if ref["graph"] in sibs else (next(iter(sibs)) if len(sibs) == 1 else None)
        if not key:
            return {"error": f"no sibling graph `{ref['graph']}` configured to read the work's structure map "
                             f"(config `sibling_graphs` keys: {sorted(sibs) or 'none'})", "slug": slug, "written": False}
        try:
            async with open_graph(sibs[key], manifests_dir or DEFAULT_MANIFESTS, readonly=True) as sg:
                observed = await read_work_structure(sg, ref["title"], ref["foreign_id"])
        except RuntimeError as e:
            return {"error": f"sibling graph `{key}` unavailable: {e}", "slug": slug, "written": False}
        if observed.get("error"):
            return {"error": observed["error"], "slug": slug, "written": False}
    work = dict(observed.get("work") or {})
    units = list(observed.get("units") or [])
    collections = list(observed.get("collections") or [])
    references = list(observed.get("references") or [])
    by_unit = await born_notes_by_unit(gx)
    born: Dict[str, Dict[str, Any]] = {}
    for u in units:
        rows = [r for r in by_unit.get(str(u.get("source_id") or ""), []) if r.get("born") and r.get("slug")]
        if rows:
            born[str(u["source_id"])] = {"slug": rows[0]["slug"], "synopsis": rows[0].get("synopsis") or "",
                                         "states": rows[0]["states"]}
    resolved = await resolve_references(gx, references)
    tkey = await note_deliverable_type(gx, note_id) or WORK_PAGE_KEY
    tprops = await load_deliverable_type(gx, tkey) or {}
    ppol = dict(tprops.get("presentation_policy") or {})
    fm = derive_work_frontmatter(str(F.prop(note, "frontmatter_raw") or ""), work, dict(ppol.get("frontmatter") or {}))
    pre = ""
    for w in await _note_section_wires(gx, note_id):
        if str(F.props(w).get("anchor")) == "_preamble":
            pre = str(F.props(w).get("raw") or "")
    pre = pre.split(BODY_MARKER, 1)[0]
    if pre and not pre.endswith("\n\n"):
        pre = pre.rstrip("\n") + "\n\n"
    card = render_work_card(work, units, collections, resolved) if ppol.get("source_card", True) else ""
    body = render_work_chapters(units, born, matter=str(ppol.get("matter") or "born-only"))
    new_text = fm + pre + BODY_MARKER + "\n\n" + (card + "\n" if card else "") + body
    path = str(F.prop(note, "path") or "")
    res = await _apply_note_text(gx, note, slug, new_text, path, write=True, write_md=write_md)
    removed = list(res.get("removed") or [])
    if removed:
        from cjm_dev_graph_schema.identity import section_node_id
        await graph_task(gx.queue, gx.graph_id, "delete_nodes",
                         node_ids=[section_node_id(note_id, a) for a in removed], cascade=True)
        res["removed_applied"] = removed
    digest = hashlib.sha256(json.dumps(
        [work, units, collections, [[k, v.get("slug"), v.get("synopsis")] for k, v in sorted(born.items())],
         [[r.get("label"), r.get("href")] for r in resolved]],
        sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
    chapters = [u for u in units if u.get("kind") == "chapter"]
    res.update(work=work.get("title"), units=len(units), chapters=len(chapters),
               chapters_born=sum(1 for u in chapters if str(u["source_id"]) in born),
               born=born, references=resolved, text=new_text,
               args={"slug": slug, "actor": actor, "substance": f"sha256:{digest}",
                     "observed": {"work": work, "units": units, "collections": collections, "references": references}})
    return res


WORK_PAGE_KEY = "work-page"   # the second deliverable type: one page per Source work (ruling a7ca900d (2); item ebb77107)

# The lecture rendering's vocabulary (rulings bc62c727, ba341c72; work item e370e5db). Read at CALL time only.
SECTION_KIND = "section"                # a SYNTHESIZED section (bc62c727 (A)): a Point whose text is the title, anchored at its first covered point
GLOSSARY_KIND = "glossary"              # renders ONLY in the derived closing section, alphabetically
GLOSSARY_HEADING = "Glossary"
BACK_LINK_WORDS = 4                     # a back-link to a point with no lead and no rendered time reads as its opening words
SOURCE_FACT_KEYS = ("series", "lecture_title", "published_at", "recorded_at", "recorded_at_precision")   # the Source facts a lecture page reads live (baa640e8); OUT of the pack digest
STRUCTURE_KINDS = ("synopsis", "section")   # points that carry structure, never coverage
LEAD_PREFIX_KINDS = ("definition", "glossary", "code")   # term-then-gloss kinds: the text may lack the lead
SPEAKER_ROLES = ("presenter", "host", "audience member", "chat")   # the closed per-source role slate (bc62c727 (B1))
OPEN_REF_ROLES = ("refers_to", "parent")   # what a link across a window cut may be (work item 3a2c94eb (3))
