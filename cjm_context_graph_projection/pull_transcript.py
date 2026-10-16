"""The transcript pull verb: harness-transcript messages onto the session spine.

The minting half of the scratchpad-v2 pull path (DEC fc6a0cdc; rung plan
e8b2f397 increment ii): extraction truth lives in `cjm-harness-transcripts`
(pure-parse, imported lazily so the CLI never hard-depends on it), and this
module lands the results on-graph — Message nodes PART_OF the Session spine,
NEXT succession along the extraction chain, STARTS_WITH at a chain head, and
the derived CC-session-UUID pairing asserted as a fact on the Session node.

Journal shape (self-contained replay): the live verb journals one
`pull-transcript` op carrying the NEW messages' full payload — never a bare
invocation, because transcripts are prunable external files and the journal
must reconstruct the graph alone (the op carries every message it lands: the
new ones, and any already on the graph missing its chain edge). Each payload message carries its chain
predecessor (`prev_uuid`), so an incremental op replays its NEXT edges without
needing earlier messages in the same op. A pull that finds nothing new journals
NOTHING (the watcher-cadence guarantee: quiet polls leave no trace)."""

from typing import Any, Dict, List, Optional, Tuple

from cjm_context_graph_layer.grammar import make_edge, SpineRelations
from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.query import EdgeQuery, NodeQuery
from cjm_dev_graph_schema.nodes import MessageNode, SessionNode
from cjm_dev_graph_schema.vocab import DevRelations

from .runtime import GraphHandle
from .write import assert_value

# The capture-source facet stamped on pulled messages (one label, many sources —
# editor-born parts stamp MESSAGE_SOURCE_COMPOSER).
MESSAGE_SOURCE_CC = "cc-transcript"

# The capture-source facet on editor-born composition parts (DEC fc6a0cdc pt 5:
# composition chain, unit = PART; same mint machinery, different provenance).
MESSAGE_SOURCE_COMPOSER = "composer"

# The facet the extractor stamps on tool-parameter prose (finding 60d719fe);
# the literal is born extractor-side (TOOL_PARAM_SOURCE, cjm-harness-transcripts)
# and mirrored here as graph-side vocabulary — the timeline COMPOSER_SOURCE pattern.
MESSAGE_SOURCE_TOOL_PARAM = "cc-tool-param"

# The facet the extractor stamps on persisted thinking summaries (item 6c3a0118:
# Claude Code 2.1.246+ stores the model's summary of a reasoning run as the
# thinking block's text) — role stays "assistant" (agent-origin); the literal is
# born extractor-side (THINKING_SUMMARY_SOURCE) and mirrored here per the pattern.
MESSAGE_SOURCE_THINKING_SUMMARY = "cc-thinking-summary"

# The facet the extractor stamps on harness task-notification records (finding
# 47b83adb): role="harness", neither party's prose — the literal is born
# extractor-side (HARNESS_SOURCE) and mirrored here per the same pattern.
MESSAGE_SOURCE_HARNESS = "cc-harness"


def build_pull_payload(
    extracted: List[Any],  # ExtractedMessage sequence (uuid/parent_uuid/role/text/timestamp), chronological
) -> List[Dict[str, Any]]:  # Journal/mint payload dicts
    """The journalable payload for an extraction sequence.

    Each message carries its chain predecessor (`prev_uuid`) explicitly, so ANY
    subset op (an incremental pull's new-message suffix) replays its NEXT edges
    standalone — the predecessor's deterministic id is derivable from the uuid
    alone, whether or not that message rides the same op."""
    payload: List[Dict[str, Any]] = []
    prev_uuid: Optional[str] = None
    for em in extracted:
        payload.append({"uuid": em.uuid, "parent_uuid": em.parent_uuid,
                        "prev_uuid": prev_uuid, "role": em.role, "text": em.text,
                        "timestamp": em.timestamp,
                        "source": getattr(em, "source", None)})
        prev_uuid = em.uuid
    return payload


