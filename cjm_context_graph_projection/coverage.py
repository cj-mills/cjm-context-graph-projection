"""The Tutorials matrix: its vocabulary as graph data and the task x stage projection
(designs 8cbdc883 / c450133a under the coverage model de808eae (1)).

Each task and each stage is an Entity (sub-kind `task` / `stage`) minted by the journaled
`entity` op -- a WHOLE record upserted by (kind, key), last op wins -- so the axes change by
journaled ops (add, rename, reorder, retire), never by a schema release. A deliverable says
what it TEACHES with the multivalued `teaches_task` / `teaches_stage` facts, whose values are
vocabulary keys (checked at write time by `check_vocab_value`).

`coverage_matrix` derives everything the page and the gap list read, and stores nothing: the
rows (tasks by position, the off-grid ones listed apart), the columns (stages by position),
each cell's tutorials, the cells a CROSS-TASK stage leaves covered by the cross-task row, the
gaps, and the REFUSALS -- a tutorial with no task fact is unsurveyed, a value naming no live
vocabulary entry is unknown, an on-grid task with no stage is unstaged; each is refused
rather than silently dropped from the page (6752db0a (9))."""

from typing import Any, Dict, List, Optional, Tuple

from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import entity_node_id
from cjm_dev_graph_schema.nodes import EntityNode
from cjm_dev_graph_schema.vocab import DevNodeKinds

from . import factlayer as F
from .runtime import GraphHandle

# The fields each declared Entity sub-kind carries beyond its name (the journaled record):
# field -> its type. A kind absent here is refused, so a new kind is declared before use.
ENTITY_FIELDS: Dict[str, Dict[str, type]] = {
    P.ENTITY_TASK: {"description": str, "position": int, "cross_task": bool, "off_grid": bool,
                    "retired": bool},
    P.ENTITY_STAGE: {"description": str, "position": int, "cross_task": bool, "retired": bool},
}
_REQUIRED = {P.ENTITY_TASK: ("position",), P.ENTITY_STAGE: ("position",)}
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
    unknown = sorted(set(fields) - set(spec))
    if unknown:
        return f"`{kind}` carries no field(s) {', '.join(unknown)} (declared: {', '.join(spec)})"
    for f, t in spec.items():
        if f not in fields:
            continue
        v = fields[f]
        if not isinstance(v, t) or (t is int and isinstance(v, bool)):   # a bool is no position
            return f"`{kind}` field `{f}` must be {t.__name__} (got {v!r})"
    missing = [f for f in _REQUIRED.get(kind, ()) if f not in fields]
    if missing:
        return f"`{kind}` needs {', '.join(missing)}"
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
    return {"entity_id": eid, "kind": kind, "key": key, "name": name, "fields": props,
            "updated": existing is not None, "written": True}


async def check_vocab_value(
    gx: GraphHandle,
    predicate: str,  # The predicate being asserted
    value: str,      # The claimed value
) -> Optional[str]:  # An error, or None (also None for a non-coverage predicate)
    """A coverage value must name a live vocabulary entry of the predicate's kind, so a
    typo'd or retired key never lands (the projection refuses one that goes stale later)."""
    kind = P.COVERAGE_KINDS.get(predicate)
    if kind is None:
        return None
    node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=entity_node_id(kind, value))
    if node is None or F.prop(node, "entity_kind") != kind:
        return (f"`{value}` is no {kind} in the vocabulary — mint it first "
                f"(`entity {kind} {value} --name ...`), or check the key")
    if F.prop(node, "retired"):
        return f"{kind} `{value}` is retired — assert its successor instead"
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
) -> Dict[str, Any]:  # The matrix, the covered cells, the gaps, the off-grid list, the refusals
    """The pure projection (no graph access): see the module docstring for the rules."""
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
        for t in ts:
            if task_keys[t].get("off_grid"):
                off_grid.setdefault(t, []).append(nid)
        if grid and not ss:
            refusals.append({"id": nid, "reason": "unstaged",
                             "detail": f"teaches {', '.join(grid)} but no teaches_stage fact"})
            continue
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
            "tutorials": tutorials, "ok": not refusals}


async def coverage_matrix(
    gx: GraphHandle,
) -> Dict[str, Any]:  # project_matrix's result over the live graph
    """The Tutorials matrix over the graph: every deliverable whose type's kind is
    `tutorial`, against the live vocabulary."""
    from .purenotes import note_types
    vocab = await load_vocab(gx)
    typed = await note_types(gx)
    ids = [nid for nid, t in typed.items() if t.get("kind") == TUTORIAL_KIND]
    tutorials: Dict[str, Dict[str, Any]] = {}
    for nid in ids:
        node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=nid)
        tutorials[nid] = {"title": F.prop(node, "title") or "", "slug": F.prop(node, "slug") or ""}
    return project_matrix(vocab[P.ENTITY_TASK], vocab[P.ENTITY_STAGE], tutorials,
                          await load_coverage_facts(gx))
