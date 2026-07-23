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

import ast
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cjm_context_graph_layer.grammar import make_edge
from cjm_context_graph_layer.ops import graph_task
from cjm_context_graph_primitives.provenance import SourceRef
from cjm_dev_graph_schema.nodes import CodeSymbolNode
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations
from cjm_markdown_decompose_core.extract import note_from_file
from cjm_markdown_decompose_core.project import note_text_from_graph_nodes
from cjm_notebook_decompose_core.project import render_notebook
from cjm_python_decompose_core.emit import emit_module_from_nodes
from cjm_python_decompose_core.parse import parse_module

from . import factlayer as F
from .projection import ambiguity_error, resolve_node_ref
from .runtime import GraphHandle
from .seeds import repo_dir_name
from .source_state import is_test_module_path, journaled_emit


async def _resolve_node(
    gx: GraphHandle,
    ref: str,  # A full node id, or a unique id prefix (>= 6 hex chars)
) -> Tuple[Optional[Any], Optional[str]]:  # (node, error) — at most one is set
    """Resolve an id-taking write verb's node argument like every read verb does.

    Unique prefix ok; ambiguity is a LOUD error naming the candidates, never a
    guess (the 66fffba6 write-verb asymmetry fix). A miss returns (None, None) —
    the caller keeps its own 'no node' wording."""
    res = await resolve_node_ref(gx, ref)
    if "candidates" in res:
        return None, ambiguity_error(ref, res["candidates"])
    return res.get("node"), None


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


async def _owning_slot_hint(gx: GraphHandle, node: Any) -> Optional[str]:
    """For a nested CodeSymbol, name the authorable slot that OWNS its text.

    A method/nested def has no verbatim body of its own — the authoring unit is
    the notebook Cell (or top-level .py symbol) that defines it. Resolving that
    here (via `_owning_slot_wire`, the shared locator) turns the 'no authorable
    slot' error into a pointer instead of sending the caller on a per-cell scan
    of the enclosing module (soak 2026-07-05: 3x ~60-read manual scans)."""
    owner = await _owning_slot_wire(gx, node)
    if owner is None:
        return None
    if owner["label"] == DevNodeKinds.CELL:
        return f"its text lives in Cell `{owner['id']}` — author that"
    return (f"its text lives in symbol `{owner['id']}` "
            f"(`{owner['properties'].get('name')}`) — author that")


