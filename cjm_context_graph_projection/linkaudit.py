"""Link liveness audit — the derived worklist for the EXTERNAL links a note carries
(finding 5761f954; living-outputs class 4 beside capture 42b4d32a's three).

A published page's outbound links rot silently: a page moves (a 301 within the same
site), dies (4xx / 5xx / no answer), or its URL is TAKEN OVER by a third party (a
redirect chain that leaves the registrable domain — the afabrega.com origin-of-the-
school-system URL 301-chained through five unrelated domains while the public website
still carried it). Nothing on the graph watched. This module is the read-only scan:
extract every external URL from every Note's frontmatter + sections, probe each once
(redirects followed HOP BY HOP so the chain is the evidence), classify, and report a
per-link worklist keyed by the notes that carry it. Nothing here writes; the fixes are
the edit lane (born notes) or a source edit + re-ingest (the archive corpus, 8d9de793).

States: `ok` (answers 2xx at the URL given, or only a scheme / www / trailing-slash
normalization away) · `moved` (a redirect that stays within the registrable domain —
propose the canonical) · `offsite` (any hop crosses to a different registrable domain
— the hijack signature; a human judges whether the new home is legitimate) · `dead`
(no cross-domain hop and the chain ends in 4xx / 5xx / a network error).

The Wayback Machine's CDX index brackets HOW LONG a link has been bad: the last
capture that answered 200 is the last-known-good date; the first later capture with
another status (when one exists) is the first-known-bad. `--wayback` asks it for the
non-ok rows only (one bounded GET each). Our own audits will bracket future rot more
tightly once their results are journaled as facts — the graph-side follow-up rides
the deliverable-types design item (8ea8d3a9).
"""

import asyncio
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from cjm_context_graph_primitives.query import PropertyPredicate
from cjm_dev_graph_schema.vocab import DevNodeKinds

from . import factlayer as F
from .runtime import GraphHandle

# Markdown link targets `](http…)`, autolinks `<http…>`, and bare URLs — http(s) only;
# relative / anchor / mailto links are the graph's own business (REFERENCES edges), not
# the web's.
LINK_RE = re.compile(
    r"\]\((https?://[^\s)]+)\)"          # [text](https://…)
    r"|<(https?://[^\s>]+)>"             # <https://…>
    r"|(?<![<\"'])(https?://[^\s<>\]\"']+)"  # bare https://… (a `)` may belong to the URL; unbalanced ones are trimmed)
)
_TRAILING = ".,;:!?'\""
# Two-level public suffixes the naive last-two-labels rule would get wrong; a small
# list on purpose (the full PSL is a dependency this scan does not need to be useful).
_TWO_LEVEL = {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au",
              "co.jp", "co.nz", "com.br", "co.in", "co.za", "com.mx", "co.kr", "com.sg"}
USER_AGENT = "cjm-context-graph link-audit/0.1"
CDX_ENDPOINT = "http://web.archive.org/cdx/search/cdx"
STATE_ORDER = ("offsite", "dead", "moved", "ok")


