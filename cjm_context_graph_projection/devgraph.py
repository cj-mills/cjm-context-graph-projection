"""Build the dev graph's nodes + edges from its sources (the dev-graph DRIVER).

Dev-domain-specific (this is where the general projection lib adopts the dev
schema): assemble the memory corpus (markdown -> Note nodes via the markdown
decomposer) and a repo map (one Entity per cjm-* repo + DEPENDS_ON edges read
from each pyproject) into the `(nodes, edges)` lists that extend_graph commits.

Kept separate from `projection`/`runtime` (which stay domain-neutral) so the pure
core remains extractable.
"""

import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cjm_context_graph_layer.grammar import make_edge
from cjm_context_graph_primitives.provenance import SourceRef
from cjm_dev_graph_schema.nodes import EntityNode
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations
from cjm_markdown_decompose_core.extract import note_from_file
from cjm_markdown_decompose_core.ingest import corpus_graph_elements
from cjm_notebook_decompose_core.compose import (decompose_notebook, decompose_notebook_file,
                                                 module_path_for_notebook)
from cjm_notebook_decompose_core.ingest import notebook_graph_elements
from cjm_notebook_decompose_core.read import parse_notebook
from cjm_python_decompose_core.extract import decompose_package, decompose_text
from cjm_python_decompose_core.ingest import (corpus_graph_elements as code_corpus_elements,
                                              resolve_import)

from .seeds import aliases_for, conceptual_key, seed_elements
from .source_state import graph_sourced_modules, is_test_module_path, latest_source_ops


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
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:  # (nodes, edges)
    """Decompose an arbitrary `<dir>/index.md` markdown corpus into graph elements.

    The corpus analogue of `memory_elements`, generalized off the hardcoded dev
    memory dir: every `index.md` under the root (the SSG permalink convention —
    `posts/<slug>/index.md`, nested allowed) becomes a Note identified by its
    directory permalink, with the per-source-type relationship harvesters (the
    `profile`, default Quarto blog posts) lighting up Topic/Series/cross-post
    edges. Self-contained — this is the FEDERATION SEAM's first leaf: ingested
    into its OWN `--graph-db-path` (a separate persistent graph), kept distinct
    from the private dev/planning graph (a public corpus → its own boundary)."""
    root = Path(corpus_root)
    files = sorted(root.rglob("index.md"))
    notes = [note_from_file(str(p), corpus_root=str(root), profile=profile, with_sections=True)
             for p in files]
    return corpus_graph_elements(notes, note_aliases)


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


