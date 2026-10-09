"""The claims the site may make about the user's work, and the deliverables that back them
(the coverage model de808eae (1), design amendment 98e99fe5; the claims list bb927799 and the
ruling 676bac8e).

A claim is an Entity (sub-kind `claim`, minted by the journaled `entity` op like the task, stage
and hardware vocabulary): its record is a short statement and a display position. Its STATE is
the `claim_state` fact with history -- `offered` (the site makes it), `building` (internal: gap
priority and the staging profile), `retired`. A deliverable backs a claim with one SUPPORTS edge
per (deliverable, claim), the support KIND on the edge (outcome / method / capability /
knowledge) with a one-line note -- written by the journaled `supports` op.

`claims_report` derives, and stores nothing: each claim with its state and its backing grouped
by kind, and the REFUSALS -- a claim with no state (`stateless`), two active states
(`conflict`), and an offered claim with no PUBLIC support whose kind carries an offer
(`unbacked`: the backing floor of 98e99fe5 (3); knowledge alone never carries one). The floor
never promotes: a building claim that meets it stays building. Only CURRENT support counts
(design amendment cbd5f154 (6)): a supporter that is archived, removed or retired has its support
WITHDRAWN -- listed apart, never counted -- and a superseded supporter's support PASSES to its
public successor (a row carrying `via`), each read off the supporter's standing (`standing`).
`public_view` is the public profile's filter -- only offered claims, only public supports, never
the withdrawn -- and every public surface reads through it (676bac8e (4))."""

from typing import Any, Dict, List, Optional, Tuple

from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.query import EdgeQuery
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import entity_node_id
from cjm_dev_graph_schema.nodes import supports_edge
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from . import factlayer as F
from .runtime import GraphHandle


async def load_supports(
    gx: GraphHandle,
) -> List[Dict[str, Any]]:  # Every SUPPORTS edge as a plain dict
    """The support edges (deliverable -> claim) with their kind and note."""
    res = await graph_task(gx.queue, gx.graph_id, "query_edges",
                           query=EdgeQuery(relation_type=DevRelations.SUPPORTS).to_dict())
    raw = getattr(res, "edges", None) or getattr(res, "rows", None) or []
    return [e.to_dict() if hasattr(e, "to_dict") else dict(e) for e in raw]


async def load_claims(
    gx: GraphHandle,
) -> List[Dict[str, Any]]:  # Every claim Entity's properties + id, by position
    """The claim Entities in display order."""
    from .coverage import _ordered
    out = []
    for n in await F.load_label(gx, DevNodeKinds.ENTITY):
        p = F.props(n)
        if p.get("entity_kind") == P.ENTITY_CLAIM:
            out.append({**p, "id": F.nid(n)})
    return _ordered(out)


async def load_claim_states(
    gx: GraphHandle,
) -> Dict[str, List[str]]:  # {claim id: sorted ACTIVE claim_state values}
    """Every claim's active state(s); more than one is a conflict the report refuses."""
    slot = [a for a in await F.load_assertions(gx) if F.prop(a, "predicate") == P.CLAIM_STATE]
    out: Dict[str, List[str]] = {}
    for a in F.active_assertions(slot, await F.load_supersedes(gx)):
        out.setdefault(str(F.prop(a, "subject_id")), []).append(str(F.prop(a, "value")))
    return {k: sorted(set(v)) for k, v in out.items()}


async def record_support(
    gx: GraphHandle,
    deliverable: str,        # The deliverable's node id, or a Note's slug
    claim: str,              # The claim Entity's key
    *,
    kind: str = "",          # outcome | method | capability | knowledge
    note: str = "",          # Why this deliverable backs the claim, in one line
    retract: bool = False,   # Remove this (deliverable, claim) support
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {edge_id, deliverable_id, claim_id, written} | {error, written: False}
    """Write one SUPPORTS edge with its kind (journaled `supports`), or retract it. A pair has
    one kind, so a restatement replaces it (the journal keeps the history)."""
    from .coverage import resolve_deliverable
    if not retract and kind not in P.SUPPORT_KINDS:
        return {"error": f"kind must be one of {', '.join(P.SUPPORT_KINDS)} (got {kind!r})", "written": False}
    did = await resolve_deliverable(gx, deliverable)
    if did is None:
        return {"error": f"no deliverable Note `{deliverable}` (a node id or a post slug)", "written": False}
    cid = entity_node_id(P.ENTITY_CLAIM, claim)
    node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=cid)
    if node is None or F.prop(node, "entity_kind") != P.ENTITY_CLAIM:
        return {"error": f"no claim `{claim}` — mint it first (`entity claim {claim} ...`)", "written": False}
    edge = supports_edge(did, cid, kind=kind, note=note)
    standing = [e for e in await load_supports(gx) if str(e["id"]) == edge["id"]]
    if standing:
        await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=[edge["id"]])
    if retract:
        return {"edge_id": edge["id"], "deliverable_id": did, "claim_id": cid, "retracted": bool(standing),
                "written": bool(standing), **({} if standing else {"error": "no such support to retract"})}
    await extend_graph(gx.queue, gx.graph_id, [], [edge])
    return {"edge_id": edge["id"], "deliverable_id": did, "claim_id": cid, "claim": claim, "kind": kind,
            "replaced": bool(standing), "written": True}


