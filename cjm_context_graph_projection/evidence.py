"""The evidence (design 7f315830; build fbf7fc0e): traffic as dated, sourced observations on a URL
node, pulled from the sources' APIs, never typed in.

THE SNAPSHOT IS THE SOURCE. `pull-evidence` asks one source's API and writes the raw responses
byte for byte under the evidence root (`<root>/<source>/<pull date>/`), each file an envelope
carrying its query, variables, window and pull time, beside a manifest of sha256 hashes; it writes
files only. `ingest-evidence <source>/<date>` verifies the hashes, registers the snapshot as an
`evidence_snapshot` Entity and lands what changed: the journaled op carries the computed run, so
replay never reads a file and never asks a source.

A TIMER PULLS, A SESSION INGESTS (ruling 56c17a80, cadence 6e283d4b). `evidence-timer install`
generates systemd user units that run `pull-evidence` on evidence.schedule, files only; `ingest-evidence`
with no snapshot named ingests every snapshot on disk the graph does not hold yet.

THE URL IS A NODE. A `web_path` Entity is keyed by the site-link resolver's equivalence key
(`site_path_key`), the host checked here since the resolver ignores it; a URL no page holds still
gets its node. Traffic is a SET fact on it, one value per (source, calendar month), and the
snapshots a measure came from are EVIDENCED_BY edges from its Assertion. A page's traffic is
DERIVED through the path ownership (`site_path_holders` follows transfers), nothing copied.

THE MONTH IS DERIVED FROM DAYS. Cloudflare keeps roughly the last week at sample interval 1 and
everything older at about 1 in 10, whatever the window, so each day is read from the snapshot that
holds it at the finest resolution, and a month's estimate is the sum of its days with a 95%
interval over the sample (one page load in s kept is a binomial draw: variance = (s - 1) x count,
zero at full resolution); the sample size stored is the true sample, so the variance stays
derivable at read (estimate^2 / sample - estimate). Cloudflare's own monthly figures are the
reconciliation check. Search Console revises recent days, so each (day, page) is
read from the latest snapshot holding it; position is impression-weighted. A window is complete
when the snapshots' windows cover the whole month and the month ended before the latest pull."""

import calendar
import datetime as dt
import hashlib
import json
import math
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from cjm_context_graph_layer.grammar import make_edge
from cjm_context_graph_layer.ops import extend_graph, graph_task
from cjm_context_graph_primitives.journal import op_now
from cjm_context_graph_primitives.query import PropertyPredicate
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import entity_node_id
from cjm_dev_graph_schema.nodes import AssertionNode, EntityNode, FactSlotNode
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from . import factlayer as F
from .runtime import GraphHandle
from .sitelinks import site_path_key

MANIFEST = "manifest.json"
CF_ENDPOINT = "https://api.cloudflare.com/client/v4/graphql"
CF_LIMIT = 10000
GSC_ENDPOINT = "https://searchconsole.googleapis.com/webmasters/v3"
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GSC_LIMIT = 25000
Z95 = 1.959964   # the 95% normal quantile, Cloudflare's confidence level
CF_CONF = ("confidence(level:0.95){level count{estimate lower upper sampleSize isValid} "
           "sum{visits{estimate lower upper sampleSize isValid}}}")
CF_SHAPES = {   # the raw shapes a Cloudflare pull keeps (the daily rows are what the months derive from)
    "daily-path": "count sum{visits} avg{sampleInterval} dimensions{date requestHost requestPath bot}",
    "month-path": f"count sum{{visits}} avg{{sampleInterval}} {CF_CONF} dimensions{{requestHost requestPath bot}}",
    "month-referer": "count sum{visits} avg{sampleInterval} dimensions{requestHost requestPath refererHost bot}",
}
CF_QUERY = ("query($a:String!,$s:Date!,$e:Date!,$t:String!,$n:Int!){viewer{accounts(filter:{accountTag:$a}){"
            "rows:rumPageloadEventsAdaptiveGroups(limit:$n,filter:{date_geq:$s,date_leq:$e,siteTag:$t}){%s}}}}")
GSC_SHAPES = {"page-date": ["page", "date"], "page-query-date": ["page", "query", "date"]}
GSC_RETENTION_DAYS = 486    # Search Console keeps about 16 months
CF_RETENTION_DAYS = 183     # Cloudflare Web Analytics keeps 26 weeks 2 days
CF_SETTLE = dt.timedelta(hours=1)   # a day's late events: a reading earlier than this after the day ends is partial
CF_FULL_DAYS = 6            # Cloudflare serves a day at interval 1 for just under 7 days (6e283d4b)
TIMER_UNIT = "cjm-evidence-pull"   # the systemd user units' name (ruling 56c17a80, cadence 6e283d4b)


# ---------------------------------------------------------------- config

def evidence_config(
    graph_cfg: Optional[Dict[str, Any]],   # The graph-sibling config (graph.config.json)
) -> Dict[str, Any]:  # {config, errors}
    """The `evidence` block of the graph-sibling config: the evidence root, the site's host, each
    source's settings and the secret files they read (never the secrets themselves)."""
    conf = dict((graph_cfg or {}).get("evidence") or {})
    errors = [f"evidence.{k} is missing" for k in ("root", "host") if not conf.get(k)]
    return {"config": conf, "errors": errors}


def _secret(path: str) -> str:
    return Path(os.path.expanduser(path)).read_text().strip()


# ---------------------------------------------------------------- snapshot files

def _blob(envelope: Dict[str, Any]) -> bytes:
    return json.dumps(envelope, indent=1, sort_keys=True).encode()