def code_elements(
    code_repos: List[str],  # Repo dirs whose own package is decomposed as code (the code source-type)
    source_journal_path: Optional[str] = None,  # Source journal — its GRAPH-SOURCED modules ingest from the journal, not the file
    journal_only_repos: Optional[List[str]] = None,  # Repo dirs whose packages are NOT scanned, but whose graph-sourced .py keys still ingest from the journal
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:  # (CodeModule/CodeSymbol nodes, edges)
    """Decompose each repo's importable package into code nodes + edges.

    Each repo's durable conceptual key anchors its modules' `ABOUT` edges to the
    repo Entity the repo map already minted (so code joins the decision/note
    neighborhood). All repos decompose into ONE corpus build, so cross-repo
    IMPORTS/CALLS resolve (e.g. the decomposer importing the schema lib). The
    importable package dir is `repo/<repo_name_with_underscores>`; a repo without
    that package is skipped. Code is a SOURCE (projected from disk), so it rebuilds
    on every `rm db && ingest` — it is not journaled. EXCEPT: a module past the N+3
    Phase-2 cutover is GRAPH-SOURCED — its text comes from the SOURCE journal (the
    authority flip, the code analogue of `skip_memory_paths`); its file is a
    generated committed artifact this ingest deliberately does not read.

    `journal_only_repos` (the golden-reference walk's MIXED window): a notebook
    repo's package dir is nbdev-export residue for its UNflipped notebooks, so it
    must not be scanned — but each module already flipped notebook->py IS a
    graph-sourced `.py` key that ingests here, from the journal alone."""
    flipped, journaled = _sourcing_state(source_journal_path)
    decomposed = []
    seen = set()
    repo_dirs_by_key = {}
    for repo_dir in journal_only_repos or []:
        d = Path(repo_dir)
        repo_dirs_by_key[conceptual_key(d.name)] = d
    for repo_dir in code_repos:
        d = Path(repo_dir)
        pkg = d / d.name.replace("-", "_")
        if not pkg.is_dir():
            continue
        repo_key = conceptual_key(d.name)
        repo_dirs_by_key[repo_key] = d
        for dm in decompose_package(repo_key, str(pkg), repo_root=str(d)):
            key = (repo_key, dm.module.module_path)
            if key in flipped and key in journaled:
                a = journaled[key]
                dm = decompose_text(repo_key, dm.module.module_path, dm.module.path,
                                    a.get("text", ""), import_name=a.get("import_name"))
            seen.add(key)
            decomposed.append(dm)
    # A graph-sourced module ingests even when its artifact file is absent — the
    # journal is sufficient (the file is regenerable via `emit-artifact`). Test
    # modules also live in the journal but ingest via `test_elements`, not here.
    for key in sorted(flipped - seen):
        repo_key, module_path = key
        if (key not in journaled or repo_key not in repo_dirs_by_key
                or is_test_module_path(module_path) or not module_path.endswith(".py")):
            continue
        a = journaled[key]
        path = str(repo_dirs_by_key[repo_key] / module_path)
        decomposed.append(decompose_text(repo_key, module_path, path, a.get("text", ""),
                                         import_name=a.get("import_name")))
    return code_corpus_elements(decomposed)


def test_elements(
    code_repos: List[str],  # Repo dirs whose tests/ + tests_manual/ decompose as test modules
    source_journal_path: Optional[str] = None,  # Source journal — its GRAPH-SOURCED test modules ingest from the journal, not the file
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:  # (test CodeModule/CodeSymbol nodes, edges)
    """Decompose each repo's pytest / manual test files into code nodes + edges.

    Tests live OUTSIDE the importable package (`tests/`, `tests_manual/` — no
    `__init__.py`), so module identity is the repo-relative path; nothing imports
    a test module, so the derived import name is inert in the corpus import maps.
    `.md` scenario files under tests_manual/ are deferred (not code). A test module
    past the N+3 Phase-2 cutover is GRAPH-SOURCED — its text comes from the SOURCE
    journal (same authority flip as `code_elements`), under the VERBATIM-import
    canonicalization (`is_test_module_path`)."""
    flipped, journaled = _sourcing_state(source_journal_path)
    decomposed = []
    seen = set()
    repo_dirs_by_key = {}
    for repo_dir in code_repos:
        d = Path(repo_dir)
        repo_key = conceptual_key(d.name)
        repo_dirs_by_key[repo_key] = d
        for sub in ("tests", "tests_manual"):
            t = d / sub
            if not t.is_dir():
                continue
            for f in sorted(t.rglob("*.py")):
                rel = f.relative_to(d).as_posix()
                key = (repo_key, rel)
                seen.add(key)
                try:
                    if key in flipped and key in journaled:
                        decomposed.append(decompose_text(repo_key, rel, str(f),
                                                         journaled[key].get("text", "")))
                    else:
                        decomposed.append(decompose_text(repo_key, rel, str(f), f.read_text()))
                except (SyntaxError, OSError):
                    continue  # unparseable/unreadable test file — skip (ingest stays robust)
    # A graph-sourced test module ingests even when its artifact file is absent —
    # the journal is sufficient (the file is regenerable via `emit-artifact`).
    for key in sorted(flipped - seen):
        repo_key, module_path = key
        if (key not in journaled or repo_key not in repo_dirs_by_key
                or not is_test_module_path(module_path)):
            continue
        d = repo_dirs_by_key[repo_key]
        decomposed.append(decompose_text(repo_key, module_path, str(d / module_path),
                                         journaled[key].get("text", "")))
    return code_corpus_elements(decomposed)


def resolve_test_edges(
    nodes: List[Dict[str, Any]],  # All assembled node wire dicts (code + notebook + tests)
) -> List[Dict[str, Any]]:  # TESTS edges (test symbol / test cell -> exercised package symbol)
    """Resolve TESTS edges across the corpus (the code<->test link).

    Sources: (a) symbols DEFINED in a test module — their calls + refs; (b) non-export
    notebook code cells — their harvested `calls` (nbdev's in-notebook test vehicle).
    Targets: NON-test CodeSymbols, resolved by UNAMBIGUOUS bare name over that
    restricted set (precision over recall, same rule as the corpus CALLS pass).
    Known v1 gap: a manual script's MODULE-LEVEL calls live in CodeText regions, not
    symbols, so they contribute no edges. Additive + idempotent (deterministic ids)."""
    modules = {n["id"]: n for n in nodes if n.get("label") == DevNodeKinds.CODE_MODULE}
    test_module_ids = {mid for mid, m in modules.items()
                       if is_test_module_path(m["properties"].get("module_path", ""))}
    symbols = [n for n in nodes if n.get("label") == DevNodeKinds.CODE_SYMBOL]
    name_to_ids: Dict[str, set] = {}
    for s in symbols:
        if s["properties"].get("module_id") in test_module_ids:
            continue  # targets are package symbols only
        bare = s["properties"].get("qualname", "").split(".")[-1]
        if bare:
            name_to_ids.setdefault(bare, set()).add(s["id"])
    target_map = {n: next(iter(ids)) for n, ids in name_to_ids.items() if len(ids) == 1}

    edges: List[Dict[str, Any]] = []
    for s in symbols:
        if s["properties"].get("module_id") not in test_module_ids:
            continue
        names = list(s["properties"].get("calls", [])) + list(s["properties"].get("refs", []))
        for name in dict.fromkeys(names):
            t = target_map.get(name)
            if t and t != s["id"]:
                edges.append(make_edge(s["id"], t, DevRelations.TESTS))
    for c in nodes:
        if c.get("label") != DevNodeKinds.CELL:
            continue
        for name in c["properties"].get("calls", []):
            t = target_map.get(name)
            if t:
                edges.append(make_edge(c["id"], t, DevRelations.TESTS))
    return edges


def _decompose_notebook_text(
    repo_key: str,   # The repo's durable conceptual slug
    path: str,       # The notebook's file path (provenance locator)
    repo_root: str,  # Repo root (for the fallback module path)
    package: str,    # Importable package name (for export-target module paths)
    text: str,       # The notebook source text (from the source journal)
):  # The DecomposedNotebook
    """Decompose a notebook from journaled TEXT — the graph-sourced ingest leg
    (the notebook analogue of `decompose_text` over a journaled `.py` state)."""
    parsed = parse_notebook(text)
    module_path = module_path_for_notebook(path, repo_root, parsed.default_exp, package)
    import_name = module_path[:-3].replace("/", ".") if module_path.endswith(".py") else None
    return decompose_notebook(repo_key, parsed, module_path, path,
                              SourceRef.compute_hash(text.encode("utf-8")),
                              import_name=import_name)


def notebook_elements(
    notebook_repos: List[str],  # Repo dirs whose nbdev notebooks are decomposed (the SOURCE for nbdev libs)
    source_journal_path: Optional[str] = None,  # Source journal — its GRAPH-SOURCED notebooks ingest from the journal, not the file
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:  # (CodeModule/Cell/CodeSymbol nodes, edges)
    """Decompose each repo's nbdev notebooks into code/cell nodes + edges.

    For an nbdev lib the NOTEBOOK is the source (the generated `.py` is a projection),
    so ingest the notebook here rather than the `.py` via `code_elements` — they share
    one module identity (`pkg/mod.py`), reinforcing graph-as-source-of-truth. Only the
    `nbs/` tree is scanned when the repo has one (quarto's `_proc` copies and `dist/`
    duplicates are residue, not source); a repo without `nbs/` falls back to a full
    scan (checkpoints skipped; unreadable notebooks skipped). Notebooks are a SOURCE
    rebuilt on every `ingest` — EXCEPT one past the N+3 Phase-2 cutover, whose text
    comes from the SOURCE journal keyed by its repo-relative `.ipynb` path (the same
    authority flip as `code_elements`; its file is a generated committed artifact)."""
    flipped, journaled = _sourcing_state(source_journal_path)
    decomposed = []
    seen = set()
    repo_dirs_by_key = {}
    for repo_dir in notebook_repos:
        d = Path(repo_dir)
        package = d.name.replace("-", "_")
        key = conceptual_key(d.name)
        repo_dirs_by_key[key] = d
        scan_root = d / "nbs" if (d / "nbs").is_dir() else d
        for nb in sorted(scan_root.rglob("*.ipynb")):
            if ".ipynb_checkpoints" in nb.parts:
                continue
            k = (key, nb.relative_to(d).as_posix())
            seen.add(k)
            try:
                if k in flipped and k in journaled:
                    decomposed.append(_decompose_notebook_text(
                        key, str(nb), str(d), package, journaled[k].get("text", "")))
                else:
                    decomposed.append(decompose_notebook_file(key, str(nb), str(d), package=package))
            except (ValueError, OSError):
                continue  # malformed/unreadable notebook — skip (batch ingest stays robust)
    # A graph-sourced notebook ingests even when its artifact file is absent — the
    # journal is sufficient (the file is regenerable via `emit-artifact`).
    for key, rel in sorted(flipped - seen):
        k = (key, rel)
        if not rel.endswith(".ipynb") or k not in journaled or key not in repo_dirs_by_key:
            continue
        d = repo_dirs_by_key[key]
        decomposed.append(_decompose_notebook_text(key, str(d / rel), str(d),
                                                   d.name.replace("-", "_"),
                                                   journaled[k].get("text", "")))
    return notebook_graph_elements(decomposed)


def resolve_corpus_code_edges(
    nodes: List[Dict[str, Any]],  # All assembled node wire dicts (code + notebook + the rest)
) -> List[Dict[str, Any]]:  # Additional CALLS/IMPORTS edges resolved across the WHOLE corpus
    """Resolve CALLS/IMPORTS edges ACROSS the whole code + notebook corpus.

    Per-source decomposition only resolves edges within one package / notebook; this
    corpus pass builds global module + symbol maps over EVERY CodeModule/CodeSymbol
    (from code packages AND notebooks) so a call/import crossing a notebook->.py or
    notebook->notebook or lib->lib boundary also lands. Additive + idempotent
    (deterministic edge ids): it unions with the within-source edges, never removes
    them. Resolution is best-effort: imports via `resolve_import` (relative against the
    importer's import name), calls by UNAMBIGUOUS bare name (precision over recall)."""
    modules = [n for n in nodes if n.get("label") == DevNodeKinds.CODE_MODULE]
    symbols = [n for n in nodes if n.get("label") == DevNodeKinds.CODE_SYMBOL]
    import_map: Dict[str, str] = {}
    for m in modules:
        inm = m["properties"].get("import_name")
        if inm:
            import_map.setdefault(inm, m["id"])
    name_to_ids: Dict[str, set] = {}
    for s in symbols:
        bare = s["properties"].get("qualname", "").split(".")[-1]
        if bare:
            name_to_ids.setdefault(bare, set()).add(s["id"])
    call_map = {n: next(iter(ids)) for n, ids in name_to_ids.items() if len(ids) == 1}

    edges: List[Dict[str, Any]] = []
    for m in modules:
        p = m["properties"]
        is_pkg = str(p.get("module_path", "")).endswith("__init__.py")
        inm = p.get("import_name", "") or ""
        for raw in p.get("imports", []):
            target = resolve_import(raw, inm, is_pkg)
            if target and target in import_map and import_map[target] != m["id"]:
                edges.append(make_edge(m["id"], import_map[target], DevRelations.IMPORTS))
    for s in symbols:
        for c in s["properties"].get("calls", []):
            t = call_map.get(c)
            if t and t != s["id"]:
                edges.append(make_edge(s["id"], t, DevRelations.CALLS))
    return edges


def build_dev_graph_elements(
    memory_dir: str,                  # Dir of memory markdown files
    repos_dir: Optional[str] = None,  # Active cjm-* repos dir (None = skip the repo map)
    seed: bool = True,                # Include the hand-seeded fine-tier slots
    note_aliases: Optional[Dict[str, str]] = None,  # Confirmed link aliases (drifted -> canonical)
    code_repos: Optional[List[str]] = None,  # Repo dirs to decompose as code (None = skip code)
    notebook_repos: Optional[List[str]] = None,  # Repo dirs whose nbdev notebooks to decompose (None = skip)
    skip_memory_paths: Optional[List[str]] = None,  # Memory `.md` paths NOT to read (journal-sourced under M3)
    source_journal_path: Optional[str] = None,  # Source journal — its graph-sourced modules ingest from the journal (N+3 Phase 2)
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:  # (all nodes, all edges)
    """Assemble the full dev graph: memory notes (+ refs), the repo map (+ deps),
    the hand-seeded fine-tier slots (the torch/hf contradiction, the stale version
    slot, the class subjects), and — when `code_repos` is given — the decomposed
    code of those repos (CodeModule/CodeSymbol nodes co-residing with the notes).

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
    if code_repos or notebook_repos:
        # Notebook repos ride as journal-only: their flipped (notebook->py, golden-reference
        # walk) modules ingest from the journal without scanning the nbdev-export residue.
        cn, ce = code_elements(code_repos or [], source_journal_path=source_journal_path,
                               journal_only_repos=notebook_repos)
        nodes += cn
        edges += ce
    if notebook_repos:
        nn, ne = notebook_elements(notebook_repos, source_journal_path=source_journal_path)
        nodes += nn
        edges += ne
    if code_repos or notebook_repos:
        tn, te = test_elements((code_repos or []) + (notebook_repos or []),
                               source_journal_path=source_journal_path)
        nodes += tn
        edges += te
        edges += resolve_corpus_code_edges(nodes)  # cross-source CALLS/IMPORTS (additive, idempotent)
        edges += resolve_test_edges(nodes)         # test symbol/cell -> exercised symbol (TESTS)
    return nodes, edges


def _sourcing_state(
    source_journal_path: Optional[str],  # Source-journal path (None = nothing is graph-sourced)
) -> Tuple[set, Dict[Tuple[str, str], Dict[str, Any]]]:  # (flipped keys, latest ops), CONCEPTUAL-key space
    """Load the journal's graph-sourced state with repo keys NORMALIZED to conceptual keys.

    The source journal records repo_key as the repo DIR name (source_state resolves
    files and journal keys as repos_dir/<repo_key>/...), while ingest keys every node
    by `conceptual_key`. For a RENAMED repo (RENAME_ALIASES) the two diverge, and an
    unnormalized comparison silently drops its graph-sourced modules from the
    projection while its tests fall back to file text (finding c89519cd: torch-utils
    device/memory/oom + hf-utils cache_config/download/loading vanished on rebuild)."""
    if not source_journal_path:
        return set(), {}
    flipped = {(conceptual_key(rk), mp)
               for rk, mp in graph_sourced_modules(source_journal_path)}
    journaled = {(conceptual_key(rk), mp): a
                 for (rk, mp), a in latest_source_ops(source_journal_path).items()}
    return flipped, journaled
