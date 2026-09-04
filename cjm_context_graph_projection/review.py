"""The review frontier: which APPROVED deliverables have stale upstream — derived, never stored.

The general projector of design 40622922 (item 730e077e), the sibling of `readiness`:
approval binds to content (an assertion carries `subject_content_hash`), and staleness
is NEVER written. A deliverable is stale when something upstream of it along its typed
dependency edges (DERIVED_FROM, DEPENDS_ON, REFERENCES, SHAPES for code) changed AFTER
its approval — computed on read here, exactly as ready/blocked are derived. Nothing walks
the chain writing flags, so a missed edge or a rebuild can never leave a wrong flag
behind, and the projector's silence on a rotten edge class is itself an audit signal.

    approval  ≡ an ACTIVE approval-class assertion (`publish_state` >= reviewed today;
                the class is schema data, `predicates.APPROVAL_CLASS`) on a deliverable D,
                stamped `asserted_at` = T and (usually) the content hash it approved
    upstream  ≡ every node reachable from D (and D's own Sections) along the dependency
                relations, to a bounded depth — each carrying the CHAIN PATH from D
    change    ≡ an upstream node whose CONTENT changed after T (journal-verified, revert-
                aware: the live text is compared against the last journaled snapshot at or
                before T), whose GOVERNING ASSERTIONS changed after T, or that became a
                dependency of D after T (a `link` journaled after the approval)
    stale     ≡ D has >= 1 change that is NOT acknowledged

CHANGE CLASSES (the useful-not-noisy bar): `self` (D's own content moved off the approved
hash — the emit-post demotion, re-approval is the only cure), `content` (a fidelity
edit), `cosmetic` (whitespace-only), `structure` (a section/symbol added), `assertion`
(a fact on the upstream node changed), `dependency-added` (a new edge since approval).

ACKNOWLEDGMENT: a reviewer's considered "no update needed" is an assertion on D —
predicate `review_verdict`, value = the change KEY (`<upstream id prefix>@<content hash
prefix>`, or `@assertion:<id>` / `@edge:<relation>` for the non-content classes). It
silences exactly that change: the same upstream changing AGAIN carries a new hash, so
it re-surfaces (the filing lane's 60f21ee1 lesson, mechanized). The triage worklist is
the propose/confirm shape: each stale row prints the ack recipe and the re-approval
recipe; nothing here writes.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from . import factlayer as F
from .display import annotate_display, node_title
from .journal import journal_touch_rows
from .runtime import GraphHandle

# The dependency edge classes the walk follows (design 40622922 (1)). `SHAPES` is the
# decision->code relation `link` mints under rule 6baa030a — a free relation string,
# not a reserved DevRelations member — so the projector follows it by name.
DEPENDENCY_RELATIONS: Tuple[str, ...] = (DevRelations.DERIVED_FROM, DevRelations.DEPENDS_ON,
                                         DevRelations.REFERENCES, "SHAPES")

# Kinds whose content the projector can verify against the journals (the content-bearing
# kinds `content_hash_of` binds) — anything else changes only by assertion or by edge.
_CONTENT_KINDS = (DevNodeKinds.NOTE, DevNodeKinds.SECTION, DevNodeKinds.CODE_SYMBOL,
                  DevNodeKinds.CODE_TEXT, DevNodeKinds.CODE_MODULE)
# Kinds whose assertion changes count as a governing-fact change (a Session's title or a
# Message's provenance facts are not what a deliverable depends on).
_ASSERTION_KINDS = _CONTENT_KINDS + (DevNodeKinds.DECISION, DevNodeKinds.ENTITY,
                                     DevNodeKinds.CHECK, DevNodeKinds.PROCEDURE)

CLASS_SELF = "self"
CLASS_CONTENT = "content"
CLASS_COSMETIC = "cosmetic"
CLASS_STRUCTURE = "structure"
CLASS_ASSERTION = "assertion"
CLASS_DEPENDENCY = "dependency-added"


def approvals_of(
    assertions: List[Any],                    # All Assertion nodes
    supersedes: List[Tuple[str, str]],        # All SUPERSEDES (superseder, superseded) pairs
) -> List[Dict[str, Any]]:  # [{assertion_id, subject_id, predicate, value, asserted_at, bound_hash, actor}]
    """Pure: the ACTIVE approval-class assertions (the roots the frontier walks from).

    Approval-class membership is schema data (`predicates.is_approval`): today
    `publish_state` at `reviewed` or `published`; a born `draft` is not an approval."""
    out: List[Dict[str, Any]] = []
    for slot_assertions in F.group_by_slot(assertions).values():
        for a in F.active_assertions(slot_assertions, supersedes):
            pred = str(F.prop(a, "predicate") or "")
            val = str(F.prop(a, "value") or "")
            subject = F.prop(a, "subject_id")
            if not subject or not P.is_approval(pred, val):
                continue
            out.append({"assertion_id": F.nid(a), "subject_id": subject, "predicate": pred,
                        "value": val, "asserted_at": float(F.prop(a, "asserted_at") or 0.0),
                        "bound_hash": str(F.prop(a, "subject_content_hash") or ""),
                        "actor": F.prop(a, "actor")})
    return sorted(out, key=lambda r: (r["subject_id"], r["predicate"], r["asserted_at"]))


def walk_upstream(
    root: str,                                          # The deliverable id (depth 0)
    components: List[str],                              # root + its own parts (Sections) — never upstream themselves
    adjacency: Dict[str, List[Tuple[str, str]]],        # node id -> [(relation, target id)] over the dependency relations
    depth: int = 3,                                     # Bounded walk depth (hops from a component)
) -> Dict[str, List[Dict[str, str]]]:  # upstream id -> the chain PATH from root: [{relation, id}, …] (shortest first found)
    """Pure: BFS upstream from the deliverable's components along the dependency edges.

    A component's outgoing edges count as the root's (a Section's REFERENCES is the
    Note's dependency; the path shows the hop). The root and its components are never
    reported as their own upstream; the first (shortest) path to a node wins."""
    own: Set[str] = set(components) | {root}
    paths: Dict[str, List[Dict[str, str]]] = {}
    frontier: List[Tuple[str, List[Dict[str, str]]]] = [(root, [])]
    for c in components:
        if c != root:
            frontier.append((c, [{"relation": DevRelations.HAS_SECTION, "id": c}]))
    hops = 0
    while frontier and hops < depth:
        nxt: List[Tuple[str, List[Dict[str, str]]]] = []
        for node, path in frontier:
            for rel, tgt in adjacency.get(node, []):
                if tgt in own or tgt in paths:
                    continue
                p = path + [{"relation": rel, "id": tgt}]
                paths[tgt] = p
                nxt.append((tgt, p))
        frontier = nxt
        hops += 1
    return paths


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def classify_text_change(
    live: str,                   # The node's LIVE verbatim content
    baseline: Optional[str],     # The journaled content at/before approval (None = no baseline)
    containment: bool = False,   # True: `live` is a REGION of `baseline` (a symbol body in a module snapshot)
) -> Optional[str]:  # None = unchanged · CLASS_COSMETIC (whitespace-only) · CLASS_CONTENT
    """Pure, revert-aware: compare the live content against its approval-time baseline.

    Equality (or containment, for a symbol body against a module snapshot) = unchanged
    even when the node was touched after approval and edited back. Whitespace-only
    drift is `cosmetic`; anything else is a `content` change. No baseline = `content`
    (the caller only asks when the journal shows a touch after approval)."""
    if baseline is None:
        return CLASS_CONTENT
    if (live in baseline) if containment else (live == baseline):
        return None
    lc, bc = _collapse_ws(live), _collapse_ws(baseline)
    if (lc in bc) if containment else (lc == bc):
        return CLASS_COSMETIC
    return CLASS_CONTENT


def change_key(
    upstream_id: str,      # The changed upstream node
    token: str,            # What changed: a content hash, `assertion:<id>`, or `edge:<relation>`
) -> str:  # The acknowledgment key a `review_verdict` value must equal
    """The change KEY an acknowledgment binds to: `<upstream 8>@<token 12>` (a hash token
    drops its `algo:` prefix). Short enough to type, exact enough that the same upstream
    changing again (a new hash) never matches an old verdict."""
    tok = token.split(":", 1)[1] if token.startswith("sha256:") else token
    if not (tok.startswith("assertion:") or tok.startswith("edge:") or tok.startswith("ts:")):
        tok = tok[:12]
    return f"{upstream_id[:8]}@{tok}"


def _journal_op(seg: str, line_no: int) -> Dict[str, Any]:
    """Fetch ONE journaled op's payload by (segment, line) — the touch rows drop payloads
    (module snapshots are large); a baseline compare re-reads exactly the line it needs."""
    try:
        lines = Path(seg).read_text().splitlines()
        return json.loads(lines[line_no])
    except (OSError, IndexError, json.JSONDecodeError):
        return {}


def _fmt_ts(ts: float) -> str:
    from datetime import datetime
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return str(ts)


def _content_change(
    node: Any,                                    # The upstream node
    kind: str,                                    # Its label
    after_ts: float,                              # The approval time T
    rows_by_ref: Dict[str, List[Dict[str, Any]]], # Journal touch rows by touched ref
    sections_of: Dict[str, List[str]],            # Note id -> Section ids
    verified: bool,                               # Journals were provided (else content is unverifiable)
) -> Tuple[Optional[str], str, bool]:  # (class or None, detail, unverifiable)
    """One upstream node's CONTENT change since T, journal-verified and revert-aware.

    CodeSymbol / CodeText: the module's `source` snapshots (the source journal) — no
    snapshot after T = unchanged with no fetch; else the live slot is compared against
    the last snapshot at/before T (or the first capture after it). Section: `section`
    STATE ops likewise (baseline raw = the last op at/before T). Note: its sections'
    edits plus `add-section` (structure). CodeModule: any snapshot after T."""
    uid = F.nid(node) or ""
    if not verified:
        return None, "", kind in _CONTENT_KINDS
    if kind in (DevNodeKinds.CODE_SYMBOL, DevNodeKinds.CODE_TEXT):
        module_id = str(F.prop(node, "module_id") or "")
        rows = sorted((r for r in rows_by_ref.get(module_id, []) if r["verb"] == "source"),
                      key=lambda r: r["ts"])
        if not rows:
            return None, "", True
        after = [r for r in rows if r["ts"] > after_ts]
        if not after:
            return None, "", False
        before = [r for r in rows if r["ts"] <= after_ts]
        base = before[-1] if before else after[0]
        text = str((_journal_op(base["seg"], base["line"]).get("args") or {}).get("text") or "")
        live = str(F.prop(node, "body" if kind == DevNodeKinds.CODE_SYMBOL else "text") or "")
        cls = classify_text_change(live, text, containment=True)
        if cls is None:
            return None, "", False
        mine = [r for r in after if (r.get("source_op") or {}).get("node_id") == uid]
        ops = sorted({str((r.get("source_op") or {}).get("op") or "absorb") for r in mine}) or ["module absorbed"]
        when = "baseline " + _fmt_ts(base["ts"]) + ("" if before else " (first capture after approval)")
        return cls, f"{'/'.join(ops)} after approval; {when}", False
    if kind == DevNodeKinds.CODE_MODULE:
        after = [r for r in rows_by_ref.get(uid, []) if r["verb"] == "source" and r["ts"] > after_ts]
        if not after:
            return None, "", False
        return CLASS_CONTENT, f"{len(after)} source snapshot(s) after approval, last {_fmt_ts(after[-1]['ts'])}", False
    if kind == DevNodeKinds.SECTION:
        rows = sorted((r for r in rows_by_ref.get(uid, []) if r["verb"] == "section"),
                      key=lambda r: r["ts"])
        after = [r for r in rows if r["ts"] > after_ts]
        if not after:
            return None, "", False
        before = [r for r in rows if r["ts"] <= after_ts]
        baseline = (str((_journal_op(before[-1]["seg"], before[-1]["line"]).get("args") or {}).get("raw") or "")
                    if before else None)
        cls = classify_text_change(str(F.prop(node, "raw") or ""), baseline)
        if cls is None:
            return None, "", False
        return cls, f"section edited {_fmt_ts(after[-1]['ts'])}" + ("" if before else " (no pre-approval baseline)"), False
    if kind == DevNodeKinds.NOTE:
        classes: List[str] = []
        details: List[str] = []
        struct = [r for r in rows_by_ref.get(uid, []) if r["verb"] in ("add-section", "new-note")
                  and r["ts"] > after_ts]
        if struct:
            classes.append(CLASS_STRUCTURE)
            details.append(f"{len(struct)} section(s) added, last {_fmt_ts(struct[-1]['ts'])}")
        for sid in sections_of.get(uid, []):
            rows = sorted((r for r in rows_by_ref.get(sid, []) if r["verb"] == "section"),
                          key=lambda r: r["ts"])
            after = [r for r in rows if r["ts"] > after_ts]
            if not after:
                continue
            before = [r for r in rows if r["ts"] <= after_ts]
            # The section's live raw is not on the Note node — an edit after approval on a
            # section with a pre-approval baseline is classified by the LATEST op's raw
            # against that baseline (both journaled), no graph read needed.
            latest = str((_journal_op(after[-1]["seg"], after[-1]["line"]).get("args") or {}).get("raw") or "")
            baseline = (str((_journal_op(before[-1]["seg"], before[-1]["line"]).get("args") or {}).get("raw") or "")
                        if before else None)
            cls = classify_text_change(latest, baseline)
            if cls is None:
                continue
            classes.append(cls)
            details.append(f"section `{sid[:8]}` edited {_fmt_ts(after[-1]['ts'])}")
        if not classes:
            return None, "", False
        cls = (CLASS_CONTENT if CLASS_CONTENT in classes
               else CLASS_STRUCTURE if CLASS_STRUCTURE in classes else CLASS_COSMETIC)
        return cls, "; ".join(details), False
    return None, "", False


async def review_frontier(
    gx: GraphHandle,
    journal_paths: Optional[List[str]] = None,  # The writes + source journals (content verification; None = graph-only)
    *,
    subject: Optional[str] = None,   # Restrict to one deliverable (id/prefix) or a label substring
    depth: int = 3,                  # Bounded upstream walk depth
    include_acked: bool = False,     # List acknowledged changes too (default: counted, hidden)
    assertions: Optional[List[Any]] = None,  # Preloaded Assertion nodes (one load per VIEW)
    supers: Optional[Any] = None,            # Preloaded supersedes, same reason
) -> Dict[str, Any]:  # {stale: [rows], counts: {approvals, stale, changes, acknowledged, unverifiable}, view}
    """The derived review frontier: approved deliverables whose upstream changed since approval.

    Pure read. For every active approval (`approvals_of`) walk upstream (`walk_upstream`)
    over the dependency relations, then classify each upstream node's change since the
    approval time: content (journal-verified against the approval-time baseline —
    `classify_text_change`), governing assertions asserted after it, and dependency
    edges linked after it. Active `review_verdict` values on the deliverable silence the
    changes whose KEY they equal. A deliverable with no unacknowledged change is not a
    row: the frontier is EMPTY when nothing upstream changed (the acceptance bar)."""
    from .write import content_hash_of  # function-local: write.py imports the projection tree

    assertions = assertions if assertions is not None else await F.load_assertions(gx)
    supers = supers if supers is not None else await F.load_supersedes(gx)
    approvals = approvals_of(assertions, supers)
    view: Dict[str, Any] = {"subject": subject, "depth": depth, "all": include_acked,
                            "journals": list(journal_paths or [])}
    counts = {"approvals": len(approvals), "stale": 0, "changes": 0, "acknowledged": 0,
              "unverifiable": 0}
    if not approvals:
        return {"stale": [], "counts": counts, "view": view}

    adjacency: Dict[str, List[Tuple[str, str]]] = {}
    for rel in DEPENDENCY_RELATIONS:
        for s, t in await F.load_edge_pairs(gx, rel):
            adjacency.setdefault(s, []).append((rel, t))
    sections_of: Dict[str, List[str]] = {}
    for n, s in await F.load_edge_pairs(gx, DevRelations.HAS_SECTION):
        sections_of.setdefault(n, []).append(s)

    # Active assertions per subject (the governing-fact change source) + acknowledgments.
    acks: Dict[str, Set[str]] = {}
    active_on: Dict[str, List[Dict[str, Any]]] = {}
    for slot_assertions in F.group_by_slot(assertions).values():
        for a in F.active_assertions(slot_assertions, supers):
            pred = str(F.prop(a, "predicate") or "")
            subj = str(F.prop(a, "subject_id") or "")
            if not subj:
                continue
            if pred == P.REVIEW_VERDICT:
                acks.setdefault(subj, set()).add(str(F.prop(a, "value") or "").strip())
                continue
            active_on.setdefault(subj, []).append(
                {"id": F.nid(a), "predicate": pred, "value": str(F.prop(a, "value") or ""),
                 "ts": float(F.prop(a, "asserted_at") or 0.0)})

    # Journal touch rows by ref (content edits, source snapshots, link births).
    rows_by_ref: Dict[str, List[Dict[str, Any]]] = {}
    link_births: Dict[Tuple[str, str, str], float] = {}
    for p in (journal_paths or []):
        if not p:
            continue
        for row in journal_touch_rows(p):
            for ref in row["refs"]:
                rows_by_ref.setdefault(ref, []).append(row)
            if row["verb"] == "link" and row.get("relation") and len(row["refs"]) == 2:
                key = (row["refs"][0], row["refs"][1], str(row["relation"]))
                link_births[key] = max(link_births.get(key, 0.0), row["ts"])
    verified = bool([p for p in (journal_paths or []) if p])

    # Drafted proposals (bb015d12): (deliverable, change key) -> Proposal id, so a stale row
    # points at the draft that answers it and `propose` never re-drafts the same change.
    from .write import PROPOSAL_LABEL
    proposals: Dict[Tuple[str, str], str] = {}
    for pn in await F.load_label(gx, PROPOSAL_LABEL):
        proposals[(str(F.prop(pn, "deliverable_id") or ""), str(F.prop(pn, "key") or ""))] = F.nid(pn)

    walks: Dict[str, Dict[str, List[Dict[str, str]]]] = {}
    for ap in approvals:
        d = ap["subject_id"]
        if d not in walks:
            walks[d] = walk_upstream(d, [d] + sections_of.get(d, []), adjacency, depth)
    need: Set[str] = set(walks)
    for up in walks.values():
        need.update(up)
        for path in up.values():
            need.update(h["id"] for h in path)
    nodes = await F.load_nodes(gx, sorted(need))
    await annotate_display(gx, list(nodes.values()))

    def _label(nid: str) -> str:
        return node_title(nodes[nid]) if nid in nodes else nid

    def _ref(nid: str) -> Dict[str, str]:
        return {"id": nid, "label": _label(nid), "kind": str(F.label(nodes.get(nid)) or "?")}

    subject_l = subject.lower() if subject else None
    stale: List[Dict[str, Any]] = []
    hash_cache: Dict[str, Optional[str]] = {}

    async def _live_hash(nid: str) -> Optional[str]:
        if nid not in hash_cache:
            hash_cache[nid] = await content_hash_of(gx, nid, "")
        return hash_cache[nid]

    for ap in approvals:
        d, t = ap["subject_id"], ap["asserted_at"]
        if subject_l is not None and not (d.startswith(subject_l) or subject_l in _label(d).lower()):
            continue
        changes: List[Dict[str, Any]] = []
        acked = acks.get(d, set())

        def _add(uid: str, path: List[Dict[str, str]], cls: str, detail: str, token: str, at: float) -> None:
            key = change_key(uid, token)
            changes.append({"upstream": _ref(uid), "path": [{**h, "label": _label(h["id"])} for h in path],
                            "class": cls, "detail": detail, "at": at, "key": key,
                            "acknowledged": key in acked, "proposal": proposals.get((d, key))})

        # self: the deliverable's own content moved off the approved hash (demotion by derivation).
        if ap["bound_hash"]:
            live = await _live_hash(d)
            if live and live != ap["bound_hash"]:
                _add(d, [], CLASS_SELF,
                     f"approved {ap['bound_hash'].split(':')[-1][:12]}, now {live.split(':')[-1][:12]} — re-approval is the only cure",
                     live, t)

        for uid, path in walks[d].items():
            node = nodes.get(uid)
            if node is None:
                continue
            kind = str(F.label(node) or "")
            cls, detail, unverifiable = _content_change(node, kind, t, rows_by_ref, sections_of, verified)
            if unverifiable:
                counts["unverifiable"] += 1
            if cls:
                live = await _live_hash(uid)
                _add(uid, path, cls, detail, live or f"ts:{int(t)}", t)
            for a in active_on.get(uid, []):
                if a["ts"] > t and kind in _ASSERTION_KINDS:
                    _add(uid, path, CLASS_ASSERTION, f"{a['predicate']}={a['value'][:60]} asserted {_fmt_ts(a['ts'])}",
                         f"assertion:{(a['id'] or '')[:8]}", a["ts"])
            if path:
                src = path[-2]["id"] if len(path) > 1 else d
                born = link_births.get((src, uid, path[-1]["relation"]))
                if born is not None and born > t:
                    live = await _live_hash(uid)
                    _add(uid, path, CLASS_DEPENDENCY, f"{path[-1]['relation']} linked {_fmt_ts(born)}",
                         live or f"edge:{path[-1]['relation']}", born)

        n_ack = sum(1 for c in changes if c["acknowledged"])
        counts["changes"] += len(changes)
        counts["acknowledged"] += n_ack
        shown = changes if include_acked else [c for c in changes if not c["acknowledged"]]
        if not shown:
            continue
        counts["stale"] += 1
        stale.append({"deliverable": _ref(d),
                      "approval": {k: ap[k] for k in ("assertion_id", "predicate", "value", "asserted_at",
                                                      "bound_hash", "actor")},
                      "changes": sorted(shown, key=lambda c: (c["class"] != CLASS_SELF, -c["at"])),
                      "acknowledged": n_ack})
    return {"stale": stale, "counts": counts, "view": view}
