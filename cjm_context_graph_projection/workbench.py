"""Workbench lens layer: the front-door / pin-tree / session-feed derived views.

The workbench TUI's data path (DEC ee9e9be6 shape · 47501c78 presentation ·
dc47dfb5 axis-D parity): ONE lens layer, TWO render profiles — every view here
returns a plain JSON-able dict that the agent CLI (`portfolio` / `lead` /
`feed`) and the human TUI consume identically; render rules live per-consumer,
never here. Derived, never stored (the readiness family): the front door and
pin tree WALK the asserted lead structure (locks + `pin.<role>` + priority
facts + registers — axis F, DEC 2a76a457), and the feed is the journal-window
lens made continuous (an open end IS live mode; poll by re-evaluating from a
`since` cursor). Node detail is NOT here: `show` already carries the full
metadata roster (facts + journal trace) — the workbench detail pane is `show`
with a TUI render profile."""

from typing import Any, Dict, List, Optional

from . import factlayer as F
from .authoring import read_node
from .display import annotate_display, node_title
from .journal import journal_window, journal_window_view, read_journal, touched_node_ids
from .onboarding import _lead_structure, _pin_target, _strip_frontmatter
from .projection import subgraph_view
from .readiness import readiness
from .runtime import GraphHandle


def _cap(text: Any, limit: int = 90) -> str:
    """One bounded, whitespace-flattened line (the ledger/roster line discipline)."""
    s = " ".join(str(text or "").split())
    return (s[: limit - 1].rstrip() + "…") if len(s) > limit else s


def _first_lock_line(text: str, limit: Optional[int] = None) -> str:
    """A lock note's NARRATIVE LEAD: its first non-empty body line.

    UNBOUNDED by default (axis-D doctrine: the view is data, bounding belongs
    to each RENDER profile — a baked-in cap gated the TUI's side-scroll, field
    round 1); pass `limit` only where a consumer wants the data pre-bounded.
    The full lock body stays one hop away in `anchor_lead_view`."""
    for line in _strip_frontmatter(text).splitlines():
        if line.strip():
            return _cap(line, limit) if limit else " ".join(line.split())
    return ""


def _op_summary(args: Dict[str, Any], limit: int = 90) -> str:
    """One bounded gloss for an op's args — the ledger's what-happened column.

    An assert reads as `predicate = value`; otherwise the most distinguishing
    text operand present wins; bodies NEVER ride the ledger (they are the card
    zoom's payload)."""
    pred = args.get("predicate")
    if isinstance(pred, str) and pred:
        val = args.get("value")
        return _cap(f"{pred} = {val}" if val not in (None, "") else pred, limit)
    for key in ("statement", "title", "gloss", "name"):
        v = args.get(key)
        if isinstance(v, str) and v.strip():
            return _cap(v, limit)
    for key in ("slug", "key", "relation", "symbol", "module_path", "path", "text", "body"):
        v = args.get(key)
        if isinstance(v, str) and v.strip():
            return f"{key}={_cap(v, limit)}"
    return ""


def journal_ops(
    paths: List[str],               # Journal files to scan (writes + source, together)
    start: Optional[float] = None,  # EXCLUSIVE cursor (unix ts): only ops strictly after it
    end: Optional[float] = None,    # Window end (unix ts; None = OPEN — live mode)
    session: Optional[str] = None,  # Session key filter (stamped, or historical window)
) -> List[Dict[str, Any]]:  # Ledger rows, ts ASCENDING: {ts, verb, actor, session, refs, summary}
    """The feed's OP-LEDGER zoom (DEC ee9e9be6): one row per journaled op.

    `journal_window` aggregates per NODE (the card zoom); the ledger keeps op
    granularity so the verify-it-landed read stops being raw JSONL. `start` is
    an EXCLUSIVE cursor (poll with the last row's ts and never re-see it).
    Session matching is a carried copy of `journal_window`'s resolution (tag
    first, historical [started_at, next-start) window fallback — DEC 6124d8bf);
    carried per the hub-spine precedent rather than extracted prematurely."""
    win_start: Optional[float] = None
    win_end: Optional[float] = None
    if session is not None:
        starts: Dict[str, float] = {}
        for path in paths:
            if not path:
                continue
            for op in read_journal(path):
                a = op.get("args") or {}
                if op.get("verb") == "session" and a.get("key") and a.get("started_at") is not None:
                    starts[a["key"]] = float(a["started_at"])  # upsert: last op wins
        if session in starts:
            win_start = starts[session]
            later = [t for t in starts.values() if t > win_start]
            win_end = min(later) if later else None  # last session = open (in-progress)
    rows: List[Dict[str, Any]] = []
    for path in paths:
        if not path:
            continue
        for op in read_journal(path):
            ts = float(op.get("ts") or 0.0)
            if start is not None and ts <= start:
                continue
            if end is not None and ts > end:
                continue
            args = op.get("args") or {}
            tag = op.get("session") or args.get("session")
            if session is not None:
                in_window = (win_start is not None and ts >= win_start
                             and (win_end is None or ts < win_end))
                if tag != session and not in_window:
                    continue
            rows.append({"ts": ts, "verb": op.get("verb") or "?",
                         "actor": op.get("actor") or args.get("actor"),
                         "session": tag, "refs": touched_node_ids(op),
                         "summary": _op_summary(args)})
    rows.sort(key=lambda r: r["ts"])
    return rows


