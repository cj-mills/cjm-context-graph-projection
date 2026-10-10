"""The facet review and the public build's facet gate (design eefda2dd (5), (7)).

The judge only PROPOSES; the user CONFIRMS. The review reads the stored judgments (facetjudge)
against the confirmed facets and the review marks, and sorts every fresh (post, entry) pair:

- a PROPOSAL: judged at or above the threshold, no confirmed fact, no mark on its basis;
- a CHALLENGED fact: confirmed, but its current judgment fell below the threshold, no mark;
- a NEAR MISS: from the store floor up to the threshold, no confirmed fact, no mark.

Rows group BY ENTRY with their posts by p, so an outlier stands out within a column. The
review DOCUMENT is markdown the user edits: a checked proposal confirms the facet, a cleared one
is rejected; a checked near miss confirms; a checked challenged fact is kept. Each row carries
its post, entry and BASIS (the post's judged state and the entry's criteria) in a comment, so
`review-facets --apply` lands the whole file as ONE journaled batch -- the confirmations assert
the facts, every reviewed proposal and every kept or confirmed row is MARKED REVIEWED on its
judged edge -- or refuses the whole file when any row's basis moved. A rejection needs no
negative fact: the mark keeps the pair from being proposed again until its basis changes.
Dropping a challenged fact needs a withdrawal the fact layer does not have yet, so the file is
refused there (work item filed in session 2026-10-03_20-43-16).

The GATE (eefda2dd (7)): a public post with stale facet judgments or unreviewed rows refuses a
public build (staging counts them), and a public post with no confirmed facet of any kind -- a
tutorial's teaches_* count -- is reported, never left bare silently (6752db0a (9))."""

import re
from typing import Any, Dict, List, Optional

from cjm_context_graph_layer.ops import graph_task
from cjm_dev_graph_schema import predicates as P

from . import factlayer as F
from .facetjudge import (applies, criteria_hash, entry_question, FACET_OF_KIND,
                         load_facet_judgments, load_facet_records, load_facet_views,
                         load_facet_vocab, review_basis, stale_pairs, STORE_FLOOR, THRESHOLD)
from .runtime import GraphHandle

SECTIONS = ("proposal", "challenged", "near")
_ROW = re.compile(r"^\s*- \[([ xX])\] .*<!-- facet (proposal|challenged|near) (\S+) (\S+) (\S+) -->\s*$")
WITHDRAWAL_ITEM = "the fact layer's withdrawal of one value from a multivalued slot"


async def load_confirmed(
    gx: GraphHandle,
) -> Dict[str, Dict[str, List[str]]]:  # {post id: {facet predicate: [active vocabulary keys]}}
    """Every post's ACTIVE confirmed facets (supersession applied)."""
    slot = [a for a in await F.load_assertions(gx) if F.prop(a, "predicate") in P.FACET_PREDICATES]
    out: Dict[str, Dict[str, List[str]]] = {}
    for a in F.active_assertions(slot, await F.load_supersedes(gx)):
        vals = out.setdefault(str(F.prop(a, "subject_id")), {}).setdefault(str(F.prop(a, "predicate")), [])
        v = str(F.prop(a, "value") or "")
        if v and v not in vals:
            vals.append(v)
    return out


async def review_state(
    gx: GraphHandle,
    threshold: float = THRESHOLD,
) -> Dict[str, Any]:  # {rows, stale, views, vocab, bases, judged, confirmed}
    """Every fresh pair's review row (module docstring), the stale pairs apart, and the reads
    the apply and the gate reuse. `bases` = {(post, entry): the pair's current basis}."""
    views, vocab = await load_facet_views(gx), await load_facet_vocab(gx)
    crit = {e: criteria_hash(entry_question(v)) for e, v in vocab.items()}
    records, judged, confirmed = (await load_facet_records(gx), await load_facet_judgments(gx),
                                  await load_confirmed(gx))
    stale = stale_pairs(views, crit, records)
    rows, bases = [], {}
    for e, v in vocab.items():
        kind, key = e.split(":", 1)
        pred = FACET_OF_KIND[kind]
        for n in sorted(views):
            if not applies(e, views[n]["kind"]) or e in stale.get(n, ()):
                continue
            basis = review_basis(records[n]["state"], crit[e])
            bases[(n, e)] = basis
            j = judged.get((n, v["id"])) or {}
            p = float(j.get("p") or 0.0)
            has = key in (confirmed.get(n) or {}).get(pred, [])
            if j.get("reviewed") == basis:
                continue
            section = ("challenged" if has and p < threshold else None if has else
                       "proposal" if p >= threshold else "near" if p >= STORE_FLOOR else None)
            if section:
                rows.append({"section": section, "post": n, "entry": e, "p": round(p, 3), "basis": basis,
                             "title": views[n]["title"], "kind": views[n]["kind"]})
    return {"rows": rows, "stale": stale, "views": views, "vocab": vocab, "bases": bases,
            "judged": judged, "confirmed": confirmed}


