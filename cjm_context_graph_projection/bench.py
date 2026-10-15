"""THE SCALE HARNESS: synthetic tiers of a graph and its journals, the canonical verbs run at each.

Design detail 3bd8673d (work item 85b0d368; the audit's synthesis 681604c1). A TIER is k copies
of one graph and its journals: copy 0 is a snapshot of the live graph, and copy c rewrites every
UUID in a row or an op -- ids, edge endpoints, properties, sources -- to uuid5(ns, "c:<uuid>"),
so each copy is a whole, self-consistent graph under the same identity rules. Each copy is
re-keyed ONCE into the staging area; a tier is assembled from the copies by plain row copies
into a fresh db (indexes built after the rows, then ANALYZE) and hard links to their compressed
journal segments, so reaching 32x rewrites 31 copies, never 62.

A tier is SAFE BY CONSTRUCTION: its own graph.config.json names an empty repos and memory
sandbox and its own journals, and every path the harness writes resolves inside the bench
directory (anything else refuses). The canonical verbs run against each tier as real CLI
invocations under an address-space limit -- one timing event each in the lane's ledger, tagged
with the tier, beside the harness's own event (wall, peak RSS, outcome) -- so a cost that
appears only at 32x shows as a curve, never a crash. `perf` reads the curves.

THE LAYOUT (<bench_dir>/<lane>/): base/ the snapshot, never run against · copies/cNNNN/ the
re-keyed copies (rows without indexes, all-cold compressed journal segments) · xK/ a tier (its
db, config, journals, sandbox and manifest). The bench directory, the tier list, the verbs and
the limits are DATA in the graph-sibling config's `bench` object; nothing here names a path on
this machine.
"""

import argparse
import json
import os
import re
import resource
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from cjm_context_graph_primitives.journal import (journal_segments, read_journal, read_segment,
                                                  write_segment)

from .config import load_graph_config, sibling_graphs
from .perf import append_timing

BENCH_DEFAULTS: Dict[str, Any] = {
    "tiers": [2, 4, 8, 16, 32],  # The user's tiers (681604c1); x1 is always measured beside them
    "memory_limit_gb": 64,       # Each measured invocation's address-space limit
    "timeout_s": 3600,           # Each measured invocation's wall limit
    "replay_tail": 2000,         # Ops the delta replay re-applies at a tier
    "workers": 8,                # Copies re-keyed in parallel
}
REKEY_NS = uuid.uuid5(uuid.NAMESPACE_URL, "cjm:bench:rekey")  # Every copy's ids derive under it
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
COPY_TABLES = ("nodes", "edges")  # Tables every copy repeats; the rest come from copy 0 alone
CONFIG_NAME = "graph.config.json"
MANIFEST = "manifest.json"

# The canonical verbs (design detail 3bd8673d (5) - (7)): reads on any graph, the delta replay
# by the lane's kind, the streamed comparator over the tier. `{name}` placeholders resolve per
# tier; a spec whose placeholder does not resolve is skipped and reported, never guessed.
READ_VERBS: List[Dict[str, Any]] = [
    {"name": "show", "argv": ["show", "{hub}"], "read": True},
    {"name": "read", "argv": ["read", "{hub}"], "read": True},
    {"name": "locate", "argv": ["locate", "{term}"], "read": True},
    {"name": "grep", "argv": ["grep", "{term}"], "read": True},
    {"name": "relevant", "argv": ["relevant", "{term}"], "read": True},
    {"name": "list", "argv": ["list", "--label", "{hub_label}"], "read": True},
]
REPLAY_VERBS: Dict[str, List[Dict[str, Any]]] = {
    "projection": [{"name": "replay-tail", "argv": ["replay", "--offset", "{tail_offset}"]}],
    "workflow": [{"name": "replay-tail",
                  "argv": ["replay", "--workflow", "--offset", "{tail_offset}"]}],
}
PROOF_VERBS: List[Dict[str, Any]] = [
    {"name": "rebuild-diff", "argv": ["rebuild-diff", "--against", "{db}"], "read": True},
]

# The concurrent replay mode (design detail 2f5033bf (2)): its settings are DATA, the bench
# config's `load` object overlaid on these.
LOAD_DEFAULTS: Dict[str, Any] = {
    "clients": [1, 8, 32],  # Client counts per load run (32 = the planning load, ruling dba17ca8)
    "tiers": [1, 8, 32],    # Tiers a load runs at
    "pace": 0.0,            # 0 = back to back (the stress bound); 1 = the recorded gaps
    "steps": 50,            # Steps per client, at most
    "verbs": ["show", "read", "locate", "grep", "list", "readiness", "journal-window", "relevant",
              "link", "assert", "decide", "check"],  # The verbs a workload keeps
    "workloads": [],        # The workload set (names in the lane's workloads directory); [] = all
    "memory_ceiling_gb": None,  # The clients' summed RSS that aborts a run (None = 70% of this machine's RAM)
}
DROP_KEYS = frozenset({"actor", "session"})  # A write op's envelope: each client stamps its own actor, no session
MARK_DESTS = ("statement", "text")           # Free text a client marks, so dedup never drops it
HEX_RE = re.compile(r"^[0-9a-f]{6,32}$")     # An id prefix a request may name


class BenchRefusal(Exception):
    """A harness precondition failed (no bench directory, a path outside it, a missing lane)."""


# ---------------------------------------------------------------------------------------------
# Configuration and lanes
# ---------------------------------------------------------------------------------------------

def bench_config(
    graph_db_path: str,  # The invoking graph's db: its sibling config carries `bench`
) -> Dict[str, Any]:  # BENCH_DEFAULTS overlaid with the config's `bench` object
    """The harness's settings as DATA; `bench_dir` has no default (a path on one machine is
    never code) and its absence refuses."""
    cfg = load_graph_config(graph_db_path) or {}
    out = {**BENCH_DEFAULTS, **(cfg.get("bench") or {})}
    if not out.get("bench_dir"):
        raise BenchRefusal("no bench directory -- set `bench.bench_dir` in the graph-sibling "
                           f"{CONFIG_NAME} (the tiers live there, never beside the live graph)")
    return out


def resolve_lane(
    graph_db_path: str,                    # The invoking graph's db
    journals: Dict[str, Optional[str]],   # The invoking graph's journal families (name -> live path)
    manifests_dir: str,                   # The capability manifests directory
    lane: Optional[str] = None,           # None = the invoking graph; else a `sibling_graphs` key
    settings: Optional[Dict[str, Any]] = None,  # `bench_config`'s result (per-lane settings)
) -> Dict[str, Any]:  # {name, kind, db, journals, manifests_dir, settings}
    """A lane: the graph a tier copies and the journal families beside it. The invoking graph
    is a projection lane named by its db stem; a sibling is a workflow lane whose writes journal
    is `<db stem>.writes.jsonl` beside its db (the layer's convention)."""
    settings = settings or {}
    if lane is None:
        name, db, kind = Path(graph_db_path).stem, graph_db_path, "projection"
        fams = {k: v for k, v in journals.items() if v}
    else:
        sibs = sibling_graphs(load_graph_config(graph_db_path) or {})
        if lane not in sibs:
            raise BenchRefusal(f"no lane {lane!r} -- the lanes are the invoking graph and the "
                               f"sibling_graphs keys ({', '.join(sorted(sibs)) or 'none'})")
        name, db, kind = lane, sibs[lane], "workflow"
        p = Path(db)
        fams = {"writes": str(p.with_name(p.stem + ".writes.jsonl"))}
    per_lane = (settings.get("lanes") or {}).get(name) or {}
    return {"name": name, "kind": per_lane.get("kind", kind), "db": str(db),
            "journals": fams, "manifests_dir": manifests_dir, "settings": per_lane}


