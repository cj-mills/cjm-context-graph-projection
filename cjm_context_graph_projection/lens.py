"""Lenses: graph-carried, parameterized views (DEC `f1b02b95` — tier 2 of the
presentation vocabulary, atop the per-kind display rules `3904190c`).

A Lens is a DECLARATIVE, RE-EVALUATABLE view spec stored as an on-graph node —
never a stored result set. Its SELECTION speaks the read-verb vocabulary (each
clause = {verb, args} over the existing read layer; richer filtering = richer
verb args, never a parallel query language); clauses UNION. An APPLICATION =
lens + bound typed params. EXPAND/projection rides the bulk `subgraph_view`
verb, so applying a lens is representative of the real read layer and its
result is directly canvas-placeable. Lenses live PER-GRAPH and upsert by slug
(deterministic id, journal-native like `display-rule`), so user-authored
lenses are agent-usable pulls and vice versa (the 8f983e28 symmetry thesis)."""

import copy
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from cjm_context_graph_layer.identity import derive_node_id
from cjm_context_graph_layer.ops import extend_graph, graph_task

from . import factlayer as F
from .listing import list_graph
from .projection import grep, relevant, subgraph_view
from .readiness import readiness
from .runtime import GraphHandle

LENS_LABEL = "Lens"

# The frozen-small v1 grammar (invariant 6: validated property blob first;
# reify innards only on evidence — feeds a85327b1).
SELECTION_VERBS = ("list", "relevant", "grep", "readiness", "journal-window", "subgraph")
PARAM_TYPES = ("string", "timestamp", "node-ref")
VIEW_KEYS = ("layout", "hide_kinds", "group_by", "color_by")
EXPAND_KEYS = ("hops", "relations")


def lens_node_id(slug: str) -> str:
    """Deterministic Lens id — one lens per slug, so re-authoring converges."""
    return derive_node_id("lens", slug)


def _coerce_ts(value: Any) -> float:
    """A timestamp param value -> unix seconds (unix float, YYYY-MM-DD_HH-MM-SS
    local — the session-key form DEC 6124d8bf — or bare YYYY-MM-DD)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%d_%H-%M-%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value), fmt).timestamp()
        except ValueError:
            continue
    raise ValueError(f"can't parse timestamp {value!r} "
                     "(unix seconds, YYYY-MM-DD_HH-MM-SS, or YYYY-MM-DD)")


def validate_lens_spec(spec: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Parse-validate a lens spec against the v1 shape; a bad spec NEVER lands.

    Returns (normalized, None) or (None, error). The grammar is deliberately
    frozen-small: params [{name,type,required?,default?}] · selection
    [{verb,args}] (non-empty LIST — clauses union) · expand {hops,relations?}
    · view {layout?,hide_kinds?,group_by?,color_by?}."""
    if not isinstance(spec, dict):
        return None, "spec must be a JSON object"
    errors: List[str] = []
    unknown = set(spec) - {"params", "selection", "expand", "view"}
    if unknown:
        errors.append(f"unknown spec key(s): {sorted(unknown)}")

    params = spec.get("params", [])
    if not isinstance(params, list):
        errors.append("params must be a list")
        params = []
    names = set()
    for i, p in enumerate(params):
        if not isinstance(p, dict) or not isinstance(p.get("name"), str) or not p.get("name"):
            errors.append(f"params[{i}]: needs a string `name`")
            continue
        if p.get("type", "string") not in PARAM_TYPES:
            errors.append(f"params[{i}] ({p['name']}): type {p.get('type')!r} "
                          f"must be one of {PARAM_TYPES}")
        if p["name"] in names:
            errors.append(f"params[{i}]: duplicate name {p['name']!r}")
        names.add(p["name"])
        bad = set(p) - {"name", "type", "required", "default"}
        if bad:
            errors.append(f"params[{i}] ({p['name']}): unknown key(s) {sorted(bad)}")

    selection = spec.get("selection")
    if not isinstance(selection, list) or not selection:
        errors.append("selection must be a NON-EMPTY list of {verb, args} clauses")
        selection = []
    for i, c in enumerate(selection):
        if not isinstance(c, dict) or c.get("verb") not in SELECTION_VERBS:
            given = c.get("verb") if isinstance(c, dict) else c
            errors.append(f"selection[{i}]: verb {given!r} must be one of {SELECTION_VERBS}")
            continue
        if not isinstance(c.get("args", {}), dict):
            errors.append(f"selection[{i}] ({c['verb']}): args must be an object")
        bad = set(c) - {"verb", "args"}
        if bad:
            errors.append(f"selection[{i}]: unknown key(s) {sorted(bad)}")

    expand = spec.get("expand", {})
    if not isinstance(expand, dict):
        errors.append("expand must be an object")
        expand = {}
    else:
        bad = set(expand) - set(EXPAND_KEYS)
        if bad:
            errors.append(f"expand: unknown key(s) {sorted(bad)} (allowed: {EXPAND_KEYS})")
        if "hops" in expand and (not isinstance(expand["hops"], int) or expand["hops"] < 0):
            errors.append("expand.hops must be an int >= 0")
        if "relations" in expand and not (isinstance(expand["relations"], list)
                                          and all(isinstance(r, str) for r in expand["relations"])):
            errors.append("expand.relations must be a list of relation-type strings")

    view = spec.get("view", {})
    if not isinstance(view, dict):
        errors.append("view must be an object")
        view = {}
    else:
        bad = set(view) - set(VIEW_KEYS)
        if bad:
            errors.append(f"view: unknown key(s) {sorted(bad)} (allowed v1: {VIEW_KEYS})")

    if errors:
        return None, "; ".join(errors)
    return {"params": params, "selection": selection, "expand": expand, "view": view}, None


