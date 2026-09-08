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
    notes-render  -> derive the body from the Points, apply as Sections, write the staging file

Journal shape: `deliverable-type` upserts (last op wins), `accept-point` carries the point AND
its segment observations (replay never opens the sibling), `retract-point` is the compensating
op, `render-notes` replays graph-only and re-derives the same Sections from the same Points.
"""

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.query import EdgeQuery, NodeQuery, OrderBy, PropertyPredicate
from cjm_dev_graph_schema.identity import note_node_id
from cjm_dev_graph_schema.nodes import (DeliverableTypeNode, POINT_KIND_GLOSSES, PointNode,
                                        ReferenceNode)
from cjm_dev_graph_schema.vocab import DevNodeKinds

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
            "structure_strata": ["apparatus"],            # read-aloud headers -> the heading hierarchy (never content)
            "exclude_strata": ["tangent", "sponsor", "disfluency"],
            "never_carry": ["research-mark", "tool-mention", "asr-error"],  # things to DO, not things the source says
        },
        presentation_policy={
            "renderings": {
                "outline": {"role": "scan + review", "line": "one per point", "headings": "derived",
                            "emphasis": "lead-term-only", "timestamps": False},
                "expanded": {"role": "reference", "quotations": "block", "timestamps": True,
                             "anchor": "pt-<key8>", "emphasis": "lead-term-only",
                             "comparison": "table", "step": "ordered-list", "sequence": "ordered-list"},
            },
            "kinds": dict(POINT_KIND_GLOSSES),
            "emphasis": "lead-term-only",
            "section_length_target": None,
            "tone": "formal; fragments by default; no hedging the source did not hedge; no first person; "
                    "no meta-commentary; digits and symbols",
            "structure": "source order (the addressing layer); headings from the structure map + apparatus strata",
            "unit": "one emitted post per chapter unit; the series link carries the work",
        },
        production_procedure=[
            "notes-pack: read the unit's effective spine + strata per the information policy; write the pack",
            "proposer: draft Points with segment runs (kind/from_i/to_i/text/lead) from the pack",
            "notes-ingest: validate rows against the pack; write the proposal set",
            "notes-accept: human confirms per point; each accept = Point + References + edges, journaled",
            "notes-coverage / notes-overlap / notes-check: the graph-read review; notes-retract undoes",
            "notes-render: derive OUTLINE + EXPANDED from the Points; Sections + staging file follow",
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
    An absent policy falls back to the pure-notes defaults when `key` is `pure-notes`."""
    base = pure_notes_type(actor) if key == PURE_NOTES_KEY else DeliverableTypeNode(key=key, actor=actor)
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
    return {"source": {"source_id": source_id, "title": str(sp.get("title") or ""),
                       "work_structure": sp.get("work_structure"), "skeleton_hash": chosen},
            "skeleton_hash": chosen, "segments": segments, "strata": strata,
            "spines": {(h or "legacy"): n for h, n in groups.items()}}


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
     "lead": "<optional lead term>", "attribution": "<quotation only: who is quoted>",
     "data": {<comparison: "columns": [...], "rows": [[...], ...] | sequence: "items": [{"when": "...", "what": "..."}]}}

