"""Judged related posts (design e09e262b): a judge run asks about every pair touching a stale
public post, stores the judgments at or above the floor as JUDGED_RELATED edges and records what
each post was judged against; a later run re-judges only what went stale, a failed pair writes
nothing, and a rebuild replays the run without asking the judge."""

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
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import note_node_id
from cjm_markdown_decompose_core.extract import note_from_text
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from cjm_context_graph_projection.judging import (QUESTIONS, STORE_FLOOR, judge_pairs, judge_related,
                                                  judgment_of, load_judged, load_records, post_view,
                                                  question_hash, related_stale, run_judge, stale_posts,
                                                  state_hash)
from cjm_context_graph_projection.purenotes import mint_deliverable_type
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.write import assert_value
from conftest import commit_all

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()
pytestmark_graph = pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")


def _answers(a_title: str, b_title: str) -> dict:
    """A deterministic judge: posts of one family (the title's first word) are strongly related."""
    same = a_title.split("-")[0] == b_title.split("-")[0]
    score = 2.5 if same else 0.4
    return {"model": "jev-test", "usage": {"input_tokens": 10}, "answers": {
        "relatedness": {"type": "score", "score": score, "confidence": 0.9,
                        "probabilities": {"0": 0.0, "1": 0.1, "2": 0.3, "3": 0.6} if same else {"0": 0.6, "1": 0.4}},
        "relation": {"type": "choice", "choice": "same_technique" if same else "unrelated", "confidence": 0.8,
                     "probabilities": {"same_technique": 0.9, "unrelated": 0.1} if same
                     else {"same_technique": 0.1, "unrelated": 0.9}}}}


def _ask(body):
    return _answers(body["state"]["post_a"]["title"], body["state"]["post_b"]["title"])


def test_the_judged_state_staleness_and_pairs():
    v = post_view("T", "D", "tutorial", {"tools": ["PyTorch", "ONNX", "PyTorch"], "subjects": [], "stages": ["Export"]},
                  [f"h{i}" for i in range(40)])
    # the confirmed categories replace the hand tags (eefda2dd (2)): sorted, unique, the empty left out
    assert v["categories"] == {"stages": ["Export"], "tools": ["ONNX", "PyTorch"]} and len(v["section_outline"]) == 30
    assert state_hash(v) == state_hash(dict(v)) and state_hash(v) != state_hash({**v, "title": "T2"})
    q = question_hash()
    views = {"a": v, "b": {**v, "title": "B"}, "c": {**v, "title": "C"}}
    records = {"a": f"{q}:{state_hash(v)}", "b": f"{q}:stale-hash", "c": f"other:{state_hash(views['c'])}"}
    assert stale_posts(views, records, q) == ["b", "c"]                 # an edited state, a changed question
    assert stale_posts(views, {}, q) == ["a", "b", "c"]                 # never judged
    assert judge_pairs(["b"], views) == [("a", "b"), ("b", "a"), ("b", "c"), ("c", "b")]
    j = judgment_of(_answers("x-1", "x-2")["answers"])
    assert j["score"] == 2.5 and j["relation"] == "same_technique" and j["relation_probabilities"]["unrelated"] == 0.1
    assert set(QUESTIONS) == {"relatedness", "relation"}


def test_a_failed_pair_is_reported(monkeypatch):
    import cjm_context_graph_projection.judgeengine as E
    monkeypatch.setattr(E, "RETRY_PAUSE", 0.0)
    views = {k: post_view(k, "", "notes", {}, []) for k in ("x-1", "x-2", "y-1")}
    seen = []

    def flaky(body):
        seen.append(body["state"]["post_b"]["title"])
        if body["state"]["post_b"]["title"] == "y-1":
            raise RuntimeError("judge unreachable after retries: HTTP 529")
        return _ask(body)
    res = run_judge(judge_pairs(sorted(views), views), views, flaky, workers=2)
    assert len(res["errors"]) == 2 and len(res["judgments"]) == 4 and res["input_tokens"] == 40
    assert seen.count("y-1") == 2 * (1 + E.RETRY_ROUNDS)                    # each round asked again

    def refused(body):
        raise RuntimeError("judge refused: 401 b'bad key'")
    calls = []
    out = E.run_requests([1, 2], lambda b: calls.append(b) or refused(b), body_of=lambda i: {"i": i},
                         read=lambda i, r: {}, label=lambda i: {"i": i})
    assert len(out["errors"]) == 2 and len(calls) == 2 and out["retried"] == 0   # a refusal is never re-asked


def test_a_transient_failure_recovers_on_a_later_round(monkeypatch):
    import cjm_context_graph_projection.judgeengine as E
    monkeypatch.setattr(E, "RETRY_PAUSE", 0.0)
    views = {k: post_view(k, "", "notes", {}, []) for k in ("x-1", "x-2", "y-1")}
    failed = set()

    def once(body):   # every pair to y-1 drops once, then answers
        pair = (body["state"]["post_a"]["title"], body["state"]["post_b"]["title"])
        if pair[1] == "y-1" and pair not in failed:
            failed.add(pair)
            raise RuntimeError("IncompleteRead(0 bytes read)")
        return _ask(body)
    res = run_judge(judge_pairs(sorted(views), views), views, once, workers=2)
    assert res["errors"] == [] and len(res["judgments"]) == 6


