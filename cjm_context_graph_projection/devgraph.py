"""Build the dev graph's nodes + edges from its sources (the dev-graph DRIVER).

Dev-domain-specific (this is where the general projection lib adopts the dev
schema): assemble the memory corpus (markdown -> Note nodes via the markdown
decomposer) and a repo map (one Entity per cjm-* repo + DEPENDS_ON edges read
from each pyproject) into the `(nodes, edges)` lists that extend_graph commits.

Kept separate from `projection`/`runtime` (which stay domain-neutral) so the pure
core remains extractable.
"""

import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cjm_dev_graph_schema.nodes import EntityNode
from cjm_markdown_decompose_core.extract import note_from_file
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from .seeds import aliases_for, conceptual_key, seed_elements


def memory_elements(
    memory_dir: str,  # Dir of memory markdown files
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:  # (Note nodes, REFERENCES edges)
    """Decompose every memory markdown file (except MEMORY.md) into graph elements."""
    mem = Path(memory_dir)
    files = sorted(p for p in mem.glob("*.md") if p.name != "MEMORY.md")
    notes = [note_from_file(str(p), corpus_root=str(mem)) for p in files]
    return corpus_graph_elements(notes)


def _cjm_dep_keys(pyproject: Path) -> List[str]:
    """The cjm-* dependency names from a pyproject (version specifiers stripped)."""
    try:
        data = tomllib.loads(pyproject.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return []
    deps = (data.get("project") or {}).get("dependencies") or []
    keys = []
    for d in deps:
        name = d.replace("'", "").replace('"', "").strip()
        name = name.split(">=")[0].split("==")[0].split("<")[0].split("~=")[0].split("[")[0].strip()
        if name.startswith("cjm-"):
            keys.append(name)
    return keys


def repo_map_elements(
    repos_dir: str,  # Dir holding the cjm-* repos (the active tree)
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:  # (Entity nodes, DEPENDS_ON edges)
    """One repo Entity per cjm-* repo (RENAME-STABLE keys) + DEPENDS_ON from pyproject.

    Each entity is keyed by its durable conceptual slug (name-independent), carries
    its current dir name + prior names as aliases, so a fact about a renamed repo
    keeps one home and old names still resolve. DEPENDS_ON targets resolve the
    pyproject dep name through the same conceptual-key map; an edge to a repo
    outside this tree still resolves to a stable id (the store drops it until that
    entity exists — same dangling semantics as note references)."""
    root = Path(repos_dir)
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not d.name.startswith("cjm-"):
            continue
        key = conceptual_key(d.name)
        ent = EntityNode(kind="repo", key=key, name=d.name, aliases=aliases_for(d.name),
                         properties={"path": str(d), "tier": "active"})
        nodes.append(ent.to_graph_node())
        pyproject = d / "pyproject.toml"
        if pyproject.exists():
            dep_keys = [conceptual_key(k) for k in _cjm_dep_keys(pyproject) if k != d.name]
            edges.extend(ent.depends_on_edges(dep_keys))
    return nodes, edges


def build_dev_graph_elements(
    memory_dir: str,                  # Dir of memory markdown files
    repos_dir: Optional[str] = None,  # Active cjm-* repos dir (None = skip the repo map)
    seed: bool = True,                # Include the hand-seeded fine-tier slots
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:  # (all nodes, all edges)
    """Assemble the full dev graph: memory notes (+ refs), the repo map (+ deps),
    and the hand-seeded fine-tier slots (the torch/hf contradiction, the stale
    version slot, the class subjects)."""
    nodes, edges = memory_elements(memory_dir)
    if repos_dir:
        rn, re = repo_map_elements(repos_dir)
        nodes += rn
        edges += re
    if seed:
        sn, se = seed_elements()
        nodes += sn
        edges += se
    return nodes, edges
