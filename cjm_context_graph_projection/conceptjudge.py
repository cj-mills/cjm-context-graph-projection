"""The concept judge (ruling ad37f62a (2), under design amendment 25a58e0e (6) and the path model ae698640).

A CONCEPT is understanding a reader brings to a post or takes from it -- a `concept` Entity with a
description, a not-for line and an area (one about-axis entry: a subject, task or stage, amendment
5c58d521). Which concepts exist is the USER's: a reasoning agent
drafts them and the review confirms them (conceptreview). Which posts teach or assume a concept,
and which Library units cover it, is the user's too; this judge only PROPOSES.

THE SCOPE is the TYPE's (membership belongs to the type, 9d301b5a): a public post whose type's
information_policy declares `concept_relations` is judged for those relations -- TEACHES / ASSUMES
on the post itself; COVERS on the unit or work its notes derive from (its one DERIVED_FROM edge,
the Library's provenance), the notes being the evidence of what the unit covers. A notes post
deriving from no unit or work, or a unit two posts derive from, is REFUSED, never guessed.

THE QUESTION per (source, concept): a post is asked one CHOICE -- teaches / assumes / touches /
unrelated -- with the concept's description and not-for line in the instructions; a unit's notes
one COVERS Noul. A source's questions go in one request over its judged state, blind to each other,
split across requests only when they exceed the model's budget (REQUEST_BUDGET).

THE JUDGED STATE (concept_view) is read from the graph, never the file: the title, the description,
the kind, the outline, every content section's prose in order -- code blocks reduced to a marker,
the code's imports and installs summarized apart (facetjudge.code_signals) -- capped at STATE_MAX.

STORAGE + staleness per (source, concept), the facet judge's: a JUDGED edge per relation the family
proposes (TEACHES and ASSUMES for a post, COVERS for a unit) at or above STORE_FLOOR or on a pair
holding the confirmed relation, carrying p, the model, the concept's criteria hash and the source's
state hash; each judged source's `concepts_judged` record names the state and every judged
concept's criteria hash, the pairs below the floor included. A pair is STALE when its source has no
record, the source's state moved, or the concept's criteria are new or moved. A run is all or
nothing and its op carries it whole, so a rebuild replays it with no network. Only the explicit
verb asks the judge (e09e262b)."""

import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.query import EdgeQuery
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import entity_node_id, series_node_id
from cjm_dev_graph_schema.nodes import judged_edge
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from . import factlayer as F, judgeengine as E
from .facetjudge import _FENCE, _lang, _plain, code_signals
from .runtime import GraphHandle

POLICY = "concept_relations"          # The type's information_policy key: the relations its posts are judged for
POST_RELATIONS = ("TEACHES", "ASSUMES")
UNIT_RELATIONS = ("COVERS",)
UNIT_KINDS = (P.ENTITY_UNIT, P.ENTITY_WORK)   # what a notes post's COVERS lands on
STORE_FLOOR = 0.2      # Judgments below it leave no edge (unless the pair holds the confirmed relation)
THRESHOLD = 0.5        # A judgment at or above it is a proposal (to be tuned on the pilot's review)
SECTION_MAX = 4000     # Characters of one section's prose in a judged state
STATE_MAX = 80000      # Characters of prose in a judged state (~20k tokens; the model's state + one question < 32k)
OUTLINE_MAX = 120      # Headings in a judged state's outline
REQUEST_BUDGET = 56000  # Estimated tokens per request (state + questions; the model's ceiling is 64k)
TOKEN_CHARS = 4        # Characters per token, for the estimate (ran within 6% on the facet judge)

# The post family's Choice: the options' text and the instructions are part of every concept's
# criteria hash, so an edit here re-asks every post pair (the concept's own fields re-ask its own).
OPTIONS = ("teaches", "assumes", "touches", "unrelated")
OPTION_OF = {"TEACHES": "teaches", "ASSUMES": "assumes"}
POST_INSTRUCTIONS = ("How does `post` stand to the concept {name}? {definition} Judge the post as a whole: "
                     "`post.sections` holds its prose in order, with code blocks reduced to markers, and "
                     "`post.code` what its code imports and installs.")
