"""The OUTLINE PASS of the notes lane — the first of the standalone lecture resource's passes
(rulings 96be1528 (11) and 776c13d3 (a); work item 81d6e669 (3)): ONE whole-source reader
proposes the page's sections AND their parents over the keyed points, and a mechanical apply
turns the answers into structure — on a PROPOSAL SET before the draft is born (section +
synopsis rows the accept lane lands), or on a STANDING DRAFT as a PLAN of journaled ops
(accept / edit / retract) confirmed against the re-rendered page. Heading depth is never
stored: a section nests by naming its parent section in `parent_key`, and the render derives
the rest (`purenotes.group_points`)."""

import json
import uuid
from typing import Any, Dict, List, Optional, Tuple

from cjm_dev_graph_schema.identity import note_node_id

from .purenotes import (_sort_key, accept_point, edit_point, load_points, observe_segments,
                        points_as_proposals, points_index, proposals_from_point_rows,
                        render_points_index, retract_point, SECTION_KIND, STRUCTURE_KINDS,
                        synopsis_of, validate_point_rows)
from .runtime import DEFAULT_MANIFESTS, GraphHandle, open_graph


def outline_of(
    points: List[Dict[str, Any]],  # load_points output (a standing draft)
) -> List[Dict[str, Any]]:  # [{key, id, section, first, parent, parent_key, depth}] — the standing sections in page order
    """The draft's STANDING outline as rows in the pass's own contract, so a reader can keep,
    retitle, re-parent, add or drop against it: each `section` point with its title, the index
    key of the first top-level point it covers (`first`, by the derived membership — the first
    root at or after its anchor; "" when no point follows), its parent section's TITLE and key,
    and its depth (the length of its parent chain, 776c13d3 (a)), in page order — ancestors
    first on a shared anchor. The index keys are the ones `points_index` gives the same
    substance points, so `first` names exactly what the brief shows."""
    pts = sorted(points, key=_sort_key)
    marks = [p for p in pts if str(p.get("kind")) == SECTION_KIND]
    by_key = {str(m.get("key")): m for m in marks}
    by_pid = {str(p.get("key")): p for p in pts}
    index = points_index(points_as_proposals([p for p in pts if str(p.get("kind")) not in STRUCTURE_KINDS]))
    roots = [e for e in index if not e["depth"]]

    def _chain(m: Dict[str, Any]) -> List[Dict[str, Any]]:  # parent, grandparent, … (a dangling parent ends the chain)
        out, cur, seen = [], m, {str(m.get("key"))}
        while str(cur.get("parent_key") or "") in by_key and str(cur.get("parent_key")) not in seen and len(out) < 8:
            cur = by_key[str(cur["parent_key"])]
            seen.add(str(cur.get("key")))
            out.append(cur)
        return out
    rows: List[Dict[str, Any]] = []
    for m in marks:
        sk = _sort_key(m)
        first = next((e["key"] for e in roots if _sort_key(by_pid[str(e["proposal_id"])]) >= sk), "")
        chain = _chain(m)
        rows.append({"key": str(m.get("key")), "id": str(m.get("id") or ""), "section": str(m.get("text") or ""),
                     "first": first, "parent": (str(chain[0].get("text") or "") if chain else ""),
                     "parent_key": (str(chain[0].get("key")) if chain else ""), "depth": len(chain)})
    rows.sort(key=lambda r: (_sort_key(by_key[r["key"]])[0], _sort_key(by_key[r["key"]])[2], r["depth"], r["key"]))
    return rows


