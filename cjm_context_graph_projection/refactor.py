"""Refactoring-candidate identification over the code graph (the IDENTIFY half of move).

The cross-corpus call/import graph + the region/body data make refactoring candidates
fall out as QUERIES ([[graph-as-source-of-truth-inversion]] "refactoring as edge updates"):

- `relocation`: a top-level symbol whose in-corpus callers are ALL in a DIFFERENT repo
  than its own. A clean cross-repo call REQUIRES the caller to import the callee's repo,
  so this alone just describes normal layering (a foundation type consumed downstream) —
  the ACTIONABLE tier is `cycle: true`, where the home repo ALSO imports the caller repo
  (a mutual dependency the symbol participates in). The future `move` verb executes a
  relocation as a `CONTAINS`/`DEFINES` edge update (stable ids keep `CALLS` intact).
- `dead_code`: a public top-level symbol with NO in-corpus callers (a removal candidate).
  WEAK signal for library code — a public API used only by OUT-of-corpus consumers also
  has no in-corpus caller — so this is propose/confirm, never an automatic verdict.
- `consolidation`: the same bare name defined as a free function/class in ≥2 DIFFERENT
  repos (possible duplication to hoist into a shared lib).
- `split`: a non-granular cell (>1 public def) whose co-located symbols have DIVERGENT
  call-neighborhoods (no shared caller/callee) — genuinely separable, vs. a shared
  neighborhood = a legitimate concept pair (needs notebooks in the graph to light up).

Findings are CANDIDATES, not verdicts (the conventions-audit posture): the compute is a
PURE function over node/edge lists; the async wrapper loads the graph slices it needs.
"""

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from . import factlayer as F

# Names whose missing in-corpus callers are expected (not dead-code signal).
_DEAD_SKIP_PREFIXES = ("_", "test_")
_DEAD_SKIP_NAMES = {"main"}


def _is_top(qualname: str) -> bool:
    """A top-level (un-nested) symbol — qualname carries no `.`."""
    return "." not in qualname


def _is_dunder(name: str) -> bool:
    """A dunder name (`__x__`) — polymorphic/protocol, never a duplication/dead signal."""
    return name.startswith("__") and name.endswith("__")


