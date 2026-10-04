"""The facet review (design eefda2dd (5), (7)): the review document groups the proposals by entry;
an edited document lands as one batch -- confirmations as facts, every reviewed row marked on its
judged edge -- or not at all when a row's basis moved; a rejected proposal is not proposed again
until its basis changes; a confirmed fact the judge stops backing is challenged; the public
build's gate counts what is stale, unreviewed or bare; and a rebuild replays the review."""

import asyncio
import json
import sqlite3
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import note_node_id

from cjm_context_graph_projection.coverage import mint_entity
from cjm_context_graph_projection.facetjudge import judge_facets, load_facet_judgments, load_facet_vocab
from cjm_context_graph_projection.facetreview import (facet_gate, load_confirmed, parse_review, review_facets,
                                                      review_state)
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.write import assert_value
from conftest import commit_all
from test_facetjudge import _VOCAB, _Judge, _ask, _corpus, _post, _run

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()
pytestmark = pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")


def _toggle(doc: str, post: str, entry: str, box: str) -> str:
    """Set one row's box in a review document."""
    out = []
    for ln in doc.splitlines():
        if f" {post} {entry} " in ln:
            ln = ln.replace("- [x]", f"- [{box}]", 1).replace("- [ ]", f"- [{box}]", 1)
        out.append(ln)
    return "\n".join(out)


def test_a_review_confirms_marks_refuses_stale_and_challenges(tmp_path):
    tut, notes = note_node_id("onnx-export"), note_node_id("unity-detection")

    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            await _corpus(gx, ("onnx-export", "unity-detection"),
                          {"onnx-export": "archive-tutorial", "unity-detection": "archive-notes"})
            # the matrix's structural rows are never facets (the user's ruling on the measuring pass)
            await mint_entity(gx, "task", "general", name="General", fields={"description": "Cross-task",
                                                                              "position": 9, "cross_task": True})
            vocab = await load_facet_vocab(gx)
            general = await assert_value(gx, notes, P.ABOUT_TASK, "general")
            await judge_facets(gx, ask=_ask)
            gate0 = await facet_gate(gx)
            doc = (await review_facets(gx))["document"]
            # the user rejects one proposal and confirms one near miss
            edited = _toggle(_toggle(doc, notes, "task:detection", " "), tut, "subject:gpu", "x")
            dry = await review_facets(gx, apply_text=edited, dry_run=True)
            done = await review_facets(gx, apply_text=edited)
            facts, after, gate1 = await load_confirmed(gx), await review_state(gx), await facet_gate(gx)
            again = await review_facets(gx, apply_text=edited)       # a reapplied document confirms nothing new
            # an entry's criteria move: the old document is refused whole
            await mint_entity(gx, "tool", "unity", name="unity", fields={"description": "Unity engine",
                                                                         "not_for": "a mention"})
            stale_doc = await review_facets(gx, apply_text=edited)

            def doubt(body):   # the judge stops backing the confirmed unity fact
                r = _ask(body)
                if "tool:unity" in r["answers"]:
                    r["answers"]["tool:unity"]["noul"] = 0.1
                return r
            await judge_facets(gx, ask=doubt)
            judged = await load_facet_judgments(gx)
            st = await review_state(gx)
            doc2 = (await review_facets(gx))["document"]
            dropped = await review_facets(gx, apply_text=_toggle(doc2, notes, "tool:unity", " "))
            kept = await review_facets(gx, apply_text=doc2)
            gate2 = await facet_gate(gx)
            return (vocab, general, gate0, doc, dry, done, facts, after, gate1, again, stale_doc, judged, st,
                    doc2, dropped, kept, gate2)
    (vocab, general, gate0, doc, dry, done, facts, after, gate1, again, stale_doc, judged, st, doc2, dropped,
     kept, gate2) = asyncio.run(go())
    assert "task:general" not in vocab and "matrix row" in general["error"]
    assert {g["id"] for g in gate0["unreviewed"]} == {tut, notes} and len(gate0["bare"]) == 2
    rows = parse_review(doc)["rows"]
    assert {(r["post"], r["entry"], r["section"], r["checked"]) for r in rows} == {
        (tut, "tool:onnx", "proposal", True), (notes, "tool:unity", "proposal", True),
        (notes, "task:detection", "proposal", True), (tut, "subject:gpu", "near", False),
        (notes, "subject:gpu", "near", False)}
    assert doc.index("## tool · onnx") < doc.index("## subject · gpu") and "<details>" in doc
    assert dry["counts"] == {"confirmed": 3, "rejected": 1, "kept": 0, "near_unchecked": 1} and not dry["written"]
    assert done["written"] and done["applied"] == {"asserted": 3, "marked": 4}
    assert facts == {tut: {"uses_tool": ["onnx"], "about_subject": ["gpu"]}, notes: {"uses_tool": ["unity"]}}
    # the rejection is marked, so only the unchecked near miss is left to review
    assert [(r["post"], r["entry"], r["section"]) for r in after["rows"]] == [(notes, "subject:gpu", "near")]
    assert gate1["unreviewed"] == [] and gate1["stale"] == [] and gate1["bare"] == []
    assert again["counts"]["confirmed"] == 0 and again["counts"]["kept"] == 3
    assert stale_doc["errors"] and "basis moved" in stale_doc["errors"][0] and not stale_doc["written"]
    # the confirmed pair's judgment is stored below the floor: the challenge's evidence
    unity = next(v["id"] for e, v in (vocab.items()) if e == "tool:unity")
    assert judged[(notes, unity)]["p"] == 0.1
    assert [(r["post"], r["entry"], r["section"]) for r in st["rows"] if r["section"] == "challenged"] == [
        (notes, "tool:unity", "challenged")]
    assert "withdrawal" in dropped["errors"][0] and not dropped["written"]
    assert kept["written"] and kept["counts"]["kept"] >= 1
    assert gate2["unreviewed"] == []


