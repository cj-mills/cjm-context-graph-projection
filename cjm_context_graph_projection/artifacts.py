"""Observed-source ARTIFACTS (design 9a7224a7, work item 4765b699): files the graph VERSIONS
without owning their text. A design system's tokens.json is the first kind -- the file stays
the authoring surface (hand edits, the CSS importer, gallery tuning) and the graph records
each version it is told to capture.

Two source-journal record kinds sit beside the code lane's, landing through the journal's one
append choke point: `artifact` (args repo_key, artifact_path, artifact_kind, text -- the
CANONICAL text, so a formatting-only edit is no new version) and `artifact-retire` (ends a
key's life; retire is a fact). Every consumer of the code lane branches on source / retire /
cutover / register and ignores these, and the args name `artifact_path`, never
`module_path`, so nothing mistakes an artifact for a CodeModule.

The FOLD derives one node per live artifact, keyed by what the artifact IS (a design system
by its slug), never by where its file sits -- a move between repos re-keys nothing. Its
`created_at` is the start of its continuous existence, its `updated_at` the last record that
changed what it derives. Drift (a file whose canonical text is not its latest capture) is
REPORTED, never a regen-gate refusal: the file is the source and nothing regenerates it."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from cjm_context_graph_layer.grammar import make_edge
from cjm_context_graph_layer.ops import graph_task
from cjm_context_graph_primitives.journal import op_now
from cjm_context_graph_primitives.query import EdgeQuery, PropertyPredicate
from cjm_design_system import tokens as T
from cjm_dev_graph_schema.identity import entity_node_id
from cjm_dev_graph_schema.nodes import EntityNode
from cjm_dev_graph_schema.predicates import ENTITY_DESIGN_SYSTEM
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from . import factlayer as F
from .runtime import GraphHandle
from .seeds import conceptual_key, repo_dir_name
from .source_state import _append_record, journal_repos, read_source_journal

ARTIFACT = "artifact"                 # The capture record: one version of an observed file
ARTIFACT_RETIRE = "artifact-retire"   # Ends a key's life (the file is gone, or no longer an artifact)
ARTIFACT_VERBS = (ARTIFACT, ARTIFACT_RETIRE)


@dataclass(frozen=True)
class ArtifactKind:
    """One kind of observed artifact: where its files sit, how a file reads canonically (and
    is refused when malformed), and the node a captured version derives."""
    name: str                                       # The kind's slug (the record's artifact_kind)
    entity_kind: str                                # The Entity sub-kind its node carries
    pattern: str                                    # Glob under a repo root the uncaptured audit walks
    canonical: Callable[[str, str], str]            # (file text, source label) -> canonical text; raises when malformed
    entity: Callable[[str, str, str], EntityNode]   # (canonical text, repo_key, artifact_path) -> its Entity


def _design_system_canonical(
    text: str,    # A tokens.json file's text
    source: str,  # Where it came from (named in a refusal)
) -> str:  # The canonical JSON text (2-space indent, key order kept, trailing newline)
    """Schema v1 checked (every problem named in one refusal), then re-serialized, so two
    files that state the same system read as the same version."""
    tok = json.loads(text)
    T.check(tok, source)
    return json.dumps(tok, indent=2, ensure_ascii=False) + "\n"


def _design_system_entity(
    text: str,           # The captured canonical tokens text
    repo_key: str,       # The repo the file sits in
    artifact_path: str,  # Its repo-relative path
) -> EntityNode:  # The design_system Entity this version derives
    """Identity = the system's slug; the record = what the captured version states, the
    locator where its source lives, and the text itself (site-build renders from the CAPTURED
    text the binding reaches, never from whatever the file holds)."""
    tok = json.loads(text)
    return EntityNode(kind=ENTITY_DESIGN_SYSTEM, key=T.slug(tok), name=str(tok["name"]),
                      properties={"modes": T.modes(tok), "scheme": tok.get("scheme") or {},
                                  "schema": tok.get("schema"), "repo_key": repo_key,
                                  "artifact_path": artifact_path, "tokens": text,
                                  "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest()})


ARTIFACT_KINDS: Dict[str, ArtifactKind] = {
    "design-system": ArtifactKind("design-system", ENTITY_DESIGN_SYSTEM, "**/systems/*/tokens.json",
                                  _design_system_canonical, _design_system_entity),
}


def append_artifact(
    path: str,           # Source-journal file path (JSONL)
    repo_key: str,       # The repo's durable conceptual slug
    artifact_path: str,  # Repo-relative path of the observed file
    artifact_kind: str,  # An ARTIFACT_KINDS key
    text: str,           # The CANONICAL text (the journaled state)
    op_meta: Optional[Dict[str, Any]] = None,  # Replay-ignored op provenance ({'op': 'capture-artifact'})
) -> bool:  # True if appended, False if identical to the key's latest capture (no-op)
    """Append an `artifact` record, skipping a capture identical to the key's latest state."""
    cur = latest_artifact_ops(path).get((repo_key, artifact_path))
    if cur is not None and cur.get("text") == text and cur.get("artifact_kind") == artifact_kind:
        return False
    record: Dict[str, Any] = {"verb": ARTIFACT, "ts": op_now(), "generation": 1,
                              "args": {"repo_key": repo_key, "artifact_path": artifact_path,
                                       "artifact_kind": artifact_kind, "text": text}}
    if op_meta:
        record["op"] = op_meta
    _append_record(path, record)
    return True


