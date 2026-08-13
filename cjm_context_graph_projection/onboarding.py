"""Project the MEMORY onboarding surface from the graph's ASSERTED lead structure.

Axis F (DEC 2a76a457): the lead stops being WRITTEN and starts being ASSERTED —
the only hand-authored narrative is the LOCK notes (`role=lock`, ABOUT each
anchor); resident pointers are `pin.<role>` assertions FROM the anchors (value =
"<node-id> — <gloss>"); priority/gating judgments are `priority` facts on the
items; registers ride `role` assertions + `<value>-register` hubs; the frontier /
recent-sessions / coverage sections are DERIVED (readiness, Session nodes,
graph_overview) and never authored. The projected surface = the PORTFOLIO lead
(role=portfolio, cross-cutting) + the ACTIVE anchor's lead (config
`active_anchor`; `--anchor` overrides) — ONE STRUCTURE, TWO RENDERS: the
workbench TUI walks the same structure as a navigable tree. The JSON config
carries only data/pointers (active_anchor, how_to_query, mirror_paths) and FAILS
LOUD on missing or retired keys — a stale in-code fallback silently projects an
old surface (the pin-miss doctrine).
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cjm_dev_graph_schema.vocab import DevRelations

from . import factlayer as F
from .authoring import read_node
from .display import annotate_display, node_title
from .projection import graph_overview
from .readiness import readiness

# --- Config contract (axis F: NO in-code seeds) ------------------------------
# The surface DERIVES from the graph; the JSON config carries only data/pointers:
#   active_anchor  REQUIRED  anchor note slug or node id (topic selection
#                            precedes session start; the CLI --anchor overrides)
#   how_to_query   REQUIRED  the substrate query-surface prose (cg-read/cg-write
#                            wrappers + journal guardrails; routes to the
#                            980a4b8e conventions endpoint when that ships)
#   mirror_paths   optional  extra --write targets (e.g. the auto-loaded MEMORY.md)
# Retired keys (push_slugs / landmarks / arc_lead) FAIL LOUD in _load_seeds:
# their content is ASSERTED on-graph now (locks / pins / priority facts).

_HOW_TO_PULL = (
    "## How to pull\n"
    "1. At task start run `relevant \"<task>\"`; `explore` descends any facet IN FULL. "
    "For PLANNING pulls, facet to `kind=Note` / `kind=Decision` — raw rankings are "
    "code-symbol-dominated.\n"
    "2. `show <id>` = structure; `read <id>` = verbatim body — pull BOTH before acting on a node.\n"
    "3. Derived views: `readiness` (frontier) · `register-drift` · `filing` · `contradictions` · "
    "`journal-window --session <key>` (the session lens).\n"
    "4. Treat pulled content as the live source of truth; this surface is only the projected map."
)


def _load_seeds(
    config_path: Optional[str],  # JSON config path (REQUIRED — no in-code fallback)
) -> Tuple[str, str]:  # (active_anchor, how_to_query)
    """Load + validate the onboarding config; FAIL LOUD, never fall back.

    Axis F retired the in-code DEFAULT_* seeds: a stale fallback silently
    projects an old surface, so a missing file/key is an ERROR (the pin-miss
    doctrine) — and a RETIRED key still present is too, because push_slugs/
    landmarks/arc_lead content lives on-graph now (locks / pins / priority
    facts, DEC 2a76a457)."""
    if not config_path or not Path(config_path).exists():
        raise RuntimeError(f"onboarding config missing: {config_path!r} — axis F has no "
                           "in-code fallback; supply JSON with active_anchor + how_to_query")
    cfg = json.loads(Path(config_path).read_text())
    missing = [k for k in ("active_anchor", "how_to_query") if not cfg.get(k)]
    if missing:
        raise RuntimeError(f"onboarding config {config_path}: missing required "
                           f"key(s) {missing} — no in-code fallback (axis F)")
    retired = [k for k in ("push_slugs", "landmarks", "arc_lead") if k in cfg]
    if retired:
        raise RuntimeError(f"onboarding config {config_path}: retired key(s) {retired} — "
                           "the lead is ASSERTED on-graph now (lock notes / pin.<role> / "
                           "priority facts, DEC 2a76a457); delete them")
    return str(cfg["active_anchor"]), str(cfg["how_to_query"])


def _load_mirror_paths(
    config_path: Optional[str],  # JSON config (else no mirrors)
) -> List[str]:
    """Extra paths the surface is ALSO written to on `--write` (config data, not code).

    The M3 cutover: the harness-auto-loaded `MEMORY.md` becomes a GENERATED mirror
    of the projected surface, so one `onboarding --write` keeps both in sync. Key
    `mirror_paths` (list of paths) in the JSON; absent -> no mirrors."""
    if config_path and Path(config_path).exists():
        cfg = json.loads(Path(config_path).read_text())
        return list(cfg.get("mirror_paths", []))
    return []


def _short(text: Any, limit: int = 70) -> str:
    """Cap a hub title to one bounded line."""
    s = " ".join(str(text or "").split())
    return (s[: limit - 1].rstrip() + "…") if len(s) > limit else s


def _render_coverage(overview: Dict[str, Any]) -> str:
    """Render the one-line by-kind coverage roster (auto-derived).

    Kept as a DRIFT CANARY as much as orientation: the counts diff on every
    `onboarding --check`, and a kind silently vanishing is the tell for a
    rebuild-lossy write path (the 2026-08-11 oracle-Procedure catch, b744b28e).
    The hub-anchors block is RETIRED (2026-08-11, user-ratified): hub degree is
    an ingestion artifact and pointed straight at unmetabolized June-era imports
    (c49f8e0b) — the frontier + recent sessions replaced it (DEC 2a76a457)."""
    by_kind = " · ".join(f"{f['kind']}×{f['count']}" for f in overview.get("by_kind", []))
    return "\n".join(["### Graph at a glance (auto-derived)", f"_By kind:_ {by_kind}"])


async def project_onboarding(
    gx: Any,                              # The open graph context (gx.queue / gx.graph_id)
    config_path: Optional[str] = None,    # JSON config (REQUIRED: active_anchor + how_to_query)
    anchor: Optional[str] = None,         # Override the config's active_anchor (slug or id)
) -> Dict[str, Any]:  # {markdown, anchor, missing_refs, mirror_paths}
    """Project the onboarding surface by WALKING the asserted lead structure.

    Renders: intro + how-to-query (config data) -> the PORTFOLIO lead (lock body +
    pins + pinned registers) -> the ACTIVE anchor's lead -> the DERIVED frontier
    (readiness scoped to the anchor, `priority` facts as tags, awaiting-user /
    closable / drift called out) -> recent sessions -> auto-derived coverage.
    Fails LOUD on: missing/retired config keys, no (or many) role=portfolio
    nodes, an unresolvable active_anchor. A pin whose target is gone renders as
    ⚠ MISSING and lands in `missing_refs` — never silently dropped."""
    active_anchor, how_to_query = _load_seeds(config_path)
    if anchor:
        active_anchor = anchor
    structure = await _lead_structure(gx)
    roles = structure["roles"]
    portfolio_ids = sorted(s for s, r in roles.items() if r == "portfolio")
    if len(portfolio_ids) != 1:
        raise RuntimeError(f"expected exactly one role=portfolio anchor, found {len(portfolio_ids)}")
    portfolio_id = portfolio_ids[0]
    anchor_ids = sorted(s for s, r in roles.items() if r in ("portfolio", "program"))
    anchor_nodes = await F.load_nodes(gx, anchor_ids)
    await annotate_display(gx, list(anchor_nodes.values()))
    active_id = None
    for aid in anchor_ids:
        slug = str(F.prop(anchor_nodes[aid], "slug") or "") if aid in anchor_nodes else ""
        if active_anchor in (aid, slug) or (len(active_anchor) >= 6 and aid.startswith(active_anchor)):
            active_id = aid
            break
    if active_id is None:
        known = [str(F.prop(anchor_nodes[a], "slug") or a[:8]) for a in anchor_ids if a in anchor_nodes]
        raise RuntimeError(f"active_anchor {active_anchor!r} matches no role-asserted "
                           f"portfolio/program anchor — known: {known}")
    active_label = node_title(anchor_nodes[active_id]) if active_id in anchor_nodes else active_id[:8]

    intro = (
        "> PROJECTED from the graph's asserted lead structure (locks + pins + priority "
        "facts + registers + readiness) — DEC 2a76a457. This file is GENERATED "
        "(`onboarding --write`): to change it, author ON-GRAPH (edit a lock note, "
        "assert/supersede pins and priorities); never edit this file. It REGENERATES "
        "mid-session — if this copy might predate the last `onboarding --write` "
        "(e.g. a harness snapshot from session start), re-read it from disk."
    )
    port_md, missing = await _render_anchor_lead(gx, portfolio_id, structure,
                                                "## Portfolio (cross-cutting)")
    roster = ["**Anchors** (each carries its own lock + pins; enter one with `--anchor <slug>` "
              "or config `active_anchor` — only the ACTIVE anchor's lead renders below):"]
    for aid in anchor_ids:
        if aid == portfolio_id:
            continue
        slug = str(F.prop(anchor_nodes[aid], "slug") or aid[:8]) if aid in anchor_nodes else aid[:8]
        lock = structure["lock_of"].get(aid)
        lock_part = f"lock `{lock[:8]}`" if lock else "⚠ NO LOCK"
        mark = " ← ACTIVE" if aid == active_id else ""
        roster.append(f"- `{slug}` — {lock_part} · pins {len(structure['pins'].get(aid, []))}{mark}")
    port_md = port_md + "\n\n" + "\n".join(roster)
    parts = [f"# Project Memory — Onboarding Surface\n\n{intro}", how_to_query, port_md]
    if active_id != portfolio_id:
        a_md, a_missing = await _render_anchor_lead(
            gx, active_id, structure, f"## Active anchor — {active_label}")
        parts.append(a_md)
        missing = missing + a_missing
    frontier = await readiness(gx)
    frontier["ready"] = (await readiness(gx, state="ready", limit=500))["ready"]
    parts.append(_render_frontier(frontier,
                                  None if active_id == portfolio_id else active_id,
                                  active_label, structure["priority"]))
    parts.append(await _render_sessions(gx))
    parts.append(_render_coverage(await graph_overview(gx)))
    parts.append(_HOW_TO_PULL)
    return {"markdown": "\n\n".join(p for p in parts if p) + "\n",
            "anchor": active_label, "missing_refs": missing,
            "mirror_paths": _load_mirror_paths(config_path)}


def _strip_frontmatter(text: str) -> str:
    """Drop a leading `---` frontmatter block — a lock note renders body-only."""
    if not text.startswith("---"):
        return text
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "".join(lines[i + 1:])
    return text


def _pin_target(value: str) -> Tuple[str, str]:
    """Split a `pin.<role>` value into (node id, gloss) — the id is the FIRST token."""
    parts = str(value).split(None, 1)
    pid = parts[0] if parts else ""
    gloss = parts[1].lstrip("—- ").strip() if len(parts) > 1 else ""
    return pid, gloss


async def _lead_structure(
    gx: Any,  # The open graph context
    assertions: Optional[List[Any]] = None,  # Preloaded Assertion nodes (one load per VIEW — f4701770)
    supers: Optional[Any] = None,            # Preloaded supersedes, same reason
) -> Dict[str, Any]:  # {roles, pins, priority, statuses, lock_of}
    """Load the ASSERTED lead structure once: roles, pins, priorities, statuses, locks.

    One assertion sweep (active values only): `role` -> subject role; `pin.<role>`
    -> per-anchor (role, value) pin lists; `priority` -> the judgment tags;
    `<value>-status` -> per-register status maps (e.g. model-status). Locks
    resolve via ABOUT edges from `role=lock` notes to their anchors."""
    assertions = assertions if assertions is not None else await F.load_assertions(gx)
    supers = supers if supers is not None else await F.load_supersedes(gx)
    roles: Dict[str, str] = {}
    pins: Dict[str, List[Tuple[str, str]]] = {}
    priority: Dict[str, str] = {}
    statuses: Dict[str, Dict[str, str]] = {}
    for slot in F.group_by_slot(assertions).values():
        pred = str(F.prop(slot[0], "predicate") or "")
        subject = F.prop(slot[0], "subject_id")
        vals = [str(F.prop(a, "value", "")) for a in F.active_assertions(slot, supers)]
        if not vals or not subject:
            continue
        if pred == "role":
            roles[subject] = vals[0]
        elif pred == "priority":
            priority[subject] = vals[0]
        elif pred.startswith("pin.") and len(pred) > len("pin."):
            pins.setdefault(subject, []).extend((pred[len("pin."):], v) for v in vals)
        elif pred.endswith("-status") and len(pred) > len("-status"):
            statuses.setdefault(pred[: -len("-status")], {})[subject] = vals[0]
    lock_of: Dict[str, str] = {}
    for src, tgt in await F.load_edge_pairs(gx, DevRelations.ABOUT):
        if roles.get(src) == "lock" and tgt not in lock_of:
            lock_of[tgt] = src
    return {"roles": roles, "pins": pins, "priority": priority,
            "statuses": statuses, "lock_of": lock_of}


async def _render_register(
    gx: Any,                    # The open graph context
    hub_id: str,                # The pinned `<value>-register` hub note id
    structure: Dict[str, Any],  # The _lead_structure result (roles + statuses)
    missing: List[str],         # Accumulator for unresolvable ids (mutated)
) -> str:
    """Render a pinned register line: members from `role` assertions + statuses.

    Ground truth = the role assertions (the hub is only the `show` handle); a
    member's `<value>-status` renders in parentheses when asserted."""
    nodes = await F.load_nodes(gx, [hub_id])
    if hub_id not in nodes:
        missing.append(hub_id)
        return f"- `[register]` ⚠ MISSING hub `{hub_id}`"
    slug = str(F.prop(nodes[hub_id], "slug") or "")
    value = slug[: -len("-register")] if slug.endswith("-register") else slug
    members = sorted(s for s, r in structure["roles"].items() if r == value)
    stat = structure["statuses"].get(value, {})
    mnodes = await F.load_nodes(gx, members)
    await annotate_display(gx, list(mnodes.values()))

    def _fmt(mid: str) -> str:
        t = _short(node_title(mnodes[mid]), 40) if mid in mnodes else mid[:8]
        s = stat.get(mid)
        return f"{t} ({s})" if s else t

    title = node_title(nodes[hub_id])
    return (f"- `[register]` **{_short(title, 40)}** — "
            + (" · ".join(_fmt(m) for m in members) or "_(no role-asserted members)_")
            + f"  ↳ `show {hub_id[:8]}`")


