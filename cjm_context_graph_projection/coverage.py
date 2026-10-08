"""The Tutorials matrix: its vocabulary as graph data and the task x stage projection
(designs 8cbdc883 / c450133a under the coverage model de808eae (1)).

Each task and each stage is an Entity (sub-kind `task` / `stage`) minted by the journaled
`entity` op -- a WHOLE record upserted by (kind, key), last op wins -- so the axes change by
journaled ops (add, rename, reorder, retire), never by a schema release. A deliverable says
what it TEACHES with the multivalued `teaches_task` / `teaches_stage` facts, whose values are
vocabulary keys (checked at write time by `check_coverage_value`).

The HARDWARE (8cbdc883 (7), c450133a (2)) rides the same grammar: one `hardware` Entity per
compute device, its `verification_standing` a fact with history, and a VERIFIED_ON edge per
(deliverable, device, os) carrying the evidence -- written by the journaled `verified-on` op.
The standing is the page's FILTER and the gate on claims, never a weight: `coverage` with a
hardware filter narrows the cells to what was verified there, and refusals stay whole.

The CLAIMS (amendment 98e99fe5) declare their Entity kind here too; their state, the SUPPORTS
edges and the backing floor live in claims.py.

The CATEGORY FACETS' vocabularies (design 0f7fcdcb, amendment 3c5cff97) -- tool, subject and
model -- declare their kinds here as well: each entry needs a description and a not-for line,
the facet judge's criteria. `mint_entities` lands a vocabulary as one batch, checked whole.

`coverage_matrix` derives everything the page and the gap list read, and stores nothing: the
rows (tasks by position, the off-grid ones listed apart), the columns (stages by position),
each cell's tutorials, the cells a CROSS-TASK stage leaves covered by the cross-task row, the
gaps, and the REFUSALS -- a tutorial with no task fact is unsurveyed, a value naming no live
vocabulary entry is unknown, an on-grid task with no stage is unstaged; each is refused
rather than silently dropped from the page (6752db0a (9))."""

from typing import Any, Dict, List, Optional, Tuple

from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.query import EdgeQuery
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import entity_node_id
from cjm_dev_graph_schema.nodes import EntityNode, unit_part_of_edge, verified_on_edge
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from . import factlayer as F
from .runtime import GraphHandle

# The fields each declared Entity sub-kind carries beyond its name (the journaled record):
# field -> its type. A kind absent here is refused, so a new kind is declared before use.
ENTITY_FIELDS: Dict[str, Dict[str, type]] = {
    # a task / stage is a facet too (its category page's page_description, amendment e38d403c (1))
    P.ENTITY_TASK: {"description": str, "position": int, "cross_task": bool, "off_grid": bool,
                    "retired": bool, "page_description": str, "page_description_basis": str},
    # a stage's transitions are data over the artifact kinds (design ae698640 (2)); see paths.py
    P.ENTITY_STAGE: {"description": str, "position": int, "cross_task": bool, "retired": bool,
                     "page_description": str, "page_description_basis": str, "transitions": list},
    P.ENTITY_HARDWARE: {"description": str, "device_class": str},
    P.ENTITY_CLAIM: {"statement": str, "position": int},   # amendment 98e99fe5 (1); see claims.py
    # The Library (design leg 4a4ef27e); see library.py
    P.ENTITY_WORK: {"form": str, "author": str, "subtitle": str, "published": str, "isbn": str,
                    "locator": str},
    P.ENTITY_UNIT: {"position": int, "part": str, "isbn": str},
    P.ENTITY_OUTPUT_CLASS: {"description": str, "position": int},
    # A web deliverable's profile (design 9a7224a7 (3)); its design system is a STYLED_BY edge,
    # its light / dark override two facts. The design_system kind is NOT here: the artifact
    # fold derives it, so the entity verb refuses it.
    P.ENTITY_SITE_PROFILE: {"description": str},
    # The category facets' vocabularies (amendment 3c5cff97): the facet judge's criteria, and
    # page_description, the reader-facing text of the entry's category page (amendment e38d403c (1)),
    # never part of the criteria, and page_description_basis, the criteria hash it was written
    # against (a criteria change re-surfaces it for review)
    **{k: {"description": str, "not_for": str, "retired": bool, "page_description": str, "page_description_basis": str} for k in P.FACET_KINDS},
    # The path model (design ae698640 (1)-(4), ad9bef5a (1)); the references a record makes are
    # checked live and its edges landed by paths.py
    P.ENTITY_ARTIFACT_KIND: {"description": str, "position": int, "retired": bool},
    P.ENTITY_ARTIFACT: {"description": str, "artifact_kind": str, "task": str, "base_model": str,
                        "precision": str, "format": str, "target": str, "license": str, "locator": str,
                        "derived_from": list, "retired": bool},
    P.ENTITY_ENVIRONMENT: {"description": str, "parts": list, "requires": list, "variants": list,
                           "retired": bool},
    P.ENTITY_CONCEPT: {"description": str, "not_for": str, "subject": str, "retired": bool},
}
_REQUIRED = {P.ENTITY_TASK: ("position",), P.ENTITY_STAGE: ("position",),
             P.ENTITY_HARDWARE: ("device_class",), P.ENTITY_CLAIM: ("statement", "position"),
             P.ENTITY_WORK: ("form",), P.ENTITY_UNIT: ("position",),
             P.ENTITY_OUTPUT_CLASS: ("position",),
             **{k: ("description", "not_for") for k in P.FACET_KINDS},
             P.ENTITY_ARTIFACT_KIND: ("description",), P.ENTITY_ARTIFACT: ("artifact_kind",),
             P.ENTITY_ENVIRONMENT: ("description",), P.ENTITY_CONCEPT: ("description", "not_for", "subject")}
