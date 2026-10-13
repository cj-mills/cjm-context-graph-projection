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
import difflib
import json
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Set, Tuple

from cjm_context_graph_primitives.journal import (journal_segments, maybe_rotate, op_clock, op_now,
                                                  read_segment)
from cjm_dev_graph_schema.identity import code_module_node_id, code_symbol_node_id
from cjm_notebook_decompose_core.project import render_notebook
from cjm_notebook_decompose_core.read import parse_notebook
from cjm_python_decompose_core.emit import emit_module_from_nodes
from cjm_python_decompose_core.extract import decompose_text
from cjm_python_decompose_core.ingest import corpus_graph_elements
from cjm_python_decompose_core.parse import source_lines

from .codesource import (absorb_records, CodeSourceError, element_canonical, ELEMENT_RETIRE,
                         SourceState)


def is_test_module_path(module_path: str) -> bool:
    """Whether a module path denotes TEST source (`tests/` or `tests_manual/`).

    Test modules keep their import block VERBATIM — never derived from ref bindings.
    pytest wires fixtures by parameter-name / string match (`usefixtures("db")`,
    `indirect=`, `getfixturevalue`) that no AST ref walk can see, and script-shaped
    manual tests nest closures inside `try`/`with` blocks the symbol walk doesn't
    extract — both channels FALSE-PRUNE a needed import under imports-as-projection.
    Verbatim is the only canonicalization where fixture imports PROVABLY survive."""
    return module_path.startswith(("tests/", "tests_manual/"))


def is_package_module_path(module_path: str) -> bool:
    """Whether a module path belongs to the PACKAGE phase of the code lane: every module that is
    not test source -- the packages and the scripts beside them (design detail ed20aa51)."""
    return not is_test_module_path(module_path)


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
        lines = source_lines(text, keepends=True)
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


def canonicalization_report(
    file_text: str,       # The module's text as the file holds it
    canonical: str,       # The canonical text a flip / emit would journal or write
    module_path: str = "",  # Repo-relative path (a notebook is never checked — its cells are not Python statements)
) -> Dict[str, Any]:  # {"diff": unified diff ("" when byte-equal), "hazards": [one line per semantic risk]}
    """What a canonicalization CHANGES, and whether the change can alter runtime behaviour
    (work item e526672a W4; ruling 865ba564; the second sighting 0fca6536).

    The diff is the unified diff file -> canonical, so the caller sees the reorder
    instead of a bare warning. A HAZARD is a change the canonical emitter can make that
    is not cosmetic: (1) the first statement changes KIND (a module docstring that stops
    being one when the import block lands above it — 53be4b98); (2) an import moves
    ABOVE a module-level non-import statement it followed in the file (a `sys.path`
    insert, an `os.environ` setting, `matplotlib.use(...)`: setup the import may depend
    on). Statements are matched by their AST, imports by the names they bind, so a
    merged or re-wrapped import line is still the same import. A `.ipynb` module or
    text that does not parse reports its diff and no hazards."""
    diff = "".join(difflib.unified_diff(file_text.splitlines(keepends=True),
                                        canonical.splitlines(keepends=True),
                                        fromfile="file", tofile="canonical"))
    if not diff or _is_notebook(module_path):
        return {"diff": diff, "hazards": []}
    try:
        before, after = ast.parse(file_text).body, ast.parse(canonical).body
    except SyntaxError:
        return {"diff": diff, "hazards": []}

    def kind(stmt: ast.stmt) -> str:
        if isinstance(stmt, ast.ImportFrom) and stmt.module == "__future__":
            return "__future__ import"
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            return "import"
        if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)):
            return "docstring"
        return type(stmt).__name__

    def bound(stmt: ast.stmt) -> List[str]:
        if not isinstance(stmt, (ast.Import, ast.ImportFrom)):
            return []
        return [a.asname or (a.name.split(".")[0] if isinstance(stmt, ast.Import) else a.name)
                for a in stmt.names]

    hazards: List[str] = []
    if before and after and kind(before[0]) != kind(after[0]):
        hazards.append(f"the first statement changes kind: {kind(before[0])} -> {kind(after[0])}")
    dumps_after = [ast.dump(s) for s in after]
    for i, stmt in enumerate(before):
        if kind(stmt) in ("import", "__future__ import") or (i == 0 and kind(stmt) == "docstring"):
            continue
        earlier = {n for s in before[:i] for n in bound(s)}
        later = {n for s in before[i + 1:] for n in bound(s)} - earlier
        if not later:
            continue
        try:
            j = dumps_after.index(ast.dump(stmt))
        except ValueError:
            continue
        moved = sorted(later & {n for s in after[:j] for n in bound(s)})
        if moved:
            line = ast.unparse(stmt).splitlines()[0]
            hazards.append(f"import of {', '.join(moved)} moves above `{line[:80]}` "
                           f"(line {stmt.lineno} of the file)")
    return {"diff": diff, "hazards": hazards}


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
        for line in read_segment(seg).splitlines():
            line = line.strip()
            if line:
                ops.append(json.loads(line))
    return ops


def latest_source_ops(
    path: str,  # Source-journal file path (JSONL)
) -> Mapping[Tuple[str, str], Dict[str, Any]]:  # (repo_key, module_path) -> latest op args
    """The LATEST source state per module, over both regimes (`codesource.SourceState`): before a
    module's capture the last `source` snapshot (last write wins — the 'journal STATE, not diff'
    semantics: re-flipping a module supersedes its prior text, replay-idempotent), after it the text
    composed from its element records (design detail 6d35c07d (2)). A `retire` op ends the key
    (dropped from the map) until a later `source` revives it."""
    return SourceState.over(read_source_journal(path)).latest_ops()


def graph_sourced_modules(
    path: str,  # Source-journal file path (JSONL)
) -> set:  # {(repo_key, module_path)} of modules PAST the Phase-2 cutover
    """The modules whose ingest source IS the journal (a `cutover` op exists for them).

    Phase distinction: a module with only `source` ops is in SHADOW (the file is still
    the ingest input, the journal soaks); a `cutover` op flips it — the journal becomes
    the source of truth and the file a generated, committed artifact. A `retire` op
    removes the key (its content lives on under a successor key, e.g. `.ipynb` -> `.py`)."""
    return SourceState.over(read_source_journal(path)).sourced