def extract_external_links(
    text: str,  # Markdown text (a section's raw, or frontmatter)
) -> List[str]:  # Distinct http(s) URLs in first-seen order, trailing punctuation stripped
    """Pull the external URLs out of markdown text.

    Three shapes: `[text](url)`, `<url>`, bare. A bare URL swallows trailing sentence
    punctuation and an unbalanced closing paren (`(see https://x.org/a).` -> `https://x.org/a`),
    so those are trimmed; a paren that closes one opened INSIDE the URL is kept
    (Wikipedia-style `Foo_(bar)`)."""
    out: List[str] = []
    seen: set = set()
    for m in LINK_RE.finditer(text or ""):
        url = next(g for g in m.groups() if g)
        url = url.rstrip(_TRAILING)
        while url.endswith(")") and url.count(")") > url.count("("):
            url = url[:-1].rstrip(_TRAILING)
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def registrable_domain(
    url: str,  # Any http(s) URL
) -> str:  # The owner-level domain (`www.blog.example.co.uk` -> `example.co.uk`), lowercased
    """The registrable (owner-level) domain of a URL — the unit a hijack crosses."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".")
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    tail2 = ".".join(labels[-2:])
    if tail2 in _TWO_LEVEL and len(labels) >= 3:
        return ".".join(labels[-3:])
    return tail2


def _normalized(url: str) -> str:  # scheme / www. / trailing slash / fragment stripped, for equivalence
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (p.path or "/").rstrip("/") or "/"
    return f"{host}{path}" + (f"?{p.query}" if p.query else "")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface each redirect as its HTTPError so the chain is walked hop by hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 — urllib hook
        return None


def probe_url(
    url: str,             # The URL to probe
    timeout: float = 10,  # Per-hop timeout, seconds
    max_hops: int = 8,    # Redirect hops to follow before giving up
) -> Dict[str, Any]:  # {"url", "chain": [[url, code], …], "final_url", "final_code", "error"}
    """Fetch a URL following redirects ONE HOP AT A TIME (GET with a browser-like
    accept; many hosts answer HEAD with 403/405), recording every hop's status so a
    cross-domain chain is visible as evidence, not collapsed into a final answer."""
    opener = urllib.request.build_opener(_NoRedirect)
    chain: List[List[Any]] = []
    cur, error = url, None
    for _ in range(max_hops + 1):
        req = urllib.request.Request(cur, headers={"User-Agent": USER_AGENT,
                                                  "Accept": "text/html,*/*;q=0.8"})
        try:
            with opener.open(req, timeout=timeout) as resp:
                chain.append([cur, int(resp.status)])
                break
        except urllib.error.HTTPError as e:
            chain.append([cur, int(e.code)])
            loc = e.headers.get("Location") if e.code in (301, 302, 303, 307, 308) else None
            if not loc:
                break
            cur = urllib.parse.urljoin(cur, loc)
            continue
        except Exception as e:  # network / TLS / DNS — the answer is "no answer"
            error = f"{type(e).__name__}: {e}"
            break
    else:
        error = f"more than {max_hops} redirects"
    final_url = chain[-1][0] if chain else url
    final_code = chain[-1][1] if chain else None
    return {"url": url, "chain": chain, "final_url": final_url,
            "final_code": final_code, "error": error}


def classify_link(
    probe: Dict[str, Any],  # A `probe_url` result
) -> str:  # ok | moved | offsite | dead
    """Classify a probe: a hop leaving the registrable domain is `offsite` (the hijack
    signature, whatever the final status); otherwise a failing end is `dead`, a
    same-domain relocation is `moved`, and a 2xx at (an equivalent of) the URL is `ok`."""
    url = probe["url"]
    home = registrable_domain(url)
    chain = probe.get("chain") or []
    if any(registrable_domain(u) != home for u, _ in chain):
        return "offsite"
    code = probe.get("final_code")
    if probe.get("error") or code is None or code >= 400:
        return "dead"
    if _normalized(probe.get("final_url") or url) != _normalized(url):
        return "moved"
    return "ok"


def bracket_from_cdx(
    rows: List[List[str]],  # CDX json rows (header row first): [timestamp, statuscode]
) -> Dict[str, Optional[str]]:  # {"last_good": ts, "first_bad": ts, "captures": n}
    """Bracket the rot from a Wayback capture list: the last capture that answered
    200 is the last-known-good; the first later capture with any other status is the
    first-known-bad (None when the archive has not caught the failure yet — then the
    bracket is [last_good, today])."""
    caps = [(r[0], r[1]) for r in rows[1:] if len(r) >= 2]
    last_good = None
    for ts, code in caps:
        if code == "200":
            last_good = ts
    first_bad = None
    if last_good is not None:
        for ts, code in caps:
            if ts > last_good and code != "200":
                first_bad = ts
                break
    return {"last_good": last_good, "first_bad": first_bad, "captures": len(caps)}


def wayback_bracket(
    url: str,             # The URL to look up
    timeout: float = 20,  # Request timeout, seconds
) -> Dict[str, Any]:  # `bracket_from_cdx` result (+ "error" when the index did not answer)
    """Ask the Wayback CDX index for the URL's capture history and bracket the rot."""
    q = urllib.parse.urlencode({"url": url, "output": "json", "fl": "timestamp,statuscode"})
    req = urllib.request.Request(f"{CDX_ENDPOINT}?{q}", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
        rows = json.loads(body) if body.strip() else []
    except Exception as e:
        return {"last_good": None, "first_bad": None, "captures": 0,
                "error": f"{type(e).__name__}: {e}"}
    return bracket_from_cdx(rows)


async def link_audit(
    gx: GraphHandle,
    *,
    note: Optional[str] = None,   # Restrict to one Note: a full node id, or a slug / title substring
    timeout: float = 10,          # Per-hop probe timeout, seconds
    workers: int = 8,             # Concurrent probes
    wayback: bool = False,        # Bracket every non-ok link against the Wayback CDX index
    include_ok: bool = False,     # List the ok rows too (default: counted only)
    limit: Optional[int] = None,  # Cap the number of distinct URLs probed (a dry sizing run)
) -> Dict[str, Any]:  # {"counts", "rows", "notes", "skipped"}
    """The audit: enumerate Notes (frontmatter + every Section's raw), extract the
    distinct external URLs with the notes that carry each, probe them concurrently,
    classify, optionally bracket the bad ones against the Wayback index, and return
    the worklist sorted worst-first (offsite, dead, moved, ok)."""
    notes = await F.load_label(gx, DevNodeKinds.NOTE)
    carriers: Dict[str, List[Dict[str, str]]] = {}
    order: List[str] = []
    scanned = 0
    for n in notes:
        nid = F.nid(n)  # typed GraphNode or wire dict — the fact layer knows both
        slug, title = str(F.prop(n, "slug") or ""), str(F.prop(n, "title") or "")
        if note and not (nid == note or note in slug or note.lower() in title.lower()):
            continue
        scanned += 1
        texts = [str(F.prop(n, "frontmatter_raw") or "")]
        secs = await F.load_label_where(gx, DevNodeKinds.SECTION,
                                        [PropertyPredicate("note_id", "eq", nid)])
        texts.extend(str(F.prop(s, "raw") or "") for s in secs)
        for url in extract_external_links("\n".join(texts)):
            if url not in carriers:
                carriers[url] = []
                order.append(url)
            if not any(c["id"] == nid for c in carriers[url]):
                carriers[url].append({"id": nid, "slug": slug, "title": title})
    urls = order[:limit] if limit else order
    sem = asyncio.Semaphore(max(1, workers))

    async def _probe(u: str) -> Dict[str, Any]:
        async with sem:
            return await asyncio.to_thread(probe_url, u, timeout)

    probes = await asyncio.gather(*[_probe(u) for u in urls])
    rows: List[Dict[str, Any]] = []
    counts = {"notes": scanned, "links": len(order), "probed": len(urls),
              "ok": 0, "moved": 0, "offsite": 0, "dead": 0}
    for p in probes:
        state = classify_link(p)
        counts[state] += 1
        row = {"url": p["url"], "state": state, "final_url": p["final_url"],
               "final_code": p["final_code"], "hops": max(0, len(p["chain"]) - 1),
               "chain": p["chain"], "error": p["error"], "notes": carriers[p["url"]]}
        if wayback and state != "ok":
            row["wayback"] = await asyncio.to_thread(wayback_bracket, p["url"])
        if state != "ok" or include_ok:
            rows.append(row)
    rank = {s: i for i, s in enumerate(STATE_ORDER)}
    rows.sort(key=lambda r: (rank[r["state"]], r["url"]))
    return {"counts": counts, "rows": rows, "skipped": len(order) - len(urls),
            "wayback": wayback}
