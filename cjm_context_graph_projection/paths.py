"""The path model (design ae698640, the walk 57287d1b, the refactor leg ad9bef5a): artifacts,
environments and concepts as Entity sub-kinds, have / know relations as edges with per-pair data,
and the paths DERIVED over them.

WRITES. The four sub-kinds ride the journaled `entity` op (coverage.mint_entity, the whole record):
an `artifact_kind` is vocabulary data; an `artifact` names its kind, task, base model, target and
LINEAGE (`derived_from`); an `environment` names its parts, the environments it requires and its
variants; a `concept` names its subject. The references a record makes are checked LIVE at write
time (a live, unretired Entity of the named kind), lineage and environment requirements are
refused when they would close a cycle, and the edges a record implies (DERIVED_FROM, PART_OF,
REQUIRES) land from it marked with the record, so a re-mint reconciles exactly its own edges. A
stage's TRANSITIONS name artifact kinds and are checked the same way. The six relations land by
the journaled `relate` op (`record_relation`): endpoint kinds checked against the schema's
RELATION_ENDPOINTS, the strength on REQUIRES / ASSUMES, one edge per (relation, source, target).
The same op lands a page's DESTINATION (cbd5f154 (3)/(4)) -- a public successor's SUPERSEDES, a
removed page's RELOCATED_TO its repo copy's web Reference -- which the walk never reads.
An environment's versions are the `environment_versions` fact (checked in coverage).

READS (`path_reads` and the `paths` verb) derive everything and store nothing: continues-from,
alternatives (with the prerequisites that differ), a step's derived stage and task, analogues,
kind-level flywheel cycles with the instance chains realizing them, gaps, prepares-you-for,
environment staleness against VERIFIED_ON evidence, and split candidates. A setup-role Section's
PRODUCES counts for its deliverable. Each read refuses what it cannot decide rather than dropping
it (6752db0a (9)): an artifact with no live kind, a step whose kinds match no transition or two."""

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from cjm_context_graph_layer.grammar import SpineRelations
from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.query import EdgeQuery, PropertyPredicate
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import entity_node_id
from cjm_dev_graph_schema.nodes import lineage_edge, record_part_of_edge, relation_edge
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from . import factlayer as F
from .runtime import GraphHandle

RELATIONS = P.PATH_RELATIONS   # the walk's relations; the destination relations share only the relate verb
# The record fields that name another Entity: field -> the kind(s) it names ("kind:key" when several)
_REF_FIELDS = {
    P.ENTITY_ARTIFACT: {"artifact_kind": (P.ENTITY_ARTIFACT_KIND,), "task": (P.ENTITY_TASK,),
                        "base_model": (P.ENTITY_MODEL,), "target": (P.ENTITY_HARDWARE, P.ENTITY_ENVIRONMENT),
                        "derived_from": (P.ENTITY_ARTIFACT,)},
    P.ENTITY_ENVIRONMENT: {"parts": P.ENVIRONMENT_PART_KINDS, "requires": (P.ENTITY_ENVIRONMENT,)},
    P.ENTITY_CONCEPT: {"subject": (P.ENTITY_SUBJECT,)},
}
_LIST_FIELDS = {"derived_from", "parts", "requires", "variants", "transitions"}


def _slug_ok(v: Any) -> bool:
    return isinstance(v, str) and bool(v) and v == v.strip() and " " not in v


def _typed_ref(v: str, kinds: Tuple[str, ...]) -> Optional[Tuple[str, str]]:
    """A reference value: a bare key when the field names one kind, `kind:key` when several."""
    if len(kinds) == 1:
        return (kinds[0], v) if _slug_ok(v) else None
    kind, sep, key = v.partition(":")
    return (kind, key) if sep and kind in kinds and _slug_ok(key) else None


def record_field_error(
    kind: str,    # The Entity sub-kind
    key: str,     # Its key
    field: str,   # The field
    value: Any,   # Its value (already type-checked)
) -> Optional[str]:  # What the value must look like, or None
    """The SHAPE of a path-model record field (pure; the live references are checked by
    `check_record_refs`): a list holds distinct slugs (a transition list holds transitions), a
    multi-kind reference is `kind:key`, a record never names itself."""
    if field == "transitions":
        return transitions_error(value)
    refs = _REF_FIELDS.get(kind, {})
    vals = value if field in _LIST_FIELDS else [value]
    if field in _LIST_FIELDS and len(set(map(str, vals))) != len(vals):
        return f"`{kind}` field `{field}` repeats a value"
    for v in vals:
        if field == "variants":
            if not (isinstance(v, str) and v.strip()):
                return f"`{kind}` variants are non-empty names (got {v!r})"
            continue
        if field in refs:
            kinds = refs[field]
            ref = _typed_ref(v, kinds) if isinstance(v, str) else None
            if ref is None:
                shape = "a key" if len(kinds) == 1 else f"`kind:key` with kind {' | '.join(kinds)}"
                return f"`{kind}` field `{field}` takes {shape} (got {v!r})"
            if ref == (kind, key):
                return f"`{kind}` `{key}` cannot name itself in `{field}`"
    return None


