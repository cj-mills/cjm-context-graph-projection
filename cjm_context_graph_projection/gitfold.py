"""Ingested sources' times from git history (design amendment 19edbe97 to 8f6f2343; leg C 7ddcea72).

The archive clone and the repo map are the sources no journal records, so git history is their
time source: every version of a file in HEAD's ancestry, walked in reverse topological order
(deterministic per history), each at its commit's committer time (%ct, when the content entered
this history, the same on every clone). What is ingested is HEAD, never the working tree: a file
with uncommitted changes is REPORTED and ingested at its HEAD state, an untracked one is
reported and absent until it is committed, so nothing is ever stamped with the clock of the
ingest.

ELEMENT GRAIN. `ElementFold` folds the versions: each version of a file is decomposed into the
graph elements it holds and diffed against the file's previous version, and every element keeps

- created_at = the commit that began its CURRENT continuous run (held by at least one file);
- updated_at = the commit that last changed its content (label, endpoints, properties). The
  `sources` locator is provenance: it is refreshed to the latest version without counting as
  a change (the code fold's rule, 2cc81d3b).

Changes settle once per commit, so an element that moves between files inside one commit (a
post's index.md becoming index.qmd) keeps its run. A shared element (a Topic, held by every
file tagging it) runs while ANY holder holds it: its run is the union of its holders' runs.
A path frozen at a commit (a retired archive source, restored from the commit its retire op
recorded) ignores every later version: its times come from its history up to that commit.

THE WALK IS CHECKED. After the last commit, every walked path must hold exactly HEAD's blob; a
history the linear walk cannot reproduce (a merge whose resolution no walked version carries)
refuses loudly rather than stamping a guess."""

import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

_ZERO = "0" * 40
_TIMELESS = ("sources", "created_at", "updated_at")   # wire keys that are never content


def _git(
    root: str,   # A git work tree
    *args: str,  # The git subcommand + its arguments
) -> str:  # stdout (text)
    """Run one git command in `root`; a failure refuses loudly (the history IS the source)."""
    r = subprocess.run(["git", "-c", "core.quotePath=false", "-C", root, *args],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise ValueError(f"git {' '.join(args[:2])} failed in {root}: {r.stderr.strip()}")
    return r.stdout


_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def unquote_path(
    path: str,  # A path as git prints it (C-quoted when it holds a quote, backslash or control byte)
) -> str:  # The path itself
    """Undo git's C-style path quoting (core.quotePath=false leaves UTF-8 bare, not `"` / `\\`)."""
    if not (len(path) >= 2 and path[0] == path[-1] == '"'):
        return path
    out, s, i = bytearray(), path[1:-1], 0
    while i < len(s):
        c = s[i]
        if c != "\\":
            out += c.encode("utf-8")
            i += 1
        elif s[i + 1] in _ESCAPES:
            out.append(_ESCAPES[s[i + 1]])
            i += 2
        else:                                   # \ooo: one raw byte in octal
            out.append(int(s[i + 1: i + 4], 8))
            i += 4
    return out.decode("utf-8")


def git_toplevel(
    path: str,  # Any path inside a work tree
) -> Optional[str]:  # The work tree's root, or None when the path is under no git history
    """The work tree `path` belongs to (None = no git history: no durable time)."""
    r = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True)
    return str(Path(r.stdout.strip()).resolve()) if r.returncode == 0 and r.stdout.strip() else None