def _post(slug: str) -> str:
    return (f"---\ntitle: \"{slug}\"\ndescription: \"About {slug}\"\ndate: 2024-01-01\n"
            f"categories: [tutorial]\n---\n\n## Overview\n\nBody.\n\n## Setup\n\nMore.\n")


@pytestmark_graph
def test_a_run_stores_the_floor_and_rejudges_only_the_stale(tmp_path):
    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            notes = [note_from_text(f"/c/posts/{s}/index.md", _post(s), corpus_root="/c/posts", lossless=True)
                     for s in ("alpha-one", "alpha-two", "beta-one", "beta-two")]
            nodes, edges = corpus_graph_elements(notes)
            await extend_graph(gx.queue, gx.graph_id, nodes, edges)
            await mint_deliverable_type(gx, "archive-tutorial", title="A", kind="tutorial", origin="archive")
            for s in ("alpha-one", "alpha-two", "beta-one"):
                await assert_value(gx, note_node_id(s), "deliverable_type", "archive-tutorial")
            before = await related_stale(gx)
            dry = await judge_related(gx, dry_run=True, ask=_ask)
            first = await judge_related(gx, ask=_ask)
            judged1, records1 = await load_judged(gx), await load_records(gx)
            again = await judge_related(gx, ask=_ask)
            # A fourth post goes public: only its pairs are asked, every other judgment stands
            await assert_value(gx, note_node_id("beta-two"), "deliverable_type", "archive-tutorial")
            asked = []

            def counting(body):
                asked.append((body["state"]["post_a"]["title"], body["state"]["post_b"]["title"]))
                return _ask(body)
            second = await judge_related(gx, ask=counting)
            judged2, records2 = await load_judged(gx), await load_records(gx)

            def broken(body):
                raise RuntimeError("judge refused: 401")
            failed = await judge_related(gx, all_posts=True, ask=broken)
            judged3 = await load_judged(gx)
            return before, dry, first, judged1, records1, again, asked, second, judged2, records2, failed, judged3
    (before, dry, first, judged1, records1, again, asked, second, judged2, records2,
     failed, judged3) = asyncio.run(go())
    a1, a2, b1, b2 = (note_node_id(s) for s in ("alpha-one", "alpha-two", "beta-one", "beta-two"))
    assert len(before) == 3 and dry["pairs"] == 6 and not dry["written"]          # only public posts are judged
    assert first["written"] and first["run"]["pairs"] == 6
    # only the family pair clears the store floor, in both directions; each post records its state
    assert set(judged1) == {(a1, a2), (a2, a1)} and all(j["score"] >= STORE_FLOOR for j in judged1.values())
    assert judged1[(a1, a2)]["model"] == "jev-test" and judged1[(a1, a2)]["question"] == question_hash()
    assert set(records1) == {a1, a2, b1} and all(v.startswith(question_hash() + ":") for v in records1.values())
    assert again["stale"] == [] and not again["written"]                           # nothing stale, nothing asked
    assert sorted(asked) == sorted([(t, "beta-two") for t in ("alpha-one", "alpha-two", "beta-one")]
                                   + [("beta-two", t) for t in ("alpha-one", "alpha-two", "beta-one")])
    assert set(judged2) == {(a1, a2), (a2, a1), (b1, b2), (b2, b1)} and set(records2) == {a1, a2, b1, b2}
    assert records2[a1] == records1[a1]                                            # untouched posts keep their record
    assert "nothing written" in failed["error"] and judged3 == judged2             # all or nothing


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
        return (sorted(r[0] for r in con.execute("select id from nodes")),
                sorted(r for r in con.execute("select id, properties from edges where relation_type = 'JUDGED_RELATED'")))
    finally:
        con.close()


@pytestmark_graph
def test_a_rebuild_replays_the_judgments_without_the_judge(tmp_path):
    import os
    corpus = tmp_path / "posts"
    for s in ("alpha-one", "alpha-two", "beta-one"):
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
    for cmd in (["notes-type", "archive-tutorial", "--title", "Archive tutorial", "--kind", "tutorial",
                 "--origin", "archive"],
                *(["assert", note_node_id(s), "deliverable_type", "archive-tutorial"]
                  for s in ("alpha-one", "alpha-two", "beta-one"))):
        r = _run(*base, *cmd)
        assert r.returncode == 0, (cmd, r.stdout, r.stderr)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Judge)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        env = {**os.environ, "TYPESAFE_API_KEY": "test-key"}
        url = f"http://127.0.0.1:{server.server_address[1]}/v1/systemone"
        r = _run(*base, "judge-related", "--url", url, "--workers", "2", env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "**judged** 6 pair(s) touching 3 post(s)" in r.stdout and "stored 2 judgment(s)" in r.stdout
    finally:
        server.shutdown()
    ops = [json.loads(line) for line in Path(journal).read_text().splitlines()]
    run = [o for o in ops if o["verb"] == "judge-related"]
    assert len(run) == 1 and "test-key" not in Path(journal).read_text()            # the key is never journaled
    fresh = str(tmp_path / "fresh" / "g.db")
    r = _run("--graph-db-path", fresh, "--journal-path", journal, "ingest-notes")   # no judge reachable
    assert r.returncode == 0, r.stdout + r.stderr
    assert _rows(fresh) == _rows(live)
    dry = _run("--graph-db-path", fresh, "judge-related", "--dry-run")
    assert dry.returncode == 0 and "**stale** 0 post(s)" in dry.stdout
