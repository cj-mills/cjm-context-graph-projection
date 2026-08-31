"""Leg-1 regression (plan f22b3f34): `--supersede` resolution + supersession gaps.

Locks the fixes for 41449193 (a prefix supersede was a silent no-op) and 6a27c56f
(partial auto-supersede + dedup dropping --supersede): a unique id PREFIX lands the
SUPERSEDES edge, a missed or ambiguous token REFUSES the whole write (nothing minted,
so the CLI journals nothing), a deduped re-assert still processes --supersede, the
`in_progress` lifecycle step auto-supersedes forward, and a slot left two-active
warns `multi_active` at write time."""

import asyncio
from pathlib import Path

import pytest

from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.write import _match_supersede_targets, assert_value, decide

# These drive the real graph-storage worker capability via open_graph().
# Skip wherever its manifest isn't discoverable (e.g. CI).
pytestmark = pytest.mark.skipif(
    not (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists(),
    reason=f"graph capability {DEFAULT_GRAPH_ID!r} not installed at {DEFAULT_MANIFESTS}",
)


def _fake(aid, value):
    """An assertion-shaped node dict for the pure matcher tests."""
    return {"id": aid, "properties": {"value": value}}


def test_matcher_resolves_full_id_value_and_unique_prefix():
    a1 = "abcdef01-0000-5000-8000-000000000001"
    a2 = "12345678-0000-5000-8000-000000000002"
    slot = [_fake(a1, "open"), _fake(a2, "done")]
    m = _match_supersede_targets([a1, "DONE", "123456"], slot, "task_state")
    assert m["ids"] == [a1, a2, a2]
    assert m["unmatched"] == [] and m["ambiguous"] == []


def test_matcher_reports_ambiguous_prefix_and_unmatched_token():
    a1 = "abcdef01-0000-5000-8000-000000000001"
    a2 = "abcdef02-0000-5000-8000-000000000002"
    slot = [_fake(a1, "open"), _fake(a2, "done")]
    m = _match_supersede_targets(["abcdef0", "deadbe", "nothere"], slot, "task_state")
    assert m["ids"] == []
    assert m["ambiguous"] == [{"token": "abcdef0", "candidates": [a1, a2]}]
    # An id-shaped miss and a value-shaped miss both land in unmatched.
    assert m["unmatched"] == ["deadbe", "nothere"]


async def _mint(gx, statement):
    res = await decide(gx, statement)
    assert not res.get("error")
    return res["decision_id"]


def test_prefix_supersede_lands_edge_and_miss_refuses_write(tmp_path):
    db = str(tmp_path / "g.db")

    async def go():
        async with open_graph(db) as gx:
            item = await _mint(gx, "WORK ITEM: supersede fixture A")
            first = await assert_value(gx, item, "priority", "next")
            assert not first.get("error")
            # A unique 8-char PREFIX resolves and supersedes (41449193).
            second = await assert_value(gx, item, "priority", "later",
                                        supersede=[first["assertion_id"][:8]])
            assert second["superseded"] == [first["assertion_id"]]
            assert not second.get("multi_active")
            # A miss REFUSES the whole write — nothing superseded, nothing minted.
            third = await assert_value(gx, item, "priority", "someday",
                                       supersede=["deadbeef"])
            assert third.get("written") is False
            assert "refused" in third["error"] and "deadbeef" in third["error"]
            # The refused value never landed: re-asserting it fresh is a NEW write.
            fourth = await assert_value(gx, item, "priority", "someday",
                                        supersede=[second["assertion_id"]])
            assert fourth["nodes_added"] >= 1
            assert fourth["superseded"] == [second["assertion_id"]]

    asyncio.run(go())


def test_cross_slot_full_id_refuses_with_diagnosis(tmp_path):
    db = str(tmp_path / "g.db")

    async def go():
        async with open_graph(db) as gx:
            a = await _mint(gx, "WORK ITEM: supersede fixture B1")
            b = await _mint(gx, "WORK ITEM: supersede fixture B2")
            on_a = await assert_value(gx, a, "priority", "next")
            # The 2026-08-27 pin.ritual class: a FULL id living on another slot
            # used to drop silently; now the refusal names the other slot.
            res = await assert_value(gx, b, "priority", "later",
                                     supersede=[on_a["assertion_id"]])
            assert res.get("written") is False
            assert "DIFFERENT slot" in res["error"]

    asyncio.run(go())


def test_dedup_reassert_still_processes_supersede(tmp_path):
    # 6a27c56f gap 2: re-asserting the same value dedupes the Assertion node
    # (deterministic id) but the --supersede edge must STILL land.
    db = str(tmp_path / "g.db")

    async def go():
        async with open_graph(db) as gx:
            item = await _mint(gx, "WORK ITEM: supersede fixture C")
            first = await assert_value(gx, item, "priority", "next")
            bare = await assert_value(gx, item, "priority", "later")
            # Two active values on a non-multivalued slot -> LOUD multi_active warn
            # (6a27c56f gap 1's surface).
            assert [w["assertion_id"] for w in bare["multi_active"]] \
                == [first["assertion_id"]]
            again = await assert_value(gx, item, "priority", "later",
                                       supersede=[first["assertion_id"][:8]])
            assert again["nodes_added"] == 0  # deduped mint...
            assert again["superseded"] == [first["assertion_id"]]  # ...edge landed
            assert again["edges_added"] >= 1
            assert not again.get("multi_active")

    asyncio.run(go())


def test_task_state_in_progress_supersedes_forward(tmp_path):
    # 6a27c56f gap 1: in_progress used to land BESIDE open (off-sequence).
    db = str(tmp_path / "g.db")

    async def go():
        async with open_graph(db) as gx:
            item = await _mint(gx, "WORK ITEM: supersede fixture D")
            opened = await assert_value(gx, item, "task_state", "open")
            started = await assert_value(gx, item, "task_state", "in_progress")
            assert started["superseded"] == [opened["assertion_id"]]
            done = await assert_value(gx, item, "task_state", "done")
            assert started["assertion_id"] in done["superseded"]
            assert not done.get("multi_active")

    asyncio.run(go())