def review_document(
    rows: List[Dict[str, Any]],              # review_state's rows
    vocab: Dict[str, Dict[str, Any]],        # The live facet vocabulary (entry order)
    threshold: float = THRESHOLD,
) -> str:  # The markdown the user edits and hands back to --apply
    """The review document, grouped by entry, each entry's rows by p (module docstring)."""
    count = {s: sum(1 for r in rows if r["section"] == s) for s in SECTIONS}
    out = [f"# Facet review · threshold {threshold} · {count['proposal']} proposal(s) · "
           f"{count['challenged']} challenged · {count['near']} near miss(es)", "",
           "Each row is one (post, entry) pair. A **proposal** is checked: leave `[x]` to confirm the facet, "
           "clear it to reject it (a rejected proposal is marked reviewed and not proposed again until the post "
           "or the entry's criteria change). A **challenged** fact is checked: leave it to keep the fact. A "
           "**near miss** (folded) is unchecked: check it to confirm. Then "
           "`cg-write --notes review-facets --apply <this file>`: the whole file lands as one journaled batch, "
           "or nothing does if a row went stale. Keep each row's trailing comment.", ""]
    by: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(r["entry"], []).append(r)

    def line(r, box):
        return (f"- [{box}] {r['p']:.2f} · {r['title']} _{r['kind']}_ "
                f"<!-- facet {r['section']} {r['post']} {r['entry']} {r['basis']} -->")
    for e, v in vocab.items():
        mine = sorted(by.get(e, []), key=lambda r: (-r["p"], r["title"], r["post"]))
        if not mine:
            continue
        lead = [r for r in mine if r["section"] != "near"]
        near = [r for r in mine if r["section"] == "near"]
        kind, key = e.split(":", 1)
        out += [f"## {kind} · {v.get('name') or key} `{key}` · {len(lead)}", "",
                f"_{v.get('description') or ''}" + (f" · not for: {v['not_for']}" if v.get("not_for") else "") + "_", ""]
        out += [line(r, "x") for r in lead]
        if near:
            out += ["", f"<details><summary>near misses · {len(near)}</summary>", ""]
            out += [line(r, " ") for r in near]
            out += ["", "</details>"]
        out.append("")
    return "\n".join(out)


def parse_review(
    text: str,  # An edited review document
) -> Dict[str, Any]:  # {rows: [{checked, section, post, entry, basis}], errors}
    """The document's rows; a repeated pair is an error (the file is refused whole)."""
    rows, errors, seen = [], [], set()
    for i, ln in enumerate(text.splitlines(), 1):
        m = _ROW.match(ln)
        if not m:
            if "<!-- facet " in ln:
                errors.append(f"line {i}: a row the review cannot read: {ln.strip()[:120]}")
            continue
        row = {"checked": m.group(1) != " ", "section": m.group(2), "post": m.group(3),
               "entry": m.group(4), "basis": m.group(5)}
        if (row["post"], row["entry"]) in seen:
            errors.append(f"line {i}: the pair {row['post'][:8]} {row['entry']} appears twice")
        seen.add((row["post"], row["entry"]))
        rows.append(row)
    return {"rows": rows, "errors": errors}


async def plan_review(
    gx: GraphHandle,
    text: str,   # The edited review document
) -> Dict[str, Any]:  # {run: {confirm, mark}, counts, errors}
    """Check every row against the graph (its pair fresh, its basis current, its edge present) and
    plan the batch: confirm each checked proposal / near miss; mark each proposal, each checked
    near miss and each kept challenged fact. Any error refuses the whole file."""
    parsed = parse_review(text)
    errors = list(parsed["errors"])
    st = await review_state(gx)
    confirm, mark = [], []
    counts = {"confirmed": 0, "rejected": 0, "kept": 0, "near_unchecked": 0}
    for r in parsed["rows"]:
        e, n = r["entry"], r["post"]
        v = st["vocab"].get(e)
        if v is None or n not in st["views"]:
            errors.append(f"{n[:8]} {e}: no live entry or public post")
            continue
        if st["bases"].get((n, e)) != r["basis"]:
            errors.append(f"{n[:8]} {e}: the pair's basis moved since the document was written "
                          "(judge-facets, then a fresh review document)")
            continue
        edge = st["judged"].get((n, v["id"]))
        kind, key = e.split(":", 1)
        pred = FACET_OF_KIND[kind]
        if r["section"] == "challenged" and not r["checked"]:
            errors.append(f"{n[:8]} {e}: dropping a confirmed facet needs {WITHDRAWAL_ITEM} -- keep it checked")
            continue
        if r["section"] == "near" and not r["checked"]:
            counts["near_unchecked"] += 1
            continue
        if edge is None:
            errors.append(f"{n[:8]} {e}: no stored judgment to mark")
            continue
        has = key in (st["confirmed"].get(n) or {}).get(pred, [])
        if not r["checked"]:   # only a proposal is left unchecked here: a rejection
            counts["rejected"] += 1
        elif has:
            counts["kept"] += 1
        else:
            confirm.append([n, pred, key])
            counts["confirmed"] += 1
        mark.append([n, v["id"], r["basis"]])
    return {"run": {"confirm": confirm, "mark": mark}, "counts": counts, "errors": errors,
            "rows": len(parsed["rows"])}


