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

**Notebook modules** (the nbdev transition window, [[off-nbdev-endpoint]]): a `module_path`
ending in `.ipynb` dispatches every verb to the CELL substrate — the journaled state is the
canonical `.ipynb` text (`render_notebook(parse_notebook(text))`: verbatim cell sources,
outputs/metadata stripped — outputs are derived, regenerated on demand). The notebook is the
TRANSITIONAL source shape; the exported `.py` stays nbdev-export's during the window and the
whole nbdev surface retires at the post-transition transformation.
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cjm_notebook_decompose_core.project import render_notebook
from cjm_notebook_decompose_core.read import parse_notebook
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


def canonical_emit_notebook(
    text: str,  # The `.ipynb` file text (JSON)
) -> str:  # The canonical notebook the graph-sourced module would emit
    """The notebook analogue of `canonical_emit`: parse to cells, re-render canonically.

    Cell sources round-trip verbatim (verbatim-first at the cell level); outputs,
    execution counts, and metadata are STRIPPED — they are derived state, regenerated
    on demand, never part of the journaled source. Raises `json.JSONDecodeError`
    (a `ValueError`) on malformed notebook JSON."""
    return render_notebook(parse_notebook(text).cells)


def _is_notebook(module_path: str) -> bool:
    """Whether a source-state module is notebook-sourced (dispatch key for every verb)."""
    return module_path.endswith(".ipynb")


def _canonical(
    repo_key: str,                       # The repo's durable conceptual slug
    module_path: str,                    # Repo-relative source path (`.py` or `.ipynb`)
    path: str,                           # File path (provenance locator)
    text: str,                           # The module/notebook source text
    import_name: Optional[str] = None,   # Override the derived dotted import name (`.py` only)
) -> str:  # The canonical source the graph would emit for this module
    """Dispatch canonical emit on the source kind (`.ipynb` → cell substrate, else `.py`)."""
    if _is_notebook(module_path):
        return canonical_emit_notebook(text)
    return canonical_emit(repo_key, module_path, path, text, import_name)


def _derive_import_name(
    repo_key: str,     # The repo's durable conceptual slug (assumed == its dir/package stem)
    module_path: str,  # Repo-relative source path
    text: str,         # The source text (a notebook's `#| default_exp` names its export target)
) -> str:  # The dotted import name of what this source builds ("" if it exports nothing)
    """Derive the import name a source state maps to. A `.py` path converts directly; a
    notebook maps to its nbdev export target (`<package>.<default_exp>`), or "" for a
    non-exporting notebook."""
    if _is_notebook(module_path):
        exp = parse_notebook(text).default_exp
        return f"{repo_key.replace('-', '_')}.{exp}" if exp else ""
    return (module_path[:-3] if module_path.endswith(".py") else module_path).replace("/", ".")


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
        if op.get("verb") != "source":
            continue  # `cutover` ops carry no text — they flag phase, not state
        a = op.get("args", {})
        latest[(a.get("repo_key"), a.get("module_path"))] = a
    return latest


def graph_sourced_modules(
    path: str,  # Source-journal file path (JSONL)
) -> set:  # {(repo_key, module_path)} of modules PAST the Phase-2 cutover
    """The modules whose ingest source IS the journal (a `cutover` op exists for them).

    Phase distinction: a module with only `source` ops is in SHADOW (the file is still
    the ingest input, the journal soaks); a `cutover` op flips it — the journal becomes
    the source of truth and the file a generated, committed artifact."""
    flipped = set()
    for op in read_source_journal(path):
        if op.get("verb") == "cutover":
            a = op.get("args", {})
            flipped.add((a.get("repo_key"), a.get("module_path")))
    return flipped


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
        canonical = _canonical(repo_key, module_path, file_path, file_text, import_name)
    except (SyntaxError, ValueError) as e:
        return {"error": f"cannot decompose {module_path}: {e}", "captured": False}
    imp = import_name or _derive_import_name(repo_key, module_path, canonical)
    appended = append_source(source_journal_path, repo_key, module_path, imp, canonical)
    sourced = (repo_key, module_path) in graph_sourced_modules(source_journal_path)
    note = ("GRAPH-SOURCED: absorbed into the source journal (this module's source of truth); "
            "if the file was not already canonical, regenerate it via emit-artifact"
            if sourced else
            "SHADOW: the file is still the ingest source; run source-check each session "
            "to soak before the Phase 2 cutover")
    return {"repo_key": repo_key, "module_path": module_path, "import_name": imp,
            "file_path": file_path, "captured": appended, "graph_sourced": sourced,
            "file_already_canonical": canonical == file_text,
            "canonical_bytes": len(canonical.encode("utf-8")),
            "note": note}


