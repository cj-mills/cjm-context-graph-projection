"""The facet judge (design eefda2dd under the category model 0f7fcdcb and amendment 3c5cff97).

A post's categories are a view over typed facets the user CONFIRMS (about_task, about_stage,
about_subject, uses_tool, uses_model); the judge only PROPOSES them. For each public post the
judge (judgeengine) is asked ONE request with one Noul per vocabulary entry -- a yes
probability, the Nouls evaluated in parallel and blind to each other -- whose instructions come
from the entry's facet kind and whose true / false criteria are the entry's description and
not-for line. A non-tutorial post is asked the task and stage entries too; a tutorial's task
and stage are its teaches_* facts.

The judged STATE (facet_view) is read from the graph, never from the file: the title, the
description, the kind, the section outline, the code signals (block languages, python imports,
C# / C++ usings and includes, install commands) and the opening prose. No hand tags: the facets
replace them.

Storage and staleness are PER (POST, ENTRY): a JUDGED_FACET edge for each judgment at or above
STORE_FLOOR (the near-miss band stays visible), carrying p, the model, the entry's criteria hash
and the post's state hash; and each judged post's `facets_judged` record naming the state and
every judged entry's criteria hash -- the pairs below the floor included, since they leave no
edge. A pair is STALE when the post's state or the entry's criteria (with its kind's
instructions) moved, or either is new; a run re-judges the stale pairs grouped per post, all or
nothing, and its journaled op carries the run whole so a rebuild replays it with no network.

The measuring pass (eefda2dd (6)) reads the stored judgments: per entry the posts clearing the
threshold, DEAD entries (none) and TOO BROAD ones (more than a third of the posts it applies to),
and a sample of posts for the user's spot check. Only the explicit verb asks the judge (e09e262b)."""

import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.query import EdgeQuery
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import entity_node_id
from cjm_dev_graph_schema.nodes import judged_facet_edge
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from . import factlayer as F, judgeengine as E
from .runtime import GraphHandle

STORE_FLOOR = 0.2    # Judgments below it leave no edge (unless the pair holds a confirmed fact): the record says they were judged
THRESHOLD = 0.5      # A judgment at or above it is a proposal (tuned on the reviewed sample)
OUTLINE_MAX = 30     # Section headings in a post's judged state
PROSE_MAX = 1800     # Characters of opening prose in a post's judged state
SIGNALS_MAX = 40     # Entries per code-signal list
TUTORIAL_KIND = "tutorial"
FACET_ORDER = (P.ENTITY_TOOL, P.ENTITY_SUBJECT, P.ENTITY_MODEL, P.ENTITY_TASK, P.ENTITY_STAGE)
NON_TUTORIAL_KINDS = (P.ENTITY_TASK, P.ENTITY_STAGE)   # asked of a non-tutorial post only
FACET_OF_KIND = {k: pred for pred, k in P.FACET_PREDICATES.items()}   # entity kind -> its confirmed facet

# Each facet kind's Noul (eefda2dd (1)): the instructions name the entry, the criteria carry the
# entry's description (true) and the kind's no-case with its not-for line (false). Their text is
# part of every entry's criteria hash, so editing a kind re-judges its pairs.
KIND_QUESTIONS: Dict[str, Dict[str, str]] = {
    # Tool, subject, task and stage ask what is CENTRAL to the post, never what it passes through
    # (the user's review of the first document, session 2026-10-03_20-43-16: a framework running
    # under the library a post is about, a table displayed with pandas, a project hosted on GitHub
    # Pages, a shared word with a subject's name were all proposed under the first wording).
    P.ENTITY_TOOL: {
        "instructions": ("Is the tool {name} central to `post`: does the post teach it, or is it the tool the "
                         "post's own work is done with, so that a reader comes away knowing how to use it? A "
                         "framework or format the post converts a model from or to is central. It does not "
                         "count when it only runs underneath another library or tool the post is about, "
                         "serves a side task (displaying a table, a utility call, an install step), or is "
                         "mentioned or listed."),
        "false": ("{name} is incidental to the post: running underneath another tool, serving a side task, "
                  "installed, or mentioned.")},
    P.ENTITY_SUBJECT: {
        "instructions": ("Is the subject {name} what `post` is about: its main topic, or one of its few main "
                         "topics, as the criteria define the subject? It does not count when the post only passes "
                         "through it (one step, a tool it happens to use, a setting or an example), or when the "
                         "match is only a shared word."),
        "false": ("{name} is not what the post is about: it appears only as a step, a tool, a setting or an "
                  "example, or shares only a word with it.")},
    P.ENTITY_MODEL: {
        "instructions": ("Does `post` work with the model architecture {name}: training, fine-tuning, "
                         "running, exporting, deploying or explaining it? A model named only in passing "
                         "or in a comparison does not count."),
        "false": "The post only names {name}, or does not involve it."},
    P.ENTITY_TASK: {
        "instructions": ("Is the machine-learning task {name} what `post` is about: does the work the post "
                         "describes perform, build toward or study that task, as the criteria define it? A task "
                         "the post only passes through or mentions does not count."),
        "false": ("The task {name} is not what the post is about: the post passes through it, mentions it, or "
                  "is about something else.")},
    P.ENTITY_STAGE: {
        "instructions": ("Is the {name} stage of a machine-learning project what `post` is about: does the "
                         "post's main content do or discuss that stage, as the criteria define it? A stage the "
                         "post passes through on the way to its real topic does not count."),
        "false": ("The {name} stage is not what the post is about: the post passes through it, mentions it, or "
                  "is about something else.")},
}

