"""The role review (ruling 6a203252; design ad9bef5a (4), amendment 25a58e0e (3)-(4)).

The judge only PROPOSES; the user CONFIRMS. The review reads the stored judgments (sectionroles)
against the confirmed roles and the review marks, and writes three kinds of row:

- a PATTERN: a heading shared by PATTERN_MIN or more posts among the H2 / H3 Sections still
  without a role, proposed the role its Sections' mean distribution ranks first and confirmed
  ONCE for them all. A Section the judge places elsewhere (its own first role differs, at or above
  DISSENT_P) leaves the pattern for its post's rows, so one confirmation never overrides a
  confident disagreement;
- a SECTION: every other H2 / H3 Section without a role, under its post, in outline order;
- an OVERRIDE: a deeper Section the judge places elsewhere than the role it inherits, by
  OVERRIDE_MARGIN or more (25a58e0e (3): only overrides reach the user). A deeper Section whose
  ancestor has no role yet waits for it.

The review DOCUMENT is markdown the user edits. Each row names a role in backticks: a checked
row confirms the role it names (edit the name to choose another); a checked `none` marks the
Section reviewed with no role; an unchecked pattern or section row is left for a later review,
an unchecked override keeps the inherited role (marked reviewed). Each row's trailing comment
carries its basis, so `review-roles --apply` lands the whole file as ONE journaled batch -- the
confirmations assert `section_role` facts (one per Section, a pattern's on each of its
Sections), the marks land on the Sections' judged edges -- or refuses it whole when any row's
basis moved."""

import re
from typing import Any, Dict, List, Optional, Tuple

from cjm_context_graph_layer.ops import graph_task
from cjm_dev_graph_schema import predicates as P

from . import judgeengine as E
from .runtime import GraphHandle
from .sectionroles import (CANDIDATE_LEVELS, distribution, effective_roles, load_role_facts,
                           load_role_judgments, load_role_views, load_role_vocab, NONE, pattern_key,
                           PATTERN_MIN, question_hash, review_basis, role_question, role_scope,
                           scope_sections, stale_sections)

DISSENT_P = 0.5         # A Section whose own first role differs from its pattern's at or above it leaves the pattern
OVERRIDE_MARGIN = 0.3   # A deeper Section is proposed an override when its first role outranks the inherited one by this
KINDS = ("pattern", "section", "override")
_ROW = re.compile(r"^\s*- \[([ xX])\] `([^`]*)` .*<!-- role (pattern|section|override) (\S+) (\S+) -->\s*$")


def _top_role(dist: List[Tuple[str, float]]) -> Optional[str]:  # The first ROLE (never none) -- the edge a mark rides
    return next((k for k, _ in dist if k != NONE), None)


