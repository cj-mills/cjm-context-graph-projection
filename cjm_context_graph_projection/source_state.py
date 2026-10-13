"""The code SOURCE journal's write side: every live module's source is its element records
(`codesource`), and every write lands through `journaled_emit` -- events before files.

The persistence flip (file -> graph source of truth) ran in two regimes. The SNAPSHOT regime
journaled a module's whole canonical text per write (`source` records, a `cutover` making the
journal its source); the ELEMENT regime (design abfc2829) journals what changed at element grain
and composes the text. Every phase of the code lane is captured (tests 2d42e2dc, packages
872b741e), so the snapshot regime's write side is RETIRED (design detail 25c1fadc): a module is
born captured, a write to it is element records, a notebook is converted from its file into a
module born captured (`flip-to-py`). The snapshot history stays in the journal and the fold keeps
reading it (f1173d0b): `SourceState` folds both regimes' records, `IdentityWalk` derives ids
through every historical move, and `retire` -- which ends a key in either regime -- is live.

A SEPARATE stream from the private planning write-journal: source records are PUBLIC code state,
the planning journal is private (journal evolution seam #2: one log per domain).
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

from cjm_context_graph_primitives.journal import maybe_rotate, op_clock, op_now, read_journal
from cjm_dev_graph_schema.identity import code_module_node_id, code_symbol_node_id
from cjm_notebook_decompose_core.read import parse_notebook
from cjm_python_decompose_core.elements import unresolved_names
from cjm_python_decompose_core.parse import source_lines

from .codesource import (absorb_records, CodeSourceError, element_canonical, ELEMENT_RETIRE,
                         SourceState)


def is_test_module_path(module_path: str) -> bool:
    """Whether a module path denotes TEST source (`tests/` or `tests_manual/`) -- the code fold's
    test / package classification (a test module's calls are TESTS edges)."""
    return module_path.startswith(("tests/", "tests_manual/"))


def derive_import_name(module_path: str) -> str:  # repo-relative path -> dotted import name
    """A module's dotted import name from its repo-relative path ("pkg/sub.py" -> "pkg.sub");
    overridable where the package layout does not map directly."""
    stem = module_path[:-3] if module_path.endswith(".py") else module_path
    return stem.replace("/", ".")


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
    The result is RAW: the caller births it captured, which composes its import block.
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


def canonicalization_report(
    file_text: str,       # The module's text as the file holds it
    canonical: str,       # The canonical text a flip / emit would journal or write
    module_path: str = "",  # Repo-relative path (reported in nothing; kept for the callers' uniform call)
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
    merged or re-wrapped import line is still the same import. Text that does not parse
    reports its diff and no hazards."""
    diff = "".join(difflib.unified_diff(file_text.splitlines(keepends=True),
                                        canonical.splitlines(keepends=True),
                                        fromfile="file", tofile="canonical"))
    if not diff:
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


def read_source_journal(
    path: str,  # Source-journal file path (JSONL)
) -> List[Dict[str, Any]]:  # The recorded source ops, in append order
    """Read every `source` op across the rotated SEGMENT FAMILY (one JSON object per
    line; missing files = []) — cold segments first, live tail last: append order. The
    primitives' family read, so the instrument measures it (681604c1)."""
    return read_journal(path)


def latest_source_ops(
    path: str,  # Source-journal file path (JSONL)
) -> Mapping[Tuple[str, str], Dict[str, Any]]:  # (repo_key, module_path) -> latest op args
    """The LATEST source state per module, over both regimes (`codesource.SourceState`): before a
    module's capture the last `source` snapshot (last write wins — the 'journal STATE, not diff'
    semantics: re-flipping a module supersedes its prior text, replay-idempotent), after it the text
    composed from its element records (design detail 6d35c07d (2)). A `retire` op ends the key
    (dropped from the map) until a later `source` revives it."""
    return SourceState.over(read_source_journal(path)).latest_ops()


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


def _defined_names(
    text: str,  # A module's source text
) -> set:  # The names its top level defines (defs, classes, assignments -- never imports)
    """What a module defines for another to import: its top-level defs and classes and the names its
    top-level statements assign (`X = ...`, `X: T = ...`, tuple targets)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    out = set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                out |= {x.id for x in ast.walk(t) if isinstance(x, ast.Name)}
    return out


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


def element_plan(
    state: SourceState,  # The source state as the write finds it
    emissions: List[Dict[str, Any]],  # The write's module texts [{repo_key, module_path, import_name, text, born?}]
    retires: Optional[List[Dict[str, Any]]] = None,  # The write's module retires [{repo_key, module_path, superseded_by?}]
    op: Optional[Dict[str, Any]] = None,  # The verb's op provenance
) -> Tuple[Dict[int, Tuple[List[Dict[str, Any]], Dict[str, Any]]], Set[Tuple[str, str]]]:  # (emission index -> its element records + report, the retired keys a re-stated module absorbs)
    """The element regime's part of one write (abfc2829 (7), design detail aba8ec79): every emission
    to a captured module, to a module the write gives birth to (`born`: born captured, the ruling
    bef8e6b8), or re-stating a captured module's path becomes element records for what changed. A
    module's part in a move is read from its text: the op's symbols it held and lost depart (their ids
    kept, their members and bindings with them), the ones it gains arrive; a module with neither is a
    caller and its write an ordinary edit. An emission to a path the journal holds no module at, and
    that is not born, is no part of the plan (the caller writes it unjournaled, loudly). Raises
    `CodeSourceError` -- the whole write refused -- for an ambiguous edit, an unbound name, a write to
    a live snapshot module or a birth at a notebook path (the snapshot regime's write side is retired,
    design detail 25c1fadc), and a symbol the write moves that no module in it gives up or takes."""
    from .seeds import conceptual_key
    op = op or {}
    kind = op.get("op")
    els = state.elements
    retires = retires or []
    restates: Dict[int, Tuple[str, Dict[str, str]]] = {}
    absorbed: Set[Tuple[str, str]] = set()
    for r in retires:
        key = (r["repo_key"], r["module_path"])
        mid = els.captured(key)
        if mid is None or kind != "rename-module":
            continue  # a delete-module: the retire ends the key and its elements
        to = op.get("to") or r.get("superseded_by")
        idx = next((i for i, e in enumerate(emissions)
                    if e["repo_key"] == key[0] and e["module_path"] == to), None)
        if idx is None:
            raise CodeSourceError(f"{key[0]}/{key[1]}: the rename names no emission for {to}")
        restates[idx] = (mid, {"repo_key": key[0], "module_path": to,
                               "import_name": emissions[idx].get("import_name") or ""})
        absorbed.add(key)
    latest = state.latest_ops()
    targets: Dict[int, Tuple[str, Optional[Dict[str, str]]]] = {}  # the emissions the element regime plans
    for i, e in enumerate(emissions):
        key = (e["repo_key"], e["module_path"])
        mid, restate = els.captured(key), None
        if i in restates:
            mid, restate = restates[i]
        elif mid is None:
            if key in latest:
                raise CodeSourceError(f"{key[0]}/{key[1]} is a live snapshot module: the snapshot regime's "
                                      "write side is retired (design detail 25c1fadc)")
            if not e.get("born"):
                continue  # no module at this path: an unjournaled file
            if not key[1].endswith(".py"):
                raise CodeSourceError(f"{key[0]}/{key[1]}: only a .py module has an element form "
                                      "(a notebook is converted with flip-to-py)")
            mid = code_module_node_id(conceptual_key(key[0]), key[1])  # born captured (bef8e6b8)
            restate = {"repo_key": key[0], "module_path": key[1],
                       "import_name": e.get("import_name") or ""}
        targets[i] = (mid, restate)
    symbols = set(op.get("symbols") or []) if kind in ("move", "regroup") else set()
    departures: Dict[str, Tuple[str, str, int]] = {}  # name -> (module id, element id, emission index): leaving its module
    gains: Dict[int, Set[str]] = {}
    for i, (mid, _restate) in targets.items():
        new_names = _top_level_names(emissions[i]["text"]) if symbols else set()
        held = ({q: eid for eid, q in els.addresses(mid).items() if "." not in q}
                if mid in els.modules and els.modules[mid].captured else {})
        for n in (symbols & set(held)) - new_names:
            departures[n] = (mid, held[n], i)
        gains[i] = (symbols & new_names) - set(held)
    for i, g in gains.items():
        stray = g - set(departures)
        if stray:
            key = (emissions[i]["repo_key"], emissions[i]["module_path"])
            raise CodeSourceError(f"{key[0]}/{key[1]} gains {', '.join(sorted(stray))}, which no module "
                                  "in the write gives up")
    lost = [n for n in departures if not any(n in g for g in gains.values())]
    if lost:
        raise CodeSourceError(f"{', '.join(sorted(lost))} leaves its module for no module in the write")
    # A move's imports between its modules (finding 73714120): a name the write leaves unbound in one
    # module that exactly one other module of the write defines at its top level is imported from it --
    # the moved def reading a helper its source keeps, the source reading the def that left. Read from
    # the modules' own definitions, never from the corpus-wide edge derivation; only a name the write
    # itself leaves unbound (a module's own earlier NameError is not the move's to mend).
    imports_for: Dict[int, Dict[str, Dict[str, Any]]] = {}
    if departures:
        defined = {i: _defined_names(emissions[i]["text"]) for i in targets}
        for i, (mid, _restate) in targets.items():
            now = unresolved_names(emissions[i]["text"]) or set()
            m = els.modules.get(mid)
            before = (unresolved_names(state.compose(mid)) or set()) if m is not None and m.captured else set()
            for n in sorted(now - before):
                homes = [j for j in targets if j != i and n in defined[j]]
                if len(homes) == 1:
                    j, home = homes[0], els.modules.get(targets[homes[0]][0])
                    module = emissions[j].get("import_name") or (home.import_name if home else "")
                    if module:
                        imports_for.setdefault(i, {})[n] = {"name": n, "kind": "from", "module": module,
                                                            "imported": n, "alias": "", "level": 0}
    plans: Dict[int, Tuple[List[Dict[str, Any]], Dict[str, Any]]] = {}
    renames, authored = _op_hints(op)
    for i, (mid, restate) in targets.items():
        plans[i] = absorb_records(state, mid, emissions[i]["text"], renames=renames, authored=authored,
                                  departing={d[1] for d in departures.values() if d[2] == i},
                                  arrivals={n: departures[n][1] for n in gains[i]},
                                  restate=restate, imports_for=imports_for.get(i))
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
    if not plans:
        raise CodeSourceError(f"{repo_key}/{module_path}: no module the journal holds")
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
    source_journal_path: str,            # The source journal
    repos_dir: str,                      # The repos root (to read the current file)
    repo_key: str,                       # The repo's durable conceptual slug
    module_path: str,                    # Repo-relative module path
    import_name: Optional[str] = None,   # Override the derived dotted import name (a birth)
    write: bool = True,                  # False = preview, journal nothing
    allow_reorder: bool = False,         # Journal the canonical form even when it carries a hazard
) -> Dict[str, Any]:  # The flip result (captured?, whether the file is already canonical, or error)
    """Absorb a module's FILE into the source journal at element grain (abfc2829 (7)): a captured
    module's out-of-band edit lands as element records for what changed only -- the tool an agent
    reaches for after a file edit stays a safe graph path; a file-born module the journal does not
    hold is BORN CAPTURED (the ruling bef8e6b8; every phase is captured, design detail 25c1fadc): its
    capture group lands, graph-sourced at once. Reports whether the file already is the canonical
    form and its unified diff; a HAZARD in it (`canonicalization_report`: the first statement
    changing kind, an import moved above a module-level statement it followed) REFUSES unless
    `allow_reorder` (work item e526672a W4). A notebook is converted with `flip-to-py`."""
    file_path = _resolve_path(repos_dir, repo_key, module_path)
    if module_path.endswith(".ipynb"):
        return {"error": f"{module_path} is a notebook: convert it with flip-to-py (its module is born "
                         "captured)", "captured": False}
    if not Path(file_path).exists():
        return {"error": f"no file at {file_path}", "captured": False}
    file_text = Path(file_path).read_text()
    state = SourceState.over(read_source_journal(source_journal_path))
    born = (repo_key, module_path) not in state.latest_ops()
    op = {"op": "flip-module"}
    imp = import_name or derive_import_name(module_path)
    try:
        if born:
            plans, _ = element_plan(state, [{"repo_key": repo_key, "module_path": module_path,
                                             "import_name": imp, "text": file_text, "born": True}],
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
    note = (("BORN CAPTURED: the module's capture group landed -- its source is its elements, "
             "graph-sourced now" if born else
             "CAPTURED: the edit landed as element records (this module's source is its elements)")
            + "; if the file was not already canonical, regenerate it via emit-artifact")
    if not write:
        note = "PREVIEW (--no-write): nothing journaled — a real flip would land these element records"
    m = state.elements.modules.get(rep["module"])
    return {**base, "import_name": m.import_name if m else imp,
            "captured": bool(appended), "previewed": not write, "graph_sourced": True,
            "element_grain": rep, "file_already_canonical": canonical == file_text,
            "canonical_bytes": len(canonical.encode("utf-8")), "note": note}


def birth_form(
    source_journal_path: Optional[str],  # The source journal (None: an empty state)
    repo_key: str,       # Journal-space repo key
    module_path: str,    # The path a module is born at
    import_name: str,    # Its dotted import name
    text: str,           # The text it is born with
) -> str:  # The text the birth lands (its capture form)
    """What a module born at this path lands as its text: born captured (the ruling bef8e6b8), its
    text is the composition of its element records -- the form `element_plan` gives the birth, which
    `add-module` shows and guards before it journals. Raises `CodeSourceError` for a birth the element
    regime refuses (an unbound name, a path the journal already holds, a notebook path)."""
    state = (SourceState.over(read_source_journal(source_journal_path)) if source_journal_path
             else SourceState())
    if (repo_key, module_path) in state.latest_ops():
        raise CodeSourceError(f"{repo_key}/{module_path}: the journal already holds this module")
    plans, _ = element_plan(state, [{"repo_key": repo_key, "module_path": module_path,
                                     "import_name": import_name, "text": text, "born": True}])
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


def source_check(
    source_journal_path: str,  # The code source journal
    repos_dir: str,            # The repos root (to read the current files for the membrane diff)
) -> Dict[str, Any]:  # Per-module regen status
    """The regen gate over every live module: two checks each.

    - **Membrane (file drift):** does the file equal the module's composed text? A mismatch is a
      stale or dirtied ARTIFACT (an out-of-band edit by an agent / `ruff` / a human) -- surfaced,
      never silently overridden: absorb it with `flip-module` or regenerate it with `emit-artifact`.
    - **Fixpoint:** the composed text lifted into elements and composed again reproduces itself
      (`element_canonical`).

    A live module that is not captured (a snapshot key in an old journal) fails the gate: the
    snapshot regime's write side is retired (design detail 25c1fadc). `regen_clean` is the gate."""
    state = SourceState.over(read_source_journal(source_journal_path))
    modules = []
    drifted = stable = 0
    for (repo_key, module_path), a in sorted(state.latest_ops().items()):
        journaled = a.get("text", "")
        file_path = _resolve_path(repos_dir, repo_key, module_path)
        file_text = Path(file_path).read_text() if Path(file_path).exists() else None
        file_matches = file_text == journaled
        captured = state.elements.captured((repo_key, module_path)) is not None
        try:
            fixpoint = captured and element_canonical(
                journaled, state.siblings((repo_key, module_path))) == journaled
        except (SyntaxError, ValueError):
            fixpoint = False
        drifted += 0 if file_matches else 1
        stable += 1 if fixpoint else 0
        modules.append({"module": a.get("import_name") or module_path, "repo_key": repo_key,
                        "graph_sourced": captured,
                        "file_present": file_text is not None,
                        "file_matches_source": file_matches, "roundtrip_fixpoint": fixpoint})
    clean = drifted == 0 and stable == len(modules)
    return {"modules": modules, "count": len(modules),
            "graph_sourced_count": sum(1 for m in modules if m["graph_sourced"]),
            "file_drift": drifted, "roundtrip_stable": stable,
            "regen_clean": clean, "clean": clean and len(modules) > 0}


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
    emissions: Optional[List[Dict[str, Any]]] = None,  # [{repo_key, module_path, import_name, text, path, born?}] — JOURNAL-space keys
    retires: Optional[List[Dict[str, Any]]] = None,    # [{repo_key, module_path, superseded_by?}]
    deletes: Optional[List[str]] = None,               # File paths to unlink AFTER events land
    registers: Optional[List[Dict[str, Any]]] = None,  # [{repo_key, repo_root?, source_kind?}]
    op: Optional[Dict[str, Any]] = None,               # Op provenance ({'op': 'move', ...}); replay-ignored
    write: bool = True,                                # False = full preview, ZERO side effects
) -> Dict[str, Any]:  # The uniform receipt every mutating verb carries (or {'error': ...})
    """The ops seam (pillar 1 of DEC 6ee4b4f2): events BEFORE files — THE file-write path.

    Three strict phases: (1) plan the WHOLE batch (`element_plan`, zero side effects — one refusal
    refuses everything); (2) append ALL journal events (each module's element records as one
    op-clock group, the register and retire events, `op` provenance riding replay-ignored); (3)
    only then touch disk. A crash between (2) and (3) leaves the journal AHEAD of the files — the
    recoverable direction (`emit-artifact` regenerates); files ahead of the journal is the
    rebuild-revert class (finding ff8522fa) this seam makes impossible by construction. Every
    emission is to a captured module, a module the write gives birth to (`born`), or a captured
    module's re-stated path (a rename-module, its retire absorbed), and lands its COMPOSED text on
    disk, so file == journal by construction. An emission at a path the journal holds no module at,
    and not born, is written as a plain file and reported LOUDLY in `unjournaled_files`;
    write=True with events to land and no journal path REFUSES outright."""
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
    state = SourceState.over(read_source_journal(source_journal_path) if source_journal_path else [])
    # Phase 1: plan everything — no side effects yet.
    try:
        eplans, absorbed = element_plan(state, emissions, retires, op)
    except (CodeSourceError, SyntaxError, ValueError) as err:
        return {"error": f"{err} — NOTHING journaled or written", **receipt}
    plans: List[Dict[str, Any]] = []
    for i, e in enumerate(emissions):
        if i in eplans:
            recs, rep = eplans[i]
            plans.append({**e, "_journaled": True, "_canonical": rep.pop("text"),
                          "_elements": recs, "_report": rep})
        else:
            plans.append({**e, "_journaled": False, "_canonical": e["text"]})
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
        appended = write and append_elements(source_journal_path, p["_elements"], op_meta=op)
        receipt["events"].append({"verb": "elements", "repo_key": p["repo_key"],
                                  "module_path": p["module_path"], "appended": bool(appended),
                                  **{k: p["_report"][k] for k in ("records", "changed", "placed",
                                                                  "born", "retired", "renamed",
                                                                  "arrived", "departed",
                                                                  "restated", "dropped",
                                                                  "derived")}})
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
        out_text = p["_canonical"]
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

    In an on-graph library every .py the repo carries should be journal-captured: a file-sourced module's edits are unjournaled plain writes,
    invisible to journal-window and unrecoverable from the journals (the 2026-08-27
    hand audit found 21 uncaptured test modules across 7 repos). Since the code fold
    (2cc81d3b) an uncaptured file is also ABSENT from the graph — ingest reads no file —
    so ingest reports this audit's rows. This walks each repo tree — tests/ INCLUDED,
    caches/runtime/hidden dirs and the stated exclusions skipped — and diffs against
    the journal's captured keys. Rows are advisory: capture each with flip-module (born
    captured; a notebook is converted with flip-to-py; nbdev v3 repos keep config in pyproject.toml — a lone settings.ini repo is
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

    Every source-journal event (element records, `retire`, `register`) lands
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
    flip_module, append_retire) its records are collected here — the group
    the live fold step (`relive.apply_live`) applies, exactly the records a rebuild will fold."""
    records: List[Dict[str, Any]] = []
    token = _APPENDED.set(records)
    try:
        yield records
    finally:
        _APPENDED.reset(token)
