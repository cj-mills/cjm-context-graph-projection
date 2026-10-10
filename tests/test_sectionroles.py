"""Section roles (ruling 6a203252): the scope is the type's role map; one Choice per Section over
the live roles and none, every role's probability stored with per-Section staleness; the review
confirms a shared heading once, each other H2 / H3 per post, and only the overrides of an inherited
role; the facts are checked at write time; a rebuild replays the run and the review without the
judge."""

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

from cjm_context_graph_projection.coverage import mint_entity
from cjm_context_graph_projection.purenotes import mint_deliverable_type
from cjm_context_graph_projection.rolereview import parse_review, review_roles
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.sectionroles import (NONE, distribution, effective_roles, judge_roles,
                                                       load_role_facts, load_role_judgments, question_hash,
                                                       role_question, role_scope, scope_sections,
                                                       section_roles, stale_sections)
from cjm_context_graph_projection.write import assert_value
from conftest import commit_all

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()
pytestmark_graph = pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")

_ROLES = [("intro", "Opens the post", "A setup section", 1), ("setup", "Prepares the environment", "A step", 2),
          ("step", "Does the work", "Setup", 3), ("how-it-works", "Explains a mechanism", "A step", 4)]
_MAP = {k: "body" for k, *_ in _ROLES}


def _vocab(**over):
    return {k: {"key": k, "description": over.get(k, d), "not_for": n, "position": i} for k, d, n, i in _ROLES}


def test_inheritance_is_derived_below_the_candidate_levels():
    rows = [{"id": "a", "level": 2, "parent": None}, {"id": "b", "level": 3, "parent": "a"},
            {"id": "c", "level": 4, "parent": "b"}, {"id": "d", "level": 4, "parent": "b"},
            {"id": "e", "level": 2, "parent": None}, {"id": "f", "level": 4, "parent": "e"}]
    eff = effective_roles(rows, {"a": "setup", "b": "step", "d": "how-it-works"})
    assert eff == {"a": ("setup", "own"), "b": ("step", "own"), "c": ("step", "inherited"),
                   "d": ("how-it-works", "own"), "e": (None, "unassigned"), "f": (None, "unassigned")}
    # an H3 carries its own role: it never inherits its H2's
    assert effective_roles(rows[:2], {"a": "setup"})["b"] == (None, "unassigned")


def test_the_choice_and_its_staleness():
    q = role_question(_vocab())
    assert q["type"] == "choice" and list(q["criteria"]) == ["intro", "setup", "step", "how-it-works", NONE]
    assert q["criteria"]["setup"] == {"covers": "Prepares the environment", "not_for": "A step"}
    h = question_hash(q)
    assert question_hash(role_question(_vocab(step="Does the post's work"))) != h   # one role's edit re-asks all
    view = {"post": {"title": "T"}, "section": {"heading": "x"}}
    from cjm_context_graph_projection.judgeengine import digest
    fresh = {k: {"p": 0.25, "state": digest(view), "criteria": h} for k in _MAP}
    roles = list(_MAP)
    assert stale_sections({"s": view}, h, {"s": fresh}, roles) == []
    assert stale_sections({"s": view}, "other", {"s": fresh}, roles) == ["s"]
    assert stale_sections({"s": {**view, "post": {"title": "U"}}}, h, {"s": fresh}, roles) == ["s"]
    assert stale_sections({"s": view}, h, {"s": {k: v for k, v in fresh.items() if k != "step"}}, roles) == ["s"]
    dist = distribution({"setup": {"p": 0.5}, "step": {"p": 0.2}}, roles)
    assert dist[0] == ("setup", 0.5) and dict(dist)[NONE] == pytest.approx(0.3)


def _post(slug: str, extra: str = "") -> str:
    return (f"---\ntitle: \"{slug}\"\ndescription: \"About {slug}\"\ndate: 2024-01-01\n---\n\n"
            "## Introduction\n\nWhat we build.\n\n"
            "## Setting Up Your Python Environment\n\n```bash\npip install torch\n```\n\n"
            "## Training the Model\n\n```python\nmodel.fit()\n```\n\n"
            "#### How the Optimizer Works\n\nAdam keeps moments.\n\n"
            "#### Plot the Loss\n\n```python\nplt.plot(loss)\n```\n" + extra)


def _choice(heading: str) -> dict:
    """A deterministic judge: the heading decides; an explainer heading is how-it-works."""
    h = heading.lower()
    top = ("intro" if "introduction" in h else "setup" if "environment" in h else
           "how-it-works" if "works" in h else NONE if "links" in h else "step")
    probs = {k: 0.04 for k in _MAP}
    probs[NONE] = 0.04
    probs[top] = 1.0 - 0.04 * len(_MAP)
    return probs


def _ask(body):
    probs = _choice(body["state"]["section"]["heading"])
    q = body["questions"]["role"]
    assert q["type"] == "choice" and set(q["criteria"]) == set(probs)
    return {"model": "jev-test", "usage": {"input_tokens": 3},
            "answers": {"role": {"type": "choice", "choice": max(probs, key=probs.get),
                                 "probabilities": probs, "confidence": 0.9}}}


