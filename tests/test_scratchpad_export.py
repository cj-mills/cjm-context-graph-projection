"""The exporter lens's pure halves: entry derivation + markdown rendering."""

from datetime import timedelta, timezone

from cjm_context_graph_projection.scratchpad_export import (
    _local_stamp, derive_entries, render_session_markdown)

KEY = "2026-08-21_11-26-36"


def msg(nid, ts, role="user", source="cc-transcript", text="hello"):
    return {"id": nid, "source_uuid": nid.replace("n", "u"), "role": role,
            "text": text, "timestamp": ts, "source": source}


def test_derive_marks_abandoned_branch_and_sorts():
    messages = [
        msg("n3", "2026-08-21T10:02:00.000Z"),
        msg("n1", "2026-08-21T10:00:00.000Z"),
        msg("n2", "2026-08-21T10:01:00.000Z", role="assistant"),
        msg("p1", "2026-08-21T10:00:30.000Z", source="composer"),
    ]
    entries = derive_entries(messages, [("n1", "n2"), ("n1", "n3")])
    assert [e["id"] for e in entries] == ["n1", "p1", "n2", "n3"]  # one clock
    flags = {e["id"]: e["on_active_path"] for e in entries}
    assert flags == {"n1": True, "p1": True, "n2": False, "n3": True}


def test_derive_recovers_severed_chain_and_keeps_superseded_branch():
    # 6cc6de01: a chain repair unlinked NEXT edges mid-session, so the tip walk
    # could not reach the pre-repair prefix — the old derivation marked it
    # superseded and `read --session` silently dropped it (2/17 user messages on
    # 2026-08-26_15-14-43). The severed prefix (t1->t2, NO edge into t3) must
    # recover as active + `recovered`; a genuine abandoned branch forking OFF the
    # active chain (t3->x vs t3->t4) stays superseded and unrecovered.
    messages = [
        msg("t1", "2026-08-26T01:00:00.000Z"),
        msg("t2", "2026-08-26T02:00:00.000Z"),
        msg("t3", "2026-08-26T03:00:00.000Z"),
        msg("x", "2026-08-26T03:30:00.000Z"),
        msg("t4", "2026-08-26T04:00:00.000Z"),
    ]
    entries = derive_entries(messages, [("t1", "t2"), ("t3", "x"), ("t3", "t4")])
    flags = {e["id"]: e["on_active_path"] for e in entries}
    assert flags == {"t1": True, "t2": True, "t3": True, "x": False, "t4": True}
    assert {e["id"] for e in entries if e.get("recovered")} == {"t1", "t2"}


def test_render_marks_recovered_messages():
    # The recovered seam must be VISIBLE in the export projection (6cc6de01 fix c):
    # a recovered message renders active with a "(recovered — severed chain)"
    # header marker, never as superseded.
    entries = derive_entries(
        [msg("t1", "2026-08-26T01:00:00.000Z", text="pre-repair"),
         msg("t2", "2026-08-26T02:00:00.000Z"),
         msg("t3", "2026-08-26T03:00:00.000Z", text="post-repair")],
        [("t1", "t2")])  # severed: no edge from t2 into t3 (the tip)
    text = render_session_markdown(KEY, entries, [])
    assert "pre-repair" in text and "post-repair" in text  # nothing dropped
    assert "_(recovered — severed chain)_" in text
    assert "_(superseded)_" not in text


def test_render_default_excludes_superseded_and_marks_sent():
    entries = derive_entries(
        [msg("n1", "2026-08-21T10:00:00.000Z", text="question"),
         msg("n2", "2026-08-21T10:01:00.000Z", role="assistant", text="dead end"),
         msg("n3", "2026-08-21T10:02:00.000Z", text="question, take 2"),
         msg("p1", "2026-08-21T10:00:20.000Z", source="composer", text="draft part")],
        [("n1", "n2"), ("n1", "n3")])
    md = render_session_markdown(KEY, entries, [("n3", "p1")],
                                 exported_at="2026-08-21T20:00:00+00:00")
    assert md.startswith(f"# Session scratchpad — {KEY}")
    assert "dead end" not in md                    # superseded excluded by default
    assert "**PART**" in md and "sent ✓" in md     # part present, send marked
    assert "question, take 2" in md
    assert "1 composition part(s)" in md and "2 transcript message(s)" in md


