"""The archive's graph-sourced cutover: the notes source journal and its fold (work item 79703485;
the element source unit 56c9c332, design amendment 56b24fd5).

A kept archive post moves from file-sourced to graph-sourced: the JOURNAL holds its elements'
own state and placement, and the file in the website tree is an emit. The journal sits beside
the private notes writes journal (its path is DATA in the notes config, `source_journal_path`)
and takes the code lane's envelope and append-and-rotate (`source_state._append_record`):
verb, ts, generation, args, op as provenance, session and actor.

RECORDS. `note {note, path, frontmatter, commit?}` -- the Note's own state, `path` relative to
the website clone, `commit` present only when read from git (capture, absorb); `section
{section, title | null, text, block_role}` -- one element's own state, title null for a
preamble, a derived block or a continuation; `place {section, note, parent | null, after |
null}` -- where the element sits, its own fact, NEXT derived from `after`; `retire {section}`;
`cutover {note}`. Content and placement are SEPARATE records (a move journals placement only,
an edit content only). One write's records share a ts and fold as one step.

THE FOLD. One step for live and rebuild: compose each touched note from state + placement
(`compose_note_text`), re-decompose it under the profile (`ArchiveSource.decompose`), so anchors,
levels, order and raw are derived exactly as ingest derives them, and map the elements to their
journaled ids by walk position. A Section keeps its id across a renamed heading -- the record
names the id -- so the derived anchor may differ from the one the id was born at. INVARIANT: a
Section's text never holds a line that opens a Section; a composition that decomposes to another
element sequence is refused by the authoring verbs and reported by the fold.

INGEST ORDER (amendment 56b24fd5 (2)): retired restore -> the git fold with each cut-over path
frozen at its capture commit (the retire mechanism) -> this fold continuing the same paths in the
same ElementFold, each journal step a new version of the path at its ts -> the writes replay.

A RETIRED SECTION STAYS A NODE: marked `retired`, out of the composition and the outline (no
HAS_SECTION / PART_OF / NEXT), its last derived properties kept, edges onto it resolving
(RETIRE IS A FACT, COMPACT IS A MOVE, a7617bd4)."""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from cjm_context_graph_layer.grammar import make_edge, SpineRelations
from cjm_context_graph_layer.ops import graph_task
from cjm_context_graph_primitives.journal import op_now
from cjm_context_graph_primitives.query import EdgeQuery, PropertyPredicate
from cjm_dev_graph_schema.identity import section_node_id
from cjm_dev_graph_schema.nodes import SectionNode
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations
from cjm_markdown_decompose_core.blocks import BLANK_BEFORE_HEADING_PROFILES
from cjm_markdown_decompose_core.ingest import corpus_graph_elements
from cjm_markdown_decompose_core.parse import find_headings
from cjm_markdown_decompose_core.project import compose_note_text, element_state

from . import factlayer as F
from .gitfold import blobs_at, head_commit
from .runtime import GraphHandle
from .source_state import _append_record, read_source_journal

NOTE = "note"          # A Note's own state: {note, path, frontmatter, commit?}
SECTION = "section"    # An element's own state: {section, title | null, text, block_role}
PLACE = "place"        # An element's placement: {section, note, parent | null, after | null}
RETIRE = "retire"      # An element leaves the note: {section}
CUTOVER = "cutover"    # The note's source becomes this journal: {note}
RECORD_VERBS = (NOTE, SECTION, PLACE, RETIRE, CUTOVER)
OUTLINE = (DevRelations.HAS_SECTION, SpineRelations.PART_OF, SpineRelations.NEXT)
_TIMELESS = ("sources", "created_at", "updated_at")   # wire keys that are never content (gitfold's rule)


class NotesFoldError(ValueError):
    """A note whose journaled state does not compose and re-decompose to the same elements."""


@dataclass
class NoteRec:
    """A journaled Note's state."""
    note: str                       # The Note id
    path: str                       # Its file, relative to the website clone
    frontmatter: str                # Its verbatim frontmatter
    commit: Optional[str] = None    # The commit its capture read
    cut_over: bool = False          # The journal is its source


@dataclass
class Element:
    """A journaled element's state and placement."""
    section: str                    # The Section id
    note: Optional[str] = None      # The note it is placed in
    title: Optional[str] = None     # Its heading text (None: unheaded)
    text: str = ""                  # Its own text, the surrounding blank lines trimmed
    block_role: str = ""            # The derived block it is ("" = the note's own content)
    parent: Optional[str] = None    # Its enclosing Section (None: the note's top level)
    after: Optional[str] = None     # The sibling it follows (None: the first)
    retired: bool = False           # Out of the note (a node still)


class NotesFold:
    """The notes source journal folded to state: notes, elements, the addresses ever used."""

    def __init__(self):
        self.notes: Dict[str, NoteRec] = {}
        self.elements: Dict[str, Element] = {}
        self.ever: Set[str] = set()        # every Section id a record has named (the birth rule's addresses)
        self.paths: Dict[str, str] = {}    # path -> the note holding it

    def apply(
        self,
        rec: Dict[str, Any],  # One journal record
    ) -> Set[str]:  # The notes it touches
        """Fold one record into the state."""
        verb, a = rec.get("verb"), rec.get("args") or {}
        if verb == NOTE:
            n = self.notes.get(a["note"])
            if n is None:
                n = self.notes[a["note"]] = NoteRec(a["note"], a["path"], a["frontmatter"])
            else:
                self.paths.pop(n.path, None)
                n.path, n.frontmatter = a["path"], a["frontmatter"]
            if a.get("commit"):
                n.commit = a["commit"]
            self.paths[n.path] = n.note
            return {n.note}
        if verb == CUTOVER:
            self.notes[a["note"]].cut_over = True
            return {a["note"]}
        sid = a.get("section")
        if not sid:
            return set()
        self.ever.add(sid)
        e = self.elements.setdefault(sid, Element(sid))
        if verb == SECTION:
            e.title, e.text, e.block_role, e.retired = a.get("title"), a.get("text") or "", a.get("block_role") or "", False
            return {e.note} if e.note else set()
        if verb == PLACE:
            old = e.note
            e.note, e.parent, e.after, e.retired = a.get("note"), a.get("parent"), a.get("after"), False
            return {n for n in (old, e.note) if n}
        if verb == RETIRE:
            e.retired = True
            return {e.note} if e.note else set()
        return set()

    def members(self, note: str) -> List[Element]:  # The note's live elements (any order)
        return [e for e in self.elements.values() if e.note == note and not e.retired]

    def retired_members(self, note: str) -> List[str]:  # The note's retired elements' ids
        return sorted(e.section for e in self.elements.values() if e.note == note and e.retired)

    def outline(
        self,
        note: str,  # A Note id
    ) -> Tuple[List[str], Dict[str, str], Dict[str, str]]:  # (ids in walk order, parent map, following map)
        """The note's outline: each element after its parent, siblings by the `after` chain --
        the walk `compose_note_text` renders, every element counted (an empty one too)."""
        members = self.members(note)
        ids = {e.section for e in members}
        parent = {e.section: e.parent for e in members if e.parent}
        following: Dict[str, str] = {}
        for e in members:
            if e.after:
                if e.after in following:
                    raise NotesFoldError(f"{note}: two elements follow {e.after}")
                following[e.after] = e.section
        children: Dict[Optional[str], List[str]] = {}
        for e in members:
            if e.parent and e.parent not in ids:
                raise NotesFoldError(f"{note}: {e.section}'s parent {e.parent} is not in the note")
            children.setdefault(e.parent, []).append(e.section)

        def ordered(sibs: List[str]) -> List[str]:
            pool = set(sibs)
            firsts = [i for i in sibs if self.elements[i].after is None]
            if len(firsts) != 1:
                raise NotesFoldError(f"{note}: {len(firsts)} first siblings among {sorted(pool)[:4]}")
            seq = [firsts[0]]
            while following.get(seq[-1]) is not None and len(seq) <= len(pool):
                seq.append(following[seq[-1]])
            if set(seq) != pool or len(seq) != len(pool):
                raise NotesFoldError(f"{note}: the after chain from {firsts[0]} does not cover its siblings once")
            for prev, sid in zip(seq, seq[1:]):   # unheaded text after a heading would read as that Section's
                if self.elements[prev].title is not None and self.elements[sid].title is None:
                    raise NotesFoldError(f"{note}: the unheaded element {sid} would follow the heading "
                                         f"{prev} and read as its text -- a heading goes after it")
            return seq

        walk: List[str] = []

        def visit(sid: str) -> None:
            walk.append(sid)
            for c in ordered(children.get(sid, [])) if children.get(sid) else []:
                visit(c)

        for sid in ordered(children[None]) if children.get(None) else []:
            visit(sid)
        return walk, parent, following

    def compose(self, note: str) -> str:  # The note's text, composed from its state
        """`compose_note_text` over the note's journaled state."""
        n = self.notes[note]
        walk, parent, following = self.outline(note)
        wires = [{"id": sid, "properties": {"level": 0 if self.elements[sid].title is None else 1,
                                            "title": self.elements[sid].title or "",
                                            "text": self.elements[sid].text}} for sid in walk]
        try:
            return compose_note_text(n.frontmatter, wires, parent, following)
        except ValueError as e:
            raise NotesFoldError(f"{n.path}: {e}") from e

    def birth_id(
        self,
        note: str,    # The Note id
        anchor: str,  # The new element's anchor at birth
    ) -> str:  # Its id: the address at birth, a generation when that address was ever used (3a4b031f)
        gen = 0
        while section_node_id(note, anchor, gen) in self.ever:
            gen += 1
        return section_node_id(note, anchor, gen)


