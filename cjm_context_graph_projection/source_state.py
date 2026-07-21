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

**Retire ops** (the golden-reference flip, [[substrate-golden-reference]]): a `retire` op
ends a module key's life in the journal — `latest_source_ops` / `graph_sourced_modules`
drop the key, so `source-check` stops holding a DELETED file (the flipped-away `.ipynb`)
to the membrane forever. Ops are processed in append order, so a later `source` op on the
same key would revive it (generic supersession, not a special case).
"""

import ast
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cjm_context_graph_primitives.journal import journal_segments, maybe_rotate
from cjm_notebook_decompose_core.project import render_notebook
from cjm_notebook_decompose_core.read import parse_notebook
from cjm_python_decompose_core.emit import emit_module_from_nodes
from cjm_python_decompose_core.extract import decompose_text
from cjm_python_decompose_core.ingest import corpus_graph_elements


def is_test_module_path(module_path: str) -> bool:
    """Whether a module path denotes TEST source (`tests/` or `tests_manual/`).

    Test modules keep their import block VERBATIM — never derived from ref bindings.
    pytest wires fixtures by parameter-name / string match (`usefixtures("db")`,
    `indirect=`, `getfixturevalue`) that no AST ref walk can see, and script-shaped
    manual tests nest closures inside `try`/`with` blocks the symbol walk doesn't
    extract — both channels FALSE-PRUNE a needed import under imports-as-projection.
    Verbatim is the only canonicalization where fixture imports PROVABLY survive."""
    return module_path.startswith(("tests/", "tests_manual/"))


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
    module is graph-sourced — the basis for the shadow soak diff. TEST modules are the
    exception: their import block stays verbatim (see `is_test_module_path`)."""
    dm = decompose_text(repo_key, module_path, path, text, import_name=import_name)
    nodes, _edges = corpus_graph_elements([dm])
    module_node = next((n for n in nodes if n["label"] == "CodeModule"), None)
    regions = [n for n in nodes if n["label"] in ("CodeSymbol", "CodeText")]
    return emit_module_from_nodes(regions, module_node=module_node,
                                  derive_imports=not is_test_module_path(module_path))


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


# An nbdev directive line (`#| export`, `#| eval: false`, ...) — stripped ANYWHERE in a
# cell, not just the leading block (nbdev-export drops them all; a mid-cell directive
# like worker.ipynb's `#| eval: false` is real).
_DIRECTIVE_LINE = re.compile(r"^\s*#\|")


def _first_line(source: str) -> str:
    """A cell's first non-blank line (the loud-report handle for a dropped cell)."""
    for line in source.splitlines():
        if line.strip():
            return line.strip()
    return ""


