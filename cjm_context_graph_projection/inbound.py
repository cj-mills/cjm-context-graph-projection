"""The inbound links (design 7f315830 (6), ruling a3c02fb1): who links the site, observed twice --
Google's report and our own fetch -- as dated observations, never typed in.

THREE PULLS, FILES ONLY. `pull-evidence search-console-links` reads the Search Console Links
drill-downs (no API, no adequate export) through a signed-in Chrome over the DevTools protocol:
the top linked pages, each page's linking sites, each (page, site)'s linking pages, every table
saved as rendered with its pager total, so a truncated table is recorded, never hidden.
`pull-evidence links-fetch` fetches every linking page the latest drill-downs and Links exports
name -- politely: robots.txt honored and kept, one request per host every FETCH_SPACING seconds,
a user agent naming the site -- and keeps each response's bytes gzipped with an envelope.
`import-export` moves hand exports (the Links CSVs, the Performance zip, Cloudflare's PDFs) into a
dated snapshot byte for byte, nothing left loose; they are checks, never measures.

ONE INGEST, JOURNALED. `ingest-evidence` reads a links snapshot and its op carries the computed
run (the harvest / judge shape): replay never fetches and never opens a browser. The linking page
is a web Reference (ReferenceNode.WEB, the URL normalized by `normalize_url`); each observation
that it links a site URL is a REFERENCES edge dated by its snapshot (`inbound_link_edge`, one per
(reference, web_path, method, date)); what a page itself showed -- listed in an export with
Google's last crawl, or the fetch's outcome -- is a `link_observation` fact on the Reference; and
Google's own totals per target are `inbound_count` facts on the web_path, the check the observed
pages reconcile against. A holder's inbound links are DERIVED through the path ownership, as its
traffic is."""

import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import re
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from cjm_context_graph_layer.grammar import make_edge
from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.journal import op_now
from cjm_context_graph_primitives.provenance import SourceRef
from cjm_context_graph_primitives.query import EdgeQuery, NodeQuery, PropertyPredicate
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import reference_node_id
from cjm_dev_graph_schema.nodes import (AssertionNode, EntityNode, FactSlotNode, inbound_link_edge,
                                        ReferenceNode)
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from . import factlayer as F
from .evidence import (MANIFEST, read_snapshot, snapshot_id, snapshot_record, web_path_id,
                       write_snapshot)
from .runtime import GraphHandle
from .sitelinks import site_path_key

SC_DRILLDOWN = "https://search.google.com/search-console/links/drilldown"
FETCH_SPACING = 2.0            # seconds between two requests to one host (ruling a3c02fb1 (3))
FETCH_TIMEOUT = 30             # seconds per request
FETCH_MAX_BYTES = 20_000_000   # a body beyond this is cut and marked truncated
FETCH_HOSTS_AT_ONCE = 12       # hosts fetched concurrently (each host stays sequential)
PAGE_SETTLE = 30.0             # seconds a drill-down may take to render its table
_PUA = re.compile("[-]")   # the drill-down's row icons (copy / open) read as private-use glyphs
_REFUSED = {401, 403, 429, 451, 999}   # LinkedIn answers 999 to a client it will not serve
_GONE = {404, 410}


# ---------------------------------------------------------------- URLs

def clean(text: str) -> str:  # A table cell without its icon glyphs and edge whitespace
    return _PUA.sub("", str(text or "")).strip()


def normalize_url(
    url: str,  # A linking page's URL as an export or a drill-down states it
) -> str:  # The web Reference's foreign id: scheme and host lowercased, fragment dropped, query kept (a3c02fb1 (2))
    p = urllib.parse.urlsplit(clean(url))
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", p.query, ""))


def site_key(
    url: str,   # A URL that may be the site's
    host: str,  # The site's host (evidence.host)
) -> Optional[str]:  # The web_path key (the site-link resolver's equivalence key), None for another host
    p = urllib.parse.urlsplit(clean(url))
    if p.netloc.lower() not in (host, f"www.{host}"):
        return None
    return site_path_key(p.path or "/")


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower()


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


# ---------------------------------------------------------------- the drill-downs (a signed-in browser)

class Browser:
    """A Chrome under the dedicated Search Console profile, driven over the DevTools protocol (one
    page target). Headless by default; `headed` shows the window, for signing in."""

    def __init__(self, profile: str, *, exe: str = "google-chrome", headed: bool = False):
        import websocket   # websocket-client: the DevTools protocol's transport
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        Path(profile).expanduser().mkdir(parents=True, exist_ok=True)
        argv = [exe, f"--user-data-dir={Path(profile).expanduser()}", f"--remote-debugging-port={self.port}",
                "--no-first-run", "--no-default-browser-check", "about:blank"]
        if not headed:
            argv.insert(1, "--headless=new")
        self.proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ws_url = None
        for _ in range(60):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=2))
                ws_url = next((t["webSocketDebuggerUrl"] for t in tabs if t.get("type") == "page"), None)
                if ws_url:
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.5)
        if not ws_url:
            self.close()
            raise RuntimeError(f"chrome did not open its DevTools port (is the profile {profile} in use by "
                               "another Chrome window? close it first)")
        self.ws = websocket.create_connection(ws_url, timeout=60, suppress_origin=True)
        self.n = 0

    def call(self, method: str, **params: Any) -> Dict[str, Any]:
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self.n:
                if "error" in m:
                    raise RuntimeError(f"devtools {method}: {m['error']}")
                return m.get("result") or {}

    def js(self, expr: str) -> Any:
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return (r.get("result") or {}).get("value")

    def goto(self, url: str) -> None:
        self.call("Page.navigate", url=url)

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


