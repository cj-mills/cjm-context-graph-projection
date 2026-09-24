"""The PLACEMENT PASS (rulings 96be1528 (1) and (3); work item 81d6e669 (3)): roles are facts on
the shared point read through the type's role map at render (meta -> the front section, aside
-> omitted, inherited down the subtree), moves are the deliverable's PLACED overlay (a root and
its subtree after the key it names, "" = the section's end), the plan is deltas against what
stands, and the apply lands journaled `assert` + `place-point` ops that replay to the same page."""
import json

import pytest
from cjm_context_graph_primitives.journal import read_journal
from cjm_context_graph_projection.notes_place import plan_placement, render_place_brief
from cjm_context_graph_projection.purenotes import effective_roles, group_points, render_points
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID
from cjm_dev_graph_schema.identity import note_node_id
from test_notes_outline import _pt
from test_purenotes import _HAVE_GRAPH, _build_sibling, _run

ROLE_MAP = {"content": "body", "meta": "front-section", "aside": "omit", "front_section_title": "About this lecture"}


def _draft():
    return [_pt("S1", "section", "Kernel launches", 10), _pt("a", "claim", "Today we cover launches", 10),
            _pt("a1", "claim", "and streams", 11, "a"), _pt("b", "claim", "Kernels launch twice", 12),
            _pt("c", "claim", "A joke about warp names", 14),
            _pt("S2", "section", "Streams", 20), _pt("d", "claim", "Streams do not share", 20),
            _pt("q", "question", "Why launch twice?", 24), _pt("q1", "claim", "The warm-up.", 25, "q"),
            _pt("S3", "section", "Closing", 30), _pt("e", "claim", "Thanks all", 30),
            _pt("syn", "synopsis", "Synopsis.", 0)]


def test_effective_roles_inherit_and_the_role_map_shapes_the_page():
    pts = _draft()
    roles = {"a": "meta", "c": "aside", "e": "meta"}
    eff = effective_roles(pts, roles)
    assert eff["a"] == "meta" and eff["a1"] == "meta" and eff["c"] == "aside" and eff["b"] == "content" and eff["q1"] == "content"
    assert "S1" not in eff and "syn" not in eff                                                    # structure kinds carry no role
    page = render_points(pts, timestamps="never", roles=roles, role_map=ROLE_MAP)
    assert page.startswith("## About this lecture\n\n- Today we cover launches")                 # the front section, first, in source order
    assert "\n  - and streams" in page.split("## Kernel launches")[0]                            # the child rides its parent's role
    assert "Thanks all" in page.split("## Kernel launches")[0] and "\n## Closing" not in page    # a meta point from the end joins the front; its section empties
    assert "A joke about warp names" not in page                                                 # the aside is omitted
    assert "\n## Kernel launches\n\n- Kernels launch twice" in page
    assert render_points(pts, timestamps="never") == render_points(pts, timestamps="never", roles={}, role_map=ROLE_MAP)   # no facts: unchanged
    assert render_points(pts, timestamps="never") == render_points(pts, timestamps="never", roles=roles, role_map=None)    # no map: unchanged
    outline = render_points(pts, rendering="outline", roles=roles, role_map=ROLE_MAP)
    assert "**About this lecture**\n\n- Today we cover launches" in outline


def test_placements_move_a_root_with_its_subtree_after_a_key_or_to_the_end():
    pts = [p for p in _draft() if p["kind"] != "synopsis"]
    unit = {}
    flat = [(h, [n["p"]["key"] for n in tree]) for h, tree, _d in group_points(pts, unit)]
    assert flat == [("Kernel launches", ["a", "b", "c"]), ("Streams", ["d", "q"]), ("Closing", ["e"])]
    moved = group_points(pts, unit, {"q": {"section": "S1", "after": "b"}})
    assert [(h, [n["p"]["key"] for n in tree]) for h, tree, _d in moved] == [("Kernel launches", ["a", "b", "q", "c"]), ("Streams", ["d"]), ("Closing", ["e"])]
    assert [k["p"]["key"] for k in moved[0][1][2]["kids"]] == ["q1"]                              # the subtree rides along
    end = group_points(pts, unit, {"q": {"section": "S1", "after": ""}, "e": {"section": "S1", "after": ""}})
    assert [n["p"]["key"] for n in end[0][1]] == ["a", "b", "c", "q", "e"] and [h for h, _t, _d in end] == ["Kernel launches", "Streams"]
    two = group_points(pts, unit, {"d": {"section": "S1", "after": "a"}, "q": {"section": "S1", "after": "a"}})
    assert [n["p"]["key"] for n in two[0][1]] == ["a", "d", "q", "b", "c"]                         # two after the same key keep source order
    child_key = group_points(pts, unit, {"e": {"section": "S2", "after": "q1"}})                  # a child key names its root
    assert [n["p"]["key"] for n in child_key[1][1]] == ["d", "q", "e"]
    assert group_points(pts, unit, {"q": {"section": "S1", "after": None}}) == group_points(pts, unit)   # no `after`: the derived slot (a verdict-only overlay)
    assert group_points(pts, unit, {"q": {"section": "GONE", "after": ""}}) == group_points(pts, unit)   # a retracted target: the derived slot
    page = render_points(pts, timestamps="never", placements={"q": {"section": "S1", "after": "b"}})
    assert page.index("Why launch twice?") < page.index("A joke about warp names") < page.index("## Streams")


