"""The judge verb family's shared engine (design eefda2dd (8), capture e0b6f945 condition 1).

A JUDGE is a model service (TypeSafe's Jev by default) asked ONLY by a family's journaled verb,
never by a build (e09e262b): its answers are stable but not deterministic and its model version
moves, so a judgment is an OBSERVATION stored as a fact, and the verb's op carries the run whole
so a rebuild replays it without calling anyone. Every family shares what is here -- the key, the
HTTP ask, the worker pool and its all-or-nothing report, the key-sorted normalization live and
replay both land through, the hashing of questions and judged states, and the audience rule's
read of the public posts (e1fd4d64) -- and supplies the rest: its state builder, its question
set, its pairing and its apply (judging.py: related posts; facetjudge.py: the category facets).
The verbs stay per family. The key lives in KEY_FILE (or KEY_ENV), never in a repo or a journal."""

import hashlib
import http.client
import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from . import factlayer as F
from .runtime import GraphHandle

JUDGE_URL = "https://api.typesafe.ai/v1/systemone"   # The System One endpoint
JUDGE_MODEL = "jev-latest"                           # The model asked for (each judgment records the version answered)
KEY_FILE = "~/.config/typesafe/api_key"              # The key, outside every repo
KEY_ENV = "TYPESAFE_API_KEY"                         # ... or from the environment
JUDGE_WORKERS = 16                                   # Concurrent requests
RETRY_ROUNDS = 3                                     # Later rounds a failed request is asked again in
RETRY_PAUSE = 5.0                                    # Seconds before round n (times n)


def digest(
    obj: Any,    # Any JSON-able value (a question set, a judged state, an entry's criteria)
    n: int = 16,  # Hex characters kept
) -> str:  # The value's identity
    """The hash staleness is keyed by: key-sorted JSON, so equal content hashes equal."""
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:n]


def normalized(
    x: Any,  # A run, a judgment, any JSON-able value an op carries
) -> Any:  # The same value with sorted keys at every depth
    """The journal writes ops `sort_keys`, so replay rebuilds every dict key-sorted: live and
    replay land through this so both store the same property text."""
    return json.loads(json.dumps(x, sort_keys=True))


def restates(
    standing: Dict[Tuple[str, ...], Dict[str, Any]],  # {(source, target, ...): a stored judgment's properties + _id}
    drop: List[str],                                  # The edge ids the run deletes
    edges: List[Dict[str, Any]],                      # The judged edges it lands
) -> bool:  # Deleting and re-landing would leave the graph as it stands
    """A family apply's PLAN no-change test (ruling 24fa72c2 (3)): a run that drops exactly the
    edges it re-lands, each between the same ends with the same properties, restates the graph --
    a re-judge whose answers did not move journals nothing."""
    held = {j["_id"]: {"source_id": key[0], "target_id": key[1], "properties": {k: v for k, v in j.items() if k != "_id"}}
            for key, j in standing.items()}
    return (set(drop) == {e["id"] for e in edges}
            and all(F.edge_holds(held.get(e["id"]), e) for e in edges))


async def land_run(
    gx: GraphHandle,
    out: Dict[str, Any],   # The verb's report so far
    run: Dict[str, Any],   # The computed run (the op carries it whole)
    apply: Callable,       # The family's apply: (gx, run, *, actor, write) -> its result
    *,
    actor: str = "agent:session",
    write: bool = True,    # False = PLAN only: the op under "op", nothing written
) -> Dict[str, Any]:  # out + {written, run, op, applied?, noop?, error?}
    """The run verbs' shared tail (ruling 24fa72c2, design detail 65f4a6c9 (1)): the run's apply
    PLANNED against the db first -- a refusal returns before anything is written, a run changing
    nothing is no change (op None), else the run gains the content hashes its records bind and the
    op is {run, actor}. With write the op is applied here (a caller with no writes journal);
    the CLI plans and lets InvocationWrites append, then apply."""
    plan = await apply(gx, run, actor=actor, write=False)
    if plan.get("error"):
        return {**out, "error": plan["error"], "written": False, "op": None}
    if "content_hashes" in plan:
        run["content_hashes"] = plan["content_hashes"]
    if plan.get("noop"):
        return {**out, "run": run, "noop": True, "written": False, "op": None}
    op = {"run": run, "actor": actor}
    if not write:
        return {**out, "run": run, "op": op, "written": False}
    applied = await apply(gx, run, actor=actor)
    applied.pop("content_hashes", None)
    return {**out, "written": True, "run": run, "op": op, "applied": applied}


def read_key() -> Optional[str]:  # The judge's API key, or None
    """The key from KEY_ENV, else KEY_FILE -- never printed, never journaled."""
    k = os.environ.get(KEY_ENV, "").strip()
    p = Path(os.path.expanduser(KEY_FILE))
    if not k and p.exists():
        k = p.read_text().strip()
    return k or None