POST_CRITERIA = {
    "teaches": ("The post teaches {name}: it explains it, or walks the reader through doing it, so that a "
                "reader who did not know {name} comes away knowing it."),
    "assumes": ("The post assumes {name}: it relies on it without explaining it, so a reader who does not "
                "already know {name} cannot follow the part of the post that uses it."),
    "touches": ("The post names or uses {name} only in passing: it neither teaches it nor needs the reader to "
                "know it beforehand."),
    "unrelated": "{name} does not figure in the post.",
}
UNIT_INSTRUCTIONS = ("Do the notes in `post` cover the concept {name}: do they record it being explained or "
                     "worked through, so that a reader of the notes learns it? {definition} A concept the "
                     "notes only mention, or rely on without explaining, does not count.")
UNIT_CRITERIA = {"true": "The notes explain or work through {name}.",
                 "false": "The notes only mention {name}, rely on it without explaining it, or do not involve it."}


def definition(concept: Dict[str, Any]) -> str:  # The concept as a question states it
    out = f"The concept: {concept.get('description') or concept['key']}"
    if concept.get("not_for"):
        out += f" Not to be confused with: {concept['not_for']}"
    return out


def concept_question(
    concept: Dict[str, Any],  # A live concept: {key, name, description, not_for}
    family: str,              # post | unit
) -> Dict[str, Any]:  # The Choice (post) or the Noul (unit) one pair is asked
    """One concept's question for a source of the family."""
    name = str(concept.get("name") or concept["key"])
    d = definition(concept)
    if family == "unit":
        return {"type": "noul", "instructions": UNIT_INSTRUCTIONS.format(name=name, definition=d),
                "criteria": {k: v.format(name=name) for k, v in UNIT_CRITERIA.items()}}
    return {"type": "choice", "instructions": POST_INSTRUCTIONS.format(name=name, definition=d),
            "criteria": {k: POST_CRITERIA[k].format(name=name) for k in OPTIONS}}


def criteria_hash(question: Dict[str, Any]) -> str:  # A concept's criteria identity for a family (12 hex)
    return E.digest(question, 12)


def family_of(source_kind: str) -> str:  # post | unit
    return "post" if source_kind == "post" else "unit"


def relations_of(source_kind: str) -> Tuple[str, ...]:  # The relations a source's judgments propose
    return POST_RELATIONS if source_kind == "post" else UNIT_RELATIONS


def section_prose(
    text: str,  # A Section's own text
) -> str:  # Its prose: code blocks as markers, markup and link targets removed, whitespace collapsed
    def marker(m):
        lines = m.group(3).count("\n")
        lang = _lang(m.group(2))
        return f" [code{': ' + lang if lang else ''}, {lines} line(s)] "
    t = _FENCE.sub(marker, text)
    t = re.sub(r"<!--.*?-->|<[^>\n]+>", " ", t, flags=re.S)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    return " ".join(_plain(t).split())


def concept_view(
    title: str,
    description: str,
    kind: str,
    sections: List[Tuple[int, str, str]],  # (level, heading, own text) per content section, in order
) -> Dict[str, Any]:  # One source's judged state -- what its staleness is measured against
    """What the concept judge sees of a post: every section's prose in order, each capped at
    SECTION_MAX, the whole at STATE_MAX -- past it a section keeps its heading and `…`."""
    outline = [("#" * lv + " " + h) for lv, h, _ in sections if h][:OUTLINE_MAX]
    rows, used = [], 0
    for lv, h, text in sections:
        prose = section_prose(text)
        if len(prose) > SECTION_MAX:
            prose = prose[:SECTION_MAX] + " …"
        if used + len(prose) > STATE_MAX:
            prose = "…"
        used += len(prose)
        if prose or h:
            rows.append({"heading": ("#" * lv + " " + h) if h else "", "text": prose})
    return {"title": title, "kind": kind, "description": description, "outline": outline,
            "sections": rows, "code": code_signals([t for _, _, t in sections])}


def record_value(state: str, criteria: Dict[str, str]) -> str:  # A concepts_judged value
    """The canonical JSON of a source's record (key-sorted, compact)."""
    return json.dumps({"criteria": dict(sorted(criteria.items())), "state": state},
                      sort_keys=True, separators=(",", ":"))


def review_basis(state: str, criteria: str) -> str:  # What a review mark was made against
    """A pair's basis: the source's judged state and the concept's criteria. A mark whose basis
    is not the pair's current one is no mark -- the pair is proposed again (eefda2dd (5))."""
    return f"{state}:{criteria}"