def notebook_to_py_source(
    nb_text: str,                     # The notebook's journaled `.ipynb` text (JSON)
    docstring: Optional[str] = None,  # Module docstring (the prose-triage fold), verbatim
) -> Dict[str, Any]:  # {text, default_exp, export_cells, markdown_cells, nonexport_code_cells, dropped_all_dunder}
    """Build a plain-`.py` module source from a notebook's EXPORT cells (the flip transform).

    The arc-lib shape, derived from the journaled notebook alone (no nbdev-export
    dependence): export/exporti cells' verbatim sources joined by blank lines, every
    `#|` directive line stripped, top-level `__all__` assignments DROPPED (they exist
    only as nbdev star-import scar-repair; the arc-lib shape carries no `__all__`).
    Everything not kept is REPORTED, never silently discarded — markdown cells (prose
    triage disposes of them BEFORE the flip) and non-export code cells (their test
    content must be projected to `tests/` BEFORE the flip) come back as loud lists.
    The result is RAW (pre-canonical): the caller runs `canonical_emit` over it.
    Raises `json.JSONDecodeError` on malformed notebook JSON, `SyntaxError` if the
    assembled module doesn't parse."""
    parsed = parse_notebook(nb_text)
    chunks: List[str] = []
    markdown_cells: List[Dict[str, Any]] = []
    nonexport_code: List[Dict[str, Any]] = []
    for c in parsed.cells:
        if c.cell_type != "code":
            markdown_cells.append({"index": c.index, "cell_key": c.cell_key,
                                   "first_line": _first_line(c.source)})
            continue
        body = "\n".join(l for l in c.source.splitlines()
                         if not _DIRECTIVE_LINE.match(l)).strip("\n")
        if not body.strip():
            continue  # directive-only cell (e.g. the `#| default_exp` header)
        if c.is_export:
            chunks.append(body)
        else:
            nonexport_code.append({"index": c.index, "cell_key": c.cell_key,
                                   "first_line": _first_line(body)})
    text = "\n\n".join(chunks) + "\n" if chunks else ""
    dropped_all = 0
    if text:
        lines = text.splitlines(keepends=True)
        drop: set = set()
        for node in ast.parse(text).body:
            if (isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "__all__"
                            for t in node.targets)):
                drop.update(range(node.lineno - 1, node.end_lineno))
                dropped_all += 1
        if drop:
            text = "".join(l for i, l in enumerate(lines) if i not in drop)
    if docstring:
        text = f'"""{docstring}"""\n\n' + text
    return {"text": text, "default_exp": parsed.default_exp,
            "export_cells": len(chunks), "markdown_cells": markdown_cells,
            "nonexport_code_cells": nonexport_code, "dropped_all_dunder": dropped_all}


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
    """Read every `source` op across the rotated SEGMENT FAMILY (one JSON object per
    line; missing files = []) — cold segments first, live tail last: append order."""
    ops: List[Dict[str, Any]] = []
    for seg in journal_segments(path):
        for line in Path(seg).read_text().splitlines():
            line = line.strip()
            if line:
                ops.append(json.loads(line))
    return ops


def latest_source_ops(
    path: str,  # Source-journal file path (JSONL)
) -> Dict[Tuple[str, str], Dict[str, Any]]:  # (repo_key, module_path) -> latest op args
    """The LATEST source state per module (last write wins — the 'journal STATE, not diff'
    semantics: re-flipping a module supersedes its prior text, replay-idempotent).
    A `retire` op ends the key (dropped from the map) until a later `source` revives it."""
    latest: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for op in read_source_journal(path):
        a = op.get("args", {})
        key = (a.get("repo_key"), a.get("module_path"))
        if op.get("verb") == "source":
            latest[key] = a
        elif op.get("verb") == "retire":
            latest.pop(key, None)
        # `cutover` ops carry no text — they flag phase, not state
    return latest


def graph_sourced_modules(
    path: str,  # Source-journal file path (JSONL)
) -> set:  # {(repo_key, module_path)} of modules PAST the Phase-2 cutover
    """The modules whose ingest source IS the journal (a `cutover` op exists for them).

    Phase distinction: a module with only `source` ops is in SHADOW (the file is still
    the ingest input, the journal soaks); a `cutover` op flips it — the journal becomes
    the source of truth and the file a generated, committed artifact. A `retire` op
    removes the key (its content lives on under a successor key, e.g. `.ipynb` -> `.py`)."""
    flipped = set()
    for op in read_source_journal(path):
        a = op.get("args", {})
        key = (a.get("repo_key"), a.get("module_path"))
        if op.get("verb") == "cutover":
            flipped.add(key)
        elif op.get("verb") == "retire":
            flipped.discard(key)
    return flipped


