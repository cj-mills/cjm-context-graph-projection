"""The write surface: `assert` a slot value, `decide` a conclusion.

Both are idempotent (deterministic ids + `extend_graph`): re-asserting the same
value (same actor) is a verified no-op; a different value mints a new Assertion =
the potential conflict. Subjects resolve through the rename-stable alias machinery
(old names + variant slugs all land on one entity); an unresolved subject mints a
lightweight `term` Entity rather than failing.

Write-time conflict UX = WARN-RECORD-FLAG, never block (the arc-plan lock): the
assertion is written, a detected hard conflict is RECORDED as CONTRADICTS edges
and RETURNED in the result, and the caller is forced to see it without a hard stop.
Ordered predicates (version) auto-supersede older values, so a healthy bump is
never a conflict; unordered predicates (rename-disposition) flag genuine
disagreement; untyped predicates report a SOFT signal (worklist, not a hard edge).
"""

import re
from typing import Any, Dict, List, Optional

from cjm_context_graph_layer.grammar import make_edge
from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.provenance import SourceRef
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.aliases import resolve_subject_id
from cjm_dev_graph_schema.identity import note_node_id, section_node_id
from cjm_dev_graph_schema.nodes import (AssertionNode, CheckNode, DecisionNode, EntityNode,
                                        FactSlotNode, SessionNode)
from cjm_dev_graph_schema.vocab import DevRelations

from . import factlayer as F
from .projection import ambiguity_error, resolve_node_ref
from .runtime import GraphHandle

# A subject shaped like a PARTIAL node id (hex+dashes, >=6 chars) but NOT a full
# UUID. Gates the never-mint rule: an unresolved PREFIX is a typo'd reference (the
# 2026-07-02 register footgun minted a term named `77f55f42`), while an unresolved
# FULL UUID keeps the legacy mint path — asserting onto a deterministic id before
# its entity node exists is a legitimate pattern (repo_purpose resolves the same
# way, so both sides converge on the same subject).
_ID_PREFIX_SHAPED_RE = re.compile(r"^[0-9a-fA-F][0-9a-fA-F-]{5,35}$")
_FULL_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                           r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _term_slug(text: str) -> str:
    """A stable conceptual slug for an unresolved subject (mint a `term` entity)."""
    s = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return s or "unnamed"


async def resolve_subject(
    gx: GraphHandle,
    subject: str,  # A node id, an entity key/name/alias, or a free term
) -> Dict[str, Any]:  # {subject_id, subject_label, created_node|None}
    """Resolve a subject to an entity id (rename-stable), minting a `term` entity
    if it resolves to nothing — so `assert` always has a real subject node."""
    # 1. Already a node id — full, or a unique id PREFIX (the read verbs accept
    # prefixes, so the write surface must too: a prefix falling through to the
    # term-minting fallback silently asserts onto a phantom `term` entity named
    # like a hex chunk — the 2026-07-02 register footgun). Ambiguity is an ERROR,
    # never a guess and never a mint.
    res = await resolve_node_ref(gx, subject)
    if "candidates" in res:
        return {"error": ambiguity_error(subject, res["candidates"]), "subject_id": None}
    node = res.get("node")
    if node is not None:
        p = F.props(node)
        label = (p.get("name") or p.get("title") or p.get("key") or subject)
        return {"subject_id": F.nid(node), "subject_label": label, "created_node": None}
    # 2. Resolve via the alias index (key / current name / prior name / variant slug).
    index, _ = await F.alias_index(gx)
    rid = resolve_subject_id(index, subject)
    if rid is not None:
        return {"subject_id": rid, "subject_label": subject, "created_node": None}
    # 3. A PREFIX-shaped subject that resolved to nothing is a typo'd reference, not
    # a concept — minting a hex-named term would be the same phantom, so fail loud.
    # (A full unresolved UUID falls through to the legacy mint, per the note above.)
    if _ID_PREFIX_SHAPED_RE.match(subject) and not _FULL_UUID_RE.match(subject):
        return {"error": f"subject `{subject}` is shaped like a node-id prefix but "
                         f"matches no node (and no alias) — not minting a term entity",
                "subject_id": None}
    # 4. Unresolved -> mint a term entity (don't fail; don't guess an existing one).
    ent = EntityNode(kind="term", key=_term_slug(subject), name=subject)
    return {"subject_id": ent.id, "subject_label": subject, "created_node": ent.to_graph_node()}


