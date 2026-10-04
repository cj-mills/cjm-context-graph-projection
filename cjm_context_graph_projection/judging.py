"""Judged related posts (design e09e262b, answering ruling 98d33f9e (1); the spike 245fb5b3).

A JUDGE rates, for an ordered pair of public posts (A -> B), how useful B is as a related read
at the end of A: a relatedness Score (0-3) and a relation Choice, each with its distribution.
The judge is a model service (TypeSafe's Jev by default) asked ONLY by the journaled
`judge-related` verb, never by a build: its answers are stable but not deterministic and its
model version moves, so a judgment is an OBSERVATION stored as a fact -- one JUDGED_RELATED
edge per ordered pair at or above STORE_FLOOR, the score, relation, distributions, model and
question hash on the edge -- and a rebuild replays it from the journal without calling anyone.

Each judged post carries a `related_judged` fact, '<question hash>:<judged-state hash>': what
its judgments were made against. The judged STATE is what the judge saw (title, description,
kind, the post's confirmed categories -- its facets, which replaced the hand tags (design
eefda2dd (2)) -- and the section outline as the page states it); a post whose current state or
question differs is STALE, as
is a public post with no record. A judge run re-judges the stale posts in both directions
(A -> every post, every post -> A) and replaces every stored judgment touching them; ranking
reads only stored judgments (postpage.related_posts), and the build reports stale posts
rather than ranking around the gap. Only public posts are sent (e1fd4d64); the key lives in
KEY_FILE (or KEY_ENV), never in a repo or a journal. The machinery every judge shares -- the
key, the ask, the pool, the hashing, the public posts -- is judgeengine.py's (design eefda2dd (8));
this module is the related family's state, questions, pairing and apply."""

from typing import Any, Callable, Dict, List, Optional, Tuple

from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.query import EdgeQuery
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.nodes import judged_related_edge
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from . import factlayer as F, judgeengine as E
from .runtime import GraphHandle

OUTLINE_MAX = 30                                     # Section headings in a post's judged state
STORE_FLOOR = 1.0   # Judgments below it are not stored: the post's record says it was judged
CATEGORY_LABELS = {P.ENTITY_TOOL: "tools", P.ENTITY_SUBJECT: "subjects", P.ENTITY_MODEL: "models",
                   P.ENTITY_TASK: "tasks", P.ENTITY_STAGE: "stages"}   # facet kind -> its key in a judged state

# The questions (the spike's, 245fb5b3): their hash keys staleness, so editing them re-judges
QUESTIONS: Dict[str, Any] = {
    "relatedness": {
        "type": "score",
        "instructions": ("A reader has just finished reading `post_a` on a technical blog. How useful would "
                         "`post_b` be as a 'Related' suggestion shown at the end of `post_a`? Judge by the "
                         "subject matter both posts actually cover (titles, descriptions, section outlines). "
                         "Each post's `categories` are its confirmed tools, subjects, models, tasks and stages; "
                         "a shared broad category alone (one framework, one field) is no relation."),
        "criteria": [
            {"what": "Unrelated, or related only through a broad framework or field both happen to use",
             "examples": ["both use PyTorch but one profiles CUDA kernels and the other quantizes an object detector",
                          "both are Unity projects but use different models for different tasks"]},
            {"what": "Loosely related: same general area, but a reader of post_a would rarely want post_b next"},
            {"what": "Clearly related: a closely connected technique, tool, model or problem that a reader of post_a would plausibly want next"},
            {"what": "Strongly related: a direct continuation or prerequisite, or the same technique applied to another model or platform",
             "examples": ["exporting one model to ONNX, then exporting a different model to ONNX",
                          "a training tutorial and the tutorial deploying that trained model"]},
        ],
    },
    "relation": {
        "type": "choice",
        "instructions": "What is the main way `post_b` relates to `post_a`, from the perspective of a reader of `post_a`?",
        "criteria": {
            "same_technique": "Applies the same technique or workflow to a different model, dataset or platform",
            "prerequisite": "Covers background or an earlier step that post_a builds on",
            "follow_up": "Builds on post_a: a later step, deployment, or deeper treatment of its subject",
            "same_tool": "Uses the same specific tool or library for a different task",
            "same_subject": "Discusses the same subject or concept from another angle (notes, lecture, log)",
            "unrelated": "No meaningful relationship beyond broad shared tags",
        },
    },
}
# The reader's reason line per relation; `unrelated` has none (it never renders)
RELATION_REASONS = {"same_technique": "Same technique", "prerequisite": "Background",
                    "follow_up": "Next step", "same_tool": "Same tool", "same_subject": "Same subject"}


def question_hash() -> str:  # The hash of what the judge is asked (12 hex)
    """The questions' identity: a changed question makes every judgment stale."""
    return E.digest([QUESTIONS, OUTLINE_MAX], 12)


