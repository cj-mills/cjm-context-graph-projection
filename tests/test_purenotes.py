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

from cjm_context_graph_layer.grammar import make_edge
from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.journal import read_journal
from cjm_context_graph_primitives.query import EdgeQuery
from cjm_context_graph_projection.purenotes import (apply_judgements, apply_outline, extra_list, merge_point_blocks,
                                                    plan_notes_blocks, render_judge_brief, render_outline_brief,
                                                    render_pairs_brief, unjudged_pairs,
                                                    close_open_refs, load_notes_propsets, merge_point_proposals,
                                                    open_reference_list, pick_propset, points_index,
                                                    render_reconcile_brief, with_points_index, write_notes_propset,
                                                    _time_link, build_notes_pack, build_point_tree,
                                                    choose_spine, coverage_gaps, derive_frontmatter,
                                                    derived_description, overlapping_points, pack_digest, plan_notes_windows,
                                                    proposals_from_point_rows, pure_notes_type, speaker_labels,
                                                    render_notes_pack, render_points, stratum_role_policy,
                                                    render_source_card, synopsis_of, unit_label, lecture_title, date_phrase,
                                                    unit_title_header, validate_point_rows,
                                                    _frontmatter_fields, render_works_table,
                                                    _replace_frontmatter_lines, derive_work_frontmatter,
                                                    render_work_card, render_work_chapters, work_page_type)
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


def test_stratum_role_policy_reads_the_role_map_and_the_legacy_lists():
    legacy = {"include_strata": ["quotation"], "structure_strata": ["section-header"],
              "exclude_strata": ["tangent"], "never_carry": ["research-mark"]}
    roles, default = stratum_role_policy(legacy)
    assert roles == {"quotation": "quote", "section-header": "header", "tangent": "exclude",
                     "research-mark": "content"}
    assert default == "content"                        # a pre-ruling policy keeps its unnamed classes plain
    roles, default = stratum_role_policy({"stratum_roles": {"qa": "span"}, "never_carry": ["asr-error"]})
    assert roles == {"qa": "span", "asr-error": "content"} and default == "annotate"
    assert stratum_role_policy({"stratum_roles": {}, "default_role": "content"})[1] == "content"
    with pytest.raises(ValueError):
        stratum_role_policy({"stratum_roles": {"qa": "structure"}})           # not a role
    with pytest.raises(ValueError):
        stratum_role_policy({"stratum_roles": {}, "default_role": "exclude"})  # a default that can hide content


def test_pack_is_identical_under_the_legacy_lists_and_the_role_map():
    # ruling e1e096fa (6): the book profile reads the same content under either vocabulary
    legacy = {"key": "pure-notes", "information_policy": {
        "include_strata": ["quotation"], "structure_strata": ["section-header"],
        "exclude_strata": ["tangent", "sponsor", "filler", "apparatus", "cross-reference", "transition"],
        "never_carry": ["research-mark", "tool-mention", "asr-error"]}}
    old = build_notes_pack(_unit(), legacy)
    new = build_notes_pack(_unit(), pure_notes_type().to_graph_node()["properties"])
    assert old["digest"] == new["digest"]
    assert old["spans"] == new["spans"] == [] and not any("notes" in r or "speaker" in r for r in new["segments"])
    strip = lambda md: "\n".join(ln for ln in md.splitlines() if not ln.startswith("# Notes pack"))
    assert strip(render_notes_pack(old)) == strip(render_notes_pack(new))
    assert "## Spans" not in render_notes_pack(new) and "## The margin" not in render_notes_pack(new)


def test_pack_roles_a_span_keeps_its_lines_and_the_margin_reaches_the_drafter():
    # finding 6735f8f1: qa under the header role REMOVED every question and answer from the read
    segs = [{"id": f"s{k}", "index": k, "text": t, "start": float(k), "end": float(k + 1)} for k, t in enumerate([
        "Can everyone hear me?", "As the slide shows, the kernel launches twice.", "Quick aside on my keyboard.",
        "Chris asks: why two launches?", "Because the first one warms the cache.", "One more thing […] on streams."])]
    def stratum(cid, cat, ids):
        return {"id": cid, "correction_type": "stratum", "payload": {"category": cat, "segment_ids": ids}}
    unit = {"source": {"source_id": "lec", "title": "Lecture"}, "segments": segs,
            "strata": [stratum("c-log", "logistics", ["s0"]), stratum("c-vis", "visual-ref", ["s1"]),
                       stratum("c-new", "never-heard-of", ["s2"]), stratum("c-qa", "qa", ["s3", "s4"])],
            "speakers": {"s1": "Alice", "s2": "Alice", "s3": "Chris", "s4": "Alice", "s5": None},
            "read": {"layer": "clean", "marker": "[…]", "spans_cut": 1, "lines_emptied": 0}}
    tprops = {"key": "lecture-notes", "information_policy": {
        "stratum_roles": {"qa": "span", "visual-ref": "annotate", "logistics": "exclude"},
        "stratum_glosses": {"qa": "one question with its answer", "visual-ref": "depends on what is shown"}}}
    pack = build_notes_pack(unit, tprops)
    assert [r["id"] for r in pack["segments"]] == ["s1", "s2", "s3", "s4", "s5"]          # only logistics left the read
    assert pack["spans"] == [{"class": "qa", "stratum_id": "c-qa", "from_i": 2, "to_i": 3}]   # the qa lines ARE content
    assert pack["segments"][0]["notes"] == ["visual-ref"]
    assert pack["segments"][1]["notes"] == ["never-heard-of"]      # an unnamed class reaches the drafter (6752db0a (9))
    assert [r["speaker"] for r in pack["segments"]] == ["Alice", "Alice", "Chris", "Alice", None]
    assert pack["stratum_glosses"] == {"qa": "one question with its answer", "visual-ref": "depends on what is shown"}
    md = render_notes_pack(pack)
    assert "- `qa` lines 2–3" in md and "one question with its answer" in md
    assert "{visual-ref} As the slide shows" in md and "`{never-heard-of}`" in md
    assert md.count("— Alice —") == 2 and "— Chris —" in md and "— ? —" in md and "CLEAN read" in md
    bare = build_notes_pack({**unit, "speakers": None, "strata": unit["strata"][:1]}, tprops)
    assert bare["digest"] != pack["digest"]                        # the margin + spans are part of what was read
    with pytest.raises(ValueError):
        build_notes_pack(unit, {"information_policy": {"stratum_roles": {"qa": "structure"}}})


def test_window_plan_cuts_at_mechanical_seams_never_inside_a_span_and_tiles_the_unit():
    # work item 3a2c94eb (1): seams are mechanical (turn, else silence), a qa block is drafted whole
    def unit(spans, turn_at, gaps=None):
        gaps = gaps or {}
        segs, t = [], 0.0
        for k in range(20):
            t += gaps.get(k, 0.5)
            segs.append({"id": f"s{k}", "index": k, "text": f"line {k}", "start": t, "end": t + 1.0})
            t += 1.0
        return {"source": {"source_id": "lec", "title": "Lecture"}, "segments": segs,
                "strata": [{"id": f"c-qa{n}", "correction_type": "stratum",
                            "payload": {"category": "qa", "segment_ids": [f"s{k}" for k in range(a, b + 1)]}}
                           for n, (a, b) in enumerate(spans)],
                "speakers": {f"s{k}": ("Alice" if k < turn_at else "Bob") for k in range(20)}}
    tprops = {"key": "lecture-notes", "information_policy": {"stratum_roles": {"qa": "span"}}}

    def plan(u, n, **kw):
        whole = build_notes_pack(u, tprops)
        windows = plan_notes_windows(whole, n, **kw)
        ids = [r["id"] for r in whole["segments"]]
        got = [[r["id"] for r in build_notes_pack(u, tprops, window=(w["start"], w["end"]))["segments"]] for w in windows]
        assert [i for g in got for i in g] == ids                                  # the windows TILE the unit
        assert got == [ids[w["from_i"]:w["to_i"] + 1] for w in windows]            # and hold the lines the plan names
        return windows
    # a turn inside the radius wins over a longer silence next to it
    w = plan(unit([], turn_at=11, gaps={9: 5.0}), 2)
    assert [(x["from_i"], x["to_i"], x["seam"]) for x in w] == [(0, 10, "start"), (11, 19, "turn")]
    # the turn sits INSIDE a qa span (lines 8–12): the only legal seam in range opens the span
    w = plan(unit([(8, 12)], turn_at=9), 2)
    assert [(x["from_i"], x["seam"]) for x in w] == [(0, "start"), (8, "silence")]
    # a span covering the whole radius: the nearest legal seam outside it, never a cut inside
    w = plan(unit([(7, 13)], turn_at=20), 2)
    assert w[1]["from_i"] == 7
    assert [x["from_i"] for x in plan(unit([], turn_at=20), 4)] == [0, 5, 10, 15] and w[-1]["end"] is None
    with pytest.raises(ValueError):
        plan_notes_windows(build_notes_pack(unit([(0, 19)], turn_at=20), tprops), 2)   # one span IS the unit
    with pytest.raises(ValueError):
        plan_notes_windows(build_notes_pack(unit([], turn_at=20), tprops), 21)


def test_window_pack_margin_is_read_only_context_the_policy_still_filters():
    # design 6752db0a (5): a window drafter reads its neighbours and cannot draft over them
    segs = [{"id": f"s{k}", "index": k, "text": f"line {k}", "start": float(2 * k), "end": float(2 * k + 1)} for k in range(10)]
    unit = {"source": {"source_id": "lec", "title": "Lecture"}, "segments": segs,
            "strata": [{"id": "c-log", "correction_type": "stratum", "payload": {"category": "logistics", "segment_ids": ["s2"]}}],
            "speakers": {f"s{k}": ("Alice" if k < 4 else "Bob") for k in range(10)}}
    tprops = {"key": "lecture-notes", "information_policy": {"stratum_roles": {"logistics": "exclude"}}}
    whole = build_notes_pack(unit, tprops)
    assert "context" not in whole and "## This window" not in render_notes_pack(whole)
    assert "context" not in build_notes_pack(unit, tprops, margin=3)               # no window, nothing next door
    bare = build_notes_pack(unit, tprops, window=(7.5, 13.5))
    pack = build_notes_pack(unit, tprops, window=(7.5, 13.5), margin=2)
    assert [r["id"] for r in pack["segments"]] == ["s4", "s5", "s6"] == [r["id"] for r in bare["segments"]]
    assert [r["i"] for r in pack["segments"]] == [0, 1, 2]                          # only the window's own lines are numbered
    assert [c["id"] for c in pack["context"]["before"]] == ["s1", "s3"]             # the excluded s2 stays out of the margin
    assert [c["id"] for c in pack["context"]["after"]] == ["s7", "s8"]
    assert pack["context"]["before"][0]["speaker"] == "Alice" and "i" not in pack["context"]["before"][0]
    assert pack["digest"] != bare["digest"]                                         # the context was read
    md = render_notes_pack(pack)
    assert md.index("## Output contract") < md.index("## This window") < md.index("## Transcript")
    assert "NO `synopsis` row and NO `section` row" in md and "(00:07 to 00:13)" in md
    assert md.index("### Context before (read-only)") < md.index("[·] 00:02–00:03  line 1") < md.index("### This window's lines")
    assert md.index("[0] 00:08–00:09  line 4") < md.index("### Context after (read-only)") < md.index("[·] 00:14–00:15  line 7")
    assert "[·]" not in render_notes_pack(bare) and "## This window" in render_notes_pack(bare)
    with pytest.raises(ValueError):
        validate_point_rows([{"kind": "claim", "from_i": 0, "to_i": 3, "text": "reaches into the margin"}], pack)


