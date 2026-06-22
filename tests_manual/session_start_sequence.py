#!/usr/bin/env python
"""Inc 2 DoD: the canonical session-start sequence orients on a real task.

Builds the dev graph on a scratch db and runs schema -> state -> relevant ->
show, asserting the reads are bounded + on-target:

  1. schema reports the coarse node labels (Note + Entity).
  2. relevant("self-hosting graph arc ...") surfaces the arc's first-slice plan
     in its top results (the 2026-06-21 planning session's home note) — i.e. the
     sequence orients on the arc task as well as reading the files would.
  3. relevant is BOUNDED (<= k) and provenance-carrying (every result has a why).
  4. show on a repo Entity returns its DEPENDS_ON neighbours (the repo map).

Run in a core env with the substrate runtime + the new libs installed:

    conda run -n cjm-transcript-correction-core --no-capture-output python \
        cjm-context-graph-projection/tests_manual/session_start_sequence.py

A SCRATCH db is used; no real graph is touched.
"""
import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

from cjm_context_graph_layer.ops import extend_graph

from cjm_context_graph_projection.devgraph import build_dev_graph_elements
from cjm_context_graph_projection.projection import get_schema, relevant, show, state
from cjm_context_graph_projection.runtime import open_graph

DEFAULT_MEMORY = ("/home/innom-dt/.claude/projects/"
                  "-mnt-SN850X-8TB-EXT4-Projects-GitHub-cj-mills-cjm-substrate/memory")
DEFAULT_REPOS = "/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills"


def check(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    return bool(cond)


async def run(memory_dir: str, repos_dir: str) -> bool:
    scratch = Path(tempfile.mkdtemp(prefix="session_start_")) / "dev.db"
    nodes, edges = build_dev_graph_elements(memory_dir, repos_dir)
    ok = True
    async with open_graph(str(scratch)) as gx:
        await extend_graph(gx.queue, gx.graph_id, nodes, edges)

        schema = await get_schema(gx)
        ok &= check("schema reports Note + Entity labels",
                    {"Note", "Entity"} <= set(schema.get("node_labels", [])))

        K = 12
        rel = await relevant(gx, "self-hosting graph arc increment projection CLI", k=K)
        titles = [r["title"] for r in rel["results"]]
        ok &= check("relevant surfaces the arc first-slice plan in top results",
                    any("First Slice Plan" in t for t in titles))
        ok &= check(f"relevant is bounded (<= {K})", len(rel["results"]) <= K)
        ok &= check("every relevant result carries a why + id",
                    all(r.get("why") and r.get("id") for r in rel["results"]))

        # state resolves a repo subject -> show its DEPENDS_ON neighbourhood.
        st = await state(gx, "cjm-markdown-decompose-core")
        rels = {n["relation"] for n in st.get("neighbours", [])}
        ok &= check("state(repo) shows DEPENDS_ON neighbours (repo map)", "DEPENDS_ON" in rels)

        # show the top relevant node -> bounded neighbour list.
        top_id = rel["results"][0]["id"]
        sh = await show(gx, top_id)
        ok &= check("show returns the node + neighbours list",
                    sh.get("node") is not None and isinstance(sh.get("neighbours"), list))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--memory-dir", default=DEFAULT_MEMORY)
    ap.add_argument("--repos-dir", default=DEFAULT_REPOS)
    args = ap.parse_args()
    ok = asyncio.run(run(args.memory_dir, args.repos_dir))
    print("SESSION-START SEQUENCE", "ALL CHECKS PASSED" if ok else "FAILURES")
    return 0 if ok else 1


sys.exit(main())