def test_placement_plan_is_deltas_against_the_standing_facts_and_overlay():
    pts = _draft()
    brief = render_place_brief(pts, slug="x", roles={"a": "meta"}, placements={"q": {"section": "S1", "after": "b"}}, role_map=ROLE_MAP)
    assert "## Kernel launches" in brief and "- `p001` [claim] 00:10  Today we cover launches  [role: meta]" in brief
    # index keys in root-start order, children after their parent: a p001 · a1 p002 · b p003 · c p004 · d p005 · q p006 · q1 p007 · e p008
    assert "- `p006` [question] 00:24  Why launch twice?  [moved after p003]" in brief and brief.index("`p006`") < brief.index("## Streams")
    assert '{"point": "p017", "section": "<a section title>", "after": "p020"}' in brief
    plan = plan_placement(pts, [
        {"point": "p001", "role": "meta", "why": "the agenda"},                # already the standing fact: no-op
        {"point": "p004", "role": "aside", "why": "a joke"},                   # new departure
        {"point": "p003", "role": "content"},                                  # content on a point with no fact: no-op
        {"point": "p006", "section": "Kernel launches", "after": "p003"},      # a move (q after b)
        {"point": "p008", "section": "Streams", "after": ""}],                 # e to the end of Streams
        roles={"a": "meta"}, placements={})
    assert [(r["point"], r["key"], r["old"], r["new"], r["why"]) for r in plan["roles"]] == [("p004", "c", "", "aside", "a joke")]
    assert [(m["point"], m["key"], m["section"], m["after"], m["after_key"]) for m in plan["moves"]] == [
        ("p006", "q", "S1", "p003", "b"), ("p008", "e", "S2", "", "")]
    assert plan["unmoves"] == [] and plan["stats"]["front_after"] == 2 and plan["stats"]["omitted_after"] == 1
    same = plan_placement(pts, [{"point": "p006", "section": "Kernel launches", "after": "p003"}, {"point": "p008", "section": ""}],
                          roles={}, placements={"q": {"section": "S1", "after": "b"}})
    assert same["moves"] == [] and same["unmoves"] == []                                              # what stands: no-ops
    undo = plan_placement(pts, [{"point": "p006", "section": ""}, {"point": "p001", "role": "content"}],
                          roles={"a": "meta"}, placements={"q": {"section": "S1", "after": "b"}})
    assert [u["key"] for u in undo["unmoves"]] == ["q"] and [(r["key"], r["old"], r["new"]) for r in undo["roles"]] == [("a", "meta", "content")]
    for bad, why in ([[{"point": "p003", "role": "meta", "section": "Streams", "after": ""}], "EITHER"],
                     [[{"point": "p002", "section": "Streams", "after": ""}], "child point"],          # a1 is indented
                     [[{"point": "p006", "section": "Nowhere", "after": ""}], "names 0 section"],
                     [[{"point": "p006", "section": "Streams"}], "carries `after`"],
                     [[{"point": "p006", "section": "Streams", "after": "p002"}], "child point"],
                     [[{"point": "p006", "role": "tangent"}], "one of"],
                     [[{"point": "p099", "role": "meta"}], "not a point"]):
        with pytest.raises(ValueError, match=why):
            plan_placement(pts, bad, roles={}, placements={})


