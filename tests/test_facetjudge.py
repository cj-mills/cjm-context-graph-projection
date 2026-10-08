"""The facet judge (design eefda2dd): one request per public post with a Noul per vocabulary
entry, the judgments at or above the store floor stored per (post, entry) with per-pair
staleness, a re-run asking only the stale pairs, all or nothing, a rebuild replaying the run
without the judge -- and the confirmed facets checked at write time."""

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
from cjm_dev_graph_schema.identity import entity_node_id, note_node_id
from cjm_markdown_decompose_core.extract import note_from_text
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from cjm_context_graph_projection.coverage import mint_entity
from cjm_context_graph_projection.facetjudge import (STORE_FLOOR, applies, code_signals, criteria_hash,
                                                     entry_question, facet_view, facets_stale, judge_facets,
                                                     load_facet_judgments, load_facet_records, measure,
                                                     opening_prose, record_value, stale_pairs)
from cjm_context_graph_projection.judgeengine import digest
from cjm_context_graph_projection.purenotes import mint_deliverable_type
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.write import assert_value
from conftest import commit_all

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()
pytestmark_graph = pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")

_CODE = """Intro with [a link](https://x.org) and ![fig](a.png){fig-align="center"}.

```python
import torch, numpy as np
from torchvision.models import resnet18
!pip install timm
```

```{.bash}
sudo apt-get install -y cmake
```

```c#
using UnityEngine;
using Unity.Barracuda;
```

```c++
#include <openvino/openvino.hpp>
using namespace cv;
```
"""


def test_the_judged_state_reads_code_signals_and_prose():
    s = code_signals([_CODE])
    assert s["languages"] == ["bash", "c#", "c++", "python"]
    assert s["python_imports"] == ["numpy", "torch", "torchvision"]
    assert s["usings"] == ["Unity.Barracuda", "UnityEngine", "cv", "openvino/openvino.hpp"]
    assert s["install_commands"] == ["pip install timm", "sudo apt-get install -y cmake"]
    prose = opening_prose([_CODE, "More text."])
    assert prose == "Intro with a link and . More text."          # code, figures and link targets go
    assert len(opening_prose(["word " * 1000])) == 1800


def _entry(kind, key, **f):
    return {"entity_kind": kind, "key": key, "name": key.title(), "description": f"About {key}", **f}


def test_staleness_is_per_pair():
    view = facet_view("T", "D", "notes", ["h"], code_signals([]), "p")
    tut = {**view, "kind": "tutorial"}
    entries = {"tool:onnx": _entry("tool", "onnx", not_for="a mention"),
               "subject:gpu": _entry("subject", "gpu"), "task:llm": _entry("task", "llm")}
    crit = {e: criteria_hash(entry_question(v)) for e, v in entries.items()}
    views = {"a": view, "t": tut}
    assert applies("task:llm", "notes") and not applies("task:llm", "tutorial")
    assert stale_pairs(views, crit, {}) == {"a": ["subject:gpu", "task:llm", "tool:onnx"],
                                            "t": ["subject:gpu", "tool:onnx"]}   # a tutorial's task is teaches_*
    rec = {"a": json.loads(record_value(digest(view), crit)), "t": json.loads(record_value(digest(tut), crit))}
    assert stale_pairs(views, crit, rec) == {}
    # an entry's criteria move: only its pairs go stale
    moved = {**crit, "tool:onnx": criteria_hash(entry_question({**entries["tool:onnx"], "not_for": "other"}))}
    assert stale_pairs(views, moved, rec) == {"a": ["tool:onnx"], "t": ["tool:onnx"]}
    # a post's state moves: every pair of it goes stale; a new entry is stale everywhere it applies
    assert stale_pairs({**views, "a": {**view, "title": "T2"}}, crit, rec) == {"a": sorted(crit)}
    new = {**crit, "subject:nlp": criteria_hash(entry_question(_entry("subject", "nlp")))}
    assert stale_pairs(views, new, rec) == {"a": ["subject:nlp"], "t": ["subject:nlp"]}
    q = entry_question(entries["tool:onnx"])
    assert q["type"] == "noul" and "Onnx" in q["instructions"] and q["criteria"]["false"].endswith("Not for: a mention")


