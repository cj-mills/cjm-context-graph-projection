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
24fa72c2); a step marked LANE writes only its note lane's source journal (a note born cut over, a
journal-first edit or render: ruling 48c4c34d, f4cc62b9) -- run once, never crashed, journaling no
writes op, its records folded by the rebuild's replay. Each run's config names a note lane: the
notes lane's source journal for a notes scenario (and any whose CONFIG names an emit root), the
memory journal for every other. A step marked with a tuple of verbs writes those ops in that order: a scenario whose longest
tuple has n ops runs n crashed variants, variant k killing each marked step at its k-th op
(CJM_JOURNAL_FAULT=after_append@k; a shorter step dies at its last), and the retry heals that op while
its plan writes the rest -- so a chain completes from every boundary (ruling 547f4123 (1)). A scenario in
MINTED runs a verb that mints a random key (an outline's new section): its ids are compared by first appearance.
The run verbs ask a local JUDGE double ({judge}) and a fake `gh`
on the run's PATH; a scenario keyed in CONFIG gets that graph config, and one in SOURCED carries the
code lane -- its CLI calls name a source journal and its rebuild ingests that journal before the
replay."""

import asyncio
import json
import os
import re
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
from cjm_context_graph_projection.evidence import write_snapshot
from cjm_context_graph_projection.purenotes import NOTES_PROPSET_FORMAT, NOTES_PROPSET_VERSION
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import (code_module_node_id, code_symbol_node_id, note_node_id,
                                           point_node_id, point_set_node_id, section_node_id)
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
LANE = "lane"           # a step writing only its note lane's source journal: run once, journals no writes op
DELIVERABLE = [(("new-note", "--content", ARCH), LANE)]
WORKS = [(("entity", "work", w, "--name", w, "--form", "book"), False) for w in ("a-book", "b-book")]
POSTS = [(("new-note", "--content", f"---\nname: {s}\ndescription: d\n---\n\nbody\n"), LANE)
         for s in ("p1", "p2", "p3")]
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
BORN_POST = "---\ntitle: \"A post\"\ndate: 2026-09-07\n---\n\nLede.\n\n## One\n\nBody one.\n"
DRAFT = "---\ntitle: \"Old draft\"\ndate: 2026-01-01\n---\n\nLede.\n\n## One\n\nBody one.\n\n\n## Two\n\nBody two.\n"
BORN = [(("new-note", "--slug", POST_SLUG, "--content", "---\ntitle: \"A chapter\"\ndate: 2026-09-07\n---\n\nNotes.\n"),
         False)]
ACCEPTED = [(("notes-type", "pure-notes"), False)] + BORN + [
    (("notes-accept", "--slug", POST_SLUG, "--set", "npropset_proof", "--accept-all"), False)]
NOCHANGE = "nochange"   # a step whose plan finds the graph already there: run once, journals nothing
TRANSCRIPT_FILE = "transcripts/aaaa1111-2222-3333-4444-555566667777.jsonl"
FILES = {"edit-message": {"transcripts/aaaa1111-2222-3333-4444-555566667777.jsonl": TRANSCRIPT},
         "pull-transcript": {"transcripts/aaaa1111-2222-3333-4444-555566667777.jsonl": TRANSCRIPT},
         "notes-render": PROPSET,
         "notes-retract": PROPSET, "notes-edit": PROPSET,
         "retire-source": {"site/posts/index.qmd": "---\ntitle: Posts\nlisting: default\n---\n"}}   # the website clone
# mint-messages / derive-message (ruling d2d05e78 (3)): a composer's parts as payload data, the second
# chained to the first; a send derived from both once the pull has landed the sent message
PARTS = [{"uuid": f"c-{i}", "parent_uuid": None, "prev_uuid": f"c-{i - 1}" if i > 1 else None, "role": "user",
          "text": f"part {i}", "timestamp": f"2026-01-01T00:00:0{i}.000Z", "source": "composer"} for i in (1, 2)]
FILES.update({v: {TRANSCRIPT_FILE: TRANSCRIPT, "parts1.json": json.dumps(PARTS[:1]), "parts2.json": json.dumps(PARTS)}
              for v in ("mint-messages", "derive-message")})
MINT = [(("mint-messages", KEY, "--messages-file", "{run}/" + f), m) for f, m in
        (("parts1.json", True), ("parts1.json", NOCHANGE), ("parts2.json", True), ("parts2.json", NOCHANGE))]
RUN_VERBS = {"judge-related", "judge-facets", "judge-roles", "judge-concepts", "review-facets", "review-roles",
             "review-concepts"}
NOTES = {"notes-retract", "notes-edit", "notes-rehome", "notes-render", "new-note-born", "cutover-born"} | RUN_VERBS   # on the notes lane's sibling + config
JOURNALED_AS = {"notes-type": "deliverable-type", "notes-retract": "retract-point", "notes-rehome": "rehome-points",
                "notes-edit": "edit-point", "notes-render": "render-notes"}   # a CLI verb whose op journals under another name


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


def _legacy_note(
    content: str,                  # The note's whole text
    rel: str = "",                 # Its file under the run (a legacy memory-regime note), or "" for a born post
    slug: str = "",                # A born post's permalink
    edits: tuple = (),             # (anchor, raw) section ops journaled after the genesis
) -> object:  # A harness action journaling the note as an earlier release did -- unapplied; the next writer heals it
    def journal(run: Path, jp: str) -> None:
        args = {"content": content, "actor": "agent:session"}
        if rel:
            (run / rel).parent.mkdir(parents=True, exist_ok=True)
            (run / rel).write_text(content)
            args["path"] = str(run / rel)
        if slug:
            args.update(slug=slug, profile="quarto_post")
        append_record(jp, {"verb": "new-note", "args": args})
        for anchor, raw in edits:
            append_record(jp, {"verb": "section", "args": {"slug": slug or rel, "anchor": anchor, "raw": raw,
                                                           "actor": "agent:session"}})
    return journal


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

# pull-transcript: a wider extraction yields a message BETWEEN the boot prompt and the reply (finding
# e358fe97) -- the new one rides the op with the reply, whose NEXT edge from it is absent; the stale
# boot -> reply edge is the planned unlink after it
PULL = ("pull-transcript", KEY, "--transcript-dir", "{run}/transcripts")


def _insert_mid(
    run: Path,  # This run's directory
    jp: str,    # Its writes journal (unused)
) -> None:
    boot, reply = (json.loads(line) for line in TRANSCRIPT.splitlines())
    mid = {"type": "assistant", "isSidechain": False, "uuid": "u-mid", "parentUuid": "u-boot",
           "timestamp": (BOOT + timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
           "message": {"role": "assistant", "content": [{"type": "text", "text": "Reading the surface."}]}}
    (run / TRANSCRIPT_FILE).write_text("".join(json.dumps(r) + "\n" for r in (boot, mid, {**reply, "parentUuid": "u-mid"})))


# ingest-evidence: a traffic snapshot and a hand export, then October re-read (a supersession)
HOST = "example.com"


def _cf_row(day: str, path: str, count: int, visits: int) -> dict:
    return {"count": count, "sum": {"visits": visits}, "avg": {"sampleInterval": 1},
            "dimensions": {"date": day, "requestHost": HOST, "requestPath": path, "bot": 0}}


def _traffic(
    pulled: str,   # The pull date
    window: list,  # [first day, last day]
    rows: list,    # The daily-path rows
) -> object:  # A harness action writing the cloudflare snapshot under {run}/evidence
    def write(run: Path, jp: str) -> None:
        write_snapshot(str(run / "evidence"), "cloudflare", pulled, {f"daily-path-{window[0]}.json": {
            "shape": "daily-path", "window": window, "pulled_at": f"{pulled}T12:00:00+00:00", "row_count": len(rows),
            "response": {"data": {"viewer": {"accounts": [{"rows": rows}]}}}}})
    return write


def _export(
    run: Path,  # This run's directory
    jp: str,    # Its writes journal (unused)
) -> None:
    """A Search Console hand export, as import_export keeps it -- at a fixed time, so both runs agree."""
    csvs = {"Latest links.csv": "Linking page,Last crawled\nhttps://devtalk.com/t/one/1,2026-09-23\n",
            "Top target pages.csv": f"Target page,Incoming links,Linking sites\nhttps://{HOST}/posts/a/,3,2\n"}
    write_snapshot(str(run / "evidence"), "search-console-export", "2026-10-08", {"export.json": {
        "source": "search-console-export", "shape": "export", "pulled_at": "2026-10-08T09:00:00+00:00",
        "files": [{"file": n} for n in sorted(csvs)]}}, blobs={n: t.encode() for n, t in csvs.items()})


# propose: a born post whose section carries `f`'s body, approved, then `f` edited on the code lane. The
# review frontier verifies code against whole-module `source` snapshots only (finding: the element-grain
# lane is unverifiable there), so the seed journals the module as such snapshots, folded live by
# replay-source as cg-rebuild's delta folds them
OLD_F, NEW_F = "def f():\n    return 1\n", "def f():\n    return 2\n"
SYM_ID = code_symbol_node_id(code_module_node_id("r", "m.py"), "f")
TUTORIAL = ("---\ntitle: \"Tutorial\"\ndate: 2026-09-04\n---\n\nLede.\n\n## Use\n\nCall it:\n\n```python\n"
            + OLD_F + "```\n\nAfter.\n")


def _code(
    body: str,          # m.py's `f`
    edited: bool = False,  # The snapshot an `author` of f's body wrote (the frontier names the op)
) -> object:  # A harness action journaling m.py's snapshot and folding it into the live db
    def capture(run: Path, jp: str) -> None:
        (run / "repos" / "r").mkdir(parents=True, exist_ok=True)
        sj = run / "source.jsonl"
        offset = len(read_journal(str(sj))) if sj.exists() else 0
        append_record(str(sj), {"verb": "source", "ts": 1.0 if not edited else datetime.now().timestamp(),
                                "args": {"repo_key": "r", "module_path": "m.py", "text": "import os\n\n\n" + body},
                                **({"op": {"node_id": SYM_ID, "op": "author", "slot": "body"}} if edited else {})})
        r = _cli(str(run / "live.db"), jp, "replay-source", "--offset", str(offset), "--repos-dir", str(run / "repos"))
        assert r.returncode == 0, r.stderr or r.stdout
    return capture


# The C-class chains (ruling 547f4123): a decide with its follow-ons, the entity batches, a survey, the
# version oracle, a relocation read back from a local copy host, a confirmed proposal
CHAIN, CAPTURE, CITES = "WORK ITEM: a chained mint", "CAPTURE: a riding one", "FINDING: a citing one"
BATCH = json.dumps([{"kind": "work", "key": "a-book", "name": "A Book", "fields": {"form": "book"}},
                    {"kind": "unit", "key": "a-book/ch1", "name": "Chapter 1", "fields": {"position": 1}},
                    {"kind": "tool", "key": "onnx", "name": "onnx", "fields": {"description": "ONNX", "not_for": "a mention"}}])
SURVEY = "\t".join(("note_id", "work_key", "work_name", "form", "author", "subtitle", "published", "isbn", "unit_key",
                    "unit_name", "unit_position", "unit_part", "unit_isbn")) + "\n" + "\t".join(
    (note_node_id("arch-note"), "a-book", "A Book", "book", "", "", "", "", "ch1", "Chapter 1", "1", "", "")) + "\n"
VOCAB_DOC = ("- [x] subject `vision` · Vision <!-- vocab subject -->\n  - description: Seeing\n  - not_for: a mention\n"
             "- [x] concept `nms` · NMS <!-- vocab concept -->\n  - description: Dropping boxes\n  - not_for: IoU\n"
             "  - area: subject:vision\n")
REPO = "cjm-proof"   # a repo the ingest's repo map mints (an Entity of kind repo)


def _version(
    v: str,  # The repo's __version__
) -> object:  # A harness action writing {run}/repos/cjm-proof/cjm_proof/__init__.py
    def write(run: Path, jp: str) -> None:
        init = run / "repos" / REPO / REPO.replace("-", "_") / "__init__.py"
        init.parent.mkdir(parents=True, exist_ok=True)
        init.write_text(f'__version__ = "{v}"\n')
    return write


def _repo(
    run: Path,  # This run's directory
    jp: str,    # Its writes journal
) -> None:
    """A committed cjm-* repo under {run}/repos, folded into the live db by an ingest that keeps the
    repo map -- the oracle reads repo Entities, and only the ingest mints them."""
    _version("0.0.2")(run, jp)
    env = {**os.environ, "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
           "GIT_AUTHOR_NAME": "proof", "GIT_AUTHOR_EMAIL": "proof@example.invalid",
           "GIT_COMMITTER_NAME": "proof", "GIT_COMMITTER_EMAIL": "proof@example.invalid"}
    repo = str(run / "repos" / REPO)
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "born"]):
        subprocess.run(["git", "-C", repo, *cmd], check=True, env=env, capture_output=True)
    r = _cli(str(run / "live.db"), jp, "ingest", "--memory-dir", str(run / "no-memory"), "--no-seed",
             "--repos-dir", str(run / "repos"))
    assert r.returncode == 0, r.stderr or r.stdout


def _described(
    run: Path,  # This run's directory
    jp: str,    # Its writes journal (unused)
) -> None:
    """The category description document, edited: the onnx entry's section against its live record."""
    from cjm_context_graph_projection.categorypages import description_basis
    from cjm_context_graph_projection.facetjudge import load_facet_vocab

    async def basis():
        async with open_graph(str(run / "live.db")) as gx:
            return description_basis((await load_facet_vocab(gx))["tool:onnx"])
    (run / "describe.md").write_text(f"## onnx · tool `onnx` · 0 posts <!-- category tool:onnx {asyncio.run(basis())} -->\n\n"
                                     "Page description: Posts that run models through ONNX.\n")


def _proposal(
    run: Path,  # This run's directory
    jp: str,    # Its writes journal
) -> None:
    """The drafted proposal's id ({proposal} in a later step): derived from its journaled op."""
    from cjm_context_graph_layer.identity import derive_node_id
    a = next(o["args"] for o in reversed(read_journal(jp)) if o["verb"] == "propose")
    (run / "proposal.txt").write_text(derive_node_id("proposal", a["deliverable_id"], a["section_id"], a["key"]))


class _Static(BaseHTTPRequestHandler):
    def do_GET(self):   # a raw-file host: the URL's path is the file's absolute path
        p = Path(self.path)
        body = p.read_bytes() if p.is_file() else None
        self.send_response(200 if body is not None else 404)
        self.send_header("Content-Length", str(len(body or b"")))
        self.end_headers()
        self.wfile.write(body or b"")

    def log_message(self, *a):
        pass


_RAW: list = []   # the module's one raw-file host, started on first use


def _raw_url() -> str:
    if not _RAW:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Static)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        _RAW.append(f"http://127.0.0.1:{server.server_address[1]}")
    return _RAW[0]