async def review_state(
    gx: GraphHandle,
) -> Dict[str, Any]:  # {patterns, sections, overrides, waiting, stale, unjudged, vocab, titles}
    """Every open row (module docstring), with the reads the plan reuses."""
    from .site import stated
    scope = await role_scope(gx)
    secs = await scope_sections(gx, scope)
    views, vocab = await load_role_views(gx, scope, secs), await load_role_vocab(gx)
    facts, judged = await load_role_facts(gx), await load_role_judgments(gx)
    roles = list(vocab)
    qhash = question_hash(role_question(vocab)) if roles else ""
    stale = set(stale_sections(views, qhash, judged, roles)) if roles else set(views)
    titles = {n: stated(scope[n]["node"], "title") for n in scope}
    cands, overrides, waiting = [], [], 0
    for n in sorted(scope, key=lambda n: (titles[n].lower(), n)):
        eff = effective_roles(secs[n], facts)
        for i, r in enumerate(secs[n]):
            s = r["id"]
            if s in stale or s in facts:
                continue
            dist = distribution(judged[s], roles)
            state = E.digest(views[s])
            mark = ((judged[s].get(_top_role(dist) or "") or {}).get("reviewed")) or ""
            row = {"post": n, "section": s, "heading": r["heading"], "level": r["level"], "order": i,
                   "dist": dist, "edge_role": _top_role(dist)}
            if r["level"] in CANDIDATE_LEVELS:
                basis = review_basis(state, qhash)
                if mark != basis:
                    cands.append({**row, "basis": basis})
                continue
            inherited = eff[s][0]
            if inherited is None:
                waiting += 1
                continue
            basis = review_basis(state, qhash, inherited)
            p = dict(dist)
            if mark != basis and dist[0][0] not in (inherited, NONE) and dist[0][1] - p.get(inherited, 0.0) >= OVERRIDE_MARGIN:
                overrides.append({**row, "basis": basis, "inherited": inherited})
    # patterns: a heading over PATTERN_MIN posts; its role ranks first in its members' mean distribution
    by: Dict[str, List[Dict[str, Any]]] = {}
    for c in cands:
        by.setdefault(pattern_key(c["heading"]), []).append(c)
    patterns, in_pattern = [], set()
    for key, members in sorted(by.items()):
        if len({m["post"] for m in members}) < PATTERN_MIN:
            continue
        mean: Dict[str, float] = {}
        for m in members:
            for k, p in m["dist"]:
                mean[k] = mean.get(k, 0.0) + p / len(members)
        ranked = sorted(mean.items(), key=lambda r: (-r[1], r[0]))
        role = ranked[0][0]
        keep = [m for m in members if m["dist"][0][0] == role or m["dist"][0][1] < DISSENT_P]
        if len({m["post"] for m in keep}) < PATTERN_MIN:
            continue
        in_pattern.update(m["section"] for m in keep)
        patterns.append({"key": key, "id": E.digest(key, 12), "heading": keep[0]["heading"], "role": role,
                         "mean": ranked, "members": keep, "posts": len({m["post"] for m in keep}),
                         "dissent": len(members) - len(keep),
                         "basis": E.digest(sorted([m["section"], m["basis"]] for m in keep), 16)})
    sections = [c for c in cands if c["section"] not in in_pattern]
    return {"patterns": sorted(patterns, key=lambda p: (-len(p["members"]), p["key"])), "sections": sections,
            "overrides": overrides, "waiting": waiting, "stale": len(stale), "unjudged": not roles,
            "vocab": vocab, "titles": titles}


def _alts(dist: List[Tuple[str, float]], n: int = 2) -> str:
    return ", ".join(f"`{k}` {p:.2f}" for k, p in dist[1:1 + n])


