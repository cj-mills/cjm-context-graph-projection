"""The B write surface: AUTHOR a verbatim-text slot on-graph, emit the canonical artifact.

The make-or-break authoring increment of [[graph-as-source-of-truth-inversion]]. A
`CodeSymbol` body, a `CodeText` region, and a notebook `Cell` source are the SAME kind
of thing — a VERBATIM-TEXT SLOT on a node — so `author` targets that slot abstraction,
not a node kind: it edits code symbols, code-text regions, and notebook cells uniformly.

Two modes (the lesson from the pre-arc NotebookEdit pain, where every change meant
rewriting the whole cell):
- `replace` — set the slot's full new text (the Write / NotebookEdit analogue), and
- `edit` — a unique-match OLD->NEW splice within the slot (the Edit analogue; the
  low-token targeted path).

Persistence = Fork-1(a) (file stays the source; the graph is the editing surface): the
edit is applied to the module's regions READ FROM THE GRAPH, the canonical artifact is
re-emitted (graph OWNS formatting), and the FILE on disk is the durable record — the next
`ingest` re-derives the graph from it (code/notebooks are rebuilt sources, NOT journaled;
a targeted OLD->NEW splice isn't replay-idempotent anyway, so the journal correctly waits
for true-B, which will journal the resulting body STATE, not the diff). So author against a
freshly-ingested graph; emit reproduces the file byte-exact except the authored change.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cjm_context_graph_layer.ops import graph_task
from cjm_context_graph_primitives.provenance import SourceRef
from cjm_dev_graph_schema.vocab import DevNodeKinds
from cjm_notebook_decompose_core.project import render_notebook
from cjm_python_decompose_core.emit import emit_module_from_nodes

from . import factlayer as F
from .runtime import GraphHandle

# Per node label: the verbatim-text slot property + the artifact kind it composes.
_SLOTS = {
    DevNodeKinds.CODE_SYMBOL: ("body", "module"),    # a top-level symbol's body -> a .py module
    DevNodeKinds.CODE_TEXT: ("text", "module"),      # a non-def region's text -> a .py module
    DevNodeKinds.CELL: ("source", "notebook"),       # a notebook cell's source -> an .ipynb
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


async def emit_artifact(
    gx: GraphHandle,
    module_id: str,        # The CodeModule id (a .py module or a notebook) to emit
    *,
    write: bool = False,   # Write to the module's path on disk (else just return the text)
) -> Dict[str, Any]:  # {artifact, artifact_path, text, written} or {error}
    """Emit a module's canonical artifact FROM THE GRAPH (graph -> .py / .ipynb).

    The round-trip read leg, standalone: reassemble a module from its stored regions/cells
    with no mutation — proves the graph is a sufficient source, and (true-B preview) lets
    a generated file be refreshed from the graph. Detects notebook vs `.py` by whether the
    module has `Cell` children."""
    module = await _module_node(gx, module_id)
    if module is None:
        return {"error": f"no module `{module_id}`", "module_id": module_id}
    artifact_path = F.prop(module, "path")
    cells = await _notebook_cell_wires(gx, module_id)
    if cells:
        artifact, text = "notebook", render_notebook(cells)
    else:
        text = emit_module_from_nodes(await _module_region_wires(gx, module_id))
        artifact = "module"
    res = {"module_id": module_id, "artifact": artifact, "artifact_path": artifact_path,
           "emitted_bytes": len(text.encode("utf-8")), "text": text, "written": False}
    if write and artifact_path:
        Path(artifact_path).write_text(text)
        res["written"] = True
    return res


async def author(
    gx: GraphHandle,
    node_id: str,                          # The node whose verbatim slot to author (CodeSymbol / CodeText / Cell)
    *,
    replace: Optional[str] = None,         # Full replacement text (replace mode)
    edit: Optional[Tuple[str, str]] = None,  # (old, new) unique-match splice (edit mode)
    actor: str = "agent:session",          # Who authored it (recorded in the result; provenance lands via decide/link)
    write: bool = True,                    # Emit the canonical artifact to disk (Fork-1(a)); False = dry run
) -> Dict[str, Any]:  # The write result (incl. error, the emitted artifact path + text)
    """Author a node's verbatim-text slot, then emit its canonical artifact to disk.

    Reads the enclosing module's regions FROM THE GRAPH, applies the edit in memory,
    reassembles the canonical `.py` (or `.ipynb` for a cell), and writes the file — the
    graph is the editing surface, the file the durable source. Returns the artifact path
    + emitted text (and, on `write=False`, emits without touching disk: a dry-run/preview)."""
    node = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=node_id)
    if node is None:
        return {"error": f"no node `{node_id}`", "node_id": node_id, "written": False}
    resolved = _slot_for(node)
    if resolved is None:
        return {"error": "node has no authorable verbatim slot (not a top-level CodeSymbol / "
                         "CodeText / Cell)", "node_id": node_id, "label": _label_of(node),
                "written": False}
    slot, artifact, label = resolved
    current = str(F.prop(node, slot, ""))
    new_text, err = _apply(current, replace, edit)
    if err is not None:
        return {"error": err, "node_id": node_id, "label": label, "slot": slot, "written": False}

    module_id = F.prop(node, "module_id")
    module = await _module_node(gx, module_id)
    if module is None:
        return {"error": f"enclosing module `{module_id}` not found", "node_id": node_id,
                "written": False}
    artifact_path = F.prop(module, "path")

    # Read the module's regions/cells from the graph, inject the mutated slot, reassemble.
    if artifact == "notebook":
        wires = await _notebook_cell_wires(gx, module_id)
    else:
        wires = await _module_region_wires(gx, module_id)
    for w in wires:
        if w["id"] == node_id:
            w["properties"][slot] = new_text
    emitted = (render_notebook(wires) if artifact == "notebook"
               else emit_module_from_nodes(wires))

    result = {
        "node_id": node_id, "label": label, "slot": slot, "actor": actor,
        "artifact": artifact, "artifact_path": artifact_path,
        "unchanged": new_text == current, "emitted_bytes": len(emitted.encode("utf-8")),
        "written": False, "emitted_text": emitted,
    }
    if write and artifact_path:
        Path(artifact_path).write_text(emitted)
        # Persist the slot change INTO the graph node too, so the graph stays consistent
        # with the file and sequential authors compose (emit reads the graph). The file is
        # still the durable source under Fork-1(a); the next `ingest` re-derives either way.
        merge: Dict[str, Any] = {slot: new_text}
        if label == DevNodeKinds.CODE_SYMBOL:
            merge["body_hash"] = SourceRef.compute_hash(new_text.encode("utf-8"))
        await graph_task(gx.queue, gx.graph_id, "update_node", node_id=node_id, properties=merge)
        result["written"] = True
    return result