def _match_supersede_targets(
    targets: List[str],            # Caller-named ids OR values to supersede
    slot_assertions: List[Any],    # Existing assertions in the slot
    predicate: str,
) -> List[str]:
    """Resolve `--supersede` tokens (assertion ids OR values) to assertion ids."""
    by_id = {F.nid(a): a for a in slot_assertions}
    by_canon: Dict[str, str] = {}
    for a in slot_assertions:
        by_canon.setdefault(P.canonical_value(predicate, F.prop(a, "value", "")), F.nid(a))
    out: List[str] = []
    for t in targets:
        if t in by_id:
            out.append(t)
        else:
            cid = by_canon.get(P.canonical_value(predicate, t))
            if cid:
                out.append(cid)
    return out


async def assert_value(
    gx: GraphHandle,
    subject: str,        # Subject (node id / entity key / name / alias / term)
    predicate: str,      # Predicate slug
    value: str,          # The claimed value
    *,
    actor: str = "agent:session",       # Who is claiming it
    evidence: Optional[List[str]] = None,  # Source-note/session/evidence node ids supporting the claim
    supersede: Optional[List[str]] = None, # Prior assertion ids OR values this claim supersedes
    asserted_at: Optional[float] = None,   # Override the timestamp (oracle uses last_verified semantics)
    method: Optional[str] = None,          # Derivation method (oracle/programmatic)
) -> Dict[str, Any]:  # The write result (incl. any conflict, warn-record-flag)
    """Write one value to a `(subject, predicate)` slot, recording any conflict.

    Auto-supersedes older values on ordered predicates; records CONTRADICTS +
    returns the conflict on unordered disagreement; reports a soft signal on
    untyped disagreement. Idempotent on re-assertion of the same value+actor."""
    r = await resolve_subject(gx, subject)
    if r.get("error"):
        return {"error": r["error"], "subject": subject, "predicate": predicate,
                "value": value, "written": False}
    subject_id, subject_label, created = r["subject_id"], r["subject_label"], r["created_node"]

    slot = FactSlotNode(subject_id=subject_id, predicate=predicate, subject_label=subject_label)
    assertion = AssertionNode(slot_id=slot.id, value=value, actor=actor, predicate=predicate,
                              subject_id=subject_id, asserted_at=asserted_at, method=method,
                              last_verified=(asserted_at if method else None))

    # Existing state of the slot BEFORE this write.
    all_assertions = await F.load_assertions(gx)
    supers = await F.load_supersedes(gx)
    slot_existing = [a for a in all_assertions if F.prop(a, "slot_id") == slot.id]
    active_existing = F.active_assertions(slot_existing, supers)

    nodes: List[Dict[str, Any]] = []
    if created:
        nodes.append(created)
    nodes.append(slot.to_graph_node())
    nodes.append(assertion.to_graph_node())

    edges: List[Dict[str, Any]] = [slot.about_edge(), assertion.on_slot_edge()]
    edges += assertion.evidenced_by_edges(evidence or [])

    superseded_ids: List[str] = []
    born_superseded = False

    # Ordered predicate: newer auto-supersedes older active values (healthy evolution).
    if P.is_ordered(predicate):
        for old in active_existing:
            old_id, old_val = F.nid(old), F.prop(old, "value", "")
            if old_id == assertion.id:
                continue
            verdict = P.ordering_supersedes(predicate, value, old_val)
            if verdict is True:
                edges.append(assertion.supersedes_edge(old_id))
                superseded_ids.append(old_id)
            elif verdict is False:
                # New value is older than an existing active one -> born superseded.
                edges.append(make_edge(old_id, assertion.id, DevRelations.SUPERSEDES))
                born_superseded = True

    # Explicit supersede targets (ids or values).
    for tid in _match_supersede_targets(supersede or [], slot_existing, predicate):
        if tid != assertion.id and tid not in superseded_ids:
            edges.append(assertion.supersedes_edge(tid))
            superseded_ids.append(tid)

    res = await extend_graph(gx.queue, gx.graph_id, nodes, edges)

    # Write-time conflict check (warn-record-flag): recompute the active set.
    all_after = await F.load_assertions(gx)
    supers_after = await F.load_supersedes(gx)
    slot_after = [a for a in all_after if F.prop(a, "slot_id") == slot.id]
    active_after = F.active_assertions(slot_after, supers_after)

    conflict: List[Dict[str, Any]] = []
    soft = False
    if not born_superseded:
        active_vals = [F.prop(a, "value", "") for a in active_after]
        if P.active_contradiction(predicate, active_vals):
            my_canon = assertion.canonical
            conflict_edges: List[Dict[str, Any]] = []
            for a in active_after:
                if F.nid(a) == assertion.id:
                    continue
                if P.canonical_value(predicate, F.prop(a, "value", "")) != my_canon:
                    conflict.append({"assertion_id": F.nid(a), "value": F.prop(a, "value"),
                                     "actor": F.prop(a, "actor")})
                    conflict_edges.append(assertion.contradicts_edge(F.nid(a)))
            if conflict_edges:
                await extend_graph(gx.queue, gx.graph_id, [], conflict_edges)
        elif P.soft_conflict(predicate, active_vals):
            soft = True

    return {
        "subject": subject, "subject_id": subject_id, "slot_id": slot.id,
        "predicate": predicate, "value": value, "actor": actor,
        "assertion_id": assertion.id, "created_subject": created is not None,
        "nodes_added": res.nodes_added, "edges_added": res.edges_added,
        "superseded": superseded_ids, "born_superseded": born_superseded,
        "conflict": conflict, "soft_conflict": soft,
    }