class SymbolIdentity:
    """Container-independent CodeSymbol identity, DERIVED from the source journal (36f649d3).

    A symbol's id is its BIRTH address (module, qualname[, generation]). A keep-identity
    op (rename-symbol / move / regroup / rename-module whose op provenance carries
    `identity: keep`) records that the symbol living at one address now lives at another;
    this map replays those records so ingest, the live verbs and add-symbol all derive the
    SAME id for a symbol wherever it lives now. Two tables: `living` = current address ->
    birth (repo_key, module_path, qualname, generation) for every re-homed or renamed
    symbol; `vacated` = address -> how many times a live symbol left it, so a NEWCOMER born
    at a freed address is generation n and never collides with the mover's kept id. Nested
    symbols follow their top-level parent (a moved class carries its methods). Keys live in
    whatever repo-key space the builder normalized to (ingest: the conceptual key). Nothing
    here is stored — the journal reproduces the map on every rebuild (test 7, REPLAY)."""

    def __init__(self):
        self.living: Dict[Tuple[str, str, str], Tuple[str, str, str, int]] = {}
        self.vacated: Dict[Tuple[str, str, str], int] = {}

    def birth_of(self, addr: Tuple[str, str, str]) -> Tuple[str, str, str, int]:
        """The birth (repo_key, module_path, qualname, generation) of the symbol living at `addr`."""
        hit = self.living.get(addr)
        return hit if hit is not None else (addr[0], addr[1], addr[2], self.vacated.get(addr, 0))

    def vacate(self, addr: Tuple[str, str, str]) -> None:
        """A live symbol left `addr`: the next symbol born there is one generation later."""
        self.living.pop(addr, None)
        self.vacated[addr] = self.vacated.get(addr, 0) + 1

    def register(self, old_addr: Tuple[str, str, str], new_addr: Tuple[str, str, str]) -> None:
        """The symbol living at `old_addr` now lives at `new_addr` (a rename, a move, a module rename)."""
        birth = self.birth_of(old_addr)
        if old_addr != new_addr:
            self.vacate(old_addr)
        self.living[new_addr] = birth

    def birth(self, repo_key: str, module_path: str, qualname: str) -> Optional[Dict[str, Any]]:
        """The birth fields (CodeSymbolNode kwargs) for the symbol at this address, or None
        when it is simply born here — the first symbol at an address nothing vacated."""
        addr = (repo_key, module_path, qualname)
        top = qualname.split(".", 1)[0]
        hit = self.living.get(addr)
        parts = qualname.split(".")
        for n in range(len(parts) - 1, 0, -1):  # the NEAREST re-homed / renamed ancestor (a method renamed in the element regime, 6d35c07d (4); a top-level one in the snapshot regime)
            if hit is not None:
                break
            anc = ".".join(parts[:n])
            parent = self.living.get((repo_key, module_path, anc))
            if parent is not None:  # nested under it: its birth module, prefixed name
                hit = (parent[0], parent[1], parent[2] + qualname[len(anc):], parent[3])
        if hit is not None:
            if hit == (repo_key, module_path, qualname, 0):
                return None  # back home at its birth address: the plain derivation
            return {"birth_repo_key": hit[0], "birth_module_path": hit[1],
                    "birth_qualname": hit[2], "generation": hit[3]}
        gen = self.vacated.get(addr, 0)
        if not gen and top != qualname:
            gen = self.vacated.get((repo_key, module_path, top), 0)  # a new class at a vacated name: its methods too
        return {"generation": gen} if gen else None

    def for_module(self, repo_key: str, module_path: str) -> Any:
        """The per-module hook `decompose_text(symbol_identity=...)` takes: qualname -> birth fields."""
        return lambda qualname: self.birth(repo_key, module_path, qualname)


def _birth_id(
    birth: Tuple[str, str, str, int],  # A birth (repo key, module path, qualname, generation)
) -> str:  # The CodeSymbol id it derives
    return code_symbol_node_id(code_module_node_id(birth[0], birth[1]), birth[2], birth[3])