def groups(
    records: Iterable[Dict[str, Any]],  # Journal records in append order
) -> Iterable[Tuple[float, List[Dict[str, Any]]]]:  # (ts, the records sharing it), in order
    """One write's records share a ts and fold as one step."""
    cur: List[Dict[str, Any]] = []
    for rec in records:
        if cur and rec.get("ts") != cur[0].get("ts"):
            yield cur[0].get("ts"), cur
            cur = []
        cur.append(rec)
    if cur:
        yield cur[0].get("ts"), cur


def fold_records(
    records: Iterable[Dict[str, Any]],  # Journal records
) -> NotesFold:
    fold = NotesFold()
    for rec in records:
        fold.apply(rec)
    return fold


def append_group(
    path: str,                                # The notes source journal
    records: List[Tuple[str, Dict[str, Any]]],  # (verb, args) in order
    op: Optional[Dict[str, Any]] = None,      # Provenance (never replay input)
) -> List[Dict[str, Any]]:  # The records as appended
    """Append one write's records under ONE ts (the op clock's), through the shared choke point."""
    ts = op_now()
    out = []
    for verb, args in records:
        rec: Dict[str, Any] = {"verb": verb, "ts": ts, "generation": 1, "args": args}
        if op:
            rec["op"] = op
        _append_record(path, rec)
        out.append(rec)
    return out


# --- Deriving a note: compose, re-decompose, key by walk position -------------------------

@dataclass
class KeyedSection(SectionNode):
    """A Section whose id is its journaled id, not the one its derived anchor would mint, with
    its outline relations named by id (a renamed heading keeps its id and its edges)."""
    node_id: str = ""                  # The journaled id
    parent_id: Optional[str] = None    # The enclosing Section's id
    next_id: Optional[str] = None      # The following sibling's id
    retired: bool = False              # Retired: out of the outline, a node still

    @property
    def id(self) -> str:  # The journaled id
        return self.node_id

    def to_graph_node(self) -> Dict[str, Any]:
        w = super().to_graph_node()
        if self.retired:
            w["properties"]["retired"] = True
        return w

    def structural_edges(self) -> List[Dict[str, Any]]:
        if self.retired:
            return []
        edges = [make_edge(self.note_id, self.id, DevRelations.HAS_SECTION)]
        if self.parent_id:
            edges.append(make_edge(self.id, self.parent_id, SpineRelations.PART_OF))
        if self.next_id:
            edges.append(make_edge(self.id, self.next_id, SpineRelations.NEXT))
        return edges


def _keyed(
    s: SectionNode,           # A decomposed Section
    sid: str,                 # Its journaled id
    ids: Dict[str, str],      # anchor -> journaled id, for the note's decomposed Sections
) -> KeyedSection:
    return KeyedSection(note_id=s.note_id, anchor=s.anchor, level=s.level, title=s.title, text=s.text,
                        order=s.order, parent_anchor=s.parent_anchor, next_anchor=s.next_anchor,
                        content_hash=s.content_hash, path=s.path, raw=s.raw, block_role=s.block_role,
                        node_id=sid, parent_id=ids.get(s.parent_anchor) if s.parent_anchor else None,
                        next_id=ids.get(s.next_anchor) if s.next_anchor else None)


def derive_note(
    fold: NotesFold,
    note: str,     # A journaled Note id
    src: Any,      # The ArchiveSource whose decomposition the ingest uses
) -> Tuple[Any, str]:  # (the NoteNode, its Sections keyed to their journaled ids, the composed text)
    """Compose the note from its state, decompose the text as ingest would, and key every
    Section to the element at the same walk position -- refusing a composition that decomposes
    to another element sequence (a text that opens a Section, an empty unheaded element)."""
    n = fold.notes[note]
    text = fold.compose(note)
    walk = fold.outline(note)[0]
    decomposed = src.decompose(n.path, text.encode("utf-8"))[0]
    if decomposed.id != note:
        raise NotesFoldError(f"{n.path}: decomposes as {decomposed.id}, not the journaled {note}")
    secs = decomposed.sections
    if len(secs) != len(walk):
        raise NotesFoldError(f"{n.path}: the composition decomposes to {len(secs)} element(s), the "
                             f"journal holds {len(walk)} -- a Section's text opens a Section (use "
                             "add-section), or an unheaded element is empty")
    for s, sid in zip(secs, walk):
        e = fold.elements[sid]
        if (s.level > 0) != (e.title is not None) or s.block_role != e.block_role:
            raise NotesFoldError(f"{n.path}: element {sid} decomposes as "
                                 f"{'a heading' if s.level else 'unheaded'} ({s.block_role or 'content'}), "
                                 f"journaled {'a heading' if e.title is not None else 'unheaded'} "
                                 f"({e.block_role or 'content'})")
    ids = {s.anchor: sid for s, sid in zip(secs, walk)}
    decomposed.sections = [_keyed(s, sid, ids) for s, sid in zip(secs, walk)]
    return decomposed, text


