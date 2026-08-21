"""The exporter lens's pure halves: entry derivation + markdown rendering."""

from cjm_context_graph_projection.scratchpad_export import (
    derive_entries, render_session_markdown)

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