def stale_next_edges(
    payload: List[Dict[str, Any]],      # build_pull_payload output (uuid / prev_uuid), chronological
    inbound: Dict[str, List[str]],      # Message node id -> source node ids of its ON-GRAPH inbound NEXT edges
) -> List[Tuple[str, str]]:  # (source_id, target_id) NEXT pairs to retract, payload order
    """The chain re-link plan (finding e358fe97) — pure, so the live pull and
    tests share it.

    Every transcript message has at most ONE predecessor (forks are outbound —
    rewind points), and the payload's `prev_uuid` is authoritative for the
    active path. A wider extraction that inserts a message mid-chain (the
    2.1.246 thinking summaries between a prompt and its assistant prose) leaves
    the stale prev->prose NEXT edge beside the new prev->insert->prose pair,
    because pulls are additive; this names every inbound NEXT edge whose source
    is not the payload predecessor. Chain heads (no prev_uuid) are skipped —
    their entry is STARTS_WITH, and a demoted head is not a case the transcript
    DAG produces."""
    plan: List[Tuple[str, str]] = []
    for m in payload:
        prev = m.get("prev_uuid")
        if not prev:
            continue
        target = MessageNode(source_uuid=m["uuid"], role="", text="").id
        expected = MessageNode(source_uuid=prev, role="", text="").id
        for src in inbound.get(target, []):
            if src != expected:
                plan.append((src, target))
    return plan