def _top_level_names(
    text: str,  # A module's source text
) -> set:  # The top-level def/class names it defines (empty when it does not parse)
    """The top-level def/class names a module text defines — what a keep-identity op is
    read against (which module lost or gained which symbol)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    return {n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


def symbol_identity_map(
    path: str,             # Source-journal file path (JSONL)
    normalize: Any = None,  # Optional repo-key normalizer (ingest passes `conceptual_key`; None = the journal's dir-name keys)
) -> SymbolIdentity:  # The derived identity map (see `SymbolIdentity`)
    """Derive the container-independent symbol identity map from the source journal (36f649d3).

    Walks the segment family in append order keeping each module key's latest text; at
    every `source` record whose op provenance says `identity: keep`, the top-level names
    of the previous vs the new text say what happened to WHICH module — a rename-symbol
    record on the DEFINING module (old name gone, new name present; importer records carry
    the same op but define neither), a move/regroup SOURCE (a named symbol gone; its birth
    goes in flight) or TARGET (present, and its birth is in flight — sources land first),
    a rename-module's new key (every name re-registered from the retired path). Records
    without the marker replay exactly as before the scheme: a pre-scheme move re-keyed,
    and the edges journaled after it name the NEW id — retroactive identity would orphan
    those instead of healing anything, so the op names the choice (the 9170669e pattern)."""
    return _identity_over(read_source_journal(path), normalize)


def _identity_over(
    records: List[Dict[str, Any]],  # Source-journal records, in append order
    normalize: Any = None,          # Optional repo-key normalizer (as `symbol_identity_map`'s)
) -> "SymbolIdentity":  # The identity map after every record
    """`symbol_identity_map` over records already read."""
    walk = IdentityWalk(normalize)
    for rec in records:
        walk.apply(rec)
    walk.flush()
    return walk.ident


class IdentityWalk:
    """The identity map's walk ONE RECORD AT A TIME — the step `symbol_identity_map` folds
    over the whole journal, shared with the code fold (design amendment 2cc81d3b), which
    decomposes each record under the map AS OF that record: a symbol renamed or moved with
    identity: keep lived at another address before the record that re-homed it, so the
    final map would hand its earlier generations the wrong ids.

    THE ELEMENT REGIME (design detail 6d35c07d (4)): the walk folds the whole journal into a
    `SourceState` too. A captured module's symbols are elements whose records name their ids, so
    they need no inference -- but a def nested in a function is no element and derives its id from
    its address, so at each group's close (`flush`) a symbol element whose qualname or module changed
    registers the move and a retired one vacates its address, and the closure follows its parent.

    Every symbol element's BIRTH is derived at its first sighting and held (`_birth`): the address
    whose derivation gives its id -- the identity map's answer where it was captured, its module's
    birth address where the element regime gave it birth, the snapshot birth it arrived with. A move
    registers that birth, never a guess from the address it leaves.

    A MOVE ACROSS THE REGIMES hands the birth over inside the op's group (design detail aba8ec79 (5)):
    a captured symbol retiring under a keep-identity move goes in flight at its retire, with the births
    of its members the ancestor rule would not give, so the snapshot target's record re-homes it under
    the same id; a symbol leaving a snapshot module goes in flight as the snapshot regime's own move
    puts it, and the captured element arriving under that id takes it at the group's close."""

    def __init__(self, normalize: Any = None):
        self.ident = SymbolIdentity()
        self.latest_text: Dict[Tuple[str, str], str] = {}
        self.in_flight: Dict[str, Tuple[str, str, str, int]] = {}  # moved symbol -> its birth (source landed, target pending)
        self.members_in_flight: Dict[str, Dict[str, Tuple[str, str, str, int]]] = {}  # moved captured symbol -> {member suffix: its birth}, where the ancestor rule would not give it
        self.norm = normalize or (lambda k: k)
        self.state = SourceState()
        self._addr: Dict[str, Tuple[str, str, str]] = {}  # symbol element id -> its address as of the last flush
        self._addr_mod: Dict[str, str] = {}                # symbol element id -> its module id as of the last flush
        self._birth: Dict[str, Tuple[str, str, str, int]] = {}  # symbol element id -> its birth (derived at its first sighting)
        self._module_birth: Dict[str, Tuple[str, str]] = {}  # captured module id -> the (repo key, path) its id derives from
        self._handed: Set[str] = set()                     # symbol element ids handed to a snapshot target in the open group
        self._pending: Set[str] = set()                    # module ids the open group touched
        self._ts: Any = None

    def _first_birth(
        self,
        eid: str,                    # A symbol element id, sighted for the first time
        mid: str,                    # Its module id
        addr: Tuple[str, str, str],  # Its address now
    ) -> Optional[Tuple[str, str, str, int]]:  # The birth whose derivation gives `eid`, or None when none is known
        """An element's birth: arrived from a snapshot module in flight, else the identity map's answer
        at its address (a capture keeps the snapshot id), else a birth in its module (`absorb_records`
        gives a newcomer the first generation no record named). A birth the map would not derive is
        registered, so a closure under it follows."""
        q = addr[2]
        hit = self.in_flight.get(q) if "." not in q else None
        if hit is not None and _birth_id(hit) == eid:  # a snapshot symbol adopted by a captured module
            del self.in_flight[q]
            self.ident.living[addr] = hit
            return hit
        f = self.ident.birth(*addr) or {}
        b = (f.get("birth_repo_key") or addr[0], f.get("birth_module_path") or addr[1],
             f.get("birth_qualname") or q, f.get("generation", 0))
        if _birth_id(b) == eid:
            return b
        home = self._module_birth.get(mid)
        if home is None:
            return None
        ever, gen = self.state.elements.ever, 0
        while True:
            cand = code_symbol_node_id(mid, q, gen)
            if cand == eid:
                b = (home[0], home[1], q, gen)
                self.ident.living[addr] = b
                return b
            if cand not in ever:
                return None
            gen += 1

    def _hand_off(
        self,
        eid: str,  # A symbol element retiring under a keep-identity move
        op: Dict[str, Any],  # The op's provenance
    ) -> None:
        """A captured symbol leaving for a snapshot module: its birth goes in flight by name, as the
        snapshot regime's own move puts a source's, with each member's birth the ancestor rule would
        not derive from it; its address is vacated now (its retire is not vacated again at the close)."""
        addr = self._addr.get(eid)
        if addr is None or "." in addr[2] or addr[2] not in (op.get("symbols") or []):
            return
        top = self._birth.get(eid) or self.ident.birth_of(addr)
        members: Dict[str, Tuple[str, str, str, int]] = {}
        for x, xa in self._addr.items():
            if xa[:2] == addr[:2] and xa[2].startswith(addr[2] + "."):
                suffix = xa[2][len(addr[2]):]
                mb = self._birth.get(x)
                if mb is not None and mb != (top[0], top[1], top[2] + suffix, top[3]):
                    members[suffix] = mb
        self.in_flight[addr[2]] = top
        self.members_in_flight[addr[2]] = members
        self.ident.vacate(addr)
        self._handed.add(eid)

    def flush(self) -> None:
        """Settle the open group's element-regime address changes into the identity map."""
        if not self._pending:
            return
        els = self.state.elements
        new: Dict[str, Tuple[str, str, str]] = {}
        for mid in self._pending:
            m = els.modules.get(mid)
            if m is None or not m.captured:
                continue
            if mid not in self._module_birth:
                home = (self.norm(m.repo_key), m.module_path)
                if code_module_node_id(*home) == mid:
                    self._module_birth[mid] = home
            try:
                addrs = els.addresses(mid)
            except (CodeSourceError, SyntaxError):
                continue  # the code fold reports a module that does not compose
            for eid, q in addrs.items():
                new[eid] = (self.norm(m.repo_key), m.module_path, q)
        gone = {eid for eid, mid in self._addr_mod.items() if mid in self._pending} - set(new)
        for eid in [*new, *sorted(gone)]:  # pre-order: a class's birth settles before its members'
            old, now = self._addr.get(eid), new.get(eid)
            if now is not None and eid not in self._birth:
                b = self._first_birth(eid, els.elements[eid].module, now)
                if b is not None:
                    self._birth[eid] = b
            if old is not None and now is not None and old != now:
                b = self._birth.get(eid)
                if b is None:
                    self.ident.register(old, now)
                else:
                    self.ident.vacate(old)
                    self.ident.living[now] = b
            elif old is not None and now is None and els.elements.get(eid) is not None \
                    and els.elements[eid].retired:
                if eid in self._handed:
                    self._handed.discard(eid)  # vacated at its hand-off
                else:
                    self.ident.vacate(old)
            if now is None:
                self._addr.pop(eid, None)
                self._addr_mod.pop(eid, None)
            else:
                self._addr[eid] = now
                self._addr_mod[eid] = els.elements[eid].module
        self._pending.clear()

    def apply(
        self,
        rec: Dict[str, Any],  # One source-journal record
    ) -> Set[str]:  # The element-regime module ids the record touches
        """Advance the identity map past one record (see `symbol_identity_map` for the rules)."""
        if rec.get("ts") != self._ts:
            self.flush()
            self._ts = rec.get("ts")
        a = rec.get("args", {})
        raw = (a.get("repo_key"), a.get("module_path"))
        conflict = rec.get("verb") == "source" and self.state.elements.captured(raw) is not None
        touched = self.state.apply(rec)
        self._pending |= touched
        key = (self.norm(a.get("repo_key")), a.get("module_path"))
        verb = rec.get("verb")
        op = rec.get("op") or {}
        if verb == ELEMENT_RETIRE and op.get("identity") == "keep" and op.get("op") in ("move", "regroup"):
            self._hand_off(a.get("element"), op)
            return touched
        if verb == "retire":
            self.latest_text.pop(key, None)
            return touched
        if verb != "source" or conflict:
            return touched
        text = a.get("text", "")
        ident, in_flight = self.ident, self.in_flight
        if op.get("identity") == "keep":
            prev, new = _top_level_names(self.latest_text.get(key, "")), _top_level_names(text)
            kind = op.get("op")
            if kind == "rename-symbol":
                renames = op.get("renames") or [{"from": op.get("from"), "to": op.get("to")}]
                for r in renames:
                    if r.get("from") in prev and r.get("to") in new and r.get("from") not in new:
                        ident.register((key[0], key[1], r["from"]), (key[0], key[1], r["to"]))
            elif kind in ("move", "regroup"):
                for s in op.get("symbols") or []:
                    if s in prev and s not in new:      # this record is a SOURCE: the symbol left
                        in_flight[s] = ident.birth_of((key[0], key[1], s))
                        self.members_in_flight.pop(s, None)
                        ident.vacate((key[0], key[1], s))
                    elif s in new and s not in prev and s in in_flight:  # the TARGET: it arrived
                        ident.living[(key[0], key[1], s)] = in_flight.pop(s)
                        for suffix, mb in self.members_in_flight.pop(s, {}).items():  # from a captured module
                            ident.living[(key[0], key[1], s + suffix)] = mb
            elif kind == "rename-module" and op.get("from") and key[1] == op.get("to"):
                for s in sorted(new):
                    ident.register((key[0], op["from"], s), (key[0], key[1], s))
        self.latest_text[key] = text
        return touched


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
    record: Dict[str, Any] = {"verb": "retire", "ts": op_now(), "generation": 1,
                              "args": {"repo_key": repo_key, "module_path": module_path,
                                       "superseded_by": superseded_by}}
    if op_meta:
        record["op"] = op_meta
    _append_record(path, record)
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
    STATE-based, verb+args only; intent replay would bake verb bugs into history).

    A CAPTURED module (the element regime, design detail 6d35c07d (2)) is refused: its source is its
    element records, and a snapshot would end that regime silently -- its writes land at element
    grain through `element_absorb` + `append_elements` (abfc2829 (7))."""
    state = SourceState.over(read_source_journal(path))
    if state.elements.captured((repo_key, module_path)) is not None:
        raise CodeSourceError(f"{repo_key}/{module_path} is captured: its source is its element "
                              "records, and a full-module snapshot is refused")
    cur = state.latest_ops().get((repo_key, module_path))
    if cur is not None and cur.get("text") == text:
        return False
    record: Dict[str, Any] = {"verb": "source", "ts": op_now(), "generation": 1,
                              "args": {"repo_key": repo_key, "module_path": module_path,
                                       "import_name": import_name, "text": text}}
    if op_meta:
        record["op"] = op_meta
    _append_record(path, record)
    return True


# Verbs whose writes to a captured module are refused: a notebook flip has no element regime.
ELEMENT_REFUSED_OPS = ("flip-to-py",)


def _op_hints(
    op: Optional[Dict[str, Any]],  # The verb's op provenance
) -> Tuple[List[Tuple[str, str]], Optional[str]]:  # (keep-identity renames, the element the verb edited)
    """What a verb's op names about identity: a rename-symbol's (old, new) qualnames, author's node."""
    op = op or {}
    renames: List[Tuple[str, str]] = []
    if op.get("op") == "rename-symbol" and op.get("identity") == "keep":
        for r in op.get("renames") or [{"from": op.get("from"), "to": op.get("to")}]:
            if r.get("from") and r.get("to"):
                renames.append((r["from"], r["to"]))
    return renames, (op.get("node_id") if op.get("op") == "author" else None)


