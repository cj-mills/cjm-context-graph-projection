"""The B write surface: AUTHOR a verbatim-text slot on-graph, emit the canonical artifact.

The make-or-break authoring increment of [[graph-as-source-of-truth-inversion]]. A
`CodeSymbol` body, a `CodeText` region, a notebook `Cell` source, and a memory
`Section`'s verbatim `raw` span are the SAME kind of thing — a VERBATIM-TEXT SLOT on a
node — so `author` targets that slot abstraction, not a node kind: it edits code
symbols, code-text regions, notebook cells, and memory sections uniformly.

The slot composes a per-kind canonical ARTIFACT: code regions -> a `.py` module, a cell
-> an `.ipynb`, a memory section -> its enclosing `.md` note (reusing M1's lossless
`frontmatter_raw + concat(section.raw)` reconstruction). Memory authoring (M2a) is the
prose case of the [[memory-files-retirement-plan]].

Two modes (the lesson from the pre-arc NotebookEdit pain, where every change meant
rewriting the whole cell):
- `replace` — set the slot's full new text (the Write / NotebookEdit analogue), and
- `edit` — a unique-match OLD->NEW splice within the slot (the Edit analogue; the
  low-token targeted path).

Persistence = Fork-1(a) (file stays the source; the graph is the editing surface): the
edit is applied to the container's regions READ FROM THE GRAPH, the canonical artifact is
re-emitted (graph OWNS formatting), and the FILE on disk is the durable record — the next
`ingest` re-derives the graph from it (code/notebooks are rebuilt sources, NOT journaled;
a targeted OLD->NEW splice isn't replay-idempotent anyway, so the journal correctly waits
for true-B, which will journal the resulting body STATE, not the diff). So author against a
freshly-ingested graph; emit reproduces the file byte-exact except the authored change.

Fork-1(a) holds for memory authoring too in this cut: the `.md` stays the source and is
re-emitted on each author, so graph-edits and hand-edits coexist safely (no clobber).
True-B for memory — journaling the section STATE so the `.md` becomes a pure projection
that survives a db rebuild — is the explicit M3 on-ramp, deferred until the editing
surface is trusted (mirrors the code path's deferral).
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cjm_context_graph_layer.ops import graph_task
from cjm_context_graph_primitives.provenance import SourceRef
from cjm_dev_graph_schema.vocab import DevNodeKinds
from cjm_markdown_decompose_core.extract import note_from_file
from cjm_markdown_decompose_core.project import note_text_from_graph_nodes
from cjm_notebook_decompose_core.project import render_notebook
from cjm_python_decompose_core.emit import emit_module_from_nodes

from . import factlayer as F
from .runtime import GraphHandle

# Per node label: the verbatim-text slot property + the artifact kind it composes.
_SLOTS = {
    DevNodeKinds.CODE_SYMBOL: ("body", "module"),    # a top-level symbol's body -> a .py module
    DevNodeKinds.CODE_TEXT: ("text", "module"),      # a non-def region's text -> a .py module
    DevNodeKinds.CELL: ("source", "notebook"),       # a notebook cell's source -> an .ipynb
    DevNodeKinds.SECTION: ("raw", "note"),           # a memory section's verbatim span -> a .md note (M2a)
}


def _label_of(node: Any) -> Optional[str]:
    """A node's label (typed GraphNode or wire dict)."""
    if isinstance(node, dict):
        return node.get("label")
    return getattr(node, "label", None)


def _slot_for(node: Any) -> Optional[Tuple[str, str, str]]:
    """Resolve (slot-property, artifact-kind, label) for a node, or None if not authorable.

    Prefers the node label; falls back to slot-property inference (so a node without a
    surfaced label still routes). Nested symbols (no `body`) are NOT authorable in v1."""
    label = _label_of(node)
    if label in _SLOTS:
        if label == DevNodeKinds.CODE_SYMBOL and not F.props(node).get("body"):
            return None  # a nested symbol (no verbatim body) — not a top-level authoring unit
        slot, artifact = _SLOTS[label]
        return slot, artifact, label
    p = F.props(node)
    if "source" in p and "cell_type" in p:
        return "source", "notebook", DevNodeKinds.CELL
    if p.get("body"):
        return "body", "module", DevNodeKinds.CODE_SYMBOL
    if "text" in p and "region_key" in p:
        return "text", "module", DevNodeKinds.CODE_TEXT
    if "note_id" in p and "anchor" in p:
        return "raw", "note", DevNodeKinds.SECTION
    return None