def transitions_error(
    transitions: List[Any],  # A stage's transitions
) -> Optional[str]:  # What is wrong, or None
    """A stage's transitions (ae698640 (2)): each {in: [kinds], optional: [kinds], out: kind}, the
    `in` and `optional` sets disjoint; `out` an artifact kind or TRANSITION_ENVIRONMENT."""
    for i, t in enumerate(transitions):
        where = f"transition {i + 1}"
        if not isinstance(t, dict):
            return f"{where} is an object {{in, optional, out}} (got {t!r})"
        unknown = sorted(set(t) - set(P.TRANSITION_FIELDS))
        if unknown:
            return f"{where} carries no {', '.join(unknown)} (fields: {', '.join(P.TRANSITION_FIELDS)})"
        if not _slug_ok(t.get("out")):
            return f"{where} needs its `out` kind"
        for f in ("in", "optional"):
            v = t.get(f, [])
            if not isinstance(v, list) or not all(_slug_ok(x) for x in v) or len(set(v)) != len(v):
                return f"{where} `{f}` is a list of distinct kinds (got {v!r})"
        if set(t.get("in") or []) & set(t.get("optional") or []):
            return f"{where} names a kind both `in` and `optional`"
    return None


def record_refs(
    kind: str,                # The Entity sub-kind
    fields: Dict[str, Any],   # Its record's fields
) -> List[Tuple[str, str, str]]:  # [(field, kind, key)] -- every Entity the record names
    """The Entities a record names (its references), from its fields."""
    out = []
    for field, kinds in _REF_FIELDS.get(kind, {}).items():
        v = fields.get(field)
        for x in (v if isinstance(v, list) else [v] if v else []):
            ref = _typed_ref(x, kinds)
            if ref:
                out.append((field, *ref))
    if kind == P.ENTITY_STAGE:
        for t in fields.get("transitions") or []:
            for k in list(t.get("in") or []) + list(t.get("optional") or []) + [t.get("out")]:
                if k != P.TRANSITION_ENVIRONMENT:
                    out.append(("transitions", P.ENTITY_ARTIFACT_KIND, k))
    return out


async def _reachable(
    gx: GraphHandle,
    relation: str,           # The relation to follow (DERIVED_FROM / REQUIRES)
    starts: Iterable[str],   # Node ids to start from
    goal: str,               # The node id that must not be reachable
) -> bool:
    """Is `goal` reachable from `starts` along `relation` (source -> target)? The cycle check."""
    seen: Set[str] = set()
    todo = [s for s in starts]
    while todo:
        n = todo.pop()
        if n == goal:
            return True
        if n in seen:
            continue
        seen.add(n)
        todo.extend(str(e["target_id"]) for e in await _edges(gx, relation, source_id=n))
    return False


async def _edges(
    gx: GraphHandle,
    relation: str,
    **filters: Any,   # EdgeQuery filters (source_id, target_id, where, ...)
) -> List[Dict[str, Any]]:  # The matching edges as plain dicts
    res = await graph_task(gx.queue, gx.graph_id, "query_edges",
                           query=EdgeQuery(relation_type=relation, **filters).to_dict())
    raw = getattr(res, "edges", None) or getattr(res, "rows", None) or []
    return [e.to_dict() if hasattr(e, "to_dict") else dict(e) for e in raw]


async def check_record_refs(
    gx: GraphHandle,
    kind: str,                       # The Entity sub-kind
    key: str,                        # Its key
    fields: Dict[str, Any],          # Its record's fields
    pending: Optional[Set[Tuple[str, str]]] = None,  # (kind, key) records earlier in the same batch
) -> Optional[str]:  # An error, or None
    """Every Entity a record names is live (or earlier in its batch) and unretired, and its
    lineage / requirements close no cycle. A refusal writes nothing."""
    pending = pending or set()
    for field, rk, rkey in record_refs(kind, fields):
        if (rk, rkey) in pending:
            continue
        node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=entity_node_id(rk, rkey))
        if node is None or F.prop(node, "entity_kind") != rk:
            return f"`{field}` names no {rk} `{rkey}` -- mint it first (`entity {rk} {rkey} ...`)"
        if F.prop(node, "retired"):
            return f"`{field}` names {rk} `{rkey}`, which is retired"
    eid = entity_node_id(kind, key)
    for field, relation in (("derived_from", DevRelations.DERIVED_FROM), ("requires", DevRelations.REQUIRES)):
        if kind not in _REF_FIELDS or field not in _REF_FIELDS[kind]:
            continue
        parents = [entity_node_id(rk, rkey) for f, rk, rkey in record_refs(kind, fields) if f == field]
        if parents and await _reachable(gx, relation, parents, eid):
            what = "lineage" if field == "derived_from" else "requirements"
            return f"`{kind}` `{key}`: its {what} would close a cycle (instance {what} stay acyclic)"
    return None


