"""The concept reviews (ruling ad37f62a (1), (2)): the vocabulary the drafts propose, and the
relations the judge proposes. The user confirms both; nothing a model said lands without them.

THE VOCABULARY REVIEW. A reasoning agent drafts concepts per post (the draft pass): JSON under the
private repo, each {source, title, concepts: [{key, name, description, not_for, area, relation,
where}]}. The drafts are an artifact, never a stored judgment: `review-concepts --vocab --drafts D
--out DOC` merges them by key into a markdown document the user edits -- one block per NEW concept,
checked (confirm), its name in the head and its description / not-for / area (one about-axis
entry: subject:KEY | task:KEY | stage:KEY, design amendment 5c58d521) as editable fields, the
posts that drafted it and as what beneath; a drafted key already live is listed as a reuse,
never re-minted; a subject no live entry names opens a NEW SUBJECT block, unchecked, since a
subject is a facet entry too and confirming one adds a category. `--vocab --apply DOC` lands the
checked records as one entity batch (coverage.mint_entities: checked whole, refused whole, one
journaled `entity` op per record, so replay needs nothing new).

THE RELATION REVIEW (the facetreview shape, grouped by source): a fresh pair whose top judged
relation clears the threshold, with no confirmed relation and no mark on its basis, is a PROPOSAL
(checked; its relation word is editable -- `teaches`, `assumes`, `assumes-recommended`); a confirmed
relation whose judgment fell below the threshold is CHALLENGED (checked = keep, cleared = retract);
a pair from the store floor up is a NEAR MISS (unchecked). `--apply DOC` lands ONE journaled
`review-concepts` op -- the retractions, then the confirmations (record_relation, the relate verb's
write path), then a review mark on every judged edge of each reviewed pair -- or refuses the whole
file when a row's basis moved. A pair holds at most one of TEACHES / ASSUMES."""

import re
from typing import Any, Dict, List, Optional

from cjm_context_graph_layer.ops import graph_task
from cjm_dev_graph_schema import predicates as P

from . import factlayer as F, judgeengine as E
from .conceptjudge import (concept_state, load_concept_judgments, load_concept_records,
                           load_confirmed_relations, POST_RELATIONS, relations_of, review_basis,
                           stale_pairs, STORE_FLOOR, THRESHOLD)
from .runtime import GraphHandle

# ------------------------------------------------------------------------------------------------
# THE VOCABULARY REVIEW

VOCAB_FIELDS = {"concept": ("description", "not_for", "area"), "subject": ("description", "not_for")}
AREA_KINDS = (P.ENTITY_SUBJECT, P.ENTITY_TASK, P.ENTITY_STAGE)   # a concept's area: one about-axis entry (5c58d521)
_VHEAD = re.compile(r"^- \[([ xX])\] (concept|subject) `([^`]+)` · (.*?)\s*<!-- vocab (concept|subject) -->\s*$")
_VFIELD = re.compile(r"^\s+- (description|not_for|area): ?(.*)$")
RELATION_WORDS = ("teaches", "assumes", "touches", "covers")


def merge_drafts(
    drafts: List[Dict[str, Any]],   # The draft pass's files: [{source, title, concepts: [...]}], in drafting order
) -> Dict[str, Any]:  # {concepts: {key: {fields..., drafted: [{title, relation, where}], alts: [...]}}, errors}
    """The drafts by key: the first draft of a key gives its fields, a later differing description
    is kept as an alternative for the user to choose from; every drafting post is listed."""
    out: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []
    for d in drafts:
        title = str(d.get("title") or d.get("source") or "?")
        for i, c in enumerate(d.get("concepts") or []):
            key = str(c.get("key") or "").strip()
            if not key or " " in key:
                errors.append(f"{title}: concept {i + 1} has no usable key ({key!r})")
                continue
            rel = str(c.get("relation") or "").lower()
            if rel and rel not in RELATION_WORDS:
                errors.append(f"{title}: `{key}` has relation {rel!r} ({', '.join(RELATION_WORDS)})")
            row = out.setdefault(key, {"key": key, "name": str(c.get("name") or key),
                                       "description": str(c.get("description") or "").strip(),
                                       "not_for": str(c.get("not_for") or "").strip(),
                                       "area": draft_area(c),
                                       "subject_new": c.get("subject_new") or None,
                                       "drafted": [], "alts": []})
            desc = str(c.get("description") or "").strip()
            if desc and desc != row["description"] and desc not in row["alts"]:
                row["alts"].append(desc)
            if not row.get("subject_new") and c.get("subject_new"):
                row["subject_new"] = c["subject_new"]
            row["drafted"].append({"title": title, "relation": rel, "where": str(c.get("where") or "")})
    return {"concepts": out, "errors": errors}


