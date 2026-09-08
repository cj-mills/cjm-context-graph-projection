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

from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.journal import read_journal
from cjm_context_graph_projection.purenotes import (build_notes_pack, choose_spine, coverage_gaps,
                                                    overlapping_points, proposals_from_point_rows,
                                                    pure_notes_type, render_notes_pack, render_points,
                                                    validate_point_rows)
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
        {"id": "c-app1", "correction_type": "stratum", "payload": {"category": "apparatus", "segment_ids": ["s0"], "start_time": 0.0}},
        {"id": "c-tan", "correction_type": "stratum", "payload": {"category": "tangent", "segment_ids": ["s2"], "start_time": 5.0}},
        {"id": "c-app2", "correction_type": "stratum", "payload": {"category": "apparatus", "segment_ids": ["s3"], "start_time": 6.0}},
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
    for bad, msg in ((
            [{"kind": "Claim!", "from_i": 0, "to_i": 0, "text": "x"}], "kebab-case"),
            ([{"kind": "claim", "from_i": 0, "to_i": 9, "text": "x"}], "outside the pack"),
            ([{"kind": "claim", "from_i": 0, "to_i": 1, "text": "x"}], "crosses a header"),
            ([{"kind": "claim", "from_i": 0, "to_i": 0, "text": "  "}], "empty"),
            ([{"kind": "comparison", "from_i": 0, "to_i": 0, "text": "x"}], "columns"),
            ([{"kind": "sequence", "from_i": 0, "to_i": 0, "text": "x", "data": {}}], "items")):
        with pytest.raises(ValueError) as ei:
            validate_point_rows(bad, pack)
        assert msg in str(ei.value)


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


def test_render_points_outline_and_expanded_are_deterministic_and_typed():
    both = render_points(_points())
    assert both == render_points(_points())
    assert both.startswith("## At a glance\n\n**Chapter 1**\n\n- [**Gatto** — Quit in 1991.](#pt-aaaaaaaa)\n")
    assert "- [**Gatto**: “I teach confusion.”](#pt-bbbbbbbb)" in both            # a quotation scans as who + opening words
    assert "## Chapter 1\n\n- []{#pt-aaaaaaaa} **Gatto** — Quit in 1991. (00:02–00:05)\n" in both
    assert "[]{#pt-bbbbbbbb}\n\n> I teach confusion.\n> — Gatto (00:07–00:09)\n" in both      # quotation block
    assert "1. []{#pt-cccccccc} Assess models. (00:09–00:10)\n2. []{#pt-dddddddd} Copy them. (00:10–00:12)\n" in both  # steps = ONE list
    assert "| Period | Nation |\n|---|---|\n| 1870s | France |\n| later | Germany |" in both          # comparison = table
    outline = render_points(_points(), rendering="outline")
    assert "## At a glance" in outline and "#pt-" not in outline and "(00:" not in outline      # no anchors/timestamps in a bare outline
    expanded = render_points(_points(), rendering="expanded")
    assert "At a glance" not in expanded and expanded.startswith("## Chapter 1")
    assert "**" not in expanded.replace("**Gatto**", "").replace("**Advisors**", "")            # lead term is the ONLY emphasis
    assert render_points([]) == "## At a glance\n"


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


# ---------------------------------------------------------------- the CLI chain

