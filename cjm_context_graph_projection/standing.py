"""A page's STANDING (design amendment cbd5f154 to the disposition pass 9a21e345, amended by
6514869f): what the ruling says a page is now, and where its readers go once it leaves.

The authored inputs are few. `currency` is the ruling, one value per page -- `current`,
`archived` (kept at its URL under a historical banner) or `removed` (ruled off the public site).
Removal is EXECUTED by `publish_state retired` plus a destination: a public successor's
SUPERSEDES edge, else a RELOCATED_TO edge to the web Reference of the page's repo copy. Every
archive post carries a currency fact, so one without is UNRULED.

Everything else is DERIVED here and stored nowhere: a page is SUPERSEDED when a public successor
holds a SUPERSEDES edge to it (a successor that is not public yet leaves it standing, cbd5f154
(3)); a retired page's destination is its successor, else its repo copy, else the nearest hub the
build derives; the relocation worklist is every removed page not yet retired; and a page's claim
support is withdrawn when it is archived, removed or retired, or passes to its successor when it
is superseded (cbd5f154 (6), read by the claims report). What the inputs cannot decide is
reported, never resolved: two active standings, a retired page ruled current, a page a public
successor supersedes while still ruled current, a destination on a page that is not leaving.

`standing_report` is the disposition worklist as a read: each page's standing beside its
traffic, its inbound links and the claims it supports, so the pass is worked from the graph
rather than from an exported file."""

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from cjm_context_graph_layer.ops import graph_task
from cjm_context_graph_primitives.query import EdgeQuery
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.vocab import DevRelations

from . import factlayer as F
from .runtime import GraphHandle

UNRULED = "unruled"
CONFLICT = "conflict"
RETIRED = P.PUBLISH_RETIRED
STANDINGS = P.CURRENCY_VALUES + (RETIRED, UNRULED, CONFLICT)   # the labels a row can carry
# The site's own pages (home, about, the hubs, 404) are not posts: the disposition pass rules posts,
# so an archive page of these kinds is never UNRULED (it shows only when it carries a standing).
RULE_EXEMPT_KINDS = ("site",)


def project_standing(
    notes: Dict[str, Dict[str, Any]],          # {deliverable id: {slug, title, origin, kind}} -- the typed Notes
    currency: Dict[str, List[str]],            # {deliverable id: active currency values}
    states: Dict[str, List[str]],              # {deliverable id: active publish_state values}
    public: Set[str],                          # The ids the public profile shows
    supersedes: Iterable[Tuple[str, str]],     # (successor, superseded) SUPERSEDES pairs, any nodes
    relocations: Dict[str, List[str]],         # {deliverable id: the repo-copy URLs its RELOCATED_TO edges name}
    pages: Optional[Dict[str, Dict[str, Any]]] = None,  # {node id: {slug, title}} -- successors that are no deliverable (a Lens page, always projected)
) -> Dict[str, Any]:  # {rows, by_id, counts, unruled, pending, anomalies}
    """The pure projection (no graph access): see the module docstring for the rules. A row is
    every archive post, every retired archive page, and every other deliverable that carries a
    standing or a destination or supersedes a page."""
    succ: Dict[str, List[str]] = {}
    pred: Dict[str, List[str]] = {}
    pages = pages or {}
    for s, t in supersedes:
        if t in notes and (s in notes or s in pages):
            succ.setdefault(t, []).append(s)
            pred.setdefault(s, []).append(t)
    live_ids = set(public) | set(pages)   # a projected page has no publish lifecycle: it is live

    def ref(i: str) -> Dict[str, str]:
        n = notes.get(i) or pages.get(i) or {}
        return {"id": i, "slug": n.get("slug", ""), "title": n.get("title", "")}

    rows: List[Dict[str, Any]] = []
    anomalies: List[Dict[str, Any]] = []
    for nid in sorted(notes, key=lambda i: (notes[i].get("slug") or "", i)):
        n = notes[nid]
        cur = sorted(set(currency.get(nid) or []))
        st = states.get(nid) or []
        relocated = sorted(set(relocations.get(nid) or []))
        ruled = n.get("origin") == P.ORIGIN_ARCHIVE and n.get("kind") not in RULE_EXEMPT_KINDS
        retired = RETIRED in st
        if not (ruled or cur or succ.get(nid) or pred.get(nid) or relocated
                or (retired and n.get("origin") == P.ORIGIN_ARCHIVE)):
            continue
        live = sorted(s for s in succ.get(nid, []) if s in live_ids)
        waiting = sorted(s for s in succ.get(nid, []) if s not in live_ids)
        if len(cur) > 1:
            label = CONFLICT
        elif retired:
            label = RETIRED
        elif cur:
            label = cur[0]
        else:
            label = UNRULED if ruled else ""
        leaving = retired or P.CURRENCY_REMOVED in cur
        destination = None
        if leaving:
            # the hub is a RETIRED page's fallback; a removal not yet executed has no destination named yet
            destination = ({"kind": "successor", "to": [ref(s) for s in live]} if live else
                           {"kind": "repo", "to": relocated} if relocated else
                           {"kind": "hub"} if retired else None)
        row = {"id": nid, "slug": n.get("slug", ""), "title": n.get("title", ""), "origin": n.get("origin", ""),
               "kind": n.get("kind", ""), "standing": label, "currency": cur[0] if len(cur) == 1 else None,
               "publish_state": st, "public": nid in public, "superseded": bool(live),
               "superseded_by": [ref(s) for s in live], "successor_waiting": [ref(s) for s in waiting],
               "supersedes": [ref(t) for t in sorted(pred.get(nid, []))], "relocated_to": relocated,
               "destination": destination,
               "pending": P.CURRENCY_REMOVED in cur and not retired}
        rows.append(row)
        why = []
        if label == CONFLICT:
            why.append(f"two active standings: {' / '.join(cur)}")
        if retired and cur and P.CURRENCY_REMOVED not in cur:
            why.append(f"retired, yet ruled {cur[0]}")
        if live and not leaving:
            why.append(f"a public successor supersedes it, yet it is ruled {label or 'nothing'} -- re-rule it")
        if relocated and not leaving:
            why.append("relocated to a repo copy, yet not removed")
        if len(st) > 1:
            why.append(f"publish_state multi-active: {' / '.join(st)}")
        if why:
            anomalies.append({"id": nid, "slug": row["slug"], "why": why})
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["standing"] or "born"] = counts.get(r["standing"] or "born", 0) + 1
    counts["superseded"] = sum(1 for r in rows if r["superseded"])
    return {"rows": rows, "by_id": {r["id"]: r for r in rows}, "counts": counts,
            "unruled": [r["id"] for r in rows if r["standing"] == UNRULED],
            "pending": [r["id"] for r in rows if r["pending"]], "anomalies": anomalies}


