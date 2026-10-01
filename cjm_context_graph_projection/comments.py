"""The post page's comments (design 39c51c15 (1), rulings 98d33f9e (2) + 86f4a34d, under the
redesign build 8079ae0f).

A post's comment thread is a FACT on its Note -- `discussion`, the number of its GitHub thread,
with history -- never a pathname lookup: a URL is a fact with history (96aff70e), so pathname
mapping orphans a thread at every path move and splits one page's thread by the URL form a
reader arrived at (the index.html duplicates).

THE BLOCK. Quarto's giscus include writes `mapping` verbatim and nothing else (no term, no
strict mode), so the build owns the widget's markup, as it owns the grid's: a page holding the
fact loads its thread by NUMBER; a page with none maps the specific term of its CANONICAL
site_path in strict mode, so every URL form of a page lands on one thread (the split closed at
its root, 86f4a34d (b)). Every superseded thread renders as a link ('Earlier comments: #N').
A tutorial's questions line opens the block. The theme pair rides the two hidden inputs
Quarto's color-scheme toggle reads, so the widget follows the site's light / dark switch. The
widget's settings are site chrome under one key of the site config (`post-comments`); a
missing repo or category refuses the build, never a guessed id.

THE HARVEST. `harvest-discussions` is the one verb that asks GitHub. It reads the comment
threads -- the utterances issues and the discussions of the comments category -- and maps
each to a Note: a path-era title (an utterances pathname, a giscus term) against every
site_path the page has held, under the site's former base paths too; a title-era one
('<title> | <site title>') against the Notes' titles; or an authored map (`--map N=<note>`)
where neither reaches. A Note with several threads keeps the one with the MOST comments
active and the others stand superseded (86f4a34d (a)); a tie or an ambiguous match refuses
the whole run, and a thread matching no Note is reported, never written. The journaled op
carries the run (the observed threads, the authored maps, the plan), so replay re-lands it
without asking GitHub; the build reads only the facts, and reports any thread fact on a Note
the profile does not render."""

import json
import posixpath
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

import yaml
from cjm_context_graph_primitives.query import PropertyPredicate
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.vocab import DevNodeKinds

from . import factlayer as F
from .runtime import GraphHandle

CONFIG_KEY = "post-comments"                                  # The site-config key holding the widget's settings
CONFIG_REQUIRED = ("repo", "repo-id", "category", "category-id")
CONFIG_DEFAULTS = {"reactions-enabled": True, "input-position": "top", "language": "en",
                   "loading": "", "base-paths": []}          # Quarto's own giscus defaults
THEME_DEFAULTS = {"light": "light", "dark": "dark"}
GISCUS_CLIENT = "https://giscus.app/client.js"
UTTERANCES_BOT = "utterances-bot"                             # The author of every utterances thread


def load_comments_config(
    website_root: str,                           # The site project root
    required: Iterable[str] = CONFIG_REQUIRED,   # The settings the caller needs (the harvest needs the repo alone)
) -> Dict[str, Any]:  # {config, site_title, errors}
    """The widget's settings from the site config (`post-comments`) and the site's title (the
    title-era threads end in ' | <site title>'). A missing required setting refuses: the build
    needs the repo and the category (a category exists once Discussions are enabled); the
    harvest needs the repo, and reads the category's discussions once it is named."""
    path = Path(website_root) / "_quarto.yml"
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        return {"config": {}, "site_title": "", "errors": [{"kind": "post-comments", "path": str(path),
                                                            "why": f"unreadable: {e}"}]}
    raw = cfg.get(CONFIG_KEY) or {}
    missing = [k for k in required if not str(raw.get(k) or "").strip()]
    if missing:
        return {"config": {}, "site_title": "", "errors": [{
            "kind": "post-comments", "path": str(path), "missing": missing,
            "why": f"the site config's `{CONFIG_KEY}` lacks the comment thread's repo or category"}]}
    theme = raw.get("theme") or {}
    if isinstance(theme, str):
        theme = {"light": theme, "dark": theme}
    out = {**CONFIG_DEFAULTS, **{k: v for k, v in raw.items() if k != "theme"},
           "theme": {**THEME_DEFAULTS, **theme}}
    out["base-paths"] = [str(b).strip("/") for b in out.get("base-paths") or [] if str(b).strip("/")]
    title = str(((cfg.get("website") or {}).get("title")) or "").strip()
    return {"config": out, "site_title": title, "errors": []}