async def live_entities(
    gx: GraphHandle,
    kind: str,   # An Entity sub-kind
) -> Dict[str, Dict[str, Any]]:  # {key: properties} -- live (unretired) entries of the kind
    return {str(F.prop(n, "key")): F.props(n) for n in await F.load_label(gx, "Entity")
            if F.prop(n, "entity_kind") == kind and not F.prop(n, "retired")}


def draft_area(
    concept: Dict[str, Any],  # One drafted concept
) -> str:  # Its area as a typed reference (design amendment 5c58d521 (2)), or ""
    """A draft names its area typed (`area: stage:export`), or -- as the pilot's first drafts did,
    before the amendment -- a bare subject key (`subject: gpu-programming`)."""
    a = str(concept.get("area") or "").strip()
    if a:
        return a
    s = str(concept.get("subject") or "").strip()
    return f"{P.ENTITY_SUBJECT}:{s}" if s else ""


async def vocab_document(
    gx: GraphHandle,
    drafts: List[Dict[str, Any]],
) -> Dict[str, Any]:  # {document, counts, errors}
    """The vocabulary review document (module docstring), grouped by area."""
    merged = merge_drafts(drafts)
    concepts, subjects = await live_entities(gx, P.ENTITY_CONCEPT), await live_entities(gx, P.ENTITY_SUBJECT)
    areas = {f"{k}:{key}": str(v.get("name") or key) for k in AREA_KINDS
             for key, v in (await live_entities(gx, k)).items()}
    new = {k: v for k, v in merged["concepts"].items() if k not in concepts}
    reused = {k: v for k, v in merged["concepts"].items() if k in concepts}
    new_subjects: Dict[str, Dict[str, Any]] = {}
    for k, v in new.items():
        kind, _, s = v["area"].partition(":")
        if kind == P.ENTITY_SUBJECT and s and s not in subjects:
            row = new_subjects.setdefault(s, {"name": s, "description": "", "not_for": "", "for": []})
            sn = v.get("subject_new") or {}
            row["name"] = row["name"] if row["name"] != s else str(sn.get("name") or s)
            row["description"] = row["description"] or str(sn.get("description") or "")
            row["not_for"] = row["not_for"] or str(sn.get("not_for") or "")
            row["for"].append(k)
    counts = {"new": len(new), "reused": len(reused), "new_subjects": len(new_subjects),
              "drafts": len(drafts), "drafted": sum(len(d.get("concepts") or []) for d in drafts)}
    out = [f"# Concept vocabulary review · {counts['new']} new concept(s) · {counts['reused']} reuse(s) of live "
           f"concepts · {counts['new_subjects']} new subject(s) · from {counts['drafts']} draft(s)", "",
           "Each block is one drafted concept. A **concept** is checked: leave `[x]` to confirm it, clear it to "
           "drop it; edit its name (after the `·`), its key (in backticks) or any field line in place. Every "
           "concept names its `area` -- one about-axis entry, `subject:KEY`, `task:KEY` or `stage:KEY` (design "
           "amendment 5c58d521): a task when the concept only makes sense within one task, a stage when it belongs "
           "to one project phase across tasks, otherwise a subject. A subject no live entry names is a **new "
           "subject** block below, unchecked -- check it to mint it (a subject is a category facet too), or point "
           "its concepts at a live entry instead. `alt:`, `moved:` and `drafted:` lines are context and are not "
           "read. Then `cg-write --notes review-concepts --vocab --apply <this file>`: the checked blocks land as "
           "one batch, or nothing does.", ""]
    if new_subjects:
        out += ["## New subjects", ""]
        for s, v in sorted(new_subjects.items()):
            out += [f"- [ ] subject `{s}` · {v['name']} <!-- vocab subject -->",
                    f"  - description: {v['description']}", f"  - not_for: {v['not_for']}",
                    f"  - drafted for: {', '.join(sorted(v['for']))}", ""]
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for v in new.values():
        groups.setdefault(v["area"], []).append(v)

    def gname(a):
        s = a.partition(":")[2]
        return str(areas.get(a) or (new_subjects.get(s) or {}).get("name") or a or "(none)")

    def tag(a):
        return ("" if a in areas else " (new subject)" if a.startswith(f"{P.ENTITY_SUBJECT}:") and
                a.partition(":")[2] in new_subjects else " (no such entry)")
    for a in sorted(groups, key=lambda a: (a not in areas, AREA_KINDS.index(a.partition(":")[0])
                                           if a.partition(":")[0] in AREA_KINDS else 9, gname(a).lower())):
        out += [f"## {gname(a)} `{a or '-'}`{tag(a)}", ""]
        for v in sorted(groups[a], key=lambda v: v["name"].lower()):
            out += [f"- [x] concept `{v['key']}` · {v['name']} <!-- vocab concept -->",
                    f"  - description: {v['description']}", f"  - not_for: {v['not_for']}",
                    f"  - area: {v['area']}"]
            out += [f"  - alt: {x}" for x in v["alts"]]
            out.append("  - drafted: " + "; ".join(
                f"{d['title']} ({d['relation'] or '?'}{' · ' + d['where'] if d['where'] else ''})" for d in v["drafted"]))
            out.append("")
    if reused:
        out += ["## Reused live concepts", "", "_Drafted again under a live key -- nothing to confirm; the relation "
                "review decides the pairs._", ""]
        for k, v in sorted(reused.items()):
            out.append(f"- `{k}` · " + "; ".join(f"{d['title']} ({d['relation'] or '?'})" for d in v["drafted"]))
        out.append("")
    return {"document": "\n".join(out), "counts": counts, "errors": merged["errors"]}


