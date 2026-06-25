"""Module cohesion audit over the code graph — the read-only cohesion ORACLE (N+1).

[[true-b-projected-structure-discussion]] ratified the projected-`.py` organization policy
as COHESION, not symbol count, surfaced by an ADVISORY oracle (propose/confirm, NOT
auto-layout). This is that oracle's read side: it measures call-neighborhood cohesion at
MODULE scope (generalizing the cell-level `split` bucket of `refactor.py`) and proposes two
directions of regrouping as candidates a human/agent confirms or rejects:

- `under_split` (grab-bag): a module of >= `min_symbols` public top-level symbols whose
  COUPLING graph splits into >= 2 components with NO dominant cluster (largest component
  <= half the symbols). Coupling = a direct USES reference, a shared in-corpus reference
  neighborhood, OR a shared significant NAME token. The disconnected components are reported
  as suggested split groups. `cjm-pytorch-utils/core.py` (image / device / seed / stats
  fused in one module) is the worked example.
- `over_split` (scattered concept): a public symbol whose in-corpus callers are ALL in a
  SINGLE OTHER module of the SAME repo — a helper living apart from its only consumer, a
  candidate to merge in. The within-repo analogue of refactor's cross-repo relocation (the
  cross-cell `@patch` scars were the within-notebook version).

NAME-TOKEN coupling is the cheap concept signal that call-graph cohesion alone lacks: a
`is_linux`/`is_macos` predicate family or a `*Error` hierarchy never call each other, so
pure call-coupling fragments them — but they share `is` / `error`, so token coupling fuses
them and they are correctly NOT flagged (demonstrating cohesion > the nbdev one-def-per-cell
rule). The "no dominant cluster" gate further damps a module that is a coherent core class
plus a few satellite types/enums (one big cluster) — only a module with NO center is a
grab-bag. Coupling is measured over USES (the reference superset of CALLS — N+2-B2), so a
type-only relationship (a base class, a field/param/return annotation type, a referenced
constant) couples symbols too: this closes the call-graph-cohesion blind spot on type-rich
modules (dataclasses/enums/exception hierarchies related by inheritance/composition, not
calls) that the first cut surfaced. Both buckets stay PROPOSE/CONFIRM, low precision by
design (a rejected proposal is itself a useful label — [[true-b-projected-structure-discussion]]
Ext-A, propose/confirm verdicts = training data). The compute is a PURE function over
node/edge lists; the async wrapper loads the slices.
"""

import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from . import factlayer as F


def _is_top(qualname: str) -> bool:
    """A top-level (un-nested) symbol — qualname carries no `.`."""
    return "." not in qualname


def _is_public(name: str) -> bool:
    """A non-underscore-prefixed bare name (the audited surface)."""
    return bool(name) and not name.startswith("_")


_TOKEN_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
# Generic tokens that should NOT by themselves couple two symbols (verbs/prepositions
# that appear across unrelated concerns — e.g. get_torch_device vs get_config).
_STOP_TOKENS = {"get", "set", "to", "from", "of", "run", "make", "build", "create",
                "the", "a", "an", "and", "or", "for", "with", "new", "init"}


def _tokens(name: str) -> Set[str]:
    """Significant lowercase token set of a name (snake_case + camelCase aware).

    `CapabilityError` -> {capability, error}; `is_apple_silicon` -> {is, apple, silicon};
    `get_torch_device` -> {torch, device} (generic `get` dropped). A non-empty intersection
    between two names = NAME coupling (the cheap concept signal call-graph cohesion lacks)."""
    out: Set[str] = set()
    for part in name.split("_"):
        out |= {t.lower() for t in _TOKEN_RE.findall(part) if t}
    return out - _STOP_TOKENS


def _components(ids: List[str], adj: Dict[str, Set[str]]) -> List[List[str]]:
    """Connected components of `ids` under adjacency `adj` (BFS; input order kept)."""
    seen: Set[str] = set()
    comps: List[List[str]] = []
    for start in ids:
        if start in seen:
            continue
        stack, comp = [start], []
        seen.add(start)
        while stack:
            n = stack.pop()
            comp.append(n)
            for m in adj.get(n, ()):  # neighbours within the module
                if m not in seen:
                    seen.add(m)
                    stack.append(m)
        comps.append(sorted(comp))
    return comps