def _apply(
    current: str,                    # The slot's current verbatim text
    replace: Optional[str],          # Full replacement text (replace mode)
    edit: Optional[Tuple[str, str]],  # (old, new) unique-match splice (edit mode)
) -> Tuple[Optional[str], Optional[str]]:  # (new_text, error)
    """Compute the new slot text from a replace or a unique-match edit (the Write/Edit split)."""
    if replace is not None and edit is not None:
        return None, "give either --replace or --edit, not both"
    if replace is not None:
        return replace, None
    if edit is not None:
        old, new = edit
        n = current.count(old)
        if n == 0:
            return None, "edit OLD not found in the slot"
        if n > 1:
            return None, f"edit OLD is not unique ({n} matches) — add context to disambiguate"
        return current.replace(old, new), None
    return None, "nothing to do: pass --replace or --edit"


async def _module_node(gx: GraphHandle, module_id: str) -> Optional[Any]:
    """Fetch the enclosing CodeModule node (carries the artifact `path`)."""
    return await graph_task(gx.queue, gx.graph_id, "get_node", node_id=module_id)


def _as_wire(node: Any, label: str) -> Dict[str, Any]:
    """A plain wire dict (id/label/properties) the cores' emit/render consume."""
    return {"id": F.nid(node), "label": label, "properties": dict(F.props(node))}


async def _module_region_wires(
    gx: GraphHandle, module_id: str,
) -> List[Dict[str, Any]]:  # CodeSymbol + CodeText wire dicts for one .py module
    """All of a .py module's region nodes (top-level symbols + code-text), as wire dicts."""
    out: List[Dict[str, Any]] = []
    for label in (DevNodeKinds.CODE_SYMBOL, DevNodeKinds.CODE_TEXT):
        for n in await F.load_label(gx, label):
            if F.prop(n, "module_id") == module_id:
                out.append(_as_wire(n, label))
    return out


async def _notebook_cell_wires(
    gx: GraphHandle, module_id: str,
) -> List[Dict[str, Any]]:  # Cell wire dicts for one notebook
    """All of a notebook's Cell nodes, as wire dicts."""
    return [_as_wire(n, DevNodeKinds.CELL)
            for n in await F.load_label(gx, DevNodeKinds.CELL)
            if F.prop(n, "module_id") == module_id]


async def read_slot(
    gx: GraphHandle,
    node_id: str,  # The node whose verbatim slot to read
) -> Dict[str, Any]:  # {slot, label, text} or {error}
    """Read a node's current verbatim-slot text (the `--editor` pop / preview input)."""
    node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=node_id)
    if node is None:
        return {"error": f"no node `{node_id}`", "node_id": node_id}
    resolved = _slot_for(node)
    if resolved is None:
        return {"error": "node has no authorable verbatim slot", "node_id": node_id,
                "label": _label_of(node)}
    slot, _artifact, label = resolved
    return {"node_id": node_id, "label": label, "slot": slot, "text": str(F.prop(node, slot, ""))}


async def _note_section_wires(
    gx: GraphHandle, note_id: str,
) -> List[Dict[str, Any]]:  # Section wire dicts for one Note (whole-note reconstruction)
    """All of a Note's Section nodes, as wire dicts (carrying `raw`/`order`)."""
    return [_as_wire(n, DevNodeKinds.SECTION)
            for n in await F.load_label(gx, DevNodeKinds.SECTION)
            if F.prop(n, "note_id") == note_id]