def support_standing(
    row: Optional[Dict[str, Any]],  # A standing row (None = a page with none: its support counts)
) -> Dict[str, Any]:  # {withdrawn: reason | None, passes_to: [successor refs]}
    """What a page's standing does to the claim support it gives (cbd5f154 (6)): a superseded
    page's support passes to its public successors; an archived, removed or retired page's is
    withdrawn; any other page's counts."""
    if not row:
        return {"withdrawn": None, "passes_to": []}
    if row["superseded"]:
        return {"withdrawn": "superseded", "passes_to": row["superseded_by"]}
    if RETIRED in row["publish_state"]:
        return {"withdrawn": RETIRED, "passes_to": []}
    if row["currency"] in P.CURRENCY_WITHDRAWN:
        return {"withdrawn": row["currency"], "passes_to": []}
    return {"withdrawn": None, "passes_to": []}


async def load_relocations(
    gx: GraphHandle,
) -> Dict[str, List[str]]:  # {deliverable id: the repo-copy URLs its RELOCATED_TO edges name}
    """Every RELOCATED_TO edge, its web Reference read back to the URL it keys."""
    res = await graph_task(gx.queue, gx.graph_id, "query_edges",
                           query=EdgeQuery(relation_type=DevRelations.RELOCATED_TO).to_dict())
    raw = getattr(res, "edges", None) or getattr(res, "rows", None) or []
    edges = [e.to_dict() if hasattr(e, "to_dict") else dict(e) for e in raw]
    refs = await F.load_nodes(gx, sorted({str(e["target_id"]) for e in edges})) if edges else {}
    out: Dict[str, List[str]] = {}
    for e in edges:
        url = str(F.prop(refs.get(str(e["target_id"])), "foreign_id") or e["target_id"])
        out.setdefault(str(e["source_id"]), []).append(url)
    return out