def retired_section(
    last: Dict[str, Any],  # The element's last derived Section wire (properties carried)
) -> Dict[str, Any]:  # The wire a retired element keeps: its last properties, marked retired
    w = {k: v for k, v in last.items() if k not in ("created_at", "updated_at")}
    w["properties"] = {**(last.get("properties") or {}), "retired": True}
    return w


def note_wires(
    note: Any,             # derive_note's NoteNode
    aliases: Optional[Dict[str, str]] = None,  # Confirmed link aliases
    retired: Iterable[Dict[str, Any]] = (),    # retired_section wires of the note
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:  # (node wires, edge wires) -- profile stamped as ingest stamps it
    nodes, edges = corpus_graph_elements([note], aliases)
    nodes += list(retired)
    return nodes, edges


# --- The fold inside the ingest -----------------------------------------------------------

def cutover_freeze(
    records: Iterable[Dict[str, Any]],  # Notes source journal records
) -> Dict[str, str]:  # path -> the capture commit, for every path a cut-over note has held
    """The git fold's freeze for the cut-over paths: each stops at its note's capture commit."""
    fold = NotesFold()
    held: Dict[str, Set[str]] = {}
    for rec in records:
        fold.apply(rec)
        if rec.get("verb") == NOTE:
            held.setdefault(rec["args"]["note"], set()).add(rec["args"]["path"])
    out: Dict[str, str] = {}
    for nid, n in fold.notes.items():
        if n.cut_over:
            if not n.commit:
                raise NotesFoldError(f"{n.path}: cut over with no capture commit")
            for p in held.get(nid, ()):
                out[p] = n.commit
    return out


def fold_source_records(
    records: List[Dict[str, Any]],  # Notes source journal records
    src: Any,                       # The ingest's ArchiveSource (cut-over paths excluded from its keep)
    hist: Any,                      # The git fold (gitfold.FoldedHistory) the cut-over paths were frozen in
) -> Dict[str, str]:  # note -> the path it holds now, for every cut-over note
    """Continue the cut-over paths in the git fold's ElementFold: each journal step that touches a
    cut-over note is a new version of its path at the step's ts, so the capture changes nothing
    and times continue. The payload of each path becomes the derived NoteNode."""
    fold = NotesFold()
    held: Dict[str, str] = {}
    last: Dict[str, Dict[str, Any]] = {}   # Section id -> its last derived wire (a retired element keeps it)
    for ts, group in groups(records):
        touched: Set[str] = set()
        for rec in group:
            touched |= fold.apply(rec)
        step: Dict[str, Optional[Dict[str, Dict[str, Any]]]] = {}
        for nid in sorted(touched):
            n = fold.notes.get(nid)
            if n is None or not n.cut_over:
                continue
            note, _ = derive_note(fold, nid, src)
            old = held.get(nid)
            if old and old != n.path:
                step[old] = None
                hist.payloads.pop(old, None)
            held[nid] = n.path
            retired = [retired_section(last[s]) for s in fold.retired_members(nid) if s in last]
            nodes, edges = note_wires(note, src.note_aliases, retired)
            for w in nodes:
                if w["label"] == DevNodeKinds.SECTION and not w["properties"].get("retired"):
                    last[w["id"]] = w
            step[n.path] = {w["id"]: w for w in nodes + edges}
            hist.payloads[n.path] = (note, retired)
        if step:
            hist.fold.commit(ts, step)
    return held


# --- Paths and git ------------------------------------------------------------------------

def archive_source(
    config: Dict[str, Any],                          # The notes graph-sibling config
    records: Optional[List[Dict[str, Any]]] = None,  # The notes source journal (None: read it from the config)
) -> Any:  # The ArchiveSource the ingest would build: retired sources restored, cut-over paths frozen
    from .archive import retired_sources
    from .devgraph import ArchiveSource
    if records is None:
        records = read_notes_journal(config)
    return ArchiveSource(config["notes_corpus"], config.get("notes_profile") or "quarto_post",
                         site_root=config.get("website_root"), site_pages=config.get("site_pages"),
                         retired=retired_sources(config.get("journal_path")),
                         cutover=cutover_freeze(records))


def read_notes_journal(
    config: Dict[str, Any],  # The notes graph-sibling config
) -> List[Dict[str, Any]]:  # Its source journal's records ([] when none is configured or written)
    p = config.get("source_journal_path")
    return read_source_journal(p) if p else []


def _rev(top: str, commit: Optional[str]) -> Optional[str]:  # A commit-ish resolved to its sha
    r = subprocess.run(["git", "-C", top, "rev-parse", "--verify", f"{commit or 'HEAD'}^{{commit}}"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def _kept_posts(src: Any, sha: str) -> List[str]:  # The kept posts a commit holds (never retired, never a site page)
    held = subprocess.run(["git", "-C", src.top, "ls-tree", "-r", "--name-only", sha],
                          capture_output=True, text=True).stdout.split("\n")
    return sorted(p for p in held if p and src.is_post(p) and p not in src.by_retired)


def _first_diff(a: str, b: str) -> Dict[str, Any]:  # The first differing line of two texts
    x, y = a.split("\n"), b.split("\n")
    i = next((k for k, (p, q) in enumerate(zip(x, y)) if p != q), min(len(x), len(y)))
    return {"line": i + 1, "a": x[i] if i < len(x) else None, "b": y[i] if i < len(y) else None}


# --- Capture and cutover ------------------------------------------------------------------

def capture_records(
    note: Any,     # The decomposed NoteNode (lossless, under the profile)
    path: str,     # Its path relative to the website clone
    commit: str,   # The commit it was read at
) -> List[Tuple[str, Dict[str, Any]]]:  # The capture group's records: note, then each element's state, then placement
    secs = list(note.sections)
    after = {s.next_anchor: s.anchor for s in secs if s.next_anchor}
    by_anchor = {s.anchor: s.id for s in secs}
    recs: List[Tuple[str, Dict[str, Any]]] = [
        (NOTE, {"note": note.id, "path": path, "frontmatter": note.frontmatter_raw, "commit": commit})]
    for s in secs:
        title, text = element_state(s)
        recs.append((SECTION, {"section": s.id, "title": title, "text": text, "block_role": s.block_role}))
    for s in secs:
        recs.append((PLACE, {"section": s.id, "note": note.id,
                             "parent": by_anchor.get(s.parent_anchor) if s.parent_anchor else None,
                             "after": by_anchor.get(after[s.anchor]) if s.anchor in after else None}))
    return recs


def capture_archive(
    config: Dict[str, Any],        # The notes graph-sibling config (source_journal_path required)
    commit: Optional[str] = None,  # The commit to capture at (default: the clone's HEAD)
    *,
    write: bool = True,            # Append the records (else report what would be captured)
) -> Dict[str, Any]:  # {commit, kept, captured, already, refused, records, appended}
    """Capture every kept post at `commit` into the notes source journal: one group, each post's
    note + element states + placement keyed to that commit. A post whose records do not compose
    to its blob is refused (listed with its first differing line); one already captured is kept."""
    jp = config.get("source_journal_path")
    if not jp:
        return {"error": "the notes config names no source_journal_path"}
    records = read_source_journal(jp)
    src = archive_source(config, records)
    sha = _rev(src.top, commit)
    if sha is None:
        return {"error": f"no commit {commit or 'HEAD'} in {src.top}"}
    fold = fold_records(records)
    kept = _kept_posts(src, sha)
    blobs = blobs_at(src.top, sha, kept)
    out: List[Tuple[str, Dict[str, Any]]] = []
    captured, already, refused = [], [], []
    for rel in kept:
        text = blobs[rel].decode("utf-8")
        note = src.decompose(rel, blobs[rel])[0]
        if note.id in fold.notes:
            already.append(rel)
            continue
        recs = capture_records(note, rel, sha)
        trial = fold_records({"verb": v, "args": a} for v, a in recs)
        try:
            composed = trial.compose(note.id)
        except NotesFoldError as e:
            refused.append({"path": rel, "error": str(e)})
            continue
        if composed != text:
            refused.append({"path": rel, **_first_diff(text, composed)})
            continue
        out.extend(recs)
        captured.append(rel)
    appended = append_group(jp, out, op={"op": "capture-archive", "commit": sha}) if write and out else []
    return {"commit": sha, "kept": len(kept), "captured": captured, "already": already,
            "refused": refused, "records": len(out), "appended": appended, "written": bool(appended)}


def cutover_archive(
    config: Dict[str, Any],             # The notes graph-sibling config
    graph_equal: Set[str],              # Kept paths whose GRAPH composition equals their HEAD blob (the round trip)
    paths: Optional[List[str]] = None,  # The paths to cut over (default: every captured, not yet cut-over note)
    *,
    write: bool = True,
) -> Dict[str, Any]:  # {cut_over, refused, appended}
    """Cut captured notes over: the journal's composition, the graph's (the round trip), HEAD's
    blob and the working tree must all agree, so the cutover changes no byte and no element."""
    jp = config.get("source_journal_path")
    if not jp:
        return {"error": "the notes config names no source_journal_path"}
    records = read_source_journal(jp)
    fold = fold_records(records)
    src = archive_source(config, records)
    head = head_commit(src.top)
    want = [n for n in fold.notes.values() if not n.cut_over and (paths is None or n.path in paths)]
    unknown = sorted(set(paths or []) - {n.path for n in fold.notes.values()})
    blobs = blobs_at(src.top, head, [n.path for n in want]) if head else {}
    out: List[Tuple[str, Dict[str, Any]]] = []
    cut, refused = [], [{"path": p, "error": "not captured"} for p in unknown]
    for n in sorted(want, key=lambda n: n.path):
        try:
            composed = fold.compose(n.note)
        except NotesFoldError as e:
            refused.append({"path": n.path, "error": str(e)})
            continue
        blob = blobs.get(n.path)
        tree = Path(src.top) / n.path
        reasons = []
        if blob is None or blob.decode("utf-8") != composed:
            reasons.append("HEAD differs from the journal")
        if not tree.exists() or tree.read_bytes().decode("utf-8") != composed:
            reasons.append("the working tree differs from the journal")
        if n.path not in graph_equal:
            reasons.append("the graph's composition differs from HEAD (round-trip)")
        if reasons:
            refused.append({"path": n.path, "error": "; ".join(reasons)})
            continue
        out.append((CUTOVER, {"note": n.note}))
        cut.append(n.path)
    appended = append_group(jp, out, op={"op": "cutover-archive", "commit": head}) if write and out else []
    return {"cut_over": cut, "refused": refused, "appended": appended, "written": bool(appended)}


# --- Source check and absorb --------------------------------------------------------------

def notes_source_check(
    config: Dict[str, Any],  # The notes graph-sibling config
) -> Dict[str, Any]:  # {notes: [{note, path, state}], counts, clean}
    """Each journaled note's file against its journal: CLEAN (working tree == HEAD == journal),
    UNCOMMITTED (the working tree is the journal's emit, HEAD not yet -- written, not drift),
    DRIFT (HEAD changed by another: absorb it), WORKTREE (an outside edit not yet committed:
    commit it, then absorb), MISSING; a captured note not yet cut over is SHADOW."""
    records = read_notes_journal(config)
    fold = fold_records(records)
    top = config.get("website_root")
    head = head_commit(top) if top else None
    blobs = blobs_at(top, head, [n.path for n in fold.notes.values()]) if head else {}
    rows = []
    for n in sorted(fold.notes.values(), key=lambda n: n.path):
        if not n.cut_over:
            rows.append({"note": n.note, "path": n.path, "state": "shadow"})
            continue
        try:
            composed = fold.compose(n.note)
        except NotesFoldError as e:
            rows.append({"note": n.note, "path": n.path, "state": "error", "error": str(e)})
            continue
        tree = Path(top) / n.path
        wt = tree.read_bytes().decode("utf-8") if tree.exists() else None
        hb = blobs.get(n.path)
        hb = hb.decode("utf-8") if hb is not None else None
        if wt is None and hb is None:
            state = "missing"
        elif wt == composed:
            state = "clean" if hb == composed else "uncommitted"
        elif hb == composed:
            state = "worktree"
        else:
            state = "drift"
        row = {"note": n.note, "path": n.path, "state": state}
        if state in ("drift", "worktree"):
            row.update(_first_diff(composed, wt if wt is not None else ""))
        rows.append(row)
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    return {"notes": rows, "counts": counts, "head": head,
            "clean": not any(r["state"] in ("drift", "worktree", "missing", "error") for r in rows)}


def absorb_records(
    fold: NotesFold,
    note: str,     # A cut-over Note id
    src: Any,      # The ArchiveSource
    text: str,     # The outside version (HEAD's blob)
    commit: str,   # The commit it was read at
) -> Tuple[List[Tuple[str, Dict[str, Any]]], Dict[str, Any]]:  # (the records, {changed, placed, born, retired, renamed?})
    """Map an outside version's elements to ids by their derived anchors (an unheaded element's
    anchor is positional: `_preamble`, `_<role>-n`, `<region>~n`) -- never a rename guess: a
    heading the edit renamed is a retire plus a birth, listed for review."""
    n = fold.notes[note]
    current, _ = derive_note(fold, note, src)
    by_anchor = {s.anchor: s.id for s in current.sections}
    outside = src.decompose(n.path, text.encode("utf-8"))[0]
    secs = list(outside.sections)
    ids: Dict[str, str] = {}
    born: List[str] = []
    for s in secs:
        sid = by_anchor.get(s.anchor)
        if sid is None or sid in ids.values():
            sid = fold.birth_id(note, s.anchor)
            fold.ever.add(sid)
            born.append(sid)
        ids[s.anchor] = sid
    after = {s.next_anchor: s.anchor for s in secs if s.next_anchor}
    recs: List[Tuple[str, Dict[str, Any]]] = []
    if outside.frontmatter_raw != n.frontmatter:
        recs.append((NOTE, {"note": note, "path": n.path, "frontmatter": outside.frontmatter_raw,
                            "commit": commit}))
    changed, placed = [], []
    for s in secs:
        sid = ids[s.anchor]
        title, body = element_state(s)
        e = fold.elements.get(sid)
        if e is None or e.retired or (e.title, e.text, e.block_role) != (title, body, s.block_role):
            recs.append((SECTION, {"section": sid, "title": title, "text": body, "block_role": s.block_role}))
            if sid not in born:
                changed.append(sid)
    for s in secs:
        sid = ids[s.anchor]
        place = {"section": sid, "note": note,
                 "parent": ids.get(s.parent_anchor) if s.parent_anchor else None,
                 "after": ids.get(after[s.anchor]) if s.anchor in after else None}
        e = fold.elements.get(sid)
        if e is None or e.retired or (e.note, e.parent, e.after) != (note, place["parent"], place["after"]):
            recs.append((PLACE, place))
            if sid not in born:
                placed.append(sid)
    gone = sorted(sid for sid in by_anchor.values() if sid not in ids.values())
    recs += [(RETIRE, {"section": sid}) for sid in gone]
    titled = {sid: fold.elements[sid].title for sid in gone if fold.elements[sid].title is not None}
    return recs, {"changed": changed, "placed": placed, "born": born, "retired": gone,
                  "review": [{"retired": sid, "title": t} for sid, t in titled.items()] if born else []}


# --- The live step ------------------------------------------------------------------------

async def _note_rows(
    gx: GraphHandle,
    note: str,  # A Note id
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:  # (the Note wire, its Section wires by id, its outline edges)
    """The note as the db holds it: its node, every Section homed in it (retired ones too), and
    every outline edge leaving them (HAS_SECTION from the Note)."""
    from .relive import _wire
    got = await F.load_nodes(gx, [note])
    nw = _wire(got[note]) if note in got else None
    secs = {str(F.nid(s)): _wire(s) for s in await F.load_label_where(
        gx, DevNodeKinds.SECTION, [PropertyPredicate("note_id", "eq", note)])}
    ids = sorted(set(secs) | {note})
    edges: List[Dict[str, Any]] = []
    for rel in OUTLINE:
        edges += [_wire(e) for e in await F.load_edges(gx, EdgeQuery(source_ids=ids, relation_type=rel))]
    return nw, secs, edges


def _content(w: Dict[str, Any]) -> Dict[str, Any]:  # What a change of counts as a content change (gitfold's rule)
    return {k: v for k, v in w.items() if k not in _TIMELESS}


async def apply_notes_live(
    gx: GraphHandle,
    config: Dict[str, Any],              # The notes graph-sibling config
    appended: List[Dict[str, Any]],      # The records this invocation appended, in order (the journal's tail)
) -> Dict[str, Any]:  # The receipt: notes, node / edge counts, relation edges
    """THE LIVE STEP: the notes fold's step applied to the db for the records a live verb just
    appended -- group by group, each touched cut-over note derived exactly as the rebuild derives
    it, changed and new nodes written whole (`import_graph` overwrite: properties, sources and
    times), the outline edges diffed whole, the relation edges re-harvested against the prior
    composition. Idempotent: a step whose records the db already carries changes nothing."""
    from .authoring import reharvest_note_relations
    from .devgraph import stamp_note_profile
    receipt: Dict[str, Any] = {"records": len(appended), "notes": [], "nodes_added": 0, "nodes_updated": 0,
                               "edges_added": 0, "edges_removed": 0, "relations": {}}
    if not appended:
        return receipt
    records = read_notes_journal(config)
    k = len(appended)
    if records[-k:] != appended:
        raise RuntimeError("live notes fold: the journal's tail is not this op's records "
                           "(a concurrent writer?) -- the db is left for the next rebuild")
    fold = fold_records(records[:-k])
    src = archive_source(config, records)
    profile = config.get("notes_profile") or "quarto_post"
    for ts, group in groups(appended):
        # The composition each note the group names had BEFORE it (the relation re-harvest's prior)
        prior: Dict[str, Optional[str]] = {}
        for nid in _named_notes(fold, group):
            n = fold.notes.get(nid)
            try:
                prior[nid] = fold.compose(nid) if n and n.cut_over else None
            except NotesFoldError:
                prior[nid] = None
        touched: Set[str] = set()
        for rec in group:
            touched |= fold.apply(rec)
        for nid in sorted(touched):
            n = fold.notes.get(nid)
            if n is None or not n.cut_over:
                continue
            note, text = derive_note(fold, nid, src)
            nw, held, held_edges = await _note_rows(gx, nid)
            retired = [retired_section(held[s]) for s in fold.retired_members(nid) if s in held]
            nodes, edges = note_wires(note, src.note_aliases, retired)
            stamp_note_profile(nodes, profile)
            await _write_delta(gx, nid, nodes, edges, nw, held, held_edges, ts, receipt)
            after_note = next(w for w in nodes if w["id"] == nid)
            if prior.get(nid) is not None and prior[nid] != text:
                receipt["relations"][nid] = await reharvest_note_relations(gx, after_note, prior[nid], text)
            receipt["notes"].append(n.path)
    return receipt


def _named_notes(
    fold: NotesFold,
    group: List[Dict[str, Any]],  # One write's records
) -> Set[str]:  # The notes the records name, as the fold stands before them
    names: Set[str] = set()
    for rec in group:
        a = rec.get("args") or {}
        if rec.get("verb") in (NOTE, CUTOVER) or (rec.get("verb") == PLACE and a.get("note")):
            names.add(a.get("note"))
        e = fold.elements.get(a.get("section") or "")
        if e is not None and e.note:
            names.add(e.note)
    return names


async def _write_delta(
    gx: GraphHandle,
    note: str,
    nodes: List[Dict[str, Any]],        # The derived node wires (Note, Sections, facets)
    edges: List[Dict[str, Any]],        # The derived edge wires
    nw: Optional[Dict[str, Any]],       # The Note as the db holds it
    held: Dict[str, Dict[str, Any]],    # Its Sections as the db holds them
    held_edges: List[Dict[str, Any]],   # Their outline edges as the db holds them
    ts: float,                          # The step's time
    receipt: Dict[str, Any],
) -> None:
    """Write the note's node delta and its outline edge delta. A node keeps every property
    another op set on it; the derived properties, its sources and its times are the fold's."""
    db = dict(held)
    if nw is not None:
        db[note] = nw
    write = []
    for w in nodes:
        if w["label"] not in (DevNodeKinds.NOTE, DevNodeKinds.SECTION):
            continue   # a facet (Topic) rides the relation re-harvest, as the edit verbs land it
        old = db.get(w["id"])
        if old is None:
            write.append({**w, "created_at": ts, "updated_at": ts})
            receipt["nodes_added"] += 1
            continue
        props = {**(old.get("properties") or {}), **w["properties"]}
        if w["label"] == DevNodeKinds.SECTION and not w["properties"].get("retired"):
            props.pop("retired", None)
        new = {"id": w["id"], "label": w["label"], "properties": props, "sources": w.get("sources") or []}
        prev = {"id": old["id"], "label": old.get("label"), "properties": old.get("properties") or {},
                "sources": old.get("sources") or []}
        if new == prev:
            continue
        changed = _content(new) != _content(prev)
        write.append({**new, "created_at": old.get("created_at"),
                      "updated_at": ts if changed else old.get("updated_at")})
        receipt["nodes_updated"] += 1
    own = set(held) | {w["id"] for w in nodes if w["label"] == DevNodeKinds.SECTION}
    want = {e["id"]: e for e in edges if e["relation_type"] in OUTLINE}
    have = {e["id"] for e in held_edges if e["target_id"] in own or e["source_id"] in own}
    gone = sorted(i for i in have if i not in want)
    add = [{**e, "created_at": ts, "updated_at": ts} for i, e in sorted(want.items()) if i not in have]
    if gone:
        await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=gone)
    if write or add:
        await graph_task(gx.queue, gx.graph_id, "import_graph",
                         graph_data={"nodes": write, "edges": add, "metadata": {}},
                         merge_strategy="overwrite")
    receipt["edges_added"] += len(add)
    receipt["edges_removed"] += len(gone)


# --- Journal-first authoring --------------------------------------------------------------

def emit_note(
    config: Dict[str, Any],  # The notes graph-sibling config
    fold: NotesFold,
    note: str,               # A cut-over Note id
) -> str:  # The file written
    """Write the note's composition to its path in the website tree (the emit; the user's commit
    and push of main is the publish gate)."""
    n = fold.notes[note]
    p = Path(config["website_root"]) / n.path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(fold.compose(note).encode("utf-8"))
    return str(p)


def _trial(
    fold: NotesFold,
    records: List[Tuple[str, Dict[str, Any]]],  # The write's records
) -> NotesFold:  # A copy of the fold with the records applied (the state the write would make)
    import copy
    t = copy.deepcopy(fold)
    for v, a in records:
        t.apply({"verb": v, "args": a})
    return t


def journal_write(
    config: Dict[str, Any],                     # The notes graph-sibling config
    note: str,                                  # The note the write touches
    build,                                      # (fold) -> [(verb, args)] | {"error": ...}: the write's records, from the current state
    op: Dict[str, Any],                         # Provenance
    *,
    write: bool = True,
) -> Dict[str, Any]:  # {records, appended, text, path, fold?} | {error}
    """The journal-first write path every authoring verb takes: build the records from the
    current state, prove the state they make composes and re-decomposes to the same elements
    (the invariant -- nothing is appended otherwise), append them as one group. The caller then
    runs the live step and emits the file."""
    jp = config.get("source_journal_path")
    if not jp:
        return {"error": "the notes config names no source_journal_path"}
    records = read_source_journal(jp)
    fold = fold_records(records)
    n = fold.notes.get(note)
    if n is None or not n.cut_over:
        return {"error": f"{note} is not a cut-over archive note (capture-archive + cutover-archive first)"}
    recs = build(fold)
    if isinstance(recs, dict):
        return recs
    if not recs:
        return {"records": [], "appended": [], "unchanged": True, "path": n.path}
    trial = _trial(fold, recs)
    try:
        _, text = derive_note(trial, note, archive_source(config, records))
    except NotesFoldError as e:
        return {"error": str(e)}
    appended = append_group(jp, recs, op=op) if write else []
    return {"records": recs, "appended": appended, "text": text, "path": n.path, "fold": trial}


def section_edit_records(
    fold: NotesFold,
    sid: str,           # The Section id
    raw: str,           # Its new heading-inclusive text (the `raw` slot as authored)
    level: int,         # Its current derived heading level (0: unheaded)
    profile: str = "quarto_post",
) -> Any:  # [(SECTION, args)] | {"error": ...}
    """An edit of one element's text: a headed element keeps its level (level is placement --
    `place` moves it) and opens exactly its own heading; an unheaded one opens none."""
    e = fold.elements.get(sid)
    if e is None or e.retired:
        return {"error": f"{sid} is not a live element of a cut-over note"}
    heads = list(find_headings(raw, profile in BLANK_BEFORE_HEADING_PROFILES))
    if e.title is None:
        if heads:
            return {"error": "the text opens a Section -- an unheaded element holds none (use add-section)"}
        title, body = None, raw
    else:
        if not heads or heads[0].start() != 0:
            return {"error": "a headed element's text starts with its heading line"}
        if len(heads) > 1:
            return {"error": "the text opens another Section -- use add-section for a new element"}
        m = heads[0]
        if len(m.group(1)) != level:
            return {"error": f"the heading level changed ({level} -> {len(m.group(1))}): level is "
                             "placement -- re-parent with `place`"}
        title, body = m.group(2).strip(), raw[m.end():]
    body = re.sub(r"(?:\n[ \t]*)+\Z", "", re.sub(r"\A(?:[ \t]*\n)+", "", body))
    body = body if body.strip() else ""
    if (title, body) == (e.title, e.text):
        return []
    return [(SECTION, {"section": sid, "title": title, "text": body, "block_role": e.block_role})]


def _siblings(fold: NotesFold, note: str, parent: Optional[str]) -> List[str]:  # A parent's children in order
    walk, par, _ = fold.outline(note)
    return [s for s in walk if par.get(s) == parent]


def place_records(
    fold: NotesFold,
    sid: str,                       # The element to move
    parent: Optional[str],          # Its new parent (None: the note's top level)
    after: Optional[str],           # The sibling it follows there (None: first)
) -> Any:  # [(PLACE, args)...] | {"error": ...}
    """A move, re-parent or reorder: the element's own placement, plus the two siblings whose
    `after` the move re-points (its old follower closes the gap, its new follower follows it)."""
    e = fold.elements.get(sid)
    if e is None or e.retired or not e.note:
        return {"error": f"{sid} is not a live element of a cut-over note"}
    note = e.note
    if parent is not None:
        p = fold.elements.get(parent)
        if p is None or p.retired or p.note != note or p.title is None:
            return {"error": f"{parent} is not a heading of the same note"}
        walk, par, _ = fold.outline(note)
        q: Optional[str] = parent
        while q is not None:
            if q == sid:
                return {"error": "an element cannot move inside itself"}
            q = par.get(q)
    if after is not None:
        a = fold.elements.get(after)
        if a is None or a.retired or a.note != note or a.parent != parent or after == sid:
            return {"error": f"{after} is not a sibling under the new parent"}
    if (e.parent, e.after) == (parent, after):
        return []
    old_sibs = _siblings(fold, note, e.parent)
    recs: List[Tuple[str, Dict[str, Any]]] = []
    i = old_sibs.index(sid)
    if i + 1 < len(old_sibs):   # the old follower closes the gap
        f = fold.elements[old_sibs[i + 1]]
        recs.append((PLACE, {"section": f.section, "note": note, "parent": f.parent, "after": e.after}))
    new_sibs = [s for s in _siblings(fold, note, parent) if s != sid]
    j = 0 if after is None else new_sibs.index(after) + 1
    if j < len(new_sibs):       # the new follower follows the moved element
        f = fold.elements[new_sibs[j]]
        recs = [r for r in recs if r[1]["section"] != f.section]
        recs.append((PLACE, {"section": f.section, "note": note, "parent": parent, "after": sid}))
    recs.append((PLACE, {"section": sid, "note": note, "parent": parent, "after": after}))
    return recs


def retire_records(
    fold: NotesFold,
    sid: str,  # The element to retire
) -> Any:  # [(RETIRE, ...), (PLACE, follower)] | {"error": ...}
    """An element leaves the note; its follower closes the gap. One with children refuses (move or
    retire them first: nothing is orphaned by implication)."""
    e = fold.elements.get(sid)
    if e is None or e.retired or not e.note:
        return {"error": f"{sid} is not a live element of a cut-over note"}
    sibs = _siblings(fold, e.note, e.parent)
    if _siblings(fold, e.note, sid):
        return {"error": f"{sid} encloses Sections -- move or retire them first"}
    recs: List[Tuple[str, Dict[str, Any]]] = [(RETIRE, {"section": sid})]
    i = sibs.index(sid)
    if i + 1 < len(sibs):
        f = fold.elements[sibs[i + 1]]
        recs.append((PLACE, {"section": f.section, "note": e.note, "parent": f.parent, "after": e.after}))
    return recs


def birth_records(
    fold: NotesFold,
    note: str,                      # The cut-over Note id
    raw: str,                       # The new element's heading-inclusive text
    parent: Optional[str],          # Its parent (None: the top level)
    after: Optional[str],           # The sibling it follows (None: first)
    profile: str = "quarto_post",
) -> Any:  # [(SECTION, ...), (PLACE, ...)...] | {"error": ...}
    """A new headed element: its address at birth (the anchor its heading slugs to), a generation
    when that address was ever used (3a4b031f); its heading at the level its placement derives
    (level is placement); the sibling that followed `after` now follows it."""
    from cjm_markdown_decompose_core.project import COMPOSED_TOP_LEVEL
    from cjm_markdown_decompose_core.sections import heading_anchor
    if note not in fold.notes or not fold.notes[note].cut_over:
        return {"error": f"{note} is not a cut-over archive note"}
    walk, par, _ = fold.outline(note)
    if parent is not None and (parent not in walk or fold.elements[parent].title is None):
        return {"error": f"{parent} is not a heading of the same note"}
    sibs = _siblings(fold, note, parent)
    if after is not None and after not in sibs:
        return {"error": f"{after} is not a child of the parent named"}
    depth, q = 0, parent
    while q is not None:
        depth, q = depth + 1, par.get(q)
    heads = list(find_headings(raw, profile in BLANK_BEFORE_HEADING_PROFILES))
    if len(heads) != 1 or heads[0].start() != 0:
        return {"error": "a new element's text opens exactly one heading, at its start"}
    m = heads[0]
    if len(m.group(1)) != COMPOSED_TOP_LEVEL + depth:
        return {"error": f"at this placement the heading is level {COMPOSED_TOP_LEVEL + depth} "
                         "(level is placement)"}
    title = m.group(2).strip()
    sid = fold.birth_id(note, heading_anchor(title))
    body = re.sub(r"(?:\n[ \t]*)+\Z", "", re.sub(r"\A(?:[ \t]*\n)+", "", raw[m.end():]))
    recs: List[Tuple[str, Dict[str, Any]]] = [
        (SECTION, {"section": sid, "title": title, "text": body if body.strip() else "", "block_role": ""}),
        (PLACE, {"section": sid, "note": note, "parent": parent, "after": after})]
    j = 0 if after is None else sibs.index(after) + 1
    if j < len(sibs):
        recs.append((PLACE, {"section": sibs[j], "note": note, "parent": parent, "after": sid}))
    return recs


# --- The verbs ----------------------------------------------------------------------------

def _element_ref(
    fold: NotesFold,
    src: Any,
    note: str,                 # The note the element lives in
    ref: Optional[str],        # An element id, a unique id prefix among the note's elements, or a current anchor
) -> Tuple[Optional[str], Optional[str]]:  # (the element id, an error)
    if ref is None:
        return None, None
    members = [e.section for e in fold.members(note)]
    if ref in members:
        return ref, None
    hits = [s for s in members if s.startswith(ref)] if len(ref) >= 6 else []
    if len(hits) == 1:
        return hits[0], None
    if len(hits) > 1:
        return None, f"`{ref}` is ambiguous among the note's elements"
    by_anchor = {s.anchor: s.id for s in derive_note(fold, note, src)[0].sections}
    if ref in by_anchor:
        return by_anchor[ref], None
    return None, f"no element `{ref}` in the note (an id, a unique prefix or a current anchor)"


async def _archive_write(
    gx: GraphHandle,
    config: Dict[str, Any],
    note: str,
    build,                       # (fold) -> records | {"error"}
    op: Dict[str, Any],
    write: bool,
) -> Dict[str, Any]:
    """Journal-first: records (proved to compose), the live step, then the emit."""
    res = journal_write(config, note, build, op, write=write)
    fold = res.pop("fold", None)
    if res.get("error") or not res.get("appended"):
        return res
    res["notes_live"] = await apply_notes_live(gx, config, res["appended"])
    res["file"] = emit_note(config, fold, note)
    return res


async def _cut_over_section(
    gx: GraphHandle,
    config: Dict[str, Any],
    node_ref: str,             # A Section id or unique prefix
) -> Tuple[Optional[Any], Optional[NotesFold], Optional[Dict[str, Any]]]:  # (the Section node, the fold, an error) -- (None, None, None) when not an archive element
    from .projection import resolve_node_ref
    if not (config.get("source_journal_path") and config.get("notes_corpus")):
        return None, None, None
    r = await resolve_node_ref(gx, node_ref)
    node = r.get("node")
    if node is None or F.label(node) != DevNodeKinds.SECTION:
        return None, None, None
    fold = fold_records(read_notes_journal(config))
    n = fold.notes.get(str(F.prop(node, "note_id")))
    if n is None:
        return None, None, None
    if not n.cut_over:
        return node, fold, {"error": f"{n.path} is captured but not cut over -- its file is still its "
                                     "source: cutover-archive it first"}
    return node, fold, None


async def archive_author(
    gx: GraphHandle,
    config: Dict[str, Any],                  # The notes graph-sibling config ({} on the dev lane)
    node_ref: str,                           # The Section to author
    *,
    replace: Optional[str] = None,           # Its whole new heading-inclusive text
    edit: Optional[Tuple[str, str]] = None,  # (old, new) splice on its current text
    actor: str = "agent:session",
    write: bool = True,
) -> Optional[Dict[str, Any]]:  # The result, or None when the node is no element of a journaled archive note
    """`author` on a cut-over archive Section, journal-first (56b24fd5 (4)): title + body; a
    changed heading level is refused (level is placement)."""
    from .authoring import _apply
    node, fold, err = await _cut_over_section(gx, config, node_ref)
    if node is None:
        return None
    if err:
        return err
    sid = str(F.nid(node))
    current = str(F.prop(node, "raw") or "")
    new_raw, aerr = _apply(current, replace, edit)
    if aerr is not None:
        return {"error": aerr, "node_id": sid}
    level = int(F.prop(node, "level") or 0)
    profile = config.get("notes_profile") or "quarto_post"
    res = await _archive_write(gx, config, str(F.prop(node, "note_id")),
                               lambda f: section_edit_records(f, sid, new_raw, level, profile),
                               {"op": "author", "node_id": sid, "actor": actor}, write)
    return {"node_id": sid, **res}


async def archive_add_section(
    gx: GraphHandle,
    config: Dict[str, Any],
    note_ref: str,                 # The Note's slug or id
    raw: str,                      # The new element's heading-inclusive text
    *,
    parent: Optional[str] = None,  # Its parent: an element id / prefix / current anchor (None: the top level)
    after: Optional[str] = None,   # The sibling it follows (None: first)
    actor: str = "agent:session",
    write: bool = True,
) -> Optional[Dict[str, Any]]:  # The result, or None when the note is no journaled archive note
    from cjm_dev_graph_schema.identity import note_node_id
    if not (config.get("source_journal_path") and config.get("notes_corpus")):
        return None
    records = read_notes_journal(config)
    fold = fold_records(records)
    nid = note_ref if note_ref in fold.notes else note_node_id(note_ref)
    n = fold.notes.get(nid)
    if n is None:
        return None
    if not n.cut_over:
        return {"error": f"{n.path} is captured but not cut over: cutover-archive it first"}
    src = archive_source(config, records)
    pid, perr = _element_ref(fold, src, nid, parent)
    aid, aerr = _element_ref(fold, src, nid, after)
    if perr or aerr:
        return {"error": perr or aerr}
    profile = config.get("notes_profile") or "quarto_post"
    return await _archive_write(gx, config, nid, lambda f: birth_records(f, nid, raw, pid, aid, profile),
                                {"op": "add-section", "actor": actor}, write)


async def archive_command(
    gx: GraphHandle,
    args: Any,                 # The parsed CLI args
    config: Dict[str, Any],    # The notes graph-sibling config
) -> Dict[str, Any]:  # The verb's result
    """The archive cutover's verbs (work item 79703485; design amendment 56b24fd5): capture-archive,
    cutover-archive, absorb-archive, place, retire-section, replay-source."""
    cmd = args.command
    write = not getattr(args, "no_write", False)
    if cmd == "replay-source":
        return await replay_source(gx, args, config)
    if not (config.get("source_journal_path") and config.get("notes_corpus")):
        return {"error": f"{cmd} runs on the notes graph: its config names no source_journal_path / notes_corpus"}
    if cmd == "capture-archive":
        res = capture_archive(config, args.commit, write=write)
        if res.get("appended"):
            res["notes_live"] = await apply_notes_live(gx, config, res["appended"])
        return res
    if cmd == "cutover-archive":
        from .archive import archive_round_trip
        rt = await archive_round_trip(gx, config)
        if rt.get("errors") and "commit" not in rt:
            return {"error": "; ".join(rt["errors"])}
        src = archive_source(config)
        bad = ({d["path"] for d in rt["differ"]} | set(rt["missing"])
               | {e.split(":", 1)[0] for e in rt["errors"]})
        graph_equal = {p for p in _kept_posts(src, rt["commit"]) if p not in bad}
        res = cutover_archive(config, graph_equal, list(args.paths) or None, write=write)
        if res.get("appended"):
            res["notes_live"] = await apply_notes_live(gx, config, res["appended"])
        return res
    if cmd == "absorb-archive":
        return await absorb_archive(gx, config, list(args.paths) or None, write=write)
    if cmd in ("place", "retire-section"):
        node, fold, err = await _cut_over_section(gx, config, args.section_id)
        if node is None:
            return {"error": f"`{args.section_id}` is no Section of a journaled archive note"}
        if err:
            return err
        sid, nid = str(F.nid(node)), str(F.prop(node, "note_id"))
        if cmd == "retire-section":
            return {"node_id": sid, **await _archive_write(
                gx, config, nid, lambda f: retire_records(f, sid),
                {"op": "retire-section", "actor": args.actor}, write)}
        src = archive_source(config)
        pid, perr = _element_ref(fold, src, nid, None if args.top else args.parent)
        aid, aerr = _element_ref(fold, src, nid, None if args.first else args.after)
        if perr or aerr:
            return {"error": perr or aerr}
        if not args.top and args.parent is None:
            pid = fold.elements[sid].parent
        return {"node_id": sid, **await _archive_write(
            gx, config, nid, lambda f: place_records(f, sid, pid, aid),
            {"op": "place", "actor": args.actor}, write)}
    return {"error": f"no archive verb {cmd}"}


async def absorb_archive(
    gx: GraphHandle,
    config: Dict[str, Any],
    paths: Optional[List[str]] = None,  # The drifted notes to absorb (default: every one source-check calls DRIFT)
    *,
    write: bool = True,
) -> Dict[str, Any]:  # {commit, notes: [{path, changed, placed, born, retired, review, canonical}], appended, re_emitted}
    """Absorb an outside edit (a typo-fix pull request merged into the website repo): HEAD's blob
    decomposed and mapped to ids by anchor -- a renamed heading is a retire plus a birth, listed
    for review -- one group for every absorbed note; a non-canonical edit is canonicalized and
    re-emitted (commit the re-emitted file)."""
    jp = config["source_journal_path"]
    records = read_source_journal(jp)
    fold = fold_records(records)
    src = archive_source(config, records)
    head = head_commit(src.top)
    chk = notes_source_check(config)
    targets = [r for r in chk["notes"] if r["state"] == "drift" and (paths is None or r["path"] in paths)]
    blobs = blobs_at(src.top, head, [r["path"] for r in targets])
    out: List[Tuple[str, Dict[str, Any]]] = []
    rows = []
    for r in targets:
        text = blobs[r["path"]].decode("utf-8")
        recs, rep = absorb_records(fold, r["note"], src, text, head)
        for v, a in recs:
            fold.apply({"verb": v, "args": a})
        try:
            composed = derive_note(fold, r["note"], src)[1]
        except NotesFoldError as e:
            return {"error": f"{r['path']}: {e}"}
        out += recs
        rows.append({"path": r["path"], "note": r["note"], **rep, "canonical": composed == text})
    skipped = [{"path": r["path"], "state": r["state"]} for r in chk["notes"]
               if r["state"] == "worktree" and (paths is None or r["path"] in paths)]
    res: Dict[str, Any] = {"commit": head, "notes": rows, "skipped": skipped, "records": len(out),
                           "appended": [], "re_emitted": []}
    if write and out:
        res["appended"] = append_group(jp, out, op={"op": "absorb-archive", "commit": head})
        res["notes_live"] = await apply_notes_live(gx, config, res["appended"])
        for row in rows:
            if not row["canonical"]:
                res["re_emitted"].append(emit_note(config, fold, row["note"]))
    return res


async def replay_source(
    gx: GraphHandle,
    args: Any,                 # --offset N: fold the source journal's records after the N-th
    config: Dict[str, Any],    # The graph-sibling config (the notes lane when it names notes_corpus)
) -> Dict[str, Any]:  # The live step's receipt over the tail
    """The rebuild swap's source-journal fold (finding 6c121287): records appended to the SOURCE
    journal family while the offline build ran fold into the new db before the swap (and after
    it, the heal pass), exactly as their live step folded them into the old one. Idempotent."""
    path = config.get("source_journal_path") if config.get("notes_corpus") else args.source_journal_path
    if not path:
        return {"error": "replay-source needs the source journal (--source-journal-path or the config's)"}
    records = read_source_journal(path)
    tail = records[max(0, int(args.offset)):]
    res: Dict[str, Any] = {"offset": int(args.offset), "records": len(tail)}
    if not tail:
        return res
    if config.get("notes_corpus"):
        res["notes_live"] = await apply_notes_live(gx, config, tail)
    else:
        from .relive import apply_live
        res["live"] = await apply_live(gx, path, args.repos_dir, tail)
    return res