def stale_pairs(
    views: Dict[str, Dict[str, Any]],      # {source id: judged state}
    criteria: Dict[str, Dict[str, str]],   # {source id: {concept key: criteria hash}} (each source's family)
    records: Dict[str, Dict[str, Any]],    # {source id: {state, criteria: {key: hash}}}
) -> Dict[str, List[str]]:  # {source id: [stale concept keys, sorted]} -- sources with none left out
    """A pair is stale when its source has no record, the source's state moved, or the record
    holds no criteria hash for the concept or another one."""
    out = {}
    for s, v in sorted(views.items()):
        rec = records.get(s) or {}
        judged = (rec.get("criteria") or {}) if rec.get("state") == E.digest(v) else {}
        stale = sorted(k for k, h in (criteria.get(s) or {}).items() if judged.get(k) != h)
        if stale:
            out[s] = stale
    return out


async def type_relations(
    gx: GraphHandle,
) -> Dict[str, Tuple[str, ...]]:  # {type key: the relations its posts are judged for} -- declaring types only
    """The deliverable types whose information_policy declares `concept_relations`."""
    out = {}
    for n in await F.load_label(gx, DevNodeKinds.DELIVERABLE_TYPE):
        rels = tuple(str(r) for r in (F.prop(n, "information_policy") or {}).get(POLICY) or ())
        if rels:
            out[str(F.prop(n, "key"))] = rels
    return out


async def concept_scope(
    gx: GraphHandle,
) -> Dict[str, Any]:  # {sources: {source id: {source_kind, key, post, node, kind}}, refusals: [{post, title, why}]}
    """The sources the judge asks (module docstring): each public post of a declaring type for
    TEACHES / ASSUMES, and the unit or work each declaring notes post derives from for COVERS."""
    from .purenotes import note_types
    from .site import stated
    posts, types, decl = await E.public_posts(gx), await note_types(gx), await type_relations(gx)
    entities = {str(F.nid(n)): F.props(n) for n in await F.load_label(gx, DevNodeKinds.ENTITY)
                if F.prop(n, "entity_kind") in UNIT_KINDS}
    derives: Dict[str, List[str]] = {}
    for a, b in await F.load_edge_pairs(gx, DevRelations.DERIVED_FROM):
        if str(b) in entities:
            derives.setdefault(str(a), []).append(str(b))
    sources: Dict[str, Dict[str, Any]] = {}
    refusals: List[Dict[str, Any]] = []
    claimed: Dict[str, str] = {}
    for n in sorted(posts):
        rels = decl.get((types.get(n) or {}).get("type") or "") or ()
        node, kind = posts[n]["node"], posts[n]["kind"]
        if any(r in POST_RELATIONS for r in rels):
            sources[n] = {"source_kind": "post", "key": str(F.prop(node, "slug") or n), "post": n,
                          "node": node, "kind": kind}
        if "COVERS" in rels:
            targets = sorted(set(derives.get(n, [])))
            why = ("derives from no Library unit or work" if not targets else
                   "derives from several Library entries" if len(targets) > 1 else
                   f"shares its unit with {claimed[targets[0]][:8]}" if targets[0] in claimed else "")
            if why:
                refusals.append({"post": n, "title": stated(node, "title"), "why": why})
                continue
            t = targets[0]
            claimed[t] = n
            sources[t] = {"source_kind": str(entities[t]["entity_kind"]), "key": str(entities[t]["key"]),
                          "post": n, "node": node, "kind": kind}
    return {"sources": sources, "refusals": refusals}


async def resolve_only(
    gx: GraphHandle,
    sources: Dict[str, Dict[str, Any]],   # concept_scope's sources
    only: Optional[List[str]] = None,     # Post ids / slugs / `unit:key` / `work:key`
    series: Optional[List[str]] = None,   # Series keys: every member post
) -> Tuple[Optional[set], List[str]]:  # (the selected source ids or None for all, refs that name no source)
    """A run's selection: each ref names a source, or a notes post whose source is its unit."""
    if not only and not series:
        return None, []
    by_post = {v["post"]: s for s, v in sources.items()}
    by_slug = {str(F.prop(v["node"], "slug") or ""): s for s, v in sources.items()}
    by_key = {f"{v['source_kind']}:{v['key']}": s for s, v in sources.items() if v["source_kind"] != "post"}
    picked, missing = set(), []
    refs = list(only or [])
    for key in series or []:
        sid = series_node_id(key)
        members = [str(a) for a, b in await F.load_edge_pairs(gx, DevRelations.IN_SERIES) if str(b) == sid]
        if not members:
            missing.append(f"series:{key}")
        refs += members
    for r in refs:
        s = (r if r in sources else by_post.get(r) or by_slug.get(r) or by_key.get(r)
             or next((s for p, s in by_post.items() if len(r) >= 6 and p.startswith(r)), None))
        if s is None:
            missing.append(r)
        else:
            picked.add(s)
    return picked, missing