def test_merge_collapses_across_cells_rotates_the_wording_and_closes_open_references(tmp_path):
    # work item 3a2c94eb (2)-(4): window sets of three arms -> ONE walkable set; links cross the cut
    segs = [{"id": f"s{k}", "index": k, "text": f"line {k}", "start": float(2 * k), "end": float(2 * k + 1)} for k in range(12)]
    unit = {"source": {"source_id": "lec", "title": "Lecture"}, "segments": segs, "strata": [],
            "speakers": {f"s{k}": "Alice" for k in range(12)}}
    tprops = {"key": "lecture-notes", "information_policy": {"stratum_roles": {}}}
    whole = build_notes_pack(unit, tprops)
    w0, w1 = (build_notes_pack(unit, tprops, window=w, margin=2) for w in ((0.0, 11.5), (11.5, None)))
    for k, w in enumerate((w0, w1)):
        w["plan"] = {"whole_pack_id": whole["pack_id"], "k": k, "of": 2}

    def ingest(pack, rows, arm, model):
        res = write_notes_propset(pack, proposals_from_point_rows(validate_point_rows(rows, pack), pack),
                                  out_root=tmp_path, proposer={"kind": "t", "name": f"{arm}-{model}", "model": model}, arm=arm)
        return res["set_id"]

    def claim(a, b, text, **kw):
        return {"kind": "claim", "from_i": a, "to_i": b, "text": text, **kw}
    ingest(w0, [claim(0, 1, "A"), claim(3, 4, "B")], "blind", "opus")
    ingest(w1, [claim(0, 2, "C", open_refs=[{"role": "refers_to", "hint": "the A thing"}]),
                claim(3, 3, "E", open_refs=[{"hint": "the B thing"}]),
                {"kind": "example", "from_i": 5, "to_i": 5, "text": "F", "open_refs": [{"role": "parent", "hint": "C stuff"}]}],
           "blind", "opus")
    seq0 = ingest(w0, [claim(0, 1, "A2"), {"kind": "example", "from_i": 3, "to_i": 4, "text": "B-ex"}], "sequential", "opus")
    sets = load_notes_propsets(tmp_path)
    w1i = with_points_index(w1, pick_propset(sets, seq0)["proposals"])
    assert [e["key"] for e in w1i["index"]] == ["p001", "p002"] and w1i["digest"] != w1["digest"]
    md = render_notes_pack(w1i)
    assert "### Points so far" in md and "- `p002` [example] 00:06 (Alice)  B-ex" in md and '"point": "p017"' in md
    assert "### Points so far" not in render_notes_pack(w1) and '"hint": "<what the earlier point said' in render_notes_pack(w1)
    ingest(w1i, [claim(0, 2, "C2", open_refs=[{"role": "refers_to", "point": "p001"}]),
                 claim(4, 5, "D", open_refs=[{"role": "parent", "point": "p002"}])], "sequential", "opus")
    ingest(whole, [claim(0, 1, "A3"), claim(6, 8, "C3", refers_to=[0]),
                   {"kind": "section", "from_i": 0, "to_i": 0, "text": "Opening"},
                   {"kind": "synopsis", "from_i": 0, "to_i": 11, "text": "What it argues."}], "undivided", "fable")
    with pytest.raises(ValueError):   # a key the index does not list; a hint AND a point; two parents
        validate_point_rows([claim(0, 0, "x", open_refs=[{"point": "p009"}])], w1i)
    with pytest.raises(ValueError):
        validate_point_rows([claim(0, 0, "x", open_refs=[{"point": "p001", "hint": "both"}])], w1i)
    with pytest.raises(ValueError):
        validate_point_rows([claim(0, 0, "p"), claim(1, 1, "x", parent=0, open_refs=[{"role": "parent", "hint": "h"}])], w1)

    sets = load_notes_propsets(tmp_path)
    res = merge_point_proposals(sets, whole)
    rows, stats = {r["text"]: r for r in res["proposals"]}, res["stats"]
    assert sorted(rows) == ["A", "B", "B-ex", "C2", "D", "E", "F"]          # 12 drafted rows -> 7 points to walk
    assert stats["structure_dropped"] == 2 and stats["by_agreement"] == {"1": 5, "3": 2}
    assert stats["cells"] == {"blind/opus": 5, "sequential/opus": 4, "undivided/fable": 2}
    assert (rows["A"]["shown"], rows["C2"]["shown"]) == ("blind/opus", "sequential/opus")   # the shown wording ROTATES
    assert sorted(o["text"] for o in rows["C2"]["origins"]) == ["C", "C2", "C3"]
    assert (rows["C2"]["from_i"], rows["C2"]["to_i"], rows["C2"]["segment_ids"]) == (6, 8, ["s6", "s7", "s8"])   # whole-pack lines
    assert rows["C2"]["refers_to"] == [rows["A"]["proposal_id"]] and "open_refs" not in rows["C2"]   # keyed + numbered links agree; the hint is moot
    assert rows["D"]["parent_key"] == rows["B-ex"]["proposal_id"]             # a keyed parent crosses the cut
    assert rows["E"]["open_refs"] == [{"role": "refers_to", "hint": "the B thing"}]
    order = [r["text"] for r in res["proposals"]]
    assert order.index("D") == order.index("B-ex") + 1                        # a child directly after its parent
    loose = merge_point_proposals(sets, whole, same_kind=False)["stats"]
    assert loose["merged"] == 5 and loose["by_agreement"] == {"1": 1, "2": 2, "3": 2}   # B + B-ex and D + F agree on the lines

    refs = open_reference_list(res["proposals"])
    assert [(r["ref"], r["role"], r["hint"]) for r in refs] == [("r01", "refers_to", "the B thing"), ("r02", "parent", "C stuff")]
    key = {e["text"]: e["key"] for e in points_index(res["proposals"])}
    brief = render_reconcile_brief(res["proposals"], set_id="x")
    assert f"- `r02` from `{key['F']}` (parent): C stuff" in brief and f"`{key['C2']}` [claim]" in brief
    done = close_open_refs(res["proposals"], [{"ref": "r01", "target": key["B"]}, {"ref": "r02", "target": key["C2"]}])
    closed = {r["text"]: r for r in done["proposals"]}
    assert done["stats"] == {"references": 2, "closed": 2, "answered_open": 0, "unanswered": 0}
    assert closed["E"]["refers_to"] == [rows["B"]["proposal_id"]] and closed["F"]["parent_key"] == rows["C2"]["proposal_id"]
    assert "open_refs" not in closed["E"] and [r["proposal_id"] for r in done["proposals"]] == [r["proposal_id"] for r in res["proposals"]]
    kept = close_open_refs(res["proposals"], [{"ref": "r01", "target": None}])
    assert kept["stats"]["answered_open"] == 1 and kept["stats"]["unanswered"] == 1
    assert {r["text"]: r for r in kept["proposals"]}["E"]["open_refs"]            # never guessed: it stays open
    for bad in ([{"ref": "r09", "target": key["A"]}], [{"ref": "r01", "target": key["F"]}],
                [{"ref": "r01", "target": key["A"]}, {"ref": "r01", "target": key["A"]}]):
        with pytest.raises(ValueError):
            close_open_refs(res["proposals"], bad)


def test_block_merge_shows_one_cell_whole_per_block_and_the_judge_folds_with_provenance(tmp_path):
    # ruling 1798a796 / work item 1561551e: blocks between common seams, ONE cell shown whole per block, extras
    # judged, a fold that keeps origins, standing detection over what remains (a set or an accepted draft)
    segs = [{"id": f"s{k}", "index": k, "text": f"line {k}", "start": float(2 * k), "end": float(2 * k + 1)} for k in range(20)]
    unit = {"source": {"source_id": "lec", "title": "Lecture"}, "segments": segs, "strata": [],
            "speakers": {f"s{k}": "Alice" for k in range(20)}}
    tprops = {"key": "lecture-notes", "information_policy": {"stratum_roles": {}}}
    whole = build_notes_pack(unit, tprops)

    def ingest(rows, arm, model):
        return write_notes_propset(whole, proposals_from_point_rows(validate_point_rows(rows, whole), whole),
                                   out_root=tmp_path, proposer={"kind": "t", "name": f"{arm}-{model}", "model": model}, arm=arm)["set_id"]

    def claim(a, b, text, **kw):
        return {"kind": "claim", "from_i": a, "to_i": b, "text": text, **kw}
    ingest([claim(0, 3, "A"), {"kind": "example", "from_i": 2, "to_i": 5, "text": "a1", "parent": 0}, claim(8, 9, "B"), claim(12, 15, "C")], "blind", "opus")
    ingest([claim(0, 3, "A'"), claim(8, 9, "B'"), claim(10, 11, "B2"), claim(12, 13, "C'"), claim(14, 15, "C2")], "blind", "fable")
    ingest([claim(0, 5, "A''"), claim(17, 19, "D")], "undivided", "opus")
    sets = load_notes_propsets(tmp_path)
    # (1) blocks: common seams are the boundaries no cell's SUBTREE spans (a1's run reaches past its parent's)
    plan = plan_notes_blocks({"o": [(0, 5), (8, 9), (12, 15)], "f": [(0, 3), (8, 9), (10, 11), (12, 13), (14, 15)], "u": [(0, 5), (17, 19)]}, 20)
    assert [(b["from_i"], b["to_i"]) for b in plan] == [(0, 5), (6, 6), (7, 7), (8, 9), (10, 11), (12, 15), (16, 16), (17, 19)]
    assert all(b["seam"] == "common" for b in plan)
    # a long block splits at the seam the FEWEST cells span (ties nearest the middle), both halves re-checked
    split = plan_notes_blocks({"o": [(0, 9)], "f": [(0, 4), (6, 9)], "u": [(0, 9)]}, 10, max_lines=6)
    assert [(b["from_i"], b["to_i"], b["seam"]) for b in split] == [(0, 4, "common"), (5, 9, "split:2")]
    assert split[1]["spanned"] == ["o", "u"] and plan_notes_blocks({}, 0) == []
    # (2) the block merge: one cell shown WHOLE per block, rotating by rows shown; the others match or become extras
    res = merge_point_blocks(sets, whole)
    rows = {r["text"]: r for r in res["proposals"]}
    st = res["stats"]
    assert st["blocks"] == 8 and st["merged"] == 5 and st["extras"] == 2 and st["inputs"] == 11
    assert [b.get("shown") for b in res["blocks"]] == ["blind/fable", None, None, "blind/opus", "blind/fable", "blind/opus", None, "undivided/opus"]
    assert sorted(t for t, r in rows.items() if not r.get("extra")) == ["A'", "B", "B2", "C", "D"]
    assert sorted(t for t, r in rows.items() if r.get("extra")) == ["C2", "a1"]
    assert sorted(o["text"] for o in rows["A'"]["origins"]) == ["A", "A'", "A''"]        # a compound over 0-5 still agrees at IoU 0.67
    assert [o["how"] for o in rows["A'"]["origins"]] == ["shown", "matched", "matched"]
    assert [o["text"] for o in rows["C"]["origins"]] == ["C", "C'"]                        # one row per cell per shown row: C2 is the extra
    assert rows["a1"]["parent_key"] == rows["A'"]["proposal_id"] and rows["a1"]["block"] == 0 and rows["C2"]["block"] == 5
    assert st["accounting"] == {"blind/opus": {"rows": 4, "shown": 2, "matched": 1, "extra": 1},
                                "blind/fable": {"rows": 5, "shown": 2, "matched": 2, "extra": 1},
                                "undivided/opus": {"rows": 2, "shown": 1, "matched": 1, "extra": 0}}
    assert [e["text"] for e in points_index(res["proposals"])] == ["A'", "B", "B2", "C", "D"]   # extras are not points yet
    xs = extra_list(res["proposals"])
    assert [(x["key"], x["text"], x["parent"]) for x in xs] == [("x001", "a1", "p001"), ("x002", "C2", "")]
    with pytest.raises(ValueError):
        apply_outline(res["proposals"], [{"section": "S", "first": "p001"}, {"synopsis": "x"}], whole)   # judge first
    # (3) the judge brief: blocks with extras only, the shown rows beside them
    brief = render_judge_brief(res["proposals"], res["blocks"], set_id="x", source="Lecture")
    assert "## Blocks (2 of 8 carry extras)" in brief and "### Block 0 — lines 0–5 · 00:00–00:11 · shown: blind/fable" in brief
    assert "- `x001` [example] 00:04 (Alice) · blind/opus · under p001  a1" in brief and "- `p004` [claim] 00:24 (Alice)  C" in brief
    assert "### Block 3" not in brief
    # (4) the fold keeps provenance; a kept extra is a point with its pairing recorded on BOTH rows; ids survive
    keys = {e["text"]: e["key"] for e in points_index(res["proposals"])}
    done = apply_judgements(res["proposals"], [{"extra": "x001", "verdict": "related", "of": [keys["A'"]]},
                                               {"extra": "x002", "verdict": "contains", "of": [keys["C"]]}])
    out = {r["text"]: r for r in done["proposals"]}
    assert (done["stats"]["points"], done["stats"]["folded"], done["stats"]["added"], done["stats"]["pending_extras"]) == (6, 1, 1, 0)
    assert [(o["text"], o["how"]) for o in out["C"]["origins"]] == [("C", "shown"), ("C'", "matched"), ("C2", "contains")]
    assert "C2" not in out and "extra" not in out["a1"] and out["a1"]["origins"][0]["how"] == "added"
    assert out["a1"]["judged"] == [{"key": out["A'"]["proposal_id"], "verdict": "related"}]
    assert out["A'"]["judged"] == [{"key": out["a1"]["proposal_id"], "verdict": "related"}]
    assert [r["proposal_id"] for r in done["proposals"]][:2] == [rows["A'"]["proposal_id"], rows["a1"]["proposal_id"]]
    assert done["stats"]["unjudged_pairs"] == 0 and unjudged_pairs(done["proposals"]) == []
    for bad in ([{"extra": "x009", "verdict": "same", "of": keys["C"]}],
                [{"extra": "x001", "verdict": "same", "of": [keys["A'"], keys["C"]]}],
                [{"extra": "x001", "verdict": "same", "of": keys["C"]}, {"extra": "x001", "verdict": "same", "of": keys["C"]}],
                [{"extra": "x002", "verdict": "maybe", "of": keys["C"]}],
                [{"extra": "x002", "verdict": "contains", "of": [keys["C"]], "keep": "both"}]):
        with pytest.raises(ValueError):
            apply_judgements(res["proposals"], bad)
    # (5) standing detection over rows: cross-origin, not nested, not judged — the pairs brief and its fold
    def row(pid, text, seg, cell, **kw):
        a, b = int(seg[0][1:]), int(seg[-1][1:])
        return {"proposal_id": pid, "kind": "claim", "text": text, "lead": "", "segment_ids": seg, "from_i": a, "to_i": b,
                "start_time": float(2 * a), "end_time": float(2 * b + 1), "heading_index": 0, "parent_key": "",
                "refers_to": [], "speaker": "Alice",
                "origins": [{"set_id": "s", "proposal_id": pid, "cell": cell, "how": "shown", "text": text}], **kw}
    rows2 = [row("X", "X", ["s4", "s5"], "blind/opus"), row("Y", "Y", ["s5", "s6"], "undivided/fable"),
             row("Z", "Z", ["s5"], "blind/opus", parent_key="X"), row("W", "W", ["s6"], "undivided/fable"),
             row("V", "V", ["s6", "s7"], "blind/fable", judged=[{"key": "Y", "verdict": "different"}])]
    qs = unjudged_pairs(rows2)
    assert [(q["key"], q["a"]["text"], q["b"]["text"]) for q in qs] == [("q001", "X", "Y"), ("q002", "Z", "Y"), ("q003", "W", "V")]
    pb = render_pairs_brief(rows2, set_id="x", source="Lecture")
    assert "3 pair(s) of points" in pb and "- `q001` · 1 shared line(s)" in pb and "  - b: `p003` [claim] 00:10 (Alice) · undivided/fable  Y" in pb
    done2 = apply_judgements(rows2, [{"pair": "q001", "verdict": "contains", "keep": "b"}, {"pair": "q002", "verdict": "different"},
                                     {"pair": "q003", "verdict": "related"}])
    out2 = {r["proposal_id"]: r for r in done2["proposals"]}
    assert "X" not in out2 and out2["Z"]["parent_key"] == "Y"                              # X folded into Y; its child follows
    assert [(o["proposal_id"], o["how"]) for o in out2["Y"]["origins"]] == [("Y", "shown"), ("X", "contains")]
    assert out2["Z"]["judged"] == [{"key": "Y", "verdict": "different"}] and {"key": "Z", "verdict": "different"} in out2["Y"]["judged"]
    assert done2["stats"]["unjudged_pairs"] == 0
    with pytest.raises(ValueError):
        apply_judgements(rows2, [{"pair": "q001", "verdict": "same"}])                     # a fold on a pair names keep
    # (6) the same detection over ACCEPTED points: flagged = cross-origin, unnested, unjudged
    pts = [dict(r, id=r["proposal_id"], key=r["proposal_id"]) for r in rows2]
    pairs = overlapping_points(pts)
    flags = {(p["a"]["key"], p["b"]["key"]): p["flagged"] for p in pairs}
    assert flags == {("X", "Y"): True, ("X", "Z"): False, ("Y", "Z"): True, ("Y", "W"): False, ("Y", "V"): False, ("W", "V"): True}
    assert next(p for p in pairs if p["a"]["key"] == "Y" and p["b"]["key"] == "V")["judged"] == "different"