def lane_dir(settings: Dict[str, Any], lane: Dict[str, Any]) -> Path:  # <bench_dir>/<lane>
    """The lane's directory under the bench directory, refused if the lane's own graph or
    journals live inside the bench (a tier must never copy a tier)."""
    root = Path(settings["bench_dir"]).resolve()
    for p in [lane["db"], *lane["journals"].values()]:
        q = Path(p).resolve()
        if q == root or root in q.parents:
            raise BenchRefusal(f"the lane's source {q} lives inside the bench directory {root}")
    return root / lane["name"]


def inside(
    path,  # A path the harness is about to write
    root,  # The bench directory
) -> Path:  # The resolved path
    """The path guard (design detail 3bd8673d (3)): every write resolves inside the bench."""
    p, r = Path(path).resolve(), Path(root).resolve()
    if p != r and r not in p.parents:
        raise BenchRefusal(f"refusing a write outside the bench directory: {p} (bench {r})")
    return p


# ---------------------------------------------------------------------------------------------
# Re-keying
# ---------------------------------------------------------------------------------------------

def rekeyer(
    copy: int,  # The copy number (0 = the original: identity)
) -> Callable[[Any], Any]:  # value -> value with every UUID rewritten into the copy's ids
    """The copy's id map, applied to any value: a string has every UUID token replaced by
    uuid5(REKEY_NS, "c:<uuid>") (memoized), anything else passes through. Copy 0 is the
    identity, so a tier's copy 0 is the live graph's own ids."""
    if copy == 0:
        return lambda v: v
    memo: Dict[str, str] = {}

    def one(m) -> str:
        u = m.group(0)
        v = memo.get(u)
        if v is None:
            v = memo[u] = str(uuid.uuid5(REKEY_NS, f"{copy}:{u}"))
        return v

    def rekey(v: Any) -> Any:
        return UUID_RE.sub(one, v) if isinstance(v, str) else v
    return rekey


# ---------------------------------------------------------------------------------------------
# Snapshot, copies, tiers
# ---------------------------------------------------------------------------------------------

def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True))
    os.replace(tmp, path)


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _count_ops(text: str) -> int:  # Non-blank lines (one op each)
    return sum(1 for line in text.splitlines() if line.strip())


def _degree_hub(con: sqlite3.Connection) -> Optional[Dict[str, Any]]:
    """The node with the most edges (either end): the default `{hub}` a read verb is aimed at."""
    row = con.execute(
        "SELECT id, COUNT(*) AS d FROM (SELECT source_id AS id FROM edges UNION ALL "
        "SELECT target_id FROM edges) GROUP BY id ORDER BY d DESC LIMIT 1").fetchone()
    if not row:
        return None
    node = con.execute("SELECT id, label, properties FROM nodes WHERE id = ?", (row[0],)).fetchone()
    if not node:
        return None
    return {"id": node[0], "label": node[1], "properties": json.loads(node[2] or "{}"),
            "degree": row[1]}


def _term_of(props: Dict[str, Any]) -> Optional[str]:
    """A word to search for: the first 3+ letter word of the hub's name-like property."""
    for key in ("name", "title", "qualname", "slug", "statement", "text"):
        v = props.get(key)
        if isinstance(v, str):
            m = re.search(r"[A-Za-z_]{3,}", v)
            if m:
                return m.group(0)
    return None


def placeholders_of(
    db: str,  # A snapshot db (read-only)
) -> Dict[str, Any]:  # {hub, hub_label, term, session} -- None where the graph has none
    """The default targets of the canonical verbs, read off the snapshot once."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        hub = _degree_hub(con)
        session = con.execute("SELECT properties FROM nodes WHERE label = 'Session' "
                              "ORDER BY created_at DESC LIMIT 1").fetchone()
    finally:
        con.close()
    key = (json.loads(session[0] or "{}") if session else {}).get("key")
    return {"hub": hub and hub["id"], "hub_label": hub and hub["label"],
            "term": hub and _term_of(hub["properties"]), "session": key}


def snapshot(
    lane: Dict[str, Any],  # `resolve_lane`'s result
    root: Path,            # The lane's bench directory
) -> Dict[str, Any]:  # The base manifest
    """Copy 0: the live db through SQLite's backup API (a consistent snapshot under a live
    writer) and every journal family's segments as they stand (the live tail cut at its last
    complete line). Counts ops per family once, for the tiers' replay offsets."""
    t0 = time.perf_counter()
    base = inside(root / "base", root.parent)
    if base.exists():
        shutil.rmtree(base)
    (base / "journals").mkdir(parents=True)
    db = base / Path(lane["db"]).name
    src = sqlite3.connect(f"file:{lane['db']}?mode=ro", uri=True)
    dst = sqlite3.connect(str(db))
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    families: Dict[str, Any] = {}
    for fam, live in lane["journals"].items():
        segs = journal_segments(live)
        rows = []
        for seg in segs:
            p = Path(seg)
            if seg == str(Path(live)):  # the live tail: a writer may be mid-line
                data = p.read_bytes()
                data = data[: data.rfind(b"\n") + 1]
                (base / "journals" / p.name).write_bytes(data)
                ops = _count_ops(data.decode("utf-8"))
            else:
                shutil.copy2(seg, base / "journals" / p.name)
                ops = _count_ops(read_segment(seg))
            rows.append({"name": p.name, "ops": ops, "live": seg == str(Path(live))})
        families[fam] = {"live": Path(live).name, "segments": rows,
                         "ops": sum(r["ops"] for r in rows)}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        nodes = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edges = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    finally:
        con.close()
    manifest = {"lane": lane["name"], "kind": lane["kind"], "source_db": lane["db"],
                "db": db.name, "taken_at": time.time(), "nodes": nodes, "edges": edges,
                "db_bytes": db.stat().st_size, "journals": families,
                "placeholders": placeholders_of(str(db)), "wall_s": time.perf_counter() - t0}
    manifest["base_id"] = f"{manifest['taken_at']:.6f}"
    _write_json(base / MANIFEST, manifest)
    return manifest