def captured_phase(
    state: SourceState,  # The source state
    module_path: str,    # A module path
) -> bool:  # Whether a module born at this path is born captured (the user's ruling bef8e6b8)
    """A phase is captured whole (`capture_phase`), so it is captured once any of its modules is:
    the tests phase (the tests pilot) and the package phase (design detail ed20aa51), each by its
    own `capture`."""
    return any(member(module_path) and any(m.captured and member(m.module_path)
                                           for m in state.elements.modules.values())
               for member in CAPTURE_PHASES.values())


def _absolute(
    binding: Dict[str, Any],  # An import-binding descriptor read in some module
    import_name: str,         # That module's dotted import name
    module_path: str,         # Its path (a package's __init__ is its package)
) -> Dict[str, Any]:  # The descriptor with a relative module resolved against that module's package
    """A binding travelling out of its package keeps naming what it named: `from .x import y` read in
    `pkg.sub.mod` lands as `from pkg.sub.x import y`."""
    level = binding.get("level") or 0
    if not level:
        return binding
    parts = import_name.split(".") if import_name else []
    package = parts if module_path.endswith("__init__.py") else parts[:-1]
    base = package[:len(package) - (level - 1)]
    mod = ".".join(base + ([binding["module"]] if binding.get("module") else []))
    return {**binding, "module": mod, "level": 0}


def _adoptions(
    state: SourceState,  # The source state as the write finds it
    by_source: Dict[Tuple[str, str], Set[str]],  # snapshot module key -> the op's symbols it gives up to a captured module
    identity: SymbolIdentity,  # The identity map as the write finds it (conceptual keys)
) -> Dict[str, Dict[str, Any]]:  # symbol -> {"ids": {qualname: id}, "bindings": [descriptor]} for each adopted subtree
    """What a captured module adopts from a snapshot module (design detail aba8ec79 (5)): every symbol
    of the subtree keeps the id the identity map gives it at its source address, and the subtree's
    import bindings in the source's derivation travel with it, a relative one resolved."""
    from .seeds import conceptual_key
    latest = state.latest_ops()
    out: Dict[str, Dict[str, Any]] = {}
    for key, names in by_source.items():
        prev = latest[key]
        ck = conceptual_key(key[0])
        dm = decompose_text(ck, key[1], "", prev["text"], import_name=prev.get("import_name") or None,
                            symbol_identity=identity.for_module(ck, key[1]))
        imp = dm.module.import_name
        for n in names:
            sub = [s for s in dm.symbols if s.qualname == n or s.qualname.startswith(n + ".")]
            seen: Set[str] = set()
            bindings = []
            for s in sub:
                for b in s.import_bindings:
                    b = _absolute(b, imp, key[1])
                    k = json.dumps(b, sort_keys=True)
                    if k not in seen:
                        seen.add(k)
                        bindings.append(b)
            out[n] = {"ids": {s.qualname: s.id for s in sub}, "bindings": bindings}
    return out


def element_plan(
    state: SourceState,  # The source state as the write finds it
    emissions: List[Dict[str, Any]],  # The write's module texts [{repo_key, module_path, import_name, text, cutover?}]
    retires: Optional[List[Dict[str, Any]]] = None,  # The write's module retires [{repo_key, module_path, superseded_by?}]
    op: Optional[Dict[str, Any]] = None,  # The verb's op provenance
    identity: Any = None,  # A callable giving the identity map as the write finds it (asked only when a captured module adopts a snapshot symbol)
) -> Tuple[Dict[int, Tuple[List[Dict[str, Any]], Dict[str, Any]]], Set[Tuple[str, str]]]:  # (emission index -> its element records + report, the retired keys a re-stated module absorbs)
    """The element regime's part of one write (abfc2829 (7), design detail aba8ec79): every emission
    to a captured module, to a module the write gives birth to in a captured phase, or re-stating a
    captured module's path becomes element records for what changed. A module's part in a move is
    read from its text: the op's symbols it held and lost depart (their ids kept, their members and
    bindings with them), the ones it gains arrive; a module with neither is a caller and its write
    an ordinary edit. ACROSS THE REGIMES ((5)): a captured symbol a snapshot module gains retires
    from the element regime (the identity walk hands its birth to the target's record); a snapshot
    symbol a captured module gains arrives under the ids the identity map gives it at its source,
    its bindings travelling. Raises `CodeSourceError` -- the whole write refused -- for an ambiguous
    edit, an unbound name, a notebook flip of a captured module, and a symbol the write moves that
    no module in it gives up or takes."""
    op = op or {}
    kind = op.get("op")
    els = state.elements
    retires = retires or []
    restates: Dict[int, Tuple[str, Dict[str, str]]] = {}
    absorbed: Set[Tuple[str, str]] = set()
    for r in retires:
        key = (r["repo_key"], r["module_path"])
        mid = els.captured(key)
        if mid is None:
            continue
        if kind in ELEMENT_REFUSED_OPS:
            raise CodeSourceError(f"{key[0]}/{key[1]} is captured: `{kind}` has no element regime")
        if kind != "rename-module":
            continue  # a delete-module: the snapshot retire ends the key and its elements
        to = op.get("to") or r.get("superseded_by")
        idx = next((i for i, e in enumerate(emissions)
                    if e["repo_key"] == key[0] and e["module_path"] == to), None)
        if idx is None:
            raise CodeSourceError(f"{key[0]}/{key[1]}: the rename names no emission for {to}")
        restates[idx] = (mid, {"repo_key": key[0], "module_path": to,
                               "import_name": emissions[idx].get("import_name") or ""})
        absorbed.add(key)
    symbols = set(op.get("symbols") or []) if kind in ("move", "regroup") else set()
    latest = state.latest_ops()
    departures: Dict[str, Tuple[str, str, int]] = {}  # name -> (module id, element id, emission index): leaving a captured module
    snap_departures: Dict[str, Tuple[str, str]] = {}  # name -> the snapshot module key it leaves
    gains: Dict[int, Set[str]] = {}
    for i, e in enumerate(emissions):
        key = (e["repo_key"], e["module_path"])
        mid = els.captured(key)
        if mid is not None and kind in ELEMENT_REFUSED_OPS:
            raise CodeSourceError(f"{key[0]}/{key[1]} is captured: `{kind}` has no element regime")
        new_names = _top_level_names(e["text"]) if symbols else set()
        if mid is not None:
            held = {q: eid for eid, q in els.addresses(mid).items() if "." not in q}
            for n in (symbols & set(held)) - new_names:
                departures[n] = (mid, held[n], i)
            gains[i] = (symbols & new_names) - set(held)
        else:
            prev = latest.get(key)
            before = _top_level_names(prev["text"]) if prev else set()
            for n in (symbols & before) - new_names:
                snap_departures[n] = key
            gains[i] = (symbols & new_names) - before
    targets: Dict[int, Tuple[str, Optional[Dict[str, str]]]] = {}  # the emissions the element regime plans
    for i, e in enumerate(emissions):
        key = (e["repo_key"], e["module_path"])
        mid, restate = els.captured(key), None
        if i in restates:
            mid, restate = restates[i]
        elif mid is None:
            if not (key not in latest and e.get("cutover") and captured_phase(state, key[1])):
                continue  # the snapshot regime's write
            from .seeds import conceptual_key
            mid = code_module_node_id(conceptual_key(key[0]), key[1])  # born captured (bef8e6b8)
            restate = {"repo_key": key[0], "module_path": key[1],
                       "import_name": e.get("import_name") or ""}
        stray = gains[i] - set(departures) - set(snap_departures)
        if stray:
            raise CodeSourceError(f"{key[0]}/{key[1]} gains {', '.join(sorted(stray))}, which no module "
                                  "in the write gives up")
        targets[i] = (mid, restate)
    lost = [n for n in departures if not any(n in g for g in gains.values())]
    if lost:
        raise CodeSourceError(f"{', '.join(sorted(lost))} leaves a captured module for no module in the write")
    exiting = {n for n in departures if not any(n in gains[j] for j in targets)}  # into a snapshot module
    by_source: Dict[Tuple[str, str], Set[str]] = {}
    for j in targets:
        for n in gains[j] & set(snap_departures) - set(departures):
            by_source.setdefault(snap_departures[n], set()).add(n)
    adopted: Dict[str, Dict[str, Any]] = {}
    if by_source:
        ident = identity() if identity is not None else None
        if ident is None:
            raise CodeSourceError(f"{', '.join(sorted(set().union(*by_source.values())))} arrives from a "
                                  "snapshot module, and the write was given no identity map to keep its ids")
        adopted = _adoptions(state, by_source, ident)
    plans: Dict[int, Tuple[List[Dict[str, Any]], Dict[str, Any]]] = {}
    renames, authored = _op_hints(op)
    for i, (mid, restate) in targets.items():
        mine = {n: d for n, d in departures.items() if d[2] == i}
        plans[i] = absorb_records(state, mid, emissions[i]["text"], renames=renames, authored=authored,
                                  departing={d[1] for n, d in mine.items() if n not in exiting},
                                  exiting={d[1] for n, d in mine.items() if n in exiting},
                                  arrivals={n: departures[n][1] for n in gains[i] if n in departures},
                                  adopted={n: adopted[n] for n in gains[i] if n in adopted},
                                  restate=restate)
    return plans, absorbed


