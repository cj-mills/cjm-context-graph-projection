"""The concept pilot (ruling ad37f62a): drafted concepts confirmed through the vocabulary review; the
relations judged one Choice per (post, concept) -- one COVERS Noul per (unit, concept) through the
unit's notes -- with per-pair staleness; the relation review confirms through the relate write path;
a rebuild replays the vocabulary, the run and the review without the judge."""

import asyncio
import json
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cjm_context_graph_layer.ops import extend_graph
from cjm_context_graph_projection.conceptjudge import (concept_question, concept_scope,
                                                       concept_view, criteria_hash, judge_concepts,
                                                       load_concept_judgments,
                                                       load_confirmed_relations, measure,
                                                       plan_requests, section_prose, stale_pairs)
from cjm_context_graph_projection.conceptreview import (merge_drafts, parse_relations, parse_vocab,
                                                        review_concepts)
from cjm_context_graph_projection.coverage import mint_entity
from cjm_context_graph_projection.library import record_provenance
from cjm_context_graph_projection.purenotes import mint_deliverable_type
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.write import assert_value
from cjm_context_graph_projection.writes import InvocationWrites
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import entity_node_id, note_node_id
from cjm_markdown_decompose_core.extract import note_from_text
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from conftest import commit_all

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()
pytestmark_graph = pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")

_C = {"key": "nms", "name": "Non-maximum suppression", "description": "Dropping overlapping boxes", "not_for": "IoU"}


def test_the_questions_and_their_hashes():
    q = concept_question(_C, "post")
    assert q["type"] == "choice" and list(q["criteria"]) == ["teaches", "assumes", "touches", "unrelated"]
    assert "Dropping overlapping boxes" in q["instructions"] and "Not to be confused with: IoU" in q["instructions"]
    u = concept_question(_C, "unit")
    assert u["type"] == "noul" and set(u["criteria"]) == {"true", "false"}
    h = criteria_hash(q)
    assert criteria_hash(concept_question({**_C, "description": "Other"}, "post")) != h   # the concept's edit re-asks it
    assert criteria_hash(u) != h


def test_the_judged_state_reduces_code_and_caps():
    text = "Intro [link](http://x) here.\n\n```python\nimport torch\nx = 1\n```\n\nAfter."
    assert section_prose(text) == "Intro link here. [code: python, 2 line(s)] After."
    v = concept_view("T", "D", "tutorial", [(2, "One", text), (2, "Two", "y" * 5000)])
    assert v["outline"] == ["## One", "## Two"] and v["code"]["python_imports"] == ["torch"]
    assert v["sections"][1]["text"].endswith(" …") and len(v["sections"][1]["text"]) == 4002


def test_staleness_per_pair_and_request_planning():
    from cjm_context_graph_projection.judgeengine import digest
    view = {"title": "T"}
    crit = {"s": {"a": "h1", "b": "h2"}}
    assert stale_pairs({"s": view}, crit, {}) == {"s": ["a", "b"]}
    rec = {"s": {"state": digest(view), "criteria": {"a": "h1", "b": "old"}}}
    assert stale_pairs({"s": view}, crit, rec) == {"s": ["b"]}
    assert stale_pairs({"s": {"title": "U"}}, crit, rec) == {"s": ["a", "b"]}   # a moved state re-asks all
    qs = {"s": {k: {"type": "noul", "instructions": "x" * 400} for k in "abcde"}}
    assert plan_requests({"s": list("abcde")}, {"s": view}, qs) == [("s", list("abcde"))]
    split = plan_requests({"s": list("abcde")}, {"s": view}, qs, budget=250)
    assert [ks for _, ks in split] == [["a", "b"], ["c", "d"], ["e"]]


def test_drafts_merge_by_key():
    m = merge_drafts([{"title": "A", "concepts": [{**_C, "relation": "teaches", "where": "## NMS"}]},
                      {"title": "B", "concepts": [{**_C, "description": "Keeping the best box", "relation": "assumes"},
                                                  {"key": "bad key", "name": "x"}]}])
    row = m["concepts"]["nms"]
    assert row["description"] == "Dropping overlapping boxes" and row["alts"] == ["Keeping the best box"]
    assert [d["relation"] for d in row["drafted"]] == ["teaches", "assumes"] and len(m["errors"]) == 1