# The rendered drill-down: the table's header and rows, its pager total, the page's own total, and
# the region holding them as rendered (the observation; the document's scripts are not)
_READ_TABLE = r"""(() => {
  if (location.host !== 'search.google.com') return {signin: location.href};
  const t = document.querySelector('table');
  if (!t) return {pending: true};
  let region = t;
  while (region.parentElement && !/Total/.test(region.innerText)) region = region.parentElement;
  const text = region.innerText;
  return {head: [...t.querySelectorAll('thead th')].map(x => x.innerText.trim()),
          rows: [...t.querySelectorAll('tbody tr')].map(tr => [...tr.children].map(c => c.innerText.trim())),
          pager: (text.match(/\d+-\d+ of ([\d,]+)/) || [])[1] || null,
          total: (text.match(/Total[^\n]*\n(?:[^\n]*\n){0,4}?\s*([\d,]+)\s*(?:\n|$)/) || [])[1] || null,
          html: region.outerHTML, url: location.href};
})()"""


def _drill_url(prop: str, target: str = "", site: str = "") -> str:
    q = urllib.parse.urlencode({"resource_id": prop, "type": "EXTERNAL", "target": target, "domain": site})
    return f"{SC_DRILLDOWN}?{q}"


def _num(s: Optional[str]) -> Optional[int]:
    return int(s.replace(",", "")) if s else None


def html_total(
    html: str,  # A drill-down's kept region, as rendered
) -> Optional[int]:  # The page's own "Total ..." figure, read past its label's icon and help text
    """The drill-down's total from the kept bytes (the snapshot is the source): the first bare
    number after a `Total` label, with any icon glyph or help sentence between them skipped."""
    text = re.sub(r"<[^>]+>", "|", html or "")
    m = re.search(r"Total[^|]*\|(?:[^|]*\|){0,4}?\s*([\d,]+)\s*\|", re.sub(r"\|+", "|", text))
    return _num(m.group(1)) if m else None


def read_drilldown(
    browser: Any,               # A Browser (or a double with goto / js)
    url: str,                   # The drill-down to read
    *,
    settle: float = PAGE_SETTLE,
    signin_wait: float = 0.0,   # Seconds to wait for a sign-in in a headed window (0 = refuse at once)
    poll: float = 0.5,
) -> Dict[str, Any]:  # {head, rows, pager, total, html, url} or {error}
    """Open one drill-down and read its table once it is stable: two consecutive reads agree and the
    rows reach the pager total (every row is in the DOM whatever the page size), or the settle time
    passes -- then the table is what it is, and the envelope records rows against the pager."""
    browser.goto(url)
    deadline, last, signin_until = time.monotonic() + settle, None, time.monotonic() + signin_wait
    while True:
        got = browser.js(_READ_TABLE) or {}
        if got.get("signin"):
            if time.monotonic() < signin_until:
                time.sleep(2.0)
                if browser.js("location.host") == "search.google.com" and browser.js("location.href") != url:
                    browser.goto(url)
                deadline = time.monotonic() + settle
                continue
            return {"error": "the browser profile is not signed in to Search Console (run `pull-evidence "
                             "search-console-links --headed` and sign in)"}
        if not got.get("pending"):
            stable = last is not None and got["rows"] == last["rows"] and got["pager"] == last["pager"]
            full = got["pager"] is not None and len(got["rows"]) >= _num(got["pager"])
            if (stable and full) or (stable and time.monotonic() > deadline):
                return got
            last = got
        elif time.monotonic() > deadline:
            return {"error": f"no table rendered at {url}"}
        time.sleep(poll)


def pull_drilldowns(
    conf: Dict[str, Any],   # The evidence config block (search_console.property, .browser_profile)
    *,
    browser: Any = None,    # A Browser or a double (default: a Chrome under the profile)
    headed: bool = False,
    signin_wait: float = 300.0,
    settle: float = PAGE_SETTLE,
) -> Dict[str, Dict[str, Any]]:  # {file name: envelope}
    """Walk the Links drill-downs: the top linked pages, each page's linking sites, each (page,
    site)'s linking pages (ruling a3c02fb1 (A)). Every table is one envelope; a refusal anywhere
    stops the pull (nothing partial is written)."""
    sc = conf.get("search_console") or {}
    prop = sc["property"]
    own = browser is None
    if own:
        browser = Browser(sc.get("browser_profile") or "~/.config/cjm/chrome-search-console",
                          exe=sc.get("browser") or "google-chrome", headed=headed)
    out: Dict[str, Dict[str, Any]] = {}

    def read(url: str, **meta: Any) -> Dict[str, Any]:
        got = read_drilldown(browser, url, settle=settle, signin_wait=signin_wait if headed else 0.0)
        if got.get("error"):
            raise RuntimeError(got["error"])
        return {"source": "search-console-links", "property": prop, "shape": meta.pop("level"), **meta,
                "url": url, "pulled_at": _now(), "head": got["head"], "rows": got["rows"],
                "row_count": len(got["rows"]), "pager_total": _num(got["pager"]), "total": _num(got["total"]),
                "html": got["html"]}
    try:
        top = read(_drill_url(prop), level="targets")
        out["targets.json"] = top
        for i, row in enumerate(top["rows"]):
            target = clean(row[0])
            sites = read(_drill_url(prop, target), level="sites", target=target)
            out[f"sites-{i:03d}.json"] = sites
            for j, srow in enumerate(sites["rows"]):
                site = clean(srow[0])
                out[f"pages-{i:03d}-{j:03d}.json"] = read(_drill_url(prop, target, site), level="pages",
                                                          target=target, site=site)
    finally:
        if own:
            browser.close()
    return out