def _post(slug: str) -> str:
    return (f"---\ntitle: \"{slug}\"\ndescription: \"About {slug}\"\ndate: 2024-01-01\n---\n\n"
            f"## Overview\n\nBody about {slug}.\n\n```python\nimport torch\n```\n")


def _p(title: str, entry: str) -> float:
    """A deterministic judge: an entry whose key is in the title is a yes, its kind's subject a near miss."""
    key = entry.split(":", 1)[1]
    return 0.9 if key in title else 0.3 if entry.startswith("subject:") else 0.05


def _ask(body):
    t = body["state"]["post"]["title"]
    return {"model": "jev-test", "usage": {"input_tokens": 7},
            "answers": {e: {"type": "noul", "noul": _p(t, e)} for e in body["questions"]}}


_VOCAB = [("tool", "onnx", {"description": "ONNX", "not_for": "a mention"}),
          ("tool", "unity", {"description": "Unity", "not_for": "a mention"}),
          ("subject", "gpu", {"description": "GPUs", "not_for": "running on one"}),
          ("task", "detection", {"description": "Detection", "position": 1}),
          ("stage", "export", {"description": "Export", "position": 1})]


async def _corpus(gx, slugs, types):
    notes = [note_from_text(f"/c/posts/{s}/index.md", _post(s), corpus_root="/c/posts", lossless=True)
             for s in slugs]
    nodes, edges = corpus_graph_elements(notes)
    await extend_graph(gx.queue, gx.graph_id, nodes, edges)
    await mint_deliverable_type(gx, "archive-tutorial", title="A", kind="tutorial", origin="archive")
    await mint_deliverable_type(gx, "archive-notes", title="N", kind="notes", origin="archive")
    for s, t in types.items():
        await assert_value(gx, note_node_id(s), "deliverable_type", t)
    for kind, key, f in _VOCAB:
        await mint_entity(gx, kind, key, name=key, fields=f)