def write_snapshot(
    root: str,                            # The evidence root
    source: str,                          # A TRAFFIC_SOURCES entry (the directory under the root)
    pulled: str,                          # The pull date ('YYYY-MM-DD'; the snapshot's directory)
    envelopes: Dict[str, Dict[str, Any]],  # {file name: envelope}
    blobs: Optional[Dict[str, bytes]] = None,   # {file name: bytes} kept verbatim beside the envelopes (a fetched body, a hand export)
) -> Dict[str, Any]:  # The manifest written
    """Write one snapshot's raw files and its manifest; an existing snapshot refuses (a pull never
    overwrites the source it is). Envelopes are JSON; blobs are written byte for byte."""
    out = Path(root) / source / pulled
    if out.exists():
        raise FileExistsError(f"snapshot {source}/{pulled} already exists at {out}")
    out.mkdir(parents=True)
    files = []
    for name, env in sorted(envelopes.items()):
        blob = _blob(env)
        (out / name).write_bytes(blob)
        files.append({"file": name, "sha256": hashlib.sha256(blob).hexdigest(), "rows": env.get("row_count"),
                      "shape": env.get("shape"), "window": env.get("window")})
    for name, blob in sorted((blobs or {}).items()):
        (out / name).write_bytes(blob)
        files.append({"file": name, "sha256": hashlib.sha256(blob).hexdigest()})
    manifest = {"snapshot": pulled, "source": source, "files": files}
    (out / MANIFEST).write_text(json.dumps(manifest, indent=1))
    return manifest


def read_snapshot(
    root: str,   # The evidence root
    key: str,    # '<source>/<pull date>'
) -> Dict[str, Any]:  # {key, source, pulled_at, window, files, envelopes, blobs} or {error}
    """Read one snapshot, every file checked against its manifest hash; any mismatch, missing file
    or unknown source refuses the whole snapshot. A `.json` file is an envelope, read; any other
    file is a blob, named by its path (its hash already checked)."""
    source, _, pulled = key.partition("/")
    if source not in P.EVIDENCE_SOURCES or not pulled:
        return {"error": f"a snapshot key is '<source>/<pull date>' with a source in {P.EVIDENCE_SOURCES}: {key!r}"}
    d = Path(root) / key
    try:
        manifest = json.loads((d / MANIFEST).read_text())
    except (OSError, ValueError) as e:
        return {"error": f"snapshot {key}: no readable manifest ({e})"}
    envelopes, files, blobs = {}, [], {}
    for f in manifest.get("files") or []:
        try:
            blob = (d / f["file"]).read_bytes()
        except OSError:
            return {"error": f"snapshot {key}: {f['file']} is missing"}
        digest = hashlib.sha256(blob).hexdigest()
        if digest != f.get("sha256"):
            return {"error": f"snapshot {key}: {f['file']} does not match its manifest hash"}
        if f["file"].endswith(".json"):
            envelopes[f["file"]] = json.loads(blob)
        else:
            blobs[f["file"]] = str(d / f["file"])
        files.append({"file": f["file"], "sha256": digest})
    if not envelopes:
        return {"error": f"snapshot {key}: the manifest lists no envelope"}
    windows = [e["window"] for e in envelopes.values() if e.get("window")]
    return {"key": key, "source": source, "files": files, "envelopes": envelopes, "blobs": blobs,
            "pulled_at": min(str(e.get("pulled_at") or "") for e in envelopes.values() if e.get("pulled_at")),
            "window": [min(w[0] for w in windows), max(w[1] for w in windows)] if windows else None}


def snapshot_record(snap: Dict[str, Any]) -> Dict[str, Any]:  # The evidence_snapshot Entity's properties
    """What the snapshot node records: its source, pull time, covered window and file hashes (the
    location is the key under the configured evidence root, so moving the root is one value)."""
    return {"source": snap["source"], "pulled_at": snap["pulled_at"], "window": snap["window"],
            "files": snap["files"]}


# ---------------------------------------------------------------- pulls (live only; files, never the graph)

def _months(first: dt.date, last: dt.date) -> Iterable[Tuple[dt.date, dt.date]]:
    d = first
    while d <= last:
        end = min(dt.date(d.year, d.month, calendar.monthrange(d.year, d.month)[1]), last)
        yield d, end
        d = end + dt.timedelta(days=1)


def _post_json(url: str, body: Dict[str, Any], token: str) -> Dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def pull_cloudflare(
    conf: Dict[str, Any],   # evidence.cloudflare: {account, site_tag, token_file}
    first: dt.date,         # First day pulled
    last: dt.date,          # Last day pulled
    *,
    ask: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,  # (query, variables) -> response; default the API
) -> Dict[str, Dict[str, Any]]:  # {file name: envelope}
    """One calendar month per query and shape (well inside the 13-week cap); bot rows kept (the
    ingest excludes them); a response at the row limit refuses rather than truncating."""
    if ask is None:
        token = _secret(conf["token_file"])
        ask = lambda q, v: _post_json(CF_ENDPOINT, {"query": q, "variables": v}, token)
    out = {}
    for s, e in _months(first, last):
        for shape, fields in CF_SHAPES.items():
            query = CF_QUERY % fields
            variables = {"a": conf["account"], "s": s.isoformat(), "e": e.isoformat(), "t": conf["site_tag"], "n": CF_LIMIT}
            pulled_at = dt.datetime.now(dt.timezone.utc).isoformat()
            resp = ask(query, variables)
            if resp.get("errors"):
                raise RuntimeError(f"cloudflare {shape} {s}..{e}: {resp['errors']}")
            rows = resp["data"]["viewer"]["accounts"][0]["rows"]
            if len(rows) >= CF_LIMIT:
                raise RuntimeError(f"cloudflare {shape} {s}..{e}: {len(rows)} rows reached the limit")
            out[f"{shape}-{s:%Y-%m-%d}.json"] = {
                "source": "cloudflare-web-analytics", "dataset": "rumPageloadEventsAdaptiveGroups",
                "site_tag": conf["site_tag"], "shape": shape, "window": [s.isoformat(), e.isoformat()],
                "pulled_at": pulled_at, "query": query,
                "variables": {k: v for k, v in variables.items() if k != "a"},
                "row_count": len(rows), "response": resp}
    return out