async def apply_review(
    gx: GraphHandle,
    run: Dict[str, Any],   # {confirm: [[post, predicate, key]], mark: [[post, Entity id, basis]], content_hashes?}
    *,
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {asserted, marked, content_hashes}
    """Land one review: assert each confirmation (checked at write time like any facet), then set
    each mark on its judged edge. Live and replay share it; replay passes the journaled content
    hashes so the facts re-land the same."""
    from .write import FactBatch, assert_value
    given, hashes = dict(run.get("content_hashes") or {}), {}
    batch = await FactBatch.load(gx)   # one read of the fact layer for the whole review (finding da6cdab6)
    for n, pred, key in run["confirm"]:
        res = await assert_value(gx, n, pred, key, actor=actor, subject_content_hash=given.get(f"{n}|{pred}|{key}"),
                                 batch=batch)
        if res.get("error"):
            raise RuntimeError(f"{pred} {key} on {n}: {res['error']}")
        hashes[f"{n}|{pred}|{key}"] = res.get("subject_content_hash")
    judged = await load_facet_judgments(gx)
    for n, eid, basis in run["mark"]:
        edge = judged.get((n, eid))
        if edge is None:
            raise RuntimeError(f"no judged edge {n} -> {eid} to mark")
        await graph_task(gx.queue, gx.graph_id, "update_edge", edge_id=edge["_id"], properties={"reviewed": basis})
    return {"asserted": len(run["confirm"]), "marked": len(run["mark"]), "content_hashes": hashes}


async def review_facets(
    gx: GraphHandle,
    *,
    apply_text: Optional[str] = None,   # An edited review document to land; None = write a fresh one
    dry_run: bool = False,              # With apply_text: plan and check only
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {document, counts, stale} | {plan counts, errors, written, run?, applied?}
    """The review verb: the document (counts and stale pairs alongside), or one landed review."""
    if apply_text is None:
        st = await review_state(gx)
        counts = {s: sum(1 for r in st["rows"] if r["section"] == s) for s in SECTIONS}
        return {"document": review_document(st["rows"], st["vocab"]), "counts": counts,
                "entries": len({r["entry"] for r in st["rows"]}),
                "stale_pairs": sum(len(v) for v in st["stale"].values()), "written": False}
    plan = await plan_review(gx, apply_text)
    out = {"counts": plan["counts"], "rows": plan["rows"], "errors": plan["errors"], "written": False}
    if plan["errors"]:
        return {**out, "error": f"{len(plan['errors'])} row(s) refused -- nothing written"}
    if dry_run or not (plan["run"]["confirm"] or plan["run"]["mark"]):
        return out
    run = plan["run"]
    applied = await apply_review(gx, run, actor=actor)
    run["content_hashes"] = applied.pop("content_hashes")
    return {**out, "written": True, "run": run, "applied": applied}


async def facet_gate(
    gx: GraphHandle,
) -> Dict[str, List[Dict[str, Any]]]:  # {stale, unreviewed, bare} -- each [{id, title, ...}]
    """The public build's facet gate (eefda2dd (7)): the public posts with stale pairs, the ones
    with unreviewed proposals or challenged facts, and the ones with no confirmed facet of any
    kind (a tutorial's teaches_* count)."""
    from .coverage import load_coverage_facts
    st = await review_state(gx)
    views = st["views"]
    open_rows: Dict[str, Dict[str, int]] = {}
    for r in st["rows"]:
        if r["section"] != "near":
            c = open_rows.setdefault(r["post"], {"proposals": 0, "challenged": 0})
            c["proposals" if r["section"] == "proposal" else "challenged"] += 1
    teaches = await load_coverage_facts(gx)
    bare = [n for n in sorted(views) if not any((st["confirmed"].get(n) or {}).values())
            and not any((teaches.get(n) or {}).values())]
    return {"stale": [{"id": n, "title": views[n]["title"], "pairs": len(es)} for n, es in sorted(st["stale"].items())],
            "unreviewed": [{"id": n, "title": views[n]["title"], **c} for n, c in sorted(open_rows.items())],
            "bare": [{"id": n, "title": views[n]["title"]} for n in bare]}
