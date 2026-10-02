"""The Library's pages (design 638b7b85): a Lens with the `library` layout projects the index --
the topic line, works grouped by form, each with its outputs by class -- and every work with
units gets a work page at its site_path, an ordered collection the post navigation walks one
output class at a time; the public profile shows only public outputs, staging marks the drafts;
a work whose units show with no page path refuses."""

import asyncio
from pathlib import Path

import pytest

from cjm_context_graph_layer.ops import extend_graph
from cjm_dev_graph_schema.identity import entity_node_id, note_node_id
from cjm_markdown_decompose_core.extract import note_from_text
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from cjm_context_graph_projection.coverage import mint_entity
from cjm_context_graph_projection.lens import lens_node_id, set_lens
from cjm_context_graph_projection.library import record_provenance
from cjm_context_graph_projection.librarypage import (render_index, render_work, topic_pages, work_description,
                                                      year)
from cjm_context_graph_projection.purenotes import mint_deliverable_type
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.site import redirect_plan
from cjm_context_graph_projection.sitepages import GENERATED, page_plan
from cjm_context_graph_projection.write import assert_value

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()


def test_the_index_groups_works_and_links_their_outputs():
    groups = [{"form": "lecture-series", "heading": "Lecture series", "works": [
                  {"name": "GPU MODE", "author": "A & B", "year": "", "page": "gpu-mode/index.qmd",
                   "classes": [{"name": "Notes", "outputs": [{"title": "L1", "href": "x", "public": True}] * 2}]}]},
              {"form": "book", "heading": "Books", "works": [
                  {"name": "The [Kill] Chain", "author": "C", "year": "2020", "page": None,
                   "classes": [{"name": "Notes", "outputs": [{"title": "Notes on it", "href": "../posts/k/index.md",
                                                              "public": False}]}]}]}]
    topics = [{"title": "Books", "href": "../series/notes/book-notes.qmd"}]
    pub = render_index(groups, topics, "public")
    assert pub.startswith("**By topic:** [Books](../series/notes/book-notes.qmd)\n")
    assert "## Lecture series {#lecture-series}" in pub and "## Books {#book}" in pub
    assert "- **[GPU MODE](gpu-mode/index.qmd)** · A & B — Notes: [2 pages](gpu-mode/index.qmd)" in pub
    assert "- **The \\[Kill\\] Chain** · C · 2020 — Notes: [Notes on it](../posts/k/index.md)\n" in pub
    assert "_(draft)_" in render_index(groups, topics, "staging")
    assert year("2019-10-08") == "2019" and year(None) == ""


def test_the_work_page_card_units_and_synopses():
    o = lambda t, cls="Notes", public=True, syn="": {"title": t, "href": f"../../posts/{t}/index.md",
                                                     "public": public, "class_name": cls, "synopsis": syn}
    work = {"form": "lecture-series", "author": "A & B", "year": "2024", "isbn": "", "locator": "https://y.t/p"}
    units = [{"name": "Lecture 1", "part": "Part I", "sources": [], "outputs": [o("l1", syn="What it says.")]},
             {"name": "Lecture 2", "part": "Part I", "sources": ["r1", "r2"], "outputs": [o("l2", public=False)]},
             {"name": "Bonus", "part": "Part II", "sources": ["r3"],
              "outputs": [o("b-notes"), o("b-res", cls="Standalone resources")]}]
    pub = render_work(work, units[:1], [], "public")
    assert pub.startswith("::: {.library-work-card}\nLecture series · A & B · 2024 · [Link](https://y.t/p)\n:::\n")
    assert "## Contents" in pub and "### Part I" in pub
    assert "- [Lecture 1](../../posts/l1/index.md) — What it says.\n" in pub
    stg = render_work(work, units, [o("whole")], "staging")
    # several classes on the page: each output names its class; staging marks drafts and sources
    assert "- [Lecture 2](../../posts/l2/index.md) · *Notes* _(draft)_ _(2 sources)_\n" in stg
    one = render_work(work, [{**units[0], "sources": ["r1"]}], [], "staging")
    assert "- [Lecture 1](../../posts/l1/index.md) _(1 source)_ — What it says.\n" in one
    assert ("- Bonus _(1 source)_ — [b-notes](../../posts/b-notes/index.md) · *Notes* · "
            "[b-res](../../posts/b-res/index.md) · *Standalone resources*") in stg
    assert "## On the whole work\n\n- [whole](../../posts/whole/index.md) · *Notes*" in stg
    assert "\n\n\n" not in stg
    # parts that interleave keep the work's order: no headings, each row labelled by its part
    course = [{"name": "Workshop 1", "part": "Workshops", "sources": [], "outputs": [o("w1")]},
              {"name": "Office Hours 1", "part": "Office Hours", "sources": [], "outputs": [o("h1")]},
              {"name": "Workshop 2", "part": "Workshops", "sources": [], "outputs": [o("w2")]}]
    mixed = render_work(work, course, [], "public")
    assert "###" not in mixed and mixed.index("Workshop 1") < mixed.index("Office Hours 1") < mixed.index("Workshop 2")
    assert "- *Workshops* · [Workshop 1](../../posts/w1/index.md)\n" in mixed
    assert work_description({"name": "GPU MODE", "form": "lecture-series", "author": "A"},
                            ["Notes", "Standalone resources"]) == \
        "Notes and standalone resources from *GPU MODE*, a lecture series by A, in the work's own order."


