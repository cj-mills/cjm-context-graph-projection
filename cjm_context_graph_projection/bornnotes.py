"""Notes born on the graph, on either note lane (ruling 48c4c34d over ruling 547f4123 (2); design
c9b29de5 (4)-(6)).

A note lane's notes are born CUT OVER: `new-note` writes one group into the lane's source journal --
the note, its elements' state and placement, the cutover -- and the note's file, if it has one, is an
emit written after the apply. Two lanes take it: the dev graph's memory notes (no file, identity from
the frontmatter name, ruling fed38997) and the notes graph's born posts (identity pinned by the slug,
the file under the emit root, 98293e72 (1)).

THE WRITES JOURNAL'S GENESIS NOTES. A note born before this -- a genesis `new-note` op in the writes
journal and the `section` / `add-section` ops after it -- is captured by `cutover_born` the way the
memory notes were: read from the db as that history built it, canonical (blank lines; a memory note's
in-body H1 becomes its title), proved, and appended with its cutover as ONE group. From then on the
source journal is the note's source; its writes-journal ops keep replaying through their appliers,
and the capture replays as a window at its ts after them (`notesource.replay_spans`)."""

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .journal import _genesis_note_id, _note_slug, read_journal
from .memorysource import is_memory_lane, MemorySource, title_records
from .notesource import (_note_rows, _trial, append_group, apply_notes_live, capture_records,
                         CUTOVER, derive_note, emit_note, fold_records, held_text, note_source,
                         NotesFoldError, RETIRE)
from .runtime import GraphHandle
from .source_state import read_source_journal


def genesis_inventory(
    lane: Dict[str, Any],          # A note lane's config (the memory lane, or the notes graph's)
    journal_path: Optional[str],   # The graph's writes journal
) -> Dict[str, str]:  # Note id -> slug, for every note of the lane a writes-journal genesis op mints
    """The lane's notes the writes journal holds: on the memory lane each `new-note` op without a
    born post's slug (identified by its frontmatter name), on the notes lane each one with it."""
    memory = is_memory_lane(lane)
    out: Dict[str, str] = {}
    for op in read_journal(journal_path) if journal_path else []:
        a = op.get("args") or {}
        if op.get("verb") == "new-note" and bool(a.get("slug")) != memory:
            nid = _genesis_note_id(op)
            if nid:
                out[nid] = a.get("slug") or _note_slug(a.get("path"), a.get("content"))
    return out


def _change(
    composed: str,  # The capture's composition
    text: str,      # The note's text as the db holds it
) -> str:  # none | blank-lines | other
    blank = lambda t: [line for line in t.split("\n") if line.strip()]
    return "none" if composed == text else "blank-lines" if blank(composed) == blank(text) else "other"


def capture_held_records(
    note_id: str,                # The Note's id
    text: str,                   # Its text as the db holds it (frontmatter + its live Sections' raw, in order)
    live_ids: Set[str],          # Its live Section ids in the db
    src: Any = None,             # The lane's source (default: the memory lane's)
    slug: Optional[str] = None,  # A born post's pinned identity (None: a memory note)
) -> Tuple[List[Tuple[str, Dict[str, Any]]], Dict[str, Any]]:  # (the capture + cutover records, {change, title?, retired, composed})
    """One note's capture + cutover records, canonical, proved: the db's text re-decomposes to the
    db's elements, the records compose and re-decompose to the same elements, and the derived id set
    is the live one less the retired. A memory note's in-body H1 becomes its title (c9b29de5 (5)); a
    born post's composition may change blank lines only. Raises NotesFoldError on any refusal."""
    src = src or MemorySource()
    data = text.encode("utf-8")
    note = (src.decompose_born(slug, data) if slug else src.decompose("", data))[0]
    if note.id != note_id:
        raise NotesFoldError(f"{note_id}: its text decomposes as {note.id} (the identity moved)")
    if {s.id for s in note.sections} != live_ids:
        raise NotesFoldError(f"{note.slug}: the db's text re-decomposes to other elements than the db "
                             "holds -- rebuild first")
    recs = capture_records(note, "", None, slug)
    h = None
    if not slug:
        recs, h = title_records(note, recs)
    recs.append((CUTOVER, {"note": note_id}))
    trial = fold_records({"verb": v, "args": a} for v, a in recs)
    derived, composed = derive_note(trial, note_id, src)
    retired = {a["section"] for v, a in recs if v == RETIRE}
    if {s.id for s in derived.sections} != live_ids - retired:
        raise NotesFoldError(f"{note.slug}: the capture derives other ids than the db holds")
    change = "title" if h is not None else _change(composed, text)
    if change == "other":
        raise NotesFoldError(f"{note.slug}: the composition changes more than blank lines")
    row: Dict[str, Any] = {"slug": note.slug, "note": note_id, "change": change, "retired": sorted(retired),
                           "composed": composed}
    if h is not None:
        row["title"] = h.title
    return recs, row


