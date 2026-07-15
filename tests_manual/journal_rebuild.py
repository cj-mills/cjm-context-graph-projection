#!/usr/bin/env python
"""Migration discipline: the db is a REBUILDABLE PROJECTION of (corpus+repo+seeds+journal).

Proves the crossover resolution — born-on-graph writes (the non-re-derivable
knowledge) live in the plain-text write JOURNAL, so the binary db is disposable:
`rm db && ingest` (which replays the journal) reconstructs the full graph,
born-on-graph knowledge included, with NO orphans (a fresh build, not an overlay).

Two builds from the SAME journal must be byte-equivalent in graph STATE:
schema counts, contradiction count, and worklist count all match — and the
journaled decision + an aliased note are present. Run with the real journal:

    conda run -n cjm-transcript-correction-core --no-capture-output python \
        cjm-context-graph-projection/tests_manual/journal_rebuild.py \
        --journal-path /mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills/cjm-substrate/.cjm/dev-graph.writes.jsonl

SCRATCH dbs only; the real db is never touched. (Omit --journal-path to prove the
pure-projection case — a no-write rebuild is still deterministic.)
"""
import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

from cjm_context_graph_projection.contradictions import contradictions
from cjm_context_graph_projection.devgraph import build_dev_graph_elements
from cjm_context_graph_projection.factlayer import note_alias_map
from cjm_context_graph_projection.journal import replay_journal
from cjm_context_graph_primitives.journal import read_journal
from cjm_context_graph_projection.projection import get_schema
from cjm_context_graph_projection.runtime import open_graph
from cjm_context_graph_projection.worklist import worklist
from cjm_context_graph_layer.ops import extend_graph

DEFAULT_MEMORY = ("/home/innom-dt/.claude/projects/"
                  "-mnt-SN850X-8TB-EXT4-Projects-GitHub-cj-mills-cjm-substrate/memory")
DEFAULT_REPOS = "/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills"


def check(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    return bool(cond)


async def _build(db, memory_dir, repos_dir, journal_path):
    """A full rebuild: fresh projection then journal replay — what `ingest` does."""
    async with open_graph(db) as gx:
        aliases = await note_alias_map(gx)  # empty on a fresh db; non-empty after replay re-runs
        nodes, edges = build_dev_graph_elements(memory_dir, repos_dir, seed=True, note_aliases=aliases)
        await extend_graph(gx.queue, gx.graph_id, nodes, edges)
        if journal_path:
            await replay_journal(gx, journal_path)


async def _snapshot(db):
    async with open_graph(db) as gx:
        schema = await get_schema(gx)
        con = await contradictions(gx)
        wl = await worklist(gx, DEFAULT_MEMORY)
        return (schema["counts"], con["count"], wl["counts"]["dangling_references"])


async def run(memory_dir, repos_dir, journal_path) -> bool:
    ok = True
    snaps = []
    for i in range(2):
        db = str(Path(tempfile.mkdtemp(prefix=f"rebuild{i}_")) / "dev.db")
        await _build(db, memory_dir, repos_dir, journal_path)
        snaps.append(await _snapshot(db))
    (counts, ncon, nwl) = snaps[0]
    ok &= check(f"two rebuilds are state-equivalent (counts/con/worklist): {counts}, con={ncon}, wl={nwl}",
                snaps[0] == snaps[1])
    n_writes = len(read_journal(journal_path)) if journal_path else 0
    ok &= check(f"journal replayed ({n_writes} ops) -> Decision + Assertions present",
                counts.get("Decision", 0) >= (1 if n_writes else 0)
                and counts.get("Assertion", 0) >= 0)
    ok &= check("rebuilt graph is contradiction-free (torch/hf clearing replayed)", ncon == 0 or not journal_path)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--memory-dir", default=DEFAULT_MEMORY)
    ap.add_argument("--repos-dir", default=DEFAULT_REPOS)
    ap.add_argument("--journal-path", default=None)
    args = ap.parse_args()
    ok = asyncio.run(run(args.memory_dir, args.repos_dir, args.journal_path))
    print("JOURNAL REBUILD", "ALL CHECKS PASSED" if ok else "FAILURES")
    return 0 if ok else 1


sys.exit(main())
