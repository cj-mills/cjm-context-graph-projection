#!/usr/bin/env python
"""M3 CUTOVER thin-slice harness — demonstrate the 6-item cutover DoD (decision 2f5222ab).

M3 = an AUTHORITY FLIP: the graph/journal becomes the SOLE source for a note's content; its
`.md` becomes a generated backup. This drives the flip on a THIN note-slice in a SANDBOX copy
of the memory corpus (the real corpus + real journal are NEVER touched) and asserts the
locked Cutover DoD end-to-end:

  1. Journal-only rebuild is byte-clean   — genesis-import the slice; rebuild with its `.md`
     NOT read; emit -> 0 byte-diffs vs the pre-cutover bytes.
  2. Edits survive without the `.md`        — a born-on-graph section edit, journaled; two
     journal-only rebuilds reproduce it identically (deterministic) with the edit present.
  3. Membrane both directions               — file->graph (hand-edit a backup `.md` ->
     reconcile --absorb survives a rebuild) and graph->file (graph-authored change -> emit
     overwrites the stale `.md`).
  4. Lineage intact                          — a post-import edit traces back to the note's
     `import:m3-baseline` genesis op.
  5. Durability                              — delete the slice `.md`; restore the corpus from
     the journal alone (rebuild + emit) -> byte-equal to the last graph state.
  6. Consumption unaffected                  — the onboarding surface still projects from the
     journal-sourced graph.

Run (SCRATCH dbs + a COPY of the memory dir; nothing real is mutated):

    conda run -n cjm-transcript-correction-core --no-capture-output python \
        cjm-context-graph-projection/tests_manual/m3_cutover.py
"""
import argparse
import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

from cjm_context_graph_layer.ops import extend_graph
from cjm_dev_graph_schema.identity import note_node_id
from cjm_dev_graph_schema.vocab import DevNodeKinds

from cjm_context_graph_projection import factlayer as F
from cjm_context_graph_projection.authoring import emit_artifact
from cjm_context_graph_projection.devgraph import build_dev_graph_elements
from cjm_context_graph_projection.factlayer import note_alias_map
from cjm_context_graph_projection.journal import m3_baseline_import, journal_sourced_note_paths, replay_journal
from cjm_context_graph_primitives.journal import append_write
from cjm_context_graph_projection.onboarding import project_onboarding
from cjm_context_graph_projection.reconcile import reconcile_memory
from cjm_context_graph_projection.runtime import open_graph
from cjm_context_graph_projection.write import author_section

DEFAULT_MEMORY = ("/home/innom-dt/.claude/projects/"
                  "-mnt-SN850X-8TB-EXT4-Projects-GitHub-cj-mills-cjm-substrate/memory")
# A thin slice of REAL note slugs (copied into the sandbox). Two notes: one with cross-links.
SLICE = ["absolute-paths-in-memory", "dev-graph-write-journal-durability"]