async def alias(
    gx: GraphHandle,
    drifted_slug: str,     # The drifted `[[wiki-link]]` slug that resolves to no note
    canonical_slug: str,   # The real note slug it means (its frontmatter `name`)
    *,
    actor: str = "agent:session",       # Who confirmed the equivalence
    evidence: Optional[List[str]] = None,  # Source-note ids that carried the broken link (auto-discovered upstream)
) -> Dict[str, Any]:  # The write result (incl. error when the canonical note is absent)
    """Confirm a drifted link slug as an alias OF a real note (the worklist payoff).

    A confirmed equivalence is born on-graph as an `aka` Assertion on the canonical
    note's `(note, aka)` slot — multivalued, so a note accrues many aliases without
    conflict. Ingest then resolves the drifted reference through it (the dangling
    edge heals) and the worklist drops it. The canonical note MUST exist (we never
    mint a phantom for a confirmed target); evidence is the notes the broken link
    appeared in (provenance of the rot). Idempotent on (note, alias, actor)."""
    canonical_id = note_node_id(canonical_slug)
    target = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=canonical_id)
    if target is None:
        return {"error": f"no note `{canonical_slug}` to alias to", "drifted": drifted_slug,
                "canonical": canonical_slug, "written": False}
    res = await assert_value(gx, canonical_id, "aka", drifted_slug,
                             actor=actor, evidence=evidence)
    return {"drifted": drifted_slug, "canonical": canonical_slug, "canonical_id": canonical_id,
            "actor": actor, "evidence": evidence or [], "assertion_id": res["assertion_id"],
            "nodes_added": res["nodes_added"], "edges_added": res["edges_added"], "written": True}