def parse_vocab(
    text: str,  # An edited vocabulary review document
) -> Dict[str, Any]:  # {records: [{checked, kind, key, name, fields}], errors}
    """The document's blocks: each head line with the field lines beneath it."""
    records, errors, cur = [], [], None
    for i, ln in enumerate(text.splitlines(), 1):
        m = _VHEAD.match(ln)
        if m:
            if m.group(2) != m.group(5):
                errors.append(f"line {i}: the block's kind and its comment disagree")
            cur = {"checked": m.group(1) != " ", "kind": m.group(2), "key": m.group(3).strip(),
                   "name": m.group(4).strip(), "fields": {}, "line": i}
            records.append(cur)
            continue
        if "<!-- vocab " in ln:
            errors.append(f"line {i}: a block head the review cannot read: {ln.strip()[:120]}")
            cur = None
            continue
        f = _VFIELD.match(ln)
        if f and cur is not None and f.group(1) in VOCAB_FIELDS[cur["kind"]]:
            cur["fields"][f.group(1)] = f.group(2).strip()
        elif ln.strip() and not ln.startswith(" "):
            cur = None
    seen = set()
    for r in records:
        if (r["kind"], r["key"]) in seen:
            errors.append(f"line {r['line']}: {r['kind']} `{r['key']}` appears twice")
        seen.add((r["kind"], r["key"]))
    return {"records": records, "errors": errors}


