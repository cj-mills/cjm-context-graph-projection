"""Prose-ref drift: id-shaped tokens in asserted prose vs the edge layer.

Seeded by finding 49455b9e (the 367eaaae case): a user-ratified schedule DEC
carried ~8 work-item ids as PROSE with ZERO edges — invisible to readiness,
filing, and every lens, so its full discharge went unrecorded for a week; the
hand-lead pointer to it was ALSO typo'd (the 367aeae case). This instrument
makes both failure modes structural:

    unlinked     = an id-shaped token that RESOLVES to a node the source shares
                   NO edge with (either direction) — a prose-only reference; the
                   proposal is `link <source> REFERENCES <target>`
    unresolvable = an id-shaped token matching NO node — a typo'd ref, a rotted
                   id, or an out-of-graph artifact id (judge, never autofix)
    degree_zero  = an asserted Decision/Note whose edges reach NOTHING at all

Worklist family (register-drift / filing / contradictions): propose/confirm,
never autofix; a derived read with no write path.
"""

import re
from typing import Any, Dict, List, Set

from cjm_context_graph_layer.ops import graph_task
from cjm_context_graph_primitives.query import EdgeQuery, NodeQuery

from . import factlayer as F
from .display import annotate_display
from .projection import node_title
from .runtime import GraphHandle

# An 8-hex prefix or a full UUID. Bare 8-char all-digit tokens (dates like
# 20260810) are excluded token-side in `extract_id_tokens`.
_ID_TOKEN_RE = re.compile(
    r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?\b")


def extract_id_tokens(
    text: str,  # Prose to scan (a Decision statement, a Note description/body)
) -> List[str]:
    """Id-shaped tokens in prose: 8-hex prefixes / full UUIDs, ordered, deduped.

    Bare 8-char all-digit tokens are dates (20260810), never ids — excluded."""
    out: List[str] = []
    for m in _ID_TOKEN_RE.finditer(text or ""):
        tok = m.group(0)
        if len(tok) == 8 and tok.isdigit():
            continue
        if tok not in out:
            out.append(tok)
    return out


async def prose_refs(
    gx: GraphHandle,
    limit: int = 30,  # Cap each reported bucket (counts stay TRUE totals)
) -> Dict[str, Any]:  # {sources_scanned, unlinked, unresolvable, degree_zero, counts}
    """The prose-ref drift audit over asserted Decisions + Notes (pure read).

    Two projected reads (all node ids · all edge pairs, relation-agnostic) feed
    three buckets: prose-only references (resolvable token, no edge either
    direction — proposal: `link REFERENCES`), unresolvable tokens (typo'd /
    rotted / out-of-graph), and degree-zero asserted nodes (the 367eaaae
    smoking gun: ratified content connected to nothing)."""
    decisions = await F.load_label(gx, "Decision")
    notes = await F.load_label(gx, "Note")
    sources = [(F.nid(n), str(F.prop(n, "statement") or "")) for n in decisions]
    sources += [(F.nid(n), " ".join((str(F.prop(n, "description") or ""),
                                     str(F.prop(n, "text") or "")))) for n in notes]
    res = await graph_task(gx.queue, gx.graph_id, "query_nodes",
                           query=NodeQuery(project=["id"]).to_dict())
    all_ids = [r["id"] for r in (res.rows or [])]
    id_set = set(all_ids)
    by_prefix: Dict[str, List[str]] = {}
    for i in all_ids:
        by_prefix.setdefault(i[:8], []).append(i)
    eres = await graph_task(gx.queue, gx.graph_id, "query_edges",
                            query=EdgeQuery(project=["source_id", "target_id"]).to_dict())
    neighbours: Dict[str, Set[str]] = {}
    for r in (eres.rows or []):
        neighbours.setdefault(r["source_id"], set()).add(r["target_id"])
        neighbours.setdefault(r["target_id"], set()).add(r["source_id"])
    unlinked: List[Dict[str, Any]] = []
    unresolvable: List[Dict[str, Any]] = []
    for sid, text in sources:
        if not sid or not text:
            continue
        near = neighbours.get(sid, set())
        for tok in extract_id_tokens(text):
            if sid.startswith(tok):
                continue  # self-reference
            targets = by_prefix.get(tok, []) if len(tok) == 8 else \
                ([tok] if tok in id_set else [])
            if not targets:
                unresolvable.append({"source_id": sid, "token": tok})
            elif not any(t in near for t in targets):
                unlinked.append({"source_id": sid, "token": tok,
                                 "target_id": targets[0],
                                 "ambiguous": len(targets) > 1})
    degree_zero = [{"id": sid} for sid, _text in sources
                   if sid and sid not in neighbours]
    counts = {"sources": len(sources), "unlinked": len(unlinked),
              "unresolvable": len(unresolvable), "degree_zero": len(degree_zero)}
    unlinked, unresolvable, degree_zero = (
        unlinked[:limit], unresolvable[:limit], degree_zero[:limit])
    label_ids = ({e["source_id"] for e in unlinked} | {e["target_id"] for e in unlinked}
                 | {e["source_id"] for e in unresolvable} | {e["id"] for e in degree_zero})
    nodes = await F.load_nodes(gx, sorted(label_ids))
    await annotate_display(gx, list(nodes.values()))
    labels = {i: (node_title(nodes[i]) if i in nodes else i) for i in label_ids}
    for e in unlinked:
        e["source"] = labels.get(e["source_id"], e["source_id"])
        e["target"] = labels.get(e["target_id"], e["target_id"])
    for e in unresolvable:
        e["source"] = labels.get(e["source_id"], e["source_id"])
    for e in degree_zero:
        e["label"] = labels.get(e["id"], e["id"])
    return {"sources_scanned": len(sources), "unlinked": unlinked,
            "unresolvable": unresolvable, "degree_zero": degree_zero,
            "counts": counts}