def record_edges(
    kind: str,               # The Entity sub-kind
    key: str,                # Its key
    fields: Dict[str, Any],  # Its record's fields
) -> List[Dict[str, Any]]:  # The edges the record implies, each marked with the record
    """The edges a record lands: an artifact's lineage, an environment's parts and requirements,
    a concept's subject."""
    eid = entity_node_id(kind, key)
    out = []
    for field, rk, rkey in record_refs(kind, fields):
        rid = entity_node_id(rk, rkey)
        if field == "derived_from":
            out.append(lineage_edge(eid, rid))
        elif field == "parts":
            out.append(record_part_of_edge(rid, eid, eid))
        elif field == "requires":
            out.append(relation_edge(DevRelations.REQUIRES, eid, rid, record=eid))
        elif field == "subject":
            out.append(record_part_of_edge(eid, rid, eid))
    return out


async def land_record_edges(
    gx: GraphHandle,
    kind: str,               # The Entity sub-kind
    key: str,                # Its key
    fields: Dict[str, Any],  # Its record's fields (the whole record just minted)
) -> Dict[str, int]:  # {landed, removed}
    """Reconcile the edges a record owns with the record: the ones it no longer implies go, the new
    ones land, so the append-ordered `entity` ops converge on the last record's edges."""
    eid = entity_node_id(kind, key)
    want = {e["id"]: e for e in record_edges(kind, key, fields)}
    have = set()
    for rel in (DevRelations.DERIVED_FROM, SpineRelations.PART_OF, DevRelations.REQUIRES):
        have |= {str(e["id"]) for e in await _edges(gx, rel, where=[PropertyPredicate("record", "eq", eid)])}
    stale = sorted(have - set(want))
    if stale:
        await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=stale)
    new = [e for i, e in want.items() if i not in have]
    if new:
        await extend_graph(gx.queue, gx.graph_id, [], new)
    return {"landed": len(new), "removed": len(stale)}


async def endpoint_kind(
    gx: GraphHandle,
    node_id: str,
) -> Optional[str]:  # deliverable | section | an Entity sub-kind | None (no such node)
    """What a node is, in RELATION_ENDPOINTS' vocabulary."""
    node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=node_id)
    if node is None:
        return None
    label = getattr(node, "label", None) or (node.get("label") if isinstance(node, dict) else None)
    if label == DevNodeKinds.NOTE:
        return P.NODE_DELIVERABLE
    if label == DevNodeKinds.SECTION:
        return P.NODE_SECTION
    if label == DevNodeKinds.REFERENCE:
        return P.NODE_REFERENCE
    if label == DevNodeKinds.ENTITY:
        return F.prop(node, "entity_kind")
    return label


async def resolve_endpoint(
    gx: GraphHandle,
    ref: str,   # A node id, a post slug, or `kind:key` for an Entity
) -> Optional[str]:  # The node id, or None
    """Resolve a relation endpoint argument: an Entity by `kind:key`, else a deliverable by id or
    slug, else any node id (a Section)."""
    from .coverage import resolve_deliverable
    kind, sep, key = ref.partition(":")
    if sep and kind and key and " " not in ref:
        eid = entity_node_id(kind, key)
        if await graph_task(gx.queue, gx.graph_id, "get_node", node_id=eid) is not None:
            return eid
    did = await resolve_deliverable(gx, ref)
    if did:
        return did
    if await graph_task(gx.queue, gx.graph_id, "get_node", node_id=ref) is not None:
        return ref
    return None


async def record_relation(
    gx: GraphHandle,
    relation: str,          # PRODUCES | REQUIRES | TEACHES | ASSUMES | COVERS | EXPLAINS
    source: str,            # The source: a node id, a post slug, or `kind:key`
    target: str,            # The target, likewise
    *,
    strength: str = "",     # REQUIRES / ASSUMES: required | recommended ("" = required)
    note: str = "",         # One line on the pair
    retract: bool = False,  # Remove this (relation, source, target) edge
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {edge_id, relation, source_id, target_id, written} | {error, written: False}
    """Write one path-model relation (journaled `relate`), or retract it. The endpoints' kinds must
    be ones RELATION_ENDPOINTS allows, a target Entity live and unretired; an environment's own
    requirements are its record's. A pair holds one edge per relation, so a restatement replaces
    it (the journal keeps the history)."""
    if relation not in P.RELATION_ENDPOINTS:
        return {"error": f"no relation `{relation}` ({', '.join(P.RELATION_ENDPOINTS)})", "written": False}
    sid, tid = await resolve_endpoint(gx, source), await resolve_endpoint(gx, target)
    if sid is None or tid is None:
        return {"error": f"no node `{source if sid is None else target}` (a node id, a post slug or `kind:key`)",
                "written": False}
    if sid == tid:
        return {"error": f"a {relation} edge joins two nodes, never one to itself", "written": False}
    sk, tk = await endpoint_kind(gx, sid), await endpoint_kind(gx, tid)
    srcs, tgts = P.RELATION_ENDPOINTS[relation]
    if (relation, sk) in P.RECORD_RELATIONS:
        return {"error": f"an {sk}'s {relation} edges are its record's (`entity {sk} <key> --requires ...`)",
                "written": False}
    if sk not in srcs:
        return {"error": f"{relation} runs from {' | '.join(srcs)}, never from a {sk}", "written": False}
    if tk not in tgts:
        return {"error": f"{relation} runs to {' | '.join(tgts)}, never to a {tk}", "written": False}
    if relation == DevRelations.SUPERSEDES and not retract:   # supersession waits on the successor (cbd5f154 (3))
        from .purenotes import public_deliverables
        if sid not in await public_deliverables(gx):
            return {"error": "a successor SUPERSEDES the page it replaces once it is public -- until then the "
                             "intent rides the successor's work item (cbd5f154 (3))", "written": False}
    if not retract and tk not in (P.NODE_DELIVERABLE, P.NODE_SECTION, P.NODE_REFERENCE):
        node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=tid)
        if F.prop(node, "retired"):
            return {"error": f"{tk} `{F.prop(node, 'key')}` is retired -- relate its successor", "written": False}
    try:
        edge = relation_edge(relation, sid, tid, strength=strength, note=note)
    except ValueError as exc:
        return {"error": str(exc), "written": False}
    standing = [e for e in await _edges(gx, relation, source_id=sid) if str(e["id"]) == edge["id"]]
    if standing:
        await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=[edge["id"]])
    base = {"edge_id": edge["id"], "relation": relation, "source_id": sid, "target_id": tid}
    if retract:
        return {**base, "retracted": bool(standing), "written": bool(standing),
                **({} if standing else {"error": f"no such {relation} edge to retract"})}
    await extend_graph(gx.queue, gx.graph_id, [], [edge])
    return {**base, **edge["properties"], "replaced": bool(standing), "written": True}