def test_outline_pass_reads_the_points_and_lands_as_section_and_synopsis_rows():
    # ruling bc62c727 (A): sections are PROPOSED over the merged points, each anchored at its first point
    segs = [{"id": f"s{k}", "index": k, "text": f"line {k}", "start": float(2 * k), "end": float(2 * k + 1)} for k in range(10)]
    unit = {"source": {"source_id": "lec", "title": "Lecture"}, "segments": segs, "strata": [],
            "speakers": {f"s{k}": "Alice" for k in range(10)}}
    pack = build_notes_pack(unit, {"key": "lecture-notes", "information_policy": {"stratum_roles": {}}})
    props = proposals_from_point_rows(validate_point_rows([
        {"kind": "claim", "from_i": 0, "to_i": 1, "text": "Kernels launch twice"},
        {"kind": "example", "from_i": 1, "to_i": 1, "text": "the warm-up launch", "parent": 0},
        {"kind": "claim", "from_i": 4, "to_i": 5, "text": "Streams do not share"},
        {"kind": "question", "from_i": 7, "to_i": 8, "text": "Why per rank?"}], pack), pack)
    brief = render_outline_brief(props, pack, set_id="x")
    assert "- `p001` [claim] 00:00 (Alice)  Kernels launch twice" in brief and "  - `p002` [example]" in brief
    assert '{"section": "<title>", "first": "p017"}' in brief and "4 points drafted from this source (3 top-level" in brief
    res = apply_outline(props, [{"section": "Kernel launches.", "first": "p001"}, {"section": "Streams per rank", "first": "p003"},
                                {"synopsis": "Launches warm the cache; streams stay per rank."}], pack)
    rows = res["proposals"]
    assert [(r["kind"], r["text"]) for r in rows] == [
        ("section", "Kernel launches"), ("claim", "Kernels launch twice"), ("example", "the warm-up launch"),
        ("section", "Streams per rank"), ("claim", "Streams do not share"), ("question", "Why per rank?"),
        ("synopsis", "Launches warm the cache; streams stay per rank.")]
    assert rows[3]["from_i"] == rows[3]["to_i"] == 4 and rows[3]["speaker"] == ""      # an anchor at its first point; nobody's line
    assert [r["proposal_id"] for r in rows if r["kind"] not in ("section", "synopsis")] == [p["proposal_id"] for p in props]
    assert res["stats"] == {"sections": 2, "points": 4, "smallest": 2, "largest": 2, "synopsis_words": 8}
    page = render_points([{**r, "key": r["proposal_id"], "ordinal": r["from_i"]} for r in rows])
    assert page.index("Kernel launches") < page.index("Kernels launch twice") < page.index("Streams per rank") < page.index("Why per rank?")
    for bad in ([{"section": "A", "first": "p002"}, {"synopsis": "s"}],                     # a child cannot open a section
                [{"section": "A", "first": "p003"}, {"synopsis": "s"}],                     # points under no heading
                [{"section": "A", "first": "p001"}, {"section": "B", "first": "p001"}, {"synopsis": "s"}],
                [{"section": "A", "first": "p001"}],                                        # no synopsis
                [{"section": "A", "first": "p001"}, {"synopsis": "s"}, {"section": "B", "first": "p003"}]):
        with pytest.raises(ValueError):
            apply_outline(props, bad, pack)
    with pytest.raises(ValueError):
        apply_outline(rows, [{"section": "A", "first": "p001"}, {"synopsis": "s"}], pack)   # already outlined


def test_lecture_rows_derive_the_speaker_and_carry_refers_to_and_the_per_kind_fields():
    # ruling ba341c72: speaker read off the lines (never drafted); refers_to / asr_form / unverified join the contract
    segs = [{"id": f"s{k}", "index": k, "text": t, "start": float(k), "end": float(k + 1)} for k, t in enumerate([
        "Nickel moves the tensors between GPUs.", "It launches one kernel per rank.",
        "Chris asks: why per rank?", "Because each rank owns a stream.", "And streams do not share."])]
    unit = {"source": {"source_id": "lec", "title": "Lecture"}, "segments": segs, "strata": [],
            "speakers": {"s0": "Alice", "s1": "Alice", "s2": "Mark", "s3": "Alice", "s4": "Bob"}}
    tprops = {"key": "lecture-notes", "information_policy": {"stratum_roles": {}},
              "presentation_policy": {"kind_fields": {"question": "never give a lead", "glossary": "asr_form when it differed"}}}
    pack = build_notes_pack(unit, tprops)
    md = render_notes_pack(pack)
    assert "## This type's row fields" in md and "* `glossary` — asr_form when it differed" in md
    assert md.index("## This type's row fields") < md.index("## Transcript")
    rows = validate_point_rows([
        {"kind": "glossary", "from_i": 0, "to_i": 0, "lead": "NCCL", "text": "NCCL moves tensors between GPUs", "asr_form": "Nickel"},
        {"kind": "code", "from_i": 1, "to_i": 1, "text": "one kernel launch per rank", "unverified": True},
        {"kind": "question", "from_i": 2, "to_i": 2, "lead": "Chris", "text": "Why one launch per rank?"},
        {"kind": "claim", "from_i": 3, "to_i": 4, "text": "Each rank owns its stream; streams are not shared",
         "parent": 2, "refers_to": [1, 1, 0]},
    ], pack)
    assert rows[0]["data"] == {"asr_form": "Nickel"} and rows[1]["data"] == {"unverified": True}
    assert rows[2]["lead"] == ""                        # a drafted questioner never refuses the row: it is derived
    assert rows[3]["refers_to"] == [1, 0]               # distinct, in the order given
    props = proposals_from_point_rows(rows, pack)
    by_text = {p["text"]: p for p in props}
    q, a = by_text["Why one launch per rank?"], by_text["Each rank owns its stream; streams are not shared"]
    assert q["speaker"] == "Mark" and q["speakers"] == []                       # who SPOKE the asking line
    assert a["speaker"] == "Alice" and a["speakers"] == ["Alice", "Bob"]        # a run that crosses a turn
    assert a["refers_to"] == [by_text["one kernel launch per rank"]["proposal_id"],
                              by_text["NCCL moves tensors between GPUs"]["proposal_id"]]
    assert a["parent_key"] == q["proposal_id"]
    for bad in ([5], [3], ["x"]):                       # a later row, itself, not a number
        with pytest.raises(ValueError):
            validate_point_rows([{"kind": "claim", "from_i": 0, "to_i": 0, "text": "a"},
                                 {"kind": "claim", "from_i": 1, "to_i": 1, "text": "b", "refers_to": bad}], pack)
    book = proposals_from_point_rows(validate_point_rows(
        [{"kind": "claim", "from_i": 0, "to_i": 0, "text": "a"}], build_notes_pack({**unit, "speakers": None}, tprops)),
        build_notes_pack({**unit, "speakers": None}, tprops))
    assert book[0]["speaker"] == "" and book[0]["refers_to"] == []


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
    assert "Quit in 1991. [(00:02–00:05)](https://www.youtube.com/watch?v=abc&t=2s){.src-ref} [§]" in addressable   # a linked span carries the quiet class (read 9d301b5a)
    assert "> — Gatto [(00:07–00:09)](https://www.youtube.com/watch?v=abc&t=7s){.src-ref} [§]" in addressable
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


def test_lecture_source_card_series_title_and_date_phrases():
    """Finding baa640e8 + ruling de9c4cda (H7): a lecture unit (no work metadata) renders the LECTURE
    card — the public title, the series, the dates at their precision, the speakers by label
    (anonymous voices left out), the talk's paraphrase sentence, the watch link first among the
    resources; the `series-lecture-notes` title policy continues the hand-notes naming; `date_phrase`
    says exactly as much as is known; a unit with neither series nor lecture title has no card."""
    unit = dict(_lecture_points()[0]["unit"])
    assert render_source_card(unit) == ""                                                   # no series, no public title: nothing yet
    unit.update(series=["GPU MODE"], lecture_title="Bonus Lecture: CUDA C++ llm.cpp",
                published_at="2024-04-27", recorded_at="2024-04-27", recorded_at_precision="around")
    card = render_source_card(unit, [{"label": "llm.c", "href": "https://github.com/karpathy/llm.c"}, {"label": "slides", "href": ""}])
    assert card == ("::: {.callout-note appearance=\"simple\" icon=false}\n"
                    "Notes on **Bonus Lecture: CUDA C++ llm.cpp**, a *GPU MODE* lecture — recorded around Apr 27, 2024, "
                    "published Apr 27, 2024. Speakers: Georgii, Mark, Audience member. The points paraphrase the talk "
                    "in the order it was given; only the quotations are verbatim.\n\n"
                    "Resources: [Watch on YouTube](https://www.youtube.com/watch?v=abc) · [llm.c](https://github.com/karpathy/llm.c) · slides\n:::\n")
    assert "Speaker 1" not in card and "SPEAKER_" not in card
    same = render_source_card({**unit, "recorded_at_precision": "day"})
    assert "— recorded and published Apr 27, 2024." in same
    only_pub = render_source_card({k: v for k, v in unit.items() if not k.startswith("recorded")}, [])
    assert "— published Apr 27, 2024. Speakers" in only_pub and "Resources: [Watch on YouTube](https://www.youtube.com/watch?v=abc)\n:::" in only_pub
    no_dates = render_source_card({k: v for k, v in unit.items() if k not in ("recorded_at", "recorded_at_precision", "published_at", "public_url")}, [])
    assert "Notes on **Bonus Lecture: CUDA C++ llm.cpp**, a *GPU MODE* lecture. Speakers:" in no_dates and "Resources" not in no_dates
    # the on-disk title's fullwidth colon folds back when no public title was bound
    assert lecture_title({"title": "Bonus Lecture： CUDA C++  llm.cpp"}) == "Bonus Lecture: CUDA C++ llm.cpp"
    assert lecture_title({"title": "PTX⧸SASS review", "lecture_title": ""}) == "PTX/SASS review"
    assert [date_phrase("2024-04-27", p) for p in ("day", "around", "month", "year")] == ["Apr 27, 2024", "around Apr 27, 2024", "Apr 2024", "2024"]
    assert date_phrase("20240427") == "" and date_phrase("2024-13-01") == ""
    # the title policy: the series + the lecture's label, `notes` after the label, the topic after the colon
    fm = '---\ntitle: "old"\ndate: 2026-09-21\ncategories: [gpu-mode, notes]\n---\n'
    pts = _lecture_points()
    policy = {"title": "series-lecture-notes", "description": "derived"}
    d = derive_frontmatter(fm, pts, policy, unit=unit)
    assert d.startswith('---\ntitle: "GPU MODE Bonus Lecture notes: CUDA C++ llm.cpp"\ndescription: "') and d.endswith('date: 2026-09-21\ncategories: [gpu-mode, notes]\n---\n')
    assert derive_frontmatter(fm, pts, policy, unit={**unit, "lecture_title": "Profiling tools"}).startswith('---\ntitle: "GPU MODE Profiling tools notes"\n')
    assert derive_frontmatter(fm, pts, policy, unit={**unit, "series": [], "lecture_title": "Lecture 12: Flash Attention"}).startswith('---\ntitle: "Lecture 12 notes: Flash Attention"\n')
    assert derive_frontmatter(d, pts, policy, unit=unit) == d                               # idempotent
    assert derive_frontmatter(fm, pts, policy).startswith('---\ntitle: "Bonus Lecture notes"\n')   # the snapshot alone (no series, no public title): the Source title, folded


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
                                             "source_id": "src-1", "rendition_id": "rend-1"}})
            # The real topology (the unit read is the correction core's spine read): Source <- AudioSegment
            # <- AudioRendition <- Segment. One segment a chunk respine REPLACED stays on the graph stamped
            # `superseded_by` — the live view never shows it (the Bonus lecture finding, 2026-09-20).
            nodes += [{"id": "aseg-1", "label": "AudioSegment", "sources": [], "properties": {"index": 0}},
                      {"id": "rend-1", "label": "AudioRendition", "sources": [],
                       "properties": {"chain": [], "is_raw": True, "preprocessing": None}},
                      {"id": "seg-old", "label": "Segment", "sources": [],
                       "properties": {"text": "Gatto quit teaching in 1981.", "index": 1, "start_time": 2.0,
                                      "end_time": 5.0, "source_id": "src-1", "rendition_id": "rend-1",
                                      "superseded_by": "seg-1"}}]
            spine_edges = [{"id": "e-aseg", "source_id": "aseg-1", "target_id": "src-1", "relation_type": "PART_OF", "properties": {}},
                           {"id": "e-rend", "source_id": "rend-1", "target_id": "aseg-1", "relation_type": "DERIVED_FROM", "properties": {}}]
            spine_edges += [{"id": f"e-seg-{k}", "source_id": k, "target_id": "rend-1", "relation_type": "PART_OF", "properties": {}}
                            for k in [f"seg-{i}" for i in range(8)] + ["seg-old"]]
            for cid, cat, sids, st in (("cor-h1", "section-header", ["seg-0"], 0.0), ("cor-t", "tangent", ["seg-2"], 5.0),
                                       ("cor-h2", "section-header", ["seg-3"], 6.0), ("cor-q", "quotation", ["seg-4", "seg-5"], 7.0)):
                nodes.append({"id": cid, "label": "Correction", "sources": [],
                              "properties": {"correction_type": "stratum", "status": "applied", "actor": "human",
                                             "session_id": "s", "created_at": 1.0,
                                             "payload": {"operation": "classify", "source_id": "src-1", "category": cat,
                                                         "segment_ids": sids, "start_time": st}}})
            await extend_graph(sg.queue, sg.graph_id, nodes, spine_edges)
    asyncio.run(go())


