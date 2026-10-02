"""The post page's sources (design 39c51c15 (3), amendment 722a8232, design 37f82f72): a Source or
a Collection states its locator and citation at observation, the link op carries them; the
draws-on block names each work or unit from the Library and links its page, a source the Library
places nowhere refuses, and a born post naming no source is reported."""

import asyncio
from pathlib import Path

import pytest

from cjm_context_graph_layer.grammar import make_edge
from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.journal import append_write
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import note_node_id
from cjm_dev_graph_schema.nodes import ReferenceNode
from cjm_markdown_decompose_core.extract import note_from_text
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from cjm_context_graph_projection import factlayer as F
from cjm_context_graph_projection.journal import replay_journal
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.sources import (citation_text, draws_plan, load_sources, observed_facts,
                                                  render_draws)
from cjm_context_graph_projection.write import assert_value, link

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()

WORK = {"title": "The Learning Game", "subtitle": "Teaching Kids", "author": "Ana Lorena Fábrega",
        "narrator": "Ana Lorena Fábrega"}
CHAPTER = {"id": "c2c2c2c2-0000-4000-8000-000000000002", "label": "Source", "properties": {
    "title": "05 - 2. How Did We Get Here？", "path": "/x/05.mp3",
    "work_structure": {"kind": "chapter", "part": 1, "part_title": "School", "chapter": 2,
                       "title": "How Did We Get Here?", "work": WORK, "evidence": ["toc"]}}}
CHAPTER_1 = {"id": "c1c1c1c1-0000-4000-8000-000000000001", "label": "Source", "properties": {
    "title": "04 - 1. Seven", "work_structure": {"kind": "chapter", "part": 1, "chapter": 1, "work": WORK}}}
BOOK = {"id": "b0b0b0b0-0000-4000-8000-00000000000b", "label": "Collection", "properties": {"title": "The Learning Game"}}
LECTURE = {"id": "1ec1ec1e-0000-4000-8000-00000000001e", "label": "Source", "properties": {
    "title": "Bonus Lecture： CUDA C++ llm.cpp", "public_url": "https://www.youtube.com/watch?v=WiB_3Csfj_Q",
    "public_url_evidence": {"kind": "playlist-metadata", "playlist_file": "/x/p.json",
                            "playlist_title": "Bonus Lecture: CUDA C++ llm.cpp"}}}
BARE = {"id": "ba5eba5e-0000-4000-8000-0000000000ba", "label": "Source", "properties": {"title": "a local recording"}}
SEGMENT = {"id": "5e95e95e-0000-4000-8000-000000000005", "label": "Segment", "properties": {"text": "Hello.", "index": 0}}
# A human-added link on the book (a Reference node of the sibling naming its source; ruling a7ca900d (3))
BOOK_LINK = {"id": "11111111-0000-4000-8000-000000000011", "label": "Reference", "properties": {
    "label": "The book page", "url": "https://pub.example/book", "role": "publisher-page",
    "source_id": "b0b0b0b0-0000-4000-8000-00000000000b"}}


def test_a_source_states_its_locator_and_citation():
    ch = observed_facts(CHAPTER)
    assert ch == {P.CITATION: {"author": "Ana Lorena Fábrega", "chapter": 2, "part": 1, "work": "The Learning Game"}}
    lec = observed_facts(LECTURE)
    assert lec[P.LOCATOR]["value"] == "https://www.youtube.com/watch?v=WiB_3Csfj_Q"
    assert lec[P.LOCATOR]["evidence"]["kind"] == "playlist-metadata"           # the evidence rides the op
    assert lec[P.CITATION] == {"title": "Bonus Lecture: CUDA C++ llm.cpp"}       # the published title, not the file's
    # a Collection is the work its members all name; members that disagree name none
    assert observed_facts(BOOK, [CHAPTER["properties"], CHAPTER_1["properties"]]) == {
        P.CITATION: {"author": "Ana Lorena Fábrega", "work": "The Learning Game"}}
    other = {"work_structure": {"work": {"title": "Another Book", "author": "X"}}}
    assert observed_facts(BOOK, [CHAPTER["properties"], other]) == {}
    assert observed_facts(BARE) == {} and observed_facts(SEGMENT) == {}
    # the links ride only when read (capture a2936020), kept to their fields, blanks dropped
    links = [{"id": "x", "label": "Page", "url": "https://p", "role": "", "notes_slug": ""}]
    assert observed_facts(BOOK, [], links)[P.RESOURCES] == [{"label": "Page", "url": "https://p"}]
    assert observed_facts(BARE, [], [])[P.RESOURCES] == [] and observed_facts(SEGMENT, [], links) == {}