_ALLOWED = {(P.ENTITY_HARDWARE, "device_class"): P.DEVICE_CLASSES,   # closed slates on a field
            (P.ENTITY_WORK, "form"): P.WORK_FORMS}
TUTORIAL_KIND = "tutorial"   # the navigation kind (predicates.DELIVERABLE_KINDS) the matrix reads


def validate_entity(
    kind: str,                 # The Entity sub-kind
    key: str,                  # The durable key
    name: str,                 # The display name
    fields: Dict[str, Any],    # The kind's extra fields
) -> Optional[str]:  # An error, or None
    """Check one `entity` record against its kind's declared fields."""
    spec = ENTITY_FIELDS.get(kind)
    if spec is None:
        return f"entity kind `{kind}` is not declared (known: {', '.join(sorted(ENTITY_FIELDS))})"
    if not key or key != key.strip() or " " in key:
        return f"an entity key is a non-empty slug without spaces (got {key!r})"
    if not name:
        return "an entity needs a name"
    if kind == P.ENTITY_UNIT:   # the work is part of a unit's identity (4a4ef27e (2))
        work, sep, unit = key.partition(P.UNIT_KEY_SEP)
        if not (work and sep and unit) or P.UNIT_KEY_SEP in unit:
            return f"a unit key is `<work key>{P.UNIT_KEY_SEP}<unit slug>` (got {key!r})"
    elif kind == P.ENTITY_WORK and P.UNIT_KEY_SEP in key:
        return f"a work key carries no `{P.UNIT_KEY_SEP}` -- only its units' keys do (got {key!r})"
    elif kind == P.ENTITY_SITE_PROFILE:   # the site is part of a profile's identity (9a7224a7 (3))
        site, sep, profile = key.partition(P.PROFILE_KEY_SEP)
        if not (site and sep and profile) or P.PROFILE_KEY_SEP in profile:
            return f"a site_profile key is `<site>{P.PROFILE_KEY_SEP}<profile>` (got {key!r})"
    unknown = sorted(set(fields) - set(spec))
    if unknown:
        return f"`{kind}` carries no field(s) {', '.join(unknown)} (declared: {', '.join(spec)})"
    for f, t in spec.items():
        if f not in fields:
            continue
        v = fields[f]
        if not isinstance(v, t) or (t is int and isinstance(v, bool)):   # a bool is no position
            return f"`{kind}` field `{f}` must be {t.__name__} (got {v!r})"
        allowed = _ALLOWED.get((kind, f))
        if allowed and v not in allowed:
            return f"`{kind}` field `{f}` must be one of {', '.join(allowed)} (got {v!r})"
        bad = field_format_error(f, v)
        if bad:
            return bad
        if t is list or kind in (P.ENTITY_ARTIFACT, P.ENTITY_ENVIRONMENT, P.ENTITY_CONCEPT):
            from .paths import record_field_error   # the path model's shapes (ae698640)
            bad = record_field_error(kind, key, f, v)
            if bad:
                return bad
    missing = [f for f in _REQUIRED.get(kind, ()) if f not in fields]
    if missing:
        return f"`{kind}` needs {', '.join(missing)}"
    return None


