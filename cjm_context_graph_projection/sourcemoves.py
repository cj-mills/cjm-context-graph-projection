"""Which rows a moved source accounts for (design amendment a9176261 to 19edbe97 (6)).

Every db records the HEAD of each git source its ingest read — the archive clone, each repo of
the repo map — through the graph-storage capability's ingest record. Two dbs that read a source
at different HEADs (by convention a live db and its rebuild, with a post committed between the
two swaps) differ on that source's rows for that reason alone. This module names those rows
EXACTLY: it re-runs the ingest's own decomposition (`ArchiveSource`, the repo map's pyproject
decomposer) over every kept path that changed between the two commits, at both versions, and
returns the element ids they produce. rebuild-diff lists rows with those ids — and the edges
they originate — under 'source moved (a -> b)', apart from drift. Attribution, never exclusion
(ruling 6752db0a(9)): the rows are still printed and counted.

What cannot be attributed is said so, never guessed: a db with no record (built before the
record existed) attributes nothing, and a source whose commit the clone no longer holds (a
rewritten history) or whose lane cannot be rebuilt from the graph config is listed with why."""

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .devgraph import (_pyproject_decomposer, ARCHIVE_SOURCE, ArchiveSource, parse_source_id,
                       repo_entity, REPO_SOURCE)
from .gitfold import blobs_at, commit_exists, paths_between


def source_lane(
    kind: str,                # The source's kind (ARCHIVE_SOURCE | REPO_SOURCE)
    root: str,                # Its root (the archive's corpus root; a repo's dir)
    config: Dict[str, Any],   # The graph-sibling config of the lane (site pages, profile, journal)
) -> Tuple[str, Callable[[str], bool], Callable[[str, bytes], List[Dict[str, Any]]], Set[str]]:  # (work tree, keep, decompose -> element wires, ids a source's birth or removal adds)
    """The ingest's own view of one source: which paths it reads and what each version of a
    path decomposes to — the same objects the ingest folds with, so the attribution cannot
    disagree with the ingest about what a path produces."""
    if kind == ARCHIVE_SOURCE:
        from .archive import retired_sources
        src = ArchiveSource(root, config.get("notes_profile") or "quarto_post",
                            site_root=config.get("website_root"), site_pages=config.get("site_pages"),
                            retired=retired_sources(config.get("journal_path")))
        return src.top, src.keep, (lambda p, d: src.decompose(p, d)[1]), set()
    if kind == REPO_SOURCE:
        ent = repo_entity(root)
        dec = _pyproject_decomposer(ent, Path(root).name)
        return root, (lambda p: p == "pyproject.toml"), (lambda p, d: dec(p, d)[1]), {ent.to_graph_node()["id"]}
    raise ValueError(f"no lane knows the source kind {kind!r}")


def moved_element_ids(
    kind: str,                # The source's kind
    root: str,                # Its root
    a: Optional[str],         # The HEAD the first db read (None: the source is new in the second)
    b: Optional[str],         # The HEAD the second db read (None: the source is gone from it)
    config: Dict[str, Any],   # The lane's graph config
) -> Tuple[Set[str], List[str]]:  # (the element ids the moved paths produce at either version, those paths)
    """Decompose every kept path that changed between `a` and `b`, at both versions; a source
    born or removed between them also accounts for its root element (a repo's Entity)."""
    top, keep, decompose, whole = source_lane(kind, root, config)
    for c in (a, b):
        if c is not None and not commit_exists(top, c):
            raise ValueError(f"commit {c[:12]} is not in {top} (a rewritten history or another clone?)")
    paths = [p for p in paths_between(top, a, b) if keep(p)]
    ids: Set[str] = set(whole) if a is None or b is None else set()
    for c in (a, b):
        if c is None:
            continue
        for p, data in blobs_at(top, c, paths).items():
            ids |= {w["id"] for w in decompose(p, data)}
    return ids, paths


def _remap(
    sid: str,                              # A source id
    path_map: List[Tuple[str, str]],       # (old prefix, new prefix) pairs
) -> str:  # The id with its root's mapped prefix rewritten (a corpus moved on disk, 19f699d0)
    kind, root = parse_source_id(sid)
    for old, new in path_map:
        if root.startswith(old):
            return f"{kind}:{new}{root[len(old):]}"
    return sid


def attribute_moved_sources(
    rec_a: Dict[str, str],   # Graph A's ingest record (source id -> HEAD)
    rec_b: Dict[str, str],   # Graph B's ingest record
    path_map: Optional[List[Tuple[str, str]]] = None,  # (old prefix, new prefix) rewrites, applied to A's roots
    config: Optional[Dict[str, Any]] = None,           # The lane's graph config
) -> Dict[str, Any]:  # {record: {a, b}, sources: [one entry per source whose HEAD differs], ids: attributed element ids}
    """Every source whose HEAD differs between the two records, with the element ids its moved
    paths account for. A side with NO record attributes nothing: every source would read as
    born, and the whole lane would hide behind it."""
    pm = list(path_map or [])
    rec_a = {_remap(k, pm): v for k, v in rec_a.items()}
    out: Dict[str, Any] = {"record": {"a": len(rec_a), "b": len(rec_b)}, "sources": [], "ids": set()}
    if not rec_a or not rec_b:
        return out
    for sid in sorted(set(rec_a) | set(rec_b)):
        a, b = rec_a.get(sid), rec_b.get(sid)
        if a == b:
            continue
        kind, root = parse_source_id(sid)
        entry: Dict[str, Any] = {"source": sid, "a": a, "b": b,
                                 "status": "moved" if a and b else ("added" if b else "removed")}
        try:
            ids, paths = moved_element_ids(kind, root, a, b, config or {})
        except ValueError as e:
            entry.update(status="unattributed", reason=str(e))
        else:
            entry.update(paths=paths, elements=len(ids))
            out["ids"] |= ids
        out["sources"].append(entry)
    return out
