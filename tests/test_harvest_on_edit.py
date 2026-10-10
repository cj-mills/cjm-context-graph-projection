"""Harvest-on-edit (finding cbde404c): relation edges follow an on-graph EDIT, not just birth.

Ingest and `new_note` harvest a note's `[[wiki-links]]` / cross-post links (REFERENCES),
categories (TAGGED) and series-page links (the Note's `site_refs`, resolved to `site_link`
REFERENCES through site_path facts — DEC 72d669c5) once; the edit verbs used to rewrite the
slot only, so a link added on-graph minted no edge until a rebuild and a removed link's edge
lingered. `reharvest_note_relations` diffs the note's PRIOR text against its NEW text at every
edit seam (`author` on a Section, `author_section`, `add_section`) and applies exactly that
difference — never touching a deliberate `link` the content never produced — so a journal-only
rebuild converges on the edge set an archive ingest of the emitted file produces.
"""
import asyncio
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.journal import read_journal
from cjm_context_graph_primitives.query import EdgeQuery
from cjm_dev_graph_schema.identity import note_node_id, section_node_id, series_node_id
from cjm_dev_graph_schema.predicates import SITE_PATH
from cjm_markdown_decompose_core.extract import note_from_file
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from cjm_context_graph_projection import factlayer as F
from cjm_context_graph_projection.authoring import author
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.series import mint_series
from cjm_context_graph_projection.sitelinks import site_link_window
from cjm_context_graph_projection.structure import add_section, new_note
from cjm_context_graph_projection.write import assert_value, author_section, link
from conftest import commit_all

