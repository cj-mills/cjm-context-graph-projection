"""Build the dev graph's nodes + edges from its sources (the dev-graph DRIVER).

Dev-domain-specific (this is where the general projection lib adopts the dev
schema): assemble the memory corpus (markdown -> Note nodes via the markdown
decomposer), a repo map (one Entity per cjm-* repo + DEPENDS_ON edges read
from each pyproject at HEAD, timed from git history — `gitfold`) and the code corpus the source journal folds into
(`codefold`) into the `(nodes, edges)` lists that extend_graph commits.

Kept separate from `projection`/`runtime` (which stay domain-neutral) so the pure
core remains extractable.
"""

from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from cjm_dev_graph_schema.nodes import EntityNode
from cjm_dev_graph_schema.vocab import DevNodeKinds
from cjm_markdown_decompose_core.extract import INDEX_FILENAMES, note_from_file, note_from_text
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from .codefold import CodeFold
from .gitfold import cjm_dep_names, fold_history, git_toplevel, head_tree, root_commit_time
from .seeds import aliases_for, conceptual_key, RENAME_ALIASES_AUTHORED, seed_elements


def memory_elements(
    memory_dir: str,  # Dir of memory markdown files
    note_aliases: Optional[Dict[str, str]] = None,  # Confirmed {drifted-slug: canonical-slug} link aliases
    skip_paths: Optional[List[str]] = None,  # `.md` paths NOT to read (journal-sourced under M3)
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:  # (Note nodes, REFERENCES edges)
    """Decompose every memory markdown file (except MEMORY.md) into graph elements.

    Decomposed `lossless=True` (M1): each note carries its verbatim `frontmatter_raw`
    and its body becomes ordered Section nodes with heading-inclusive `raw` spans (+ a
    level-0 preamble), so the file reconstructs BYTE-EXACT from the graph — memory is
    the high-stakes corpus (the sole human-readable planning record), so the bar is
    whole-file fidelity, not the posts' Scope-A section grain. The `read` verb delivers
    these bodies, which is what lets graph-pull replace reading the `.md` files.

    `skip_paths` are the files the M3 authority flip has moved on-graph (a genesis
    `new-note` op reconstructs them from the journal), so reading them here would
    double-build the note — the per-note flip that widens slice->corpus mechanically.

    Confirmed `note_aliases` (the worklist's output, read off the graph) resolve
    drifted `[[wiki-links]]` to their real note so the once-dangling edge lands."""
    mem = Path(memory_dir)
    skip = {str(Path(p).resolve()) for p in (skip_paths or [])}
    files = sorted(p for p in mem.glob("*.md")
                   if p.name != "MEMORY.md" and str(p.resolve()) not in skip)
    notes = [note_from_file(str(p), corpus_root=str(mem), lossless=True) for p in files]
    return corpus_graph_elements(notes, note_aliases)


def notes_corpus_elements(
    corpus_root: str,                  # Root of an arbitrary markdown notes corpus (e.g. christianjmills/posts)
    profile: str = "quarto_post",      # Relationship-harvest profile (see the markdown core's PROFILES)
    note_aliases: Optional[Dict[str, str]] = None,  # Confirmed {drifted-slug: canonical-slug} link aliases
    *,
    site_root: Optional[str] = None,          # The site project root the pages live under
    site_pages: Optional[List[str]] = None,   # The site's own pages, relative to site_root (config DATA)
    retired: Optional[List[Dict[str, Any]]] = None,  # The journal's retire records (archive.retired_sources): restored from git, never read from the tree
    report: Optional[Dict[str, Any]] = None,  # Filled with what was read: {root, head, commits, versions, uncommitted, untracked, sources}
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:  # (nodes, edges), each carrying its created_at / updated_at
    """Decompose a git-held `<dir>/index.md` / `index.qmd` markdown corpus into graph elements.

    The corpus analogue of `memory_elements`, generalized off the hardcoded dev
    memory dir: every `index.md` or `index.qmd` under the root (`corpus_index_files`; the SSG permalink convention —
    `posts/<slug>/index.md`, nested allowed) becomes a Note identified by its
    directory permalink, with the per-source-type relationship harvesters (the
    `profile`, default Quarto blog posts) lighting up Topic/Series/cross-post
    edges. Self-contained — this is the FEDERATION SEAM's first leaf: ingested
    into its OWN `--graph-db-path` (a separate persistent graph), kept distinct
    from the private dev/planning graph (a public corpus → its own boundary).

    Decomposed `lossless=True` (2026-09-03, finding 24825bd6): every post Section
    carries its heading-inclusive verbatim `raw` span + `order`, the pre-first-heading
    lede is a level-0 `_preamble` Section and the Note carries `frontmatter_raw`, so
    `read <note>` reconstructs the post byte-for-byte and the within-note sequence is
    recoverable by traversal — the fidelity the memory corpus has had since M1, and
    the precondition for carrying the membrane to posts (733d3b94).

    THE ARCHIVE IS HEAD (design amendment 19edbe97): the corpus is read from the commit its
    work tree's HEAD names, never from the tree, and every element's times come from git
    history at the element grain (`gitfold`): created_at = the commit that began its current
    continuous run, updated_at = the commit that last changed it. A file with uncommitted
    changes is reported and ingested at HEAD; an untracked one is reported and absent until
    committed. A corpus under no git history has no durable time and is refused. The
    report's `sources` is the ingest record's entry (DEC a9176261): the archive and the HEAD read."""
    # The archive's paths and their decomposition: one definition with rebuild-diff's
    # attribution of a moved archive (DEC a9176261). A RETIRED archive source (design amendment
    # e916a4b9 (2)) is restored from the commit its retire op recorded, never read from HEAD: its
    # file may be gone, and a copy still at HEAD must not claim the same identity twice. Its
    # times stop at that commit (19edbe97 (7)). The site's own pages (about, the front page, the
    # listing hubs …) ride the same ingest as archive Sources, so every public page is a node its
    # site_path fact can hold (ruling 96aff70e; user, 2026-09-28).
    src = ArchiveSource(corpus_root, profile, note_aliases, site_root=site_root, site_pages=site_pages,
                        retired=retired)
    hist = fold_history(src.top, src.keep, src.decompose,
                        freeze={p: r["commit"] for p, r in src.by_retired.items()})
    tree = head_tree(src.top)
    post_paths = sorted((p for p in tree if src.is_post(p) and p not in src.by_retired),
                        key=lambda p: PurePosixPath(p).parts)
    by_dir: Dict[str, List[str]] = {}
    for p in post_paths:
        by_dir.setdefault(str(PurePosixPath(p).parent), []).append(p)
    both = sorted(d for d, fs in by_dir.items() if len(fs) > 1)
    if both:
        raise ValueError("post directories holding more than one index file "
                         f"({', '.join(INDEX_FILENAMES)}): " + ", ".join(both))
    notes = [hist.payloads[p] for p in post_paths]
    posts = {n.slug for n in notes}
    missing = sorted(p for p in src.pages if p not in tree and p not in hist.untracked)
    if missing:
        raise ValueError(f"site pages HEAD does not hold: {', '.join(missing)}")
    page_notes = [hist.payloads[p] for p in src.pages if p in tree]
    clash = sorted(p.slug for p in page_notes if p.slug in posts)
    if clash:
        raise ValueError(f"site page identities collide with posts: {', '.join(clash)}")
    notes += page_notes
    back = []
    for p, r in src.by_retired.items():
        note = hist.payloads.get(p)
        if note is None:
            raise ValueError(f"retired source {p} is not at commit {r['commit'][:12]} in {site_root} "
                             "(a shallow clone or rewritten history?) — the rebuild cannot restore it")
        if note.id != r["note"]:
            raise ValueError(f"retired source {p} restores as {note.id}, not the retired node {r['note']}")
        back.append(note)
    live = {n.id for n in notes}
    dup = sorted(n.slug for n in back if n.id in live)
    if dup:
        raise ValueError(f"retired sources still ingested from HEAD: {', '.join(dup)}")
    notes += back
    nodes, edges = corpus_graph_elements(notes, note_aliases)
    timeless = hist.fold.stamp(nodes, edges)
    if timeless:
        raise ValueError(f"{len(timeless)} archive element(s) the history fold holds no time for "
                         f"(first: {timeless[0]}) — the fold and the ingest decompose differently")
    if report is not None:
        report.update({"root": src.top, "head": hist.head, "commits": hist.commits, "versions": hist.versions,
                       "uncommitted": hist.uncommitted, "untracked": hist.untracked,
                       "sources": {source_id(ARCHIVE_SOURCE, corpus_root): hist.head} if hist.head else {}})
    return stamp_note_profile(nodes, profile), edges   # the profile is READABLE at edit time (cbde404c)


def _pyproject_decomposer(
    ent: EntityNode,  # The repo Entity whose pyproject versions are folded
    name: str,        # Its current dir name (a self-dependency is dropped)
):  # (path, bytes) -> (dep keys, DEPENDS_ON wires): one pyproject version's dependency edges
    """The repo map's per-version decomposition for `fold_history` (19edbe97 (4))."""
    def decompose(path: str, data: bytes) -> Tuple[Any, List[Dict[str, Any]]]:
        keys = [conceptual_key(k) for k in cjm_dep_names(data.decode("utf-8")) if k != name]
        return keys, ent.depends_on_edges(keys)
    return decompose


def stamp_note_profile(
    nodes: List[Dict[str, Any]],  # Node wire dicts (a corpus_graph_elements result)
    profile: Optional[str],       # The harvest profile the notes were parsed with (None = auto-detected, leave unstamped)
) -> List[Dict[str, Any]]:  # The same list, each Note carrying `profile`
    """Record the relationship-harvest profile on every Note wire dict (in place).

    Harvest-on-edit (cbde404c) re-runs the profile's harvesters when a note is edited
    on-graph, so the profile a note was ingested/born with has to be READABLE off the
    node — an explicit `quarto_post` corpus would otherwise re-detect from frontmatter
    alone at edit time. `None` (auto-detected) stamps nothing: detection re-runs the
    same way it did at ingest."""
    if profile:
        for n in nodes:
            if n.get("label") == DevNodeKinds.NOTE:
                n.setdefault("properties", {})["profile"] = profile
    return nodes


def repo_map_elements(
    repos_dir: str,  # Dir holding the cjm-* repos (the active tree)
    report: Optional[Dict[str, Any]] = None,  # Filled with {repos, no_history, uncommitted, sources}
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:  # (Entity nodes, DEPENDS_ON edges), each carrying its times
    """One repo Entity per cjm-* repo (RENAME-STABLE keys) + DEPENDS_ON from pyproject.

    Each entity is keyed by its durable conceptual slug (name-independent), carries
    its current dir name + prior names as aliases, so a fact about a renamed repo
    keeps one home and old names still resolve. DEPENDS_ON targets resolve the
    pyproject dep name through the same conceptual-key map; an edge to a repo
    outside this tree still resolves to a stable id (the store drops it until that
    entity exists — same dangling semantics as note references).

    TIMES FROM GIT (design amendment 19edbe97 (4)): a repo Entity's created_at =
    updated_at = its root commit — nothing a commit changes is on it (its aliases are seed
    data, so a renamed repo's updated_at is the aliases' authored time); DEPENDS_ON is read
    from pyproject.toml at HEAD and timed from the start of that dependency's current
    continuous run across the pyproject versions. A cjm-* dir with no git history of its
    own has no durable time: it is reported and left out of the map."""
    root = Path(repos_dir)
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    no_history: List[str] = []
    uncommitted: List[str] = []
    sources: Dict[str, str] = {}   # the ingest record's entries: each repo and the HEAD read (DEC a9176261)
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not d.name.startswith("cjm-"):
            continue
        born = root_commit_time(str(d)) if git_toplevel(str(d)) == str(d.resolve()) else None
        if born is None:
            no_history.append(d.name)
            continue
        aliases = aliases_for(d.name)
        ent = repo_entity(str(d))
        wire = ent.to_graph_node()
        wire["created_at"] = born
        wire["updated_at"] = max(born, RENAME_ALIASES_AUTHORED) if aliases else born
        nodes.append(wire)
        hist = fold_history(str(d), lambda p: p == "pyproject.toml", _pyproject_decomposer(ent, d.name))
        deps = ent.depends_on_edges(hist.payloads.get("pyproject.toml") or [])
        timeless = hist.fold.stamp([], deps)
        if timeless:
            raise ValueError(f"{d.name}: {len(timeless)} DEPENDS_ON edge(s) with no time in its history")
        edges.extend(deps)
        uncommitted += [f"{d.name}/{p}" for p in hist.uncommitted]
        sources[source_id(REPO_SOURCE, str(d))] = hist.head
    if report is not None:
        report.update({"repos": len(nodes), "no_history": no_history, "uncommitted": uncommitted,
                       "sources": sources})
    return nodes, edges


def build_dev_graph_elements(
    memory_dir: str,                  # Dir of memory markdown files
    repos_dir: Optional[str] = None,  # Active cjm-* repos dir (None = skip the repo map)
    seed: bool = True,                # Include the hand-seeded fine-tier slots
    note_aliases: Optional[Dict[str, str]] = None,  # Confirmed link aliases (drifted -> canonical)
    code_fold: Optional[CodeFold] = None,  # The code lane folded over the source journal (None = skip code)
    artifact_fold: Optional[Any] = None,  # The artifact lane (artifacts.ArtifactFold) folded over the same journal (None = skip artifacts; design 9a7224a7)
    skip_memory_paths: Optional[List[str]] = None,  # Memory `.md` paths NOT to read (journal-sourced under M3)
    report: Optional[Dict[str, Any]] = None,  # Filled with {"repo_map": repo_map_elements' report}
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:  # (all nodes, all edges)
    """Assemble the full dev graph: memory notes (+ refs), the repo map (+ deps),
    the hand-seeded fine-tier slots (the torch/hf contradiction, the stale version
    slot, the class subjects), and — when `code_fold` is given — the code corpus the
    source journal projects (design amendment 2cc81d3b): every code node and edge
    carries the time of the record that produced it, and the inventory is the
    journal's live keys, never a list of repos.

    `skip_memory_paths` (the M3 genesis-imported notes) are left to journal replay to
    reconstruct rather than read from disk — the authority flip, scoped per note."""
    nodes, edges = memory_elements(memory_dir, note_aliases, skip_paths=skip_memory_paths)
    if repos_dir:
        repo_report: Dict[str, Any] = {}
        rn, re = repo_map_elements(repos_dir, report=repo_report)
        if report is not None:
            report["repo_map"] = repo_report
        nodes += rn
        edges += re
    if seed:
        sn, se = seed_elements()
        nodes += sn
        edges += se
    if code_fold is not None:
        cn, ce = code_fold.elements()
        nodes += cn
        edges += ce
    if artifact_fold is not None:   # the observed-source artifacts: one node per identity (9a7224a7)
        an, ae = artifact_fold.elements()
        nodes += an
        edges += ae
    return nodes, edges


def site_page_slug(
    rel: str,  # A site page's path under the site root ("about.qmd", "series/notes/index.md")
) -> str:  # Its identity: the path without the extension, a directory index named by its directory
    """A site page's slug — "about.qmd" -> "about", "series/notes/index.md" -> "series/notes";
    the root index keeps "index" (the empty path names no page)."""
    stem = Path(rel).with_suffix("").as_posix()
    return stem[: -len("/index")] if stem.endswith("/index") else stem


def source_id(
    kind: str,  # ARCHIVE_SOURCE | REPO_SOURCE
    root: str,  # The source's root (the archive's corpus root; a repo's dir)
) -> str:  # Its id in the ingest record: "<kind>:<resolved root>"
    """An ingested git source's id (DEC a9176261): its kind and where it lives — the root a
    rebuild-diff runs git in, rewritten by a path map like every other path."""
    return f"{kind}:{Path(root).resolve()}"


def parse_source_id(
    sid: str,  # An ingest-record source id
) -> Tuple[str, str]:  # (kind, root)
    kind, sep, root = sid.partition(":")
    if not sep or not root:
        raise ValueError(f"not an ingest-record source id: {sid!r}")
    return kind, root


def repo_entity(
    repo_dir: str,  # A cjm-* repo dir under the repos dir
) -> EntityNode:  # Its repo Entity: the rename-stable key, the current name, prior names as aliases
    d = Path(repo_dir)
    return EntityNode(kind="repo", key=conceptual_key(d.name), name=d.name, aliases=aliases_for(d.name),
                      properties={"path": str(d), "tier": "active"})


class ArchiveSource:
    """The archive's kept paths and their decomposition — ONE definition for the ingest that
    folds it (`notes_corpus_elements`) and for rebuild-diff's attribution of a moved archive
    (DEC a9176261), so the rows a moved path accounts for are exactly the rows it ingests to.

    Paths are relative to the archive's work tree (`top`): the posts under the corpus root
    (`<prefix>.../index.md|qmd`), the site's own pages (identity = their path, 96aff70e) and
    the retired sources (restored from the commit their retire op recorded, e916a4b9 (2) —
    never kept from HEAD)."""

    def __init__(
        self,
        corpus_root: str,              # Root of the posts corpus (e.g. christianjmills/posts)
        profile: str = "quarto_post",  # Relationship-harvest profile
        note_aliases: Optional[Dict[str, str]] = None,  # Confirmed {drifted-slug: canonical-slug} link aliases
        *,
        site_root: Optional[str] = None,          # The site project root the pages live under
        site_pages: Optional[List[str]] = None,   # The site's own pages, relative to site_root
        retired: Optional[List[Dict[str, Any]]] = None,  # The journal's retire records
    ):
        self.root = Path(corpus_root)
        top = git_toplevel(str(self.root))
        if top is None:
            raise ValueError(f"the archive {corpus_root} is under no git history — its times come from "
                             "git (design amendment 19edbe97): commit it first")
        retired = list(retired or [])
        if retired and not site_root:
            raise ValueError("retired sources need the site root they are restored from (`website_root`)")
        if site_pages and not site_root:
            raise ValueError("site_pages need the site root they live under (`website_root`)")
        if site_root and str(Path(site_root).resolve()) != top:
            raise ValueError(f"the site root {site_root} is not the archive's work tree {top}")
        self.top, self.profile, self.note_aliases, self.site_root = top, profile, note_aliases, site_root
        rel = self.root.resolve().relative_to(top).as_posix()
        self.prefix = "" if rel == "." else rel + "/"
        self.by_retired = {r["path"]: r for r in retired}
        self.pages = {p: site_page_slug(p) for p in (site_pages or []) if p not in self.by_retired}

    def is_post(self, path: str) -> bool:  # A post's index file under the corpus root
        return path.startswith(self.prefix) and PurePosixPath(path).name in INDEX_FILENAMES

    def keep(self, path: str) -> bool:  # What HEAD's walk ingests (a retired source is frozen instead)
        return (self.is_post(path) or path in self.pages) and path not in self.by_retired

    def decompose(
        self,
        path: str,   # A kept (or frozen) path, relative to the work tree
        data: bytes,  # One version's bytes
    ) -> Tuple[Any, List[Dict[str, Any]]]:  # (the Note, its element wires)
        text = data.decode("utf-8")
        if path in self.by_retired or path in self.pages:
            slug = self.by_retired[path]["slug"] if path in self.by_retired else self.pages[path]
            note = note_from_text(str(Path(self.site_root) / path), text, corpus_root=str(self.site_root),
                                  profile=self.profile, lossless=True, slug=slug)
        else:
            note = note_from_text(str(self.root / path[len(self.prefix):]), text, corpus_root=str(self.root),
                                  profile=self.profile, lossless=True)
        n, e = corpus_graph_elements([note], self.note_aliases)
        return note, n + e


ARCHIVE_SOURCE = "archive"  # The ingest record's kind for the notes lane's archive clone (DEC a9176261)
REPO_SOURCE = "repo"        # The ingest record's kind for one repo of the repo map
