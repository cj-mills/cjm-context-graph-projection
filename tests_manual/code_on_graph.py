#!/usr/bin/env python
"""Code-on-graph dogfood: decompose the arc libs' own `.py` and prove the cross-link.

The first post-slice spiral turn (the "beyond memory files" source type): the new
`cjm-python-decompose-core` decomposes the ecosystem's own Python into CodeModule /
CodeSymbol nodes that CO-RESIDE with the markdown decision/note nodes on one graph.
This harness pins the DoD end-to-end against a SCRATCH db (the real persistent
dev-graph is never touched):

  1. Code ingests: the arc libs' modules + symbols land as nodes, with DEFINES
     (module->symbol, class->method), IMPORTS (module->module, cross-repo), CALLS
     (symbol->symbol), and ABOUT (module->repo Entity) edges.
  2. CO-RESIDENCE + relevance: a `relevant` query about a concept returns the
     governing Decision AND the CodeSymbols that implement it in one ranked, bounded
     read (code joins the decision/note neighborhood through the shared repo Entity
     and the DEFINES/CALLS edges).
  3. The direct cross-link: `link <decision> IMPLEMENTED_BY <code-symbol>` mints a
     deliberate heterogeneous edge (the general connector behind the perf-debt /
     cross-project-reference vision); `show <decision>` then lists the symbol as a
     neighbour. The link is idempotent and journaled (durable across a db rebuild).

Run in a core env with the substrate runtime + the new libs installed -e:

    conda run -n cjm-transcript-correction-core --no-capture-output python \
        cjm-context-graph-projection/tests_manual/code_on_graph.py
"""
import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

from cjm_context_graph_layer.ops import extend_graph
from cjm_dev_graph_schema.identity import (code_module_node_id, code_symbol_node_id,
                                           decision_node_id)
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from cjm_context_graph_projection.devgraph import build_dev_graph_elements
from cjm_context_graph_projection.projection import relevant, show
from cjm_context_graph_projection.runtime import open_graph
from cjm_context_graph_projection.seeds import conceptual_key
from cjm_context_graph_projection.write import decide, link

REPOS = "/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills"
MEMORY = ("/home/innom-dt/.claude/projects/"
          "-mnt-SN850X-8TB-EXT4-Projects-GitHub-cj-mills-cjm-substrate/memory")
ARC_LIBS = ["cjm-dev-graph-schema", "cjm-markdown-decompose-core",
            "cjm-context-graph-projection", "cjm-python-decompose-core"]
# The Inc-4 alias-persistence decision + the write-path symbol that implements it.
DEC_STMT = ("A confirmed note-link alias is born on-graph as a multivalued 'aka' "
            "Assertion on the canonical note's slot; ingest resolves drifted "
            "[[wiki-link]] refs through it so the dangling edge heals without editing "
            "the .md file (graph+files coexist).")


def _check(label, ok):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    return ok


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repos-dir", default=REPOS)
    ap.add_argument("--memory-dir", default=MEMORY)
    args = ap.parse_args()

    code_repos = [str(Path(args.repos_dir) / n) for n in ARC_LIBS]
    nodes, edges = build_dev_graph_elements(args.memory_dir, args.repos_dir, seed=True,
                                            code_repos=code_repos)
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "code_on_graph.db")
        async with open_graph(db) as gx:
            res = await extend_graph(gx.queue, gx.graph_id, nodes, edges)
            print(f"ingested: {res.nodes_added} nodes / {res.edges_added} edges")

            # Birth the alias-persistence Decision on-graph (a born-on-graph write,
            # so the harness is self-contained — it does not depend on the real journal).
            await decide(gx, DEC_STMT, actor="human", session="2026-06-22-inc4")

            # 1. code nodes + edge kinds present
            sch = await gx_schema(gx)
            ok &= _check("CodeModule + CodeSymbol nodes present",
                         sch["counts"].get("CodeModule", 0) > 0
                         and sch["counts"].get("CodeSymbol", 0) > 0)
            ok &= _check("DEFINES/IMPORTS/CALLS/ABOUT edge kinds present",
                         {"DEFINES", "IMPORTS", "CALLS", "ABOUT"} <= set(sch["edge_types"]))

            # 2. co-residence: a relevance query returns BOTH a Decision and CodeSymbols
            rel = await relevant(gx, "alias write path resolve drifted link slug to canonical note",
                                 k=12)
            labels = {r["label"] for r in rel["results"]}
            ok &= _check("relevant surfaces a Decision AND CodeSymbols together",
                         "Decision" in labels and "CodeSymbol" in labels)

            # 3. the direct cross-link (Decision -> the CodeSymbol that implements it)
            dec_id = decision_node_id(" ".join(DEC_STMT.split()))
            mod_id = code_module_node_id(conceptual_key("cjm-context-graph-projection"),
                                         "cjm_context_graph_projection/write.py")
            sym_id = code_symbol_node_id(mod_id, "alias")
            lr = await link(gx, dec_id, sym_id, "IMPLEMENTED_BY", actor="human")
            ok &= _check("link Decision -> CodeSymbol written", lr.get("written"))
            lr2 = await link(gx, dec_id, sym_id, "IMPLEMENTED_BY", actor="human")
            ok &= _check("re-link is idempotent (0 edges added)", lr2.get("edges_added") == 0)
            sh = await show(gx, dec_id)
            nb = [n for n in sh.get("neighbours", [])
                  if n["relation"] == "IMPLEMENTED_BY" and n["node"]["id"] == sym_id]
            ok &= _check("show Decision lists the IMPLEMENTED_BY CodeSymbol neighbour", bool(nb))
            bad = await link(gx, dec_id, "does-not-exist", "IMPLEMENTED_BY")
            ok &= _check("missing-endpoint link is refused", bad.get("error") is not None)

    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


async def gx_schema(gx):
    from cjm_context_graph_projection.projection import get_schema
    return await get_schema(gx)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