# ---------------------------------------------------------------- the verify fetch (files only)

def _latest(root: str, source: str) -> Optional[str]:
    d = Path(root) / source
    snaps = sorted(m.parent.name for m in d.glob(f"*/{MANIFEST}")) if d.exists() else []
    return f"{source}/{snaps[-1]}" if snaps else None


def _export_rows(snap: Dict[str, Any]) -> Dict[str, List[Dict[str, str]]]:
    """A Search Console export snapshot's CSVs by role: latest (Linking page, Last crawled), sample
    (Linking page) and targets (Target page, Incoming links, Linking sites)."""
    out: Dict[str, List[Dict[str, str]]] = {}
    for name, path in (snap.get("blobs") or {}).items():
        role = ("latest" if "Latest links" in name else "sample" if "More sample links" in name
                else "targets" if "Top target pages" in name else None)
        if role and name.endswith(".csv"):
            text = Path(path).read_bytes().decode("utf-8-sig")
            out[role] = list(csv.DictReader(io.StringIO(text)))
    return out


def fetch_list(
    root: str,  # The evidence root
) -> List[str]:  # Every linking page the latest drill-downs and the latest Links export name, normalized
    urls: Set[str] = set()
    key = _latest(root, "search-console-links")
    if key:
        snap = read_snapshot(root, key)
        if snap.get("error"):
            raise RuntimeError(snap["error"])
        for env in snap["envelopes"].values():
            if env.get("shape") == "pages":
                urls |= {normalize_url(r[0]) for r in env["rows"] if r and clean(r[0])}
    key = _latest(root, "search-console-export")
    if key:
        snap = read_snapshot(root, key)
        if snap.get("error"):
            raise RuntimeError(snap["error"])
        for role, rows in _export_rows(snap).items():
            if role in ("latest", "sample"):
                urls |= {normalize_url(r["Linking page"]) for r in rows if clean(r.get("Linking page"))}
    return sorted(urls)


def _get(url: str, ua: str) -> Dict[str, Any]:
    """One GET: status, final URL, content type and body (None on any failure, the reason kept). The
    request percent-encodes what a URL may not carry raw (a space in a search page's query); the
    Reference keeps the URL as the export states it."""
    wire = urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%~")
    try:
        req = urllib.request.Request(wire, headers={"User-Agent": ua, "Accept": "text/html,application/xhtml+xml,"
                                                    "application/pdf;q=0.9,*/*;q=0.5"})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
            body = r.read(FETCH_MAX_BYTES + 1)
            return {"status": r.status, "final_url": r.geturl(), "content_type": r.headers.get("Content-Type", ""),
                    "body": body[:FETCH_MAX_BYTES], "truncated": len(body) > FETCH_MAX_BYTES}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "final_url": e.geturl() or url, "content_type": e.headers.get("Content-Type", "") if e.headers else "",
                "body": None, "reason": f"HTTP {e.code}"}
    except Exception as e:   # a page that cannot be asked is an observation, never a crash of the run
        return {"status": None, "final_url": url, "content_type": "", "body": None,
                "reason": f"{type(e).__name__}: {getattr(e, 'reason', e)}"[:200]}


def pull_fetch(
    conf: Dict[str, Any],   # The evidence config block (root, host)
    urls: List[str],        # The linking pages to fetch (fetch_list)
    *,
    get: Optional[Callable[[str, str], Dict[str, Any]]] = None,   # (url, user agent) -> response; default urllib
    spacing: float = FETCH_SPACING,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, bytes]]:  # ({file name: envelope}, {file name: gzipped body})
    """Fetch every page politely (ruling a3c02fb1 (3)): each host's robots.txt read once and kept,
    a page it disallows recorded as refused by robots and never asked, one request per host every
    `spacing` seconds, the bytes kept gzipped. Hosts run concurrently, each one sequential."""
    get = get or _get
    ua = f"cjm-evidence/1.0 (+https://{conf['host']}/; verifying links to {conf['host']})"
    by_host: Dict[str, List[str]] = {}
    for u in urls:
        by_host.setdefault(_host(u), []).append(u)
    index = {u: i for i, u in enumerate(sorted(urls))}

    def host_run(host: str) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, bytes]]:
        envs: Dict[str, Dict[str, Any]] = {}
        blobs: Dict[str, bytes] = {}
        scheme = urllib.parse.urlsplit(by_host[host][0]).scheme or "https"
        robots_url = f"{scheme}://{host}/robots.txt"
        r = get(robots_url, ua)
        text = (r.get("body") or b"").decode("utf-8", "replace") if r.get("status") == 200 else ""
        rp = urllib.robotparser.RobotFileParser()
        if r.get("status") in (401, 403):
            rp.disallow_all = True      # the convention urllib's own reader keeps: an access-controlled robots.txt
        else:
            rp.parse(text.splitlines())   # a missing or unreachable robots.txt allows (the convention)
        safe = re.sub(r"[^a-z0-9.-]", "_", host)
        envs[f"robots-{safe}.json"] = {"source": "links-fetch", "shape": "robots", "host": host, "url": robots_url,
                                       "pulled_at": _now(), "status": r.get("status"), "text": text,
                                       "reason": r.get("reason")}
        last = 0.0
        for u in by_host[host]:
            n = index[u]
            env: Dict[str, Any] = {"source": "links-fetch", "shape": "page", "url": u, "user_agent": ua}
            if not rp.can_fetch(ua, u):
                envs[f"page-{n:04d}.json"] = {**env, "pulled_at": _now(), "robots": "disallowed", "status": None,
                                              "final_url": u, "content_type": "", "body": None}
                continue
            wait = last + spacing - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                got = get(u, ua)
            except Exception as e:   # an injected getter's failure is the page's, never the run's
                got = {"status": None, "final_url": u, "content_type": "", "body": None,
                       "reason": f"{type(e).__name__}: {e}"[:200]}
            last = time.monotonic()
            body = got.pop("body", None)
            if body is not None:
                blobs[f"page-{n:04d}.body.gz"] = gzip.compress(body, mtime=0)
            envs[f"page-{n:04d}.json"] = {**env, "pulled_at": _now(), "robots": "allowed", **got,
                                          "body": f"page-{n:04d}.body.gz" if body is not None else None,
                                          "body_sha256": hashlib.sha256(body).hexdigest() if body is not None else None}
        return envs, blobs

    envelopes: Dict[str, Dict[str, Any]] = {}
    blobs: Dict[str, bytes] = {}
    with ThreadPoolExecutor(max_workers=FETCH_HOSTS_AT_ONCE) as pool:
        for envs, bl in pool.map(host_run, sorted(by_host)):
            envelopes.update(envs)
            blobs.update(bl)
    return envelopes, blobs


