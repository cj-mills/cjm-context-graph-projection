"""The content-access reads ledger: id extraction, arm/disarm, stamping, fail-open (no graph)."""

import json
import pytest

from cjm_context_graph_projection.reads import (_IDS_CAP, configure_reads, delivered_ids,
                                                record_read)


@pytest.fixture(autouse=True)
def _disarm():
    """Module-level arm state must never leak between tests."""
    yield
    configure_reads(None)


def test_delivered_ids_first_seen_order_dedup():
    a = "11111111-2222-3333-4444-555555555555"
    b = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    data = {"node": {"id": a}, "rows": [{"id": b}, {"id": a}], "text": f"see {b} again"}
    assert delivered_ids(data) == [a, b]


def test_record_read_noop_unarmed(tmp_path):
    configure_reads(None)
    record_read("show", {"id": "11111111-2222-3333-4444-555555555555"})
    assert list(tmp_path.iterdir()) == []


def test_record_read_appends_stamped_events_dedup_off(tmp_path, monkeypatch):
    monkeypatch.setenv("CJM_SESSION", "s1")
    monkeypatch.setenv("CJM_ACTOR", "agent:test")
    p = str(tmp_path / "g.reads.jsonl")
    a = "11111111-2222-3333-4444-555555555555"
    configure_reads(p, request={"node_id": a[:8]})
    record_read("show", {"id": a})
    record_read("show", {"id": a})  # a repeat read is one MORE event, never deduped
    rows = [json.loads(line) for line in open(p)]
    assert [r["verb"] for r in rows] == ["show", "show"]
    assert rows[0]["ids"] == [a] and rows[0]["n"] == 1
    assert rows[0]["session"] == "s1" and rows[0]["actor"] == "agent:test"
    assert rows[0]["request"] == {"node_id": a[:8]}
    assert rows[0]["ts"] > 0


def test_record_read_caps_ids_keeps_true_count(tmp_path):
    p = str(tmp_path / "g.reads.jsonl")
    configure_reads(p)
    many = [f"{i:08x}-1111-2222-3333-444444444444" for i in range(_IDS_CAP + 2)]
    record_read("grep", {"rows": [{"id": i} for i in many]})
    row = json.loads(open(p).readline())
    assert row["n"] == _IDS_CAP + 2 and len(row["ids"]) == _IDS_CAP


def test_record_read_fail_open(tmp_path, capsys):
    configure_reads(str(tmp_path))  # a DIRECTORY: the append fails, the read must not
    record_read("show", {"id": "11111111-2222-3333-4444-555555555555"})
    assert "reads-ledger append failed" in capsys.readouterr().err