async def apply_vocab(
    gx: GraphHandle,
    text: str,
    *,
    dry_run: bool = False,
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {counts, errors, plan, landed, written}
    """Land the checked blocks as one entity batch (subjects first, so a concept may name a subject
    the same file confirms); any error refuses the whole file."""
    from .coverage import mint_entities
    parsed = parse_vocab(text)
    errors = list(parsed["errors"])
    subjects = await live_entities(gx, P.ENTITY_SUBJECT)
    chosen = [r for r in parsed["records"] if r["checked"]]
    confirmed_subjects = {r["key"] for r in chosen if r["kind"] == "subject"}
    records = []
    for r in sorted(chosen, key=lambda r: (r["kind"] != "subject", r["line"])):
        fields = {k: v for k, v in r["fields"].items() if v}
        if r["kind"] == "concept":
            kind, _, s = fields.get("area", "").partition(":")
            if kind == P.ENTITY_SUBJECT and s not in subjects and s not in confirmed_subjects:
                errors.append(f"line {r['line']}: concept `{r['key']}` sits in subject `{s}`, neither live nor "
                              "confirmed in this file")
        records.append({"kind": P.ENTITY_CONCEPT if r["kind"] == "concept" else P.ENTITY_SUBJECT,
                        "key": r["key"], "name": r["name"], "fields": fields})
    counts = {"concepts": sum(1 for r in chosen if r["kind"] == "concept"),
              "subjects": len(confirmed_subjects),
              "dropped": sum(1 for r in parsed["records"] if not r["checked"] and r["kind"] == "concept")}
    if errors:
        return {"counts": counts, "errors": errors, "plan": [], "landed": [], "written": False,
                "error": f"{len(errors)} error(s) -- nothing written"}
    res = await mint_entities(gx, records, apply=not dry_run, actor=actor)
    if res["errors"]:
        return {"counts": counts, "errors": res["errors"], "plan": res["plan"], "landed": [], "written": False,
                "error": f"{len(res['errors'])} record(s) refused -- nothing written"}
    return {"counts": counts, "errors": [], "plan": res["plan"], "landed": res["landed"], "written": res["written"]}


# ------------------------------------------------------------------------------------------------
# THE RELATION REVIEW

SECTIONS = ("proposal", "challenged", "near")
_ROW = re.compile(r"^\s*- \[([ xX])\] `([\w-]+)` .*<!-- crel (proposal|challenged|near) (\S+) (\S+) (\S+) -->\s*$")
WORDS = {"teaches": ("TEACHES", ""), "assumes": ("ASSUMES", ""), "assumes-recommended": ("ASSUMES", "recommended"),
         "covers": ("COVERS", "")}


def word_of(relation: str, strength: str = "") -> str:  # The document's word for a relation
    return relation.lower() + ("-recommended" if strength == "recommended" else "")


async def relation_state(
    gx: GraphHandle,
    *,
    only: Optional[List[str]] = None,
    series: Optional[List[str]] = None,
    threshold: float = THRESHOLD,
) -> Dict[str, Any]:  # {rows, stale, st, bases, judged, confirmed}
    """Every fresh pair's review row (module docstring), the stale pairs apart, and the reads the
    apply reuses. `bases` = {(source, concept key): the pair's current basis}."""
    st = await concept_state(gx, only=only, series=series)
    records, judged, confirmed = (await load_concept_records(gx), await load_concept_judgments(gx),
                                  await load_confirmed_relations(gx))
    stale = stale_pairs(st["views"], st["criteria"], records)
    rows, bases, reviewed = [], {}, set()
    for s, v in st["sources"].items():
        rels = relations_of(v["source_kind"])
        for k, c in st["vocab"].items():
            if k in stale.get(s, ()) or s not in records:
                continue
            basis = review_basis(records[s]["state"], st["criteria"][s][k])
            bases[(s, k)] = basis
            edges = {r: judged.get((s, c["id"], r)) or {} for r in rels}
            if any(e.get("reviewed") == basis for e in edges.values()):
                reviewed.add((s, k))
                continue
            ps = {r: round(float(e.get("p") or 0.0), 3) for r, e in edges.items()}
            mine = [x for x in confirmed.get((s, c["id"])) or [] if x["relation"] in rels]
            base = {"source": s, "concept": k, "p": ps, "basis": basis, "title": st["views"][s]["title"],
                    "name": str(c.get("name") or k), "kind": v["source_kind"]}
            if mine:
                rows += [{**base, "section": "challenged", "relation": x["relation"], "strength": x["strength"]}
                         for x in mine if ps[x["relation"]] < threshold]
                continue
            top = max(rels, key=lambda r: (ps[r], r))
            section = "proposal" if ps[top] >= threshold else "near" if ps[top] >= STORE_FLOOR else None
            if section:
                rows.append({**base, "section": section, "relation": top, "strength": ""})
    return {"rows": rows, "stale": stale, "st": st, "bases": bases, "reviewed": reviewed, "judged": judged,
            "confirmed": confirmed}


def relation_document(
    rows: List[Dict[str, Any]],
    threshold: float = THRESHOLD,
) -> str:  # The markdown the user edits and hands back to --apply
    """The relation review document, grouped by source, each source's rows by p."""
    count = {s: sum(1 for r in rows if r["section"] == s) for s in SECTIONS}
    out = [f"# Concept relation review · threshold {threshold} · {count['proposal']} proposal(s) · "
           f"{count['challenged']} challenged · {count['near']} near miss(es)", "",
           "Each row is one (source, concept) pair with the judge's probabilities (T = teaches, A = assumes, "
           "C = covers). A **proposal** is checked: leave `[x]` to confirm it, clear it to reject it (marked "
           "reviewed, not proposed again until the post or the concept changes); edit the word in backticks to "
           "confirm another relation (`teaches`, `assumes`, `assumes-recommended`; a unit's notes take `covers`). "
           "A **challenged** relation is checked: leave it to keep it, clear it to retract it. A **near miss** "
           "(folded) is unchecked: check it to confirm. Then `cg-write --notes review-concepts --apply <this "
           "file>`: one journaled batch, or nothing if a row went stale. Keep each row's trailing comment.", ""]
    by: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(r["source"], []).append(r)

    def line(r, box):
        ps = " · ".join(f"{rel[0]} {p:.2f}" for rel, p in sorted(r["p"].items(), key=lambda kv: kv[0] != "TEACHES"))
        return (f"- [{box}] `{word_of(r['relation'], r['strength'])}` · {ps} · **{r['name']}** `{r['concept']}` "
                f"<!-- crel {r['section']} {r['source']} {r['concept']} {r['basis']} -->")
    for s in sorted(by, key=lambda s: (by[s][0]["title"].lower(), s)):
        mine = sorted(by[s], key=lambda r: (-max(r["p"].values()), r["concept"]))
        lead = [r for r in mine if r["section"] != "near"]
        near = [r for r in mine if r["section"] == "near"]
        out += [f"## {mine[0]['title']} _{mine[0]['kind']}_ · {len(lead)}", ""]
        out += [line(r, "x") for r in lead]
        if near:
            out += ["", f"<details><summary>near misses · {len(near)}</summary>", ""]
            out += [line(r, " ") for r in near]
            out += ["", "</details>"]
        out.append("")
    return "\n".join(out)


def parse_relations(
    text: str,
) -> Dict[str, Any]:  # {rows: [{checked, word, section, source, concept, basis}], errors}
    rows, errors, seen = [], [], set()
    for i, ln in enumerate(text.splitlines(), 1):
        m = _ROW.match(ln)
        if not m:
            if "<!-- crel " in ln:
                errors.append(f"line {i}: a row the review cannot read: {ln.strip()[:120]}")
            continue
        row = {"checked": m.group(1) != " ", "word": m.group(2), "section": m.group(3), "source": m.group(4),
               "concept": m.group(5), "basis": m.group(6), "line": i}
        key = (row["source"], row["concept"], row["section"] == "challenged" and row["word"])
        if key in seen:
            errors.append(f"line {i}: the pair {row['source'][:8]} {row['concept']} appears twice")
        seen.add(key)
        rows.append(row)
    return {"rows": rows, "errors": errors}


async def plan_relations(
    gx: GraphHandle,
    text: str,
    *,
    only: Optional[List[str]] = None,
    series: Optional[List[str]] = None,
) -> Dict[str, Any]:  # {run: {retract, confirm, mark}, counts, errors, rows}
    """Check every row against the graph and plan the batch (module docstring); any error refuses."""
    parsed = parse_relations(text)
    errors = list(parsed["errors"])
    rs = await relation_state(gx, only=only, series=series)
    st = rs["st"]
    retract, confirm, mark = [], [], {}
    counts = {"confirmed": 0, "rejected": 0, "kept": 0, "retracted": 0, "near_unchecked": 0, "applied": 0}
    for r in parsed["rows"]:
        s, k = r["source"], r["concept"]
        src, c = st["sources"].get(s), st["vocab"].get(k)
        where = f"line {r['line']} ({s[:8]} {k})"
        if src is None or c is None:
            errors.append(f"{where}: no source in scope or no live concept")
            continue
        if rs["bases"].get((s, k)) != r["basis"]:
            errors.append(f"{where}: the pair's basis moved since the document was written "
                          "(judge-concepts, then a fresh review document)")
            continue
        if r["word"] not in WORDS:
            errors.append(f"{where}: `{r['word']}` is no relation word ({', '.join(WORDS)})")
            continue
        rel, strength = WORDS[r["word"]]
        if rel not in relations_of(src["source_kind"]):
            errors.append(f"{where}: a {src['source_kind']} source takes {', '.join(relations_of(src['source_kind']))}, "
                          f"never {rel}")
            continue
        standing = [x for x in rs["confirmed"].get((s, c["id"])) or []]
        if (s, k) in rs["reviewed"]:
            # Reviewed on this basis: a row whose outcome stands was landed by this document -- a
            # crashed --apply's retry is no change (ruling 24fa72c2 (2)); any other is refused
            held = {x["relation"]: x["strength"] or "required" for x in standing}
            if r["section"] == "challenged":
                landed = (rel in held) == r["checked"]
            else:
                landed = not r["checked"] or held.get(rel) == (strength or "required")
            if landed:
                counts["applied"] += 1
                continue
            errors.append(f"{where}: already reviewed on this basis -- write a fresh review document")
            continue
        if r["section"] == "challenged":
            if rel not in {x["relation"] for x in standing}:
                errors.append(f"{where}: a challenged row keeps or retracts its relation -- its word cannot change")
                continue
            if r["checked"]:
                counts["kept"] += 1
            else:
                retract.append([s, rel, k])
                counts["retracted"] += 1
        elif r["section"] == "near" and not r["checked"]:
            counts["near_unchecked"] += 1
            continue
        elif not r["checked"]:
            counts["rejected"] += 1
        else:
            confirm.append([s, rel, k, strength])
            counts["confirmed"] += 1
        mark[(s, c["id"])] = r["basis"]
    gone = {(s, rel, k) for s, rel, k in retract}
    for s, rel, k, _ in confirm:
        cid = st["vocab"][k]["id"]
        left = {x["relation"] for x in rs["confirmed"].get((s, cid)) or [] if (s, x["relation"], k) not in gone}
        left |= {r2 for s2, r2, k2, _ in confirm if (s2, k2) == (s, k)}
        if rel in POST_RELATIONS and set(POST_RELATIONS) <= left:
            errors.append(f"{s[:8]} {k}: a pair holds at most one of {' / '.join(POST_RELATIONS)} -- retract the "
                          "other in the same file")
    run = {"retract": retract, "confirm": confirm, "mark": [[s, cid, b] for (s, cid), b in sorted(mark.items())]}
    return {"run": run, "counts": counts, "errors": errors, "rows": len(parsed["rows"])}


async def apply_relations(
    gx: GraphHandle,
    run: Dict[str, Any],   # {retract: [[source, REL, key]], confirm: [[source, REL, key, strength]], mark: [[source, concept id, basis]]}
    *,
    actor: str = "agent:session",
    write: bool = True,    # False = PLAN only (ruling 24fa72c2): every refusal, nothing written
) -> Dict[str, Any]:  # {retracted, related, marked} (+ noop / error when planned)
    """Land one relation review: the retractions, the confirmations (record_relation, checked at
    write time like any relate), then the marks on every judged edge of each reviewed pair. Live
    and replay share it. The PLAN (write=False) runs each relation's own plan -- a refusal returns
    as `error` before anything lands -- and a review whose every relation and mark already stands
    is `noop`. A retraction then a confirmation of one edge plans the retraction as a change, so
    the confirmation's plan against the edge still standing never hides the run."""
    from .paths import record_relation
    changes = 0
    for s, rel, k in run["retract"]:
        res = await record_relation(gx, rel, s, f"{P.ENTITY_CONCEPT}:{k}", retract=True, actor=actor, write=write)
        if res.get("error"):
            if not write:
                return {"error": f"retract {rel} {s} -> {k}: {res['error']}"}
            raise RuntimeError(f"retract {rel} {s} -> {k}: {res['error']}")
        changes += write or res.get("op") is not None
    for s, rel, k, strength in run["confirm"]:
        res = await record_relation(gx, rel, s, f"{P.ENTITY_CONCEPT}:{k}", strength=strength, actor=actor,
                                    write=write)
        if res.get("error"):
            if not write:
                return {"error": f"{rel} {s} -> {k}: {res['error']}"}
            raise RuntimeError(f"{rel} {s} -> {k}: {res['error']}")
        changes += write or res.get("op") is not None
    judged = await load_concept_judgments(gx)
    marked = 0
    for s, cid, basis in run["mark"]:
        for (a, b, _), e in judged.items():
            if (a, b) == (s, cid):
                if write:
                    await graph_task(gx.queue, gx.graph_id, "update_edge", edge_id=e["_id"], properties={"reviewed": basis})
                changes += write or e.get("reviewed") != basis
                marked += 1
    out = {"retracted": len(run["retract"]), "related": len(run["confirm"]), "marked": marked}
    if not write:
        out["noop"] = not changes
    return out


async def review_concepts(
    gx: GraphHandle,
    *,
    vocab: bool = False,                          # The vocabulary review (else the relation review)
    drafts: Optional[List[Dict[str, Any]]] = None,  # With vocab and no apply_text: the draft pass's files
    apply_text: Optional[str] = None,             # An edited document to land; None = write a fresh one
    dry_run: bool = False,
    only: Optional[List[str]] = None,
    series: Optional[List[str]] = None,
    actor: str = "agent:session",
    write: bool = True,   # False = PLAN only (ruling 24fa72c2): a relation review's op under "op" (None = changes nothing), nothing written
) -> Dict[str, Any]:
    """The review verb: either review's document, or one landed (or refused) review."""
    if vocab:
        if apply_text is None:
            return {"vocab": True, **(await vocab_document(gx, drafts or [])), "written": False}
        return {"vocab": True, **(await apply_vocab(gx, apply_text, dry_run=dry_run, actor=actor))}
    if apply_text is None:
        rs = await relation_state(gx, only=only, series=series)
        counts = {s: sum(1 for r in rs["rows"] if r["section"] == s) for s in SECTIONS}
        return {"document": relation_document(rs["rows"]), "counts": counts,
                "sources": len({r["source"] for r in rs["rows"]}),
                "stale_pairs": sum(len(v) for v in rs["stale"].values()), "written": False}
    plan = await plan_relations(gx, apply_text, only=only, series=series)
    out = {"counts": plan["counts"], "rows": plan["rows"], "errors": plan["errors"], "written": False}
    if plan["errors"]:
        return {**out, "error": f"{len(plan['errors'])} row(s) refused -- nothing written"}
    run = plan["run"]
    if dry_run or not (run["retract"] or run["confirm"] or run["mark"]):
        return out if dry_run else {**out, "noop": True}   # an empty review lands nothing
    return await E.land_run(gx, out, run, apply_relations, actor=actor, write=write)