def _edit_sibling_segment(sdb: str, seg_id: str, text: str):
    async def go():
        async with open_graph(sdb) as sg:
            await graph_task(sg.queue, sg.graph_id, "update_node", node_id=seg_id, properties={"text": text})
    asyncio.run(go())


def _build_lecture_sibling(sdb: str):
    """A FAKE transcription graph for a LECTURE: one Source in a confirmed Collection (the series)
    with a bound public URL (+ the playlist title the binding matched), the dates
    `bind-source-dates` lands, a short spine, and no work structure — the shape the source card
    and the series title read (finding baa640e8)."""
    async def go():
        async with open_graph(sdb) as sg:
            nodes = [{"id": "src-lec", "label": "Source", "sources": [],
                      "properties": {"title": "Bonus Lecture： CUDA C++ llm.cpp", "media_type": "audio",
                                     "public_url": "https://www.youtube.com/watch?v=abc",
                                     "public_url_evidence": {"kind": "playlist-metadata", "playlist_title": "Bonus Lecture: CUDA C++ llm.cpp"},
                                     "published_at": "2024-04-27", "recorded_at": "2024-04-27", "recorded_at_precision": "around"}},
                     {"id": "coll-gm", "label": "Collection", "sources": [], "properties": {"title": "GPU MODE", "status": "confirmed"}},
                     {"id": "coll-old", "label": "Collection", "sources": [], "properties": {"title": "GPU MODE_OLD", "status": "retired"}},
                     {"id": "aseg-l", "label": "AudioSegment", "sources": [], "properties": {"index": 0}},
                     {"id": "rend-l", "label": "AudioRendition", "sources": [],
                      "properties": {"chain": [], "is_raw": True, "preprocessing": None}}]
            for i, (t, a, b) in enumerate([("Thrust wraps CUB.", 0.0, 4.0),
                                           ("CUB is the device layer.", 4.0, 8.0),
                                           ("Kernels launch through it.", 8.0, 12.0)]):
                nodes.append({"id": f"lseg-{i}", "label": "Segment", "sources": [],
                              "properties": {"text": t, "index": i, "start_time": a, "end_time": b,
                                             "source_id": "src-lec", "rendition_id": "rend-l"}})
            edges = [{"id": "e-l-coll", "source_id": "src-lec", "target_id": "coll-gm", "relation_type": "PART_OF", "properties": {}},
                     {"id": "e-l-old", "source_id": "src-lec", "target_id": "coll-old", "relation_type": "PART_OF", "properties": {}},
                     {"id": "e-l-aseg", "source_id": "aseg-l", "target_id": "src-lec", "relation_type": "PART_OF", "properties": {}},
                     {"id": "e-l-rend", "source_id": "rend-l", "target_id": "aseg-l", "relation_type": "DERIVED_FROM", "properties": {}}]
            edges += [{"id": f"e-lseg-{i}", "source_id": f"lseg-{i}", "target_id": "rend-l", "relation_type": "PART_OF", "properties": {}}
                      for i in range(3)]
            await extend_graph(sg.queue, sg.graph_id, nodes, edges)
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
        {"kind": "claim", "from_i": 3, "to_i": 4, "text": "Isolation → no coherent picture.", "lead": "coherent", "parent": 2,
         "refers_to": [0]},   # a back-link (ruling ba341c72 (2)): rows -> proposal ids -> a REFERENCES edge at accept
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

    async def _back_links(db):   # point -> point REFERENCES edges minted from `refers_to`
        async with open_graph(db) as g:
            res = await graph_task(g.queue, g.graph_id, "query_edges",
                                   query=EdgeQuery(relation_type="REFERENCES").to_dict())
            rows = [e.to_dict() if hasattr(e, "to_dict") else dict(e) for e in (getattr(res, "edges", None) or res.rows or [])]
            return [(e["source_id"], e["target_id"]) for e in rows if (e.get("properties") or {}).get("role") == "refers_to"]
    assert ops[3]["args"]["point"]["refers_to"] == [ops[0]["args"]["point"]["key"]]
    links = asyncio.run(_back_links(pdb))
    assert len(links) == 1 and links[0][0] != links[0][1]
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
    # the child's refers_to prints as a back-link to the standing point it leans on (work item e370e5db (2))
    assert "- Subjects taught in isolation. [§]" in staged and "\n  - Isolation → no **coherent** picture. (see [§ Gatto](#pt-" in staged
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
    assert "\n  - Isolation = no **coherent** picture. (see [§ Gatto](#pt-" in staged and f"#pt-{child_key[:8]}" in staged   # the edit keeps its back-link
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
    assert "\n  - Isolation = no **coherent** picture. (see [§ Gatto](#pt-" in regrouped        # the nesting (and the back-link) survives the regroup
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
    assert asyncio.run(_back_links(rdb)) == links  # and so does the back-link

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
def test_cli_points_belong_to_the_source_rehome_and_replay_parity(tmp_path):
    """Ruling 96be1528 (P) over the CLI (the re-home piece of 81d6e669): an accept lands
    SUBSTANCE under the PointSet of its (Source, unit) — minted on first use, the Note RENDERS
    it, the op names it — and the Note's own points (a section) under the Note; a journal of
    PRE-re-home accepts (no `point_set` on the op) replays under the Note and ONE journaled
    `rehome-points` op moves them: same keys, same References in the same order, the nesting
    and back-link edges re-derived, the render byte-identical; the whole journal replays onto
    a fresh db to the same ids and bytes (the migration oracle); a second re-home is a no-op;
    a shared set refuses the clean-slate retract."""
    from cjm_context_graph_primitives.query import NodeQuery
    from cjm_context_graph_projection.purenotes import (accept_point, deliverable_owns, ensure_point_set, load_points,
                                                        rehome_points, retract_note_points)
    from cjm_dev_graph_schema.identity import note_node_id, point_set_node_id
    sib_dir, pri_dir = tmp_path / "sib", tmp_path / "pri"
    sib_dir.mkdir(); pri_dir.mkdir()
    sdb = str(sib_dir / "sib.db")
    _build_sibling(sdb)
    pdb, pj = str(pri_dir / "pri.db"), str(pri_dir / "writes.jsonl")
    (pri_dir / "graph.config.json").write_text(json.dumps(
        {"notes_profile": "quarto_post", "emit_root": str(pri_dir / "staging"), "sibling_graphs": {"tx": sdb}}))
    base = ("--graph-db-path", pdb, "--journal-path", pj, "--source-journal-path", str(pri_dir / "source.jsonl"))
    slug = "the-learning-game/ch01"
    assert _run(*base, "notes-type", "pure-notes").returncode == 0
    post = "---\ntitle: \"The Learning Game, Chapter 1\"\ndate: 2026-09-07\ncategories: [book, notes]\n---\n\nPreamble.\n"
    r = _run(*base, "new-note", "--slug", slug, "--content", post)
    assert r.returncode == 0, r.stderr or r.stdout
    note_id = note_node_id(slug)
    r = _run(*base, "notes-pack", "--source", "Seven Dangerous")
    assert r.returncode == 0, r.stderr or r.stdout
    pack_json = next((pri_dir / "purenotes" / "packs").glob("npack_*.json"))
    rows = tmp_path / "rows.jsonl"
    rows.write_text("\n".join(json.dumps(x) for x in [
        {"kind": "claim", "from_i": 0, "to_i": 0, "text": "Gatto quit teaching in 1991.", "lead": "Gatto"},
        {"kind": "quotation", "from_i": 1, "to_i": 2, "text": "I teach confusion.", "attribution": "Gatto"},
        {"kind": "claim", "from_i": 3, "to_i": 3, "text": "Subjects taught in isolation."},
        {"kind": "claim", "from_i": 3, "to_i": 4, "text": "Isolation = no coherent picture.", "lead": "coherent", "parent": 2,
         "refers_to": [0]},
        {"kind": "synopsis", "from_i": 0, "to_i": 4, "text": "Gatto quit in 1991; school teaches confusion by isolating subjects."},
    ]) + "\n")
    r = _run(*base, "notes-ingest", "--pack", str(pack_json), "--rows", str(rows), "--proposer", "test")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run(*base, "notes-accept", "--slug", slug, "--accept-all")
    assert r.returncode == 0 and "accepted 5" in r.stdout, r.stderr or r.stdout
    ops = [o for o in read_journal(pj) if o["verb"] == "accept-point"]
    set_id = point_set_node_id("tx", "src-1", "")
    assert len(ops) == 5 and all(o["args"]["point_set"] == {"graph": "tx", "source_id": "src-1", "unit": ""} for o in ops)
    r = _run(*base, "notes-render", "--slug", slug)
    assert r.returncode == 0 and "from 5 point(s)" in r.stdout, r.stderr or r.stdout
    staged = (pri_dir / "staging" / "the-learning-game" / "ch01" / "index.md").read_text()
    assert "## Lesson 1. Confusion" in staged and "(see [§ Gatto](#pt-" in staged

    async def _shape(db):   # the graph's point structure: sets, owners, ids, keys, edge multiset
        async with open_graph(db) as g:
            res = await graph_task(g.queue, g.graph_id, "query_nodes", query=NodeQuery(label="PointSet").to_dict())
            sets = sorted(n.id for n in (res.nodes or []))
            res = await graph_task(g.queue, g.graph_id, "query_nodes", query=NodeQuery(label="Point").to_dict())
            pts = {n.id: dict(n.properties) for n in (res.nodes or [])}
            edges = []
            for rel in ("HAS_POINT", "DERIVED_FROM", "ELABORATES", "REFERENCES", "RENDERS"):
                res = await graph_task(g.queue, g.graph_id, "query_edges", query=EdgeQuery(relation_type=rel).to_dict())
                rows = [e.to_dict() if hasattr(e, "to_dict") else dict(e) for e in (getattr(res, "edges", None) or res.rows or [])]
                edges += sorted((rel, e["source_id"], e["target_id"], json.dumps(e.get("properties") or {}, sort_keys=True)) for e in rows)
            return {"sets": sets, "owners": sorted({p["owner_id"] for p in pts.values()}), "ids": sorted(pts),
                    "keys": sorted(p["key"] for p in pts.values()), "edges": edges,
                    "note_id_on_wire": any("note_id" in p for p in pts.values())}
    live = asyncio.run(_shape(pdb))
    assert live["sets"] == [set_id] and live["owners"] == [set_id] and not live["note_id_on_wire"]
    assert ("RENDERS", note_id, set_id, "{}") in live["edges"]
    assert all(s == set_id for rel, s, t, _ in live["edges"] if rel == "HAS_POINT")
    assert sum(1 for e in live["edges"] if e[0] == "ELABORATES") == 1
    assert sum(1 for e in live["edges"] if e[0] == "REFERENCES") == 1
    assert sum(1 for e in live["edges"] if e[0] == "DERIVED_FROM") == 11        # 1 + 2 + 1 + 2 + 5 segment References
    assert _run("--graph-db-path", pdb, "read", note_id).stdout == staged

    # (2) a PRE-re-home journal: the same ops without `point_set` land under the NOTE, as they did live
    legacy = pri_dir / "legacy.jsonl"
    with legacy.open("w") as fh:
        for line in Path(pj).read_text().splitlines():
            o = json.loads(line)
            if o["verb"] == "accept-point":
                o["args"] = {k: v for k, v in o["args"].items() if k != "point_set"}
            fh.write(json.dumps(o, ensure_ascii=False) + "\n")
    ldb = str(pri_dir / "legacy.db")
    r = _run("--graph-db-path", ldb, "--journal-path", str(legacy), "replay")
    assert r.returncode == 0, r.stderr or r.stdout
    old = asyncio.run(_shape(ldb))
    assert old["sets"] == [] and old["owners"] == [note_id] and old["keys"] == live["keys"] and old["ids"] != live["ids"]
    assert _run("--graph-db-path", ldb, "read", note_id).stdout == staged
    # (3) ONE journaled re-home: the points move to the set; ids, owners and edges converge on the live graph
    lbase = ("--graph-db-path", ldb, "--journal-path", str(legacy), "--source-journal-path", str(pri_dir / "lsource.jsonl"))
    r = _run(*lbase, "notes-rehome", "--slug", slug)
    assert r.returncode == 0 and "5 substance point(s) re-homed" in r.stdout, r.stderr or r.stdout
    assert len([o for o in read_journal(str(legacy)) if o["verb"] == "rehome-points"]) == 1
    assert asyncio.run(_shape(ldb)) == live
    r = _run(*lbase, "notes-rehome", "--slug", slug)
    assert r.returncode == 0 and "nothing to move" in r.stdout
    assert len([o for o in read_journal(str(legacy)) if o["verb"] == "rehome-points"]) == 1   # the no-op journals nothing
    # the render is byte-identical (the body is a function of the substance, not the owner) and lands no new op
    r = _run(*lbase, "notes-render", "--slug", slug)
    assert r.returncode == 0, r.stderr or r.stdout
    assert (pri_dir / "staging" / "the-learning-game" / "ch01" / "index.md").read_text() == staged
    assert len([o for o in read_journal(str(legacy)) if o["verb"] == "render-notes"]) == 1
    # (4) the migrated journal replays onto a fresh db to the same ids, edges and bytes — the oracle
    rdb = str(pri_dir / "rep.db")
    r = _run("--graph-db-path", rdb, "--journal-path", str(legacy), "replay")
    assert r.returncode == 0, r.stderr or r.stdout
    assert asyncio.run(_shape(rdb)) == live
    assert _run("--graph-db-path", rdb, "read", note_id).stdout == staged
    # an edit by prefix on a set-owned point re-parents under the set; replay agrees
    child_key = ops[3]["args"]["point"]["key"]
    r = _run("--graph-db-path", ldb, "--format", "agent", "locate", child_key[:8])
    child_id = [m for m in json.loads(r.stdout)["matches"] if m.get("label") == "Point"][0]["id"]
    r = _run(*lbase, "notes-edit", child_id[:8], "--parent", "none")
    assert r.returncode == 0 and "parent_key changed" in r.stdout, r.stderr or r.stdout
    r = _run(*lbase, "notes-edit", child_id[:8], "--parent", ops[2]["args"]["point"]["key"])
    assert r.returncode == 0, r.stderr or r.stdout
    assert asyncio.run(_shape(ldb)) == live
    rdb2 = str(pri_dir / "rep2.db")
    assert _run("--graph-db-path", rdb2, "--journal-path", str(legacy), "replay").returncode == 0
    assert asyncio.run(_shape(rdb2)) == live

    # (5) the deliverable's OWN points: a `section` lands under the Note even with a point_set on the op;
    #     the re-home leaves it; a second deliverable sharing the set refuses the clean-slate retract
    async def _own(db):
        async with open_graph(db) as g:
            sec = {"key": "sec-1", "kind": "section", "text": "Lesson one", "ordinal": 0, "start_time": 0.0,
                   "segment_ids": [], "unit": ops[0]["args"]["point"]["unit"]}
            assert deliverable_owns(sec) and not deliverable_owns(ops[0]["args"]["point"])
            r = await accept_point(g, slug, sec, observations=[], actor="user:test",
                                   point_set={"graph": "tx", "source_id": "src-1", "unit": ""})
            assert r["owner_id"] == note_id and r["set_id"] == set_id and r["written"], r
            pts = await load_points(g, note_id)
            assert len(pts) == 6 and {p["owner_id"] for p in pts} == {note_id, set_id}
            r = await rehome_points(g, slug, actor="user:test")
            assert not r["written"] and r["moved"] == 0 and r["kept"] == 1 and r["set_id"] == set_id
            # a sibling deliverable renders the same set: the set's points are the source's, not this Note's to wipe
            other = "the-learning-game/ch01-distilled"
            await extend_graph(g.queue, g.graph_id, [{"id": note_node_id(other), "label": "Note",
                                                      "properties": {"slug": other, "title": other, "root_kind": "asserted"},
                                                      "sources": []}], [])
            assert await ensure_point_set(g, note_node_id(other), ops[0]["args"]["point"]["unit"]) == set_id
            assert len(await load_points(g, note_node_id(other))) == 5
            r = await retract_note_points(g, slug, actor="user:test")
            assert r.get("error") and "also rendered" in r["error"] and r["retracted"] == []
    asyncio.run(_own(pdb))


