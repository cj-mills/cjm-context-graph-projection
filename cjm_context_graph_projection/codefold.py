"""The code lane as a FOLD over the source journal (design amendment 2cc81d3b to 8f6f2343).

Every code node, every code edge and every time on them is a function of the SOURCE JOURNAL
alone. `CodeFold.apply` advances the projected code corpus past ONE record: the record's
module is re-decomposed under the identity map AS OF that record, its nodes and local edges
are diffed against the module's previous derivation, and the corpus-level edges whose
condition moved (a name that became or stopped being unambiguous, an import that started or
stopped resolving) are re-resolved. Each change carries the record's ts, so

- a node's `created_at` is the start of its CURRENT continuous existence and its
  `updated_at` the last record that changed its content (label, properties) — every code
  node's `sources` locates the WHOLE file state (its content hash moves with any edit to
  the module), so the locator is refreshed to the latest state without counting as a change;
- an edge's `created_at` is when its justification last began to hold — a CALLS edge is born
  when the called name became unambiguous, not when the caller was written.

The inventory IS the journal: every live `.py` key is projected, whatever repo it lives in
(the finding 7a2d54ae fix — no hand-kept list decides what code the graph holds). A retired
key leaves; `.ipynb` records advance the identity walk but project nothing (no notebook lane
remains; a notebook key still live at the end is reported).

GROUPS. Records sharing one ts are one invocation (the op clock, efd659a1), and records
sharing one identity-keep `op` envelope are one refactor (the pre-clock history of move /
regroup / rename-module). Removals are settled at the group's end, so a symbol that leaves
one module and arrives in another inside the group keeps its `created_at`.

THE RESOLUTION RULES are the latest-state ingest's, held incrementally (each rule a `_Scope`):
package CALLS / USES / IMPORTS resolve within package modules, test ones within test modules,
the corpus-wide CALLS / IMPORTS across both, and TESTS from test symbols onto package symbols.
Every name resolves only when UNAMBIGUOUS in its scope — including import names, where the
latest-state ingest let the first or last duplicate win by scan order (no journal carries
that order).

The rebuild folds every record and commits `elements()`; the live verbs (build B2 of leg B
0e3508fd) apply the same step over state read from the db.
"""

from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from cjm_context_graph_layer.grammar import make_edge
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations
from cjm_python_decompose_core.extract import decompose_text
from cjm_python_decompose_core.ingest import resolve_import

from .seeds import conceptual_key, repo_dir_name
from .source_state import IdentityWalk, is_test_module_path, read_source_journal


def _bare(
    wire: Dict[str, Any],  # A CodeSymbol wire
) -> Optional[str]:  # Its bare name (the last qualname segment), or None
    """A symbol's bare name — the key every name-resolution rule matches on."""
    return (wire["properties"].get("qualname") or "").split(".")[-1] or None


def _import_name(
    wire: Dict[str, Any],  # A CodeModule wire
) -> Optional[str]:  # Its dotted import name, or None
    """A module's dotted import name — the key the IMPORTS rules match on."""
    return wire["properties"].get("import_name") or None


def _resolved_imports(
    wire: Dict[str, Any],  # A CodeModule wire
) -> List[str]:  # The absolute dotted names its raw imports resolve to
    """A module's imports, resolved against its own package (relative imports included)."""
    p = wire["properties"]
    inm = p.get("import_name", "") or ""
    is_pkg = str(p.get("module_path", "")).endswith("__init__.py")
    out = []
    for raw in p.get("imports", []) or []:
        t = resolve_import(raw, inm, is_pkg)
        if t:
            out.append(t)
    return out


