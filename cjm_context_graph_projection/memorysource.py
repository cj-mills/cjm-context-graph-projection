"""The memory notes on the element source unit (design c9b29de5, work item 40a35288).

The dev graph's memory notes take the notes lane's grammar (`notesource`: note / section / place /
retire / cutover records, the fold, derive_note, the live step) in a journal of their own, the
MEMORY JOURNAL beside the writes and source journals (`memory_journal_path` in the graph-sibling
config, DATA). A lane is config plus a source object: this module supplies the memory lane's.

NO FILE (ruling fed38997): a cut-over memory note is emitted nowhere and carries no path; the
graph is how it is read. CAPTURE AND CUTOVER ARE ONE GROUP, READ FROM THE GRAPH: there is no
outside file to shadow, so the cutover (`bornnotes.cutover_born`) read each note as the db held it (its writes-journal
history: the genesis new-note op and its section / add-section ops) and appends its element
records with the cutover record in one group. From then on the memory journal is the note's
source and its writes-journal history stops growing; a new memory note is born cut over
(`bornnotes.born_new_note`).

THE CANONICALIZATION AT CAPTURE (c9b29de5 (5)): the blank lines between elements take the
composed form, and an in-body H1 is the note's TITLE -- its text moves into the frontmatter as
`title`, the H1 element keeps its id and becomes the note's opening unheaded element, a preamble
before it merges in and retires, and its children step up to the top level."""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cjm_markdown_decompose_core.extract import note_from_text
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from .journal import read_journal
from .notesource import NOTE, NotesFoldError, PLACE, RETIRE, SECTION

MEMORY_LANE = "memory"       # The lane key a note-lane config carries (`lane`)
MEMORY_PROFILE = "memory"    # The memory notes' harvest profile, fixed by the lane, never detected


class MemorySource:
    """The memory lane's decomposition: the memory profile, identity from the frontmatter name,
    no file location (fed38997: the path is empty, as an unplaced born post's is)."""

    profile = MEMORY_PROFILE
    note_aliases = None   # as the writes-journal genesis built the notes (reconstruct_note harvests none)

    def decompose(
        self,
        path: str,    # Unused: a memory note has no file
        data: bytes,  # The note's text
    ) -> Tuple[Any, List[Dict[str, Any]]]:  # (the Note, its element wires)
        note = note_from_text("", data.decode("utf-8"), corpus_root="", profile=MEMORY_PROFILE,
                              lossless=True)
        n, e = corpus_graph_elements([note], self.note_aliases)
        return note, n + e


def memory_lane(
    config: Dict[str, Any],  # The dev graph's graph-sibling config
) -> Dict[str, Any]:  # The memory lane's note-lane config ({} when the config names no memory journal)
    """The note-lane config the notes lane's machinery reads: the memory journal as its source
    journal, the memory profile, the lane key."""
    p = config.get("memory_journal_path")
    if not p:
        return {}
    return {"lane": MEMORY_LANE, "source_journal_path": p, "notes_profile": MEMORY_PROFILE,
            "memory_dir": config.get("memory_dir")}


def note_lane(
    config: Dict[str, Any],  # A graph-sibling config (the dev graph's or the notes graph's)
) -> Dict[str, Any]:  # The note lane its journaled notes take: the archive's (the config itself) or the memory lane
    """One graph, one note lane: the notes graph's archive, else the dev graph's memory notes."""
    if config.get("notes_corpus"):
        return config
    return memory_lane(config) or config


def is_memory_lane(config: Dict[str, Any]) -> bool:  # A note-lane config naming the memory lane
    return config.get("lane") == MEMORY_LANE


def stray_memory_files(
    memory_dir: Optional[str],     # The harness memory dir
    journal_path: Optional[str],   # The writes journal (its genesis ops name the files it once read)
) -> List[str]:  # The .md files there no journal sources: capture candidates (new-note), never ingested
    """A memory note is born on the graph; a markdown file that appears in the harness memory dir
    (MEMORY.md, the projected mirror, aside) is REPORTED for capture, never read by ingest."""
    if not memory_dir or not Path(memory_dir).is_dir():
        return []
    known = set()
    for op in read_journal(journal_path) if journal_path else []:
        p = (op.get("args") or {}).get("path")
        if op.get("verb") == "new-note" and p:
            known.add(str(Path(p).resolve()))
    return sorted(str(p) for p in Path(memory_dir).glob("*.md")
                  if p.name != "MEMORY.md" and str(p.resolve()) not in known)


# --- The capture: the note as the graph holds it, canonical -------------------------------

def title_records(
    note: Any,                                   # The note decomposed (lossless, the memory profile)
    recs: List[Tuple[str, Dict[str, Any]]],      # Its capture records (note, sections, places)
) -> Tuple[List[Tuple[str, Dict[str, Any]]], Optional[Any]]:  # (the records, the H1 Section moved -- None when the note has none)
    """An in-body H1 is the note's title (c9b29de5 (5)): its text moves into the frontmatter as
    `title`; the H1 element keeps its id and becomes the opening unheaded element; a preamble
    before it merges in (its text first) and retires; its children step up to the top level."""
    secs = list(note.sections)
    h1 = [s for s in secs if s.level == 1]
    if not h1:
        return recs, None
    if len(h1) > 1:
        raise NotesFoldError(f"{note.slug}: {len(h1)} in-body H1s -- one title per note")
    h = h1[0]
    before = secs[:secs.index(h)]
    if any(s.level != 0 or s.anchor != "_preamble" for s in before):
        raise NotesFoldError(f"{note.slug}: the H1 is not the note's first heading")
    fm = recs[0][1]["frontmatter"]
    if re.search(r"^title:", fm, re.M):
        raise NotesFoldError(f"{note.slug}: the frontmatter already names a title beside the in-body H1")
    lines = fm.split("\n")
    at = next((k for k, line in enumerate(lines) if line.startswith("name:")), None)
    if at is None:
        raise NotesFoldError(f"{note.slug}: no frontmatter name to place the title after")
    lines.insert(at + 1, "title: " + json.dumps(h.title, ensure_ascii=False))
    state = {a["section"]: dict(a) for v, a in recs if v == SECTION}
    place = {a["section"]: dict(a) for v, a in recs if v == PLACE}
    out_note = (NOTE, {**recs[0][1], "frontmatter": "\n".join(lines)})
    retire: List[str] = []
    text = state[h.id]["text"]
    if before:
        pre = before[0]
        lede = state[pre.id]["text"]
        text = f"{lede}\n\n{text}" if lede and text else (lede or text)
        place[h.id]["after"] = place[pre.id]["after"]
        del state[pre.id]   # its placement stays: a retired element is still homed in its note (RETIRE IS A FACT)
        retire.append(pre.id)
    state[h.id] = {**state[h.id], "title": None, "text": text}
    kids = [s.id for s in secs if place.get(s.id, {}).get("parent") == h.id]
    for k in kids:
        place[k]["parent"] = None
        if place[k]["after"] is None:
            place[k]["after"] = h.id
    for sid, a in place.items():   # a top-level sibling that followed the H1 follows its last child
        if a["after"] == h.id and sid not in kids:
            a["after"] = kids[-1] if kids else h.id
    out = ([out_note] + [(SECTION, a) for a in state.values()] + [(PLACE, a) for a in place.values()]
           + [(RETIRE, {"section": s}) for s in retire])
    return out, h