def _gsc_token(key_file: str) -> str:
    """A service-account access token (the JWT bearer grant, signed with the key file's RSA key)."""
    import base64
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    key = json.loads(_secret(key_file))
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=")
    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    head = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claim = b64(json.dumps({"iss": key["client_email"], "scope": GSC_SCOPE, "aud": key["token_uri"],
                            "iat": now, "exp": now + 600}).encode())
    signer = serialization.load_pem_private_key(key["private_key"].encode(), None)
    sig = b64(signer.sign(head + b"." + claim, padding.PKCS1v15(), hashes.SHA256()))
    body = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                   "assertion": (head + b"." + claim + b"." + sig).decode()}).encode()
    with urllib.request.urlopen(urllib.request.Request(key["token_uri"], data=body)) as r:
        return json.load(r)["access_token"]


def pull_search_console(
    conf: Dict[str, Any],   # evidence.search_console: {property, key_file}
    first: dt.date,
    last: dt.date,
    *,
    ask: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,  # (request body) -> response; default the API
) -> Dict[str, Dict[str, Any]]:  # {file name: envelope}
    """Each shape per calendar month, paged by startRow until a short page (dataState all: the
    fresh days a later pull revises)."""
    if ask is None:
        token = _gsc_token(conf["key_file"])
        url = f"{GSC_ENDPOINT}/sites/{urllib.parse.quote(conf['property'], safe='')}/searchAnalytics/query"
        ask = lambda body: _post_json(url, body, token)
    out = {}
    for s, e in _months(first, last):
        for shape, dims in GSC_SHAPES.items():
            pages, start = [], 0
            pulled_at = dt.datetime.now(dt.timezone.utc).isoformat()
            while True:
                body = {"startDate": s.isoformat(), "endDate": e.isoformat(), "dimensions": dims,
                        "rowLimit": GSC_LIMIT, "startRow": start, "dataState": "all"}
                resp = ask(body)
                if resp.get("error"):
                    raise RuntimeError(f"search console {shape} {s}..{e}: {resp['error']}")
                pages.append({"request": body, "response": resp})
                got = len(resp.get("rows") or [])
                if got < GSC_LIMIT:
                    break
                start += got
            out[f"{shape}-{s:%Y-%m-%d}.json"] = {
                "source": "search-console", "property": conf["property"], "shape": shape,
                "window": [s.isoformat(), e.isoformat()], "pulled_at": pulled_at,
                "row_count": sum(len(p["response"].get("rows") or []) for p in pages), "pages": pages}
    return out


def _covered_until(root: str, source: str) -> Optional[dt.date]:
    d = Path(root) / source
    last = None
    for m in sorted(d.glob(f"*/{MANIFEST}")) if d.exists() else []:
        for f in json.loads(m.read_text()).get("files") or []:
            w = f.get("window")
            if w:
                end = dt.date.fromisoformat(w[1])
                last = end if last is None or end > last else last
    return last


def pull_evidence(
    conf: Dict[str, Any],          # The evidence config block
    source: str,                   # A TRAFFIC_SOURCES entry
    *,
    first: Optional[str] = None,   # 'YYYY-MM-DD' (default: from the last pull's end, re-reading the overlap)
    last: Optional[str] = None,    # 'YYYY-MM-DD' (default: today)
    today: Optional[dt.date] = None,
    ask: Optional[Callable] = None,
    headed: bool = False,          # search-console-links only: show the browser window (to sign in)
) -> Dict[str, Any]:  # {key, files, window} or {error}
    """Pull one source into a new snapshot. By default it starts where the snapshots on disk end,
    re-reading a few days (Cloudflare holds a fresh day at full resolution for about a week;
    Search Console revises recent days), and a first pull reaches back the source's retention.
    A source already pulled today is skipped without asking it (a snapshot is never overwritten,
    and the timer's retry after a failed sibling source must not fail on the one that landed)."""
    today = today or dt.date.today()
    if source in P.LINK_SOURCES:   # the inbound links (ruling a3c02fb1): a browser's drill-downs, the verify fetch
        from .inbound import pull_links
        return pull_links(conf, source, today=today, headed=headed)
    overlap = {"cloudflare": 1, "search-console": 4}.get(source)
    if overlap is None:
        return {"error": f"unknown evidence source {source!r} (one of {P.TRAFFIC_SOURCES + P.LINK_SOURCES})"}
    sub = conf.get({"cloudflare": "cloudflare", "search-console": "search_console"}[source]) or {}
    if not sub:
        return {"error": f"evidence.{'search_console' if source == 'search-console' else source} is not configured"}
    if (Path(conf["root"]) / source / today.isoformat()).exists():
        return {"key": f"{source}/{today.isoformat()}", "skipped": "already pulled today"}
    end = dt.date.fromisoformat(last) if last else today
    if first:
        start = dt.date.fromisoformat(first)
    else:
        until = _covered_until(conf["root"], source)
        retention = CF_RETENTION_DAYS if source == "cloudflare" else GSC_RETENTION_DAYS
        start = until - dt.timedelta(days=overlap) if until else today - dt.timedelta(days=retention)
        start = max(start, today - dt.timedelta(days=retention))
    if start > end:
        return {"error": f"nothing to pull: {start} is after {end}"}
    try:
        envelopes = (pull_cloudflare(sub, start, end, ask=ask) if source == "cloudflare"
                     else pull_search_console(sub, start, end, ask=ask))
        manifest = write_snapshot(conf["root"], source, today.isoformat(), envelopes)
    except (RuntimeError, OSError, KeyError, urllib.error.URLError) as e:
        return {"error": f"{source} pull: {e}"[:400]}
    return {"key": f"{source}/{today.isoformat()}", "window": [start.isoformat(), end.isoformat()],
            "files": manifest["files"]}