def post_view(
    title: str,
    description: str,
    kind: str,
    categories: Dict[str, List[str]],   # {tools | subjects | models | tasks | stages: display names}
    outline: List[str],   # Its section headings in order (derived blocks already removed)
) -> Dict[str, Any]:  # The judged state of one post
    """What the judge sees of a post -- and what its staleness is measured against. An empty
    category is left out; names are sorted and unique, so the state is order-free."""
    return {"title": title, "kind": kind, "description": description,
            "categories": {k: sorted(set(v)) for k, v in sorted(categories.items()) if v},
            "section_outline": list(outline[:OUTLINE_MAX])}


def state_hash(view: Dict[str, Any]) -> str:  # The judged state's hash (16 hex)
    """A post's judged-state identity."""
    return E.digest(view)


def stale_posts(
    views: Dict[str, Dict[str, Any]],  # {post id: judged state}
    records: Dict[str, str],           # {post id: active related_judged value}
    question: str,                     # The current question hash
) -> List[str]:  # The stale post ids, sorted
    """A post is stale when it has no record or its record names another state or question."""
    return sorted(n for n, v in views.items() if records.get(n) != f"{question}:{state_hash(v)}")


def judge_pairs(
    stale: List[str],                  # The posts to re-judge
    views: Dict[str, Dict[str, Any]],  # Every judged post
) -> List[Tuple[str, str]]:  # Ordered pairs touching a stale post
    """Both directions for every stale post, against every other judged post."""
    s = set(stale)
    return [(a, b) for a in sorted(views) for b in sorted(views) if a != b and (a in s or b in s)]


def judgment_of(answers: Dict[str, Any]) -> Dict[str, Any]:  # The stored judgment
    """The judgment as the edge stores it, from the service's typed answers."""
    s, r = answers["relatedness"], answers["relation"]
    return {"score": float(s["score"]), "relation": str(r["choice"]),
            "score_probabilities": dict(s.get("probabilities") or {}),
            "relation_probabilities": dict(r.get("probabilities") or {}),
            "score_confidence": s.get("confidence"), "relation_confidence": r.get("confidence")}


def run_judge(
    pairs: List[Tuple[str, str]],
    views: Dict[str, Dict[str, Any]],
    ask: Callable[[Dict[str, Any]], Dict[str, Any]],   # body -> response (http_ask, or a test double)
    *,
    model: str = E.JUDGE_MODEL,
    workers: int = E.JUDGE_WORKERS,
) -> Dict[str, Any]:  # {judgments: [{a, b, model, ...judgment}], input_tokens, errors}
    """Ask the judge about every pair; every answer is kept (the caller applies the floor)."""
    res = E.run_requests(
        pairs, ask, workers=workers,
        body_of=lambda p: {"state": {"post_a": views[p[0]], "post_b": views[p[1]]}, "model": model,
                           "questions": QUESTIONS},
        read=lambda p, r: {"a": p[0], "b": p[1], "model": str(r.get("model") or model), **judgment_of(r["answers"])},
        label=lambda p: {"a": p[0], "b": p[1]})
    return {"judgments": res["results"], "input_tokens": res["input_tokens"], "errors": res["errors"]}


async def load_post_views(
    gx: GraphHandle,
) -> Dict[str, Dict[str, Any]]:  # {post id: judged state} for every PUBLIC post
    """The judged state of every public post (the audience rule: only public posts are sent)."""
    from .coverage import TUTORIAL_KIND, load_coverage_facts
    from .facetjudge import post_outline
    from .facetreview import load_confirmed
    from .site import stated   # the judge sees what the page states (finding 12d98020)
    posts, sections = await E.public_posts(gx), await E.post_sections(gx)
    names = {(F.prop(e, "entity_kind"), F.prop(e, "key")): str(F.prop(e, "name") or F.prop(e, "key"))
             for e in await F.load_label(gx, DevNodeKinds.ENTITY)}
    confirmed, teaches = await load_confirmed(gx), await load_coverage_facts(gx)
    out = {}
    for n, p in posts.items():
        # the confirmed facets; a tutorial's task and stage are its teaches_* facts (eefda2dd (4))
        facts = dict(confirmed.get(n) or {})
        if p["kind"] == TUTORIAL_KIND:
            facts.update(teaches.get(n) or {})
        cats: Dict[str, List[str]] = {}
        for pred, vals in facts.items():
            kind = P.FACET_PREDICATES.get(pred) or P.COVERAGE_KINDS.get(pred)
            cats.setdefault(CATEGORY_LABELS[kind], []).extend(names.get((kind, v), v) for v in vals)
        out[n] = post_view(stated(p["node"], "title"), stated(p["node"], "description"), p["kind"],
                           cats, post_outline(sections.get(n, [])))
    return out


async def load_judged(
    gx: GraphHandle,
) -> Dict[Tuple[str, str], Dict[str, Any]]:  # {(post, related): the edge's judgment}
    """Every stored judgment."""
    res = await graph_task(gx.queue, gx.graph_id, "query_edges",
                           query=EdgeQuery(relation_type=DevRelations.JUDGED_RELATED).to_dict())
    raw = getattr(res, "edges", None) or getattr(res, "rows", None) or []
    out = {}
    for e in raw:
        e = e.to_dict() if hasattr(e, "to_dict") else dict(e)
        out[(str(e["source_id"]), str(e["target_id"]))] = {**(e.get("properties") or {}), "_id": str(e["id"])}
    return out