async def read_node(
    gx: GraphHandle,
    node_id: str,  # The node whose verbatim content to deliver
) -> Dict[str, Any]:  # {label, kind, text, ...} or {error}
    """Deliver a node's verbatim CONTENT — the read DUAL of `author`/`emit`.

    The file-crutch killer: graph-pull can RANK and POINT at a node (`relevant`/`show`)
    but `show` renders only docstring + neighbours, so reading content still meant
    opening the `.md`/`.py`. This delivers the verbatim text for any content-bearing
    node, heterogeneously — the same slot abstraction `author` writes:

      - Note     -> the WHOLE file reconstructed lossless (frontmatter_raw + ordered
                    section `raw` spans) — needs a `lossless` ingest (M1).
      - Section  -> that one section's verbatim `raw` span (the fine, per-section unit).
      - CodeModule -> the module/notebook reassembled read-only (reuses `emit`).
      - CodeSymbol body / CodeText / Cell -> that authorable verbatim slot.

    A nested symbol (a method) carries no own body — its text lives in the enclosing
    class block, so we point there rather than returning empty."""
    from .projection import ambiguity_error, resolve_node_ref
    res = await resolve_node_ref(gx, node_id)
    if "candidates" in res:
        return {"error": ambiguity_error(node_id, res["candidates"]),
                "node_id": node_id, "candidates": res["candidates"]}
    node = res.get("node")
    if node is None:
        return {"error": f"no node `{node_id}`", "node_id": node_id}
    node_id = F.nid(node) or node_id
    label = _label_of(node)
    p = F.props(node)
    if label == DevNodeKinds.NOTE:
        secs = await _note_section_wires(gx, node_id)
        if not secs and not p.get("frontmatter_raw"):
            return {"error": "note has no body on-graph (ingested non-lossless?)",
                    "node_id": node_id, "label": label}
        return {"node_id": node_id, "label": label, "kind": "note", "sections": len(secs),
                "text": note_text_from_graph_nodes(_as_wire(node, label), secs)}
    if label == DevNodeKinds.SECTION:
        return {"node_id": node_id, "label": label, "kind": "section",
                "anchor": p.get("anchor"), "text": str(p.get("raw") or p.get("text") or "")}
    if label == DevNodeKinds.CODE_MODULE:
        em = await emit_artifact(gx, node_id, write=False)
        if em.get("error"):
            return em
        return {"node_id": node_id, "label": label, "kind": em.get("artifact", "module"),
                "artifact_path": em.get("artifact_path"), "text": em.get("text", "")}
    if label == DevNodeKinds.DECISION:
        # A born-on-graph Decision carries its content in `statement` (no `.md` to open) —
        # the read DUAL that closes the `show`-only gap dogfooded this session: `read`-ing a
        # Decision returned "no readable content", forcing a fall-back to SQL/`show`.
        return {"node_id": node_id, "label": label, "kind": "statement",
                "text": str(p.get("statement") or "")}
    resolved = _slot_for(node)
    if resolved is not None:
        slot, _artifact, lab = resolved
        return {"node_id": node_id, "label": lab, "kind": "slot", "slot": slot,
                "text": str(F.prop(node, slot, ""))}
    if label == DevNodeKinds.CODE_SYMBOL:
        return {"node_id": node_id, "label": label, "kind": "nested", "text": "",
                "module_id": p.get("module_id"),
                "hint": "nested symbol (no own body) — read its enclosing class or module"}
    return {"error": "node has no readable verbatim content", "node_id": node_id, "label": label}


async def graph_section_raws(
    gx: GraphHandle,
    note_id: str,  # The enclosing Note id
) -> Dict[str, str]:  # {anchor: verbatim raw span} as STORED on the graph
    """Each of a note's sections' on-graph `raw` span, keyed by anchor (the divergence/
    reconcile read leg)."""
    return {str(F.props(w).get("anchor")): str(F.props(w).get("raw") or "")
            for w in await _note_section_wires(gx, note_id)}


def file_section_raws(
    path: str,  # The note's `.md` file path
) -> Dict[str, str]:  # {anchor: verbatim raw span} as currently ON DISK (re-decomposed lossless)
    """Each of a note's sections' `raw` span as the FILE currently decomposes (the other
    side of the divergence/reconcile diff)."""
    note = note_from_file(path, corpus_root=str(Path(path).parent), lossless=True)
    return {s.anchor: s.raw for s in note.sections}