async def portfolio_view(
    gx: GraphHandle,
    journal_paths: Optional[List[str]] = None,  # Writes/source journals (the last-touch column)
) -> Dict[str, Any]:  # {counts, anchors: [row], links: [{source, target, relation}]}
    """The workbench FRONT DOOR (DEC ee9e9be6): every role-asserted anchor, one row.

    Row = identity (slug/title/role) + the lock's narrative lead line + derived
    vitals (per-anchor ready/blocked/closable/open-FINDING counts via the
    readiness frontier's PART_OF filing) + pin count + journal last-touch (max
    over the anchor, its lock, its pins, and its filed open items). Anchor-to-
    anchor edges are the cross-project connections. Derived top to bottom —
    nothing here is stored."""
    assertions = await F.load_assertions(gx)
    supers = await F.load_supersedes(gx)
    structure = await _lead_structure(gx, assertions=assertions, supers=supers)
    roles = structure["roles"]
    anchor_ids = sorted(s for s, r in roles.items() if r in ("portfolio", "program"))
    nodes = await F.load_nodes(gx, anchor_ids)
    await annotate_display(gx, list(nodes.values()))
    frontier = await readiness(gx, state="all", assertions=assertions, supers=supers)
    closable_ids = {e["id"] for e in frontier["closable"]}
    vitals: Dict[str, Dict[str, int]] = {
        aid: {"ready": 0, "blocked": 0, "closable": 0, "findings": 0} for aid in anchor_ids}
    for bucket in ("ready", "blocked"):
        for e in frontier[bucket]:
            aid = (e.get("program") or {}).get("id")
            if aid not in vitals:
                continue
            vitals[aid][bucket] += 1
            if e["id"] in closable_ids:
                vitals[aid]["closable"] += 1
            if str(e.get("label", "")).startswith("FINDING"):
                vitals[aid]["findings"] += 1
    touch: Dict[str, float] = {}
    if journal_paths:
        for rec in journal_window(journal_paths)["touched"]:
            touch[rec["ref"]] = rec["last_ts"]
    anchors: List[Dict[str, Any]] = []
    for aid in anchor_ids:
        node = nodes.get(aid)
        lock_id = structure["lock_of"].get(aid)
        lock: Optional[Dict[str, Any]] = None
        if lock_id:
            res = await read_node(gx, lock_id)
            lock = {"id": lock_id}
            if res.get("error"):
                lock["error"] = res["error"]
            else:
                lock["lead"] = _first_lock_line(str(res.get("text", "")))
        pin_ids = [_pin_target(v)[0] for _, v in structure["pins"].get(aid, [])]
        touch_ids = [aid, *([lock_id] if lock_id else []), *pin_ids,
                     *(e["id"] for b in ("ready", "blocked") for e in frontier[b]
                       if (e.get("program") or {}).get("id") == aid)]
        last = max((touch.get(t, 0.0) for t in touch_ids), default=0.0)
        anchors.append({"id": aid, "role": roles[aid],
                        "slug": str(F.prop(node, "slug") or aid[:8]) if node is not None else aid[:8],
                        "title": node_title(node) if node is not None else aid[:8],
                        "lock": lock, "pins": len(pin_ids), "vitals": vitals[aid],
                        "last_touch": last or None})
    sub = await subgraph_view(gx, anchor_ids)
    links = [{"source": e["source_id"], "target": e["target_id"],
              "relation": e["relation_type"]} for e in sub["edges"]]
    return {"counts": frontier["counts"], "anchors": anchors, "links": links}