def render_outline_brief(
    proposals: List[Dict[str, Any]],  # One proposal set's rows (normally the merged set), or a standing draft's points as proposals
    pack: Dict[str, Any],             # The whole-unit pack (its source line heads the brief); a draft passes {"source": the unit}
    *,
    set_id: str = "",                 # The set — or the draft's slug — the brief is about (printed only)
    current: Optional[List[Dict[str, Any]]] = None,  # A standing draft's outline (`outline_of`): shown for the reader to keep, retitle, re-parent, add or drop against
    synopsis: str = "",               # The standing synopsis (draft mode): shown, so the reader re-states it only to change it
) -> str:  # The outline pass's brief (markdown)
    """The brief of the whole-source OUTLINE PASS (ruling bc62c727 (A); parents per 776c13d3 (a)):
    after the window merge — or over a standing draft — ONE reader proposes the SECTIONS, each
    a title anchored at the first point it covers and nesting, where the lecture itself frames
    a run of sections as one topic, under a parent section named by its title; and the unit's
    synopsis. It reads the keyed Points, never the spine (design 6752db0a (6)); slide titles are
    not the basis (the ruling), the points' own content is. Nesting is never forced: flat is
    legitimate when the content is flat."""
    src = pack.get("source") or {}
    index = points_index(proposals)
    tops = sum(1 for e in index if not e["depth"])
    lines = [f"# Outline pass — {'draft' if current is not None else 'set'} `{set_id}`", "",
             f"Source: **{src.get('title') or src.get('source_id')}**", "",
             f"Below are the {len(index)} points drafted from this source ({tops} top-level; children indented), in source "
             "order, each with a key, its kind, the time it starts and who says it. They will render as ONE page of "
             "notes. Propose the page's SECTIONS — nested where the source's own structure calls for it — and its SYNOPSIS.", "",
             "## Output contract", "",
             "ONE JSON object per line: the sections in source order, then the synopsis LAST.", "",
             '    {"section": "<title>", "first": "p017"}',
             '    {"section": "<title>", "first": "p017", "parent": "<the title of an earlier section row>"}',
             '    {"synopsis": "<one or two sentences>"}', "",
             "* A section is a stretch of the source a returning reader would JUMP to: one topic, one demonstration, "
             "one question-and-answer block. `first` is the key of the FIRST point it covers — a TOP-LEVEL point (never "
             "an indented child); the section runs until the next section's `first` at ANY level. The first section's "
             "`first` is the first top-level point, so every point falls under a heading.",
             "* NESTING: a section may name a `parent` — the title of an earlier section row — when the source frames a "
             "run of sections as ONE larger topic (an arc of the talk, a demonstration with its parts). A parent's `first` "
             "is at or before its first child's: on the same point when the parent opens with its first child, earlier "
             "when the parent has an introduction of its own. Never force nesting: a flat outline is legitimate when the "
             "content is flat, and one level of parents is the common case. Two sections share a `first` only when one "
             "is the other's parent.",
             "* Cut where the SUBJECT changes, judged by what the points say — never at even intervals, never one "
             "section per speaker turn. A run of questions on one subject is one section; a long topic with a clear "
             "internal turn is two. Sections of very different lengths are fine when the source is like that.",
             "* Title: a short noun phrase in the source's own terms, naming what the section is ABOUT (\"Replacing "
             "raw pointers with mdspan\"), never a generic label (\"Introduction\", \"Part 2\", \"Discussion\"), never a "
             "sentence, no trailing period, no numbering; titles are unique within the outline. A Q&A section's title "
             "names the subject asked about.",
             "* `synopsis`: one or two sentences, under 30 words, on what the source ARGUES or SHOWS — its claim and its "
             "move, in its own terms; never a list of the section titles. It becomes the page's description.",
             "* Rows only — no prose, no code fences.", ""]
    if current is not None:
        # The draft mode (81d6e669 (3)): the standing outline, in the contract's own shape, is the
        # baseline — the reader hands back the WHOLE outline; the plan lands only what differs.
        lines += ["## The standing outline", "",
                  f"The draft already carries {len(current)} section(s), below in the output contract's shape. Hand back "
                  "the WHOLE outline: keep a row as it is (same title, same `first`), retitle it (same `first`, a new "
                  "title), give it a `parent`, add a section, or leave one out to drop it. Only the differences land." +
                  (" Omit the synopsis row to keep the standing one:" if synopsis else ""), ""]
        for r in current:
            row = {"section": r["section"], "first": r["first"], **({"parent": r["parent"]} if r.get("parent") else {})}
            lines.append("    " + json.dumps(row, ensure_ascii=False))
        if synopsis:
            lines.append("    " + json.dumps({"synopsis": synopsis}, ensure_ascii=False))
        lines.append("")
    lines += ["## Points", ""]
    return "\n".join(lines + render_points_index(index)) + "\n"