async def section_divergence(
    gx: GraphHandle,
    note_id: str,                       # The Note whose graph state to compare against its file
    *,
    file_path: Optional[str] = None,    # Override the file path (else the Note's stored `path`)
) -> Dict[str, Any]:  # {in_sync, changed, added, removed, ...} or {error}
    """Read-only: detect, at SECTION grain, where a note's `.md` has drifted from the graph.

    The memory analogue of `source_check`'s whole-module drift membrane, at the finer
    section grain M1 unlocked: re-decompose the file lossless and compare each section's
    on-disk `raw` span with the graph's STORED `raw`, reporting `changed` / `added` /
    `removed` anchors. The atomic DETECTION primitive both the deferred memory-true-B
    reconcile and the code-contributor merge need (which sections changed out-of-band).

    A PURE diagnostic — no graph mutation, no file write, and deliberately NO who-wins
    policy (that merge IS the deferred true-B reconcile; this only surfaces the drift,
    mirroring `source_check`'s 'surfaced, never silently overridden')."""
    node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=note_id)
    if node is None:
        return {"error": f"no note `{note_id}`", "note_id": note_id}
    if _label_of(node) != DevNodeKinds.NOTE:
        return {"error": "not a Note", "note_id": note_id, "label": _label_of(node)}
    path = file_path or F.prop(node, "path")
    if not path or not Path(path).exists():
        return {"error": f"no file at `{path}`", "note_id": note_id, "path": path}

    graph_secs = await graph_section_raws(gx, note_id)
    file_secs = file_section_raws(path)

    changed = sorted(a for a in graph_secs.keys() & file_secs.keys()
                     if graph_secs[a] != file_secs[a])
    added = sorted(file_secs.keys() - graph_secs.keys())     # in the file, not yet on-graph
    removed = sorted(graph_secs.keys() - file_secs.keys())   # on-graph, gone from the file
    return {"note_id": note_id, "path": path,
            "in_sync": not (changed or added or removed),
            "changed": changed, "added": added, "removed": removed,
            "graph_sections": len(graph_secs), "file_sections": len(file_secs)}


async def emit_artifact(
    gx: GraphHandle,
    module_id: str,        # The CodeModule id (a .py module / notebook) or a Note id to emit
    *,
    write: bool = False,   # Write to the container's path on disk (else just return the text)
) -> Dict[str, Any]:  # {artifact, artifact_path, text, written} or {error}
    """Emit a container's canonical artifact FROM THE GRAPH (graph -> .py / .ipynb / .md).

    The round-trip read leg, standalone: reassemble the file from its stored
    regions/cells/sections with no mutation — proves the graph is a sufficient source, and
    (true-B preview) lets a generated file be refreshed from the graph. Detects a Note
    (-> `.md` via M1's lossless reconstruction) by label, else notebook vs `.py` by whether
    the module has `Cell` children."""
    module = await _module_node(gx, module_id)
    if module is None:
        return {"error": f"no module `{module_id}`", "module_id": module_id}
    artifact_path = F.prop(module, "path")
    if _label_of(module) == DevNodeKinds.NOTE:
        secs = await _note_section_wires(gx, module_id)
        artifact = "note"
        text = note_text_from_graph_nodes(_as_wire(module, DevNodeKinds.NOTE), secs)
    else:
        cells = await _notebook_cell_wires(gx, module_id)
        if cells:
            artifact, text = "notebook", render_notebook(cells)
        else:
            text = emit_module_from_nodes(await _module_region_wires(gx, module_id),
                                          module_node=module, derive_imports=True)
            artifact = "module"
    res = {"module_id": module_id, "artifact": artifact, "artifact_path": artifact_path,
           "emitted_bytes": len(text.encode("utf-8")), "text": text, "written": False}
    if write and artifact_path:
        Path(artifact_path).write_text(text)
        res["written"] = True
    return res


