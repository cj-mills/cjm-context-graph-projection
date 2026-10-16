"""THE WRITE ORDER'S PER-VERB PROOF (design detail 65f4a6c9 (1)-(2), work item eeda5977 leg L2).

Every verb planned -> appended -> applied is held to one proof, driven through the real CLI:
- run A takes its scenario uncrashed;
- run B takes it with each marked step's process killed between its append and its apply
  (CJM_JOURNAL_FAULT=after_append, exit 86), then that step RETRIED: the retry's writer heals the
  journaled op, and its plan finds the db already there and journals nothing (ruling 17daa86b);
- each live db equals the replay of its own journal ELEMENT FOR ELEMENT (`rebuild-diff`, ruling
  f8b9c728), and the two journals hold the same ops in the same order -- one op per step, an
  A -> B -> A sequence kept whole.
A verb joins the proof by adding its scenario to SCENARIOS."""

import json
import os
import subprocess
import sys
from pathlib import Path

import psutil
import pytest
from cjm_context_graph_primitives.journal import read_journal
from cjm_context_graph_primitives.writer import FAULT_EXIT, JOURNAL_FAULT_ENV
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS
from cjm_dev_graph_schema.nodes import DecisionNode

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

# Every scenario runs after the seed; each step writes exactly ONE op, and a True step is the
# one crashed between its append and its apply in run B ({run} = the run's own directory)
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
}


def _cli(
    db: str,       # The graph db
    journal: str,  # Its writes journal
    *args: str,    # The verb and its arguments
    crash: bool = False,  # Arm the crash between append and apply
) -> subprocess.CompletedProcess:
    env = {**os.environ, JOURNAL_FAULT_ENV: "after_append"} if crash else None
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


def _run(
    run: Path,       # This run's directory
    scenario: str,
    crash: bool,     # Crash (then retry) every marked step
) -> list:  # The journal as (verb, args) with the run's directory abstracted
    (run / "memory").mkdir(parents=True)
    (run / "no-memory").mkdir()
    db, jp = str(run / "live.db"), str(run / "writes.jsonl")
    for args, crash_it in SEED + SCENARIOS[scenario]:
        args = [a.replace("{run}", str(run)) for a in args]
        if crash and crash_it:
            r = _cli(db, jp, *args, crash=True)
            assert r.returncode == FAULT_EXIT, f"{args}: no crash ({r.returncode}): {r.stderr or r.stdout}"
        r = _cli(db, jp, *args)
        assert r.returncode == 0, f"{args}: {r.stderr or r.stdout}"
        if crash and crash_it:
            assert "healed: 1 journaled op(s)" in r.stderr, f"{args}: the retry healed nothing"
    rebuilt = str(run / "rebuilt.db")
    r = _cli(rebuilt, jp, "replay")
    assert r.returncode == 0, r.stderr or r.stdout
    r = subprocess.run([sys.executable, "-m", "cjm_context_graph_projection.cli",
                        "--graph-db-path", db, "rebuild-diff", "--against", rebuilt],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"live != its replay:\n{r.stdout}{r.stderr}"
    return [(o["verb"], json.loads(json.dumps(o["args"]).replace(str(run), "{run}")))
            for o in read_journal(jp)]


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_a_crashed_write_heals_to_the_uncrashed_run(tmp_path, scenario):
    uncrashed = _run(tmp_path / "a", scenario, crash=False)
    crashed = _run(tmp_path / "b", scenario, crash=True)
    assert [v for v, _ in uncrashed] == [args[0] for args, _ in SEED + SCENARIOS[scenario]]
    assert crashed == uncrashed