# ---------------------------------------------------------------------------------------------
# READS -- derived, never stored (design ae698640 (6), ad9bef5a (2)). `load_path_graph` reads the
# graph once into plain data; every read below is a PURE function of that data.

READS = ("all", "step", "continues", "alternatives", "stages", "analogues", "cycles", "gaps", "prepares",
         "stale", "splits")
_ENTITY_KINDS = (P.ENTITY_ARTIFACT, P.ENTITY_ARTIFACT_KIND, P.ENTITY_ENVIRONMENT, P.ENTITY_CONCEPT,
                 P.ENTITY_STAGE, P.ENTITY_TASK, P.ENTITY_MODEL, P.ENTITY_UNIT, P.ENTITY_WORK,
                 P.ENTITY_HARDWARE, P.ENTITY_SUBJECT, P.ENTITY_TOOL)


async def load_path_graph(
    gx: GraphHandle,
) -> Dict[str, Any]:  # {entities, deliverables, relations, lineage, verifications, versions}
    """The path model's slice of the graph as plain data: the Entities it names (by id: their
    properties), every relation edge (a setup Section's PRODUCES rolled up to its deliverable,
    the Section kept as `via`), the artifacts' lineage, the verifications and each environment's
    ACTIVE versions."""
    from .site import stated
    entities: Dict[str, Dict[str, Any]] = {}
    for n in await F.load_label(gx, DevNodeKinds.ENTITY):
        p = F.props(n)
        if p.get("entity_kind") in _ENTITY_KINDS:
            entities[F.nid(n)] = {**p, "id": F.nid(n)}
    relations: List[Dict[str, Any]] = []
    for rel in RELATIONS:
        for e in await _edges(gx, rel):
            relations.append({"relation": rel, "source": str(e["source_id"]), "target": str(e["target_id"]),
                              **{k: v for k, v in (e.get("properties") or {}).items() if k in ("strength", "note")}})
    sections = sorted({r["source"] for r in relations if r["source"] not in entities})
    owner: Dict[str, str] = {}
    if sections:
        for e in await _edges(gx, DevRelations.HAS_SECTION, target_ids=sections):
            owner[str(e["target_id"])] = str(e["source_id"])
    for r in relations:   # a setup-role Section's PRODUCES counts for its deliverable (ad9bef5a (1))
        if r["source"] in owner:
            r["via"], r["source"] = r["source"], owner[r["source"]]
    lineage = [(str(e["source_id"]), str(e["target_id"]))
               for e in await _edges(gx, DevRelations.DERIVED_FROM)
               if (e.get("properties") or {}).get("record") == str(e["source_id"])]
    deliverables: Dict[str, Dict[str, Any]] = {}
    for nid in sorted({x for r in relations for x in (r["source"], r["target"]) if x not in entities}):
        node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=nid)
        if node is not None:
            deliverables[nid] = {"id": nid, "title": stated(node, "title") or str(F.prop(node, "title") or ""),
                                 "slug": str(F.prop(node, "slug") or "")}
    verifications = []
    for e in await _edges(gx, DevRelations.VERIFIED_ON):
        p = e.get("properties") or {}
        verifications.append({"deliverable": str(e["source_id"]), "hardware": str(e["target_id"]),
                              "os": p.get("os", ""), "date": p.get("date", ""), "versions": dict(p.get("versions") or {})})
    slot = [a for a in await F.load_assertions(gx) if F.prop(a, "predicate") == P.ENVIRONMENT_VERSIONS]
    versions: Dict[str, List[Dict[str, str]]] = {}
    for a in F.active_assertions(slot, await F.load_supersedes(gx)):
        versions.setdefault(str(F.prop(a, "subject_id")), []).append(P.versions_of(str(F.prop(a, "value"))))
    return {"entities": entities, "deliverables": deliverables, "relations": relations, "lineage": lineage,
            "verifications": verifications, "versions": versions}


