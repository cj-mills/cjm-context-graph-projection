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
must reconstruct the graph alone. Each payload message carries its chain
predecessor (`prev_uuid`), so an incremental op replays its NEXT edges without
needing earlier messages in the same op. A pull that finds nothing new journals
NOTHING (the watcher-cadence guarantee: quiet polls leave no trace)."""

from typing import Any, Dict, List, Optional, Tuple

from cjm_context_graph_layer.grammar import make_edge, SpineRelations
from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.query import EdgeQuery
from cjm_dev_graph_schema.nodes import MessageNode, SessionNode
from cjm_dev_graph_schema.vocab import DevRelations

from .runtime import GraphHandle
from .write import assert_value, unlink

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
) -> Dict[str, Any]:  # The write result
    """Land pulled messages on the spine — the code path live pull AND replay share.

    Idempotent by construction: Message ids derive from capture-record uuids and
    edge ids from their endpoints, so re-minting converges to verified no-ops."""
    sess = SessionNode(key=session_key)
    nodes, edges = build_mint_batch(session_key, messages)
    res = await extend_graph(gx.queue, gx.graph_id, nodes, edges)
    fact: Optional[Dict[str, Any]] = None
    if cc_session_uuid:
        fact = await assert_value(
            gx, sess.id, "cc_session_uuid", cc_session_uuid, actor=actor,
            evidence=f"derived by pull-transcript for session {session_key}")
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
) -> Dict[str, Any]:  # The write result
    """In-place body edit of a Message — the journaled edit-op half of the
    correction flow (DEC 91c47b4a pts 3-4: no SUPERSEDES ceremony for drafts;
    the journal op is the durable record, the node converges by last-op-wins).
    Replay tolerates a missing node (the mint op precedes it in append order).
    `properties` widens the same op to facet corrections (finding 47b83adb):
    extra keys ride the update alongside the body, last-op-wins identically."""
    node_id = MessageNode(source_uuid=source_uuid, role="", text="").id
    existing = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=node_id)
    if existing is None:
        return {"error": f"no Message node for source uuid {source_uuid}",
                "written": False}
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
) -> Dict[str, Any]:  # The write result
    """The compose-send aggregation seam (DEC fc6a0cdc pt 5): the sent message
    DERIVED_FROM each composition part it was assembled from, order riding
    edge properties (the IN_SERIES pattern — no new relation). Deterministic
    edge ids make re-derivation converge to no-ops."""
    sent_id = MessageNode(source_uuid=sent_uuid, role="", text="").id
    res = await extend_graph(gx.queue, gx.graph_id, [],
                             build_derived_edges(sent_uuid, part_uuids))
    return {"message_id": sent_id, "sent_uuid": sent_uuid,
            "parts": len(part_uuids), "edges_added": res.edges_added,
            "written": True}


async def pull_transcript(
    gx: GraphHandle,
    session_key: str,          # The session spine key to pull for
    transcript_dir: str,       # The harness project transcript dir (*.jsonl)
    *,
    require_signal: bool = True,   # Only match boot prompts carrying the mint signal
    actor: str = "agent:session",
) -> Dict[str, Any]:  # The pull result (incl. the NEW-message payload for journaling)
    """The live pull: derive the mapping, extract the active path, mint the delta.

    Extraction is always FULL (idempotency is free — DEC fc6a0cdc point 3); the
    watermark optimization can come later without touching correctness. The
    result's `new_messages` payload is exactly what the CLI journals — messages
    already on-graph re-mint as no-ops and stay OUT of the journal, so a
    watcher-cadence pull with nothing new journals nothing."""
    try:
        from cjm_harness_transcripts.extract import extract_messages
        from cjm_harness_transcripts.mapping import find_transcripts_for_key
        from cjm_harness_transcripts.records import TranscriptDag
    except ModuleNotFoundError:
        return {"error": "cjm-harness-transcripts is not installed in this env — "
                         "pip install -e it (the pull verb's extraction truth)",
                "written": False}
    matches = find_transcripts_for_key(transcript_dir, session_key,
                                       require_signal=require_signal)
    if not matches:
        return {"error": f"no transcript in {transcript_dir} matches session "
                         f"{session_key} (require_signal={require_signal})",
                "written": False}
    best = matches[0]
    payload = build_pull_payload(extract_messages(TranscriptDag.load(best.path)))
    new_payload: List[Dict[str, Any]] = []
    for m in payload:
        existing = await graph_task(gx.queue, gx.graph_id, "get_node",
                                    node_id=MessageNode(source_uuid=m["uuid"],
                                                        role="", text="").id)
        if existing is None:
            new_payload.append(m)
    res = await mint_pulled_messages(gx, session_key, best.cc_session_uuid,
                                     payload, actor=actor)
    # Chain re-link (finding e358fe97): pulls are ADDITIVE, so a message the
    # extractor now yields MID-chain (a 2.1.246 thinking summary on a session
    # pulled before the 6c3a0118 facet) leaves the stale prev->next NEXT edge
    # beside the new pair. Read every payload message's inbound NEXT edges,
    # retract the ones the payload's prev_uuid does not vouch for, and report
    # them — the caller journals the compensating unlinks after the pull op.
    # Runs even when nothing new minted: that is the repair pull.
    ids = [MessageNode(source_uuid=m["uuid"], role="", text="").id for m in payload]
    inbound: Dict[str, List[str]] = {}
    for i in range(0, len(ids), 500):
        eres = await graph_task(gx.queue, gx.graph_id, "query_edges",
                                query=EdgeQuery(relation_type=SpineRelations.NEXT,
                                                target_ids=ids[i:i + 500],
                                                project=["id"]).to_dict())
        for row in (eres.rows or []):
            inbound.setdefault(row["target_id"], []).append(row["source_id"])
    retracted: List[Dict[str, Any]] = []
    for src, dst in stale_next_edges(payload, inbound):
        ures = await unlink(gx, src, dst, SpineRelations.NEXT, actor=actor)
        if ures.get("written"):
            retracted.append({"source_id": src, "target_id": dst,
                              "relation": SpineRelations.NEXT})
    return {**res, "transcript_path": str(best.path),
            "messages_total": len(payload), "messages_new": len(new_payload),
            "new_messages": new_payload, "retracted_edges": retracted,
            "other_candidates": [m.cc_session_uuid for m in matches[1:]]}