async def load_concept_vocab(
    gx: GraphHandle,
) -> Dict[str, Dict[str, Any]]:  # {concept key: the live Entity's properties + id}, by key
    """The live (unretired) concepts -- the questions' subjects."""
    rows = {}
    for n in await F.load_label(gx, DevNodeKinds.ENTITY):
        p = F.props(n)
        if p.get("entity_kind") == P.ENTITY_CONCEPT and not p.get("retired"):
            rows[str(p["key"])] = {**p, "id": str(F.nid(n))}
    return dict(sorted(rows.items()))


async def load_concept_views(
    gx: GraphHandle,
    sources: Dict[str, Dict[str, Any]],   # concept_scope's sources
) -> Dict[str, Dict[str, Any]]:  # {source id: judged state}
    """Every source's judged state, from its post's Sections in the graph."""
    from .site import stated
    sections = await E.post_sections(gx)
    out = {}
    for s, v in sources.items():
        rows = [(int(F.prop(x, "level") or 0), " ".join(_plain(str(F.prop(x, "title") or "")).split()),
                 str(F.prop(x, "text") or ""))
                for x in sections.get(v["post"], [])
                if not F.prop(x, "block_role") and not F.prop(x, "retired")]
        rows = [(lv, "" if (h.startswith("_") or not lv) else h, t) for lv, h, t in rows]
        out[s] = concept_view(stated(v["node"], "title"), stated(v["node"], "description"), v["kind"], rows)
    return out


def source_criteria(
    sources: Dict[str, Dict[str, Any]],
    questions: Dict[str, Dict[str, Dict[str, Any]]],   # {family: {concept key: question}}
) -> Dict[str, Dict[str, str]]:  # {source id: {concept key: criteria hash}}
    hashes = {f: {k: criteria_hash(q) for k, q in qs.items()} for f, qs in questions.items()}
    return {s: hashes[family_of(v["source_kind"])] for s, v in sources.items()}


async def load_concept_records(
    gx: GraphHandle,
) -> Dict[str, Dict[str, Any]]:  # {source id: {state, criteria, _value}} -- the active concepts_judged records
    slot = [a for a in await F.load_assertions(gx) if F.prop(a, "predicate") == P.CONCEPTS_JUDGED]
    out = {}
    for a in F.active_assertions(slot, await F.load_supersedes(gx)):
        try:
            raw = str(F.prop(a, "value"))
            out[str(F.prop(a, "subject_id"))] = {**json.loads(raw), "_value": raw}
        except ValueError:   # a record that is not JSON judges nothing: its pairs read stale
            continue
    return out


async def load_concept_judgments(
    gx: GraphHandle,
) -> Dict[Tuple[str, str, str], Dict[str, Any]]:  # {(source, concept Entity id, relation): the edge's judgment + _id}
    """Every stored concept-relation judgment (the JUDGED family proposing TEACHES / ASSUMES / COVERS)."""
    res = await graph_task(gx.queue, gx.graph_id, "query_edges",
                           query=EdgeQuery(relation_type=DevRelations.JUDGED).to_dict())
    out = {}
    for e in getattr(res, "edges", None) or getattr(res, "rows", None) or []:
        e = e.to_dict() if hasattr(e, "to_dict") else dict(e)
        props = e.get("properties") or {}
        if props.get("proposes") in P.JUDGEABLE_RELATIONS:
            out[(str(e["source_id"]), str(e["target_id"]), str(props["proposes"]))] = {**props, "_id": str(e["id"])}
    return out