def thread_url(
    repo: str,    # owner/name
    number: int,  # The thread's number
) -> str:  # The thread on GitHub
    """A thread's page: comment threads are discussions once the publish converts them."""
    return f"https://github.com/{repo}/discussions/{number}"


def _js(value: Any) -> str:
    """A value as a JavaScript literal, safe inside an inline <script>."""
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def render_comments(
    config: Dict[str, Any],                 # load_comments_config's config
    *,
    number: Optional[int] = None,           # The page's thread (its active discussion fact)
    term: str = "",                         # The page's canonical site_path (the fallback's term)
    earlier: Iterable[int] = (),            # Its superseded threads, each linked
    questions: str = "",                    # A tutorial's questions line (the site's copy)
) -> str:  # The comments block (markdown with one raw HTML block)
    """The comments block: the questions line, the earlier threads, and the giscus widget --
    the thread by number, else the canonical site_path as a strict specific term."""
    data = {"repo": config["repo"], "repoId": config["repo-id"], "category": config["category"],
            "categoryId": config["category-id"],
            "reactionsEnabled": "1" if config.get("reactions-enabled", True) else "0",
            "emitMetadata": "0", "inputPosition": config.get("input-position") or "top",
            "lang": config.get("language") or "en"}
    if number is not None:
        data.update(mapping="number", term=str(int(number)))
    else:
        data.update(mapping="specific", term=term, strict="1")
    if config.get("loading"):
        data["loading"] = str(config["loading"])
    theme = config.get("theme") or THEME_DEFAULTS
    lines = []
    if questions:
        lines.append(questions)
    olds = [int(n) for n in earlier]
    if olds:
        links = ", ".join(f"[#{n}]({thread_url(config['repo'], n)})" for n in olds)
        lines.append(f"Earlier comments: {links}")
    # Quarto's color-scheme toggle re-themes any giscus frame from these two inputs
    html = (f'<input type="hidden" id="giscus-base-theme" value="{theme["light"]}">\n'
            f'<input type="hidden" id="giscus-alt-theme" value="{theme["dark"]}">\n'
            '<div class="giscus"></div>\n'
            "<script>\n(function () {\n"
            '  const s = document.createElement("script");\n'
            f"  s.src = {_js(GISCUS_CLIENT)};\n  s.async = true;\n  s.crossOrigin = \"anonymous\";\n"
            f"  Object.assign(s.dataset, {_js(data)});\n"
            f"  s.dataset.theme = document.body.classList.contains(\"quarto-dark\") ? {_js(theme['dark'])} : {_js(theme['light'])};\n"
            "  document.currentScript.parentNode.appendChild(s);\n})();\n</script>")
    body = "".join(line + "\n\n" for line in lines)
    return "::: {.post-comments}\n" + body + "```{=html}\n" + html + "\n```\n:::\n"


async def load_threads(
    gx: GraphHandle,
) -> Dict[str, Dict[str, Any]]:  # {Note id: {active: [numbers], earlier: [numbers]}}
    """Every Note's comment threads: the active discussion fact and the superseded ones."""
    slot = await F.load_label_where(gx, DevNodeKinds.ASSERTION,
                                    [PropertyPredicate("predicate", "eq", P.DISCUSSION)])
    if not slot:
        return {}
    supers = await F.load_supersedes(gx)
    active = {F.nid(a) for a in F.active_assertions(slot, supers)}
    out: Dict[str, Dict[str, Any]] = {}
    for a in slot:
        try:
            n = int(str(F.prop(a, "value") or "").strip())
        except ValueError:
            continue
        rec = out.setdefault(str(F.prop(a, "subject_id")), {"active": [], "earlier": []})
        rec["active" if F.nid(a) in active else "earlier"].append(n)
    for rec in out.values():
        rec["active"].sort()
        rec["earlier"] = sorted(set(rec["earlier"]) - set(rec["active"]))
    return out


