"""The Library's provenance (design 5de7fae9, design leg 4a4ef27e).

A `work` Entity is what was learned from (a book, a course, a lecture series, a talk, a video,
a body of documentation); a `unit` Entity is one part of it, keyed `<work key>/<unit slug>`, its
PART_OF landed by the `entity` op (coverage.mint_entity). Both are minted by the journaled
`entity` op, so a rename, a new form or a moved position is a re-mint, never a schema release.

An ARCHIVE deliverable's provenance is ONE asserted DERIVED_FROM edge to its unit, or to its
work when the work has no units -- the journaled `derived-from` op (`record_provenance`). A
BORN deliverable's provenance is never asserted: it is the rollup of the source points it
places (5de7fae9 (3)), so the verb refuses a born Note.

THE SURVEY (5de7fae9 (5)): the reviewed table lands as one batch. `plan_survey` is pure -- it
reads the rows, checks every one (a work's record agrees across its rows, its rows all name a
unit or none does, unit positions are distinct) and refuses the whole batch on any error; the
CLI then lands the works, the units and the edges through the same write paths as the single
verbs, one journaled op each, so replay needs no survey verb."""

import csv
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.query import EdgeQuery
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import entity_node_id
from cjm_dev_graph_schema.nodes import work_provenance_edge
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from . import factlayer as F
from .coverage import resolve_deliverable, validate_entity
from .runtime import GraphHandle

SURVEY_COLUMNS = ("note_id", "work_key", "work_name", "form", "author", "subtitle", "published", "isbn",
                  "unit_key", "unit_name", "unit_position", "unit_part", "unit_isbn")


def source_entity_id(
    source: str,  # A work key, or a unit key (`<work key>/<unit slug>`)
) -> Tuple[str, str]:  # (entity sub-kind, node id)
    """The Entity a provenance key names: a key carrying the unit separator is a unit's."""
    kind = P.ENTITY_UNIT if P.UNIT_KEY_SEP in source else P.ENTITY_WORK
    return kind, entity_node_id(kind, source)


async def record_provenance(
    gx: GraphHandle,
    deliverable: str,        # The archive deliverable's node id, or a Note's slug
    source: str,             # The work key, or the unit key it derives from
    *,
    retract: bool = False,   # Remove the deliverable's provenance edge
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {edge_id, deliverable_id, source_id, source, replaced, written} | {error, written: False}
    """Write an ARCHIVE deliverable's one DERIVED_FROM edge to its work or unit (journaled
    `derived-from`), or retract it. One per deliverable, so a restatement re-lands it on its
    new target (the journal keeps the history)."""
    from .purenotes import note_types
    did = await resolve_deliverable(gx, deliverable)
    if did is None:
        return {"error": f"no deliverable Note `{deliverable}` (a node id or a post slug)", "written": False}
    edge = work_provenance_edge(did, "")   # the id is the deliverable's alone
    res = await graph_task(gx.queue, gx.graph_id, "query_edges",
                           query=EdgeQuery(source_ids=[did], relation_type=DevRelations.DERIVED_FROM).to_dict())
    raw = getattr(res, "edges", None) or getattr(res, "rows", None) or []
    standing = [e for e in (r.to_dict() if hasattr(r, "to_dict") else dict(r) for r in raw)
                if str(e["id"]) == edge["id"]]
    if retract:
        if standing:
            await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=[edge["id"]])
            return {"edge_id": edge["id"], "deliverable_id": did, "retracted": True, "written": True}
        return {"error": "no provenance edge to retract", "written": False}
    origin = ((await note_types(gx)).get(did) or {}).get("origin")
    if origin != P.ORIGIN_ARCHIVE:
        return {"error": f"`{deliverable}` is not an archive deliverable (origin {origin or 'undeclared'}) -- "
                         "a born deliverable's provenance is the rollup of the points it places", "written": False}
    kind, sid = source_entity_id(source)
    node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=sid)
    if node is None or F.prop(node, "entity_kind") != kind:
        return {"error": f"no {kind} `{source}` -- mint it first (`entity {kind} {source} ...`)", "written": False}
    edge = work_provenance_edge(did, sid)
    if standing:
        if str(standing[0].get("target_id")) == sid:
            return {"edge_id": edge["id"], "deliverable_id": did, "source_id": sid, "source": source,
                    "replaced": False, "unchanged": True, "written": False}
        await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=[edge["id"]])
    await extend_graph(gx.queue, gx.graph_id, [], [edge])
    return {"edge_id": edge["id"], "deliverable_id": did, "source_id": sid, "source": source,
            "replaced": bool(standing), "written": True}