def head_commit(
    root: str,  # A git work tree
) -> Optional[str]:  # HEAD's commit, or None (no commit yet)
    r = subprocess.run(["git", "-C", root, "rev-parse", "--verify", "-q", "HEAD"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None


def head_tree(
    root: str,  # A git work tree
) -> Dict[str, str]:  # path (relative to the root) -> blob id at HEAD
    """Every file HEAD holds."""
    if head_commit(root) is None:
        return {}
    out: Dict[str, str] = {}
    for line in _git(root, "ls-tree", "-r", "HEAD").splitlines():
        meta, path = line.split("\t", 1)
        out[unquote_path(path)] = meta.split()[2]
    return out


def worktree_changes(
    root: str,                   # A git work tree
    keep: Callable[[str], bool],  # Which relative paths the ingest reads
) -> Tuple[List[str], List[str]]:  # (uncommitted: tracked paths differing from HEAD, untracked paths)
    """What HEAD does not carry: reported by the ingest, never read from the tree."""
    uncommitted, untracked = [], []
    entries = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        i += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        moved_from = None
        if code[0] in "RC":           # a staged rename / copy: its source path is the next entry
            moved_from, i = entries[i], i + 1
        for p in (path, moved_from):
            if p and keep(p):
                (untracked if code == "??" else uncommitted).append(p)
    return sorted(set(uncommitted)), sorted(set(untracked))


def git_versions(
    root: str,                   # A git work tree
    keep: Callable[[str], bool],  # Which relative paths are walked
) -> List[Tuple[str, float, List[Tuple[str, Optional[str]]]]]:  # [(commit, %ct, [(path, blob | None = deleted)])]
    """Every commit in HEAD's ancestry, reverse topological order, with the kept paths it set.

    No path limiting (so no history simplification hides a side branch); a merge lists a path
    only when its result differs from every parent (`-c`), i.e. when the merge itself made a
    version. Each path's last walked blob is checked against HEAD by `ElementFold`'s driver."""
    if head_commit(root) is None:
        return []
    out: List[Tuple[str, float, List[Tuple[str, Optional[str]]]]] = []
    cur: Optional[Tuple[str, float, List[Tuple[str, Optional[str]]]]] = None
    log = _git(root, "log", "--reverse", "--topo-order", "--root", "--raw", "-c", "--no-abbrev",
               "--no-renames", "--format=C %H %ct")
    for line in log.splitlines():
        if line.startswith("C "):
            _, sha, ct = line.split()
            cur = (sha, float(ct), [])
            out.append(cur)
        elif line.startswith(":") and cur is not None:
            meta, path = line.split("\t", 1)
            path = unquote_path(path)
            if not keep(path):
                continue
            colons = len(meta) - len(meta.lstrip(":"))
            f = meta[colons:].split()
            blob = f[2 * (colons + 1) - 1]   # the result's blob: modes, then parent blobs, then it
            cur[2].append((path, None if blob == _ZERO else blob))
    return out


def read_blobs(
    root: str,             # A git work tree
    blobs: Iterable[str],  # Blob ids
) -> Dict[str, bytes]:  # blob id -> its bytes
    """Many blobs through one `git cat-file --batch`."""
    want = sorted(set(blobs))
    if not want:
        return {}
    r = subprocess.run(["git", "-C", root, "cat-file", "--batch"], input="\n".join(want).encode() + b"\n",
                       capture_output=True)
    if r.returncode != 0:
        raise ValueError(f"git cat-file failed in {root}: {r.stderr.decode().strip()}")
    out: Dict[str, bytes] = {}
    buf, i = r.stdout, 0
    while i < len(buf):
        nl = buf.index(b"\n", i)
        head = buf[i:nl].decode().split()
        if len(head) < 3 or head[1] != "blob":
            raise ValueError(f"git cat-file: {' '.join(head)} in {root}")
        size = int(head[2])
        out[head[0]] = buf[nl + 1: nl + 1 + size]
        i = nl + 1 + size + 1
    return out


def _content(
    wire: Dict[str, Any],  # A node or edge wire
) -> Dict[str, Any]:  # What a change of counts as a content change
    return {k: v for k, v in wire.items() if k not in _TIMELESS}


class ElementFold:
    """Element times over file versions: runs by holder count, content changes by wire."""

    def __init__(self):
        self.held: Dict[str, Dict[str, Dict[str, Any]]] = {}   # path -> {element id: wire}
        self.recs: Dict[str, Dict[str, Any]] = {}              # element id -> {wire, created_at, updated_at, n}

    def commit(
        self,
        ts: float,                                               # The commit's %ct
        changes: Dict[str, Optional[Dict[str, Dict[str, Any]]]],  # path -> its elements now (None = it left)
    ) -> None:
        """Settle one commit's versions at once."""
        delta: Dict[str, int] = {}
        latest: Dict[str, Dict[str, Any]] = {}
        for path in sorted(changes):
            new = changes[path]
            old = self.held.pop(path, {})
            if new is not None:
                self.held[path] = new
            for eid in old:
                delta[eid] = delta.get(eid, 0) - 1
            for eid, w in (new or {}).items():
                delta[eid] = delta.get(eid, 0) + 1
                latest[eid] = w
        for eid in sorted(delta):
            rec = self.recs.get(eid)
            n = (rec["n"] if rec else 0) + delta[eid]
            if n <= 0:
                self.recs.pop(eid, None)
                continue
            wire = latest.get(eid)
            if rec is None:
                self.recs[eid] = {"wire": wire, "created_at": ts, "updated_at": ts, "n": n}
                continue
            rec["n"] = n
            if wire is not None:
                if _content(wire) != _content(rec["wire"]):
                    rec["updated_at"] = ts
                rec["wire"] = wire

    def times(
        self,
        eid: str,  # An element id
    ) -> Optional[Tuple[float, float]]:  # (created_at, updated_at), or None (not held at the end)
        rec = self.recs.get(eid)
        return (rec["created_at"], rec["updated_at"]) if rec else None

    def stamp(
        self,
        nodes: List[Dict[str, Any]],  # HEAD's node wires
        edges: List[Dict[str, Any]],  # HEAD's edge wires
    ) -> List[str]:  # Ids the fold holds no time for (a bug in the caller's decomposition: never stamped now())
        """Set every HEAD element's created_at / updated_at from its run, in place."""
        missing = []
        for w in list(nodes) + list(edges):
            t = self.times(w["id"])
            if t is None:
                missing.append(w["id"])
                continue
            w["created_at"], w["updated_at"] = t
        return missing


@dataclass
class FoldedHistory:
    """One work tree's kept paths folded to HEAD: the HEAD-state per-path payloads + the times."""
    root: str                                                     # The work tree
    head: Optional[str]                                           # HEAD's commit (None: no commit)
    payloads: Dict[str, Any] = field(default_factory=dict)        # path -> the decomposer's payload at its last walked version
    fold: ElementFold = field(default_factory=ElementFold)
    commits: int = 0                                              # Commits walked
    versions: int = 0                                             # Versions decomposed
    uncommitted: List[str] = field(default_factory=list)          # Kept paths whose tree differs from HEAD (ingested at HEAD)
    untracked: List[str] = field(default_factory=list)            # Kept paths HEAD does not hold (absent until committed)


def fold_history(
    root: str,                                                   # A git work tree
    keep: Callable[[str], bool],                                 # Which relative paths are walked
    decompose: Callable[[str, bytes], Tuple[Any, List[Dict[str, Any]]]],  # (path, bytes) -> (payload, element wires)
    freeze: Optional[Dict[str, str]] = None,                     # path -> the commit its history stops at
) -> FoldedHistory:
    """Walk the kept paths' versions, decompose each, fold the element times, check the end
    state: every unfrozen path ends at HEAD's blob and every frozen path's commit was walked."""
    freeze = dict(freeze or {})
    walk = git_versions(root, lambda p: keep(p) or p in freeze)
    blobs = read_blobs(root, (b for _, _, ch in walk for _, b in ch if b))
    out = FoldedHistory(root=root, head=head_commit(root), commits=len(walk))
    last: Dict[str, Optional[str]] = {}
    frozen: Set[str] = set()
    for sha, ts, changes in walk:
        step: Dict[str, Optional[Dict[str, Dict[str, Any]]]] = {}
        for path, blob in changes:
            if path in frozen:
                continue
            last[path] = blob
            if blob is None:
                out.payloads.pop(path, None)
                step[path] = None
                continue
            payload, wires = decompose(path, blobs[blob])
            out.payloads[path] = payload
            step[path] = {w["id"]: w for w in wires}
            out.versions += 1
        if step:
            out.fold.commit(ts, step)
        frozen |= {p for p, c in freeze.items() if c == sha}
    unseen = sorted(p for p in freeze if p not in frozen)
    if unseen:
        raise ValueError(f"frozen path(s) whose commit is not in HEAD's ancestry of {root} (a shallow "
                         "clone or rewritten history?) — the rebuild cannot restore it: "
                         + ", ".join(f"{p} @ {freeze[p][:12]}" for p in unseen))
    tree = head_tree(root)
    bad = sorted(p for p, b in last.items() if p not in frozen and tree.get(p) != b)
    bad += sorted(p for p, b in tree.items() if keep(p) and p not in last and p not in frozen)
    if bad:
        raise ValueError(f"the linear walk of {root} does not reproduce HEAD for: {', '.join(bad[:10])}"
                         f"{' …' if len(bad) > 10 else ''} — a merge carries a version no commit made")
    out.uncommitted, out.untracked = worktree_changes(root, keep)
    return out


def cjm_dep_names(
    text: str,  # A pyproject.toml's text
) -> List[str]:  # The cjm-* dependency names (version specifiers stripped)
    """The cjm-* dependencies a pyproject declares (an unparseable file declares none)."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    deps = (data.get("project") or {}).get("dependencies") or []
    keys = []
    for d in deps:
        name = d.replace("'", "").replace('"', "").strip()
        name = name.split(">=")[0].split("==")[0].split("<")[0].split("~=")[0].split("[")[0].strip()
        if name.startswith("cjm-"):
            keys.append(name)
    return keys


def root_commit_time(
    root: str,  # A git work tree
) -> Optional[float]:  # The earliest root commit's %ct (None: no commit)
    """When a repo began: its root commit (the earliest, when several histories were joined)."""
    if head_commit(root) is None:
        return None
    times = [float(line.split()[1]) for line in
             _git(root, "log", "--max-parents=0", "--format=%H %ct", "HEAD").splitlines() if line.strip()]
    return min(times) if times else None


def commit_exists(
    root: str,    # A git work tree
    commit: str,  # A commit id
) -> bool:  # Whether the clone holds that commit
    r = subprocess.run(["git", "-C", root, "rev-parse", "--verify", "-q", f"{commit}^{{commit}}"],
                       capture_output=True, text=True)
    return r.returncode == 0


def paths_between(
    root: str,            # A git work tree
    a: Optional[str],     # The earlier commit (None: the source did not exist — every path at b)
    b: Optional[str],     # The later commit (None: the source is gone — every path at a)
) -> List[str]:  # Relative paths whose blob differs between the two commits (added and deleted too)
    """What moved between two commits of one source (renames are a delete + an add: identity
    is the path, as in the fold)."""
    if a is None or b is None:
        return sorted(p for p in _git(root, "ls-tree", "-r", "-z", "--name-only", a or b).split("\0") if p)
    return sorted(p for p in _git(root, "diff", "--no-renames", "--name-only", "-z", a, b).split("\0") if p)


def blobs_at(
    root: str,             # A git work tree
    commit: str,           # A commit id
    paths: Iterable[str],  # Relative paths
) -> Dict[str, bytes]:  # path -> its bytes at that commit (a path the commit does not hold is absent)
    """Several paths' contents at one commit through one ls-tree + one cat-file batch."""
    want = sorted(set(paths))
    if not want:
        return {}
    ids: Dict[str, str] = {}
    for entry in _git(root, "ls-tree", "-r", "-z", commit, "--", *(f":(literal){p}" for p in want)).split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        if meta.split()[1] == "blob":
            ids[path] = meta.split()[2]
    data = read_blobs(root, ids.values())
    return {p: data[i] for p, i in ids.items()}