async def load_records(
    gx: GraphHandle,
) -> Dict[str, str]:  # {post id: the active related_judged value}
    """What each judged post's judgments were made against."""
    slot = [a for a in await F.load_assertions(gx) if F.prop(a, "predicate") == P.RELATED_JUDGED]
    return {str(F.prop(a, "subject_id")): str(F.prop(a, "value"))
            for a in F.active_assertions(slot, await F.load_supersedes(gx))}


async def related_stale(
    gx: GraphHandle,
) -> List[Dict[str, str]]:  # [{id, title}] of the stale public posts
    """The public posts whose related judgments are missing or stale -- the build's report."""
    views = await load_post_views(gx)
    return [{"id": n, "title": views[n]["title"]}
            for n in stale_posts(views, await load_records(gx), question_hash())]


async def apply_judgments(
    gx: GraphHandle,
    run: Dict[str, Any],   # {question, posts: {id: state hash}, judgments: [...], content_hashes?}
    *,
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {deleted, landed, recorded, content_hashes}
    """Land one judge run: every stored judgment touching a re-judged post is replaced by the
    run's, and each re-judged post's record moves to the state it was judged against. Live and
    replay share it; replay passes the journaled content hashes so the records re-land the same."""
    from .write import assert_value
    judged = set(run["posts"])
    standing = await load_judged(gx)
    drop = [p["_id"] for (a, b), p in standing.items() if a in judged or b in judged]
    if drop:
        await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=drop)
    # Sorted keys at every depth: the journal stores the run key-sorted, so live and replay must
    # land the same property text
    norm = [E.normalized(j) for j in run["judgments"]]
    edges = [judged_related_edge(j["a"], j["b"], model=j["model"], question=run["question"],
                                 judgment={k: v for k, v in j.items() if k not in ("a", "b", "model")})
             for j in norm]
    if edges:
        await extend_graph(gx.queue, gx.graph_id, [], edges)
    records, given = await load_records(gx), dict(run.get("content_hashes") or {})
    hashes, recorded = {}, 0
    for n, h in sorted(run["posts"].items()):
        value, prior = f"{run['question']}:{h}", records.get(n)
        if prior == value:
            continue
        res = await assert_value(gx, n, P.RELATED_JUDGED, value, actor=actor,
                                 supersede=[prior] if prior else None,
                                 subject_content_hash=given.get(n))
        if res.get("error"):
            raise RuntimeError(f"related_judged on {n}: {res['error']}")
        hashes[n] = res.get("subject_content_hash")
        recorded += 1
    return {"deleted": len(drop), "landed": len(edges), "recorded": recorded, "content_hashes": hashes}


async def judge_related(
    gx: GraphHandle,
    *,
    all_posts: bool = False,   # Re-judge every public post, not only the stale ones
    dry_run: bool = False,     # Report the stale posts and the pair count; ask nothing
    ask: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,   # The judge (default: HTTP with the key)
    model: str = E.JUDGE_MODEL,
    url: str = E.JUDGE_URL,    # The judge endpoint (a local double in tests)
    workers: int = E.JUDGE_WORKERS,
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {stale, pairs, written, run?, applied?, input_tokens?, error?}
    """The judge verb: find the stale posts, judge every pair touching them, land the run.
    All or nothing -- a run with any failed pair writes nothing and reports the failures."""
    views = await load_post_views(gx)
    q = question_hash()
    stale = sorted(views) if all_posts else stale_posts(views, await load_records(gx), q)
    pairs = judge_pairs(stale, views)
    out: Dict[str, Any] = {"stale": [{"id": n, "title": views[n]["title"]} for n in stale],
                           "pairs": len(pairs), "question": q, "written": False}
    if dry_run or not stale:
        return out
    ask, why = E.resolve_ask(ask, url)
    if ask is None:
        return {**out, "error": why}
    res = run_judge(pairs, views, ask, model=model, workers=workers)
    out["input_tokens"] = res["input_tokens"]
    if res["errors"]:
        return {**out, "error": f"{len(res['errors'])} of {len(pairs)} pairs failed -- nothing written",
                "failures": res["errors"][:5]}
    kept = [j for j in res["judgments"] if j["score"] >= STORE_FLOOR]
    run = {"question": q, "posts": {n: state_hash(views[n]) for n in stale},
           "judgments": sorted(kept, key=lambda j: (j["a"], j["b"])), "pairs": len(pairs),
           "models": sorted({j["model"] for j in res["judgments"]})}
    applied = await apply_judgments(gx, run, actor=actor)
    run["content_hashes"] = applied.pop("content_hashes")
    return {**out, "written": True, "run": run, "applied": applied}
