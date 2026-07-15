#!/usr/bin/env python
"""M2b phase-1 (SHADOW) dogfood: the `section` journal verb + `reconcile-memory`.

Validates the born-on-graph memory-authoring SHADOW end-to-end on scratch dbs + temp files
+ a temp journal (the real corpus / journal are NEVER touched):

  A. SECTION JOURNAL VERB survives a db rebuild: author a section (file+graph), journal its
     STATE, REVERT the file, then on a FRESH db decompose the reverted file + replay the
     journal -> the section's raw is the JOURNALED state, not the file's (the journal is
     load-bearing; replay applies via update_node, sidestepping the content-hash guard that
     a re-extend trips). Replaying twice is idempotent.
  B. RECONCILE dry-run -> absorb: a hand-edit out-of-band is REPORTED (dry-run, journal
     untouched), then `--absorb-all` folds it in as a self-describing `section` op (actor
     reconcile:absorb + the replaced raw), snapshots the .md, and converges graph==file.
  C. UNDO an absorption via a compensating author from the op's recorded `replaces`.
  D. Replay TOLERATES a section op whose anchor is gone from the file (no raise).

    conda run -n cjm-transcript-correction-core --no-capture-output python \
        cjm-context-graph-projection/tests_manual/m2b_shadow.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path

from cjm_context_graph_layer.ops import extend_graph
from cjm_dev_graph_schema.identity import note_node_id, section_node_id

from cjm_context_graph_projection.authoring import author, read_slot, section_divergence
from cjm_context_graph_projection.journal import replay_journal
from cjm_context_graph_primitives.journal import append_write, read_journal
from cjm_context_graph_projection.reconcile import reconcile_memory
from cjm_context_graph_projection.runtime import open_graph
from cjm_context_graph_projection.write import author_section

from cjm_markdown_decompose_core.extract import note_from_file
from cjm_markdown_decompose_core.ingest import corpus_graph_elements


def _md(slug, beta="Beta body."):
    return (f"---\nname: {slug}\ndescription: shadow.\nmetadata:\n  type: project\n---\n\n"
            f"Preamble.\n\n## Alpha\n\nAlpha body.\n\n## Beta\n\n{beta}\n")


def _check(label, ok):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    return ok


async def _ingest(gx, path, tmp):
    note = note_from_file(str(path), corpus_root=tmp, lossless=True)
    nodes, edges = corpus_graph_elements([note])
    await extend_graph(gx.queue, gx.graph_id, nodes, edges)


async def part_a(tmp) -> bool:
    ok = True
    f = Path(tmp) / "a.md"
    f.write_text(_md("shadow-a"))
    J = str(Path(tmp) / "a.writes.jsonl")
    note_id = note_node_id("shadow-a")
    beta_id = section_node_id(note_id, "beta")

    async with open_graph(str(Path(tmp) / "a1.db")) as gx:
        await _ingest(gx, f, tmp)
        r = await author(gx, beta_id, replace="## Beta\n\nBeta AUTHORED on-graph.\n", write=True)
        ok &= _check("author wrote the memory section (file+graph)", r.get("written"))
        append_write(J, "section", {"slug": r["note_slug"], "anchor": r["anchor"],
                                    "raw": r["new_text"], "actor": "agent:session"})
    ok &= _check("the section op was journaled",
                 any(o["verb"] == "section" for o in read_journal(J)))

    # REVERT the file (so file-decomposition alone would give the ORIGINAL beta) — isolates
    # the journal's contribution.
    f.write_text(_md("shadow-a"))
    async with open_graph(str(Path(tmp) / "a2.db")) as gx2:  # FRESH db (a rebuild)
        await _ingest(gx2, f, tmp)
        counts = await replay_journal(gx2, J)
        ok &= _check("replay applied the section op", counts.get("section", 0) >= 1)
        beta = (await read_slot(gx2, beta_id)).get("text", "")
        ok &= _check("rebuilt section carries the JOURNALED state, not the reverted file",
                     "Beta AUTHORED on-graph." in beta)
        await replay_journal(gx2, J)  # idempotent re-apply
        beta2 = (await read_slot(gx2, beta_id)).get("text", "")
        ok &= _check("replay is idempotent (re-apply is a no-op)", beta2 == beta)
    return ok


async def part_bcd(tmp) -> bool:
    ok = True
    f = Path(tmp) / "b.md"
    f.write_text(_md("shadow-b"))
    J = str(Path(tmp) / "b.writes.jsonl")
    backup_dir = str(Path(tmp) / "backups")
    note_id = note_node_id("shadow-b")
    beta_id = section_node_id(note_id, "beta")

    async with open_graph(str(Path(tmp) / "b.db")) as gx:
        await _ingest(gx, f, tmp)
        r = await author(gx, beta_id, replace="## Beta\n\nBeta AUTHORED.\n", write=True)
        append_write(J, "section", {"slug": r["note_slug"], "anchor": r["anchor"],
                                    "raw": r["new_text"], "actor": "agent:session"})

        # hand-edit the .md out-of-band (NOT through the graph).
        f.write_text(f.read_text().replace("Beta AUTHORED.", "Beta HAND-EDITED out of band."))

        # B. dry-run: drift surfaced, journal untouched, nothing absorbed.
        jlen = len(read_journal(J))
        dry = await reconcile_memory(gx)
        ok &= _check("dry-run reports the out-of-band drift",
                     any(d["slug"] == "shadow-b" and any(c["anchor"] == "beta" for c in d["changed"])
                         for d in dry.get("drift", [])))
        ok &= _check("dry-run absorbs nothing + leaves the journal untouched",
                     dry.get("absorbed_count") == 0 and len(read_journal(J)) == jlen)

        # B. absorb: file-wins, self-describing op, file snapshot, graph converges to file.
        absd = await reconcile_memory(gx, absorb_all=True, journal_path=J, backup_dir=backup_dir)
        ok &= _check("absorb folds the hand-edit into the journal",
                     any(a["anchor"] == "beta" for a in absd.get("absorbed", [])))
        ops = read_journal(J)
        ab_op = next((o for o in ops if o["verb"] == "section"
                      and o["args"].get("actor") == "reconcile:absorb"), None)
        ok &= _check("the absorb op is self-describing (actor + recorded `replaces`)",
                     ab_op is not None and "replaces" in ab_op["args"]
                     and "Beta AUTHORED." in ab_op["args"]["replaces"]
                     and "HAND-EDITED" in ab_op["args"]["raw"])
        ok &= _check("the .md was snapshotted before absorbing",
                     bool(list(Path(backup_dir).glob("*.bak"))))
        beta_now = (await read_slot(gx, beta_id)).get("text", "")
        ok &= _check("graph converged to the file (absorbed)", "HAND-EDITED" in beta_now)
        clean = await reconcile_memory(gx, note_slug="shadow-b")
        ok &= _check("re-running reconcile is now clean for that note", clean.get("clean"))

        # C. UNDO via compensating author from the op's recorded prior value.
        prior = ab_op["args"]["replaces"]
        await author_section(gx, "shadow-b", "beta", prior, actor="agent:session")
        beta_undone = (await read_slot(gx, beta_id)).get("text", "")
        ok &= _check("undo (compensating author from `replaces`) restores the prior state",
                     "Beta AUTHORED." in beta_undone and "HAND-EDITED" not in beta_undone)

        # D. replay tolerates a section op whose anchor no longer exists.
        append_write(J, "section", {"slug": "shadow-b", "anchor": "ghost-anchor",
                                    "raw": "## Ghost\n\nx\n", "actor": "agent:session"})
        try:
            await replay_journal(gx, J)
            ok &= _check("replay tolerates a section op for a missing anchor (no raise)", True)
        except Exception as e:  # noqa
            ok &= _check(f"replay tolerates a missing anchor (raised {type(e).__name__})", False)
    return ok


async def main() -> int:
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        print("A — section journal verb survives a db rebuild (journal is load-bearing):")
        ok &= await part_a(tmp)
        print("B/C/D — reconcile dry-run/absorb, undo, missing-anchor tolerance:")
        ok &= await part_bcd(tmp)
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