def check(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    return bool(cond)


async def _rebuild(db, memory_dir, journal_path):
    """A journal-only-aware rebuild: build the projection (slice `.md` SKIPPED) + replay."""
    async with open_graph(db) as gx:
        aliases = await note_alias_map(gx)
        skip = journal_sourced_note_paths(journal_path)
        nodes, edges = build_dev_graph_elements(memory_dir, None, seed=False,
                                                note_aliases=aliases, skip_memory_paths=skip)
        await extend_graph(gx.queue, gx.graph_id, nodes, edges)
        await replay_journal(gx, journal_path)


async def _emit_slice(db, slugs):
    """Emit each slice note FROM THE GRAPH (the journal-sourced reconstruction)."""
    out = {}
    async with open_graph(db) as gx:
        for slug in slugs:
            res = await emit_artifact(gx, note_node_id(slug), write=False)
            out[slug] = res.get("text", "")
    return out


async def _section_anchors(db, slug):
    async with open_graph(db) as gx:
        nid = note_node_id(slug)
        secs = [(int(F.props(n).get("order") or 0), str(F.props(n).get("anchor")),
                 str(F.props(n).get("raw") or ""))
                for n in await F.load_label(gx, DevNodeKinds.SECTION)
                if F.prop(n, "note_id") == nid]
        return sorted(secs)


def _newdb():
    return str(Path(tempfile.mkdtemp(prefix="m3_")) / "dev.db")


async def run(real_memory) -> bool:
    ok = True
    sandbox = Path(tempfile.mkdtemp(prefix="m3_mem_"))
    mem = sandbox / "memory"
    shutil.copytree(real_memory, mem)
    journal = str(sandbox / "dev.writes.jsonl")
    slugs = SLICE

    # Pre-cutover baseline bytes (from the copied corpus), keyed by slug, via decompose.
    from cjm_markdown_decompose_core.extract import note_from_file
    slug_to_path = {}
    for p in sorted(mem.glob("*.md")):
        if p.name == "MEMORY.md":
            continue
        slug_to_path[note_from_file(str(p), corpus_root=str(mem), lossless=True).slug] = p
    missing = [s for s in slugs if s not in slug_to_path]
    if missing:
        print(f"  SKIP slice not in corpus: {missing}")
        slugs = [s for s in slugs if s in slug_to_path]
    baseline = {s: slug_to_path[s].read_text() for s in slugs}

    # --- Curation ops appended BEFORE genesis (the real ordering the flip introduced) ---
    # A `link` between two notes + an `assert` on a note, journaled while the .md was still the
    # source. Genesis ops get appended AFTER these, so a single-pass replay would hit them before
    # the note exists (dropping the edge / minting a stray term entity). Pass-1 genesis must fix it.
    a_id, b_id = note_node_id(slugs[0]), note_node_id(slugs[1])
    append_write(journal, "link",
                 {"source_id": a_id, "target_id": b_id, "relation": "ELABORATES",
                  "actor": "curation"})
    append_write(journal, "assert",
                 {"subject": a_id, "predicate": "status", "value": "curated",
                  "actor": "curation", "evidence": None, "supersede": None})

    # --- Genesis import the slice (appended AFTER the curation ops) ---
    imp = m3_baseline_import(str(mem), journal, slugs=slugs)
    ok &= check(f"genesis import emitted {imp['imported_count']} baseline op(s) {slugs}",
                imp["imported_count"] == len(slugs) and not imp["unknown"])

    # === DoD 1: journal-only rebuild is byte-clean ===
    db1 = _newdb()
    await _rebuild(db1, str(mem), journal)
    emitted = await _emit_slice(db1, slugs)
    clean = all(emitted[s] == baseline[s] for s in slugs)
    for s in slugs:
        if emitted[s] != baseline[s]:
            print(f"    byte-diff on {s}: emit {len(emitted[s])}b vs baseline {len(baseline[s])}b")
    ok &= check("DoD#1 journal-only rebuild byte-clean (slice .md NOT read; emit == pre-cutover)",
                clean)

    # === DoD 1b: curation links/assertions ordered BEFORE genesis carry over (two-pass replay) ===
    async with open_graph(db1) as gx:
        refs = await F.load_edge_pairs(gx, "ELABORATES")
        fslots = [n for n in await F.load_label(gx, DevNodeKinds.FACT_SLOT)
                  if F.prop(n, "predicate") == "status"]
        ents = await F.load_label(gx, DevNodeKinds.ENTITY)
    link_ok = (a_id, b_id) in refs                                   # the edge landed on the notes
    assert_ok = any(F.prop(s, "subject_id") == a_id for s in fslots)  # assertion on the note...
    no_stray = not any(F.prop(e, "kind") == "term" for e in ents)     # ...not a stray term entity
    ok &= check("DoD#1b curation link+assert (journaled before genesis) carry over to the on-graph note",
                link_ok and assert_ok and no_stray)

    # === DoD 2: edits survive without the .md (born-on-graph, journaled) ===
    edit_slug = slugs[0]
    anchors = await _section_anchors(db1, edit_slug)
    order, anchor, raw = anchors[-1]  # last section: appending stays within its span
    marker = "M3-DOD-EDIT-MARKER"
    new_raw = raw.rstrip("\n") + f"\n\n{marker} (born on-graph)\n"
    async with open_graph(db1) as gx:
        await author_section(gx, edit_slug, anchor, new_raw, actor="agent:session")
    append_write(journal, "section",
                 {"slug": edit_slug, "anchor": anchor, "raw": new_raw, "actor": "agent:session"})
    # Two fresh journal-only rebuilds — deterministic + the edit present.
    dba, dbb = _newdb(), _newdb()
    await _rebuild(dba, str(mem), journal)
    await _rebuild(dbb, str(mem), journal)
    ea, eb = await _emit_slice(dba, [edit_slug]), await _emit_slice(dbb, [edit_slug])
    ok &= check("DoD#2 edit survives journal-only rebuild (deterministic + marker present)",
                ea[edit_slug] == eb[edit_slug] and marker in ea[edit_slug])

    # === DoD 3: membrane both directions ===
    # (a) graph->file: emit overwrites the stale backup .md so it matches the graph.
    async with open_graph(dba) as gx:
        em = await emit_artifact(gx, note_node_id(edit_slug), write=True)
    file_after_emit = slug_to_path[edit_slug].read_text()
    g2f = file_after_emit == ea[edit_slug] and marker in file_after_emit
    # (b) file->graph: hand-edit the backup .md, reconcile --absorb, survive a rebuild.
    hand = file_after_emit.rstrip("\n") + "\n\nM3-DOD-HANDEDIT (file-wins)\n"
    slug_to_path[edit_slug].write_text(hand)
    async with open_graph(dba) as gx:
        rc = await reconcile_memory(gx, note_slug=edit_slug, absorb_all=True, journal_path=journal)
    db3 = _newdb()
    await _rebuild(db3, str(mem), journal)
    e3 = await _emit_slice(db3, [edit_slug])
    f2g = rc.get("absorbed_count", 0) >= 1 and "M3-DOD-HANDEDIT" in e3[edit_slug]
    ok &= check(f"DoD#3 membrane both ways (graph->file emit + file->graph absorb survives rebuild)",
                g2f and f2g)

    # === DoD 4: lineage intact (post-import edit traces to the genesis baseline) ===
    base_paths = set(journal_sourced_note_paths(journal))
    edit_has_genesis = str(slug_to_path[edit_slug].resolve()) in base_paths
    ok &= check("DoD#4 lineage: the edited note carries an import:m3-baseline genesis op",
                edit_has_genesis)

    # === DoD 5: durability — restore the corpus from the journal alone ===
    last_graph_state = e3  # what the graph holds now (post hand-edit absorb)
    for s in slugs:
        slug_to_path[s].unlink()  # simulate .md loss
    db5 = _newdb()
    await _rebuild(db5, str(mem), journal)  # slice reconstructed from the journal alone
    async with open_graph(db5) as gx:
        for s in slugs:
            await emit_artifact(gx, note_node_id(s), write=True)  # restore the .md from the graph
    restored_ok = (slug_to_path[edit_slug].exists()
                   and slug_to_path[edit_slug].read_text() == last_graph_state[edit_slug])
    ok &= check("DoD#5 durability: .md deleted -> restored byte-equal from the journal alone",
                restored_ok)

    # === DoD 6: consumption unaffected (onboarding surface still projects) ===
    async with open_graph(db5) as gx:
        ob = await project_onboarding(gx)
    ok &= check(f"DoD#6 onboarding surface projects from journal-sourced graph "
                f"(notes={ob.get('note_count')})",
                bool(ob.get("markdown")) and ob.get("note_count", 0) > 0)

    shutil.rmtree(sandbox, ignore_errors=True)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--memory-dir", default=DEFAULT_MEMORY)
    args = ap.parse_args()
    ok = asyncio.run(run(args.memory_dir))
    print("M3 CUTOVER DoD", "ALL CHECKS PASSED" if ok else "FAILURES")
    return 0 if ok else 1


sys.exit(main())
