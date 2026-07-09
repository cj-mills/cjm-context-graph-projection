"""Lenses (DEC f1b02b95): spec validation, upsert-by-slug, apply (union + params
+ expand), journal replay, render."""

import asyncio
from pathlib import Path

import pytest

from cjm_context_graph_layer.grammar import make_edge
from cjm_context_graph_layer.ops import extend_graph

from cjm_context_graph_projection.lens import (apply_lens, bind_params, lens_node_id,
                                               load_lenses, set_lens, validate_lens_spec)
from cjm_context_graph_projection.render import render
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph

_GATED = pytest.mark.skipif(
    not (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists(),
    reason=f"graph capability {DEFAULT_GRAPH_ID!r} not installed at {DEFAULT_MANIFESTS}",
)

A = "aaaaaaaa-0000-5000-8000-000000000001"
B = "bbbbbbbb-0000-5000-8000-000000000002"
C = "cccccccc-0000-5000-8000-000000000003"
D = "dddddddd-0000-5000-8000-000000000004"

GOOD_SPEC = {"params": [{"name": "kind", "type": "string", "required": True}],
             "selection": [{"verb": "list", "args": {"label": "{kind}"}}],
             "expand": {"hops": 1, "relations": ["ABOUT"]},
             "view": {"group_by": "label"}}


# --- pure: grammar + identity + binding -------------------------------------

def test_validate_lens_spec_accepts_and_normalizes():
    norm, err = validate_lens_spec(GOOD_SPEC)
    assert err is None
    assert norm["selection"][0]["verb"] == "list" and norm["expand"]["hops"] == 1


def test_validate_lens_spec_rejects_loudly():
    _, err = validate_lens_spec({"selection": [{"verb": "sql", "args": {}}],
                                 "params": [{"name": "x", "type": "float"}],
                                 "view": {"font": "comic sans"}, "extra": 1})
    assert err is not None
    for fragment in ("sql", "float", "font", "extra"):
        assert fragment in err or fragment in err.lower()
    # An empty selection is not a lens (a lens SELECTS something).
    _, err = validate_lens_spec({"selection": []})
    assert "NON-EMPTY" in err


def test_lens_node_id_deterministic():
    assert lens_node_id("session-window") == lens_node_id("session-window")
    assert lens_node_id("session-window") != lens_node_id("north-stars")


def test_bind_params_defaults_required_and_types():
    decls = [{"name": "session", "type": "string", "required": True},
             {"name": "start", "type": "timestamp"},
             {"name": "k", "default": "12"}]
    bound, err = bind_params(decls, {"session": "s1", "start": "2026-07-08_10-58-13"})
    assert err is None and bound["session"] == "s1" and bound["k"] == "12"
    assert isinstance(bound["start"], float)  # timestamp coerced to unix seconds
    _, err = bind_params(decls, {})
    assert "session" in err                    # missing required -> loud
    _, err = bind_params(decls, {"session": "s1", "nope": "x"})
    assert "nope" in err                       # stray binding -> loud
    _, err = bind_params(decls, {"session": "s1", "start": "someday"})
    assert "someday" in err                    # bad timestamp -> loud


# --- graph-backed: upsert + apply + replay -----------------------------------

def _node(nid, title, label="Decision"):
    return {"id": nid, "label": label,
            "properties": {"title": title, "root_kind": "asserted"}, "sources": []}


async def _build(db):
    nodes = [_node(A, "Alpha"), _node(B, "Beta"), _node(C, "Gamma", "Note"),
             _node(D, "Delta", "Note")]
    edges = [make_edge(A, B, "REFERENCES"), make_edge(A, C, "ABOUT"),
             make_edge(B, D, "GATED_BY")]
    async with open_graph(db) as gx:
        await extend_graph(gx.queue, gx.graph_id, nodes, edges)


@_GATED
def test_set_lens_upserts_by_slug_and_rejects_bad_specs(tmp_path):
    db = str(tmp_path / "g.db")

    async def go():
        async with open_graph(db) as gx:
            bad = await set_lens(gx, "x", {"selection": []})
            first = await set_lens(gx, "decisions", GOOD_SPEC, title="Decisions")
            second = await set_lens(gx, "decisions", GOOD_SPEC, title="All decisions")
            shelf = await load_lenses(gx)
            return bad, first, second, shelf

    bad, first, second, shelf = asyncio.run(go())
    assert bad["written"] is False and "NON-EMPTY" in bad["error"]
    assert first["written"] and not first["updated"]
    assert second["updated"] and second["lens_id"] == first["lens_id"]
    assert [l["slug"] for l in shelf] == ["decisions"]
    assert shelf[0]["title"] == "All decisions"  # last author wins


@_GATED
def test_apply_lens_union_params_expand(tmp_path):
    db = str(tmp_path / "g.db")
    spec = {"params": [{"name": "kind", "type": "string", "required": True}],
            "selection": [{"verb": "list", "args": {"label": "{kind}"}},
                          {"verb": "subgraph", "args": {"refs": [C]}}],
            "expand": {"hops": 1, "relations": ["ABOUT"]},
            "view": {}}

    async def go():
        await _build(db)
        async with open_graph(db) as gx:
            await set_lens(gx, "by-kind", spec, title="By kind")
            missing = await apply_lens(gx, "by-kind")           # required param absent
            unknown = await apply_lens(gx, "nope")              # unknown slug
            res = await apply_lens(gx, "by-kind", {"kind": "Decision"})
            return missing, unknown, res

    missing, unknown, res = asyncio.run(go())
    assert "kind" in missing["error"] and missing["params"]     # declares its shape back
    assert "no lens" in unknown["error"] and "by-kind" in unknown["error"]
    # Union: Decisions (A, B) ∪ subgraph refs (C); expand 1 hop over ABOUT only
    # adds nothing new (A—ABOUT→C already inside; GATED_BY filtered out).
    assert {n["id"] for n in res["nodes"]} == {A, B, C}
    assert res["bound"] == {"kind": "Decision"}
    assert [c["selected"] for c in res["clauses"]] == [2, 1]
    rels = {e["relation_type"] for e in res["edges"]}
    assert "REFERENCES" in rels and "ABOUT" in rels             # interconnect is unfiltered


@_GATED
def test_list_predicate_value_filter_selects_register_subjects(tmp_path):
    from cjm_context_graph_projection.listing import list_graph
    from cjm_context_graph_projection.write import assert_value

    db = str(tmp_path / "g.db")

    async def go():
        await _build(db)
        async with open_graph(db) as gx:
            await assert_value(gx, A, "role", "north-star")
            await assert_value(gx, B, "role", "rule")
            stars = await list_graph(gx, predicate="role", value="north-star")
            misuse = await list_graph(gx, label="Note", value="x")
            return stars, misuse

    stars, misuse = asyncio.run(go())
    assert [r["subject_id"] for r in stars["rows"]] == [A]
    assert "predicate mode only" in misuse["error"]


@_GATED
def test_set_lens_replays_from_journal(tmp_path):
    from cjm_context_graph_projection.journal import append_write, replay_journal

    db = str(tmp_path / "g.db")
    jp = str(tmp_path / "writes.jsonl")
    append_write(jp, "set-lens", {"slug": "frontier", "title": "Frontier",
                                  "spec": {"selection": [{"verb": "readiness", "args": {}}]}})

    async def go():
        async with open_graph(db) as gx:
            counts = await replay_journal(gx, jp)
            return counts, await load_lenses(gx)

    counts, shelf = asyncio.run(go())
    assert counts["set-lens"] == 1
    assert [l["slug"] for l in shelf] == ["frontier"]


# --- render ------------------------------------------------------------------

def test_render_lens_and_set_lens_human():
    ok = {"slug": "s", "title": "Session window", "description": "one window",
          "bound": {"session": "w1"}, "clauses": [{"verb": "journal-window", "selected": 3}],
          "view": {"group_by": "label"},
          "nodes": [{"id": A, "label": "Decision", "title": "Alpha", "expanded": False}],
          "edges": [], "missing": [], "ambiguous": [],
          "seed_count": 1, "expanded_count": 0, "truncated": False}
    out = render("lens", ok, "human")
    assert "## Lens `s` — Session window" in out
    assert "session=w1" in out and "journal-window×3" in out and "group_by=label" in out
    assert "**Alpha**" in out
    err = render("lens", {"error": "missing required param(s): session (string)",
                          "params": [{"name": "session", "type": "string", "required": True}]},
                 "human")
    assert "⚠" in err and "declares: session (string, required)" in err
    assert "lens authored" in render(
        "set-lens", {"slug": "s", "lens_id": "x", "updated": False, "written": True}, "human")