def http_ask(
    key: str,
    url: str = JUDGE_URL,
    timeout: float = 60.0,
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:  # body -> response ({model, answers, usage})
    """The HTTP judge: one POST per request, retried with backoff on overload and network faults."""
    def ask(body: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(body).encode()
        last = ""
        for attempt in range(6):
            req = urllib.request.Request(url, data, {"Authorization": f"Bearer {key}",
                                                     "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code not in (429, 500, 502, 503, 529):
                    raise RuntimeError(f"judge refused: {e.code} {e.read()[:300]!r}") from None
                last = f"HTTP {e.code}"
            # a connection dropped mid-read or a truncated body is as transient as an overload
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException,
                    ValueError) as e:
                last = repr(e)
            time.sleep(2 ** attempt)
        raise RuntimeError(f"judge unreachable after retries: {last}")
    return ask


def resolve_ask(
    ask: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]],  # A judge already in hand (a test double), or None
    url: str = JUDGE_URL,
) -> Tuple[Optional[Callable[[Dict[str, Any]], Dict[str, Any]]], Optional[str]]:  # (the judge, None) | (None, why not)
    """The judge a run asks: the one given, else HTTP with the key -- or the reason there is none."""
    if ask is not None:
        return ask, None
    key = read_key()
    if not key:
        return None, f"no judge key: {KEY_FILE} or ${KEY_ENV}"
    return http_ask(key, url), None


def run_requests(
    items: List[Any],                                    # One per request (a post pair, a post)
    ask: Callable[[Dict[str, Any]], Dict[str, Any]],     # body -> response
    *,
    body_of: Callable[[Any], Dict[str, Any]],            # item -> request body
    read: Callable[[Any, Dict[str, Any]], Dict[str, Any]],  # (item, response) -> the family's result row
    label: Callable[[Any], Dict[str, Any]],              # item -> the fields naming it in a failure row
    workers: int = JUDGE_WORKERS,
    retry_rounds: int = RETRY_ROUNDS,
) -> Dict[str, Any]:  # {results: [rows, in item order], input_tokens, errors: [label + error], retried}
    """Ask every request on a worker pool; every answer is kept (the family applies its floor).
    A failed item is asked again in up to `retry_rounds` later rounds, after a pause and on a
    smaller pool, before it counts -- a run of tens of thousands of requests must not be lost to
    a few transient faults; a refusal (`judge refused: <4xx>`) is never re-asked. What still
    fails is reported per item and never raised -- the family writes nothing if any failed."""
    def one(item):
        try:
            r = ask(body_of(item))
            return {**read(item, r), "_tokens": (r.get("usage") or {}).get("input_tokens", 0)}
        except Exception as e:
            return {**label(item), "error": str(e)[:300]}
    with ThreadPoolExecutor(max(1, workers)) as ex:
        rows = list(ex.map(one, items))
    retried = 0
    for rnd in range(1, retry_rounds + 1):
        again = [i for i, r in enumerate(rows) if "error" in r and not r["error"].startswith("judge refused")]
        if not again:
            break
        retried += len(again)
        time.sleep(RETRY_PAUSE * rnd)
        with ThreadPoolExecutor(max(1, min(workers, 4))) as ex:
            for i, r in zip(again, ex.map(one, [items[i] for i in again])):
                rows[i] = r
    ok = [r for r in rows if "error" not in r]
    return {"results": [{k: v for k, v in r.items() if k != "_tokens"} for r in ok],
            "input_tokens": sum(r["_tokens"] for r in ok),
            "errors": [r for r in rows if "error" in r], "retried": retried}


async def public_posts(
    gx: GraphHandle,
) -> Dict[str, Dict[str, Any]]:  # {post id: {node, kind}} for every PUBLIC post
    """The posts a judge may be shown (the audience rule e1fd4d64: only public posts are sent)."""
    from .postpage import POST_KINDS
    from .purenotes import note_types, public_deliverables
    public, types = await public_deliverables(gx), await note_types(gx)
    out = {}
    for n in await F.load_label(gx, DevNodeKinds.NOTE):
        nid = str(F.nid(n))
        kind = (types.get(nid) or {}).get("kind")
        if nid in public and kind in POST_KINDS:
            out[nid] = {"node": n, "kind": str(kind)}
    return out


async def post_sections(
    gx: GraphHandle,
) -> Dict[str, List[Any]]:  # {note id: [Section nodes in document order]}
    """Every Note's sections in order (HAS_SECTION), for a family's judged state to read from
    the graph, never from the file."""
    sections = {str(F.nid(s)): s for s in await F.load_label(gx, DevNodeKinds.SECTION)}
    out: Dict[str, List[Tuple[int, Any]]] = {}
    for s, t in await F.load_edge_pairs(gx, DevRelations.HAS_SECTION):
        sec = sections.get(str(t))
        if sec is not None:
            out.setdefault(str(s), []).append((int(F.prop(sec, "order") or 0), sec))
    return {n: [sec for _, sec in sorted(rows, key=lambda r: r[0])] for n, rows in out.items()}