def compute_cohesion(
    symbols: Iterable[Any],            # CodeSymbol nodes (GraphNodes or wire dicts)
    modules: Iterable[Any],            # CodeModule nodes (for repo_key + module_path)
    uses: Iterable[Tuple[str, str]],   # USES (referencer_sym_id, referenced_sym_id) pairs — the CALLS superset
    scope: Optional[str] = None,       # Restrict to one repo_key (None = whole corpus)
    min_symbols: int = 4,              # Min public symbols for a module to be grab-bag-audited
) -> Dict[str, Any]:  # The cohesion result (counts + finding lists)
    """Compute module cohesion candidates from the code graph slices (pure).

    Coupling is measured over USES (the reference superset of CALLS), so type-only
    relationships (a base class, a field/annotation type) couple symbols too — closing
    the call-graph-cohesion blind spot on type-rich modules."""
    mod = {F.nid(m): (F.prop(m, "repo_key", ""), F.prop(m, "module_path", "")) for m in modules}
    sym: Dict[str, Dict[str, Any]] = {}
    for s in symbols:
        sid = F.nid(s)
        mid = F.prop(s, "module_id")
        repo, mpath = mod.get(mid, ("", ""))
        qual = F.prop(s, "qualname", "") or ""
        name = qual.split(".")[-1]
        sym[sid] = {"id": sid, "qualname": qual, "name": name, "kind": F.prop(s, "symbol_kind", ""),
                    "module_id": mid, "repo": repo, "module_path": mpath,
                    "top": _is_top(qual), "public": _is_public(name)}

    callers: Dict[str, Set[str]] = {}   # callee -> caller ids
    callees: Dict[str, Set[str]] = {}   # caller -> callee ids
    for src, tgt in uses:
        if src in sym and tgt in sym:
            callers.setdefault(tgt, set()).add(src)
            callees.setdefault(src, set()).add(tgt)

    def in_scope(repo: str) -> bool:
        return scope is None or repo == scope

    # --- under_split: per-module coupling components on the public top-level surface ---
    by_mod: Dict[str, List[str]] = {}
    for sid, s in sym.items():
        if s["top"] and s["public"] and in_scope(s["repo"]):
            by_mod.setdefault(s["module_id"], []).append(sid)
    under_split: List[Dict[str, Any]] = []
    dominant_damped = 0
    for mid, ids in by_mod.items():
        if len(ids) < min_symbols:
            continue
        toks = {i: _tokens(sym[i]["name"]) for i in ids}
        adj: Dict[str, Set[str]] = {i: set() for i in ids}
        for x, a in enumerate(ids):
            ha = callers.get(a, set()) | callees.get(a, set())
            for b in ids[x + 1:]:
                hb = callers.get(b, set()) | callees.get(b, set())
                direct = (b in callees.get(a, set())) or (a in callees.get(b, set()))
                shared = bool((ha & hb) - {a, b})
                named = bool(toks[a] & toks[b])     # a shared significant NAME token
                if direct or shared or named:
                    adj[a].add(b)
                    adj[b].add(a)
        comps = _components(ids, adj)
        if len(comps) < 2:                          # one cohesive cluster -> fine
            continue
        if max(len(c) for c in comps) * 2 > len(ids):  # a dominant core + satellites -> fine
            dominant_damped += 1
            continue
        repo, mpath = mod.get(mid, ("", ""))
        under_split.append({"module_id": mid, "module_path": mpath, "repo": repo,
                            "num_symbols": len(ids), "num_components": len(comps),
                            "groups": [sorted(sym[i]["qualname"] for i in c) for c in comps]})
    under_split.sort(key=lambda u: (-u["num_components"], u["module_path"]))

    # A module's fan-out = the distinct OTHER modules it calls into. A high-fan-out module is
    # a DRIVER/aggregator (e.g. a CLI dispatching to every command) — a helper used "only" by
    # such a module is expected layering, NOT a scattered concept (the over_split analogue of
    # refactor's "expected foundation layering" relocation tier).
    fanout: Dict[str, Set[str]] = {}
    for src, tgt in uses:
        if src in sym and tgt in sym:
            sm, tm = sym[src]["module_id"], sym[tgt]["module_id"]
            if sm and tm and sm != tm:
                fanout.setdefault(sm, set()).add(tm)
    driver_fanout = 4

    # --- over_split: a public symbol whose only callers live in ONE other same-repo module ---
    over_split: List[Dict[str, Any]] = []
    over_split_driver_damped = 0
    for sid, s in sym.items():
        if not (s["top"] and s["public"] and in_scope(s["repo"])):
            continue
        if s["module_path"].endswith("__init__.py"):
            continue
        cr = callers.get(sid, set())
        if not cr:
            continue
        caller_mods = {sym[c]["module_id"] for c in cr if c in sym}
        if len(caller_mods) != 1:
            continue
        cm = next(iter(caller_mods))
        crepo, cpath = mod.get(cm, ("", ""))
        if cm == s["module_id"] or crepo != s["repo"]:
            continue
        if len(fanout.get(cm, set())) >= driver_fanout:   # the consumer is a driver -> expected
            over_split_driver_damped += 1
            continue
        over_split.append({"id": sid, "qualname": s["qualname"], "repo": s["repo"],
                          "home_module": s["module_path"], "consumer_module": cpath,
                          "num_callers": len(cr)})
    over_split.sort(key=lambda o: (o["consumer_module"], o["qualname"]))

    return {
        "scope": scope,
        "counts": {"under_split": len(under_split), "over_split": len(over_split),
                   "dominant_damped": dominant_damped,
                   "over_split_driver_damped": over_split_driver_damped},
        "under_split": under_split,
        "over_split": over_split,
    }


async def cohesion(
    gx,
    scope: Optional[str] = None,  # Restrict to one repo_key (None = whole corpus)
) -> Dict[str, Any]:  # The cohesion result
    """Audit module cohesion: grab-bag (under_split) + scattered-helper (over_split) candidates."""
    symbols = await F.load_label(gx, DevNodeKinds.CODE_SYMBOL)
    modules = await F.load_label(gx, DevNodeKinds.CODE_MODULE)
    uses = await F.load_edge_pairs(gx, DevRelations.USES)
    return compute_cohesion(symbols, modules, uses, scope)