async def anchor_lead_view(
    gx: GraphHandle,
    anchor: str,  # Anchor slug, full id, or id prefix (>= 6 hex chars)
) -> Dict[str, Any]:  # {anchor, lock, pins: [row], registers: [row]} (or {error})
    """One anchor's LEAD as STRUCTURE (DEC ee9e9be6): the navigable pin tree.

    The structured dual of the onboarding render: lock body verbatim, pins
    grouped by role with resolved targets (id + title + gloss), register hubs
    expanded to their role-asserted members + statuses. A pin whose target is
    gone carries `missing: True` — never silently dropped (the stale-pin
    signal, same doctrine as the rendered surface)."""
    structure = await _lead_structure(gx)
    roles = structure["roles"]
    anchor_ids = sorted(s for s, r in roles.items() if r in ("portfolio", "program"))
    nodes = await F.load_nodes(gx, anchor_ids)
    await annotate_display(gx, list(nodes.values()))
    active_id = None
    for aid in anchor_ids:
        slug = str(F.prop(nodes[aid], "slug") or "") if aid in nodes else ""
        if anchor in (aid, slug) or (len(anchor) >= 6 and aid.startswith(anchor)):
            active_id = aid
            break
    if active_id is None:
        known = [str(F.prop(nodes[a], "slug") or a[:8]) for a in anchor_ids if a in nodes]
        return {"error": f"anchor {anchor!r} matches no role-asserted portfolio/program "
                         f"anchor — known: {known}"}
    node = nodes.get(active_id)
    out: Dict[str, Any] = {"anchor": {
        "id": active_id, "role": roles[active_id],
        "slug": str(F.prop(node, "slug") or active_id[:8]) if node is not None else active_id[:8],
        "title": node_title(node) if node is not None else active_id[:8]}}
    lock_id = structure["lock_of"].get(active_id)
    lock: Optional[Dict[str, Any]] = None
    if lock_id:
        res = await read_node(gx, lock_id)
        lock = {"id": lock_id}
        if res.get("error"):
            lock["error"] = res["error"]
        else:
            lock["body"] = _strip_frontmatter(str(res.get("text", ""))).strip()
    out["lock"] = lock
    pin_list = structure["pins"].get(active_id, [])
    plain = sorted((r, v) for r, v in pin_list if r != "register")
    pnodes = await F.load_nodes(gx, [_pin_target(v)[0] for _, v in plain])
    await annotate_display(gx, list(pnodes.values()))
    pins: List[Dict[str, Any]] = []
    for role, value in plain:
        pid, gloss = _pin_target(value)
        row: Dict[str, Any] = {"role": role, "id": pid, "gloss": gloss}
        if pid in pnodes:
            row["title"] = node_title(pnodes[pid])
        else:
            row["missing"] = True
        pins.append(row)
    out["pins"] = pins
    registers: List[Dict[str, Any]] = []
    for _role, value in sorted((r, v) for r, v in pin_list if r == "register"):
        hub_id, _gloss = _pin_target(value)
        hnodes = await F.load_nodes(gx, [hub_id])
        if hub_id not in hnodes:
            registers.append({"id": hub_id, "missing": True})
            continue
        slug = str(F.prop(hnodes[hub_id], "slug") or "")
        value_name = slug[: -len("-register")] if slug.endswith("-register") else slug
        members = sorted(s for s, r in roles.items() if r == value_name)
        stat = structure["statuses"].get(value_name, {})
        mnodes = await F.load_nodes(gx, members)
        await annotate_display(gx, list(mnodes.values()))
        registers.append({
            "id": hub_id, "slug": slug, "title": node_title(hnodes[hub_id]),
            "members": [{"id": m,
                         "title": node_title(mnodes[m]) if m in mnodes else m[:8],
                         **({"status": stat[m]} if m in stat else {})}
                        for m in members]})
    out["registers"] = registers
    return out


async def session_feed(
    gx: GraphHandle,
    journal_paths: List[str],        # Writes + source journals (the feed's substrate)
    *,
    session: Optional[str] = None,   # Session key (None = whole-journal window)
    since: Optional[float] = None,   # EXCLUSIVE cursor (unix ts): only ops strictly after it
    limit: int = 200,                # Ledger rows returned (newest kept)
) -> Dict[str, Any]:  # {window, ops, cards, missing}
    """The TWO-ZOOM session feed (DEC ee9e9be6): op ledger + touched-node cards.

    Zoom 1 (`ops`) = `journal_ops` with every ref joined to its live node title
    (ONE bulk `subgraph_view`). Zoom 2 (`cards`) = the per-node aggregation
    (`journal_window_view` — the existing session lens). Declarative and
    re-evaluatable (DEC f1b02b95): an open end IS live mode — poll by
    re-evaluating with `since` = the last `cursor`; a ref whose node no longer
    exists stays visible with `missing: True` (read-parity, never silently
    dropped)."""
    ops = journal_ops(journal_paths, start=since, session=session)
    total = len(ops)
    ops = ops[-max(0, limit):]
    refs: List[str] = []
    seen: set = set()
    for op in ops:
        for r in op["refs"]:
            if r not in seen:
                seen.add(r)
                refs.append(r)
    sg = await subgraph_view(gx, refs) if refs else {"resolved": {}, "nodes": []}
    by_id = {n["id"]: n for n in sg["nodes"]}
    for op in ops:
        joined: List[Dict[str, Any]] = []
        for r in op["refs"]:
            node = by_id.get(sg["resolved"].get(r, ""))
            if node is None:  # unresolvable OR ambiguous prefix — both stay visible
                joined.append({"ref": r, "missing": True})
            else:
                joined.append({"ref": r, "id": node["id"], "label": node.get("label"),
                               "title": node.get("title")})
        op["refs"] = joined
    cards = await journal_window_view(gx, journal_paths, start=since, session=session)
    cursor = max((op["ts"] for op in ops), default=since or 0.0) or None
    return {"window": {"session": session, "since": since, "cursor": cursor,
                       "total_ops": total, "shown": len(ops)},
            "ops": ops, "cards": cards["touched"], "missing": cards.get("missing", 0)}