def append_artifact_retire(
    path: str,           # Source-journal file path (JSONL)
    repo_key: str,       # The repo's durable conceptual slug
    artifact_path: str,  # The key to end
    op_meta: Optional[Dict[str, Any]] = None,  # Replay-ignored op provenance
) -> bool:  # True if appended, False if the key is not live (no-op)
    """Append an `artifact-retire` record ending a key's life."""
    if (repo_key, artifact_path) not in latest_artifact_ops(path):
        return False
    record: Dict[str, Any] = {"verb": ARTIFACT_RETIRE, "ts": op_now(), "generation": 1,
                              "args": {"repo_key": repo_key, "artifact_path": artifact_path}}
    if op_meta:
        record["op"] = op_meta
    _append_record(path, record)
    return True


def latest_artifact_ops(
    path: str,  # Source-journal file path (JSONL)
    records: Optional[List[Dict[str, Any]]] = None,  # Already-read records (None = read the journal)
) -> Dict[Tuple[str, str], Dict[str, Any]]:  # (repo_key, artifact_path) -> its latest capture's args
    """The LATEST capture per live key (last record wins; a retire ends the key)."""
    latest: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for rec in records if records is not None else read_source_journal(path):
        verb = rec.get("verb")
        if verb not in ARTIFACT_VERBS:
            continue
        a = rec.get("args") or {}
        key = (a.get("repo_key"), a.get("artifact_path"))
        if verb == ARTIFACT:
            latest[key] = a
        else:
            latest.pop(key, None)
    return latest