_FENCE = re.compile(r"^(`{3,}|~{3,})[ \t]*([^\n]*)\n(.*?)^\1[ \t]*$", re.M | re.S)
_PY_IMPORT = re.compile(r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import\b|import\s+([A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)*))", re.M)
_USING = re.compile(r"^\s*(?:using\s+(?:static\s+|namespace\s+)?([A-Za-z_][\w.:]*)\s*;|#\s*include\s*[<\"]([^>\"]+)[>\"])", re.M)
_INSTALL = re.compile(r"(?:^|[\s!%])((?:sudo\s+)?(?:pip3?|conda|mamba|micromamba|npm|yarn|apt(?:-get)?|brew|winget|choco|cargo|uv)\s+(?:install|add)\b[^\n]*)", re.M)
_PY_LANGS = {"python", "py", "python3", "ipython"}
_USING_LANGS = {"c#", "csharp", "cs", "c++", "cpp", "c", "cuda", "cu", "h", "hpp"}


def _lang(info: str) -> str:
    """A fence's language: `{.bash}` / `{python}` / `python title=...` -> bash / python."""
    w = info.strip().strip("{}").split()
    return w[0].lstrip(".").lower() if w else ""


def code_signals(
    texts: List[str],  # The post's content sections' text, in order
) -> Dict[str, List[str]]:  # {languages, python_imports, usings, install_commands}
    """What the post's code says about its tools, read from its fenced blocks."""
    langs, imports, usings, installs = set(), set(), set(), []
    for t in texts:
        for m in _FENCE.finditer(t):
            lang, body = _lang(m.group(2)), m.group(3)
            if lang:
                langs.add(lang)
            if lang in _PY_LANGS:
                for a, b in _PY_IMPORT.findall(body):
                    for name in ([a] if a else [x.strip() for x in b.split(",")]):
                        imports.add(name.split(".")[0])
            if lang in _USING_LANGS:
                for a, b in _USING.findall(body):
                    usings.add(a or b)
            for cmd in _INSTALL.findall(body):
                c = " ".join(cmd.split())[:120]
                if c not in installs:
                    installs.append(c)
    return {"languages": sorted(langs), "python_imports": sorted(imports)[:SIGNALS_MAX],
            "usings": sorted(usings)[:SIGNALS_MAX], "install_commands": installs[:SIGNALS_MAX]}


def opening_prose(
    texts: List[str],  # The post's content sections' text, in order
    limit: int = PROSE_MAX,
) -> str:  # The prose the post opens with: code, markup and link targets removed
    """The post's opening prose, whitespace collapsed."""
    out = []
    for t in texts:
        t = _FENCE.sub(" ", t)
        t = re.sub(r"<!--.*?-->|<[^>\n]+>", " ", t, flags=re.S)
        t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
        out.append(_plain(t))
    return " ".join(" ".join(out).split())[:limit]


def _plain(t: str) -> str:
    """Markdown links to their text; Pandoc attribute blocks and div fences removed."""
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    return re.sub(r"^:::.*$|\{[#.][^{}\n]*\}|\{[\w-]+=[^{}\n]*\}", " ", t, flags=re.M)


