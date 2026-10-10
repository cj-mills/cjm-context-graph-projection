"""Section roles (design ad9bef5a (1), amendment 25a58e0e (2)-(4), ruling 6a203252).

A Section's ROLE is what it does for its reader -- intro, setup, step, rationale, how-it-works,
background, conclusion: the keys of the live `section_role` vocabulary Entities, whose
description and not-for line are the judge's criteria. The SCOPE is the TYPE's: a public
deliverable whose type's presentation_policy declares a `section_roles` map (role -> placement)
carries roles -- the archive tutorials and logs, every role placed in the body, so the archive
renders as authored.

STORED: the `section_role` FACTS the user confirmed -- one on each H2 / H3 Section
(CANDIDATE_LEVELS), and an OVERRIDE on a deeper Section whose content disagrees with what it
inherits. DERIVED at read: a deeper Section's inherited role (its nearest ancestor's along
PART_OF), the heading patterns the review groups by, and the judge's proposals.

THE JUDGE (judgeengine, Jev) is asked by `judge-roles` and nothing else (e09e262b): one request
per Section with one CHOICE over the live roles and `none` (no role fits). Its judged state is
the post's title, kind and outline and the Section's heading path, level, sub-headings and text.
Every role's probability lands as a JUDGED edge (Section -> role Entity, proposes `section_role`)
carrying the model, the criteria hash -- the whole question, since a Choice's options are judged
together, so editing one role's criteria re-asks every Section -- and the state hash; `none` is
what the stored roles leave of 1. A Section is STALE when an edge is missing or its state or
criteria moved. A run is all or nothing and its op carries every answer, so a rebuild replays it
without calling anyone."""

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from cjm_context_graph_layer.grammar import SpineRelations
from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.query import EdgeQuery
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import entity_node_id
from cjm_dev_graph_schema.nodes import judged_edge
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from . import factlayer as F, judgeengine as E
from .runtime import GraphHandle

ROLE_POLICY = "section_roles"   # The type's presentation_policy key: {role: placement}
PLACEMENTS = ("body",)          # The placements a role map may name (the archive renders as authored)
CANDIDATE_LEVELS = (2, 3)       # The levels that carry their own role; a deeper Section inherits
PATTERN_MIN = 2                 # Posts sharing a heading for the review to confirm it once (25a58e0e (4))
NONE = "none"                   # The Choice's no-match option -- never a role, never a fact
TEXT_MAX = 2400                 # Characters of a Section's own text in its judged state
OUTLINE_MAX = 80                # Headings of the post's outline in a judged state
SUBHEADS_MAX = 20               # Child headings in a judged state

# The one question (ruling 6a203252 (2)): its text is part of the criteria hash with every
# option's, so editing it re-asks every Section.
INSTRUCTIONS = ("What does the section `section` do for the reader of `post`? Judge the section as a "
                "whole, in its place in the post's outline (`post.outline`): `section.heading_path` "
                "names the sections it sits under and `section.subheadings` the ones under it, so a "
                "section whose own text is short may be a container for them. Pick `none` only when "
                "no role describes it.")
NONE_CRITERIA = ("No role in the list describes the section: navigation to other posts, a list of "
                 "links, a changelog or update note, or anything else outside the roles.")


def heading(title: str) -> str:  # A Section's heading as the page states it, links reduced to text
    from .facetjudge import _plain
    return " ".join(_plain(str(title or "")).split())


def pattern_key(title: str) -> str:  # The key the review groups headings by: case and spacing folded
    return heading(title).lower()


async def load_role_vocab(
    gx: GraphHandle,
) -> Dict[str, Dict[str, Any]]:  # {role key: the live Entity's properties + id} in position order
    """The live (unretired) `section_role` entries -- the Choice's options."""
    rows = []
    for n in await F.load_label(gx, DevNodeKinds.ENTITY):
        p = F.props(n)
        if p.get("entity_kind") == P.ENTITY_SECTION_ROLE and not p.get("retired"):
            rows.append((p.get("position") if p.get("position") is not None else 1 << 30, str(p["key"]),
                         {**p, "id": str(F.nid(n))}))
    return {r[1]: r[2] for r in sorted(rows, key=lambda r: r[:2])}


