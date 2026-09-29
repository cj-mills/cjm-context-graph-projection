"""One resolver for every in-body site link (ruling d31e9ba7): relative targets, anchors onto
Sections or reported, never a dangling edge."""

import asyncio
from pathlib import Path

import pytest

from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_dev_graph_schema.identity import note_node_id, section_node_id
from cjm_dev_graph_schema.predicates import SITE_PATH
from cjm_markdown_decompose_core.extract import note_from_text
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.sitelinks import resolve_site_links
from cjm_context_graph_projection.write import assert_value

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()

FM = "---\ntitle: T\ndate: 2024-01-01\ncategories: [x]\n---\n\n"
PART_2 = FM + ("See [part one](../part-1/), [its setup](../part-1/#setup), [a gone heading](../part-1/#gone), "
               "[nowhere](/posts/nowhere/), [myself](./#more) and [the same place again](/posts/s/part-1/).\n\n"
               "## More\n\nText.\n")
PART_1 = FM + "## Setup\n\nSteps.\n"


@pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")
def test_one_resolver_places_post_links_and_reports_what_it_cannot(tmp_path):
    root = tmp_path / "posts"

    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            notes = [note_from_text(str(root / "s" / p / "index.md"), text, corpus_root=str(root),
                                    profile="quarto_post", lossless=True)
                     for p, text in (("part-2", PART_2), ("part-1", PART_1))]
            nodes, edges = corpus_graph_elements(notes)
            await extend_graph(gx.queue, gx.graph_id, nodes, edges)
            for p in ("part-1", "part-2"):
                await assert_value(gx, note_node_id(f"s/{p}"), SITE_PATH, f"/posts/s/{p}/")
            first = await resolve_site_links(gx)
            again = await resolve_site_links(gx)
            return first, again

    first, again = asyncio.run(go())
    part1, part2 = note_node_id("s/part-1"), note_node_id("s/part-2")
    # relative targets resolve against the linking page's own path; the plain link, its rooted
    # twin and the link whose anchor names no Section all land on the page: ONE edge (the id is
    # the triple; the first link in document order gives its properties); the anchored link
    # lands on the Section
    # (the site_path asserts' live hook already placed them: this pass only confirms)
    assert first["links"] == 6 and first["resolved"] == 2 and first["added"] == 0
    assert [r["target"] for r in first["unresolved"]] == ["/posts/nowhere/"]
    # an anchor naming no Section: the edge stays on the page, the anchor kept, and reported
    assert [(r["target"], r["anchor"]) for r in first["anchors"]] == [("../part-1/#gone", "gone")]
    # the self-link is no cross-reference; a second pass is a no-op
    assert not first["ambiguous"] and again["added"] == 0 and again["removed"] == 0
    assert section_node_id(part1, "setup") != part1


@pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")
def test_a_changed_anchor_re_mints_the_standing_edge(tmp_path):
    root = tmp_path / "posts"

    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            src = FM + "[a](../part-1/#gone)\n"
            notes = [note_from_text(str(root / "s" / p / "index.md"), text, corpus_root=str(root),
                                    profile="quarto_post", lossless=True)
                     for p, text in (("part-2", src), ("part-1", PART_1))]
            nodes, edges = corpus_graph_elements(notes)
            await extend_graph(gx.queue, gx.graph_id, nodes, edges)
            for p in ("part-1", "part-2"):
                await assert_value(gx, note_node_id(f"s/{p}"), SITE_PATH, f"/posts/s/{p}/")
            first = await resolve_site_links(gx)
            # the link now names another missing anchor: same page, same edge id, new anchor
            await graph_task(gx.queue, gx.graph_id, "update_node", node_id=note_node_id("s/part-2"),
                             properties={"site_refs": ["../part-1/#also-gone"]})
            second = await resolve_site_links(gx)
            return first, second

    first, second = asyncio.run(go())
    assert first["resolved"] == 1 and [r["anchor"] for r in first["anchors"]] == ["gone"]
    assert second["added"] == 1 and second["removed"] == 1
    assert [r["anchor"] for r in second["anchors"]] == ["also-gone"]
