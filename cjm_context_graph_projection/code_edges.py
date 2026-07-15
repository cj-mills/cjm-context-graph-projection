"""Orphaned code-target edge detector: journaled links whose endpoint no longer resolves.

Code is not yet on-graph (Fork-1(a): re-ingested from disk each rebuild) and
CodeSymbol/CodeModule ids are DETERMINISTIC from (module_path, symbol_name) — so
a rename mints a NEW id and the old node simply never re-ingests. Any
hand-authored `link` op targeting the old id (a Decision's SHAPES edge onto the
symbol it shaped) then hits a missing endpoint on replay and is SILENTLY dropped:
orphaned provenance, the same integrity break the memory membrane hit. This
projector is the detector (worklist family — propose/confirm, never auto-fix):

    orphan = a journaled `link` op with >=1 endpoint absent from the CURRENT graph
             (exactly the set the next replay will drop)

PROPOSALS need the old NAME, and a legacy op carries only the opaque old id — so
`link` now journals endpoint LABELS as audit-only fields (replay ignores them):
a post-enrichment orphan fuzzy-matches its recorded label against the current
code names and proposes the remap; a label-less legacy orphan still DETECTS,
with the resolving side + relation as the trail back to intent. The long-term
preventive lives in the rename verbs (they know old->new at rename time); this
detector is needed regardless, as the safety net under the authored-on-graph
transition.
"""

import difflib
from typing import Any, Dict, List, Optional, Set

from cjm_context_graph_primitives.journal import read_journal
from cjm_dev_graph_schema.vocab import DevNodeKinds

from . import factlayer as F
from .display import annotate_display, node_title
from .runtime import GraphHandle


def classify_orphaned_links(
    link_ops: List[Dict[str, Any]],       # journaled link args: {source_id, target_id, relation, *_label?}
    resolved_ids: Set[str],               # ids that exist in the CURRENT graph
    code_names: Optional[Dict[str, str]] = None,  # current code name -> node id (the proposal universe)
    cutoff: float = 0.6,                  # difflib similarity cutoff for a proposal
) -> List[Dict[str, Any]]:  # one entry per orphaned edge (deduped)
    """Pure: the journaled links the next replay will silently drop.

    A missing endpoint WITH a journaled label gets a fuzzy remap proposal against
    the current code names (no auto-guess — confirming means re-linking to the
    proposed id and retiring the stale op's edge). Absence of a label downgrades
    to detection-only, never to a guess."""
    orphans: List[Dict[str, Any]] = []
    seen: Set[tuple] = set()
    for op in link_ops:
        key = (op.get("source_id"), op.get("relation"), op.get("target_id"))
        if key in seen:
            continue
        seen.add(key)
        missing: List[Dict[str, Any]] = []
        for side in ("source", "target"):
            oid = op.get(f"{side}_id")
            if not oid or oid in resolved_ids:
                continue
            entry: Dict[str, Any] = {"side": side, "id": oid,
                                     "label": op.get(f"{side}_label")}
            label = entry["label"]
            if label and code_names:
                match = difflib.get_close_matches(label, list(code_names), n=1, cutoff=cutoff)
                if match:
                    entry["proposal"] = {
                        "name": match[0], "id": code_names[match[0]],
                        "score": round(difflib.SequenceMatcher(None, label, match[0]).ratio(), 3)}
            missing.append(entry)
        if missing:
            orphans.append({"source_id": op.get("source_id"), "target_id": op.get("target_id"),
                            "relation": op.get("relation"), "missing": missing})
    return orphans


async def orphaned_edges(
    gx: GraphHandle,
    journal_path: str,  # The write journal (JSONL) whose link ops are audited
) -> Dict[str, Any]:  # {orphans, counts}
    """The derived orphan report over journal `link` ops + the current graph.

    Pure read: gathers every journaled link op, batch-resolves all endpoint ids
    against the current graph, builds the current code-name universe (CodeSymbol +
    CodeModule) for proposals, classifies, then decorates each orphan's RESOLVING
    side with a display label (the trail back to what the edge meant)."""
    all_ops = read_journal(journal_path)
    # A retracted link is not an orphan — its edge is GONE by intent (unlink,
    # 2f1d9382), so proposing a remap for it would resurrect retracted noise.
    retracted = {((o.get("args") or {}).get("source_id"), (o.get("args") or {}).get("relation"),
                  (o.get("args") or {}).get("target_id"))
                 for o in all_ops if o.get("verb") == "unlink"}
    ops = [op.get("args", {}) for op in all_ops
           if op.get("verb") == "link"
           and ((op.get("args") or {}).get("source_id"), (op.get("args") or {}).get("relation"),
                (op.get("args") or {}).get("target_id")) not in retracted]

    ids: Set[str] = set()
    for op in ops:
        ids.update(i for i in (op.get("source_id"), op.get("target_id")) if i)
    nodes = await F.load_nodes(gx, list(ids))
    resolved = set(nodes)

    code_names: Dict[str, str] = {}
    for label in (DevNodeKinds.CODE_SYMBOL, DevNodeKinds.CODE_MODULE):
        for n in await F.load_label(gx, label):
            name = F.prop(n, "name") or F.prop(n, "title")
            if name:
                code_names.setdefault(str(name), F.nid(n))

    orphans = classify_orphaned_links(ops, resolved, code_names)

    context_ids = {op_id for o in orphans
                   for op_id in (o["source_id"], o["target_id"])
                   if op_id in resolved}
    context_nodes = [nodes[i] for i in context_ids]
    await annotate_display(gx, context_nodes)
    labels = {i: node_title(nodes[i]) for i in context_ids}
    for o in orphans:
        for side in ("source", "target"):
            oid = o[f"{side}_id"]
            if oid in labels:
                o[f"{side}_context"] = labels[oid]

    proposed = sum(1 for o in orphans for m in o["missing"] if "proposal" in m)
    return {"orphans": orphans,
            "counts": {"link_ops": len(ops), "orphaned": len(orphans),
                       "with_proposal": proposed}}
