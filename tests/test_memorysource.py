"""The memory notes on the element source unit (design c9b29de5, work item 40a35288): the capture
from the graph with its canonicalization (blank lines; an in-body H1 is the title), the memory
lane's replay windows and session touches, and end to end the legacy regime handing over to the
memory journal -- the live db equal to its rebuild after EVERY op, the node ids unchanged across
the cutover, the writes journal no longer growing for a cut-over note."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from cjm_context_graph_projection.journal import _source_spans, touched_node_ids
from cjm_context_graph_projection.memorysource import (capture_memory_records, memory_lane,
                                                       MemorySource, note_lane)
from cjm_context_graph_projection.notesource import fold_records, NotesFoldError
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS
from cjm_dev_graph_schema.identity import note_node_id, section_node_id

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()

_FM = "---\nname: {}\ndescription: d\nmetadata:\n  type: project\n---\n"
_ALPHA = _FM.format("alpha") + "\n# Alpha Title\n\nIntro.\n\n## One\n\nBody one. [[beta]]\n\n## Two\n\nBody two.\n"
_LEDE = _FM.format("lede") + "\nA capture note.\n\n# Lede Title\n\nIntro.\n\n## One\n\nx\n"
_BETA = _FM.format("beta") + "\nLede.\n\n\n## Only\n\nx\n"   # a doubled blank line: not the composed form


def _capture(text):
    note = MemorySource().decompose("", text.encode())[0]
    return capture_memory_records(note.id, text, {s.id for s in note.sections}), note


def test_the_in_body_h1_becomes_the_title_and_its_element_the_opening():
    (recs, row), note = _capture(_ALPHA)
    nid = note.id
    h1 = section_node_id(nid, "alpha-title")
    assert row["change"] == "title" and row["title"] == "Alpha Title"
    fold = fold_records({"verb": v, "args": a} for v, a in recs)
    assert 'title: "Alpha Title"' in fold.notes[nid].frontmatter
    pre = section_node_id(nid, "_preamble")
    assert row["retired"] == [pre] and fold.elements[pre].retired          # the (empty) preamble merged in
    assert fold.retired_members(nid) == [pre]                              # ... and stays homed in its note
    assert fold.elements[h1].title is None and fold.elements[h1].text == "Intro."   # the H1 keeps its id
    one = section_node_id(nid, "one")
    assert fold.elements[one].parent is None and fold.elements[one].after == h1    # its children step up
    assert row["composed"].endswith("\nIntro.\n\n## One\n\nBody one. [[beta]]\n\n## Two\n\nBody two.\n")


def test_a_preamble_before_the_h1_merges_in_first():
    (recs, row), note = _capture(_LEDE)
    fold = fold_records({"verb": v, "args": a} for v, a in recs)
    h1 = section_node_id(note.id, "lede-title")
    assert fold.elements[h1].text == "A capture note.\n\nIntro."
    assert row["retired"] == [section_node_id(note.id, "_preamble")]


def test_blank_lines_take_the_composed_form_and_nothing_else_changes():
    (recs, row), _ = _capture(_BETA)
    assert row["change"] == "blank-lines" and row["composed"] == _BETA.replace("Lede.\n\n\n", "Lede.\n\n")
    (_, row), _ = _capture(_FM.format("plain") + "\n## A\n\nx\n")
    assert row["change"] == "none"
    note = MemorySource().decompose("", _BETA.encode())[0]
    with pytest.raises(NotesFoldError, match="other elements"):
        capture_memory_records(note.id, _BETA, {section_node_id(note.id, "gone")})
    two = _FM.format("two") + "\n# A\n\nx\n\n# B\n\ny\n"
    note = MemorySource().decompose("", two.encode())[0]
    with pytest.raises(NotesFoldError, match="one title per note"):
        capture_memory_records(note.id, two, {s.id for s in note.sections})


def test_the_memory_lane_replays_every_group_and_its_records_are_session_touches(tmp_path):
    (recs, _), note = _capture(_ALPHA)
    j = tmp_path / "m.jsonl"
    j.write_text("".join(json.dumps({"verb": v, "ts": 1.0, "generation": 1, "args": a}) + "\n" for v, a in recs))
    lane = memory_lane({"memory_journal_path": str(j)})
    assert note_lane({"memory_journal_path": str(j)}) == lane and note_lane({"notes_corpus": "x"}) == {"notes_corpus": "x"}
    records, spans = _source_spans(lane, None)
    assert spans == [(0, len(recs))]                  # the capture + cutover group is a window, never ingest
    h1 = section_node_id(note.id, "alpha-title")
    assert touched_node_ids({"verb": "section", "args": {"section": h1, "title": None}}) == [h1]
    assert touched_node_ids({"verb": "place", "args": {"section": h1, "note": note.id}}) == [note.id, h1]
    assert touched_node_ids({"verb": "cutover", "args": {"note": note.id}}) == [note.id]


def _run(*args):
    return subprocess.run([sys.executable, "-m", "cjm_context_graph_projection.cli", *args],
                          capture_output=True, text=True)


def _lines(path: Path) -> int:
    return len(path.read_text().splitlines()) if path.exists() else 0


@pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")
def test_the_memory_notes_hand_over_to_the_memory_journal_live_equal_to_rebuild(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    writes, mj = tmp_path / "w.jsonl", tmp_path / "m.jsonl"
    config = {"memory_dir": str(mem)}
    live = tmp_path / "live"
    live.mkdir()
    db = str(live / "g.db")
    n = [0]

    def configure():
        (live / "graph.config.json").write_text(json.dumps(config))

    def cli(*args, at=db):
        return _run("--graph-db-path", at, "--journal-path", str(writes),
                    "--source-journal-path", str(tmp_path / "s.jsonl"), *args)

    def ingest(at):
        return cli("ingest", "--no-code", "--no-repo-map", "--no-seed", "--memory-dir", str(mem), at=at)

    def rebuild() -> str:
        n[0] += 1
        d = tmp_path / f"rebuild{n[0]}"
        d.mkdir()
        (d / "graph.config.json").write_text(json.dumps(config))
        r = ingest(str(d / "g.db"))
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

    def node_ids(path: str) -> set:
        import sqlite3
        con = sqlite3.connect(path)
        try:
            return {r[0] for r in con.execute("select id from nodes")}
        finally:
            con.close()

    # The legacy regime: born with a file and a genesis op, edited through section ops
    configure()
    ok(ingest(db))
    for slug, text in (("alpha", _ALPHA), ("beta", _BETA)):
        ok(cli("new-note", "--path", str(mem / f"{slug}.md"), "--content", text))
    a, b = note_node_id("alpha"), note_node_id("beta")
    one = section_node_id(a, "one")
    ok(cli("author", one, "--edit", "Body one.", "Body one, legacy."))
    pre = same("legacy")

    # The lane on: the cutover, listed first, then one group
    config["memory_journal_path"] = str(mj)
    configure()
    ok(cli("cutover-memory", "--no-write"), "2 note(s)", "blank-lines 1", "title 1", "title “Alpha Title”",
       "nothing appended")
    assert _lines(mj) == 0
    ok(cli("cutover-memory"), "2 note(s)", "🧬 notes live: 2 note(s)")
    cut = same("cutover")
    assert node_ids(pre) == node_ids(cut)               # the cutover changes no node id (retired ones stay)
    import sqlite3
    con = sqlite3.connect(db)
    retired = {r[0] for r in con.execute("select id from nodes where json_extract(properties, \x27$.retired\x27)")}
    con.close()
    assert retired == {section_node_id(a, "_preamble")}  # the merged preamble: a node still, marked retired
    ok(cli("cutover-memory"), "0 note(s)", "2 already cut over")

    # Journal-first on the memory lane: the writes journal stops growing for these notes
    w0, m0 = _lines(writes), _lines(mj)
    ok(cli("author", one, "--edit", "Body one, legacy.", "Body one, memory lane."))
    same("author")
    ok(cli("author", a, "--edit", "description: d", "description: the alpha note"))   # the Note: its frontmatter
    same("frontmatter")
    r = cli("author", a, "--edit", "name: alpha", "name: renamed")                   # identity: refused
    assert r.returncode == 1 and "decomposes as" in r.stdout, r.stdout
    r = cli("author", a, "--replace", "x")
    assert r.returncode == 1 and "--replace on a Note is refused" in r.stdout, r.stdout
    ok(cli("add-section", "alpha", "--content", "### Sub\n\nBody sub.", "--parent", "one"))
    sub = section_node_id(a, "sub")
    same("birth")
    ok(cli("place", section_node_id(a, "two"), "--top", "--after", section_node_id(a, "alpha-title")))
    same("place")
    ok(cli("retire-section", sub))
    same("retire")
    assert _lines(writes) == w0 and _lines(mj) > m0
    assert not (mem / "alpha.md").read_text().count("memory lane")   # no emit (ruling fed38997)

    # Born on the memory lane: no file, no path; an in-body H1 refused
    r = cli("new-note", "--path", str(mem / "gamma.md"), "--content", _FM.format("gamma") + "\n## G\n\ng\n")
    assert r.returncode == 1 and "has no file" in r.stderr, r.stderr
    r = cli("new-note", "--content", _FM.format("gamma") + "\n# G\n\ng\n")
    assert r.returncode == 1 and "title" in r.stdout, r.stdout
    ok(cli("new-note", "--content", _FM.format("gamma") + "\n## G\n\ng [[alpha]]\n"), "`gamma`")
    assert not (mem / "gamma.md").exists()
    same("new-note")

    # The legacy verbs retire on the lane; a stray file is reported, never ingested
    r = cli("reconcile-memory")
    assert r.returncode == 1 and "retired" in r.stderr, r.stderr
    (mem / "stray.md").write_text(_FM.format("stray") + "\nx\n")
    r = ingest(rebuild().replace("g.db", "g2.db"))
    assert r.returncode == 0 and "stray memory file" in r.stdout and "stray.md" in r.stdout, r.stdout
    (mem / "stray.md").unlink()

    # The rebuild swap's delta: a memory record appended while an offline build ran
    mid = rebuild()
    w1, m1 = _lines(writes), _lines(mj)
    ok(cli("author", one, "--edit", "Body one, memory lane.", "Body one, mid-build."))
    r = cli("replay", "--offset", str(w1), "--memory-offset", str(m1), at=mid)
    assert r.returncode == 0, r.stdout + r.stderr
    r = _run("--graph-db-path", db, "rebuild-diff", "--against", mid)
    assert r.returncode == 0, r.stdout