def page_comments(
    threads: Dict[str, Any],  # load_threads' entry for the page ({} when it holds none)
    term: str,                # The page's canonical site_path
) -> Dict[str, Any]:  # {number, term, earlier} for render_comments, or {error}
    """What a page's block loads: its one active thread, else its canonical path as the term."""
    act = threads.get("active") or []
    if len(act) > 1:
        return {"error": f"{len(act)} active discussion facts ({', '.join(f'#{n}' for n in act)})"}
    return {"number": act[0] if act else None, "term": term, "earlier": threads.get("earlier") or []}


# ---- the harvest ------------------------------------------------------------------------

_ISSUES_Q = """query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 100, after: $after, filterBy: {createdBy: "%s"}) {
      pageInfo { hasNextPage endCursor }
      nodes { number title createdAt comments(last: 1) { totalCount nodes { createdAt } } }
    }
  }
}""" % UTTERANCES_BOT
_DISCUSSIONS_Q = """query($owner: String!, $name: String!, $category: ID!, $after: String) {
  repository(owner: $owner, name: $name) {
    hasDiscussionsEnabled
    discussions(first: 100, after: $after, categoryId: $category) {
      pageInfo { hasNextPage endCursor }
      nodes { number title createdAt comments(last: 1) { totalCount nodes { createdAt } } }
    }
  }
}"""


def gh_graphql(
    query: str,
    variables: Dict[str, Any],
) -> Dict[str, Any]:  # The response's `data`
    """One GraphQL request through the GitHub CLI (its own authentication)."""
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        if v is not None:
            cmd += ["-f", f"{k}={v}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gh api graphql failed: {(r.stderr or r.stdout).strip()[:300]}")
    body = json.loads(r.stdout)
    if body.get("errors"):
        raise RuntimeError(f"GitHub refused: {body['errors'][0].get('message', body['errors'])}")
    return body["data"]


def _thread(kind: str, n: Dict[str, Any]) -> Dict[str, Any]:
    last = (n.get("comments") or {}).get("nodes") or []
    return {"number": int(n["number"]), "kind": kind, "title": str(n.get("title") or ""),
            "comments": int((n.get("comments") or {}).get("totalCount") or 0),
            "created": str(n.get("createdAt") or ""), "last_comment": str(last[0]["createdAt"]) if last else ""}


def fetch_threads(
    repo: str,                       # owner/name
    category_id: str,                # The comments category (its discussions are threads)
    ask: Callable[[str, Dict[str, Any]], Dict[str, Any]] = gh_graphql,   # query -> data (a test double)
) -> List[Dict[str, Any]]:  # [{number, kind, title, comments, created, last_comment}] by number
    """Every comment thread on the repo: the utterances issues, and the discussions of the
    comments category once it is named and Discussions are enabled."""
    owner, name = repo.split("/", 1)
    out: List[Dict[str, Any]] = []
    sources = [("issue", _ISSUES_Q, {})]
    if category_id:
        sources.append(("discussion", _DISCUSSIONS_Q, {"category": category_id}))
    for kind, query, extra in sources:
        after = None
        while True:
            data = ask(query, {"owner": owner, "name": name, "after": after, **extra})["repository"]
            if kind == "discussion" and not data.get("hasDiscussionsEnabled"):
                break
            page = data["issues" if kind == "issue" else "discussions"]
            out += [_thread(kind, n) for n in page["nodes"]]
            if not page["pageInfo"]["hasNextPage"]:
                break
            after = page["pageInfo"]["endCursor"]
    return sorted(out, key=lambda t: t["number"])


def path_key(
    title: str,                       # A path-era thread title
    base_paths: Iterable[str] = (),   # The site's former base paths ('christianjmills-quarto')
) -> Optional[str]:  # The site_path it names ('/posts/x/'), or None for a title-era thread
    """A pathname (utterances, 'posts/x/' or 'posts/x/index') or a giscus term ('/posts/x/')
    as the site_path form; a former base path is dropped."""
    t = title.strip()
    if not t or " " in t or "/" not in t:
        return None
    t = t.lstrip("/")
    for b in base_paths:
        if t.startswith(b + "/"):
            t = t[len(b) + 1:]
            break
    head, tail = posixpath.split(t)
    if tail in ("index", "index.html"):
        t = head + "/"
    elif tail and not tail.endswith(".html"):
        t = t + "/"
    return "/" + t


