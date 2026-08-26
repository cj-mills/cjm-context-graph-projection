"""The transcript pull verb's pure halves: payload chaining + mint-batch assembly."""

from types import SimpleNamespace

from cjm_context_graph_projection.pull_transcript import (
    MESSAGE_SOURCE_CC, MESSAGE_SOURCE_HARNESS, MESSAGE_SOURCE_TOOL_PARAM,
    build_mint_batch, build_pull_payload)
from cjm_dev_graph_schema.identity import message_node_id, session_node_id
from cjm_dev_graph_schema.vocab import DevNodeKinds


def em(uuid, role="user", text="hi", parent=None, ts="2026-08-20T22:00:00.000Z"):
    return SimpleNamespace(uuid=uuid, parent_uuid=parent, role=role, text=text, timestamp=ts)


def test_payload_threads_prev_uuid():
    payload = build_pull_payload([em("u1"), em("a1", role="assistant", parent="u1"),
                                  em("u2", parent="a1")])
    assert [m["prev_uuid"] for m in payload] == [None, "u1", "a1"]
    assert payload[1]["parent_uuid"] == "u1"  # DAG ancestry rides beside the chain


def test_mint_batch_shapes_the_spine():
    key = "2026-08-20_17-05-20"
    payload = build_pull_payload([em("u1"), em("a1", role="assistant", parent="u1")])
    nodes, edges = build_mint_batch(key, payload)

    assert nodes[0]["id"] == session_node_id(key)          # the spine anchor rides along
    assert [n["label"] for n in nodes[1:]] == [DevNodeKinds.MESSAGE] * 2
    assert nodes[1]["id"] == message_node_id("u1")          # deterministic = idempotent
    assert nodes[1]["properties"]["source"] == MESSAGE_SOURCE_CC

    rels = [(e["source_id"], e["relation_type"], e["target_id"]) for e in edges]
    assert (message_node_id("u1"), "PART_OF", session_node_id(key)) in rels
    assert (session_node_id(key), "STARTS_WITH", message_node_id("u1")) in rels  # chain head
    assert (message_node_id("u1"), "NEXT", message_node_id("a1")) in rels        # succession
    assert len(edges) == 4  # 2 PART_OF + 1 STARTS_WITH + 1 NEXT


def test_incremental_suffix_chains_to_prior_op():
    # An incremental pull's payload starts mid-chain: prev_uuid points at a message
    # minted by an EARLIER op — the NEXT edge must land without it in this batch.
    payload = [{"uuid": "u9", "parent_uuid": "a8", "prev_uuid": "a8",
                "role": "user", "text": "later", "timestamp": ""}]
    _, edges = build_mint_batch("2026-08-20_17-05-20", payload)
    rels = [(e["source_id"], e["relation_type"], e["target_id"]) for e in edges]
    assert (message_node_id("a8"), "NEXT", message_node_id("u9")) in rels
    assert not any(r[1] == "STARTS_WITH" for r in rels)  # not a chain head


def test_pull_transcript_is_a_journal_verb():
    from cjm_context_graph_projection.journal import JOURNAL_VERBS
    assert "pull-transcript" in JOURNAL_VERBS  # replay counts + durability registry


def test_message_write_verbs_are_journal_verbs():
    # The scratchpad-v2 composition seam (DEC 93e3e881 pt 5): composer mints,
    # in-place edits, and compose-send derivations all replay from the journal.
    from cjm_context_graph_projection.journal import JOURNAL_VERBS
    assert {"mint-messages", "edit-message", "derive-message"} <= set(JOURNAL_VERBS)


def test_derived_edges_carry_send_order():
    from cjm_context_graph_projection.pull_transcript import build_derived_edges
    edges = build_derived_edges("sent1", ["p1", "p2", "p3"])
    assert [e["relation_type"] for e in edges] == ["DERIVED_FROM"] * 3
    assert all(e["source_id"] == message_node_id("sent1") for e in edges)
    assert [e["target_id"] for e in edges] == [message_node_id(p) for p in ("p1", "p2", "p3")]
    assert [e["properties"]["order"] for e in edges] == [0, 1, 2]  # send order on the edge


def test_composer_source_rides_the_same_mint_batch():
    # One label, many sources (DEC 91c47b4a pt 1): a composer part is the same
    # payload shape with its own provenance facet, never a new label.
    from cjm_context_graph_projection.pull_transcript import MESSAGE_SOURCE_COMPOSER
    payload = [{"uuid": "part1", "parent_uuid": None, "prev_uuid": None, "role": "user",
                "text": "draft", "timestamp": "2026-08-21T16:00:00.000Z",
                "source": MESSAGE_SOURCE_COMPOSER}]
    nodes, _ = build_mint_batch("2026-08-21_11-26-36", payload)
    assert nodes[1]["properties"]["source"] == MESSAGE_SOURCE_COMPOSER
    assert nodes[1]["label"] == DevNodeKinds.MESSAGE


def test_tool_param_source_rides_payload_to_mint():
    # Extractor-faceted entries (finding 60d719fe) keep their birth class
    # through payload -> mint; unfaceted entries default to the transcript
    # facet — and older journal ops without the key replay the same way.
    faceted = SimpleNamespace(uuid="t1", parent_uuid="a1", role="assistant",
                              text="caption", timestamp="2026-08-21T21:00:05.000Z",
                              source=MESSAGE_SOURCE_TOOL_PARAM)
    payload = build_pull_payload([em("u1"), faceted])
    assert payload[0]["source"] is None                        # em() has no source attr
    assert payload[1]["source"] == MESSAGE_SOURCE_TOOL_PARAM
    nodes, _ = build_mint_batch("2026-08-21_20-46-05", payload)
    assert nodes[1]["properties"]["source"] == MESSAGE_SOURCE_CC
    assert nodes[2]["properties"]["source"] == MESSAGE_SOURCE_TOOL_PARAM


def test_harness_role_and_source_ride_payload_to_mint():
    # The third-author facet (finding 47b83adb): a distilled task-notification
    # extracts as role="harness" + MESSAGE_SOURCE_HARNESS and keeps both
    # through payload -> mint untouched — no user/assistant assumption anywhere.
    notice = SimpleNamespace(uuid="n1", parent_uuid="a1", role="harness",
                             text='Background command "Run sweep" completed (exit code 0)',
                             timestamp="2026-08-22T20:05:00.000Z",
                             source=MESSAGE_SOURCE_HARNESS)
    payload = build_pull_payload([em("u1"), notice])
    assert payload[1]["role"] == "harness"
    assert payload[1]["source"] == MESSAGE_SOURCE_HARNESS
    nodes, _ = build_mint_batch("2026-08-22_20-29-06", payload)
    assert nodes[2]["properties"]["role"] == "harness"
    assert nodes[2]["properties"]["source"] == MESSAGE_SOURCE_HARNESS