def review_document(
    st: Dict[str, Any],   # review_state output
) -> str:  # The markdown the user edits and hands back to --apply
    """The review document: the patterns, then each post's Sections, then the overrides."""
    vocab = st["vocab"]
    roles = " · ".join(f"`{k}` {v.get('description') or ''}" for k, v in vocab.items())
    out = [f"# Role review · {len(st['patterns'])} pattern(s) over "
           f"{sum(len(p['members']) for p in st['patterns'])} section(s) · {len(st['sections'])} section(s) · "
           f"{len(st['overrides'])} override(s)", "",
           "Each row names a role in backticks. A **checked** row confirms the role it names -- edit the name to "
           "choose another; a checked `none` marks the section reviewed with no role. An **unchecked** pattern or "
           "section row is left for a later review; an unchecked **override** keeps the inherited role. A pattern's "
           "role lands on every section it lists. Then `cg-write --notes review-roles --apply <this file>`: the "
           "whole file lands as one journaled batch, or nothing does if a row went stale. Keep each row's "
           "trailing comment.", "", f"_Roles:_ {roles} · `none` no role fits", ""]
    if st["waiting"]:
        out += [f"_{st['waiting']} deeper section(s) wait for an ancestor's role; they are judged against it "
                "in the next review._", ""]
    if st["stale"]:
        out += [f"_⚠ {st['stale']} section(s) have missing or stale judgments and are left out -- run judge-roles._", ""]
    if st["patterns"]:
        out += [f"## Patterns · {len(st['patterns'])}", ""]
        for p in st["patterns"]:
            dissent = f" · {p['dissent']} dissenting, listed under their posts" if p["dissent"] else ""
            out.append(f"- [x] `{p['role']}` · **{p['heading']}** · {len(p['members'])} section(s) in "
                       f"{p['posts']} post(s) · mean {p['mean'][0][1]:.2f} · then {_alts(p['mean'])}{dissent} "
                       f"<!-- role pattern {p['id']} {p['basis']} -->")
        out.append("")
    if st["sections"]:
        out += [f"## Sections · {len(st['sections'])}", ""]
        post = None
        for c in st["sections"]:
            if c["post"] != post:
                post = c["post"]
                out += ["", f"### {st['titles'].get(post) or post[:8]}", ""]
            top, p = c["dist"][0]
            out.append(f"- [x] `{top}` · {'#' * c['level']} {c['heading']} · {p:.2f} · then {_alts(c['dist'])} "
                       f"<!-- role section {c['section']} {c['basis']} -->")
        out.append("")
    if st["overrides"]:
        out += [f"## Overrides · {len(st['overrides'])}", ""]
        post = None
        for o in st["overrides"]:
            if o["post"] != post:
                post = o["post"]
                out += ["", f"### {st['titles'].get(post) or post[:8]}", ""]
            top, p = o["dist"][0]
            out.append(f"- [x] `{top}` · {'#' * o['level']} {o['heading']} · inherits `{o['inherited']}` "
                       f"{dict(o['dist']).get(o['inherited'], 0.0):.2f} · {top} {p:.2f} "
                       f"<!-- role override {o['section']} {o['basis']} -->")
        out.append("")
    return "\n".join(out)


def parse_review(
    text: str,  # An edited review document
) -> Dict[str, Any]:  # {rows: [{checked, role, kind, id, basis}], errors}
    """The document's rows; a repeated row is an error (the file is refused whole)."""
    rows, errors, seen = [], [], set()
    for i, ln in enumerate(text.splitlines(), 1):
        m = _ROW.match(ln)
        if not m:
            if "<!-- role " in ln:
                errors.append(f"line {i}: a row the review cannot read: {ln.strip()[:120]}")
            continue
        row = {"checked": m.group(1) != " ", "role": m.group(2).strip(), "kind": m.group(3),
               "id": m.group(4), "basis": m.group(5), "line": i}
        if (row["kind"], row["id"]) in seen:
            errors.append(f"line {i}: the {row['kind']} {row['id'][:8]} appears twice")
        seen.add((row["kind"], row["id"]))
        rows.append(row)
    return {"rows": rows, "errors": errors}