async def decide(
    gx: GraphHandle,
    statement: str,   # The decision statement
    *,
    actor: str = "agent:session",
    supports: Optional[List[str]] = None,    # Premise Assertion ids the decision rests on
    supersedes: Optional[List[str]] = None,  # Prior Decision ids this one replaces
    session: Optional[str] = None,           # Session key this was decided in
    title: Optional[str] = None,             # Explicit display title (tier-1 override; else the first-clause extractor)
) -> Dict[str, Any]:  # The write result
    """Record a Decision + its `SUPPORTED_BY` premise edges (reasoning substrate).

    Minimal in the cut: bank the premise edges now; the premise-drift checker is
    deferred. Idempotent on the canonical statement."""
    decision = DecisionNode(statement=statement, actor=actor)
    node = decision.to_graph_node()
    if title:
        node["properties"]["display_title"] = title
    nodes: List[Dict[str, Any]] = [node]
    edges: List[Dict[str, Any]] = decision.supported_by_edges(supports or [])
    edges += [decision.supersedes_edge(s) for s in (supersedes or [])]
    if session:
        sess = SessionNode(key=session)
        nodes.append(sess.to_graph_node())
        edges.append(decision.decided_in_edge(sess.id))
    res = await extend_graph(gx.queue, gx.graph_id, nodes, edges)
    return {"decision_id": decision.id, "statement": statement, "actor": actor,
            "supports": supports or [], "supersedes": supersedes or [],
            "session": session, "title": title,
            "nodes_added": res.nodes_added, "edges_added": res.edges_added}


async def link(
    gx: GraphHandle,
    source_id: str,    # The source node: full id, or a unique id prefix (must already exist)
    target_id: str,    # The target node: full id, or a unique id prefix (must already exist)
    relation: str,     # The edge relation (any string; the grammar is open by design)
    *,
    actor: str = "agent:session",  # Who asserted the link (recorded on the edge, not its identity)
) -> Dict[str, Any]:  # The write result (incl. error when an endpoint is missing/ambiguous)
    """Mint a deliberate edge between two EXISTING nodes (heterogeneous interlink).

    The general-purpose connector behind the larger context-graph vision: any node
    kind may link to any other (a Decision -> the CodeSymbol that implements it; a
    future debt node -> a code node; a cross-project reference -> another graph's
    symbol). The relation is a free string — the edge grammar is intentionally open,
    with no node-kind-pair validation. Both endpoints MUST exist (a deliberate link
    is never left dangling — that distinguishes it from a `[[ref]]`) and resolve like
    every other id-taking verb (unique prefix ok; ambiguity is a loud error, never a
    guess — the 66fffba6 asymmetry fix). The result carries the RESOLVED ids (what
    the journal must record); the edge id is deterministic from (source, relation,
    target), so re-linking is a no-op."""
    src_res = await resolve_node_ref(gx, source_id)
    tgt_res = await resolve_node_ref(gx, target_id)
    for ref, res in ((source_id, src_res), (target_id, tgt_res)):
        if "candidates" in res:
            return {"error": ambiguity_error(ref, res["candidates"]), "source_id": source_id,
                    "target_id": target_id, "relation": relation, "written": False}
    src, tgt = src_res.get("node"), tgt_res.get("node")
    missing = [ref for ref, node in ((source_id, src), (target_id, tgt)) if node is None]
    if missing:
        return {"error": f"missing node(s): {missing}", "source_id": source_id,
                "target_id": target_id, "relation": relation, "written": False}
    source_id, target_id = F.nid(src), F.nid(tgt)  # the RESOLVED endpoint ids

    def _label(node: Any) -> str:
        p = F.props(node)
        return str(p.get("display_title") or p.get("title") or p.get("name")
                   or p.get("statement") or "")[:120]

    edge = make_edge(source_id, target_id, relation, properties={"actor": actor})
    res = await extend_graph(gx.queue, gx.graph_id, [], [edge])
    return {"source_id": source_id, "target_id": target_id, "relation": relation,
            "actor": actor, "edge_id": edge["id"], "edges_added": res.edges_added,
            "source_label": _label(src), "target_label": _label(tgt),
            "written": True}


