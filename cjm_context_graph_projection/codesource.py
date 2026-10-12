"""The code lane's ELEMENT regime: a captured module's source is its element records (design abfc2829,
ruling 21b8fcca, design detail 6d35c07d; the tests pilot's leg 2, work item 8caa95e1).

The code source journal holds two regimes in one stream. Before a module's `capture` its state is the
latest `source` snapshot (full-module text); after it, its state is the element records below and its
text is COMPOSED (`cjm_python_decompose_core.elements.compose_module`): the import block rendered from
the elements' bindings at its derived slot, seams and indentation derived. `SourceState` answers "the
module's current text" for both regimes, so every reader of `latest_source_ops` sees a captured module's
composed text unchanged.

RECORDS (each re-states its whole own state, as the notes lane's do; one write's records share a ts and
fold as one step):
- `module {module, repo_key, module_path, import_name}` -- the module's own state (`module` = its
  CodeModule id; `repo_key` journal-space, as on `source` records);
- `element {element, kind, text, bindings}` -- one element's content: kind symbol | text | imports,
  text at depth 0 (an imports element's is derived, so empty), bindings the import descriptors it owns;
- `place {element, module, parent | null, after | null}` -- where it sits: its class (`parent`) and the
  sibling it follows (`after`); an imports element's place names the module only, its slot is derived;
- `element-retire {element}` -- the element leaves its module;
- `capture {module}` -- the module's source becomes its element records.
Distinct verb names from the snapshot regime's `retire` / `cutover`, which key on (repo_key,
module_path): one verb, one shape.

NODES (`derive_captured`): composed text parsed once for properties (calls, refs, lineno, docstring,
decorators, module imports); every node keyed by its element id. The imports element is ONE CodeText
node; a class-level text member is a CodeText node carrying `parent_id` + `member_index` (never
`order_index`, which marks a top-level region); a class CONTAINS its ordered members. A def nested in a
function is no element: its id derives through the identity map, which `IdentityWalk` keeps fed from
element renames, moves and retires (`ElementFold.addresses`). INVARIANT: `lift_imports` of the composed
text reproduces the elements -- a composition that does not is refused (`CodeSourceError`).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set, Tuple

from cjm_context_graph_layer.grammar import make_edge
from cjm_context_graph_primitives.provenance import SourceRef
from cjm_dev_graph_schema.identity import code_module_node_id, code_text_node_id
from cjm_dev_graph_schema.nodes import CodeTextNode
from cjm_dev_graph_schema.vocab import DevRelations
from cjm_python_decompose_core.elements import (_parse_own, binding_union, CodeElement,
                                                compose_module, import_slot, IMPORTS, lift_imports)
from cjm_python_decompose_core.emit import render_import_block, sibling_modules
from cjm_python_decompose_core.extract import _text_region_kind, decompose_text
from cjm_python_decompose_core.parse import _text_region_key, parse_regions, SourceRegion

MODULE = "module"                  # A module's own state: {module, repo_key, module_path, import_name}
ELEMENT = "element"                # An element's content: {element, kind, text, bindings}
PLACE = "place"                    # An element's placement: {element, module, parent | null, after | null}
ELEMENT_RETIRE = "element-retire"  # An element leaves its module: {element}
CAPTURE = "capture"                # The module's source becomes its element records: {module}
ELEMENT_VERBS = (MODULE, ELEMENT, PLACE, ELEMENT_RETIRE, CAPTURE)

Key = Tuple[str, str]  # (repo_key, module_path) -- journal-space, as the snapshot regime keys


class CodeSourceError(ValueError):
    """A captured module whose element state does not compose, or does not decompose back to itself."""


@dataclass
class ModuleState:
    """A journaled module's own state."""

    module: str                  # The CodeModule id
    repo_key: str                # Journal-space repo key (the repo's directory name, as on source records)
    module_path: str             # Repo-relative path
    import_name: str = ""        # Dotted import name
    captured: bool = False       # The element records are its source


@dataclass
class ElementState:
    """A journaled element's content and placement."""

    element: str                         # The element (node) id
    kind: str = ""                       # symbol | text | imports
    text: str = ""                       # Its own text at depth 0 ("" for an imports element)
    bindings: list = field(default_factory=list)  # The import descriptors it owns
    module: Optional[str] = None         # The module it is placed in
    parent: Optional[str] = None         # Its enclosing class element (None: top level)
    after: Optional[str] = None          # The sibling it follows (None: the first)
    retired: bool = False                # Out of its module