def test_a_citation_reads_in_reader_order():
    assert citation_text({"work": "The Learning Game", "author": "Ana Lorena Fábrega", "part": 1, "chapter": 2}) == \
        "The Learning Game, Ana Lorena Fábrega, Part 1, Chapter 2"
    assert citation_text({"title": "A [talk]"}) == "A [talk]"


# Draws-on lines as the Library index plans them (design 37f82f72 (2)): a unitless work's index
# entry, and a unit's row on its work page with the observed locators of its sibling Sources
WORK_LINE = {"work": "Chip [War]", "work_key": "chip-war", "form": "book", "author": "Chris Miller",
             "year": "2022", "isbn": "", "href": "/library/#chip-war", "locators": []}
UNIT_LINE = {"work": "GPU MODE", "work_key": "gpu-mode", "form": "lecture-series", "author": "A & B", "year": "",
             "isbn": "", "unit": "Bonus Lecture", "unit_isbn": "", "href": "/library/gpu-mode/#bonus",
             "locators": ["https://y/1", "https://y/2"]}


def test_the_block_names_from_the_library_and_links_its_page():
    one = render_draws([WORK_LINE])
    assert one == "::: {.post-sources}\n**Draws on**\n\n- [Chip \\[War\\]](/library/#chip-war) · Chris Miller · 2022\n:::\n"
    two = render_draws([{**UNIT_LINE, "locators": ["https://y/1"]}, UNIT_LINE])
    assert "- [GPU MODE — Bonus Lecture](/library/gpu-mode/#bonus) · A & B · [source](https://y/1)\n" in two
    assert "· [source 1](https://y/1) · [source 2](https://y/2)" in two      # several sources numbered
    assert render_draws([]) == ""


def test_the_plan_refuses_an_unplaced_source_and_reports_born_posts_naming_none():
    loaded = {"posts": {"n1": [{"id": "r1", "kind": "Source", "title": "t1", "locator": "https://y", "citation": {}},
                               {"id": "r2", "kind": "Source", "title": "t2", "locator": "", "citation": {"title": "T2"}}]},
              "derived": {"n1", "n3", "n4"}}
    rendered = {"n1": {"title": "One", "origin": "born"}, "n2": {"title": "Two", "origin": "archive"},
                "n3": {"title": "Three", "origin": "born"}, "n4": {"title": "Four", "origin": "archive"}}
    plan = draws_plan(loaded, {"n1": [UNIT_LINE], "n2": [WORK_LINE], "n5": [WORK_LINE]}, ["r1"], rendered)
    assert sorted(plan["blocks"]) == ["n1", "n2"]                     # n5 is not rendered by the profile
    assert plan["unplaced"] == [{"id": "n1", "post": "One", "reference": "r2", "title": "T2"}]
    assert plan["missing"] == [{"id": "n3", "title": "Three"}]        # an archive post is not born
    assert plan["counts"] == {"draws_on": 2, "draws_lines": 2, "draws_located": 1,
                              "draws_unplaced": 1, "sources_missing": 1}
    # no Library planned: every source a post names is unplaced, and no block renders
    bare = draws_plan(loaded, {}, [], rendered)
    assert bare["blocks"] == {} and [u["reference"] for u in bare["unplaced"]] == ["r1", "r2"]