@pytest.mark.skipif(not _HAVE_GRAPH, reason=f"graph capability {DEFAULT_GRAPH_ID!r} not installed (CI)")
def test_cli_placement_pass_lands_facts_and_overlay_and_replays_to_the_same_bytes(tmp_path):
    sib_dir, pri_dir = tmp_path / "sib", tmp_path / "pri"
    sib_dir.mkdir(); pri_dir.mkdir()
    sdb = str(sib_dir / "sib.db")
    _build_sibling(sdb)
    pdb, pj = str(pri_dir / "pri.db"), str(pri_dir / "writes.jsonl")
    (pri_dir / "graph.config.json").write_text(json.dumps(
        {"notes_profile": "quarto_post", "emit_root": str(pri_dir / "staging"), "sibling_graphs": {"tx": sdb}}))
    base = ("--graph-db-path", pdb, "--journal-path", pj, "--source-journal-path", str(pri_dir / "source.jsonl"))
    slug = "the-learning-game/ch01"
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"presentation_policy": {"point_roles": ROLE_MAP}}))
    r = _run(*base, "notes-type", "pure-notes", "--policy-file", str(policy))
    assert r.returncode == 0, r.stderr or r.stdout
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
    assert "accepted 5" in _run(*base, "notes-accept", "--slug", slug, "--accept-all").stdout
    o1 = tmp_path / "o1.jsonl"
    o1.write_text(json.dumps({"section": "Gatto", "first": "p001"}) + "\n" + json.dumps({"section": "Isolation", "first": "p003"}) + "\n")
    assert _run(*base, "notes-outline", "--slug", slug, "--rows", str(o1), "--apply").returncode == 0
    assert _run(*base, "notes-render", "--slug", slug).returncode == 0
    staged = pri_dir / "staging" / "the-learning-game" / "ch01" / "index.md"
    before = staged.read_text()
    assert "## Gatto\n" in before and "## Isolation\n" in before and "About this lecture" not in before
    # (1) the brief: the page as it stands, with keys; (2) the plan; (3) the apply
    r = _run(*base, "notes-place", "--slug", slug)
    assert r.returncode == 0 and "placement brief" in r.stdout, r.stderr or r.stdout
    brief = (pri_dir / "purenotes" / "passes" / slug / "place.md").read_text()
    assert "## Gatto\n" in brief and "- `p001` [claim]" in brief and "  - `p004` [claim]" in brief and "## Output contract" in brief
    p1 = tmp_path / "p1.jsonl"
    p1.write_text("\n".join(json.dumps(x) for x in [
        {"point": "p001", "role": "meta", "why": "who the speaker is"},
        {"point": "p003", "section": "Gatto", "after": "p002"}]) + "\n")
    r = _run(*base, "notes-place", "--slug", slug, "--rows", str(p1))
    assert r.returncode == 0 and "placement plan" in r.stdout and "roles 1" in r.stdout and "moves 1" in r.stdout, r.stderr or r.stdout
    assert not [o for o in read_journal(pj) if o["verb"] in ("assert", "place-point") and o["args"].get("predicate") == "point_role"]
    r = _run(*base, "notes-place", "--slug", slug, "--rows", str(p1), "--apply")
    assert r.returncode == 0 and "**applied** 2 journaled op(s)" in r.stdout, r.stderr or r.stdout
    verbs = [o["verb"] for o in read_journal(pj)]
    assert verbs[-2:] == ["assert", "place-point"]
    assert _run(*base, "notes-render", "--slug", slug).returncode == 0
    after = staged.read_text()
    assert "## About this lecture\n\n- **Gatto** quit teaching in 1991." in after                   # the meta point opens the page
    assert "## Isolation" not in after                                                                # its only root moved out: the section renders nothing
    assert after.index("I teach confusion") < after.index("Subjects taught in isolation")           # moved into Gatto, after the quotation
    # (4) a second pass: undo the move, revert the role — deltas, journaled, the page back to `before`
    p2 = tmp_path / "p2.jsonl"
    p2.write_text(json.dumps({"point": "p003", "section": ""}) + "\n" + json.dumps({"point": "p001", "role": "content"}) + "\n")
    r = _run(*base, "notes-place", "--slug", slug, "--rows", str(p2), "--apply")
    assert r.returncode == 0 and "**applied** 2 journaled op(s)" in r.stdout, r.stderr or r.stdout
    assert _run(*base, "notes-render", "--slug", slug).returncode == 0
    assert staged.read_text() == before
    # (5) re-apply the first pass and replay the whole journal: the same bytes on a fresh db
    assert _run(*base, "notes-place", "--slug", slug, "--rows", str(p1), "--apply").returncode == 0
    assert _run(*base, "notes-render", "--slug", slug).returncode == 0
    final = staged.read_text()
    assert final == after
    rdb = str(pri_dir / "rep.db")
    r = _run("--graph-db-path", rdb, "--journal-path", pj, "replay")
    assert r.returncode == 0, r.stderr or r.stdout
    assert _run("--graph-db-path", rdb, "read", note_node_id(slug)).stdout == final
