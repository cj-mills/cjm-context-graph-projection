#!/usr/bin/env python
"""M2 GRADIENT dogfood: structural memory authoring (`new-note` + `add-section`).

The CREATION primitives the [[memory-files-retirement-plan]] M2 promise needs ("new memory
is born on-graph"). On SCRATCH dbs + temp files (real corpus NEVER touched):

  A. NEW-NOTE: create a note from text -> on-graph (Note + sections) + the .md written +
     graph==file (reconcile clean) + byte-exact round-trip.
  B. ADD-SECTION (append): append a section -> new section added + prior boundary updated +
     graph==file + the .md round-trips + the new section is LAST.
  C. ADD-SECTION (--after, mid-note): insert after an anchor -> new section added + the
     subsequent sections' ORDER updated + correct document position + graph==file.
  D. GUARDS: add to a missing note / missing --after anchor / new-note for an existing slug.

    conda run -n cjm-transcript-correction-core --no-capture-output python \
        cjm-context-graph-projection/tests_manual/structure.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path

from cjm_dev_graph_schema.identity import note_node_id, section_node_id

from cjm_context_graph_projection.authoring import read_slot, section_divergence
from cjm_context_graph_projection.runtime import open_graph
from cjm_context_graph_projection.structure import add_section, new_note

from cjm_markdown_decompose_core.extract import note_from_file


def _md(slug):
    return (f"---\nname: {slug}\ndescription: structural.\nmetadata:\n  type: project\n---\n\n"
            f"Preamble.\n\n## Alpha\n\nAlpha body.\n\n## Beta\n\nBeta body.\n")


def _check(label, ok):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    return ok


async def _anchors_in_order(gx, slug):
    note_id = note_node_id(slug)
    from cjm_context_graph_projection.authoring import _note_section_wires
    from cjm_context_graph_projection import factlayer as F
    secs = [(int(F.props(w).get("order") or 0), str(F.props(w).get("anchor")))
            for w in await _note_section_wires(gx, note_id)]
    return [a for _, a in sorted(secs)]


async def _section_raws(gx, slug):  # ordered [(anchor, raw)] — the full structural STATE
    note_id = note_node_id(slug)
    from cjm_context_graph_projection.authoring import _note_section_wires
    from cjm_context_graph_projection import factlayer as F
    secs = sorted(((int(F.props(w).get("order") or 0), str(F.props(w).get("anchor")),
                   str(F.props(w).get("raw") or "")) for w in await _note_section_wires(gx, note_id)),
                  key=lambda t: t[0])
    return [(a, r) for _, a, r in secs]


async def part_a(gx, tmp) -> bool:
    ok = True
    path = str(Path(tmp) / "new.md")
    content = _md("structural-new")
    res = await new_note(gx, path, content, write=True)
    ok &= _check("new-note created the note on-graph", res.get("written") and res.get("slug") == "structural-new")
    ok &= _check("the .md was written", Path(path).exists())
    div = await section_divergence(gx, note_node_id("structural-new"))
    ok &= _check("graph == file right after new-note (reconcile clean)", div.get("in_sync") is True)
    note = note_from_file(path, corpus_root=tmp, lossless=True)
    from cjm_markdown_decompose_core.project import render_note_text
    ok &= _check("new note round-trips byte-exact",
                 render_note_text(note.frontmatter_raw, note.sections) == content)
    # dry-run add-section: reports the plan but mutates NOTHING (graph + file unchanged).
    before = Path(path).read_text()
    dry = await add_section(gx, "structural-new", "## Dryrun\n\nx\n", write=False)
    ok &= _check("dry-run add-section reports the plan (added)", dry.get("added") == ["dryrun"])
    ok &= _check("dry-run did NOT write the file", Path(path).read_text() == before)
    ok &= _check("dry-run did NOT mutate the graph (section absent)",
                 (await read_slot(gx, section_node_id(note_node_id("structural-new"), "dryrun"))).get("error") is not None)
    return ok


async def part_b(gx, tmp) -> bool:
    ok = True
    path = str(Path(tmp) / "append.md")
    Path(path).write_text(_md("structural-append"))
    # ingest the base note fresh onto the scratch graph.
    from cjm_markdown_decompose_core.extract import note_from_file as _nff
    from cjm_markdown_decompose_core.ingest import corpus_graph_elements
    from cjm_context_graph_layer.ops import extend_graph
    n = _nff(path, corpus_root=tmp, lossless=True)
    nodes, edges = corpus_graph_elements([n])
    await extend_graph(gx.queue, gx.graph_id, nodes, edges)

    res = await add_section(gx, "structural-append", "## Gamma\n\nGamma body.\n", write=True)
    ok &= _check("add-section appended a new section", res.get("added") == ["gamma"])
    ok &= _check("the prior section's boundary was updated (not a stale re-extend)",
                 "beta" in res.get("updated", []))
    div = await section_divergence(gx, note_node_id("structural-append"))
    ok &= _check("graph == file after append (reconcile clean)", div.get("in_sync") is True)
    ok &= _check("the appended section is on-graph + LAST in order",
                 (await _anchors_in_order(gx, "structural-append"))[-1] == "gamma")
    note = note_from_file(path, corpus_root=tmp, lossless=True)
    from cjm_markdown_decompose_core.project import render_note_text
    ok &= _check("appended note round-trips byte-exact",
                 render_note_text(note.frontmatter_raw, note.sections) == Path(path).read_text())
    ok &= _check("the new section's body is readable on-graph",
                 "Gamma body." in (await read_slot(gx, section_node_id(note_node_id("structural-append"), "gamma"))).get("text", ""))
    return ok


async def part_c(gx, tmp) -> bool:
    ok = True
    path = str(Path(tmp) / "insert.md")
    Path(path).write_text(_md("structural-insert"))
    from cjm_markdown_decompose_core.extract import note_from_file as _nff
    from cjm_markdown_decompose_core.ingest import corpus_graph_elements
    from cjm_context_graph_layer.ops import extend_graph
    n = _nff(path, corpus_root=tmp, lossless=True)
    nodes, edges = corpus_graph_elements([n])
    await extend_graph(gx.queue, gx.graph_id, nodes, edges)

    res = await add_section(gx, "structural-insert", "## Inserted\n\nInserted body.\n", after="alpha", write=True)
    ok &= _check("add-section --after inserted a new section", res.get("added") == ["inserted"])
    order = await _anchors_in_order(gx, "structural-insert")
    ok &= _check("the new section sits AFTER `alpha` and BEFORE `beta`",
                 order.index("inserted") == order.index("alpha") + 1 and order.index("inserted") < order.index("beta"))
    ok &= _check("the subsequent section's order shifted (update, not stale)",
                 "beta" in res.get("updated", []) or order.index("beta") > order.index("inserted"))
    div = await section_divergence(gx, note_node_id("structural-insert"))
    ok &= _check("graph == file after mid-insert (reconcile clean)", div.get("in_sync") is True)
    note = note_from_file(path, corpus_root=tmp, lossless=True)
    from cjm_markdown_decompose_core.project import render_note_text
    ok &= _check("mid-inserted note round-trips byte-exact",
                 render_note_text(note.frontmatter_raw, note.sections) == Path(path).read_text())
    return ok


async def part_d(gx, tmp) -> bool:
    ok = True
    miss = await add_section(gx, "no-such-note", "## X\n\nx\n", write=False)
    ok &= _check("add-section to a missing note is refused", bool(miss.get("error")))
    bad_after = await add_section(gx, "structural-append", "## Y\n\ny\n", after="ghost", write=False)
    ok &= _check("add-section --after a missing anchor is refused", bool(bad_after.get("error")))
    dup = await new_note(gx, str(Path(tmp) / "dup.md"), _md("structural-append"), write=False)
    # structural-append exists from part B -> slug collision refused (dry-run still checks)
    ok &= _check("new-note for an existing slug is refused", bool(dup.get("error")))
    return ok


async def _ingest_note(gx, path, tmp):  # ingest a plain .md onto a scratch graph
    from cjm_markdown_decompose_core.extract import note_from_file as _nff
    from cjm_markdown_decompose_core.ingest import corpus_graph_elements
    from cjm_context_graph_layer.ops import extend_graph
    n = _nff(path, corpus_root=tmp, lossless=True)
    nodes, edges = corpus_graph_elements([n])
    await extend_graph(gx.queue, gx.graph_id, nodes, edges)


async def part_e(tmp) -> bool:
    """The DURABILITY fix (approach A): a journaled `add-section` op is re-spliced on rebuild, so
    a journal-sourced note's later structure survives `rm db && replay`. The money check: a graph
    built the LIVE way (ingest + live add-section) is byte-identical to one built purely by
    REPLAYING the journal (new-note genesis + add-section ops) — replay == live structure."""
    from cjm_context_graph_projection.journal import append_write, replay_journal
    from cjm_context_graph_projection.authoring import read_slot
    ok = True
    slug = "structural-replay"
    content = _md(slug)  # preamble + Alpha + Beta
    gamma = "## Gamma\n\nGamma body.\n"
    delta = "## Delta\n\nDelta body.\n"

    # LIVE build: ingest the base .md, then two live add-sections (append Gamma, insert Delta
    # after alpha — same order the journal records).
    live_path = str(Path(tmp) / "live.md")
    Path(live_path).write_text(content)
    async with open_graph(str(Path(tmp) / "live.db")) as gx_live:
        await _ingest_note(gx_live, live_path, tmp)
        await add_section(gx_live, slug, gamma, write=True)
        await add_section(gx_live, slug, delta, after="alpha", write=True)
        live_raws = await _section_raws(gx_live, slug)

    # REPLAY build: a journal with the note's genesis + the two structural adds, replayed onto a
    # FRESH graph — no .md is read (graph-only reconstruction).
    journal = str(Path(tmp) / "e.writes.jsonl")
    jpath = str(Path(Path(tmp) / "replay.md").resolve())
    append_write(journal, "new-note", {"path": jpath, "content": content, "actor": "agent:session"})
    append_write(journal, "add-section", {"slug": slug, "raw": gamma, "after": None, "actor": "agent:session"})
    append_write(journal, "add-section", {"slug": slug, "raw": delta, "after": "alpha", "actor": "agent:session"})
    async with open_graph(str(Path(tmp) / "replay.db")) as gx2:
        await replay_journal(gx2, journal)
        replay_raws = await _section_raws(gx2, slug)
        anchors = [a for a, _ in replay_raws]
        ok &= _check("replay reconstructed genesis + both structural adds",
                     {"_preamble", "alpha", "beta", "gamma", "delta"} <= set(anchors))
        ok &= _check("replayed --after add landed in order (delta after alpha, before beta)",
                     anchors.index("delta") == anchors.index("alpha") + 1 and anchors.index("delta") < anchors.index("beta"))
        ok &= _check("replayed append add is LAST", anchors[-1] == "gamma")
        ok &= _check("REPLAY == LIVE structure, byte-for-byte (anchors + raws)", replay_raws == live_raws)
        ok &= _check("replayed section body is readable on-graph",
                     "Gamma body." in (await read_slot(gx2, section_node_id(note_node_id(slug), "gamma"))).get("text", ""))
        # IDEMPOTENT: replaying the journal a SECOND time duplicates nothing (anchor-exists no-op).
        await replay_journal(gx2, journal)
        ok &= _check("second full replay is idempotent (no duplicate sections)",
                     await _section_raws(gx2, slug) == replay_raws)
        # The anchor-exists guard as a direct no-op (not an error).
        dup = await add_section(gx2, slug, "## Gamma\n\ndifferent.\n", write=False)
        ok &= _check("add-section of an existing anchor is a no-op, not a dup",
                     dup.get("existing") is True and dup.get("added") == [] and not dup.get("error"))
    return ok


async def main() -> int:
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        async with open_graph(str(Path(tmp) / "structure.db")) as gx:
            print("A — new-note:")
            ok &= await part_a(gx, tmp)
            print("B — add-section (append):")
            ok &= await part_b(gx, tmp)
            print("C — add-section (--after, mid-note):")
            ok &= await part_c(gx, tmp)
            print("D — guards:")
            ok &= await part_d(gx, tmp)
        print("E — add-section journaling + replay (durability):")
        ok &= await part_e(tmp)
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