def _copy(
    run: Path,  # This run's directory
    jp: str,    # Its writes journal (unused)
) -> None:
    """arch-note's projected copy in a clone of o/retired-posts, and the same bytes on the raw host."""
    clone = run / "clone"
    (clone / "posts" / "arch-note").mkdir(parents=True)
    for cmd in (["init", "-q", "-b", "main"], ["remote", "add", "origin", "https://github.com/o/retired-posts.git"]):
        subprocess.run(["git", "-C", str(clone), *cmd], check=True, capture_output=True)
    text = "# Arch note\n\nKept as it was written.\n"
    (clone / "posts" / "arch-note" / "README.md").write_text(text)
    served = run / "served" / "o" / "retired-posts" / "main" / "posts" / "arch-note" / "README.md"
    served.parent.mkdir(parents=True)
    served.write_text(text)


CONFIG = {"ingest-evidence": {"evidence": {"root": "{run}/evidence", "host": HOST}},
          "propose": {"notes_profile": "quarto_post", "emit_root": "{run}/staging",
                      "source_journal_path": "{run}/notes.source.jsonl"},
          "confirm-proposal": {"notes_profile": "quarto_post", "emit_root": "{run}/staging",
                               "source_journal_path": "{run}/notes.source.jsonl"},
          "relocate": {"relocation": {"retired_repo": "o/retired-posts", "raw_base": "{raw}{run}/served"}}}
