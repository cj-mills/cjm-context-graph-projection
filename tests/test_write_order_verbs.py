"""THE WRITE ORDER'S PER-VERB PROOF (design detail 65f4a6c9 (1)-(2), work item eeda5977 leg L2).

Every verb planned -> appended -> applied is held to one proof, driven through the real CLI:
- run A takes its scenario uncrashed;
- run B takes it with each marked step's process killed between its append and its apply
  (CJM_JOURNAL_FAULT=after_append, exit 86), then that step RETRIED: the retry's writer heals the
  journaled op, and its plan finds the db already there and journals nothing (ruling 17daa86b);
- each live db equals the replay of its own journal ELEMENT FOR ELEMENT (`rebuild-diff`, ruling
  f8b9c728), and the two journals hold the same ops in the same order -- a crashed step journals
  exactly ONE op, every step the same count in both runs, an A -> B -> A sequence kept whole.
A verb joins the proof by adding its scenario to SCENARIOS; a scenario naming `tx:` (or listed in
NOTES) gets the sibling graph a cross-graph link observes, and one keyed in FILES gets those files in
its run. A step whose first element is a function is a HARNESS action (a git commit, a journal written
by an earlier release, a review document written), run in both runs alike and never counted; a step
marked NOCHANGE is a write whose plan finds the graph already there -- it journals nothing (ruling
24fa72c2). The run verbs ask a local JUDGE double ({judge}) and a fake `gh` on the run's PATH."""

import asyncio
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil
import pytest
from cjm_context_graph_layer.ops import extend_graph
from cjm_context_graph_primitives.journal import append_record, read_journal
from cjm_context_graph_primitives.writer import FAULT_EXIT, JOURNAL_FAULT_ENV
from cjm_context_graph_projection.purenotes import NOTES_PROPSET_FORMAT, NOTES_PROPSET_VERSION
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import note_node_id, point_node_id, point_set_node_id
from cjm_dev_graph_schema.nodes import DecisionNode, ReferenceNode