class _Scope:
    """One resolution rule: each source's names resolve to the UNIQUE target keyed by that
    name within the scope (precision over recall — never mint a guessed edge). Sources and
    targets belong to the scope by their module's test-ness."""

    def __init__(
        self,
        relation: str,                                        # The edge relation it mints
        label: str,                                           # The node label it indexes (CodeSymbol / CodeModule)
        target_key: Callable[[Dict[str, Any]], Optional[str]],  # A target wire -> its key
        source_names: Callable[[Dict[str, Any]], Iterable[str]],  # A source wire -> the names it references
        sources_in: Callable[[bool], bool],                   # module is-test -> its nodes are sources here
        targets_in: Callable[[bool], bool],                   # module is-test -> its nodes are targets here
        skip_self: bool,                                      # Whether a self-resolution mints no edge
    ):
        self.relation, self.label = relation, label
        self.target_key, self.source_names = target_key, source_names
        self.sources_in, self.targets_in, self.skip_self = sources_in, targets_in, skip_self
        self.targets: Dict[str, Set[str]] = {}          # key -> target ids
        self.sources: Dict[str, Tuple[str, ...]] = {}   # source id -> names (ordered, deduped)
        self.callers: Dict[str, Set[str]] = {}          # name -> source ids referencing it
        self.out: Dict[str, Dict[str, Dict[str, Any]]] = {}  # source id -> {edge id: wire}

    def resolve(self, name: str) -> Optional[str]:
        """The one target keyed by `name`, or None when absent or ambiguous."""
        ids = self.targets.get(name)
        return next(iter(ids)) if ids and len(ids) == 1 else None

    def change(
        self,
        old: Dict[str, Dict[str, Any]],  # The module's previous wires of this scope's label
        new: Dict[str, Dict[str, Any]],  # Its new wires
        test: bool,                      # Whether the module is a test module
    ) -> Set[str]:  # Source ids whose edges must be re-resolved
        """Swap one module's wires in the indexes; the dirty sources are the module's own plus
        every caller of a name whose resolution moved."""
        dirty: Set[str] = set()
        touched: Dict[str, Optional[str]] = {}  # name -> resolution BEFORE the change
        is_src, is_tgt = self.sources_in(test), self.targets_in(test)
        for sid, w in old.items():
            if is_tgt:
                k = self.target_key(w)
                if k:
                    touched.setdefault(k, self.resolve(k))
                    ids = self.targets.get(k)
                    if ids is not None:
                        ids.discard(sid)
                        if not ids:
                            del self.targets[k]
            if sid in self.sources:
                for n in self.sources.pop(sid):
                    c = self.callers.get(n)
                    if c is not None:
                        c.discard(sid)
                        if not c:
                            del self.callers[n]
                dirty.add(sid)
        for sid, w in new.items():
            if is_tgt:
                k = self.target_key(w)
                if k:
                    touched.setdefault(k, self.resolve(k))
                    self.targets.setdefault(k, set()).add(sid)
            if is_src:
                names = tuple(dict.fromkeys(n for n in self.source_names(w) if n))
                self.sources[sid] = names
                for n in names:
                    self.callers.setdefault(n, set()).add(sid)
                dirty.add(sid)
        for k, before in touched.items():
            if self.resolve(k) != before:
                dirty |= self.callers.get(k, set())
        return dirty

    def edges_for(self, sid: str) -> Dict[str, Dict[str, Any]]:
        """The edges source `sid` currently justifies in this scope."""
        out: Dict[str, Dict[str, Any]] = {}
        for n in self.sources.get(sid, ()):
            t = self.resolve(n)
            if t and not (self.skip_self and t == sid):
                e = make_edge(sid, t, self.relation)
                out[e["id"]] = e
        return out


def _scopes() -> List[_Scope]:
    """The latest-state ingest's resolution rules, one scope each (see the module docstring)."""
    sym, mod = DevNodeKinds.CODE_SYMBOL, DevNodeKinds.CODE_MODULE
    calls = lambda w: w["properties"].get("calls", []) or []
    refs = lambda w: w["properties"].get("refs", []) or []
    pkg, tst, both = (lambda t: not t), (lambda t: t), (lambda t: True)
    return [
        # per-corpus: the package corpus and the test corpus each resolve within themselves
        # (corpus_graph_elements: CALLS keeps a self-call, USES and IMPORTS skip self)
        _Scope(DevRelations.CALLS, sym, _bare, calls, pkg, pkg, skip_self=False),
        _Scope(DevRelations.USES, sym, _bare, refs, pkg, pkg, skip_self=True),
        _Scope(DevRelations.IMPORTS, mod, _import_name, _resolved_imports, pkg, pkg, skip_self=True),
        _Scope(DevRelations.CALLS, sym, _bare, calls, tst, tst, skip_self=False),
        _Scope(DevRelations.USES, sym, _bare, refs, tst, tst, skip_self=True),
        _Scope(DevRelations.IMPORTS, mod, _import_name, _resolved_imports, tst, tst, skip_self=True),
        # corpus-wide (resolve_corpus_code_edges): across package and test modules
        _Scope(DevRelations.CALLS, sym, _bare, calls, both, both, skip_self=True),
        _Scope(DevRelations.IMPORTS, mod, _import_name, _resolved_imports, both, both, skip_self=True),
        # TESTS (resolve_test_edges): test symbols' calls + refs onto package symbols
        _Scope(DevRelations.TESTS, sym, _bare, lambda w: [*calls(w), *refs(w)], tst, pkg,
               skip_self=True),
    ]