async def set_lens(
    gx: GraphHandle,
    slug: str,                          # The lens's durable key (upsert identity)
    spec: Any,                          # {params, selection, expand?, view?} (validated here)
    *,
    title: Optional[str] = None,        # Display title (else the slug shows)
    description: Optional[str] = None,  # One orientation line for the shelf
    actor: str = "agent:session",       # Who authored the view
) -> Dict[str, Any]:  # The write result (incl. error on a malformed spec)
    """Author/update a graph-carried Lens (journaled upsert-by-slug).

    Mirrors `set_display_rule`: validate BEFORE writing, deterministic id so a
    re-author UPDATES the node (update_node — never trips the re-extend
    content-hash guard) and the journal's last `set-lens` op per slug wins on
    replay. CONSUMERS BIND THE SLUG, never the title (the session-picker lesson,
    d377f5e6)."""
    if not slug or not isinstance(slug, str):
        return {"error": "a lens needs a slug", "written": False}
    normalized, err = validate_lens_spec(spec)
    if err:
        return {"error": err, "slug": slug, "written": False}

    node_id = lens_node_id(slug)
    props: Dict[str, Any] = {"key": slug, "actor": actor, **normalized}
    if title is not None:
        props["title"] = title
    if description is not None:
        props["description"] = description

    existing = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=node_id)
    if existing is not None:
        await graph_task(gx.queue, gx.graph_id, "update_node", node_id=node_id, properties=props)
        return {"lens_id": node_id, "slug": slug, "updated": True, "written": True}
    node = {"id": node_id, "label": LENS_LABEL, "properties": props, "sources": []}
    await extend_graph(gx.queue, gx.graph_id, [node], [])
    return {"lens_id": node_id, "slug": slug, "updated": False, "written": True}


def _lens_row(n: Any) -> Optional[Dict[str, Any]]:
    """A stored Lens node -> its parsed shelf row (a malformed stored lens is skipped)."""
    slug = F.prop(n, "key")
    if not slug:
        return None
    normalized, err = validate_lens_spec({k: F.prop(n, k) for k in
                                          ("params", "selection", "expand", "view")
                                          if F.prop(n, k) is not None})
    if err:
        return None  # a bad stored lens never breaks reads (display-rule discipline)
    return {"id": F.nid(n), "slug": slug, "title": F.prop(n, "title") or slug,
            "description": F.prop(n, "description"), **normalized}


async def load_lenses(gx: GraphHandle) -> List[Dict[str, Any]]:
    """Every well-formed Lens on this graph (the shelf feed), slug-sorted."""
    rows = [r for r in (_lens_row(n) for n in await F.load_label(gx, LENS_LABEL, limit=1000))
            if r is not None]
    return sorted(rows, key=lambda r: r["slug"])