# These drive the real graph-storage worker capability via open_graph().
# Skip wherever its manifest isn't discoverable (e.g. CI).
pytestmark = pytest.mark.skipif(
    not (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists(),
    reason=f"graph capability {DEFAULT_GRAPH_ID!r} not installed at {DEFAULT_MANIFESTS}",
)

MEMO_A = "---\nname: note-a\ndescription: d\n---\n\nIntro.\n\n## Alpha\n\nAlpha body.\n"
MEMO_C = "---\nname: note-c\ndescription: d\n---\n\nC body.\n"
MEMO_D = "---\nname: note-d\ndescription: d\n---\n\nD body.\n"
LINKED = "## Alpha\n\nAlpha body, see [[note-d]].\n"
POST = ("---\ntitle: \"Born post\"\ndate: 2026-09-04\ncategories: [notes]\n---\n\n"
        "Lede.\n\n## First\n\nBody one.\n")
LINKS = "See [the series](/series/notes/education-notes.html) and [other](/posts/other-post/#intro)."


async def _ingest_memory(gx, mem: Path):
    notes = [note_from_file(str(p), corpus_root=str(mem), lossless=True)
             for p in sorted(mem.glob("*.md"))]
    nodes, edges = corpus_graph_elements(notes)
    await extend_graph(gx.queue, gx.graph_id, nodes, edges)


async def _targets(gx, source_id: str, relation: str) -> set:
    q = EdgeQuery(relation_type=relation, source_ids=[source_id], project=["id"])
    res = await graph_task(gx.queue, gx.graph_id, "query_edges", query=q.to_dict())
    return {r["target_id"] for r in (res.rows or [])}


def test_author_on_section_mints_and_retracts_wiki_link_edges_but_keeps_deliberate_links(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    (mem / "a.md").write_text(MEMO_A)
    (mem / "c.md").write_text(MEMO_C)
    (mem / "d.md").write_text(MEMO_D)   # the wiki-link target (the store drops a dangling edge)
    a, c, d = note_node_id("note-a"), note_node_id("note-c"), note_node_id("note-d")
    alpha = section_node_id(a, "alpha")

    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            await _ingest_memory(gx, mem)
            assert await _targets(gx, a, "REFERENCES") == set()
            # A deliberate link the CONTENT never produced — the diff must never touch it.
            await link(gx, a, c, "REFERENCES")
            r1 = await author(gx, alpha, replace=LINKED, write=True)
            after_add = await _targets(gx, a, "REFERENCES")
            assert (mem / "a.md").read_text().endswith(LINKED)   # the .md emitted the edit
            r2 = await author(gx, alpha, replace="## Alpha\n\nAlpha body.\n", write=True)
            after_remove = await _targets(gx, a, "REFERENCES")
            r3 = await author(gx, alpha, replace="## Alpha\n\nAlpha body.\n", write=True)
            return r1, after_add, r2, after_remove, r3

    r1, after_add, r2, after_remove, r3 = asyncio.run(go())
    assert r1["written"] and r1["relations"]["added"] == [("REFERENCES", d)]
    assert after_add == {d, c}                      # the wiki-link landed; the deliberate link stands
    assert r2["relations"]["removed"] == [("REFERENCES", d)] and r2["relations"]["added"] == []
    assert after_remove == {c}                      # retracted; the deliberate link untouched
    assert r3["unchanged"] and "relations" not in r3   # a no-op edit harvests nothing


def test_born_post_add_section_and_author_section_follow_series_and_post_links(tmp_path):
    root = tmp_path / "emit"
    (root / "born").mkdir(parents=True)
    path = str(root / "born" / "index.md")
    note_id = note_node_id("born")
    series = series_node_id("education-notes")
    other_intro = section_node_id(note_node_id("other-post"), "intro")

    (root / "other-post").mkdir()
    other = ("---\ntitle: \"Other\"\ndate: 2026-09-04\ncategories: [notes]\n---\n\n"
             "## Intro\n\nOther body.\n")

    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            born = await new_note(gx, path, POST, profile="quarto_post",
                                  corpus_root=str(root), slug="born")
            assert born.get("written"), born
            # The post TARGET exists and holds its public path: the one resolver maps a post
            # link through site_path facts like any site link (ruling d31e9ba7).
            assert (await new_note(gx, str(root / "other-post" / "index.md"), other,
                                   profile="quarto_post", corpus_root=str(root),
                                   slug="other-post")).get("written")
            await assert_value(gx, note_node_id("other-post"), SITE_PATH, "/posts/other-post/")
            # The series page is a born Series holding its public path (DEC 72d669c5).
            await mint_series(gx, "education-notes", title="Education")
            await assert_value(gx, series, SITE_PATH, "/series/notes/education-notes.html")
            note = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=note_id)
            profile = F.prop(note, "profile")
            links1, links2 = [], []
            async with site_link_window(gx, report=links1.append):   # one live write window each
                r1 = await add_section(gx, "born", f"## More\n\n{LINKS}\n")
            in_series = await _targets(gx, note_id, "IN_SERIES")
            refs = await _targets(gx, note_id, "REFERENCES")
            async with site_link_window(gx, report=links2.append):
                r2 = await author_section(gx, "born", "more", "## More\n\nNothing here.\n")
            refs_after = await _targets(gx, note_id, "REFERENCES")
            series_after = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=series)
            tagged = await _targets(gx, note_id, "TAGGED")
            return profile, r1, in_series, refs, r2, refs_after, series_after, tagged, links1[0], links2[0]

    (profile, r1, in_series, refs, r2, refs_after, series_after, tagged,
     links1, links2) = asyncio.run(go())
    assert profile == "quarto_post"                 # the born profile is readable at edit time
    # Both links are verbatim site_refs the window's step resolves (ruling d31e9ba7, 9ee4e346): the series
    # page onto the Series — never IN_SERIES membership — and the anchored post link onto the
    # Section its anchor names; neither is an ingest edge any more.
    assert set(r1["relations"]["added"]) == set()
    assert r1["relations"]["site_refs"] == ["/series/notes/education-notes.html", "/posts/other-post/#intro"]
    assert links1["added"] == 2 and not links1["anchors"]
    assert in_series == set() and refs == {other_intro, series}
    assert Path(path).read_text().endswith(f"{LINKS}\n")
    assert set(r2["relations"]["removed"]) == set()
    assert links2["removed"] == 2
    assert refs_after == set()
    assert series_after is not None                  # a born Series is not a facet to drop
    assert len(tagged) == 1                          # the birth category's TAGGED edge untouched


def _run(*args):
    return subprocess.run([sys.executable, "-m", "cjm_context_graph_projection.cli", *args],
                          capture_output=True, text=True)


def _content_ids(db):
    kinds = "('Note','Section','Topic','Series')"
    con = sqlite3.connect(str(db))
    try:
        nodes = sorted(r[0] for r in con.execute(f"select id from nodes where label in {kinds}"))
        edges = sorted((r[0], r[1]) for r in con.execute(
            "select e.id, e.relation_type from edges e join nodes s on s.id = e.source_id "
            f"join nodes t on t.id = e.target_id where s.label in {kinds} and t.label in {kinds}"))
        return nodes, edges
    finally:
        con.close()