def _post(slug: str, body: str) -> str:
    return f"---\ntitle: \"{slug}\"\ndescription: \"About {slug}\"\ndate: 2024-01-01\n---\n\n{body}\n"


_POSTS = {"a-train": _post("a-train", "## Introduction\n\nWe train a detector.\n\n## Non-Maximum Suppression\n\n"
                                      "How NMS drops boxes.\n"),
          "b-export": _post("b-export", "## Export\n\nRun NMS after export, as before.\n"),
          "c-notes": _post("c-notes", "## Kernels\n\nA CUDA kernel runs on many threads.\n")}
# The deterministic judge: (post slug, concept key) -> the top option; a unit's notes cover what is listed
_TRUTH = {("a-train", "nms"): "teaches", ("a-train", "cnn"): "assumes", ("b-export", "nms"): "assumes",
          ("b-export", "onnx"): "teaches"}
_COVERS = {("c-notes", "cuda-kernel")}
_DRAFTS = [{"source": "a-train", "title": "a-train", "concepts": [
               {**_C, "area": "task:object-detection", "relation": "teaches", "where": "## Non-Maximum Suppression"},
               {"key": "cnn", "name": "Convolutional networks", "description": "Networks of convolutions",
                "not_for": "Transformers", "subject": "deep-learning-foundations", "relation": "assumes"}]},
           {"source": "b-export", "title": "b-export", "concepts": [
               {**_C, "area": "task:object-detection", "relation": "assumes"},
               {"key": "onnx", "name": "ONNX export", "description": "Saving a model as ONNX", "not_for": "TorchScript",
                "subject": "deep-learning-foundations", "relation": "teaches"},
               {"key": "drop-me", "name": "Drop me", "description": "A draft the user drops", "not_for": "-",
                "subject": "deep-learning-foundations", "relation": "touches"}]},
           {"source": "c-notes", "title": "c-notes", "concepts": [
               {"key": "cuda-kernel", "name": "CUDA kernels", "description": "Functions run on the GPU",
                "not_for": "Shaders", "subject": "gpu-programming", "relation": "covers",
                "subject_new": {"name": "GPU programming", "description": "Code on the GPU", "not_for": "Hardware"}}]}]


def _ask(body):
    title = body["state"]["post"]["title"]
    answers = {}
    for k, q in body["questions"].items():
        if q["type"] == "noul":
            answers[k] = {"type": "noul", "noul": 0.9 if (title, k) in _COVERS else 0.05}
        else:
            top = _TRUTH.get((title, k), "unrelated")
            probs = {o: 0.04 for o in q["criteria"]}
            probs[top] = 0.88
            answers[k] = {"type": "choice", "choice": top, "probabilities": probs, "confidence": 0.9}
    return {"model": "jev-test", "usage": {"input_tokens": 5}, "answers": answers}


async def _corpus(gx):
    notes = [note_from_text(f"/c/posts/{s}/index.md", t, corpus_root="/c/posts", lossless=True) for s, t in _POSTS.items()]
    nodes, edges = corpus_graph_elements(notes)
    await extend_graph(gx.queue, gx.graph_id, nodes, edges)
    await mint_deliverable_type(gx, "archive-tutorial", title="A", kind="tutorial", origin="archive",
                                information_policy={"concept_relations": ["TEACHES", "ASSUMES"]})
    await mint_deliverable_type(gx, "archive-notes", title="N", kind="notes", origin="archive",
                                information_policy={"concept_relations": ["COVERS"]})
    for s, t in (("a-train", "archive-tutorial"), ("b-export", "archive-tutorial"), ("c-notes", "archive-notes")):
        await assert_value(gx, note_node_id(s), "deliverable_type", t)
    await mint_entity(gx, P.ENTITY_SUBJECT, "deep-learning-foundations", name="Deep learning foundations",
                      fields={"description": "How networks work", "not_for": "Tools"})
    await mint_entity(gx, P.ENTITY_TASK, "object-detection", name="Object detection", fields={"position": 1})
    await mint_entity(gx, P.ENTITY_WORK, "gpu", name="GPU lectures", fields={"form": "lecture-series"})
    await mint_entity(gx, P.ENTITY_UNIT, "gpu/lec-1", name="Lecture 1", fields={"position": 1})
    assert (await record_provenance(gx, "c-notes", "gpu/lec-1"))["written"]