def pull_links(
    conf: Dict[str, Any],   # The evidence config block
    source: str,            # search-console-links | links-fetch
    *,
    today: Optional[dt.date] = None,
    headed: bool = False,
    browser: Any = None,
    get: Optional[Callable] = None,
    spacing: float = FETCH_SPACING,
) -> Dict[str, Any]:  # {key, files} | {key, skipped} | {error}
    """Pull one links source into a new snapshot (files only, never the graph); a source already
    pulled today is skipped, as the traffic pulls are."""
    today = today or dt.date.today()
    key = f"{source}/{today.isoformat()}"
    if (Path(conf["root"]) / key).exists():
        return {"key": key, "skipped": "already pulled today"}
    try:
        if source == "search-console-links":
            if not (conf.get("search_console") or {}).get("property"):
                return {"error": "evidence.search_console.property is not configured"}
            envelopes, blobs = pull_drilldowns(conf, browser=browser, headed=headed), {}
        else:
            urls = fetch_list(conf["root"])
            if not urls:
                return {"error": "nothing to fetch: no search-console-links or search-console-export snapshot names a linking page"}
            envelopes, blobs = pull_fetch(conf, urls, get=get, spacing=spacing)
        manifest = write_snapshot(conf["root"], source, today.isoformat(), envelopes, blobs=blobs)
    except (RuntimeError, OSError, KeyError) as e:
        return {"error": f"{source} pull: {e}"[:400]}
    return {"key": key, "files": manifest["files"],
            "pages": sum(1 for e in envelopes.values() if e.get("shape") in ("page", "pages"))}


# ---------------------------------------------------------------- hand exports (files only)

def import_export(
    conf: Dict[str, Any],      # The evidence config block
    source: str,               # One of P.EXPORT_SOURCES
    files: List[str],          # The exported files, as downloaded
    *,
    date: Optional[str] = None,  # The export date ('YYYY-MM-DD'; default: the files' latest modification day)
    keep: bool = False,          # Leave the originals in place (default: moved, nothing left loose)
) -> Dict[str, Any]:  # {key, files} or {error}
    """Keep hand exports as a dated snapshot, byte for byte (ruling a3c02fb1 (4)): each file copied
    and hash-checked, an `export.json` envelope naming each file's original name and modification
    time, a manifest of hashes; the originals then removed. An existing snapshot refuses."""
    if source not in P.EXPORT_SOURCES:
        return {"error": f"an export source is one of {P.EXPORT_SOURCES}: {source!r}"}
    paths = [Path(f).expanduser() for f in files]
    missing = [str(p) for p in paths if not p.is_file()]
    if missing or not paths:
        return {"error": f"no such file(s): {missing or files}"}
    names = [p.name for p in paths]
    if len(set(names)) != len(names):
        return {"error": "two exports share a file name"}
    mtimes = {p.name: dt.datetime.fromtimestamp(p.stat().st_mtime).astimezone() for p in paths}
    day = date or max(mtimes.values()).date().isoformat()
    out = Path(conf["root"]) / source / day
    if out.exists():
        return {"error": f"snapshot {source}/{day} already exists at {out}"}
    blobs = {p.name: p.read_bytes() for p in paths}
    envelope = {"source": source, "shape": "export", "pulled_at": max(mtimes.values()).isoformat(),
                "imported_at": _now(),
                "files": [{"file": n, "modified_at": mtimes[n].isoformat()} for n in sorted(blobs)]}
    manifest = write_snapshot(conf["root"], source, day, {"export.json": envelope}, blobs=blobs)
    for p in paths:   # the copies are written and hashed; only then do the originals go
        if hashlib.sha256((out / p.name).read_bytes()).hexdigest() != hashlib.sha256(blobs[p.name]).hexdigest():
            return {"error": f"{p.name}: the copy does not match the original (left in place)"}
    if not keep:
        for p in paths:
            p.unlink()
    return {"key": f"{source}/{day}", "files": manifest["files"], "moved": not keep}


# ---------------------------------------------------------------- reading what a fetch found