def timer_units(
    conf: Dict[str, Any],   # The evidence config block (its schedule and the sources configured)
    db_path: str,           # The graph db whose sibling config the pull reads
    exe: str,               # The cjm-context-graph console script the service runs
) -> Dict[str, str]:  # {unit file name: text}
    """The pull-only timer as systemd user units (ruling 56c17a80; cadence 6e283d4b): the service
    runs pull-evidence for every configured source and nothing else -- files under the evidence
    root, never the graph; a failed pull retries every half hour, at most a dozen times a day; the
    timer is persistent, so a run missed while the machine was off runs at the next boot."""
    def q(s: str) -> str:   # systemd's argument quoting: the specifier and variable signs doubled
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'
    sources = [s for s in P.TRAFFIC_SOURCES if conf.get("search_console" if s == "search-console" else s)]
    head = f"Pull the traffic evidence for {db_path} (files only; design 7f315830)".replace("%", "%%")
    service = ("[Unit]\n"
               f"Description={head}\n"
               "StartLimitIntervalSec=1d\nStartLimitBurst=12\n\n"
               "[Service]\nType=oneshot\n"
               f"ExecStart={q(exe)} --graph-db-path {q(db_path)} pull-evidence {' '.join(sources)}\n"
               "Restart=on-failure\nRestartSec=30min\n")
    timer = ("[Unit]\n"
             f"Description={head}, on {conf['schedule']}\n\n"
             f"[Timer]\nOnCalendar={conf['schedule']}\nPersistent=true\n\n"
             "[Install]\nWantedBy=timers.target\n")
    return {f"{TIMER_UNIT}.service": service, f"{TIMER_UNIT}.timer": timer}


def _run_systemd(argv: List[str]) -> Tuple[int, str]:  # (exit code, stdout + stderr) of one systemd tool call
    try:
        p = subprocess.run(argv, capture_output=True, text=True)
    except FileNotFoundError:
        return 127, f"{argv[0]} is not on this machine (the timer needs a systemd user session)"
    return p.returncode, (p.stdout + p.stderr).strip()


def evidence_timer(
    conf: Dict[str, Any],     # The evidence config block
    action: str,              # 'install' | 'status' | 'remove'
    *,
    db_path: str,             # The graph db the service passes (its sibling config is the pull's)
    exe: Optional[str] = None,       # Default: the console script beside this interpreter
    unit_dir: Optional[str] = None,  # Default: ~/.config/systemd/user
    run: Optional[Callable[[List[str]], Tuple[int, str]]] = None,   # argv -> (rc, output); default subprocess
    today: Optional[dt.date] = None,
) -> Dict[str, Any]:  # {action, unit_dir, units, installed, stale, latest, warnings} or {error}
    """Install, report or remove the pull-only timer. The units are GENERATED here from the config
    and this environment's console script, never committed (aa00d43c: nothing from this machine's
    paths in a repo); install checks the schedule and the units with systemd-analyze before
    enabling. Status reads systemd's view of both units, flags an installed unit that differs from
    what the config now generates, and names each source's latest snapshot on disk with a warning
    once Cloudflare's oldest full-resolution day is about to age out."""
    run = run or _run_systemd
    udir = Path(os.path.expanduser(unit_dir or "~/.config/systemd/user"))
    exe = exe or str(Path(sys.executable).parent / "cjm-context-graph")
    db = str(Path(db_path).resolve())
    timer = f"{TIMER_UNIT}.timer"
    if action not in ("install", "status", "remove"):
        return {"error": f"evidence-timer takes install, status or remove (got {action!r})"}
    if action == "install":
        if not conf.get("schedule"):
            return {"error": "evidence.schedule is missing (a systemd OnCalendar expression, e.g. 'Mon,Thu *-*-* 09:30')"}
        if not Path(exe).exists():
            return {"error": f"the console script the service would run is not at {exe}"}
        if not (conf.get("cloudflare") or conf.get("search_console")):
            return {"error": "no evidence source is configured (evidence.cloudflare / evidence.search_console)"}
        units = timer_units(conf, db, exe)
        rc, out = run(["systemd-analyze", "calendar", conf["schedule"]])
        if rc:
            return {"error": f"evidence.schedule {conf['schedule']!r} is not a systemd calendar expression: {out}"}
        udir.mkdir(parents=True, exist_ok=True)
        for name, text in units.items():
            (udir / name).write_text(text)
        for argv in (["systemd-analyze", "--user", "verify", *[str(udir / n) for n in units]],
                     ["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", timer]):
            rc, out = run(argv)
            if rc:
                return {"error": f"{' '.join(argv[:3])}: {out}"[:600]}
    elif action == "remove":
        rc, out = run(["systemctl", "--user", "disable", "--now", timer])
        for name in (f"{TIMER_UNIT}.service", timer):
            (udir / name).unlink(missing_ok=True)
        run(["systemctl", "--user", "daemon-reload"])
        if rc and "not loaded" not in out and "does not exist" not in out:
            return {"error": f"systemctl --user disable: {out}"[:600]}
    units_now: Dict[str, Dict[str, str]] = {}
    for name, props in ((timer, "ActiveState,UnitFileState,NextElapseUSecRealtime,LastTriggerUSec"),
                        (f"{TIMER_UNIT}.service", "ActiveState,Result,ExecMainStatus,ExecMainExitTimestamp")):
        rc, out = run(["systemctl", "--user", "show", name, "-p", props])
        units_now[name] = dict(line.split("=", 1) for line in out.splitlines() if "=" in line) if not rc else {}
    installed = all((udir / n).exists() for n in (f"{TIMER_UNIT}.service", timer))
    stale = []
    if installed and conf.get("schedule") and Path(exe).exists():
        stale = [n for n, text in timer_units(conf, db, exe).items() if (udir / n).read_text() != text]
    today = today or dt.date.today()
    latest: Dict[str, Dict[str, Any]] = {}
    warnings = []
    for source in P.TRAFFIC_SOURCES:
        snaps = sorted(p.parent.name for p in (Path(conf["root"]) / source).glob(f"*/{MANIFEST}"))
        through = _covered_until(conf["root"], source)
        if snaps:
            latest[source] = {"key": f"{source}/{snaps[-1]}", "through": through.isoformat() if through else None}
        if source == "cloudflare" and through and (today - through).days >= CF_FULL_DAYS:
            warnings.append(f"cloudflare is covered through {through}: the next day leaves Cloudflare's full "
                            f"resolution within about a day -- pull now (pull-evidence cloudflare)")
    if action != "remove" and not installed:
        warnings.append("the timer is not installed (evidence-timer install)")
    return {"action": action, "unit_dir": str(udir), "exe": exe, "db": db, "schedule": conf.get("schedule"),
            "units": units_now, "installed": installed, "stale": stale, "latest": latest, "warnings": warnings}