async def plan_review(
    gx: GraphHandle,
    text: str,   # The edited review document
) -> Dict[str, Any]:  # {run: {confirm, mark}, counts, rows, errors}
    """Check every row against the graph (its basis current, its role live) and plan the batch.
    Any error refuses the whole file."""
    parsed = parse_review(text)
    errors = list(parsed["errors"])
    st = await review_state(gx)
    pats = {p["id"]: p for p in st["patterns"]}
    secs = {c["section"]: c for c in st["sections"]}
    ovs = {o["section"]: o for o in st["overrides"]}
    confirm, mark, touched = [], [], set()
    counts = {"confirmed": 0, "no_role": 0, "kept_inherited": 0, "left": 0}

    def land(c, role, where):
        if c["section"] in touched:
            errors.append(f"{where}: section {c['section'][:8]} is in two rows")
            return
        touched.add(c["section"])
        if role == NONE:
            mark.append([c["section"], c["edge_role"], c["basis"]])
            counts["no_role"] += 1
        else:
            confirm.append([c["section"], role])
            counts["confirmed"] += 1
    for r in parsed["rows"]:
        where = f"line {r['line']}"
        if r["checked"] and r["role"] != NONE and r["role"] not in st["vocab"]:
            errors.append(f"{where}: `{r['role']}` is no live role ({', '.join(st['vocab'])}, or `none`)")
            continue
        if r["kind"] == "pattern":
            p = pats.get(r["id"])
            if p is None or p["basis"] != r["basis"]:
                errors.append(f"{where}: the pattern moved since the document was written (a fresh review document)")
                continue
            if not r["checked"]:
                counts["left"] += len(p["members"])
                continue
            for m in p["members"]:
                land(m, r["role"], where)
            continue
        src = secs if r["kind"] == "section" else ovs
        c = src.get(r["id"])
        if c is None or c["basis"] != r["basis"]:
            errors.append(f"{where}: the {r['kind']} {r['id'][:8]} moved since the document was written "
                          "(judge-roles, then a fresh review document)")
            continue
        if r["kind"] == "override":
            if r["checked"] and r["role"] == NONE:
                errors.append(f"{where}: an override names a role -- uncheck the row to keep `{c['inherited']}`")
                continue
            if not r["checked"] or r["role"] == c["inherited"]:
                touched.add(c["section"])
                mark.append([c["section"], c["edge_role"], c["basis"]])
                counts["kept_inherited"] += 1
                continue
            land(c, r["role"], where)
            continue
        if not r["checked"]:
            counts["left"] += 1
            continue
        land(c, r["role"], where)
    return {"run": {"confirm": confirm, "mark": mark}, "counts": counts, "errors": errors,
            "rows": len(parsed["rows"])}


async def apply_review(
    gx: GraphHandle,
    run: Dict[str, Any],   # {confirm: [[section, role]], mark: [[section, the role its mark rides, basis]], content_hashes?}
    *,
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {asserted, marked, content_hashes}
    """Land one review: assert each role (checked at write time like any vocabulary fact), then
    set each mark on its judged edge. Live and replay share it; replay passes the journaled
    content hashes so the facts re-land the same."""
    from .write import FactBatch, assert_value
    given, hashes = dict(run.get("content_hashes") or {}), {}
    batch = await FactBatch.load(gx)   # one read of the fact layer for the whole review (finding da6cdab6)
    for s, role in run["confirm"]:
        res = await assert_value(gx, s, P.SECTION_ROLE, role, actor=actor, subject_content_hash=given.get(f"{s}|{role}"),
                                 batch=batch)
        if res.get("error"):
            raise RuntimeError(f"section_role {role} on {s}: {res['error']}")
        hashes[f"{s}|{role}"] = res.get("subject_content_hash")
    judged = await load_role_judgments(gx)
    for s, role, basis in run["mark"]:
        edge = (judged.get(s) or {}).get(role)
        if edge is None:
            raise RuntimeError(f"no judged edge {s} -> {role} to mark")
        await graph_task(gx.queue, gx.graph_id, "update_edge", edge_id=edge["_id"], properties={"reviewed": basis})
    return {"asserted": len(run["confirm"]), "marked": len(run["mark"]), "content_hashes": hashes}


async def review_roles(
    gx: GraphHandle,
    *,
    apply_text: Optional[str] = None,   # An edited review document to land; None = write a fresh one
    dry_run: bool = False,              # With apply_text: plan and check only
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {document, counts} | {plan counts, errors, written, run?, applied?}
    """The review verb: the document (with its counts), or one landed review."""
    if apply_text is None:
        st = await review_state(gx)
        counts = {"patterns": len(st["patterns"]), "pattern_sections": sum(len(p["members"]) for p in st["patterns"]),
                  "sections": len(st["sections"]), "overrides": len(st["overrides"]), "waiting": st["waiting"],
                  "stale": st["stale"]}
        return {"document": review_document(st), "counts": counts, "written": False}
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