def post_outline(
    sections: List[Any],  # A post's Section nodes in document order (judgeengine.post_sections)
) -> List[str]:  # Its headings as the page states them, derived blocks and the preamble left out
    """A post's section outline for a judged state: each content section's title, links reduced
    to their text -- never the section's name, which carries a dedup suffix (`conclusion~1`)."""
    heads = [" ".join(_plain(str(F.prop(s, "title") or "")).split()) for s in sections
             if not F.prop(s, "block_role") and not str(F.prop(s, "name") or "_").startswith("_")]
    return [h for h in heads if h]


def facet_view(
    title: str,
    description: str,
    kind: str,
    outline: List[str],             # Its section headings in order (derived blocks already removed)
    signals: Dict[str, List[str]],  # code_signals
    prose: str,                     # opening_prose
) -> Dict[str, Any]:  # The judged state of one post
    """What the facet judge sees of a post -- and what its staleness is measured against."""
    return {"title": title, "kind": kind, "description": description,
            "section_outline": list(outline[:OUTLINE_MAX]), "code": signals, "opening_prose": prose}


def entry_id(kind: str, key: str) -> str:  # A vocabulary entry's name in a run and a record
    return f"{kind}:{key}"


def entry_question(
    entry: Dict[str, Any],  # A live vocabulary entry: {entity_kind, key, name, description, not_for}
) -> Dict[str, Any]:  # The entry's Noul
    """One entry's Noul: its kind's instructions naming it, its description and not-for line as criteria."""
    q = KIND_QUESTIONS[entry["entity_kind"]]
    name = str(entry.get("name") or entry["key"])
    false = q["false"].format(name=name)
    if entry.get("not_for"):
        false += f" Not for: {entry['not_for']}"
    return {"type": "noul", "instructions": q["instructions"].format(name=name),
            "criteria": {"true": str(entry.get("description") or name), "false": false}}


def criteria_hash(question: Dict[str, Any]) -> str:  # An entry's criteria identity (12 hex)
    """The hash of an entry's Noul as asked: the entry's fields and its kind's text together."""
    return E.digest(question, 12)


def is_facet_entry(
    entry: Dict[str, Any],  # A vocabulary Entity's properties
) -> bool:  # May the judge ask it, and a confirmed facet name it?
    """A live entry of a facet kind that is not a matrix-structural task row: the cross-task row
    (`general`) and the off-grid row (`other`) organize the Tutorials matrix and say nothing as a
    post's chip (user ruling on the measuring pass, session 2026-10-03_20-43-16)."""
    if entry.get("entity_kind") not in FACET_ORDER or entry.get("retired"):
        return False
    return not (entry.get("entity_kind") == P.ENTITY_TASK and (entry.get("cross_task") or entry.get("off_grid")))


def applies(entry: str, post_kind: str) -> bool:  # Is this entry asked of a post of this kind?
    """Task and stage entries are asked of non-tutorial posts only (eefda2dd (4))."""
    return not (post_kind == TUTORIAL_KIND and entry.split(":", 1)[0] in NON_TUTORIAL_KINDS)


def stale_pairs(
    views: Dict[str, Dict[str, Any]],    # {post id: judged state}
    criteria: Dict[str, str],            # {entry: criteria hash} for every live entry
    records: Dict[str, Dict[str, Any]],  # {post id: {state, criteria: {entry: hash}}}
) -> Dict[str, List[str]]:  # {post id: [stale entries, sorted]} -- posts with none are left out
    """A pair is stale when its post has no record, the post's state moved, or the record holds
    no criteria hash for the entry or another one."""
    out = {}
    for n, v in sorted(views.items()):
        rec = records.get(n) or {}
        fresh = rec.get("state") == E.digest(v)
        judged = (rec.get("criteria") or {}) if fresh else {}
        stale = sorted(e for e, h in criteria.items() if applies(e, v["kind"]) and judged.get(e) != h)
        if stale:
            out[n] = stale
    return out


def review_basis(state: str, criteria: str) -> str:  # What a review mark was made against
    """A pair's basis: the post's judged state and the entry's criteria. A mark whose basis is
    not the pair's current one is no mark -- the pair is proposed again (eefda2dd (5))."""
    return f"{state}:{criteria}"


def record_value(state: str, criteria: Dict[str, str]) -> str:  # A facets_judged value
    """The canonical JSON of a post's record (key-sorted, compact)."""
    return json.dumps({"criteria": dict(sorted(criteria.items())), "state": state},
                      sort_keys=True, separators=(",", ":"))