def _symbol_name(
    text: str,  # A symbol element's own text
) -> str:  # The def / class name it declares
    """A symbol element's own name -- the last statement of its own text is its def or class."""
    node = _parse_own(text).body[-1]
    return getattr(node, "name", "")


class ElementFold:
    """The element records folded to state: modules, elements, every id a record has named."""

    def __init__(self):
        self.modules: Dict[str, ModuleState] = {}
        self.by_key: Dict[Key, str] = {}
        self.elements: Dict[str, ElementState] = {}
        self.ever: Set[str] = set()
        self._names: Dict[str, str] = {}  # symbol element text -> its own name (cache)

    def apply(
        self,
        rec: Dict[str, Any],  # One element-regime record
    ) -> Set[str]:  # The module ids it touches
        """Fold one record into the state."""
        verb, a = rec.get("verb"), rec.get("args") or {}
        if verb == MODULE:
            m = self.modules.get(a["module"])
            if m is None:
                m = self.modules[a["module"]] = ModuleState(a["module"], a["repo_key"], a["module_path"])
            else:
                self.by_key.pop((m.repo_key, m.module_path), None)
                m.repo_key, m.module_path = a["repo_key"], a["module_path"]
            m.import_name = a.get("import_name") or ""
            self.by_key[(m.repo_key, m.module_path)] = m.module
            return {m.module}
        if verb == CAPTURE:
            self.modules[a["module"]].captured = True
            return {a["module"]}
        eid = a.get("element")
        if not eid:
            return set()
        self.ever.add(eid)
        e = self.elements.setdefault(eid, ElementState(eid))
        if verb == ELEMENT:
            e.kind, e.text, e.bindings = a["kind"], a.get("text") or "", list(a.get("bindings") or [])
            return {e.module} if e.module else set()
        if verb == PLACE:
            old = e.module
            e.module, e.parent, e.after, e.retired = a["module"], a.get("parent"), a.get("after"), False
            return {m for m in (old, e.module) if m}
        if verb == ELEMENT_RETIRE:
            e.retired = True
            return {e.module} if e.module else set()
        return set()

    def end(
        self,
        key: Key,  # A module key the snapshot regime retired
    ) -> Optional[str]:  # The module id whose element regime ended, or None
        """A module-level retire ends the key -- and with it the element regime of the module holding it."""
        mid = self.by_key.pop(key, None)
        if mid is not None:
            self.modules[mid].captured = False
        return mid

    def captured(
        self,
        key: Key,  # A module key
    ) -> Optional[str]:  # The captured module's id, or None
        mid = self.by_key.get(key)
        return mid if mid is not None and self.modules[mid].captured else None

    def outline(
        self,
        mid: str,  # A module id
    ) -> List[Tuple[str, Optional[str]]]:  # Its placed elements in pre-order: (id, parent id); the imports element excluded
        """The module's elements in pre-order, each parent's children chained by `after`. A broken
        chain (no first, two firsts, a cycle, an `after` naming no sibling) is refused."""
        kids: Dict[Optional[str], List[ElementState]] = {}
        for e in self.elements.values():
            if e.module == mid and not e.retired and e.kind != IMPORTS:
                kids.setdefault(e.parent, []).append(e)
        out: List[Tuple[str, Optional[str]]] = []

        def walk(parent: Optional[str]) -> None:
            sibs = kids.get(parent, [])
            nxt = {}
            for e in sibs:
                if e.after in nxt:
                    raise CodeSourceError(f"{mid}: two elements follow {e.after!r} under {parent!r}")
                nxt[e.after] = e
            cur, seen = None, 0
            while cur in nxt:
                e = nxt[cur]
                out.append((e.element, parent))
                seen += 1
                walk(e.element)
                cur = e.element
                if seen > len(sibs):
                    break
            if seen != len(sibs):
                raise CodeSourceError(f"{mid}: the elements under {parent!r} do not form one chain")

        walk(None)
        placed = {i for i, _ in out}
        stray = [p for p in kids if p is not None and p not in placed]
        if stray:
            raise CodeSourceError(f"{mid}: elements placed under {stray[0]!r}, which is not in the module")
        return out

    def imports_element(self, mid: str) -> Optional[str]:  # The module's imports element id, or None
        found = [e.element for e in self.elements.values()
                 if e.module == mid and not e.retired and e.kind == IMPORTS]
        if len(found) > 1:
            raise CodeSourceError(f"{mid}: {len(found)} imports elements (at most one)")
        return found[0] if found else None

    def code_elements(
        self,
        mid: str,  # A module id
    ) -> Tuple[List[CodeElement], List[str]]:  # (its elements in pre-order, the imports element at its slot; their ids)
        """The module's elements as the decompose core composes them."""
        order = self.outline(mid)
        index = {eid: i for i, (eid, _) in enumerate(order)}
        depth: Dict[str, int] = {}
        els: List[CodeElement] = []
        for eid, parent in order:
            e = self.elements[eid]
            depth[eid] = 0 if parent is None else depth[parent] + 1
            els.append(CodeElement(e.kind, e.text, depth[eid], None if parent is None else index[parent],
                                   bindings=list(e.bindings)))
        ids = [eid for eid, _ in order]
        imp = self.imports_element(mid)
        if imp is not None:
            slot = import_slot(els)
            els = [CodeElement(e.kind, e.text, e.depth,
                               None if e.parent is None else e.parent + (e.parent >= slot),
                               e.qualname, e.symbol_kind, e.bindings) for e in els]
            els.insert(slot, CodeElement(IMPORTS, "", bindings=list(self.elements[imp].bindings)))
            ids.insert(slot, imp)
        return els, ids

    def addresses(
        self,
        mid: str,  # A captured module id
    ) -> Dict[str, str]:  # symbol element id -> its qualname now
        """Every symbol element's qualname, derived from its own name and its parents' -- the address
        the identity map keys on."""
        out: Dict[str, str] = {}
        for eid, parent in self.outline(mid):
            e = self.elements[eid]
            if e.kind != "symbol":
                continue
            name = self._names.get(e.text)
            if name is None:
                name = self._names[e.text] = _symbol_name(e.text)
            out[eid] = f"{out[parent]}.{name}" if parent is not None and parent in out else name
        return out


