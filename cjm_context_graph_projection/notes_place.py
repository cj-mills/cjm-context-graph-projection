"""The PLACEMENT PASS of the notes lane — the second of the standalone lecture resource's
passes (rulings 96be1528 (1) and (3), folded into ONE pass; work item 81d6e669 (3)): ONE
whole-source reader over the keyed points AS THE PAGE ORDERS THEM proposes ROLES (a point's
`point_role` fact: meta or aside, departures from content only, each with a reason) and
MOVES (a point beside the topic it belongs with: a section and the point it follows — the
deliverable's PLACED overlay, a question moving with its subtree). The plan resolves the
rows against the standing facts and overlay, mutating nothing; the apply lands them as the
journaled `assert` (point_role) and `place-point` ops the render reads live. Roles ride the
SHARED substance point (the classification is the point's); moves ride the deliverable's
own edge (the placement is this page's)."""

import time
from typing import Any, Dict, List, Optional, Tuple

from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import note_node_id

from .notes_outline import outline_of
from .purenotes import (_fmt_ts, _sort_key, effective_roles, GLOSSARY_KIND, group_points,
                        place_point, points_as_proposals, points_index, STRUCTURE_KINDS)
from .runtime import GraphHandle
from .write import assert_value


def _page_index(
    points: List[Dict[str, Any]],  # load_points output
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:  # (points_index over the substance, {point key: entry}, {index key: entry})
    """The pass's keys: `points_index` over the draft's substance points (the same keys the
    outline pass hands a reader), plus lookups by point key and by index key."""
    pts = sorted(points, key=_sort_key)
    index = points_index(points_as_proposals([p for p in pts if str(p.get("kind")) not in STRUCTURE_KINDS]))
    return index, {str(e["proposal_id"]): e for e in index}, {str(e["key"]): e for e in index}


def _brief_lines(
    node: Dict[str, Any],           # a build_point_tree node
    depth: int,                     # the indent depth (0 = a root)
    by_key: Dict[str, Dict[str, Any]],  # {point key: index entry}
    tags: Dict[str, List[str]],     # {point key: the standing role / move tags}
) -> List[str]:  # one brief line per point of the subtree, children indented
    """One point of the brief as the reader sees it — `p017 [kind] 12:03 (who)  **lead** — text
    → p003  [role: meta; moved after p020]` — and its subtree beneath (a top-level helper, so
    the names it uses are the module's own bindings)."""
    p = node["p"]
    e = by_key.get(str(p.get("key")))
    if e is None:
        return []
    lead = f"**{e['lead']}** — " if e.get("lead") else ""
    who = f" ({e['speaker']})" if e.get("speaker") else ""
    refs = [by_key[r]["key"] for r in (p.get("refers_to") or []) if r in by_key]
    pk = str(p.get("key"))
    tail = (f"  → {', '.join(refs)}" if refs else "") + (f"  [{'; '.join(tags[pk])}]" if pk in tags else "")
    out = [f"{'  ' * depth}- `{e['key']}` [{e.get('kind')}] {_fmt_ts(e.get('start_time'))}{who}  {lead}{e.get('text')}{tail}"]
    for c in node["kids"]:
        out += _brief_lines(c, depth + 1, by_key, tags)
    return out


def render_place_brief(
    points: List[Dict[str, Any]],  # load_points output (the standing draft)
    *,
    slug: str = "",                # The draft (printed only)
    roles: Optional[Dict[str, str]] = None,             # load_point_roles output: the standing facts (shown as tags)
    placements: Optional[Dict[str, Dict[str, Any]]] = None,  # load_placements output: the standing moves (shown as tags; they shape the order too)
    role_map: Optional[Dict[str, Any]] = None,          # the type's `point_roles` map (the front section's title, what each role does)
) -> str:  # The placement pass's brief (markdown)
    """The brief of the PLACEMENT PASS (rulings 96be1528 (1)/(3)): the keyed points laid out
    UNDER THE CONFIRMED OUTLINE'S HEADINGS in the order the page renders them (the standing
    moves applied), each with its kind, time, speaker, back-links and any standing role, so
    the reader judges what is ABOUT the lecture rather than of it (meta), what a reader of
    the notes loses nothing by (aside — case by case, 6752db0a (10)), and which points sit
    away from the topic they belong with (a question held for the end, an answer to an
    earlier thread). Rows are DELTAS against what stands; content is the default and is
    never listed except to undo a departure."""
    index, by_key, _by_ikey = _page_index(points)
    unit = next((dict(p.get("unit") or {}) for p in points if p.get("unit")), {})
    rmap = {k: v for k, v in dict(role_map or {}).items() if not str(k).startswith("_")}
    front_title = str(rmap.get("front_section_title") or "About this lecture")
    tags = {k: [f"role: {v}"] for k, v in (roles or {}).items()}   # by POINT key; the index key prints
    for k, pl in (placements or {}).items():
        if pl.get("after") is not None:
            follows = by_key[str(pl["after"])]["key"] if str(pl.get("after") or "") in by_key else "the section end"
            tags.setdefault(k, []).append(f"moved after {follows}")
    pts = [p for p in sorted(points, key=_sort_key) if str(p.get("kind")) not in ("synopsis", GLOSSARY_KIND)]
    groups = group_points(pts, unit, placements)
    out = [f"# Placement pass — draft `{slug}`", "",
           f"Source: **{unit.get('title') or unit.get('source_id')}**", "",
           f"Below are the {len(index)} points of this draft under its confirmed outline, in the order the page renders "
           "them (children indented), each with a key, its kind, the time it starts, who says it, its back-links "
           "(`→ p017` = the point it leans on) and any standing role or move. Propose ROLES and MOVES.", "",
           "## Output contract", "",
           "ONE JSON object per line, each a DELTA against what stands; content is the default and is never listed "
           "except to undo a departure.", "",
           '    {"point": "p017", "role": "meta", "why": "<why it is about the lecture rather than of it>"}',
           '    {"point": "p017", "role": "aside", "why": "<why a reader of the notes loses nothing>"}',
           '    {"point": "p017", "role": "content"}',
           '    {"point": "p017", "section": "<a section title>", "after": "p020"}',
           '    {"point": "p017", "section": "<a section title>", "after": ""}',
           '    {"point": "p017", "section": ""}', "",
           f"* ROLES. `meta` = a point ABOUT the lecture rather than of it — its topic, motivation, goals, who is "
           f"speaking, the logistics of the talk; it renders in ONE front section, \"{front_title}\", in source order. "
           "`aside` = a tangent a reader of the notes loses nothing by; it is omitted from this page (never deleted — "
           "another rendering of the same points may show it). Judge case by case; a role on a question reaches its "
           "answers; a `why` is one short clause. `content` is the default: write it only to undo an earlier departure.",
           "* MOVES. A move puts a TOP-LEVEL point (its subtree with it) beside the topic it belongs with: `section` = "
           "the title of a section of this outline, `after` = the key of a top-level point in that section it renders "
           "after, or \"\" for the section's end. The usual case is a question held for the end whose answer leans on "
           "an earlier point (its `→` back-links name the topic), or an answer to an earlier thread. Source order is "
           "the default and moving breaks the lecture's own flow, so a handful of moves is the expected outcome, "
           "never a reshuffle; `\"section\": \"\"` undoes a standing move.",
           "* Never move a section (a title is not a point) or an indented child (it moves with its parent). Rows "
           "only — no prose, no code fences.", "",
           "## The page as it stands", ""]
    for h, tree, d in groups:
        if h:
            out += [f"{'#' * (2 + d)} {h}", ""]
        for n in tree:
            out += _brief_lines(n, 0, by_key, tags)
        out.append("")
    return "\n".join(out) + "\n"


def _read_place_rows(
    rows: List[Dict[str, Any]],            # The pass's rows: role deltas and move deltas
    index: List[Dict[str, Any]],           # points_index output — the keys the rows may name
    sections: List[Dict[str, Any]],        # outline_of output — the section titles a move may name
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:  # ({index key: {role, why}}, {index key: {section: section key | "", after}})
    """The pass's answer contract, checked loud on the first bad row: a row names a `point` of
    the index and carries EITHER a `role` (content | meta | aside, a `why` beside a departure)
    OR a `section` (the exact title of one section of the outline; "" = undo the standing
    move) with `after` (a TOP-LEVEL key of the index, or "" = the section's end); a move's
    point is top-level; at most one role row and one move row per point."""
    entry = {e["key"]: e for e in index}
    by_title: Dict[str, List[Dict[str, Any]]] = {}
    for s in sections:
        by_title.setdefault(str(s["section"]).strip().casefold(), []).append(s)
    role_rows: Dict[str, Dict[str, Any]] = {}
    move_rows: Dict[str, Dict[str, Any]] = {}
    for n, a in enumerate(rows, start=1):
        if not isinstance(a, dict):
            raise ValueError(f"placement row {n}: not an object")
        key = str(a.get("point") or "").strip()
        if key not in entry:
            raise ValueError(f"placement row {n}: `point` {key!r} is not a point of this draft")
        has_role, has_move = a.get("role") is not None, a.get("section") is not None
        if has_role == has_move:
            raise ValueError(f"placement row {n}: a row carries EITHER `role` OR `section` (+ `after`)")
        if has_role:
            role = str(a.get("role") or "").strip().lower()
            if role not in P.POINT_ROLES:
                raise ValueError(f"placement row {n}: role {role!r} — one of {' | '.join(P.POINT_ROLES)}")
            if key in role_rows:
                raise ValueError(f"placement row {n}: a second role for {key}")
            role_rows[key] = {"role": role, "why": str(a.get("why") or "").strip()}
            continue
        section = str(a.get("section") or "").strip()
        if entry[key]["depth"]:
            raise ValueError(f"placement row {n}: {key} is a child point — it moves with its parent")
        if key in move_rows:
            raise ValueError(f"placement row {n}: a second move for {key}")
        if not section:
            move_rows[key] = {"section": "", "after": None}
            continue
        hits = by_title.get(section.casefold()) or []
        if len(hits) != 1:
            raise ValueError(f"placement row {n}: `section` {section!r} names {len(hits)} section(s) of this outline — the exact title of one")
        after = a.get("after")
        if after is None:
            raise ValueError(f"placement row {n}: a move carries `after` — a top-level point key of that section, or \"\" for its end")
        after = str(after).strip()
        if after:
            if after not in entry:
                raise ValueError(f"placement row {n}: `after` {after!r} is not a point of this draft")
            if entry[after]["depth"]:
                raise ValueError(f"placement row {n}: `after` {after} is a child point — name a top-level point")
            if after == key:
                raise ValueError(f"placement row {n}: {key} cannot follow itself")
        move_rows[key] = {"section": str(hits[0]["key"]), "section_title": str(hits[0]["section"]), "after": after}
    return role_rows, move_rows


def plan_placement(
    points: List[Dict[str, Any]],   # load_points output (the standing draft)
    rows: List[Dict[str, Any]],     # The pass's rows (deltas)
    *,
    roles: Optional[Dict[str, str]] = None,                  # load_point_roles output: the standing facts
    placements: Optional[Dict[str, Dict[str, Any]]] = None,  # load_placements output: the standing overlay
) -> Dict[str, Any]:  # {"roles": [...], "moves": [...], "unmoves": [...], "stats"}
    """The pass's plan, mutating nothing: each row resolved to a point key and compared with what
    stands — a role equal to the standing FACT (not the inherited role) is a no-op, as is a
    move to the standing section after the standing key; `content` on a point with no fact is
    a no-op too (content is the default). Reasons ride the plan for the journal's evidence."""
    index, _by_pkey, entry = _page_index(points)
    sections = outline_of(points)
    role_rows, move_rows = _read_place_rows(rows, index, sections)
    have_roles = dict(roles or {})
    have_moves = dict(placements or {})
    by_pid = {str(p.get("key")): p for p in points}
    role_plan: List[Dict[str, Any]] = []
    for k, r in role_rows.items():
        pk = str(entry[k]["proposal_id"])
        old = have_roles.get(pk)
        if r["role"] == (old or P.POINT_ROLE_CONTENT) and (old is not None or r["role"] == P.POINT_ROLE_CONTENT):
            continue
        role_plan.append({"point": k, "key": pk, "id": str(by_pid[pk].get("id") or ""), "old": old or "", "new": r["role"],
                          "why": r["why"], "text": str(by_pid[pk].get("text") or "")[:80]})
    moves: List[Dict[str, Any]] = []
    unmoves: List[Dict[str, Any]] = []
    for k, m in move_rows.items():
        pk = str(entry[k]["proposal_id"])
        cur = have_moves.get(pk) or {}
        if not m["section"]:
            if cur.get("after") is not None:
                unmoves.append({"point": k, "key": pk, "old_section": cur.get("section"), "keep_refs": cur.get("refs_shown")})
            continue
        after_key = str(entry[m["after"]]["proposal_id"]) if m["after"] else ""
        if cur.get("section") == m["section"] and cur.get("after") == after_key:
            continue
        moves.append({"point": k, "key": pk, "section": m["section"], "section_title": m["section_title"],
                      "after": m["after"], "after_key": after_key, "old_section": cur.get("section") or "",
                      "text": str(by_pid[pk].get("text") or "")[:80]})
    eff = effective_roles(points, {**have_roles, **{r["key"]: r["new"] for r in role_plan}})
    return {"roles": role_plan, "moves": moves, "unmoves": unmoves,
            "stats": {"rows": len(rows), "roles": len(role_plan), "meta": sum(1 for r in role_plan if r["new"] == P.POINT_ROLE_META),
                      "aside": sum(1 for r in role_plan if r["new"] == P.POINT_ROLE_ASIDE),
                      "moves": len(moves), "unmoves": len(unmoves), "points": len(index),
                      "front_after": sum(1 for k, v in eff.items() if v == P.POINT_ROLE_META),
                      "omitted_after": sum(1 for k, v in eff.items() if v == P.POINT_ROLE_ASIDE)}}


async def apply_placement_plan(
    gx: GraphHandle,
    slug: str,                  # The standing draft's slug
    plan: Dict[str, Any],       # plan_placement output
    *,
    actor: str = "user:cli",
) -> Dict[str, Any]:  # {slug, ops: [(verb, args)…] in landing order, roles, moves, unmoves, written} | {error, ops}
    """Land a plan as the journaled ops it names: each role as ONE `assert` of `point_role` on
    the shared point (a standing fact superseded explicitly — the predicate CHANGES, a flip is
    a supersession, 14cafef6), each move as ONE `place-point` (the PLACED edge with `after`,
    the standing `refs_shown` kept), each undo as a `place-point` with no section. Every op
    that landed rides `ops` for the CLI to journal, a later failure notwithstanding."""
    if await gx_note_missing(gx, slug):
        return {"error": f"no note `{slug}`", "ops": [], "written": False}
    ops: List[Tuple[str, Dict[str, Any]]] = []
    out: Dict[str, Any] = {"slug": slug, "ops": ops, "roles": [], "moves": [], "unmoves": [], "written": False}

    def _fail(msg: str) -> Dict[str, Any]:
        out.update(error=msg, written=bool(ops))
        return out
    for r in plan.get("roles") or []:
        st = await assert_value(gx, r["id"] or r["key"], P.POINT_ROLE, r["new"], actor=actor,
                                supersede=([r["old"]] if r.get("old") else None))
        if st.get("error"):
            return _fail(st["error"])
        # `at` keeps a flip back (meta -> content -> meta -> content) a NEW record: the journal dedups an op
        # identical in verb + args over its whole history (replay stamps asserted_at from the envelope)
        ops.append(("assert", {"subject": r["id"] or r["key"], "predicate": P.POINT_ROLE, "value": r["new"], "actor": actor,
                               "evidence": None, "supersede": ([r["old"]] if r.get("old") else None),
                               "subject_content_hash": st.get("subject_content_hash"), "at": round(time.time(), 3)}))
        out["roles"].append({"point": r["point"], "key": r["key"], "old": r["old"], "new": r["new"], "why": r.get("why", "")})
    for m in plan.get("moves") or []:
        res = await place_point(gx, slug, m["key"], m["section"], after=m["after_key"], actor=actor)
        if res.get("error"):
            return _fail(res["error"])
        if res.get("written"):
            ops.append(("place-point", res["args"]))
        out["moves"].append({"point": m["point"], "key": m["key"], "section": m["section_title"], "after": m["after"]})
    for u in plan.get("unmoves") or []:
        res = await place_point(gx, slug, u["key"], "", actor=actor)
        if res.get("error"):
            return _fail(res["error"])
        if res.get("written"):
            ops.append(("place-point", res["args"]))
        out["unmoves"].append({"point": u["point"], "key": u["key"]})
    out["written"] = bool(ops)
    return out


async def gx_note_missing(
    gx: GraphHandle,
    slug: str,  # The deliverable Note's slug
) -> bool:  # True when no Note stands under the slug
    """Whether the draft exists — the one graph read the apply makes before its first op."""
    from cjm_context_graph_layer.ops import graph_task
    return await graph_task(gx.queue, gx.graph_id, "get_node", node_id=note_node_id(slug)) is None
