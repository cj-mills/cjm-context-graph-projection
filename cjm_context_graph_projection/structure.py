"""M2a GRADIENT — structural memory authoring: create a note / add a section, born on-graph.

M2a/M2b author the verbatim slot of an EXISTING section; this adds the CREATION primitives
the [[memory-files-retirement-plan]] M2 promise needs ("new memory is born on-graph"):
`new-note` and `add-section`.

Mechanism = compute the new note TEXT, re-decompose it lossless (M1), and apply the DIFF
against the graph: a new section via `extend_graph`, a boundary-shifted existing section
(adding a section moves the prior section's `raw` span, as the divergence probe found) via
`update_node` — sidestepping the content-hash guard a re-extend would trip — and a changed
frontmatter via `update_node`. Re-decomposing is the source of truth for the new structure,
so anchors / orders / hashes match exactly what a later rebuild would derive.

Under the M2b SHADOW the `.md` stays the ingest source, so new structure flows through the
FILE (a rebuild re-derives it); applying now just keeps THIS session's graph current. So,
unlike `author` (which journals the deliberate edit shadow), structural ops are NOT journaled
yet — journaling structure (new section/note ops) is deferred to the true-B/M3 cutover, when
the `.md` is no longer the source. Section REMOVAL is deferred too (needs `delete_node`);
removed sections are REPORTED, not applied.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_dev_graph_schema.identity import note_node_id

from cjm_markdown_decompose_core.extract import note_from_text
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from . import factlayer as F
from .authoring import _note_section_wires
from .runtime import GraphHandle


async def _apply_note_text(
    gx: GraphHandle,
    note_node: Any,        # The existing Note graph node
    slug: str,             # The note's stable slug
    new_text: str,         # The full desired note text (frontmatter + body)
    path: str,             # The note's file path
    *,
    write: bool = True,    # Write the new text to the `.md` (Fork-1(a) / shadow source)
) -> Dict[str, Any]:  # {added, updated, removed, frontmatter_changed, written, ...}
    """Re-decompose `new_text` and apply the section/frontmatter DIFF to the graph.

    New sections -> `extend_graph`; existing sections whose `raw`/`order` shifted ->
    `update_node` (the content-hash-guard-safe path); changed frontmatter -> `update_node`.
    Removed sections are reported but NOT deleted (deferred). Writes the `.md` last."""
    decomposed = note_from_text(path, new_text, corpus_root=str(Path(path).parent), lossless=True)
    desired = {s.anchor: s for s in decomposed.sections}
    graph = {str(F.props(w).get("anchor")): (str(F.props(w).get("raw") or ""),
                                             int(F.props(w).get("order") or 0))
             for w in await _note_section_wires(gx, F.nid(note_node))}

    add_nodes: List[Dict[str, Any]] = []
    add_edges: List[Dict[str, Any]] = []
    updates: List[Any] = []   # (section, ) to update_node when applying
    added: List[str] = []
    updated: List[str] = []
    for anchor, s in desired.items():
        if anchor not in graph:
            add_nodes.append(s.to_graph_node())
            add_edges.extend(s.structural_edges())
            added.append(anchor)
        elif s.raw != graph[anchor][0] or s.order != graph[anchor][1]:
            updates.append(s)
            updated.append(anchor)
    removed = sorted(a for a in graph if a not in desired)
    fm_changed = decomposed.frontmatter_raw != str(F.prop(note_node, "frontmatter_raw") or "")

    # Apply to the graph + write the file ONLY on write=True; write=False is a true dry-run
    # (compute + return the diff, mutate nothing) — same posture as `author --no-write`.
    if write:
        for s in updates:
            await graph_task(gx.queue, gx.graph_id, "update_node", node_id=s.id,
                             properties={"raw": s.raw, "content_hash": s.content_hash,
                                         "order": s.order})
        if add_nodes or add_edges:
            await extend_graph(gx.queue, gx.graph_id, add_nodes, add_edges)
        if fm_changed:
            await graph_task(gx.queue, gx.graph_id, "update_node", node_id=F.nid(note_node),
                             properties={"frontmatter_raw": decomposed.frontmatter_raw})
        if path:
            Path(path).write_text(new_text)
    return {"slug": slug, "path": path, "added": sorted(added), "updated": sorted(updated),
            "removed": removed, "frontmatter_changed": fm_changed,
            "written": bool(write and path), "emitted_text": new_text}


async def add_section(
    gx: GraphHandle,
    slug: str,                       # The note to add to (by slug)
    section_raw: str,                # The new section's heading-inclusive verbatim text ("## H\n\n...")
    *,
    after: Optional[str] = None,     # Insert after this anchor (None = append at end of body)
    write: bool = True,              # Write the new `.md` (else dry-run)
) -> Dict[str, Any]:  # The apply result (incl. error)
    """Add a section to an existing note (append, or insert after an anchor), born on-graph.

    Reconstructs the note text FROM THE GRAPH (the editing surface), splices the new section
    in, and applies the diff. Adding a section moves the PRIOR section's `raw` boundary, so the
    apply updates that section too — handled generically by the re-decompose diff."""
    note_node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=note_node_id(slug))
    if note_node is None:
        return {"error": f"no note `{slug}`", "slug": slug, "written": False}
    path = F.prop(note_node, "path")
    fm = str(F.prop(note_node, "frontmatter_raw") or "")
    secs = sorted(((int(F.props(w).get("order") or 0), str(F.props(w).get("anchor")),
                   str(F.props(w).get("raw") or "")) for w in await _note_section_wires(gx, F.nid(note_node))),
                  key=lambda t: t[0])
    anchors = [a for _, a, _ in secs]
    raws = [r for _, _, r in secs]
    if not section_raw.endswith("\n"):
        section_raw += "\n"

    def _lead(prev: str) -> str:  # a blank line before the new heading (markdown hygiene)
        return "" if prev.endswith("\n\n") else ("\n" if prev.endswith("\n") else "\n\n")

    if after is None:
        prev = raws[-1] if raws else fm
        new_text = fm + "".join(raws) + _lead(prev) + section_raw
    elif after in anchors:
        i = anchors.index(after)
        following = "".join(raws[i + 1:])
        # blank line before the NEXT heading too, when inserting mid-note.
        block = _lead(raws[i]) + section_raw + ("\n" if following and not section_raw.endswith("\n\n") else "")
        new_text = fm + "".join(raws[:i + 1]) + block + following
    else:
        return {"error": f"no section `{after}` in note `{slug}` to insert after",
                "slug": slug, "written": False}
    return await _apply_note_text(gx, note_node, slug, new_text, path, write=write)


async def new_note(
    gx: GraphHandle,
    path: str,            # Where to write the new `.md` (the file location)
    content: str,         # The full note text (frontmatter + body)
    *,
    write: bool = True,   # Write the file + apply to the graph (else a parse-only dry-run)
) -> Dict[str, Any]:  # {slug, sections, written} or {error}
    """Create a brand-new note, born on-graph (write the `.md` + ingest it this session).

    All nodes are NEW, so a plain `extend_graph` applies (no content-hash-guard issue). Under
    the shadow the file is the source, so a later rebuild re-derives the same note; this just
    makes it visible now. Dry-run (`write=False`) parses + reports without writing/ingesting."""
    if not content.endswith("\n"):
        content += "\n"
    note = note_from_text(path, content, corpus_root=str(Path(path).parent), lossless=True)
    slug = note.slug
    existing = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=note_node_id(slug))
    if existing is not None:
        return {"error": f"note `{slug}` already exists (use add-section / author)",
                "slug": slug, "written": False}
    res = {"slug": slug, "path": path, "sections": len(note.sections), "written": False}
    if write:
        Path(path).write_text(content)
        nodes, edges = corpus_graph_elements([note])
        r = await extend_graph(gx.queue, gx.graph_id, nodes, edges)
        res.update(written=True, nodes_added=r.nodes_added, edges_added=r.edges_added)
    return res