def field_format_error(
    field: str,  # The Entity field
    value: Any,  # Its value (already type-checked)
) -> Optional[str]:  # What the value must look like, or None when it is well formed
    """The formats some fields carry beyond their type (design leg 4a4ef27e): `published` is an
    ISO date at the precision known (YYYY, YYYY-MM or YYYY-MM-DD, a real calendar date) and
    `isbn` an ISBN-13, digits only, its check digit valid."""
    import datetime
    if field == "published":
        for fmt, n in (("%Y", 4), ("%Y-%m", 7), ("%Y-%m-%d", 10)):
            if len(value) == n:
                try:
                    datetime.datetime.strptime(value, fmt)
                    return None
                except ValueError:
                    break
        return f"`published` is an ISO date: YYYY, YYYY-MM or YYYY-MM-DD (got {value!r})"
    if field == "isbn":
        digits = [int(c) for c in value] if value.isdigit() and len(value) == 13 else None
        if digits is None or sum(d * (3 if i % 2 else 1) for i, d in enumerate(digits)) % 10:
            return f"`isbn` is an ISBN-13, digits only, with a valid check digit (got {value!r})"
    return None


async def mint_entity(
    gx: GraphHandle,
    kind: str,                              # The Entity sub-kind (task | stage)
    key: str,                               # The durable key (identity; name-independent)
    *,
    name: str,                              # The display name (a rename is a re-mint)
    fields: Optional[Dict[str, Any]] = None,  # The kind's extra fields (ENTITY_FIELDS)
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {entity_id, kind, key, updated, written} | {error, written: False}
    """Mint or update a typed Entity from its WHOLE record (journaled `entity`; upsert by
    (kind, key)). A field left out clears, so replaying the append-ordered ops converges on
    the last one; the id is (kind, key)-derived, so a rename keeps every fact pointing home."""
    fields = dict(fields or {})
    err = validate_entity(kind, key, name, fields)
    if err:
        return {"error": err, "written": False}
    work_id = None
    if kind == P.ENTITY_UNIT:   # a unit lands under a minted work (4a4ef27e (2))
        work_key = key.partition(P.UNIT_KEY_SEP)[0]
        work_id = entity_node_id(P.ENTITY_WORK, work_key)
        work = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=work_id)
        if work is None or F.prop(work, "entity_kind") != P.ENTITY_WORK:
            return {"error": f"no work `{work_key}` -- mint it first (`entity work {work_key} ...`)",
                    "written": False}
    from .paths import check_record_refs, land_record_edges   # the path model's records (ae698640)
    err = await check_record_refs(gx, kind, key, fields)
    if err:
        return {"error": err, "written": False}
    props = {f: fields[f] for f in ENTITY_FIELDS[kind] if f in fields}
    node = EntityNode(kind=kind, key=key, name=name, properties=props).to_graph_node()
    eid = node["id"]
    existing = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=eid)
    if existing is not None:
        whole = dict(node["properties"])
        for f in ENTITY_FIELDS[kind]:
            whole.setdefault(f, None)   # an omitted field clears (the whole-record rule)
        await graph_task(gx.queue, gx.graph_id, "update_node", node_id=eid, properties=whole)
    else:
        await extend_graph(gx.queue, gx.graph_id, [node], [])
    if work_id is not None:   # the unit's PART_OF, from its key (one per unit, so a re-mint re-lands it)
        await extend_graph(gx.queue, gx.graph_id, [], [unit_part_of_edge(eid, work_id)])
    edges = await land_record_edges(gx, kind, key, props)   # lineage, parts, requires, subject
    return {"entity_id": eid, "kind": kind, "key": key, "name": name, "fields": props,
            "updated": existing is not None, "written": True,
            **({"record_edges": edges} if any(edges.values()) else {})}