class ArtifactFold:
    """The artifact lane folded over the source journal: one node per live artifact identity,
    its times from the records -- the same fold for a rebuild and the live step."""

    def __init__(self) -> None:
        self.keys: Dict[Tuple[str, str], str] = {}          # live key -> the node id it derives
        self.key_ts: Dict[Tuple[str, str], float] = {}      # live key -> its latest capture's ts
        self.held: Dict[str, Dict[str, Any]] = {}           # node id -> {wire, created_at, updated_at, keys}
        self.records = 0
        self.failures: List[Dict[str, Any]] = []

    def apply(
        self,
        rec: Dict[str, Any],  # One source-journal record (anything but an artifact record is ignored)
    ) -> None:
        verb = rec.get("verb")
        if verb not in ARTIFACT_VERBS:
            return
        self.records += 1
        a = rec.get("args") or {}
        key, ts = (a.get("repo_key"), a.get("artifact_path")), rec.get("ts")
        wire = None
        if verb == ARTIFACT:
            kind = ARTIFACT_KINDS.get(a.get("artifact_kind"))
            try:
                if kind is None:
                    raise ValueError(f"unknown artifact kind {a.get('artifact_kind')!r}")
                wire = kind.entity(a["text"], key[0], key[1]).to_graph_node()
            except Exception as e:   # a record this code cannot read: reported, never guessed
                self.failures.append({"repo_key": key[0], "artifact_path": key[1], "ts": ts,
                                      "error": str(e)})
        if verb == ARTIFACT and wire is None:
            return                         # an unreadable record: its previous state stands
        prev = self.keys.get(key)
        if prev is not None and (wire is None or wire["id"] != prev):
            # the key leaves its identity (retired, or now deriving another one)
            del self.keys[key], self.key_ts[key]
            h = self.held[prev]
            h["keys"].discard(key)
            if not h["keys"]:              # its continuous existence ends
                del self.held[prev]
        if wire is None:
            return
        nid = wire["id"]
        h = self.held.get(nid)
        if h is None:
            self.held[nid] = {"wire": wire, "created_at": ts, "updated_at": ts, "keys": {key}}
        else:
            h["keys"].add(key)
            if h["wire"] != wire:
                h["wire"], h["updated_at"] = wire, ts
        self.keys[key], self.key_ts[key] = nid, ts

    def conflicts(self) -> List[Dict[str, Any]]:  # Identities two live files derive at once
        """Two live files deriving one identity (a copy of a system under a second path):
        the latest capture's file holds the node, the conflict is reported."""
        return [{"id": nid, "keys": sorted(f"{k[0]}/{k[1]}" for k in h["keys"])}
                for nid, h in sorted(self.held.items()) if len(h["keys"]) > 1]

    def elements(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:  # (node wires, edge wires), times carried
        """Each live identity's node, and its ABOUT edge to the repo Entity its latest
        capture's file sits in (the store skips it until that Entity exists)."""
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        for nid, h in sorted(self.held.items()):
            nodes.append({**h["wire"], "created_at": h["created_at"], "updated_at": h["updated_at"]})
            key = max(h["keys"], key=lambda k: self.key_ts[k])
            e = make_edge(nid, entity_node_id("repo", conceptual_key(key[0])), DevRelations.ABOUT)
            edges.append({**e, "created_at": self.key_ts[key], "updated_at": self.key_ts[key]})
        return nodes, edges


def fold_artifacts(
    records: List[Dict[str, Any]],  # The source journal's records, in append order
) -> ArtifactFold:  # The folded artifact lane
    fold = ArtifactFold()
    for rec in records:
        fold.apply(rec)
    return fold


def _entity_kinds() -> List[str]:  # Every Entity sub-kind an artifact derives
    return sorted({k.entity_kind for k in ARTIFACT_KINDS.values()})


async def apply_artifacts_live(
    gx: GraphHandle,
    records: List[Dict[str, Any]],  # The whole source journal, the verb's records included
) -> Dict[str, Any]:  # Receipt: {records, nodes_added, nodes_updated, nodes_removed, edges_added, edges_removed, edges_skipped, conflicts, failures}
    """THE SAME FOLD a rebuild runs, committed in place: artifact nodes the fold no longer
    holds are deleted, new and changed ones written (created_at / updated_at from the
    records), and each node's ABOUT edge reconciled to the file its latest capture names."""
    fold = fold_artifacts(records)
    nodes, edges = fold.elements()
    held = {}
    for kind in _entity_kinds():
        for n in await F.load_label_where(gx, DevNodeKinds.ENTITY,
                                          [PropertyPredicate("entity_kind", "eq", kind)]):
            w = n.to_dict() if hasattr(n, "to_dict") else dict(n)
            held[w["id"]] = w
    want = {n["id"]: n for n in nodes}
    removed = sorted(set(held) - set(want))
    write = [n for nid, n in want.items()
             if nid not in held or held[nid].get("properties") != n["properties"]
             or held[nid].get("updated_at") != n["updated_at"]]
    have_edges: Dict[str, Dict[str, Any]] = {}
    if held:
        res = await graph_task(gx.queue, gx.graph_id, "query_edges",
                               query=EdgeQuery(source_ids=sorted(held)).to_dict())
        for e in (getattr(res, "edges", None) or (res.get("edges") if isinstance(res, dict) else None) or []):
            w = e.to_dict() if hasattr(e, "to_dict") else dict(e)
            if w["relation_type"] == DevRelations.ABOUT:
                have_edges[w["id"]] = w
    gone = sorted(eid for eid in have_edges if eid not in {e["id"] for e in edges})
    add = [e for e in edges if e["id"] not in have_edges]
    receipt: Dict[str, Any] = {"records": fold.records,
                               "nodes_added": sum(1 for n in write if n["id"] not in held),
                               "nodes_updated": sum(1 for n in write if n["id"] in held),
                               "nodes_removed": len(removed), "edges_added": len(add),
                               "edges_removed": len(gone), "edges_skipped": 0,
                               "conflicts": fold.conflicts(), "failures": fold.failures}
    if gone:
        await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=gone)
    if removed:
        await graph_task(gx.queue, gx.graph_id, "delete_nodes", node_ids=removed, cascade=True)
    if write or add:
        res = await graph_task(gx.queue, gx.graph_id, "import_graph",
                               graph_data={"nodes": write, "edges": add, "metadata": {}},
                               merge_strategy="overwrite")
        receipt["edges_skipped"] = len(add) - int((res or {}).get("edges_created", 0))
    return receipt


