"""The OUTLINE PASS with parents (rulings bc62c727 (A) and 776c13d3 (a); work item 81d6e669 (3)):
section rows nest by naming a parent section, the set-mode apply orders a parent before the
child that shares its anchor, the render derives heading depth from the chain, and the
draft mode plans — then lands, journaled and replay-faithful — the difference between the
reader's whole outline and the standing one."""
import json
from pathlib import Path

import pytest
from cjm_context_graph_primitives.journal import read_journal
from cjm_context_graph_projection.notes_outline import apply_outline, outline_of, plan_outline, render_outline_brief
from cjm_context_graph_projection.purenotes import (build_notes_pack, group_points, points_as_proposals,
                                                    proposals_from_point_rows, render_points, validate_point_rows)
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID
from cjm_dev_graph_schema.identity import note_node_id
from test_purenotes import _HAVE_GRAPH, _build_sibling, _run


def _pack_and_points():
    """A ten-line unit and five proposal rows (one nested): the set the outline pass reads."""
    segs = [{"id": f"s{k}", "index": k, "text": f"line {k}", "start": float(2 * k), "end": float(2 * k + 1)} for k in range(10)]
    unit = {"source": {"source_id": "lec", "title": "Lecture"}, "segments": segs, "strata": [],
            "speakers": {f"s{k}": "Alice" for k in range(10)}}
    pack = build_notes_pack(unit, {"key": "lecture-notes", "information_policy": {"stratum_roles": {}}})
    props = proposals_from_point_rows(validate_point_rows([
        {"kind": "claim", "from_i": 0, "to_i": 1, "text": "Kernels launch twice"},
        {"kind": "example", "from_i": 1, "to_i": 1, "text": "the warm-up launch", "parent": 0},
        {"kind": "claim", "from_i": 4, "to_i": 5, "text": "Streams do not share"},
        {"kind": "question", "from_i": 7, "to_i": 8, "text": "Why per rank?"},
        {"kind": "claim", "from_i": 9, "to_i": 9, "text": "Ranks pin a stream"}], pack), pack)
    return pack, props


def _pt(key, kind, text, start, parent_key=""):
    """A standing point in load_points shape (the unit empty: no roster, no spans)."""
    return {"id": f"id-{key}", "key": key, "kind": kind, "text": text, "lead": "", "heading": "", "heading_index": 0,
            "segment_ids": [f"s{int(start)}"], "start_time": float(start), "end_time": float(start) + 1.0,
            "ordinal": int(start), "speaker": "", "unit": {}, "parent_key": parent_key}