def test_topic_pages_are_all_notes_lens_pages_and_a_mix_refuses():
    types = {"n1": {"kind": "notes"}, "n2": {"kind": "notes"}, "t1": {"kind": "tutorial"}}
    planned = [{"kind": "Lens", "source": "series/notes/b.qmd", "subject": "B", "title": "Books", "listed": ["n1"]},
               {"kind": "Series", "source": "series/notes/s.qmd", "subject": "S", "title": "S", "listed": ["n2"],
                "sequence": True},
               {"kind": "Lens", "source": "series/tutorials/t.qmd", "subject": "T", "title": "T", "listed": ["t1"]}]
    planned[0]["listed"].append("fixture")   # an untyped member (a staging fixture) decides nothing
    ok = topic_pages(planned, types, "library/index.qmd")
    assert ok == {"topics": [{"title": "Books", "href": "../series/notes/b.qmd"}], "errors": []}
    mixed = topic_pages(planned + [{"kind": "Lens", "source": "x.qmd", "subject": "X", "title": "X",
                                    "listed": ["n1", "t1"]}], types, "library/index.qmd")
    assert [(e["kind"], e["subject"]) for e in mixed["errors"]] == [("topic-mixed", "X")]


def _post(title: str, day: str) -> str:
    return f"---\ntitle: \"{title}\"\ndate: {day}\ndescription: \"About {title}.\"\n---\n\n## Overview\n\nBody.\n"


@pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")
def test_the_library_lens_projects_the_index_and_the_work_pages(tmp_path):
    root = tmp_path / "site"
    posts = {"gpu-1": ("Lecture 1 notes", "2024-02-01"), "gpu-2": ("Lecture 2 notes", "2024-03-01"),
             "kill-chain": ("Notes on The Kill Chain", "2023-05-01"), "solo-1": ("Solo ch 1", "2022-01-01")}
    for s, (t, d) in posts.items():
        (root / "posts" / s).mkdir(parents=True)
        (root / "posts" / s / "index.md").write_text(_post(t, d))

    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            notes = [note_from_text(str(root / "posts" / s / "index.md"), (root / "posts" / s / "index.md").read_text(),
                                    corpus_root=str(root / "posts"), lossless=True) for s in posts]
            nodes, edges = corpus_graph_elements(notes)
            await extend_graph(gx.queue, gx.graph_id, nodes, edges)
            await mint_entity(gx, "output_class", "notes", name="Notes", fields={"position": 4})
            await mint_deliverable_type(gx, "archive-notes", title="N", kind="notes", origin="archive",
                                        output_class="notes")
            for s in ("gpu-1", "gpu-2", "kill-chain"):
                await _assert(gx, s, "deliverable_type", "archive-notes")
            await _assert(gx, "gpu-2", "publish_state", "draft")
            await mint_entity(gx, "work", "gpu-mode", name="GPU MODE", fields={"form": "lecture-series",
                                                                              "author": "A & B"})
            await mint_entity(gx, "work", "kill-chain", name="The Kill Chain",
                              fields={"form": "book", "author": "C", "published": "2020"})
            for n in (1, 2):
                await mint_entity(gx, "unit", f"gpu-mode/gpu-{n}", name=f"Lecture {n}", fields={"position": n})
                await record_provenance(gx, f"gpu-{n}", f"gpu-mode/gpu-{n}")
            await record_provenance(gx, "kill-chain", "kill-chain")
            await set_lens(gx, "books", {"selection": [{"verb": "list", "args": {"label": "Note",
                                                                                 "deliverable_kind": "notes"}}]},
                           title="Books")
            await _assert(gx, lens_node_id("books"), "site_path", "/series/notes/book-notes.html", raw=True)
            spec = {"selection": [{"verb": "list", "args": {"label": "Entity", "where": ["entity_kind=work"]}}],
                    "view": {"layout": "library"}}
            await set_lens(gx, "library", spec, title="Library", description="What came of each work.")
            await _assert(gx, lens_node_id("library"), "site_path", "/library/", raw=True)
            await _assert(gx, entity_node_id("work", "gpu-mode"), "site_path", "/library/gpu-mode/", raw=True)
            pages = (await redirect_plan(gx))["pages"]
            pub = await page_plan(gx, str(root), "public", pages, drafts_dir=None)
            stg = await page_plan(gx, str(root), "staging", pages, drafts_dir=None)
            # A work whose units show with no page path refuses the build, never drops
            await mint_entity(gx, "work", "solo", name="Solo", fields={"form": "book"})
            await mint_entity(gx, "unit", "solo/solo-1", name="Ch 1", fields={"position": 1})
            await _assert(gx, "solo-1", "deliverable_type", "archive-notes")
            await record_provenance(gx, "solo-1", "solo/solo-1")
            refused = await page_plan(gx, str(root), "public", pages, drafts_dir=None)
            return pub, stg, refused
    pub, stg, refused = asyncio.run(go())
    assert pub["errors"] == [], pub["errors"]
    by_src = {p["source"]: p for p in pub["pages"]}
    index = by_src["library/index.qmd"]["text"]
    assert index.startswith(f"---\n{GENERATED}\ntitle: Library\ndescription: What came of each work.\n")
    assert "**By topic:** [Books](../series/notes/book-notes.qmd)" in index
    assert "- **[GPU MODE](gpu-mode/index.qmd)** · A & B — Notes: [1 page](gpu-mode/index.qmd)" in index
    assert "- **The Kill Chain** · C · 2020 — Notes: [Notes on The Kill Chain](../posts/kill-chain/index.md)" in index
    assert index.index("## Books") < index.index("## Lecture series")   # the slate's order
    work = by_src["library/gpu-mode/index.qmd"]
    assert work["sequence"] and work["listed"] == [note_node_id("gpu-1")] and work["title"] == "GPU MODE"
    assert work["groups"] == {note_node_id("gpu-1"): "notes"} and work["kind"] == "work"
    assert "- [Lecture 1](../../posts/gpu-1/index.md)\n" in work["text"] and "Lecture 2" not in work["text"]
    assert "description: Notes from *GPU MODE*, a lecture series by A & B, in the work's own order." in work["text"]
    staged = {p["source"]: p for p in stg["pages"]}
    assert "- [Lecture 2](../../posts/gpu-2/index.md) _(draft)_" in staged["library/gpu-mode/index.qmd"]["text"]
    assert "Notes: [2 pages](gpu-mode/index.qmd)" in staged["library/index.qmd"]["text"]
    assert [(e["kind"], e.get("work")) for e in refused["errors"]] == [("work-unpaged", "solo")]


async def _assert(gx, subject, predicate, value, raw=False, supersede=None):
    res = await assert_value(gx, subject if raw else note_node_id(subject), predicate, value, supersede=supersede)
    assert not res.get("error"), res
    return res