def _html_links(body: bytes, base: str, host: str) -> Tuple[str, Dict[str, Dict[str, Any]]]:
    """A page's title and every link to the site: {web_path key: {urls, anchors}}."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(body, "html.parser")
    title = clean(soup.title.get_text(" ")) if soup.title else ""
    found: Dict[str, Dict[str, Any]] = {}
    for a in soup.find_all("a", href=True):
        url = urllib.parse.urljoin(base, str(a["href"]).strip())
        k = site_key(url, host)
        if k:
            f = found.setdefault(k, {"urls": set(), "anchors": []})
            f["urls"].add(urllib.parse.urldefrag(url)[0])
            text = " ".join(a.get_text(" ").split())[:200]
            if text and text not in f["anchors"]:
                f["anchors"].append(text)
    return title[:300], found


def _pdf_links(body: bytes, host: str) -> Tuple[str, Dict[str, Dict[str, Any]]]:
    """A PDF's title and every link annotation to the site."""
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(body))
    title = clean(str((reader.metadata or {}).get("/Title") or ""))
    found: Dict[str, Dict[str, Any]] = {}
    for page in reader.pages:
        for annot in page.get("/Annots") or []:
            a = annot.get_object()
            act = a.get("/A")
            act = act.get_object() if hasattr(act, "get_object") else (act or {})
            uri = act.get("/URI") if a.get("/Subtype") == "/Link" else None
            k = site_key(str(uri), host) if uri else None
            if k:
                found.setdefault(k, {"urls": set(), "anchors": []})["urls"].add(urllib.parse.urldefrag(str(uri))[0])
    return title[:300], found


def fetch_outcome(env: Dict[str, Any]) -> Tuple[str, str]:  # (outcome, reason) of one fetch envelope
    if env.get("robots") == "disallowed":
        return "refused", "robots.txt disallows it"
    status = env.get("status")
    if status is None:
        return "error", env.get("reason") or "no response"
    if status in _GONE:
        return "gone", f"HTTP {status}"
    if status in _REFUSED:
        return "refused", f"HTTP {status}"
    if status >= 400:
        return "error", f"HTTP {status}"
    return "ok", ""


# ---------------------------------------------------------------- plan (live only) + apply (live and replay)

def _ref(url: str, title: str = "", observed_hash: str = "", observed_at: Optional[float] = None) -> Dict[str, Any]:
    return {"url": url, "title": title, "observed_hash": observed_hash, "observed_at": observed_at}