# ---------------------------------------------------------------- the month measures

def _days(window: List[str]) -> Set[str]:
    a, b = dt.date.fromisoformat(window[0]), dt.date.fromisoformat(window[1])
    return {(a + dt.timedelta(days=i)).isoformat() for i in range((b - a).days + 1)}


def _complete(month: str, covered: Set[str], latest_pull: str) -> bool:
    y, m = int(month[:4]), int(month[5:])
    days = _days([f"{month}-01", f"{month}-{calendar.monthrange(y, m)[1]:02d}"])
    return days <= covered and max(days) < latest_pull[:10]


def _day_complete(
    day: str,         # A Cloudflare date dimension ('YYYY-MM-DD', UTC)
    pulled_at: str,   # When the reading was taken (ISO, with its offset)
) -> bool:  # True when the reading was taken after the day ended and settled
    """A day read before it ended is partial: once the complete day is read, that reading wins
    even at a coarser interval (6e283d4b)."""
    end = dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone.utc) + dt.timedelta(days=1) + CF_SETTLE
    at = dt.datetime.fromisoformat(pulled_at)
    return (at if at.tzinfo else at.replace(tzinfo=dt.timezone.utc)) >= end


def _interval(est: float, var: float, sample: float) -> Dict[str, Any]:
    half = Z95 * math.sqrt(max(var, 0.0))
    return {"estimate": round(est), "lower": round(max(0.0, est - half), 1), "upper": round(est + half, 1),
            "sample_size": round(sample)}


def cloudflare_measures(
    snapshots: List[Dict[str, Any]],   # Every cloudflare snapshot (read_snapshot results)
    host: str,                          # The site's host; other hosts are reported, never counted
) -> Dict[str, Any]:  # {measures: {(key, month): {measure, snapshots}}, foreign, unkeyed, day_source}
    """Each day from the snapshot holding its best reading: a COMPLETE reading (taken after the UTC
    day ended) over a partial one whatever their resolutions (6e283d4b: the pull day itself is
    partial), then the finest resolution, then the latest pull; bot rows excluded; a month's page
    loads and visits summed over its days with a binomial interval."""
    best: Dict[str, Tuple[bool, float, str, str]] = {}   # day -> (complete, -mean interval, pulled_at, snapshot key)
    rows_by: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    covered: Set[str] = set()
    for snap in snapshots:
        per_day: Dict[str, List[float]] = {}
        read_at: Dict[str, str] = {}
        for env in snap["envelopes"].values():
            if env.get("shape") != "daily-path":
                continue
            covered |= _days(env["window"])
            for r in env["response"]["data"]["viewer"]["accounts"][0]["rows"]:
                day = r["dimensions"]["date"]
                rows_by.setdefault((snap["key"], day), []).append(r)
                per_day.setdefault(day, []).append(float(r["avg"]["sampleInterval"]))
                read_at[day] = str(env.get("pulled_at") or snap["pulled_at"])
        for day, ivs in per_day.items():
            cand = (_day_complete(day, read_at[day]), -(sum(ivs) / len(ivs)), snap["pulled_at"], snap["key"])
            cur = best.get(day)
            if cur is None or cand[:3] > cur[:3]:
                best[day] = cand
    acc: Dict[Tuple[str, str], Dict[str, Any]] = {}
    foreign: Dict[str, int] = {}
    unkeyed: Set[str] = set()
    for day, (_, _, _, key) in sorted(best.items()):
        for r in rows_by[(key, day)]:
            d = r["dimensions"]
            if d.get("bot"):
                continue
            if d.get("requestHost") != host:
                foreign[d.get("requestHost") or ""] = foreign.get(d.get("requestHost") or "", 0) + r["count"]
                continue
            k = site_path_key(str(d.get("requestPath") or ""))
            if not k:
                unkeyed.add(str(d.get("requestPath")))
                continue
            a = acc.setdefault((k, day[:7]), {"pl": 0.0, "plv": 0.0, "pln": 0.0, "v": 0.0, "vv": 0.0, "vn": 0.0,
                                               "days": set(), "snaps": set()})
            s = max(float(r["avg"]["sampleInterval"]), 1.0)
            a["pl"] += r["count"]; a["plv"] += (s - 1) * r["count"]; a["pln"] += r["count"] / s
            a["v"] += r["sum"]["visits"]; a["vv"] += (s - 1) * r["sum"]["visits"]; a["vn"] += r["sum"]["visits"] / s
            a["days"].add(day); a["snaps"].add(key)
    latest = max((s["pulled_at"] for s in snapshots), default="")
    measures = {}
    for (k, month), a in acc.items():
        measures[(k, month)] = {"snapshots": sorted(a["snaps"]), "measure": {
            "source": "cloudflare", "window": month, "complete": _complete(month, covered, latest),
            "days": len(a["days"]), "pageloads": _interval(a["pl"], a["plv"], a["pln"]),
            "visits": _interval(a["v"], a["vv"], a["vn"])}}
    return {"measures": measures, "foreign": foreign, "unkeyed": sorted(unkeyed),
            "day_source": {d: v[3] for d, v in best.items()}}


