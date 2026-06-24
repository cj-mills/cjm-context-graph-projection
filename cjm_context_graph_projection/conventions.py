"""Structural convention audit over the code/notebook graph (the enforcement nbdev lacks).

nbdev cannot enforce authoring conventions — granular cells, prose adjacent to each
definition, tests per symbol. Under graph-as-source-of-truth those become QUERIES over
the graph. This is the read-side first cut, focused on notebook-sourced symbols (the
ones a `Cell` produced):

- `undocumented`: a top-level public symbol with NO incoming `DOCUMENTS` prose cell.
- `no_docstring`: a top-level public symbol whose docstring/description is empty.
- `non_granular_cells`: a cell defining MORE THAN ONE top-level public symbol (an
  nbdev best-practice is roughly one definition per cell).

A symbol is "notebook-sourced" when it carries a `cell_key` (set by the compositor) —
so plain `.py`-sourced symbols, which legitimately have no notebook prose, are not
flagged for missing `DOCUMENTS`. The compute is a PURE function over node lists; the
async wrapper just loads the graph slices it needs.
"""

from typing import Any, Dict, Iterable, List, Optional

from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from . import factlayer as F


def _is_public(qualname: str) -> bool:
    """True for a top-level, non-underscore-prefixed name (the audited surface)."""
    return "." not in qualname and not qualname.split(".")[-1].startswith("_")


def compute_conventions(
    symbols: Iterable[Any],        # CodeSymbol nodes (GraphNodes or wire dicts)
    documented_ids: set,           # Symbol ids that have an incoming DOCUMENTS edge
    scope: Optional[str] = None,   # Restrict to one module id (None = whole graph)
) -> Dict[str, Any]:  # The audit result (counts + finding lists)
    """Compute convention findings from CodeSymbol nodes + the documented-id set (pure)."""
    undocumented: List[Dict[str, str]] = []
    no_docstring: List[Dict[str, str]] = []
    by_cell: Dict[str, List[str]] = {}

    for s in symbols:
        sid = F.nid(s)
        qual = F.prop(s, "qualname", "") or ""
        cell_key = F.prop(s, "cell_key")
        module_id = F.prop(s, "module_id")
        if cell_key is None:               # not notebook-sourced -> not audited here
            continue
        if scope is not None and module_id != scope:
            continue
        if not _is_public(qual):           # audit the public top-level surface only
            continue
        entry = {"id": sid, "qualname": qual, "module_id": module_id, "cell_key": cell_key}
        if sid not in documented_ids:
            undocumented.append(entry)
        if not (F.prop(s, "description") or "").strip():
            no_docstring.append(entry)
        by_cell.setdefault(f"{module_id}::{cell_key}", []).append(qual)

    non_granular = [{"cell": k, "symbols": sorted(v)} for k, v in by_cell.items() if len(v) > 1]
    return {
        "scope": scope,
        "counts": {"undocumented": len(undocumented), "no_docstring": len(no_docstring),
                   "non_granular_cells": len(non_granular)},
        "undocumented": undocumented,
        "no_docstring": no_docstring,
        "non_granular_cells": non_granular,
    }


async def conventions(
    gx,
    scope: Optional[str] = None,  # Restrict to one notebook module id (None = whole graph)
) -> Dict[str, Any]:  # The audit result
    """Audit notebook-sourced symbols for missing prose/docstrings + non-granular cells."""
    symbols = await F.load_label(gx, DevNodeKinds.CODE_SYMBOL)
    documented = {tgt for _src, tgt in await F.load_edge_pairs(gx, DevRelations.DOCUMENTS)}
    return compute_conventions(symbols, documented, scope)