def test_frontmatter_lecture_label_leads_title_and_subtitle():
    """Ruling 96be1528 (9): the standalone lecture resource's title is the lecture's OWN label;
    the series and the lecture's slot move to a `subtitle` line — inserted after the title,
    replaced in place on a re-derive, untouched under the companion policy."""
    fm = '---\ntitle: "old"\ndate: 2026-09-22\ncategories: [gpu-mode]\n---\n'
    unit = {"graph": "tx", "source_id": "s", "title": "Bonus Lecture: CUDA C++ llm.cpp", "series": ["GPU MODE"]}
    pts = [{"key": "k", "kind": "claim", "text": "t", "start_time": 1.0, "ordinal": 0, "unit": unit}]
    out = derive_frontmatter(fm, pts, {"title": "lecture-label-leads", "description": "synopsis"}, synopsis="What it shows.", unit=unit)
    assert out == ('---\ntitle: "CUDA C++ llm.cpp"\nsubtitle: "Notes on the GPU MODE Bonus Lecture"\n'
                   'description: "What it shows."\ndate: 2026-09-22\ncategories: [gpu-mode]\n---\n')
    assert derive_frontmatter(out, pts, {"title": "lecture-label-leads", "description": "synopsis"}, synopsis="What it shows.", unit=unit) == out
    # the companion policy keeps the pre-graph naming and never writes a subtitle
    comp = derive_frontmatter(fm, pts, {"title": "series-lecture-notes"}, unit=unit)
    assert comp.startswith('---\ntitle: "GPU MODE Bonus Lecture notes: CUDA C++ llm.cpp"\ndate:') and "subtitle" not in comp
    # a label that already names the series is not doubled in the subtitle
    doubled = derive_frontmatter(fm, pts, {"title": "lecture-label-leads"}, unit={**unit, "title": "GPU MODE Lecture 12: Flash Attention"})
    assert doubled.startswith('---\ntitle: "Flash Attention"\nsubtitle: "Notes on the GPU MODE Lecture 12"\n')
    # a title with no colon: the whole title leads, the subtitle names the series lecture
    plain = derive_frontmatter(fm, pts, {"title": "lecture-label-leads"}, unit={**unit, "title": "Building a GPU kernel"})
    assert plain.startswith('---\ntitle: "Building a GPU kernel"\nsubtitle: "Notes on the GPU MODE"\n')


@pytest.mark.skipif(not _HAVE_GRAPH, reason=f"graph capability {DEFAULT_GRAPH_ID!r} not installed (CI)")
def test_cli_block_merge_judge_accept_and_standing_detection_replay(tmp_path):
    # ruling 1798a796 / work item 1561551e over the CLI: block merge -> judge -> accept (origins + judgements ride
    # the points) -> standing detection on the accepted draft -> the judge over the draft (edits / a fold) -> replay
    sib_dir, pri_dir = tmp_path / "sib", tmp_path / "pri"
    sib_dir.mkdir(); pri_dir.mkdir()
    sdb = str(sib_dir / "sib.db")
    _build_sibling(sdb)
    pdb, pj = str(pri_dir / "pri.db"), str(pri_dir / "writes.jsonl")
    lane = pri_dir / "purenotes"
    (pri_dir / "graph.config.json").write_text(json.dumps(
        {"notes_profile": "quarto_post", "emit_root": str(pri_dir / "staging"), "sibling_graphs": {"tx": sdb}}))
    base = ("--graph-db-path", pdb, "--journal-path", pj, "--source-journal-path", str(pri_dir / "source.jsonl"))
    assert _run(*base, "notes-type", "pure-notes").returncode == 0
    r = _run(*base, "new-note", "--slug", "the-learning-game/ch01", "--content", "---\ntitle: \"Ch 1\"\ndate: 2026-09-22\n---\n\nBorn.\n")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run(*base, "notes-pack", "--source", "Seven Dangerous")
    assert r.returncode == 0, r.stderr or r.stdout
    pack_json = next((lane / "packs").glob("npack_*.json"))

    def ingest(name, arm, model, rows):
        f = tmp_path / f"{name}.jsonl"
        f.write_text("\n".join(json.dumps(x) for x in rows) + "\n")
        r = _run(*base, "notes-ingest", "--pack", str(pack_json), "--rows", str(f), "--proposer", name, "--arm", arm, "--model", model)
        assert r.returncode == 0, r.stderr or r.stdout

    def claim(a, b, text, **kw):
        return {"kind": "claim", "from_i": a, "to_i": b, "text": text, **kw}
    quote = {"kind": "quotation", "from_i": 1, "to_i": 2, "text": "I teach confusion.", "attribution": "Gatto"}
    ingest("bo", "blind", "opus", [claim(0, 0, "Gatto quit teaching in 1991.", lead="Gatto"), quote,
                                   claim(3, 3, "Subjects taught in isolation."), claim(4, 4, "Kids never build a coherent picture.")])
    ingest("bf", "blind", "fable", [claim(0, 0, "Gatto quit in 1991."), quote, claim(3, 4, "Isolation leaves no coherent picture.")])
    ingest("uf", "undivided", "fable", [claim(0, 0, "Gatto left teaching in 1991."), claim(4, 4, "No coherent picture forms.")])
    # (1) the block merge: 3 blocks, one cell shown whole per block, the rest matched or extras; the judge brief beside the set
    r = _run(*base, "notes-merge", "--pack", str(pack_json), "--iou", "0.6")
    assert r.returncode == 0 and "over 3 block(s)" in r.stdout and "**3** shown point(s) + **2** extra(s)" in r.stdout \
        and "judge brief" in r.stdout, r.stderr or r.stdout
    merged = pick_propset(load_notes_propsets(lane / "proposals"), None)
    set_id = merged["manifest"]["proposal_set_id"]
    assert merged["manifest"]["merge"]["blocks"][2]["shown"] == "undivided/fable" and len(merged["manifest"]["merged_from"]) == 3
    judge_md = Path(merged["path"]).parent / "judge.md"
    assert judge_md.exists() and "## Blocks (1 of 3 carry extras)" in judge_md.read_text()
    r = _run(*base, "notes-accept", "--slug", "the-learning-game/ch01", "--set", set_id, "--accept-all")
    assert r.returncode == 1 and "unjudged extra(s)" in r.stderr                       # extras are never walked
    # (2) the judge: the compound folds into the shown row it contains (provenance kept); the lone claim is added
    xs = {x["text"]: x["key"] for x in extra_list(merged["proposals"])}
    ks = {e["text"]: e["key"] for e in points_index(merged["proposals"])}
    ans = tmp_path / "judge.jsonl"
    ans.write_text(json.dumps({"extra": xs["Isolation leaves no coherent picture."], "verdict": "contains",
                               "of": [ks["No coherent picture forms."]], "keep": "shown"}) + "\n"
                   + json.dumps({"extra": xs["Subjects taught in isolation."], "verdict": "different", "of": []}) + "\n")
    r = _run(*base, "notes-judge", "--set", set_id, "--rows", str(ans), "--model", "fable")
    assert r.returncode == 0 and "4 point(s): extras 2 (answered 2 · folded 1 · added 1 · pending 0)" in r.stdout \
        and "unjudged pairs left: 0" in r.stdout, r.stderr or r.stdout
    judged = pick_propset(load_notes_propsets(lane / "proposals"), None)
    jid = judged["manifest"]["proposal_set_id"]
    assert judged["manifest"]["judged_from"] == set_id and judged["manifest"]["judges"][0]["model"] == "fable"
    rows = {p["text"]: p for p in judged["proposals"]}
    assert [(o["cell"], o["how"]) for o in rows["No coherent picture forms."]["origins"]] == \
        [("undivided/fable", "shown"), ("blind/opus", "matched"), ("blind/fable", "contains")]
    assert rows["Subjects taught in isolation."]["origins"][0]["how"] == "added" and not any(p.get("extra") for p in judged["proposals"])
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"extra": "x009", "verdict": "same", "of": "p001"}) + "\n")
    r = _run(*base, "notes-judge", "--set", set_id, "--rows", str(bad), "--model", "opus")   # a second judge, refused loudly
    assert r.returncode == 1 and "unknown extra" in r.stderr
    # (3) accept: origins ride the points (in the journaled accept op); the draft is CLEAN
    r = _run(*base, "notes-accept", "--slug", "the-learning-game/ch01", "--set", jid, "--accept-all")
    assert r.returncode == 0 and "accepted 4" in r.stdout, r.stderr or r.stdout
    ops = [o for o in read_journal(pj) if o["verb"] == "accept-point"]
    origins = {o["args"]["point"]["text"]: o["args"]["point"].get("origins") for o in ops}
    assert [x["how"] for x in origins["Gatto quit in 1991."]] == ["shown", "matched", "matched"]
    r = _run(*base, "notes-overlap", "--slug", "the-learning-game/ch01")
    assert r.returncode == 0 and "CLEAN" in r.stdout and "0 overlapping pair(s)" in r.stdout, r.stderr or r.stdout
    # (4) a later solo point over the same lines (no origins = unknown) flags the draft UNCLEAN; --brief names the pairs
    ingest("solo", "", "", [claim(3, 4, "Isolation → an incoherent picture.")])
    solo = pick_propset(load_notes_propsets(lane / "proposals"), None)
    r = _run(*base, "notes-accept", "--slug", "the-learning-game/ch01", "--set", solo["manifest"]["proposal_set_id"], "--accept-all")
    assert r.returncode == 0 and "accepted 1" in r.stdout, r.stderr or r.stdout
    brief = tmp_path / "pairs.md"
    r = _run(*base, "notes-overlap", "--slug", "the-learning-game/ch01", "--brief", str(brief))
    assert r.returncode == 0 and "UNCLEAN — 2 unjudged cross-origin pair(s)" in r.stdout, r.stderr or r.stdout
    text = brief.read_text()
    assert "2 pair(s) of points" in text and "- `q001`" in text and "- `q002`" in text
    # (5) the judge over the ACCEPTED draft: verdicts land as journaled edit-point ops; clean again; replay agrees
    ans2 = tmp_path / "judge2.jsonl"
    ans2.write_text(json.dumps({"pair": "q001", "verdict": "different"}) + "\n" + json.dumps({"pair": "q002", "verdict": "related"}) + "\n")
    r = _run(*base, "notes-overlap", "--slug", "the-learning-game/ch01", "--rows", str(ans2))
    assert r.returncode == 0 and "3 point(s) edited · 0 retracted" in r.stdout and "unjudged pairs left: 0" in r.stdout, r.stderr or r.stdout
    assert len([o for o in read_journal(pj) if o["verb"] == "edit-point"]) == 3
    r = _run(*base, "notes-overlap", "--slug", "the-learning-game/ch01")
    assert "CLEAN" in r.stdout and "2 judged" in r.stdout, r.stderr or r.stdout
    # (6) a fold over the accepted draft: the duplicate is retracted (journaled), the survivor stands; replay converges
    ingest("solo2", "", "", [claim(0, 0, "Gatto quit teaching, 1991.")])
    solo2 = pick_propset(load_notes_propsets(lane / "proposals"), None)
    r = _run(*base, "notes-accept", "--slug", "the-learning-game/ch01", "--set", solo2["manifest"]["proposal_set_id"], "--accept-all")
    assert r.returncode == 0 and "accepted 1" in r.stdout, r.stderr or r.stdout
    r = _run(*base, "notes-overlap", "--slug", "the-learning-game/ch01", "--brief", str(brief))
    assert "UNCLEAN — 1 unjudged cross-origin pair(s)" in r.stdout, r.stderr or r.stdout
    side = next(l.strip()[2] for l in brief.read_text().splitlines() if "Gatto quit in 1991." in l and l.strip()[:3] in ("- a", "- b"))
    ans3 = tmp_path / "judge3.jsonl"
    ans3.write_text(json.dumps({"pair": "q001", "verdict": "same", "keep": side}) + "\n")
    r = _run(*base, "notes-overlap", "--slug", "the-learning-game/ch01", "--rows", str(ans3))
    assert r.returncode == 0 and "0 point(s) edited · 1 retracted" in r.stdout, r.stderr or r.stdout
    assert len([o for o in read_journal(pj) if o["verb"] == "retract-point"]) == 1
    r = _run(*base, "notes-overlap", "--slug", "the-learning-game/ch01")
    assert "CLEAN" in r.stdout and "_5 points" in r.stdout, r.stderr or r.stdout
    rep = tmp_path / "rep"; rep.mkdir()
    r = _run("--graph-db-path", str(rep / "rep.db"), "--journal-path", pj, "replay")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run("--graph-db-path", str(rep / "rep.db"), "notes-overlap", "--slug", "the-learning-game/ch01")
    assert "CLEAN" in r.stdout and "_5 points" in r.stdout and "2 judged" in r.stdout, r.stderr or r.stdout


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
                                                                    "category": "section-header", "segment_ids": ["seg-b0"], "start_time": 0.0}}},
                     {"id": "aseg-2", "label": "AudioSegment", "sources": [], "properties": {"index": 0}},
                     {"id": "rend-2", "label": "AudioRendition", "sources": [],
                      "properties": {"chain": [], "is_raw": True, "preprocessing": None}}]
            for n in nodes:
                if n["label"] == "Segment":
                    n["properties"]["rendition_id"] = "rend-2"
            edges = [{"id": "e-aseg-2", "source_id": "aseg-2", "target_id": "src-2", "relation_type": "PART_OF", "properties": {}},
                     {"id": "e-rend-2", "source_id": "rend-2", "target_id": "aseg-2", "relation_type": "DERIVED_FROM", "properties": {}},
                     {"id": "e-seg-b0", "source_id": "seg-b0", "target_id": "rend-2", "relation_type": "PART_OF", "properties": {}},
                     {"id": "e-seg-b1", "source_id": "seg-b1", "target_id": "rend-2", "relation_type": "PART_OF", "properties": {}}]
            await extend_graph(sg.queue, sg.graph_id, nodes, edges)
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