async def add_check(
    gx: GraphHandle,
    item: str,        # The work item (node id, or a unique id prefix)
    text: str,        # The check statement
    *,
    actor: str = "agent:session",  # Who attached it
) -> Dict[str, Any]:  # The write result (incl. error when the item is missing/ambiguous)
    """Attach a definition-of-done check to a work item (DoD-as-graph-objects).

    One journaled op = Check node + `CHECKS` edge + a `task_state=open` assertion,
    so the check is born on the same derivation machinery as the items it verifies.
    Close it later with `assert <check-id> task_state done --evidence <proof>`; the
    readiness projector derives `closable` (open item, all checks done) and `drift`
    (done item, open checks) — never ready/blocked, which a DoD must not gate.
    Deterministic (item, text) identity makes re-attaching (and journal replay) a
    verified no-op; a replayed `open` after a later `done` lands born-superseded
    (ordered-predicate supersession), so a rebuild converges on the true state.
    The item MUST exist — a check is never left dangling (like `link`)."""
    res = await resolve_node_ref(gx, item)
    if "candidates" in res:
        return {"error": ambiguity_error(item, res["candidates"]), "item": item,
                "text": text, "written": False}
    node = res.get("node")
    if node is None:
        return {"error": f"no node `{item}` to attach a check to", "item": item,
                "text": text, "written": False}
    item_id = F.nid(node)
    props = F.props(node)
    item_label = str(props.get("display_title") or props.get("title")
                     or props.get("name") or props.get("statement") or item_id)
    check = CheckNode(item_id=item_id, text=text, actor=actor)
    await extend_graph(gx.queue, gx.graph_id, [check.to_graph_node()], [check.checks_edge()])
    state = await assert_value(gx, check.id, P.TASK_STATE, P.TASK_OPEN, actor=actor)
    return {"check_id": check.id, "item_id": item_id, "item_label": item_label,
            "text": text, "actor": actor,
            "assertion_id": state.get("assertion_id"), "written": True}


async def author_section(
    gx: GraphHandle,
    slug: str,                     # The note's stable slug (frontmatter `name`) — durable identity
    anchor: str,                   # The section's heading anchor ("_preamble" for the level-0 span)
    raw: str,                      # The section's verbatim heading-inclusive `raw` STATE (not a diff)
    *,
    actor: str = "agent:session",  # Who authored it (deliberate `author` vs `reconcile:absorb`)
) -> Dict[str, Any]:  # The write result (incl. error when the section is absent)
    """Apply a memory section's verbatim `raw` STATE to the graph — the born-on-graph leg
    behind M2b (true-B for memory): new memory content lives in the write JOURNAL and the
    `.md` becomes a projection of it. The journaled, replayable verb behind shadow authoring.

    The GRAPH-ONLY mutation (no file emit — that is `author`/`emit`'s job, or the `.md` is the
    generated projection under cutover): sets the Section node's `raw` (+ content_hash) via
    `update_node`, NOT `extend_graph`. Deliberate — a plain re-extend of a changed node raises
    the content-hash integrity guard, so the journaled STATE must be applied by mutation (the
    section-divergence probe characterized exactly this). Replay-idempotent: re-applying the
    same raw is a verified no-op. The section MUST already exist — minting a NEW section/note is
    the deferred M2a gradient; a missing section is reported, never silently created."""
    note_id = note_node_id(slug)
    section_id = section_node_id(note_id, anchor)
    existing = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=section_id)
    if existing is None:
        return {"error": f"no section `{anchor}` on note `{slug}` (new-section authoring is deferred)",
                "slug": slug, "anchor": anchor, "written": False}
    unchanged = str(F.prop(existing, "raw", "")) == raw
    merge = {"raw": raw, "content_hash": SourceRef.compute_hash(raw.encode("utf-8"))}
    await graph_task(gx.queue, gx.graph_id, "update_node", node_id=section_id, properties=merge)
    return {"slug": slug, "anchor": anchor, "section_id": section_id, "actor": actor,
            "unchanged": unchanged, "written": True}