def _check(doc: str, marker: str, box: str = "x") -> str:
    return doc.replace(f"- [ ] {marker}", f"- [{box}] {marker}", 1) if box == "x" else \
        doc.replace(f"- [x] {marker}", f"- [ ] {marker}", 1)


@pytestmark_graph
def test_draft_confirm_judge_review_measure(tmp_path):
    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            await _corpus(gx)
            scope = await concept_scope(gx)
            empty = await judge_concepts(gx, ask=_ask)                              # no vocabulary yet
            vdoc = (await review_concepts(gx, vocab=True, drafts=_DRAFTS))
            dropped = _check(vdoc["document"], "concept `drop-me`", " ")
            refused_subject = await review_concepts(gx, vocab=True, apply_text=dropped, writes=InvocationWrites(gx, None))
            ok_doc = _check(dropped, "subject `gpu-programming`")
            minted = await review_concepts(gx, vocab=True, apply_text=ok_doc, writes=InvocationWrites(gx, None))
            dry = await judge_concepts(gx, dry_run=True, ask=_ask)
            only = await judge_concepts(gx, dry_run=True, only=["c-notes"], ask=_ask)
            missing = await judge_concepts(gx, dry_run=True, only=["no-such-post"], ask=_ask)

            def broken(body):
                raise RuntimeError("judge refused: 401")
            failed = await judge_concepts(gx, ask=broken)
            first = await judge_concepts(gx, ask=_ask)
            again = await judge_concepts(gx, ask=_ask)
            rdoc = await review_concepts(gx)
            # a-train / nms is proposed as teaches: confirm it as is; b-export / onnx flips to assumes
            text = rdoc["document"]
            lines = text.splitlines()
            idx = next(i for i, ln in enumerate(lines) if "`onnx`" in ln and note_node_id("b-export") in ln)
            lines[idx] = lines[idx].replace("- [x] `teaches`", "- [x] `assumes-recommended`")
            idx2 = next(i for i, ln in enumerate(lines) if "`cnn`" in ln and note_node_id("a-train") in ln)
            lines[idx2] = lines[idx2].replace("- [x]", "- [ ]")                    # rejected
            edited = "\n".join(lines)
            landed = await review_concepts(gx, apply_text=edited)
            reapplied = await review_concepts(gx, apply_text=edited)                # landed already: no change (24fa72c2 (2))
            lines[idx2] = lines[idx2].replace("- [ ]", "- [x]")                    # the rejection now a confirmation
            refused = await review_concepts(gx, apply_text="\n".join(lines))        # an outcome that differs
            confirmed = await load_confirmed_relations(gx)
            judged = await load_concept_judgments(gx)
            after = await review_concepts(gx)
            m = await measure(gx)
            return (scope, empty, vdoc, refused_subject, minted, dry, only, missing, failed, first, again, rdoc,
                    landed, reapplied, refused, confirmed, judged, after, m)
    (scope, empty, vdoc, refused_subject, minted, dry, only, missing, failed, first, again, rdoc, landed, reapplied, refused,
     confirmed, judged, after, m) = asyncio.run(go())
    a, b, unit = note_node_id("a-train"), note_node_id("b-export"), entity_node_id(P.ENTITY_UNIT, "gpu/lec-1")
    assert set(scope["sources"]) == {a, b, unit} and scope["sources"][unit]["post"] == note_node_id("c-notes")
    assert "no live concept vocabulary" in empty["error"]
    assert vdoc["counts"] == {"new": 5, "reused": 0, "new_subjects": 1, "drafts": 3, "drafted": 6}
    assert [r["key"] for r in parse_vocab(vdoc["document"])["records"]][0] == "gpu-programming"
    # the area is typed (5c58d521): a typed draft keeps it, a first-shape draft's bare subject converts
    assert "  - area: task:object-detection" in vdoc["document"]
    assert "  - area: subject:deep-learning-foundations" in vdoc["document"]
    assert any("neither live nor confirmed" in e for e in refused_subject["errors"]) and not refused_subject["written"]
    assert minted["written"] and minted["counts"] == {"concepts": 4, "subjects": 1, "dropped": 1}
    assert [p["key"] for p in minted["plan"]][0] == "gpu-programming"
    assert dry["sources"] == 3 and dry["pairs"] == 12 and dry["requests"] == 3 and not dry["written"]
    assert only["sources"] == 1 and only["pairs"] == 4
    assert "name no source" in missing["error"]
    assert "nothing written" in failed["error"]
    assert first["written"] and first["applied"]["recorded"] == 3
    # stored: the post pairs' teaches + assumes at or above the floor, the unit's one cover
    assert first["applied"]["landed"] == 5 and again["pairs"] == 0 and not again["written"]
    assert rdoc["counts"] == {"proposal": 5, "challenged": 0, "near": 0}
    rows = parse_relations(rdoc["document"])["rows"]
    assert sorted(r["word"] for r in rows) == ["assumes", "assumes", "covers", "teaches", "teaches"]
    assert not landed.get("error") and landed["counts"]["confirmed"] == 4 and landed["counts"]["rejected"] == 1
    assert not reapplied.get("error") and not reapplied["written"] and reapplied["noop"]
    assert reapplied["counts"]["applied"] == 5 and reapplied["counts"]["confirmed"] == 0
    assert "nothing written" in refused["error"] and any("already reviewed" in e for e in refused["errors"])
    cid = lambda k: entity_node_id(P.ENTITY_CONCEPT, k)
    assert confirmed[(a, cid("nms"))][0]["relation"] == "TEACHES"
    assert confirmed[(b, cid("onnx"))][0] == {**confirmed[(b, cid("onnx"))][0], "relation": "ASSUMES",
                                              "strength": "recommended"}
    assert confirmed[(unit, cid("cuda-kernel"))][0]["relation"] == "COVERS" and (a, cid("cnn")) not in confirmed
    assert all(e.get("reviewed") for (s, c, _), e in judged.items() if (s, c) == (a, cid("cnn")))
    assert after["counts"] == {"proposal": 0, "challenged": 0, "near": 0}
    at5 = {a_["relation"]: a_ for a_ in m["agreement"] if a_["threshold"] == 0.5}
    assert at5["TEACHES"]["precision"] == 0.5 and at5["ASSUMES"]["recall"] == 0.5 and m["reviewed"] == 5
    assert m["confusions"]["TEACHES->ASSUMES"] == 1