async def author(
    gx: GraphHandle,
    node_id: str,                          # The node whose verbatim slot to author (CodeSymbol / CodeText / Cell / Section)
    *,
    replace: Optional[str] = None,         # Full replacement text (replace mode)
    edit: Optional[Tuple[str, str]] = None,  # (old, new) unique-match splice (edit mode)
    actor: str = "agent:session",          # Who authored it (recorded in the result; provenance lands via decide/link)
    write: bool = True,                    # Emit the canonical artifact to disk (Fork-1(a)); False = dry run
) -> Dict[str, Any]:  # The write result (incl. error, the emitted artifact path + text)
    """Author a node's verbatim-text slot, then emit its canonical artifact to disk.

    Reads the enclosing container's regions FROM THE GRAPH (a CodeModule's regions/cells,
    or a Note's sections), applies the edit in memory, reassembles the canonical artifact
    (`.py` / `.ipynb` for a cell / `.md` for a memory section), and writes the file — the
    graph is the editing surface, the file the durable source. Returns the artifact path
    + emitted text (and, on `write=False`, emits without touching disk: a dry-run/preview)."""
    node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=node_id)
    if node is None:
        return {"error": f"no node `{node_id}`", "node_id": node_id, "written": False}
    resolved = _slot_for(node)
    if resolved is None:
        return {"error": "node has no authorable verbatim slot (not a top-level CodeSymbol / "
                         "CodeText / Cell / Section)", "node_id": node_id, "label": _label_of(node),
                "written": False}
    slot, artifact, label = resolved
    current = str(F.prop(node, slot, ""))
    new_text, err = _apply(current, replace, edit)
    if err is not None:
        return {"error": err, "node_id": node_id, "label": label, "slot": slot, "written": False}

    # Resolve the enclosing container: a CodeModule for code/notebooks, a Note for memory.
    container_key = "note_id" if artifact == "note" else "module_id"
    container_id = F.prop(node, container_key)
    container = await _module_node(gx, container_id)  # get_node — works for a Note too
    if container is None:
        return {"error": f"enclosing container `{container_id}` not found", "node_id": node_id,
                "written": False}
    artifact_path = F.prop(container, "path")

    # Read the container's regions/cells/sections from the graph, inject the mutated slot.
    if artifact == "notebook":
        wires = await _notebook_cell_wires(gx, container_id)
    elif artifact == "note":
        wires = await _note_section_wires(gx, container_id)
        # The whole note is reconstructed from its sections' `raw`; a non-lossless ingest
        # (sections without `raw`) would silently truncate it — refuse rather than corrupt.
        if any(not str(w["properties"].get("raw") or "")
               for w in wires if w["id"] != node_id):
            return {"error": "note not lossless-ingested (a section has no `raw` span); "
                             "re-ingest with lossless=True before authoring", "node_id": node_id,
                    "label": label, "written": False}
    else:
        wires = await _module_region_wires(gx, container_id)
    for w in wires:
        if w["id"] == node_id:
            w["properties"][slot] = new_text
    if artifact == "notebook":
        emitted = render_notebook(wires)
    elif artifact == "note":
        emitted = note_text_from_graph_nodes(_as_wire(container, DevNodeKinds.NOTE), wires)
    else:
        emitted = emit_module_from_nodes(wires)

    result = {
        "node_id": node_id, "label": label, "slot": slot, "actor": actor,
        "artifact": artifact, "artifact_path": artifact_path,
        "unchanged": new_text == current, "emitted_bytes": len(emitted.encode("utf-8")),
        "written": False, "emitted_text": emitted, "new_text": new_text,
    }
    if artifact == "note":
        # The durable section identity (slug, anchor) the journal records for M2b's shadow.
        result["note_slug"] = F.prop(container, "slug")
        result["anchor"] = F.prop(node, "anchor")
    if write and artifact_path:
        Path(artifact_path).write_text(emitted)
        # Persist the slot change INTO the graph node too, so the graph stays consistent
        # with the file and sequential authors compose (emit reads the graph). The file is
        # still the durable source under Fork-1(a); the next `ingest` re-derives either way.
        merge: Dict[str, Any] = {slot: new_text}
        if label == DevNodeKinds.CODE_SYMBOL:
            merge["body_hash"] = SourceRef.compute_hash(new_text.encode("utf-8"))
        elif label == DevNodeKinds.SECTION:
            # Mirror the extractor: a section's content_hash is over its `raw` span.
            merge["content_hash"] = SourceRef.compute_hash(new_text.encode("utf-8"))
        await graph_task(gx.queue, gx.graph_id, "update_node", node_id=node_id, properties=merge)
        result["written"] = True
    return result
