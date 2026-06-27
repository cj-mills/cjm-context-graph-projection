#!/usr/bin/env python
"""Section-grain divergence probe — the read-only down-payment toward the file<->graph
reconcile membrane (the deferred memory-true-B / code-contributor merge).

The make-or-break primitive that membrane needs is DETECTION: when a `.md` is edited
OUT-OF-BAND relative to the graph, can we identify — precisely, at section grain — which
sections changed/added/removed? This probes exactly that (`authoring.section_divergence`,
the section-grain analogue of `source_check`'s whole-module drift membrane), and then
CHARACTERIZES a known rough edge M1 flagged: does a plain re-ingest update a drifted
section's stored `raw`, or does the verify-collide-by-content-hash swallow the change
(forcing a clean `rm` rebuild)? Read-only / scratch db / temp file — NO policy decided.

    conda run -n cjm-transcript-correction-core --no-capture-output python \
        cjm-context-graph-projection/tests_manual/divergence_probe.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path

from cjm_context_graph_layer.ops import extend_graph, GraphIntegrityError
from cjm_dev_graph_schema.identity import note_node_id, section_node_id

from cjm_context_graph_projection.authoring import read_slot, section_divergence
from cjm_context_graph_projection.runtime import open_graph

from cjm_markdown_decompose_core.extract import note_from_file
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

ORIGINAL = ("---\n"
            "name: probe-note\n"
            "description: A divergence-probe note.\n"
            "metadata:\n"
            "  type: project\n"
            "---\n\n"
            "Preamble.\n\n"
            "## Alpha\n\nAlpha body.\n\n"
            "## Beta\n\nBeta body.\n\n"
            "## Gamma\n\nGamma body.\n")


def _check(label, ok):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    return ok


async def _ingest(gx, path, tmp):
    note = note_from_file(str(path), corpus_root=tmp, lossless=True)
    nodes, edges = corpus_graph_elements([note])
    await extend_graph(gx.queue, gx.graph_id, nodes, edges)


async def main() -> int:
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "probe_note.md"
        f.write_text(ORIGINAL)
        note_id = note_node_id("probe-note")
        beta_id = section_node_id(note_id, "beta")

        async with open_graph(str(Path(tmp) / "probe.db")) as gx:
            await _ingest(gx, f, tmp)

            # 0. fresh ingest == file: no drift.
            d0 = await section_divergence(gx, note_id)
            ok &= _check("no drift right after ingest (in_sync)", d0.get("in_sync") is True)

            # 1. edit ONE section body out-of-band -> detected at that anchor ONLY (precision).
            f.write_text(f.read_text().replace("Beta body.", "Beta body, hand-edited."))
            d1 = await section_divergence(gx, note_id)
            ok &= _check("an out-of-band section edit is detected at exactly its anchor",
                         d1.get("changed") == ["beta"])
            ok &= _check("sibling sections are NOT flagged (detection precision)",
                         not d1.get("added") and not d1.get("removed"))

            # 2. add a new section -> surfaced as `added`. (Appending also shifts the PRIOR
            #    section's raw boundary — gamma legitimately changes too; the probe catches it.)
            f.write_text(f.read_text() + "\n## Delta\n\nDelta body.\n")
            d2 = await section_divergence(gx, note_id)
            ok &= _check("a file-added section is surfaced as `added`", d2.get("added") == ["delta"])
            ok &= _check("a boundary-shifted prior section is also detected (gamma's raw grew)",
                         "beta" in d2.get("changed", []) and "gamma" in d2.get("changed", []))

            # 3. remove a section from the file -> surfaced as `removed`.
            f.write_text(f.read_text().replace("## Gamma\n\nGamma body.\n\n", ""))
            d3 = await section_divergence(gx, note_id)
            ok &= _check("a file-removed section is surfaced as `removed`",
                         d3.get("removed") == ["gamma"] and d3.get("added") == ["delta"])

            # 4. CHARACTERIZE what a plain re-ingest does to a drifted node (the M1 rough edge).
            reingest_error = None
            try:
                await _ingest(gx, f, tmp)
            except GraphIntegrityError as e:
                reingest_error = str(e)
            if reingest_error:
                print("    · OBSERVED: a plain re-ingest of the drifted file RAISES "
                      "GraphIntegrityError (content-hash guard) — it neither swallows nor heals; "
                      "reconcile needs a clean `rm` rebuild or an explicit update_node (the path "
                      "`author` already takes). This is exactly why M1 required a clean rebuild.")
                ok &= _check("plain re-ingest cannot silently heal drift (hard content-hash guard)",
                             True)
                d4 = await section_divergence(gx, note_id)
                ok &= _check("drift persists after the failed re-ingest (graph left unchanged)",
                             "beta" in d4.get("changed", []))
            else:
                beta_after = (await read_slot(gx, beta_id)).get("text", "")
                beta_updated = "hand-edited" in beta_after
                print(f"    · OBSERVED: plain re-ingest "
                      f"{'UPDATED' if beta_updated else 'did NOT update'} the drifted section's raw")
                d4 = await section_divergence(gx, note_id)
                ok &= _check("divergence stays consistent with the post-re-ingest graph state",
                             ("beta" in d4.get("changed", [])) == (not beta_updated))

    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