def compute_refactor_candidates(
    symbols: Iterable[Any],                 # CodeSymbol nodes (GraphNodes or wire dicts)
    modules: Iterable[Any],                 # CodeModule nodes (for repo_key + module_path)
    calls: Iterable[Tuple[str, str]],       # CALLS (caller_sym_id, callee_sym_id) pairs
    imports: Iterable[Tuple[str, str]] = (),  # IMPORTS (importer_module_id, imported_module_id) pairs
    scope: Optional[str] = None,            # Restrict to one repo_key (None = whole corpus)
) -> Dict[str, Any]:  # The candidate result (counts + finding lists)
    """Compute refactoring candidates from the code graph slices (pure)."""
    mod = {F.nid(m): (F.prop(m, "repo_key", ""), F.prop(m, "module_path", "")) for m in modules}
    # repo-level dependency from module IMPORTS: {repo_a: {repos a imports}}.
    repo_imports: Dict[str, Set[str]] = {}
    for imp_src, imp_tgt in imports:
        ra = mod.get(imp_src, ("", ""))[0]
        rb = mod.get(imp_tgt, ("", ""))[0]
        if ra and rb and ra != rb:
            repo_imports.setdefault(ra, set()).add(rb)
    sym: Dict[str, Dict[str, Any]] = {}
    for s in symbols:
        sid = F.nid(s)
        mid = F.prop(s, "module_id")
        repo, mpath = mod.get(mid, ("", ""))
        qual = F.prop(s, "qualname", "") or ""
        sym[sid] = {
            "id": sid, "qualname": qual, "name": qual.split(".")[-1], "kind": F.prop(s, "symbol_kind", ""),
            "module_id": mid, "repo": repo, "module_path": mpath,
            "cell_key": F.prop(s, "cell_key"), "top": _is_top(qual),
        }

    callers: Dict[str, Set[str]] = {}   # callee -> caller ids
    callees: Dict[str, Set[str]] = {}   # caller -> callee ids
    for src, tgt in calls:
        if src in sym and tgt in sym:
            callers.setdefault(tgt, set()).add(src)
            callees.setdefault(src, set()).add(tgt)

    def in_scope(repo: str) -> bool:
        return scope is None or repo == scope

    relocation: List[Dict[str, Any]] = []
    dead_code: List[Dict[str, Any]] = []
    for sid, s in sym.items():
        if not s["top"] or not in_scope(s["repo"]) or _is_dunder(s["name"]):
            continue
        callr = callers.get(sid, set())
        caller_repos: Dict[str, int] = {}
        for c in callr:
            caller_repos[sym[c]["repo"]] = caller_repos.get(sym[c]["repo"], 0) + 1
        if callr and s["repo"] and s["repo"] not in caller_repos:
            # every in-corpus caller is in another repo -> relocation candidate.
            # `cycle` = the home repo ALSO imports a caller repo (mutual dep -> actionable);
            # otherwise it is the expected downstream-consumer case (low precision on a
            # small/foundation-heavy corpus).
            home_deps = repo_imports.get(s["repo"], set())
            cycle = any(r in home_deps for r in caller_repos)
            relocation.append({"id": sid, "qualname": s["qualname"], "home_repo": s["repo"],
                               "caller_repos": caller_repos, "cycle": cycle})
        elif not callr:
            nm = s["name"]
            if (s["kind"] in ("function", "class") and not _is_dunder(nm)
                    and not nm.startswith(_DEAD_SKIP_PREFIXES) and nm not in _DEAD_SKIP_NAMES
                    and not s["module_path"].endswith("__init__.py")):
                dead_code.append({"id": sid, "qualname": s["qualname"], "repo": s["repo"],
                                  "module_path": s["module_path"], "kind": s["kind"]})

    # consolidation: same bare name as a free function/class across ≥2 repos.
    by_name: Dict[str, List[Dict[str, Any]]] = {}
    for sid, s in sym.items():
        if s["top"] and s["kind"] in ("function", "class") and not _is_dunder(s["name"]) \
                and not s["name"].startswith("_") and in_scope(s["repo"]):
            by_name.setdefault(s["name"], []).append(s)
    consolidation = []
    for name, occ in sorted(by_name.items()):
        repos = {o["repo"] for o in occ}
        if len(repos) >= 2:
            consolidation.append({"name": name, "repos": sorted(repos),
                                  "occurrences": [{"id": o["id"], "qualname": o["qualname"],
                                                   "repo": o["repo"], "module_path": o["module_path"]}
                                                  for o in occ]})

    # split: non-granular cells whose co-located public symbols have divergent neighborhoods.
    by_cell: Dict[str, List[str]] = {}
    for sid, s in sym.items():
        ck = s["cell_key"]
        if ck is not None and s["top"] and not s["name"].startswith("_") and in_scope(s["repo"]):
            by_cell.setdefault(f"{s['module_id']}::{ck}", []).append(sid)
    split = []
    for cell, ids in by_cell.items():
        if len(ids) < 2:
            continue
        hoods = {i: (callers.get(i, set()) | callees.get(i, set())) - {i} for i in ids}
        divergent = all(not (hoods[a] & hoods[b]) for x, a in enumerate(ids) for b in ids[x + 1:])
        if divergent:
            split.append({"cell": cell, "symbols": sorted(sym[i]["qualname"] for i in ids)})

    # cycle-tier first (actionable), then the expected downstream-consumer findings.
    relocation.sort(key=lambda r: (not r["cycle"], r["qualname"]))
    return {
        "scope": scope,
        "counts": {"relocation": len(relocation),
                   "relocation_cycles": sum(1 for r in relocation if r["cycle"]),
                   "dead_code": len(dead_code),
                   "consolidation": len(consolidation), "split": len(split)},
        "relocation": relocation, "dead_code": dead_code,
        "consolidation": consolidation, "split": split,
    }


async def refactor_candidates(
    gx,
    scope: Optional[str] = None,  # Restrict to one repo_key (None = whole corpus)
) -> Dict[str, Any]:  # The candidate result
    """Identify relocation / dead-code / consolidation / split candidates over the code graph."""
    symbols = await F.load_label(gx, DevNodeKinds.CODE_SYMBOL)
    modules = await F.load_label(gx, DevNodeKinds.CODE_MODULE)
    calls = await F.load_edge_pairs(gx, DevRelations.CALLS)
    imports = await F.load_edge_pairs(gx, DevRelations.IMPORTS)
    return compute_refactor_candidates(symbols, modules, calls, imports, scope)