def _rows(db):
    con = sqlite3.connect(str(db))
    try:
        return (sorted(r for r in con.execute(
                    "select id, properties from edges where relation_type = 'JUDGED_FACET'")),
                sorted(r for r in con.execute(
                    "select json_extract(properties, '$.subject_id'), json_extract(properties, '$.predicate'), "
                    "json_extract(properties, '$.value') from nodes where label = 'Assertion' and "
                    "json_extract(properties, '$.predicate') in ('uses_tool', 'about_subject', 'about_task')")))
    finally:
        con.close()


def test_a_rebuild_replays_the_review(tmp_path):
    import os
    corpus = tmp_path / "posts"
    for s in ("onnx-export", "unity-detection"):
        (corpus / s).mkdir(parents=True)
        (corpus / s / "index.md").write_text(_post(s))
    commit_all(corpus)
    journal = str(tmp_path / "writes.jsonl")
    for sub in ("live", "fresh"):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "graph.config.json").write_text(json.dumps(
            {"notes_corpus": str(corpus), "notes_profile": "quarto_post"}))
    live = str(tmp_path / "live" / "g.db")
    base = ["--graph-db-path", live, "--journal-path", journal]
    assert _run(*base, "ingest-notes").returncode == 0
    cmds = [["notes-type", "archive-tutorial", "--title", "T", "--kind", "tutorial", "--origin", "archive"],
            ["notes-type", "archive-notes", "--title", "N", "--kind", "notes", "--origin", "archive"],
            ["assert", note_node_id("onnx-export"), "deliverable_type", "archive-tutorial"],
            ["assert", note_node_id("unity-detection"), "deliverable_type", "archive-notes"]]
    for kind, key, f in _VOCAB:
        cmds.append(["entity", kind, key, "--name", key, "--description", f["description"]]
                    + (["--not-for", f["not_for"]] if "not_for" in f else [])
                    + (["--position", str(f["position"])] if "position" in f else []))
    for cmd in cmds:
        r = _run(*base, *cmd)
        assert r.returncode == 0, (cmd, r.stdout, r.stderr)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Judge)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        env = {**os.environ, "TYPESAFE_API_KEY": "test-key"}
        url = f"http://127.0.0.1:{server.server_address[1]}/v1/systemone"
        r = _run(*base, "judge-facets", "--url", url, "--workers", "2", env=env)
        assert r.returncode == 0, r.stdout + r.stderr
    finally:
        server.shutdown()
    doc = tmp_path / "review.md"
    r = _run(*base, "review-facets", "--out", str(doc))
    assert r.returncode == 0 and "**review** 3 proposal(s) · 0 challenged · 2 near miss(es)" in r.stdout, r.stdout + r.stderr
    doc.write_text(_toggle(doc.read_text(), note_node_id("unity-detection"), "task:detection", " "))
    r = _run(*base, "review-facets", "--apply", str(doc))
    assert r.returncode == 0 and "confirmed 2 · rejected 1" in r.stdout and "marked 3 judgment(s)" in r.stdout, r.stdout
    fresh = str(tmp_path / "fresh" / "g.db")
    r = _run("--graph-db-path", fresh, "--journal-path", journal, "ingest-notes")   # no judge reachable
    assert r.returncode == 0, r.stdout + r.stderr
    assert _rows(fresh) == _rows(live) and len(_rows(live)[1]) == 2
    assert sum(1 for _, p in _rows(live)[0] if "reviewed" in json.loads(p)) == 3
    r = _run("--graph-db-path", fresh, "review-facets")
    assert "**review** 0 proposal(s)" in r.stdout