def role_question(
    vocab: Dict[str, Dict[str, Any]],  # load_role_vocab output
) -> Dict[str, Any]:  # The Choice every Section is asked
    """One Choice over the live roles in position order and `none`: each role's description is
    what it covers, its not-for line what it does not."""
    crit: Dict[str, Any] = {k: {"covers": str(v.get("description") or k), "not_for": str(v.get("not_for") or "")}
                            for k, v in vocab.items()}
    crit[NONE] = NONE_CRITERIA
    return {"type": "choice", "instructions": INSTRUCTIONS, "criteria": crit}


def question_hash(question: Dict[str, Any]) -> str:  # The criteria identity of a run (12 hex)
    return E.digest(question, 12)


async def role_types(
    gx: GraphHandle,
) -> Dict[str, Dict[str, str]]:  # {type key: its role map} -- only the types that declare one
    """The deliverable types whose presentation_policy declares a `section_roles` map."""
    out = {}
    for n in await F.load_label(gx, DevNodeKinds.DELIVERABLE_TYPE):
        rmap = dict((F.prop(n, "presentation_policy") or {}).get(ROLE_POLICY) or {})
        if rmap:
            out[str(F.prop(n, "key"))] = {str(k): str(v) for k, v in rmap.items()}
    return out


async def role_scope(
    gx: GraphHandle,
) -> Dict[str, Dict[str, Any]]:  # {note id: {node, kind, type, roles}} -- the public posts whose type maps roles
    """The posts that carry roles: public (the audience rule, e1fd4d64 -- the judge sees only
    public posts) and typed by a type declaring a role map (the membership is the type's)."""
    from .purenotes import note_types
    posts, types, maps = await E.public_posts(gx), await note_types(gx), await role_types(gx)
    out = {}
    for n, p in posts.items():
        t = (types.get(n) or {}).get("type")
        if t in maps:
            out[n] = {**p, "type": t, "roles": maps[t]}
    return out


async def scope_sections(
    gx: GraphHandle,
    scope: Dict[str, Dict[str, Any]],  # role_scope output
) -> Dict[str, List[Dict[str, Any]]]:  # {note id: [{id, node, heading, level, parent}] in document order}
    """Each scoped post's content Sections -- derived blocks, the preamble and retired Sections
    left out -- with each one's enclosing Section (PART_OF) and level."""
    sections = await E.post_sections(gx)
    parent = {str(a): str(b) for a, b in await F.load_edge_pairs(gx, SpineRelations.PART_OF)}
    out = {}
    for n in scope:
        rows = []
        for s in sections.get(n, []):
            if (F.prop(s, "block_role") or F.prop(s, "retired")
                    or str(F.prop(s, "name") or "_").startswith("_")):
                continue
            sid = str(F.nid(s))
            rows.append({"id": sid, "node": s, "heading": heading(str(F.prop(s, "title") or "")),
                         "level": int(F.prop(s, "level") or 0), "parent": parent.get(sid)})
        out[n] = rows
    return out


def section_view(
    post: Dict[str, Any],          # {title, kind, outline}
    path: List[str],               # The headings of the Sections it sits under, outermost first
    level: int,
    title: str,
    subheadings: List[str],        # Its direct children's headings
    text: str,                     # Its own text
) -> Dict[str, Any]:  # One Section's judged state -- what its staleness is measured against
    body = text if len(text) <= TEXT_MAX else text[:TEXT_MAX] + " …"
    return {"post": post, "section": {"heading": title, "level": level, "heading_path": list(path),
                                      "subheadings": list(subheadings[:SUBHEADS_MAX]), "text": body}}


