"""Triage proposals: an agent DRAFTS the update for a stale deliverable (work item bb015d12).

The automation half of design 40622922 (6), split out of the review frontier (730e077e)
at its close. `review-frontier` is the worklist: a stale row prints the chain, the change
class and two confirm recipes (acknowledge via `review_verdict`, or update + re-assert
`publish_state`). This module adds the PROPOSE leg — for a chosen stale row, draft the
deliverable's updated content from the changed upstream and land it as a `Proposal`
node BESIDE the approved content (never over it), carrying `DERIVED_FROM` edges to the
upstream nodes it drew on and an `answers_change` fact naming the change key.

    first slice ≡ a code-referencing deliverable whose referenced CodeSymbol body changed:
                  the fenced code block that carries the symbol's approval-time body is
                  re-rendered from the LIVE body and the section is marked
    confirm     ≡ `confirm-proposal <id>` — applies the draft as a journaled `section`
                  op and re-asserts the approval, which binds to the new hash and
                  supersedes the stale one (write.confirm_proposal)
    reject      ≡ the ordinary `assert <deliverable> review_verdict <key>` ack

The useful-not-noisy bar (bb015d12 (c)): a proposal is minted only for an UNACKNOWLEDGED
change of a class the drafter can act on (`content` / `structure` / `dependency-added`
on a code reference), never for `cosmetic` or `assertion` classes, and a re-run over an
unchanged frontier mints nothing (deterministic proposal identity + the frontier's own
`proposal` pointer). Everything the drafter cannot act on is REPORTED as skipped, with
the reason — the drafter's silence on a change class is an audit signal, like the
projector's.
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from cjm_context_graph_layer.ops import graph_task
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations
from cjm_markdown_decompose_core.parse import fenced_code_spans

from . import factlayer as F
from .authoring import _slice_block
from .journal import journal_touch_rows
from .review import _journal_op, CLASS_CONTENT, CLASS_DEPENDENCY, CLASS_STRUCTURE, review_frontier
from .runtime import GraphHandle
from .write import mint_proposal

# The change classes a drafter can act on (bb015d12 (c)); cosmetic and assertion never draft.
ACTIONABLE_CLASSES: Tuple[str, ...] = (CLASS_CONTENT, CLASS_STRUCTURE, CLASS_DEPENDENCY)

# The section mark a draft leaves (an HTML comment renders to nothing in Quarto/markdown
# output; a second draft for the same key never doubles it).
PROPOSAL_MARK = "<!-- proposal {key}: code block re-rendered from `{name}` (live body) -->"


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def draft_code_block_update(
    section_raw: str,            # The target section's verbatim `raw`
    baseline_body: Optional[str],  # The symbol's body at approval time (None = no journaled baseline)
    live_body: str,              # The symbol's LIVE body
    name: str,                   # The symbol's bare name (the header the block must open)
    key: str,                    # The change key (rides the mark)
) -> Optional[str]:  # The drafted section raw, or None when no fenced block carries the symbol
    """Pure: re-render the fenced code block that carries `name`'s body from the live body.

    Block selection, strictest first: (1) the approval-time body appears VERBATIM inside a
    fenced block — only that span is replaced (surrounding lines in the block stay);
    (2) it appears modulo whitespace — the whole block content is re-rendered (the block
    had drifted cosmetically); (3) no baseline — a block whose content opens a `def`/`class`
    of `name` is re-rendered whole. A section without such a block is not draftable here
    (None): the drafter reports it skipped rather than guessing. The mark lands on its
    own line right after the block; an identical mark already present is not doubled."""
    live = live_body.rstrip("\n")
    base = baseline_body.rstrip("\n") if baseline_body else None
    header = re.compile(rf"^\s*(async\s+def|def|class)\s+{re.escape(name)}\b", re.M)
    for start, end in fenced_code_spans(section_raw):
        block = section_raw[start:end]
        lines = block.split("\n")
        if len(lines) < 2:
            continue
        open_line = lines[0]
        # the closing fence is the last non-empty line of the span
        close_idx = len(lines) - 1
        while close_idx > 0 and not lines[close_idx].strip():
            close_idx -= 1
        inner = "\n".join(lines[1:close_idx])
        tail = "\n".join(lines[close_idx:])
        new_inner: Optional[str] = None
        if base is not None and base in inner:
            new_inner = inner.replace(base, live, 1)
        elif base is not None and _collapse_ws(base) and _collapse_ws(base) in _collapse_ws(inner):
            new_inner = live
        elif base is None and header.search(inner):
            new_inner = live
        if new_inner is None or new_inner == inner:
            continue
        mark = PROPOSAL_MARK.format(key=key, name=name)
        rest = section_raw[end:]
        marked = rest if mark in rest else ("\n" + mark + "\n\n" + rest.lstrip("\n") if rest.strip() else "\n" + mark + "\n")
        return section_raw[:start] + open_line + "\n" + new_inner + "\n" + tail + marked
    return None


def symbol_baseline_body(
    symbol: Any,                                    # The CodeSymbol node
    after_ts: float,                                # The approval time T
    rows_by_ref: Dict[str, List[Dict[str, Any]]],   # Journal touch rows by touched ref (the frontier's substrate)
) -> Optional[str]:  # The symbol's body sliced from the module snapshot at/before T (None = no snapshot)
    """The approval-time body: the module's last `source` snapshot at/before T (else the
    first capture after it — the frontier's own baseline rule), sliced by name."""
    module_id = str(F.prop(symbol, "module_id") or "")
    rows = sorted((r for r in rows_by_ref.get(module_id, []) if r["verb"] == "source"),
                  key=lambda r: r["ts"])
    if not rows:
        return None
    before = [r for r in rows if r["ts"] <= after_ts]
    base = before[-1] if before else rows[0]
    text = str((_journal_op(base["seg"], base["line"]).get("args") or {}).get("text") or "")
    return _slice_block(text, str(F.prop(symbol, "name") or "")) if text else None