# Drives the real CLI, which needs the graph-storage worker capability installed.
pytestmark = pytest.mark.skipif(
    not (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists(),
    reason=f"graph capability {DEFAULT_GRAPH_ID!r} not installed at {DEFAULT_MANIFESTS}",
)

ITEM, OTHER = "WORK ITEM: the proof's subject", "FINDING: the proof's other end"
ITEM_ID, OTHER_ID = DecisionNode(statement=ITEM).id, DecisionNode(statement=OTHER).id
KEY = "2026-01-01_00-00-00"
NOTE = "---\nname: canon-note\ndescription: d\n---\n\nbody\n"
LENS = json.dumps({"params": [{"name": "kind", "type": "string", "required": True}],
                   "selection": [{"verb": "list", "args": {"label": "{kind}"}}]})
ARCH = "---\nname: arch-note\ndescription: d\n---\n\nbody\n"
SOURCE = "5f0c0a1e-0000-4000-8000-000000000001"   # the sibling graph's one Source
REF_ID = ReferenceNode(graph="tx", foreign_id=SOURCE, foreign_label="Source", title="").id
DELIVERABLE = [(("new-note", "--path", "{run}/memory/arch.md", "--content", ARCH), False)]
WORKS = [(("entity", "work", w, "--name", w, "--form", "book"), False) for w in ("a-book", "b-book")]
POSTS = [(("new-note", "--path", f"{{run}}/memory/{s}.md", "--content",
           f"---\nname: {s}\ndescription: d\n---\n\nbody\n"), False) for s in ("p1", "p2", "p3")]
SERIES = [(("series", "a-series", "--title", "A"), False)]
# A harness transcript booted just after KEY's mint (the key is LOCAL time, a record's stamp UTC)
BOOT = (datetime.strptime(KEY, "%Y-%m-%d_%H-%M-%S").astimezone() + timedelta(seconds=5)).astimezone(timezone.utc)
TRANSCRIPT = "".join(json.dumps(r) + "\n" for r in (
    {"type": "user", "isSidechain": False, "uuid": "u-boot", "parentUuid": None,
     "timestamp": BOOT.isoformat().replace("+00:00", "Z"),
     "message": {"role": "user", "content": "Resume. New session minted in-scratchpad."}},
    {"type": "assistant", "isSidechain": False, "uuid": "u-reply", "parentUuid": "u-boot",
     "timestamp": (BOOT + timedelta(seconds=4)).isoformat().replace("+00:00", "Z"),
     "message": {"role": "assistant", "content": [{"type": "text", "text": "On it."}]}}))
# The sibling's Segments a point derives from, and a proposal set over them with FIXED proposal ids
# (a drafted set mints uuid4 keys; a point's id is (owner, key), so both runs must accept the same keys)
SEGMENTS = [{"id": f"seg-{i}", "label": "Segment", "properties": {"text": t, "index": i, "start_time": float(i),
                                                                   "end_time": i + 1.0, "source_id": SOURCE}}
            for i, t in enumerate(("Gatto quit teaching in 1991.", "Subjects taught in isolation.",
                                   "Kids never build a coherent picture."))]
UNIT = {"graph": "tx", "source_id": SOURCE, "title": "Chapter 1"}
KEYS = [f"0000000{i}-1111-4222-8333-444455556666" for i in range(3)]
PROPS = [{"proposal_id": k, "kind": "claim", "text": seg["properties"]["text"], "segment_ids": [seg["id"]],
          "from_i": i, "to_i": i, "heading": "", "heading_index": 0, "start_time": float(i), "end_time": i + 1.0,
          **({"lead": "Gatto"} if i == 0 else {})} for i, (k, seg) in enumerate(zip(KEYS, SEGMENTS))]
PROPSET = {"purenotes/proposals/npropset_proof/manifest.json": json.dumps(
               {"format": NOTES_PROPSET_FORMAT, "version": NOTES_PROPSET_VERSION, "proposal_set_id": "npropset_proof",
                "created_at": 1.0, "type": "pure-notes", "model": {"kind": "test"}, "source": UNIT,
                "files": {"proposals": "proposals.jsonl"}}),
           "purenotes/proposals/npropset_proof/proposals.jsonl": "".join(json.dumps(p) + "\n" for p in PROPS)}
PT = [point_node_id(point_set_node_id("tx", SOURCE, ""), k) for k in KEYS]   # each accepted point, set-owned
POST_SLUG = "a-book/ch01"
BORN = [(("new-note", "--slug", POST_SLUG, "--content", "---\ntitle: \"A chapter\"\ndate: 2026-09-07\n---\n\nNotes.\n"),
         False)]
ACCEPTED = [(("notes-type", "pure-notes"), False)] + BORN + [
    (("notes-accept", "--slug", POST_SLUG, "--set", "npropset_proof", "--accept-all"), False)]
NOCHANGE = "nochange"   # a step whose plan finds the graph already there: run once, journals nothing
FILES = {"edit-message": {"transcripts/aaaa1111-2222-3333-4444-555566667777.jsonl": TRANSCRIPT},
         "notes-retract": PROPSET, "notes-edit": PROPSET,
         "retire-source": {"site/posts/index.qmd": "---\ntitle: Posts\nlisting: default\n---\n"}}   # the website clone
RUN_VERBS = {"judge-related", "judge-facets", "judge-roles", "judge-concepts", "review-facets", "review-roles",
             "review-concepts"}
NOTES = {"notes-retract", "notes-edit", "notes-rehome"} | RUN_VERBS   # scenarios on the notes lane's sibling + config
JOURNALED_AS = {"notes-type": "deliverable-type", "notes-retract": "retract-point", "notes-rehome": "rehome-points",
                "notes-edit": "edit-point"}   # a CLI verb whose op journals under another name


def _legacy_accepts(
    run: Path,  # This run's directory
    jp: str,    # Its writes journal
) -> None:
    """Points accepted BEFORE the re-home (ruling 96be1528 (P)): accept-point ops carrying no
    `point_set`, so they land under the Note -- the journal an earlier release wrote, never the
    live CLI's (which names the set). Appended unapplied; the next invocation's heal lands them."""
    for k, seg in zip(KEYS[:2], SEGMENTS):
        obs = ReferenceNode.observe("tx", seg, observed_at=1.0).observation()
        point = {"key": k, "kind": "claim", "text": seg["properties"]["text"], "segment_ids": [seg["id"]],
                 "ordinal": 0, "unit": UNIT}
        append_record(jp, {"verb": "accept-point", "args": {"slug": POST_SLUG, "point": point, "observations": [obs],
                                                            "actor": "user:cli", "proposal_set_id": "npropset_proof"}})


def _commit_site(
    run: Path,  # This run's directory
    jp: str,    # Its writes journal (unused: a commit journals nothing)
) -> None:
    """The website clone's history: everything under {run}/site committed at a FIXED time and author,
    so both runs reach the same commit id (the retire-source op carries it)."""
    env = {**os.environ, "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
           "GIT_AUTHOR_NAME": "proof", "GIT_AUTHOR_EMAIL": "proof@example.invalid",
           "GIT_COMMITTER_NAME": "proof", "GIT_COMMITTER_EMAIL": "proof@example.invalid"}
    site = str(run / "site")
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "the archive"]):
        subprocess.run(["git", "-C", site, *cmd], check=True, env=env, capture_output=True)