@pytest.mark.skipif(not _HAVE_GRAPH, reason=f"graph capability {DEFAULT_GRAPH_ID!r} not installed (CI)")
def test_cli_lecture_page_reads_series_dates_and_style_live_and_replays(tmp_path):
    """Finding baa640e8 + ruling de9c4cda end to end: a lecture type whose policy asks for the
    series title, the lecture card and the head/start render style renders the series-lecture
    title, the card (series, dates at precision, watch link) and quiet start-time spans with
    head permalinks — the series and dates read LIVE from the sibling (the accepted point's
    snapshot predates a date bound later); the render op journals the facts it observed, and a
    replay onto a fresh db with NO sibling reproduces the live text byte for byte."""
    sib_dir, pri_dir = tmp_path / "sib", tmp_path / "pri"
    sib_dir.mkdir(); pri_dir.mkdir()
    sdb = str(sib_dir / "sib.db")
    _build_lecture_sibling(sdb)
    pdb, pj = str(pri_dir / "pri.db"), str(pri_dir / "writes.jsonl")
    (pri_dir / "graph.config.json").write_text(json.dumps(
        {"notes_profile": "quarto_post", "emit_root": str(pri_dir / "staging"), "sibling_graphs": {"tx": sdb}}))
    base = ("--graph-db-path", pdb, "--journal-path", pj, "--source-journal-path", str(pri_dir / "source.jsonl"))
    policy = tmp_path / "lecture.policy.json"
    policy.write_text(json.dumps({"title": "Lecture notes (test)",
                                  "information_policy": pure_notes_type("test").information_policy,   # the read policy is pure-notes'; only the presentation differs
                                  "presentation_policy": {
                                      "frontmatter": {"title": "series-lecture-notes", "description": "derived"},
                                      "source_card": "lecture", "render_style": {"span": "start", "anchor": "head"}, "public": "expanded"}}))
    assert _run(*base, "notes-type", "lecture-notes", "--policy-file", str(policy)).returncode == 0
    post = "---\ntitle: \"placeholder\"\ndate: 2026-09-21\ncategories: [gpu-mode, notes]\n---\n\nPreamble.\n"
    r = _run(*base, "new-note", "--slug", "gpu-mode-notes/bonus", "--content", post)
    assert r.returncode == 0, r.stderr or r.stdout
    note_id = [o for o in read_journal(pj) if o["verb"] == "assert"][-1]["args"]["subject"]
    # the accepted point's unit snapshot is taken BEFORE the dates exist on the Source: strip them first
    async def strip_dates():
        async with open_graph(sdb) as sg:
            await graph_task(sg.queue, sg.graph_id, "update_node", node_id="src-lec",
                             properties={"published_at": "", "recorded_at": "", "recorded_at_precision": ""})
    asyncio.run(strip_dates())
    r = _run(*base, "notes-pack", "--source", "llm.cpp", "--type", "lecture-notes")
    assert r.returncode == 0, r.stderr or r.stdout
    pack_json = next(l.split(None, 1)[1].strip() for l in r.stdout.splitlines() if l.strip().startswith("json"))
    pack = json.loads(Path(pack_json).read_text())
    assert pack["source"]["series"] == ["GPU MODE"] and pack["source"]["lecture_title"] == "Bonus Lecture: CUDA C++ llm.cpp"   # the snapshot carries the series, never the retired collection
    assert "published_at" not in pack["source"]
    rows = tmp_path / "rows.jsonl"
    rows.write_text("\n".join(json.dumps(x) for x in [
        {"kind": "section", "from_i": 0, "to_i": 0, "text": "The core libraries"},
        {"kind": "claim", "from_i": 0, "to_i": 0, "text": "Thrust wraps CUB.", "lead": "Thrust"},
        {"kind": "claim", "from_i": 1, "to_i": 2, "text": "CUB is the device layer; kernels launch through it."}]) + "\n")
    assert _run(*base, "notes-ingest", "--pack", pack_json, "--rows", str(rows), "--proposer", "test").returncode == 0
    r = _run(*base, "notes-accept", "--slug", "gpu-mode-notes/bonus", "--accept-all", "--type", "lecture-notes")   # the first accept binds the type
    assert r.returncode == 0 and "accepted 3" in r.stdout, r.stderr or r.stdout
    # the dates land AFTER the accept (bind-source-dates); the render reads them live all the same
    async def bind_dates():
        async with open_graph(sdb) as sg:
            await graph_task(sg.queue, sg.graph_id, "update_node", node_id="src-lec",
                             properties={"published_at": "2024-04-27", "recorded_at": "2024-04-27", "recorded_at_precision": "around"})
    asyncio.run(bind_dates())
    r = _run(*base, "notes-render", "--slug", "gpu-mode-notes/bonus")
    assert r.returncode == 0, r.stderr or r.stdout
    staged_path = pri_dir / "staging" / "gpu-mode-notes" / "bonus" / "index.md"
    staged = staged_path.read_text()
    assert staged.startswith('---\ntitle: "GPU MODE Bonus Lecture notes: CUDA C++ llm.cpp"\ndescription: "')
    assert ("Notes on **Bonus Lecture: CUDA C++ llm.cpp**, a *GPU MODE* lecture — recorded around Apr 27, 2024, "
            "published Apr 27, 2024. The points paraphrase the talk in the order it was given; only the quotations are verbatim."
            "\n\nResources: [Watch on YouTube](https://www.youtube.com/watch?v=abc)\n:::") in staged
    assert "## The core libraries\n\n- [§](#pt-" in staged and "){.src-ref}\n" in staged and "(00:00–00:04)" not in staged
    assert "[00:00](https://www.youtube.com/watch?v=abc&t=0s){.src-ref}" in staged
    ops = [o for o in read_journal(pj) if o["verb"] == "render-notes"]
    assert len(ops) == 1 and ops[0]["args"]["facts"] == {
        "series": ["GPU MODE"], "lecture_title": "Bonus Lecture: CUDA C++ llm.cpp", "public_url": "https://www.youtube.com/watch?v=abc",
        "published_at": "2024-04-27", "recorded_at": "2024-04-27", "recorded_at_precision": "around"}
    live = _run("--graph-db-path", pdb, "read", note_id).stdout
    assert live == staged
    rep = str(tmp_path / "rep.db")
    r = _run("--graph-db-path", rep, "--journal-path", pj, "replay")
    assert r.returncode == 0, r.stderr or r.stdout
    assert _run("--graph-db-path", rep, "read", note_id).stdout == staged


def test_work_page_renderers_are_pure_and_the_type_is_data():
    # Item ebb77107 (ruling a7ca900d (2)): the work card carries the work-level content, the
    # chapters section is the TOC that is also the executive summary (a born chapter links to
    # its page and carries its synopsis; an unborn one is its title alone), the frontmatter is
    # type-owned, and the profile is graph data with the pure-notes shape.
    work = {"title": "The Learning Game", "subtitle": "Teaching Kids to Think", "author": "Ana Lorena Fábrega",
            "narrator": "Ana Lorena Fábrega"}
    units = [{"source_id": "s0", "file": 2, "kind": "front-matter", "chapter": None, "part": None, "part_title": "", "unit": "foreword", "title": "Foreword"},
             {"source_id": "s1", "file": 4, "kind": "chapter", "chapter": 1, "part": 1, "part_title": "School", "unit": "", "title": "Seven Dangerous Lessons"},
             {"source_id": "s2", "file": 5, "kind": "chapter", "chapter": 2, "part": 1, "part_title": "School", "unit": "", "title": "How Did We Get Here?"},
             {"source_id": "s3", "file": 9, "kind": "chapter", "chapter": 3, "part": 2, "part_title": "", "unit": "", "title": "Learning to Love Learning"},
             {"source_id": "s4", "file": 23, "kind": "back-matter", "chapter": None, "part": None, "part_title": "", "unit": "conclusion", "title": "Conclusion"}]
    card = render_work_card(work, units, ["The Learning Game"],
                            [{"label": "Publisher page", "href": "https://x.test/tlg"}, {"label": "Dangling", "href": ""}])
    assert card.startswith("::: {.callout-note")
    assert ("Notes on **The Learning Game**: *Teaching Kids to Think* by Ana Lorena Fábrega (read by the author) "
            "— 3 chapters in 2 parts, 5 files.") in card
    assert "Part of" not in card                                   # a book's collection IS the work
    assert "Resources: [Publisher page](https://x.test/tlg) · Dangling" in card
    series = render_work_card({"title": "Lecture 17: NCCL", "author": "GPU MODE"}, units[1:2], ["GPU MODE"])
    assert "Part of *GPU MODE*." in series and "narrated" not in series
    assert render_work_card({}, units) == ""
    born = {"s1": {"slug": "the-learning-game/ch01-notes", "synopsis": "Gatto's seven lessons."},
            "s3": {"slug": "the-learning-game/ch03-notes", "synopsis": ""}}
    body = render_work_chapters(units, born, matter="all")
    assert body.split("\n")[0] == "## Chapters"
    assert "### Front matter\n\n- Foreword\n" in body
    # the default policy: unborn front/back matter (credits, acknowledgments) is apparatus with no page — left out;
    # a front-matter unit with a born page lists like any other
    default = render_work_chapters(units, born)
    assert "Front matter" not in default and "Back matter" not in default and "Foreword" not in default
    assert "1. [Seven Dangerous Lessons]" in default and "2. How Did We Get Here?" in default
    with_foreword = render_work_chapters(units, {**born, "s0": {"slug": "the-learning-game/foreword-notes", "synopsis": ""}})
    assert "### Front matter\n\n- [Foreword](/posts/the-learning-game/foreword-notes/)\n" in with_foreword
    assert ("### Part 1 — School\n\n1. [Seven Dangerous Lessons](/posts/the-learning-game/ch01-notes/) — Gatto's seven lessons.\n"
            "2. How Did We Get Here?\n") in body
    assert "### Part 2\n\n3. [Learning to Love Learning](/posts/the-learning-game/ch03-notes/)\n" in body   # born, no synopsis yet: the link alone
    assert "### Back matter\n\n- Conclusion\n" in body
    assert render_work_chapters([], {}) == ""
    fm = '---\ntitle: "x"\ndate: 2026-09-10\naliases: [/posts/the-learning-game-book-notes/]\n---\n'
    pol = {"title": "notes-on-work", "description": "work-summary"}
    out = derive_work_frontmatter(fm, work, pol)
    assert out.startswith('---\ntitle: "Notes on *The Learning Game*"\ndescription: "Chapter-by-chapter notes on '
                          '*The Learning Game: Teaching Kids to Think* by Ana Lorena Fábrega')
    assert "date: 2026-09-10\naliases: [/posts/the-learning-game-book-notes/]\n---\n" in out
    assert derive_work_frontmatter(fm, {}, pol) == fm
    assert out == derive_work_frontmatter(out, work, pol)   # idempotent
    assert _replace_frontmatter_lines("no block", {"title": "t"}) == "no block"
    t = work_page_type()
    assert t.key == "work-page" and t.presentation_policy["frontmatter"] == pol
    assert t.presentation_policy["public"] == "expanded" and any("DERIVED_FROM" in s for s in t.production_procedure)