async def load_facet_vocab(
    gx: GraphHandle,
) -> Dict[str, Dict[str, Any]]:  # {entry: the live Entity's properties} in facet then position order
    """Every entry of the five facet kinds the judge asks (is_facet_entry)."""
    rows = []
    for n in await F.load_label(gx, DevNodeKinds.ENTITY):
        p = F.props(n)
        k = p.get("entity_kind")
        if is_facet_entry(p):
            rows.append((FACET_ORDER.index(k), p.get("position") if p.get("position") is not None else 1 << 30,
                         str(p["key"]), {**p, "id": str(F.nid(n))}))
    return {entry_id(r[3]["entity_kind"], r[2]): r[3] for r in sorted(rows, key=lambda r: r[:3])}


async def load_facet_views(
    gx: GraphHandle,
) -> Dict[str, Dict[str, Any]]:  # {post id: judged state} for every PUBLIC post
    """The facet judge's state of every public post, from the graph (the audience rule: only
    public posts are sent)."""
    from .site import stated   # the judge sees what the page states (finding 12d98020)
    posts, sections = await E.public_posts(gx), await E.post_sections(gx)
    out = {}
    for n, p in posts.items():
        content = [s for s in sections.get(n, []) if not F.prop(s, "block_role")]
        texts = [str(F.prop(s, "text") or "") for s in content]
        out[n] = facet_view(stated(p["node"], "title"), stated(p["node"], "description"), p["kind"],
                            post_outline(content), code_signals(texts), opening_prose(texts))
    return out


async def load_facet_records(
    gx: GraphHandle,
) -> Dict[str, Dict[str, Any]]:  # {post id: {state, criteria, _value}} -- the active facets_judged records
    """What each judged post's facet judgments were made against (`_value` = the fact's text)."""
    slot = [a for a in await F.load_assertions(gx) if F.prop(a, "predicate") == P.FACETS_JUDGED]
    out = {}
    for a in F.active_assertions(slot, await F.load_supersedes(gx)):
        try:
            raw = str(F.prop(a, "value"))
            out[str(F.prop(a, "subject_id"))] = {**json.loads(raw), "_value": raw}
        except ValueError:   # a record that is not JSON judges nothing: its pairs read stale
            continue
    return out


async def load_facet_judgments(
    gx: GraphHandle,
) -> Dict[Tuple[str, str], Dict[str, Any]]:  # {(post, Entity id): the edge's judgment + _id}
    """Every stored facet judgment."""
    res = await graph_task(gx.queue, gx.graph_id, "query_edges",
                           query=EdgeQuery(relation_type=DevRelations.JUDGED_FACET).to_dict())
    raw = getattr(res, "edges", None) or getattr(res, "rows", None) or []
    out = {}
    for e in raw:
        e = e.to_dict() if hasattr(e, "to_dict") else dict(e)
        out[(str(e["source_id"]), str(e["target_id"]))] = {**(e.get("properties") or {}), "_id": str(e["id"])}
    return out


async def facets_stale(
    gx: GraphHandle,
) -> Dict[str, List[str]]:  # {post id: [stale entries]} -- the build's report
    """The public posts with missing or stale facet judgments."""
    vocab = await load_facet_vocab(gx)
    crit = {e: criteria_hash(entry_question(v)) for e, v in vocab.items()}
    return stale_pairs(await load_facet_views(gx), crit, await load_facet_records(gx))