* `kind`: a kind from the slate above, or a NEW kebab-case kind when none fits (say so in
  the text's lead). Every statement the source makes that a reader returning to these
  notes would want is a point; nothing outside the lines below may enter a point.
* `text`: what the source says, in ITS framing — fragments by default, digits and symbols,
  no hedging the source did not hedge, no first person, no meta-commentary, no
  interpretation, no restating one fact under two kinds. `quotation` text is VERBATIM.
* `from_i`/`to_i`: inclusive pack line numbers (the `[i]` prefixes) the point derives from —
  the smallest run that contains the statement. Two points may share lines; one point
  never spans a header.
* `lead`: the term the reader's eye keys on (a name, a concept); the only emphasis applied.

Rows only — no prose before or after, no code fences.
"""


def render_notes_pack(pack: Dict[str, Any]) -> str:  # The proposer brief (markdown)
    """Render a pack as the brief a proposer reads: the unit, the kind slate, the headers
    the notes will use, the quote spans, the output contract, then the numbered lines with
    `[H]` header rows interleaved. Deterministic for a given pack."""
    src = pack.get("source") or {}
    ws = src.get("work_structure") or {}
    unit_bits = [f"{k}: {ws[k]}" for k in ("kind", "part", "part_title", "chapter", "title") if ws.get(k)]
    lines: List[str] = [
        f"# Notes pack `{pack.get('pack_id')}` — type `{pack.get('type')}`", "",
        f"Source: **{src.get('title') or src.get('source_id')}**  (`{src.get('source_id')}`; "
        f"spine `{(src.get('skeleton_hash') or 'legacy')[-12:]}`)",
        ("Unit: " + " · ".join(unit_bits)) if unit_bits else "Unit: (no structure map on this source)",
        f"{len(pack.get('segments') or [])} content lines · {len(pack.get('headers') or [])} headers · "
        f"{len(pack.get('quote_spans') or [])} quote spans · digest `{pack.get('digest', '')[-12:]}`", "",
        "## Task", "",
        "Read the numbered lines below and propose POINTS: the smallest statements attributable to",
        "the source without interpretation, in source order, each over the run of lines it derives",
        "from. Everything below is main-topic content already (excluded strata are gone); `[H]` rows",
        "are the section headers the notes will render under — never make a point of a header.", "",
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
) -> List[Dict[str, Any]]:  # Normalized rows (kind/from_i/to_i/text/lead/attribution/data)
    """Validate + normalize proposer rows against their pack — loud on the first bad row.
    Enforces the contract: kind token, in-range inclusive run, non-empty text, a run that
    never crosses a header, `comparison` carries columns+rows, `sequence` carries items."""
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
        if segs[fi]["h"] != segs[ti]["h"]:
            raise ValueError(f"row {k}: run {fi}..{ti} crosses a header (a point never spans sections)")
        text = str(raw.get("text") or "").strip()
        if not text:
            raise ValueError(f"row {k}: text is empty")
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        if kind == "comparison" and not (data.get("columns") and data.get("rows")):
            raise ValueError(f"row {k}: a comparison needs data.columns and data.rows")
        if kind == "sequence" and not data.get("items"):
            raise ValueError(f"row {k}: a sequence needs data.items")
        out.append({"kind": kind, "from_i": fi, "to_i": ti, "text": text,
                    "lead": str(raw.get("lead") or "").strip(),
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
            "evidence": {"pack_id": pack.get("pack_id"), "digest": pack.get("digest"),
                         "from_i": r["from_i"], "to_i": r["to_i"]},
        })
    out.sort(key=lambda p: (p["from_i"], p["to_i"]))
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
                     unit=dict(p.get("unit") or {}), actor=actor)


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
    existing = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=node.id)
    nodes: List[Dict[str, Any]] = [] if existing is not None else [node.to_graph_node()]
    changed = False
    if existing is not None:
        new_props = node.to_graph_node()["properties"]
        changed = any(F.prop(existing, k) != new_props.get(k)
                      for k in ("text", "kind", "lead", "attribution", "heading", "data"))
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
    covered = sum(1 for r in pack["segments"] if any(r["id"] in (p.get("segment_ids") or []) for p in points))
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


def _span(p: Dict[str, Any]) -> str:  # (mm:ss–mm:ss) when the point carries times
    if p.get("start_time") is None:
        return ""
    return f" ({_fmt_ts(p.get('start_time'))}–{_fmt_ts(p.get('end_time'))})"


def _lead_text(p: Dict[str, Any]) -> str:  # "**Lead** — text" | "text"
    lead, text = str(p.get("lead") or "").strip(), str(p.get("text") or "").strip()
    return f"**{lead}** — {text}" if lead else text


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


def _render_table(data: Dict[str, Any]) -> List[str]:
    cols = [str(c) for c in (data.get("columns") or [])]
    rows = data.get("rows") or []
    if not cols:
        return []
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for r in rows:
        cells = [str(c) for c in (r if isinstance(r, (list, tuple)) else [r])]
        cells += [""] * (len(cols) - len(cells))
        out.append("| " + " | ".join(cells[:len(cols)]) + " |")
    return out


def render_points(
    points: List[Dict[str, Any]],   # load_points output (source order)
    *,
    rendering: str = "both",        # "outline" | "expanded" | "both"
    outline_title: str = "At a glance",
    body_title: str = "Notes",      # Heading used when the unit has no section headers
) -> str:  # The body markdown (after the preamble)
    """Render the body from the Points — deterministic, so a replayed `render-notes` derives
    the same Sections. OUTLINE: one line per point (lead bold, linked to its anchor), under
    the derived headings. EXPANDED: under the same headings, each point with its anchor and
    timestamps; `quotation` as a block with attribution; consecutive `step`s as ONE ordered
    list; `sequence` as an ordered list of its items; `comparison` as a table. No emphasis
    beyond the lead term; no section length target."""
    pts = sorted(points, key=_sort_key)
    groups: List[Tuple[str, List[Dict[str, Any]]]] = []
    for p in pts:
        h = str(p.get("heading") or "")
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
            for p in ps:
                lines.append(f"- [{_outline_text(p)}](#{_anchor(p)})" if want_expanded else f"- {_outline_text(p)}")
            lines.append("")

    if want_expanded:
        for h, ps in groups:
            lines += [f"## {_heading_text(h) if h else body_title}", ""]
            i = 0
            while i < len(ps):
                p = ps[i]
                kind = str(p.get("kind") or "claim")
                a = _anchor(p)
                if kind == "step":
                    n = 1
                    while i < len(ps) and str(ps[i].get("kind")) == "step":
                        q = ps[i]
                        lines.append(f"{n}. []{{#{_anchor(q)}}} {_lead_text(q)}{_span(q)}")
                        n += 1
                        i += 1
                    lines.append("")
                    continue
                if kind == "quotation":
                    who = str(p.get("attribution") or "").strip()
                    text = str(p.get("text") or "").strip()
                    lines.append(f"[]{{#{a}}}")
                    lines.append("")
                    lines.append(f"> {text}")
                    tail = (f"— {who}" if who else "").strip()
                    lines.append(f">" + (f" {tail}" if tail else "") + _span(p))
                    lines.append("")
                elif kind == "comparison":
                    lines.append(f"[]{{#{a}}} {_lead_text(p)}{_span(p)}")
                    lines.append("")
                    lines += _render_table(dict(p.get("data") or {}))
                    lines.append("")
                elif kind == "sequence":
                    lines.append(f"[]{{#{a}}} {_lead_text(p)}{_span(p)}")
                    lines.append("")
                    for n, it in enumerate((p.get("data") or {}).get("items") or [], start=1):
                        when = str((it or {}).get("when") or "").strip() if isinstance(it, dict) else ""
                        what = str((it or {}).get("what") or it or "").strip() if isinstance(it, dict) else str(it)
                        lines.append(f"{n}. " + (f"**{when}** — {what}" if when else what))
                    lines.append("")
                else:
                    lines.append(f"- []{{#{a}}} {_lead_text(p)}{_span(p)}")
                    # run the plain kinds together as one list
                    if i + 1 >= len(ps) or str(ps[i + 1].get("kind")) in ("step", "quotation", "comparison", "sequence"):
                        lines.append("")
                i += 1
    text = "\n".join(lines).rstrip("\n") + "\n"
    return text


async def render_notes(
    gx: GraphHandle,
    slug: str,                          # The deliverable Note's slug
    *,
    rendering: str = "both",            # "outline" | "expanded" | "both"
    write_md: bool = True,              # Write the staging `.md` (replay passes False)
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {slug, points, added, updated, removed, written, text} | {error}
    """Derive the Note's body from its Points and APPLY it: the authored frontmatter + preamble
    stay, everything after is re-derived, the diff lands as Section adds/updates, and Sections
    the rendering no longer produces are deleted (render owns the body). The staging file is
    rewritten from the same text. Idempotent: the same Points render the same bytes."""
    from .authoring import _note_section_wires
    from .structure import _apply_note_text
    note_id = note_node_id(slug)
    note = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=note_id)
    if note is None:
        return {"error": f"no note `{slug}`", "slug": slug, "written": False}
    if rendering not in ("outline", "expanded", "both"):
        return {"error": f"rendering must be outline | expanded | both, got {rendering!r}", "written": False}
    points = await load_points(gx, note_id)
    wires = await _note_section_wires(gx, note_id)
    pre = ""
    for w in wires:
        if str(F.props(w).get("anchor")) == "_preamble":
            pre = str(F.props(w).get("raw") or "")
    fm = str(F.prop(note, "frontmatter_raw") or "")
    body = render_points(points, rendering=rendering)
    if pre and not pre.endswith("\n\n"):
        pre = pre.rstrip("\n") + "\n\n"
    new_text = fm + pre + body
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
    digest = hashlib.sha256(json.dumps(
        [[p.get("key"), p.get("kind"), p.get("text"), p.get("lead"), p.get("heading"), p.get("data")]
         for p in points], sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
    res.update(points=len(points), rendering=rendering, text=new_text,
               args={"slug": slug, "rendering": rendering, "actor": actor,
                     "substance": f"sha256:{digest}"})
    return res
