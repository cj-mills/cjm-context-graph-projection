"""Hand-seeded load-bearing slots + the rename-stable repo-key machinery.

Hand-seed the load-bearing structured slots; extract the rest (the graph is
correctable). Three seeds in the cut:

1. **The torch/hf-utils rename-disposition contradiction** — the DoD headline. A
   real single-source-of-truth failure: "keep names" originated in the
   foundational-picture note and was DUPLICATED into the arc-status note + the
   overview doc, then propagated a wrong "keep their names" assessment, until the
   user caught it and stage 9 renamed both. On-graph it is ONE slot with TWO
   assertions — a `keep` claim carrying provenance edges to ALL its source notes
   (the dedup win, not three drifting copies) vs a `rename` claim — both initially
   active so `contradictions` detects it. The clearing beat (rename SUPERSEDES
   keep) is exercised through the write surface, not seeded.
2. **A stale `cjm-substrate` version slot** — seeded BEHIND the real version so the
   oracle demonstrably bumps it (DoD goal 2).
3. **A small cores/utils class-subject seed** — just enough to prove class-subjects
   and get real-world evidence.

Rename-stable identity (A+aliases): a repo entity's `key` is a durable conceptual
slug, name-INDEPENDENT, so a fact about a renamed repo keeps one home. Only repos
with a KNOWN rename history get a name-independent slug here; the rest default to
their (stable) current name as key. Slug-drift auto-resolution stays in the
worklist (propose/confirm) — never guessed.
"""

from typing import Any, Dict, List, Tuple

from cjm_dev_graph_schema.identity import note_node_id
from cjm_dev_graph_schema.nodes import AssertionNode, EntityNode, FactSlotNode
from cjm_context_graph_layer.grammar import make_edge
from cjm_dev_graph_schema.vocab import DevRelations

# Known repo renames: conceptual key -> current dir name + prior names (aliases).
RENAME_ALIASES: Dict[str, Dict[str, Any]] = {
    "torch-utils": {"current": "cjm-substrate-torch-utils", "aliases": ["cjm-torch-plugin-utils"]},
    "hf-utils": {"current": "cjm-substrate-hf-utils", "aliases": ["cjm-hf-plugin-utils"]},
}
# Reverse index: any name (current or prior) -> its conceptual key.
_NAME_TO_KEY: Dict[str, str] = {}
for _k, _v in RENAME_ALIASES.items():
    _NAME_TO_KEY[_v["current"]] = _k
    for _a in _v["aliases"]:
        _NAME_TO_KEY[_a] = _k


def conceptual_key(repo_name: str) -> str:
    """The durable conceptual key for a repo (rename-aware; defaults to the name)."""
    return _NAME_TO_KEY.get(repo_name, repo_name)


def aliases_for(repo_name: str) -> List[str]:
    """Prior names that should resolve to this repo (empty unless it was renamed)."""
    key = _NAME_TO_KEY.get(repo_name)
    return list(RENAME_ALIASES[key]["aliases"]) if key else []


# The torch/hf-utils contradiction provenance (the DoD headline), per renamed lib.
_RENAME_CONTRADICTION = {
    "torch-utils": "cjm-substrate-torch-utils",
    "hf-utils": "cjm-substrate-hf-utils",
}
# "keep names" was duplicated across THREE docs historically; two are on-graph
# memory Notes (below). The third copy lives in claude-docs/substrate-overview-
# context.md, which isn't ingested yet (claude-docs ingestion is deferred), so it
# contributes no edge on-graph for now. The dedup win is still ONE claim carrying
# provenance to all its source NODES, not N duplicate claims — the third edge
# resolves for free when claude-docs are ingested.
_KEEP_SOURCES = ["substrate-foundational-picture-2026-06-03", "current-arc-status"]
_RENAME_SOURCE = "stage9-terminal-decisions"


def rename_contradiction_elements() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """The torch/hf-utils `rename-disposition` slots with BOTH claims active.

    `keep` (human, evidenced by the two on-graph source notes — the third drifting
    copy is the not-yet-ingested overview-context doc) vs `rename:<new>` (human,
    evidenced by the stage-9 decision note). No SUPERSEDES — both active — so
    `contradictions` detects the conflict; the rename-supersedes-keep clearing is
    driven through the write surface."""
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    for key, new_name in _RENAME_CONTRADICTION.items():
        subject_id = EntityNode(kind="repo", key=key, name=new_name).id
        slot = FactSlotNode(subject_id=subject_id, predicate="rename-disposition",
                            subject_label=new_name)
        keep = AssertionNode(slot_id=slot.id, value="keep", actor="human",
                             predicate="rename-disposition", subject_id=subject_id)
        rename = AssertionNode(slot_id=slot.id, value=f"rename:{new_name}", actor="human",
                               predicate="rename-disposition", subject_id=subject_id)
        nodes += [slot.to_graph_node(), keep.to_graph_node(), rename.to_graph_node()]
        edges += [slot.about_edge(), keep.on_slot_edge(), rename.on_slot_edge()]
        edges += keep.evidenced_by_edges([note_node_id(s) for s in _KEEP_SOURCES])
        edges += rename.evidenced_by_edges([note_node_id(_RENAME_SOURCE)])
    return nodes, edges


def stale_version_seed_elements() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """A `cjm-substrate` version slot seeded BEHIND the real version (oracle bumps it)."""
    subject_id = EntityNode(kind="repo", key="cjm-substrate", name="cjm-substrate").id
    slot = FactSlotNode(subject_id=subject_id, predicate="version", subject_label="cjm-substrate")
    stale = AssertionNode(slot_id=slot.id, value="0.0.1", actor="manual-seed",
                          predicate="version", subject_id=subject_id)
    return ([slot.to_graph_node(), stale.to_graph_node()],
            [slot.about_edge(), stale.on_slot_edge()])


# A small class-subject seed (membership distributed at query time). Conceptual
# keys match the repo-map keys so the membership edges resolve to real repos.
_CLASS_SUBJECTS = {
    "the-cores": {"name": "the workflow cores",
                  "members": ["cjm-transcript-correction-core",
                              "cjm-transcript-decomp-core"]},
    "the-survivor-utils": {"name": "the rename-in-place survivor utils",
                           "members": ["torch-utils", "hf-utils"]},
}


def class_subject_elements() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Class-subject entities + PART_OF-style membership edges (ABOUT member->class)."""
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    for key, spec in _CLASS_SUBJECTS.items():
        cls = EntityNode(kind="class", key=key, name=spec["name"])
        nodes.append(cls.to_graph_node())
        for member_key in spec["members"]:
            member_id = EntityNode(kind="repo", key=member_key, name=member_key).id
            edges.append(make_edge(member_id, cls.id, DevRelations.ABOUT))
    return nodes, edges


def seed_elements() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """All hand-seeded elements (rename contradiction + stale version + class subjects)."""
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    for build in (rename_contradiction_elements, stale_version_seed_elements,
                  class_subject_elements):
        n, e = build()
        nodes += n
        edges += e
    return nodes, edges
