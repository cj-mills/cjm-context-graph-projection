"""The pure-notes lane (ruling a7262fe7, item fdafeed9): pure pieces (spine choice, pack,
row validation, rendering, coverage/overlap), then the CLI chain end to end over a FAKE
sibling graph — type -> pack -> ingest -> accept -> review -> render -> frontier -> retract
-> replay (Points + Sections reproduced from the journal alone; the sibling never opened)."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.journal import read_journal
from cjm_context_graph_primitives.query import EdgeQuery
from cjm_context_graph_projection.purenotes import (_time_link, build_notes_pack, build_point_tree,
                                                    choose_spine, coverage_gaps, derive_frontmatter,
                                                    derived_description, overlapping_points,
                                                    proposals_from_point_rows, pure_notes_type,
                                                    render_notes_pack, render_points,
                                                    render_source_card, synopsis_of, unit_label,
                                                    unit_title_header, validate_point_rows,
                                                    _frontmatter_fields, render_works_table)
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()


def _run(*args):
    return subprocess.run([sys.executable, "-m", "cjm_context_graph_projection.cli", *args],
                          capture_output=True, text=True)


# ---------------------------------------------------------------- pure pieces

def test_choose_spine_auto_legacy_prefix_and_refusals():
    assert choose_spine({None: 10}) == (None, True)
    assert choose_spine({"sha256:abc": 5}) == ("sha256:abc", False)
    with pytest.raises(ValueError):
        choose_spine({None: 10, "sha256:abc": 5})                     # two coexist: refuse
    assert choose_spine({None: 10, "sha256:abc": 5}, "legacy") == (None, True)
    assert choose_spine({None: 10, "sha256:abcdef": 5}, "abc") == ("sha256:abcdef", False)
    with pytest.raises(ValueError):
        choose_spine({"sha256:abc": 1, "sha256:abd": 1}, "ab")        # ambiguous prefix
    with pytest.raises(ValueError):
        choose_spine({"sha256:abc": 1}, "legacy")                     # no legacy spine


def _unit():
    segs = [
        {"id": "s0", "index": 0, "text": "Chapter 1. Seven lessons.", "start": 0.0, "end": 2.0},   # apparatus header
        {"id": "s1", "index": 1, "text": "Gatto quit teaching in 1991.", "start": 2.0, "end": 5.0},
        {"id": "s2", "index": 2, "text": "By the way my mic is bad.", "start": 5.0, "end": 6.0},   # tangent
        {"id": "s3", "index": 3, "text": "Lesson 1. Confusion.", "start": 6.0, "end": 7.0},        # apparatus header
        {"id": "s4", "index": 4, "text": "Quote. I teach confusion.", "start": 7.0, "end": 9.0},   # quotation
        {"id": "s5", "index": 5, "text": "End quote.", "start": 9.0, "end": 9.5},                  # quotation
        {"id": "s6", "index": 6, "text": "Subjects taught in isolation.", "start": 9.5, "end": 12.0},
        {"id": "s7", "index": 7, "text": "", "start": 12.0, "end": 12.5},
    ]
    strata = [
        {"id": "c-app1", "correction_type": "stratum", "payload": {"category": "section-header", "segment_ids": ["s0"], "start_time": 0.0}},
        {"id": "c-tan", "correction_type": "stratum", "payload": {"category": "tangent", "segment_ids": ["s2"], "start_time": 5.0}},
        {"id": "c-app2", "correction_type": "stratum", "payload": {"category": "section-header", "segment_ids": ["s3"], "start_time": 6.0}},
        {"id": "c-q", "correction_type": "stratum", "payload": {"category": "quotation", "segment_ids": ["s4", "s5"], "start_time": 7.0}},
        {"id": "c-rm", "correction_type": "stratum", "payload": {"category": "research-mark", "segment_ids": ["s1"], "start_time": 2.0}},
    ]
    return {"source": {"source_id": "src", "title": "Ch 1", "skeleton_hash": None,
                       "work_structure": {"kind": "chapter", "chapter": 1, "title": "Seven Dangerous Lessons"}},
            "segments": [s for s in segs if s["text"]], "strata": strata}


def test_pack_applies_the_stratum_query_headers_quote_spans_and_digest():
    tprops = pure_notes_type().to_graph_node()["properties"]
    pack = build_notes_pack(_unit(), tprops)
    ids = [r["id"] for r in pack["segments"]]
    assert ids == ["s1", "s4", "s5", "s6"]                       # tangent out, headers out, research-mark KEPT (content)
    assert [(h["i_before"], h["text"]) for h in pack["headers"]] == [(0, "Chapter 1. Seven lessons."), (1, "Lesson 1. Confusion.")]
    assert [r["h"] for r in pack["segments"]] == [1, 2, 2, 2]     # header count before each line
    assert pack["quote_spans"] == [{"stratum_id": "c-q", "from_i": 1, "to_i": 2}]
    assert any(k["kind"] == "quotation" for k in pack["kinds"])
    d1 = pack["digest"]
    again = build_notes_pack(_unit(), tprops)
    assert again["digest"] == d1 and again["pack_id"] != pack["pack_id"]   # digest = the read, not the id
    brief = render_notes_pack(pack)
    assert "[H] Chapter 1. Seven lessons." in brief and "[H] Lesson 1. Confusion." in brief
    assert "[0] 00:02–00:05  Gatto quit teaching in 1991." in brief
    assert "lines 1–2" in brief and "## Output contract" in brief
    # a window trims lines
    win = build_notes_pack(_unit(), tprops, window=(6.5, None))
    assert [r["id"] for r in win["segments"]] == ["s4", "s5", "s6"]


def test_validate_rows_and_resolve_proposals():
    pack = build_notes_pack(_unit(), pure_notes_type().to_graph_node()["properties"])
    good = [{"kind": "claim", "from_i": 0, "to_i": 0, "text": "Gatto quit 1991.", "lead": "Gatto"},
            {"kind": "quotation", "from_i": 1, "to_i": 2, "text": "I teach confusion.", "attribution": "Gatto"},
            {"kind": "claim", "from_i": 3, "to_i": 3, "text": "Subjects taught in isolation."}]
    rows = validate_point_rows(good, pack)
    props = proposals_from_point_rows(rows, pack)
    assert [p["heading"] for p in props] == ["Chapter 1. Seven lessons.", "Lesson 1. Confusion.", "Lesson 1. Confusion."]
    assert props[1]["segment_ids"] == ["s4", "s5"] and props[1]["start_time"] == 7.0 and props[1]["end_time"] == 9.5
    assert props[0]["heading_index"] == 1 and props[1]["heading_index"] == 2
    assert len({p["proposal_id"] for p in props}) == 3 and props[0]["evidence"]["digest"] == pack["digest"]
    top = {"kind": "claim", "from_i": 3, "to_i": 3, "text": "Subjects in isolation."}
    for bad, msg in ((
            [{"kind": "Claim!", "from_i": 0, "to_i": 0, "text": "x"}], "kebab-case"),
            ([{"kind": "claim", "from_i": 0, "to_i": 9, "text": "x"}], "outside the pack"),
            ([{"kind": "claim", "from_i": 0, "to_i": 1, "text": "x"}], "crosses a header"),
            ([{"kind": "claim", "from_i": 0, "to_i": 0, "text": "  "}], "empty"),
            ([{"kind": "comparison", "from_i": 0, "to_i": 0, "text": "x"}], "columns"),
            # e1fd4d64 (I): a lead is bolded IN PLACE, so it must appear in the text (definition exempt)
            ([{"kind": "claim", "from_i": 0, "to_i": 0, "text": "Quit in 1991.", "lead": "Gatto"}], "does not appear"),
            # 5625b74e (2): at most ONE arrow per point — the ch. 2 page chained them
            ([{"kind": "claim", "from_i": 0, "to_i": 0, "text": "Gatto → quit → 1991."}], "at most ONE"),
            # e1fd4d64 (H): parent = an EARLIER, top-level, same-header row
            ([{"kind": "claim", "from_i": 3, "to_i": 3, "text": "x", "parent": 0}], "EARLIER"),
            ([top, {"kind": "claim", "from_i": 3, "to_i": 3, "text": "y", "parent": 0},
              {"kind": "claim", "from_i": 3, "to_i": 3, "text": "z", "parent": 1},
              {"kind": "claim", "from_i": 3, "to_i": 3, "text": "w", "parent": 2}], "two levels"),
            ([{"kind": "claim", "from_i": 0, "to_i": 0, "text": "x"},
              {"kind": "claim", "from_i": 3, "to_i": 3, "text": "y", "parent": 0}], "another header"),
            # second-read ruling: an event is a sequence's child with a `when`; one unit-spanning synopsis
            ([top, {"kind": "event", "from_i": 3, "to_i": 3, "text": "e", "parent": 0, "data": {"when": "1991"}}], "`sequence`"),
            ([{"kind": "sequence", "from_i": 3, "to_i": 3, "text": "s"},
              {"kind": "event", "from_i": 3, "to_i": 3, "text": "e", "parent": 0}], "data.when"),
            ([{"kind": "synopsis", "from_i": 0, "to_i": 2, "text": "s"}], "spans the whole unit"),
            ([{"kind": "synopsis", "from_i": 0, "to_i": 3, "text": "s"},
              {"kind": "synopsis", "from_i": 0, "to_i": 3, "text": "t"}], "second synopsis")):
        with pytest.raises(ValueError) as ei:
            validate_point_rows(bad, pack)
        assert msg in str(ei.value)
    assert validate_point_rows([{"kind": "definition", "from_i": 0, "to_i": 0, "text": "Quit in 1991.", "lead": "Gatto"}], pack)[0]["lead"] == "Gatto"
    lenient = validate_point_rows([{"kind": "claim", "from_i": 0, "to_i": 0, "text": "Quit in 1991.", "lead": "Gatto"}], pack, lenient_leads=True)
    assert lenient[0]["lead"] == ""
    # a nested row resolves to the parent's proposal id and sorts DIRECTLY after its parent
    nested = validate_point_rows([{"kind": "claim", "from_i": 1, "to_i": 2, "text": "Confusion quote."},
                                  top,
                                  {"kind": "example", "from_i": 3, "to_i": 3, "text": "Trig never meets a house.", "parent": 1},
                                  {"kind": "claim", "from_i": 3, "to_i": 3, "text": "Another top."}], pack)
    props = proposals_from_point_rows(nested, pack)
    assert [p["text"] for p in props] == ["Confusion quote.", "Subjects in isolation.", "Trig never meets a house.", "Another top."]
    assert props[2]["parent_key"] == props[1]["proposal_id"] and props[1]["parent_key"] == "" and props[3]["parent_key"] == ""
    # depth two: a sequence, its events, an event's support — and the synopsis sorts LAST whatever its lines
    deep = validate_point_rows([{"kind": "synopsis", "from_i": 0, "to_i": 3, "text": "Gatto quit; confusion taught."},
                                {"kind": "sequence", "from_i": 3, "to_i": 3, "text": "Gatto's exit"},
                                {"kind": "event", "from_i": 3, "to_i": 3, "text": "quits", "parent": 1, "data": {"when": "1991"}},
                                {"kind": "claim", "from_i": 3, "to_i": 3, "text": "Article shook many", "parent": 2},
                                {"kind": "claim", "from_i": 1, "to_i": 2, "text": "Earlier top."}], pack)
    props = proposals_from_point_rows(deep, pack)
    assert [p["text"] for p in props] == ["Earlier top.", "Gatto's exit", "quits", "Article shook many", "Gatto quit; confusion taught."]
    assert props[3]["parent_key"] == props[2]["proposal_id"] and props[2]["parent_key"] == props[1]["proposal_id"]


def _points():
    return [
        {"id": "p1", "key": "aaaaaaaa-1", "kind": "claim", "text": "Quit in 1991.", "lead": "Gatto",
         "heading": "Chapter 1.", "heading_index": 1, "segment_ids": ["s1"], "start_time": 2.0, "end_time": 5.0, "ordinal": 0},
        {"id": "p2", "key": "bbbbbbbb-2", "kind": "quotation", "text": "I teach confusion.", "attribution": "Gatto",
         "heading": "Lesson 1.", "heading_index": 2, "segment_ids": ["s4", "s5"], "start_time": 7.0, "end_time": 9.5, "ordinal": 1},
        {"id": "p3", "key": "cccccccc-3", "kind": "step", "text": "Assess models.", "heading": "Lesson 1.",
         "heading_index": 2, "segment_ids": ["s6"], "start_time": 9.5, "end_time": 10.0, "ordinal": 3},
        {"id": "p4", "key": "dddddddd-4", "kind": "step", "text": "Copy them.", "heading": "Lesson 1.",
         "heading_index": 2, "segment_ids": ["s6"], "start_time": 10.0, "end_time": 12.0, "ordinal": 3},
        {"id": "p5", "key": "eeeeeeee-5", "kind": "comparison", "text": "Advisors by period.", "lead": "Advisors",
         "heading": "Lesson 1.", "heading_index": 2, "segment_ids": ["s6"], "start_time": 11.0, "end_time": 12.0, "ordinal": 3,
         "data": {"columns": ["Period", "Nation"], "rows": [["1870s", "France"], ["later", "Germany"]]}},
    ]


def _glyph(key8):
    return f"[§](#pt-{key8}){{#pt-{key8} .pt-anchor}}"


def test_render_expanded_is_the_public_post_glyph_anchors_no_outline_no_audiobook_spans():
    """Ruling e1fd4d64: the default rendering is EXPANDED (the outline is the review view, (E));
    every point carries a visible permalink glyph that IS its anchor (F); a source with no public
    time-addressable URL renders no spans (D); typed kinds keep their shapes (a7262fe7)."""
    out = render_points(_points())
    assert out == render_points(_points())
    assert out.startswith(f"## Chapter 1\n\n- **Gatto** — Quit in 1991. {_glyph('aaaaaaaa')}\n")   # a legacy lead the text lacks: prefix fallback
    assert "At a glance" not in out and "(00:" not in out and "[]{#" not in out
    assert f"> I teach confusion.\n> — Gatto {_glyph('bbbbbbbb')}\n" in out                       # quotation block
    assert f"1. Assess models. {_glyph('cccccccc')}\n2. Copy them. {_glyph('dddddddd')}\n" in out   # steps = ONE list
    assert f"- **Advisors** by period. {_glyph('eeeeeeee')}\n\n  | Period | Nation |\n  |---|---|\n  | 1870s | France |\n  | later | Germany |" in out  # comparison = table INSIDE its item; lead bolded in place
    assert "**" not in out.replace("**Gatto**", "").replace("**Advisors**", "")                     # lead term is the ONLY emphasis
    assert render_points([]) == ""
    outline = render_points(_points(), rendering="outline")
    assert outline.startswith("## At a glance\n\n**Chapter 1**\n\n- **Gatto** — Quit in 1991.\n")
    assert "- **Gatto**: “I teach confusion.”" in outline and "#pt-" not in outline              # a quotation scans as who + opening words
    both = render_points(_points(), rendering="both")
    assert "- [**Gatto** — Quit in 1991.](#pt-aaaaaaaa)" in both and "## Chapter 1\n" in both


def test_render_timestamps_policy_always_addressable_never():
    """e1fd4d64 (D): spans render ALWAYS on request, only as LINKS when the unit carries a public
    time-addressable URL under the default `addressable`, never on `never`."""
    assert "- **Gatto** — Quit in 1991. (00:02–00:05) [§]" in render_points(_points(), timestamps="always")
    pts = _points()
    for p in pts:
        p["unit"] = {"public_url": "https://www.youtube.com/watch?v=abc"}
    addressable = render_points(pts)
    assert "Quit in 1991. [(00:02–00:05)](https://www.youtube.com/watch?v=abc&t=2s) [§]" in addressable
    assert "> — Gatto [(00:07–00:09)](https://www.youtube.com/watch?v=abc&t=7s) [§]" in addressable
    assert "(00:" not in render_points(pts, timestamps="never")
    assert _time_link("https://youtu.be/abc", 65.9) == "https://youtu.be/abc?t=65"
    assert _time_link("https://pod.example/ep1.mp3", 5) == "https://pod.example/ep1.mp3#t=5"


def test_render_lead_in_place_definition_keeps_the_glossary_shape():
    """e1fd4d64 (I): a lead is bolded where it occurs in the text; only `definition` prefixes."""
    base = {"heading": "Lesson 7", "heading_index": 1, "segment_ids": ["s1"], "start_time": 1.0, "end_time": 2.0}
    pts = [{"id": "p1", "key": "11111111", "kind": "claim", "ordinal": 0, "lead": "troublemakers",
            "text": "Kids labeled Troublemakers for asking hard questions.", **base},
           {"id": "p2", "key": "22222222", "kind": "definition", "ordinal": 1, "lead": "Budget",
            "text": "A plan for money not yet spent.", **base},
           # 06fa8cb5: a definition whose text already carries the term (the proposer's self-check moved it
           # in) is bolded in place — never "**Term** — Term — gloss" (ch. 2's Elastic thinking row)
           {"id": "p3", "key": "33333333", "kind": "definition", "ordinal": 2, "lead": "Elastic thinking",
            "text": "Elastic thinking — new perspectives by letting the mind wander.", **base}]
    out = render_points(pts)
    assert f"- Kids labeled **Troublemakers** for asking hard questions. {_glyph('11111111')}\n" in out   # case kept, bolded in place
    assert f"- **Budget** — A plan for money not yet spent. {_glyph('22222222')}\n" in out
    assert f"- **Elastic thinking** — new perspectives by letting the mind wander. {_glyph('33333333')}\n" in out
    assert "Elastic thinking — Elastic thinking" not in out
    assert render_points(pts, rendering="outline").startswith("## At a glance\n\n**Lesson 7**\n\n- Kids labeled **Troublemakers**")


def test_render_one_level_nesting_and_unit_title_header_suppression_and_derived_frontmatter():
    """e1fd4d64 (H): a child renders as a sub-item under its parent (quotation inline); an orphan
    (parent retracted) stays top-level. (C): the first header restating the unit's own title is
    not a section. (A)+(B): title and description are derived from the same data."""
    unit = {"title": "04 - 1. Seven Dangerous Lessons Taught in Schools",
            "work_structure": {"kind": "chapter", "part": 1, "part_title": "School", "chapter": 1,
                               "title": "Seven Dangerous Lessons Taught in Schools"}}
    l1 = {"heading": "Lesson 1. Confusion", "heading_index": 2, "unit": unit}
    part = {"heading": "Part 1. School. Chapter 1. Seven Dangerous Lessons Taught in Schools.", "heading_index": 1, "unit": unit}
    pts = [
        {"id": "a", "key": "aaaa0001", "kind": "claim", "text": "Gatto quit.", "ordinal": 0, "segment_ids": ["s0"],
         "start_time": 0.0, "end_time": 1.0, **part},
        {"id": "a2", "key": "aaaa0002", "kind": "claim", "text": "Denounced the system.", "ordinal": 1, "segment_ids": ["s0b"],
         "start_time": 1.0, "end_time": 1.5, "parent_key": "aaaa0001", **part},   # same suppressed header: ONE group, still nested
        {"id": "b", "key": "bbbb0001", "kind": "claim", "text": "Subjects taught in isolation.", "ordinal": 1,
         "segment_ids": ["s1"], "start_time": 2.0, "end_time": 3.0, **l1},
        {"id": "c", "key": "cccc0001", "kind": "example", "text": "Trigonometry never meets a house plan.", "ordinal": 2,
         "segment_ids": ["s2"], "start_time": 3.0, "end_time": 4.0, "parent_key": "bbbb0001", **l1},
        {"id": "d", "key": "dddd0001", "kind": "quotation", "text": "I teach confusion.", "attribution": "Gatto",
         "ordinal": 3, "segment_ids": ["s3"], "start_time": 4.0, "end_time": 5.0, "parent_key": "bbbb0001", **l1},
        {"id": "e", "key": "eeee0001", "kind": "claim", "text": "Orphan.", "ordinal": 4, "segment_ids": ["s4"],
         "start_time": 6.0, "end_time": 7.0, "parent_key": "gone0000", **l1},
    ]
    out = render_points(pts)
    assert out.startswith(f"- Gatto quit. {_glyph('aaaa0001')}\n  - Denounced the system. {_glyph('aaaa0002')}\n\n"
                          "## Lesson 1. Confusion\n\n")   # the unit's own title is no section; its points stay one nested group
    assert "Part 1" not in out
    assert (f"- Subjects taught in isolation. {_glyph('bbbb0001')}\n"
            f"  - Trigonometry never meets a house plan. {_glyph('cccc0001')}\n"
            f"\n  > I teach confusion.\n  > — Gatto {_glyph('dddd0001')}\n\n"      # a child quotation keeps the block form (ruling (5))
            f"- Orphan. {_glyph('eeee0001')}\n") in out
    both = render_points(pts, rendering="both")
    assert "- [Subjects taught in isolation.](#pt-bbbb0001)\n  - [Trigonometry never meets a house plan.](#pt-cccc0001)\n" in both
    assert unit_title_header("Chapter 1. Seven Dangerous Lessons Taught in Schools.", unit)
    assert unit_title_header("Part 1. School. Chapter 1. Seven dangerous lessons taught in schools", unit)
    assert not unit_title_header("Lesson 1. Confusion", unit) and not unit_title_header("", unit)
    desc = "Seven Dangerous Lessons Taught in Schools: Lesson 1. Confusion. What the chapter says, in its own order, without added commentary."
    assert derived_description(pts) == desc and derived_description([]) == ""
    fm = '---\ntitle: "The Learning Game, Chapter 1 — notes"\ndate: 2026-09-07\ncategories: [book]\n---\n'
    policy = {"title": "unit-title", "description": "derived"}
    d = derive_frontmatter(fm, pts, policy)
    assert d == ('---\ntitle: "Seven Dangerous Lessons Taught in Schools"\n'
                 f'description: "{desc}"\ndate: 2026-09-07\ncategories: [book]\n---\n')
    assert derive_frontmatter(d, pts, policy) == d                       # idempotent over derived lines
    assert derive_frontmatter(fm, pts, {}) == fm and derive_frontmatter(fm, [], policy) == fm


def test_render_depth_two_events_synopsis_source_card_and_short_title():
    """Second-read ruling: depth two (an event's support under the event), a sequence's items
    as `event` children, the synopsis out of the body and into the description, the source
    card from the work metadata, the short title shape."""
    unit = {"title": "04 - 1. Seven Dangerous Lessons Taught in Schools",
            "work_structure": {"kind": "chapter", "part": 1, "part_title": "School", "chapter": 1,
                               "title": "Seven Dangerous Lessons Taught in Schools",
                               "work": {"title": "The Learning Game", "author": "Ana Lorena Fábrega"}}}
    base = {"heading": "Part 1. School. Chapter 1. Seven Dangerous Lessons Taught in Schools.", "heading_index": 1, "unit": unit}
    pts = [
        {"id": "s", "key": "seq00001", "kind": "sequence", "text": "Gatto's break with the system", "lead": "Gatto",
         "ordinal": 0, "segment_ids": ["s0"], "start_time": 0.0, "end_time": 9.0, **base},
        {"id": "e1", "key": "evt00001", "kind": "event", "text": "Wall Street Journal article: he quits", "ordinal": 1,
         "segment_ids": ["s1"], "start_time": 1.0, "end_time": 3.0, "parent_key": "seq00001", "data": {"when": "1991"}, **base},
        {"id": "c", "key": "cla00001", "kind": "claim", "text": "Announcement shook many", "ordinal": 2,
         "segment_ids": ["s2"], "start_time": 2.0, "end_time": 3.0, "parent_key": "evt00001", **base},
        {"id": "e2", "key": "evt00002", "kind": "event", "text": "publishes Dumbing Us Down", "ordinal": 3,
         "segment_ids": ["s3"], "start_time": 4.0, "end_time": 5.0, "parent_key": "seq00001", "data": {"when": "months later"}, **base},
        {"id": "x", "key": "cla00002", "kind": "claim", "text": "Not part of the series", "ordinal": 4,
         "segment_ids": ["s4"], "start_time": 6.0, "end_time": 7.0, "parent_key": "seq00001", **base},
        {"id": "y", "key": "syn00001", "kind": "synopsis", "text": "Gatto's seven lessons diagnose what school teaches by design.",
         "ordinal": 5, "segment_ids": ["s0", "s1", "s2", "s3", "s4"], "start_time": 0.0, "end_time": 9.0, **base},
    ]
    out = render_points(pts)
    assert out == (f"- **Gatto**'s break with the system {_glyph('seq00001')}\n"
                   f"  1. **1991** — Wall Street Journal article: he quits {_glyph('evt00001')}\n"
                   f"     - Announcement shook many {_glyph('cla00001')}\n"
                   f"  2. **months later** — publishes Dumbing Us Down {_glyph('evt00002')}\n"
                   f"  - Not part of the series {_glyph('cla00002')}\n")
    assert "seven lessons diagnose" not in out                                  # the synopsis never renders in the body
    assert synopsis_of(pts) == "Gatto's seven lessons diagnose what school teaches by design."
    tree = build_point_tree(pts)
    assert [n["p"]["key"] for n in tree] == ["seq00001", "syn00001"]
    assert [c["p"]["key"] for c in tree[0]["kids"]] == ["evt00001", "evt00002", "cla00002"]
    assert tree[0]["kids"][0]["kids"][0]["p"]["key"] == "cla00001"
    assert unit_label(unit) == "Ch. 1" and unit_label({"work_structure": {"kind": "front-matter", "title": "Foreword"}}) == "Foreword"
    card = render_source_card(unit)
    assert card == ("::: {.callout-note appearance=\"simple\" icon=false}\n"
                    "Notes on **The Learning Game** by Ana Lorena Fábrega — Part 1 (School) · Chapter 1, "
                    "*Seven Dangerous Lessons Taught in Schools*. The points paraphrase the chapter in its own order; "
                    "only the quotations are verbatim.\n:::\n")
    assert render_source_card({"work_structure": {"kind": "chapter", "title": "x"}}) == ""       # no work metadata: no card
    fm = '---\ntitle: "old"\ndate: 2026-09-08\n---\n'
    policy = {"title": "work-unit-notes", "description": "synopsis"}
    d = derive_frontmatter(fm, pts, policy, synopsis=synopsis_of(pts))
    assert d == ('---\ntitle: "The Learning Game, Ch. 1 notes"\n'
                 'description: "Gatto\'s seven lessons diagnose what school teaches by design."\ndate: 2026-09-08\n---\n')
    # without a synopsis the description falls back to the derived headings; without work metadata the title falls back
    d2 = derive_frontmatter(fm, pts, policy)
    assert 'description: "The Learning Game — Seven Dangerous Lessons Taught in Schools. What the chapter says, in its own order' in d2
    bare = [dict(p, unit={"work_structure": {"kind": "chapter", "chapter": 1, "title": "Seven Dangerous Lessons"}}) for p in pts]
    assert derive_frontmatter(fm, bare, policy).startswith('---\ntitle: "Seven Dangerous Lessons"\n')


def test_coverage_gaps_and_overlap_pairs():
    pack = build_notes_pack(_unit(), pure_notes_type().to_graph_node()["properties"])
    gaps = coverage_gaps(pack["segments"], [{"segment_ids": ["s4", "s5"]}])
    assert [(g["from_i"], g["to_i"], g["lines"]) for g in gaps] == [(0, 0, 1), (3, 3, 1)]
    assert gaps[0]["text"] == "Gatto quit teaching in 1991." and gaps[1]["start"] == 9.5
    assert coverage_gaps(pack["segments"], [{"segment_ids": ["s1", "s4", "s5", "s6"]}]) == []
    pairs = overlapping_points(_points())
    keys = {(p["a"]["id"], p["b"]["id"]): p for p in pairs}
    assert ("p3", "p4") in keys and keys[("p3", "p4")]["same_kind"] is True     # two steps on one segment: flagged
    assert ("p3", "p5") in keys and keys[("p3", "p5")]["same_kind"] is False
    assert ("p1", "p2") not in keys


def test_frontmatter_fields_and_works_table_are_plain_data():
    # Item 140981e9 (b)/(c): the staging index parses only the listing fields (a date becomes
    # its ISO string), tolerates absent or broken frontmatter, and the works table names the
    # held work's missing chapters.
    fm = '---\ntitle: "The Learning Game, Ch. 2 notes"\ndate: 2026-09-08\ncategories: [book, education, notes]\ndescription: "School descends from Prussia."\nextra: ignored\n---\n'
    assert _frontmatter_fields(fm) == {"title": "The Learning Game, Ch. 2 notes", "date": "2026-09-08",
                                       "categories": ["book", "education", "notes"],
                                       "description": "School descends from Prussia."}
    assert _frontmatter_fields("no frontmatter") == {} and _frontmatter_fields("---\ntitle: [unclosed\n---\n") == {}
    assert _frontmatter_fields("---\n- a list, not a map\n---\n") == {}
    works = [{"work": "The Learning Game", "chapters_total": 19, "chapters_born": 2, "condition_met": False,
              "missing": [{"chapter": 3, "title": "How Tests and Rewards Go Wrong"}, {"chapter": None, "title": "Conclusion"}]},
             {"work": "Superstruct Manifesto", "chapters_total": 4, "chapters_born": 4, "condition_met": True, "missing": []}]
    table = render_works_table(works)
    assert "| The Learning Game | 2 of 19 | HELD | ch. 3, Conclusion |" in table
    assert "| Superstruct Manifesto | 4 of 4 | READY — every chapter born | — |" in table
    assert render_works_table([]).startswith("_No works")


def test_source_card_folds_resolved_references_and_a_dangling_target_renders_as_its_label():
    # Item ae103970 (ruling a7ca900d (3)): human-added links render INSIDE the source card as a
    # Resources line, in the order given; a link with no resolvable target is its label alone.
    unit = {"work_structure": {"kind": "chapter", "part": 1, "chapter": 1, "title": "Seven Dangerous Lessons",
                               "work": {"title": "The Learning Game", "author": "Ana Lorena Fábrega"}}}
    plain = render_source_card(unit)
    assert plain.startswith("::: {.callout-note") and "Resources" not in plain
    assert render_source_card(unit, []) == plain and render_source_card(unit, None) == plain
    card = render_source_card(unit, [
        {"label": "Dumbing Us Down (publisher page)", "href": "https://newsociety.com/dud"},
        {"label": "Notes on Dumbing Us Down", "href": "/posts/dumbing-us-down/ch01-notes/"},
        {"label": "no target yet", "href": ""},
        {"label": "   ", "href": "https://ignored.example"}])
    assert card.startswith(plain.split("\n:::")[0])
    assert ("\n\nResources: [Dumbing Us Down (publisher page)](https://newsociety.com/dud) · "
            "[Notes on Dumbing Us Down](/posts/dumbing-us-down/ch01-notes/) · no target yet\n:::\n") in card
    assert "ignored.example" not in card
    assert render_source_card({}, [{"label": "x", "href": "y"}]) == ""    # no work metadata -> no card at all


# ---------------------------------------------------------------- the CLI chain

def _build_sibling(sdb: str):
    """A FAKE transcription graph: one Source with a structure map, a spine of Segments,
    strata Corrections (apparatus header / tangent / quotation) over them."""
    async def go():
        async with open_graph(sdb) as sg:
            nodes = [{"id": "src-1", "label": "Source", "sources": [],
                      "properties": {"title": "The Learning Game — 04 - 1. Seven Dangerous Lessons",
                                     "work_structure": {"kind": "chapter", "part": 1, "chapter": 1,
                                                        "title": "Seven Dangerous Lessons",
                                                        "work": {"title": "The Learning Game", "author": "Ana Lorena Fábrega"}}}}]
            for i, (t, a, b) in enumerate([("Chapter 1. Seven dangerous lessons.", 0.0, 2.0),
                                           ("Gatto quit teaching in 1991.", 2.0, 5.0),
                                           ("By the way my mic is bad.", 5.0, 6.0),
                                           ("Lesson 1. Confusion.", 6.0, 7.0),
                                           ("Quote. I teach confusion.", 7.0, 9.0),
                                           ("End quote.", 9.0, 9.5),
                                           ("Subjects taught in isolation.", 9.5, 12.0),
                                           ("Kids never build a coherent picture.", 12.0, 14.0)]):
                nodes.append({"id": f"seg-{i}", "label": "Segment", "sources": [],
                              "properties": {"text": t, "index": i, "start_time": a, "end_time": b,
                                             "source_id": "src-1"}})
            for cid, cat, sids, st in (("cor-h1", "section-header", ["seg-0"], 0.0), ("cor-t", "tangent", ["seg-2"], 5.0),
                                       ("cor-h2", "section-header", ["seg-3"], 6.0), ("cor-q", "quotation", ["seg-4", "seg-5"], 7.0)):
                nodes.append({"id": cid, "label": "Correction", "sources": [],
                              "properties": {"correction_type": "stratum", "status": "applied", "actor": "human",
                                             "session_id": "s", "created_at": 1.0,
                                             "payload": {"operation": "classify", "source_id": "src-1", "category": cat,
                                                         "segment_ids": sids, "start_time": st}}})
            await extend_graph(sg.queue, sg.graph_id, nodes, [])
    asyncio.run(go())


def _edit_sibling_segment(sdb: str, seg_id: str, text: str):
    async def go():
        async with open_graph(sdb) as sg:
            await graph_task(sg.queue, sg.graph_id, "update_node", node_id=seg_id, properties={"text": text})
    asyncio.run(go())


@pytest.mark.skipif(not _HAVE_GRAPH, reason=f"graph capability {DEFAULT_GRAPH_ID!r} not installed (CI)")
def test_cli_pure_notes_lane_end_to_end_and_replay(tmp_path):
    sib_dir, pri_dir = tmp_path / "sib", tmp_path / "pri"
    sib_dir.mkdir(); pri_dir.mkdir()
    sdb = str(sib_dir / "sib.db")
    _build_sibling(sdb)
    pdb, pj = str(pri_dir / "pri.db"), str(pri_dir / "writes.jsonl")
    lane = pri_dir / "purenotes"
    (pri_dir / "graph.config.json").write_text(json.dumps(
        {"notes_profile": "quarto_post", "emit_root": str(pri_dir / "staging"),
         "sibling_graphs": {"tx": sdb}}))
    base = ("--graph-db-path", pdb, "--journal-path", pj, "--source-journal-path", str(pri_dir / "source.jsonl"))

    # (1) the type profile as data, journaled; a re-mint is an upsert
    r = _run(*base, "notes-type", "pure-notes")
    assert r.returncode == 0 and "created" in r.stdout, r.stderr or r.stdout
    r = _run(*base, "notes-type", "pure-notes")
    assert "updated" in r.stdout
    assert len([o for o in read_journal(pj) if o["verb"] == "deliverable-type"]) == 1   # identical op deduped
    # pack refuses before the type exists for an unknown type
    r = _run(*base, "notes-pack", "--source", "Seven Dangerous", "--type", "essay")
    assert r.returncode == 1 and "not minted" in r.stderr

    # (2) the deliverable is BORN with authored frontmatter + preamble only (draft at birth)
    post = ("---\ntitle: \"The Learning Game, Chapter 1\"\ndate: 2026-09-07\ncategories: [book, notes]\n---\n\n"
            "::: {.callout-note title=\"About this note\"}\nPure notes born on the graph.\n:::\n")
    r = _run(*base, "new-note", "--slug", "the-learning-game/ch01", "--content", post)
    assert r.returncode == 0, r.stderr or r.stdout
    note_id = [o for o in read_journal(pj) if o["verb"] == "assert"][-1]["args"]["subject"]

    # (3) pack: the stratum query applied over the sibling, headers + quote spans + brief
    r = _run(*base, "notes-pack", "--source", "Seven Dangerous")
    assert r.returncode == 0, r.stderr or r.stdout
    pack_json = next((lane / "packs").glob("npack_*.json"))
    pack = json.loads(pack_json.read_text())
    assert [s["text"] for s in pack["segments"]] == ["Gatto quit teaching in 1991.", "Quote. I teach confusion.",
                                                      "End quote.", "Subjects taught in isolation.",
                                                      "Kids never build a coherent picture."]
    assert [h["text"] for h in pack["headers"]] == ["Chapter 1. Seven dangerous lessons.", "Lesson 1. Confusion."]
    assert pack["quote_spans"] == [{"stratum_id": "cor-q", "from_i": 1, "to_i": 2}]
    assert pack["source"]["graph"] == "tx" and pack["source"]["work_structure"]["chapter"] == 1
    brief = pack_json.with_suffix(".md").read_text()
    assert "[H] Lesson 1. Confusion." in brief and "Work: **The Learning Game** by Ana Lorena Fábrega" in brief

    # (4) a proposer's rows -> a proposal set; a bad row refuses loudly
    rows = tmp_path / "rows.jsonl"
    rows.write_text("\n".join(json.dumps(x) for x in [
        {"kind": "claim", "from_i": 0, "to_i": 0, "text": "Gatto quit teaching in 1991.", "lead": "Gatto"},
        {"kind": "quotation", "from_i": 1, "to_i": 2, "text": "I teach confusion.", "attribution": "Gatto"},
        {"kind": "claim", "from_i": 3, "to_i": 3, "text": "Subjects taught in isolation."},
        {"kind": "claim", "from_i": 3, "to_i": 4, "text": "Isolation → no coherent picture.", "lead": "coherent", "parent": 2},
        {"kind": "synopsis", "from_i": 0, "to_i": 4, "text": "Gatto quit in 1991; school teaches confusion by isolating subjects."},
    ]) + "\n")
    r = _run(*base, "notes-ingest", "--pack", str(pack_json), "--rows", str(rows), "--proposer", "test")
    assert r.returncode == 0 and "5 point(s)" in r.stdout, r.stderr or r.stdout
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"kind": "claim", "from_i": 0, "to_i": 1, "text": "x"}) + "\n")
    r = _run(*base, "notes-ingest", "--pack", str(pack_json), "--rows", str(bad), "--proposer", "test")
    assert r.returncode == 1 and "crosses a header" in r.stderr

    # (5) accept: list first, then all — Points + References + edges (ELABORATES for the nested
    #     child), one journaled op each, the deliverable_type fact asserted on first accept
    r = _run(*base, "notes-accept", "--slug", "the-learning-game/ch01")
    assert r.returncode == 0 and "5 pending" in r.stdout and "[quotation]" in r.stdout and "↳" in r.stdout, r.stderr or r.stdout
    r = _run(*base, "notes-accept", "--slug", "the-learning-game/ch01", "--accept-all")
    assert r.returncode == 0 and "accepted 5" in r.stdout and "deliverable_type asserted: `pure-notes`" in r.stdout, r.stderr or r.stdout
    ops = [o for o in read_journal(pj) if o["verb"] == "accept-point"]
    assert len(ops) == 5 and ops[4]["args"]["point"]["kind"] == "synopsis"
    assert all(len(o["args"]["observations"]) == len(o["args"]["point"]["segment_ids"]) for o in ops)
    assert ops[1]["args"]["point"]["kind"] == "quotation" and ops[1]["args"]["observations"][0]["graph"] == "tx"
    assert ops[0]["args"]["point"]["unit"]["source_id"] == "src-1" and ops[0]["args"]["point"]["heading"] == "Chapter 1. Seven dangerous lessons."
    assert ops[3]["args"]["point"]["parent_key"] == ops[2]["args"]["point"]["key"]      # the child rides its parent's key

    async def _elaborates(db):
        async with open_graph(db) as g:
            res = await graph_task(g.queue, g.graph_id, "query_edges",
                                   query=EdgeQuery(relation_type="ELABORATES", project=[]).to_dict())
            return len(res.rows or [])
    assert asyncio.run(_elaborates(pdb)) == 1
    r = _run("--graph-db-path", pdb, "--format", "agent", "list", "--label", "Point")
    points = json.loads(r.stdout)
    assert points.get("total", len(points.get("items", []))) == 5 or len(points.get("nodes", [])) == 5
    # re-accepting is a no-op that journals nothing
    r = _run(*base, "notes-accept", "--slug", "the-learning-game/ch01", "--accept-all")
    assert "0 pending" in r.stdout or "accepted 0" in r.stdout
    assert len([o for o in read_journal(pj) if o["verb"] == "accept-point"]) == 5

    # (6) the review reads: overlap flags the two same-kind claims on line 3; coverage is complete
    r = _run(*base, "notes-overlap", "--slug", "the-learning-game/ch01")
    assert r.returncode == 0 and "1 overlapping pair(s) · 1 same-kind" in r.stdout, r.stderr or r.stdout
    r = _run(*base, "notes-coverage", "--slug", "the-learning-game/ch01")
    assert r.returncode == 0 and "5 of 5 content lines covered" in r.stdout and "0 gap run(s)" in r.stdout, r.stderr or r.stdout
    quote_pid = [o["args"]["point"]["key"] for o in ops if o["args"]["point"]["kind"] == "quotation"][0]
    r = _run("--graph-db-path", pdb, "--format", "agent", "locate", quote_pid[:8])
    hits = [m for m in json.loads(r.stdout)["matches"] if m.get("label") == "Point"]
    assert len(hits) == 1
    point_id = hits[0]["id"]
    r = _run(*base, "notes-check", point_id[:8])
    assert r.returncode == 0 and "Quote. I teach confusion." in r.stdout and "moved" not in r.stdout, r.stderr or r.stdout

    # (7) render: Sections DERIVED from the Points; the type-owned frontmatter lines (title =
    #     the unit title, description derived) re-derived, the rest + the preamble kept; the
    #     header restating the unit's title suppressed; the child nested; no audiobook spans
    r = _run(*base, "notes-render", "--slug", "the-learning-game/ch01")
    assert r.returncode == 0 and "from 5 point(s)" in r.stdout, r.stderr or r.stdout
    staged = (pri_dir / "staging" / "the-learning-game" / "ch01" / "index.md").read_text()
    assert staged.startswith('---\ntitle: "The Learning Game, Ch. 1 notes"\ndescription: "Gatto quit in 1991; school teaches '
                             'confusion by isolating subjects."\ndate: 2026-09-07\n'), staged[:300]
    assert "Pure notes born on the graph." in staged
    assert ("Notes on **The Learning Game** by Ana Lorena Fábrega — Part 1 · Chapter 1, *Seven Dangerous Lessons*. "
            "The points paraphrase the chapter in its own order; only the quotations are verbatim.") in staged
    assert "isolating subjects" not in staged.split("---\n", 2)[2]         # the synopsis is not body content
    assert "At a glance" not in staged and "## Chapter 1" not in staged and "## Lesson 1. Confusion" in staged
    assert staged.index("**Gatto** quit teaching in 1991.") < staged.index("## Lesson 1. Confusion")   # the opening points stand before the first section
    assert f"> I teach confusion.\n> — Gatto [§](#pt-{quote_pid[:8]}){{#pt-{quote_pid[:8]} .pt-anchor}}\n" in staged
    assert "(00:" not in staged
    assert "- Subjects taught in isolation. [§]" in staged and "\n  - Isolation → no **coherent** picture. [§]" in staged
    r = _run("--graph-db-path", pdb, "read", note_id)
    assert r.stdout == staged                                     # the graph reconstruction IS the file
    live_text = r.stdout
    assert len([o for o in read_journal(pj) if o["verb"] == "render-notes"]) == 1
    # a re-render with nothing changed lands no new op (identical args dedup)
    _run(*base, "notes-render", "--slug", "the-learning-game/ch01")
    assert len([o for o in read_journal(pj) if o["verb"] == "render-notes"]) == 1

    # (7b) the per-point repair (ruling 5625b74e): text / parent edited IN PLACE — same node, same
    #      anchor; ingest's arrow + lead contracts hold on the edit; a parent change rewires
    #      ELABORATES; each real change journals one `edit-point`; a no-change edit journals nothing
    child_key = ops[3]["args"]["point"]["key"]
    r = _run("--graph-db-path", pdb, "--format", "agent", "locate", child_key[:8])
    child_id = [m for m in json.loads(r.stdout)["matches"] if m.get("label") == "Point"][0]["id"]
    r = _run(*base, "notes-edit", child_id[:8], "--text", "Isolation → no coherent picture → despair.")
    assert r.returncode == 1 and "at most ONE" in r.stdout + r.stderr
    r = _run(*base, "notes-edit", child_id[:8], "--text", "Isolation = no coherent picture.")
    assert r.returncode == 0 and "text changed (journaled)" in r.stdout, r.stderr or r.stdout
    r = _run(*base, "notes-edit", child_id[:8], "--parent", "none")
    assert r.returncode == 0 and "parent_key changed" in r.stdout, r.stderr or r.stdout
    assert asyncio.run(_elaborates(pdb)) == 0
    r = _run(*base, "notes-edit", child_id[:8], "--parent", ops[2]["args"]["point"]["key"])
    assert r.returncode == 0, r.stderr or r.stdout
    assert asyncio.run(_elaborates(pdb)) == 1
    r = _run(*base, "notes-edit", child_id[:8], "--text", "Isolation = no coherent picture.")
    assert r.returncode == 0 and "nothing changed" in r.stdout
    assert len([o for o in read_journal(pj) if o["verb"] == "edit-point"]) == 3
    r = _run(*base, "notes-render", "--slug", "the-learning-game/ch01")
    assert r.returncode == 0, r.stderr or r.stdout
    staged = (pri_dir / "staging" / "the-learning-game" / "ch01" / "index.md").read_text()
    assert "\n  - Isolation = no **coherent** picture. [§]" in staged and f"#pt-{child_key[:8]}" in staged
    assert "→" not in staged.split("---\n", 2)[2]
    live_text = _run("--graph-db-path", pdb, "read", note_id).stdout
    assert live_text == staged

    # (7c) rehead (ruling b398d73f): a header reclassified upstream vanishes from a FRESH pack;
    #      every point under it re-derives its heading in place (one edit-point each, anchors
    #      kept); the render regroups; the reverse pack restores the sections
    nolesson = tmp_path / "pack_nolesson.json"
    nolesson.write_text(json.dumps({**pack, "headers": [h for h in pack["headers"] if not h["text"].startswith("Lesson 1")]}))
    r = _run(*base, "notes-rehead", "--slug", "the-learning-game/ch01", "--pack", str(nolesson))
    assert r.returncode == 0 and "3 of 5 point(s) re-headed" in r.stdout, r.stderr or r.stdout
    assert len([o for o in read_journal(pj) if o["verb"] == "edit-point"]) == 6
    r = _run(*base, "notes-render", "--slug", "the-learning-game/ch01")
    regrouped = (pri_dir / "staging" / "the-learning-game" / "ch01" / "index.md").read_text()
    assert "## Lesson 1. Confusion" not in regrouped and f"#pt-{child_key[:8]}" in regrouped
    assert "\n  - Isolation = no **coherent** picture. [§]" in regrouped        # the nesting survives the regroup
    r = _run(*base, "notes-rehead", "--slug", "the-learning-game/ch01", "--pack", str(pack_json))
    assert r.returncode == 0 and "3 of 5 point(s) re-headed" in r.stdout, r.stderr or r.stdout
    r = _run(*base, "notes-rehead", "--slug", "the-learning-game/ch01", "--pack", str(pack_json))
    assert "0 of 5 point(s) re-headed" in r.stdout and "nothing to do" in r.stdout
    r = _run(*base, "notes-render", "--slug", "the-learning-game/ch01")
    assert (pri_dir / "staging" / "the-learning-game" / "ch01" / "index.md").read_text() == staged
    assert len([o for o in read_journal(pj) if o["verb"] == "edit-point"]) == 9

    # (8) frontier: approve; a segment edit IN THE SIBLING surfaces through Point -> Reference
    r = _run(*base, "assert", note_id, "publish_state", "published")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run(*base, "review-frontier")
    assert "nothing stale" in r.stdout, r.stderr or r.stdout
    _edit_sibling_segment(sdb, "seg-4", "Quote. I teach confusion, said Gatto.")
    r = _run(*base, "review-frontier")
    assert "stale 1" in r.stdout and "_Reference_" in r.stdout and "changed since" in r.stdout, r.stderr or r.stdout
    r = _run(*base, "notes-check", point_id[:8])
    assert "⚠ moved since observation" in r.stdout

    # (9) retract one point; render drops its content and journals the compensating op
    r = _run(*base, "notes-retract", point_id[:8])
    assert r.returncode == 0 and "retracted" in r.stdout, r.stderr or r.stdout
    r = _run(*base, "notes-render", "--slug", "the-learning-game/ch01")
    assert "from 4 point(s)" in r.stdout
    staged2 = (pri_dir / "staging" / "the-learning-game" / "ch01" / "index.md").read_text()
    assert "I teach confusion." not in staged2 and "## Lesson 1. Confusion" in staged2
    r = _run("--graph-db-path", pdb, "read", note_id)
    assert r.stdout == staged2

    # (10) REPLAY onto a fresh db with NO config and NO sibling: type + points + references +
    #      rendered sections all rebuild from the journal alone, byte-equal to the live note
    rep = tmp_path / "rep"; rep.mkdir()
    rdb = str(rep / "rep.db")
    r = _run("--graph-db-path", rdb, "--journal-path", pj, "replay")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run("--graph-db-path", rdb, "read", note_id)
    assert r.stdout == staged2
    r = _run("--graph-db-path", rdb, "--format", "agent", "list", "--label", "Point")
    body = json.loads(r.stdout)
    n = body.get("total") if isinstance(body.get("total"), int) else len(body.get("nodes") or body.get("items") or [])
    assert n == 4
    r = _run("--graph-db-path", rdb, "--format", "agent", "locate", "pure-notes")
    assert any(m.get("label") == "DeliverableType" for m in json.loads(r.stdout)["matches"])
    assert live_text != staged2   # the retract really changed the body (sanity on the equality checks above)
    assert asyncio.run(_elaborates(rdb)) == 1      # the nesting edge replays from the accept op alone

    # (11) the re-drive's clean slate: retract EVERY point (children first), render drops the
    #      body, and a second replay converges on the empty deliverable
    r = _run(*base, "notes-retract", "--slug", "the-learning-game/ch01", "--all")
    assert r.returncode == 0 and "retract-point ×4" in r.stdout, r.stderr or r.stdout
    assert len([o for o in read_journal(pj) if o["verb"] == "retract-point"]) == 5
    r = _run(*base, "notes-render", "--slug", "the-learning-game/ch01")
    assert "from 0 point(s)" in r.stdout
    staged3 = (pri_dir / "staging" / "the-learning-game" / "ch01" / "index.md").read_text()
    assert "## Lesson 1" not in staged3 and "Pure notes born on the graph." in staged3
    rep2 = tmp_path / "rep2"; rep2.mkdir()
    r = _run("--graph-db-path", str(rep2 / "rep.db"), "--journal-path", pj, "replay")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run("--graph-db-path", str(rep2 / "rep.db"), "read", note_id)
    assert r.stdout == staged3


@pytest.mark.skipif(not _HAVE_GRAPH, reason=f"graph capability {DEFAULT_GRAPH_ID!r} not installed (CI)")
def test_cli_draft_lifecycle_staging_index_and_work_promotion_gate(tmp_path):
    # Item 140981e9 (ruling a7ca900d (1)/(4)): the draft lifecycle end to end — the work-page
    # promotion condition holds a published chapter until EVERY chapter of the work is born;
    # the staging index lists fixture / draft / reviewed / published / retired pages apart;
    # a draft re-stated as a fixture (named --supersede) stops counting as born and never
    # emits; retired is terminal and never emits; the whole chain replays from the journal.
    sib_dir, pri_dir = tmp_path / "sib", tmp_path / "pri"
    sib_dir.mkdir(); pri_dir.mkdir()
    sdb = str(sib_dir / "sib.db")
    _build_sibling(sdb)

    async def second_chapter():   # a SECOND chapter unit of the same work, so the condition has something to wait on
        async with open_graph(sdb) as sg:
            ws = {"kind": "chapter", "part": 1, "chapter": 2, "title": "How Did We Get Here",
                  "work": {"title": "The Learning Game", "author": "Ana Lorena Fábrega"}}
            nodes = [{"id": "src-2", "label": "Source", "sources": [],
                      "properties": {"title": "The Learning Game — 05 - 2. How Did We Get Here", "work_structure": ws}},
                     {"id": "seg-b0", "label": "Segment", "sources": [],
                      "properties": {"text": "Chapter 2. How did we get here.", "index": 0, "start_time": 0.0, "end_time": 2.0, "source_id": "src-2"}},
                     {"id": "seg-b1", "label": "Segment", "sources": [],
                      "properties": {"text": "Prussia built the modern school.", "index": 1, "start_time": 2.0, "end_time": 5.0, "source_id": "src-2"}},
                     {"id": "cor-bh", "label": "Correction", "sources": [],
                      "properties": {"correction_type": "stratum", "status": "applied", "actor": "human", "session_id": "s",
                                     "created_at": 1.0, "payload": {"operation": "classify", "source_id": "src-2",
                                                                    "category": "section-header", "segment_ids": ["seg-b0"], "start_time": 0.0}}}]
            await extend_graph(sg.queue, sg.graph_id, nodes, [])
    asyncio.run(second_chapter())
    pdb, pj = str(pri_dir / "pri.db"), str(pri_dir / "writes.jsonl")
    staging, site = pri_dir / "staging", pri_dir / "site"
    (pri_dir / "graph.config.json").write_text(json.dumps(
        {"notes_profile": "quarto_post", "emit_root": str(staging / "posts"), "website_root": str(site),
         "sibling_graphs": {"tx": sdb}}))
    base = ("--graph-db-path", pdb, "--journal-path", pj, "--source-journal-path", str(pri_dir / "source.jsonl"))
    r = _run(*base, "notes-type", "pure-notes")
    assert r.returncode == 0, r.stderr or r.stdout

    def born(slug, source, text):   # a typed chapter page: born draft, one accepted point, rendered
        post = f"---\ntitle: \"{slug}\"\ndate: 2026-09-09\ncategories: [book, notes]\n---\n\nPreamble.\n"
        r = _run(*base, "new-note", "--slug", slug, "--content", post)
        assert r.returncode == 0, r.stderr or r.stdout
        note_id = [o for o in read_journal(pj) if o["verb"] == "assert"][-1]["args"]["subject"]
        r = _run(*base, "notes-pack", "--source", source)
        assert r.returncode == 0, r.stderr or r.stdout
        pack_json = next(l.split(None, 1)[1].strip() for l in r.stdout.splitlines() if l.strip().startswith("json"))
        rows = tmp_path / (slug.replace("/", "_") + ".jsonl")
        rows.write_text(json.dumps({"kind": "claim", "from_i": 0, "to_i": 0, "text": text}) + "\n")
        r = _run(*base, "notes-ingest", "--pack", pack_json, "--rows", str(rows), "--proposer", "test")
        assert r.returncode == 0, r.stderr or r.stdout
        r = _run(*base, "notes-accept", "--slug", slug, "--accept-all")
        assert r.returncode == 0 and "accepted 1" in r.stdout, r.stderr or r.stdout
        r = _run(*base, "notes-render", "--slug", slug)
        assert r.returncode == 0, r.stderr or r.stdout
        return note_id

    ch1 = born("the-learning-game/ch01", "Seven Dangerous", "Gatto quit teaching in 1991.")
    # (c) one of two chapters born -> the work is HELD; a published chapter still does not emit
    r = _run(*base, "notes-promotion")
    assert r.returncode == 0 and "1 of 2" in r.stdout and "HELD" in r.stdout and "missing ch. 2" in r.stdout, r.stderr or r.stdout
    r = _run(*base, "assert", ch1, "publish_state", "published")
    assert r.returncode == 0, r.stderr or r.stdout
    landed = site / "posts" / "the-learning-game" / "ch01" / "index.md"
    r = _run(*base, "emit-post", ch1)
    assert r.returncode != 0 and "promotion condition" in (r.stdout + r.stderr) and not landed.exists()
    ch2 = born("the-learning-game/ch02", "How Did We", "Prussia built the modern school.")
    r = _run(*base, "notes-promotion", "--work", "The Learning Game")
    assert "2 of 2" in r.stdout and "READY" in r.stdout, r.stderr or r.stdout
    r = _run(*base, "emit-post", ch1)
    assert r.returncode == 0 and landed.exists(), r.stderr or r.stdout
    # (b) the staging index: one listing file per state + the works table, from the facts alone
    r = _run(*base, "notes-staging-index")
    assert r.returncode == 0 and "report only" in r.stdout and not (staging / "lists").exists(), r.stderr or r.stdout
    r = _run(*base, "notes-staging-index", "--write")
    assert r.returncode == 0 and "written" in r.stdout, r.stderr or r.stdout
    lists = staging / "lists"
    load = lambda name: yaml.safe_load((lists / name).read_text())
    pub, dr = load("published.yml"), load("draft.yml")
    assert [i["path"] for i in pub] == ["../posts/the-learning-game/ch01/index.md"]   # relative to the listing file (Quarto)
    assert pub[0]["note_id"] == ch1 and pub[0]["publish_state"] == "published" and pub[0]["date"] == "2026-09-09"
    assert pub[0]["categories"] == ["book", "notes"] and pub[0]["title"]
    assert [i["note_id"] for i in dr] == [ch2]
    assert load("fixture.yml") == [] and load("retired.yml") == [] and load("reviewed.yml") == [] and load("multi-active.yml") == []
    works_md = (lists / "_works.md").read_text()   # an underscore file: includable by Quarto, never a page of its own
    assert "| The Learning Game | 2 of 2 | READY" in works_md
    # (d) a draft re-stated as a FIXTURE with the draft named: it lists as a fixture, never emits,
    #     and no longer counts as born — the work is HELD again and ch01 cannot re-emit
    r = _run(*base, "assert", ch2, "publish_state", "fixture", "--supersede", "draft")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run(*base, "notes-staging-index", "--write")
    assert r.returncode == 0, r.stderr or r.stdout
    assert [i["note_id"] for i in load("fixture.yml")] == [ch2] and load("draft.yml") == [] and load("multi-active.yml") == []
    assert "| The Learning Game | 1 of 2 | HELD | ch. 2 |" in (lists / "_works.md").read_text()
    r = _run(*base, "emit-post", ch2)
    assert r.returncode != 0 and "FIXTURE" in (r.stdout + r.stderr)
    landed.unlink()
    r = _run(*base, "emit-post", ch1)
    assert r.returncode != 0 and "promotion condition" in (r.stdout + r.stderr) and not landed.exists()
    # (a) retired is TERMINAL: it supersedes published outright and the emit refuses by name
    r = _run(*base, "assert", ch1, "publish_state", "retired")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run(*base, "emit-post", ch1)
    assert r.returncode != 0 and "RETIRED" in (r.stdout + r.stderr)
    r = _run(*base, "notes-staging-index", "--write")
    assert [i["note_id"] for i in load("retired.yml")] == [ch1] and load("published.yml") == []
    # (e) the lifecycle chain is journaled: a fresh db from the journal alone lists the same states
    rep = str(tmp_path / "rep.db")
    r = _run("--graph-db-path", rep, "--journal-path", pj, "replay")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run("--graph-db-path", rep, "notes-staging-index", "--project-root", str(tmp_path / "rep-staging"), "--write")
    assert r.returncode == 0, r.stderr or r.stdout
    rl = lambda name: yaml.safe_load((tmp_path / "rep-staging" / "lists" / name).read_text())
    assert [i["note_id"] for i in rl("retired.yml")] == [ch1] and [i["note_id"] for i in rl("fixture.yml")] == [ch2]


@pytest.mark.skipif(not _HAVE_GRAPH, reason=f"graph capability {DEFAULT_GRAPH_ID!r} not installed (CI)")
def test_cli_references_render_into_the_source_card_and_replay(tmp_path):
    # Item ae103970 (ruling a7ca900d (3)): Reference nodes on the sibling's Source render as the
    # source card's Resources line (never a body edit); a cross-work link to a not-yet-born
    # note falls back to its URL and RESOLVES to the born page on the next render (finding
    # 962866ae, second datapoint); the render op journals what it observed, so a replay with
    # no sibling reproduces the live text byte for byte.
    sib_dir, pri_dir = tmp_path / "sib", tmp_path / "pri"
    sib_dir.mkdir(); pri_dir.mkdir()
    sdb = str(sib_dir / "sib.db")
    _build_sibling(sdb)

    async def add_refs():   # what the transcription core's `add-reference` lands
        async with open_graph(sdb) as sg:
            nodes = [{"id": "ref-1", "label": "Reference", "sources": [],
                      "properties": {"source_id": "src-1", "label": "Dumbing Us Down (publisher page)",
                                     "url": "https://newsociety.com/dud", "notes_slug": "", "role": "cited-work", "added_by": "human:test"}},
                     {"id": "ref-2", "label": "Reference", "sources": [],
                      "properties": {"source_id": "src-1", "label": "Notes on Dumbing Us Down",
                                     "url": "https://example.org/hand-notes", "notes_slug": "dumbing-us-down/ch01-notes",
                                     "role": "related-notes", "added_by": "human:test"}}]
            edges = [{"id": f"e-{r}", "source_id": "src-1", "target_id": r, "relation_type": "HAS_REFERENCE", "properties": {}}
                     for r in ("ref-1", "ref-2")]
            await extend_graph(sg.queue, sg.graph_id, nodes, edges)
    asyncio.run(add_refs())
    pdb, pj = str(pri_dir / "pri.db"), str(pri_dir / "writes.jsonl")
    (pri_dir / "graph.config.json").write_text(json.dumps(
        {"notes_profile": "quarto_post", "emit_root": str(pri_dir / "staging"), "sibling_graphs": {"tx": sdb}}))
    base = ("--graph-db-path", pdb, "--journal-path", pj, "--source-journal-path", str(pri_dir / "source.jsonl"))
    assert _run(*base, "notes-type", "pure-notes").returncode == 0
    post = "---\ntitle: \"ch01\"\ndate: 2026-09-09\ncategories: [book, notes]\n---\n\nPreamble.\n"
    r = _run(*base, "new-note", "--slug", "the-learning-game/ch01", "--content", post)
    assert r.returncode == 0, r.stderr or r.stdout
    note_id = [o for o in read_journal(pj) if o["verb"] == "assert"][-1]["args"]["subject"]
    r = _run(*base, "notes-pack", "--source", "Seven Dangerous")
    assert r.returncode == 0, r.stderr or r.stdout
    pack_json = next(l.split(None, 1)[1].strip() for l in r.stdout.splitlines() if l.strip().startswith("json"))
    pack = json.loads(Path(pack_json).read_text())
    assert [x["role"] for x in pack["source"]["references"]] == ["cited-work", "related-notes"]   # the unit snapshot carries them
    rows = tmp_path / "rows.jsonl"
    rows.write_text(json.dumps({"kind": "claim", "from_i": 0, "to_i": 0, "text": "Gatto quit teaching in 1991."}) + "\n")
    assert _run(*base, "notes-ingest", "--pack", pack_json, "--rows", str(rows), "--proposer", "test").returncode == 0
    r = _run(*base, "notes-accept", "--slug", "the-learning-game/ch01", "--accept-all")
    assert r.returncode == 0 and "accepted 1" in r.stdout, r.stderr or r.stdout
    # (1) render: the Resources line inside the source card; the cross-work target is unborn -> fallback URL
    r = _run(*base, "notes-render", "--slug", "the-learning-game/ch01")
    assert r.returncode == 0, r.stderr or r.stdout
    staged_path = pri_dir / "staging" / "the-learning-game" / "ch01" / "index.md"
    staged = staged_path.read_text()
    assert ("Resources: [Dumbing Us Down (publisher page)](https://newsociety.com/dud) · "
            "[Notes on Dumbing Us Down](https://example.org/hand-notes)\n:::") in staged
    ops = [o for o in read_journal(pj) if o["verb"] == "render-notes"]
    assert len(ops) == 1 and [x["notes_slug"] for x in ops[0]["args"]["references"]] == ["", "dumbing-us-down/ch01-notes"]
    # (2) the target is born -> the next render resolves the link to the born page (a NEW op: the
    #     substance digest covers the resolution, so the re-render is never deduped away)
    r = _run(*base, "new-note", "--slug", "dumbing-us-down/ch01-notes",
             "--content", "---\ntitle: \"DUD ch01\"\ndate: 2026-09-09\n---\n\nBorn.\n")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run(*base, "notes-render", "--slug", "the-learning-game/ch01")
    assert r.returncode == 0, r.stderr or r.stdout
    staged2 = staged_path.read_text()
    assert "[Notes on Dumbing Us Down](/posts/dumbing-us-down/ch01-notes/)" in staged2 and "hand-notes" not in staged2
    assert len([o for o in read_journal(pj) if o["verb"] == "render-notes"]) == 2
    live = _run("--graph-db-path", pdb, "read", note_id).stdout
    assert live == staged2
    # (3) replay onto a fresh db with NO sibling: the journaled references reproduce the live text
    rep = str(tmp_path / "rep.db")
    r = _run("--graph-db-path", rep, "--journal-path", pj, "replay")
    assert r.returncode == 0, r.stderr or r.stdout
    assert _run("--graph-db-path", rep, "read", note_id).stdout == staged2
