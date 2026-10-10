"""The archive's graph-sourced cutover (work item 79703485; the element source unit 56c9c332, design
amendment 56b24fd5): capture, cutover, journal-first authoring, absorb and the rebuild swap's
source-journal fold -- the live db equal to its rebuild after EVERY op (the craft's rule: a later op
that re-derives a note would heal an earlier op's miss and hide it), and the ids unchanged across
the cutover."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cjm_dev_graph_schema.identity import note_node_id, section_node_id
from cjm_markdown_decompose_core.extract import note_from_text

from cjm_context_graph_projection.journal import _merge_windows
from cjm_context_graph_projection.notesource import (birth_records, capture_records, CUTOVER,
                                                    fold_records, ingest_records, NotesFoldError,
                                                    place_records, replay_spans, retire_records,
                                                    section_edit_records)
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS

from conftest import commit_all

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()

_A = ("---\ntitle: \"A\"\ndate: 2024-01-01\ncategories: [tutorial]\n---\n\nLede.\n\n## One\n\nBody one.\n\n"
      "### Two\n\nBody two.\n\n## Three\n\nBody three.\n")
_B = "---\ntitle: \"B\"\n---\n\n## One  \nBody.\n\n\n#### Deep\n\nx\n"   # not canonical: refused at capture


def _fold(text: str, slug: str = "a"):
    note = note_from_text(f"/site/posts/{slug}/index.md", text, corpus_root="/site/posts",
                          profile="quarto_post", lossless=True)
    recs = [{"verb": v, "args": a} for v, a in capture_records(note, f"posts/{slug}/index.md", "c0")]
    recs.append({"verb": CUTOVER, "args": {"note": note.id}})
    return fold_records(recs), note


def _apply(fold, recs):
    assert not isinstance(recs, dict), recs
    for v, a in recs:
        fold.apply({"verb": v, "args": a})


def test_capture_composes_and_the_outline_moves():
    fold, note = _fold(_A)
    nid = note.id
    sid = {a: section_node_id(nid, a) for a in ("_preamble", "one", "two", "three")}
    assert fold.compose(nid) == _A
    _apply(fold, place_records(fold, sid["three"], None, sid["_preamble"]))   # Three before One
    assert fold.compose(nid).index("## Three") < fold.compose(nid).index("## One")
    _apply(fold, place_records(fold, sid["two"], sid["three"], None))        # Two under Three
    text = fold.compose(nid)
    assert text.index("## Three") < text.index("### Two") < text.index("## One")
    _apply(fold, retire_records(fold, sid["two"]))
    assert "Two" not in fold.compose(nid) and fold.elements[sid["two"]].retired
    assert "error" in retire_records(fold, sid["two"])          # a retired element is no live element
    # the birth rule: an address an earlier Section held mints the next generation
    born = birth_records(fold, nid, "### Two\n\nAgain.", sid["one"], None)
    assert born[0][1]["section"] == section_node_id(nid, "two", 1) != sid["two"]
    _apply(fold, born)
    assert "### Two\n\nAgain." in fold.compose(nid)


def test_an_edit_keeps_its_level_and_opens_no_section():
    fold, note = _fold(_A)
    two = section_node_id(note.id, "two")
    assert section_edit_records(fold, two, "### Two\n\nBody two.\n", 3) == []   # unchanged
    assert "level" in section_edit_records(fold, two, "## Two\n\nBody two.\n", 3)["error"]
    assert "add-section" in section_edit_records(fold, two, "### Two\n\nx\n\n### Sneaky\n\ny\n", 3)["error"]
    pre = section_node_id(note.id, "_preamble")
    assert "add-section" in section_edit_records(fold, pre, "Lede.\n\n## Sneaky\n\ny\n", 0)["error"]
    rec = section_edit_records(fold, two, "### Two, Renamed\n\nBody two.\n\n", 3)
    assert rec == [("section", {"section": two, "title": "Two, Renamed", "text": "Body two.",
                                "block_role": ""})]


def test_the_groups_after_a_cutover_replay_and_merge_into_the_writes_windows():
    # design amendment fa61d93a (1): a capture or a cutover folds at ingest; every later group of
    # a cut-over note is a replay window; a group touching both kinds of note is refused
    def post(slug):
        return note_from_text(f"/site/posts/{slug}/index.md", _A, corpus_root="/site/posts",
                              profile="quarto_post", lossless=True)

    def at(ts, recs):
        return [{"verb": v, "ts": ts, "args": a} for v, a in recs]

    a, b = post("a"), post("b")
    two = section_node_id(a.id, "two")
    records = at(1.0, capture_records(a, "posts/a/index.md", "c0")) + at(2.0, [(CUTOVER, {"note": a.id})])
    n = len(records)
    records += at(3.0, section_edit_records(fold_records(records), two, "### Two\n\nEdited.\n", 3))
    records += at(4.0, retire_records(fold_records(records), two))
    records += at(5.0, capture_records(b, "posts/b/index.md", "c1"))   # another note's capture: ingest
    spans = replay_spans(records)
    assert spans == [(n, n + 1), (n + 1, n + 2)]
    assert ingest_records(records, spans) == records[:n] + records[n + 2:]
    mixed = records + at(6.0, [("section", {"section": two, "title": "Two", "text": "x", "block_role": ""}),
                               ("note", {"note": b.id, "path": "posts/b/index.md", "frontmatter": ""})])
    with pytest.raises(NotesFoldError, match="in one group"):
        replay_spans(mixed)
    # (2): merged by ts -- a group sharing a writes window's ts joins it; a pre-ts window keeps its place
    windows = [[{"ts": 0.5}], [{"ts": 3.0}], [{}], [{"ts": 4.5}]]
    merged = [(ts, len(w), span) for ts, w, span in _merge_windows(windows, records, spans)]
    assert merged == [(0.5, 1, None), (3.0, 1, spans[0]), (None, 1, None), (4.0, 0, spans[1]), (4.5, 1, None)]


def _run(*args):
    return subprocess.run([sys.executable, "-m", "cjm_context_graph_projection.cli", *args],
                          capture_output=True, text=True)


def _lines(path: Path) -> int:
    return len(path.read_text().splitlines()) if path.exists() else 0


@pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")
def test_cutover_authoring_and_absorb_keep_live_equal_to_rebuild(tmp_path):
    site = tmp_path / "site"
    for d in ("posts/a", "posts/b"):
        (site / d).mkdir(parents=True)
    (site / "posts/a/index.md").write_text(_A)
    (site / "posts/b/index.md").write_text(_B)
    commit_all(site, "site")
    priv = tmp_path / "private"
    priv.mkdir()
    config = {"notes_corpus": str(site / "posts"), "notes_profile": "quarto_post", "website_root": str(site),
              "journal_path": str(priv / "w.jsonl"), "source_journal_path": str(priv / "s.jsonl")}
    live = tmp_path / "live"
    live.mkdir()
    (live / "graph.config.json").write_text(json.dumps(config))
    db = str(live / "g.db")
    n = [0]

    def cli(*args):
        return _run("--graph-db-path", db, "--journal-path", config["journal_path"], *args)

    def rebuild() -> str:
        n[0] += 1
        d = tmp_path / f"rebuild{n[0]}"
        d.mkdir()
        (d / "graph.config.json").write_text(json.dumps(config))
        r = _run("--graph-db-path", str(d / "g.db"), "--journal-path", config["journal_path"], "ingest-notes")
        assert r.returncode == 0, r.stdout + r.stderr
        return str(d / "g.db")

    def same(step: str) -> str:
        rb = rebuild()
        r = _run("--graph-db-path", db, "rebuild-diff", "--against", rb)
        assert r.returncode == 0, f"{step}: live != rebuild\n{r.stdout}{r.stderr}"
        return rb

    def ok(r, *needles):
        assert r.returncode == 0, r.stdout + r.stderr
        for s in needles:
            assert s in r.stdout, r.stdout

    def ids(path: str) -> set:
        import sqlite3
        con = sqlite3.connect(path)
        try:
            return {r[0] for r in con.execute("select id from nodes")} | {
                r[0] for r in con.execute("select id from edges")}
        finally:
            con.close()

    ok(cli("ingest-notes"))
    pre = rebuild()
    cap = cli("capture-archive")
    assert cap.returncode == 1 and "1 of 2 kept post(s)" in cap.stdout, cap.stdout
    assert "refused `posts/b/index.md`" in cap.stdout
    ok(cli("cutover-archive"), "1 post(s) now graph-sourced")
    cut = same("cutover")
    assert ids(pre) == ids(cut)                                 # the cutover changes no id
    nid = note_node_id("a")
    sid = {a: section_node_id(nid, a) for a in ("_preamble", "one", "two", "three")}
    post = site / "posts/a/index.md"

    ok(cli("author", sid["two"], "--edit", "Body two.", "Body two, edited."))
    assert "Body two, edited." in post.read_text()
    same("author")
    ok(cli("source-check"), "uncommitted 1")
    commit_all(site, "emit")
    ok(cli("source-check"), "CLEAN")

    ok(cli("author", sid["two"], "--edit", "### Two", "### Two Renamed"))   # a rename keeps the id
    same("rename")
    r = cli("author", sid["two"], "--edit", "### Two Renamed", "## Two Renamed")
    assert r.returncode == 1 and "level" in r.stdout, r.stdout
    r = cli("author", sid["two"], "--edit", "Body two, edited.", "Body.\n\n### Sneaky\n\nx")
    assert r.returncode == 1 and "add-section" in r.stdout, r.stdout

    r = cli("place", sid["three"], "--first")                  # before the lede: it would read as Three's text
    assert r.returncode == 1 and "would follow the heading" in r.stdout, r.stdout
    ok(cli("place", sid["three"], "--after", "_preamble"))
    assert post.read_text().index("## Three") < post.read_text().index("## One")
    same("place")
    ok(cli("add-section", "a", "--content", "### Four\n\nBody four.", "--parent", "one", "--after", sid["two"]))
    four = section_node_id(nid, "four")
    assert "### Four\n\nBody four." in post.read_text()
    same("birth")
    ok(cli("retire-section", four))
    assert "Four" not in post.read_text()
    same("retire")
    commit_all(site, "authored")
    ok(cli("source-check"), "CLEAN")

    # The rebuild swap's source-journal fold (finding 6c121287): a record appended while an
    # offline build ran reaches the new db through replay-source.
    mid = rebuild()
    s0 = _lines(priv / "s.jsonl")
    ok(cli("author", sid["one"], "--edit", "Body one.", "Body one, mid-build."))
    r = _run("--graph-db-path", mid, "replay-source", "--offset", str(s0))
    assert r.returncode == 0, r.stdout + r.stderr
    r = _run("--graph-db-path", db, "rebuild-diff", "--against", mid)
    assert r.returncode == 0, r.stdout
    commit_all(site, "mid")

    # An outside edit merged into the website repo: a typo fix and a renamed heading
    text = post.read_text().replace("Body three.", "Body three, fixed.").replace("## One", "## One Again")
    post.write_text(text)
    commit_all(site, "outside")
    r = cli("source-check")
    assert r.returncode == 1 and "drift" in r.stdout, r.stdout
    ok(cli("absorb-archive"), "1 changed", "1 born", "1 retired", "review: heading “One”")
    same("absorb")
    ok(cli("source-check"), "CLEAN")


@pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")
def test_a_born_draft_edit_re_derives_the_whole_note(tmp_path):
    # Finding 1d83a4d8: a section edit on a born draft re-derives level, anchor and the outline
    # edges live, exactly as the replayed section op and the rebuild derive them.
    site = tmp_path / "site"
    (site / "posts").mkdir(parents=True)
    commit_all(site, "empty")
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    config = {"notes_corpus": str(site / "posts"), "notes_profile": "quarto_post", "website_root": str(site),
              "emit_root": str(drafts), "journal_path": str(tmp_path / "w.jsonl"),
              "source_journal_path": str(tmp_path / "s.jsonl")}
    for d in ("live", "rebuilt"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "graph.config.json").write_text(json.dumps(config))
    db, rb = str(tmp_path / "live/g.db"), str(tmp_path / "rebuilt/g.db")

    def cli(*args, at=db):
        r = _run("--graph-db-path", at, "--journal-path", config["journal_path"], *args)
        assert r.returncode == 0, r.stdout + r.stderr
        return r

    cli("ingest-notes")
    text = "---\ntitle: \"D\"\n---\n\n## One\n\nx\n\n## Two\n\ny\n"
    cli("new-note", "--slug", "d", "--content", text)
    two = section_node_id(note_node_id("d"), "two")
    cli("author", two, "--edit", "## Two", "### Two")
    cli("ingest-notes", at=rb)
    r = _run("--graph-db-path", db, "rebuild-diff", "--against", rb)
    assert r.returncode == 0, r.stdout