def search_console_measures(
    snapshots: List[Dict[str, Any]],   # Every search-console snapshot
    host: str,
) -> Dict[str, Any]:  # {measures, foreign, unkeyed}
    """Each DAY from the latest snapshot whose window covers it (Search Console revises recent
    days, so a later pull is authoritative for its whole day, never merged with an earlier one);
    a month's clicks and impressions summed, its position impression-weighted."""
    owner: Dict[str, Tuple[str, str]] = {}      # day -> (pulled_at, snapshot key)
    rows_by: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    covered: Set[str] = set()
    for snap in snapshots:
        for env in snap["envelopes"].values():
            if env.get("shape") != "page-date":
                continue
            days = _days(env["window"])
            covered |= days
            for day in days:
                if day not in owner or snap["pulled_at"] > owner[day][0]:
                    owner[day] = (snap["pulled_at"], snap["key"])
            for page in env["pages"]:
                for r in page["response"].get("rows") or []:
                    rows_by.setdefault((snap["key"], r["keys"][1]), []).append(r)
    acc: Dict[Tuple[str, str], Dict[str, Any]] = {}
    foreign: Dict[str, int] = {}
    unkeyed: Set[str] = set()
    picked = [(day, key, r) for day, (_, key) in sorted(owner.items()) for r in rows_by.get((key, day), [])]
    for day, key, r in picked:
        url = r["keys"][0]
        parts = urllib.parse.urlsplit(url)
        if parts.netloc != host:
            foreign[parts.netloc] = foreign.get(parts.netloc, 0) + int(r["impressions"])
            continue
        k = site_path_key(url)
        if not k:
            unkeyed.add(url)
            continue
        a = acc.setdefault((k, day[:7]), {"c": 0, "i": 0, "pw": 0.0, "days": set(), "snaps": set()})
        a["c"] += int(r["clicks"]); a["i"] += int(r["impressions"]); a["pw"] += float(r["position"]) * int(r["impressions"])
        a["days"].add(day); a["snaps"].add(key)
    latest = max((s["pulled_at"] for s in snapshots), default="")
    measures = {}
    for (k, month), a in acc.items():
        measures[(k, month)] = {"snapshots": sorted(a["snaps"]), "measure": {
            "source": "search-console", "window": month, "complete": _complete(month, covered, latest),
            "days": len(a["days"]), "clicks": a["c"], "impressions": a["i"],
            "position": round(a["pw"] / a["i"], 2) if a["i"] else None}}
    return {"measures": measures, "foreign": foreign, "unkeyed": sorted(unkeyed)}


# ---------------------------------------------------------------- plan + apply (live and replay)

def web_path_id(key: str) -> str:
    return entity_node_id(P.ENTITY_WEB_PATH, key)


def snapshot_id(key: str) -> str:
    return entity_node_id(P.ENTITY_EVIDENCE_SNAPSHOT, key)


async def _standing(gx: GraphHandle) -> Dict[str, Any]:
    """The traffic Assertions on the graph: per (web_path id, source, window) the active values,
    and every (assertion, snapshot) EVIDENCED_BY pair."""
    rows = await F.load_label_where(gx, DevNodeKinds.ASSERTION, [PropertyPredicate("predicate", "eq", P.TRAFFIC)])
    supers = await F.load_supersedes(gx) if rows else []
    active: Dict[Tuple[str, str, str], List[str]] = {}
    for group in F.group_by_slot(rows).values():
        for a in F.active_assertions(group, supers):
            m = P.traffic_of(str(F.prop(a, "value") or ""))
            if m:
                active.setdefault((str(F.prop(a, "subject_id")), m["source"], m["window"]), []).append(
                    P.canonical_value(P.TRAFFIC, str(F.prop(a, "value"))))
    evidenced = set(await F.load_edge_pairs(gx, DevRelations.EVIDENCED_BY)) if rows else set()
    return {"rows": rows, "active": active, "evidenced": evidenced}


async def plan_evidence(
    gx: GraphHandle,
    snap: Dict[str, Any],                 # The snapshot being ingested (read_snapshot)
    computed: Dict[Tuple[str, str], Dict[str, Any]],   # The source's measures over every snapshot
) -> Dict[str, Any]:  # The run: {snapshot, measures: [{key, value, snapshots, supersede}]}
    """What lands: each measure whose value differs from its window's active value (superseding
    it), and each unchanged one a snapshot newly corroborates (one more EVIDENCED_BY edge)."""
    have = await _standing(gx)
    by_value = {(str(F.prop(a, "subject_id")), P.canonical_value(P.TRAFFIC, str(F.prop(a, "value")))): str(F.nid(a))
                for a in have["rows"]}
    out = []
    for (key, month), c in sorted(computed.items()):
        value = P.traffic_value(c["measure"])
        wp = web_path_id(key)
        standing = have["active"].get((wp, c["measure"]["source"], month), [])
        if standing == [value]:
            aid = by_value[(wp, value)]
            fresh = [s for s in c["snapshots"] if (aid, snapshot_id(s)) not in have["evidenced"]]
            if fresh:
                out.append({"key": key, "value": value, "snapshots": fresh, "supersede": []})
            continue
        out.append({"key": key, "value": value, "snapshots": c["snapshots"],
                    "supersede": [v for v in standing if v != value]})
    return {"snapshot": {"key": snap["key"], "record": snapshot_record(snap)}, "measures": out}