def test_journal_replay_after_an_edit_matches_archive_ingest_of_the_emitted_post(tmp_path):
    # The round-trip standard, now across an EDIT: born post -> CLI author adds a series link
    # -> the journal-only projection (what a rebuild replays) carries the same Note/Section/
    # Topic/Series nodes AND relation edges as an archive ingest of the emitted tree.
    emit = tmp_path / "emit"
    journal = str(tmp_path / "notes.writes.jsonl")
    (tmp_path / "graph.config.json").write_text(json.dumps(
        {"notes_profile": "quarto_post", "emit_root": str(emit), "notes_corpus": str(emit)}))
    r = _run("--graph-db-path", str(tmp_path / "notes.db"), "--journal-path", journal,
             "new-note", "--slug", "born-post", "--content", POST)
    assert r.returncode == 0, r.stderr or r.stdout
    first = section_node_id(note_node_id("born-post"), "first")
    r = _run("--graph-db-path", str(tmp_path / "notes.db"), "--journal-path", journal,
             "--source-journal-path", str(tmp_path / "notes.source.jsonl"),
             "author", first, "--replace", f"## First\n\nBody one. {LINKS}\n")
    assert r.returncode == 0, r.stderr or r.stdout
    assert [o["verb"] for o in read_journal(journal)] == ["new-note", "assert", "section"]
    assert LINKS in (emit / "born-post" / "index.md").read_text()

    r2 = _run("--graph-db-path", str(tmp_path / "replay.db"), "--journal-path", journal, "replay")
    assert r2.returncode == 0, r2.stderr or r2.stdout
    commit_all(emit)   # the archive is HEAD (19edbe97)
    r3 = _run("--graph-db-path", str(tmp_path / "ingest.db"), "ingest-notes")
    assert r3.returncode == 0, r3.stderr or r3.stdout
    replayed, ingested = _content_ids(tmp_path / "replay.db"), _content_ids(tmp_path / "ingest.db")
    assert replayed == ingested
    assert "IN_SERIES" not in {rel for _, rel in replayed[1]}   # a link is never membership
    # With no page holding that path, both projections report the link unresolved alike.
    for db in ("replay.db", "ingest.db"):
        r4 = _run("--graph-db-path", str(tmp_path / db), "site-links")
        assert r4.returncode == 2 and "unresolved: `born-post` -> /series/notes/education-notes.html" in r4.stdout


def test_a_live_insert_keeps_the_outline_relations_an_ingest_derives(tmp_path):
    # Order and nesting are relations (design 56c9c332 (3)): a section inserted mid-note re-points
    # its preceding sibling's NEXT and re-parents the sub-section it now encloses -- live -- so the
    # live graph carries exactly the PART_OF / NEXT edges an ingest of the emitted post derives.
    emit = tmp_path / "emit"
    journal = str(tmp_path / "notes.writes.jsonl")
    (tmp_path / "graph.config.json").write_text(json.dumps(
        {"notes_profile": "quarto_post", "emit_root": str(emit), "notes_corpus": str(emit)}))
    live = str(tmp_path / "notes.db")
    post = POST + "\n### Sub\n\nSub body.\n\n## Last\n\nEnd.\n"
    r = _run("--graph-db-path", live, "--journal-path", journal, "new-note", "--slug", "born-post", "--content", post)
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run("--graph-db-path", live, "--journal-path", journal, "add-section", "born-post",
             "--after", "first", "--content", "## Middle\n\nMid.\n")
    assert r.returncode == 0, r.stderr or r.stdout
    commit_all(emit)   # the archive is HEAD (19edbe97)
    r = _run("--graph-db-path", str(tmp_path / "ingest.db"), "ingest-notes")
    assert r.returncode == 0, r.stderr or r.stdout
    sid = lambda a: section_node_id(note_node_id("born-post"), a)

    def outline(db):
        con = sqlite3.connect(db)
        try:
            return sorted(con.execute("select source_id, relation_type, target_id from edges "
                                      "where relation_type in ('PART_OF', 'NEXT')"))
        finally:
            con.close()

    assert outline(live) == outline(str(tmp_path / "ingest.db"))
    assert (sid("first"), "NEXT", sid("middle")) in outline(live)
    assert (sid("middle"), "NEXT", sid("last")) in outline(live)
    assert (sid("sub"), "PART_OF", sid("middle")) in outline(live)       # re-parented, live
    assert (sid("sub"), "PART_OF", sid("first")) not in outline(live)    # the stale edge is gone