def _read_outline_rows(
    answers: List[Dict[str, Any]],  # The outline pass's rows: {"section", "first"[, "parent"]}… then {"synopsis"}
    index: List[Dict[str, Any]],    # points_index output — the keys the rows may name
    *,
    need_synopsis: bool = True,     # A set's outline carries the unit's synopsis; a standing draft already has one
) -> Tuple[List[Dict[str, Any]], str]:  # ([{title, first, parent: row number | None}] in row order, synopsis)
    """The pass's answer contract, checked loud on the first bad row (shared by the set and the
    draft modes): a section row carries `section` (the title) and `first` (a TOP-LEVEL point key
    of the index) and may name its `parent` — the TITLE of an earlier section row (776c13d3 (a):
    the parent is a relation, never a level); titles are unique within one outline; sections
    run in source order, and two sections open on the same point only when the earlier one is
    an ANCESTOR of the later (a parent and its first child share an anchor; siblings never do);
    the first section opens on the first top-level point, so no point falls under no heading;
    the synopsis, when present, is ONE row and goes LAST."""
    order = {e["key"]: n for n, e in enumerate(index)}
    entry = {e["key"]: e for e in index}
    tops = [e["key"] for e in index if not e["depth"]]
    secs: List[Dict[str, Any]] = []
    titles: Dict[str, int] = {}
    synopsis = ""
    for n, a in enumerate(answers, start=1):
        if not isinstance(a, dict):
            raise ValueError(f"outline row {n}: not an object")
        if a.get("synopsis"):
            if synopsis:
                raise ValueError(f"outline row {n}: a second synopsis — one per unit")
            synopsis = str(a["synopsis"]).strip()
            continue
        title, first = str(a.get("section") or "").strip().rstrip("."), str(a.get("first") or "").strip()
        parent = str(a.get("parent") or "").strip().rstrip(".")
        if not title or not first:
            raise ValueError(f"outline row {n}: a section row carries `section` (the title) and `first` (a point key)")
        if synopsis:
            raise ValueError(f"outline row {n}: the synopsis goes LAST")
        if first not in order:
            raise ValueError(f"outline row {n}: `first` {first!r} is not a point of this set")
        if entry[first]["depth"]:
            raise ValueError(f"outline row {n}: `first` {first} is a child point — a section opens on a TOP-LEVEL point")
        if title.casefold() in titles:
            raise ValueError(f"outline row {n}: a second section titled {title!r} — titles are unique within one outline")
        pidx: Optional[int] = None
        if parent:
            pidx = titles.get(parent.casefold())
            if pidx is None:
                raise ValueError(f"outline row {n}: `parent` {parent!r} is not the title of an EARLIER section row")
        if secs:
            prev = secs[-1]
            if order[first] < order[prev["first"]]:
                raise ValueError(f"outline row {n}: sections run in source order — {first} does not follow {prev['first']}")
            if order[first] == order[prev["first"]]:
                anc, ok = pidx, False
                while anc is not None:
                    if anc == len(secs) - 1:
                        ok = True
                        break
                    anc = secs[anc]["parent"]
                if not ok:
                    raise ValueError(f"outline row {n}: two sections open on {first} — only a parent and its first child "
                                     f"share an anchor (name {prev['title']!r} as this row's `parent`, or move `first`)")
        titles[title.casefold()] = len(secs)
        secs.append({"title": title, "first": first, "parent": pidx})
    if not secs:
        raise ValueError("the outline proposes no section")
    if tops and secs[0]["first"] != tops[0]:
        raise ValueError(f"the first section opens on {secs[0]['first']}, not the first top-level point {tops[0]} — "
                         f"points would fall under no heading")
    if need_synopsis and not synopsis:
        raise ValueError("the outline carries no synopsis")
    return secs, synopsis


