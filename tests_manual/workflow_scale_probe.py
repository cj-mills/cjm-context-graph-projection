"""Workflow-graph scale probe (post-865e6a33 follow-on): does the transcription
workflow db (~354MB, 156k nodes / 327k edges, ~15x the dev graph) stay
responsive through the substrate path the Qt workbench uses?

Manual, like latency_probe: run against the LIVE workflow graph from a neutral
cwd; read-only by construction (get_node / query_nodes / query_edges / show).
2026-08-13 baseline (post per-call fix): open_graph 338ms cold · point read
3.7ms · episode open (edge query + 1030-segment batch) 51ms · count Segment
5.3ms · Segment page limit=1000 39ms · full NEXT spine 121,879 pairs 270ms
(the one interactive-budget breaker — windowed views stay doctrine) · show on
a 4462-neighbour CorrectionSession 77ms journal-free / 94ms warm journals ·
COLD 284MB journal-family parse 1292ms once per process."""

import asyncio
import os
import time

from cjm_context_graph_layer.ops import graph_task
from cjm_context_graph_primitives.query import EdgeQuery
from cjm_context_graph_projection.factlayer import (
    count_label, load_edge_pairs, load_label_where, load_nodes)
from cjm_context_graph_projection.projection import show
from cjm_context_graph_projection.runtime import DEFAULT_MANIFESTS, open_graph

os.environ.setdefault("CJM_WORKSPACE",
                      "/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills/cjm-transcription-core")
WF = ("/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills/cjm-transcription-core/"
      ".cjm/data/cjm-capability-graph-sqlite")
DB = f"{WF}/context_graph.db"
JP = [f"{WF}/context_graph.writes.jsonl"]

TRANSCRIPT = "8dfa33df-8829-534a-bfe4-519273c2c41e"   # a whisper transcript
RENDITION = "00c01e81-08ff-5276-b06c-6bce459d67f8"    # biggest: 1030 segments
BUSY_SESSION = "f9a44bca-74ce-4f40-8ce8-54f7c08297d8" # CorrectionSession, 4462 edges


async def timed(label, coro_fn, n=3):
    vals = []
    out = None
    for _ in range(n):
        t = time.perf_counter()
        out = await coro_fn()
        vals.append((time.perf_counter() - t) * 1000)
    extra = f" (best of {n}; worst {max(vals):.1f})" if n > 1 else ""
    print(f"{label:44s} {min(vals):8.1f} ms{extra}")
    return out


async def main():
    t0 = time.perf_counter()
    async with open_graph(DB, DEFAULT_MANIFESTS) as gx:
        print(f"{'open_graph (354MB db, cold)':44s} {(time.perf_counter() - t0) * 1000:8.1f} ms")

        await timed("get_node (point read)",
                    lambda: graph_task(gx.queue, gx.graph_id, "get_node", node_id=TRANSCRIPT))

        # "Open an episode" shape: edge query for the rendition's segment ids,
        # then ONE batched node fetch of all 1030 segments.
        async def episode_open():
            res = await graph_task(
                gx.queue, gx.graph_id, "query_edges",
                query=EdgeQuery(relation_type="PART_OF", target_id=RENDITION,
                                project=["source_id"]).to_dict())
            ids = [r["source_id"] for r in (res.rows or [])]
            nodes = await load_nodes(gx, ids)
            return len(nodes)

        n = await timed("episode open (edges + 1030-segment batch)", episode_open)
        print(f"{'':44s}   ({n} segments)")

        await timed("count_label Segment (120,807 server-side)",
                    lambda: count_label(gx, "Segment"))
        await timed("page pull: Segment limit=1000",
                    lambda: load_label_where(gx, "Segment", [], limit=1000))
        await timed("full spine: NEXT edge pairs (121,879)",
                    lambda: load_edge_pairs(gx, "NEXT"))
        await timed("show busiest CorrectionSession (4462 nbrs, no journals)",
                    lambda: show(gx, BUSY_SESSION, journal_paths=[]))
        await timed("show (WITH 284MB journal family, COLD)",
                    lambda: show(gx, BUSY_SESSION, journal_paths=JP), n=1)
        await timed("show (WITH journal family, warm)",
                    lambda: show(gx, BUSY_SESSION, journal_paths=JP))


if __name__ == "__main__":  # runtime-order: must trail every def (python -m executes in slot order)
    asyncio.run(main())