async def load_standing(
    gx: GraphHandle,
) -> Dict[str, Any]:  # project_standing's result over the live graph
    """Every page's standing over the live graph."""
    from .lens import LENS_LABEL
    from .purenotes import note_publish_states, note_types, public_deliverables
    from .site import stated
    types = await note_types(gx)
    nodes = await F.load_nodes(gx, sorted(types)) if types else {}
    notes = {i: {"slug": str(F.prop(nodes.get(i), "slug") or ""), "title": stated(nodes.get(i), "title") or "",
                 "origin": t.get("origin", ""), "kind": t.get("kind", "")} for i, t in types.items()}
    slot = [a for a in await F.load_assertions(gx) if F.prop(a, "predicate") == P.CURRENCY]
    currency: Dict[str, List[str]] = {}
    supersedes = await F.load_supersedes(gx)
    for a in F.active_assertions(slot, supersedes):
        currency.setdefault(str(F.prop(a, "subject_id")), []).append(str(F.prop(a, "value")))
    others = sorted({s for s, t in supersedes if t in notes and s not in notes})
    found = await F.load_nodes(gx, others) if others else {}
    pages = {i: {"slug": str(F.prop(n, "site_path") or F.prop(n, "slug") or F.prop(n, "key") or ""),
                 "title": stated(n, "title") or str(F.prop(n, "name") or "")}
             for i, n in found.items() if n is not None and F.label(n) == LENS_LABEL}
    return project_standing(notes, currency, await note_publish_states(gx), await public_deliverables(gx),
                            supersedes, await load_relocations(gx), pages)


def standing_label(
    row: Optional[Dict[str, Any]],  # A standing row, or None
) -> str:  # The row's standing as the evidence reads print it ("" = none)
    """One page's standing in a word or two: the label, `superseded` beside it when it is."""
    if not row:
        return ""
    return " · ".join(x for x in (row["standing"], "superseded" if row["superseded"] else "") if x)


def matches(
    row: Optional[Dict[str, Any]],  # A standing row, or None
    want: str,                      # A standing label, `superseded`, or `pending`
) -> bool:  # True when the row carries it
    """The `--standing` filter the standing, traffic and inbound-links reads share."""
    if not row:
        return False
    return (row["standing"] == want or (want == "superseded" and row["superseded"])
            or (want == "pending" and row["pending"]))


async def standing_report(
    gx: GraphHandle,
    *,
    standing: Optional[str] = None,  # Only rows carrying this label (`superseded` / `pending` too)
    evidence: bool = True,           # Join each row's traffic, inbound links and claims
    since: Optional[str] = None,     # The traffic's first month ('YYYY-MM'; None = every month)
) -> Dict[str, Any]:  # project_standing's result (+ evidence per row) | {error}
    """The disposition worklist as a read (9a21e345): each page's standing, and with `evidence`
    its traffic (Cloudflare visits, Search Console clicks / impressions), its inbound links
    (linking pages, hosts, Google's total, the pages our fetch verified) and the claims it
    supports -- each counting, withdrawn or passed on by its standing."""
    if standing and standing not in STANDINGS + ("superseded", "pending"):
        return {"error": f"no standing `{standing}` ({', '.join(STANDINGS + ('superseded', 'pending'))})"}
    res = await load_standing(gx)
    if evidence:
        from .claims import claims_report
        from .evidence import traffic_report
        from .inbound import inbound_report
        tr = {h["id"]: h for h in (await traffic_report(gx, since=since, standings=res))["holders"]}
        ib = {h["id"]: h for h in (await inbound_report(gx, standings=res))["holders"]}
        cl: Dict[str, List[Dict[str, Any]]] = {}
        for c in (await claims_report(gx, standing=res))["claims"]:
            for kind, items in c["supports"].items():
                for it in items:
                    if not it.get("via"):   # a passed-on support is listed on the page that gave it
                        cl.setdefault(it["id"], []).append({"claim": c["key"], "kind": kind})
            for it in c.get("withdrawn") or []:
                cl.setdefault(it["id"], []).append(
                    {"claim": c["key"], "kind": it["kind"], "withdrawn": it["withdrawn"],
                     **({"passed_to": [s["slug"] for s in it["passes_to"]]} if it.get("passes_to") else {})})
        for r in res["rows"]:
            t, i = tr.get(r["id"]) or {}, ib.get(r["id"])
            cf, sc = (t.get("cloudflare") or {}).get("visits"), t.get("search_console") or {}
            r["traffic"] = {"visits": cf, "clicks": sc.get("clicks"), "impressions": sc.get("impressions")}
            r["inbound"] = ({"pages": len(i["pages"]), "hosts": len(i["sites"]), "google_links": i["google_links"],
                             "verified": i["verified"]} if i else None)
            r["claims"] = cl.get(r["id"], [])
    if standing:
        res["rows"] = [r for r in res["rows"] if matches(r, standing)]
    res.pop("by_id", None)
    res["filter"] = standing
    return res