def _judged(body: dict) -> dict:
    """A deterministic judge for every family: related pairs score high; a Section's role follows
    its heading; every other question is a yes (a Noul at 0.9, a Choice's first related option)."""
    st, qs = body["state"], body.get("questions") or {}
    if "post_a" in st:
        answers = {"relatedness": {"type": "score", "score": 2.5, "confidence": 0.9,
                                   "probabilities": {"0": 0.0, "1": 0.1, "2": 0.3, "3": 0.6}},
                   "relation": {"type": "choice", "choice": "same_technique", "confidence": 0.8,
                                "probabilities": {"same_technique": 0.9, "unrelated": 0.1}}}
    else:
        answers = {}
        for k, q in qs.items():
            if q["type"] == "noul":
                answers[k] = {"type": "noul", "noul": 0.9}
                continue
            opts = sorted(q["criteria"])
            h = str((st.get("section") or {}).get("heading", "")).lower()
            top = ("intro" if "introduction" in h else "step") if "section" in st else \
                next(o for o in opts if o not in ("unrelated", "none"))
            probs = {o: 0.04 for o in opts}
            probs[top] = round(1.0 - 0.04 * (len(opts) - 1), 2)
            answers[k] = {"type": "choice", "choice": top, "probabilities": probs, "confidence": 0.9}
    return {"model": "jev-test", "usage": {"input_tokens": 3}, "answers": answers}