def map_threads(
    threads: List[Dict[str, Any]],     # fetch_threads' result
    paths: Dict[str, List[str]],       # {site_path value: [Note ids holding it, active or before]}
    titles: Dict[str, List[str]],      # {Note title: [Note ids]}
    maps: Dict[int, str],              # Authored {thread number: Note id}
    site_title: str,
    base_paths: Iterable[str] = (),
) -> List[Dict[str, Any]]:  # Each thread with {note, via} (note None = unmapped) or {ambiguous}
    """Each thread's Note: an authored map wins; a path-era title by the page's site_path
    history; a title-era one by the Note's title."""
    suffix = f" | {site_title}" if site_title else None
    out = []
    for t in threads:
        row = {**t, "note": None, "via": ""}
        if t["number"] in maps:
            row.update(note=maps[t["number"]], via="map")
        else:
            key = path_key(t["title"], base_paths)
            if key is not None:
                hits, via = paths.get(key, []), "path"
            elif suffix and t["title"].endswith(suffix):
                hits, via = titles.get(t["title"][: -len(suffix)].strip(), []), "title"
            else:
                hits, via = [], ""
            if len(hits) == 1:
                row.update(note=hits[0], via=via)
            elif len(hits) > 1:
                row.update(ambiguous=sorted(hits), via=via)
        out.append(row)
    return out


def plan_threads(
    mapped: List[Dict[str, Any]],   # map_threads' result
) -> Dict[str, Any]:  # {plan: {note: {winner, earlier}}, ties: [{note, numbers}]}
    """Per Note, the thread with the MOST comments stands; the others are earlier threads
    (86f4a34d (a)). A tie at the top is not decided here."""
    by: Dict[str, List[Dict[str, Any]]] = {}
    for t in mapped:
        if t.get("note"):
            by.setdefault(t["note"], []).append(t)
    plan, ties = {}, []
    for note, ts in sorted(by.items()):
        ts = sorted(ts, key=lambda t: (-t["comments"], t["number"]))
        if len(ts) > 1 and ts[0]["comments"] == ts[1]["comments"]:
            ties.append({"note": note, "numbers": [t["number"] for t in ts if t["comments"] == ts[0]["comments"]]})
            continue
        plan[note] = {"winner": ts[0]["number"], "earlier": sorted(t["number"] for t in ts[1:])}
    return {"plan": plan, "ties": ties}


async def _harvest_index(
    gx: GraphHandle,
) -> Dict[str, Any]:  # {paths, titles, notes}
    """The site_path history (every value a page has held, transfers included) and the titles."""
    from .site import redirect_plan
    notes = {str(F.nid(n)): n for n in await F.load_label(gx, DevNodeKinds.NOTE)}
    paths: Dict[str, List[str]] = {}
    for subject, page in (await redirect_plan(gx))["pages"].items():
        for v in [page["active"], *page["superseded"]]:
            paths.setdefault(v, []).append(subject)
    titles: Dict[str, List[str]] = {}
    for i, n in notes.items():
        t = str(F.prop(n, "title") or "").strip()
        if t:
            titles.setdefault(t, []).append(i)
    return {"paths": paths, "titles": titles, "notes": notes}


def _changes(
    plan: Dict[str, Dict[str, Any]],      # plan_threads' plan
    facts: Dict[str, Dict[str, Any]],     # load_threads' result
) -> List[Dict[str, Any]]:  # [{note, assert, supersede, backfill}] -- what lands, in order
    out = []
    for note, p in sorted(plan.items()):
        have = facts.get(note) or {"active": [], "earlier": []}
        on_slot = set(have["active"]) | set(have["earlier"])
        win = p["winner"] if have["active"] != [p["winner"]] else None
        back = [n for n in p["earlier"] if n not in on_slot]
        if win is not None or back:
            out.append({"note": note, "assert": win,
                        "supersede": [v for v in have["active"] if v != win] if win is not None else [],
                        "backfill": back})
    return out