async def record_work_member(
    gx: GraphHandle,
    reference: str,          # A Source or Collection Reference's node id
    target: str = "",        # The unit key (a Source) or the work key (a Source of a unitless work, a Collection)
    *,
    retract: bool = False,   # Remove the Reference's place in its work
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {edge_id, reference_id, target_id, target, replaced, written} | {error, written: False}
    """Place a metabolized source in its work (journaled `work-member`): a Source Reference
    PART_OF its unit, or its work when the work has no units; a Collection Reference PART_OF
    its work, never a unit. One place per Reference, so a restatement re-lands it."""
    from cjm_dev_graph_schema.nodes import work_member_edge
    ref = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=reference)
    label = F.prop(ref, "foreign_label") if ref is not None else None
    if ref is None or F.label(ref) != DevNodeKinds.REFERENCE or label not in ("Source", "Collection"):
        return {"error": f"`{reference}` is no Source or Collection Reference on this graph", "written": False}
    edge = work_member_edge(reference, "")   # the id is the Reference's alone
    res = await graph_task(gx.queue, gx.graph_id, "query_edges",
                           query=EdgeQuery(source_ids=[reference], relation_type="PART_OF").to_dict())
    raw = getattr(res, "edges", None) or getattr(res, "rows", None) or []
    standing = [e for e in (r.to_dict() if hasattr(r, "to_dict") else dict(r) for r in raw)
                if str(e["id"]) == edge["id"]]
    if retract:
        if standing:
            await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=[edge["id"]])
            return {"edge_id": edge["id"], "reference_id": reference, "retracted": True, "written": True}
        return {"error": "no work membership to retract", "written": False}
    kind, tid = source_entity_id(target)
    if label == "Collection" and kind != P.ENTITY_WORK:
        return {"error": "a Collection is PART_OF its work, never a unit", "written": False}
    node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=tid)
    if node is None or F.prop(node, "entity_kind") != kind:
        return {"error": f"no {kind} `{target}` -- mint it first (`entity {kind} {target} ...`)", "written": False}
    edge = work_member_edge(reference, tid)
    if standing:
        if str(standing[0].get("target_id")) == tid:
            return {"edge_id": edge["id"], "reference_id": reference, "target_id": tid, "target": target,
                    "replaced": False, "unchanged": True, "written": False}
        await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=[edge["id"]])
    await extend_graph(gx.queue, gx.graph_id, [], [edge])
    return {"edge_id": edge["id"], "reference_id": reference, "target_id": tid, "target": target,
            "replaced": bool(standing), "written": True}