def apply_outline(
    proposals: List[Dict[str, Any]],  # One proposal set's rows (normally the merged set)
    answers: List[Dict[str, Any]],    # The outline pass's rows: {"section", "first"[, "parent"]}… then {"synopsis"}
    pack: Dict[str, Any],             # The whole-unit pack the set's rows are numbered in
) -> Dict[str, Any]:  # {"proposals": rows + the section and synopsis rows in accept order, "stats": {...}}
    """Turn the outline pass's answers into STRUCTURE rows on a proposal SET (ruling bc62c727 (A);
    parents per 776c13d3 (a)) — mechanically checked by `_read_outline_rows`, loud on the first
    bad row. A section becomes a `section` row anchored at the first line of its first point
    (validated by the same contract a drafter's row meets), its `parent` the earlier section row
    it nests under, placed directly before that point — a parent before the child that shares
    its anchor; the synopsis spans the unit and goes last. A set that already carries structure
    rows is refused — the outline is proposed once, over points."""
    if any(p.get("kind") in STRUCTURE_KINDS for p in proposals):
        raise ValueError("the set already carries section / synopsis rows — outline a set of points")
    pending = sum(1 for p in proposals if p.get("extra"))
    if pending:
        raise ValueError(f"the set still carries {pending} unjudged extra(s) — `notes-judge` first, then outline the judged set")
    index = points_index(proposals)
    order = {e["key"]: n for n, e in enumerate(index)}
    entry = {e["key"]: e for e in index}
    by_id = {p["proposal_id"]: p for p in proposals}
    secs, synopsis = _read_outline_rows(answers, index)
    rows: List[Dict[str, Any]] = []
    for s in secs:
        line = int(by_id[entry[s["first"]]["proposal_id"]]["from_i"])
        rows.append({"kind": SECTION_KIND, "from_i": line, "to_i": line, "text": s["title"],
                     **({"parent": s["parent"]} if s["parent"] is not None else {})})
    rows.append({"kind": "synopsis", "from_i": 0, "to_i": len(pack.get("segments") or []) - 1, "text": synopsis})
    made = {(p["kind"], p["from_i"], p["text"]): p for p in proposals_from_point_rows(validate_point_rows(rows, pack), pack)}
    before: Dict[str, List[Dict[str, Any]]] = {}   # a point -> the section rows that open on it, parent first
    for s, r in zip(secs, rows):
        before.setdefault(entry[s["first"]]["proposal_id"], []).append(made[(SECTION_KIND, r["from_i"], r["text"])])
    out: List[Dict[str, Any]] = []
    for p in proposals:
        out += before.get(p["proposal_id"], [])
        out.append(p)
    out.append(made[("synopsis", 0, synopsis)])
    firsts = list(dict.fromkeys(s["first"] for s in secs))   # distinct anchors, in order
    sizes = [(order[firsts[k + 1]] if k + 1 < len(firsts) else len(index)) - order[firsts[k]] for k in range(len(firsts))]

    def _depth(i: int) -> int:
        d, cur = 0, secs[i]["parent"]
        while cur is not None:
            d, cur = d + 1, secs[cur]["parent"]
        return d
    return {"proposals": out, "stats": {"sections": len(secs), "nested": sum(1 for s in secs if s["parent"] is not None),
                                        "depth": max(_depth(i) for i in range(len(secs))), "points": len(index),
                                        "smallest": min(sizes), "largest": max(sizes), "synopsis_words": len(synopsis.split())}}