async def load_role_views(
    gx: GraphHandle,
    scope: Optional[Dict[str, Dict[str, Any]]] = None,
    secs: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Dict[str, Any]]:  # {section id: judged state} for every scoped content Section
    """Every scoped Section's judged state, from the graph (never the file)."""
    from .site import stated
    scope = scope if scope is not None else await role_scope(gx)
    secs = secs if secs is not None else await scope_sections(gx, scope)
    out = {}
    for n, rows in secs.items():
        post = {"title": stated(scope[n]["node"], "title"), "kind": scope[n]["kind"],
                "outline": ["#" * r["level"] + " " + r["heading"] for r in rows][:OUTLINE_MAX]}
        by = {r["id"]: r for r in rows}
        kids: Dict[str, List[str]] = {}
        for r in rows:
            if r["parent"] in by:
                kids.setdefault(r["parent"], []).append(r["heading"])
        for r in rows:
            path, cur, seen = [], r["parent"], set()
            while cur in by and cur not in seen:
                seen.add(cur)
                path.insert(0, by[cur]["heading"])
                cur = by[cur]["parent"]
            out[r["id"]] = section_view(post, path, r["level"], r["heading"], kids.get(r["id"], []),
                                        str(F.prop(r["node"], "text") or "").strip())
    return out


async def load_role_facts(
    gx: GraphHandle,
) -> Dict[str, str]:  # {section id: the ACTIVE section_role value}
    """The confirmed roles (supersession applied)."""
    slot = [a for a in await F.load_assertions(gx) if F.prop(a, "predicate") == P.SECTION_ROLE]
    return {str(F.prop(a, "subject_id")): str(F.prop(a, "value") or "")
            for a in F.active_assertions(slot, await F.load_supersedes(gx))}


def effective_roles(
    rows: List[Dict[str, Any]],    # One post's scope_sections rows (document order)
    facts: Dict[str, str],         # load_role_facts output
) -> Dict[str, Tuple[Optional[str], str]]:  # {section id: (role or None, how: own | inherited | unassigned)}
    """Each Section's role as the page reads it: its own fact; else, below the candidate levels,
    its nearest ancestor's (derived, never stored -- 25a58e0e (3)); else unassigned."""
    out: Dict[str, Tuple[Optional[str], str]] = {}
    for r in rows:
        own = facts.get(r["id"])
        if own:
            out[r["id"]] = (own, "own")
        elif r["level"] not in CANDIDATE_LEVELS and (out.get(r["parent"]) or (None,))[0]:
            out[r["id"]] = (out[r["parent"]][0], "inherited")
        else:
            out[r["id"]] = (None, "unassigned")
    return out


async def load_role_judgments(
    gx: GraphHandle,
) -> Dict[str, Dict[str, Dict[str, Any]]]:  # {section id: {role key: the edge's judgment + _id}}
    """Every stored role judgment, keyed by the role's vocabulary key (retired roles included)."""
    keys = {str(F.nid(n)): str(F.prop(n, "key")) for n in await F.load_label(gx, DevNodeKinds.ENTITY)
            if F.prop(n, "entity_kind") == P.ENTITY_SECTION_ROLE}
    res = await graph_task(gx.queue, gx.graph_id, "query_edges",
                           query=EdgeQuery(relation_type=DevRelations.JUDGED).to_dict())
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for e in getattr(res, "edges", None) or getattr(res, "rows", None) or []:
        e = e.to_dict() if hasattr(e, "to_dict") else dict(e)
        props = e.get("properties") or {}
        if props.get("proposes") == P.SECTION_ROLE and str(e["target_id"]) in keys:
            out.setdefault(str(e["source_id"]), {})[keys[str(e["target_id"])]] = {**props, "_id": str(e["id"])}
    return out


def stale_sections(
    views: Dict[str, Dict[str, Any]],                  # {section id: judged state}
    qhash: str,                                         # The current question's hash
    judged: Dict[str, Dict[str, Dict[str, Any]]],       # load_role_judgments output
    roles: List[str],                                   # The live role keys
) -> List[str]:  # The Sections to ask, sorted
    """A Section is stale when a live role has no judgment of it, or any judgment's state or
    criteria is not the current one."""
    out = []
    for s, v in sorted(views.items()):
        mine, h = judged.get(s) or {}, E.digest(v)
        if any(k not in mine or mine[k].get("state") != h or mine[k].get("criteria") != qhash for k in roles):
            out.append(s)
    return out