def append_retire(
    path: str,                            # Source-journal file path (JSONL)
    repo_key: str,                        # The repo's durable conceptual slug
    module_path: str,                     # The module key to END (e.g. the flipped-away .ipynb)
    superseded_by: Optional[str] = None,  # The successor key carrying the content (audit trail)
    op_meta: Optional[Dict[str, Any]] = None,  # Replay-ignored op provenance ({'op': 'delete-module', ...})
) -> bool:  # True if appended, False if the key is not currently live (no-op)
    """Append a `retire` op ending a module key's journal life.

    The re-key half of the notebook->py flip AND the delete/rename verbs' event: the old
    key would otherwise sit in `latest_source_ops` forever, holding source-check to a
    deleted file. `superseded_by` is audit-only (replay keys off repo_key+module_path);
    `generation`/`op` ride the record as replay-ignored envelope (see `append_source`)."""
    if (repo_key, module_path) not in latest_source_ops(path):
        return False
    record = _stamp_session({"verb": "retire", "ts": time.time(), "generation": 1,
                             "args": {"repo_key": repo_key, "module_path": module_path,
                                      "superseded_by": superseded_by}})
    if op_meta:
        record["op"] = op_meta
    with Path(path).open("a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    maybe_rotate(path)
    return True


def append_source(
    path: str,             # Source-journal file path (JSONL)
    repo_key: str,         # The repo's durable conceptual slug
    module_path: str,      # Repo-relative module path
    import_name: str,      # Dotted import name
    text: str,             # The module's canonical source text (the journaled STATE)
    op_meta: Optional[Dict[str, Any]] = None,  # Replay-ignored op provenance ({'op': 'move', ...})
) -> bool:  # True if appended, False if identical to the current latest state (no-op)
    """Append a `source` op, skipping a write identical to the module's current latest state.

    New records carry `generation` (1 = module-snapshot grain; replay will UPCAST older
    generations when the symbol-granular endpoint lands — DEC 6ee4b4f2 pillar 2) and,
    when given, an `op` envelope naming the mutating verb that produced the state — pure
    provenance for the session lens / orphaned-edges remap, NEVER replay input (replay is
    STATE-based, verb+args only; intent replay would bake verb bugs into history)."""
    cur = latest_source_ops(path).get((repo_key, module_path))
    if cur is not None and cur.get("text") == text:
        return False
    record = _stamp_session({"verb": "source", "ts": time.time(), "generation": 1,
                             "args": {"repo_key": repo_key, "module_path": module_path,
                                      "import_name": import_name, "text": text}})
    if op_meta:
        record["op"] = op_meta
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    maybe_rotate(path)
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
    write: bool = True,                  # False = preview the capture, journal nothing
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
    appended = (append_source(source_journal_path, repo_key, module_path, imp, canonical)
                if write else False)
    sourced = (repo_key, module_path) in graph_sourced_modules(source_journal_path)
    note = ("GRAPH-SOURCED: absorbed into the source journal (this module's source of truth); "
            "if the file was not already canonical, regenerate it via emit-artifact"
            if sourced else
            "SHADOW: the file is still the ingest source; run source-check each session "
            "to soak before the Phase 2 cutover")
    if not write:
        note = "PREVIEW (--no-write): nothing journaled — a real flip would capture this state"
    return {"repo_key": repo_key, "module_path": module_path, "import_name": imp,
            "file_path": file_path, "captured": appended, "previewed": not write,
            "graph_sourced": sourced,
            "file_already_canonical": canonical == file_text,
            "canonical_bytes": len(canonical.encode("utf-8")),
            "note": note}


def cutover_module(
    source_journal_path: str,  # The source journal (holds the module's shadow state)
    repos_dir: str,            # The repos root (to verify/write the file artifact)
    repo_key: str,             # The repo's durable conceptual slug
    module_path: str,          # Repo-relative module path to cut over
    write: bool = True,        # False = run every guard, flip nothing (the preview)
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
    artifact_missing = not Path(file_path).exists()
    if not artifact_missing and Path(file_path).read_text() != journaled:
        return {"error": f"file drifted from the journaled state for {repo_key}/{module_path} "
                         "— absorb it (flip-module) or regenerate it (emit-artifact) first",
                "cut_over": False}
    if not write:
        return {"repo_key": repo_key, "module_path": module_path,
                "import_name": a.get("import_name"), "file_path": file_path,
                "cut_over": False, "previewed": True, "artifact_written": False,
                "note": "PREVIEW (--no-write): every guard passes — a real cutover would make "
                        "the journal this module's source of truth"
                        + (" and write its first artifact" if artifact_missing else "")}
    # Journal-first (the seam discipline): the cutover event lands BEFORE the missing
    # artifact is regenerated — a crash between the two leaves the journal ahead of the
    # files, the recoverable direction (emit-artifact regenerates).
    _append_cutover(source_journal_path, repo_key, module_path)
    artifact_written = False
    if artifact_missing:
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        Path(file_path).write_text(journaled)
        artifact_written = True
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


def _stamp_session(record: Dict[str, Any]) -> Dict[str, Any]:
    """Stamp the active session key (`CJM_SESSION`) on a source-journal record.

    The source-journal twin of `journal.current_session` stamping (DEC 6124d8bf) —
    kept LOCAL because this module is deliberately a LEAF (authoring imports FROM
    it; a `.journal` import here would cycle). Stamped top-level so source replay
    and latest-state folding stay session-blind; the session lens reads it."""
    session = os.environ.get("CJM_SESSION") or None
    if session:
        record["session"] = session
    return record


def _append_cutover(
    path: str,         # Source-journal file path (JSONL)
    repo_key: str,     # The repo's durable conceptual slug
    module_path: str,  # The module key crossing the Phase-2 boundary
    op_meta: Optional[Dict[str, Any]] = None,  # Replay-ignored op provenance
) -> None:
    """Append the raw `cutover` record (generation-tagged) — ONE grammar site per event.

    The GUARDED path is `cutover_module` (shadow-clean checks); this is the shared
    append it and `journaled_emit` route through when the caller has already validated
    (e.g. flip-to-py births a `.py` graph-sourced from a state it just canonicalized)."""
    record = _stamp_session({"verb": "cutover", "ts": time.time(), "generation": 1,
                             "args": {"repo_key": repo_key, "module_path": module_path}})
    if op_meta:
        record["op"] = op_meta
    with Path(path).open("a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    maybe_rotate(path)


def append_register(
    path: str,                        # Source-journal file path (JSONL)
    repo_key: str,                    # The repo's durable conceptual slug
    repo_root: Optional[str] = None,  # Absolute repo root on disk (the rebuild ingest anchor)
    source_kind: str = "code",        # Ingest substrate: "code" (.py) or "notebook"
) -> bool:  # True if appended, False if the repo's latest register is identical (no-op)
    """Append a `register` event — repo inventory as JOURNAL DATA (DEC c47912f6).

    The forward-compat half of retiring the hardcoded DEFAULT_CODE_LIBS/NOTEBOOK_LIBS
    tuples (finding a7bc1424: a repo outside them silently drops out of rebuilds):
    `new-module` auto-registers its repo so the inventory event exists from the repo's
    FIRST on-graph write. Rebuild CONSUMPTION + the m3-baseline backfill ride 640bc713,
    not this seam. Latest-wins per repo_key; an identical re-register is skipped
    (idempotent retries as contract)."""
    latest: Optional[Dict[str, Any]] = None
    for rec in read_source_journal(path):
        if rec.get("verb") == "register" and rec.get("args", {}).get("repo_key") == repo_key:
            latest = rec.get("args")
    args = {"repo_key": repo_key, "repo_root": repo_root, "source_kind": source_kind}
    if latest == args:
        return False
    record = _stamp_session({"verb": "register", "ts": time.time(), "generation": 1,
                             "args": args})
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    maybe_rotate(path)
    return True


def journaled_emit(
    source_journal_path: Optional[str],  # The source journal — REQUIRED for any real write
    *,
    emissions: Optional[List[Dict[str, Any]]] = None,  # [{repo_key, module_path, import_name, text, path, cutover?}] — JOURNAL-space keys
    retires: Optional[List[Dict[str, Any]]] = None,    # [{repo_key, module_path, superseded_by?}]
    deletes: Optional[List[str]] = None,               # File paths to unlink AFTER events land
    registers: Optional[List[Dict[str, Any]]] = None,  # [{repo_key, repo_root?, source_kind?}]
    op: Optional[Dict[str, Any]] = None,               # Op provenance ({'op': 'move', ...}); replay-ignored
    write: bool = True,                                # False = full preview, ZERO side effects
) -> Dict[str, Any]:  # The uniform receipt every mutating verb carries (or {'error': ...})
    """The ops seam (pillar 1 of DEC 6ee4b4f2): events BEFORE files — THE file-write path.

    Three strict phases: (1) validate + canonicalize the WHOLE batch (zero side effects —
    one canonicalization failure refuses everything); (2) append ALL journal events
    (source / cutover / retire / register, generation-tagged, `op` provenance riding
    replay-ignored); (3) only then touch disk. A crash between (2) and (3) leaves the
    journal AHEAD of the files — the recoverable direction (`emit-artifact` regenerates);
    files ahead of the journal is the rebuild-revert class (finding ff8522fa) this seam
    makes impossible by construction. An emission whose (repo_key, module_path) has NO
    live journal key is written as a plain file and reported LOUDLY in
    `unjournaled_files` (auto-capturing would flip phase discipline silently —
    `flip-module` is the deliberate capture verb); write=True with events to land and no
    journal path REFUSES outright. Journaled emissions land their CANONICAL text on disk,
    so file == journal by construction (the absorb-rewrite folded in)."""
    emissions, retires = emissions or [], retires or []
    deletes, registers = deletes or [], registers or []
    receipt: Dict[str, Any] = {"op": (op or {}).get("op"), "journal_first": True,
                               "written": False, "events": [], "files_written": [],
                               "files_deleted": [], "unjournaled_files": [],
                               "canonicalized": []}
    if write and not source_journal_path and (emissions or retires or registers):
        return {"error": "journaled_emit refuses: no source-journal path — journal-first "
                         "means events land before files (pass source_journal_path; "
                         "cg-write bakes it)", **receipt}
    latest = latest_source_ops(source_journal_path) if source_journal_path else {}
    sourced = graph_sourced_modules(source_journal_path) if source_journal_path else set()
    # Phase 1: validate/canonicalize everything — no side effects yet.
    plans: List[Dict[str, Any]] = []
    for e in emissions:
        key = (e["repo_key"], e["module_path"])
        journaled = key in latest or bool(e.get("cutover"))
        canonical = e["text"]
        if journaled:
            try:
                canonical = _canonical(e["repo_key"], e["module_path"],
                                       e.get("path") or "", e["text"], e.get("import_name"))
            except (SyntaxError, ValueError) as err:
                return {"error": f"canonical emit failed for {e['repo_key']}/"
                                 f"{e['module_path']}: {err} — NOTHING journaled or written",
                        **receipt}
        plans.append({**e, "_journaled": journaled, "_canonical": canonical})
    # Phase 2: events (journal leads).
    for r in registers:
        appended = write and append_register(source_journal_path, r["repo_key"],
                                             repo_root=r.get("repo_root"),
                                             source_kind=r.get("source_kind", "code"))
        receipt["events"].append({"verb": "register", "repo_key": r["repo_key"],
                                  "appended": bool(appended)})
    for p in plans:
        if not p["_journaled"]:
            continue
        appended = write and append_source(source_journal_path, p["repo_key"],
                                           p["module_path"], p.get("import_name") or "",
                                           p["_canonical"], op_meta=op)
        receipt["events"].append({"verb": "source", "repo_key": p["repo_key"],
                                  "module_path": p["module_path"], "appended": bool(appended)})
        if p.get("cutover") and (p["repo_key"], p["module_path"]) not in sourced:
            if write:
                _append_cutover(source_journal_path, p["repo_key"], p["module_path"],
                                op_meta=op)
            receipt["events"].append({"verb": "cutover", "repo_key": p["repo_key"],
                                      "module_path": p["module_path"], "appended": write})
    for r in retires:
        appended = write and append_retire(source_journal_path, r["repo_key"],
                                           r["module_path"],
                                           superseded_by=r.get("superseded_by"), op_meta=op)
        receipt["events"].append({"verb": "retire", "repo_key": r["repo_key"],
                                  "module_path": r["module_path"], "appended": bool(appended)})
    # Phase 3: files (only now — the journal can never trail the disk).
    for p in plans:
        path = p.get("path")
        if not path:
            continue
        out_text = p["_canonical"] if p["_journaled"] else p["text"]
        if p["_journaled"] and out_text != p["text"]:
            receipt["canonicalized"].append(p["module_path"])
        if not p["_journaled"]:
            receipt["unjournaled_files"].append(path)
        if write:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(out_text)
        receipt["files_written"].append(path)
    for d in deletes:
        if not d:
            continue
        if write and Path(d).exists():
            Path(d).unlink()
        receipt["files_deleted"].append(d)
    receipt["written"] = write
    return receipt