def build_mint_batch(
    session_key: str,                # The session spine key the messages belong to
    messages: List[Dict[str, Any]],  # Payload dicts (see build_pull_payload)
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:  # (nodes, edges) wire dicts
    """The pure node/edge assembly the live mint AND replay share.

    Per message: the Message node, its PART_OF containment on the Session, and
    either a NEXT edge from its chain predecessor or (chain head) the Session's
    STARTS_WITH. All ids deterministic — re-minting converges to no-ops."""
    sess = SessionNode(key=session_key)
    nodes: List[Dict[str, Any]] = [sess.to_graph_node()]
    edges: List[Dict[str, Any]] = []
    for m in messages:
        node = MessageNode(
            source_uuid=m["uuid"], role=m["role"], text=m["text"],
            timestamp=m.get("timestamp") or "", source=m.get("source") or MESSAGE_SOURCE_CC,
            parent_source_uuid=m.get("parent_uuid") or "", session_key=session_key,
        )
        nodes.append(node.to_graph_node())
        edges.append(node.part_of_edge(sess.id))
        prev = m.get("prev_uuid")
        if prev:
            edges.append(node.next_edge_from(MessageNode(
                source_uuid=prev, role="", text="").id))
        else:
            edges.append(node.starts_with_edge(sess.id))
    return nodes, edges


async def mint_pulled_messages(
    gx: GraphHandle,
    session_key: str,          # The session spine key the messages belong to
    cc_session_uuid: str,      # The paired CC transcript uuid ("" = skip the fact assert)
    messages: List[Dict[str, Any]],  # Payload: {uuid, parent_uuid, prev_uuid, role, text, timestamp}
    *,
    actor: str = "agent:session",
    write: bool = True,        # False = PLAN only (ruling d2d05e78 (3)): the op under "op" (None = changes nothing), nothing written
) -> Dict[str, Any]:  # The write result
    """Land pulled messages on the spine — the code path live pull AND replay share.

    Idempotent by construction: Message ids derive from capture-record uuids and
    edge ids from their endpoints, so re-minting converges to verified no-ops.

    THE PLAN (write=False) is `mint-messages`' (a composer's parts; the pull plans its own op and
    pairing fact in `plan_pull`, so a pairing uuid is refused here): the op carries the messages
    `plan_messages` finds new or missing their chain edge -- None when every one stands."""
    sess = SessionNode(key=session_key)
    if not write:
        if cc_session_uuid:
            return {"error": "a pairing fact is the pull's to plan (plan_pull), never mint-messages'",
                    "written": False}
        new, riding = await plan_messages(gx, session_key, messages)
        return {"session_id": sess.id, "session_key": session_key, "new_messages": new,
                "messages": riding, "written": False, "noop": not riding,
                "op": ({"session_key": session_key, "messages": riding, "actor": actor}
                       if riding else None)}
    nodes, edges = build_mint_batch(session_key, messages)
    res = await extend_graph(gx.queue, gx.graph_id, nodes, edges)
    fact: Optional[Dict[str, Any]] = None
    if cc_session_uuid:
        # no evidence: the journaled pull-transcript op is the provenance -- a prose string
        # here landed one dropped edge per character on every tick (finding 0ccdca26)
        fact = await assert_value(gx, sess.id, "cc_session_uuid", cc_session_uuid, actor=actor)
    return {"session_id": sess.id, "session_key": session_key,
            "cc_session_uuid": cc_session_uuid, "minted": len(messages),
            "nodes_added": res.nodes_added, "edges_added": res.edges_added,
            "fact_assertion": (fact or {}).get("assertion_id"), "written": True}


async def edit_message(
    gx: GraphHandle,
    source_uuid: str,   # The message's capture-source uuid (identity input)
    text: str,          # The replacement body
    *,
    properties: Optional[Dict[str, Any]] = None,  # Extra property updates (e.g. role/source — the 47b83adb retro-sweep)
    actor: str = "user:scratchpad",
    write: bool = True,  # False = PLAN only (design detail 65f4a6c9 (1)): the refusal, then the op under "op" (None = changes nothing), nothing written
) -> Dict[str, Any]:  # The write result
    """In-place body edit of a Message — the journaled edit-op half of the
    correction flow (DEC 91c47b4a pts 3-4: no SUPERSEDES ceremony for drafts;
    the journal op is the durable record, the node converges by last-op-wins).
    Replay tolerates a missing node (the mint op precedes it in append order).
    `properties` widens the same op to facet corrections (finding 47b83adb):
    extra keys ride the update alongside the body, last-op-wins identically.

    THE PLAN (write=False): the op -- or None when the Message already carries this body and
    every property (ruling 17daa86b: update_node restamps, so an unchanged edit is a write)."""
    node_id = MessageNode(source_uuid=source_uuid, role="", text="").id
    existing = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=node_id)
    if existing is None:
        return {"error": f"no Message node for source uuid {source_uuid}",
                "written": False}
    if not write:
        from .factlayer import holds
        noop = holds(existing, {"text": text, **(properties or {})})
        op = {"source_uuid": source_uuid, "text": text, "actor": actor,
              **({"properties": dict(properties)} if properties else {})}
        return {"message_id": node_id, "source_uuid": source_uuid, "written": False, "noop": noop,
                "op": None if noop else op}
    await graph_task(gx.queue, gx.graph_id, "update_node", node_id=node_id,
                     properties={"text": text, **(properties or {})})
    return {"message_id": node_id, "source_uuid": source_uuid, "written": True}


def build_derived_edges(
    sent_uuid: str,           # The sent transcript message's capture uuid
    part_uuids: List[str],    # Composition-part uuids, send order
) -> List[Dict[str, Any]]:  # DERIVED_FROM edge wire dicts
    """The pure aggregation-seam assembly: sent Message DERIVED_FROM each part,
    send order riding the `order` edge property."""
    sent_id = MessageNode(source_uuid=sent_uuid, role="", text="").id
    return [make_edge(sent_id, MessageNode(source_uuid=pu, role="", text="").id,
                      DevRelations.DERIVED_FROM, properties={"order": i})
            for i, pu in enumerate(part_uuids)]