def _same_group(
    prev: Dict[str, Any],  # The previous record
    rec: Dict[str, Any],   # The next record
) -> bool:  # Whether they belong to one invocation / one refactor
    """One ts = one invocation (the op clock); one identity-keep op envelope = one refactor."""
    if prev.get("ts") == rec.get("ts"):
        return True
    op = rec.get("op")
    return bool(op) and op.get("identity") == "keep" and prev.get("op") == op


class CodeFold:
    """The projected code corpus as a fold over source-journal records (see module docstring)."""

    def __init__(
        self,
        repos_dir: str,  # The repos root (a node's provenance path = repos_dir/<current dir>/<module_path>)
    ):
        self.repos_dir = Path(repos_dir)
        self.walk = IdentityWalk(conceptual_key)
        self.scopes = _scopes()
        self.live: Dict[Tuple[str, str], Tuple[int, Dict[str, Any]]] = {}  # raw key -> (seq, args)
        self.modules: Dict[Tuple[str, str], Dict[str, Any]] = {}  # conceptual key -> its derivation
        self.nodes: Dict[str, Dict[str, Any]] = {}   # id -> {wire, created_at, updated_at, holders}
        self.edges: Dict[str, Dict[str, Any]] = {}   # id -> {wire, created_at, updated_at, n}
        self._pending_nodes: Set[str] = set()
        self._pending_edges: Set[str] = set()
        self._prev: Optional[Dict[str, Any]] = None
        self._seq = 0
        self.failures: List[Dict[str, Any]] = []  # records whose text did not decompose
        self.records = 0

    # -- node / edge bookkeeping ------------------------------------------------------------
    def _node_put(self, holder, wire, ts) -> None:
        rec = self.nodes.get(wire["id"])
        if rec is None:
            self.nodes[wire["id"]] = {"wire": wire, "created_at": ts, "updated_at": ts,
                                      "holders": {holder}}
            return
        rec["holders"].add(holder)
        self._pending_nodes.discard(wire["id"])
        old = rec["wire"]
        if (old["label"], old.get("properties")) != (wire["label"], wire.get("properties")):
            rec["updated_at"] = ts
        rec["wire"] = wire  # the provenance locator always follows the latest file state

    def _node_drop(self, holder, nid) -> None:
        rec = self.nodes.get(nid)
        if rec is None:
            return
        rec["holders"].discard(holder)
        if not rec["holders"]:
            self._pending_nodes.add(nid)

    def _edge_hold(self, wire, ts) -> None:
        rec = self.edges.get(wire["id"])
        if rec is None:
            self.edges[wire["id"]] = {"wire": wire, "created_at": ts, "updated_at": ts, "n": 1}
            return
        rec["n"] += 1
        self._pending_edges.discard(wire["id"])
        if rec["wire"] != wire:
            rec["wire"], rec["updated_at"] = wire, ts

    def _edge_release(self, eid) -> None:
        rec = self.edges.get(eid)
        if rec is None:
            return
        rec["n"] -= 1
        if rec["n"] <= 0:
            self._pending_edges.add(eid)

    def _settle(self) -> None:
        """End of a group: whatever lost its last holder inside it is gone."""
        for nid in self._pending_nodes:
            rec = self.nodes.get(nid)
            if rec is not None and not rec["holders"]:
                del self.nodes[nid]
        for eid in self._pending_edges:
            rec = self.edges.get(eid)
            if rec is not None and rec["n"] <= 0:
                del self.edges[eid]
        self._pending_nodes.clear()
        self._pending_edges.clear()

    # -- the step ---------------------------------------------------------------------------
    def apply(
        self,
        rec: Dict[str, Any],  # One source-journal record, in append order
    ) -> None:
        """Advance the corpus past one record (the step the rebuild folds and live verbs share)."""
        if self._prev is not None and not _same_group(self._prev, rec):
            self._settle()
        self._prev = rec
        self.records += 1
        self.walk.apply(rec)
        verb, a = rec.get("verb"), rec.get("args", {})
        raw = (a.get("repo_key"), a.get("module_path"))
        if verb == "source":
            self._seq += 1
            self.live[raw] = (self._seq, a)
        elif verb == "retire":
            self.live.pop(raw, None)
        else:
            return  # cutover / register: phase and inventory events, no code state
        if not str(raw[1]).endswith(".py"):
            return  # a notebook record: identity only (no notebook lane)
        self._rederive((conceptual_key(raw[0]), raw[1]), rec.get("ts"))

    def _current_args(self, key) -> Optional[Dict[str, Any]]:
        """The latest live record for a conceptual key (a renamed repo's old and new dir-name
        keys fold onto one module; the most recent live one holds)."""
        best = None
        for raw, (seq, a) in self.live.items():
            if raw[1] == key[1] and conceptual_key(raw[0]) == key[0]:
                if best is None or seq > best[0]:
                    best = (seq, a)
        return best[1] if best else None

    def _rederive(self, key, ts) -> None:
        a = self._current_args(key)
        old = self.modules.get(key) or {"nodes": {}, "local": {}}
        test = is_test_module_path(key[1])
        if a is None:
            new = {"nodes": {}, "local": {}}
        else:
            path = str(self.repos_dir / repo_dir_name(key[0]) / key[1])
            try:
                dm = decompose_text(key[0], key[1], path, a.get("text", ""),
                                    import_name=a.get("import_name"),
                                    symbol_identity=self.walk.ident.for_module(*key))
            except (SyntaxError, ValueError) as e:
                self.failures.append({"repo_key": key[0], "module_path": key[1], "ts": ts,
                                      "error": str(e)})
                return  # the previous derivation stands; reported
            wires = [dm.module.to_graph_node(), *(s.to_graph_node() for s in dm.symbols),
                     *(t.to_graph_node() for t in dm.texts)]
            new = {"nodes": {w["id"]: w for w in wires},
                   "local": {e["id"]: e for e in dm.local_edges}}
        for w in new["nodes"].values():
            self._node_put(key, w, ts)
        for nid in old["nodes"].keys() - new["nodes"].keys():
            self._node_drop(key, nid)
        for eid in new["local"].keys() - old["local"].keys():
            self._edge_hold(new["local"][eid], ts)
        for eid in old["local"].keys() - new["local"].keys():
            self._edge_release(eid)
        for scope in self.scopes:
            o = {i: w for i, w in old["nodes"].items() if w["label"] == scope.label}
            n = {i: w for i, w in new["nodes"].items() if w["label"] == scope.label}
            if not o and not n:
                continue
            for sid in scope.change(o, n, test):
                was = scope.out.pop(sid, {})
                now = scope.edges_for(sid)
                if now:
                    scope.out[sid] = now
                for eid in now.keys() - was.keys():
                    self._edge_hold(now[eid], ts)
                for eid in was.keys() - now.keys():
                    self._edge_release(eid)
        if new["nodes"]:
            self.modules[key] = new
        else:
            self.modules.pop(key, None)

    # -- results ----------------------------------------------------------------------------
    def elements(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:  # (nodes, edges), times carried
        """The corpus as `extend_graph` wires, each carrying its `created_at` / `updated_at`."""
        self._settle()
        nodes = [{**r["wire"], "created_at": r["created_at"], "updated_at": r["updated_at"]}
                 for r in self.nodes.values()]
        edges = [{**r["wire"], "created_at": r["created_at"], "updated_at": r["updated_at"]}
                 for r in self.edges.values()]
        return nodes, edges

    def live_notebooks(self) -> List[Tuple[str, str]]:
        """Notebook keys still live — journaled source no lane projects (reported, never guessed)."""
        return sorted(k for k in self.live if not str(k[1]).endswith(".py"))


def fold_source_journal(
    source_journal_path: str,  # The source journal (segment family)
    repos_dir: str,            # The repos root
) -> CodeFold:  # The folded corpus (call `elements()` for the wires)
    """Fold every record of the source journal, in append order (the rebuild's code lane)."""
    fold = CodeFold(repos_dir)
    for rec in read_source_journal(source_journal_path):
        fold.apply(rec)
    return fold