async def cutover_born(
    gx: GraphHandle,
    lane: Dict[str, Any],                # The graph's note lane (memorysource.note_lane)
    journal_path: Optional[str],         # The writes journal (the inventory's genesis ops)
    slugs: Optional[List[str]] = None,   # The notes to cut over (default: every genesis note not yet cut over)
    *,
    write: bool = True,
) -> Dict[str, Any]:  # {notes: [{slug, note, change, title?, retired}], refused, records, appended, notes_live?, files?}
    """Capture + cut over the lane's writes-journal genesis notes as ONE group, each read from the db
    as its writes-journal history built it, canonical; every refusal listed, nothing appended unless
    every target is proved. A born post's file is re-emitted after the apply (its canonical form)."""
    jp = lane.get("source_journal_path")
    if not jp:
        return {"error": "the graph config names no note lane's source journal (memory_journal_path, or "
                         "the notes graph's source_journal_path)"}
    records = read_source_journal(jp)
    fold = fold_records(records)
    inv = genesis_inventory(lane, journal_path)
    unknown = sorted(set(slugs or []) - set(inv.values()))
    targets = sorted((nid for nid, s in inv.items() if nid not in fold.notes and (not slugs or s in slugs)),
                     key=lambda nid: inv[nid])
    memory = is_memory_lane(lane)
    src = note_source(lane, records)
    out: List[Tuple[str, Dict[str, Any]]] = []
    rows: List[Dict[str, Any]] = []
    refused = [{"path": s, "error": "no genesis note of that slug"} for s in unknown]
    for nid in targets:
        nw, held, _ = await _note_rows(gx, nid)
        if nw is None:
            refused.append({"path": inv[nid], "error": "not in the db -- rebuild first"})
            continue
        text, live_ids = held_text(nw, held)
        try:
            recs, row = capture_held_records(nid, text, live_ids, src, None if memory else inv[nid])
        except NotesFoldError as e:
            refused.append({"path": inv[nid], "error": str(e)})
            continue
        row.pop("composed")
        out += recs
        rows.append(row)
    res: Dict[str, Any] = {"notes": rows, "refused": refused, "records": len(out), "appended": [],
                           "already": len([n for n in inv if n in fold.notes])}
    if write and out and not refused:
        res["appended"] = append_group(jp, out, op={"op": "cutover-born"})
        res["notes_live"] = await apply_notes_live(gx, lane, res["appended"])
        done = fold_records(read_source_journal(jp))
        res["files"] = [f for f in (emit_note(lane, done, r["note"]) for r in rows) if f]
    res["written"] = bool(res["appended"])
    return res