async def propose_updates(
    gx: GraphHandle,
    journal_paths: Optional[List[str]] = None,  # The writes + source journals (the frontier's content verification)
    *,
    subject: Optional[str] = None,   # Restrict to one deliverable (id) or a label substring — as review-frontier
    depth: int = 3,                  # Upstream walk depth
    actor: str = "agent:session",    # The drafter
    write: bool = True,              # Mint the proposals (else a dry run: report what WOULD be drafted)
) -> Dict[str, Any]:  # {proposals: [mint results + draft args], skipped: [{deliverable, upstream, key, reason}], counts}
    """Draft a proposal for every actionable, unacknowledged, not-yet-proposed change on the
    review frontier (bb015d12 (a)+(c)).

    Runs the frontier, then for each stale row and each change of an actionable class
    whose upstream is a CodeSymbol: take the symbol's approval-time body from the source
    journal, find the deliverable section whose fenced code block carries it (the chain's
    Section hop first, else every section of the deliverable), re-render the block from
    the live body (`draft_code_block_update`) and mint the Proposal. Everything else is
    reported under `skipped` with its reason. The returned `proposals` carry the exact
    mint args the CLI journals (`propose` op) so replay re-lands the same draft."""
    fr = await review_frontier(gx, journal_paths, subject=subject, depth=depth)
    rows_by_ref: Dict[str, List[Dict[str, Any]]] = {}
    for p in (journal_paths or []):
        if not p:
            continue
        for row in journal_touch_rows(p):
            for ref in row["refs"]:
                rows_by_ref.setdefault(ref, []).append(row)
    sections_of: Dict[str, List[str]] = {}
    for n, s in await F.load_edge_pairs(gx, DevRelations.HAS_SECTION):
        sections_of.setdefault(n, []).append(s)

    proposals: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    def _skip(d: str, ch: Dict[str, Any], reason: str) -> None:
        skipped.append({"deliverable": d, "upstream": (ch.get("upstream") or {}).get("id"),
                        "key": ch.get("key"), "class": ch.get("class"), "reason": reason})

    for row in fr.get("stale", []):
        d = row["deliverable"]["id"]
        ap = row["approval"]
        note = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=d)
        slug = str(F.prop(note, "slug") or "") if note is not None else ""
        secs = await F.load_nodes(gx, sections_of.get(d, []))
        for ch in row.get("changes", []):
            if ch.get("acknowledged"):
                _skip(d, ch, "acknowledged")
                continue
            if ch.get("class") not in ACTIONABLE_CLASSES:
                _skip(d, ch, f"class `{ch.get('class')}` never drafts")
                continue
            if ch.get("proposal"):
                _skip(d, ch, f"already proposed `{str(ch['proposal'])[:8]}`")
                continue
            up = ch.get("upstream") or {}
            if up.get("kind") != DevNodeKinds.CODE_SYMBOL:
                _skip(d, ch, f"upstream is a {up.get('kind')} — the first slice drafts code references only")
                continue
            sym = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=up["id"])
            if sym is None:
                _skip(d, ch, "upstream symbol not found")
                continue
            live = str(F.prop(sym, "body") or "")
            name = str(F.prop(sym, "name") or "")
            baseline = symbol_baseline_body(sym, float(ap.get("asserted_at") or 0.0), rows_by_ref)
            path = ch.get("path") or []
            cands = ([path[0]["id"]] if path and path[0].get("relation") == DevRelations.HAS_SECTION
                     else list(sections_of.get(d, [])))
            drafted: Optional[Tuple[str, str]] = None
            for sid in cands:
                sec = secs.get(sid)
                if sec is None:
                    continue
                new_raw = draft_code_block_update(str(F.prop(sec, "raw") or ""), baseline, live, name, ch["key"])
                if new_raw is not None:
                    drafted = (sid, new_raw)
                    break
            if drafted is None:
                _skip(d, ch, f"no fenced code block in the deliverable carries `{name}`")
                continue
            sid, new_raw = drafted
            args = {"deliverable_id": d, "section_id": sid, "key": ch["key"], "raw": new_raw,
                    "slug": slug, "anchor": str(F.prop(secs[sid], "anchor") or ""),
                    "upstream_ids": [up["id"], sid],
                    "summary": f"re-rendered `{name}`'s code block from the live body ({ch.get('detail')})",
                    "approval": {"predicate": ap.get("predicate"), "value": ap.get("value")},
                    "actor": actor}
            res = ({"proposal_id": None, "written": False, "dry_run": True} if not write
                   else await mint_proposal(gx, args["deliverable_id"], args["section_id"], args["key"],
                                            args["raw"], slug=args["slug"], anchor=args["anchor"],
                                            upstream_ids=args["upstream_ids"], summary=args["summary"],
                                            approval=args["approval"], actor=actor))
            proposals.append({**res, "args": args, "upstream": up, "deliverable": row["deliverable"]})
    return {"proposals": proposals, "skipped": skipped,
            "counts": {"stale": len(fr.get("stale", [])), "proposed": len(proposals),
                       "skipped": len(skipped)}, "frontier_counts": fr.get("counts", {})}