FILES.update({"entity-batch": {"batch.json": BATCH}, "library-survey": {"survey.tsv": SURVEY},
              "review-concepts-vocab": {"vocab.md": VOCAB_DOC}, "relocate": FILES["retire-source"]})
SOURCED = {"propose", "confirm-proposal", "oracle"}   # scenarios on the code lane (their rebuild is an ingest)

# The notes loops (ruling 547f4123, slice C2): a set accepted whole, the clean slate, a re-head against a fresh
# pack, an outline (a nested section), a placement pass over it (a role and a move)
PACK = json.dumps({"segments": [{"id": seg["id"], "i": i} for i, seg in enumerate(SEGMENTS)],
                   "headers": [{"i_before": 0, "text": "Lessons"}, {"i_before": 2, "text": "Pictures"}]})
OUTLINE = "".join(json.dumps(r) + "\n" for r in ({"section": "Teaching", "first": "p001"},
                                                 {"section": "The picture", "first": "p003", "parent": "Teaching"}))
PLACE = "".join(json.dumps(r) + "\n" for r in ({"point": "p002", "role": "aside", "why": "a digression"},
                                               {"point": "p003", "section": "Teaching", "after": "p001"}))
OUTLINED = [(("notes-outline", "--slug", POST_SLUG, "--rows", "{run}/outline.jsonl", "--apply"), ("accept-point",) * 2)]
FILES.update({"notes-accept": PROPSET, "notes-retract-all": PROPSET, "notes-rehead": {**PROPSET, "pack.json": PACK},
              "notes-outline": {**PROPSET, "outline.jsonl": OUTLINE},
              "notes-place": {**PROPSET, "outline.jsonl": OUTLINE, "place.jsonl": PLACE}})