async def load_confirmed_relations(
    gx: GraphHandle,
) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:  # {(source, concept Entity id): [{relation, strength, _id}]}
    """Every confirmed TEACHES / ASSUMES / COVERS edge onto a concept, per pair."""
    out: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for rel in P.JUDGEABLE_RELATIONS:
        res = await graph_task(gx.queue, gx.graph_id, "query_edges", query=EdgeQuery(relation_type=rel).to_dict())
        for e in getattr(res, "edges", None) or getattr(res, "rows", None) or []:
            e = e.to_dict() if hasattr(e, "to_dict") else dict(e)
            props = e.get("properties") or {}
            out.setdefault((str(e["source_id"]), str(e["target_id"])), []).append(
                {"relation": rel, "strength": props.get("strength") or "", "_id": str(e["id"])})
    return out


def request_body(
    view: Dict[str, Any],                  # The source's judged state
    questions: Dict[str, Dict[str, Any]],  # {concept key: question}
    model: str = E.JUDGE_MODEL,
) -> Dict[str, Any]:  # One System One request
    return {"state": {"post": view}, "model": model, "questions": questions}


def tokens(x: Any) -> int:  # The estimate the budget is held to
    return len(json.dumps(x)) // TOKEN_CHARS


def plan_requests(
    todo: Dict[str, List[str]],                      # {source id: [concept keys to ask]}
    views: Dict[str, Dict[str, Any]],
    questions: Dict[str, Dict[str, Dict[str, Any]]],  # {source id: {concept key: its question}}
    budget: int = REQUEST_BUDGET,
) -> List[Tuple[str, List[str]]]:  # [(source id, the keys one request asks)], in source then key order
    """A source's questions in as few requests as the budget allows -- each over the whole state."""
    out = []
    for s in sorted(todo):
        base, chunk, used = tokens({"post": views[s]}), [], 0
        for k in todo[s]:
            t = tokens(questions[s][k])
            if chunk and base + used + t > budget:
                out.append((s, chunk))
                chunk, used = [], 0
            chunk.append(k)
            used += t
        if chunk:
            out.append((s, chunk))
    return out


def run_concepts(
    requests: List[Tuple[str, List[str]]],
    views: Dict[str, Dict[str, Any]],
    questions: Dict[str, Dict[str, Dict[str, Any]]],  # {source id: {concept key: question}}
    families: Dict[str, str],                         # {source id: post | unit}
    ask: Callable[[Dict[str, Any]], Dict[str, Any]],
    *,
    model: str = E.JUDGE_MODEL,
    workers: int = E.JUDGE_WORKERS,
) -> Dict[str, Any]:  # {judgments: [{source, model, p: {key: {option or covers: p}}}], input_tokens, errors}
    """Ask every request; an answer missing an asked concept (or one of a Choice's options) fails."""
    def read(item, r):
        s, keys = item
        ans = r.get("answers") or {}
        out = {}
        for k in keys:
            a = ans.get(k)
            if a is None:
                raise RuntimeError(f"no answer for concept `{k}`")
            if families[s] == "unit":
                out[k] = {"COVERS": float(a["noul"])}
            else:
                probs = a.get("probabilities") or {}
                if any(o not in probs for o in OPTIONS):
                    raise RuntimeError(f"concept `{k}`: the Choice answered without every option")
                out[k] = {rel: float(probs[opt]) for rel, opt in OPTION_OF.items()}
        return {"source": s, "model": str(r.get("model") or model), "p": out}
    res = E.run_requests(requests, ask, workers=workers, read=read,
                         label=lambda item: {"source": item[0], "concepts": len(item[1])},
                         body_of=lambda item: request_body(views[item[0]], {k: questions[item[0]][k] for k in item[1]},
                                                           model))
    return {"judgments": res["results"], "input_tokens": res["input_tokens"], "errors": res["errors"]}