def plan_outline(
    points: List[Dict[str, Any]],   # load_points output (the standing draft)
    answers: List[Dict[str, Any]],  # The outline pass's rows over the draft's brief (the WHOLE outline; a synopsis row only to change it)
) -> Dict[str, Any]:  # {"sections": the resolved outline, "keep", "retitle", "add", "reparent", "retract", "synopsis", "stats"}
    """The DRAFT mode's plan (81d6e669 (3); rulings 96be1528 (11) — a pass is a proposal set and a
    confirm): the reader's whole outline against the standing one, resolved to the ops that
    make the draft match it, mutating nothing. A proposed row matches a standing section by
    (first point, title) exactly — KEEP — else by its first point alone — RETITLE (an edit of
    the section point, identity kept); an unmatched proposed row is an ADD (a new section point,
    its key minted here so the plan and its apply agree); a standing section no row matches is
    a RETRACT (its points fall into the previous section — bc62c727 (A3)); every kept or
    retitled section whose parent differs from the row's is a RE-PARENT (an edit of `parent_key`,
    one ELABORATES edge — 776c13d3 (a)); a synopsis row that differs edits the synopsis point.
    Parents resolve to standing keys or to the keys minted for the adds. Identity is the
    section point's, so anchors and permalinks survive a retitle or a move."""
    pts = sorted(points, key=_sort_key)
    index = points_index(points_as_proposals([p for p in pts if str(p.get("kind")) not in STRUCTURE_KINDS]))
    entry = {e["key"]: e for e in index}
    secs, synopsis = _read_outline_rows(answers, index, need_synopsis=False)
    standing = outline_of(points)
    used: set = set()
    match: List[Optional[Dict[str, Any]]] = [None] * len(secs)
    for exact in (True, False):
        for i, s in enumerate(secs):
            if match[i] is not None:
                continue
            for r in standing:
                if r["key"] in used or r["first"] != s["first"]:
                    continue
                if exact and r["section"].strip().casefold() != s["title"].casefold():
                    continue
                match[i] = r
                used.add(r["key"])
                break
    keys: List[str] = []
    keep: List[str] = []
    retitle: List[Dict[str, Any]] = []
    add: List[Dict[str, Any]] = []
    for i, s in enumerate(secs):
        r = match[i]
        if r is None:
            k = str(uuid.uuid4())
            keys.append(k)
            add.append({"key": k, "section": s["title"], "first": s["first"], "first_key": str(entry[s["first"]]["proposal_id"]),
                        "parent": (secs[s["parent"]]["title"] if s["parent"] is not None else ""), "parent_key": ""})
        else:
            keys.append(r["key"])
            if r["section"].strip() != s["title"]:
                retitle.append({"key": r["key"], "id": r["id"], "old": r["section"], "new": s["title"]})
            else:
                keep.append(r["key"])
    adds = {a["key"]: a for a in add}
    reparent: List[Dict[str, Any]] = []
    for i, s in enumerate(secs):
        want = keys[s["parent"]] if s["parent"] is not None else ""
        r = match[i]
        if r is None:
            adds[keys[i]]["parent_key"] = want
        elif r["parent_key"] != want:
            reparent.append({"key": r["key"], "id": r["id"], "section": s["title"], "old": r["parent_key"], "new": want,
                             "new_title": (secs[s["parent"]]["title"] if s["parent"] is not None else "")})
    retract = [{"key": r["key"], "id": r["id"], "section": r["section"]} for r in standing if r["key"] not in used]
    syn: Optional[Dict[str, Any]] = None
    if synopsis and synopsis != synopsis_of(points):
        sp = next((p for p in pts if str(p.get("kind")) == "synopsis"), None)
        if sp is None:
            raise ValueError("the draft carries no synopsis point to edit — a synopsis is born with the draft's first set")
        syn = {"key": str(sp.get("key")), "id": str(sp.get("id") or ""), "old": synopsis_of(points), "new": synopsis}

    def _depth(i: int) -> int:
        d, cur = 0, secs[i]["parent"]
        while cur is not None:
            d, cur = d + 1, secs[cur]["parent"]
        return d
    return {"sections": [{"section": s["title"], "first": s["first"], "key": keys[i],
                          "parent": (secs[s["parent"]]["title"] if s["parent"] is not None else ""), "depth": _depth(i)}
                         for i, s in enumerate(secs)],
            "keep": keep, "retitle": retitle, "add": add, "reparent": reparent, "retract": retract, "synopsis": syn,
            "stats": {"sections": len(secs), "nested": sum(1 for s in secs if s["parent"] is not None),
                      "depth": max(_depth(i) for i in range(len(secs))), "points": len(index), "standing": len(standing)}}