async def apply_evidence(
    gx: GraphHandle,
    run: Dict[str, Any],   # plan_evidence's run (the journaled op carries it whole)
    *,
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {snapshot, web_paths, landed, superseded, corroborated}
    """Land one run in ONE batch: the snapshot node, every web_path the run names, each measure's
    slot and Assertion with its EVIDENCED_BY edges, and the SUPERSEDES edge to each prior value of
    its window (resolved on the slot by value, so replay resolves it the same). Live and replay
    share it; the Assertions are dated by the op's time. A links run (ruling a3c02fb1) is
    `inbound.apply_links`'."""
    if run.get("kind") == "links":
        from .inbound import apply_links
        return await apply_links(gx, run, actor=actor)
    have = await _standing(gx)
    by_value = {(str(F.prop(a, "subject_id")), P.canonical_value(P.TRAFFIC, str(F.prop(a, "value")))): str(F.nid(a))
                for a in have["rows"]}
    at = op_now()
    snap_key = run["snapshot"]["key"]
    nodes = [EntityNode(kind=P.ENTITY_EVIDENCE_SNAPSHOT, key=snap_key, name=snap_key,
                        properties=dict(run["snapshot"]["record"])).to_graph_node()]
    edges: List[Dict[str, Any]] = []
    seen_paths: Set[str] = set()
    landed = superseded = corroborated = 0
    supers = set(await F.load_supersedes(gx))
    lifted: List[str] = []
    for m in run["measures"]:
        key, wp = m["key"], web_path_id(m["key"])
        if key not in seen_paths:
            seen_paths.add(key)
            nodes.append(EntityNode(kind=P.ENTITY_WEB_PATH, key=key, name=key).to_graph_node())
        slot = FactSlotNode(subject_id=wp, predicate=P.TRAFFIC, subject_label=key)
        aid = by_value.get((wp, m["value"]))
        if aid is None:
            a = AssertionNode(slot_id=slot.id, value=m["value"], actor=actor, predicate=P.TRAFFIC,
                              subject_id=wp, asserted_at=at)
            aid = a.id
            nodes += [slot.to_graph_node(), a.to_graph_node()]
            edges += [slot.about_edge(), a.on_slot_edge()]
            landed += 1
        else:
            corroborated += 1
            # REINSTATEMENT: a window's measure back at an earlier value -- the value it now
            # supersedes demoted this very Assertion, and that edge is lifted first (left standing,
            # the two edges form a cycle and neither value resolves active)
            lifted += [make_edge(by_value[(wp, old)], aid, DevRelations.SUPERSEDES)["id"] for old in m["supersede"]
                       if (by_value.get((wp, old)), aid) in supers]
        edges += [make_edge(aid, snapshot_id(s), DevRelations.EVIDENCED_BY) for s in m["snapshots"]]
        for old in m["supersede"]:
            oid = by_value.get((wp, old))
            if oid is None:
                raise RuntimeError(f"traffic on {key}: the value to supersede is not on its slot")
            edges.append(make_edge(aid, oid, DevRelations.SUPERSEDES))
            superseded += 1
    # every snapshot a measure names must be a node before its edge lands (a prior one already is)
    missing = {s for m in run["measures"] for s in m["snapshots"]} - {snap_key}
    if missing:
        present = {str(F.nid(n)) for n in await F.load_label_where(
            gx, DevNodeKinds.ENTITY, [PropertyPredicate("entity_kind", "eq", P.ENTITY_EVIDENCE_SNAPSHOT)])}
        gone = sorted(s for s in missing if snapshot_id(s) not in present)
        if gone:
            raise RuntimeError(f"measures name snapshots never ingested: {gone}")
    if lifted:
        await graph_task(gx.queue, gx.graph_id, "delete_edges", edge_ids=lifted)
    await extend_graph(gx.queue, gx.graph_id, nodes, edges)
    return {"snapshot": snap_key, "web_paths": len(seen_paths), "landed": landed,
            "superseded": superseded, "corroborated": corroborated, "reinstated": len(lifted)}


async def ingested_snapshots(gx: GraphHandle) -> List[str]:   # The snapshot keys on the graph
    rows = await F.load_label_where(gx, DevNodeKinds.ENTITY,
                                    [PropertyPredicate("entity_kind", "eq", P.ENTITY_EVIDENCE_SNAPSHOT)])
    return sorted(str(F.prop(n, "key")) for n in rows)


def pending_snapshots(
    root: str,                # The evidence root
    ingested: Iterable[str],  # The snapshot keys already on the graph
) -> List[str]:  # '<source>/<pull date>' keys on disk and not on the graph, each source in pull order
    """What the next ingest reads (ruling 56c17a80: the timer pulls, a session ingests every
    snapshot written since the last one): each snapshot directory with a manifest under an
    evidence source that no evidence_snapshot node holds -- the exports first, then the traffic,
    then the links (the fetch after the drill-downs it verifies)."""
    have = set(ingested)
    out = []
    for source in P.EVIDENCE_SOURCES:
        d = Path(root) / source
        for m in sorted(d.glob(f"*/{MANIFEST}")) if d.exists() else []:
            key = f"{source}/{m.parent.name}"
            if key not in have:
                out.append(key)
    return out


async def ingest_evidence(
    gx: GraphHandle,
    conf: Dict[str, Any],       # The evidence config block
    key: str,                   # The snapshot to ingest ('<source>/<pull date>')
    *,
    dry_run: bool = False,
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {snapshot, changes, foreign, unkeyed, written, run?, applied?} or {error}
    """Ingest one snapshot: its source's months are recomputed over every snapshot of that source
    on the graph plus this one (all hash-checked), and what changed lands. Nothing lands on any
    refusal."""
    snap = read_snapshot(conf["root"], key)
    if snap.get("error"):
        return {"error": snap["error"], "written": False}
    if snap["source"] not in P.TRAFFIC_SOURCES:   # an export or a links snapshot (ruling a3c02fb1)
        from .inbound import ingest_links
        return await ingest_links(gx, conf, snap, dry_run=dry_run, actor=actor)
    keys = sorted({k for k in await ingested_snapshots(gx) if k.startswith(snap["source"] + "/")} | {key})
    snaps = []
    for k in keys:
        s = snap if k == key else read_snapshot(conf["root"], k)
        if s.get("error"):
            return {"error": s["error"], "written": False}
        snaps.append(s)
    fn = cloudflare_measures if snap["source"] == "cloudflare" else search_console_measures
    got = fn(snaps, conf["host"])
    run = await plan_evidence(gx, snap, got["measures"])
    out = {"snapshot": key, "source": snap["source"], "window": snap["window"], "computed": len(got["measures"]),
           "changes": len(run["measures"]), "superseding": sum(1 for m in run["measures"] if m["supersede"]),
           "foreign": got["foreign"], "unkeyed": got["unkeyed"], "written": False}
    if dry_run:
        return out
    applied = await apply_evidence(gx, run, actor=actor)
    return {**out, "written": True, "run": run, "applied": applied}


# ---------------------------------------------------------------- the read: a page's share, derived

def _combine(parts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Sum estimates whose intervals came from independent samples, each part's variance derived
    from its estimate and sample (estimate^2 / sample - estimate), so the total is derived, never
    stored."""
    est = sum(p["estimate"] for p in parts)
    var = sum(p["estimate"] ** 2 / p["sample_size"] - p["estimate"] for p in parts if p.get("sample_size"))
    return _interval(float(est), var, sum(p.get("sample_size") or 0 for p in parts))


async def traffic_report(
    gx: GraphHandle,
    *,
    since: Optional[str] = None,     # First month counted ('YYYY-MM'; None = every month on the graph)
    until: Optional[str] = None,     # Last month counted
    holder: Optional[str] = None,    # One holder (node id or unique prefix) in full
) -> Dict[str, Any]:  # {holders: [...], unresolved: [...], ambiguous: [...], months}
    """Each page's traffic DERIVED across every path it holds or held (the path ownership follows
    transfers), per source over the months in range, and every web_path no page holds."""
    from .site import stated
    from .sitelinks import site_path_holders
    holders, active = await site_path_holders(gx)
    have = await _standing(gx)
    per_key: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    months: Set[str] = set()
    wp_rows = await F.load_label_where(gx, DevNodeKinds.ENTITY, [PropertyPredicate("entity_kind", "eq", P.ENTITY_WEB_PATH)])
    key_of = {str(F.nid(n)): str(F.prop(n, "key")) for n in wp_rows}
    for (wp, source, month), values in have["active"].items():
        if (since and month < since) or (until and month > until) or wp not in key_of:
            continue
        months.add(month)
        for v in values:
            per_key.setdefault(key_of[wp], {}).setdefault(source, []).append(P.traffic_of(v))
    owned: Dict[str, List[str]] = {}
    unresolved, ambiguous = [], []
    for key in sorted(per_key):
        hs = holders.get(key) or set()
        if len(hs) == 1:
            owned.setdefault(next(iter(hs)), []).append(key)
        elif hs:
            ambiguous.append({"key": key, "holders": sorted(hs)})
        else:
            unresolved.append({"key": key, **_totals(per_key[key])})
    nodes = await F.load_nodes(gx, list(owned)) if owned else {}
    rows = []
    for h, keys in owned.items():
        merged: Dict[str, List[Dict[str, Any]]] = {}
        for k in keys:
            for source, ms in per_key[k].items():
                merged.setdefault(source, []).extend(ms)
        n = nodes.get(h)
        rows.append({"id": h, "title": (stated(n, "title") or F.prop(n, "name") or "") if n is not None else "",
                     "path": active.get(h, ""), "paths": sorted(keys), **_totals(merged)})
    rows.sort(key=lambda r: -(r.get("cloudflare") or {}).get("visits", {}).get("estimate", 0))
    unresolved.sort(key=lambda r: -(r.get("cloudflare") or {}).get("visits", {}).get("estimate", 0))
    if holder:
        rows = [r for r in rows if r["id"] == holder or r["id"].startswith(holder)]
        if len(rows) != 1:
            return {"error": f"holder {holder!r} matches {len(rows)} page(s) with traffic"}
    return {"holders": rows, "unresolved": unresolved, "ambiguous": ambiguous, "months": sorted(months)}


def _totals(by_source: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    cf = by_source.get("cloudflare") or []
    if cf:
        out["cloudflare"] = {"visits": _combine([m["visits"] for m in cf]), "pageloads": _combine([m["pageloads"] for m in cf]),
                             "months": sorted(m["window"] for m in cf),
                             "incomplete": sorted(m["window"] for m in cf if not m["complete"])}
    sc = by_source.get("search-console") or []
    if sc:
        impr = sum(m["impressions"] for m in sc)
        out["search_console"] = {"clicks": sum(m["clicks"] for m in sc), "impressions": impr,
                                 "position": (round(sum((m["position"] or 0) * m["impressions"] for m in sc) / impr, 2)
                                              if impr else None),
                                 "months": sorted(m["window"] for m in sc)}
    return out
