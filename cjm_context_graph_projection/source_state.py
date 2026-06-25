"""N+3 Phase 1 (SHADOW): capture a module's canonical source into a SOURCE journal and
validate it against the file across sessions — WITHOUT yet making it the ingest input.

The persistence flip ([[true-b-projected-structure-discussion]] N+3) inverts file→graph
source-of-truth. It is the make-or-break inversion, so it is decomposed (per the ratified
gate) into a safe SHADOW phase first: the module's source-of-truth state is journaled and
diffed against the file each session (the soak), while the FILE stays the real ingest input —
zero risk. A later, tiny cutover (Phase 2) makes the journal the actual source and the file a
generated, committed build artifact.

Two deliberate design choices (user-ratified):
- **Full-module-text granularity (v1).** A `source` op carries the module's whole canonical
  text; the existing `decompose_text` re-derives symbols/regions/bindings/USES/emit unchanged —
  so a graph-sourced module is just "the source bytes live in the journal, not in a file."
  Per-slot granularity is a planned near-term refinement.
- **A SEPARATE source stream** (NOT the private planning write-journal): `source` ops are
  PUBLIC code state (they will eventually ship per-repo with the generated files), the planning
  journal is private — don't entangle them (journal evolution seam #2: one log per domain).

`canonical_emit` is exactly what Phase 2's ingest+emit will produce for the module, so the
shadow diff faithfully predicts the cutover: `decompose_text` → the same `corpus_graph_elements`
wire dicts ingest builds → `emit_module_from_nodes(derive_imports=True)` ("graph owns
formatting", imports-as-projection).
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cjm_python_decompose_core.emit import emit_module_from_nodes
from cjm_python_decompose_core.extract import decompose_text
from cjm_python_decompose_core.ingest import corpus_graph_elements


def canonical_emit(
    repo_key: str,                       # The repo's durable conceptual slug
    module_path: str,                    # Repo-relative module path (identity input)
    path: str,                           # File path (provenance locator)
    text: str,                           # The module source text
    import_name: Optional[str] = None,   # Override the derived dotted import name
) -> str:  # The canonical .py the graph-sourced module would emit (== Phase 2 ingest+emit)
    """Decompose source text and re-emit it canonically — the exact graph→`.py` Phase 2 yields.

    Routes through the SAME `decompose_text` + `corpus_graph_elements` ingest builds, then
    `emit_module_from_nodes(derive_imports=True)` (the import block is regenerated from the
    symbols' bindings). So this is a faithful predictor of what the file would become once the
    module is graph-sourced — the basis for the shadow soak diff."""
    dm = decompose_text(repo_key, module_path, path, text, import_name=import_name)
    nodes, _edges = corpus_graph_elements([dm])
    module_node = next((n for n in nodes if n["label"] == "CodeModule"), None)
    regions = [n for n in nodes if n["label"] in ("CodeSymbol", "CodeText")]
    return emit_module_from_nodes(regions, module_node=module_node, derive_imports=True)


def read_source_journal(
    path: str,  # Source-journal file path (JSONL)
) -> List[Dict[str, Any]]:  # The recorded source ops, in append order
    """Read every `source` op (one JSON object per line; missing file = [])."""
    p = Path(path)
    if not p.exists():
        return []
    ops: List[Dict[str, Any]] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            ops.append(json.loads(line))
    return ops


def latest_source_ops(
    path: str,  # Source-journal file path (JSONL)
) -> Dict[Tuple[str, str], Dict[str, Any]]:  # (repo_key, module_path) -> latest op args
    """The LATEST source state per module (last write wins — the 'journal STATE, not diff'
    semantics: re-flipping a module supersedes its prior text, replay-idempotent)."""
    latest: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for op in read_source_journal(path):
        a = op.get("args", {})
        latest[(a.get("repo_key"), a.get("module_path"))] = a
    return latest


def append_source(
    path: str,             # Source-journal file path (JSONL)
    repo_key: str,         # The repo's durable conceptual slug
    module_path: str,      # Repo-relative module path
    import_name: str,      # Dotted import name
    text: str,             # The module's canonical source text (the journaled STATE)
) -> bool:  # True if appended, False if identical to the current latest state (no-op)
    """Append a `source` op, skipping a write identical to the module's current latest state."""
    cur = latest_source_ops(path).get((repo_key, module_path))
    if cur is not None and cur.get("text") == text:
        return False
    record = {"verb": "source", "ts": time.time(),
              "args": {"repo_key": repo_key, "module_path": module_path,
                       "import_name": import_name, "text": text}}
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    return True


def _resolve_path(repos_dir: str, repo_key: str, module_path: str) -> str:
    """The file path for a (repo, module) — repos_dir/<repo_key>/<module_path>."""
    return str(Path(repos_dir) / repo_key / module_path)


def flip_module(
    source_journal_path: str,            # Where to record the source op (a SEPARATE stream)
    repos_dir: str,                      # The repos root (to read the current file)
    repo_key: str,                       # The repo's durable conceptual slug
    module_path: str,                    # Repo-relative module path to flip into shadow source
    import_name: Optional[str] = None,   # Override the derived dotted import name
) -> Dict[str, Any]:  # The flip result (captured?, whether the file is already canonical, or error)
    """Capture a module's CANONICAL source into the shadow source journal (Phase 1).

    Reads the current file, computes the canonical emit (what the graph-sourced module would
    produce), and records it as the module's source state. The file remains the real ingest
    input — this only starts the shadow soak. Reports whether the file is ALREADY canonical
    (byte-exact) or whether the flip implies a one-time canonicalization (e.g. a reordered
    import block) that Phase 2's first emit would apply."""
    file_path = _resolve_path(repos_dir, repo_key, module_path)
    if not Path(file_path).exists():
        return {"error": f"no file at {file_path}", "captured": False}
    file_text = Path(file_path).read_text()
    try:
        canonical = canonical_emit(repo_key, module_path, file_path, file_text, import_name)
    except SyntaxError as e:
        return {"error": f"cannot decompose {module_path}: {e}", "captured": False}
    imp = import_name or (module_path[:-3] if module_path.endswith(".py") else module_path).replace("/", ".")
    appended = append_source(source_journal_path, repo_key, module_path, imp, canonical)
    return {"repo_key": repo_key, "module_path": module_path, "import_name": imp,
            "file_path": file_path, "captured": appended,
            "file_already_canonical": canonical == file_text,
            "canonical_bytes": len(canonical.encode("utf-8")),
            "note": "SHADOW: the file is still the ingest source; run source-check each session "
                    "to soak before the Phase 2 cutover"}


def source_check(
    source_journal_path: str,  # The shadow source journal
    repos_dir: str,            # The repos root (to read the current files for the membrane diff)
) -> Dict[str, Any]:  # Per-module soak status (the cross-session validation instrument)
    """The soak instrument: for each shadow-sourced module, check two things.

    - **Membrane (file drift):** does the current file still equal the journaled source state?
      A mismatch means the file was edited OUT-OF-BAND (an agent / `ruff` / a human) — surfaced,
      never silently overridden (the reconcile leg can absorb it into a new source op).
    - **Round-trip fixpoint:** re-decomposing+emitting the journaled text reproduces it exactly
      (the graph-sourced emit is stable). A clean soak = both true across several sessions, the
      gate to the Phase 2 cutover."""
    modules = []
    drifted = stable = 0
    for (repo_key, module_path), a in sorted(latest_source_ops(source_journal_path).items()):
        journaled = a.get("text", "")
        file_path = _resolve_path(repos_dir, repo_key, module_path)
        file_text = Path(file_path).read_text() if Path(file_path).exists() else None
        file_matches = file_text == journaled
        try:
            reemit = canonical_emit(repo_key, module_path, file_path, journaled, a.get("import_name"))
            fixpoint = reemit == journaled
        except SyntaxError:
            fixpoint = False
        drifted += 0 if file_matches else 1
        stable += 1 if fixpoint else 0
        modules.append({"module": a.get("import_name") or module_path, "repo_key": repo_key,
                        "file_present": file_text is not None,
                        "file_matches_source": file_matches, "roundtrip_fixpoint": fixpoint})
    return {"modules": modules, "count": len(modules),
            "file_drift": drifted, "roundtrip_stable": stable,
            "clean": drifted == 0 and stable == len(modules) and len(modules) > 0}