async def apply_facet_run(
    gx: GraphHandle,
    run: Dict[str, Any],   # {posts: {id: state hash}, asked: {id: [entries]}, criteria, judgments, content_hashes?}
    *,
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {deleted, landed, recorded, content_hashes}
    """Land one facet run: each asked pair's standing judgment is replaced by the run's (a post
    judged against a new state loses every standing judgment), and each judged post's record
    moves to the state and criteria it was judged against. Live and replay share it; replay
    passes the journaled content hashes so the records re-land the same."""
    from .write import assert_value
    run = E.normalized(run)
    records, standing = await load_facet_records(gx), await load_facet_judgments(gx)
    ids = {e: entity_node_id(*e.split(":", 1)) for es in run["asked"].values() for e in es}
    drop = []
    for n, h in run["posts"].items():
        moved = (records.get(n) or {}).get("state") != h
        asked = {ids[e] for e in run["asked"].get(n, [])}
        drop += [j["_id"] for (a, b), j in standing.items() if a == n and (moved or b in asked)]
    if drop:
        await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=sorted(set(drop)))
    edges = []
    for j in run["judgments"]:
        edge = judged_facet_edge(j["post"], ids[j["entry"]], p=j["p"], model=j["model"],
                                 criteria=run["criteria"][j["entry"]], state=run["posts"][j["post"]])
        mark = (standing.get((j["post"], ids[j["entry"]])) or {}).get("reviewed")
        if mark and mark == review_basis(run["posts"][j["post"]], run["criteria"][j["entry"]]):
            edge["properties"]["reviewed"] = mark
        edges.append(edge)
    if edges:
        await extend_graph(gx.queue, gx.graph_id, [], edges)
    given, hashes, recorded = dict(run.get("content_hashes") or {}), {}, 0
    for n, h in sorted(run["posts"].items()):
        rec = records.get(n) or {}
        kept = dict(rec.get("criteria") or {}) if rec.get("state") == h else {}
        kept.update({e: run["criteria"][e] for e in run["asked"].get(n, [])})
        value = record_value(h, kept)
        prior = rec.get("_value")
        if prior == value:
            continue
        res = await assert_value(gx, n, P.FACETS_JUDGED, value, actor=actor,
                                 supersede=[prior] if prior else None, subject_content_hash=given.get(n))
        if res.get("error"):
            raise RuntimeError(f"facets_judged on {n}: {res['error']}")
        hashes[n] = res.get("subject_content_hash")
        recorded += 1
    return {"deleted": len(set(drop)), "landed": len(edges), "recorded": recorded, "content_hashes": hashes}


def run_facets(
    todo: Dict[str, List[str]],            # {post id: [entries to ask]}
    views: Dict[str, Dict[str, Any]],
    questions: Dict[str, Dict[str, Any]],  # {entry: its Noul}
    ask: Callable[[Dict[str, Any]], Dict[str, Any]],
    *,
    model: str = E.JUDGE_MODEL,
    workers: int = E.JUDGE_WORKERS,
) -> Dict[str, Any]:  # {judgments: [{post, model, p: {entry: p}}], input_tokens, errors}
    """One request per post with a Noul per entry; a response missing an asked entry is a failure."""
    def read(n, r):
        ans = r.get("answers") or {}
        missing = [e for e in todo[n] if e not in ans]
        if missing:
            raise RuntimeError(f"no answer for {len(missing)} entr(ies), e.g. {missing[0]}")
        return {"post": n, "model": str(r.get("model") or model),
                "p": {e: float(ans[e]["noul"]) for e in todo[n]}}
    res = E.run_requests(sorted(todo), ask, workers=workers, read=read, label=lambda n: {"post": n},
                         body_of=lambda n: request_body(views[n], {e: questions[e] for e in todo[n]}, model))
    return {"judgments": res["results"], "input_tokens": res["input_tokens"], "errors": res["errors"]}


def request_body(
    view: Dict[str, Any],                  # The post's judged state
    questions: Dict[str, Dict[str, Any]],  # {entry: Noul}
    model: str = E.JUDGE_MODEL,
) -> Dict[str, Any]:  # One System One request
    return {"state": {"post": view}, "model": model, "questions": questions}