def test_outline_rows_nest_by_parent_and_the_set_apply_orders_a_parent_before_its_first_child():
    pack, props = _pack_and_points()
    brief = render_outline_brief(props, pack, set_id="x")
    assert '{"section": "<title>", "first": "p017", "parent": "<the title of an earlier section row>"}' in brief
    assert "Never force nesting" in brief and "## The standing outline" not in brief and brief.startswith("# Outline pass — set `x`")
    res = apply_outline(props, [
        {"section": "Launching work", "first": "p001"},
        {"section": "Kernel launches", "first": "p001", "parent": "Launching work"},       # shares its parent's anchor
        {"section": "Streams per rank", "first": "p003", "parent": "Launching work"},
        {"section": "Questions", "first": "p004"},
        {"synopsis": "Launches warm the cache; streams stay per rank."}], pack)
    rows = res["proposals"]
    assert [(r["kind"], r["text"]) for r in rows] == [
        ("section", "Launching work"), ("section", "Kernel launches"), ("claim", "Kernels launch twice"),
        ("example", "the warm-up launch"), ("section", "Streams per rank"), ("claim", "Streams do not share"),
        ("section", "Questions"), ("question", "Why per rank?"), ("claim", "Ranks pin a stream"),
        ("synopsis", "Launches warm the cache; streams stay per rank.")]
    assert rows[1]["parent_key"] == rows[0]["proposal_id"] == rows[4]["parent_key"] and not rows[0]["parent_key"] and not rows[6]["parent_key"]
    assert rows[0]["from_i"] == rows[1]["from_i"] == 0 and rows[4]["from_i"] == 4                    # anchors at the first point's first line
    assert res["stats"] == {"sections": 4, "nested": 2, "depth": 1, "points": 5, "smallest": 1, "largest": 2, "synopsis_words": 8}
    pts = [{**r, "key": r["proposal_id"], "ordinal": r["from_i"]} for r in rows]
    page = render_points(pts, timestamps="never")
    assert page.startswith("## Launching work\n\n### Kernel launches\n\n- *Alice:* Kernels launch twice")   # the parent heading stands over its first child
    assert "\n\n### Streams per rank\n\n- *Alice:* Streams do not share" in page and "\n\n## Questions\n\n- " in page
    outline = render_points(pts, rendering="outline")
    assert "**Launching work**\n\n**› Kernel launches**\n\n- *Alice:* Kernels launch twice" in outline and "**Questions**\n\n- **Q** (*Alice*): Why per rank?" in outline
    deeper = apply_outline(props, [{"section": "A", "first": "p001"}, {"section": "B", "first": "p001", "parent": "A"},
                                   {"section": "C", "first": "p001", "parent": "B"}, {"synopsis": "s"}], pack)
    assert deeper["stats"]["depth"] == 2 and render_points([{**r, "key": r["proposal_id"], "ordinal": r["from_i"]} for r in deeper["proposals"]],
                                                            timestamps="never").startswith("## A\n\n### B\n\n#### C\n\n- *Alice:* Kernels")
    for bad, why in ([[{"section": "A", "first": "p001", "parent": "B"}, {"section": "B", "first": "p003"}, {"synopsis": "s"}], "EARLIER section row"],
                     [[{"section": "A", "first": "p001"}, {"section": "A", "first": "p003"}, {"synopsis": "s"}], "titles are unique"],
                     [[{"section": "A", "first": "p001"}, {"section": "B", "first": "p001"}, {"synopsis": "s"}], "only a parent and its first child"],
                     [[{"section": "A", "first": "p003"}, {"section": "B", "first": "p001", "parent": "A"}, {"synopsis": "s"}], "source order"],
                     [[{"section": "A", "first": "p001"}, {"section": "B", "first": "p002", "parent": "A"}, {"synopsis": "s"}], "child point"]):
        with pytest.raises(ValueError, match=why):
            apply_outline(props, bad, pack)


def test_group_points_derives_depth_from_the_parent_chain_and_a_dangling_parent_flattens():
    pts = [_pt("P", "section", "Parent", 10), _pt("C1", "section", "First child", 10, "P"), _pt("a", "claim", "A", 10),
           _pt("C2", "section", "Second child", 20, "P"), _pt("b", "claim", "B", 20),
           _pt("G", "section", "Grandchild", 25, "C2"), _pt("c", "claim", "C", 25),
           _pt("Q", "section", "Next top", 30), _pt("d", "claim", "D", 30)]
    assert [(h, d, [n["p"]["key"] for n in tree]) for h, tree, d in group_points(pts, {})] == [
        ("Parent", 0, []), ("First child", 1, ["a"]), ("Second child", 1, ["b"]), ("Grandchild", 2, ["c"]), ("Next top", 0, ["d"])]
    page = render_points(pts, timestamps="never")
    assert "## Parent\n\n### First child\n\n- A" in page and "\n\n#### Grandchild\n\n- C" in page and "\n\n## Next top\n\n- D" in page
    # a retracted parent: its children stand as top-level sections, the grandchild one level up — nothing disappears
    orphan = [p for p in pts if p["key"] != "P"]
    assert [(h, d) for h, _t, d in group_points(orphan, {})] == [("First child", 0), ("Second child", 0), ("Grandchild", 1), ("Next top", 0)]
    # a parent and a child with no point beneath at any depth render nothing
    empty = [_pt("P", "section", "Parent", 10), _pt("C1", "section", "Child", 10, "P"), _pt("Q", "section", "Next", 30), _pt("d", "claim", "D", 30)]
    assert [(h, d) for h, _t, d in group_points(empty, {})] == [("Next", 0)]
    # a flat outline is byte-for-byte what it was: every heading `##`, the book path's captured headers at depth 0
    flat = [_pt("S", "section", "Only", 10), _pt("a", "claim", "A", 10)]
    assert render_points(flat, timestamps="never").startswith("## Only\n\n- A")
    captured = [{**_pt("a", "claim", "A", 10), "heading": "Lesson 1"}]
    assert [(h, d) for h, _t, d in group_points(captured, {})] == [("Lesson 1", 0)]