async def _corpus(gx):
    posts = {"a-tutorial": _post("a-tutorial", "\n## Tutorial Links\n\n- [x](y)\n"),
             "b-tutorial": _post("b-tutorial"), "c-notes": _post("c-notes")}
    notes = [note_from_text(f"/c/posts/{s}/index.md", t, corpus_root="/c/posts", lossless=True) for s, t in posts.items()]
    nodes, edges = corpus_graph_elements(notes)
    await extend_graph(gx.queue, gx.graph_id, nodes, edges)
    await mint_deliverable_type(gx, "archive-tutorial", title="A", kind="tutorial", origin="archive",
                                presentation_policy={"section_roles": _MAP})
    await mint_deliverable_type(gx, "archive-notes", title="N", kind="notes", origin="archive")
    for s, t in (("a-tutorial", "archive-tutorial"), ("b-tutorial", "archive-tutorial"), ("c-notes", "archive-notes")):
        await assert_value(gx, note_node_id(s), "deliverable_type", t)
    for k, d, n, i in _ROLES:
        await mint_entity(gx, P.ENTITY_SECTION_ROLE, k, name=k, fields={"description": d, "not_for": n, "position": i})


def _edit(doc: str, heading: str, role: str) -> str:
    """Rename the role on the one row that names `heading`."""
    out = []
    for ln in doc.splitlines():
        if heading in ln and "<!-- role " in ln:
            ln = ln.replace(ln[ln.index("`"):ln.index("`", ln.index("`") + 1) + 1], f"`{role}`", 1)
        out.append(ln)
    return "\n".join(out)


@pytestmark_graph
def test_judge_review_and_apply(tmp_path):
    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            await _corpus(gx)
            scope = await role_scope(gx)
            secs = await scope_sections(gx, scope)
            dry = await judge_roles(gx, dry_run=True, ask=_ask)
            first = await judge_roles(gx, ask=_ask)
            judged1 = await load_role_judgments(gx)
            again = await judge_roles(gx, ask=_ask)

            def broken(body):
                raise RuntimeError("judge refused: 401")
            failed = await judge_roles(gx, all_sections=True, ask=broken)
            doc1 = (await review_roles(gx))["document"]
            edited = _edit(doc1, "Training the Model", "setup")             # a pattern's role changed
            landed = await review_roles(gx, apply_text=edited)
            refused = await review_roles(gx, apply_text=edited)               # every row moved on: refused whole
            facts1 = await load_role_facts(gx)
            doc2 = (await review_roles(gx))["document"]
            first_step = doc2.index("- [x] `step`", doc2.index("## Overrides"))
            doc2e = doc2[:first_step] + "- [ ] `step`" + doc2[first_step + len("- [x] `step`"):]   # keep one inherited
            landed2 = await review_roles(gx, apply_text=doc2e)
            facts2 = await load_role_facts(gx)
            read = await section_roles(gx, note_node_id("a-tutorial"))
            doc3 = await review_roles(gx)
            # the write-time check
            notes_sec = next(r["id"] for r in (await scope_sections(gx, {note_node_id("c-notes"): {}}))[note_node_id("c-notes")])
            a_sec = secs[note_node_id("a-tutorial")][0]["id"]
            checks = [await assert_value(gx, notes_sec, P.SECTION_ROLE, "intro"),
                      await assert_value(gx, a_sec, P.SECTION_ROLE, "conclusion"),
                      await assert_value(gx, note_node_id("a-tutorial"), P.SECTION_ROLE, "intro")]
            return (scope, secs, dry, first, judged1, again, failed, doc1, landed, refused, facts1, doc2,
                    landed2, facts2, read, doc3, checks)
    (scope, secs, dry, first, judged1, again, failed, doc1, landed, refused, facts1, doc2, landed2, facts2,
     read, doc3, checks) = asyncio.run(go())
    a, b = note_node_id("a-tutorial"), note_node_id("b-tutorial")
    assert set(scope) == {a, b}                                                 # the notes type maps no roles
    assert [r["level"] for r in secs[a]] == [2, 2, 2, 4, 4, 2] and [r["level"] for r in secs[b]] == [2, 2, 2, 4, 4]
    assert secs[a][3]["parent"] == secs[a][2]["id"]                               # PART_OF to the H2
    assert dry["requests"] == 11 and not dry["written"] and dry["token_estimate"] > 0
    assert first["written"] and first["applied"]["landed"] == 11 * 4 and first["input_tokens"] == 33
    assert all(len(js) == 4 and all(j["model"] == "jev-test" for j in js.values()) for js in judged1.values())
    assert again["requests"] == 0 and not again["written"]
    assert "nothing written" in failed["error"]
    # the document: three shared headings confirmed once, Tutorial Links per post, no override before the H2's role
    rows = parse_review(doc1)["rows"]
    assert [r["kind"] for r in rows].count("pattern") == 3 and [r["kind"] for r in rows].count("section") == 1
    assert not any(r["kind"] == "override" for r in rows) and "4 deeper section(s) wait" in doc1
    assert not landed.get("error") and landed["counts"] == {"confirmed": 6, "no_role": 1, "kept_inherited": 0, "left": 0}
    assert "nothing written" in refused["error"]
    assert facts1[secs[a][2]["id"]] == "setup" and facts1[secs[b][0]["id"]] == "intro"
    assert secs[a][5]["id"] not in facts1                                         # none: reviewed, no fact
    # the second review: How the Optimizer Works overrides its inherited setup; Plot the Loss (step) too
    rows2 = parse_review(doc2)["rows"]
    assert sorted(r["role"] for r in rows2 if r["kind"] == "override") == ["how-it-works", "how-it-works", "step", "step"]
    assert landed2["counts"]["confirmed"] == 3 and landed2["counts"]["kept_inherited"] == 1
    assert facts2[secs[a][3]["id"]] == "how-it-works"
    eff = {r["heading"]: (r["role"], r["how"]) for r in read["posts"][0]["rows"]}
    assert eff["How the Optimizer Works"] == ("how-it-works", "own") and eff["Tutorial Links"] == (None, "unassigned")
    assert read["totals"]["overrides"] == 3 and read["totals"]["candidates"] == 7 and read["totals"]["inherited"] == 1
    assert doc3["counts"]["patterns"] == 0 and doc3["counts"]["sections"] == 0 and doc3["counts"]["overrides"] == 0
    notes_check, not_mapped, not_a_section = checks
    assert "declares no `section_roles` map" in notes_check["error"]
    assert "no section_role in the vocabulary" in not_mapped["error"]
    assert "belongs to a Section" in not_a_section["error"]


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
        return (sorted(r for r in con.execute("select id, properties from edges where relation_type = 'JUDGED'")),
                sorted(r for r in con.execute(
                    "select json_extract(properties, '$.subject_id'), json_extract(properties, '$.value') "
                    "from nodes where label = 'Assertion' and json_extract(properties, '$.predicate') = 'section_role'")))
    finally:
        con.close()