class LatestSources(Mapping):
    """`latest_source_ops`' answer over both regimes: (repo_key, module_path) -> the module's latest
    args, a captured module's `text` composed on first access (most readers ask for one key)."""

    def __init__(self, state: "SourceState"):
        self._state = state
        self._cache: Dict[Key, Dict[str, Any]] = {}

    def __getitem__(self, key: Key) -> Dict[str, Any]:
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        st = self._state
        mid = st.elements.captured(key)
        if mid is None:
            return st.latest[key]
        m = st.elements.modules[mid]
        args = {"repo_key": key[0], "module_path": key[1], "import_name": m.import_name,
                "text": st.compose(mid)}
        self._cache[key] = args
        return args

    def __iter__(self) -> Iterator[Key]:
        return iter(self._state.live_keys())

    def __len__(self) -> int:
        return len(self._state.live_keys())

    def __contains__(self, key: object) -> bool:
        return key in self._state.latest or (isinstance(key, tuple) and self._state.elements.captured(key) is not None)


class SourceState:
    """The code source journal folded over BOTH regimes: the latest snapshot per key, the phase-2
    cutover set, and the element regime's state."""

    def __init__(self):
        self.latest: Dict[Key, Dict[str, Any]] = {}  # snapshot regime: key -> latest source args
        self.sourced: Set[Key] = set()               # keys past the phase-2 cutover
        self.elements = ElementFold()
        self.conflicts: List[Dict[str, Any]] = []    # snapshot records naming a captured key (reported, never applied)

    @classmethod
    def over(cls, records: Iterable[Dict[str, Any]]) -> "SourceState":  # The state after every record
        st = cls()
        for rec in records:
            st.apply(rec)
        return st

    def apply(
        self,
        rec: Dict[str, Any],  # One source-journal record
    ) -> Set[str]:  # The element-regime module ids it touches (empty for a snapshot record)
        verb, a = rec.get("verb"), rec.get("args") or {}
        if verb in ELEMENT_VERBS:
            return self.elements.apply(rec)
        key = (a.get("repo_key"), a.get("module_path"))
        if verb == "source":
            if self.elements.captured(key) is not None:
                self.conflicts.append(rec)  # a snapshot after capture: refused at write, never applied
            else:
                self.latest[key] = a
        elif verb == "retire":
            self.latest.pop(key, None)
            self.sourced.discard(key)
            mid = self.elements.end(key)
            return {mid} if mid else set()
        elif verb == "cutover":
            self.sourced.add(key)
        return set()

    def live_keys(self) -> Set[Key]:  # Every live module key, both regimes
        return set(self.latest) | {k for k, mid in self.elements.by_key.items()
                                   if self.elements.modules[mid].captured}

    def siblings(
        self,
        key: Key,  # A module key
    ) -> frozenset:  # Its sibling module names (the import block's local section)
        return sibling_modules(key[1], {mp for rk, mp in self.live_keys() if rk == key[0]})

    def compose(
        self,
        mid: str,  # A captured module id
    ) -> str:  # Its text
        m = self.elements.modules[mid]
        els, _ids = self.elements.code_elements(mid)
        return compose_module(els, self.siblings((m.repo_key, m.module_path)))

    def latest_ops(self) -> LatestSources:  # `latest_source_ops`' mapping
        return LatestSources(self)