async def mint_entities(
    gx: GraphHandle,
    records: List[Dict[str, Any]],   # Whole entity records: [{kind, key, name, fields}]
    *,
    apply: bool = False,             # Land the batch; else check and plan only
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {errors, plan: [{kind, key, action}], landed: [records], written}
    """A vocabulary batch (amendment 3c5cff97), checked WHOLE before anything lands, as the
    library survey is: an invalid record, a repeated (kind, key) or a unit whose work is
    neither live nor earlier in the batch refuses the batch with nothing written. A record
    equal to its live Entity is `unchanged` and lands no op; every other one goes through
    mint_entity, so the journal carries one `entity` op per landed record."""
    errors: List[str] = []
    plan: List[Dict[str, Any]] = []
    todo: List[Dict[str, Any]] = []
    seen = set()
    for i, r in enumerate(records):
        kind, key, name = str(r.get("kind") or ""), str(r.get("key") or ""), str(r.get("name") or "")
        fields = dict(r.get("fields") or {})
        err = validate_entity(kind, key, name, fields)
        if not err and (kind, key) in seen:
            err = "repeats an earlier record"
        if not err and kind == P.ENTITY_UNIT:
            work_key = key.partition(P.UNIT_KEY_SEP)[0]
            work = await graph_task(gx.queue, gx.graph_id, "get_node",
                                    node_id=entity_node_id(P.ENTITY_WORK, work_key))
            if (P.ENTITY_WORK, work_key) not in seen and (
                    work is None or F.prop(work, "entity_kind") != P.ENTITY_WORK):
                err = f"no work `{work_key}` live or earlier in the batch"
        if not err:   # the path model's references, live or earlier in the batch (ae698640)
            from .paths import check_record_refs
            err = await check_record_refs(gx, kind, key, fields, pending=seen)
        if err:
            errors.append(f"record {i + 1} ({kind} `{key}`): {err}")
            continue
        seen.add((kind, key))
        node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=entity_node_id(kind, key))
        if node is None:
            action = "new"
        elif F.prop(node, "name") == name and all(F.prop(node, f) == fields.get(f) for f in ENTITY_FIELDS[kind]):
            action = "unchanged"
        else:
            action = "updated"
        plan.append({"kind": kind, "key": key, "action": action})
        if action != "unchanged":
            todo.append({"kind": kind, "key": key, "name": name, "fields": fields})
    out: Dict[str, Any] = {"errors": errors, "plan": plan, "landed": [], "written": False}
    if errors or not apply:
        return out
    for r in todo:
        res = await mint_entity(gx, r["kind"], r["key"], name=r["name"], fields=r["fields"], actor=actor)
        if res.get("error"):   # checked above, so a refusal here is a defect, never data
            raise RuntimeError(f"{r['kind']} `{r['key']}`: {res['error']}")
        out["landed"].append(r)
    out["written"] = bool(out["landed"])
    return out


async def check_coverage_value(
    gx: GraphHandle,
    predicate: str,   # The predicate being asserted
    value: str,       # The claimed value
    subject_id: str,  # The resolved subject's node id
) -> Optional[str]:  # An error, or None (also None for a predicate outside the coverage model)
    """A coverage value must name a live vocabulary entry of the predicate's kind, so a
    typo'd or retired key never lands (the projection refuses one that goes stale later);
    a verification standing belongs to a hardware Entity and a claim state to a claim Entity
    (98e99fe5 (1)), each from its closed slate. A confirmed category facet (eefda2dd (4)) is
    checked as coverage is, and about_task / about_stage are refused on a tutorial, whose
    task and stage are its teaches_* facts."""
    owned = {P.VERIFICATION_STANDING: ("verification standing", P.VERIFICATION_STANDINGS, P.ENTITY_HARDWARE),
             P.CLAIM_STATE: ("claim state", P.CLAIM_STATES, P.ENTITY_CLAIM)}
    if predicate in owned:
        what, slate, owner = owned[predicate]
        if value not in slate:
            return f"`{value}` is no {what} ({', '.join(slate)})"
        node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=subject_id)
        if node is None or F.prop(node, "entity_kind") != owner:
            return f"a {what} belongs to a {owner} Entity (`entity {owner} <key> ...`)"
        return None
    if predicate == P.ENVIRONMENT_VERSIONS:   # an environment's versions (ae698640 (3))
        import json
        try:
            P.versions_value(json.loads(value))
        except (ValueError, AttributeError):
            return ('environment versions are a JSON object of component versions '
                    f'(e.g. {{"torch": "2.4.1", "cuda": "12.4"}}; got {value!r})')
        node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=subject_id)
        if node is None or F.prop(node, "entity_kind") != P.ENTITY_ENVIRONMENT:
            return "environment versions belong to an environment Entity (`entity environment <key> ...`)"
        return None
    kind = P.COVERAGE_KINDS.get(predicate) or P.FACET_PREDICATES.get(predicate)
    if kind is None:
        return None
    if predicate in P.NON_TUTORIAL_FACETS:
        from .purenotes import note_types
        if ((await note_types(gx)).get(subject_id) or {}).get("kind") == TUTORIAL_KIND:
            return (f"`{predicate}` belongs to a non-tutorial post -- a tutorial states it as "
                    f"`{predicate.replace('about_', 'teaches_')}`")
    node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=entity_node_id(kind, value))
    if node is None or F.prop(node, "entity_kind") != kind:
        return (f"`{value}` is no {kind} in the vocabulary — mint it first "
                f"(`entity {kind} {value} --name ...`), or check the key")
    if F.prop(node, "retired"):
        return f"{kind} `{value}` is retired — assert its successor instead"
    if predicate in P.FACET_PREDICATES and kind == P.ENTITY_TASK and (
            F.prop(node, "cross_task") or F.prop(node, "off_grid")):
        return f"task `{value}` is a matrix row (cross-task or off-grid), never a post's facet"
    return None