# notes-overlap --rows: a second set's two points over the first set's lines -- one pair judged different, one folded
SOLO = [f"0000000{i}-1111-4222-8333-444455556666" for i in (3, 4)]
SOLO_ROWS = [{"proposal_id": k, "kind": "claim", "text": t, "segment_ids": [SEGMENTS[i]["id"]], "from_i": i, "to_i": i,
              "heading": "", "heading_index": 0, "start_time": i + 0.5, "end_time": i + 1.0,
              "origins": [{"set_id": "npropset_solo", "proposal_id": k, "cell": "solo/x", "how": "shown"}]}
             for k, i, t in zip(SOLO, (0, 1), ("Gatto stopped teaching.", "Subjects come apart."))]
SOLO_SET = {"purenotes/proposals/npropset_solo/manifest.json": json.dumps(
                {"format": NOTES_PROPSET_FORMAT, "version": NOTES_PROPSET_VERSION, "proposal_set_id": "npropset_solo",
                 "created_at": 2.0, "type": "pure-notes", "model": {"kind": "test"}, "source": UNIT,
                 "files": {"proposals": "proposals.jsonl"}}),
            "purenotes/proposals/npropset_solo/proposals.jsonl": "".join(json.dumps(p) + "\n" for p in SOLO_ROWS),
            "judge.jsonl": "".join(json.dumps(r) + "\n" for r in (
                {"pair": f"q{KEYS[0][:8]}.{SOLO[0][:8]}", "verdict": "different"},
                {"pair": f"q{KEYS[1][:8]}.{SOLO[1][:8]}", "verdict": "same", "keep": "a"}))}