def _name(g: Dict[str, Any], nid: str) -> Dict[str, Any]:
    """A node's reference in a read: id, kind and display name (a deliverable's title)."""
    e = g["entities"].get(nid)
    if e is not None:
        return {"id": nid, "kind": e.get("entity_kind"), "key": e.get("key"), "name": e.get("name") or e.get("key")}
    d = g["deliverables"].get(nid) or {}
    return {"id": nid, "kind": P.NODE_DELIVERABLE, "key": d.get("slug", ""), "name": d.get("title") or d.get("slug") or nid}


def steps_of(g: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Every deliverable with relations, as a step: what it requires (artifact / environment ->
    strength), produces, teaches, assumes (concept -> strength) and explains."""
    steps: Dict[str, Dict[str, Any]] = {}
    for r in g["relations"]:
        if r["source"] not in g["deliverables"]:
            continue
        s = steps.setdefault(r["source"], {"requires": {}, "produces": [], "teaches": [], "assumes": {},
                                           "explains": []})
        rel = r["relation"]
        if rel in ("REQUIRES", "ASSUMES"):
            s[rel.lower()][r["target"]] = r.get("strength") or P.STRENGTH_REQUIRED
        elif rel in ("PRODUCES", "TEACHES", "EXPLAINS") and r["target"] not in s[rel.lower()]:
            s[rel.lower()].append(r["target"])
    return steps


def _kind(g: Dict[str, Any], nid: str) -> Optional[str]:
    """The artifact kind of an artifact, TRANSITION_ENVIRONMENT for an environment, else None."""
    e = g["entities"].get(nid) or {}
    if e.get("entity_kind") == P.ENTITY_ENVIRONMENT:
        return P.TRANSITION_ENVIRONMENT
    return e.get("artifact_kind") if e.get("entity_kind") == P.ENTITY_ARTIFACT else None


def _is_artifact(g: Dict[str, Any], nid: str) -> bool:
    return (g["entities"].get(nid) or {}).get("entity_kind") == P.ENTITY_ARTIFACT


def continues_from(g: Dict[str, Any], steps: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """continues-from: a step's REQUIRES matched to another step's PRODUCES -- [{step, from, via,
    strength}], one row per (step, producer, thing)."""
    producers: Dict[str, List[str]] = {}
    for sid, s in steps.items():
        for x in s["produces"]:
            producers.setdefault(x, []).append(sid)
    out = []
    for sid in sorted(steps):
        for x, strength in sorted(steps[sid]["requires"].items()):
            for p in sorted(producers.get(x, [])):
                if p != sid:
                    out.append({"step": sid, "from": p, "via": x, "strength": strength})
    return out


def alternatives(g: Dict[str, Any], steps: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """alternatives: steps sharing an input ARTIFACT and producing one artifact KIND -- [{input,
    kind, steps: [{step, produces, differs}]}], `differs` = the prerequisites the step has that
    not every alternative shares (where alternatives part ways: Jetson vs the Pi)."""
    groups: Dict[Tuple[str, str], Set[str]] = {}
    for sid, s in steps.items():
        kinds = {_kind(g, x) for x in s["produces"] if _is_artifact(g, x)} - {None}
        for x in s["requires"]:
            if _is_artifact(g, x):
                for k in kinds:
                    groups.setdefault((x, k), set()).add(sid)
    out = []
    for (x, k), members in sorted(groups.items()):
        if len(members) < 2:
            continue
        shared = set.intersection(*(set(steps[m]["requires"]) for m in members))
        out.append({"input": x, "kind": k, "steps": [
            {"step": m, "produces": sorted(p for p in steps[m]["produces"] if _kind(g, p) == k),
             "differs": sorted(set(steps[m]["requires"]) - shared)} for m in sorted(members)]})
    return out


def stage_matches(g: Dict[str, Any], steps: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """A step's DERIVED stage and task (ae698640 (2)): its input artifact kinds and output kinds
    matched against every live stage's transitions; its task from the model artifacts it produces.
    {step: {inputs, outputs, stage, transition, matches, task, refusal?}} -- a step that produces
    nothing has no stage; one matching no transition is `unmatched`, several stages `ambiguous`."""
    stages = [e for e in g["entities"].values() if e.get("entity_kind") == P.ENTITY_STAGE and not e.get("retired")]
    out = {}
    for sid, s in sorted(steps.items()):
        ins = sorted({_kind(g, x) for x in s["requires"] if _is_artifact(g, x)} - {None})
        outs = sorted({_kind(g, x) for x in s["produces"]} - {None})
        matches = [(st["key"], i) for st in sorted(stages, key=lambda e: (e.get("position") or 0, e["key"]))
                   for i, t in enumerate(st.get("transitions") or []) if P.transition_matches(t, ins, outs)]
        tasks = sorted({(g["entities"].get(x) or {}).get("task") for x in s["produces"] if _is_artifact(g, x)} - {None, ""})
        row: Dict[str, Any] = {"inputs": ins, "outputs": outs, "stage": None, "transition": None,
                               "matches": matches, "task": tasks[0] if len(tasks) == 1 else None, "tasks": tasks}
        named = sorted({m[0] for m in matches})
        if not outs:
            row["note"] = "produces nothing (no stage)"
        elif not named:
            row["refusal"] = "unmatched"
        elif len(named) > 1:
            row["refusal"] = "ambiguous"
        else:
            row["stage"], row["transition"] = matches[0]
        if len(tasks) > 1:
            row["refusal"] = row.get("refusal") or "several tasks"
        out[sid] = row
    return out


def analogues(g: Dict[str, Any], steps: Dict[str, Dict[str, Any]],
              stages: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """analogues (ad9bef5a (2)): steps making the SAME stage transition with DIFFERENT base models
    (the YOLOX ONNX export beside the Keypoint R-CNN one) -- [{stage, transition, steps: [{step,
    base_models}]}] for every transition carried by two or more base models."""
    groups: Dict[Tuple[str, int], Dict[str, List[str]]] = {}
    for sid, row in stages.items():
        if row.get("stage") is None:
            continue
        bases = sorted({(g["entities"].get(x) or {}).get("base_model") for x in steps[sid]["produces"]
                        if _is_artifact(g, x)} - {None, ""})
        if bases:
            groups.setdefault((row["stage"], row["transition"]), {})[sid] = bases
    out = []
    for (stage, i), members in sorted(groups.items()):
        if len({b for bs in members.values() for b in bs}) > 1:
            out.append({"stage": stage, "transition": i,
                        "steps": [{"step": m, "base_models": members[m]} for m in sorted(members)]})
    return out


def _kind_order(g: Dict[str, Any]) -> Dict[str, Tuple[int, str]]:
    return {e["key"]: (e.get("position") if e.get("position") is not None else 1 << 30, e["key"])
            for e in g["entities"].values() if e.get("entity_kind") == P.ENTITY_ARTIFACT_KIND}


def flywheel_cycles(g: Dict[str, Any], steps: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Flywheel paths (ae698640 (2)): a flywheel is a CYCLE AT THE KIND LEVEL over ACYCLIC instance
    lineage, so it is read off the instances. An INSTANCE LOOP is a lineage chain (an artifact's
    DERIVED_FROM edges, plus each step's required -> produced artifacts) from an artifact to a later
    one of the SAME kind, through at least one other kind and no other artifact of that kind. Only
    the FULLEST chains to an end count: a chain whose path after its start lies inside a chain to
    the same end covering MORE kinds is that loop seen from a SIDE INPUT of multi-parent lineage
    (predictions also derive from the dataset they ran on; new field samples join the deployment
    loop, they do not make one). Each loop's kind sequence -- consecutive same-kind steps one kind,
    rotated to its lowest-positioned kind -- is a cycle: {cycles: [{kinds, chains}]}. A same-kind
    derivation (a corrected dataset, a class extension's checkpoint) is a revision, never a loop.
    Instance lineage must be acyclic: a cycle there is refused, never walked."""
    order = _kind_order(g)
    key = lambda k: order.get(k, (1 << 30, k))
    children: Dict[str, Set[str]] = {}
    for child, parent in g["lineage"]:
        children.setdefault(parent, set()).add(child)
    for s in steps.values():
        for x in s["requires"]:
            for y in s["produces"]:
                if _is_artifact(g, x) and _is_artifact(g, y) and x != y:
                    children.setdefault(x, set()).add(y)
    loop = _lineage_cycle([(c, p) for p, cs in children.items() for c in cs])
    if loop:
        return {"cycles": [], "lineage_cycle": loop}
    loops: Dict[str, List[List[str]]] = {}   # end artifact -> the loop chains reaching it
    for a in sorted(x for x in g["entities"] if _is_artifact(g, x) and _kind(g, x)):
        k = _kind(g, a)
        todo = [[a]]
        while todo:
            chain = todo.pop()
            for ch in sorted(children.get(chain[-1], ())):
                ck = _kind(g, ch)
                if not ck:
                    continue
                if ck == k:
                    if len(chain) > 1:   # through at least one other kind
                        loops.setdefault(ch, []).append(chain + [ch])
                else:
                    todo.append(chain + [ch])

    def kinds_of(c: List[str]) -> List[str]:   # consecutive same-kind steps are revisions: one kind
        ks: List[str] = []
        for x in c[:-1]:
            if not ks or ks[-1] != _kind(g, x):
                ks.append(_kind(g, x))
        return ks

    def side(x: List[str], y: List[str]) -> bool:   # x's path after its start lies inside y, which covers more kinds
        it = iter(y[1:])
        return len(kinds_of(x)) < len(kinds_of(y)) and all(n in it for n in x[1:])
    cycles: Dict[Tuple[str, ...], List[List[str]]] = {}
    for chains in loops.values():
        for c in chains:
            if any(side(c, o) for o in chains):
                continue
            kinds = kinds_of(c)
            r = min(range(len(kinds)), key=lambda i: key(kinds[i]))
            cycles.setdefault(tuple(kinds[r:] + kinds[:r]), []).append(c)
    out = [{"kinds": list(ks), "chains": sorted(cs)}
           for ks, cs in sorted(cycles.items(), key=lambda kv: (len(kv[0]), [key(k) for k in kv[0]]))]
    return {"cycles": out}


def _lineage_cycle(lineage: List[Tuple[str, str]]) -> Optional[List[str]]:
    """An instance lineage cycle, if one exists (the write check refuses them; the read reports)."""
    adj: Dict[str, List[str]] = {}
    for child, parent in lineage:
        adj.setdefault(child, []).append(parent)
    color: Dict[str, int] = {}
    for root in sorted(adj):
        if color.get(root):
            continue
        stack = [(root, iter(sorted(adj.get(root, []))))]
        path = [root]
        color[root] = 1
        while stack:
            n, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                color[n] = 2
                stack.pop()
                path.pop()
            elif color.get(nxt) == 1:
                return path[path.index(nxt):] + [nxt]
            elif not color.get(nxt):
                color[nxt] = 1
                path.append(nxt)
                stack.append((nxt, iter(sorted(adj.get(nxt, [])))))
    return None


def gaps(g: Dict[str, Any], steps: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """gaps: an ASSUMED concept that no deliverable TEACHES and no Library unit or work COVERS --
    [{concept, assumed_by: [{step, strength}]}]."""
    met = {r["target"] for r in g["relations"] if r["relation"] in ("TEACHES", "COVERS")}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for sid, s in steps.items():
        for c, strength in s["assumes"].items():
            if c not in met:
                out.setdefault(c, []).append({"step": sid, "strength": strength})
    return [{"concept": c, "assumed_by": sorted(v, key=lambda r: r["step"])} for c, v in sorted(out.items())]


def prepares_for(g: Dict[str, Any], steps: Dict[str, Dict[str, Any]], node: str) -> Dict[str, Any]:
    """What a work, a unit or a deliverable PREPARES YOU FOR (e2b3a414): the deliverables that
    assume a concept it covers or teaches, or require an artifact or environment it produces --
    {node, gives: [concepts / things], prepares: [{step, through: [...]}]}. A work counts its
    units' COVERS too."""
    e = g["entities"].get(node) or {}
    sources = {node}
    if e.get("entity_kind") == P.ENTITY_WORK:
        sources |= {i for i, x in g["entities"].items() if x.get("entity_kind") == P.ENTITY_UNIT
                    and str(x.get("key", "")).partition(P.UNIT_KEY_SEP)[0] == e.get("key")}
    gives = {r["target"] for r in g["relations"] if r["source"] in sources and r["relation"] in ("COVERS", "TEACHES")}
    if node in steps:
        gives |= set(steps[node]["produces"])
    prepares: Dict[str, Set[str]] = {}
    for sid, s in steps.items():
        if sid == node:
            continue
        through = (set(s["assumes"]) | set(s["requires"])) & gives
        if through:
            prepares[sid] = through
    return {"node": node, "gives": sorted(gives),
            "prepares": [{"step": sid, "through": sorted(t)} for sid, t in sorted(prepares.items())]}


def staleness(g: Dict[str, Any], steps: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Environment staleness (ae698640 (3)): a deliverable that requires or produces an environment
    and was VERIFIED_ON evidence naming an OLDER version of one of its components than the
    environment's active versions -- {stale: [{step, environment, hardware, os, date, components:
    [{component, verified, current}]}], incomparable: [...], conflicts: [environments with two
    active versions values]}."""
    stale, incomparable, conflicts = [], [], []
    current: Dict[str, Dict[str, str]] = {}
    for env, vals in sorted(g["versions"].items()):
        if len(vals) > 1:
            conflicts.append(env)
        else:
            current[env] = vals[0]
    by_step: Dict[str, List[Dict[str, Any]]] = {}
    for v in g["verifications"]:
        by_step.setdefault(v["deliverable"], []).append(v)
    for sid, s in sorted(steps.items()):
        envs = [x for x in list(s["requires"]) + s["produces"] if x in current]
        for env in sorted(set(envs)):
            for v in by_step.get(sid, []):
                comps, odd = [], []
                for comp, now in sorted(current[env].items()):
                    then = v["versions"].get(comp)
                    if then is None:
                        continue
                    a, b = P.version_key(then), P.version_key(now)
                    if a is None or b is None:
                        odd.append({"component": comp, "verified": then, "current": now})
                    elif a < b:
                        comps.append({"component": comp, "verified": then, "current": now})
                row = {"step": sid, "environment": env, "hardware": v["hardware"], "os": v["os"], "date": v["date"]}
                if comps:
                    stale.append({**row, "components": comps})
                if odd:
                    incomparable.append({**row, "components": odd})
    return {"stale": stale, "incomparable": incomparable, "conflicts": conflicts}


def split_candidates(g: Dict[str, Any], steps: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """SPLIT CANDIDATES (ae698640 (6)): a post turning A into B and B into C -- it PRODUCES B and C,
    C derived from B -- where another step also starts from B (REQUIRES it). [{step, at, then,
    also_from}] -- the structural check for the disposition pass and the refactor."""
    parents: Dict[str, Set[str]] = {}
    for child, parent in g["lineage"]:
        parents.setdefault(child, set()).add(parent)

    def derives(c: str, b: str) -> bool:
        todo, seen = [c], set()
        while todo:
            n = todo.pop()
            for p in parents.get(n, ()):
                if p == b:
                    return True
                if p not in seen:
                    seen.add(p)
                    todo.append(p)
        return False
    users: Dict[str, Set[str]] = {}
    for sid, s in steps.items():
        for x in s["requires"]:
            users.setdefault(x, set()).add(sid)
    out = []
    for sid, s in sorted(steps.items()):
        for b in sorted(s["produces"]):
            then = sorted(c for c in s["produces"] if c != b and derives(c, b))
            others = sorted(users.get(b, set()) - {sid})
            if then and others:
                out.append({"step": sid, "at": b, "then": then, "also_from": others})
    return out


def step_view(g: Dict[str, Any], steps: Dict[str, Dict[str, Any]], sid: str) -> Dict[str, Any]:
    """One step in context: its relations, what it continues from and what continues from it, its
    alternatives and analogues, its derived stage and task, and the companions explaining it or
    what it produces."""
    s = steps.get(sid) or {"requires": {}, "produces": [], "teaches": [], "assumes": {}, "explains": []}
    cont = continues_from(g, steps)
    st = stage_matches(g, steps)
    explained = sorted({r["source"] for r in g["relations"] if r["relation"] == "EXPLAINS"
                        and (r["target"] == sid or r["target"] in s["produces"])})
    return {"step": sid, **s,
            "continues_from": [c for c in cont if c["step"] == sid],
            "continued_by": [c for c in cont if c["from"] == sid],
            "alternatives": [a for a in alternatives(g, steps) if any(m["step"] == sid for m in a["steps"])],
            "analogues": [a for a in analogues(g, steps, st) if any(m["step"] == sid for m in a["steps"])],
            "stage": st.get(sid), "explained_by": explained}


async def path_reads(
    gx: GraphHandle,
    read: str = "all",   # One of READS
    *,
    node: str = "",      # step: the deliverable; prepares: a work / unit (kind:key) or a deliverable
) -> Dict[str, Any]:  # {read, ..., names: {id: {kind, key, name}}} | {error}
    """The `paths` read verb: one derived read, or all of them, with every node it mentions named."""
    if read not in READS:
        return {"error": f"no read `{read}` ({', '.join(READS)})"}
    g = await load_path_graph(gx)
    steps = steps_of(g)
    res: Dict[str, Any] = {"read": read}
    if read in ("step", "prepares"):
        if not node:
            if read == "step":
                return {"error": "the step read names a deliverable (id or post slug)"}
        else:
            nid = await resolve_endpoint(gx, node)
            if nid is None:
                return {"error": f"no node `{node}` (a deliverable id or slug, or kind:key)"}
            node = nid
    if read == "step":
        res["step_view"] = step_view(g, steps, node)
    st = stage_matches(g, steps)
    if read in ("all", "continues"):
        res["continues"] = continues_from(g, steps)
    if read in ("all", "alternatives"):
        res["alternatives"] = alternatives(g, steps)
    if read in ("all", "stages"):
        res["stages"] = st
    if read in ("all", "analogues"):
        res["analogues"] = analogues(g, steps, st)
    if read in ("all", "cycles"):
        res["cycles"] = flywheel_cycles(g, steps)
    if read in ("all", "gaps"):
        res["gaps"] = gaps(g, steps)
    if read == "prepares":
        roots = [node] if node else sorted(i for i, e in g["entities"].items() if e.get("entity_kind") == P.ENTITY_WORK)
        res["prepares"] = [prepares_for(g, steps, r) for r in roots]
    if read in ("all", "stale"):
        res["stale"] = staleness(g, steps)
    if read in ("all", "splits"):
        res["splits"] = split_candidates(g, steps)
    refusals = [{"step": sid, "reason": row["refusal"], "detail": f"inputs {row['inputs']} -> outputs {row['outputs']}"
                 + (f" · tasks {row['tasks']}" if len(row["tasks"]) > 1 else "")}
                for sid, row in st.items() if row.get("refusal")]
    for aid, e in sorted(g["entities"].items()):
        if e.get("entity_kind") == P.ENTITY_ARTIFACT:
            k = g["entities"].get(entity_node_id(P.ENTITY_ARTIFACT_KIND, str(e.get("artifact_kind"))))
            if k is None or k.get("retired"):
                refusals.append({"step": aid, "reason": "artifact kind",
                                 "detail": f"`{e.get('artifact_kind')}` is {'retired' if k else 'not live'}"})
    loop = _lineage_cycle(g["lineage"])
    if loop:
        refusals.append({"step": loop[0], "reason": "lineage cycle", "detail": " <- ".join(loop)})
    res["refusals"] = refusals
    res["ok"] = not refusals
    mentioned = (set(g["deliverables"]) | {x for r in g["relations"] for x in (r["source"], r["target"])}
                 | {x for pair in g["lineage"] for x in pair} | {v["hardware"] for v in g["verifications"]}
                 | set(g["versions"]) | {i for i, e in g["entities"].items()
                                         if e.get("entity_kind") in (P.ENTITY_ARTIFACT, P.ENTITY_WORK)})
    res["names"] = {i: _name(g, i) for i in sorted(mentioned)}
    res["steps"] = len(steps)
    return res