def element_absorb(
    state: SourceState,  # The source state as the write finds it
    repo_key: str,       # Journal-space repo key
    module_path: str,    # Repo-relative module path (a captured module)
    text: str,           # The module's new text
    op: Optional[Dict[str, Any]] = None,  # The verb's op provenance (identity hints)
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:  # (the element records for what changed, the report)
    """One captured module's write at element grain -- `element_plan` over a single emission (a file
    edit, an authored text)."""
    plans, _ = element_plan(state, [{"repo_key": repo_key, "module_path": module_path, "text": text}],
                            op=op)
    return plans[0]


def append_elements(
    path: str,                      # Source-journal file path (JSONL)
    records: List[Dict[str, Any]],  # Element-regime records ({verb, args}) from `element_absorb`
    op_meta: Optional[Dict[str, Any]] = None,  # Replay-ignored op provenance
) -> int:  # How many records landed
    """Append one write's element records under ONE op-clock time: they fold as one group (the
    placement chain is whole only at the group's close)."""
    with op_clock():
        for r in records:
            record: Dict[str, Any] = {"verb": r["verb"], "ts": op_now(), "generation": 1,
                                      "args": r["args"]}
            if op_meta:
                record["op"] = op_meta
            _append_record(path, record)
    return len(records)


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
    allow_reorder: bool = False,         # Journal a canonicalization even when it carries a hazard
) -> Dict[str, Any]:  # The flip result (captured?, whether the file is already canonical, or error)
    """Capture a module's CANONICAL source into the shadow source journal (Phase 1).

    Reads the current file, computes the canonical emit (what the graph-sourced module would
    produce), and records it as the module's source state. The file remains the real ingest
    input — this only starts the shadow soak. Reports whether the file is ALREADY canonical
    (byte-exact) or what the one-time canonicalization changes: its unified diff, and any
    HAZARD (`canonicalization_report`: the first statement changing kind, an import moved
    above a module-level statement it followed). A hazard REFUSES the flip unless
    `allow_reorder` (work item e526672a W4) — restructure the file so its canonical form
    keeps its behaviour, or accept the reorder knowingly."""
    file_path = _resolve_path(repos_dir, repo_key, module_path)
    if not Path(file_path).exists():
        return {"error": f"no file at {file_path}", "captured": False}
    file_text = Path(file_path).read_text()
    state = SourceState.over(read_source_journal(source_journal_path))
    born = ((repo_key, module_path) not in state.latest_ops()
            and not _is_notebook(module_path) and captured_phase(state, module_path))
    if born or state.elements.captured((repo_key, module_path)) is not None:
        return _flip_captured(source_journal_path, state, repo_key, module_path, file_path,
                              file_text, write, allow_reorder, born=born,
                              import_name=import_name)
    try:
        canonical = _canonical(repo_key, module_path, file_path, file_text, import_name)
    except (SyntaxError, ValueError) as e:
        return {"error": f"cannot decompose {module_path}: {e}", "captured": False}
    report = canonicalization_report(file_text, canonical, module_path)
    if report["hazards"] and not allow_reorder:
        return {"error": f"the canonical form of {module_path} can change its behaviour — "
                         + "; ".join(report["hazards"])
                         + " (restructure the file, or pass --allow-reorder to journal it anyway)",
                "captured": False, "repo_key": repo_key, "module_path": module_path,
                "canonicalization_diff": report["diff"], "hazards": report["hazards"]}
    imp = import_name or _derive_import_name(repo_key, module_path, canonical)
    appended = (append_source(source_journal_path, repo_key, module_path, imp, canonical)
                if write else False)
    sourced = (repo_key, module_path) in graph_sourced_modules(source_journal_path)
    note = ("GRAPH-SOURCED: absorbed into the source journal (this module's source of truth); "
            "if the file was not already canonical, regenerate it via emit-artifact"
            if sourced else
            "SHADOW: journaled and on the graph (the code fold projects every live key, "
            "2cc81d3b); cut it over to make the journal its source of truth at the regen gate")
    if not write:
        note = "PREVIEW (--no-write): nothing journaled — a real flip would capture this state"
    return {"repo_key": repo_key, "module_path": module_path, "import_name": imp,
            "file_path": file_path, "captured": appended, "previewed": not write,
            "graph_sourced": sourced,
            "file_already_canonical": canonical == file_text,
            "canonicalization_diff": report["diff"], "hazards": report["hazards"],
            "canonical_bytes": len(canonical.encode("utf-8")),
            "note": note}