FILES["notes-overlap"] = {**PROPSET, **SOLO_SET}
NOTES |= {"notes-accept", "notes-retract-all", "notes-rehead", "notes-outline", "notes-place", "notes-overlap"}
MINTED = {"notes-outline", "notes-place"}
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
REPO_MAP = {"oracle"}   # ... whose ingest keeps the repo map

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
    "alias": [(("new-note", "--content", NOTE), LANE),
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
    "retire-source": [(_legacy_note(ARCH, rel="site/posts/arch.md"), False),
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
    "mint-messages": MINT,
    "derive-message": [(PULL, False)] + MINT[2:3] + [(("derive-message", "u-boot", "c-1", "c-2"), True),
                                                     (("derive-message", "u-boot", "c-1", "c-2"), NOCHANGE)],
    "pull-transcript": [(PULL, True), (PULL, NOCHANGE), (_insert_mid, False), (PULL, ("pull-transcript", "unlink")),
                        (PULL, NOCHANGE)],
    "ingest-evidence": [(_traffic("2026-10-01", ["2026-09-01", "2026-10-01"],
                                  [_cf_row("2026-09-02", "/posts/a/", 100, 90), _cf_row("2026-10-01", "/posts/a/", 10, 10)]),
                         False), (_export, False),
                        (("ingest-evidence",), ("ingest-evidence", "ingest-evidence")),   # the export, then the traffic
                        (("ingest-evidence", "cloudflare/2026-10-01"), NOCHANGE),
                        (("ingest-evidence", "search-console-export/2026-10-08"), NOCHANGE),
                        (_traffic("2026-10-08", ["2026-10-01", "2026-10-08"], [_cf_row("2026-10-01", "/posts/a/", 12, 11)]),
                         False),
                        (("ingest-evidence",), True)],
    # a born post's render is element state, journal-first in the notes source journal (ruling f4cc62b9)
    "notes-render": ACCEPTED + [(("notes-render", "--slug", POST_SLUG), LANE),
                                (("notes-render", "--slug", POST_SLUG), NOCHANGE),
                                (("notes-render", "--slug", POST_SLUG, "--rendering", "outline"), LANE),
                                (("notes-render", "--slug", POST_SLUG), LANE),
                                (("notes-edit", PT[2], "--text", "Kids never see the whole."), True),
                                (("notes-render", "--slug", POST_SLUG), LANE)],
    # C3 (ruling 48c4c34d): a post born cut over, its draft state planned after the birth applied
    "new-note-born": [(("new-note", "--slug", "a-post", "--content", BORN_POST), ("assert",)),
                      (("new-note", "--slug", "a-post", "--content", BORN_POST), NOCHANGE)],
    # ... and a draft the writes journal's genesis op born, captured from the db, then authored journal-first
    "cutover-born": [(_legacy_note(DRAFT, slug="old-draft", edits=(("one", "## One\n\nBody one, legacy.\n\n"),)), False),
                     (("cutover-born",), LANE), (("cutover-born",), NOCHANGE),
                     (("author", section_node_id(note_node_id("old-draft"), "one"), "--edit", "legacy", "cut over"),
                      LANE),
                     (("add-section", "old-draft", "--content", "## Three\n\nBody three.", "--after", "two"), LANE),
                     (("assert", note_node_id("old-draft"), "publish_state", "reviewed"), True)],
    "propose": [(_code(OLD_F), False), (("new-note", "--slug", "tutorial", "--content", TUTORIAL), False),
                (("link", section_node_id(note_node_id("tutorial"), "use"), "REFERENCES", SYM_ID), False),
                (("assert", note_node_id("tutorial"), "publish_state", "published"), False),
                (_code(NEW_F, edited=True), False), (("propose",), True), (("propose",), NOCHANGE)],
    "decide": [(("decide", CHAIN, "--state", "open"), ("decide", "assert")),
               (("decide", CHAIN, "--state", "open"), NOCHANGE),
               (("decide", CAPTURE, "--capture", f"riding:{ITEM_ID[:8]}"), ("decide", "assert", "link")),
               (("decide", CITES, "--supports", ITEM_ID[:8], "--supersedes", OTHER_ID[:8]), True)],
    "entity": [(("entity", "tool", "onnx", "--name", "onnx", "--description", "ONNX", "--not-for", "a mention"),
                ("entity", "assert")),
               (("entity", "tool", "onnx", "--name", "onnx", "--description", "ONNX", "--not-for", "a mention"),
                NOCHANGE)],
    "entity-batch": [(("entity-batch", "{run}/batch.json", "--apply"), ("entity", "entity", "entity", "assert")),
                     (("entity-batch", "{run}/batch.json", "--apply"), NOCHANGE)],
    "describe-categories": FACET_VOCAB[:1] + [(_described, False),
                                              (("describe-categories", "--apply", "{run}/describe.md"), ("entity",)),
                                              (("describe-categories", "--apply", "{run}/describe.md"), NOCHANGE)],
    "review-concepts-vocab": [(("review-concepts", "--vocab", "--apply", "{run}/vocab.md"),
                               ("entity", "entity", "assert")),
                              (("review-concepts", "--vocab", "--apply", "{run}/vocab.md"), NOCHANGE)],
    "library-survey": DELIVERABLE + [
        (("notes-type", "archive-notes", "--title", "Archive", "--kind", "notes", "--origin", "archive"), False),
        (("assert", note_node_id("arch-note"), "deliverable_type", "archive-notes"), False),
        (("library-survey", "{run}/survey.tsv", "--apply"), ("entity", "entity", "derived-from")),
        (("library-survey", "{run}/survey.tsv", "--apply"), NOCHANGE)],
    "oracle": [(_repo, False),
               (("oracle", "--repos-dir", "{run}/repos"), ("procedure", "assert")),
               (("oracle", "--repos-dir", "{run}/repos"), NOCHANGE), (_version("0.0.3"), False),
               (("oracle", "--repos-dir", "{run}/repos"), ("assert",)), (_version("0.0.1"), False),
               (("oracle", "--repos-dir", "{run}/repos"), NOCHANGE)],
    "relocate": [(_legacy_note(ARCH, rel="site/posts/arch.md"), False),
                 (("notes-type", "archive-notes", "--title", "Archive", "--kind", "notes", "--origin", "archive"), False),
                 (("assert", note_node_id("arch-note"), "deliverable_type", "archive-notes"), False),
                 (("assert", note_node_id("arch-note"), P.CURRENCY, "removed"), False),
                 (_commit_site, False), (_copy, False),
                 (("relocate", "land", "arch-note", "--into", "{run}/clone", "--website-root", "{run}/site"),
                  ("retire-source", "relocate")),
                 (("relocate", "land", "arch-note", "--into", "{run}/clone", "--website-root", "{run}/site"), NOCHANGE)],
    "confirm-proposal": [(_code(OLD_F), False), (("new-note", "--slug", "tutorial", "--content", TUTORIAL), False),
                         (("link", section_node_id(note_node_id("tutorial"), "use"), "REFERENCES", SYM_ID), False),
                         (("assert", note_node_id("tutorial"), "publish_state", "published"), False),
                         (_code(NEW_F, edited=True), False), (("propose",), False), (_proposal, False),
                         (("confirm-proposal", "{proposal}"), ("assert",)),   # the section: journal-first (48c4c34d (6))
                         (("confirm-proposal", "{proposal}"), NOCHANGE)],
    "notes-accept": [(("notes-type", "pure-notes"), False)] + BORN + [
        (("notes-accept", "--slug", POST_SLUG, "--set", "npropset_proof", "--accept-all"),
         ("link", "assert") + ("accept-point",) * 3),
        (("notes-accept", "--slug", POST_SLUG, "--set", "npropset_proof", "--accept-all"), NOCHANGE)],
    "notes-retract-all": ACCEPTED + [(("notes-retract", "--slug", POST_SLUG, "--all"), ("retract-point",) * 3),
                                     (("notes-retract", "--slug", POST_SLUG, "--all"), NOCHANGE)],
    "notes-rehead": ACCEPTED + [(("notes-rehead", "--slug", POST_SLUG, "--pack", "{run}/pack.json"), ("edit-point",) * 3),
                                (("notes-rehead", "--slug", POST_SLUG, "--pack", "{run}/pack.json"), NOCHANGE)],
    "notes-outline": ACCEPTED + OUTLINED + [(OUTLINED[0][0], NOCHANGE)],
    "notes-place": ACCEPTED + [(OUTLINED[0][0], False),
                               (("notes-place", "--slug", POST_SLUG, "--rows", "{run}/place.jsonl", "--apply"),
                                ("assert", "place-point")),
                               (("notes-place", "--slug", POST_SLUG, "--rows", "{run}/place.jsonl", "--apply"), NOCHANGE)],
    "notes-overlap": ACCEPTED + [
        (("notes-accept", "--slug", POST_SLUG, "--set", "npropset_solo", "--accept-all"), False),
        (("notes-overlap", "--slug", POST_SLUG, "--rows", "{run}/judge.jsonl"), ("edit-point",) * 3 + ("retract-point",)),
        (("notes-overlap", "--slug", POST_SLUG, "--rows", "{run}/judge.jsonl"), NOCHANGE)],
    "transfer-path": [(("assert", ITEM_ID, "site_path", "/a/"), False),
                      (("transfer-path", ITEM_ID[:8], OTHER_ID[:8]), True),
                      (("transfer-path", OTHER_ID, ITEM_ID), True),
                      (("transfer-path", ITEM_ID, OTHER_ID), True)],
}


def _cli(
    db: str,       # The graph db
    journal: str,  # Its writes journal
    *args: str,    # The verb and its arguments
    crash: int = 0,  # Arm the crash between append and apply at the invocation's crash-th write (0 = none)
) -> subprocess.CompletedProcess:
    # Every call sees the run's harness: the judge's key and the run's bin/ (a fake gh) ahead of PATH,
    # and on the code lane (a run with repos/) its source journal
    run = Path(db).parent
    sourced = ("--source-journal-path", str(run / "source.jsonl")) if (run / "repos").exists() else ()
    env = {**os.environ, "TYPESAFE_API_KEY": "test-key", "PATH": f"{run / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
           **({JOURNAL_FAULT_ENV: f"after_append@{crash}"} if crash else {})}
    proc = subprocess.Popen([sys.executable, "-m", "cjm_context_graph_projection.cli",
                             "--graph-db-path", db, "--journal-path", journal, *sourced, *args],
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
        config.update(notes_profile="quarto_post", emit_root=str(run / "staging"),
                      source_journal_path=str(run / "notes.source.jsonl"))
    else:
        config["memory_journal_path"] = str(run / "memory.jsonl")
    (run / "graph.config.json").write_text(json.dumps(config))


def _ops(
    jp: str,  # A writes journal
) -> list:  # Its records (none before the first write)
    return read_journal(jp) if Path(jp).exists() else []


def _run(
    run: Path,       # This run's directory
    scenario: str,
    crash: int,      # Crash (then retry) every marked step at this op of it (0 = uncrashed; a shorter step at its last)
) -> tuple:  # (the journal as (verb, args) with the run's directory and clock abstracted, each step's op count)
    (run / "memory").mkdir(parents=True)
    (run / "no-memory").mkdir()
    db, jp = str(run / "live.db"), str(run / "writes.jsonl")
    steps = SEED + SCENARIOS[scenario]
    # every run names a note lane (ruling 48c4c34d): the memory journal, or the notes lane's source journal
    (run / "graph.config.json").write_text(json.dumps({"memory_journal_path": str(run / "memory.jsonl")}))
    if scenario in NOTES or any(a.startswith("tx:") for args, _ in steps if not callable(args) for a in args):
        _sibling(run, notes=scenario in NOTES)
    if scenario in CONFIG:
        (run / "graph.config.json").write_text(json.dumps(CONFIG[scenario]).replace("{raw}", _raw_url())
                                               .replace("{run}", str(run)))
    for rel, text in FILES.get(scenario, {}).items():
        (run / rel).parent.mkdir(parents=True, exist_ok=True)
        (run / rel).write_text(text)
    counts = []
    for args, crash_it in steps:
        if callable(args):
            args(run, jp)
            continue
        proposal = (run / "proposal.txt").read_text() if (run / "proposal.txt").exists() else ""
        args = [a.replace("{run}", str(run)).replace("{judge}", _judge_url()).replace("{proposal}", proposal)
                for a in args]
        before = len(_ops(jp))
        if crash_it == LANE:
            r = _cli(db, jp, *args)
            assert r.returncode == 0, f"{args}: {r.stderr or r.stdout}"
            assert _ops(jp)[before:] == [], f"{args}: a source-journal step wrote the writes journal: {_ops(jp)[before:]}"
            continue
        if crash_it == NOCHANGE:
            r = _cli(db, jp, *args)
            assert r.returncode == 0 and _ops(jp)[before:] == [], f"{args}: a no-change step wrote: {r.stdout}{r.stderr}"
            assert "no change" in r.stdout, f"{args}: {r.stdout}"
            continue
        if crash and crash_it:
            r = _cli(db, jp, *args, crash=min(crash, len(crash_it) if isinstance(crash_it, tuple) else 1))
            assert r.returncode == FAULT_EXIT, f"{args}: no crash ({r.returncode}): {r.stderr or r.stdout}"
        r = _cli(db, jp, *args)
        assert r.returncode == 0, f"{args}: {r.stderr or r.stdout}"
        if crash and crash_it:
            assert "healed: 1 journaled op(s)" in r.stderr, f"{args}: the retry healed nothing"
        new = _ops(jp)[before:]
        if crash_it:
            want = list(crash_it) if isinstance(crash_it, tuple) else [JOURNALED_AS.get(args[0], args[0])]
            assert [o["verb"] for o in new] == want, f"{args}: the step journals {want}: {new}"
        counts.append(len(new))
    rebuilt = str(run / "rebuilt.db")
    r = (_cli(rebuilt, jp, "ingest", "--memory-dir", str(run / "no-memory"),
              *(() if scenario in REPO_MAP else ("--no-repo-map",)), "--no-seed", "--repos-dir", str(run / "repos"))
         if scenario in SOURCED else _cli(rebuilt, jp, "replay"))
    assert r.returncode == 0, r.stderr or r.stdout
    r = subprocess.run([sys.executable, "-m", "cjm_context_graph_projection.cli",
                        "--graph-db-path", db, "rebuild-diff", "--against", rebuilt],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"live != its replay:\n{r.stdout}{r.stderr}"
    ops = []
    for o in read_journal(jp):
        args = json.loads(json.dumps(o["args"]).replace(str(run), "{run}"))
        for obs in ([args["observation"]] if isinstance(args.get("observation"), dict) else []) + list(
                args.get("observations") or []) + ([args] if o["verb"] == "relocate" else []):
            obs["observed_at"] = "{clock}"   # an observation's time is its run's own clock
        ops.append((o["verb"], args))
    if scenario in MINTED:   # a minted key is random per run: each id compared by its first appearance
        seen: dict = {}
        ops = json.loads(UUID.sub(lambda m: seen.setdefault(m.group(0), f"{{id{len(seen)}}}"), json.dumps(ops)))
    return ops, counts


def _variants(
    scenario: str,
) -> range:  # The op boundaries its chains are crashed at: 1 .. its longest marked step
    return range(1, max([len(m) for _, m in SCENARIOS[scenario] if isinstance(m, tuple)] + [1]) + 1)


@pytest.mark.parametrize("scenario,k", [(s, k) for s in sorted(SCENARIOS) for k in _variants(s)])
def test_a_crashed_write_heals_to_the_uncrashed_run(tmp_path, scenario, k):
    uncrashed, counts = _run(tmp_path / "a", scenario, crash=0)
    crashed, crashed_counts = _run(tmp_path / "b", scenario, crash=k)
    assert all(counts), f"a step journaled nothing: {counts}"
    assert crashed_counts == counts and crashed == uncrashed


def test_derive_message_refuses_a_message_not_on_the_graph(tmp_path):
    # ruling d2d05e78 (3): an edge to nothing is refused in the plan -- nothing journaled, nothing landed
    (tmp_path / "graph.config.json").write_text(json.dumps({"memory_journal_path": str(tmp_path / "memory.jsonl")}))
    db, jp = str(tmp_path / "live.db"), str(tmp_path / "writes.jsonl")
    r = _cli(db, jp, "derive-message", "u-none", "c-1")
    assert r.returncode == 1 and "no Message node for source uuid(s) u-none, c-1" in r.stderr, r.stderr
    assert _ops(jp) == []