def _build_sibling(sdb: str):
    """A FAKE transcription graph: one Source with a structure map, a spine of Segments,
    strata Corrections (apparatus header / tangent / quotation) over them."""
    async def go():
        async with open_graph(sdb) as sg:
            nodes = [{"id": "src-1", "label": "Source", "sources": [],
                      "properties": {"title": "The Learning Game — 04 - 1. Seven Dangerous Lessons",
                                     "work_structure": {"kind": "chapter", "part": 1, "chapter": 1,
                                                        "title": "Seven Dangerous Lessons"}}}]
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
            for cid, cat, sids, st in (("cor-h1", "apparatus", ["seg-0"], 0.0), ("cor-t", "tangent", ["seg-2"], 5.0),
                                       ("cor-h2", "apparatus", ["seg-3"], 6.0), ("cor-q", "quotation", ["seg-4", "seg-5"], 7.0)):
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
    assert "[H] Lesson 1. Confusion." in pack_json.with_suffix(".md").read_text()

    # (4) a proposer's rows -> a proposal set; a bad row refuses loudly
    rows = tmp_path / "rows.jsonl"
    rows.write_text("\n".join(json.dumps(x) for x in [
        {"kind": "claim", "from_i": 0, "to_i": 0, "text": "Quit teaching in 1991.", "lead": "Gatto"},
        {"kind": "quotation", "from_i": 1, "to_i": 2, "text": "I teach confusion.", "attribution": "Gatto"},
        {"kind": "claim", "from_i": 3, "to_i": 3, "text": "Subjects taught in isolation."},
        {"kind": "claim", "from_i": 3, "to_i": 4, "text": "Isolation → no coherent picture.", "lead": "Consequence"},
    ]) + "\n")
    r = _run(*base, "notes-ingest", "--pack", str(pack_json), "--rows", str(rows), "--proposer", "test")
    assert r.returncode == 0 and "4 point(s)" in r.stdout, r.stderr or r.stdout
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"kind": "claim", "from_i": 0, "to_i": 1, "text": "x"}) + "\n")
    r = _run(*base, "notes-ingest", "--pack", str(pack_json), "--rows", str(bad), "--proposer", "test")
    assert r.returncode == 1 and "crosses a header" in r.stderr

    # (5) accept: list first, then all — Points + References + edges, one journaled op each,
    #     the deliverable_type fact asserted on first accept
    r = _run(*base, "notes-accept", "--slug", "the-learning-game/ch01")
    assert r.returncode == 0 and "4 pending" in r.stdout and "[quotation]" in r.stdout, r.stderr or r.stdout
    r = _run(*base, "notes-accept", "--slug", "the-learning-game/ch01", "--accept-all")
    assert r.returncode == 0 and "accepted 4" in r.stdout and "deliverable_type asserted: `pure-notes`" in r.stdout, r.stderr or r.stdout
    ops = [o for o in read_journal(pj) if o["verb"] == "accept-point"]
    assert len(ops) == 4
    assert all(len(o["args"]["observations"]) == len(o["args"]["point"]["segment_ids"]) for o in ops)
    assert ops[1]["args"]["point"]["kind"] == "quotation" and ops[1]["args"]["observations"][0]["graph"] == "tx"
    assert ops[0]["args"]["point"]["unit"]["source_id"] == "src-1" and ops[0]["args"]["point"]["heading"] == "Chapter 1. Seven dangerous lessons."
    r = _run("--graph-db-path", pdb, "--format", "agent", "list", "--label", "Point")
    points = json.loads(r.stdout)
    assert points.get("total", len(points.get("items", []))) == 4 or len(points.get("nodes", [])) == 4
    # re-accepting is a no-op that journals nothing
    r = _run(*base, "notes-accept", "--slug", "the-learning-game/ch01", "--accept-all")
    assert "0 pending" in r.stdout or "accepted 0" in r.stdout
    assert len([o for o in read_journal(pj) if o["verb"] == "accept-point"]) == 4

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

    # (7) render: Sections DERIVED from the Points; frontmatter + preamble kept; staging file written
    r = _run(*base, "notes-render", "--slug", "the-learning-game/ch01")
    assert r.returncode == 0 and "from 4 point(s)" in r.stdout, r.stderr or r.stdout
    staged = (pri_dir / "staging" / "the-learning-game" / "ch01" / "index.md").read_text()
    assert staged.startswith("---\ntitle: \"The Learning Game, Chapter 1\"") and "Pure notes born on the graph." in staged
    assert "## At a glance" in staged and "## Chapter 1. Seven dangerous lessons" in staged and "## Lesson 1. Confusion" in staged
    assert "> I teach confusion.\n> — Gatto (00:07–00:09)" in staged and f"#pt-{quote_pid[:8]}" in staged
    r = _run("--graph-db-path", pdb, "read", note_id)
    assert r.stdout == staged                                     # the graph reconstruction IS the file
    live_text = r.stdout
    assert len([o for o in read_journal(pj) if o["verb"] == "render-notes"]) == 1
    # a re-render with nothing changed lands no new op (identical args dedup)
    _run(*base, "notes-render", "--slug", "the-learning-game/ch01")
    assert len([o for o in read_journal(pj) if o["verb"] == "render-notes"]) == 1

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
    assert "from 3 point(s)" in r.stdout
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
    assert n == 3
    r = _run("--graph-db-path", rdb, "--format", "agent", "locate", "pure-notes")
    assert any(m.get("label") == "DeliverableType" for m in json.loads(r.stdout)["matches"])
    assert live_text != staged2   # the retract really changed the body (sanity on the equality checks above)