async def born_new_note(
    gx: GraphHandle,
    lane: Dict[str, Any],          # The graph's note lane (memorysource.note_lane)
    content: str,                  # The whole note: frontmatter + body
    *,
    slug: Optional[str] = None,    # A born post's permalink (the notes lane); None = a memory note
    actor: str = "agent:session",
    write: bool = True,
) -> Dict[str, Any]:  # {note_id, slug, sections, canonical, records, appended, unchanged?, notes_live?, file?} | {error}
    """A note born cut over (c9b29de5 (6); ruling 48c4c34d (3)): one group -- the note, its elements'
    state and placement, the cutover. A memory note has no file and no path, its title frontmatter (an
    in-body H1 refused); a born post is pinned by its slug, needs a frontmatter `title` (the Quarto
    minimum), and its file is written after the apply. A note already standing cut over with this
    composition is NO CHANGE (a retry resumes the chain after it); any other existing note is refused."""
    from cjm_context_graph_layer.ops import graph_task
    jp = lane.get("source_journal_path")
    if not jp:
        return {"error": "the graph config names no note lane's source journal (memory_journal_path, or "
                         "the notes graph's source_journal_path)"}
    if bool(slug) == is_memory_lane(lane):
        return {"error": ("a memory note has no slug -- its identity is its frontmatter name" if slug else
                          "a born post needs --slug (its permalink)")}
    if not content.endswith("\n"):
        content += "\n"
    records = read_source_journal(jp)
    src = note_source(lane, records)
    data = content.encode("utf-8")
    try:
        note = (src.decompose_born(slug, data) if slug else src.decompose("", data))[0]
    except ValueError as e:
        return {"error": str(e)}
    if not slug and any(s.level == 1 for s in note.sections):
        return {"error": "an in-body H1 is the note's title -- put it in the frontmatter as `title:` "
                         "(design c9b29de5 (5))", "slug": note.slug}
    if slug and not re.search(r"^title:\s*\S", note.frontmatter_raw, re.M):
        return {"error": f"a born post needs a frontmatter `title` (born post `{note.slug}`)", "slug": note.slug}
    recs = capture_records(note, "", None, slug)
    recs.append((CUTOVER, {"note": note.id}))
    fold = fold_records(records)
    try:
        derived, composed = derive_note(_trial(fold_records([]), recs), note.id, src)
    except NotesFoldError as e:
        return {"error": str(e), "slug": note.slug}
    res: Dict[str, Any] = {"note_id": note.id, "slug": note.slug, "sections": len(derived.sections),
                           "canonical": composed == content, "records": recs, "appended": [],
                           "born_post": bool(slug)}
    standing = fold.notes.get(note.id)
    if standing is not None or await graph_task(gx.queue, gx.graph_id, "get_node", node_id=note.id):
        try:
            same = standing is not None and standing.cut_over and fold.compose(note.id) == composed
        except NotesFoldError:
            same = False
        if not same:
            return {"error": f"note `{note.slug}` already exists (author / add-section edit it)", "slug": note.slug}
        res.update(records=[], unchanged=True, written=False)
        f = emit_note(lane, fold, note.id) if write else None   # the file a crashed birth may not have written
        if f:
            res["file"] = f
        return res
    if write:
        res["appended"] = append_group(jp, recs, op={"op": "new-note", "actor": actor})
        res["notes_live"] = await apply_notes_live(gx, lane, res["appended"])
        f = emit_note(lane, _trial(fold, recs), note.id)
        if f:
            res["file"] = f
    res["written"] = bool(res["appended"])
    return res


async def genesis_refusal(
    gx: GraphHandle,
    lane: Dict[str, Any],          # The graph's note lane (memorysource.note_lane)
    journal_path: Optional[str],   # The graph's writes journal
    note_ref: str,                 # A Note / Section id or unique prefix, or a note slug
) -> Optional[str]:  # The refusal when it names a note a writes-journal genesis op born and no source journal holds
    """The writes journal's note regime retired for new writes (ruling 48c4c34d (5)): a note its
    genesis op born is edited only once cut over (`cutover-born`); a file-sourced note (a site page,
    its git history the source) and every other node pass."""
    from cjm_dev_graph_schema.identity import note_node_id
    from . import factlayer as F
    from .projection import resolve_node_ref
    node = (await resolve_node_ref(gx, note_ref)).get("node")
    if node is None:
        nid = note_node_id(note_ref)
    elif F.label(node) == "Note":
        nid = str(F.nid(node))
    elif F.label(node) == "Section":
        nid = str(F.prop(node, "note_id") or "")
    else:
        return None
    if not any(op.get("verb") == "new-note" and _genesis_note_id(op) == nid
               for op in (read_journal(journal_path) if journal_path else [])):
        return None
    jp = lane.get("source_journal_path")
    n = fold_records(read_source_journal(jp)).notes.get(nid) if jp else None
    if n is not None and n.cut_over:
        return None
    return (f"{nid[:8]} was born by a writes-journal genesis op and is not cut over -- its source is the "
            "retired writes-journal note regime (ruling 48c4c34d (5)): cutover-born it first")
