"""Retiring an archive source, restoring it from git, and moving a page's path between holders
(design amendment e916a4b9 under the Tutorials page design 7f200ecb; RETIRE IS A FACT a7617bd4).

An archive Source is ingested from the website clone. Retiring it is a journaled FACT: its
publish_state becomes `retired` (the terminal value of the ratified vocabulary) and the op
records where its source lived -- the path under the website clone and the commit that last
held it. Its file may then leave the tree: the notes ingest reads the journal's retire ops
BEFORE replay and rebuilds each retired node from `git show <commit>:<path>`, byte-for-byte
under its original path and slug, so every journaled op that names it keeps resolving and a
rebuild stays id-identical. Projections filter retired nodes (`is_retired`).

A page's path moves between holders through `transfer_site_path`: the new holder asserts the
old holder's active site_path, and the new assertion SUPERSEDES the old one ACROSS slots
(`resolve_active` treats any superseded assertion as inactive). A page MERGED into another (a
retired page whose reader belongs on a page that exists, design ce17606b (6)) moves the same way,
except that the target keeps its own active path and that assertion supersedes the old one, so
the merged page's URL redirects to the target's. Nothing is copied:
`path_owners` derives, for every site_path assertion, the page whose ACTIVE path its
supersession chain ends at -- so the redirect projection and the link resolver count the old
holder's earlier paths as the new holder's history."""

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cjm_context_graph_layer.grammar import make_edge, SpineRelations
from cjm_context_graph_layer.ops import extend_graph
from cjm_context_graph_primitives.query import PropertyPredicate
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations
from cjm_markdown_decompose_core.project import compose_note_text

from . import factlayer as F
from .devgraph import ArchiveSource
from .gitfold import blobs_at
from .runtime import GraphHandle

RETIRE_VERB = "retire-source"     # The journaled retire op (one per retired archive source)
TRANSFER_VERB = "transfer-path"   # The journaled path transfer (one per moved page path)


def is_retired(
    note_id: str,                      # A Note id
    states: Dict[str, List[str]],      # note_publish_states' map
) -> bool:
    """A retired node is never listed, linked to or rendered, under any profile."""
    return P.PUBLISH_RETIRED in (states.get(note_id) or [])


def git_blob(
    repo_root: str,  # The git work tree the source lived in
    commit: str,     # The commit that held it
    rel_path: str,   # Its path under the work tree
) -> Optional[bytes]:  # The file's bytes at that commit, or None
    r = subprocess.run(["git", "-C", repo_root, "show", f"{commit}:{rel_path}"],
                       capture_output=True)
    return r.stdout if r.returncode == 0 else None