@pytest.mark.skipif(not _HAVE_GRAPH, reason=f"graph capability {DEFAULT_GRAPH_ID!r} not installed (CI)")
def test_cli_work_page_binds_by_edge_renders_the_toc_gates_on_published_chapters_and_replays(tmp_path):
    # Item ebb77107 (ruling a7ca900d (2)): the WORK PAGE is a born Note bound to its work by a
    # DERIVED_FROM edge to the work's Collection on the sibling; its body derives from the
    # structure map + the born chapter notes (linked, with their synopses) + the work's
    # Reference links; it emits only when every chapter page is PUBLISHED; the render op
    # journals its sibling observation so a replay reproduces the page with no sibling open.
    sib_dir, pri_dir = tmp_path / "sib", tmp_path / "pri"
    sib_dir.mkdir(); pri_dir.mkdir()
    sdb = str(sib_dir / "sib.db")
    _build_sibling(sdb)
    work = {"title": "The Learning Game", "subtitle": "Teaching Kids to Think", "author": "Ana Lorena Fábrega"}
    COL = "c011ec71-0000-5000-8000-00000000c011"   # the work's Collection on the sibling (a hex id: a foreign ref resolves by prefix)

    async def more_sibling():   # a foreword, a second chapter, the work's Collection + a work-level link
        async with open_graph(sdb) as sg:
            await graph_task(sg.queue, sg.graph_id, "update_node", node_id="src-1",
                             properties={"title": "The Learning Game — 04 - 1. Seven Dangerous Lessons",
                                         "work_structure": {"kind": "chapter", "part": 1, "chapter": 1, "file": 4,
                                                            "title": "Seven Dangerous Lessons", "work": work}})
            nodes = [{"id": "src-0", "label": "Source", "sources": [],
                      "properties": {"title": "02 - Foreword",
                                     "work_structure": {"kind": "front-matter", "unit": "foreword", "file": 2, "title": "Foreword", "work": work}}},
                     {"id": "src-2", "label": "Source", "sources": [],
                      "properties": {"title": "05 - 2. How Did We Get Here",
                                     "work_structure": {"kind": "chapter", "part": 1, "chapter": 2, "file": 5, "title": "How Did We Get Here", "work": work}}},
                     {"id": "seg-b0", "label": "Segment", "sources": [],
                      "properties": {"text": "Chapter 2. How did we get here.", "index": 0, "start_time": 0.0, "end_time": 2.0, "source_id": "src-2"}},
                     {"id": "seg-b1", "label": "Segment", "sources": [],
                      "properties": {"text": "Prussia built the modern school.", "index": 1, "start_time": 2.0, "end_time": 5.0, "source_id": "src-2"}},
                     {"id": "cor-bh", "label": "Correction", "sources": [],
                      "properties": {"correction_type": "stratum", "status": "applied", "actor": "human", "session_id": "s",
                                     "created_at": 1.0, "payload": {"operation": "classify", "source_id": "src-2",
                                                                    "category": "section-header", "segment_ids": ["seg-b0"], "start_time": 0.0}}},
                     {"id": COL, "label": "Collection", "sources": [], "properties": {"title": "The Learning Game", "status": "confirmed"}},
                     {"id": "ref-1", "label": "Reference", "sources": [],
                      "properties": {"source_id": COL, "label": "Publisher page", "url": "https://x.test/tlg", "notes_slug": "", "role": "publisher-page"}}]
            edges = [make_edge(s, COL, "PART_OF") for s in ("src-0", "src-1", "src-2")] + [make_edge(COL, "ref-1", "HAS_REFERENCE")]
            # src-2's spine hangs under a rendition, as the correction core's spine read expects
            nodes += [{"id": "aseg-2", "label": "AudioSegment", "sources": [], "properties": {"index": 0}},
                      {"id": "rend-2", "label": "AudioRendition", "sources": [],
                       "properties": {"chain": [], "is_raw": True, "preprocessing": None}}]
            for n in nodes:
                if n["label"] == "Segment":
                    n["properties"]["rendition_id"] = "rend-2"
            edges += [make_edge("aseg-2", "src-2", "PART_OF"), make_edge("rend-2", "aseg-2", "DERIVED_FROM"),
                      make_edge("seg-b0", "rend-2", "PART_OF"), make_edge("seg-b1", "rend-2", "PART_OF")]
            await extend_graph(sg.queue, sg.graph_id, nodes, edges)
    asyncio.run(more_sibling())
    pdb, pj = str(pri_dir / "pri.db"), str(pri_dir / "writes.jsonl")
    staging, site = pri_dir / "staging", pri_dir / "site"
    (pri_dir / "graph.config.json").write_text(json.dumps(
        {"notes_profile": "quarto_post", "emit_root": str(staging / "posts"), "website_root": str(site),
         "sibling_graphs": {"tx": sdb}}))
    base = ("--graph-db-path", pdb, "--journal-path", pj, "--source-journal-path", str(pri_dir / "source.jsonl"))
    for key in ("pure-notes", "work-page"):
        r = _run(*base, "notes-type", key)
        assert r.returncode == 0 and "created" in r.stdout, r.stderr or r.stdout

    def born(slug, source, text, synopsis):   # a typed chapter page: born draft, a claim + a synopsis accepted, rendered
        post = f"---\ntitle: \"{slug}\"\ndate: 2026-09-10\ncategories: [book, notes]\n---\n\nPreamble.\n"
        r = _run(*base, "new-note", "--slug", slug, "--content", post)
        assert r.returncode == 0, r.stderr or r.stdout
        note_id = [o for o in read_journal(pj) if o["verb"] == "assert"][-1]["args"]["subject"]
        r = _run(*base, "notes-pack", "--source", source)
        assert r.returncode == 0, r.stderr or r.stdout
        pack_json = next(l.split(None, 1)[1].strip() for l in r.stdout.splitlines() if l.strip().startswith("json"))
        n = len(json.loads(Path(pack_json).read_text())["segments"])
        rows = tmp_path / (slug.replace("/", "_") + ".jsonl")
        rows.write_text(json.dumps({"kind": "claim", "from_i": 0, "to_i": 0, "text": text}) + "\n"
                        + json.dumps({"kind": "synopsis", "from_i": 0, "to_i": n - 1, "text": synopsis}) + "\n")
        r = _run(*base, "notes-ingest", "--pack", pack_json, "--rows", str(rows), "--proposer", "test")
        assert r.returncode == 0, r.stderr or r.stdout
        r = _run(*base, "notes-accept", "--slug", slug, "--accept-all")
        assert r.returncode == 0 and "accepted 2" in r.stdout, r.stderr or r.stdout
        r = _run(*base, "notes-render", "--slug", slug)
        assert r.returncode == 0, r.stderr or r.stdout
        return note_id

    ch1 = born("the-learning-game/ch01", "Seven Dangerous", "Gatto quit teaching in 1991.", "Gatto's seven lessons diagnose school.")
    # the work page: born draft and typed; a render before the edge is refused with the recipe
    post = ('---\ntitle: "x"\ndate: 2026-09-10\ncategories: [book, notes]\n'
            'aliases: [/posts/the-learning-game-book-notes/]\n---\n\nAuthored preamble.\n')
    r = _run(*base, "new-note", "--slug", "the-learning-game", "--content", post)
    assert r.returncode == 0, r.stderr or r.stdout
    wp = [o for o in read_journal(pj) if o["verb"] == "assert"][-1]["args"]["subject"]
    r = _run(*base, "assert", wp, "deliverable_type", "work-page")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run(*base, "notes-render", "--slug", "the-learning-game")
    assert r.returncode != 0 and "not bound" in (r.stdout + r.stderr)
    r = _run(*base, "link", wp, "DERIVED_FROM", f"tx:{COL[:8]}")   # a PREFIX resolves sibling-side
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run(*base, "notes-render", "--slug", "the-learning-game")
    assert r.returncode == 0 and "work page" in r.stdout and "1 of 2 chapter(s) born" in r.stdout, r.stderr or r.stdout
    page = staging / "posts" / "the-learning-game" / "index.md"
    text = page.read_text()
    assert text.startswith('---\ntitle: "Notes on *The Learning Game*"\ndescription: "Chapter-by-chapter notes on '
                           '*The Learning Game: Teaching Kids to Think* by Ana Lorena Fábrega'), text[:300]
    assert "aliases: [/posts/the-learning-game-book-notes/]" in text and "Authored preamble." in text
    assert "Notes on **The Learning Game**: *Teaching Kids to Think* by Ana Lorena Fábrega — 2 chapters, 3 files." in text
    assert "Resources: [Publisher page](https://x.test/tlg)" in text
    assert "Front matter" not in text and "Foreword" not in text   # unborn front matter is apparatus: left out (policy `matter`)
    assert ("1. [Seven Dangerous Lessons](/posts/the-learning-game/ch01/) — Gatto's seven lessons diagnose school.\n"
            "2. How Did We Get Here\n") in text, text
    # the promotion readout names the work page beside the work it belongs to
    r = _run(*base, "notes-promotion")
    assert r.returncode == 0 and "work page `the-learning-game`" in r.stdout and "1 of 2" in r.stdout, r.stderr or r.stdout
    # the second chapter born -> a re-render links it (a new op, not a dedup); the condition now holds
    ch2 = born("the-learning-game/ch02", "How Did We", "Prussia built the modern school.", "School descends from Prussia.")
    r = _run(*base, "notes-render", "--slug", "the-learning-game")
    assert r.returncode == 0 and "2 of 2 chapter(s) born" in r.stdout, r.stderr or r.stdout
    text = page.read_text()
    assert "2. [How Did We Get Here](/posts/the-learning-game/ch02/) — School descends from Prussia.\n" in text
    assert sum(1 for o in read_journal(pj) if o["verb"] == "render-work-page") == 2
    # the work page's own emit: published, the work READY, but the chapter pages still draft -> held (the stricter gate)
    r = _run(*base, "assert", wp, "publish_state", "published")
    assert r.returncode == 0, r.stderr or r.stdout
    landed = site / "posts" / "the-learning-game" / "index.md"
    r = _run(*base, "emit-post", wp)
    assert r.returncode != 0 and "0 of 2 chapter page(s) published" in (r.stdout + r.stderr) and not landed.exists()
    for ch in (ch1, ch2):
        r = _run(*base, "assert", ch, "publish_state", "published")
        assert r.returncode == 0, r.stderr or r.stdout
    r = _run(*base, "emit-post", wp)
    assert r.returncode == 0 and landed.exists() and landed.read_text() == text, r.stderr or r.stdout
    # replay: a fresh db from the journal alone carries the same page — the render replays from
    # its journaled observation (the sibling is opened only by emit's gate, never by replay)
    rep = str(tmp_path / "rep.db")
    (tmp_path / "graph.config.json").write_text(json.dumps({"website_root": str(tmp_path / "rep-site"), "sibling_graphs": {"tx": sdb}}))
    r = _run("--graph-db-path", rep, "--journal-path", pj, "replay")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run("--graph-db-path", rep, "emit-post", wp, "--website-root", str(tmp_path / "rep-site"))
    assert r.returncode == 0, r.stderr or r.stdout
    assert (tmp_path / "rep-site" / "posts" / "the-learning-game" / "index.md").read_text() == text


def _lecture_points():
    """A lecture in miniature (work item e370e5db; rulings bc62c727, ba341c72): two presenters, a
    host relaying a chat question, an anonymous audience voice, two synthesized sections, a glossary
    term the ASR mangled, an unverified code identifier, and an answer leaning back on the body."""
    unit = {"graph": "tx", "source_id": "src-lec", "title": "Bonus Lecture", "public_url": "https://www.youtube.com/watch?v=abc",
            "speaker_roster": [{"speaker": "Georgii", "name": "Georgii", "role": ""},
                               {"speaker": "SPEAKER_07", "name": "", "role": ""},
                               {"speaker": "Mark", "name": "Mark", "role": "host"},
                               {"speaker": "SPEAKER_09", "name": "", "role": "audience member"},
                               {"speaker": "SPEAKER_11", "name": "", "role": ""}]}

    def pt(key, kind, text, start, speaker="", **kw):
        return {"id": key, "key": key, "kind": kind, "text": text, "heading": "", "heading_index": 0,
                "segment_ids": [f"s{int(start)}"], "start_time": float(start), "end_time": float(start) + 4.0,
                "ordinal": int(start), "speaker": speaker, "unit": unit, **kw}
    return [
        pt("c1c1c1c1-0", "claim", "Thrust wraps CUB.", 10, "Georgii", lead="Thrust"),
        pt("5ec00001-0", "section", "The core libraries", 10),                     # sorts BEFORE the point it opens
        pt("c2c2c2c2-0", "claim", "CUB is the device layer.", 20, "Georgii"),
        pt("90550001-0", "glossary", "NCCL is the collective library.", 25, "Georgii", lead="NCCL", data={"asr_form": "nickel"}),
        pt("c3c3c3c3-0", "claim", "Kernels launch through it.", 30, "SPEAKER_07"),
        pt("5ec00002-0", "section", "Questions from the chat", 40),
        pt("99990001-0", "question", "Does it support AMD?", 40, "Mark", data={"relayed": "chat", "asker": "Chris"}),
        pt("a1a1a1a1-0", "claim", "Only through HIP.", 44, "Georgii", parent_key="99990001-0",
           refers_to=["c1c1c1c1-0", "c2c2c2c2-0", "deadbeef-0"]),
        pt("c0de0001-0", "code", "cub::DeviceReduce sums on device.", 50, "Georgii", lead="cub::DeviceReduce",
           data={"unverified": True}),
        pt("99990002-0", "question", "Is it header-only?", 60, "SPEAKER_09"),
        pt("a2a2a2a2-0", "claim", "Yes.", 64, "SPEAKER_11", parent_key="99990002-0"),
        pt("90550002-0", "glossary", "The CUDA C++ Core Libraries.", 70, "Georgii", lead="CCCL"),
    ]


