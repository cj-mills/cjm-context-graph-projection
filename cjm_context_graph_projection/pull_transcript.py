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

from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_dev_graph_schema.nodes import MessageNode, SessionNode

from .runtime import GraphHandle
from .write import assert_value

# The capture-source facet stamped on pulled messages (one label, many sources —
# editor-born parts will stamp their own).
MESSAGE_SOURCE_CC = "cc-transcript"


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
                        "timestamp": em.timestamp})
        prev_uuid = em.uuid
    return payload


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
    return {**res, "transcript_path": str(best.path),
            "messages_total": len(payload), "messages_new": len(new_payload),
            "new_messages": new_payload,
            "other_candidates": [m.cc_session_uuid for m in matches[1:]]}