async def register_session(
    gx: GraphHandle,
    key: str,           # Stable session key (the start-time timestamp, e.g. 2026-07-08_10-58-13)
    *,
    started_at: Optional[float] = None,  # Unix start time (the window anchor; end is DERIVED, never stored)
    title: Optional[str] = None,         # Human-friendly name — asserted at session END, like decision titles
    actor: str = "agent:session",
) -> Dict[str, Any]:  # The write result
    """Register/update a timestamp-keyed Session node — the session SPINE (DEC 6124d8bf).

    Upsert like `set_display_rule` (sessions are data, not content): the journal's last
    `session` op per key wins on replay, so end-of-session naming is an ordinary
    re-register with --title. Existing properties are MERGED — a later title write must
    not clobber `started_at`. The window's END is derived at read time (next session's
    start / last write), never stored: a stored end would drift from the journal it
    summarizes. Pre-spine NAMED Sessions stay as phases (PHASE_OF), not competitors."""
    sess = SessionNode(key=key, title=title or "")
    node = sess.to_graph_node()
    props = node["properties"]
    props["actor"] = actor
    if started_at is not None:
        props["started_at"] = float(started_at)
    if title:
        props["display_title"] = title
    existing = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=sess.id)
    if existing is not None:
        cur = (existing.get("properties") if isinstance(existing, dict)
               else getattr(existing, "properties", None)) or {}
        merged = dict(cur)
        merged.update(props)
        await graph_task(gx.queue, gx.graph_id, "update_node", node_id=sess.id,
                         properties=merged)
        return {"session_id": sess.id, "key": key, "updated": True, "written": True,
                "started_at": merged.get("started_at"), "title": merged.get("display_title")}
    await extend_graph(gx.queue, gx.graph_id, [node], [])
    return {"session_id": sess.id, "key": key, "updated": False, "written": True,
            "started_at": props.get("started_at"), "title": title or None}


async def unlink(
    gx: GraphHandle,
    source_id: str,    # The source node: full id, or a unique id prefix (must resolve)
    target_id: str,    # The target node: full id, or a unique id prefix (must resolve)
    relation: str,     # The edge relation to retract
    *,
    actor: str = "agent:session",  # Who retracted it (journal provenance; the edge itself is gone)
) -> Dict[str, Any]:  # The write result (incl. error when an endpoint is missing/ambiguous)
    """RETRACT a deliberate edge — the write dual of `link` (finding 2f1d9382).

    A mis-minted link was journal-permanent: the journal is append-only by design,
    so retraction must be an OP, not an erasure — replay applies the unlink in
    append order AFTER the link it retracts, and a rebuild converges with the
    edge absent. The edge id is deterministic from (source, relation, target),
    so the retraction derives it rather than looking it up; a missing edge is a
    tolerated no-op (`deleted: 0`) — that is what makes replay idempotent.
    Endpoints resolve like `link` (unique prefix ok, ambiguity loud). A missing
    endpoint is fatal only for PREFIX refs (the edge id cannot be derived) — a
    FULL-id triple retracts anyway (finding 78cff95f): the db edge is already
    gone (cascade), but the JOURNALED link op would poison the orphan audit
    forever and resurrect the edge if the endpoint id ever re-resolved, so the
    compensating op must stay recordable."""
    src_res = await resolve_node_ref(gx, source_id)
    tgt_res = await resolve_node_ref(gx, target_id)
    for ref, res in ((source_id, src_res), (target_id, tgt_res)):
        if "candidates" in res:
            return {"error": ambiguity_error(ref, res["candidates"]), "source_id": source_id,
                    "target_id": target_id, "relation": relation, "written": False}
    src, tgt = src_res.get("node"), tgt_res.get("node")
    missing = [ref for ref, node in ((source_id, src), (target_id, tgt)) if node is None]
    if missing and not (_FULL_UUID_RE.match(source_id) and _FULL_UUID_RE.match(target_id)):
        return {"error": f"missing node(s): {missing} — retracting an orphaned edge needs "
                         f"the FULL id triple (a prefix cannot derive the edge id)",
                "source_id": source_id, "target_id": target_id, "relation": relation,
                "written": False}
    source_id = F.nid(src) if src is not None else source_id
    target_id = F.nid(tgt) if tgt is not None else target_id
    edge_id = make_edge(source_id, target_id, relation)["id"]
    deleted = await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=[edge_id])
    return {"source_id": source_id, "target_id": target_id, "relation": relation,
            "actor": actor, "edge_id": edge_id, "deleted": int(deleted or 0),
            "written": True}