def cutover_module(
    source_journal_path: str,  # The source journal (holds the module's shadow state)
    repos_dir: str,            # The repos root (to verify/write the file artifact)
    repo_key: str,             # The repo's durable conceptual slug
    module_path: str,          # Repo-relative module path to cut over
) -> Dict[str, Any]:  # The cutover result (or the guard that refused it)
    """Phase 2: make the JOURNAL the module's source of truth (the persistence flip).

    Guarded — refuses unless the shadow is provably clean RIGHT NOW: a journaled source
    state exists, it is a round-trip fixpoint, and the file byte-equals it (a drifted
    file means an out-of-band edit the reconcile leg must absorb first via `flip-module`).
    A MISSING file is not drift: it is (re)written from the journal — post-cutover the
    file is a generated, committed artifact, and this is its first emit. On success a
    `cutover` op is appended; `ingest` then reads this module's text from the journal
    and `source-check` holds the file to the regen gate."""
    a = latest_source_ops(source_journal_path).get((repo_key, module_path))
    if a is None:
        return {"error": f"no journaled source state for {repo_key}/{module_path} — "
                         "run flip-module (shadow) first", "cut_over": False}
    if (repo_key, module_path) in graph_sourced_modules(source_journal_path):
        return {"repo_key": repo_key, "module_path": module_path, "cut_over": False,
                "already_graph_sourced": True,
                "note": "already past the cutover — the journal is this module's source"}
    journaled = a.get("text", "")
    file_path = _resolve_path(repos_dir, repo_key, module_path)
    try:
        if _canonical(repo_key, module_path, file_path, journaled, a.get("import_name")) != journaled:
            return {"error": f"journaled source for {repo_key}/{module_path} is not a "
                             "round-trip fixpoint — re-flip before cutting over", "cut_over": False}
    except (SyntaxError, ValueError) as e:
        return {"error": f"journaled source for {repo_key}/{module_path} does not parse: {e}",
                "cut_over": False}
    artifact_written = False
    if Path(file_path).exists():
        if Path(file_path).read_text() != journaled:
            return {"error": f"file drifted from the journaled state for {repo_key}/{module_path} "
                             "— absorb it (flip-module) or regenerate it (emit-artifact) first",
                    "cut_over": False}
    else:
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        Path(file_path).write_text(journaled)
        artifact_written = True
    record = {"verb": "cutover", "ts": time.time(),
              "args": {"repo_key": repo_key, "module_path": module_path}}
    with Path(source_journal_path).open("a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    return {"repo_key": repo_key, "module_path": module_path,
            "import_name": a.get("import_name"), "file_path": file_path,
            "cut_over": True, "artifact_written": artifact_written,
            "note": "GRAPH-SOURCED: the journal is now this module's source of truth; the file "
                    "is a generated committed artifact — source-check gates its regen"}


