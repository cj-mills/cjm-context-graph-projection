"""Read-path latency seed probe (work item f4701770): where does the half-second go?

Manual, like the paint probes: run against the LIVE dev graph from a neutral cwd,
one open graph, best-of-3 per view. 2026-08-13 baseline: open_graph 240ms · show
254ms WITH journals vs 51ms without (the journal-scan term — O(journal) by
construction) · read_node 55ms · portfolio_view 672/480ms (frontier vitals
dominate even journal-free) · anchor_lead_view 138ms."""

import asyncio
import os
import time

from cjm_context_graph_projection.authoring import read_node
from cjm_context_graph_projection.projection import show
from cjm_context_graph_projection.runtime import DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.workbench import anchor_lead_view, portfolio_view

os.environ.setdefault("CJM_WORKSPACE",
                      "/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills/cjm-transcription-core")
SUB = "/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills/cjm-substrate/.cjm"
DB = f"{SUB}/dev-graph.db"
JP = [f"{SUB}/dev-graph.writes.jsonl", f"{SUB}/dev-graph.source.jsonl"]
NODE = "c81a750d-9ec5-50ff-9623-b83552bfc443"  # the craft register note (big body, many sections)


async def main():
    t0 = time.perf_counter()
    async with open_graph(DB, DEFAULT_MANIFESTS) as gx:
        t_open = time.perf_counter() - t0

        async def timed(label, coro_fn, n=3):
            vals = []
            for _ in range(n):
                t = time.perf_counter()
                await coro_fn()
                vals.append((time.perf_counter() - t) * 1000)
            print(f"{label:34s} {min(vals):7.1f} ms (best of {n}; worst {max(vals):.1f})")

        print(f"{'open_graph':34s} {t_open * 1000:7.1f} ms")
        await timed("show (with journals)", lambda: show(gx, NODE, journal_paths=JP))
        await timed("show (NO journals)", lambda: show(gx, NODE, journal_paths=[]))
        await timed("read_node", lambda: read_node(gx, NODE))
        await timed("portfolio_view (with journals)", lambda: portfolio_view(gx, journal_paths=JP))
        await timed("portfolio_view (NO journals)", lambda: portfolio_view(gx, journal_paths=[]))
        await timed("anchor_lead_view", lambda: anchor_lead_view(gx, "program-substrate-foundations"))


if __name__ == "__main__":  # runtime-order: must trail every def (python -m executes in slot order)
    asyncio.run(main())
