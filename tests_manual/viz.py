#!/usr/bin/env python
"""Minimal read-only VIZ projector — the readiness frontier + dependency DAG as self-contained HTML.

The P1 "see it while the capstone grows" tool (DEC-ARC 17bf27d4), a PROJECTION over `readiness`:

  A. VIZ_ELEMENTS (pure): a frontier -> Cytoscape nodes+edges. Work-items carry their true
     done/ready/blocked state; a gate target that is ALSO a work-item keeps that state (not
     downgraded to `prereq`); a non-work-item gate target is minted as `prereq`; every edge
     endpoint resolves (no dangling); edge count == total gate references.
  B. RENDER (pure): one self-contained HTML doc — doctype, Cytoscape+dagre from CDN, the elements
     inlined as PARSEABLE JSON, a legend + counts header; long labels truncated (full label kept).
  C. PROJECT_VIZ (integration): runs end-to-end on a scratch graph (empty frontier -> valid,
     zero-node HTML) — the pipe from graph to HTML holds.

    conda run -n cjm-transcript-correction-core --no-capture-output python \
        cjm-context-graph-projection/tests_manual/viz.py
"""
import asyncio
import json
import re
import sys
import tempfile
from pathlib import Path

from cjm_context_graph_projection.runtime import open_graph
from cjm_context_graph_projection.viz import project_viz, render_viz_html, viz_elements


def _check(label, ok):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    return ok


def _nodes_edges(elements):
    nodes = {e["data"]["id"]: e["data"] for e in elements if "source" not in e["data"]}
    edges = [e["data"] for e in elements if "source" in e["data"]]
    return nodes, edges


# A synthetic frontier: `blocked1` is gated by `ready1` (a work-item) AND `ext1` (a non-work-item),
# so it exercises the "a work-item referenced as a gate keeps its state" path AND the external mint.
FRONTIER = {
    "done": [{"id": "done1", "label": "a finished thing"}],
    "ready": [{"id": "ready1", "label": "a ready thing", "gates": [{"id": "done1", "label": "a finished thing"}]}],
    "blocked": [{"id": "blocked1", "label": "x " * 60 + "END",  # long label -> truncation
                 "blocked_by": [{"id": "ready1", "label": "a ready thing"},
                                {"id": "ext1", "label": "an external prerequisite"}]}],
    "counts": {"ready": 1, "blocked": 1, "done": 1},
}


def part_a() -> bool:
    ok = True
    els = viz_elements(FRONTIER)
    nodes, edges = _nodes_edges(els)
    ok &= _check("one node per distinct id (3 work-items + 1 external = 4)", len(nodes) == 4)
    ok &= _check("work-item states are the true derived states",
                 nodes["done1"]["state"] == "done" and nodes["ready1"]["state"] == "ready"
                 and nodes["blocked1"]["state"] == "blocked")
    ok &= _check("a work-item used as a gate keeps its state (ready1 not downgraded to prereq)",
                 nodes["ready1"]["state"] == "ready")
    ok &= _check("a non-work-item gate target is minted as `prereq`", nodes["ext1"]["state"] == "prereq")
    ok &= _check("edge count == total gate references (1 for ready1 + 2 for blocked1 = 3)", len(edges) == 3)
    ids = set(nodes)
    ok &= _check("no dangling edge endpoints",
                 all(e["source"] in ids and e["target"] in ids for e in edges))
    ok &= _check("edges point item -> prerequisite",
                 {(e["source"], e["target"]) for e in edges} ==
                 {("ready1", "done1"), ("blocked1", "ready1"), ("blocked1", "ext1")})
    ok &= _check("a long label is truncated on the node face but the full label is retained",
                 nodes["blocked1"]["label"].endswith("…") and nodes["blocked1"]["full"].endswith("END"))
    return ok


def part_b() -> bool:
    ok = True
    els = viz_elements(FRONTIER)
    doc = render_viz_html(els, FRONTIER["counts"], scope="mem")
    ok &= _check("is a full HTML document", doc.lstrip().startswith("<!doctype html>") and "</html>" in doc)
    ok &= _check("self-contained: Cytoscape + dagre pulled from CDN",
                 "cytoscape.min.js" in doc and "dagre.min.js" in doc and "cytoscape-dagre" in doc)
    m = re.search(r"const elements = (\[.*?\]);", doc, re.S)
    ok &= _check("elements are inlined as PARSEABLE JSON", m is not None and json.loads(m.group(1)) == els)
    ok &= _check("header shows the counts + scope", "1 ready · 1 blocked · 1 done" in doc and "mem" in doc)
    ok &= _check("legend names every state", all(name in doc for _, name in
                 (("done", "done"), ("ready", "ready"), ("blocked", "blocked"))))
    ok &= _check("script tags are balanced", doc.count("<script") == doc.count("</script>"))
    return ok


async def part_c() -> bool:
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        async with open_graph(str(Path(tmp) / "viz.db")) as gx:
            res = await project_viz(gx)  # empty graph -> empty frontier
            ok &= _check("project_viz runs end-to-end on a scratch graph", "html" in res)
            ok &= _check("empty frontier -> zero nodes/edges but still valid HTML",
                         res["node_count"] == 0 and res["edge_count"] == 0
                         and res["html"].lstrip().startswith("<!doctype html>"))
            ok &= _check("counts are all zero on an empty graph",
                         res["counts"] == {"ready": 0, "blocked": 0, "done": 0})
    return ok


async def main() -> int:
    ok = True
    print("A — viz_elements (pure data model):")
    ok &= part_a()
    print("B — render_viz_html (self-contained page):")
    ok &= part_b()
    print("C — project_viz (end-to-end on a scratch graph):")
    ok &= await part_c()
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