async def derive_message(
    gx: GraphHandle,
    sent_uuid: str,           # The sent transcript message's capture uuid
    part_uuids: List[str],    # Composition-part uuids, send order
    *,
    actor: str = "user:scratchpad",
    write: bool = True,       # False = PLAN only (ruling d2d05e78 (3)): the refusal, then the op under "op" (None = changes nothing), nothing written
) -> Dict[str, Any]:  # The write result
    """The compose-send aggregation seam (DEC fc6a0cdc pt 5): the sent message
    DERIVED_FROM each composition part it was assembled from, order riding
    edge properties (the IN_SERIES pattern — no new relation). Deterministic
    edge ids make re-derivation converge to no-ops.

    THE PLAN (write=False): refuses a sent or part Message not on the graph (an edge to nothing --
    the scratchpad derives only once the pull has landed the sent message), and is no change when
    every DERIVED_FROM edge stands (extend_graph never updates a standing edge, so nothing else
    could change)."""
    sent_id = MessageNode(source_uuid=sent_uuid, role="", text="").id
    edges = build_derived_edges(sent_uuid, part_uuids)
    if not write:
        ids = [sent_id] + [MessageNode(source_uuid=pu, role="", text="").id for pu in part_uuids]
        res = await graph_task(gx.queue, gx.graph_id, "query_nodes", query=NodeQuery(ids=ids).to_dict())
        present = {str(n.id) for n in (res.nodes or [])}
        missing = [u for u, i in zip([sent_uuid] + list(part_uuids), ids) if i not in present]
        if missing:
            return {"error": f"no Message node for source uuid(s) {', '.join(missing)}", "written": False}
        noop = all([await graph_task(gx.queue, gx.graph_id, "get_edge", edge_id=e["id"]) is not None
                    for e in edges])
        return {"message_id": sent_id, "sent_uuid": sent_uuid, "parts": len(part_uuids),
                "written": False, "noop": noop,
                "op": None if noop else {"sent_uuid": sent_uuid, "part_uuids": list(part_uuids),
                                         "actor": actor}}
    res = await extend_graph(gx.queue, gx.graph_id, [], edges)
    return {"message_id": sent_id, "sent_uuid": sent_uuid,
            "parts": len(part_uuids), "edges_added": res.edges_added,
            "written": True}


async def _inbound_next(
    gx: GraphHandle,
    ids: List[str],   # Message node ids
) -> Dict[str, List[str]]:  # Message node id -> the source ids of its on-graph inbound NEXT edges
    inbound: Dict[str, List[str]] = {}
    for i in range(0, len(ids), 500):
        eres = await graph_task(gx.queue, gx.graph_id, "query_edges",
                                query=EdgeQuery(relation_type=SpineRelations.NEXT,
                                                target_ids=ids[i:i + 500],
                                                project=["id"]).to_dict())
        for row in (eres.rows or []):
            inbound.setdefault(row["target_id"], []).append(row["source_id"])
    return inbound