async def read_slot(
    gx: GraphHandle,
    node_id: str,  # The node whose verbatim slot to read
) -> Dict[str, Any]:  # {slot, label, text} or {error}
    """Read a node's current verbatim-slot text (the `--editor` pop / preview input)."""
    node, amb = await _resolve_node(gx, node_id)
    if amb:
        return {"error": amb, "node_id": node_id}
    if node is None:
        return {"error": f"no node `{node_id}`", "node_id": node_id}
    node_id = F.nid(node)
    resolved = _slot_for(node)
    if resolved is None:
        err = "node has no authorable verbatim slot"
        hint = await _owning_slot_hint(gx, node)
        return {"error": err + (f" — {hint}" if hint else ""), "node_id": node_id,
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
    class block / notebook Cell, so we deliver a server-side SLICE of the owning slot
    (via `_slice_block`) rather than a pointer the reader must chase."""
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
        owner = await _owning_slot_wire(gx, node)
        if owner is not None:
            src_slot = "source" if owner["label"] == DevNodeKinds.CELL else "body"
            basename = str(p.get("qualname") or p.get("name") or "").split(".")[-1]
            block = _slice_block(str(owner["properties"].get(src_slot) or ""), basename)
            if block is not None:
                return {"node_id": node_id, "label": label, "kind": "slice",
                        "owner_id": owner["id"], "slot": src_slot,
                        "module_id": p.get("module_id"), "text": block}
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
    source_journal_path: Optional[str] = None,  # The source journal (a code write routes journal-first)
    repos_dir: Optional[str] = None,  # The repos root (notebook journal keys derive under it)
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
            text = emit_module_from_nodes(
                await _module_region_wires(gx, module_id), module_node=module,
                derive_imports=not is_test_module_path(F.prop(module, "module_path", "")))
            artifact = "module"
    res = {"module_id": module_id, "artifact": artifact, "artifact_path": artifact_path,
           "emitted_bytes": len(text.encode("utf-8")), "text": text, "written": False}
    if write and artifact_path and artifact != "note" and source_journal_path is not None:
        emission = _source_emission(F.prop(module, "repo_key"),
                                    str(F.prop(module, "module_path") or ""),
                                    artifact_path, text, repos_dir,
                                    import_name=F.prop(module, "import_name"))
        if emission is None:
            return {**res, "error": "cannot derive the source-journal key for "
                    f"{artifact_path!r} under repos_dir={repos_dir!r} — refusing to "
                    "write unjournaled"}
        rec = journaled_emit(source_journal_path, emissions=[emission],
                             op={"op": "emit", "module_id": module_id})
        if rec.get("error"):
            return {**res, "error": rec["error"]}
        res["journal"] = rec
        res["written"] = True
    elif write and artifact_path:
        # Notes ride the writes-journal domain; bare path is TRANSITIONAL (seam rollout).
        Path(artifact_path).write_text(text)
        res["written"] = True
    return res


async def _route_nested_symbol(
    gx: GraphHandle,
    node: Any,                             # The nested CodeSymbol the caller tried to author
    *,
    replace: Optional[str],                # Replace mode is NOT routable (re-indent trap) — refused loudly
    edit: Optional[Tuple[str, str]],       # The (old, new) splice to route to the owning slot
    actor: str,
    write: bool,
    source_journal_path: Optional[str] = None,  # Forwarded to the owning author call (journal-first)
    repos_dir: Optional[str] = None,
) -> Optional[Dict[str, Any]]:  # The routed author result, or None when this node doesn't route
    """Route an author call on a NESTED CodeSymbol through its OWNING verbatim slot.

    A method/nested def has no body of its own — the authoring unit is the top-level
    `.py` symbol (or notebook Cell) whose text defines it. The 3e13d95a hint NAMED that
    owner; this executes the edit through it: resolve the owner via `_owning_slot_wire`
    (the shared locator behind the hint and the read-slice, so the surfaces never drift),
    then re-enter `author` on the owner with the caller's splice (uniqueness is
    validated against the owner's whole slot, so an OLD matching a sibling method fails
    loudly with the add-context error). Replace mode is refused rather than routed: a
    whole-method replacement would need re-indent logic inside the owner body — pass a
    unique --edit splice, or author the owning slot directly."""
    if _label_of(node) != DevNodeKinds.CODE_SYMBOL or F.props(node).get("body"):
        return None
    qualname = str(F.prop(node, "qualname") or F.prop(node, "name") or "")
    owner = await _owning_slot_wire(gx, node)
    if owner is None:
        return None
    if edit is None:
        return {"error": f"nested symbol `{qualname}` routes --edit splices only (a --replace "
                         "would need re-indent logic inside the owner body) — pass a unique "
                         f"OLD/NEW, or author the owning slot `{owner['id']}` directly",
                "node_id": F.nid(node), "written": False}
    res = await author(gx, owner["id"], edit=edit, actor=actor, write=write,
                       source_journal_path=source_journal_path, repos_dir=repos_dir)
    if not res.get("error"):
        res["routed_from"] = F.nid(node)
        res["routed_note"] = (f"nested `{qualname}` routed through owning slot "
                              f"`{owner['id']}`")
    return res


async def author(
    gx: GraphHandle,
    node_id: str,                          # The node whose verbatim slot to author (CodeSymbol / CodeText / Cell / Section)
    *,
    replace: Optional[str] = None,         # Full replacement text (replace mode)
    edit: Optional[Tuple[str, str]] = None,  # (old, new) unique-match splice (edit mode)
    actor: str = "agent:session",          # Who authored it (recorded in the result; provenance lands via decide/link)
    write: bool = True,                    # Emit the canonical artifact to disk (Fork-1(a)); False = dry run
    source_journal_path: Optional[str] = None,  # The source journal (code artifacts route journal-first)
    repos_dir: Optional[str] = None,       # The repos root (a notebook's journal key derives under it)
) -> Dict[str, Any]:  # The write result (incl. error, the emitted artifact path + text)
    """Author a node's verbatim-text slot, then emit its canonical artifact to disk.

    Reads the enclosing container's regions FROM THE GRAPH (a CodeModule's regions/cells,
    or a Note's sections), applies the edit in memory, reassembles the canonical artifact
    (`.py` / `.ipynb` for a cell / `.md` for a memory section), and writes the file — the
    graph is the editing surface, the file the durable source. Returns the artifact path
    + emitted text (and, on `write=False`, emits without touching disk: a dry-run/preview)."""
    node, amb = await _resolve_node(gx, node_id)
    if amb:
        return {"error": amb, "node_id": node_id, "written": False}
    if node is None:
        return {"error": f"no node `{node_id}`", "node_id": node_id, "written": False}
    node_id = F.nid(node)
    resolved = _slot_for(node)
    if resolved is None:
        # A nested CodeSymbol ROUTES through its owning slot (the 3e13d95a hint, executed).
        routed = await _route_nested_symbol(gx, node, replace=replace, edit=edit,
                                            actor=actor, write=write,
                                            source_journal_path=source_journal_path,
                                            repos_dir=repos_dir)
        if routed is not None:
            return routed
        err = ("node has no authorable verbatim slot (not a top-level CodeSymbol / "
               "CodeText / Cell / Section)")
        hint = await _owning_slot_hint(gx, node)
        return {"error": err + (f" — {hint}" if hint else ""), "node_id": node_id,
                "label": _label_of(node), "written": False}
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

    # Re-derive the edited slot's bindings (2b6090dc): the author edit is the moment
    # a code slot's references change, so the frozen import_bindings refresh HERE —
    # the next canonical emit (add-symbol / move) derives from live bindings instead
    # of the last ingest's. A CodeText import edit mints module-level bindings the
    # way add_text does. Nested/method edits inherit this via the owner routing.
    rebind: Dict[str, Any] = {}
    module_bindings_merged: Optional[List[Dict[str, Any]]] = None
    minted = 0
    if artifact == "module" and label == DevNodeKinds.CODE_SYMBOL:
        try:
            ps = parse_module(new_text).symbols[0]
            available = _available_bindings(container, wires)
            rebind["import_bindings"] = [b for r in _flat_refs(ps)
                                         for b in available.get(r, [])]
        except (SyntaxError, IndexError):
            rebind = {}
    elif artifact == "module" and label == DevNodeKinds.CODE_TEXT:
        try:
            parsed_bindings = parse_module(new_text).import_bindings
        except SyntaxError:
            parsed_bindings = {}
        if parsed_bindings:
            def _bkey(b: Dict[str, Any]) -> Tuple:
                return (b.get("name"), b.get("kind"), b.get("level", 0),
                        b.get("module", ""), b.get("imported", ""), b.get("alias", ""))
            merged = list(F.prop(container, "import_bindings") or [])
            have = {_bkey(b) for b in merged}
            for descs in parsed_bindings.values():
                for b in descs:
                    if _bkey(b) not in have:
                        have.add(_bkey(b))
                        merged.append(b)
                        minted += 1
            if minted:
                module_bindings_merged = merged
    result = {
        "node_id": node_id, "label": label, "slot": slot, "actor": actor,
        "artifact": artifact, "artifact_path": artifact_path,
        "unchanged": new_text == current, "emitted_bytes": len(emitted.encode("utf-8")),
        "written": False, "emitted_text": emitted, "new_text": new_text,
    }
    if rebind.get("import_bindings") is not None:
        result["rebound_bindings"] = len(rebind["import_bindings"])
    if minted:
        result["new_import_bindings"] = minted
    if artifact == "note":
        # The durable section identity (slug, anchor) the journal records for M2b's shadow.
        result["note_slug"] = F.prop(container, "slug")
        result["anchor"] = F.prop(node, "anchor")
    elif artifact in ("module", "notebook"):
        # The durable module identity, so the CLI can absorb an authored edit of a
        # GRAPH-SOURCED module into the source journal (N+3 Phase 2). A notebook's
        # journal key is its .ipynb source path, not the export-target `module_path`
        # this carries — the CLI re-derives it from `artifact_path` + --repos-dir.
        result["repo_key"] = F.prop(container, "repo_key")
        result["module_path"] = F.prop(container, "module_path")
    if artifact_path and artifact != "note" and source_journal_path is not None:
        # Journal-first routing (the seam) — runs on --no-write too, so the dry run
        # carries the same uniform PREVIEW receipt (zero side effects).
        emission = _source_emission(result.get("repo_key"), result.get("module_path"),
                                    artifact_path, emitted, repos_dir,
                                    import_name=F.prop(container, "import_name"))
        if emission is None:
            return {**result, "error": "cannot derive the source-journal key for "
                    f"{artifact_path!r} under repos_dir={repos_dir!r} — refusing to "
                    "write unjournaled"}
        rec = journaled_emit(source_journal_path, emissions=[emission],
                             op={"op": "author", "node_id": node_id, "slot": slot,
                                 "actor": actor}, write=write)
        if rec.get("error"):
            return {**result, "error": rec["error"]}
        result["journal"] = rec
    if write and artifact_path:
        if artifact == "note" or source_journal_path is None:
            # Notes ride the WRITES-journal domain (M2b section states at the CLI seam;
            # pillar-3 unification owns merging the domains). The bare-path branch is
            # TRANSITIONAL scaffolding until every caller threads the journal.
            Path(artifact_path).write_text(emitted)
        # Persist the slot change INTO the graph node too, so the graph stays consistent
        # with the file and sequential authors compose (emit reads the graph). The file is
        # still the durable source under Fork-1(a); the next `ingest` re-derives either way.
        merge: Dict[str, Any] = {slot: new_text}
        merge.update(rebind)
        if label == DevNodeKinds.CODE_SYMBOL:
            merge["body_hash"] = SourceRef.compute_hash(new_text.encode("utf-8"))
        elif label == DevNodeKinds.SECTION:
            # Mirror the extractor: a section's content_hash is over its `raw` span.
            merge["content_hash"] = SourceRef.compute_hash(new_text.encode("utf-8"))
        await graph_task(gx.queue, gx.graph_id, "update_node", node_id=node_id, properties=merge)
        if module_bindings_merged is not None:
            await graph_task(gx.queue, gx.graph_id, "update_node", node_id=container_id,
                             properties={"import_bindings": module_bindings_merged})
        result["written"] = True
    return result


async def add_symbol(
    gx: GraphHandle,
    module_id: str,             # The CodeModule to mint the symbol into (.py modules, v1)
    body: str,                  # The symbol's verbatim source: exactly ONE top-level def/class
    *,
    actor: str = "agent:session",  # Who authored it (recorded in the result)
    write: bool = True,         # Add the node + emit the artifact (False = dry-run preview)
    source_journal_path: Optional[str] = None,  # The source journal (journal-first routing)
    repos_dir: Optional[str] = None,  # The repos root (notebook journal keys derive under it)
) -> Dict[str, Any]:  # The add result (incl. the emitted artifact path + text), or error
    """Mint a NEW top-level CodeSymbol into a module, then emit its canonical artifact.

    The CREATE leg of authoring-on-graph (`author` edits an EXISTING slot; this closes
    the no-add-symbol soak gap). The body must parse standalone as exactly ONE top-level
    def/class (decorators + leading comments ride along verbatim); the new region
    APPENDS at the end of the module — placement stays a property in v1, relational
    placement is the composed-modules item's business. The node lands with the SAME
    identity ingest derives (`code_symbol_node_id(module, qualname)`) plus its
    DEFINES/CONTAINS edges, so the next rebuild re-derives it in place rather than
    conflicting. Derived overlays (USES/CALLS edges, a new class's method children) are
    left to the next ingest — the same contract as `author`. Import bindings are bound
    against what the module ALREADY imports (module-level + every symbol's); a ref that
    needs a genuinely new import is the author's next edit, surfaced by the test run,
    never guessed."""
    module, amb = await _resolve_node(gx, module_id)
    if amb:
        return {"error": amb, "written": False}
    if module is None:
        return {"error": f"no module `{module_id}`", "written": False}
    module_id = F.nid(module)
    if _label_of(module) != DevNodeKinds.CODE_MODULE:
        return {"error": f"add-symbol targets a CodeModule (got {_label_of(module)})",
                "written": False}
    if await _notebook_cell_wires(gx, module_id):
        return {"error": "v1 adds symbols to .py modules only (this module has notebook "
                         "cells — author a new Cell instead)", "written": False}
    text = body.rstrip("\n")
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return {"error": f"body does not parse: {e}", "written": False}
    if len(tree.body) != 1 or not isinstance(
            tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {"error": "body must be exactly ONE top-level def/class (decorators + "
                         "leading comments ride along; imports/constants are CodeText "
                         "regions, not symbols)", "written": False}
    qualname = tree.body[0].name
    wires = await _module_region_wires(gx, module_id)
    dup = next((w for w in wires if w["label"] == DevNodeKinds.CODE_SYMBOL
                and w["properties"].get("qualname") == qualname), None)
    if dup is not None:
        return {"error": f"symbol `{qualname}` already exists in this module — author it "
                         f"(`{dup['id']}`)", "written": False}
    order = max((w["properties"].get("order_index") if
                 w["properties"].get("order_index") is not None else -1 for w in wires),
                default=-1) + 1

    # Bind the new symbol's refs — nested defs INCLUDED (a minted class binds what
    # its methods reference, 2b6090dc) — against everything the module carries,
    # region-text import lines included (_available_bindings).
    ps = parse_module(text).symbols[0]
    available = _available_bindings(module, wires)
    sym = CodeSymbolNode(
        module_id=module_id, qualname=qualname,
        symbol_kind="class" if isinstance(tree.body[0], ast.ClassDef) else "function",
        path=str(F.prop(module, "path") or ""),
        docstring=ps.docstring, calls=list(ps.calls), refs=list(ps.refs),
        import_bindings=[b for r in _flat_refs(ps) for b in available.get(r, [])],
        body=text, body_hash=SourceRef.compute_hash(text.encode("utf-8")),
        order_index=order,
        properties={"decorators": list(ps.decorators)} if ps.decorators else {},
    )
    gn = sym.to_graph_node()
    module_path = str(F.prop(module, "module_path") or "")
    emitted = emit_module_from_nodes(
        wires + [gn], module_node=module,
        derive_imports=not is_test_module_path(module_path))
    artifact_path = F.prop(module, "path")
    result = {
        "module_id": module_id, "symbol_id": sym.id, "qualname": qualname,
        "symbol_kind": sym.symbol_kind, "order_index": order, "actor": actor,
        "artifact": "module", "artifact_path": artifact_path,
        "repo_key": F.prop(module, "repo_key"), "module_path": module_path,
        "emitted_bytes": len(emitted.encode("utf-8")), "emitted_text": emitted,
        "unchanged": False, "written": False,
    }
    if artifact_path and source_journal_path is not None:
        emission = _source_emission(F.prop(module, "repo_key"), module_path,
                                    artifact_path, emitted, repos_dir,
                                    import_name=F.prop(module, "import_name"))
        if emission is None:
            return {**result, "error": "cannot derive the source-journal key for "
                    f"{artifact_path!r} under repos_dir={repos_dir!r} — refusing to "
                    "write unjournaled"}
        rec = journaled_emit(source_journal_path, emissions=[emission],
                             op={"op": "add-symbol", "qualname": qualname,
                                 "actor": actor}, write=write)
        if rec.get("error"):
            return {**result, "error": rec["error"]}
        result["journal"] = rec
    if write and artifact_path:
        if source_journal_path is None:
            Path(artifact_path).write_text(emitted)  # TRANSITIONAL bare path (seam rollout)
        await graph_task(gx.queue, gx.graph_id, "add_nodes", nodes=[gn])
        await graph_task(gx.queue, gx.graph_id, "add_edges", edges=[
            make_edge(module_id, sym.id, DevRelations.DEFINES),
            make_edge(module_id, sym.id, DevRelations.CONTAINS)])
        result["written"] = True
    return result


async def add_text(
    gx: GraphHandle,
    module_id: str,             # The CodeModule to mint the region into (.py modules, v1)
    body: str,                  # The region's verbatim source: top-level NON-def statements only
    *,
    actor: str = "agent:session",  # Who authored it (recorded in the result)
    write: bool = True,         # Add the node + emit the artifact (False = dry-run preview)
    source_journal_path: Optional[str] = None,  # The source journal (journal-first routing)
    repos_dir: Optional[str] = None,  # The repos root (notebook journal keys derive under it)
) -> Dict[str, Any]:  # The add result (incl. the emitted artifact path + text), or error
    """Mint a NEW CodeText region (imports/constants/docstring/`__all__`) into a module, then emit.

    The CodeText dual of `add_symbol` — the CREATE leg for the regions that are NOT
    symbols, closing the born-on-graph seeding gap (a `new-module` module could never
    acquire imports or constants through the CLI). The body must parse standalone and
    contain NO top-level def/class (those are `add-symbol`'s); the region lands
    VERBATIM with the SAME identity ingest derives (`code_text_node_id(module,
    first-non-blank-line)`) and appends at the end of the module's region order.
    Top-level import statements ALSO merge their parsed bindings into the module
    node's `import_bindings`, so the canonical emit renders them and later
    `add_symbol` calls bind refs against them — the fresh-module import bootstrap
    (without it, a born-on-graph module's derived import block stays empty no matter
    what the region says). Canonical emit repositions the derived import block at the
    top regardless of the region's order slot."""
    # Local imports: adding an import line to THIS module's imports region is the open
    # binding-table gap (47b256de) — stay self-contained until it closes.
    from cjm_python_decompose_core.parse import parse_regions
    from cjm_dev_graph_schema.nodes import CodeTextNode

    module, amb = await _resolve_node(gx, module_id)
    if amb:
        return {"error": amb, "written": False}
    if module is None:
        return {"error": f"no module `{module_id}`", "written": False}
    module_id = F.nid(module)
    if _label_of(module) != DevNodeKinds.CODE_MODULE:
        return {"error": f"add-text targets a CodeModule (got {_label_of(module)})",
                "written": False}
    if await _notebook_cell_wires(gx, module_id):
        return {"error": "v1 adds text regions to .py modules only (this module has "
                         "notebook cells — author a Cell instead)", "written": False}
    text = body.rstrip("\n")
    if not text.strip():
        return {"error": "empty body", "written": False}
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return {"error": f"body does not parse: {e}", "written": False}
    if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
           for n in tree.body):
        return {"error": "body must contain NO top-level def/class — mint those with "
                         "add-symbol (a CodeText holds the substrate BETWEEN symbols)",
                "written": False}
    regions = parse_regions(text)
    if len(regions) != 1:  # defensive: with no defs, parse_regions yields one whole region
        return {"error": f"body decomposed into {len(regions)} regions — add ONE "
                         "contiguous region per call", "written": False}
    region = regions[0]
    stripped = text.lstrip()
    # Mirrors ingest's region-kind classification (extract._text_region_kind).
    kind = ("imports" if stripped.startswith(("import ", "from "))
            else "docstring" if stripped.startswith(('"""', "'''", '"', "'"))
            else "code")
    wires = await _module_region_wires(gx, module_id)
    dup = next((w for w in wires if w["label"] == DevNodeKinds.CODE_TEXT
                and w["properties"].get("region_key") == region.region_key), None)
    if dup is not None:
        return {"error": f"a region leading with the same line already exists — author "
                         f"it (`{dup['id']}`)", "written": False}
    order = max((w["properties"].get("order_index") if
                 w["properties"].get("order_index") is not None else -1 for w in wires),
                default=-1) + 1
    ct = CodeTextNode(
        module_id=module_id, region_key=region.region_key, text=region.text,
        content_hash=SourceRef.compute_hash(region.text.encode("utf-8")),
        order_index=order, path=str(F.prop(module, "path") or ""), kind=kind)
    gn = ct.to_graph_node()

    # Fresh-module import bootstrap: merge the body's top-level import bindings into
    # the module node so canonical emit + later add_symbol calls see them.
    new_bindings: List[Dict[str, Any]] = []
    merged = list(F.prop(module, "import_bindings") or [])
    if any(isinstance(n, (ast.Import, ast.ImportFrom)) for n in tree.body):
        def _bkey(b: Dict[str, Any]) -> Tuple:
            return (b.get("name"), b.get("kind"), b.get("level", 0),
                    b.get("module", ""), b.get("imported", ""), b.get("alias", ""))
        have = {_bkey(b) for b in merged}
        for descs in parse_module(text).import_bindings.values():
            for b in descs:
                if _bkey(b) not in have:
                    have.add(_bkey(b))
                    merged.append(b)
                    new_bindings.append(b)
    module_wire = _as_wire(module, DevNodeKinds.CODE_MODULE)
    if new_bindings:
        module_wire["properties"]["import_bindings"] = merged

    module_path = str(F.prop(module, "module_path") or "")
    emitted = emit_module_from_nodes(
        wires + [gn], module_node=module_wire,
        derive_imports=not is_test_module_path(module_path))
    artifact_path = F.prop(module, "path")
    result = {
        "module_id": module_id, "text_id": ct.id, "region_key": region.region_key,
        "kind": kind, "order_index": order, "actor": actor,
        "new_import_bindings": len(new_bindings),
        "artifact": "module", "artifact_path": artifact_path,
        "repo_key": F.prop(module, "repo_key"), "module_path": module_path,
        "emitted_bytes": len(emitted.encode("utf-8")), "emitted_text": emitted,
        "unchanged": False, "written": False,
    }
    if artifact_path and source_journal_path is not None:
        emission = _source_emission(F.prop(module, "repo_key"), module_path,
                                    artifact_path, emitted, repos_dir,
                                    import_name=F.prop(module, "import_name"))
        if emission is None:
            return {**result, "error": "cannot derive the source-journal key for "
                    f"{artifact_path!r} under repos_dir={repos_dir!r} — refusing to "
                    "write unjournaled"}
        rec = journaled_emit(source_journal_path, emissions=[emission],
                             op={"op": "add-text", "region_key": region.region_key,
                                 "actor": actor}, write=write)
        if rec.get("error"):
            return {**result, "error": rec["error"]}
        result["journal"] = rec
    if write and artifact_path:
        if source_journal_path is None:
            # TRANSITIONAL bare path (seam rollout). A born-on-graph package's FIRST
            # region precedes its directory on disk.
            Path(artifact_path).parent.mkdir(parents=True, exist_ok=True)
            Path(artifact_path).write_text(emitted)
        await graph_task(gx.queue, gx.graph_id, "add_nodes", nodes=[gn])
        await graph_task(gx.queue, gx.graph_id, "add_edges", edges=[
            make_edge(module_id, ct.id, DevRelations.CONTAINS)])
        if new_bindings:
            await graph_task(gx.queue, gx.graph_id, "update_node", node_id=module_id,
                             properties={"import_bindings": merged})
        result["written"] = True
    return result


def _slice_block(text: str, basename: str) -> Optional[str]:
    """Slice one def/class block (decorators included) out of an enclosing body, by name.

    The server-side read-slice for NESTED symbols (de9d7696 (a)): a method's text lives
    in its owning top-level symbol / notebook Cell, and 'read the encloser, then find
    your method in it' was exactly the routing rule weaker readers could not hold. Plain
    line-scan, not ast: the input is ONE verbatim slot (a Cell may hold several
    statements), and the block ends at the first subsequent non-blank line at <= the
    header's indent. Contiguous decorator lines directly above the header (same indent)
    ride along. First name-match wins; None when the name opens no block here."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        for kw in ("async def ", "def ", "class "):
            if not stripped.startswith(kw + basename):
                continue
            tail = stripped[len(kw) + len(basename):]
            if tail and tail[0] not in "(:" and not tail[0].isspace():
                continue  # name-prefix false positive (`foo2` is not `foo`)
            indent = len(line) - len(stripped)
            start = i
            while (start > 0 and lines[start - 1].lstrip().startswith("@")
                   and len(lines[start - 1]) - len(lines[start - 1].lstrip()) == indent):
                start -= 1
            end = len(lines)
            for j in range(i + 1, len(lines)):
                s = lines[j].strip()
                if s and len(lines[j]) - len(lines[j].lstrip()) <= indent:
                    end = j
                    break
            return "\n".join(lines[start:end]).rstrip()
    return None


async def _owning_slot_wire(
    gx: GraphHandle, node: Any,
) -> Optional[Dict[str, Any]]:  # The owning slot's wire dict, or None when nothing owns it
    """Locate the verbatim slot that OWNS a nested CodeSymbol's text — the ONE locator.

    A method/nested def has no body of its own; its text lives in a top-level `.py`
    symbol or a notebook Cell. This is the shared resolver behind the author routing
    (`_route_nested_symbol`), the error-path pointer (`_owning_slot_hint`), and the
    nested read-slice (`read_node`), so the three surfaces can never drift apart:
    qualname's top-level prefix first, def/class marker scan as fallback (the
    monkey-patch/@patch idiom, where the def lives under another region)."""
    if _label_of(node) != DevNodeKinds.CODE_SYMBOL or F.props(node).get("body"):
        return None
    module_id = F.prop(node, "module_id")
    qualname = str(F.prop(node, "qualname") or F.prop(node, "name") or "")
    basename = qualname.split(".")[-1]
    if not module_id or not basename:
        return None
    container = await _module_node(gx, module_id)
    if container is None:
        return None
    markers = (f"def {basename}(", f"class {basename}(", f"class {basename}:")
    if str(F.prop(container, "path") or "").endswith(".ipynb"):
        for w in await _notebook_cell_wires(gx, module_id):
            if any(m in str(w["properties"].get("source") or "") for m in markers):
                return w
        return None
    wires = [w for w in await _module_region_wires(gx, module_id)
             if w["label"] == DevNodeKinds.CODE_SYMBOL and w["id"] != F.nid(node)]
    top = qualname.split(".")[0]
    owner = next((w for w in wires if w["properties"].get("qualname") == top), None)
    if owner is None:
        owner = next((w for w in wires
                      if any(m in str(w["properties"].get("body") or "") for m in markers)),
                     None)
    return owner


def _source_emission(
    repo_key: str,             # The node's rename-stable CONCEPTUAL repo key
    module_path: str,          # The node's module_path (a notebook's EXPORT-TARGET .py path)
    artifact_path: str,        # The absolute artifact path the emit targets
    text: str,                 # The emitted artifact text
    repos_dir: Optional[str],  # The repos root (a notebook's journal path derives under it)
    import_name: Optional[str] = None,  # Dotted import name (rides the source event)
) -> Optional[Dict[str, Any]]:  # A journaled_emit emission dict, or None (key underivable)
    """Map an authoring emit into the SOURCE JOURNAL's key space.

    The node carries the rename-stable conceptual repo key and (for notebooks) the nbdev
    EXPORT-TARGET module_path; the source journal keys by repo DIR name and the .ipynb
    SOURCE path (what cutover recorded) — the c89519cd/f06ef1a6 lessons: an unmapped key
    once skipped journaling SILENTLY while the file still wrote. A notebook's journal path
    re-derives from artifact_path under repos_dir/<dir_key>; None (underivable) tells the
    caller to go LOUD — never write unjournaled."""
    dir_key = repo_dir_name(repo_key)
    src_path = module_path
    if str(artifact_path).endswith(".ipynb"):
        if not repos_dir:
            return None
        try:
            src_path = Path(artifact_path).relative_to(Path(repos_dir) / dir_key).as_posix()
        except ValueError:
            return None
    return {"repo_key": dir_key, "module_path": src_path, "import_name": import_name,
            "text": text, "path": str(artifact_path)}


def _flat_refs(
    ps: Any,  # A ParsedSymbol (with nested children)
) -> List[str]:  # Its refs + every descendant's refs (dedup, order-preserved)
    """A symbol's referenced names INCLUDING its nested defs' (the class-body reach).

    `ParsedSymbol.refs` excludes nested def bodies by design (methods carry their
    own refs), but a binding walk over ONE authored/minted symbol must see the whole
    region: a name referenced only inside a method body binds imports too — without
    this, a class-heavy symbol's import_bindings miss everything its methods use
    and the next canonical emit prunes those imports (finding 2b6090dc)."""
    names: "dict[str, None]" = {}

    def walk(s: Any) -> None:
        for r in s.refs:
            names.setdefault(r, None)
        for c in s.children:
            walk(c)

    walk(ps)
    return list(names)


def _available_bindings(
    module: Any,                   # The CodeModule node (or wire dict)
    wires: List[Dict[str, Any]],   # The module's region wires (CodeSymbol + CodeText)
) -> Dict[str, List[Dict[str, Any]]]:  # name -> coexisting binding descriptors
    """Every import binding the module carries, keyed by bound local name.

    Unions three sources: the module node's bindings, every region wire's frozen
    per-symbol bindings, and the import statements parsed out of the CodeText
    regions' VERBATIM text. The parse leg is the 47b256de/2b6090dc closer: an
    author-edited import line lives only in a region's text (CodeText carries no
    binding table), so parsing it here makes region edits mint bindings the way
    `add_text` does — a later symbol's refs can bind against it. A name maps to a
    LIST of descriptors: coexisting plain submodule imports (`import a.b` +
    `import a.c`) share one bound name — a one-per-name map silently drops all
    but one."""
    available: Dict[str, List[Dict[str, Any]]] = {}
    seen: set = set()

    def _add(b: Dict[str, Any]) -> None:
        k = (b.get("kind"), b.get("level", 0), b.get("module", ""),
             b.get("imported", ""), b.get("alias", ""))
        if k not in seen:
            seen.add(k)
            available.setdefault(b.get("name"), []).append(b)

    for b in (F.prop(module, "import_bindings") or []):
        _add(b)
    for w in wires:
        for b in (w["properties"].get("import_bindings") or []):
            _add(b)
    for w in wires:
        if w["label"] != DevNodeKinds.CODE_TEXT:
            continue
        t = str(w["properties"].get("text") or "")
        if "import" not in t:
            continue
        try:
            for descs in parse_module(t).import_bindings.values():
                for b in descs:
                    _add(b)
        except SyntaxError:
            continue
    return available