def _ordered(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(nodes, key=lambda n: (n.get("position") if n.get("position") is not None else 1 << 30,
                                        n["key"]))


async def load_vocab(
    gx: GraphHandle,
) -> Dict[str, List[Dict[str, Any]]]:  # {task: [...], stage: [...]} — live entries by position
    """The live (unretired) vocabulary of both axes, each entry its properties plus `id`."""
    out: Dict[str, List[Dict[str, Any]]] = {P.ENTITY_TASK: [], P.ENTITY_STAGE: []}
    for n in await F.load_label(gx, DevNodeKinds.ENTITY):
        p = F.props(n)
        k = p.get("entity_kind")
        if k in out and not p.get("retired"):
            out[k].append({**p, "id": F.nid(n)})
    return {k: _ordered(v) for k, v in out.items()}


async def load_coverage_facts(
    gx: GraphHandle,
) -> Dict[str, Dict[str, List[str]]]:  # {subject id: {predicate: [active values]}}
    """Every subject's ACTIVE coverage values (both predicates, supersession applied)."""
    slot = [a for a in await F.load_assertions(gx) if F.prop(a, "predicate") in P.COVERAGE_KINDS]
    out: Dict[str, Dict[str, List[str]]] = {}
    for a in F.active_assertions(slot, await F.load_supersedes(gx)):
        vals = out.setdefault(str(F.prop(a, "subject_id")), {}).setdefault(str(F.prop(a, "predicate")), [])
        v = str(F.prop(a, "value") or "")
        if v and v not in vals:
            vals.append(v)
    return out


def project_matrix(
    tasks: List[Dict[str, Any]],             # Live task entries, by position
    stages: List[Dict[str, Any]],            # Live stage entries, by position
    tutorials: Dict[str, Dict[str, Any]],    # {note id: {title, slug}} — every tutorial-kind deliverable
    facts: Dict[str, Dict[str, List[str]]],  # {subject id: {predicate: [values]}}
    include: Optional[set] = None,           # The hardware filter's tutorials (None = no filter)
) -> Dict[str, Any]:  # The matrix, the covered cells, the gaps, the off-grid list, the refusals
    """The pure projection (no graph access): see the module docstring for the rules. A filter
    narrows the cells, the cover and the gaps to the included tutorials; every tutorial is
    still checked, so a refusal never hides behind a filter."""
    task_keys = {t["key"]: t for t in tasks}
    stage_keys = {s["key"]: s for s in stages}
    cells: Dict[Tuple[str, str], List[str]] = {}
    off_grid: Dict[str, List[str]] = {}
    refusals: List[Dict[str, Any]] = []
    for nid in sorted(tutorials, key=lambda i: (tutorials[i].get("slug") or "", i)):
        f = facts.get(nid, {})
        ts, ss = f.get(P.TEACHES_TASK, []), f.get(P.TEACHES_STAGE, [])
        bad = ([("task", v) for v in ts if v not in task_keys]
               + [("stage", v) for v in ss if v not in stage_keys])
        if not ts:
            refusals.append({"id": nid, "reason": "unsurveyed", "detail": "no teaches_task fact"})
            continue
        if bad:
            refusals.append({"id": nid, "reason": "unknown",
                             "detail": ", ".join(f"{k} `{v}`" for k, v in bad)})
            continue
        grid = [t for t in ts if not task_keys[t].get("off_grid")]
        if grid and not ss:
            refusals.append({"id": nid, "reason": "unstaged",
                             "detail": f"teaches {', '.join(grid)} but no teaches_stage fact"})
            continue
        if include is not None and nid not in include:
            continue
        for t in ts:
            if task_keys[t].get("off_grid"):
                off_grid.setdefault(t, []).append(nid)
        for t in grid:
            for s in ss:
                cells.setdefault((t, s), []).append(nid)
    grid_tasks = [t for t in tasks if not t.get("off_grid")]
    cross_rows = [t["key"] for t in grid_tasks if t.get("cross_task")]
    covered, gaps = [], []
    for t in grid_tasks:
        if t.get("cross_task"):
            continue   # the cross-task row supports the others; its own empty cells are not gaps
        for s in stages:
            if cells.get((t["key"], s["key"])):
                continue
            by = [r for r in cross_rows if cells.get((r, s["key"]))] if s.get("cross_task") else []
            if by:
                covered.append({"task": t["key"], "stage": s["key"], "by": by})
            else:
                gaps.append({"task": t["key"], "stage": s["key"]})
    return {"tasks": grid_tasks, "stages": stages,
            "off_grid_tasks": [t for t in tasks if t.get("off_grid")],
            "cells": {f"{t}|{s}": ids for (t, s), ids in cells.items()},
            "covered": covered, "gaps": gaps, "off_grid": off_grid, "refusals": refusals,
            "tutorials": tutorials, "ok": not refusals,
            "filtered": None if include is None else len([i for i in tutorials if i in include])}


async def coverage_matrix(
    gx: GraphHandle,
    hardware: Optional[List[str]] = None,  # Filter: tutorials verified on any of these device keys
    in_set: bool = False,                  # Filter: tutorials verified on any in-set device
) -> Dict[str, Any]:  # project_matrix's result over the live graph (+ the filter it applied)
    """The Tutorials matrix over the graph: every deliverable whose type's kind is
    `tutorial` and that is not retired (design amendment e916a4b9 (3)), against the live
    vocabulary, optionally narrowed by the hardware filter."""
    from .archive import is_retired
    from .purenotes import note_publish_states, note_types
    from .site import stated
    vocab = await load_vocab(gx)
    typed = await note_types(gx)
    states = await note_publish_states(gx)
    ids = [nid for nid, t in typed.items() if t.get("kind") == TUTORIAL_KIND and not is_retired(nid, states)]
    tutorials: Dict[str, Dict[str, Any]] = {}
    for nid in ids:
        node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=nid)
        tutorials[nid] = {"title": stated(node, "title"), "slug": F.prop(node, "slug") or ""}
    include, label = None, None
    if hardware or in_set:
        devices = await load_hardware(gx)
        unknown = [k for k in hardware or [] if k not in devices]
        if unknown:
            return {"error": f"no hardware `{'`, `'.join(unknown)}` (list them: `hardware`)", "ok": False}
        keys = set(hardware or []) | ({k for k, d in devices.items()
                                       if d.get("standing") == P.STANDING_IN_SET} if in_set else set())
        ids = {devices[k]["id"] for k in keys}
        include = {str(e["source_id"]) for e in await load_verifications(gx) if str(e["target_id"]) in ids}
        label = ", ".join(sorted(keys)) + (" (the in-set devices)" if in_set else "")
    res = project_matrix(vocab[P.ENTITY_TASK], vocab[P.ENTITY_STAGE], tutorials,
                         await load_coverage_facts(gx), include=include)
    res["filter"] = label
    return res