def _flip_captured(
    source_journal_path: str,  # The source journal
    state: SourceState,        # Its state (the module is captured)
    repo_key: str,             # Journal-space repo key
    module_path: str,          # Repo-relative module path
    file_path: str,            # The file read
    file_text: str,            # Its text (the out-of-band edit to absorb)
    write: bool,               # False = preview, journal nothing
    allow_reorder: bool,       # Journal even when the canonical form carries a hazard
    born: bool = False,        # A file-born module captured into a captured phase: born captured
    import_name: Optional[str] = None,  # Override the derived dotted import name (a birth)
) -> Dict[str, Any]:  # flip_module's result, with the element grain's report
    """flip-module on a CAPTURED module: the file's edit lands as element records for what changed
    only (abfc2829 (7)) -- the tool an agent reaches for after a file edit stays a safe graph path.
    A file-born module first captured where its phase is captured is BORN CAPTURED (the ruling
    bef8e6b8): its capture group lands, graph-sourced at once, so no cutover follows and the phase
    never holds a snapshot module."""
    op = {"op": "flip-module"}
    try:
        if born:
            imp = import_name or _derive_import_name(repo_key, module_path, file_text)
            plans, _ = element_plan(state, [{"repo_key": repo_key, "module_path": module_path,
                                             "import_name": imp, "text": file_text, "cutover": True}],
                                    op=op)
            recs, rep = plans[0]
        else:
            recs, rep = element_absorb(state, repo_key, module_path, file_text, op=op)
    except (CodeSourceError, SyntaxError, ValueError) as e:
        return {"error": f"cannot absorb {module_path} at element grain: {e}", "captured": False,
                "repo_key": repo_key, "module_path": module_path}
    canonical = rep.pop("text")
    report = canonicalization_report(file_text, canonical, module_path)
    base = {"repo_key": repo_key, "module_path": module_path, "file_path": file_path,
            "canonicalization_diff": report["diff"], "hazards": report["hazards"]}
    if report["hazards"] and not allow_reorder:
        return {**base, "captured": False,
                "error": f"the canonical form of {module_path} can change its behaviour — "
                         + "; ".join(report["hazards"])
                         + " (restructure the file, or pass --allow-reorder to journal it anyway)"}
    appended = append_elements(source_journal_path, recs, op_meta=op) if write and recs else 0
    note = (("BORN CAPTURED: its phase is captured, so the module's capture group landed -- its source is "
             "its elements, graph-sourced now (no cutover follows)" if born else
             "CAPTURED: the edit landed as element records (this module's source is its elements)")
            + "; if the file was not already canonical, regenerate it via emit-artifact")
    if not write:
        note = "PREVIEW (--no-write): nothing journaled — a real flip would land these element records"
    m = state.elements.modules.get(rep["module"])
    return {**base, "import_name": m.import_name if m else imp,
            "captured": bool(appended), "previewed": not write, "graph_sourced": True,
            "element_grain": rep, "file_already_canonical": canonical == file_text,
            "canonical_bytes": len(canonical.encode("utf-8")), "note": note}


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


CAPTURE_PHASES = {"tests": is_test_module_path, "packages": is_package_module_path}  # A phase of the code lane -> its module-path predicate


def capture_phase(
    source_journal_path: str,  # The source journal
    repos_dir: str,            # The repos root (the files the capture checks and rewrites)
    phase: str = "tests",      # The phase to capture whole (`CAPTURE_PHASES`)
    write: bool = True,        # False = run every guard and report the capture, journal nothing
    allow_reorder: bool = False,  # Capture even when a module's capture form carries a hazard
    diffs: bool = False,       # Carry each module's unified diff (snapshot -> capture form) in its row
) -> Dict[str, Any]:  # The capture's receipt (or {'error': ..., 'refused': [rows]})
    """Capture a phase of the code lane into the element regime (design abfc2829 (6), design detail
    6d35c07d; the tests pilot's leg 2, work item 8caa95e1): every live module of the phase becomes
    its element records, ONE journaled group per module (module, element and place records, then
    capture -- `capture_records`). The phase is captured whole -- the ruling bef8e6b8 holds one
    regime per phase -- so a phase holding a shadow module (journaled, never cut over) or an
    already captured one is refused. Every module is guarded before anything lands (one refusal
    refuses the phase): its file byte-equals its snapshot (a drifted file is an edit to absorb
    first), its capture form composes back to itself, it introduces no name the snapshot left
    bound (the binding check, abfc2829 (2)), and a HAZARD in the layout change refuses unless
    `allow_reorder`. Journal first, then the files whose capture form differs. The receipt names
    each module's layout change, the import-region ids that leave the graph, the births and the
    unclaimed bindings -- what the user reviews before the capture lands."""
    from .codesource import capture_records
    from .seeds import conceptual_key
    from cjm_python_decompose_core.elements import compose_module, lift_imports, unresolved_names
    member = CAPTURE_PHASES.get(phase)
    if member is None:
        return {"error": f"no phase `{phase}` -- one of {', '.join(sorted(CAPTURE_PHASES))}"}
    records = read_source_journal(source_journal_path)
    state = SourceState.over(records)
    latest = state.latest_ops()
    keys = sorted(k for k in latest if member(k[1]) and k[1].endswith(".py"))
    done = [k for k in keys if state.elements.captured(k) is not None]
    if done:
        return {"error": f"the {phase} phase is already captured ({len(done)} of {len(keys)} "
                         "modules) -- a module born into it is born captured (bef8e6b8)"}
    shadow = [f"{rk}/{mp}" for rk, mp in keys if (rk, mp) not in state.sourced]
    if shadow:
        return {"error": f"{len(shadow)} {phase} module(s) are shadows, never cut over -- cut each "
                         "over (or retire it) first, so the phase captures whole: "
                         + ", ".join(shadow[:10])}
    identity = _identity_over(records, conceptual_key)
    ever = set(state.elements.ever)
    groups: List[List[Dict[str, Any]]] = []
    rows: List[Dict[str, Any]] = []
    planned: List[Tuple[str, str]] = []  # The modules with an element form, in `keys` order
    refused: List[Dict[str, Any]] = []
    for rk, mp in keys:
        a = latest[(rk, mp)]
        ck = conceptual_key(rk)
        try:
            recs, rep = capture_records(rk, ck, mp, a.get("import_name") or "", a["text"],
                                        symbol_identity=identity.for_module(ck, mp), ever=ever)
        except (CodeSourceError, SyntaxError, ValueError) as e:  # no element form (design detail ed20aa51 (2))
            refused.append({"repo_key": rk, "module_path": mp, "faults": [str(e)]})
            continue
        ever |= {r["args"]["element"] for r in recs if r["args"].get("element")}
        planned.append((rk, mp))
        groups.append(recs)
        rows.append({**rep, "file_path": _resolve_path(repos_dir, rk, mp)})
    after = SourceState.over(records + [{**r, "ts": 0.0} for g in groups for r in g])
    composed = after.latest_ops()
    for (rk, mp), row in zip(planned, rows):
        before, text = latest[(rk, mp)]["text"], composed[(rk, mp)]["text"]
        faults: List[str] = []
        fp = Path(row["file_path"])
        if fp.exists() and fp.read_text() != before:
            faults.append("the file drifted from its snapshot -- absorb it (flip-module) first")
        if compose_module(lift_imports(text).elements, after.siblings((rk, mp))) != text:
            faults.append("the capture form does not compose back to itself")
        unbound = unresolved_names(text)
        if unbound:
            unbound -= unresolved_names(before) or set()
            if unbound:
                faults.append(f"the capture form reads {', '.join(sorted(unbound))}, which nothing binds")
        report = canonicalization_report(before, text, mp)
        row.update(layout_changed=bool(report["diff"]), hazards=report["hazards"])
        if diffs:
            row["diff"] = report["diff"]
        if report["hazards"] and not allow_reorder:
            faults.append("a hazard: " + "; ".join(report["hazards"]))
        if faults:
            refused.append({"repo_key": rk, "module_path": mp, "faults": faults})
        row["_text"] = text
    receipt: Dict[str, Any] = {
        "phase": phase, "modules": len(keys), "records": sum(len(g) for g in groups),
        "layout_changes": sum(1 for r in rows if r["layout_changed"]),
        "leaving": sum(len(r["leaving"]) for r in rows), "born": sum(len(r["born"]) for r in rows),
        "unclaimed_modules": sum(1 for r in rows if r["unclaimed"]),
        "captured": False, "previewed": not write, "files_written": [], "refused": refused}
    rows_out = [_capture_row(r) for r in rows]
    if refused:
        return {**receipt, "rows": rows_out,
                "error": f"{len(refused)} {phase} module(s) refused the capture -- NOTHING journaled"}
    # Through the seam: every group lands before any file is rewritten to its capture form
    journal = journaled_emit(source_journal_path, op={"op": "capture", "phase": phase}, write=write,
                             element_groups=[{"repo_key": k[0], "module_path": k[1], "records": g,
                                              "text": r["_text"], "path": r["file_path"]}
                                             for k, g, r in zip(planned, groups, rows)])
    if journal.get("error"):
        return {**receipt, "rows": rows_out, "error": journal["error"]}
    receipt.update(captured=write, files_written=journal["files_written"] if write else [])
    return {**receipt, "rows": rows_out}