def _post(title: str) -> str:
    return f"---\ntitle: \"{title}\"\ndate: 2026-09-07\ncategories: [notes]\n---\n\n## Body\n\nText.\n"


async def _seed(gx, root: Path) -> None:
    notes = []
    for slug in ("ch2", "book", "lecture", "notes"):
        (root / slug).mkdir(parents=True, exist_ok=True)
        (root / slug / "index.md").write_text(_post(slug))
        notes.append(note_from_text(str(root / slug / "index.md"), _post(slug), corpus_root=str(root), lossless=True))
    nodes, edges = corpus_graph_elements(notes)
    await extend_graph(gx.queue, gx.graph_id, nodes, edges)


async def _facts(gx):
    rows = [a for a in await F.load_assertions(gx) if F.prop(a, "predicate") in (P.LOCATOR, P.CITATION, P.RESOURCES)]
    ids = {F.nid(a) for a in rows}
    supers = sorted(p for p in await F.load_supersedes(gx) if p[0] in ids and p[1] in ids)
    return sorted((str(F.prop(a, "subject_id")), str(F.prop(a, "predicate")), str(F.prop(a, "value")),
                   str(F.prop(a, "method"))) for a in rows), supers


@pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")
def test_a_link_lands_the_facts_and_a_replay_lands_them_without_the_sibling(tmp_path):
    sib = str(tmp_path / "sibling.db")
    siblings = {"tx": sib}
    jp = str(tmp_path / "writes.jsonl")

    async def seed_sibling():
        async with open_graph(sib) as sg:
            await extend_graph(sg.queue, sg.graph_id, [CHAPTER, CHAPTER_1, BOOK, LECTURE, SEGMENT, BOOK_LINK],
                               [make_edge("c2c2c2c2-0000-4000-8000-000000000002", "b0b0b0b0-0000-4000-8000-00000000000b", "PART_OF"), make_edge("c1c1c1c1-0000-4000-8000-000000000001", "b0b0b0b0-0000-4000-8000-00000000000b", "PART_OF")])

    async def move(node, **props):   # the sibling's node changes (its own lane's write)
        async with open_graph(sib) as sg:
            await graph_task(sg.queue, sg.graph_id, "update_node", node_id=node["id"],
                             properties={**node["properties"], **props})

    def journal(res, relation="DERIVED_FROM"):
        op = {"source_id": res["source_id"], "target_id": res["target_id"], "relation": relation,
              "actor": res["actor"], "observation": res["observation"]}
        if res.get("source_facts"):
            op["source_facts"] = res["source_facts"]
        append_write(jp, "link", op)

    async def live():
        await seed_sibling()
        async with open_graph(str(tmp_path / "live.db")) as gx:
            await _seed(gx, tmp_path / "posts")
            got = {}
            for slug, target in (("ch2", "c2c2c2c2-0000-4000-8000-000000000002"), ("book", "b0b0b0b0-0000-4000-8000-00000000000b"), ("lecture", "1ec1ec1e-0000-4000-8000-00000000001e")):
                got[slug] = await link(gx, note_node_id(slug), f"tx:{target}", "DERIVED_FROM", siblings=siblings)
                journal(got[slug])
            again = await link(gx, note_node_id("lecture"), "tx:1ec1ec1e-0000-4000-8000-00000000001e", "DERIVED_FROM", siblings=siblings)
            # a born notes post: its Point derives from a Segment, its Note names no source yet
            ref = ReferenceNode.observe("tx", SEGMENT, observed_at=1.0).to_graph_node()
            await extend_graph(gx.queue, gx.graph_id, [ref, {"id": "pt-1", "label": "Point",
                                                             "properties": {"owner_id": note_node_id("notes"), "text": "x"}}],
                               [make_edge("pt-1", ref["id"], "DERIVED_FROM")])
            before = await load_sources(gx)
            # the lecture moves to a new URL: the re-link supersedes the observed locator
            await move(LECTURE, public_url="https://www.youtube.com/watch?v=NEW")
            moved = await link(gx, note_node_id("lecture"), "tx:1ec1ec1e-0000-4000-8000-00000000001e", "DERIVED_FROM", siblings=siblings)
            journal(moved)
            # a human's locator is intent: a later observation never supersedes it
            ch2_ref = got["ch2"]["target_id"]
            await assert_value(gx, ch2_ref, P.LOCATOR, "https://example.com/book")
            append_write(jp, "assert", {"subject": ch2_ref, "predicate": P.LOCATOR, "value": "https://example.com/book",
                                        "actor": "agent:session", "evidence": None, "supersede": None})
            await move(CHAPTER, public_url="https://observed/ch2")
            held = await link(gx, note_node_id("ch2"), "tx:c2c2c2c2-0000-4000-8000-000000000002", "DERIVED_FROM", siblings=siblings)
            journal(held)
            # the book's link is removed upstream: the re-link clears the observed links
            await move(BOOK_LINK, source_id="elsewhere")
            cleared = await link(gx, note_node_id("book"), "tx:b0b0b0b0-0000-4000-8000-00000000000b", "DERIVED_FROM", siblings=siblings)
            journal(cleared)
            return got, again, before, moved, held, cleared, await load_sources(gx), await _facts(gx)

    async def replayed():
        async with open_graph(str(tmp_path / "fresh.db")) as gx:
            await _seed(gx, tmp_path / "posts2")
            await replay_journal(gx, jp)
            return await load_sources(gx), await _facts(gx)

    got, again, before, moved, held, cleared, after, facts = asyncio.run(live())
    # a source with no links mints no empty links fact (the op still carries the empty read)
    assert got["ch2"]["facts_asserted"] == [P.CITATION] and got["lecture"]["facts_asserted"] == [P.LOCATOR, P.CITATION]
    assert got["ch2"]["source_facts"][P.RESOURCES] == []
    assert got["book"]["source_facts"] == {P.CITATION: {"author": "Ana Lorena Fábrega", "work": "The Learning Game"},
                                           P.RESOURCES: [{"label": "The book page", "role": "publisher-page",
                                                          "url": "https://pub.example/book"}]}
    assert got["book"]["facts_asserted"] == [P.CITATION, P.RESOURCES]
    # an emptied link set supersedes the observed one with no links
    assert cleared["facts_asserted"] == [P.RESOURCES] and not cleared["noop"]
    book_links = [v for (s, pr, v, m) in facts[0] if pr == P.RESOURCES]
    assert sorted(book_links) == ["[]", P.resources_value(got["book"]["source_facts"][P.RESOURCES])]
    assert len(facts[1]) == 2                                                       # the locator's and the links' supersession
    assert again["noop"] and again["facts_asserted"] == []                       # unchanged: nothing to journal
    lec = before["posts"][note_node_id("lecture")][0]
    assert lec["locator"] == "https://www.youtube.com/watch?v=WiB_3Csfj_Q" and lec["citation"] == {
        "title": "Bonus Lecture: CUDA C++ llm.cpp"} and lec["title"] == "Bonus Lecture： CUDA C++ llm.cpp"
    assert before["derived"] == {note_node_id("notes")} and note_node_id("notes") not in before["posts"]
    assert moved["facts_asserted"] == [P.LOCATOR] and not moved["noop"]
    assert after["posts"][note_node_id("lecture")][0]["locator"] == "https://www.youtube.com/watch?v=NEW"
    assert held["facts_held"] == [{"predicate": P.LOCATOR, "value": "https://observed/ch2",
                                   "held_by": "https://example.com/book"}]
    assert after["posts"][note_node_id("ch2")][0]["locator"] == "https://example.com/book"
    fresh_sources, fresh_facts = asyncio.run(replayed())
    assert fresh_facts == facts                                                     # replay opens no sibling
    assert {k: v for k, v in fresh_sources["posts"].items()} == after["posts"]