def read_survey(
    path: str,  # A TSV with the SURVEY_COLUMNS header (extra columns are ignored)
) -> List[Dict[str, str]]:  # The rows, every value stripped
    """Read the reviewed survey table."""
    with Path(path).expanduser().open(newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    return [{k: (v or "").strip() for k, v in r.items() if k} for r in rows]


def _int(value: str) -> Optional[int]:
    try:
        return int(value)
    except ValueError:
        return None


def plan_survey(
    rows: List[Dict[str, str]],  # The survey rows (read_survey)
) -> Dict[str, Any]:  # {works: [{key, name, fields}], units: [...], edges: [{deliverable, source}], errors, ok}
    """The pure plan of a survey batch: the works and units to mint and the provenance edge per
    deliverable, or the errors that refuse the whole batch."""
    errors: List[str] = []
    works: Dict[str, Dict[str, Any]] = {}
    units: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, str]] = []
    with_unit: Dict[str, List[bool]] = {}
    seen: set = set()
    missing = [c for c in ("note_id", "work_key", "work_name", "form") if rows and c not in rows[0]]
    if missing:
        return {"works": [], "units": [], "edges": [], "ok": False,
                "errors": [f"the table has no column(s) {', '.join(missing)}"]}
    for i, r in enumerate(rows, 2):   # line 1 is the header
        where = f"line {i} ({r.get('note_id', '')[:8] or '?'})"
        did, wkey = r.get("note_id", ""), r.get("work_key", "")
        if not did or not wkey:
            errors.append(f"{where}: needs a note_id and a work_key")
            continue
        if did in seen:
            errors.append(f"{where}: the deliverable appears twice")
            continue
        seen.add(did)
        fields: Dict[str, Any] = {f: r[f] for f in ("form", "author", "subtitle", "published", "isbn") if r.get(f)}
        rec = {"key": wkey, "name": r.get("work_name", ""), "fields": fields}
        err = validate_entity(P.ENTITY_WORK, wkey, rec["name"], fields)
        if err:
            errors.append(f"{where}: {err}")
            continue
        prior = works.setdefault(wkey, rec)
        if prior != rec:
            errors.append(f"{where}: work `{wkey}` disagrees with an earlier row "
                          f"({prior['name']!r} {prior['fields']} vs {rec['name']!r} {fields})")
            continue
        source = wkey
        if r.get("unit_key") or r.get("unit_name"):
            ukey = f"{wkey}{P.UNIT_KEY_SEP}{r.get('unit_key', '')}"
            ufields: Dict[str, Any] = {f: r[f"unit_{f}"] for f in ("part", "isbn") if r.get(f"unit_{f}")}
            if _int(r.get("unit_position", "")) is not None:
                ufields["position"] = _int(r["unit_position"])
            urec = {"key": ukey, "name": r.get("unit_name", ""), "fields": ufields}
            err = validate_entity(P.ENTITY_UNIT, ukey, urec["name"], ufields)
            if err:
                errors.append(f"{where}: {err}")
                continue
            if units.setdefault(ukey, urec) != urec:
                errors.append(f"{where}: unit `{ukey}` disagrees with an earlier row")
                continue
            source = ukey
        with_unit.setdefault(wkey, []).append(source != wkey)
        edges.append({"deliverable": did, "source": source})
    for wkey, flags in sorted(with_unit.items()):
        if any(flags) and not all(flags):
            errors.append(f"work `{wkey}`: some rows name a unit and some do not -- a work with units "
                          "takes every deliverable through one")
        elif not any(flags) and len(flags) > 1:
            errors.append(f"work `{wkey}`: {len(flags)} deliverables and no units -- name each one's unit")
    by_pos: Dict[Tuple[str, int], List[str]] = {}
    for u in units.values():
        by_pos.setdefault((u["key"].partition(P.UNIT_KEY_SEP)[0], u["fields"]["position"]), []).append(u["key"])
    errors += [f"work `{w}`: units {', '.join(sorted(ks))} share position {p}"
               for (w, p), ks in sorted(by_pos.items()) if len(ks) > 1]
    return {"works": sorted(works.values(), key=lambda w: w["key"]),
            "units": sorted(units.values(), key=lambda u: (u["key"].partition(P.UNIT_KEY_SEP)[0],
                                                           u["fields"]["position"])),
            "edges": edges, "errors": errors, "ok": not errors}


async def check_survey_targets(
    gx: GraphHandle,
    plan: Dict[str, Any],  # plan_survey's result
) -> List[str]:  # The errors (empty = every deliverable is an archive Note on this graph)
    """The graph half of the survey check, run before anything is written: each row's
    deliverable resolves to a Note whose type is archive-origin, and is resolved to its id."""
    from .purenotes import note_types
    types = await note_types(gx)
    errors = []
    for e in plan["edges"]:
        did = await resolve_deliverable(gx, e["deliverable"])
        if did is None:
            errors.append(f"`{e['deliverable']}`: no deliverable Note on this graph")
        elif (types.get(did) or {}).get("origin") != P.ORIGIN_ARCHIVE:
            errors.append(f"`{e['deliverable']}`: not an archive deliverable "
                          f"(origin {(types.get(did) or {}).get('origin') or 'undeclared'})")
        else:
            e["deliverable"] = did
    return errors