class _Judge(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        out = json.dumps(_ask(body)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def _run(*args, env=None):
    return subprocess.run([sys.executable, "-m", "cjm_context_graph_projection.cli", *args],
                          capture_output=True, text=True, env=env)


def _rows(db):
    con = sqlite3.connect(str(db))
    try:
        return (sorted(con.execute("select id, properties from edges where relation_type in "
                                   "('JUDGED', 'TEACHES', 'ASSUMES', 'COVERS')")),
                sorted(con.execute("select id from nodes where label = 'Entity' and "
                                   "json_extract(properties, '$.entity_kind') in ('concept', 'subject')")),
                sorted(con.execute("select json_extract(properties, '$.subject_id'), json_extract(properties, '$.value') "
                                   "from nodes where label = 'Assertion' and "
                                   "json_extract(properties, '$.predicate') = 'concepts_judged'")))
    finally:
        con.close()


@pytestmark_graph
def test_a_rebuild_replays_the_vocabulary_run_and_review_without_the_judge(tmp_path):
    import os
    corpus = tmp_path / "posts"
    for s, t in _POSTS.items():
        (corpus / s).mkdir(parents=True)
        (corpus / s / "index.md").write_text(t)
    commit_all(corpus)
    journal = str(tmp_path / "writes.jsonl")
    for sub in ("live", "fresh"):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "graph.config.json").write_text(json.dumps(
            {"notes_corpus": str(corpus), "notes_profile": "quarto_post"}))
    live = str(tmp_path / "live" / "g.db")
    base = ["--graph-db-path", live, "--journal-path", journal]
    assert _run(*base, "ingest-notes").returncode == 0
    tpol, npol = tmp_path / "t.json", tmp_path / "n.json"
    tpol.write_text(json.dumps({"information_policy": {"concept_relations": ["TEACHES", "ASSUMES"]}}))
    npol.write_text(json.dumps({"information_policy": {"concept_relations": ["COVERS"]}}))
    cmds = [["notes-type", "archive-tutorial", "--title", "T", "--kind", "tutorial", "--origin", "archive",
             "--policy-file", str(tpol)],
            ["notes-type", "archive-notes", "--title", "N", "--kind", "notes", "--origin", "archive",
             "--policy-file", str(npol)]]
    cmds += [["assert", note_node_id(s), "deliverable_type", t]
             for s, t in (("a-train", "archive-tutorial"), ("b-export", "archive-tutorial"), ("c-notes", "archive-notes"))]
    cmds += [["entity", P.ENTITY_SUBJECT, "deep-learning-foundations", "--name", "DLF", "--description", "d",
              "--not-for", "n"],
             ["entity", P.ENTITY_TASK, "object-detection", "--name", "OD", "--position", "1"],
             ["entity", P.ENTITY_WORK, "gpu", "--name", "GPU", "--form", "lecture-series"],
             ["entity", P.ENTITY_UNIT, "gpu/lec-1", "--name", "L1", "--position", "1"],
             ["derived-from", "c-notes", "gpu/lec-1"]]
    for cmd in cmds:
        r = _run(*base, *cmd)
        assert r.returncode == 0, (cmd, r.stdout, r.stderr)
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    for i, d in enumerate(_DRAFTS):
        (drafts / f"{i:02d}.json").write_text(json.dumps(d))
    vdoc = tmp_path / "vocab.md"
    r = _run(*base, "review-concepts", "--vocab", "--drafts", str(drafts), "--out", str(vdoc))
    assert r.returncode == 0 and "5 new concept(s)" in r.stdout, r.stdout + r.stderr
    vdoc.write_text(vdoc.read_text().replace("- [ ] subject `gpu-programming`", "- [x] subject `gpu-programming`"))
    r = _run(*base, "review-concepts", "--vocab", "--apply", str(vdoc))
    assert r.returncode == 0 and "**minted** 5 concept(s) · 1 subject(s)" in r.stdout, r.stdout + r.stderr
    r = _run(*base, "concepts")
    assert r.returncode == 0 and "5 live" in r.stdout, r.stdout + r.stderr
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Judge)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        env = {**os.environ, "TYPESAFE_API_KEY": "test-key"}
        url = f"http://127.0.0.1:{server.server_address[1]}/v1/systemone"
        r = _run(*base, "judge-concepts", "--url", url, "--workers", "2", env=env)
        assert r.returncode == 0 and "**judged** 15 pair(s) in 3 request(s)" in r.stdout, r.stdout + r.stderr
    finally:
        server.shutdown()
    rdoc = tmp_path / "rel.md"
    r = _run(*base, "review-concepts", "--out", str(rdoc))
    assert r.returncode == 0 and "5 proposal(s)" in r.stdout, r.stdout + r.stderr
    r = _run(*base, "review-concepts", "--apply", str(rdoc))
    assert r.returncode == 0 and "confirmed 5" in r.stdout, r.stdout + r.stderr
    r = _run(*base, "judge-concepts", "--measure")
    assert r.returncode == 0 and "5 reviewed pair(s)" in r.stdout, r.stdout + r.stderr
    ops = [json.loads(line) for line in Path(journal).read_text().splitlines()]
    verbs = [o["verb"] for o in ops]
    assert verbs.count("judge-concepts") == 1 and verbs.count("review-concepts") == 1
    assert "test-key" not in Path(journal).read_text()
    fresh = str(tmp_path / "fresh" / "g.db")
    r = _run("--graph-db-path", fresh, "--journal-path", journal, "ingest-notes")   # no judge reachable
    assert r.returncode == 0, r.stdout + r.stderr
    assert _rows(fresh) == _rows(live) and len(_rows(live)[0]) == 5 + 5 and len(_rows(live)[2]) == 3
    dry = _run("--graph-db-path", fresh, "judge-concepts", "--dry-run")
    assert dry.returncode == 0 and "**stale** 0 pair(s)" in dry.stdout, dry.stdout