async def apply_concept_run(
    gx: GraphHandle,
    run: Dict[str, Any],   # {sources: {id: state hash}, asked: {id: [keys]}, criteria: {id: {key: hash}}, judgments, content_hashes?}
    *,
    actor: str = "agent:session",
    write: bool = True,    # False = PLAN only (ruling 24fa72c2): every refusal and the content hashes, nothing written
) -> Dict[str, Any]:  # {deleted, landed, recorded, content_hashes} (+ noop / error when planned)
    """Land one run: each asked pair's standing judgments are replaced by the run's (a source
    judged against a new state loses every standing judgment), a review mark kept only where its
    basis is unchanged, and each judged source's record moves to the state and criteria it was
    judged against. Live and replay share it; replay passes the journaled content hashes. The PLAN
    (write=False) reads the same db, writes nothing, returns a record's refusal as `error` and
    fixes the content hashes the op carries; a run restating the graph is `noop`."""
    from .write import FactBatch, assert_value
    run = E.normalized(run)
    records, standing = await load_concept_records(gx), await load_concept_judgments(gx)
    cid = {k: entity_node_id(P.ENTITY_CONCEPT, k) for ks in run["asked"].values() for k in ks}
    drop = []
    for s, h in run["sources"].items():
        moved = (records.get(s) or {}).get("state") != h
        asked = {cid[k] for k in run["asked"].get(s, [])}
        drop += [j["_id"] for (a, b, _), j in standing.items() if a == s and (moved or b in asked)]
    if drop and write:
        await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=sorted(set(drop)))
    edges = []
    for j in run["judgments"]:
        s, k, rel = j["source"], j["concept"], j["relation"]
        state, crit = run["sources"][s], run["criteria"][s][k]
        edge = judged_edge(s, cid[k], proposes=rel, p=j["p"], model=j["model"], criteria=crit, state=state)
        mark = (standing.get((s, cid[k], rel)) or {}).get("reviewed")
        if mark and mark == review_basis(state, crit):
            edge["properties"]["reviewed"] = mark
        edges.append(edge)
    if edges and write:
        await extend_graph(gx.queue, gx.graph_id, [], edges)
    given, hashes, recorded = dict(run.get("content_hashes") or {}), {}, 0
    batch = await FactBatch.load(gx)
    for s, h in sorted(run["sources"].items()):
        rec = records.get(s) or {}
        kept = dict(rec.get("criteria") or {}) if rec.get("state") == h else {}
        kept.update({k: run["criteria"][s][k] for k in run["asked"].get(s, [])})
        value = record_value(h, kept)
        prior = rec.get("_value")
        if prior == value:
            continue
        res = await assert_value(gx, s, P.CONCEPTS_JUDGED, value, actor=actor,
                                 supersede=[prior] if prior else None, subject_content_hash=given.get(s),
                                 batch=batch, write=write)
        if res.get("error"):
            if not write:
                return {"error": f"concepts_judged on {s}: {res['error']}"}
            raise RuntimeError(f"concepts_judged on {s}: {res['error']}")
        hashes[s] = res.get("subject_content_hash")
        recorded += 1
    out = {"deleted": len(set(drop)), "landed": len(edges), "recorded": recorded, "content_hashes": hashes}
    if not write:
        out["noop"] = not recorded and E.restates(standing, drop, edges)
    return out


async def concept_state(
    gx: GraphHandle,
    *,
    only: Optional[List[str]] = None,
    series: Optional[List[str]] = None,
) -> Dict[str, Any]:  # {scope, sources (selected), views, vocab, questions, criteria, families, missing}
    """The reads a run, a review and a measure share: the selected sources, their states, the
    live vocabulary and each source's questions with their criteria hashes."""
    scope = await concept_scope(gx)
    picked, missing = await resolve_only(gx, scope["sources"], only, series)
    sources = {s: v for s, v in scope["sources"].items() if picked is None or s in picked}
    views, vocab = await load_concept_views(gx, sources), await load_concept_vocab(gx)
    by_family = {f: {k: concept_question(c, f) for k, c in vocab.items()} for f in ("post", "unit")}
    families = {s: family_of(v["source_kind"]) for s, v in sources.items()}
    return {"scope": scope, "sources": sources, "views": views, "vocab": vocab, "families": families,
            "questions": {s: by_family[f] for s, f in families.items()},
            "criteria": source_criteria(sources, by_family), "missing": missing}