async def load_verifications(
    gx: GraphHandle,
) -> List[Dict[str, Any]]:  # Every VERIFIED_ON edge as a plain dict
    """The verification edges (deliverable -> device) with their evidence."""
    res = await graph_task(gx.queue, gx.graph_id, "query_edges",
                           query=EdgeQuery(relation_type=DevRelations.VERIFIED_ON).to_dict())
    raw = getattr(res, "edges", None) or getattr(res, "rows", None) or []
    return [e.to_dict() if hasattr(e, "to_dict") else dict(e) for e in raw]


async def load_hardware(
    gx: GraphHandle,
) -> Dict[str, Dict[str, Any]]:  # {device key: its properties + id, standing, verified}
    """Every hardware Entity with its ACTIVE standing (None if never asserted; two active
    standings come back as a `conflict`) and how many deliverables were verified on it."""
    devices: Dict[str, Dict[str, Any]] = {}
    for n in await F.load_label(gx, DevNodeKinds.ENTITY):
        p = F.props(n)
        if p.get("entity_kind") == P.ENTITY_HARDWARE:
            devices[str(p.get("key"))] = {**p, "id": F.nid(n), "standing": None, "verified": 0}
    by_id = {d["id"]: d for d in devices.values()}
    slot = [a for a in await F.load_assertions(gx) if F.prop(a, "predicate") == P.VERIFICATION_STANDING]
    active: Dict[str, List[str]] = {}
    for a in F.active_assertions(slot, await F.load_supersedes(gx)):
        active.setdefault(str(F.prop(a, "subject_id")), []).append(str(F.prop(a, "value")))
    for sid, vals in active.items():
        if sid in by_id:
            vals = sorted(set(vals))
            by_id[sid]["standing"] = vals[0] if len(vals) == 1 else None
            if len(vals) > 1:
                by_id[sid]["conflict"] = vals
    for e in await load_verifications(gx):
        d = by_id.get(str(e["target_id"]))
        if d is not None:
            d["verified"] += 1
    return devices