def git_head(
    repo_root: str,  # A git work tree
) -> Optional[str]:  # Its HEAD commit, or None
    r = subprocess.run(["git", "-C", repo_root, "rev-parse", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def _source_hashes(
    node: Any,  # A node (dict or object) carrying its ingest provenance
) -> set:  # The content hashes its source refs recorded
    """The ingest records the file's hash on the node's SourceRef, not as a property."""
    srcs = node.get("sources") if isinstance(node, dict) else getattr(node, "sources", None)
    out = set()
    for s in srcs or []:
        h = s.get("content_hash") if isinstance(s, dict) else getattr(s, "content_hash", None)
        if h:
            out.add(h)
    return out


async def retire_source(
    gx: GraphHandle,
    note: str,                            # The archive Note's id (or a unique id prefix)
    *,
    reason: str,                          # Why it retires (the op's record)
    successor: Optional[str] = None,      # The node that takes its place (id), if any
    website_root: Optional[str] = None,   # LIVE: the website clone, where the source is read from git
    commit: Optional[str] = None,         # REPLAY: the journaled commit (skips the git check)
    rel_path: Optional[str] = None,       # REPLAY: the journaled path under the website clone
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {note_id, slug, path, commit, reason, successor_id, written} | {error, written: False}
    """Retire an archive source (journaled `retire-source`). LIVE, the source must be committed
    as ingested: the clone's HEAD holds the file and its bytes hash to the node's content hash,
    so a rebuild restores exactly this node. The fact is `publish_state retired`; a successor
    gets a SUPERSEDES edge onto the retired node."""
    from .purenotes import note_types
    from .write import assert_value
    from .projection import resolve_node_ref
    if not reason:
        return {"error": "a retirement needs its --reason", "written": False}
    res = await resolve_node_ref(gx, note)
    node = res.get("node")
    if node is None or F.label(node) != DevNodeKinds.NOTE:
        return {"error": f"no Note `{note}`" + (" (ambiguous prefix)" if "candidates" in res else ""),
                "written": False}
    nid, slug = str(F.nid(node)), str(F.prop(node, "slug") or "")
    if (await note_types(gx)).get(nid, {}).get("origin") != P.ORIGIN_ARCHIVE:
        return {"error": f"`{slug}` is not an archive source — a born note retires by "
                         "`assert <note> publish_state retired`", "written": False}
    if commit is None:
        if not website_root:
            return {"error": "a live retirement needs the website clone (`website_root`)", "written": False}
        src = Path(str(F.prop(node, "path") or "")).resolve()
        root = Path(website_root).resolve()
        if not src.is_relative_to(root):
            return {"error": f"`{slug}`'s source {src} is not under the website clone {root}", "written": False}
        rel_path = src.relative_to(root).as_posix()
        commit = git_head(str(root))
        blob = git_blob(str(root), commit, rel_path) if commit else None
        if blob is None:
            return {"error": f"the website clone's HEAD holds no {rel_path} — commit it first", "written": False}
        from cjm_context_graph_primitives.provenance import SourceRef
        if SourceRef.compute_hash(blob) not in _source_hashes(node):
            return {"error": f"{rel_path} at HEAD differs from the ingested node — commit the file as "
                             "ingested (or re-ingest) before retiring it, so a rebuild restores this node",
                    "written": False}
    successor_id = None
    if successor:
        sres = await resolve_node_ref(gx, successor)
        if sres.get("node") is None:
            return {"error": f"no successor node `{successor}`", "written": False}
        successor_id = str(F.nid(sres["node"]))
    fact = await assert_value(gx, nid, P.PUBLISH_STATE, P.PUBLISH_RETIRED, actor=actor)
    if fact.get("error"):
        return {"error": fact["error"], "written": False}
    # A successor already standing (the relate verb's SUPERSEDES, cbd5f154 (3)) is not landed twice.
    if successor_id and (successor_id, nid) not in set(await F.load_supersedes(gx)):
        await extend_graph(gx.queue, gx.graph_id, [], [make_edge(successor_id, nid, DevRelations.SUPERSEDES)])
    return {"note_id": nid, "slug": slug, "path": rel_path, "commit": commit, "reason": reason,
            "successor_id": successor_id, "written": True}


def retired_sources(
    journal_path: str,  # The notes graph's write journal
) -> List[Dict[str, Any]]:  # The retire ops' records, the last per note, in journal order
    """What the ingest restores before replay: every archive source a retire op names."""
    from .journal import read_journal
    out: Dict[str, Dict[str, Any]] = {}
    if not journal_path or not Path(journal_path).exists():
        return []
    for op in read_journal(journal_path):
        if op.get("verb") == RETIRE_VERB and (op.get("args") or {}).get("note"):
            out[op["args"]["note"]] = op["args"]
    return list(out.values())


def restore_retired(
    site_root: str,                      # The website clone (a git work tree with its history)
    retired: List[Dict[str, Any]],       # retired_sources' records
    profile: str = "quarto_post",        # The harvest profile the corpus is ingested with
) -> List[Any]:  # The retired sources as NoteNodes, identical to their live ingest
    """Rebuild each retired archive source from git, under its original path and slug. A missing
    blob (a shallow clone, a rewritten history) or an identity that no longer matches refuses
    loudly: the rebuild must never silently lose a node the journal names."""
    from cjm_markdown_decompose_core.extract import note_from_text
    notes = []
    for r in retired:
        blob = git_blob(site_root, r["commit"], r["path"])
        if blob is None:
            raise ValueError(f"retired source {r['path']} is not at commit {r['commit'][:12]} in "
                             f"{site_root} (a shallow clone or rewritten history?) — the rebuild "
                             "cannot restore it")
        note = note_from_text(str(Path(site_root) / r["path"]), blob.decode("utf-8"),
                              corpus_root=site_root, profile=profile, lossless=True, slug=r["slug"])
        if note.id != r["note"]:
            raise ValueError(f"retired source {r['path']} restores as {note.id}, not the retired "
                             f"node {r['note']}")
        notes.append(note)
    return notes


def path_owners(
    assertions: List[Any],                 # Every site_path Assertion node
    supers: List[Tuple[str, str]],         # All SUPERSEDES (superseder, superseded) pairs
) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:  # ({assertion id: owning subject id}, ambiguity rows)
    """The page each site_path value now belongs to: follow the supersession chain UP from the
    assertion to an ACTIVE assertion; its subject owns the value. Within one slot that is the
    slot's own subject (a prior path of the same page); across slots it is the holder the path
    was transferred to. A value whose chain reaches two different pages is ambiguous and owned
    by none (the caller reports it, never guesses)."""
    by_id = {str(F.nid(a)): a for a in assertions}
    up: Dict[str, List[str]] = {}
    for s, d in supers:
        if s in by_id and d in by_id and s != d:
            up.setdefault(d, []).append(s)
    memo: Dict[str, Optional[set]] = {}

    def owners(aid: str, seen: frozenset) -> set:
        if aid in memo:
            return memo[aid] or set()
        if aid in seen:
            return set()   # a supersession cycle owns nothing
        above = up.get(aid)
        if not above:
            got = {str(F.prop(by_id[aid], "subject_id") or "")}
        else:
            got = set().union(*(owners(s, seen | {aid}) for s in above))
        memo[aid] = got
        return got

    out: Dict[str, str] = {}
    errors: List[Dict[str, Any]] = []
    for aid in by_id:
        got = owners(aid, frozenset())
        if len(got) == 1:
            out[aid] = next(iter(got))
        elif len(got) > 1:
            errors.append({"kind": "path-owner", "assertion": aid, "value": F.prop(by_id[aid], "value"),
                           "owners": sorted(got),
                           "why": "a site_path value's supersession reaches two pages"})
    return out, errors


async def transfer_site_path(
    gx: GraphHandle,
    source: str,    # The page node losing its path (id or unique prefix)
    target: str,    # The node taking it (id or unique prefix)
    *,
    actor: str = "agent:session",
    merge: bool = False,   # The target keeps its own active path: the source's page merges into it
) -> Dict[str, Any]:  # {from_id, to_id, value, assertion_id, superseded, merged?, written} | {error, written: False}
    """Move a page's ACTIVE site_path to another node (journaled `transfer-path`): the target
    asserts the value, and that assertion SUPERSEDES the source's across slots, so the source
    holds no active path and its whole history reads as the target's (`path_owners`). The
    target must hold no active path of its own -- a page has exactly one -- unless the source's
    page MERGES into the target's (design ce17606b (6)): then the target's one active path
    supersedes the source's, and the source's URL redirects to the target's page."""
    from .projection import resolve_node_ref
    from .write import assert_value
    ids = []
    for ref in (source, target):
        res = await resolve_node_ref(gx, ref)
        if res.get("node") is None:
            return {"error": f"no node `{ref}`" + (" (ambiguous prefix)" if "candidates" in res else ""),
                    "written": False}
        ids.append(str(F.nid(res["node"])))
    src, dst = ids
    if src == dst:
        return {"error": "a path transfers between two different nodes", "written": False}
    slot = await F.load_label_where(gx, DevNodeKinds.ASSERTION,
                                    [PropertyPredicate("predicate", "eq", P.SITE_PATH)])
    supers = await F.load_supersedes(gx)
    active = F.active_assertions(slot, supers)
    have = {sid: [a for a in active if str(F.prop(a, "subject_id")) == sid] for sid in (src, dst)}
    if len(have[src]) != 1:
        return {"error": f"the source holds {len(have[src])} active site_path value(s) — a transfer "
                         "moves exactly one", "written": False}
    if merge and len(have[dst]) != 1:
        return {"error": f"a merge needs the target's one active path; it holds {len(have[dst])}",
                "written": False}
    if have[dst] and not merge:
        return {"error": f"the target already holds {F.prop(have[dst][0], 'value')} — a page has one "
                         "active path", "written": False}
    old = have[src][0]
    value = str(F.prop(old, "value"))
    if merge:
        keep = have[dst][0]
        await extend_graph(gx.queue, gx.graph_id, [],
                           [make_edge(str(F.nid(keep)), str(F.nid(old)), DevRelations.SUPERSEDES)])
        return {"from_id": src, "to_id": dst, "value": value, "assertion_id": str(F.nid(keep)),
                "superseded": str(F.nid(old)), "merged": True, "written": True}
    # The site-link step resolves the moved path once, at the write window's close (9ee4e346).
    res = await assert_value(gx, dst, P.SITE_PATH, value, actor=actor)
    if res.get("error"):
        return {"error": res["error"], "written": False}
    await extend_graph(gx.queue, gx.graph_id, [],
                       [make_edge(res["assertion_id"], str(F.nid(old)), DevRelations.SUPERSEDES)])
    return {"from_id": src, "to_id": dst, "value": value, "assertion_id": res["assertion_id"],
            "superseded": str(F.nid(old)), "written": True}


async def archive_round_trip(
    gx: GraphHandle,
    config: Dict[str, Any],        # The notes graph-sibling config (notes_corpus, website_root, site_pages, journal_path)
    commit: Optional[str] = None,  # The commit whose blobs the compositions are compared with (default: the clone's HEAD)
) -> Dict[str, Any]:  # {commit, kept, equal, differ, missing, extra, errors}
    """THE ROUND-TRIP INVARIANT, the archive cutover's gate (design 56c9c332; work item f86be52f).

    Every kept post -- a post under the corpus root at `commit`, never a retired one, never a
    site page -- is COMPOSED FROM THE GRAPH (its Note's frontmatter, its Sections' own state
    walked by PART_OF + NEXT) and compared byte for byte with its git blob at that commit, never
    the working tree (a checkout's line endings are not the archive's). A post that differs
    reports its first differing line; a kept path with no Note, or an archive Note whose path the
    commit does not keep, is listed apart. Read-only."""
    errors: List[str] = []
    root, top = config.get("notes_corpus"), config.get("website_root")
    if not root or not top:
        return {"errors": ["round-trip needs notes_corpus and website_root in the sibling config"]}
    src = ArchiveSource(root, config.get("notes_profile") or "quarto_post", site_root=top,
                        site_pages=config.get("site_pages"),
                        retired=retired_sources(config.get("journal_path")))
    r = subprocess.run(["git", "-C", src.top, "rev-parse", "--verify", f"{commit or 'HEAD'}^{{commit}}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return {"errors": [f"no commit {commit or 'HEAD'} in {src.top}"]}
    sha = r.stdout.strip()
    held = subprocess.run(["git", "-C", src.top, "ls-tree", "-r", "--name-only", sha],
                          capture_output=True, text=True).stdout.split("\n")
    kept = sorted(p for p in held if p and src.is_post(p) and p not in src.by_retired)
    blobs = blobs_at(src.top, sha, kept)

    by_path: Dict[str, Any] = {}
    for n in await F.load_label(gx, DevNodeKinds.NOTE):
        path = str(F.prop(n, "path") or "")
        try:
            rel = Path(path).resolve().relative_to(Path(src.top).resolve()).as_posix()
        except ValueError:
            continue
        if src.is_post(rel) and rel not in src.by_retired:
            by_path[rel] = n
    sections: Dict[str, List[Any]] = {}
    for s in await F.load_label(gx, DevNodeKinds.SECTION):
        sections.setdefault(str(F.prop(s, "note_id")), []).append(s)
    own = {F.nid(s) for ss in sections.values() for s in ss}
    parent = {a: b for a, b in await F.load_edge_pairs(gx, SpineRelations.PART_OF) if a in own and b in own}
    following = {a: b for a, b in await F.load_edge_pairs(gx, SpineRelations.NEXT) if a in own and b in own}

    equal, differ, missing = 0, [], []
    for rel in kept:
        node = by_path.get(rel)
        if node is None:
            missing.append(rel)
            continue
        blob = blobs[rel].decode("utf-8")
        try:
            composed = compose_note_text(str(F.prop(node, "frontmatter_raw") or ""),
                                         sections.get(F.nid(node), []), parent, following)
        except ValueError as e:
            errors.append(f"{rel}: {e}")
            continue
        if composed == blob:
            equal += 1
            continue
        a, b = blob.split("\n"), composed.split("\n")
        i = next((k for k, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
        differ.append({"path": rel, "slug": F.prop(node, "slug"), "line": i + 1,
                       "blob": a[i] if i < len(a) else None, "composed": b[i] if i < len(b) else None})
    extra = sorted(p for p in by_path if p not in blobs)
    return {"commit": sha, "kept": len(kept), "equal": equal, "differ": differ, "missing": missing,
            "extra": extra, "errors": errors}