async def judge_concepts(
    gx: GraphHandle,
    *,
    only: Optional[List[str]] = None,     # Restrict the run to these sources (module docstring of resolve_only)
    series: Optional[List[str]] = None,   # ... or to every member of these series
    all_pairs: bool = False,              # Re-judge every pair of the selection, not only the stale ones
    dry_run: bool = False,                # Report the stale pairs, the requests and a token estimate; ask nothing
    ask: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,   # The judge (default: HTTP with the key)
    model: str = E.JUDGE_MODEL,
    url: str = E.JUDGE_URL,
    workers: int = E.JUDGE_WORKERS,
    actor: str = "agent:session",
    write: bool = True,   # False = PLAN only (ruling 24fa72c2): the run asked and planned, the op under "op" (None = changes nothing), nothing written
) -> Dict[str, Any]:  # {sources, concepts, requests, pairs, token_estimate, stale, refusals, written, run?, op?, applied?, error?}
    """The concept judge verb: find the stale pairs, ask, land the run. All or nothing -- a run with
    any failed request writes nothing and reports the failures."""
    st = await concept_state(gx, only=only, series=series)
    sources, views, vocab = st["sources"], st["views"], st["vocab"]
    out: Dict[str, Any] = {"scoped": len(st["scope"]["sources"]), "sources": len(sources), "concepts": len(vocab),
                           "refusals": st["scope"]["refusals"], "missing": st["missing"], "written": False}
    if st["missing"]:
        return {**out, "requests": 0, "pairs": 0, "token_estimate": 0,
                "error": f"{len(st['missing'])} selection ref(s) name no source in scope: {', '.join(st['missing'][:5])}"}
    if not vocab:
        return {**out, "requests": 0, "pairs": 0, "token_estimate": 0,
                "error": f"no live {P.ENTITY_CONCEPT} vocabulary -- confirm the drafted concepts first (review-concepts --vocab)"}
    todo = ({s: sorted(vocab) for s in sources} if all_pairs else
            stale_pairs(views, st["criteria"], await load_concept_records(gx)))
    reqs = plan_requests(todo, views, st["questions"])
    est = sum(tokens(request_body(views[s], {k: st["questions"][s][k] for k in ks}, model)) for s, ks in reqs)
    over = [s for s in todo if tokens({"post": views[s]}) + max(tokens(st["questions"][s][k]) for k in todo[s]) > 32000]
    out.update({"requests": len(reqs), "pairs": sum(len(ks) for ks in todo.values()), "token_estimate": est,
                "stale": [{"id": s, "kind": sources[s]["source_kind"], "key": sources[s]["key"],
                           "title": views[s]["title"], "concepts": len(ks)} for s, ks in todo.items()]})
    if over:
        return {**out, "error": f"{len(over)} source state(s) exceed the model's 32k state + question budget: "
                                f"{', '.join(sources[s]['key'] for s in over[:5])}"}
    if dry_run or not todo:
        return out
    ask, why = E.resolve_ask(ask, url)
    if ask is None:
        return {**out, "error": why}
    res = run_concepts(reqs, views, st["questions"], st["families"], ask, model=model, workers=workers)
    out["input_tokens"] = res["input_tokens"]
    if res["errors"]:
        return {**out, "error": f"{len(res['errors'])} of {len(reqs)} requests failed -- nothing written",
                "failures": res["errors"][:5]}
    confirmed = await load_confirmed_relations(gx)
    cids = {k: v["id"] for k, v in vocab.items()}
    held = {(s, cid, r["relation"]) for (s, cid), rs in confirmed.items() for r in rs}
    kept = sorted(({"source": r["source"], "concept": k, "relation": rel, "p": p, "model": r["model"]}
                   for r in res["judgments"] for k, ps in r["p"].items() for rel, p in ps.items()
                   if p >= STORE_FLOOR or (r["source"], cids[k], rel) in held),
                  key=lambda j: (j["source"], j["concept"], j["relation"]))
    run = {"sources": {s: E.digest(views[s]) for s in todo}, "asked": todo,
           "criteria": {s: {k: st["criteria"][s][k] for k in ks} for s, ks in todo.items()},
           "judgments": kept, "requests": len(reqs),
           "models": sorted({r["model"] for r in res["judgments"]})}
    return await E.land_run(gx, out, run, apply_concept_run, actor=actor, write=write)


THRESHOLDS = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8)