async def _render_anchor_lead(
    gx: Any,                    # The open graph context
    anchor_id: str,             # The anchor node whose lead to render
    structure: Dict[str, Any],  # The _lead_structure result
    heading: str,               # The section heading (e.g. "## Portfolio (cross-cutting)")
) -> Tuple[str, List[str]]:  # (markdown, missing ref ids)
    """Render one anchor's LEAD: lock body + pins grouped by role + pinned registers.

    A pin whose target no longer resolves renders as ⚠ MISSING and is returned in
    the missing list (the stale-pin signal) — never silently dropped; a lockless
    anchor says so LOUDLY instead of falling back to hand-rolled prose."""
    missing: List[str] = []
    lines: List[str] = [heading]
    lock_id = structure["lock_of"].get(anchor_id)
    if lock_id:
        res = await read_node(gx, lock_id)
        if res.get("error"):
            lines.append(f"_(lock note {lock_id[:8]} unreadable: {res['error']})_")
        else:
            lines.append(_strip_frontmatter(str(res.get("text", ""))).strip())
    else:
        lines.append("_(NO LOCK NOTE asserted for this anchor — author a `role=lock` note ABOUT it)_")
    pin_list = structure["pins"].get(anchor_id, [])
    plain = [(r, v) for r, v in pin_list if r != "register"]
    reg_pins = [(r, v) for r, v in pin_list if r == "register"]
    if plain or reg_pins:
        lines.append("**Pins:**")
    if plain:
        ids = [_pin_target(v)[0] for _, v in plain]
        nodes = await F.load_nodes(gx, ids)
        await annotate_display(gx, list(nodes.values()))
        for role, value in sorted(plain):
            pid, gloss = _pin_target(value)
            if pid not in nodes:
                missing.append(pid)
                lines.append(f"- `[{role}]` ⚠ MISSING pin target `{pid}` — {gloss}")
                continue
            title = node_title(nodes[pid])
            lines.append(f"- `[{role}]` **{_short(title, 60)}** — {gloss}  ↳ `show {pid[:8]}`")
    for _role, value in sorted(reg_pins):
        hub_id, _gloss = _pin_target(value)
        lines.append(await _render_register(gx, hub_id, structure, missing))
    return "\n".join(lines), missing