async def apply_harvest(
    gx: GraphHandle,
    run: Dict[str, Any],   # {plan: {note: {winner, earlier}}, content_hashes?}
    *,
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {asserted, backfilled, content_hashes}
    """Land one harvest: each Note's winning thread stands (superseding a different active
    one), and every earlier thread not yet on the slot is back-filled superseded by it. Live
    and replay share it; replay passes the journaled content hashes."""
    from .write import assert_value
    given = dict(run.get("content_hashes") or {})
    hashes: Dict[str, Any] = {}
    asserted = backfilled = 0
    for c in _changes(run["plan"], await load_threads(gx)):
        n, winner = c["note"], str(run["plan"][c["note"]]["winner"])
        if c["assert"] is not None:
            res = await assert_value(gx, n, P.DISCUSSION, winner, actor=actor,
                                     supersede=[str(v) for v in c["supersede"]] or None,
                                     subject_content_hash=given.get(n))
            if res.get("error"):
                raise RuntimeError(f"discussion on {n}: {res['error']}")
            hashes[n] = res.get("subject_content_hash")
            asserted += 1
        for old in c["backfill"]:
            res = await assert_value(gx, n, P.DISCUSSION, str(old), actor=actor, superseded_by=[winner],
                                     subject_content_hash=given.get(n))
            if res.get("error"):
                raise RuntimeError(f"discussion #{old} on {n}: {res['error']}")
            hashes[n] = res.get("subject_content_hash")
            backfilled += 1
    return {"asserted": asserted, "backfilled": backfilled, "content_hashes": hashes}


async def harvest_discussions(
    gx: GraphHandle,
    website_root: str,                       # The site project (the widget's settings, the site title)
    *,
    maps: Optional[Dict[int, str]] = None,   # Authored {thread number: Note id or prefix}
    dry_run: bool = False,                   # Report the mapping and the changes; write nothing
    ask: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,   # GitHub (default: gh)
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {threads, unmapped, ties, ambiguous, changes, written, run?, applied?, error?}
    """The harvest verb: read every comment thread, map it to a Note, land the facts. All or
    nothing -- a tie, an ambiguous match or an unresolved map writes nothing."""
    cfg = load_comments_config(website_root, required=("repo",))
    if cfg["errors"]:
        return {"error": cfg["errors"][0]["why"], "written": False}
    conf = cfg["config"]
    idx = await _harvest_index(gx)
    resolved: Dict[int, str] = {}
    bad = []
    for num, ref in sorted((maps or {}).items()):
        hits = [i for i in idx["notes"] if i == ref or i.startswith(ref)]
        if len(hits) == 1:
            resolved[int(num)] = hits[0]
        else:
            bad.append(f"#{num} -> {ref} ({'no Note' if not hits else f'{len(hits)} Notes'})")
    if bad:
        return {"error": "authored maps that resolve to no single Note: " + "; ".join(bad), "written": False}
    try:
        threads = fetch_threads(conf["repo"], str(conf.get("category-id") or ""), ask or gh_graphql)
    except Exception as e:   # GitHub unreachable or refusing: nothing is written
        return {"error": str(e)[:300], "written": False}
    mapped = map_threads(threads, idx["paths"], idx["titles"], resolved, cfg["site_title"], conf["base-paths"])
    planned = plan_threads(mapped)
    title = lambda n: str(F.prop(idx["notes"][n], "title") or "") if n in idx["notes"] else ""
    out: Dict[str, Any] = {
        "repo": conf["repo"],
        "threads": [{**{k: t[k] for k in ("number", "kind", "title", "comments", "created", "last_comment", "via")},
                     "note": t.get("note"), "note_title": title(t["note"]) if t.get("note") else ""}
                    for t in mapped],
        "unmapped": [t["number"] for t in mapped if not t.get("note") and not t.get("ambiguous")],
        "ambiguous": [{"number": t["number"], "notes": t["ambiguous"]} for t in mapped if t.get("ambiguous")],
        "ties": planned["ties"], "plan": planned["plan"], "written": False}
    out["changes"] = _changes(planned["plan"], await load_threads(gx))
    if out["ambiguous"] or out["ties"]:
        out["error"] = (f"{len(out['ambiguous'])} ambiguous thread(s), {len(out['ties'])} tie(s) "
                        "-- settle each with --map N=<note>; nothing written")
        return out
    if dry_run or not out["changes"]:
        return out
    run = {"repo": conf["repo"], "plan": planned["plan"], "maps": {str(k): v for k, v in sorted(resolved.items())},
           "threads": [{k: t[k] for k in ("number", "kind", "title", "comments", "created", "last_comment")}
                       for t in mapped]}
    applied = await apply_harvest(gx, run, actor=actor)
    run["content_hashes"] = applied.pop("content_hashes")
    return {**out, "written": True, "run": run, "applied": applied}