async def judge_facets(
    gx: GraphHandle,
    *,
    all_posts: bool = False,   # Re-judge every pair of every public post, not only the stale ones
    dry_run: bool = False,     # Report the stale pairs, the request count and a token estimate; ask nothing
    ask: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,   # The judge (default: HTTP with the key)
    model: str = E.JUDGE_MODEL,
    url: str = E.JUDGE_URL,    # The judge endpoint (a local double in tests)
    workers: int = E.JUDGE_WORKERS,
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {posts, entries, requests, pairs, token_estimate, written, run?, applied?, input_tokens?, error?}
    """The facet judge verb: find the stale pairs, ask one request per post, land the run.
    All or nothing -- a run with any failed request writes nothing and reports the failures."""
    views, vocab = await load_facet_views(gx), await load_facet_vocab(gx)
    questions = {e: entry_question(v) for e, v in vocab.items()}
    crit = {e: criteria_hash(q) for e, q in questions.items()}
    todo = stale_pairs(views, crit, {} if all_posts else await load_facet_records(gx))
    est = sum(len(json.dumps(request_body(views[n], {e: questions[e] for e in es}, model))) for n, es in todo.items()) // 4
    out: Dict[str, Any] = {"posts": len(views), "entries": len(vocab), "requests": len(todo),
                           "pairs": sum(len(es) for es in todo.values()), "token_estimate": est,
                           "stale": [{"id": n, "title": views[n]["title"], "entries": len(es)}
                                     for n, es in todo.items()], "written": False}
    if dry_run or not todo:
        return out
    ask, why = E.resolve_ask(ask, url)
    if ask is None:
        return {**out, "error": why}
    res = run_facets(todo, views, questions, ask, model=model, workers=workers)
    from .facetreview import load_confirmed
    confirmed = {(n, entry_id(P.FACET_PREDICATES[pred], v)) for n, facts in (await load_confirmed(gx)).items()
                 for pred, vals in facts.items() for v in vals}
    out["input_tokens"] = res["input_tokens"]
    if res["errors"]:
        return {**out, "error": f"{len(res['errors'])} of {len(todo)} requests failed -- nothing written",
                "failures": res["errors"][:5]}
    kept = sorted(({"post": r["post"], "entry": e, "p": p, "model": r["model"]}
                   for r in res["judgments"] for e, p in r["p"].items()
                   if p >= STORE_FLOOR or (r["post"], e) in confirmed),
                  key=lambda j: (j["post"], j["entry"]))
    run = {"posts": {n: E.digest(views[n]) for n in todo}, "asked": todo,
           "criteria": {e: crit[e] for e in sorted({e for es in todo.values() for e in es})},
           "judgments": kept, "requests": len(todo),
           "models": sorted({r["model"] for r in res["judgments"]})}
    applied = await apply_facet_run(gx, run, actor=actor)
    run["content_hashes"] = applied.pop("content_hashes")
    return {**out, "written": True, "run": run, "applied": applied}


async def measure(
    gx: GraphHandle,
    *,
    threshold: float = THRESHOLD,
    sample: int = 20,
) -> Dict[str, Any]:  # {threshold, posts, entries: [...], dead, broad, sample: [...], stale}
    """The measuring pass's report (eefda2dd (6)), from the stored judgments: per entry the posts
    clearing the threshold (DEAD = none: retire; TOO BROAD = more than a third of the posts it
    applies to: narrow; a task or stage no non-tutorial post clears is MATRIX-ONLY, never dead --
    the tutorials name it through teaches_*), and a sample of posts spread across the corpus with their proposals
    and near misses for the user's spot check. Stale pairs are counted, never measured around."""
    views, vocab = await load_facet_views(gx), await load_facet_vocab(gx)
    judged = await load_facet_judgments(gx)
    by_entity = {v["id"]: e for e, v in vocab.items()}
    hits: Dict[str, List[Tuple[float, str]]] = {e: [] for e in vocab}
    per_post: Dict[str, List[Tuple[float, str]]] = {}
    for (n, t), j in judged.items():
        e = by_entity.get(t)
        if e is None or n not in views:
            continue
        p = float(j.get("p") or 0.0)
        per_post.setdefault(n, []).append((p, e))
        if p >= threshold:
            hits[e].append((p, n))
    rows = []
    for e, v in vocab.items():
        pool = sum(1 for x in views.values() if applies(e, x["kind"]))
        n = len(hits[e])
        rows.append({"entry": e, "name": v.get("name"), "count": n, "applicable": pool,
                     "verdict": ("matrix-only" if n == 0 and e.split(":", 1)[0] in NON_TUTORIAL_KINDS
                                 else "dead" if n == 0 else "too-broad" if pool and n > pool / 3 else ""),
                     "top": [{"id": i, "title": views[i]["title"], "p": round(p, 3)}
                             for p, i in sorted(hits[e], key=lambda r: (-r[0], r[1]))[:5]]})
    picks = sorted(views, key=lambda n: E.digest(n))[:sample]
    samp = [{"id": n, "title": views[n]["title"], "kind": views[n]["kind"],
             "proposals": [{"entry": e, "p": round(p, 3)} for p, e in sorted(per_post.get(n, []), reverse=True) if p >= threshold],
             "near": [{"entry": e, "p": round(p, 3)} for p, e in sorted(per_post.get(n, []), reverse=True) if p < threshold]}
            for n in picks]
    bare = sorted(n for n in views if not any(p >= threshold for p, _ in per_post.get(n, [])))
    stale = await facets_stale(gx)
    return {"threshold": threshold, "posts": len(views), "entries": rows,
            "dead": [r["entry"] for r in rows if r["verdict"] == "dead"],
            "broad": [r["entry"] for r in rows if r["verdict"] == "too-broad"],
            "bare": [{"id": n, "title": views[n]["title"]} for n in bare],
            "sample": samp, "stale_posts": len(stale), "stale_pairs": sum(len(v) for v in stale.values())}