def stage_copy(
    base_dir: str,  # The snapshot directory
    copy_dir: str,  # This copy's staging directory
    copy: int,      # The copy number (>= 1)
) -> Dict[str, Any]:  # The copy's manifest
    """Re-key copy `copy` of the snapshot once: its rows (nodes, edges -- no indexes, the
    tiers build those) and every journal segment, each stored as a compressed cold segment
    (the base's live tail included), so a tier hard-links them and never rewrites a copy."""
    t0 = time.perf_counter()
    base, out = Path(base_dir), Path(copy_dir)
    man = _read_json(base / MANIFEST) or {}
    if out.exists():
        shutil.rmtree(out)
    (out / "journals").mkdir(parents=True)
    rekey = rekeyer(copy)
    con = sqlite3.connect(str(out / "rows.db"))
    try:
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("PRAGMA synchronous=OFF")
        con.execute("ATTACH DATABASE ? AS b", (f"file:{base / man['db']}?mode=ro",))
        for table in COPY_TABLES:
            con.execute(f"CREATE TABLE {table} AS SELECT * FROM b.{table} WHERE 0")
            cols = len(con.execute(f"SELECT * FROM b.{table} LIMIT 0").description)
            sql = f"INSERT INTO {table} VALUES ({','.join('?' * cols)})"
            cur = con.execute(f"SELECT * FROM b.{table}")
            while True:
                rows = cur.fetchmany(20000)
                if not rows:
                    break
                con.executemany(sql, [tuple(rekey(v) for v in r) for r in rows])
        con.commit()
    finally:
        con.close()
    families: Dict[str, Any] = {}
    for fam, info in man["journals"].items():
        stem = info["live"][: -len(".jsonl")] if info["live"].endswith(".jsonl") else info["live"]
        rows = []
        for i, seg in enumerate(info["segments"], start=1):
            text = read_segment(str(base / "journals" / seg["name"]))
            name = f"{stem}.{i:04d}.jsonl.xz"
            write_segment(str(out / "journals" / name), rekey(text))
            rows.append({"name": name, "ops": seg["ops"]})
        families[fam] = {"segments": rows, "ops": sum(r["ops"] for r in rows)}
    manifest = {"copy": copy, "base_id": man["base_id"], "journals": families,
                "wall_s": time.perf_counter() - t0,
                "peak_rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    _write_json(out / MANIFEST, manifest)
    return manifest


def _schema(con: sqlite3.Connection, kind: str) -> List[str]:  # The snapshot's own DDL
    return [r[0] for r in con.execute(
        "SELECT sql FROM b.sqlite_master WHERE type = ? AND sql IS NOT NULL "
        "AND name NOT LIKE 'sqlite_%' ORDER BY rowid", (kind,))]


def assemble_tier(
    root: Path,  # The lane's bench directory
    k: int,      # The tier: copies 0 .. k-1
    lane: Dict[str, Any],  # `resolve_lane`'s result (its manifests directory)
) -> Dict[str, Any]:  # The tier's manifest
    """Build tier xK: a fresh db holding the snapshot's rows plus copies 1 .. k-1 (plain row
    copies; the snapshot's own indexes created after the rows, then ANALYZE), the journal
    families as hard links to every copy's segments with the snapshot's live tail as the
    tier's own live tail, and the tier's config -- every path inside the bench."""
    t0 = time.perf_counter()
    phases: Dict[str, float] = {}
    base = root / "base"
    man = _read_json(base / MANIFEST) or {}
    tier = inside(root / f"x{k}", root.parent)
    if tier.exists():
        shutil.rmtree(tier)
    for d in ("journals", "repos", "memory"):
        (tier / d).mkdir(parents=True)
    os.environ["SQLITE_TMPDIR"] = str(tier)  # index sorts spill beside the tier, never to /tmp
    db = tier / man["db"]
    tmp = db.with_name(db.name + ".building")
    con = sqlite3.connect(str(tmp))
    try:
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("PRAGMA synchronous=OFF")
        con.execute("PRAGMA cache_size=-2000000")
        con.execute("ATTACH DATABASE ? AS b", (f"file:{base / man['db']}?mode=ro",))
        for ddl in _schema(con, "table"):
            con.execute(ddl)
        tables = [r[0] for r in con.execute(
            "SELECT name FROM b.sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        t = time.perf_counter()
        for table in tables:
            con.execute(f"INSERT INTO main.{table} SELECT * FROM b.{table}")
        con.commit()
        con.execute("DETACH DATABASE b")
        for c in range(1, k):
            con.execute("ATTACH DATABASE ? AS c",
                        (f"file:{root / 'copies' / f'c{c:04d}' / 'rows.db'}?mode=ro",))
            for table in COPY_TABLES:
                con.execute(f"INSERT INTO main.{table} SELECT * FROM c.{table}")
            con.commit()
            con.execute("DETACH DATABASE c")
        phases["rows"] = time.perf_counter() - t
        t = time.perf_counter()
        con.execute("ATTACH DATABASE ? AS b", (f"file:{base / man['db']}?mode=ro",))
        indexes = _schema(con, "index")
        con.execute("DETACH DATABASE b")
        for ddl in indexes:
            con.execute(ddl)
        phases["indexes"] = time.perf_counter() - t
        t = time.perf_counter()
        for table in COPY_TABLES:
            con.execute(f"ANALYZE {table}")
        con.commit()
        phases["analyze"] = time.perf_counter() - t
        con.execute("PRAGMA journal_mode=WAL")
    finally:
        con.close()
    os.replace(tmp, db)
    t = time.perf_counter()
    families: Dict[str, Any] = {}
    for fam, info in man["journals"].items():
        live = info["live"]
        stem = live[: -len(".jsonl")] if live.endswith(".jsonl") else live
        cold, index = [], 0
        sources = [base / "journals" / s["name"] for s in info["segments"] if not s["live"]]
        cold_ops = [s["ops"] for s in info["segments"] if not s["live"]]
        for c in range(1, k):
            cm = _read_json(root / "copies" / f"c{c:04d}" / MANIFEST) or {}
            for s in cm["journals"][fam]["segments"]:
                sources.append(root / "copies" / f"c{c:04d}" / "journals" / s["name"])
                cold_ops.append(s["ops"])
        for src, ops in zip(sources, cold_ops):
            index += 1
            suffix = ".jsonl.xz" if str(src).endswith(".xz") else ".jsonl"
            name = f"{stem}.{index:04d}{suffix}"
            os.link(src, inside(tier / "journals" / name, root.parent))
            cold.append({"name": name, "ops": ops})
        tail = [s for s in info["segments"] if s["live"]]
        tail_ops = 0
        if tail:
            shutil.copyfile(base / "journals" / tail[0]["name"], tier / "journals" / live)
            tail_ops = tail[0]["ops"]
        families[fam] = {"live": str(tier / "journals" / live), "cold": cold,
                         "ops": sum(r["ops"] for r in cold) + tail_ops}
    phases["journals"] = time.perf_counter() - t
    config = {"_": "A bench tier (design detail 3bd8673d): generated by `bench build`, never "
                   "edited -- every path inside the bench directory, no sibling graphs.",
              "repos_dir": str(tier / "repos"), "memory_dir": str(tier / "memory"),
              "manifests_dir": lane["manifests_dir"], "code_exclude": [], "sibling_graphs": {}}
    if "memory" in families:
        config["memory_journal_path"] = families["memory"]["live"]
    _write_json(tier / CONFIG_NAME, config)
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        nodes = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edges = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    finally:
        con.close()
    manifest = {"k": k, "lane": lane["name"], "kind": lane["kind"], "base_id": man["base_id"],
                "db": str(db), "built_at": time.time(), "nodes": nodes, "edges": edges,
                "db_bytes": db.stat().st_size, "journals": families, "phases": phases,
                "wall_s": time.perf_counter() - t0, "placeholders": man.get("placeholders") or {}}
    _write_json(tier / MANIFEST, manifest)
    return manifest


def build(
    lane: Dict[str, Any],          # `resolve_lane`'s result
    settings: Dict[str, Any],      # `bench_config`'s result
    tiers: Optional[List[int]] = None,  # Tiers to build (default: the configured list + x1)
    resnapshot: bool = False,      # Take a fresh snapshot (every copy and tier is rebuilt)
    ledger: Optional[str] = None,  # The timings ledger: one `bench-build` event per stage
) -> Dict[str, Any]:  # {lane, base, copies, tiers}
    """Snapshot (once), stage the copies the tiers need (missing or from another snapshot, in
    parallel), then assemble each tier not already built from this snapshot."""
    root = lane_dir(settings, lane)
    inside(root, settings["bench_dir"])
    wanted = sorted({1, *(tiers or settings["tiers"])})
    base = _read_json(root / "base" / MANIFEST)
    out: Dict[str, Any] = {"lane": lane["name"], "copies": [], "tiers": []}
    if resnapshot or base is None:
        base = snapshot(lane, root)
        _event(ledger, "bench-snapshot", base["wall_s"], lane, 1,
               {"nodes": base["nodes"], "edges": base["edges"], "db_bytes": base["db_bytes"]})
    out["base"] = {k: base[k] for k in ("base_id", "nodes", "edges", "db_bytes", "taken_at")}
    need = [c for c in range(1, max(wanted))
            if (_read_json(root / "copies" / f"c{c:04d}" / MANIFEST) or {}).get("base_id")
            != base["base_id"]]
    if need:
        (root / "copies").mkdir(exist_ok=True)
        with ProcessPoolExecutor(max_workers=max(1, int(settings["workers"]))) as pool:
            futs = [pool.submit(stage_copy, str(root / "base"),
                                str(inside(root / "copies" / f"c{c:04d}", root.parent)), c)
                    for c in need]
            for f in futs:
                m = f.result()
                out["copies"].append({"copy": m["copy"], "wall_s": m["wall_s"],
                                      "peak_rss_kb": m["peak_rss_kb"]})
                _event(ledger, "bench-copy", m["wall_s"], lane, m["copy"],
                       {"peak_rss_kb": m["peak_rss_kb"]})
    for k in wanted:
        tm = _read_json(root / f"x{k}" / MANIFEST)
        if tm and tm.get("base_id") == base["base_id"] and not resnapshot:
            out["tiers"].append({**_tier_row(tm), "reused": True})
            continue
        tm = assemble_tier(root, k, lane)
        _event(ledger, "bench-build", tm["wall_s"], lane, k,
               {"nodes": tm["nodes"], "edges": tm["edges"], "db_bytes": tm["db_bytes"],
                "phases": tm["phases"]})
        out["tiers"].append({**_tier_row(tm), "reused": False})
    return out


def _tier_row(tm: Dict[str, Any]) -> Dict[str, Any]:  # A tier manifest's summary row
    jb = 0
    for fam in tm["journals"].values():
        for p in [*(Path(tm["db"]).parent / "journals" / c["name"] for c in fam["cold"]),
                  Path(fam["live"])]:
            jb += p.stat().st_size if p.exists() else 0
    return {"k": tm["k"], "nodes": tm["nodes"], "edges": tm["edges"], "db_bytes": tm["db_bytes"],
            "journal_bytes": jb, "wall_s": tm["wall_s"], "built_at": tm["built_at"]}


def _event(
    ledger: Optional[str],  # The timings ledger (None = not recorded)
    verb: str,              # The event's name
    wall: float,            # Seconds
    lane: Dict[str, Any],   # The lane
    tier: int,              # The tier (or copy) it measures
    extra: Dict[str, Any],  # Anything else the event carries
    attrs: Optional[Dict[str, Any]] = None,  # More identifying context (a load run's load, clients, client)
    **fields,               # Top-level fields (run_id, outcome, ...)
) -> None:
    """One harness event in the lane's ledger (fail-open, like every timing append)."""
    if not ledger:
        return
    ev = {"verb": verb, "kind": "bench", "ts": time.time(), "wall_s": round(wall, 6),
          "attrs": {"lane": lane["name"], "tier": tier, **(attrs or {})}, **extra, **fields}
    append_timing(ledger, ev)


# ---------------------------------------------------------------------------------------------
# Running the canonical verbs
# ---------------------------------------------------------------------------------------------

def verb_specs(lane: Dict[str, Any]) -> List[Dict[str, Any]]:  # The lane's canonical verbs
    """The canonical verbs for a lane: the configured `verbs` replace the default list (the
    generic reads, the delta replay by the lane's kind, the comparator); `extra_verbs` add to
    it (the dev lane's readiness, journal-window and writes are DATA in its config)."""
    per = lane["settings"]
    base = per.get("verbs") or [*READ_VERBS, *REPLAY_VERBS.get(lane["kind"], []), *PROOF_VERBS]
    return [*base, *(per.get("extra_verbs") or [])]


def family_ops(fam: Dict[str, Any]) -> int:  # Ops across a tier family now
    """Ops in a tier's family as it stands: the cold segments counted at assembly (immutable),
    the live tail and any segment rotated since counted now."""
    known = {c["name"]: c["ops"] for c in fam["cold"]}
    total = 0
    for seg in journal_segments(fam["live"]):
        name = Path(seg).name
        total += known[name] if name in known else _count_ops(read_segment(seg))
    return total


def _fill(arg: str, values: Dict[str, Any]) -> Optional[str]:  # One argv item, or None
    missing = False

    def sub(m):
        nonlocal missing
        v = values.get(m.group(1))
        if v is None:
            missing = True
            return ""
        return str(v)
    out = re.sub(r"\{(\w+)\}", sub, arg)
    return None if missing else out


def env_python(
    env: Optional[str],  # A conda env's name (None = this interpreter's)
) -> str:  # Its interpreter
    """The interpreter of the env an app runs in (design detail 4389a579: a gesture driver runs
    in its app's env), the envs root derived from this interpreter's sys.prefix -- never a path
    on this machine (6dfe00e9). A missing env refuses loudly."""
    if not env:
        return sys.executable
    py = Path(sys.prefix).parent / env / "bin" / "python"
    if not py.exists():
        raise BenchRefusal(f"no env {env!r} beside {sys.prefix} (a gesture driver's env is "
                           "DATA in the lane's bench config)")
    return str(py)


def tree_rss_kb(
    pid: int,  # The root of a process tree
) -> int:  # Resident KB of the process and every live descendant now (0 when unreadable)
    """The tree's resident set, read from /proc (statm + each task's children). An invocation's
    graph worker is its child, and its memory is the one a whole-scope read grows -- wait4 sees
    only reaped children, so the harness samples the tree instead. 0 off Linux."""
    page_kb = os.sysconf("SC_PAGE_SIZE") // 1024
    total, stack, seen = 0, [pid], set()
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        try:
            total += int(Path(f"/proc/{p}/statm").read_text().split()[1]) * page_kb
            for task in Path(f"/proc/{p}/task").iterdir():
                stack += [int(c) for c in (task / "children").read_text().split()]
        except (OSError, ValueError):
            continue
    return total


def measure(
    argv: List[str],         # The full command line
    env: Dict[str, str],     # Its environment
    limit_bytes: int,        # Address-space limit
    timeout_s: float,        # Wall limit
    out_path: Path,          # Where its stdout lands (inside the bench)
) -> Dict[str, Any]:  # {outcome, wall_s, peak_rss_kb, maxrss_kb, out_bytes}
    """Run one invocation under the limits: its peak RSS is the process tree's (the graph
    worker included), sampled from /proc while it runs; wait4's own maxrss rides beside it."""
    def limit():
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
    t0 = time.perf_counter()
    # the answer reaches the harness through a pipe, as a caller's does, and the HARNESS writes
    # the file: the measured process's disk bytes are its own, never its answer's (7345f40d)
    with open(out_path, "wb") as out:
        proc = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                preexec_fn=limit)
        drain = threading.Thread(target=shutil.copyfileobj, args=(proc.stdout, out), daemon=True)
        drain.start()
        status, usage, timed_out, peak = None, None, False, 0
        while True:
            peak = max(peak, tree_rss_kb(proc.pid))
            pid, st, ru = os.wait4(proc.pid, os.WNOHANG)
            if pid:
                status, usage = st, ru
                break
            if time.perf_counter() - t0 > timeout_s:
                proc.kill()
                _, status, usage = os.wait4(proc.pid, 0)
                timed_out = True
                break
            time.sleep(0.02)
        drain.join(5.0 if timed_out else None)  # a killed run's orphans may hold the pipe
        proc.returncode = os.waitstatus_to_exitcode(status)
    outcome: Any = "timeout" if timed_out else proc.returncode
    maxrss = usage.ru_maxrss if usage else None
    # the tree's block I/O (design detail 2f5033bf (1)): wait4 counts every reaped descendant,
    # GROSS -- a cancelled write still counts; the net figure is the invocation's own event
    return {"outcome": outcome, "wall_s": time.perf_counter() - t0,
            "peak_rss_kb": max(peak, maxrss or 0) or None, "maxrss_kb": maxrss,
            "out_bytes": out_path.stat().st_size,
            "read_bytes": usage.ru_inblock * 512 if usage else None,
            "write_bytes": usage.ru_oublock * 512 if usage else None}


def run(
    lane: Dict[str, Any],       # `resolve_lane`'s result
    settings: Dict[str, Any],   # `bench_config`'s result
    ledger: str,                # The timings ledger every event lands in
    tiers: Optional[List[int]] = None,  # Tiers to run (default: every built tier)
    verbs: Optional[List[str]] = None,  # Spec names to run (default: all)
    repeat: int = 1,            # Runs per verb per tier
) -> Dict[str, Any]:  # {lane, runs: [...], skipped: [...]}
    """Run the canonical verbs against each tier: real CLI invocations with the tier's db,
    journals and config, the lane's ledger, the tier's tags in the environment, under the
    address-space and wall limits; one harness event per invocation beside the CLI's own.

    A spec naming a `driver` runs an app's canonical GESTURES instead (design detail 4389a579):
    the kit's gesture runner in the app's `env`, the driver given the spec's filled `params`
    plus the tier's db, manifests and journals; each gesture is one event from the session's
    own seam (CJM_TIMINGS_PATH names the lane's ledger), the harness's event beside them
    carrying the driver process's wall and peak RSS."""
    root = lane_dir(settings, lane)
    built = sorted(int(p.name[1:]) for p in root.glob("x*") if p.name[1:].isdigit()
                   and (p / MANIFEST).exists())
    todo = [k for k in built if tiers is None or k in tiers]
    if not todo:
        raise BenchRefusal(f"no built tier to run in {root} -- `bench build` first")
    specs = [s for s in verb_specs(lane) if verbs is None or s["name"] in verbs]
    limit = int(float(settings["memory_limit_gb"]) * 1024 ** 3)
    out: Dict[str, Any] = {"lane": lane["name"], "runs": [], "skipped": []}
    for k in todo:
        tm = _read_json(root / f"x{k}" / MANIFEST)
        tier = inside(root / f"x{k}", root.parent)
        db = tm["db"]
        fams = tm["journals"]
        values: Dict[str, Any] = {**tm.get("placeholders", {}),
                                  **(lane["settings"].get("placeholders") or {}), "db": db}
        for _ in range(max(1, repeat)):
            for spec in specs:
                values["stamp"] = time.strftime("%Y-%m-%dT%H:%M:%S") + f" x{k}"
                if "writes" in fams:
                    values["tail_offset"] = max(0, family_ops(fams["writes"])
                                                - int(settings["replay_tail"]))
                run_id = str(uuid.uuid4())
                env = {**os.environ, "CJM_RUN_ID": run_id, "CJM_BENCH_TIER": str(k),
                       "CJM_BENCH_LANE": lane["name"], "CJM_ACTOR": "bench:harness",
                       "CJM_T0": repr(time.time())}
                if spec.get("driver"):
                    params = {key: _fill(v, values) if isinstance(v, str) else v
                              for key, v in (spec.get("params") or {}).items()}
                    if any(v is None for v in params.values()):
                        out["skipped"].append({"tier": k, "verb": spec["name"],
                                               "reason": "a placeholder did not resolve"})
                        continue
                    params.update({"db": db, "manifests_dir": lane["manifests_dir"],
                                   "journals": {name: fam["live"] for name, fam in fams.items()}})
                    cmd = [env_python(spec.get("env")), "-m", "cjm_substrate_qt_kit.gesturebench",
                           spec["driver"], "--params", json.dumps(params)]
                    env["CJM_TIMINGS_PATH"] = ledger
                    what = {"driver": spec["driver"]}
                else:
                    argv = [_fill(a, values) for a in spec["argv"]]
                    if any(a is None for a in argv):
                        out["skipped"].append({"tier": k, "verb": spec["name"],
                                               "reason": "a placeholder did not resolve"})
                        continue
                    cmd = [sys.executable, "-m", "cjm_context_graph_projection.cli",
                           "--graph-db-path", db, "--manifests-dir", lane["manifests_dir"],
                           "--timings-path", ledger]
                    if "writes" in fams:
                        cmd += ["--journal-path", fams["writes"]["live"]]
                    if "source" in fams:
                        cmd += ["--source-journal-path", fams["source"]["live"]]
                    if spec.get("read"):
                        cmd.append("--read-only")
                    if spec.get("format"):
                        cmd += ["--format", spec["format"]]
                    cmd += argv
                    what = {"cli_verb": argv[0]}
                (tier / "runs").mkdir(exist_ok=True)
                res = measure(cmd, env, limit, float(settings["timeout_s"]),
                              inside(tier / "runs" / f"{run_id}.out", root.parent))
                if spec.get("capture") and res["outcome"] == 0:
                    m = UUID_RE.search((tier / "runs" / f"{run_id}.out").read_text(errors="replace"))
                    values[spec["capture"]] = m.group(0) if m else None
                row = {"tier": k, "verb": spec["name"], "run_id": run_id, **res}
                if res["outcome"] == 0:
                    (tier / "runs" / f"{run_id}.out").unlink()
                else:  # a failed run keeps its output (the traceback is the diagnosis)
                    row["out"] = str(tier / "runs" / f"{run_id}.out")
                out["runs"].append(row)
                _event(ledger, spec["name"], res["wall_s"], lane, k,
                       {"peak_rss_kb": res["peak_rss_kb"], "maxrss_kb": res["maxrss_kb"],
                        "out_bytes": res["out_bytes"], "read_bytes": res["read_bytes"],
                        "write_bytes": res["write_bytes"]},
                       run_id=run_id, outcome=res["outcome"], **what)
    return out


def load_settings(settings: Dict[str, Any]) -> Dict[str, Any]:  # LOAD_DEFAULTS overlaid with `load`
    """The concurrent replay mode's settings as DATA: the bench config's `load` object."""
    return {**LOAD_DEFAULTS, **(settings.get("load") or {})}


def verb_parser(
    parser,     # The CLI's parser (`cli.build_parser()`)
    verb: str,  # A verb
):  # Its subparser, or None when the CLI has no such verb
    """One verb's subparser, read off the parser's subcommands."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices.get(verb)
    return None


def invert_request(
    parser,                    # The CLI's parser
    verb: str,                 # The recorded verb
    request: Dict[str, Any],   # Its recorded keys: argparse dests (a read's request, a write's args)
    drop: frozenset = frozenset(),  # Keys never inverted (a write op's envelope: DROP_KEYS)
) -> Dict[str, Any]:  # {argv, dropped, error}: argv None when it does not invert
    """A recorded request back to its argv by the verb's OWN parser (design detail 2f5033bf (2)):
    a key that is a dest of the verb lands as its positional (in order) or its option; a key no
    argument takes is DROPPED (an envelope field: a label, an observation) and reported; a missing
    required positional refuses the step -- never guessed."""
    sub = verb_parser(parser, verb)
    if sub is None:
        return {"argv": None, "dropped": sorted(request), "error": f"no verb {verb!r}"}
    actions = {a.dest: a for a in sub._actions if a.dest != "help"}
    positional, options, dropped = [], [], []
    for a in sub._actions:
        if a.option_strings or a.dest == "help":
            continue
        v = request.get(a.dest)
        if v in (None, "", []):
            if a.nargs not in ("?", "*"):
                return {"argv": None, "dropped": [], "error": f"missing {a.dest}"}
            continue
        positional += [str(x) for x in v] if isinstance(v, list) else [str(v)]
    for k, v in request.items():
        a = actions.get(k)
        if k in drop or a is None:
            dropped.append(k)
            continue
        if not a.option_strings or v in (None, "", [], False):
            continue
        if isinstance(v, str) and v.startswith("[") and (a.nargs in ("*", "+") or
                                                          isinstance(a, argparse._AppendAction)):
            try:
                v = json.loads(v)  # the reads ledger JSON-dumps a list
            except ValueError:
                pass
        flag = a.option_strings[-1]
        if isinstance(a, (argparse._StoreTrueAction, argparse._StoreConstAction)):
            options.append(flag)
        elif isinstance(a, argparse._AppendAction):
            for x in (v if isinstance(v, list) else [v]):
                options += [flag, str(x)]
        elif isinstance(v, list):
            options += [flag, *[str(x) for x in v]]
        else:
            options += [flag, str(v)]
    return {"argv": [verb, *positional, *options], "dropped": sorted(dropped), "error": None}


def _normalized(
    value: Any,  # A recorded request value
) -> Any:  # The value, a JSON-dumped list decoded (the reads ledger dumps a non-string value)
    if isinstance(value, str) and value.startswith("["):
        try:
            v = json.loads(value)
            return v if isinstance(v, list) else value
        except ValueError:
            return value
    return value


def _expand_prefixes(
    value: Any,               # A request value
    con: sqlite3.Connection,  # The snapshot (read-only)
) -> Any:  # The value with every whole-token id prefix expanded to its full id (unique matches only)
    def one(s: str) -> str:
        if not HEX_RE.match(s):
            return s
        rows = con.execute("SELECT id FROM nodes WHERE id >= ? AND id < ? LIMIT 2",
                           (s, s + "g")).fetchall()
        return rows[0][0] if len(rows) == 1 else s
    if isinstance(value, list):
        return [one(v) if isinstance(v, str) else v for v in value]
    return one(value) if isinstance(value, str) else value


def _unknown_ids(
    request: Dict[str, Any],  # A step's request, prefixes expanded
    con: sqlite3.Connection,  # The snapshot (read-only)
    minted: set,              # Ids the trace minted before this step
) -> List[str]:  # The ids it names that neither the snapshot nor the trace holds
    out = []
    for v in request.values():
        for x in (v if isinstance(v, list) else [v]):
            if not isinstance(x, str):
                continue
            if HEX_RE.match(x):
                out.append(x)  # a prefix that matched no node (or several)
            elif UUID_RE.fullmatch(x) and x not in minted and not con.execute(
                    "SELECT 1 FROM nodes WHERE id = ?", (x,)).fetchone():
                out.append(x)
    return out


def extract_workload(
    lane: Dict[str, Any],       # `resolve_lane`'s result
    settings: Dict[str, Any],   # `bench_config`'s result
    session: str,               # The recorded session to replay
    actor: Optional[str] = None,  # Only this actor's steps (a sub-agent's trace), else the session's all
    name: Optional[str] = None,   # The workload's name (default: the session, and the actor)
) -> Dict[str, Any]:  # The workload, written to <lane>/workloads/<name>.json
    """A recorded session as a WORKLOAD (design detail 2f5033bf (2)): its reads-ledger requests
    and writes-journal ops in ts order, the verbs the load settings keep, each step's request as
    the verb's dests (inverted to argv at load time, per client), id prefixes expanded against
    the snapshot so a client's re-keying reaches them; a decide records the id it minted (its
    canonical statement's) so a later step naming it follows the client's own. Derived and
    regenerable; what did not invert is counted, never guessed."""
    from cjm_dev_graph_schema.nodes import DecisionNode
    from .cli import build_parser
    root = lane_dir(settings, lane)
    base = _read_json(root / "base" / MANIFEST)
    if not base:
        raise BenchRefusal(f"no snapshot in {root} -- `bench build` first")
    ls = load_settings(settings)
    keep = set(ls["verbs"])
    db = Path(lane["db"])
    reads = db.with_name(db.stem + ".reads.jsonl")
    rows = []
    if reads.exists():
        rows += [("read", e.get("verb"), e.get("request") or {}, e.get("ts") or 0, e.get("actor"))
                 for e in read_journal(str(reads)) if e.get("session") == session]
    if lane["journals"].get("writes"):
        rows += [("write", e.get("verb"), e.get("args") or {}, e.get("ts") or 0,
                  (e.get("args") or {}).get("actor") or e.get("actor"))
                 for e in read_journal(lane["journals"]["writes"]) if e.get("session") == session]
    rows = sorted((r for r in rows if actor is None or r[4] == actor), key=lambda r: r[3])
    parser = build_parser()
    con = sqlite3.connect(f"file:{root / 'base' / base['db']}?mode=ro", uri=True)
    steps, skipped, dropped, minted = [], {}, {}, set()
    try:
        prev = None
        for kind, verb, request, ts, _ in rows:
            if verb not in keep:
                skipped[f"{verb} (not kept)"] = skipped.get(f"{verb} (not kept)", 0) + 1
                continue
            request = {k: _normalized(v) for k, v in request.items()}
            drop = DROP_KEYS if kind == "write" else frozenset()
            inv = invert_request(parser, verb, request, drop)
            if inv["argv"] is None:
                skipped[f"{verb} ({inv['error']})"] = skipped.get(f"{verb} ({inv['error']})", 0) + 1
                continue
            for k in inv["dropped"]:
                dropped[f"{verb}.{k}"] = dropped.get(f"{verb}.{k}", 0) + 1
            req = {k: _expand_prefixes(v, con) for k, v in request.items() if k not in inv["dropped"]}
            missing = _unknown_ids(req, con, minted)
            if missing:  # a node newer than the snapshot: the step would fail at every tier
                why = f"{verb} (names a node the snapshot lacks)"
                skipped[why] = skipped.get(why, 0) + 1
                continue
            step = {"kind": kind, "verb": verb, "request": req, "drop": sorted(drop),
                    "gap_s": round(ts - prev, 3) if prev is not None else 0.0}
            if verb == "decide" and req.get("statement"):
                step["mints"] = DecisionNode(statement=str(req["statement"])).id
                minted.add(step["mints"])
            steps.append(step)
            prev = ts
    finally:
        con.close()
    name = name or re.sub(r"[^A-Za-z0-9_.-]+", "_", session + (f"--{actor}" if actor else ""))
    out = {"name": name, "lane": lane["name"], "base_id": base["base_id"],
           "source": {"session": session, "actor": actor}, "steps": steps,
           "reads": sum(1 for s in steps if s["kind"] == "read"),
           "writes": sum(1 for s in steps if s["kind"] == "write"),
           "skipped": skipped, "dropped": dropped, "made_at": time.time()}
    (root / "workloads").mkdir(exist_ok=True)
    _write_json(inside(root / "workloads" / f"{name}.json", root.parent), out)
    return out


def _client_argv(
    parser,                  # The CLI's parser
    step: Dict[str, Any],    # One workload step
    rekey: Callable[[Any], Any],  # The client's copy's id map
    minted: Dict[str, str],  # Ids the trace minted (re-keyed) -> the ids this client minted
    marker: str,             # The client's run-unique marker
) -> Dict[str, Any]:  # {argv, mints}: the step's argv for this client, and the id it will mint
    """One step for one client: ids re-keyed into the client's copy, then any the trace minted
    earlier swapped for the client's own; free text marked; a decide's new id computed from its
    marked statement (the same identity rule)."""
    from cjm_dev_graph_schema.nodes import DecisionNode

    def remap(v: Any) -> Any:
        if isinstance(v, list):
            return [remap(x) for x in v]
        if not isinstance(v, str):
            return v
        v = rekey(v)
        return UUID_RE.sub(lambda m: minted.get(m.group(0), m.group(0)), v)
    req = {k: remap(v) for k, v in step["request"].items()}
    for k in MARK_DESTS:
        if isinstance(req.get(k), str) and step["kind"] == "write":
            req[k] = f"{req[k]} {marker}"
    mints = None
    if step.get("mints") and req.get("statement"):
        mints = DecisionNode(statement=str(req["statement"])).id
        minted[rekey(step["mints"])] = mints
    return {"argv": invert_request(parser, step["verb"], req, frozenset(step.get("drop") or ()))["argv"],
            "mints": mints}


def _integrity(
    tm: Dict[str, Any],         # The tier's manifest
    load_id: str,               # The load run
    decided: Dict[int, List[str]],  # Per client: the decision ids its successful decides minted
) -> Dict[str, Any]:  # {ok, bad_lines, marked, in_journal, in_db, duplicated}
    """The load's integrity check (design detail 2f5033bf (2)): every line of the writes family
    parses; each client's marked decides appear EXACTLY once in the journal and their nodes are in
    the db; no op of the load is duplicated -- a torn line or a lost op is a finding, never
    absorbed."""
    fam = tm["journals"].get("writes")
    if not fam:
        return {"ok": None, "reason": "no writes family"}
    known = {c["name"] for c in fam["cold"]}
    bad, seen, dup, marked = 0, set(), 0, {}
    tag = f"[bench load {load_id} "
    for seg in journal_segments(fam["live"]):
        if Path(seg).name in known:
            continue  # cold segments of the snapshot: immutable, never written by a load
        for line in read_segment(seg).splitlines():
            if not line.strip():
                continue
            try:
                op = json.loads(line)
            except ValueError:
                bad += 1
                continue
            text = json.dumps(op.get("args") or {}, sort_keys=True)
            if tag not in text:
                continue
            key = (op.get("verb"), text)
            dup += key in seen
            seen.add(key)
            if op.get("verb") == "decide":
                st = str((op.get("args") or {}).get("statement") or "")
                marked[st] = marked.get(st, 0) + 1
    want = [i for ids in decided.values() for i in ids]
    con = sqlite3.connect(f"file:{tm['db']}?mode=ro", uri=True)
    try:
        in_db = sum(con.execute("SELECT COUNT(*) FROM nodes WHERE id = ?", (i,)).fetchone()[0]
                    for i in want)
    finally:
        con.close()
    once = sum(1 for n in marked.values() if n == 1)
    return {"ok": bad == 0 and dup == 0 and in_db == len(want) and once == len(want) == len(marked),
            "bad_lines": bad, "decides": len(want), "in_journal_once": once,
            "in_journal": len(marked), "in_db": in_db, "duplicated": dup}


def memory_ceiling_kb(
    ls: Dict[str, Any],  # `load_settings`'s result
) -> int:  # The clients' summed RSS (KB) that aborts a load run
    """The machine guard (design detail 2f5033bf (2)): a load must never push the machine into
    swap or the OOM killer -- the configured ceiling, else 70% of MemTotal."""
    if ls.get("memory_ceiling_gb"):
        return int(float(ls["memory_ceiling_gb"]) * 1024 ** 2)
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(int(line.split()[1]) * 0.7)
    except (OSError, ValueError):
        pass
    return 16 * 1024 ** 2


def _kill_descendants() -> None:
    """SIGKILL every live descendant of this process (the load's clients and their workers)."""
    import signal
    stack, seen = [os.getpid()], set()
    kids: List[int] = []
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        try:
            for task in Path(f"/proc/{p}/task").iterdir():
                c = [int(x) for x in (task / "children").read_text().split()]
                kids += c
                stack += c
        except (OSError, ValueError):
            continue
    for k in kids:
        try:
            os.kill(k, signal.SIGKILL)
        except OSError:
            pass


def load(
    lane: Dict[str, Any],       # `resolve_lane`'s result
    settings: Dict[str, Any],   # `bench_config`'s result
    ledger: str,                # The timings ledger every event lands in
    tiers: Optional[List[int]] = None,    # Tiers (default: the load settings', those built)
    clients: Optional[List[int]] = None,  # Client counts (default: the load settings')
    workloads: Optional[List[str]] = None,  # Workload names (default: the settings' set, else all)
    steps: Optional[int] = None,          # Steps per client, at most (default: the settings')
) -> Dict[str, Any]:  # {lane, loads: [...]}
    """THE CONCURRENT REPLAY MODE (design detail 2f5033bf (2)): at each tier and client count, N
    clients each replay a workload (round robin over the set) as real CLI invocations under the
    harness's limits -- every call a tier-tagged event carrying the load id, the client count and
    the client (each its own actor, ids re-keyed into copy i mod k so clients act as distinct
    projects; at x1 all share copy 0, the hot spot) -- the clients' summed RSS sampled throughout,
    then the integrity check; one `bench-load` event per run carries the summary. Writes land in
    the tier, as the canonical writes do. THE MACHINE GUARD: when the clients' summed RSS passes
    the memory ceiling the run aborts (every client killed, no further step) and the event says
    so -- itself a reading: the per-process shape cannot hold that many clients at that tier."""
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from .cli import build_parser
    root = lane_dir(settings, lane)
    ls = load_settings(settings)
    names = workloads or ls["workloads"] or sorted(p.stem for p in (root / "workloads").glob("*.json"))
    loaded = [w for w in (_read_json(root / "workloads" / f"{n}.json") for n in names) if w]
    if not loaded:
        raise BenchRefusal(f"no workload in {root / 'workloads'} -- `bench workload --session` first")
    built = sorted(int(p.name[1:]) for p in root.glob("x*") if p.name[1:].isdigit()
                   and (p / MANIFEST).exists())
    todo = [k for k in (tiers or ls["tiers"]) if k in built]
    if not todo:
        raise BenchRefusal(f"no built tier among {tiers or ls['tiers']} in {root}")
    parser = build_parser()
    limit = int(float(settings["memory_limit_gb"]) * 1024 ** 3)
    cap = int(steps or ls["steps"])
    pace = float(ls["pace"])
    ceiling = memory_ceiling_kb(ls)
    out: Dict[str, Any] = {"lane": lane["name"], "loads": []}
    for k in todo:
        tm = _read_json(root / f"x{k}" / MANIFEST)
        tier = inside(root / f"x{k}", root.parent)
        (tier / "runs").mkdir(exist_ok=True)
        for n in clients or ls["clients"]:
            load_id = str(uuid.uuid4())
            decided: Dict[int, List[str]] = {}
            rows: List[Dict[str, Any]] = []
            lock = threading.Lock()

            stop, aborted = threading.Event(), [None]

            def client(i: int) -> None:
                w = loaded[i % len(loaded)]
                rekey, minted = rekeyer(i % k), {}
                marker = f"[bench load {load_id} client {i}]"
                for step in w["steps"][:cap]:
                    if stop.is_set():
                        break
                    if pace and step.get("gap_s"):
                        time.sleep(min(step["gap_s"] * pace, 60.0))
                    made = _client_argv(parser, step, rekey, minted, marker)
                    if made["argv"] is None:
                        continue
                    run_id = str(uuid.uuid4())
                    env = {**os.environ, "CJM_RUN_ID": run_id, "CJM_BENCH_TIER": str(k),
                           "CJM_BENCH_LANE": lane["name"], "CJM_BENCH_LOAD": load_id,
                           "CJM_BENCH_CLIENTS": str(n), "CJM_BENCH_CLIENT": str(i),
                           "CJM_ACTOR": f"bench:load:{i}", "CJM_T0": repr(time.time())}
                    cmd = [sys.executable, "-m", "cjm_context_graph_projection.cli",
                           "--graph-db-path", tm["db"], "--manifests-dir", lane["manifests_dir"],
                           "--timings-path", ledger]
                    for fam, flag in (("writes", "--journal-path"), ("source", "--source-journal-path")):
                        if fam in tm["journals"]:
                            cmd += [flag, tm["journals"][fam]["live"]]
                    if step["kind"] == "read":
                        cmd.append("--read-only")
                    res = measure(cmd + made["argv"], env, limit, float(settings["timeout_s"]),
                                  inside(tier / "runs" / f"{run_id}.out", root.parent))
                    if res["outcome"] == 0:
                        (tier / "runs" / f"{run_id}.out").unlink()
                        if made["mints"]:
                            with lock:
                                decided.setdefault(i, []).append(made["mints"])
                    _event(ledger, step["verb"], res["wall_s"], lane, k,
                           {"peak_rss_kb": res["peak_rss_kb"], "out_bytes": res["out_bytes"],
                            "read_bytes": res["read_bytes"], "write_bytes": res["write_bytes"]},
                           attrs={"load": load_id, "clients": n, "client": i},
                           run_id=run_id, outcome=res["outcome"], cli_verb=step["verb"])
                    with lock:
                        rows.append({"client": i, "verb": step["verb"], "outcome": res["outcome"],
                                     "wall_s": res["wall_s"]})

            peak, done = [0], threading.Event()

            def sample() -> None:  # the clients' summed RSS: every live descendant of the harness
                me = os.getpid()
                page_kb = os.sysconf("SC_PAGE_SIZE") // 1024
                while not done.is_set():
                    try:
                        own = int(Path(f"/proc/{me}/statm").read_text().split()[1]) * page_kb
                    except (OSError, ValueError):
                        own = 0
                    peak[0] = max(peak[0], tree_rss_kb(me) - own)
                    if peak[0] > ceiling and not stop.is_set():
                        aborted[0] = (f"the clients' summed RSS {peak[0] / 1024 ** 2:.1f} GB passed the "
                                      f"{ceiling / 1024 ** 2:.1f} GB ceiling")
                        stop.set()
                    if stop.is_set():
                        _kill_descendants()
                    done.wait(0.05)
            sampler = threading.Thread(target=sample, daemon=True)
            t0 = time.perf_counter()
            sampler.start()
            with ThreadPoolExecutor(max_workers=n) as pool:
                for f in [pool.submit(client, i) for i in range(n)]:
                    f.result()
            done.set()
            sampler.join()
            wall = time.perf_counter() - t0
            check = _integrity(tm, load_id, decided)
            extra = {"peak_rss_kb": peak[0] or None, "integrity": check,
                     "workloads": [w["name"] for w in loaded], "steps": len(rows),
                     "aborted": aborted[0]}
            _event(ledger, "bench-load", wall, lane, k, extra, attrs={"load": load_id, "clients": n})
            out["loads"].append({"tier": k, "clients": n, "load": load_id, "wall_s": wall,
                                 "steps": len(rows),
                                 "failed": sum(1 for r in rows if r["outcome"] != 0),
                                 "peak_rss_kb": peak[0] or None, "integrity": check,
                                 "aborted": aborted[0]})
    return out


def status(
    lane: Dict[str, Any],      # `resolve_lane`'s result
    settings: Dict[str, Any],  # `bench_config`'s result
) -> Dict[str, Any]:  # {lane, root, base, copies, tiers}
    """What the bench holds for a lane: the snapshot, the staged copies, the built tiers."""
    root = lane_dir(settings, lane)
    base = _read_json(root / "base" / MANIFEST)
    copies = sorted(int(p.name[1:]) for p in (root / "copies").glob("c*")
                    if (p / MANIFEST).exists()) if (root / "copies").exists() else []
    tiers = []
    for p in sorted(root.glob("x*"), key=lambda p: int(p.name[1:]) if p.name[1:].isdigit() else 0):
        tm = _read_json(p / MANIFEST)
        if tm:
            tiers.append({**_tier_row(tm), "current": bool(base) and tm["base_id"] == base["base_id"]})
    return {"lane": lane["name"], "root": str(root),
            "base": base and {k: base[k] for k in ("base_id", "nodes", "edges", "db_bytes",
                                                     "taken_at")},
            "copies": copies, "tiers": tiers}


def _gb(n: Optional[float]) -> str:
    return "-" if n is None else (f"{n / 1024 ** 3:.2f} GB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:.1f} MB")


def render_bench(
    action: str,           # build | run | status
    res: Dict[str, Any],   # The action's result
    fmt: str = "human",    # human | agent (JSON)
) -> str:  # The rendered result
    """Render a bench result: tables for a person, the result as JSON for an agent."""
    if fmt == "agent":
        return json.dumps(res, indent=1, default=str)
    out = [f"## Bench {action} — lane {res['lane']}", ""]
    if action == "run":
        out += ["| tier | verb | outcome | wall s | peak RSS | output |", "|---:|---|---|---:|---:|---:|"]
        for r in res["runs"]:
            rss = r["peak_rss_kb"] and r["peak_rss_kb"] * 1024
            out.append(f"| x{r['tier']} | {r['verb']} | {r['outcome']} | {r['wall_s']:.2f} | "
                       f"{_gb(rss)} | {_gb(r['out_bytes'])} |")
        for s in res["skipped"]:
            out.append(f"- skipped x{s['tier']} {s['verb']}: {s['reason']}")
        return "\n".join(out)
    if action == "workload":  # design detail 2f5033bf (2)
        src = res["source"]
        out.append(f"- workload `{res['name']}` from session {src['session']}"
                   + (f" (actor {src['actor']})" if src.get("actor") else "")
                   + f": {res['reads']} read(s) · {res['writes']} write(s)")
        out += [f"- skipped {k}: {n}" for k, n in sorted(res["skipped"].items())]
        out += [f"- dropped {k}: {n}" for k, n in sorted(res["dropped"].items())]
        return "\n".join(out)
    if action == "load":
        out += ["| tier | clients | steps | failed | wall s | summed peak RSS | integrity |",
                "|---:|---:|---:|---:|---:|---:|---|"]
        for r in res["loads"]:
            rss = r["peak_rss_kb"] and r["peak_rss_kb"] * 1024
            check = r["integrity"] or {}
            out.append(f"| x{r['tier']} | {r['clients']} | {r['steps']} | {r['failed']} | "
                       f"{r['wall_s']:.1f} | {_gb(rss)} | {'ok' if check.get('ok') else check} |")
        out += ["", "_perf reads each run: per verb p50 / p99, throughput, failures by outcome._"]
        return "\n".join(out)
    if res.get("base"):
        b = res["base"]
        out.append(f"- snapshot {b['base_id']}: {b['nodes']:,} nodes · {b['edges']:,} edges · "
                   f"{_gb(b['db_bytes'])}")
    if action == "build" and res.get("copies"):
        out.append(f"- staged {len(res['copies'])} copy(ies) this run")
    if action == "status":
        out.append(f"- root {res['root']} · {len(res['copies'])} staged copy(ies)")
    out += ["", "| tier | nodes | edges | db | journals | build s | |", "|---:|---:|---:|---:|---:|---:|---|"]
    for t in res["tiers"]:
        note = ("reused" if t.get("reused") else "built") if action == "build" else \
            ("current" if t.get("current") else "STALE (an older snapshot)")
        out.append(f"| x{t['k']} | {t['nodes']:,} | {t['edges']:,} | {_gb(t['db_bytes'])} | "
                   f"{_gb(t['journal_bytes'])} | {t['wall_s']:.1f} | {note} |")
    return "\n".join(out)