def plan_links(
    snap: Dict[str, Any],   # A links or export snapshot (read_snapshot)
    host: str,              # The site's host
) -> Dict[str, Any]:  # The run: {kind, snapshot, references, edges, observations, counts, report}
    """What one snapshot observed, computed from its files alone (the op carries it whole):
    - search-console-export: each listed page's `link_observation` (Google's last crawl where the
      export states it) and each target's `inbound_count`; the other exports (the Performance zip,
      Cloudflare's PDFs) land as the snapshot node only;
    - search-console-links: each target's `inbound_count` and one search-console edge per (linking
      page, linked web_path), with the reconciliation of rows against Google's own totals;
    - links-fetch: each page's fetch `link_observation` and one fetch edge per site URL it links."""
    source, date = snap["source"], snap["key"].split("/", 1)[1]
    refs: Dict[str, Dict[str, Any]] = {}
    edges: Dict[Tuple[str, str], Dict[str, Any]] = {}
    observations: Dict[str, Dict[str, Any]] = {}
    counts: Dict[str, Dict[str, Any]] = {}
    report: Dict[str, Any] = {}
    unkeyed: Set[str] = set()

    def count(target: str, links: Optional[int], sites: Optional[int]) -> None:
        # Google's totals for one target; two targets one web_path keys (x.html and x/) add up,
        # their number kept in the value (the sites may overlap, so that sum is an upper bound)
        k = site_key(target, host)
        if k is None:
            unkeyed.add(target)
        elif links is not None and sites is not None:
            c = counts.get(k)
            if c is None:
                counts[k] = {"source": "search-console", "date": date, "links": links, "sites": sites}
            else:
                c.update(links=c["links"] + links, sites=c["sites"] + sites, targets=c.get("targets", 1) + 1)

    if source == "search-console-export":
        rows = _export_rows(snap)
        crawled = {normalize_url(r["Linking page"]): clean(r.get("Last crawled")) for r in rows.get("latest", [])}
        for role in ("latest", "sample"):
            for r in rows.get(role, []):
                u = normalize_url(r["Linking page"])
                refs.setdefault(u, _ref(u))
                obs = {"method": "search-console-export", "date": date}
                if crawled.get(u):
                    obs["crawled"] = crawled[u]
                observations[u] = obs
        for r in rows.get("targets", []):
            count(r["Target page"], _num(r.get("Incoming links")), _num(r.get("Linking sites")))
        report = {"listed": len(observations), "targets": len(counts)}
    elif source == "search-console-links":
        envs = snap["envelopes"]
        checks: List[Dict[str, Any]] = []
        sites_of: Dict[str, List[Tuple[str, int]]] = {}
        for env in envs.values():
            if env.get("shape") == "targets":
                for r in env["rows"]:
                    count(clean(r[0]), _num(clean(r[1])), _num(clean(r[2])))
            elif env.get("shape") == "sites":
                sites_of[env["target"]] = [(clean(r[0]), _num(clean(r[1])) or 0) for r in env["rows"]]
        for env in sorted((e for e in envs.values() if e.get("shape") == "pages"), key=lambda e: (e["target"], e["site"])):
            tk = site_key(env["target"], host)
            for r in env["rows"]:
                u = normalize_url(r[0])
                linked = clean(r[1]) if len(r) > 1 and clean(r[1]) not in ("", "N/A") else env["target"]
                k = site_key(linked, host) or tk
                if k is None:
                    unkeyed.add(linked)
                    continue
                refs.setdefault(u, _ref(u))
                e = edges.setdefault((u, k), {"url": u, "key": k, "method": "search-console", "date": date,
                                              "linked_urls": set(), "target_url": env["target"], "anchors": []})
                e["linked_urls"].add(linked)
            if env.get("pager_total") is not None and env["row_count"] < env["pager_total"]:
                checks.append({"target": env["target"], "site": env["site"], "truncated":
                               f"{env['row_count']} of {env['pager_total']} rows"})
            # the page's own total (links from this site) against the sites table's figure for it
            total = env.get("total") if env.get("total") is not None else html_total(env.get("html") or "")
            site_links = dict(sites_of.get(env["target"], [])).get(env["site"])
            if total is not None and site_links is not None and total != site_links:
                checks.append({"target": env["target"], "site": env["site"], "total": total, "site_links": site_links})
            if total is not None and env["row_count"] > total:
                checks.append({"target": env["target"], "site": env["site"], "total": total,
                               "rows": env["row_count"]})   # more linking pages than links: never
        totals = {clean(r[0]): (_num(clean(r[1])), _num(clean(r[2]))) for env in envs.values()
                  if env.get("shape") == "targets" for r in env["rows"]}
        for target, (links, sites) in sorted(totals.items()):   # Google's own totals: a target's sites sum to its links
            listed = sites_of.get(target)
            if listed is None:
                checks.append({"target": target, "missing": "no sites table"})
            elif sum(n for _, n in listed) != links or len(listed) != sites:
                checks.append({"target": target, "links": links, "sites_summed": sum(n for _, n in listed),
                               "sites": sites, "sites_listed": len(listed)})
        report = {"targets": len(counts), "pages": len(refs), "edges": len(edges), "mismatches": checks}
    elif source == "links-fetch":
        outcomes: Dict[str, int] = {}
        for name, env in snap["envelopes"].items():
            if env.get("shape") != "page":
                continue
            u = env["url"]
            outcome, reason = fetch_outcome(env)
            title, found, digest, mentions = "", {}, "", 0
            if outcome == "ok":
                path = (snap.get("blobs") or {}).get(env.get("body") or "")
                body = gzip.decompress(Path(path).read_bytes()) if path else b""
                digest = SourceRef.compute_hash(body)
                # the host anywhere in the bytes: a page that carries the site without a parsed anchor
                # (a README shipped as JSON, a URL in plain text) stays visible, never read as gone
                mentions = body.lower().count(host.encode())
                ctype = str(env.get("content_type") or "").lower()
                try:
                    if "pdf" in ctype or body[:5] == b"%PDF-":
                        title, found = _pdf_links(body, host)
                    elif "html" in ctype or b"<html" in body[:2000].lower():
                        title, found = _html_links(body, env.get("final_url") or u, host)
                    else:
                        outcome, reason = "unreadable", f"content type {ctype or 'unknown'}"
                except Exception as ex:   # a malformed document is an observation, never a crash
                    outcome, reason = "unreadable", f"{type(ex).__name__}: {ex}"[:200]
            at = dt.datetime.fromisoformat(env["pulled_at"]).timestamp()
            refs[u] = _ref(u, title or u, digest, at) if outcome == "ok" else _ref(u)
            obs: Dict[str, Any] = {"method": "fetch", "date": date, "outcome": outcome, "status": env.get("status"),
                                   "targets": len(found)}
            if outcome == "ok":
                obs["mentions"] = mentions
            if reason:
                obs["reason"] = reason
            if env.get("final_url") and normalize_url(env["final_url"]) != u:
                obs["final_url"] = env["final_url"]
            if env.get("truncated"):
                obs["truncated"] = True
            observations[u] = obs
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            for k, f in found.items():
                edges[(u, k)] = {"url": u, "key": k, "method": "fetch", "date": date,
                                 "linked_urls": set(f["urls"]), "target_url": "", "anchors": f["anchors"]}
        report = {"pages": len(observations), "outcomes": outcomes, "edges": len(edges),
                  "linking": len({u for u, _ in edges}),
                  "mentioned_unlinked": sum(1 for o in observations.values()
                                            if o.get("outcome") == "ok" and not o["targets"] and o.get("mentions"))}
    report["unkeyed"] = sorted(unkeyed)
    return {"kind": "links", "snapshot": {"key": snap["key"], "record": snapshot_record(snap)},
            "references": [refs[u] for u in sorted(refs)],
            "edges": [{**e, "linked_urls": sorted(e["linked_urls"])} for _, e in sorted(edges.items())],
            "observations": [{"url": u, "value": P.link_observation_value(observations[u])} for u in sorted(observations)],
            "counts": [{"key": k, "value": P.inbound_count_value(counts[k])} for k in sorted(counts)],
            "report": report}


def _reference_node(r: Dict[str, Any]) -> Dict[str, Any]:
    return ReferenceNode(graph=ReferenceNode.WEB, foreign_id=r["url"], foreign_label="web page",
                         title=r.get("title") or r["url"], observed_hash=r.get("observed_hash") or "",
                         observed_at=r.get("observed_at")).to_graph_node()


async def _standing_facts(gx: GraphHandle, predicate: str) -> Dict[Tuple[str, str], str]:
    rows = await F.load_label_where(gx, DevNodeKinds.ASSERTION, [PropertyPredicate("predicate", "eq", predicate)])
    return {(str(F.prop(a, "subject_id")), P.canonical_value(predicate, str(F.prop(a, "value")))): str(F.nid(a))
            for a in rows}