async def capture_artifact(
    gx: GraphHandle,
    repo_key: str,       # The repo the file sits in (its directory under the repos root)
    artifact_path: str,  # The file's repo-relative path
    kind: str = "design-system",  # An ARTIFACT_KINDS key
    *,
    retire: bool = False,  # End the key's life instead of capturing a version
    write: bool = True,    # False = dry run: validate and report, journal nothing
    source_journal_path: str,  # The source journal
    repos_dir: str,            # The repos root
) -> Dict[str, Any]:  # {repo_key, artifact_path, kind, captured | retired | unchanged, entity, live} or {error}
    """Capture the file's current version (validated, canonical) or retire its key, then the
    live step. A malformed file is refused before anything is journaled."""
    rep: Dict[str, Any] = {"repo_key": repo_key, "artifact_path": artifact_path, "kind": kind}
    ak = ARTIFACT_KINDS.get(kind)
    if ak is None:
        return {**rep, "error": f"unknown artifact kind {kind!r} -- known: {sorted(ARTIFACT_KINDS)}"}
    meta = {"op": "capture-artifact"}
    if retire:
        if (repo_key, artifact_path) not in latest_artifact_ops(source_journal_path):
            return {**rep, "error": "not a live artifact key -- nothing to retire"}
        if not write:
            return {**rep, "preview": "would retire"}
        append_artifact_retire(source_journal_path, repo_key, artifact_path, op_meta=meta)
        rep["retired"] = True
    else:
        fp = Path(repos_dir) / repo_key / artifact_path
        if not fp.is_file():
            return {**rep, "error": f"no file at {fp}"}
        try:
            text = ak.canonical(fp.read_text(encoding="utf-8"), str(fp))
        except Exception as e:   # the kind's own refusal (a schema check names every problem)
            return {**rep, "error": str(e)}
        ent = ak.entity(text, repo_key, artifact_path)
        rep["entity"] = {"id": ent.id, "kind": ent.kind, "key": ent.key, "name": ent.name}
        if not write:
            return {**rep, "preview": "would capture"}
        appended = append_artifact(source_journal_path, repo_key, artifact_path, kind, text, op_meta=meta)
        rep["captured" if appended else "unchanged"] = True
    rep["artifacts_live"] = await apply_artifacts_live(gx, read_source_journal(source_journal_path))
    return rep


def artifact_check(
    source_journal_path: str,  # The source journal
    repos_dir: str,            # The repos root
    records: Optional[List[Dict[str, Any]]] = None,  # Already-read records (None = read the journal)
) -> Dict[str, Any]:  # {count, drift: [...], missing: [...], invalid: [...], clean}
    """Every live artifact against its file: DRIFT = the file's canonical text is not the
    latest capture (an uncaptured edit), MISSING = the file is gone, INVALID = the file no
    longer reads as its kind. Reported, never a gate: the file is the source."""
    rep: Dict[str, Any] = {"count": 0, "drift": [], "missing": [], "invalid": []}
    for (repo_key, apath), a in sorted(latest_artifact_ops(source_journal_path, records).items()):
        rep["count"] += 1
        label = f"{repo_key}/{apath}"
        fp = Path(repos_dir) / repo_key / apath
        ak = ARTIFACT_KINDS.get(a.get("artifact_kind"))
        if not fp.is_file():
            rep["missing"].append(label)
            continue
        try:
            text = ak.canonical(fp.read_text(encoding="utf-8"), str(fp)) if ak else None
        except Exception as e:
            rep["invalid"].append({"artifact": label, "error": str(e)})
            continue
        if text != a.get("text"):
            rep["drift"].append(label)
    rep["clean"] = not (rep["drift"] or rep["missing"] or rep["invalid"])
    return rep


def uncaptured_artifacts(
    source_journal_path: str,  # The source journal
    repos_dir: str,            # The repos root
    repos: Optional[List[str]] = None,  # Repo keys to walk (None = every repo the journal holds)
    records: Optional[List[Dict[str, Any]]] = None,  # Already-read records (None = read the journal)
) -> Dict[str, List[str]]:  # repo_key -> [kind: path] of artifact files no live capture holds
    """Every file a kind's pattern matches, in every on-graph repo, that no record holds."""
    live = set(latest_artifact_ops(source_journal_path, records))
    out: Dict[str, List[str]] = {}
    for repo_key in repos if repos is not None else journal_repos(source_journal_path):
        root = Path(repos_dir) / repo_dir_name(repo_key)
        for ak in ARTIFACT_KINDS.values():
            for fp in sorted(root.glob(ak.pattern)):
                rel = fp.relative_to(root).as_posix()
                if (repo_dir_name(repo_key), rel) not in live and "/.git/" not in f"/{rel}":
                    out.setdefault(repo_key, []).append(f"{ak.name}: {rel}")
    return out