def bind_params(
    decls: List[Dict[str, Any]],       # The lens's param declarations
    given: Optional[Dict[str, Any]],   # Caller-provided values (strings ok — coerced by type)
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Bind an application's params: defaults + provided, typed, loud on gaps."""
    given = dict(given or {})
    declared = {d["name"] for d in decls}
    stray = set(given) - declared
    if stray:
        return None, (f"unknown param(s) {sorted(stray)} — this lens declares "
                      f"{sorted(declared) or 'none'}")
    bound: Dict[str, Any] = {}
    missing: List[str] = []
    for d in decls:
        name = d["name"]
        if name in given:
            value = given[name]
        elif "default" in d:
            value = d["default"]
        elif d.get("required"):
            missing.append(f"{name} ({d.get('type', 'string')})")
            continue
        else:
            continue
        if d.get("type") == "timestamp":
            try:
                value = _coerce_ts(value)
            except ValueError as e:
                return None, f"param {name}: {e}"
        bound[name] = value
    if missing:
        return None, "missing required param(s): " + ", ".join(missing)
    return bound, None


def _substitute(args: Any, bound: Dict[str, Any]) -> Any:
    """Fill `{name}` placeholders in clause args (deep). A string that IS exactly
    one placeholder takes the bound value's TYPE (a timestamp stays a float);
    embedded placeholders interpolate as text."""
    if isinstance(args, dict):
        return {k: _substitute(v, bound) for k, v in args.items()}
    if isinstance(args, list):
        return [_substitute(v, bound) for v in args]
    if isinstance(args, str):
        for name, value in bound.items():
            if args == "{" + name + "}":
                return copy.deepcopy(value)
        for name, value in bound.items():
            args = args.replace("{" + name + "}", str(value))
        return args
    return args


async def _clause_refs(
    gx: GraphHandle,
    verb: str,
    args: Dict[str, Any],
    journal_paths: Optional[List[str]],
) -> Tuple[List[str], Optional[str]]:
    """Run ONE selection clause through the real read layer -> the refs it selects.

    journal-window yields the journal's raw REFS (not just resolved ids) so an
    op whose node no longer exists stays loud through `subgraph_view.missing`
    (read-parity, 60aae839 theme 4)."""
    if verb == "subgraph":
        refs = args.get("refs")
        if not isinstance(refs, list) or not refs:
            return [], "subgraph clause needs a non-empty `refs` list"
        return [str(r) for r in refs], None
    if verb == "list":
        res = await list_graph(gx, label=args.get("label"), predicate=args.get("predicate"),
                               relation=args.get("relation"), limit=args.get("limit", 500),
                               contains=args.get("contains"), where=args.get("where"),
                               value=args.get("value"))
        if res.get("error"):
            return [], res["error"]
        refs: List[str] = []
        for row in res.get("rows", []):
            for key in ("id", "subject_id", "source_id", "target_id"):
                if row.get(key):
                    refs.append(row[key])
        return refs, None
    if verb == "relevant":
        res = await relevant(gx, args.get("task", ""), k=args.get("k", 12))
        return [r["id"] for r in res.get("results", []) if r.get("id")], None
    if verb == "grep":
        res = await grep(gx, args.get("term", ""), limit=args.get("limit", 25))
        return [m["id"] for m in res.get("matches", []) if m.get("id")], None
    if verb == "readiness":
        res = await readiness(gx, scope=args.get("scope"), state="all")  # selections need every bucket
        states = args.get("states", ["ready", "blocked"])
        if not (isinstance(states, list) and set(states) <= {"ready", "blocked", "done"}):
            return [], "readiness clause: states must be a subset of ready/blocked/done"
        return [e["id"] for s in states for e in res.get(s, []) if e.get("id")], None
    if verb == "journal-window":
        if not journal_paths:
            return [], "journal-window clause needs the graph's journals (none available here)"
        # Function-local: `.journal` imports this module for `set-lens` replay.
        from .journal import journal_window_view
        start = _coerce_ts(args["start"]) if args.get("start") is not None else None
        end = _coerce_ts(args["end"]) if args.get("end") is not None else None
        res = await journal_window_view(gx, journal_paths, start=start, end=end,
                                        session=args.get("session"))
        return [t["ref"] for t in res.get("touched", [])], None
    return [], f"unknown selection verb {verb!r}"


async def apply_lens(
    gx: GraphHandle,
    slug: str,                                   # Which lens (the durable key)
    params: Optional[Dict[str, Any]] = None,     # The application's param bindings
    *,
    journal_paths: Optional[List[str]] = None,   # Needed iff a clause reads the journal
) -> Dict[str, Any]:  # lens meta + clause stats + the subgraph_view projection + view hints
    """APPLY a lens: bind params -> run each selection clause through the real
    read verbs -> UNION the refs -> project via the bulk `subgraph_view`
    (expand.hops/relations ride straight through). Server-side by design
    (invariant 5): the CLI, serve endpoint, and shelf all call THIS, so a lens
    means the same thing to every consumer."""
    rows = {r["slug"]: r for r in await load_lenses(gx)}
    lens = rows.get(slug)
    if lens is None:
        known = ", ".join(sorted(rows)) or "(none on this graph)"
        return {"error": f"no lens {slug!r} — known: {known}", "slug": slug}
    bound, err = bind_params(lens["params"], params)
    if err:
        return {"error": err, "slug": slug,
                "params": lens["params"]}  # declare the shape, so the retry can bind
    clauses: List[Dict[str, Any]] = []
    union: List[str] = []
    seen = set()
    for clause in lens["selection"]:
        args = _substitute(clause.get("args", {}), bound)
        try:
            refs, cerr = await _clause_refs(gx, clause["verb"], args, journal_paths)
        except ValueError as e:
            refs, cerr = [], str(e)
        if cerr:
            return {"error": f"clause {clause['verb']}: {cerr}", "slug": slug}
        clauses.append({"verb": clause["verb"], "selected": len(refs)})
        for r in refs:
            if r not in seen:
                seen.add(r)
                union.append(r)
    expand = lens["expand"]
    sub = await subgraph_view(gx, union, hops=expand.get("hops", 0),
                              relations=expand.get("relations"))
    return {"slug": slug, "title": lens["title"], "description": lens["description"],
            "bound": bound, "clauses": clauses, "view": lens["view"], **sub}
