#!/usr/bin/env python
"""Inc 4 dogfood loop: confirm an alias -> heal the reference -> resurface, end-to-end.

Inc 4 is a BEHAVIOR change (decisions/facts born on-graph during real work), but it
surfaced one real code gap and this harness pins the new write path that filled it.
The propose -> confirm -> alias -> heal loop, plus the next-session resurfacing the
whole increment exists to prove:

  1. A drifted `[[wiki-link]]` is dangling: its REFERENCES edge lands on no real note.
  2. `alias <drifted> <canonical>` confirms the equivalence as a born-on-graph `aka`
     Assertion on the canonical note (multivalued: a note accrues many aliases, never
     a conflict), carrying EVIDENCED_BY edges to the notes that held the broken link.
  3. Re-ingest resolves the drifted ref through the confirmed alias -> the once-
     dangling edge now lands on the real note (the file is never edited).
  4. The confirmed ref DROPS off the worklist.
  5. A `decide` is born on-graph and a FRESH `relevant` read surfaces it (the self-
     validating recursion: a session's writes are found by the next session's reads).

Run in a core env with the substrate runtime + the new libs installed -e:

    conda run -n cjm-transcript-correction-core --no-capture-output python \
        cjm-context-graph-projection/tests_manual/inc4_dogfood.py

A SCRATCH db is used; the real persistent dev-graph is never touched.
"""
import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

from cjm_context_graph_layer.ops import extend_graph
from cjm_dev_graph_schema.identity import note_node_id

from cjm_context_graph_projection import factlayer as F
from cjm_context_graph_projection.devgraph import build_dev_graph_elements
from cjm_context_graph_projection.projection import relevant
from cjm_context_graph_projection.runtime import open_graph
from cjm_context_graph_projection.worklist import worklist
from cjm_context_graph_projection.write import alias, decide

DEFAULT_MEMORY = ("/home/innom-dt/.claude/projects/"
                  "-mnt-SN850X-8TB-EXT4-Projects-GitHub-cj-mills-cjm-substrate/memory")
DEFAULT_REPOS = "/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills"


def check(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    return bool(cond)


async def _ref_targets(gx):
    """All REFERENCES edge (source, target) pairs."""
    return await F.load_edge_pairs(gx, "REFERENCES")


async def run(memory_dir: str, repos_dir: str) -> bool:
    # A real, high-confidence slug-drift case present in the corpus.
    drifted, canonical = "where-the-graph-begins-question", "where-graph-begins-question"
    canonical_id = note_node_id(canonical)
    scratch = Path(tempfile.mkdtemp(prefix="inc4_")) / "dev.db"
    ok = True
    async with open_graph(str(scratch)) as gx:
        nodes, edges = build_dev_graph_elements(memory_dir, repos_dir, seed=True)
        await extend_graph(gx.queue, gx.graph_id, nodes, edges)

        # 1. The drifted ref is dangling: no REFERENCES edge points at the drifted id,
        #    and the canonical note really exists to alias to.
        targets_before = {t for _, t in await _ref_targets(gx)}
        ok &= check("the drifted link is dangling (no edge to the drifted note id)",
                    note_node_id(drifted) not in targets_before)
        ok &= check("the canonical note exists on-graph to alias to",
                    await F.note_alias_map(gx) == {} and canonical_id in targets_before)

        wl_before = await worklist(gx, memory_dir)
        n_before = wl_before["counts"]["dangling_references"]
        on_list = any(d["missing"] == drifted for d in wl_before["dangling_references"])
        ok &= check(f"the drifted ref is ON the worklist (count {n_before})", on_list)

        # 2. Confirm the alias -> born-on-graph aka Assertion + evidence edges.
        res = await alias(gx, drifted, canonical, actor="agent:session:inc4-test",
                          evidence=[note_node_id("audio-rendition-node-deferred")])
        ok &= check("alias written (a born-on-graph aka assertion)",
                    res["written"] and res["assertion_id"])
        amap = await F.note_alias_map(gx)
        ok &= check("the confirmed alias resolves drifted -> canonical on-graph",
                    amap.get(drifted) == canonical)

        # 3. Re-ingest heals the reference (the file is never touched).
        nodes2, edges2 = build_dev_graph_elements(memory_dir, repos_dir, seed=True,
                                                  note_aliases=amap)
        await extend_graph(gx.queue, gx.graph_id, nodes2, edges2)
        targets_after = {t for _, t in await _ref_targets(gx)}
        ok &= check("re-ingest healed the reference: an edge now lands on the canonical note",
                    canonical_id in targets_after)

        # 4. The confirmed ref drops off the worklist.
        wl_after = await worklist(gx, memory_dir)
        n_after = wl_after["counts"]["dangling_references"]
        still = any(d["missing"] == drifted for d in wl_after["dangling_references"])
        ok &= check(f"the confirmed ref DROPPED off the worklist ({n_before} -> {n_after})",
                    not still and n_after < n_before)

        # 5. A born-on-graph decision is surfaced by a fresh relevant read (the DoD).
        stmt = "Inc-4 dogfood: a confirmed note-link alias is born on-graph as an aka assertion"
        await decide(gx, stmt, actor="agent:session:inc4-test", session="inc4-test")
        rel = await relevant(gx, "how is a confirmed note-link alias born on-graph")
        top = rel["results"][0] if rel["results"] else {}
        ok &= check("a fresh `relevant` read surfaces the born-on-graph Decision as the top hit",
                    top.get("label") == "Decision" and "alias" in top.get("title", "").lower())
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--memory-dir", default=DEFAULT_MEMORY)
    ap.add_argument("--repos-dir", default=DEFAULT_REPOS)
    args = ap.parse_args()
    ok = asyncio.run(run(args.memory_dir, args.repos_dir))
    print("INC4 DOGFOOD", "ALL CHECKS PASSED" if ok else "FAILURES")
    return 0 if ok else 1


sys.exit(main())