def _capture_row(row: Dict[str, Any]) -> Dict[str, Any]:  # A capture receipt row, its composed text dropped
    return {k: v for k, v in row.items() if k != "_text"}


def birth_form(
    source_journal_path: Optional[str],  # The source journal (None: no state to read, no captured phase)
    repo_key: str,       # Journal-space repo key
    module_path: str,    # The path a module is born at
    import_name: str,    # Its dotted import name
    text: str,           # Its canonical snapshot text
) -> Optional[str]:  # The text the birth lands in a captured phase (its capture form), else None
    """What a module born at this path lands as its text: born into a captured phase (the ruling
    bef8e6b8) it is born captured, so its text is the composition of its element records -- the
    form `element_plan` gives the birth, which `add-module` shows and guards before it journals.
    Raises `CodeSourceError` for a birth the element regime refuses (an unbound name)."""
    if not source_journal_path:
        return None
    state = SourceState.over(read_source_journal(source_journal_path))
    if (repo_key, module_path) in state.latest_ops() or not captured_phase(state, module_path):
        return None
    plans, _ = element_plan(state, [{"repo_key": repo_key, "module_path": module_path,
                                     "import_name": import_name, "text": text, "cutover": True}])
    return plans[0][1]["text"]


def emit_source_artifact(
    source_journal_path: str,  # The source journal (the authoritative text)
    repos_dir: str,            # The repos root (where the artifact file lives)
    repo_key: str,             # The repo's durable conceptual slug
    module_path: str,          # Repo-relative module path
    write: bool = True,        # False = report what would change without touching disk
    allow_reorder: bool = False,  # Overwrite even when the change carries a hazard
) -> Dict[str, Any]:  # The regen result
    """(Re)generate a module's file artifact from its journaled source (the recovery /
    post-authoring emit). The journal is authoritative — this OVERWRITES the file; to
    keep an out-of-band file edit instead, absorb it with `flip-module`. The result
    carries the unified diff file -> journal; a HAZARD in it (`canonicalization_report`)
    refuses the overwrite unless `allow_reorder` (work item e526672a W4)."""
    a = latest_source_ops(source_journal_path).get((repo_key, module_path))
    if a is None:
        return {"error": f"no journaled source state for {repo_key}/{module_path}", "written": False}
    journaled = a.get("text", "")
    file_path = _resolve_path(repos_dir, repo_key, module_path)
    existing = Path(file_path).read_text() if Path(file_path).exists() else None
    changed = existing != journaled
    report = (canonicalization_report(existing, journaled, module_path)
              if existing is not None and changed else {"diff": "", "hazards": []})
    res = {"repo_key": repo_key, "module_path": module_path, "file_path": file_path,
           "changed": changed, "written": False,
           "artifact_bytes": len(journaled.encode("utf-8")),
           "canonicalization_diff": report["diff"], "hazards": report["hazards"]}
    if report["hazards"] and not allow_reorder:
        return {**res, "error": f"overwriting {module_path} can change its behaviour — "
                                + "; ".join(report["hazards"])
                                + " (absorb the file with flip-module, or pass --allow-reorder)"}
    if write and changed:
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        Path(file_path).write_text(journaled)
        res["written"] = True
    return res


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
    file so artifact == journal and the regen gate stays clean by construction. A CAPTURED
    module's edit lands at element grain (`element_absorb`)."""
    state = SourceState.over(read_source_journal(source_journal_path))
    if state.elements.captured((repo_key, module_path)) is not None:
        try:
            recs, rep = element_absorb(state, repo_key, module_path, emitted_text,
                                       op={"op": "author"})
        except (CodeSourceError, SyntaxError, ValueError) as e:
            return {"error": f"authored text for {repo_key}/{module_path}: {e}", "absorbed": False}
        appended = append_elements(source_journal_path, recs, op_meta={"op": "author"}) if recs else 0
        if rep["text"] != emitted_text:
            Path(file_path).write_text(rep["text"])
        return {"repo_key": repo_key, "module_path": module_path, "absorbed": bool(appended),
                "canonicalized": rep["text"] != emitted_text,
                "note": "authored state journaled as element records (the module is captured)"}
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
    state = SourceState.over(read_source_journal(source_journal_path))
    flipped = state.sourced
    modules = []
    drifted = stable = 0
    regen_clean = True
    for (repo_key, module_path), a in sorted(state.latest_ops().items()):
        journaled = a.get("text", "")
        file_path = _resolve_path(repos_dir, repo_key, module_path)
        file_text = Path(file_path).read_text() if Path(file_path).exists() else None
        file_matches = file_text == journaled
        try:
            if state.elements.captured((repo_key, module_path)) is not None:  # the element regime's fixpoint
                reemit = element_canonical(journaled, state.siblings((repo_key, module_path)))
            else:
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
    actor = os.environ.get("CJM_ACTOR") or None
    if actor:
        record["actor"] = actor
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
    record: Dict[str, Any] = {"verb": "cutover", "ts": op_now(), "generation": 1,
                              "args": {"repo_key": repo_key, "module_path": module_path}}
    if op_meta:
        record["op"] = op_meta
    _append_record(path, record)


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
    record: Dict[str, Any] = {"verb": "register", "ts": op_now(), "generation": 1,
                              "args": args}
    _append_record(path, record)
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
    element_groups: Optional[List[Dict[str, Any]]] = None,  # [{repo_key, module_path, records, text, path}] — element records the caller planned and guarded (a capture), one op-clock group each
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
    so file == journal by construction (the absorb-rewrite folded in). The element regime's part of
    the write (`element_plan`) lands as element records: an emission to a captured module, a module
    born into a captured phase, a captured module's re-stated path (its retire absorbed); a move
    across the two regimes or a notebook flip of a captured module is refused whole. Element groups
    the caller planned whole (`capture_phase`) land as one op-clock group each, then their files."""
    emissions, retires = emissions or [], retires or []
    element_groups = element_groups or []
    deletes, registers = deletes or [], registers or []
    receipt: Dict[str, Any] = {"op": (op or {}).get("op"), "journal_first": True,
                               "written": False, "events": [], "files_written": [],
                               "files_deleted": [], "unjournaled_files": [],
                               "canonicalized": []}
    if write and not source_journal_path and (emissions or retires or registers or element_groups):
        return {"error": "journaled_emit refuses: no source-journal path — journal-first "
                         "means events land before files (pass source_journal_path; "
                         "cg-write bakes it)", **receipt}
    from .seeds import conceptual_key
    records = read_source_journal(source_journal_path) if source_journal_path else []
    state = SourceState.over(records)
    latest, sourced = state.latest_ops(), state.sourced
    # Phase 1: validate/canonicalize everything — no side effects yet.
    plans: List[Dict[str, Any]] = []
    try:
        eplans, absorbed = element_plan(state, emissions, retires, op,
                                        identity=lambda: _identity_over(records, conceptual_key))
    except (CodeSourceError, SyntaxError, ValueError) as err:
        return {"error": f"{err} — NOTHING journaled or written", **receipt}
    for i, e in enumerate(emissions):
        key = (e["repo_key"], e["module_path"])
        if i in eplans:
            recs, rep = eplans[i]
            plans.append({**e, "_journaled": True, "_canonical": rep.pop("text"),
                          "_elements": recs, "_report": rep})
            continue
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
        if "_elements" in p:
            appended = write and append_elements(source_journal_path, p["_elements"], op_meta=op)
            receipt["events"].append({"verb": "elements", "repo_key": p["repo_key"],
                                      "module_path": p["module_path"], "appended": bool(appended),
                                      **{k: p["_report"][k] for k in ("records", "changed", "placed",
                                                                      "born", "retired", "renamed",
                                                                      "arrived", "departed", "exited",
                                                                      "restated")}})
            continue
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
    for g in element_groups:
        appended = write and append_elements(source_journal_path, g["records"], op_meta=op)
        receipt["events"].append({"verb": "elements", "repo_key": g["repo_key"],
                                  "module_path": g["module_path"], "appended": bool(appended),
                                  "records": len(g["records"])})
    for r in retires:
        if (r["repo_key"], r["module_path"]) in absorbed:
            continue  # a re-stated captured module: its module record moved the key
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
    for g in element_groups:
        path = g.get("path")
        if not path or (Path(path).exists() and Path(path).read_text() == g["text"]):
            continue
        if write:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(g["text"])
        receipt["canonicalized"].append(g["module_path"])
        receipt["files_written"].append(path)
    for d in deletes:
        if not d:
            continue
        if write and Path(d).exists():
            Path(d).unlink()
        receipt["files_deleted"].append(d)
    receipt["written"] = write
    return receipt


def journal_repos(
    source_journal_path: str,  # The source journal (JSONL)
) -> List[str]:  # Conceptual keys of every repo holding a live journal key, sorted
    """The on-graph repos — DERIVED from the journal (finding 7a2d54ae): a repo is on-graph
    because the journal holds its modules, never because a list names it."""
    from .seeds import conceptual_key
    return sorted({conceptual_key(rk) for rk, _ in latest_source_ops(source_journal_path)})


def uncaptured_modules(
    source_journal_path: str,   # The source journal (JSONL)
    code_libs,                  # Iterable of conceptual repo keys to walk (on-graph repos: `journal_repos`)
    repos_dir: str,             # The repos root the journal keys resolve under
    exclude: Optional[List[str]] = None,  # "<repo_key>/<path prefix>" entries stated as outside the library (config `code_exclude`)
) -> Dict[str, List[str]]:  # {repo_key: [module_path, ...]} — .py files with NO journal capture
    """The uncaptured-module audit (build a6453f70) — the ac3d52f4 recipe as a verb.

    In an on-graph library every .py the repo carries should be journal-captured
    (shadow or cut over): a file-sourced module's edits are unjournaled plain writes,
    invisible to journal-window and unrecoverable from the journals (the 2026-08-27
    hand audit found 21 uncaptured test modules across 7 repos). Since the code fold
    (2cc81d3b) an uncaptured file is also ABSENT from the graph — ingest reads no file —
    so ingest reports this audit's rows. This walks each repo tree — tests/ INCLUDED,
    caches/runtime/hidden dirs and the stated exclusions skipped — and diffs against
    the journal's captured keys. Rows are advisory: capture each with flip-module +
    cutover (nbdev v3 repos keep config in pyproject.toml — a lone settings.ini repo is
    unmigrated, see the nbdev-v3 note e73525de)."""
    from .seeds import repo_dir_name
    captured = set(latest_source_ops(source_journal_path))
    skip = {"__pycache__", ".git", ".venv", "runtime", "build", "dist",
            ".ipynb_checkpoints", "_proc", "node_modules"}
    out: Dict[str, List[str]] = {}
    for key in code_libs:
        root = Path(repos_dir) / repo_dir_name(key)
        if not root.is_dir():
            continue
        prefixes = tuple(e.split("/", 1)[1] for e in exclude or []
                         if "/" in e and e.split("/", 1)[0] in (key, repo_dir_name(key)))
        missing = []
        for p in sorted(root.rglob("*.py")):
            rel = p.relative_to(root)
            if any(part in skip or part.startswith(".") for part in rel.parts[:-1]):
                continue
            if prefixes and rel.as_posix().startswith(prefixes):
                continue
            if (repo_dir_name(key), rel.as_posix()) not in captured:
                missing.append(rel.as_posix())
        if missing:
            out[key] = missing
    return out


def _append_record(
    path: str,                # Source-journal file path (JSONL)
    record: Dict[str, Any],   # The op record — `verb`/`ts`/`generation`/`args` (+ optional `op`), pre-built by the caller
) -> None:
    """THE source-journal append choke point (finding 160466f7, P-48).

    Every source-journal event (`source` / `retire` / `cutover` / `register`) lands
    through here: session+actor stamping (`_stamp_session`), parent-dir creation, the
    one-line JSONL write, and the POST-append rotation check (`maybe_rotate`) live in
    ONE place, so append-level behavior added later (validation, telemetry, a rotation
    policy) cannot silently miss a site. Callers own the record's shape and any
    no-op/dedup decision; this helper only lands what it is given. Kept LOCAL (no
    `.journal` import — this module is a leaf). Regression: no `open("a")` may exist
    in this module outside this function."""
    _stamp_session(record)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True)
    with p.open("a") as f:
        f.write(line + "\n")
    maybe_rotate(path)
    appended = _APPENDED.get()
    if appended is not None:
        appended.append(json.loads(line))


# The records appended inside `collect_appends` (None outside one) — what a live code verb
# hands the code fold's step (design amendment 2cc81d3b, build B2 of leg B).
_APPENDED: ContextVar[Optional[List[Dict[str, Any]]]] = ContextVar("source_appended", default=None)


@contextmanager
def collect_appends() -> Iterator[List[Dict[str, Any]]]:  # The records appended inside the block, in order
    """Collect every source-journal record appended inside the block, as it landed on disk.

    Every append goes through `_append_record`, so whatever helper a verb uses (journaled_emit,
    flip_module, cutover_module, append_retire) its records are collected here — the group
    the live fold step (`relive.apply_live`) applies, exactly the records a rebuild will fold."""
    records: List[Dict[str, Any]] = []
    token = _APPENDED.set(records)
    try:
        yield records
    finally:
        _APPENDED.reset(token)