async def apply_outline_plan(
    gx: GraphHandle,
    slug: str,                          # The standing draft's slug
    plan: Dict[str, Any],               # plan_outline output
    *,
    siblings: Dict[str, str],           # {graph key: db path} — a new section's anchor segment is observed there
    manifests_dir: str = DEFAULT_MANIFESTS,
    graph_key: str,                     # The sibling the points' segments live in
    actor: str = "user:cli",
) -> Dict[str, Any]:  # {slug, ops: [(verb, args)…] in landing order, retracted, added, retitled, reparented, synopsis, written} | {error, ops}
    """Land a plan as the journaled ops it names, in an order every step of which stands on its
    own: RETRACTS first (a dropped section's points fall into the one before it), then ADDS in
    outline order (a parent row precedes its children, so a new child's parent already stands
    at accept — `accept_point` with a `section` point anchored at its first point's first
    segment, observed in the sibling like any accept, owned by the deliverable), then RETITLES
    and RE-PARENTS (`edit_point`: identity kept, the ELABORATES edge re-derived — a kept child of
    a NEW parent re-parents here, after the add), then the synopsis edit. Every op that landed
    rides `ops` for the CLI to journal, even when a later one fails — the journal and the db
    never diverge. Then `notes-render` re-derives the page."""
    note_id = note_node_id(slug)
    points = await load_points(gx, note_id)
    by_key = {str(p.get("key")): p for p in points}
    ops: List[Tuple[str, Dict[str, Any]]] = []
    out: Dict[str, Any] = {"slug": slug, "ops": ops, "retracted": [], "added": [], "retitled": [], "reparented": [],
                           "synopsis": None, "written": False}

    def _fail(msg: str) -> Dict[str, Any]:
        out.update(error=msg, written=bool(ops))
        return out
    for r in plan.get("retract") or []:
        res = await retract_point(gx, r["id"] or str(by_key.get(r["key"], {}).get("id") or r["key"]), actor=actor)
        if res.get("error"):
            return _fail(res["error"])
        if res.get("deleted"):
            ops.append(("retract-point", res["args"]))
            out["retracted"].append({"key": r["key"], "section": r["section"]})
    if plan.get("add"):
        path = siblings.get(graph_key)
        if not path:
            return _fail(f"no sibling graph `{graph_key}` in this graph's config (`sibling_graphs` keys: {sorted(siblings) or 'none'})")
        try:
            async with open_graph(path, manifests_dir, readonly=True) as sg:
                for a in plan["add"]:
                    first = by_key.get(a["first_key"])
                    if first is None:
                        return _fail(f"section {a['section']!r} opens on point `{a['first_key'][:8]}`, which the draft no longer holds")
                    sid = [str(s) for s in (first.get("segment_ids") or [])][:1]
                    obs = await observe_segments(graph_key, sid, siblings, manifests_dir, handle=sg)
                    if obs.get("error"):
                        return _fail(obs["error"])
                    st = first.get("start_time")
                    point = {"key": a["key"], "kind": SECTION_KIND, "text": a["section"], "lead": "", "attribution": "",
                             "data": {}, "ordinal": int(first.get("ordinal") or 0), "heading": str(first.get("heading") or ""),
                             "heading_index": int(first.get("heading_index") or 0), "segment_ids": sid,
                             "start_time": st, "end_time": st, "unit": dict(first.get("unit") or {}),
                             "parent_key": str(a.get("parent_key") or ""), "speaker": "", "speakers": [], "refers_to": []}
                    res = await accept_point(gx, slug, point, observations=obs["observations"], actor=actor,
                                             proposal_set_id=f"outline-pass:{slug}")
                    if res.get("error"):
                        return _fail(res["error"])
                    if not res.get("existing") or res.get("changed"):
                        ops.append(("accept-point", res["args"]))
                    out["added"].append({"key": a["key"], "section": a["section"], "first": a["first"], "parent": a.get("parent") or ""})
        except RuntimeError as e:
            return _fail(f"sibling graph `{graph_key}` unavailable: {e}")
    for r in plan.get("retitle") or []:
        res = await edit_point(gx, r["id"] or r["key"], text=r["new"], actor=actor)
        if res.get("error"):
            return _fail(res["error"])
        if res.get("written"):
            ops.append(("edit-point", res["args"]))
            out["retitled"].append({"key": r["key"], "old": r["old"], "new": r["new"]})
    for r in plan.get("reparent") or []:
        res = await edit_point(gx, r["id"] or r["key"], parent=str(r["new"] or ""), actor=actor)
        if res.get("error"):
            return _fail(res["error"])
        if res.get("written"):
            ops.append(("edit-point", res["args"]))
            out["reparented"].append({"key": r["key"], "section": r.get("section"), "old": r["old"], "new": r["new"]})
    if plan.get("synopsis"):
        s = plan["synopsis"]
        res = await edit_point(gx, s["id"] or s["key"], text=s["new"], actor=actor)
        if res.get("error"):
            return _fail(res["error"])
        if res.get("written"):
            ops.append(("edit-point", res["args"]))
            out["synopsis"] = {"old": s["old"], "new": s["new"]}
    out["written"] = bool(ops)
    return out