def test_render_lecture_sections_speakers_questions_glossary_and_code():
    """Work item e370e5db on FIXTURE points (the Bonus lecture has none accepted yet — ruling
    bc62c727 (C)): synthesized sections group by derived membership; a speaker label prints only
    where the speaker changes (and again under each heading); a question leads with who asked —
    a relayed one names its asker and its relay; an answer's back-links reach only the points
    that STAND; a code identifier is inline code with its unverified mark; the glossary is ONE
    alphabetical closer and never renders in the body; no cluster id is ever printed."""
    out = render_points(_lecture_points())
    assert out == render_points(_lecture_points())                                                   # deterministic
    body, closer = out.split("## Glossary\n")
    assert out.startswith("## The core libraries\n\n- *Georgii:* **Thrust** wraps CUB. [(00:10–00:14)]")  # the section opens ABOVE the point it anchors at
    assert "\n- CUB is the device layer. [(00:20" in body                                             # same speaker: no label
    assert "\n- *Speaker 1:* Kernels launch through it." in body                                      # an unnamed, role-less voice: numbered by first appearance
    assert "SPEAKER_" not in out                                                                      # a diarization cluster id is never printed
    assert "## Questions from the chat\n\n- **Q** (*Chris*, relayed by *Mark*): Does it support AMD?" in body
    assert (f"\n  - *Georgii:* Only through HIP. (see [§ Thrust](#pt-c1c1c1c1), [§ 00:20](#pt-c2c2c2c2)) "
            f"[(00:44–00:48)](https://www.youtube.com/watch?v=abc&t=44s){{.src-ref}} {_glyph('a1a1a1a1')}\n") in body   # the unaccepted target renders nothing
    assert "deadbeef" not in out
    assert "(see [§ Thrust](#pt-c1c1c1c1), [§ CUB is the device…](#pt-c2c2c2c2))" in render_points(_lecture_points(), timestamps="never")   # no lead, no rendered time: the opening words
    assert "\n- `cub::DeviceReduce` sums on device. *(unverified)*" in body                           # same speaker as the answer: no label
    assert "\n- **Q** (*Audience member*): Is it header-only?" in body and "\n  - *Speaker 2:* Yes." in body
    assert "NCCL" not in body and "CCCL" not in body                                                  # glossary points live in the closer only
    assert closer.index("**CCCL** — The CUDA C++ Core Libraries.") < closer.index("**NCCL** (heard as “nickel”) — is the collective library.")
    assert _glyph("90550001") in closer and out.count("{#pt-90550001") == 1                           # ONE anchor per point
    outline = render_points(_lecture_points(), rendering="outline")
    assert "**The core libraries**\n\n- *Georgii:* **Thrust** wraps CUB.\n- CUB is the device layer.\n- *Speaker 1:*" in outline
    assert "- **Q** (*Chris*, relayed by *Mark*): Does it support AMD?\n  - *Georgii:* Only through HIP.\n" in outline and "(see " not in outline
    assert outline.rstrip().endswith("**Glossary**\n\n- **CCCL** — The CUDA C++ Core Libraries.\n- **NCCL** (heard as “nickel”) — is the collective library.")


def test_render_lecture_retracted_section_falls_into_the_previous_and_relayed_channel():
    """Ruling bc62c727 (A3): retracting a section drops its points into the section before it —
    membership is derived, nothing is re-stamped. A relayed question with no named asker leads
    with the channel; with neither, it still says it was relayed. A roster-less (legacy) point
    prints its own speaker string."""
    pts = [p for p in _lecture_points() if p["key"] != "5ec00002-0"]
    out = render_points(pts)
    assert "Questions from the chat" not in out and out.count("\n## ") == 1 and out.startswith("## The core libraries")   # only the Glossary heading follows
    assert out.index("Kernels launch through it.") < out.index("**Q** (*Chris*")
    q = next(p for p in pts if p["key"] == "99990001-0")
    q["data"] = {"relayed": "chat"}
    assert "- **Q** (chat, relayed by *Mark*): Does it support AMD?" in render_points(pts)
    q["data"] = {"relayed": True}
    assert "- **Q** (relayed by *Mark*): Does it support AMD?" in render_points(pts)
    bare = [{**p, "unit": {}} for p in pts]
    assert "- *SPEAKER_07:* Kernels launch through it." in render_points(bare)                        # no roster: the string stands (pre-roster points carry names)
    assert derived_description(_lecture_points()).startswith("Notes: The core libraries · Questions from the chat.")


def test_render_style_start_spans_head_anchors_and_intra_section_back_links_left_out():
    """Ruling de9c4cda + read 9d301b5a on the lecture fixture: with the lecture type's render style
    the span is the START time alone as a quiet `src-ref` link and the permalink sits at the HEAD
    of the item (the anchor a wrapped point scrolls to is its first line); a back-link whose target
    sits in the point's own section renders nothing while one into another section stands; the
    glossary closer and a quotation follow the same style; the book defaults are byte-identical
    to a call with no style at all."""
    pts = _lecture_points()
    pts.append({**pts[0], "id": "b1b1b1b1-0", "key": "b1b1b1b1-0", "kind": "quotation", "text": "Thrust is CUB with manners.",
                "attribution": "", "start_time": 12.0, "end_time": 15.0, "ordinal": 12, "lead": ""})
    pts.append({**pts[0], "id": "5ec00003-0", "key": "5ec00003-0", "kind": "section", "text": "The device layer", "speaker": "",
                "start_time": 20.0, "end_time": 24.0, "ordinal": 20, "lead": ""})
    plain = render_points(pts)
    assert render_points(pts, style={}) == plain and render_points(pts, style={"span": "range", "anchor": "tail"}) == plain
    assert "[(00:10–00:14)](https://www.youtube.com/watch?v=abc&t=10s){.src-ref} [§](#pt-c1c1c1c1){#pt-c1c1c1c1 .pt-anchor}" in plain   # the default: range at the tail, quiet class
    styled = render_points(pts, style={"span": "start", "anchor": "head"})
    assert ("## The core libraries\n\n- [§](#pt-c1c1c1c1){#pt-c1c1c1c1 .pt-anchor} *Georgii:* **Thrust** wraps CUB. "
            "[00:10](https://www.youtube.com/watch?v=abc&t=10s){.src-ref}\n") in styled
    assert "(00:10–00:14)" not in styled and styled.count("{#pt-c1c1c1c1") == 1
    # the answer (in 'Questions from the chat') leans on c1 (section 1) and c2 (moved into 'The device layer' by
    # the new mark) — both stand — and on the question right above it, in its OWN section: left out
    a1 = next(p for p in pts if p["key"] == "a1a1a1a1-0")
    a1["refers_to"] = ["c1c1c1c1-0", "c2c2c2c2-0", "99990001-0"]
    for out in (render_points(pts, style={"span": "start", "anchor": "head"}), render_points(pts)):
        line = next(l for l in out.splitlines() if "Only through HIP" in l)
        assert "(see [§ Thrust](#pt-c1c1c1c1), [§ 00:20](#pt-c2c2c2c2))" in line and "pt-99990001" not in line
    styled = render_points(pts, style={"span": "start", "anchor": "head"})
    # the quotation: permalink at the head of the quote line, the span on the attribution line
    assert "> [§](#pt-b1b1b1b1){#pt-b1b1b1b1 .pt-anchor} Thrust is CUB with manners.\n> [00:12](https://www.youtube.com/watch?v=abc&t=12s){.src-ref}\n" in styled
    closer = styled.split("## Glossary\n")[1]
    assert closer.startswith("\n- [§](#pt-90550002){#pt-90550002 .pt-anchor} **CCCL** — The CUDA C++ Core Libraries. [01:10]")
    outline = render_points(pts, rendering="outline", style={"span": "start", "anchor": "head"})
    assert "src-ref" not in outline and ".pt-anchor" not in outline                       # the scan view carries neither


def test_lecture_row_contract_section_anchor_relayed_question_and_prefix_leads():
    """Ruling bc62c727 at ingest: a `section` row is an anchor (one line, a title, nothing else),
    nothing nests under it or refers to it, and it carries no speaker; `relayed` / `asker` belong
    to a question and ride its data; glossary and code leads may be terms the text lacks; coverage
    and the overlap review ignore sections."""
    segs = [{"i": i, "id": f"s{i}", "index": i, "start": float(i), "end": float(i) + 1.0, "text": f"line {i}", "h": 0,
             "speaker": "Mark" if i == 2 else "Georgii"} for i in range(5)]
    pack = {"pack_id": "npack_x", "digest": "sha256:x", "segments": segs, "headers": []}
    rows = validate_point_rows([
        {"kind": "section", "from_i": 0, "to_i": 0, "text": "Opening"},
        {"kind": "claim", "from_i": 0, "to_i": 1, "text": "A claim."},
        {"kind": "question", "from_i": 2, "to_i": 2, "text": "Why?", "asker": "Chris"},
        {"kind": "question", "from_i": 2, "to_i": 2, "text": "How?", "relayed": "Chat"},
        {"kind": "glossary", "from_i": 3, "to_i": 3, "text": "the collective library", "lead": "NCCL", "asr_form": "nickel"},
        {"kind": "code", "from_i": 4, "to_i": 4, "text": "sums on device", "lead": "cub::DeviceReduce", "unverified": True}], pack)
    assert rows[2]["data"] == {"relayed": True, "asker": "Chris"} and rows[3]["data"] == {"relayed": "chat"}
    assert rows[4]["lead"] == "NCCL" and rows[5]["lead"] == "cub::DeviceReduce"
    props = proposals_from_point_rows(rows, pack)
    sec = next(p for p in props if p["kind"] == "section")
    assert sec["speaker"] == "" and sec["segment_ids"] == ["s0"]
    for bad, why in [
        ({"kind": "section", "from_i": 0, "to_i": 1, "text": "T"}, "anchor, not a run"),
        ({"kind": "section", "from_i": 0, "to_i": 0, "text": "T", "lead": "T"}, "nothing else"),
        ({"kind": "claim", "from_i": 1, "to_i": 1, "text": "x", "relayed": True}, "belong to a `question`"),
        ({"kind": "question", "from_i": 2, "to_i": 2, "text": "x", "relayed": "email"}, "relayed takes true or the channel"),
    ]:
        with pytest.raises(ValueError, match=why):
            validate_point_rows([bad], pack)
    with pytest.raises(ValueError, match="is a section"):
        validate_point_rows([{"kind": "section", "from_i": 0, "to_i": 0, "text": "T"},
                             {"kind": "claim", "from_i": 0, "to_i": 0, "text": "x", "parent": 0}], pack)
    with pytest.raises(ValueError, match="is the section"):
        validate_point_rows([{"kind": "section", "from_i": 0, "to_i": 0, "text": "T"},
                             {"kind": "claim", "from_i": 0, "to_i": 0, "text": "x", "refers_to": [0]}], pack)
    pts = [{"id": "a", "kind": "section", "segment_ids": ["s0"]}, {"id": "b", "kind": "claim", "segment_ids": ["s0", "s1"]}]
    assert overlapping_points(pts) == []
    assert [g["from_i"] for g in coverage_gaps(segs, pts[:1])] == [0]                                  # a section alone covers nothing
    assert speaker_labels([{"speaker": "A", "name": "Ann"}, {"speaker": "c1", "role": "audience member"},
                           {"speaker": "c2"}, {"speaker": "c3"}]) == {"A": "Ann", "c1": "Audience member", "c2": "Speaker 1", "c3": "Speaker 2"}
    with_roster = {**pack, "source": {"source_id": "x", "speaker_roster": [{"speaker": "Georgii", "name": "Georgii", "role": ""}]}}
    assert pack_digest(with_roster) == pack_digest({**pack, "source": {"source_id": "x"}})             # the roster is how a label prints, not what was read


def test_standing_detection_ignores_fold_in_origins():
    """A compound folded into several finer survivors copies its origins onto EACH of them (the fold's
    every-survivor rule); those copies are judgement provenance, not authorship, so two other drafters'
    rows still flag as cross-origin (the nine-cell Bonus run of 2026-09-22: 24 standing versions of one
    point passed as 'one drafter's'). A shared `matched` origin — a drafter's own row — still exempts."""
    def row(pid, seg, cell, extra_origins=()):
        a, b = int(seg[0][1:]), int(seg[-1][1:])
        return {"proposal_id": pid, "kind": "claim", "text": pid, "lead": "", "segment_ids": seg, "from_i": a, "to_i": b,
                "start_time": float(2 * a), "end_time": float(2 * b + 1), "heading_index": 0, "parent_key": "",
                "refers_to": [], "speaker": "Alice",
                "origins": [{"set_id": "s", "proposal_id": pid, "cell": cell, "how": "added", "text": pid}, *extra_origins]}
    fold_in = [{"set_id": "s", "proposal_id": "C", "cell": c, "how": "contains", "text": "C"} for c in ("blind/opus", "undivided/fable")]
    rows = [row("P", ["s1", "s2"], "blind/fable", fold_in), row("Q", ["s2"], "sequential/opus", fold_in),
            row("R", ["s3", "s4"], "blind/fable", [{"set_id": "s", "proposal_id": "R2", "cell": "sequential/opus", "how": "matched", "text": "R"}]),
            row("S", ["s4"], "sequential/opus")]
    assert [(q["a"]["text"], q["b"]["text"]) for q in unjudged_pairs(rows)] == [("P", "Q")]
    pts = [dict(r, id=r["proposal_id"], key=r["proposal_id"]) for r in rows]
    flags = {(p["a"]["key"], p["b"]["key"]): (p["cross_origin"], p["flagged"]) for p in overlapping_points(pts)}
    assert flags == {("P", "Q"): (True, True), ("R", "S"): (False, False)}