async def resolve_deliverable(
    gx: GraphHandle,
    deliverable: str,  # A deliverable Note's node id, or its post slug
) -> Optional[str]:  # The Note's id, or None
    """Resolve a deliverable argument to its Note id (an id first, then a slug)."""
    from cjm_dev_graph_schema.identity import note_node_id
    for cand in (deliverable, note_node_id(deliverable)):
        node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=cand)
        if node is not None and (getattr(node, "label", None) or (node.get("label") if isinstance(node, dict) else None)) == DevNodeKinds.NOTE:
            return cand
    return None


async def record_verification(
    gx: GraphHandle,
    deliverable: str,                          # The deliverable's node id, or a Note's slug
    hardware: str,                             # The hardware Entity's key
    *,
    os: str = "",                              # The OS it ran under ("" = unknown)
    date: str = "",                            # When it was verified (verbatim)
    basis: str = "stated",                     # stated | timeline
    versions: Optional[Dict[str, str]] = None,  # Driver / runtime / library versions
    note: str = "",                            # What ran there
    retract: bool = False,                     # Remove this (deliverable, device, os) verification
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {edge_id, deliverable_id, hardware_id, written} | {error, written: False}
    """Write one VERIFIED_ON edge with its evidence (journaled `verified-on`), or retract it.
    A re-verification under the same OS replaces the evidence (the journal keeps the history)."""
    if basis not in P.VERIFICATION_BASES:
        return {"error": f"basis must be one of {', '.join(P.VERIFICATION_BASES)} (got {basis!r})",
                "written": False}
    if not retract and not date:
        return {"error": "a verification needs its --date", "written": False}
    did = await resolve_deliverable(gx, deliverable)
    if did is None:
        return {"error": f"no deliverable Note `{deliverable}` (a node id or a post slug)", "written": False}
    hid = entity_node_id(P.ENTITY_HARDWARE, hardware)
    if await graph_task(gx.queue, gx.graph_id, "get_node", node_id=hid) is None:
        return {"error": f"no hardware `{hardware}` — mint it first (`entity hardware {hardware} ...`)",
                "written": False}
    edge = verified_on_edge(did, hid, os=os, date=date, basis=basis, versions=versions, note=note)
    standing = [e for e in await load_verifications(gx) if str(e["id"]) == edge["id"]]
    if standing:
        await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=[edge["id"]])
    if retract:
        return {"edge_id": edge["id"], "deliverable_id": did, "hardware_id": hid, "retracted": bool(standing),
                "written": bool(standing), **({} if standing else {"error": "no such verification to retract"})}
    await extend_graph(gx.queue, gx.graph_id, [], [edge])
    return {"edge_id": edge["id"], "deliverable_id": did, "hardware_id": hid, "hardware": hardware,
            "os": os, "date": date, "basis": basis, "replaced": bool(standing), "written": True}
