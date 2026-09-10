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
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml
from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.query import EdgeQuery, NodeQuery, OrderBy, PropertyPredicate
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import note_node_id, point_node_id
from cjm_dev_graph_schema.nodes import (DeliverableTypeNode, POINT_KIND_GLOSSES, PointNode,
                                        ReferenceNode)
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from . import factlayer as F
from .runtime import DEFAULT_MANIFESTS, GraphHandle, open_graph

PURE_NOTES_KEY = "pure-notes"

NOTES_PACK_FORMAT = "cjm-context-graph-projection/notes-pack"
NOTES_PACK_VERSION = "0.1.0"
NOTES_PROPSET_FORMAT = "cjm-context-graph-projection/notes-proposal-set"
NOTES_PROPSET_VERSION = "0.1.0"

RENDERINGS = ("outline", "expanded")   # the two renderings one substance carries (ruling a7262fe7 (4))
HEADER_MAX_WORDS = 15                  # a structure (apparatus) run longer than this is boilerplate, not a heading


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
            "include_strata": ["quotation"],              # verbatim units, carried as `quotation` points
            "structure_strata": ["section-header"],       # read-aloud section titles -> the heading hierarchy (never content)
            # 2047cf1d ruling (2026-09-09): a cross-reference or a transition is never a heading and never
            # content; apparatus (credits, legal, boilerplate) is excluded outright, no longer a header source
            "exclude_strata": ["tangent", "sponsor", "disfluency", "apparatus", "cross-reference", "transition"],
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
    skeleton: Optional[str] = None,     # Spine selector (see `choose_spine`)
) -> Dict[str, Any]:  # {source, skeleton_hash, segments: [{id,index,text,start,end}], strata: [correction dicts]} | {error}
    """Read one source unit: the EFFECTIVE spine (layer-0 + applied corrections, via the
    correction core's projection — imported lazily, the pull-transcript pattern) and the
    live strata over it. Segments are read by `source_id` + the chosen skeleton, ordered."""
    src = await graph_task(sg.queue, sg.graph_id, "get_node", node_id=source_id)
    if src is None:
        return {"error": f"no Source `{source_id}` in the sibling graph"}
    q = NodeQuery(label="Segment", where=[PropertyPredicate("source_id", "eq", source_id)],
                  order_by=OrderBy(prop="index"),
                  project=["index", "text", "start_time", "end_time", "skeleton_hash"], limit=200000)
    res = await graph_task(sg.queue, sg.graph_id, "query_nodes", query=q.to_dict())
    rows = list(res.rows or [])
    groups: Dict[Optional[str], int] = {}
    for r in rows:
        groups[r.get("skeleton_hash")] = groups.get(r.get("skeleton_hash"), 0) + 1
    try:
        chosen, _legacy = choose_spine(groups, skeleton)
    except ValueError as e:
        return {"error": str(e)}
    rows = [r for r in rows if r.get("skeleton_hash") == chosen]
    rows.sort(key=lambda r: int(r.get("index") or 0))
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
    try:
        from cjm_transcript_correction_core.graph import project_effective_spine
        from cjm_transcript_correction_core.models import SpineSegment
    except ModuleNotFoundError:
        return {"error": "cjm-transcript-correction-core is not installed in this env — it owns the "
                         "effective-spine projection the notes pack reads"}
    segs = [SpineSegment(id=r["id"], index=int(r.get("index") or 0), text=r.get("text") or "",
                         start_time=r.get("start_time"), end_time=r.get("end_time")) for r in rows]
    eff = project_effective_spine(segs, active)
    segments = [{"id": s.id, "index": s.index, "text": s.text,
                 "start": (float(s.start_time) if s.start_time is not None else None),
                 "end": (float(s.end_time) if s.end_time is not None else None)}
                for s in eff if (s.text or "").strip()]
    sp = dict(F.props(src))
    # A public, time-addressable URL (YouTube / a podcast player) makes the source ADDRESSABLE:
    # only then does the public rendering carry timestamps, as links (ruling e1fd4d64 (D)).
    public_url = str(sp.get("public_url") or sp.get("url") or "").strip()
    # Human-added resource links ride the unit snapshot too (item ae103970) — the pack brief
    # shows them and a render with no sibling at hand falls back to this snapshot.
    references = await read_source_references(sg, source_id)
    return {"source": {"source_id": source_id, "title": str(sp.get("title") or ""),
                       "work_structure": sp.get("work_structure"), "skeleton_hash": chosen,
                       **({"public_url": public_url} if public_url else {}),
                       **({"references": references} if references else {})},
            "skeleton_hash": chosen, "segments": segments, "strata": strata,
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
    set records so the read-trace is verifiable, independent of pack id / timestamps."""
    body = {"source": pack.get("source"),
            "headers": [[h["i_before"], h["text"]] for h in pack.get("headers") or []],
            "segments": [[r["i"], r["id"], r["start"], r["end"], r["text"]] for r in pack.get("segments") or []]}
    return "sha256:" + hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def build_notes_pack(
    unit: Dict[str, Any],           # `read_source_unit` output (source / segments / strata)
    type_props: Dict[str, Any],     # The DeliverableType node's properties (the policies)
    *,
    window: Optional[Tuple[float, Optional[float]]] = None,  # (start, end) source seconds; None = whole unit
) -> Dict[str, Any]:  # The pack (JSON-serializable)
    """Apply the type's INFORMATION POLICY (a stratum query) to the unit and number what a
    proposer reads: content lines (unclassified + included strata) 0..n-1, the structure
    strata as HEADERS between lines (never content), quote spans over the numbered lines,
    the kind slate with glosses, and the output contract."""
    info = dict(type_props.get("information_policy") or {})
    exclude = set(info.get("exclude_strata") or [])
    structure = set(info.get("structure_strata") or [])
    include = set(info.get("include_strata") or [])
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
    for s in unit.get("segments") or []:
        if w0 is not None and s["end"] is not None and s["end"] <= w0:
            continue
        if w1 is not None and s["start"] is not None and s["start"] >= w1:
            continue
        cats = by_seg.get(s["id"], [])
        names = {c for c, _ in cats}
        if names & exclude:
            continue
        if names & structure:
            # a read-aloud header: accumulate its run, flush as one header before the next content line
            sid = next((i for c, i in cats if c in structure), None)
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
        rows.append({"i": i, "id": s["id"], "index": s["index"], "start": s["start"], "end": s["end"],
                     "text": s["text"], "h": len(headers)})   # h = count of headers before this line
        for c, cid in cats:
            if c in include:
                quote_runs.setdefault(cid, []).append(i)
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
    kinds = dict((type_props.get("presentation_policy") or {}).get("kinds") or POINT_KIND_GLOSSES)
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
        "segments": rows,
    }
    pack["digest"] = pack_digest(pack)
    return pack


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
    the notes will use, the quote spans, the output contract, then the numbered lines with
    `[H]` header rows interleaved. Deterministic for a given pack."""
    src = pack.get("source") or {}
    ws = src.get("work_structure") or {}
    unit_bits = [f"{k}: {ws[k]}" for k in ("kind", "part", "part_title", "chapter", "title") if ws.get(k)]
    work = dict(ws.get("work") or {})
    work_line = (f"Work: **{work.get('title')}**" + (f" by {work.get('author')}" if work.get("author") else "")
                 + (f" (narrated by {work.get('narrator')})" if work.get("narrator") and work.get("narrator") != work.get("author") else "")
                 + " — name the author by surname where the source says \"I\"; never write \"the author\".")
    lines: List[str] = [
        f"# Notes pack `{pack.get('pack_id')}` — type `{pack.get('type')}`", "",
        f"Source: **{src.get('title') or src.get('source_id')}**  (`{src.get('source_id')}`; "
        f"spine `{(src.get('skeleton_hash') or 'legacy')[-12:]}`)",
        *([work_line] if work.get("title") else []),
        ("Unit: " + " · ".join(unit_bits)) if unit_bits else "Unit: (no structure map on this source)",
        f"{len(pack.get('segments') or [])} content lines · {len(pack.get('headers') or [])} headers · "
        f"{len(pack.get('quote_spans') or [])} quote spans · digest `{pack.get('digest', '')[-12:]}`", "",
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
    lines += ["", OUTPUT_CONTRACT, "## Transcript", ""]
    headers = pack.get("headers") or []
    hi = 0
    for r in pack.get("segments") or []:
        while hi < len(headers) and headers[hi]["i_before"] <= r["i"]:
            lines.append(f"[H] {headers[hi]['text']}")
            hi += 1
        lines.append(f"[{r['i']}] {_fmt_ts(r['start'])}–{_fmt_ts(r['end'])}  {r['text']}")
    while hi < len(headers):
        lines.append(f"[H] {headers[hi]['text']}")
        hi += 1
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
    a `lead` the text CONTAINS (bolded in place — ruling e1fd4d64 (I); `definition` exempt),
    and `parent` = an EARLIER row under the same header that is itself top-level (ONE level
    of nesting — e1fd4d64 (H))."""
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
        text = str(raw.get("text") or "").strip()
        if not text:
            raise ValueError(f"row {k}: text is empty")
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        if kind == "comparison" and not (data.get("columns") and data.get("rows")):
            raise ValueError(f"row {k}: a comparison needs data.columns and data.rows")
        lead = str(raw.get("lead") or "").strip()
        if lead and kind != "definition" and lead.lower() not in text.lower():
            if not lenient_leads:
                raise ValueError(f"row {k}: lead {lead!r} does not appear in the text (a lead is bolded IN PLACE; "
                                 f"only `definition` may carry a lead the text lacks) — fix the row or ingest --lenient")
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
        out.append({"kind": kind, "from_i": fi, "to_i": ti, "text": text, "lead": lead, "parent": parent,
                    "attribution": str(raw.get("attribution") or "").strip(),
                    "data": data})
    return out


def proposals_from_point_rows(
    rows: List[Dict[str, Any]],  # validate_point_rows output
    pack: Dict[str, Any],        # The pack the rows reference
) -> List[Dict[str, Any]]:  # Proposal rows, source order, pack positions resolved to segment identity
    """Resolve validated rows to proposal rows: a minted proposal id (the point's future
    key), the segment ids + times of the run, the header the run falls under, and the
    read-trace (pack id + run)."""
    segs = pack.get("segments") or []
    headers = pack.get("headers") or []
    out: List[Dict[str, Any]] = []
    for r in rows:
        run = segs[r["from_i"]:r["to_i"] + 1]
        starts = [s["start"] for s in run if s.get("start") is not None]
        ends = [s["end"] for s in run if s.get("end") is not None]
        h = int(run[0]["h"])
        parent_row = r.get("parent")
        out.append({
            "proposal_id": str(uuid.uuid4()),
            "kind": r["kind"], "text": r["text"], "lead": r["lead"],
            "attribution": r["attribution"], "data": r["data"],
            "from_i": r["from_i"], "to_i": r["to_i"],
            "segment_ids": [s["id"] for s in run],
            "start_time": (round(min(starts), 3) if starts else None),
            "end_time": (round(max(ends), 3) if ends else None),
            "heading": (headers[h - 1]["text"] if h > 0 and h - 1 < len(headers) else ""),
            "heading_index": h,
            # ONE level of nesting (e1fd4d64 (H)): the parent's proposal id becomes the point's
            # `parent_key` at accept (validated as an earlier, top-level, same-header row).
            "parent_key": (out[parent_row]["proposal_id"] if parent_row is not None else ""),
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
                "files": {"proposals": "proposals.jsonl"}, "counts": counts}
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


# --------------------------------------------------------------------------------------
# Accept / retract (the journaled substance writes)
# --------------------------------------------------------------------------------------


def point_from_args(
    note_id: str,           # The deliverable Note id
    p: Dict[str, Any],      # The journaled point args (key/kind/text/…)
    actor: str = "agent:session",
) -> PointNode:  # The PointNode the accept op describes
    """The op-args -> PointNode mapping the live accept AND replay share."""
    return PointNode(note_id=note_id, key=str(p["key"]), kind=str(p["kind"]), text=str(p["text"]),
                     ordinal=int(p.get("ordinal") or 0), lead=str(p.get("lead") or ""),
                     heading=str(p.get("heading") or ""), heading_index=int(p.get("heading_index") or 0),
                     segment_ids=list(p.get("segment_ids") or []),
                     start_time=p.get("start_time"), end_time=p.get("end_time"),
                     attribution=str(p.get("attribution") or ""), data=dict(p.get("data") or {}),
                     unit=dict(p.get("unit") or {}), parent_key=str(p.get("parent_key") or ""), actor=actor)


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
) -> Dict[str, Any]:  # {point_id, note_id, existing, args, written} | {error}
    """Land ONE accepted point: the Point node, its segment References (from observations —
    live accept observed them a moment ago, replay carries them), `HAS_POINT` from the Note,
    `DERIVED_FROM` to each Reference. Idempotent: deterministic ids make a re-accept a
    verified no-op; a moved observation refreshes the stand-in in place (as `link` does)."""
    note_id = note_node_id(slug)
    note = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=note_id)
    if note is None:
        return {"error": f"no note `{slug}` — birth the deliverable first (new-note --slug {slug} …)",
                "slug": slug, "written": False}
    if len(observations) != len(point.get("segment_ids") or []):
        return {"error": "observations must match segment_ids one-to-one", "slug": slug, "written": False}
    node = point_from_args(note_id, point, actor=str(point.get("actor") or actor))
    if node.parent_key:
        # ONE level of nesting: the parent must already stand (accept order = the set's order,
        # children after their parent); a missing parent is a SKIP the caller reports, not a
        # dangling edge.
        parent = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=node.parent_id)
        if parent is None:
            return {"error": f"parent point `{node.parent_key[:8]}` is not accepted yet (accept it first)",
                    "skippable": True, "slug": slug, "written": False}
        gp_key = str(F.prop(parent, "parent_key") or "")
        if gp_key:
            gp = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=point_node_id(note_id, gp_key))
            if gp is not None and str(F.prop(gp, "parent_key") or ""):
                return {"error": f"parent point `{node.parent_key[:8]}` is already a grandchild — two levels at most",
                        "skippable": True, "slug": slug, "written": False}
    existing = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=node.id)
    nodes: List[Dict[str, Any]] = [] if existing is not None else [node.to_graph_node()]
    changed = False
    if existing is not None:
        new_props = node.to_graph_node()["properties"]
        changed = any(F.prop(existing, k) != new_props.get(k)
                      for k in ("text", "kind", "lead", "attribution", "heading", "data", "parent_key"))
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
    res = await extend_graph(gx.queue, gx.graph_id, nodes, edges)
    args = {"slug": slug, "point": {**point, "key": node.key}, "observations": list(observations),
            "actor": actor, "proposal_set_id": proposal_set_id}
    return {"point_id": node.id, "note_id": note_id, "key": node.key, "kind": node.kind,
            "text": node.text, "existing": existing is not None, "changed": changed,
            "references": ref_ids, "nodes_added": res.nodes_added, "edges_added": res.edges_added,
            "args": args, "written": True}


async def retract_point(
    gx: GraphHandle,
    point_ref: str,                 # The Point id (or unique prefix)
    *,
    actor: str = "user:cli",
) -> Dict[str, Any]:  # {point_id, note_id, key, deleted, args, written} | {error}
    """Retract a point: delete the node (its edges cascade). The compensating op of accept —
    journaled, replayed in append order after the accept it undoes; a missing point is a
    tolerated no-op so a rebuild converges with the point absent."""
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
    pid, note_id, key = F.nid(node), str(F.prop(node, "note_id") or ""), str(F.prop(node, "key") or "")
    await graph_task(gx.queue, gx.graph_id, "delete_nodes", node_ids=[pid], cascade=True)
    return {"point_id": pid, "note_id": note_id, "key": key, "deleted": True, "written": True,
            "args": {"point_id": pid, "slug": "", "key": key, "actor": actor}}


async def retract_note_points(
    gx: GraphHandle,
    slug: str,                      # The deliverable Note's slug
    *,
    actor: str = "user:cli",
) -> Dict[str, Any]:  # {slug, retracted: [retract results], written}
    """Retract EVERY point of a Note (the re-drive's clean slate — ruling e1fd4d64 (5)): one
    `retract_point` per point, children before parents so no ELABORATES edge ever dangles;
    the caller journals one `retract-point` op per result (replay-identical to singles)."""
    points = await load_points(gx, note_node_id(slug))
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


async def edit_point(
    gx: GraphHandle,
    point_ref: str,                     # The Point id (or unique prefix)
    *,
    text: Optional[str] = None,         # New statement text (None = keep)
    lead: Optional[str] = None,         # New lead term ("" clears; None = keep)
    parent: Optional[str] = None,       # New parent: a Point key, id or prefix in the same Note; "" = top level; None = keep
    heading: Optional[str] = None,      # Re-derived section heading (the rehead pass; None = keep)
    heading_index: Optional[int] = None,  # Its order within the unit (None = keep)
    actor: str = "user:cli",
) -> Dict[str, Any]:  # {point_id, note_id, key, changed: {field: [old, new]}, args, written} | {error}
    """Edit an accepted point IN PLACE — the per-point repair the ch. 2 staging read demanded
    (ruling 5625b74e; the lane gap named in b542896b (b) and 5fdeb80c): text, lead, parent.
    Identity is (note, key), so the node, its `pt-` anchor and its References all stand; a
    parent change rewires the ELABORATES edge under the accept-time rules (same Note, an
    accepted parent, depth two at most — a point with children cannot become a grandchild —
    and never a descendant of the point itself). The lead and arrow contracts of ingest hold
    on the edited text. Journaled as `edit-point` with the FIELD SET applied; replay re-applies
    it after the accept it edits (a missing point is a tolerated no-op — the accept may have
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
    pid, note_id, key = F.nid(node), str(F.prop(node, "note_id") or ""), str(F.prop(node, "key") or "")
    cur = {k: F.prop(node, k) for k in ("text", "kind", "lead", "attribution", "heading", "heading_index",
                                        "segment_ids", "start_time", "end_time", "data", "unit",
                                        "parent_key", "ordinal", "actor")}
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
            pnode = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=point_node_id(note_id, parent))
            if pnode is None:
                pr = await resolve_node_ref(gx, parent)
                if "candidates" in pr:
                    return {"error": ambiguity_error(parent, pr["candidates"]), "written": False}
                pnode = pr.get("node")
            if pnode is None or F.label(pnode) != DevNodeKinds.POINT:
                return {"error": f"parent `{parent}` is not an accepted Point", "written": False}
            if str(F.prop(pnode, "note_id") or "") != note_id:
                return {"error": f"parent `{parent[:8]}` belongs to another deliverable", "written": False}
            if F.nid(pnode) == pid:
                return {"error": "a point cannot elaborate itself", "written": False}
            new_parent_key = str(F.prop(pnode, "key") or "")
            # Depth: the parent's ancestry decides the point's depth; its own children ride along.
            gp_key = str(F.prop(pnode, "parent_key") or "")
            if gp_key:
                gp = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=point_node_id(note_id, gp_key))
                if gp is not None and str(F.prop(gp, "parent_key") or ""):
                    return {"error": f"parent `{new_parent_key[:8]}` is already a grandchild — two levels at most",
                            "written": False}
                if gp_key == key:
                    return {"error": f"parent `{new_parent_key[:8]}` is this point's own child (cycle)", "written": False}
            siblings = await load_points(gx, note_id)
            children = [p for p in siblings if str(p.get("parent_key") or "") == key]
            if gp_key and children:
                return {"error": f"`{key[:8]}` has {len(children)} child point(s) — under `{new_parent_key[:8]}` they would "
                                 f"sit at depth three; re-parent them first", "written": False}
            if any(str(p.get("parent_key") or "") == key and str(p.get("key")) == new_parent_key for p in siblings):
                return {"error": f"parent `{new_parent_key[:8]}` is this point's own child (cycle)", "written": False}
        if new_parent_key != old_parent_key:
            fields["parent_key"] = new_parent_key
    if not fields:
        return {"point_id": pid, "note_id": note_id, "key": key, "changed": {}, "written": False,
                "args": {"point_id": pid, "key": key, "fields": {}, "actor": actor}}
    merged = {**{k: v for k, v in cur.items() if v is not None}, **fields, "key": key}
    pn = point_from_args(note_id, merged, actor=str(cur.get("actor") or actor))
    props = pn.to_graph_node()["properties"]
    props.update(fields)   # update_node MERGES: a cleared lead / parent_key lands as "" explicitly, never by absence
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
    changed = {k: [cur.get(k) if cur.get(k) is not None else "", v] for k, v in fields.items()}
    return {"point_id": pid, "note_id": note_id, "key": key, "changed": changed, "text": pn.text, "written": True,
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


# --------------------------------------------------------------------------------------
# Reads: the review verbs
# --------------------------------------------------------------------------------------


def _sort_key(p: Dict[str, Any]) -> Tuple[float, int, str]:
    st = p.get("start_time")
    return (float(st) if st is not None else float("inf"), int(p.get("ordinal") or 0), str(p.get("key") or ""))


async def load_points(
    gx: GraphHandle,
    note_id: str,  # The deliverable Note
) -> List[Dict[str, Any]]:  # Point property dicts (+ id), source order
    """A Note's Points, in source order (start_time, then pack ordinal, then key)."""
    out: List[Dict[str, Any]] = []
    for n in await F.load_label_where(gx, DevNodeKinds.POINT, [PropertyPredicate("note_id", "eq", note_id)]):
        d = dict(F.props(n))
        d["id"] = F.nid(n)
        out.append(d)
    out.sort(key=_sort_key)
    return out


def overlapping_points(
    points: List[Dict[str, Any]],  # load_points output
) -> List[Dict[str, Any]]:  # [{a, b, shared: [segment ids]}] — pairs whose segment runs intersect
    """Pure: the duplication candidates — two points deriving from a shared segment. A
    `quotation` beside a `claim` over the same run is expected; two claims are the flag."""
    out: List[Dict[str, Any]] = []
    points = [p for p in points if str(p.get("kind")) != "synopsis"]   # the unit-spanning synopsis overlaps everything by design
    for i, a in enumerate(points):
        sa = set(a.get("segment_ids") or [])
        for b in points[i + 1:]:
            shared = sorted(sa & set(b.get("segment_ids") or []))
            if shared:
                out.append({"a": {"id": a["id"], "kind": a.get("kind"), "text": a.get("text")},
                            "b": {"id": b["id"], "kind": b.get("kind"), "text": b.get("text")},
                            "shared": shared,
                            "same_kind": a.get("kind") == b.get("kind")})
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
        if str(p.get("kind")) == "synopsis":
            continue   # spans the unit by design; it never counts as coverage
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
    pack = build_notes_pack(read, tprops)
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


def _span(p: Dict[str, Any], timestamps: str = "addressable") -> str:  # " (mm:ss–mm:ss)" | " [(mm:ss–mm:ss)](url)" | ""
    """The source span: `always` = plain; `addressable` = ONLY when the unit carries a public
    time-addressable URL, rendered as a LINK into it (ruling e1fd4d64 (D) — an audiobook span
    resolves against nobody else's file split); `never` = none. Review verbs show spans
    regardless — this governs the rendered post."""
    if p.get("start_time") is None or timestamps == "never":
        return ""
    text = f"({_fmt_ts(p.get('start_time'))}–{_fmt_ts(p.get('end_time'))})"
    if timestamps == "always":
        return " " + text
    url = str((p.get("unit") or {}).get("public_url") or "").strip()
    if not url:
        return ""
    return f" [{text}]({_time_link(url, float(p.get('start_time') or 0.0))})"


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


def _tail(p: Dict[str, Any], timestamps: str) -> str:  # " (span) [§](#pt-x){…}"
    return f"{_span(p, timestamps)} {_anchor_link(p)}"


def _render_node(
    node: Dict[str, Any],   # {"p", "kids"} from build_point_tree
    indent: str,            # the item's own indent ("" at top level); children indent to the item's CONTENT column
    timestamps: str,
) -> List[str]:  # markdown lines for the point and its subtree
    """One point as a list item at `indent`, its children beneath it. Block kinds keep their
    shapes at every depth: a `quotation` is a `>` block (blank-line-separated, indented to
    the item's content column — ruling (5)); a `sequence` lists its `event` children as an
    ordered list (legacy `data.items` still renders); a `comparison` carries its table inside
    the item; everything else is a bullet whose lead is bolded in place. Indents are the
    CONTENT column of the enclosing item ("- " = 2, "1. " = 3), the only way Pandoc keeps a
    nested block inside the item."""
    p, kids = node["p"], node["kids"]
    kind = str(p.get("kind") or "claim")
    sub = indent + "  "          # content column of a "- " item
    out: List[str] = []
    if kind == "quotation":
        who = str(p.get("attribution") or "").strip()
        text = str(p.get("text") or "").strip()
        if indent:
            out.append("")
        out.append(f"{indent}> {text}")
        out.append(f"{indent}>" + (f" — {who}" if who else "") + _tail(p, timestamps))
        out.append("")
        for k in kids:
            out += _render_node(k, indent, timestamps)   # a quotation's support sits at the quotation's own indent
        return out
    if kind == "sequence":
        out.append(f"{indent}- {_lead_text(p)}{_tail(p, timestamps)}")
        events = [k for k in kids if str(k["p"].get("kind")) == "event"]
        others = [k for k in kids if str(k["p"].get("kind")) != "event"]
        n = 1
        for ev in events:
            q = ev["p"]
            when = str((q.get("data") or {}).get("when") or "").strip()
            out.append(f"{sub}{n}. " + (f"**{when}** — " if when else "") + f"{_lead_text(q)}{_tail(q, timestamps)}")
            for g in ev["kids"]:
                out += _render_node(g, sub + "   ", timestamps)   # the ordered item's content column
            n += 1
        if not events:
            out += _sequence_items(p, sub)
        for k in others:
            out += _render_node(k, sub, timestamps)
        return out
    if kind == "comparison":
        out.append(f"{indent}- {_lead_text(p)}{_tail(p, timestamps)}")
        out.append("")
        out += _render_table(dict(p.get("data") or {}), sub)
        out.append("")
        for k in kids:
            out += _render_node(k, sub, timestamps)
        return out
    if kind == "event":
        when = str((p.get("data") or {}).get("when") or "").strip()
        out.append(f"{indent}- " + (f"**{when}** — " if when else "") + f"{_lead_text(p)}{_tail(p, timestamps)}")
    else:
        out.append(f"{indent}- {_lead_text(p)}{_tail(p, timestamps)}")
    for k in kids:
        out += _render_node(k, sub, timestamps)
    return out


def render_points(
    points: List[Dict[str, Any]],   # load_points output (source order)
    *,
    rendering: str = "expanded",    # "outline" | "expanded" | "both"
    timestamps: str = "addressable",  # "always" | "addressable" | "never" (see _span)
    outline_title: str = "At a glance",
) -> str:  # The body markdown (after the preamble)
    """Render the body from the Points — deterministic, so a replayed `render-notes` derives
    the same Sections. EXPANDED (the public post): under the derived headings — a header
    that restates the unit's own title is suppressed (e1fd4d64 (C)) — each root point with
    its permalink glyph and its subtree (depth two in practice), spans only when the source
    is addressable; block kinds keep their shapes at every depth; consecutive top-level
    `step`s are ONE ordered list; the `synopsis` never renders in the body (it is the
    description). OUTLINE (review / the work page): one line per point, same headings."""
    pts = [p for p in sorted(points, key=_sort_key) if str(p.get("kind")) != "synopsis"]
    unit = dict((pts[0].get("unit") or {}) if pts else {})
    groups: List[Tuple[str, List[Dict[str, Any]]]] = []
    for p in pts:
        h = str(p.get("heading") or "")
        if unit_title_header(h, unit):
            h = ""   # the unit's own title, not a section — wherever it occurs, so its points stay ONE group
        if groups and groups[-1][0] == h:
            groups[-1][1].append(p)
        else:
            groups.append((h, [p]))
    lines: List[str] = []
    want_outline = rendering in ("outline", "both")
    want_expanded = rendering in ("expanded", "both")

    if want_outline:
        lines += [f"## {outline_title}", ""]
        for h, ps in groups:
            if h:
                lines += [f"**{_heading_text(h)}**", ""]

            def _walk(node: Dict[str, Any], depth: int) -> None:
                q = node["p"]
                ind = "  " * depth
                lines.append(f"{ind}- [{_outline_text(q)}](#{_anchor(q)})" if want_expanded else f"{ind}- {_outline_text(q)}")
                for k in node["kids"]:
                    _walk(k, depth + 1)
            for n in build_point_tree(ps):
                _walk(n, 0)
            lines.append("")

    if want_expanded:
        block_kinds = ("step", "quotation")
        for h, ps in groups:
            if h:
                lines += [f"## {_heading_text(h)}", ""]
            # a unit with no section headers renders its points with no heading at all (a lone
            # "## Notes" says nothing to a reader; the source card already names the unit)
            tree = build_point_tree(ps)
            i = 0
            while i < len(tree):
                node = tree[i]
                kind = str(node["p"].get("kind") or "claim")
                if kind == "step":
                    n = 1
                    while i < len(tree) and str(tree[i]["p"].get("kind")) == "step":
                        q = tree[i]["p"]
                        lines.append(f"{n}. {_lead_text(q)}{_tail(q, timestamps)}")
                        for k in tree[i]["kids"]:
                            lines += _render_node(k, "   ", timestamps)   # the "1. " content column
                        n += 1
                        i += 1
                    lines.append("")
                    continue
                lines += _render_node(node, "", timestamps)
                nxt = str(tree[i + 1]["p"].get("kind")) if i + 1 < len(tree) else None
                if kind != "quotation" and (nxt is None or nxt in block_kinds):
                    lines.append("")   # close the bullet run before a block or the section end
                i += 1
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


def render_source_card(
    unit: Dict[str, Any],  # A point's `unit` (carries work_structure incl. `work`)
    references: Optional[List[Dict[str, Any]]] = None,  # RESOLVED human-added links [{label, href}] (ae103970); none = no Resources line
) -> str:  # A Quarto callout naming the work, author, and unit; "" without work metadata
    """The reader-facing provenance (second-read ruling (1)): a derived one-line callout under
    the title so the post is never mistaken for the source — the work, its author, the
    part/chapter, and the paraphrase/verbatim rule. Nothing of the lane's vocabulary. The
    Source's human-added resource links (ruling a7ca900d (3)) render as a `Resources:` line
    INSIDE the card — derived from Reference nodes, never authored into the body; a link
    with no resolvable target renders as its label alone (never an empty link)."""
    ws = dict((unit or {}).get("work_structure") or {})
    work = dict(ws.get("work") or {})
    if not work.get("title"):
        return ""
    who = f" by {work['author']}" if work.get("author") else ""
    where_bits: List[str] = []
    if ws.get("part") is not None:
        where_bits.append(f"Part {ws['part']}" + (f" ({ws['part_title']})" if ws.get("part_title") else ""))
    if ws.get("kind") == "chapter" and ws.get("chapter") is not None:
        where_bits.append(f"Chapter {ws['chapter']}")
    where = " · ".join(where_bits)
    title = str(ws.get("title") or "").strip()
    place = (f" — {where}" if where else "") + (f", *{title}*" if title else "")
    refs = [r for r in (references or []) if str(r.get("label") or "").strip()]
    resources = ""
    if refs:
        parts = [(f"[{str(r['label']).strip()}]({r['href']})" if str(r.get("href") or "").strip()
                  else str(r["label"]).strip()) for r in refs]
        resources = "\n\nResources: " + " · ".join(parts)
    return ("::: {.callout-note appearance=\"simple\" icon=false}\n"
            f"Notes on **{work['title']}**{who}{place}. The points paraphrase the {ws.get('kind') or 'source'} "
            "in its own order; only the quotations are verbatim." + resources + "\n:::\n")


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
        h = _heading_text(str(p.get("heading") or ""))
        if h and h not in heads and not (not heads and unit_title_header(h, unit)):
            heads.append(h)
    work = str((ws.get("work") or {}).get("title") or "").strip() if isinstance(ws.get("work"), dict) else ""
    lead = (f"{work} — " if work else "") + (title or "Notes")
    body = (": " + " · ".join(heads)) if heads else ""
    return f"{lead}{body}. What the {kind} says, in its own order, without added commentary."


def derive_frontmatter(
    fm_raw: str,                    # The authored frontmatter block ("---\\n…\\n---\\n")
    points: List[Dict[str, Any]],   # load_points output
    policy: Dict[str, Any],         # presentation_policy["frontmatter"] ({"title": "work-unit-notes" | "unit-title", "description": "synopsis" | "derived"})
    *,
    synopsis: str = "",             # The accepted synopsis point's text (policy "synopsis"; derived headings as fallback)
) -> str:  # The frontmatter with the policy-owned lines replaced (or inserted after title)
    """The type may OWN the title and the description — the rest of the authored frontmatter
    (date, categories, …) stays. Title `work-unit-notes` (second-read ruling (1), the short
    shape) = "<work>, Ch. n notes", falling back to the unit title without work metadata;
    `unit-title` = the unit title alone. Description `synopsis` = the accepted synopsis
    point (ruling (2)), falling back to the derived headings; `derived` = the headings.
    Idempotent: a re-derive over derived lines yields the same bytes."""
    if not fm_raw.startswith("---") or not points:
        return fm_raw
    pts = sorted(points, key=_sort_key)
    unit = dict(pts[0].get("unit") or {})
    ws = dict(unit.get("work_structure") or {})
    work = dict(ws.get("work") or {}) if isinstance(ws.get("work"), dict) else {}
    want: Dict[str, str] = {}
    tpol = policy.get("title")
    if tpol == "work-unit-notes" and str(work.get("title") or "").strip():
        lab = unit_label(unit)
        want["title"] = f"{work['title']}, {lab} notes" if lab else f"{work['title']} notes"
    elif tpol in ("unit-title", "work-unit-notes") and str(ws.get("title") or "").strip():
        want["title"] = str(ws.get("title")).strip()
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
    siblings: Optional[Dict[str, str]] = None,        # {graph key: db path} — read the unit's LIVE Reference nodes from the sibling (ae103970)
    graph_key: Optional[str] = None,                  # Sibling key (default: the points' unit graph, else the sole key)
    manifests_dir: Optional[str] = None,              # Capability manifests dir
    references: Optional[List[Dict[str, Any]]] = None,  # Raw Reference rows to render (REPLAY passes the journaled ones; None = read live, else the unit snapshot)
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
    the RESOLVED hrefs so a re-render after a cross-work target is born lands a new op."""
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
    syn = synopsis_of(points)
    fm = derive_frontmatter(str(F.prop(note, "frontmatter_raw") or ""), points, fm_policy, synopsis=syn)
    card = ""
    resolved: List[Dict[str, Any]] = []
    if ppol.get("source_card", True) and points:
        # The reader-facing provenance (second-read ruling (1)) — derived from the unit's work
        # metadata, rendered above the body, never authored. Its Resources line (ae103970):
        # explicit rows (replay) > the sibling's LIVE Reference nodes > the unit snapshot.
        unit0 = dict(sorted(points, key=_sort_key)[0].get("unit") or {})
        if references is None:
            key = graph_key or str(unit0.get("graph") or "")
            if siblings and (key in siblings or len(siblings) == 1) and unit0.get("source_id"):
                key = key if key in siblings else next(iter(siblings))
                try:
                    async with open_graph(siblings[key], manifests_dir or DEFAULT_MANIFESTS, readonly=True) as sg:
                        references = await read_source_references(sg, str(unit0["source_id"]))
                except RuntimeError:
                    references = None   # sibling unavailable -> the snapshot below
            if references is None:
                references = list(unit0.get("references") or [])
        resolved = await resolve_references(gx, references)
        card = render_source_card(unit0, resolved)
    body = render_points(points, rendering=rendering, timestamps=timestamps)
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
    digest = hashlib.sha256(json.dumps(
        [[[p.get("key"), p.get("kind"), p.get("text"), p.get("lead"), p.get("heading"), p.get("data"),
           p.get("parent_key")] for p in points],
         [[r.get("label"), r.get("href")] for r in resolved]],
        sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
    res.update(points=len(points), rendering=rendering, timestamps=timestamps, text=new_text,
               references=resolved,
               args={"slug": slug, "rendering": rendering, "timestamps": timestamps, "actor": actor,
                     "substance": f"sha256:{digest}",
                     **({"references": [dict(r) for r in references]} if references else {})})
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
    a Note's unit rides its Points (the first point's unit names the Source), its state is the
    publish_state fact — born = draft or better (a fixture, a retired page, or a Note without
    the fact does not count) — and its synopsis is the accepted `synopsis` point's text. One
    pass over the Points; the promotion condition and the work page both read it."""
    live = {P.PUBLISH_DRAFT, P.PUBLISH_REVIEWED, P.PUBLISH_PUBLISHED}
    states = await note_publish_states(gx)
    unit_of_note: Dict[str, Dict[str, Any]] = {}
    synopsis_of_note: Dict[str, str] = {}
    for n in await F.load_label(gx, DevNodeKinds.POINT):
        pr = F.props(n)
        nid_ = str(pr.get("note_id") or "")
        if not nid_:
            continue
        unit = dict(pr.get("unit") or {})
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
    want: Dict[str, str],    # {key: value} — the policy-owned lines to replace (or insert after title)
) -> str:  # The frontmatter with those lines replaced; unchanged when the block is malformed or nothing is wanted
    """Pure: the line surgery shared by every type's frontmatter derivation — replace a top-level
    key's line in place, insert a missing `title` first and a missing `description` right after
    the title, keep everything else (date, categories, aliases, …) verbatim. Idempotent."""
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
    for key in ("title", "description"):
        if key in want and key not in seen:
            at = next((i for i, l in enumerate(out) if l.startswith("title:")), 0) + 1 if key == "description" else 1
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