def _render_frontier(
    frontier: Dict[str, Any],  # readiness() result (ready swapped for the FULL ready list)
    anchor_id: Optional[str],  # Scope anchor id; None = whole-portfolio grouped view
    anchor_label: str,         # Display label for the scope
    priority: Dict[str, str],  # subject id -> active `priority` value (judgment tags)
) -> str:
    """Render the DERIVED frontier: true counts + scoped ready + judgment call-outs.

    `priority` facts render as `[tags]` on items; awaiting-user items get their
    own call-out (they block on the user, not on gates)."""
    c = frontier["counts"]
    unfiled = f" · unfiled {c['unfiled']}" if "unfiled" in c else ""
    lines = ["## Frontier (derived — `readiness` pages the rest)",
             f"_ready {c['ready']} · blocked {c['blocked']} · done {c['done']} · "
             f"closable {c['closable']}{unfiled}_"]

    def _tag(e: Dict[str, Any]) -> str:
        p = priority.get(e["id"])
        return f" `[{p}]`" if p else ""

    def _line(e: Dict[str, Any]) -> str:
        return f"- {_short(e['label'], 90)}{_tag(e)}  `{e['id'][:8]}`"

    ready = frontier["ready"]
    if anchor_id is None:
        by_prog: Dict[str, List[Dict[str, Any]]] = {}
        for e in ready:
            by_prog.setdefault(e.get("program", {}).get("label", "(unfiled)"), []).append(e)
        for prog in sorted(by_prog):
            lines.append(f"**Ready ▸ {_short(prog, 60)}** (top by last touch):")
            lines.extend(_line(e) for e in by_prog[prog][:5])
    else:
        mine = [_line(e) for e in ready if e.get("program", {}).get("id") == anchor_id]
        lines.append(f"**Ready @ {_short(anchor_label, 60)}** (top by last touch):")
        lines.extend(mine[:10] if mine
                     else ["- _(none filed here — `readiness --state ready` pages all)_"])
    awaiting = [e for e in ready + frontier["blocked"]
                if priority.get(e["id"]) == "awaiting-user"]
    if awaiting:
        lines.append("**Awaiting user:** " + " · ".join(
            f"{_short(e['label'], 60)} `{e['id'][:8]}`" for e in awaiting))
    if frontier["closable"]:
        lines.append("**Closable 🏁:** " + " · ".join(
            f"{_short(e['label'], 60)} `{e['id'][:8]}`" for e in frontier["closable"]))
    if frontier["drift"]:
        lines.append("**DoD drift ⚠:** " + " · ".join(
            f"{_short(e['label'], 60)} `{e['id'][:8]}`" for e in frontier["drift"]))
    return "\n".join(lines)


async def _render_sessions(
    gx: Any,         # The open graph context
    limit: int = 3,  # How many recent sessions to surface
) -> str:
    """The most recent registered sessions — the round-DEC lens hooks (derived)."""
    nodes = await F.load_label(gx, "Session")
    rows = sorted((str(F.prop(n, "key") or ""), str(F.prop(n, "title") or ""))
                  for n in nodes)
    # Timestamp-keyed sessions (YYYY-MM-DD_*) are the recency spine; named
    # phase-sessions sort after digits lexicographically and would shadow them.
    stamped = [r for r in rows if r[0][:4].isdigit()]
    rows = stamped or rows
    lines = ["## Recent sessions (derived — `journal-window --session <key>` is the lens)"]
    for key, title in rows[-limit:][::-1]:
        suffix = f" — {title}" if title else ""
        lines.append(f"- `{key}`{suffix}")
    return "\n".join(lines)