def emit_source_artifact(
    source_journal_path: str,  # The source journal (the authoritative text)
    repos_dir: str,            # The repos root (where the artifact file lives)
    repo_key: str,             # The repo's durable conceptual slug
    module_path: str,          # Repo-relative module path
    write: bool = True,        # False = report what would change without touching disk
) -> Dict[str, Any]:  # The regen result
    """(Re)generate a module's file artifact from its journaled source (the recovery /
    post-authoring emit). The journal is authoritative — this OVERWRITES the file; to
    keep an out-of-band file edit instead, absorb it with `flip-module`."""
    a = latest_source_ops(source_journal_path).get((repo_key, module_path))
    if a is None:
        return {"error": f"no journaled source state for {repo_key}/{module_path}", "written": False}
    journaled = a.get("text", "")
    file_path = _resolve_path(repos_dir, repo_key, module_path)
    existing = Path(file_path).read_text() if Path(file_path).exists() else None
    changed = existing != journaled
    if write and changed:
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        Path(file_path).write_text(journaled)
    return {"repo_key": repo_key, "module_path": module_path, "file_path": file_path,
            "changed": changed, "written": write and changed,
            "artifact_bytes": len(journaled.encode("utf-8"))}


def absorb_authored_text(
    source_journal_path: str,  # The source journal (the authoritative stream)
    repo_key: str,             # The repo's durable conceptual slug
    module_path: str,          # Repo-relative module path
    file_path: str,            # The artifact file the author verb just wrote
    emitted_text: str,         # The text the author verb emitted
    import_name: Optional[str] = None,  # Override the derived dotted import name
) -> Dict[str, Any]:  # The absorb result
    """Absorb an `author` edit of a GRAPH-SOURCED module into the source journal.

    The author verb emits verbatim (its import block is whatever the graph carried);
    the journaled state is CANONICAL (imports-as-projection). So: canonicalize the
    emitted text, journal it as the module's new source state, and — if
    canonicalization changed anything (e.g. a now-dead import pruned) — rewrite the
    file so artifact == journal and the regen gate stays clean by construction."""
    try:
        canonical = _canonical(repo_key, module_path, file_path, emitted_text, import_name)
    except (SyntaxError, ValueError) as e:
        return {"error": f"authored text for {repo_key}/{module_path} does not parse: {e}",
                "absorbed": False}
    imp = import_name or _derive_import_name(repo_key, module_path, canonical)
    appended = append_source(source_journal_path, repo_key, module_path, imp, canonical)
    canonicalized = canonical != emitted_text
    if canonicalized:
        Path(file_path).write_text(canonical)
    return {"repo_key": repo_key, "module_path": module_path, "absorbed": appended,
            "canonicalized": canonicalized,
            "note": "authored state journaled (the journal is this module's source)"}


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
      gate to the Phase 2 cutover.

    Post-cutover (GRAPH-SOURCED) modules are held to the same two checks with inverted
    meaning: the journal is the source, so a file mismatch is a stale/dirtied ARTIFACT
    (the regen gate — nbdev-style `emit(graph)==file`), absorbed via `flip-module` or
    regenerated via `emit-artifact`. `regen_clean` reports that gate alone."""
    flipped = graph_sourced_modules(source_journal_path)
    modules = []
    drifted = stable = 0
    regen_clean = True
    for (repo_key, module_path), a in sorted(latest_source_ops(source_journal_path).items()):
        journaled = a.get("text", "")
        file_path = _resolve_path(repos_dir, repo_key, module_path)
        file_text = Path(file_path).read_text() if Path(file_path).exists() else None
        file_matches = file_text == journaled
        try:
            reemit = _canonical(repo_key, module_path, file_path, journaled, a.get("import_name"))
            fixpoint = reemit == journaled
        except (SyntaxError, ValueError):
            fixpoint = False
        graph_sourced = (repo_key, module_path) in flipped
        if graph_sourced and not (file_matches and fixpoint):
            regen_clean = False
        drifted += 0 if file_matches else 1
        stable += 1 if fixpoint else 0
        modules.append({"module": a.get("import_name") or module_path, "repo_key": repo_key,
                        "graph_sourced": graph_sourced,
                        "file_present": file_text is not None,
                        "file_matches_source": file_matches, "roundtrip_fixpoint": fixpoint})
    return {"modules": modules, "count": len(modules),
            "graph_sourced_count": sum(1 for m in modules if m["graph_sourced"]),
            "file_drift": drifted, "roundtrip_stable": stable,
            "regen_clean": regen_clean,
            "clean": drifted == 0 and stable == len(modules) and len(modules) > 0}