def test_outline_plan_keeps_retitles_adds_reparents_and_retracts_against_the_standing_outline():
    pts = [_pt("S1", "section", "Kernel launches", 10), _pt("a", "claim", "A", 10),
           _pt("S2", "section", "Streams", 20), _pt("b", "claim", "B", 20),
           _pt("S3", "section", "Old questions", 30), _pt("c", "question", "C?", 30),
           _pt("syn", "synopsis", "Old synopsis.", 0)]
    cur = outline_of(pts)
    assert [(r["section"], r["first"], r["parent"], r["depth"]) for r in cur] == [
        ("Kernel launches", "p001", "", 0), ("Streams", "p002", "", 0), ("Old questions", "p003", "", 0)]
    subs = points_as_proposals([p for p in pts if p["kind"] not in ("section", "synopsis")])
    brief = render_outline_brief(subs, {"source": {"title": "Lec"}}, set_id="a/slug", current=cur, synopsis="Old synopsis.")
    assert brief.startswith("# Outline pass — draft `a/slug`") and "## The standing outline" in brief
    assert '    {"section": "Streams", "first": "p002"}\n' in brief and '    {"synopsis": "Old synopsis."}\n' in brief
    assert "- `p003` [question] 00:30  C?" in brief
    plan = plan_outline(pts, [
        {"section": "Launching work", "first": "p001"},                                  # ADD: a parent over the first two
        {"section": "Kernel launches", "first": "p001", "parent": "Launching work"},     # KEEP, re-parented
        {"section": "Streams per rank", "first": "p002", "parent": "Launching work"},    # RETITLE (same anchor), re-parented
        {"synopsis": "New synopsis."}])                                                  # S3 left out: RETRACT; the synopsis edited
    new_key = plan["add"][0]["key"]
    assert [(a["section"], a["first"], a["first_key"], a["parent"], a["parent_key"]) for a in plan["add"]] == [("Launching work", "p001", "a", "", "")]
    assert plan["keep"] == ["S1"]
    assert plan["retitle"] == [{"key": "S2", "id": "id-S2", "old": "Streams", "new": "Streams per rank"}]
    assert [(r["key"], r["old"], r["new"], r["new_title"]) for r in plan["reparent"]] == [("S1", "", new_key, "Launching work"),
                                                                                         ("S2", "", new_key, "Launching work")]
    assert [r["key"] for r in plan["retract"]] == ["S3"]
    assert plan["synopsis"] == {"key": "syn", "id": "id-syn", "old": "Old synopsis.", "new": "New synopsis."}
    assert plan["stats"] == {"sections": 3, "nested": 2, "depth": 1, "points": 3, "standing": 3}
    assert [(s["section"], s["key"], s["parent"], s["depth"]) for s in plan["sections"]] == [
        ("Launching work", new_key, "", 0), ("Kernel launches", "S1", "Launching work", 1), ("Streams per rank", "S2", "Launching work", 1)]
    # the standing outline handed back as it is plans nothing; a child under a NEW parent resolves to the minted key
    same = plan_outline(pts, [{"section": r["section"], "first": r["first"]} for r in cur])
    assert same["keep"] == ["S1", "S2", "S3"] and not (same["add"] or same["retitle"] or same["reparent"] or same["retract"] or same["synopsis"])
    nested_new = plan_outline(pts, [{"section": "Kernel launches", "first": "p001"}, {"section": "Streams", "first": "p002"},
                                    {"section": "Questions", "first": "p003"}, {"section": "Old questions", "first": "p003", "parent": "Questions"}])
    assert nested_new["retract"] == [] and nested_new["add"][0]["section"] == "Questions"
    assert nested_new["reparent"] == [{"key": "S3", "id": "id-S3", "section": "Old questions", "old": "", "new": nested_new["add"][0]["key"],
                                       "new_title": "Questions"}]
    with pytest.raises(ValueError, match="no synopsis point"):
        plan_outline([p for p in pts if p["kind"] != "synopsis"], [{"section": "Kernel launches", "first": "p001"}, {"synopsis": "x"}])