def project_claims(
    claims: List[Dict[str, Any]],             # Claim entries (properties + id), by position
    states: Dict[str, List[str]],             # {claim id: active states}
    supports: List[Dict[str, Any]],           # SUPPORTS edges (source_id, target_id, properties)
    deliverables: Dict[str, Dict[str, Any]],  # {note id: {title, slug, public}} -- supporters and their successors
    standing: Optional[Dict[str, Dict[str, Any]]] = None,  # {supporter id: standing.support_standing(...)}; None = every support counts
) -> Dict[str, Any]:  # {claims: [...], refusals: [...], ok}
    """The pure projection (no graph access): see the module docstring for the rules."""
    by_claim: Dict[str, Dict[Tuple[str, str], Dict[str, Any]]] = {}
    withdrawn: Dict[str, List[Dict[str, Any]]] = {}

    def row_of(nid: str, kind: str, note: str) -> Dict[str, Any]:
        d = deliverables.get(nid) or {}
        return {"id": nid, "title": d.get("title", ""), "slug": d.get("slug", ""),
                "public": bool(d.get("public")), "kind": kind, "note": note}

    passed = []
    for e in supports:
        props = e.get("properties") or {}
        src, cid = str(e["source_id"]), str(e["target_id"])
        row = row_of(src, props.get("kind", ""), props.get("note", ""))
        ss = (standing or {}).get(src) or {}
        if not ss.get("withdrawn"):
            by_claim.setdefault(cid, {})[(src, row["kind"])] = row
            continue
        withdrawn.setdefault(cid, []).append({**row, "withdrawn": ss["withdrawn"],
                                              "passes_to": list(ss.get("passes_to") or [])})
        passed += [(cid, s["id"], row, src) for s in ss.get("passes_to") or []]
    for cid, sid, row, src in passed:   # a superseded page's support passes to its successor; its own wins
        mine = by_claim.setdefault(cid, {})
        have = mine.get((sid, row["kind"]))
        if have is None:
            mine[(sid, row["kind"])] = {**row_of(sid, row["kind"], row["note"]), "via": [src]}
        elif "via" in have and src not in have["via"]:
            have["via"].append(src)
    out, refusals = [], []
    for c in claims:
        vals = states.get(c["id"], [])
        state = vals[0] if len(vals) == 1 else None
        rows = sorted(by_claim.get(c["id"], {}).values(), key=lambda r: (r["slug"], r["id"]))
        grouped = {k: [r for r in rows if r["kind"] == k] for k in P.SUPPORT_KINDS}
        backed = any(r["public"] and r["kind"] in P.BACKING_KINDS for r in rows)
        entry = {"key": c.get("key"), "id": c["id"], "name": c.get("name", ""),
                 "statement": c.get("statement", ""), "position": c.get("position"),
                 "state": state, "supports": grouped, "backed": backed,
                 "withdrawn": sorted(withdrawn.get(c["id"], []), key=lambda r: (r["withdrawn"], r["slug"], r["id"]))}
        if not vals:
            refusals.append({"claim": c.get("key"), "reason": "stateless", "detail": "no claim_state fact"})
        elif len(vals) > 1:
            entry["conflict"] = vals
            refusals.append({"claim": c.get("key"), "reason": "conflict", "detail": " / ".join(vals)})
        elif state == P.CLAIM_OFFERED and not backed:
            refusals.append({"claim": c.get("key"), "reason": "unbacked",
                             "detail": "offered, but no public support of kind "
                                       + " / ".join(P.BACKING_KINDS)})
        out.append(entry)
    return {"claims": out, "refusals": refusals, "ok": not refusals}


def public_view(
    report: Dict[str, Any],  # project_claims' result
) -> List[Dict[str, Any]]:  # The offered claims, each with only its public supports
    """The public profile's filter (676bac8e (4)): building and retired claims, and supports
    from deliverables that are not public, never reach a public surface."""
    out = []
    for c in report["claims"]:
        if c.get("state") != P.CLAIM_OFFERED:
            continue
        out.append({**{k: v for k, v in c.items() if k != "withdrawn"},
                    "supports": {k: [r for r in rows if r["public"]] for k, rows in c["supports"].items()}})
    return out


async def claims_report(
    gx: GraphHandle,
    public: bool = False,  # True = the public view (offered claims, public supports only)
    standing: Optional[Dict[str, Any]] = None,  # standing.load_standing's result, when the caller holds it
) -> Dict[str, Any]:  # project_claims' result (+ `public` = the filtered claims when asked)
    """The claims over the live graph: every claim Entity with its state and its CURRENT backing
    (cbd5f154 (6)) -- each supporter's standing withdraws its support or passes it on."""
    from .purenotes import public_deliverables
    from .site import stated
    from .standing import load_standing, support_standing
    supports = await load_supports(gx)
    by_id = (standing or await load_standing(gx))["by_id"]
    sources = sorted({str(e["source_id"]) for e in supports})
    ss = {i: support_standing(by_id.get(i)) for i in sources}
    ids = sorted(set(sources) | {s["id"] for v in ss.values() for s in v["passes_to"]})
    nodes = await F.load_nodes(gx, ids)
    pub = await public_deliverables(gx)
    deliverables = {i: {"title": stated(nodes.get(i), "title"),
                        "slug": str(F.prop(nodes.get(i), "slug") or ""), "public": i in pub}
                    for i in ids}
    res = project_claims(await load_claims(gx), await load_claim_states(gx), supports, deliverables, ss)
    if public:
        res["public"] = public_view(res)
    return res
