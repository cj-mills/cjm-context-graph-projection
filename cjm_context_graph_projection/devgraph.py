"""Build the dev graph's nodes + edges from its sources (the dev-graph DRIVER).

Dev-domain-specific (this is where the general projection lib adopts the dev
schema): assemble the memory corpus (markdown -> Note nodes via the markdown
decomposer), a repo map (one Entity per cjm-* repo + DEPENDS_ON edges read
from each pyproject) and the code corpus the source journal folds into
(`codefold`) into the `(nodes, edges)` lists that extend_graph commits.

Kept separate from `projection`/`runtime` (which stay domain-neutral) so the pure
core remains extractable.
"""

import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cjm_dev_graph_schema.nodes import EntityNode
from cjm_dev_graph_schema.vocab import DevNodeKinds
from cjm_markdown_decompose_core.extract import corpus_index_files, note_from_file
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from .codefold import CodeFold
from .seeds import aliases_for, conceptual_key, seed_elements


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
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:  # (nodes, edges)
    """Decompose an arbitrary `<dir>/index.md` / `index.qmd` markdown corpus into graph elements.

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
    the precondition for carrying the membrane to posts (733d3b94)."""
    root = Path(corpus_root)
    # A RETIRED archive source (design amendment e916a4b9 (2)) is restored from the commit its
    # retire op recorded, never read from the tree: its file may be gone, and a copy still in
    # the tree must not claim the same identity twice
    retired = list(retired or [])
    if retired and not site_root:
        raise ValueError("retired sources need the site root they are restored from (`website_root`)")
    gone = {str((Path(site_root) / r["path"]).resolve()) for r in retired}
    files = [p for p in corpus_index_files(corpus_root) if str(p.resolve()) not in gone]
    notes = [note_from_file(str(p), corpus_root=str(root), profile=profile, lossless=True)
             for p in files]
    # The site's own pages (about, the front page, the listing hubs …) ride the same ingest as
    # archive Sources, so every public page is a node its site_path fact can hold (ruling
    # 96aff70e; user, 2026-09-28). Identity = the page's path under the site root.
    if site_pages:
        if not site_root:
            raise ValueError("site_pages need the site root they live under (`website_root`)")
        posts = {n.slug for n in notes}
        pages = [note_from_file(str(Path(site_root) / rel), corpus_root=str(site_root), profile=profile,
                                lossless=True, slug=site_page_slug(rel)) for rel in site_pages
                 if str((Path(site_root) / rel).resolve()) not in gone]
        clash = sorted(p.slug for p in pages if p.slug in posts)
        if clash:
            raise ValueError(f"site page identities collide with posts: {', '.join(clash)}")
        notes += pages
    if retired:
        from .archive import restore_retired
        back = restore_retired(site_root, retired, profile)
        live = {n.id for n in notes}
        dup = sorted(n.slug for n in back if n.id in live)
        if dup:
            raise ValueError(f"retired sources still ingested from the tree: {', '.join(dup)}")
        notes += back
    nodes, edges = corpus_graph_elements(notes, note_aliases)
    return stamp_note_profile(nodes, profile), edges   # the profile is READABLE at edit time (cbde404c)


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


def _cjm_dep_keys(pyproject: Path) -> List[str]:
    """The cjm-* dependency names from a pyproject (version specifiers stripped)."""
    try:
        data = tomllib.loads(pyproject.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return []
    deps = (data.get("project") or {}).get("dependencies") or []
    keys = []
    for d in deps:
        name = d.replace("'", "").replace('"', "").strip()
        name = name.split(">=")[0].split("==")[0].split("<")[0].split("~=")[0].split("[")[0].strip()
        if name.startswith("cjm-"):
            keys.append(name)
    return keys


def repo_map_elements(
    repos_dir: str,  # Dir holding the cjm-* repos (the active tree)
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:  # (Entity nodes, DEPENDS_ON edges)
    """One repo Entity per cjm-* repo (RENAME-STABLE keys) + DEPENDS_ON from pyproject.

    Each entity is keyed by its durable conceptual slug (name-independent), carries
    its current dir name + prior names as aliases, so a fact about a renamed repo
    keeps one home and old names still resolve. DEPENDS_ON targets resolve the
    pyproject dep name through the same conceptual-key map; an edge to a repo
    outside this tree still resolves to a stable id (the store drops it until that
    entity exists — same dangling semantics as note references)."""
    root = Path(repos_dir)
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not d.name.startswith("cjm-"):
            continue
        key = conceptual_key(d.name)
        ent = EntityNode(kind="repo", key=key, name=d.name, aliases=aliases_for(d.name),
                         properties={"path": str(d), "tier": "active"})
        nodes.append(ent.to_graph_node())
        pyproject = d / "pyproject.toml"
        if pyproject.exists():
            dep_keys = [conceptual_key(k) for k in _cjm_dep_keys(pyproject) if k != d.name]
            edges.extend(ent.depends_on_edges(dep_keys))
    return nodes, edges


def build_dev_graph_elements(
    memory_dir: str,                  # Dir of memory markdown files
    repos_dir: Optional[str] = None,  # Active cjm-* repos dir (None = skip the repo map)
    seed: bool = True,                # Include the hand-seeded fine-tier slots
    note_aliases: Optional[Dict[str, str]] = None,  # Confirmed link aliases (drifted -> canonical)
    code_fold: Optional[CodeFold] = None,  # The code lane folded over the source journal (None = skip code)
    skip_memory_paths: Optional[List[str]] = None,  # Memory `.md` paths NOT to read (journal-sourced under M3)
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
        rn, re = repo_map_elements(repos_dir)
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
    return nodes, edges


def site_page_slug(
    rel: str,  # A site page's path under the site root ("about.qmd", "series/notes/index.md")
) -> str:  # Its identity: the path without the extension, a directory index named by its directory
    """A site page's slug — "about.qmd" -> "about", "series/notes/index.md" -> "series/notes";
    the root index keeps "index" (the empty path names no page)."""
    stem = Path(rel).with_suffix("").as_posix()
    return stem[: -len("/index")] if stem.endswith("/index") else stem