@pytest.mark.skipif(not _HAVE_GRAPH, reason=f"graph capability {DEFAULT_GRAPH_ID!r} not installed (CI)")
def test_cli_outline_pass_over_a_standing_draft_lands_journaled_ops_and_replays_to_the_same_bytes(tmp_path):
    """The draft mode end to end (81d6e669 (3)): the brief over the live points, the plan, the
    apply as accept-point / edit-point / retract-point ops, the re-render with derived heading
    depth — and the whole journal replayed onto a fresh db reproduces the page byte for byte."""
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
    assert _run(*base, "new-note", "--slug", slug, "--content", post).returncode == 0
    assert _run(*base, "notes-pack", "--source", "Seven Dangerous").returncode == 0
    pack_json = next((pri_dir / "purenotes" / "packs").glob("npack_*.json"))
    rows = tmp_path / "rows.jsonl"
    rows.write_text("\n".join(json.dumps(x) for x in [
        {"kind": "claim", "from_i": 0, "to_i": 0, "text": "Gatto quit teaching in 1991.", "lead": "Gatto"},
        {"kind": "quotation", "from_i": 1, "to_i": 2, "text": "I teach confusion.", "attribution": "Gatto"},
        {"kind": "claim", "from_i": 3, "to_i": 3, "text": "Subjects taught in isolation."},
        {"kind": "claim", "from_i": 3, "to_i": 4, "text": "Isolation = no coherent picture.", "lead": "coherent", "parent": 2},
        {"kind": "synopsis", "from_i": 0, "to_i": 4, "text": "Gatto quit in 1991; school teaches confusion by isolating subjects."}]) + "\n")
    assert _run(*base, "notes-ingest", "--pack", str(pack_json), "--rows", str(rows), "--proposer", "test").returncode == 0
    r = _run(*base, "notes-accept", "--slug", slug, "--accept-all")
    assert r.returncode == 0 and "accepted 5" in r.stdout, r.stderr or r.stdout
    assert _run(*base, "notes-render", "--slug", slug).returncode == 0
    staged = pri_dir / "staging" / "the-learning-game" / "ch01" / "index.md"
    flat = staged.read_text()
    assert "## Lesson 1. Confusion" in flat                                   # the captured header (the book path) before any section

    # (1) the brief over the standing draft: no section yet, the synopsis shown
    r = _run(*base, "notes-outline", "--slug", slug)
    assert r.returncode == 0 and "outline brief" in r.stdout and "0 standing section(s)" in r.stdout, r.stderr or r.stdout
    brief = (pri_dir / "purenotes" / "passes" / slug / "outline.md").read_text()
    assert "## The standing outline" in brief and "carries 0 section(s)" in brief and '{"synopsis": "Gatto quit in 1991' in brief
    assert "\n- `p001` [claim] " in brief and "\n  - `p004` [claim] " in brief
    # both modes at once, or neither, refuse
    assert _run(*base, "notes-outline", "--slug", slug, "--set", "x").returncode == 1
    assert _run(*base, "notes-outline", "--set", "x").returncode == 1
    # (2) the plan, then the apply: a parent over two children, one sharing the parent's anchor
    o1 = tmp_path / "o1.jsonl"
    o1.write_text("\n".join(json.dumps(x) for x in [{"section": "Gatto", "first": "p001"},
                                                    {"section": "Confusion", "first": "p001", "parent": "Gatto"},
                                                    {"section": "Isolation", "first": "p003", "parent": "Gatto"}]) + "\n")
    r = _run(*base, "notes-outline", "--slug", slug, "--rows", str(o1))
    assert r.returncode == 0 and "add 3" in r.stdout and "+ Confusion @ p001  ⊂ Gatto" in r.stdout and "--apply" in r.stdout, r.stderr or r.stdout
    assert len([o for o in read_journal(pj) if o["verb"] == "accept-point"]) == 5          # the dry run lands nothing
    r = _run(*base, "notes-outline", "--slug", slug, "--rows", str(o1), "--apply")
    assert r.returncode == 0 and "**applied** 3 journaled op(s): −0 +3 ~0 ⊂0" in r.stdout, r.stderr or r.stdout
    ops = [o for o in read_journal(pj) if o["verb"] == "accept-point"]
    assert len(ops) == 8 and [o["args"]["point"]["kind"] for o in ops[5:]] == ["section"] * 3 and "point_set" not in ops[5]["args"]
    assert ops[6]["args"]["point"]["parent_key"] == ops[5]["args"]["point"]["key"] == ops[7]["args"]["point"]["parent_key"]
    assert _run(*base, "notes-render", "--slug", slug).returncode == 0
    nested = staged.read_text()
    assert "\n## Gatto\n\n### Confusion\n\n- **Gatto** quit teaching" in nested and "\n### Isolation\n\n- Subjects taught" in nested
    assert "Lesson 1. Confusion" not in nested                                            # synthesized structure replaces the captured headers
    # (3) a second pass: a retitle, a drop — identity kept, the dropped section's points fall into the one before
    o2 = tmp_path / "o2.jsonl"
    o2.write_text("\n".join(json.dumps(x) for x in [{"section": "Gatto", "first": "p001"},
                                                    {"section": "Confusion at school", "first": "p001", "parent": "Gatto"}]) + "\n")
    r = _run(*base, "notes-outline", "--slug", slug, "--rows", str(o2), "--apply")
    assert r.returncode == 0 and "~1" in r.stdout and "−1" in r.stdout and "keep 1" in r.stdout, r.stderr or r.stdout
    assert [o["verb"] for o in read_journal(pj)][-2:] == ["retract-point", "edit-point"]
    assert _run(*base, "notes-render", "--slug", slug).returncode == 0
    again = staged.read_text()
    assert "### Confusion at school\n" in again and "Isolation" not in again.split("### Confusion at school")[0] and "\n### Isolation" not in again
    assert again.count("{#pt-") == nested.count("{#pt-") == 4                                # a section is a heading, never an anchor: every point anchor kept
    # (4) two siblings on one anchor refuse; flattening re-parents to the top and retracts the parent
    o3 = tmp_path / "o3.jsonl"
    o3.write_text(json.dumps({"section": "Gatto", "first": "p001"}) + "\n" + json.dumps({"section": "Confusion at school", "first": "p001"}) + "\n")
    r = _run(*base, "notes-outline", "--slug", slug, "--rows", str(o3), "--apply")
    assert r.returncode == 1 and "only a parent and its first child" in r.stderr
    o3.write_text(json.dumps({"section": "Confusion at school", "first": "p001"}) + "\n")
    r = _run(*base, "notes-outline", "--slug", slug, "--rows", str(o3), "--apply")
    assert r.returncode == 0 and "−1 +0 ~0 ⊂1" in r.stdout, r.stderr or r.stdout
    assert _run(*base, "notes-render", "--slug", slug).returncode == 0
    final = staged.read_text()
    assert "\n## Confusion at school\n\n- **Gatto** quit" in final and "\n## Gatto\n" not in final and "###" not in final
    # (5) the oracle: the whole journal replayed onto a fresh db reproduces the page byte for byte
    rdb = str(pri_dir / "rep.db")
    r = _run("--graph-db-path", rdb, "--journal-path", pj, "replay")
    assert r.returncode == 0, r.stderr or r.stdout
    assert _run("--graph-db-path", rdb, "read", note_node_id(slug)).stdout == final
    assert _run("--graph-db-path", pdb, "read", note_node_id(slug)).stdout == final