# -- capture ---------------------------------------------------------------------------------------
def _text_id(
    mid: str,         # The module id
    key: str,         # The birth key (a leading line, member keys prefixed by their class)
    taken: Set[str],  # Ids already named (the journal's `ever` plus this capture's)
) -> str:  # The text element's birth id: its address, a generation suffix when the address was used
    gen, out = 0, code_text_node_id(mid, key)
    while out in taken:
        gen += 1
        out = code_text_node_id(mid, f"{key} #{gen}")
    return out


def capture_records(
    repo_key: str,            # Journal-space repo key
    conceptual: str,          # Its conceptual key (the identity space ids derive in)
    module_path: str,         # Repo-relative path
    import_name: str,         # Dotted import name
    text: str,                # The module's canonical snapshot text
    symbol_identity: Any = None,  # The identity hook as of the capture (qualname -> birth fields)
    ever: Optional[Set[str]] = None,  # Element ids the journal already named (a birth never reuses one)
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:  # (the capture's records, its report)
    """The records that capture one module into the element regime, and what the capture changes:
    every symbol keeps the id the snapshot derivation gives it; a top-level text element keeps the id
    of the region it holds content of; the imports element takes the first import region the lift
    empties, else its birth address; a class-level text member is born. Pure -- the caller appends."""
    mid = code_module_node_id(conceptual, module_path)
    dm = decompose_text(conceptual, module_path, "", text, import_name=import_name,
                        symbol_identity=symbol_identity)
    lifted = lift_imports(text)
    regions = parse_regions(text)
    text_ids = iter(t.id for t in dm.texts)
    region_id = {i: next(text_ids) for i, r in enumerate(regions) if r.kind == "text"}
    sym_id = {s.qualname: s.id for s in dm.symbols}
    taken = set(ever or ()) | set(sym_id.values())
    ids: List[str] = []
    qual: List[str] = []
    for i, e in enumerate(lifted.elements):
        if e.kind == "symbol":
            q = e.qualname
            eid = sym_id[q]
        elif e.kind == IMPORTS:
            q = ""
            eid = (region_id[lifted.imports_origin] if lifted.imports_origin is not None
                   else _text_id(mid, "imports", taken))
        elif e.parent is None and lifted.origins[i] is not None and region_id[lifted.origins[i]] not in taken:
            q = ""
            eid = region_id[lifted.origins[i]]
        else:
            q = ""
            owner = qual[e.parent] if e.parent is not None else ""
            eid = _text_id(mid, f"{owner}: {_text_region_key(e.text)}" if owner else _text_region_key(e.text),
                           taken)
        taken.add(eid)
        ids.append(eid)
        qual.append(q)
    born = [ids[i] for i, e in enumerate(lifted.elements)
            if (e.kind == "text" and (e.parent is not None or lifted.origins[i] is None))
            or (e.kind == IMPORTS and lifted.imports_origin is None)]
    leaving = [region_id[k] for k in lifted.emptied if k != lifted.imports_origin]
    recs: List[Dict[str, Any]] = [{"verb": MODULE, "args": {"module": mid, "repo_key": repo_key,
                                                           "module_path": module_path,
                                                           "import_name": import_name}}]
    for eid, e in zip(ids, lifted.elements):
        recs.append({"verb": ELEMENT, "args": {"element": eid, "kind": e.kind,
                                               "text": "" if e.kind == IMPORTS else e.text,
                                               "bindings": list(e.bindings)}})
    last: Dict[Optional[int], Optional[str]] = {}
    for i, (eid, e) in enumerate(zip(ids, lifted.elements)):
        if e.kind == IMPORTS:
            recs.append({"verb": PLACE, "args": {"element": eid, "module": mid, "parent": None,
                                                 "after": None}})
            continue
        recs.append({"verb": PLACE, "args": {"element": eid, "module": mid,
                                             "parent": None if e.parent is None else ids[e.parent],
                                             "after": last.get(e.parent)}})
        last[e.parent] = eid
    recs.append({"verb": CAPTURE, "args": {"module": mid}})
    imports = next(e for e in lifted.elements if e.kind == IMPORTS)
    report = {"module": mid, "repo_key": repo_key, "module_path": module_path,
              "elements": len(ids), "born": born, "leaving": leaving,
              "unclaimed": [b.get("name") for b in imports.bindings]}
    return recs, report


# -- derivation --------------------------------------------------------------------------------------
def _shape(els: List[CodeElement]) -> List[Tuple]:
    """What the element invariant compares: kinds, depths, parents, own texts (an imports element's
    is derived) and the bindings each element owns."""
    return [(e.kind, e.depth, e.parent, "" if e.kind == IMPORTS else e.text,
             sorted(tuple(sorted(b.items())) for b in e.bindings)) for e in els]


def derive_captured(
    state: SourceState,      # The source state (element regime + inventory)
    mid: str,                # The captured module id
    conceptual: str,         # The module's conceptual repo key
    path: str,               # The provenance path (repos_dir/<dir>/<module_path>)
    symbol_identity: Any = None,  # The identity hook for the module (closures' ids)
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:  # (node wires, local edges, composed text)
    """A captured module's nodes and local edges, every node keyed by its element id (see the module
    docstring). Raises `CodeSourceError` when the state does not compose back to itself."""
    m = state.elements.modules[mid]
    els, ids = state.elements.code_elements(mid)
    text = compose_module(els, state.siblings((m.repo_key, m.module_path)))
    try:
        back = lift_imports(text).elements
    except (SyntaxError, ValueError) as e:
        raise CodeSourceError(f"{m.module_path}: the composed text does not parse: {e}") from e
    if _shape(back) != _shape(els):
        raise CodeSourceError(f"{m.module_path}: the composed text does not decompose to its elements")
    dm = decompose_text(conceptual, m.module_path, path, text, import_name=m.import_name or None,
                        symbol_identity=symbol_identity)
    if dm.module.id != mid:
        raise CodeSourceError(f"{m.module_path}: module id {dm.module.id} is not the journaled {mid}")
    by_qual: Dict[str, int] = {}
    for i, e in enumerate(back):
        if e.kind == "symbol":
            if e.qualname in by_qual:
                raise CodeSourceError(f"{m.module_path}: two elements named {e.qualname}")
            by_qual[e.qualname] = i
    remap: Dict[str, str] = {}
    wires: List[Dict[str, Any]] = [dm.module.to_graph_node()]
    top = [i for i, e in enumerate(els) if e.parent is None]
    order = {i: n for n, i in enumerate(top)}
    for s in dm.symbols:
        w = s.to_graph_node()
        i = by_qual.get(s.qualname)
        if i is not None:
            remap[w["id"]] = ids[i]
            w["id"] = ids[i]
            props = w["properties"]
            if els[i].bindings:
                props["import_bindings"] = list(els[i].bindings)
            else:
                props.pop("import_bindings", None)
            if i in order:
                props["order_index"] = order[i]
        wires.append(w)
    kids: Dict[int, List[int]] = {}
    for i, e in enumerate(els):
        if e.parent is not None:
            kids.setdefault(e.parent, []).append(i)
    block = render_import_block(binding_union(els), state.siblings((m.repo_key, m.module_path)))
    for i, e in enumerate(els):
        if e.kind == "symbol":
            continue
        t = block if e.kind == IMPORTS else e.text
        if not t:
            continue  # an empty import block composes to nothing and projects no node
        region = SourceRegion(kind="text", text=t, start_line=0, end_line=0)
        node = CodeTextNode(module_id=mid,
                            region_key=IMPORTS if e.kind == IMPORTS else _text_region_key(t),
                            text=t, content_hash=SourceRef.compute_hash(t.encode("utf-8")),
                            order_index=order.get(i), path=path,
                            kind=IMPORTS if e.kind == IMPORTS else _text_region_kind(region))
        w = node.to_graph_node()
        w["id"] = ids[i]
        if e.parent is not None:
            w["properties"]["parent_id"] = ids[e.parent]
            w["properties"]["member_index"] = kids[e.parent].index(i)
        wires.append(w)
    present = {w["id"] for w in wires}
    local: List[Dict[str, Any]] = [dm.module.about_edge()]
    for edge in dm.local_edges:
        if edge["relation_type"] != DevRelations.DEFINES:
            continue
        src, dst = remap.get(edge["source_id"], edge["source_id"]), remap.get(edge["target_id"], edge["target_id"])
        local.append(make_edge(src, dst, DevRelations.DEFINES))
    local.extend(make_edge(mid, ids[i], DevRelations.CONTAINS) for i in top if ids[i] in present)
    for p, members in kids.items():
        local.extend(make_edge(ids[p], ids[i], DevRelations.CONTAINS) for i in members if ids[i] in present)
    return wires, local, text