@pytestmark_graph
def test_a_rebuild_replays_the_run_and_the_review_without_the_judge(tmp_path):
    import os
    corpus = tmp_path / "posts"
    for s in ("a-tutorial", "b-tutorial"):
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
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"presentation_policy": {"section_roles": _MAP}}))
    cmds = [["notes-type", "archive-tutorial", "--title", "T", "--kind", "tutorial", "--origin", "archive",
             "--policy-file", str(policy)]]
    cmds += [["assert", note_node_id(s), "deliverable_type", "archive-tutorial"] for s in ("a-tutorial", "b-tutorial")]
    cmds += [["entity", P.ENTITY_SECTION_ROLE, k, "--name", k, "--description", d, "--not-for", n, "--position", str(i)]
             for k, d, n, i in _ROLES]
    for cmd in cmds:
        r = _run(*base, *cmd)
        assert r.returncode == 0, (cmd, r.stdout, r.stderr)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Judge)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        env = {**os.environ, "TYPESAFE_API_KEY": "test-key"}
        url = f"http://127.0.0.1:{server.server_address[1]}/v1/systemone"
        r = _run(*base, "judge-roles", "--url", url, "--workers", "2", env=env)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "**judged** 10 section(s) of 10 in 2 post(s)" in r.stdout and "stored 40 judgment(s)" in r.stdout
    finally:
        server.shutdown()
    doc = tmp_path / "review.md"
    r = _run(*base, "review-roles", "--out", str(doc))
    assert r.returncode == 0 and "**review** 3 pattern(s) over 6 section(s)" in r.stdout, r.stdout + r.stderr
    r = _run(*base, "review-roles", "--apply", str(doc))
    assert r.returncode == 0 and "confirmed 6" in r.stdout, r.stdout + r.stderr
    r = _run(*base, "review-roles", "--out", str(doc))
    assert r.returncode == 0 and "2 override(s)" in r.stdout, r.stdout
    r = _run(*base, "review-roles", "--apply", str(doc))
    assert r.returncode == 0 and "confirmed 2" in r.stdout and "kept inherited 0" in r.stdout, r.stdout + r.stderr
    ops = [json.loads(line) for line in Path(journal).read_text().splitlines()]
    assert [o["verb"] for o in ops].count("judge-roles") == 1 and [o["verb"] for o in ops].count("review-roles") == 2
    assert "test-key" not in Path(journal).read_text()
    fresh = str(tmp_path / "fresh" / "g.db")
    r = _run("--graph-db-path", fresh, "--journal-path", journal, "ingest-notes")   # no judge reachable
    assert r.returncode == 0, r.stdout + r.stderr
    assert _rows(fresh) == _rows(live) and len(_rows(live)[1]) == 8
    dry = _run("--graph-db-path", fresh, "judge-roles", "--dry-run")
    assert dry.returncode == 0 and "**stale** 0 section(s)" in dry.stdout, dry.stdout
    read = _run("--graph-db-path", fresh, "section-roles")
    assert read.returncode == 0 and "own 8 · inherited 2 · unassigned 0 · overrides 2" in read.stdout, read.stdout