async def plan_messages(
    gx: GraphHandle,
    session_key: str,                # The session spine key
    payload: List[Dict[str, Any]],   # Mint payload dicts, chronological
    inbound: Optional[Dict[str, List[str]]] = None,  # The payload's inbound NEXT sources, when the caller read them (None = read here)
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:  # (the new messages, the messages an op must carry)
    """THE MESSAGES' PLAN (rulings 24fa72c2 (5), d2d05e78 (3)), shared by the pull and
    mint-messages: an op carries every NEW message plus every one already on the graph whose
    expected inbound edge is absent (a NEXT from its payload predecessor, or the Session's
    STARTS_WITH at a chain head) -- a mid-chain insert's successor re-links on replay too, the
    applier unchanged."""
    sess = SessionNode(key=session_key)
    ids = [MessageNode(source_uuid=m["uuid"], role="", text="").id for m in payload]
    present = set()
    for i in range(0, len(ids), 500):
        res = await graph_task(gx.queue, gx.graph_id, "query_nodes",
                               query=NodeQuery(ids=ids[i:i + 500]).to_dict())
        present |= {str(n.id) for n in (res.nodes or [])}
    if inbound is None:
        inbound = await _inbound_next(gx, ids)
    eres = await graph_task(gx.queue, gx.graph_id, "query_edges",
                            query=EdgeQuery(relation_type=SpineRelations.STARTS_WITH,
                                            source_ids=[sess.id], project=["id"]).to_dict())
    heads = {row["target_id"] for row in (eres.rows or [])}
    new, riding = [], []
    for m, mid in zip(payload, ids):
        if mid not in present:
            new.append(m)
            riding.append(m)
        elif m.get("prev_uuid"):
            if MessageNode(source_uuid=m["prev_uuid"], role="", text="").id not in inbound.get(mid, []):
                riding.append(m)
        elif mid not in heads:
            riding.append(m)
    return new, riding


async def plan_pull(
    gx: GraphHandle,
    session_key: str,                # The session spine key to pull for
    cc_session_uuid: str,            # The best-matching transcript's uuid
    payload: List[Dict[str, Any]],   # build_pull_payload over the whole active path
    *,
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {new_messages, messages, cc_session_uuid_changes, op, unlinks, noop}
    """THE PULL'S PLAN (ruling 24fa72c2 (5)), read before anything lands: the op's messages are
    `plan_messages`'; a cc_session_uuid fact that would change is itself a write (an op with no
    message journals it); the op always names the matched transcript -- provenance its readers
    take (the bench's ingest workload), and a standing value re-asserts to the same Assertion, a
    verified no-op; the stale NEXT pairs (the same before the mint as after it) are planned
    `unlink` ops after it. Nothing to land = no op."""
    sess = SessionNode(key=session_key)
    inbound = await _inbound_next(gx, [MessageNode(source_uuid=m["uuid"], role="", text="").id
                                       for m in payload])
    new, riding = await plan_messages(gx, session_key, payload, inbound)
    fact_changes = bool(cc_session_uuid)
    if fact_changes and await graph_task(gx.queue, gx.graph_id, "get_node", node_id=sess.id) is not None:
        fact = await assert_value(gx, sess.id, "cc_session_uuid", cc_session_uuid, actor=actor, write=False)
        fact_changes = bool(fact.get("op")) and not fact.get("error")
    unlinks = [{"source_id": src, "target_id": dst, "relation": SpineRelations.NEXT, "actor": actor}
               for src, dst in stale_next_edges(payload, inbound)]
    op = ({"session_key": session_key, "cc_session_uuid": cc_session_uuid, "messages": riding, "actor": actor}
          if riding or fact_changes else None)
    return {"new_messages": new, "messages": riding, "cc_session_uuid_changes": fact_changes,
            "op": op, "unlinks": unlinks, "noop": op is None and not unlinks}


def read_transcript(
    session_key: str,          # The session spine key to pull for
    transcript_dir: str,       # The harness project transcript dir (*.jsonl)
    *,
    require_signal: bool = True,   # Only match boot prompts carrying the mint signal
) -> Dict[str, Any]:  # {session_key, cc_session_uuid, transcript_path, payload, messages_total, other_candidates} | {error}
    """The pull's READ: derive the mapping, extract the active path, build its payload -- file
    work only, no graph read, so a writer runs it OUTSIDE the family's lock (ruling d2d05e78) and
    takes the lock for the plan and its writes alone. Extraction is always FULL (idempotency is
    free -- DEC fc6a0cdc point 3)."""
    try:
        from cjm_harness_transcripts.extract import extract_messages
        from cjm_harness_transcripts.mapping import find_transcripts_for_key
        from cjm_harness_transcripts.records import TranscriptDag
    except ModuleNotFoundError:
        return {"error": "cjm-harness-transcripts is not installed in this env — "
                         "pip install -e it (the pull verb's extraction truth)"}
    matches = find_transcripts_for_key(transcript_dir, session_key,
                                       require_signal=require_signal)
    if not matches:
        return {"error": f"no transcript in {transcript_dir} matches session "
                         f"{session_key} (require_signal={require_signal})"}
    best = matches[0]
    payload = build_pull_payload(extract_messages(TranscriptDag.load(best.path)))
    return {"session_key": session_key, "cc_session_uuid": best.cc_session_uuid,
            "transcript_path": str(best.path), "payload": payload, "messages_total": len(payload),
            "other_candidates": [m.cc_session_uuid for m in matches[1:]]}


async def land_pull(
    gx: GraphHandle,
    writes: Any,              # The unit of work (writes.InvocationWrites)
    found: Dict[str, Any],    # read_transcript's result
    *,
    actor: str = "agent:session",
) -> Dict[str, Any]:  # The plan, plus `applied` (the pull op's apply result) and `unlinked` (the unlinks written)
    """THE PULL (ruling 24fa72c2 (5)): planned against the healed db, the op appended and applied
    through the replay's applier, then each planned `unlink` the same way (the chain re-link,
    finding e358fe97). Nothing to land = nothing journaled, the watcher-cadence guarantee."""
    if found.get("error"):
        return found
    await writes.begin()
    plan = await plan_pull(gx, found["session_key"], found["cc_session_uuid"], found["payload"],
                           actor=actor)
    out = {**{k: v for k, v in found.items() if k != "payload"}, **plan,
           "messages_new": len(plan["new_messages"]), "applied": {}, "unlinked": []}
    if plan["op"]:
        out["applied"] = (await writes.write("pull-transcript", plan["op"])).result or {}
    for u in plan["unlinks"]:
        await writes.write("unlink", u)
        out["unlinked"].append(u)
    return out


async def land_mint_messages(
    gx: GraphHandle,
    writes: Any,                     # The unit of work (writes.InvocationWrites)
    session_key: str,                # The session spine key
    messages: List[Dict[str, Any]],  # Mint payload dicts (a composer's parts)
    *,
    actor: str = "user:scratchpad",
) -> Dict[str, Any]:  # The mint result (the plan's, when it changes nothing)
    """Mint messages onto the spine (a composer's committed parts, DEC 93e3e881 pt 5): planned,
    appended, applied; every message already standing with its chain edge is no change."""
    await writes.begin()
    res = await mint_pulled_messages(gx, session_key, "", messages, actor=actor, write=False)
    if res.get("op"):
        res = (await writes.write("mint-messages", res["op"])).result
    return res


async def land_edit_message(
    gx: GraphHandle,
    writes: Any,         # The unit of work (writes.InvocationWrites)
    source_uuid: str,    # The message's capture-source uuid
    text: str,           # The replacement body
    *,
    properties: Optional[Dict[str, Any]] = None,  # Extra property updates
    actor: str = "user:scratchpad",
) -> Dict[str, Any]:  # The edit result (the plan's, when it changes nothing)
    """Edit a Message's body in place: planned, appended, applied; a body and properties already
    standing are no change (ruling 17daa86b)."""
    await writes.begin()
    res = await edit_message(gx, source_uuid, text, properties=properties, actor=actor, write=False)
    if res.get("op"):
        res = (await writes.write("edit-message", res["op"])).result
    return res


async def land_derive_message(
    gx: GraphHandle,
    writes: Any,              # The unit of work (writes.InvocationWrites)
    sent_uuid: str,           # The sent transcript message's capture uuid
    part_uuids: List[str],    # Composition-part uuids, send order
    *,
    actor: str = "user:scratchpad",
) -> Dict[str, Any]:  # The derive result (the plan's, when it changes nothing or refuses)
    """Land a reconciled compose-send's DERIVED_FROM edges: planned (a missing Message refused),
    appended, applied; every edge already standing is no change."""
    await writes.begin()
    res = await derive_message(gx, sent_uuid, part_uuids, actor=actor, write=False)
    if res.get("op"):
        res = (await writes.write("derive-message", res["op"])).result
    return res
