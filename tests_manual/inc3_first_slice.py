#!/usr/bin/env python
"""Inc 3 DoD: the first-slice-complete cut, validated end-to-end through real storage.

The three locked DoD goals + the supporting beats:

  1. `contradictions` detects the torch/hf-utils "keep vs rename" case (the
     hand-seeded headline) — BOTH the torch-utils and hf-utils slots.
  2. The version oracle auto-updates the `cjm-substrate` version slot (a stale
     0.0.1 seed is bumped to the real installed version, older auto-superseded).
  3. Re-asserting a conflicting value FLAGS at write time (warn-record-flag): the
     conflicting assertion is written, the conflict is returned + recorded as a
     CONTRADICTS edge, nothing blocks.

Plus: rename-STABLE subject resolution (the old repo name resolves to the same
entity/slot), the clearing beat (rename SUPERSEDES keep -> the contradiction
clears), the worklist surfaces dangling references, and re-ingest is idempotent.

Run in a core env with the substrate runtime + the new libs installed -e:

    conda run -n cjm-transcript-correction-core --no-capture-output python \
        cjm-context-graph-projection/tests_manual/inc3_first_slice.py

A SCRATCH db is used; no real graph is touched.
"""
import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

from cjm_context_graph_layer.ops import extend_graph
from cjm_dev_graph_schema.identity import entity_node_id, factslot_node_id

from cjm_context_graph_projection import factlayer as F
from cjm_context_graph_projection.contradictions import contradictions
from cjm_context_graph_projection.devgraph import build_dev_graph_elements
from cjm_context_graph_projection.oracle import run_version_oracle
from cjm_context_graph_projection.runtime import open_graph
from cjm_context_graph_projection.write import assert_value, resolve_subject

DEFAULT_MEMORY = ("/home/innom-dt/.claude/projects/"
                  "-mnt-SN850X-8TB-EXT4-Projects-GitHub-cj-mills-cjm-substrate/memory")
DEFAULT_REPOS = "/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills"


def check(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    return bool(cond)


async def _active_value(gx, slot_id):
    """The single active assertion value for a slot (None if 0/ambiguous)."""
    assertions = [a for a in await F.load_assertions(gx) if F.prop(a, "slot_id") == slot_id]
    supers = await F.load_supersedes(gx)
    active = F.active_assertions(assertions, supers)
    return F.prop(active[0], "value") if len(active) == 1 else None


async def run(memory_dir: str, repos_dir: str) -> bool:
    scratch = Path(tempfile.mkdtemp(prefix="inc3_")) / "dev.db"
    nodes, edges = build_dev_graph_elements(memory_dir, repos_dir, seed=True)
    ok = True
    async with open_graph(str(scratch)) as gx:
        res = await extend_graph(gx.queue, gx.graph_id, nodes, edges)
        print(f"ingested: {res.nodes_added} nodes / {res.edges_added} edges")

        # --- DoD 1: contradictions detects the torch/hf rename-disposition case ---
        con = await contradictions(gx)
        subjects = {c["subject"] for c in con["contradictions"]}
        preds = {c["predicate"] for c in con["contradictions"]}
        ok &= check("DoD1: contradictions detects torch-utils + hf-utils rename-disposition",
                    {"cjm-substrate-torch-utils", "cjm-substrate-hf-utils"} <= subjects
                    and "rename-disposition" in preds)
        # each detected slot carries BOTH the keep and the rename claim
        torch = next((c for c in con["contradictions"]
                      if c["subject"] == "cjm-substrate-torch-utils"), None)
        vals = {a["value"] for a in (torch["assertions"] if torch else [])}
        ok &= check("DoD1: the torch-utils slot holds both 'keep' and a 'rename:' claim",
                    "keep" in vals and any(v.startswith("rename:") for v in vals))

        # --- DoD 2: the version oracle auto-updates the cjm-substrate version slot ---
        substrate_id = entity_node_id("repo", "cjm-substrate")
        version_slot = factslot_node_id(substrate_id, "version")
        before = await _active_value(gx, version_slot)
        orc = await run_version_oracle(gx, repos_dir, only=["cjm-substrate"])
        after = await _active_value(gx, version_slot)
        ok &= check("DoD2: stale seed was 0.0.1", before == "0.0.1")
        ok &= check("DoD2: oracle bumped cjm-substrate (recorded a bump, superseded the stale value)",
                    orc["counts"]["bumped"] == 1)
        ok &= check(f"DoD2: effective version updated to the real installed version (now {after!r})",
                    after is not None and after != "0.0.1")

        # --- DoD 3: re-asserting a conflicting value flags at write time ---
        a1 = await assert_value(gx, "demo-conflict-subject", "rename-disposition", "keep",
                                actor="human")
        ok &= check("DoD3: first claim on a fresh slot has no conflict", not a1["conflict"])
        a2 = await assert_value(gx, "demo-conflict-subject", "rename-disposition",
                                "rename:cjm-demo", actor="agent:session")
        ok &= check("DoD3: the conflicting re-assert FLAGS (conflict returned, not blocked)",
                    bool(a2["conflict"]) and a2["assertion_id"])
        contradicts = await F.load_contradicts(gx)
        ok &= check("DoD3: the conflict was RECORDED as a CONTRADICTS edge",
                    any(a2["assertion_id"] in pair for pair in contradicts))

        # --- rename-STABLE subject resolution: the OLD repo name lands on the same entity ---
        r_old = await resolve_subject(gx, "cjm-torch-plugin-utils")
        ok &= check("alias: the prior repo name resolves to the torch-utils entity",
                    r_old["subject_id"] == entity_node_id("repo", "torch-utils")
                    and r_old["created_node"] is None)

        # --- clearing beat: rename SUPERSEDES keep -> the torch-utils contradiction clears ---
        await assert_value(gx, "cjm-torch-plugin-utils", "rename-disposition",
                           "rename:cjm-substrate-torch-utils", actor="human", supersede=["keep"])
        con2 = await contradictions(gx)
        subjects2 = {c["subject"] for c in con2["contradictions"]}
        ok &= check("clearing beat: torch-utils contradiction cleared after rename SUPERSEDES keep",
                    "cjm-substrate-torch-utils" not in subjects2)
        ok &= check("clearing beat: hf-utils contradiction still stands (untouched)",
                    "cjm-substrate-hf-utils" in subjects2)

        # --- worklist surfaces dangling references (corpus-findings triage) ---
        from cjm_context_graph_projection.worklist import worklist
        wl = await worklist(gx, memory_dir)
        ok &= check("worklist surfaces dangling references with suggestions",
                    wl["counts"]["dangling_references"] > 0
                    and any(d["suggestion"] for d in wl["dangling_references"]))

        # --- idempotency: re-ingesting the seed adds nothing ---
        res2 = await extend_graph(gx.queue, gx.graph_id, nodes, edges)
        ok &= check("idempotent re-ingest (0 nodes added, all verified)",
                    res2.nodes_added == 0 and res2.nodes_verified > 0)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--memory-dir", default=DEFAULT_MEMORY)
    ap.add_argument("--repos-dir", default=DEFAULT_REPOS)
    args = ap.parse_args()
    ok = asyncio.run(run(args.memory_dir, args.repos_dir))
    print("INC3 FIRST-SLICE", "ALL CHECKS PASSED" if ok else "FAILURES")
    return 0 if ok else 1


sys.exit(main())