async def apply_links(
    gx: GraphHandle,
    run: Dict[str, Any],   # plan_links' run (the journaled op carries it whole)
    *,
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {snapshot, references, refreshed, edges, observations, counts}
    """Land one links run in ONE batch: the snapshot node; every web_path and web Reference it
    names (a Reference a fetch re-observed is refreshed in place, as `link` re-observes a foreign
    node; one only listed is never blanked by a later listing); each observation and count as an
    Assertion EVIDENCED_BY the snapshot (an equal one already standing gains the edge); each
    dated inbound edge; and each Reference EVIDENCED_BY the snapshot that names it. Every write is
    idempotent, so a re-ingest lands nothing new."""
    snap_key = run["snapshot"]["key"]
    sid = snapshot_id(snap_key)
    nodes = [EntityNode(kind=P.ENTITY_EVIDENCE_SNAPSHOT, key=snap_key, name=snap_key,
                        properties=dict(run["snapshot"]["record"])).to_graph_node()]
    edges: List[Dict[str, Any]] = []
    keys = sorted({e["key"] for e in run["edges"]} | {c["key"] for c in run["counts"]})
    nodes += [EntityNode(kind=P.ENTITY_WEB_PATH, key=k, name=k).to_graph_node() for k in keys]
    wires = {r["url"]: _reference_node(r) for r in run["references"]}
    existing = {}
    if wires:
        res = await graph_task(gx.queue, gx.graph_id, "query_nodes",
                               query=NodeQuery(ids=sorted(w["id"] for w in wires.values())).to_dict())
        existing = {str(F.nid(n)): n for n in (getattr(res, "nodes", None) or [])}
    refreshed = 0
    for r in run["references"]:
        w = wires[r["url"]]
        have = existing.get(w["id"])
        if have is None:
            nodes.append(w)
        elif r.get("observed_hash") and (F.prop(have, "observed_hash") != w["properties"]["observed_hash"]
                                         or F.prop(have, "title") != w["properties"]["title"]):
            await graph_task(gx.queue, gx.graph_id, "update_node", node_id=w["id"], properties=w["properties"])
            refreshed += 1
        edges.append(make_edge(w["id"], sid, DevRelations.EVIDENCED_BY))
    new_facts = {}
    at = op_now()
    for predicate, items, subject_of, label_of in (
            (P.LINK_OBSERVATION, run["observations"], lambda i: wires[i["url"]]["id"], lambda i: i["url"]),
            (P.INBOUND_COUNT, run["counts"], lambda i: web_path_id(i["key"]), lambda i: i["key"])):
        standing = await _standing_facts(gx, predicate) if items else {}
        landed = 0
        for i in items:
            subject = subject_of(i)
            aid = standing.get((subject, i["value"]))
            if aid is None:
                slot = FactSlotNode(subject_id=subject, predicate=predicate, subject_label=label_of(i))
                a = AssertionNode(slot_id=slot.id, value=i["value"], actor=actor, predicate=predicate,
                                  subject_id=subject, asserted_at=at)
                aid = a.id
                nodes += [slot.to_graph_node(), a.to_graph_node()]
                edges += [slot.about_edge(), a.on_slot_edge()]
                landed += 1
            edges.append(make_edge(aid, sid, DevRelations.EVIDENCED_BY))
        new_facts[predicate] = landed
    for e in run["edges"]:
        edges.append(inbound_link_edge(wires[e["url"]]["id"] if e["url"] in wires else reference_node_id(ReferenceNode.WEB, e["url"]),
                                       web_path_id(e["key"]), method=e["method"], date=e["date"],
                                       linked_urls=e["linked_urls"], target_url=e["target_url"], anchors=e["anchors"]))
    res = await extend_graph(gx.queue, gx.graph_id, nodes, edges)
    return {"snapshot": snap_key, "references": len(run["references"]), "refreshed": refreshed,
            "edges": len(run["edges"]), "observations": new_facts[P.LINK_OBSERVATION],
            "counts": new_facts[P.INBOUND_COUNT], "nodes_added": res.nodes_added, "edges_added": res.edges_added}


async def ingest_links(
    gx: GraphHandle,
    conf: Dict[str, Any],   # The evidence config block
    snap: Dict[str, Any],   # The snapshot (read_snapshot, hash-checked)
    *,
    dry_run: bool = False,
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {snapshot, source, kind, report, written, run?, applied?}
    run = plan_links(snap, conf["host"])
    out = {"snapshot": snap["key"], "source": snap["source"], "kind": "links", "report": run["report"],
           "references": len(run["references"]), "edges": len(run["edges"]),
           "observations": len(run["observations"]), "counts": len(run["counts"]), "written": False}
    if dry_run:
        return out
    applied = await apply_links(gx, run, actor=actor)
    return {**out, "written": True, "run": run, "applied": applied}


# ---------------------------------------------------------------- the read: a page's inbound links, derived

async def _inbound_edges(gx: GraphHandle) -> List[Dict[str, Any]]:
    res = await graph_task(gx.queue, gx.graph_id, "query_edges",
                           query=EdgeQuery(relation_type=DevRelations.REFERENCES).to_dict())
    raw = getattr(res, "edges", None) or getattr(res, "rows", None) or []
    out = []
    for e in raw:
        d = e.to_dict() if hasattr(e, "to_dict") else dict(e)
        if (d.get("properties") or {}).get("inbound_link"):
            out.append(d)
    return out


def _latest_by(values: List[Dict[str, Any]], method: str) -> Optional[Dict[str, Any]]:
    got = [v for v in values if v.get("method") == method]
    return max(got, key=lambda v: v["date"]) if got else None


async def inbound_report(
    gx: GraphHandle,
    *,
    holder: Optional[str] = None,   # One holder (node id or unique prefix) in full
) -> Dict[str, Any]:  # {holders, unresolved, ambiguous, untargeted, observers}
    """Each page's inbound links DERIVED across every path it holds or held: per linking site, the
    pages and what each observer last saw (Google's report, the fetch's outcome), beside Google's
    own latest totals for the page's paths; every linked web_path no page holds; every linking
    page no observer has targeted yet."""
    from .site import stated
    from .sitelinks import site_path_holders
    holders, active = await site_path_holders(gx)
    edges = await _inbound_edges(gx)
    refs = {str(F.nid(n)): n for n in await F.load_label(gx, DevNodeKinds.REFERENCE)
            if F.prop(n, "graph") == ReferenceNode.WEB}
    wps = {str(F.nid(n)): str(F.prop(n, "key")) for n in await F.load_label_where(
        gx, DevNodeKinds.ENTITY, [PropertyPredicate("entity_kind", "eq", P.ENTITY_WEB_PATH)])}
    obs: Dict[str, List[Dict[str, Any]]] = {}
    for a in await F.load_label_where(gx, DevNodeKinds.ASSERTION, [PropertyPredicate("predicate", "eq", P.LINK_OBSERVATION)]):
        obs.setdefault(str(F.prop(a, "subject_id")), []).append(P.observation_of(str(F.prop(a, "value"))))
    counts: Dict[str, List[Dict[str, Any]]] = {}
    for a in await F.load_label_where(gx, DevNodeKinds.ASSERTION, [PropertyPredicate("predicate", "eq", P.INBOUND_COUNT)]):
        counts.setdefault(wps.get(str(F.prop(a, "subject_id")), ""), []).append(P.observation_of(str(F.prop(a, "value"))))
    per_key: Dict[str, Dict[str, Dict[str, Any]]] = {}   # key -> ref id -> {methods: {method: last date}, anchors}
    for e in edges:
        k = wps.get(str(e["target_id"]))
        if k is None:
            continue
        p = e["properties"]
        r = per_key.setdefault(k, {}).setdefault(str(e["source_id"]), {"methods": {}, "anchors": []})
        r["methods"][p["method"]] = max(r["methods"].get(p["method"], ""), p["date"])
        r["anchors"] += [a for a in p.get("anchors") or [] if a not in r["anchors"]]

    def ref_row(rid: str, r: Dict[str, Any]) -> Dict[str, Any]:
        url = str(F.prop(refs.get(rid), "foreign_id") or rid)
        fetch = _latest_by(obs.get(rid, []), "fetch")
        return {"url": url, "site": _host(url), "google": r["methods"].get("search-console"),
                "fetched": r["methods"].get("fetch"), "fetch": fetch, "anchors": r["anchors"][:3]}

    owned: Dict[str, List[str]] = {}
    unresolved, ambiguous = [], []
    for key in sorted(per_key):
        hs = holders.get(key) or set()
        if len(hs) == 1:
            owned.setdefault(next(iter(hs)), []).append(key)
        elif hs:
            ambiguous.append({"key": key, "holders": sorted(hs)})
        else:
            unresolved.append({"key": key, "pages": [ref_row(i, r) for i, r in sorted(per_key[key].items())]})
    nodes = await F.load_nodes(gx, list(owned)) if owned else {}
    rows = []
    for h, keys in owned.items():
        merged: Dict[str, Dict[str, Any]] = {}
        for k in keys:
            for rid, r in per_key[k].items():
                m = merged.setdefault(rid, {"methods": {}, "anchors": []})
                for meth, d in r["methods"].items():
                    m["methods"][meth] = max(m["methods"].get(meth, ""), d)
                m["anchors"] += [a for a in r["anchors"] if a not in m["anchors"]]
        pages = sorted((ref_row(i, r) for i, r in merged.items()), key=lambda x: (x["site"], x["url"]))
        google = [max(counts[k], key=lambda c: c["date"]) for k in keys if counts.get(k)]
        n = nodes.get(h)
        rows.append({"id": h, "title": (stated(n, "title") or F.prop(n, "name") or "") if n is not None else "",
                     "path": active.get(h, ""), "paths": sorted(keys), "pages": pages,
                     "sites": sorted({p["site"] for p in pages}),
                     "google_links": sum(c["links"] for c in google) if google else None,
                     "verified": sum(1 for p in pages if p["fetched"])})
    rows.sort(key=lambda r: (-len(r["pages"]), r["path"]))
    targeted = {str(e["source_id"]) for e in edges}
    untargeted = sorted(({"url": str(F.prop(n, "foreign_id")), "fetch": _latest_by(obs.get(rid, []), "fetch")}
                         for rid, n in refs.items() if rid not in targeted), key=lambda x: x["url"])
    if holder:
        rows = [r for r in rows if r["id"] == holder or r["id"].startswith(holder)]
        if len(rows) != 1:
            return {"error": f"holder {holder!r} matches {len(rows)} page(s) with inbound links"}
    observers = {"references": len(refs), "edges": len(edges),
                 "fetch_outcomes": {}}
    for rid in refs:
        f = _latest_by(obs.get(rid, []), "fetch")
        if f:
            observers["fetch_outcomes"][f["outcome"]] = observers["fetch_outcomes"].get(f["outcome"], 0) + 1
    return {"holders": rows, "unresolved": unresolved, "ambiguous": ambiguous, "untargeted": untargeted,
            "observers": observers}