def distribution(
    judgments: Dict[str, Dict[str, Any]],  # One Section's {role key: judgment}
    roles: List[str],                      # The live role keys
) -> List[Tuple[str, float]]:  # [(role or NONE, p)] by p descending, NONE = what the roles leave of 1
    """A Section's judged distribution over the live roles and `none`."""
    ps = [(k, float((judgments.get(k) or {}).get("p") or 0.0)) for k in roles]
    ps.append((NONE, max(0.0, 1.0 - sum(p for _, p in ps))))
    return sorted(ps, key=lambda r: (-r[1], r[0]))


def review_basis(state: str, criteria: str, inherited: str = "") -> str:  # What a review mark was made against
    """A Section's basis: its judged state and the question, and for an override row the role it
    inherits -- a mark whose basis is not the current one is no mark (eefda2dd (5)'s rule)."""
    return f"{state}:{criteria}" + (f":{inherited}" if inherited else "")


async def apply_role_run(
    gx: GraphHandle,
    run: Dict[str, Any],   # {sections: {id: state hash}, criteria, judgments: [{section, model, p: {role: p}}]}
    *,
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {deleted, landed}
    """Land one run: each asked Section's standing judgments are replaced by the run's, a review
    mark kept only where its basis is unchanged. Live and replay share it."""
    run = E.normalized(run)
    standing = await load_role_judgments(gx)
    drop = sorted(j["_id"] for s in run["sections"] for j in (standing.get(s) or {}).values())
    if drop:
        await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=drop)
    edges = []
    for j in run["judgments"]:
        s, state = j["section"], run["sections"][j["section"]]
        for k, p in sorted(j["p"].items()):
            edge = judged_edge(s, entity_node_id(P.ENTITY_SECTION_ROLE, k), proposes=P.SECTION_ROLE,
                               p=p, model=j["model"], criteria=run["criteria"], state=state)
            mark = ((standing.get(s) or {}).get(k) or {}).get("reviewed")
            if mark and mark.startswith(review_basis(state, run["criteria"])):
                edge["properties"]["reviewed"] = mark
            edges.append(edge)
    if edges:
        await extend_graph(gx.queue, gx.graph_id, [], edges)
    return {"deleted": len(drop), "landed": len(edges)}


def request_body(
    view: Dict[str, Any],          # One Section's judged state
    question: Dict[str, Any],      # role_question output
    model: str = E.JUDGE_MODEL,
) -> Dict[str, Any]:  # One System One request
    return {"state": view, "model": model, "questions": {"role": question}}


def run_roles(
    todo: List[str],                         # The Sections to ask
    views: Dict[str, Dict[str, Any]],
    question: Dict[str, Any],
    ask: Callable[[Dict[str, Any]], Dict[str, Any]],
    *,
    model: str = E.JUDGE_MODEL,
    workers: int = E.JUDGE_WORKERS,
) -> Dict[str, Any]:  # {judgments: [{section, model, p: {role: p}}], input_tokens, errors}
    """One request per Section; an answer missing a role's probability is a failure."""
    roles = [k for k in question["criteria"] if k != NONE]

    def read(s, r):
        probs = ((r.get("answers") or {}).get("role") or {}).get("probabilities") or {}
        missing = [k for k in roles if k not in probs]
        if missing:
            raise RuntimeError(f"no probability for {len(missing)} role(s), e.g. {missing[0]}")
        return {"section": s, "model": str(r.get("model") or model), "p": {k: float(probs[k]) for k in roles}}
    res = E.run_requests(todo, ask, workers=workers, read=read, label=lambda s: {"section": s},
                         body_of=lambda s: request_body(views[s], question, model))
    return {"judgments": res["results"], "input_tokens": res["input_tokens"], "errors": res["errors"]}