def project_library(
    entities: Dict[str, Dict[str, Dict[str, Any]]],  # load_library_entities: {work | unit | output_class: {key: props + id}}
    provenance: List[Tuple[str, str]],               # Asserted (deliverable, work | unit entity id) DERIVED_FROM pairs
    members: Dict[str, str],                         # {Source / Collection Reference id: the unit | work entity id it is PART_OF}
    born_sources: Dict[str, List[str]],              # {deliverable id: the Source Reference ids its point sets name}
    notes: Dict[str, Dict[str, Any]],                # {Note id: {slug, title, type, kind, origin, output_class, public}}
) -> Dict[str, Any]:  # {works: [...], classes: [...], refusals: [...], ok}
    """The Library as data, deriving everything and storing nothing (design 5de7fae9 (3), leg
    4a4ef27e): each work with its units by position and the outputs of each, an archive output
    through its asserted edge, a born one by the rollup of its point sets' Sources through their
    units. REFUSED, never dropped (6752db0a (9)): a notes-kind deliverable that reaches no work, a
    born source placed in no work, an output whose type carries no output class."""
    by_id: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    for kind in (P.ENTITY_WORK, P.ENTITY_UNIT):
        for e in entities.get(kind, {}).values():
            by_id[e["id"]] = (kind, e)
    classes = sorted(entities.get(P.ENTITY_OUTPUT_CLASS, {}).values(),
                     key=lambda c: (c.get("position") if c.get("position") is not None else 1 << 30, c["key"]))
    rank = {c["key"]: i for i, c in enumerate(classes)}
    works = {e["id"]: {"key": e["key"], "id": e["id"], "name": e.get("name", ""),
                       **{f: e.get(f) for f in ("form", "author", "subtitle", "published", "isbn", "locator") if e.get(f)},
                       "units": [], "outputs": []}
             for e in entities.get(P.ENTITY_WORK, {}).values()}
    units: Dict[str, Dict[str, Any]] = {}
    for e in entities.get(P.ENTITY_UNIT, {}).values():
        wid = entity_node_id(P.ENTITY_WORK, e["key"].partition(P.UNIT_KEY_SEP)[0])
        u = {"key": e["key"], "id": e["id"], "name": e.get("name", ""), "position": e.get("position"),
             **{f: e.get(f) for f in ("part", "isbn") if e.get(f)}, "sources": [], "outputs": []}
        units[e["id"]] = u
        if wid in works:
            works[wid]["units"].append(u)
    for ref, eid in sorted(members.items()):
        if eid in units:
            units[eid]["sources"].append(ref)
        elif eid in works:
            works[eid].setdefault("sources", []).append(ref)
    refusals: List[Dict[str, str]] = []
    placed: Dict[str, set] = {}
    for did, eid in provenance:
        if eid in by_id:
            placed.setdefault(did, set()).add(eid)
    for did, refs in born_sources.items():
        for ref in refs:
            if ref in members:
                placed.setdefault(did, set()).add(members[ref])
            else:
                refusals.append({"deliverable": notes.get(did, {}).get("slug") or did, "reason": "source-unplaced",
                                 "detail": f"its points come from Reference {ref}, which is PART_OF no unit or work"})
    for did, meta in sorted(notes.items(), key=lambda x: str(x[1].get("slug") or x[0])):
        targets = placed.get(did, set())
        if not targets:
            if meta.get("kind") == "notes" and meta.get("output_class") and did not in born_sources:
                refusals.append({"deliverable": meta.get("slug") or did, "reason": "unplaced",
                                 "detail": "a notes deliverable that derives from no work or unit"})
            continue
        if not meta.get("output_class"):
            refusals.append({"deliverable": meta.get("slug") or did, "reason": "classless",
                             "detail": f"its type `{meta.get('type')}` carries no output_class"})
            continue
        out = {"id": did, "slug": meta.get("slug", ""), "title": meta.get("title", ""), "type": meta.get("type"),
               "output_class": meta["output_class"], "origin": meta.get("origin"), "public": bool(meta.get("public"))}
        for eid in sorted(targets):
            (units[eid] if eid in units else works[eid])["outputs"].append(out)
    def _order(outputs):
        return sorted(outputs, key=lambda o: (rank.get(o["output_class"], len(rank)), str(o["title"]).casefold()))
    for w in works.values():
        w["units"].sort(key=lambda u: (u["position"] if u["position"] is not None else 1 << 30, u["key"]))
        w["outputs"] = _order(w["outputs"])
        for u in w["units"]:
            u["outputs"] = _order(u["outputs"])
    return {"works": sorted(works.values(), key=lambda w: (str(w["name"]).casefold(), w["key"])),
            "classes": [{"key": c["key"], "name": c.get("name", ""), "position": c.get("position")} for c in classes],
            "refusals": refusals, "ok": not refusals}