@pytestmark_graph
def test_a_run_stores_the_floor_and_rejudges_only_the_stale_pairs(tmp_path):
    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            await _corpus(gx, ("onnx-export", "unity-detection", "draft"),
                          {"onnx-export": "archive-tutorial", "unity-detection": "archive-notes"})
            dry = await judge_facets(gx, dry_run=True, ask=_ask)
            first = await judge_facets(gx, ask=_ask)
            judged1, records1 = await load_facet_judgments(gx), await load_facet_records(gx)
            again = await judge_facets(gx, ask=_ask)
            # one entry's criteria change: only its pairs are asked, the others stand
            await mint_entity(gx, "tool", "unity", name="unity", fields={"description": "Unity engine",
                                                                         "not_for": "a mention"})
            asked = []

            def counting(body):
                asked.append((body["state"]["post"]["title"], sorted(body["questions"])))
                return _ask(body)
            second = await judge_facets(gx, ask=counting)
            judged2, records2 = await load_facet_judgments(gx), await load_facet_records(gx)
            report = await measure(gx, sample=5)

            def broken(body):
                raise RuntimeError("judge refused: 401")
            failed = await judge_facets(gx, all_posts=True, ask=broken)
            judged3, stale = await load_facet_judgments(gx), await facets_stale(gx)
            # the confirmed facets are checked at write time
            tut, notes = note_node_id("onnx-export"), note_node_id("unity-detection")
            checks = [await assert_value(gx, tut, P.USES_TOOL, "onnx"),
                      await assert_value(gx, tut, P.USES_TOOL, "pytorch"),
                      await assert_value(gx, tut, P.ABOUT_TASK, "detection"),
                      await assert_value(gx, notes, P.ABOUT_TASK, "detection"),
                      await assert_value(gx, notes, P.ABOUT_SUBJECT, "detection")]
            return (dry, first, judged1, records1, again, asked, second, judged2, records2, report,
                    failed, judged3, stale, checks)
    (dry, first, judged1, records1, again, asked, second, judged2, records2, report,
     failed, judged3, stale, checks) = asyncio.run(go())
    tut, notes = note_node_id("onnx-export"), note_node_id("unity-detection")
    ent = {f"{k}:{key}": entity_node_id(k, key) for k, key, _ in _VOCAB}
    # only public posts; a tutorial is asked the tool / subject / model entries, a notes post all five kinds
    assert dry["requests"] == 2 and dry["pairs"] == 3 + 5 and not dry["written"] and dry["token_estimate"] > 0
    assert first["written"] and first["input_tokens"] == 14
    assert set(judged1) == {(tut, ent["tool:onnx"]), (tut, ent["subject:gpu"]), (notes, ent["tool:unity"]),
                            (notes, ent["task:detection"]), (notes, ent["subject:gpu"])}   # 0.3 >= the floor
    assert all(j["p"] >= STORE_FLOOR and j["model"] == "jev-test" for j in judged1.values())
    assert sorted(records1[tut]["criteria"]) == ["subject:gpu", "tool:onnx", "tool:unity"]
    assert len(records1[notes]["criteria"]) == 5
    assert again["requests"] == 0 and not again["written"]                  # nothing stale, nothing asked
    assert sorted(asked) == [("onnx-export", ["tool:unity"]), ("unity-detection", ["tool:unity"])]
    assert second["pairs"] == 2 and set(judged2) == set(judged1)
    assert judged2[(notes, ent["tool:unity"])]["criteria"] != judged1[(notes, ent["tool:unity"])]["criteria"]
    assert judged2[(tut, ent["tool:onnx"])] == judged1[(tut, ent["tool:onnx"])]   # untouched pairs stand
    assert records2[tut]["state"] == records1[tut]["state"]
    rows = {r["entry"]: r for r in report["entries"]}
    assert rows["tool:onnx"]["count"] == 1 and rows["stage:export"]["verdict"] == "matrix-only"   # never dead
    assert rows["stage:export"]["applicable"] == 1 and report["stale_pairs"] == 0
    assert "nothing written" in failed["error"] and judged3 == judged2 and stale == {}   # all or nothing
    ok, unknown, tutorial_task, notes_task, wrong_kind = checks
    assert not ok.get("error") and not notes_task.get("error")
    assert "no tool in the vocabulary" in unknown["error"]
    assert "non-tutorial" in tutorial_task["error"]
    assert "no subject in the vocabulary" in wrong_kind["error"]


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
                sorted(r for r in con.execute("select id, properties from edges where relation_type = 'JUDGED'")),
                sorted(r for r in con.execute(   # the records (a live assertion's asserted_at is the live clock's)
                    "select json_extract(properties, '$.subject_id'), json_extract(properties, '$.value') "
                    "from nodes where label = 'Assertion' and json_extract(properties, '$.predicate') = 'facets_judged'")))
    finally:
        con.close()


@pytestmark_graph
def test_a_rebuild_replays_the_facet_run_without_the_judge(tmp_path):
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
        assert "**judged** 8 pair(s) in 2 request(s)" in r.stdout and "stored 5 judgment(s)" in r.stdout
    finally:
        server.shutdown()
    ops = [json.loads(line) for line in Path(journal).read_text().splitlines()]
    assert len([o for o in ops if o["verb"] == "judge-facets"]) == 1
    assert "test-key" not in Path(journal).read_text()                       # the key is never journaled
    fresh = str(tmp_path / "fresh" / "g.db")
    r = _run("--graph-db-path", fresh, "--journal-path", journal, "ingest-notes")   # no judge reachable
    assert r.returncode == 0, r.stdout + r.stderr
    assert _rows(fresh) == _rows(live)
    dry = _run("--graph-db-path", fresh, "judge-facets", "--dry-run")
    assert dry.returncode == 0 and "**stale** 0 pair(s) on 0 post(s)" in dry.stdout
    m = _run("--graph-db-path", fresh, "judge-facets", "--measure", "--sample", "2")
    assert m.returncode == 0 and "**measure** 2 public post(s)" in m.stdout and "| `export` | 0 | 1 | matrix-only |" in m.stdout