async def judge_roles(
    gx: GraphHandle,
    *,
    all_sections: bool = False,  # Re-judge every scoped Section, not only the stale ones
    dry_run: bool = False,       # Report the stale Sections, the request count and a token estimate; ask nothing
    ask: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,   # The judge (default: HTTP with the key)
    model: str = E.JUDGE_MODEL,
    url: str = E.JUDGE_URL,
    workers: int = E.JUDGE_WORKERS,
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {posts, sections, roles, requests, token_estimate, written, run?, applied?, input_tokens?, error?}
    """The role judge verb: find the stale Sections, ask one request each, land the run. All or
    nothing -- a run with any failed request writes nothing and reports the failures."""
    scope = await role_scope(gx)
    views, vocab = await load_role_views(gx, scope), await load_role_vocab(gx)
    out: Dict[str, Any] = {"posts": len(scope), "sections": len(views), "roles": len(vocab), "written": False}
    if not vocab:
        return {**out, "requests": 0, "token_estimate": 0,
                "error": f"no live {P.ENTITY_SECTION_ROLE} vocabulary -- mint the roles first"}
    question = role_question(vocab)
    qhash = question_hash(question)
    todo = sorted(views) if all_sections else stale_sections(views, qhash, await load_role_judgments(gx), list(vocab))
    out.update({"requests": len(todo),
                "token_estimate": sum(len(json.dumps(request_body(views[s], question, model))) for s in todo) // 4})
    if dry_run or not todo:
        return out
    ask, why = E.resolve_ask(ask, url)
    if ask is None:
        return {**out, "error": why}
    res = run_roles(todo, views, question, ask, model=model, workers=workers)
    out["input_tokens"] = res["input_tokens"]
    if res["errors"]:
        return {**out, "error": f"{len(res['errors'])} of {len(todo)} requests failed -- nothing written",
                "failures": res["errors"][:5]}
    run = {"sections": {s: E.digest(views[s]) for s in todo}, "criteria": qhash,
           "judgments": sorted(res["judgments"], key=lambda j: j["section"]),
           "models": sorted({j["model"] for j in res["judgments"]})}
    out["applied"] = await apply_role_run(gx, run, actor=actor)
    return {**out, "written": True, "run": run}


async def section_roles(
    gx: GraphHandle,
    note: Optional[str] = None,   # One post's id (else every scoped post's counts)
) -> Dict[str, Any]:  # {posts: [{id, title, type, counts, rows?}], totals, stale}
    """The derived read: each scoped post's Sections with the role the page reads (own,
    inherited or unassigned) and the judge's top answer; the counts the review drives to zero."""
    scope = await role_scope(gx)
    secs = await scope_sections(gx, scope)
    views, vocab = await load_role_views(gx, scope, secs), await load_role_vocab(gx)
    facts, judged = await load_role_facts(gx), await load_role_judgments(gx)
    roles = list(vocab)
    stale = set(stale_sections(views, question_hash(role_question(vocab)), judged, roles)) if roles else set(views)
    unknown = sorted({p for m in (s["roles"] for s in scope.values()) for p in m.values()} - set(PLACEMENTS))
    posts, totals = [], {"candidates": 0, "own": 0, "inherited": 0, "unassigned": 0, "overrides": 0, "stale": 0}
    from .site import stated
    titles = {n: stated(scope[n]["node"], "title") for n in scope}
    for n in sorted(scope, key=lambda n: (titles[n].lower(), n)):
        rows, eff = secs[n], effective_roles(secs[n], facts)
        counts = {k: 0 for k in totals}
        out_rows = []
        for r in rows:
            role, how = eff[r["id"]]
            top = distribution(judged.get(r["id"]) or {}, roles)[0] if r["id"] in judged and roles else None
            counts["candidates"] += r["level"] in CANDIDATE_LEVELS
            counts[how] += 1
            counts["overrides"] += how == "own" and r["level"] not in CANDIDATE_LEVELS
            counts["stale"] += r["id"] in stale
            out_rows.append({"id": r["id"], "heading": r["heading"], "level": r["level"], "role": role, "how": how,
                             "judged": top, "stale": r["id"] in stale})
        for k in totals:
            totals[k] += counts[k]
        entry = {"id": n, "title": titles[n], "type": scope[n]["type"], "counts": counts}
        if note and n == note:
            entry["rows"] = out_rows
        if not note or n == note:
            posts.append(entry)
    return {"posts": posts, "totals": totals, "roles": roles, "unknown_placements": unknown,
            "scoped": len(scope)}