class _Judge(BaseHTTPRequestHandler):
    def do_POST(self):
        out = json.dumps(_judged(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


_JUDGE: list = []   # the module's one judge double, started on first use


def _judge_url() -> str:
    if not _JUDGE:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Judge)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        _JUDGE.append(f"http://127.0.0.1:{server.server_address[1]}/v1/systemone")
    return _JUDGE[0]


THREADS = [(1, "posts/a/", 9), (2, "posts/a/index", 3), (3, "posts/b/", 2)]   # a: a winner and an earlier thread
GH = """import json, sys
q = next(a[len("query="):] for a in sys.argv if a.startswith("query="))
if "discussions" in q:
    print(json.dumps({"data": {"repository": {"hasDiscussionsEnabled": False}}}))
else:
    print(json.dumps({"data": {"repository": {"issues": {"pageInfo": {"hasNextPage": False, "endCursor": None},
        "nodes": [{"number": n, "title": t, "createdAt": f"2024-01-{n:02d}T00:00:00Z",
                   "comments": {"totalCount": c, "nodes": []}} for n, t, c in %r]}}}}))
""" % (THREADS,)


def _fake_gh(
    run: Path,  # This run's directory (its bin/ leads the CLI's PATH)
    jp: str,    # Its writes journal (unused)
) -> None:
    """GitHub, as the harvest asks it through `gh api graphql`: fixed threads, no discussions."""
    (run / "bin").mkdir(exist_ok=True)
    gh = run / "bin" / "gh"
    gh.write_text(f"#!{sys.executable}\n" + GH)
    gh.chmod(0o755)


def _document(
    verb: str,  # A review verb
) -> object:  # A harness action writing its review document to {run}/<verb>.md, unedited
    def write(run: Path, jp: str) -> None:
        r = _cli(str(run / "live.db"), jp, verb, "--out", str(run / f"{verb}.md"))
        assert r.returncode == 0, r.stderr or r.stdout
    return write


# The run verbs' seed (notes lane): two public tutorials sharing their headings, a type mapping every role
ROLES = ("intro", "step")
TUTS = ("onnx-a", "onnx-b")
TUTORIALS = [(("new-note", "--slug", t, "--content",
               f"---\ntitle: \"{t}\"\ndescription: \"About {t}\"\ndate: 2024-01-01\n---\n\n## Introduction\n\n"
               f"We export {t}.\n\n## Training the Model\n\nmodel.fit()\n"), False) for t in TUTS] + [
    (("notes-type", "tut", "--title", "Tutorials", "--kind", "tutorial", "--origin", "archive",
      "--policy-file", "{run}/policy.json"), False)] + [
    (("assert", note_node_id(t), "deliverable_type", "tut"), False) for t in TUTS] + [
    (("assert", note_node_id(t), "publish_state", "published"), False) for t in TUTS]   # born a draft: public once published
POLICY = {"policy.json": json.dumps({"presentation_policy": {"section_roles": {k: "body" for k in ROLES}},
                                     "information_policy": {"concept_relations": ["TEACHES", "ASSUMES"]}})}
FACET_VOCAB = [(("entity", "tool", "onnx", "--name", "onnx", "--description", "ONNX", "--not-for", "a mention"), False),
               (("entity", "task", "detection", "--name", "detection", "--description", "Detection",
                 "--position", "1"), False)]
ROLE_VOCAB = [(("entity", P.ENTITY_SECTION_ROLE, k, "--name", k, "--description", f"The {k}", "--not-for", "-",
                "--position", str(i)), False) for i, k in enumerate(ROLES, 1)]
CONCEPT_VOCAB = [(("entity", "task", "a-task", "--name", "T", "--position", "1"), False),
                 (("entity", "concept", "nms", "--name", "NMS", "--description", "Dropping boxes", "--not-for", "IoU",
                   "--area", "task:a-task"), False)]
JUDGE = ("--url", "{judge}", "--workers", "2")
FILES.update({v: POLICY for v in RUN_VERBS})
FILES["harvest-discussions"] = {"site/_quarto.yml": (
    "website:\n  title: The Site\npost-comments:\n  repo: o/r\n  repo-id: R_1\n  category: Comments\n"
    "  category-id: C_1\n  base-paths: [old-project]\n")}

# Every scenario runs after the seed; a True step writes exactly ONE op and is the one crashed
# between its append and its apply in run B (a False step may write several: an Entity's category
# path rides its op) ({run} = the run's own directory)
SEED = [(("decide", ITEM), False), (("decide", OTHER), False)]
SCENARIOS = {
    "link-unlink-relink": [(("link", ITEM_ID, "REFERENCES", OTHER_ID), True),
                           (("unlink", ITEM_ID, "REFERENCES", OTHER_ID), True),
                           (("link", ITEM_ID[:8], "REFERENCES", OTHER_ID[:8]), True)],
    "check": [(("check", ITEM_ID[:8], "the proof holds"), True)],
    "session-titles": [(("session", KEY), True), (("session", KEY, "--title", "T1"), True),
                       (("session", KEY, "--title", "T2"), False),
                       (("session", KEY, "--title", "T1"), True)],
    "retract-session": [(("session", KEY), False), (("retract-session", KEY), True)],
    "alias": [(("new-note", "--path", "{run}/memory/canon.md", "--content", NOTE), False),
              (("alias", "drifted-slug", "canon-note", "--memory-dir", "{run}/no-memory",
                "--evidence", ITEM_ID[:8]), True)],
    "display-rule": [(("display-rule", "Assertion", "--title", "{predicate} @ {->ABOUT}"), True),
                     (("display-rule", "Assertion", "--title", "{predicate}"), False),
                     (("display-rule", "Assertion", "--title", "{predicate} @ {->ABOUT}"), True)],
    "set-lens": [(("set-lens", "by-kind", "--spec", LENS, "--title", "By kind"), True),
                 (("set-lens", "by-kind", "--spec", LENS, "--title", "Kinds"), False),
                 (("set-lens", "by-kind", "--spec", LENS, "--title", "By kind"), True)],
    "series": [(("series", "a-series", "--title", "A", "--category", "x", "--category", "y"), True),
               (("series", "a-series", "--title", "A", "--category", "x"), True),
               (("series", "a-series", "--title", "A", "--category", "x", "--category", "y"), True)],
    "derived-from": DELIVERABLE + [
        (("notes-type", "archive-notes", "--title", "Archive", "--kind", "notes", "--origin", "archive"), False),
        (("assert", note_node_id("arch-note"), "deliverable_type", "archive-notes"), False)] + WORKS + [
        (("derived-from", "arch-note", "a-book"), True), (("derived-from", "arch-note", "b-book"), True),
        (("derived-from", "arch-note", "a-book"), True), (("derived-from", "arch-note", "--retract"), True)],
    "work-member": [(("link", ITEM_ID, "REFERENCES", f"tx:{SOURCE}"), False)] + WORKS + [
        (("work-member", REF_ID, "a-book"), True), (("work-member", REF_ID, "b-book"), True),
        (("work-member", REF_ID, "a-book"), True), (("work-member", REF_ID, "--retract"), True)],
    "verified-on": DELIVERABLE + [
        (("entity", "hardware", "a-gpu", "--name", "A GPU", "--device-class", "gpu"), False)] + [
        (("verified-on", "arch-note", "a-gpu", "--os", "linux", "--date", d), True)
        for d in ("2024-01-01", "2024-02-02", "2024-01-01")] + [
        (("verified-on", "arch-note", "a-gpu", "--os", "linux", "--retract"), True)],
    "supports": DELIVERABLE + [
        (("entity", "claim", "a-claim", "--name", "A claim", "--statement", "It works", "--position", "1"), False)] + [
        (("supports", "arch-note", "a-claim", "--kind", k), True) for k in ("outcome", "method", "outcome")] + [
        (("supports", "arch-note", "a-claim", "--retract"), True)],
    "relate": DELIVERABLE + [
        (("entity", "task", "a-task", "--name", "T", "--position", "1"), False),
        (("entity", "concept", "a-concept", "--name", "C", "--description", "d", "--not-for", "n",
          "--area", "task:a-task"), False)] + [
        (("relate", "ASSUMES", "arch-note", "concept:a-concept", "--strength", s), True)
        for s in ("required", "recommended", "required")] + [
        (("relate", "ASSUMES", "arch-note", "concept:a-concept", "--retract"), True)],
    "series-members": POSTS + SERIES + [
        (("series-members", "a-series", *order), True)
        for order in (("p1", "p2", "p3"), ("p3", "p2", "p1"), ("p1", "p2", "p3"))],
    "place-in-series": POSTS + SERIES + [(("series-members", "a-series", "p1", "p2", "p3"), False),
                                         (("place-in-series", "a-series", "p3", "--first"), True),
                                         (("place-in-series", "a-series", "p3", "--after", "p2"), True),
                                         (("place-in-series", "a-series", "p3", "--first"), True),
                                         (("place-in-series", "a-series", "p2", "--remove"), True)],
    "edit-message": [(("pull-transcript", KEY, "--transcript-dir", "{run}/transcripts"), False)] + [
        (("edit-message", "u-boot", "--text", t), True) for t in ("T1", "T2", "T1")] + [
        (("edit-message", "u-boot", "--text", "T1", "--set", "flag=1"), True)],
    "notes-type": [(("notes-type", "a-type", "--title", t, "--kind", "notes"), True) for t in ("A", "B", "A")],
    "retire-source": [(("new-note", "--path", "{run}/site/posts/arch.md", "--content", ARCH), False),
                      (("notes-type", "archive-notes", "--title", "Archive", "--kind", "notes", "--origin", "archive"),
                       False),
                      (("assert", note_node_id("arch-note"), "deliverable_type", "archive-notes"), False),
                      (_commit_site, False),
                      (("retire-source", note_node_id("arch-note")[:8], "--reason", "gone",
                        "--website-root", "{run}/site"), True),
                      (("retire-source", note_node_id("arch-note"), "--reason", "gone", "--successor", OTHER_ID[:8],
                        "--website-root", "{run}/site"), True)],
    "notes-retract": ACCEPTED + [(("notes-retract", PT[2]), True),
                                 (("notes-accept", "--slug", POST_SLUG, "--set", "npropset_proof", "--accept-all"),
                                  False),
                                 (("notes-retract", PT[2][:12]), True)],
    "notes-edit": ACCEPTED + [(("notes-edit", PT[2], "--text", t), True)
                              for t in ("Kids never see the whole.", SEGMENTS[2]["properties"]["text"],
                                        "Kids never see the whole.")] + [
        (("notes-edit", PT[2][:12], "--parent", PT[0][:12]), True), (("notes-edit", PT[2], "--lead", "Kids"), True),
        (("notes-edit", PT[2], "--parent", "none"), True)],
    "notes-rehome": [(("notes-type", "pure-notes"), False)] + BORN + [(_legacy_accepts, False),
                                                                     (("notes-rehome", "--slug", POST_SLUG), True)],
    "judge-related": TUTORIALS + [(("judge-related", *JUDGE), True), (("judge-related", "--all", *JUDGE), NOCHANGE)],
    "judge-facets": TUTORIALS + FACET_VOCAB + [(("judge-facets", *JUDGE), True),
                                               (("judge-facets", "--all", *JUDGE), NOCHANGE)],
    "judge-roles": TUTORIALS + ROLE_VOCAB + [(("judge-roles", *JUDGE), True), (("judge-roles", "--all", *JUDGE), NOCHANGE)],
    "judge-concepts": TUTORIALS + CONCEPT_VOCAB + [(("judge-concepts", *JUDGE), True),
                                                   (("judge-concepts", "--all", *JUDGE), NOCHANGE)],
    "review-facets": TUTORIALS + FACET_VOCAB + [(("judge-facets", *JUDGE), False), (_document("review-facets"), False),
                                                (("review-facets", "--apply", "{run}/review-facets.md"), True),
                                                (("review-facets", "--apply", "{run}/review-facets.md"), NOCHANGE)],
    "review-roles": TUTORIALS + ROLE_VOCAB + [(("judge-roles", *JUDGE), False), (_document("review-roles"), False),
                                              (("review-roles", "--apply", "{run}/review-roles.md"), True),
                                              (("review-roles", "--apply", "{run}/review-roles.md"), NOCHANGE)],
    "review-concepts": TUTORIALS + CONCEPT_VOCAB + [(("judge-concepts", *JUDGE), False),
                                                    (_document("review-concepts"), False),
                                                    (("review-concepts", "--apply", "{run}/review-concepts.md"), True),
                                                    (("review-concepts", "--apply", "{run}/review-concepts.md"), NOCHANGE)],
    "harvest-discussions": POSTS[:2] + [(("assert", note_node_id("p1"), "site_path", "/posts/a/"), False),
                                        (("assert", note_node_id("p2"), "site_path", "/posts/b/"), False),
                                        (_fake_gh, False),
                                        (("harvest-discussions", "--website-root", "{run}/site"), True),
                                        (("harvest-discussions", "--website-root", "{run}/site"), NOCHANGE)],
    "transfer-path": [(("assert", ITEM_ID, "site_path", "/a/"), False),
                      (("transfer-path", ITEM_ID[:8], OTHER_ID[:8]), True),
                      (("transfer-path", OTHER_ID, ITEM_ID), True),
                      (("transfer-path", ITEM_ID, OTHER_ID), True)],
}


def _cli(
    db: str,       # The graph db
    journal: str,  # Its writes journal
    *args: str,    # The verb and its arguments
    crash: bool = False,  # Arm the crash between append and apply
) -> subprocess.CompletedProcess:
    # Every call sees the run's harness: the judge's key and the run's bin/ (a fake gh) ahead of PATH
    run = Path(db).parent
    env = {**os.environ, "TYPESAFE_API_KEY": "test-key", "PATH": f"{run / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
           **({JOURNAL_FAULT_ENV: "after_append"} if crash else {})}
    proc = subprocess.Popen([sys.executable, "-m", "cjm_context_graph_projection.cli",
                             "--graph-db-path", db, "--journal-path", journal, *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    out, err = proc.communicate()
    if crash:
        _reap(proc.pid)
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


def _reap(pid: int) -> None:
    """The crashed host's capability worker outlives it (finding f1734c73): stop it by its
    recorded --ppid."""
    for p in psutil.process_iter(["cmdline"]):
        cmd = p.info["cmdline"] or []
        if "--ppid" in cmd and cmd[cmd.index("--ppid") + 1:cmd.index("--ppid") + 2] == [str(pid)]:
            p.terminate()
            try:
                p.wait(10)
            except psutil.TimeoutExpired:
                p.kill()


def _sibling(
    run: Path,           # This run's directory
    notes: bool = False,  # The notes lane's config too: born posts emit under {run}/staging
) -> None:
    """The sibling graph a cross-graph link observes -- one Source and the Segments a point derives
    from -- named in the run's config. Replay reads the observations off the op, never the sibling."""
    async def mint():
        async with open_graph(str(run / "sibling.db")) as sg:
            await extend_graph(sg.queue, sg.graph_id,
                               [{"id": SOURCE, "label": "Source", "properties": {"title": "Chapter 1"}}] + SEGMENTS, [])
    asyncio.run(mint())
    config = {"sibling_graphs": {"tx": str(run / "sibling.db")}}
    if notes:
        config.update(notes_profile="quarto_post", emit_root=str(run / "staging"))
    (run / "graph.config.json").write_text(json.dumps(config))


def _ops(
    jp: str,  # A writes journal
) -> list:  # Its records (none before the first write)
    return read_journal(jp) if Path(jp).exists() else []


def _run(
    run: Path,       # This run's directory
    scenario: str,
    crash: bool,     # Crash (then retry) every marked step
) -> tuple:  # (the journal as (verb, args) with the run's directory and clock abstracted, each step's op count)
    (run / "memory").mkdir(parents=True)
    (run / "no-memory").mkdir()
    db, jp = str(run / "live.db"), str(run / "writes.jsonl")
    steps = SEED + SCENARIOS[scenario]
    if scenario in NOTES or any(a.startswith("tx:") for args, _ in steps if not callable(args) for a in args):
        _sibling(run, notes=scenario in NOTES)
    for rel, text in FILES.get(scenario, {}).items():
        (run / rel).parent.mkdir(parents=True, exist_ok=True)
        (run / rel).write_text(text)
    counts = []
    for args, crash_it in steps:
        if callable(args):
            args(run, jp)
            continue
        args = [a.replace("{run}", str(run)).replace("{judge}", _judge_url()) for a in args]
        before = len(_ops(jp))
        if crash_it == NOCHANGE:
            r = _cli(db, jp, *args)
            assert r.returncode == 0 and _ops(jp)[before:] == [], f"{args}: a no-change step wrote: {r.stdout}{r.stderr}"
            assert "no change" in r.stdout, f"{args}: {r.stdout}"
            continue
        if crash and crash_it:
            r = _cli(db, jp, *args, crash=True)
            assert r.returncode == FAULT_EXIT, f"{args}: no crash ({r.returncode}): {r.stderr or r.stdout}"
        r = _cli(db, jp, *args)
        assert r.returncode == 0, f"{args}: {r.stderr or r.stdout}"
        if crash and crash_it:
            assert "healed: 1 journaled op(s)" in r.stderr, f"{args}: the retry healed nothing"
        new = _ops(jp)[before:]
        if crash_it:
            assert [o["verb"] for o in new] == [JOURNALED_AS.get(args[0], args[0])], \
                f"{args}: a crashed step journals ONE op: {new}"
        counts.append(len(new))
    rebuilt = str(run / "rebuilt.db")
    r = _cli(rebuilt, jp, "replay")
    assert r.returncode == 0, r.stderr or r.stdout
    r = subprocess.run([sys.executable, "-m", "cjm_context_graph_projection.cli",
                        "--graph-db-path", db, "rebuild-diff", "--against", rebuilt],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"live != its replay:\n{r.stdout}{r.stderr}"
    ops = []
    for o in read_journal(jp):
        args = json.loads(json.dumps(o["args"]).replace(str(run), "{run}"))
        for obs in ([args["observation"]] if isinstance(args.get("observation"), dict) else []) + list(
                args.get("observations") or []):   # an observation's time is its run's own clock
            obs["observed_at"] = "{clock}"
        ops.append((o["verb"], args))
    return ops, counts


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_a_crashed_write_heals_to_the_uncrashed_run(tmp_path, scenario):
    uncrashed, counts = _run(tmp_path / "a", scenario, crash=False)
    crashed, crashed_counts = _run(tmp_path / "b", scenario, crash=True)
    assert all(counts), f"a step journaled nothing: {counts}"
    assert crashed_counts == counts and crashed == uncrashed