def test_render_superseded_included_when_configured():
    entries = derive_entries(
        [msg("n1", "2026-08-21T10:00:00.000Z"),
         msg("n2", "2026-08-21T10:01:00.000Z", role="assistant", text="dead end"),
         msg("n3", "2026-08-21T10:02:00.000Z")],
        [("n1", "n2"), ("n1", "n3")])
    md = render_session_markdown(KEY, entries, [], config={"superseded": True})
    assert "dead end" in md and "_(superseded)_" in md


def test_render_separate_lanes_and_title():
    entries = derive_entries(
        [msg("n1", "2026-08-21T10:00:00.000Z"),
         msg("p1", "2026-08-21T10:00:30.000Z", source="composer")], [])
    md = render_session_markdown(KEY, entries, [], config={"lanes": "separate"},
                                 title="Walkthrough sitting 2")
    assert "— Walkthrough sitting 2" in md
    assert "## Transcript" in md and "## Composition" in md
    assert md.index("## Transcript") < md.index("## Composition")


def test_render_bodies_verbatim_headers_never_headings():
    entries = derive_entries(
        [msg("n1", "2026-08-21T10:00:00.000Z", role="assistant",
             text="## A body heading\n\ncode: `x = 1`")], [])
    md = render_session_markdown(KEY, entries, [])
    assert "## A body heading" in md               # body embeds verbatim
    assert "**CLAUDE**" in md                      # header is a bold line, not a heading


def test_render_chain_filters():
    entries = derive_entries(
        [msg("n1", "2026-08-21T10:00:00.000Z"),
         msg("p1", "2026-08-21T10:00:30.000Z", source="composer", text="only part")], [])
    md = render_session_markdown(KEY, entries, [], config={"transcript": False})
    assert "only part" in md and "hello" not in md


def test_header_stamps_render_local_time():
    # Stored stamps are UTC Z; the projection renders them in local time
    # (tz pinned so the assertion is machine-independent); odd shapes pass
    # through verbatim.
    pinned = timezone(timedelta(hours=-7))
    assert _local_stamp("2026-08-22T04:25:10.574Z", tz=pinned) == "2026-08-21 21:25:10"
    assert _local_stamp("garbage", tz=pinned) == "garbage"


def test_read_session_messages_orders_filters_role_and_hides_branches(monkeypatch):
    """1d8d4486: `read --session` delivers the ACTIVE transcript path in chain order,
    role-filtered; superseded branches and composer parts are opt-in."""
    import asyncio
    import cjm_context_graph_projection.scratchpad_export as se

    async def fake_load(gx, key):
        return {"messages": [msg("n3", "2026-08-21T10:02:00.000Z", text="take 2"),
                             msg("n1", "2026-08-21T10:00:00.000Z", text="q"),
                             msg("n2", "2026-08-21T10:01:00.000Z", role="assistant",
                                 text="dead end"),
                             msg("p1", "2026-08-21T10:00:30.000Z", source="composer",
                                 text="draft")],
                "next_pairs": [("n1", "n2"), ("n1", "n3")], "derived_pairs": [],
                "title": "T"}

    monkeypatch.setattr(se, "_load_session_messages", fake_load)
    res = asyncio.run(se.read_session_messages(None, KEY))
    assert res["kind"] == "messages" and [m["id"] for m in res["items"]] == ["n1", "n3"]
    users = asyncio.run(se.read_session_messages(None, KEY, role="user"))
    assert [m["text"] for m in users["items"]] == ["q", "take 2"]
    everything = asyncio.run(se.read_session_messages(None, KEY, include_superseded=True,
                                                      include_parts=True))
    assert [m["id"] for m in everything["items"]] == ["n1", "p1", "n2", "n3"]
    assert everything["items"][2]["on_active_path"] is False