async def measure(
    gx: GraphHandle,
    *,
    only: Optional[List[str]] = None,
    series: Optional[List[str]] = None,
    threshold: float = THRESHOLD,
) -> Dict[str, Any]:  # {threshold, sources: [...], agreement: [...], confusions, stale_pairs, reviewed}
    """The pilot's measure (ruling ad37f62a (4)), from the stored judgments and the confirmed
    relations: per source the proposals at the threshold and the relations confirmed; across the
    REVIEWED fresh pairs (a review mark on the current basis, or a confirmed relation), the judge's
    agreement with the user's confirmations at each threshold -- precision (confirmed among the
    proposed) and recall (proposed among the confirmed), per relation; and the TEACHES / ASSUMES
    confusions (the user confirmed the other relation). Stale pairs are counted, never measured."""
    st = await concept_state(gx, only=only, series=series)
    sources, views, vocab = st["sources"], st["views"], st["vocab"]
    records, judged = await load_concept_records(gx), await load_concept_judgments(gx)
    confirmed = await load_confirmed_relations(gx)
    stale = stale_pairs(views, st["criteria"], records)
    by_id = {v["id"]: k for k, v in vocab.items()}
    rows, pairs = [], []
    for s, v in sorted(sources.items(), key=lambda kv: views[kv[0]]["title"].lower()):
        rels = relations_of(v["source_kind"])
        props, conf = {r: 0 for r in rels}, {r: 0 for r in rels}
        for k, c in vocab.items():
            if k in stale.get(s, ()):
                continue
            ps = {r: float((judged.get((s, c["id"], r)) or {}).get("p") or 0.0) for r in rels}
            mine = confirmed.get((s, c["id"])) or []
            got = "+".join(sorted(r["relation"] for r in mine))
            reviewed = bool(mine) or any((judged.get((s, c["id"], r)) or {}).get("reviewed") for r in rels)
            top = max(rels, key=lambda r: ps[r])
            if ps[top] >= threshold:
                props[top] += 1
            if got in conf:
                conf[got] += 1
            if reviewed:
                pairs.append({"source": s, "concept": k, "p": ps, "top": top, "confirmed": got})
        rows.append({"id": s, "kind": v["source_kind"], "key": v["key"], "title": views[s]["title"],
                     "proposals": props, "confirmed": conf, "stale": len(stale.get(s, ()))})
    agreement = []
    for t in THRESHOLDS:
        for rel in P.JUDGEABLE_RELATIONS:
            mine = [p for p in pairs if rel in p["p"]]
            proposed = [p for p in mine if p["top"] == rel and p["p"][rel] >= t]
            conf = [p for p in mine if p["confirmed"] == rel]
            hit = [p for p in proposed if p["confirmed"] == rel]
            if mine:
                agreement.append({"threshold": t, "relation": rel, "proposed": len(proposed), "confirmed": len(conf),
                                  "agreed": len(hit),
                                  "precision": round(len(hit) / len(proposed), 3) if proposed else None,
                                  "recall": round(len([p for p in conf if p in proposed]) / len(conf), 3) if conf else None})
    confusions = {f"{a}->{b}": sum(1 for p in pairs if p["top"] == a and p["confirmed"] == b and p["p"][a] >= threshold)
                  for a in POST_RELATIONS for b in POST_RELATIONS if a != b}
    return {"threshold": threshold, "sources": rows, "agreement": agreement, "confusions": confusions,
            "reviewed": len(pairs), "concepts": len(vocab), "stale_pairs": sum(len(v) for v in stale.values()),
            "refusals": st["scope"]["refusals"]}


async def concepts_read(
    gx: GraphHandle,
) -> Dict[str, Any]:  # {concepts: [{key, name, area, description, not_for, relations: {REL: n}}], areas}
    """The live concept vocabulary with each concept's confirmed relation counts -- the read the
    drafting agents are handed (so a match reuses a key) and the user browses."""
    vocab = await load_concept_vocab(gx)
    confirmed = await load_confirmed_relations(gx)
    counts: Dict[str, Dict[str, int]] = {}
    for (_, cid), rs in confirmed.items():
        for r in rs:
            counts.setdefault(cid, {}).setdefault(r["relation"], 0)
            counts[cid][r["relation"]] += 1
    kinds = (P.ENTITY_SUBJECT, P.ENTITY_TASK, P.ENTITY_STAGE)   # the about axes a concept sits in (5c58d521)
    areas = {f"{F.prop(n, 'entity_kind')}:{F.prop(n, 'key')}": str(F.prop(n, "name") or F.prop(n, "key"))
             for n in await F.load_label(gx, DevNodeKinds.ENTITY)
             if F.prop(n, "entity_kind") in kinds and not F.prop(n, "retired")}
    return {"concepts": [{"key": k, "name": c.get("name"), "area": c.get("area"),
                          "description": c.get("description"), "not_for": c.get("not_for"),
                          "relations": counts.get(c["id"], {})} for k, c in vocab.items()],
            "areas": dict(sorted(areas.items()))}