async def library_index(
    gx: GraphHandle,
) -> Dict[str, Any]:  # project_library's result
    """Load what the Library derives from and project it: the Library entities, the asserted
    provenance, each Reference's place in its work, every Note's point-set Sources (the sets it
    RENDERS and the owners of the points it PLACES), and each Note's type, class and public state."""
    from cjm_dev_graph_schema.identity import reference_node_id
    from .purenotes import note_types, public_deliverables
    entities = await load_library_entities(gx)
    lib_ids = {e["id"] for k in (P.ENTITY_WORK, P.ENTITY_UNIT) for e in entities[k].values()}
    provenance = [(s, t) for s, t in await F.load_edge_pairs(gx, DevRelations.DERIVED_FROM) if t in lib_ids]
    refs = {F.nid(r) for r in await F.load_label(gx, DevNodeKinds.REFERENCE)}
    members = {s: t for s, t in await F.load_edge_pairs(gx, "PART_OF") if s in refs and t in lib_ids}
    set_ref: Dict[str, str] = {}
    for ps in await F.load_label(gx, DevNodeKinds.POINT_SET):
        if F.prop(ps, "graph") and F.prop(ps, "source_id"):
            set_ref[F.nid(ps)] = reference_node_id(str(F.prop(ps, "graph")), str(F.prop(ps, "source_id")))
    sets_of: Dict[str, set] = {}
    for note, ps in await F.load_edge_pairs(gx, DevRelations.RENDERS):
        if ps in set_ref:
            sets_of.setdefault(note, set()).add(ps)
    owner = {F.nid(p): str(F.prop(p, "owner_id") or "") for p in await F.load_label(gx, DevNodeKinds.POINT)}
    for point, section in await F.load_edge_pairs(gx, DevRelations.PLACED):
        if owner.get(point) in set_ref and owner.get(section):
            sets_of.setdefault(owner[section], set()).add(owner[point])
    born_sources = {n: sorted({set_ref[s] for s in ss}) for n, ss in sets_of.items()}
    types = {str(F.prop(t, "key")): F.props(t) for t in await F.load_label(gx, DevNodeKinds.DELIVERABLE_TYPE)}
    public = await public_deliverables(gx)
    titles = {F.nid(n): F.props(n) for n in await F.load_label(gx, DevNodeKinds.NOTE)}
    notes = {}
    for nid, t in (await note_types(gx)).items():
        p = titles.get(nid, {})
        notes[nid] = {"slug": p.get("slug") or p.get("name") or "", "title": p.get("title") or p.get("name") or "",
                      "type": t.get("type"), "kind": t.get("kind"), "origin": t.get("origin"),
                      "output_class": str((types.get(t.get("type") or "") or {}).get("output_class") or ""),
                      "public": nid in public}
    return project_library(entities, provenance, members, born_sources, notes)


async def load_library_entities(
    gx: GraphHandle,
) -> Dict[str, Dict[str, Dict[str, Any]]]:  # {work | unit | output_class: {key: properties + id}}
    """Every work, unit and output-class Entity on the graph, keyed by sub-kind then key."""
    kinds = (P.ENTITY_WORK, P.ENTITY_UNIT, P.ENTITY_OUTPUT_CLASS)
    out: Dict[str, Dict[str, Dict[str, Any]]] = {k: {} for k in kinds}
    for n in await F.load_label(gx, DevNodeKinds.ENTITY):
        p = F.props(n)
        if p.get("entity_kind") in out:
            out[p["entity_kind"]][str(p.get("key"))] = {**p, "id": F.nid(n)}
    return out
