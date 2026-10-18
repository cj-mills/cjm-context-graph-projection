"""THE SCALE HARNESS: synthetic tiers of a graph and its journals, the canonical verbs run at each.

Design detail 3bd8673d (work item 85b0d368; the audit's synthesis 681604c1). A TIER is k copies
of one graph and its journals: copy 0 is a snapshot of the live graph, and copy c rewrites every
UUID in a row or an op -- ids, edge endpoints, properties, sources -- to uuid5(ns, "c:<uuid>"),
so each copy is a whole, self-consistent graph under the same identity rules. Each copy is
re-keyed ONCE into the staging area; a tier is assembled from the copies by plain row copies
into a fresh db (indexes built after the rows, then ANALYZE) and hard links to their compressed
journal segments, so reaching 32x rewrites 31 copies, never 62.

A tier is SAFE BY CONSTRUCTION: its own graph.config.json names an empty repos and memory
sandbox, its own journals and its own substrate data root (every client's CJM_DATA_DIR, so a
client's graph worker writes its diagnostics and stores inside the tier, 08441cae), and every
path the harness writes resolves inside the bench directory (anything else refuses). The canonical verbs run against each tier as real CLI
invocations under an address-space limit -- one timing event each in the lane's ledger, tagged
with the tier, beside the harness's own event (wall, peak RSS, outcome) -- so a cost that
appears only at 32x shows as a curve, never a crash. `perf` reads the curves.

THE LAYOUT (<bench_dir>/<lane>/): base/ the snapshot, never run against · copies/cNNNN/ the
re-keyed copies (rows without indexes, all-cold compressed journal segments) · xK/ a tier (its
db, config, journals, sandbox, substrate/ root and manifest). The bench directory, the tier list, the verbs and
the limits are DATA in the graph-sibling config's `bench` object; nothing here names a path on
this machine.
"""

import argparse
import hashlib
import json
import math
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
    "pin_keep": 2,               # Pins that keep their env copies (ruling dba3f272); every lane's current pin too
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
    "roles": {},            # Each named load's role -> its source: {workloads: [...]} or {compose: {from, verbs, burst, idle_s}} (2c4bad75 (2))
    "transcript_dir": None,  # Where an ingest workload's recorded transcripts live (2c4bad75 (3))
    "probe_interval_s": 0.01,  # The held reader's poll (7v, 2c4bad75 (4))
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


def _stamp_cursors(
    db: Path,                      # The tier's db (built, not yet open to any client)
    families: Dict[str, Any],      # The tier's journal families: {fam: {live, cold: [{name, ops}], ops}}
) -> Dict[str, Dict[str, Any]]:  # family name -> the cursor stamped
    """A tier's APPLIED CURSORS (ruling e4b2dfbb / 65f4a6c9 (2c); finding beside 6a2b88c5): the tier
    db and its journal families are assembled together from one snapshot, so the db holds every
    family whole -- each family's END is its cursor, stamped at assembly, never carried over from
    the live db (whose cursor names the live family's segments, not the tier's). The ordinal is
    the manifests' record count; the place and digest are read off the last segment holding a
    record (only that segment is read), then CHECKED through the reader the writers use."""
    import hashlib
    import lzma
    from cjm_context_graph_primitives.journal import committed_length, indexed_segments
    from cjm_context_graph_primitives.writer import family_name, JournalPosition, ORIGIN, read_tail
    out: Dict[str, Dict[str, Any]] = {}
    con = sqlite3.connect(str(db))
    try:  # the store's own table (the graph capability creates it the same way on open)
        con.execute("CREATE TABLE IF NOT EXISTS journal_cursors (family TEXT PRIMARY KEY, "
                    "segment INTEGER NOT NULL, byte_offset INTEGER NOT NULL, "
                    "ordinal INTEGER NOT NULL, digest TEXT NOT NULL)")
        for fam in families.values():
            live = fam["live"]
            end = ORIGIN
            for index, seg in reversed(indexed_segments(live)):
                if seg.endswith(".xz"):
                    data = lzma.decompress(Path(seg).read_bytes())
                else:
                    with open(seg, "rb") as fh:
                        data = fh.read(committed_length(seg) if seg == live else -1)
                lines = data.splitlines(keepends=True)
                while lines and not lines[-1].strip():
                    lines.pop()
                if lines:
                    end = JournalPosition(index, len(b"".join(lines)), int(fam["ops"]),
                                          hashlib.sha256(lines[-1]).hexdigest())
                    break
            if read_tail(live, end):  # the check: nothing past the stamped end, the digest holds
                raise BenchRefusal(f"{family_name(live)}: records past the stamped end -- the manifest's count is wrong")
            con.execute("INSERT INTO journal_cursors (family, segment, byte_offset, ordinal, digest) "
                        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(family) DO UPDATE SET segment = excluded.segment, "
                        "byte_offset = excluded.byte_offset, ordinal = excluded.ordinal, digest = excluded.digest",
                        (family_name(live), end.segment, end.offset, end.ordinal, end.digest))
            out[family_name(live)] = end.to_dict()
        con.commit()
    finally:
        con.close()
    return out


def assemble_tier(
    root: Path,  # The lane's bench directory
    k: int,      # The tier: copies 0 .. k-1
    lane: Dict[str, Any],  # `resolve_lane`'s result (its manifests directory)
) -> Dict[str, Any]:  # The tier's manifest
    """Build tier xK: a fresh db holding the snapshot's rows plus copies 1 .. k-1 (plain row
    copies; the snapshot's own indexes created after the rows, then ANALYZE), the journal
    families as hard links to every copy's segments with the snapshot's live tail as the
    tier's own live tail, the db's APPLIED CURSOR at each family's end (`_stamp_cursors`: a write
    on the tier plans against a db holding its whole journal, e4b2dfbb), the tier's config, and its
    substrate data root (`tier_env`: every client's CJM_DATA_DIR, 08441cae) -- every path inside
    the bench."""
    t0 = time.perf_counter()
    phases: Dict[str, float] = {}
    base = root / "base"
    man = _read_json(base / MANIFEST) or {}
    tier = inside(root / f"x{k}", root.parent)
    if tier.exists():
        shutil.rmtree(tier)
    for d in ("journals", "repos", "memory", "substrate"):
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
    t = time.perf_counter()
    cursors = _stamp_cursors(db, families)  # the db holds every family whole: each family's end
    phases["cursors"] = time.perf_counter() - t
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
                "db_bytes": db.stat().st_size, "journals": families, "cursors": cursors, "phases": phases,
                "substrate_dir": str(tier / "substrate"),
                "wall_s": time.perf_counter() - t0, "placeholders": man.get("placeholders") or {}}
    _write_json(tier / MANIFEST, manifest)
    return manifest


def build(
    lane: Dict[str, Any],          # `resolve_lane`'s result
    settings: Dict[str, Any],      # `bench_config`'s result
    tiers: Optional[List[int]] = None,  # Tiers to build (default: the configured list + x1)
    resnapshot: bool = False,      # Take a fresh snapshot (every copy and tier is rebuilt)
    ledger: Optional[str] = None,  # The timings ledger: one `bench-build` event per stage
    fresh: bool = False,           # Reassemble the tiers asked for even when current (drop what runs wrote)
) -> Dict[str, Any]:  # {lane, base, copies, tiers}
    """Snapshot (once), stage the copies the tiers need (missing or from another snapshot, in
    parallel), then assemble each tier not already built from this snapshot -- or, `fresh`,
    every tier asked for: a tier accumulates what measured writes and loads land in it, and a
    fresh one holds the snapshot's size again."""
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
        # a tier assembled before its substrate data root (08441cae) is reassembled, never reused
        if tm and tm.get("base_id") == base["base_id"] and tm.get("substrate_dir") \
                and not (resnapshot or fresh):
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


def _in_env(
    python: str,  # An env's interpreter
    script: str,  # Python source, run isolated
    cwd: Path,    # The neutral directory it runs from (inside the bench)
    *args: str,   # Its argv
) -> Any:  # The JSON the script printed
    """A probe run inside an env -- isolated (-I: no user site, no PYTHON* variables, no cwd on
    sys.path) from a directory inside the bench, so nothing outside the env answers for it."""
    res = subprocess.run([python, "-I", "-c", script, *args], cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        raise BenchRefusal(f"a probe in {python} failed: {res.stderr.strip()[-400:]}")
    return json.loads(res.stdout)


def env_editables(
    python: str,  # An env's interpreter
    cwd: Path,    # A neutral directory inside the bench
) -> Dict[str, str]:  # {dist: its checkout} for every editable install in the env
    """The env's EDITABLE dists, each with the checkout it imports from (its direct_url.json) --
    the live code a pin replaces (ruling dba3f272)."""
    return _in_env(python, (
        "import importlib.metadata as md, json\n"
        "out = {}\n"
        "for d in md.distributions():\n"
        "    t = d.read_text('direct_url.json')\n"
        "    u = json.loads(t) if t else {}\n"
        "    if u.get('dir_info', {}).get('editable') and u.get('url', '').startswith('file://'):\n"
        "        out[d.metadata['Name']] = u['url'][len('file://'):]\n"
        "print(json.dumps(out))\n"), cwd)


def pin_proof(
    python: str,         # A copied env's interpreter
    prefix: str,         # The copy's prefix
    pinned: List[str],   # The dists installed from the pin's wheels
    cwd: Path,           # A neutral directory inside the bench
) -> List[str]:  # What leaks ([] = sealed)
    """THE PIN'S PROOF, run inside the copy (ruling dba3f272): every sys.path entry inside the copy
    (a .pth naming a path outside would import from it), no editable dist left, and every .py file
    of every pinned dist resolving inside the copy."""
    return _in_env(python, (
        "import importlib.metadata as md, json, os, sys\n"
        "prefix, pinned = os.path.realpath(sys.argv[1]), json.loads(sys.argv[2])\n"
        "def inside(p):\n"
        "    p = os.path.realpath(str(p))\n"
        "    return p == prefix or p.startswith(prefix + os.sep)\n"
        "bad = ['sys.path ' + p for p in sys.path if p and not inside(p)]\n"
        "for d in md.distributions():\n"
        "    t = d.read_text('direct_url.json')\n"
        "    if t and json.loads(t).get('dir_info', {}).get('editable'):\n"
        "        bad.append('editable ' + d.metadata['Name'])\n"
        "for name in pinned:\n"
        "    d = md.distribution(name)\n"
        "    bad += [name + ': ' + str(f) for f in (d.files or [])\n"
        "            if str(f).endswith('.py') and not inside(d.locate_file(f))]\n"
        "print(json.dumps(bad))\n"), cwd, prefix, json.dumps(pinned))


def _git(repo: str, *args: str) -> str:  # One git query's output, stripped (a failure refuses)
    res = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if res.returncode != 0:
        raise BenchRefusal(f"git {' '.join(args)} in {repo} failed: {res.stderr.strip()}")
    return res.stdout.strip()


def _wheel(
    dist: str,     # The dist
    repo: str,     # Its checkout
    commit: str,   # The commit pinned
    python: str,   # An env holding the dist (its own build backend builds the wheel)
    cache: Path,   # <bench_dir>/wheels: one directory per (dist, commit)
    tmp: Path,     # Scratch inside the bench
) -> Path:  # The wheel
    """A dist's wheel at a commit, built once (ruling dba3f272): `git archive` of the commit -- never
    the working tree, so an uncommitted edit is never in it -- built without isolation by the env's
    own backend (offline); the cache keys on (dist, commit), so a re-pin rebuilds only what moved
    (ee35f222: rebuild cost scales with what changed)."""
    import io
    import tarfile
    out = cache / dist / commit
    got = sorted(out.glob("*.whl")) if out.exists() else []
    if got:
        return got[0]
    src = tmp / f"{dist}-{commit}"
    shutil.rmtree(src, ignore_errors=True)
    src.mkdir(parents=True)
    arch = subprocess.run(["git", "-C", repo, "archive", "--format=tar", commit], capture_output=True)
    if arch.returncode != 0:
        raise BenchRefusal(f"git archive {commit} in {repo} failed: {arch.stderr.decode(errors='replace')}")
    with tarfile.open(fileobj=io.BytesIO(arch.stdout)) as tf:
        tf.extractall(src, filter="data")
    building = out.with_name(out.name + ".building")
    shutil.rmtree(building, ignore_errors=True)
    building.mkdir(parents=True)
    res = subprocess.run([python, "-I", "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
                          "--no-cache-dir", "--wheel-dir", str(building), str(src)],
                         cwd=tmp, capture_output=True, text=True, env={**os.environ, "TMPDIR": str(tmp)})
    shutil.rmtree(src, ignore_errors=True)
    if res.returncode != 0 or not list(building.glob("*.whl")):
        raise BenchRefusal(f"building {dist} at {commit[:12]} failed: {(res.stdout + res.stderr)[-600:]}")
    os.replace(building, out)
    return next(out.glob("*.whl"))


def pin_envs(
    lane: Dict[str, Any],  # `resolve_lane`'s result
) -> Dict[str, str]:  # {env name: prefix}: every env the lane's measured process tree runs in
    """The envs a lane's measured invocations run in (ruling dba3f272): the client's (this
    interpreter's -- every CLI invocation is `<python> -m ...cli`), the graph capability's worker
    env (its manifest's install.python_path: the one capability a graph run loads) and each gesture
    driver's env (DATA on its spec). An env is named by its prefix; two prefixes sharing a name
    refuse, never guessed."""
    from .runtime import DEFAULT_GRAPH_ID
    man = _read_json(Path(lane["manifests_dir"]) / f"{DEFAULT_GRAPH_ID}.json") or {}
    py = (man.get("install") or {}).get("python_path")
    if not py:
        raise BenchRefusal(f"no {DEFAULT_GRAPH_ID} manifest naming an install.python_path in "
                           f"{lane['manifests_dir']} -- its worker env cannot be pinned")
    prefixes = [Path(sys.prefix), Path(py).parent.parent]
    prefixes += [Path(env_python(s.get("env"))).parent.parent for s in verb_specs(lane) if s.get("driver")]
    out: Dict[str, str] = {}
    for p in prefixes:
        if out.get(p.name, str(p)) != str(p):
            raise BenchRefusal(f"two envs named {p.name!r}: {out[p.name]} and {p}")
        out[p.name] = str(p)
    return out


def _set_current(
    settings: Dict[str, Any],  # `bench_config`'s result
    lane: Dict[str, Any],      # The lane
    pid: str,                  # The pin it now measures
) -> None:
    """The lane's CURRENT pin -- what `bench run` / `bench load` measure unless told otherwise."""
    d = lane_dir(settings, lane)
    d.mkdir(parents=True, exist_ok=True)
    (inside(d / "pin", settings["bench_dir"])).write_text(pid + "\n")


def _prune_pins(
    settings: Dict[str, Any],  # `bench_config`'s result
) -> List[str]:  # The pins whose env copies were removed
    """The env copies older pins hold, removed: the `pin_keep` newest pins and every lane's current
    pin keep theirs; a pruned pin keeps its manifest (its commit set stays readable) and refuses to
    run until pinned again."""
    bench = Path(settings["bench_dir"])
    root = bench / "pins"
    current = {f.read_text().strip() for f in bench.glob("*/pin") if f.is_file()}
    pins = [p for p in (_read_json(d / "pin.json") for d in sorted(root.glob("*"))
                        if d.is_dir() and not d.name.startswith(".")) if p and not p.get("pruned")]
    pins.sort(key=lambda p: p["made_at"], reverse=True)
    pruned = []
    for p in pins[int(settings.get("pin_keep", BENCH_DEFAULTS["pin_keep"])):]:
        if p["id"] in current:
            continue
        shutil.rmtree(inside(root / p["id"] / "envs", bench), ignore_errors=True)
        _write_json(root / p["id"] / "pin.json", {**p, "pruned": time.time()})
        pruned.append(p["id"])
    return pruned


def load_pin(
    settings: Dict[str, Any],  # `bench_config`'s result
    lane: Dict[str, Any],      # The lane
    ref: str = "current",      # "current" (the lane's) or a pin's id
) -> Dict[str, Any]:  # The pin's manifest
    """The pin a run measures (ruling dba3f272): the lane's current one, or one named by id. No
    current pin, an unknown id or a pruned pin refuses, naming the remedy."""
    if ref == "current":
        f = lane_dir(settings, lane) / "pin"
        if not f.exists():
            raise BenchRefusal(f"lane {lane['name']} has no current pin -- `bench pin` first (or "
                               "measure the editable checkouts with --live)")
        ref = f.read_text().strip()
    p = _read_json(Path(settings["bench_dir"]) / "pins" / ref / "pin.json")
    if not p:
        raise BenchRefusal(f"no pin {ref!r} in {Path(settings['bench_dir']) / 'pins'}")
    if p.get("pruned"):
        raise BenchRefusal(f"pin {ref} was pruned (its env copies removed) -- `bench pin` makes it again")
    return p


def pin_manifests(
    src: Path,                          # The lane's manifests directory
    dst: Path,                          # The pin's
    copies: Dict[str, Dict[str, str]],  # The pin's env copies by name ({python, ...})
) -> Dict[str, List[str]]:  # {kept, left_out}: manifest file names
    """The pin's MANIFESTS (ruling dba3f272): the lane's whole set, since a graph run needs more than
    its capability (the adapter manifests bind its tasks) -- each manifest naming an env the pin
    copied re-pointed at the copy's interpreter, an env-free one (a binding) kept as it is, and one
    naming an env the pin did not copy LEFT OUT, so nothing a pinned run loads can import live code."""
    dst.mkdir(parents=True, exist_ok=True)
    out: Dict[str, List[str]] = {"kept": [], "left_out": []}
    for f in sorted(src.glob("*.json")):
        man = _read_json(f) or {}
        py = (man.get("install") or {}).get("python_path")
        if py:
            name = Path(py).parent.parent.name
            if name not in copies:
                out["left_out"].append(f.name)
                continue
            man["install"]["python_path"] = copies[name]["python"]
        _write_json(dst / f.name, man)
        out["kept"].append(f.name)
    return out


def pin(
    lane: Dict[str, Any],          # `resolve_lane`'s result
    settings: Dict[str, Any],      # `bench_config`'s result
    ledger: Optional[str] = None,  # The timings ledger: one `bench-pin` event carrying the commit set
) -> Dict[str, Any]:  # The pin's manifest
    """THE CODE A RUN MEASURES, PINNED (ruling dba3f272, finding 2682d8cc): every env the lane's
    process tree runs in (`pin_envs`) COPIED inside the bench, its editable dists replaced by
    NON-EDITABLE installs of wheels built from their checkouts' HEAD commits (`_wheel`); the lane's
    manifests carried over, each env-bound one re-pointed at its copy (`pin_manifests`); then THE
    PROOF in each copy
    (`pin_proof`) -- a leak writes no pin. The id derives from the env set and the commit set, so
    the same code pins once; a checkout with uncommitted edits is named `dirty` (the edit is never
    measured). The lane's current pin becomes it; one `bench-pin` event carries the commit set
    into the ledger, every later event its id; then older pins' copies are pruned (`_prune_pins`).

    A COPY, NOT A CLONE: a conda env's interpreter resolves its prefix from its own location, so a
    copied prefix runs as itself (the proof checks it); an offline clone needs the source's package
    cache, which a micromamba root may no longer hold, and an online one the network."""
    from .runtime import DEFAULT_GRAPH_ID
    t0 = time.perf_counter()
    bench = Path(settings["bench_dir"])
    root = inside(bench / "pins", bench)
    tmp = inside(root / ".tmp", bench)
    tmp.mkdir(parents=True, exist_ok=True)
    envs = pin_envs(lane)
    dists: Dict[str, Dict[str, Any]] = {}
    for name, prefix in envs.items():
        for dist, repo in env_editables(str(Path(prefix) / "bin" / "python"), tmp).items():
            d = dists.setdefault(dist, {"repo": repo, "envs": []})
            if d["repo"] != repo:
                raise BenchRefusal(f"{dist} is editable from two checkouts: {d['repo']} and {repo}")
            d["envs"].append(name)
    for d in dists.values():
        d["commit"] = _git(d["repo"], "rev-parse", "HEAD")
        d["dirty"] = bool(_git(d["repo"], "status", "--porcelain", "--untracked-files=no"))
    commits = {k: d["commit"] for k, d in sorted(dists.items())}
    # the layout version rides the identity: a pin made by an older layout never answers for this one
    pid = hashlib.sha256(json.dumps({"layout": 2, "envs": envs, "dists": commits},
                                    sort_keys=True).encode()).hexdigest()[:12]
    pdir = inside(root / pid, bench)
    have = _read_json(pdir / "pin.json")
    if have and not have.get("pruned"):
        _set_current(settings, lane, pid)
        return {**have, "reused": True}
    shutil.rmtree(pdir, ignore_errors=True)
    for dist, d in dists.items():
        d["wheel"] = str(_wheel(dist, d["repo"], d["commit"], str(Path(envs[d["envs"][0]]) / "bin" / "python"),
                                inside(bench / "wheels", bench), tmp))
    copies: Dict[str, Dict[str, str]] = {}
    for name, prefix in envs.items():
        dst = pdir / "envs" / name
        shutil.copytree(prefix, dst, symlinks=True)
        py = str(dst / "bin" / "python")
        mine = sorted(k for k, d in dists.items() if name in d["envs"])
        if mine:
            res = subprocess.run([py, "-I", "-m", "pip", "install", "--no-deps", "--force-reinstall", "--no-index",
                                  "--no-cache-dir", "--no-warn-script-location", *(dists[k]["wheel"] for k in mine)],
                                 cwd=tmp, capture_output=True, text=True, env={**os.environ, "TMPDIR": str(tmp)})
            if res.returncode != 0:
                raise BenchRefusal(f"installing the pinned wheels into {name}'s copy failed: {res.stderr[-600:]}")
        leaks = pin_proof(py, str(dst), mine, tmp)
        if leaks:
            raise BenchRefusal(f"the pin's copy of {name} is not sealed: {leaks[:5]}")
        copies[name] = {"source": prefix, "prefix": str(dst), "python": py}
    mdir = pdir / "manifests"
    mans = pin_manifests(Path(lane["manifests_dir"]), mdir, copies)
    if f"{DEFAULT_GRAPH_ID}.json" not in mans["kept"]:
        raise BenchRefusal(f"the pin's manifests lack {DEFAULT_GRAPH_ID}: {mans}")
    out = {"id": pid, "lane": lane["name"], "made_at": time.time(), "client": Path(sys.prefix).name,
           "envs": copies, "dists": dists, "manifests_dir": str(mdir), "manifests": mans,
           "dirty": sorted(k for k, d in dists.items() if d["dirty"]), "wall_s": time.perf_counter() - t0}
    _write_json(pdir / "pin.json", out)
    _set_current(settings, lane, pid)
    _event(ledger, "bench-pin", out["wall_s"], lane, 0,
           {"commits": commits, "dirty": out["dirty"], "envs": sorted(envs)}, attrs={"pin": pid})
    out["pruned"] = _prune_pins(settings)
    return out


def code_for(
    lane: Dict[str, Any],             # The lane
    pin: Optional[Dict[str, Any]],    # The pin measured (`load_pin`), or None
) -> Dict[str, Any]:  # {id, python, manifests_dir, envs, client}
    """What a run launches (ruling dba3f272): a pin's copied client interpreter, its manifests and
    its env copies by name -- or, with no pin, the editable checkouts ('live': this interpreter and
    the lane's manifests), named so a curve never mistakes the one for the other."""
    if not pin:
        return {"id": "live", "python": sys.executable, "manifests_dir": lane["manifests_dir"],
                "envs": None, "client": None}
    return {"id": pin["id"], "python": pin["envs"][pin["client"]]["python"],
            "manifests_dir": pin["manifests_dir"], "client": pin["client"],
            "envs": {n: e["python"] for n, e in pin["envs"].items()}}


def code_python(
    code: Dict[str, Any],  # `code_for`'s result
    env: Optional[str],    # A gesture driver's env (None = the client's)
) -> str:  # The interpreter it runs in
    """A gesture driver's interpreter under `code`: its env's copy in the pin, else (live) the env
    itself. A pin holding no copy of it refuses -- it was pinned before the driver was configured."""
    if code["envs"] is None:
        return env_python(env)
    name = env or code["client"]
    if name not in code["envs"]:
        raise BenchRefusal(f"pin {code['id']} holds no copy of env {name!r} -- `bench pin` again")
    return code["envs"][name]


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


def tier_env(
    tm: Dict[str, Any],  # A tier's manifest
) -> Dict[str, str]:  # The environment every process the harness launches on the tier carries
    """The tier's substrate data root as each client's CJM_DATA_DIR (finding 08441cae): a client's
    graph worker writes its diagnostics, its substrate journal and its stores there, inside the
    tier, never the live data root its cwd's cjm.yaml names -- the every-write-inside-the-bench
    guarantee (3bd8673d (3)) reaching the worker. A tier assembled before its root refuses."""
    root = tm.get("substrate_dir")
    if not root:
        raise BenchRefusal(f"tier x{tm.get('k')} has no substrate data root (assembled before "
                           "08441cae) -- `bench build` reassembles it")
    return {"CJM_DATA_DIR": root}


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


def machine_busy_s() -> Optional[float]:  # The machine's busy CPU seconds since boot, every core (None off Linux)
    """/proc/stat's first line -- user + nice + system + irq + softirq + steal jiffies over the clock
    tick, idle and iowait left out: two readings around a call give the machine's busy CPU seconds
    while it ran, the concurrent-load covariate (finding 2682d8cc, ruling dba3f272)."""
    try:
        f = [int(x) for x in Path("/proc/stat").read_text().split("\n", 1)[0].split()[1:9]]
        user, nice, system, _idle, _iowait, irq, softirq, steal = f
        return (user + nice + system + irq + softirq + steal) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError):
        return None


def measure(
    argv: List[str],         # The full command line
    env: Dict[str, str],     # Its environment
    limit_bytes: int,        # Address-space limit
    timeout_s: float,        # Wall limit
    out_path: Path,          # Where its stdout lands (inside the bench)
    cwd: Optional[Path] = None,  # Where it runs (a tier: no checkout's package dir lands on its sys.path)
) -> Dict[str, Any]:  # {outcome, wall_s, peak_rss_kb, maxrss_kb, out_bytes, read_bytes, write_bytes, concurrent}
    """Run one invocation under the limits: its peak RSS is the process tree's (the graph
    worker included), sampled from /proc while it runs; wait4's own maxrss rides beside it.
    THE CONCURRENT LOAD rides as a covariate (ruling dba3f272): the 1-minute loadavg at its start,
    the machine's busy CPU seconds while it ran, its own tree's CPU seconds, and the others' (the
    difference -- other clients included). `cwd` matters to a pin: `python -m` puts the cwd first on
    sys.path, so a run launched inside a checkout imports that checkout over the pinned install."""
    def limit():
        resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
    try:
        load0: Optional[float] = os.getloadavg()[0]
    except OSError:
        load0 = None
    busy0 = machine_busy_s()
    t0 = time.perf_counter()
    # the answer reaches the harness through a pipe, as a caller's does, and the HARNESS writes
    # the file: the measured process's disk bytes are its own, never its answer's (7345f40d)
    with open(out_path, "wb") as out:
        proc = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                preexec_fn=limit, cwd=cwd)
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
    busy1, own = machine_busy_s(), (usage.ru_utime + usage.ru_stime) if usage else None
    busy = busy1 - busy0 if busy0 is not None and busy1 is not None else None
    concurrent = {"loadavg_1m": load0, "machine_cpu_s": busy, "own_cpu_s": own,
                  "others_cpu_s": max(0.0, busy - own) if busy is not None and own is not None else None}
    return {"outcome": outcome, "wall_s": time.perf_counter() - t0, "concurrent": concurrent,
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
    pin: Optional[Dict[str, Any]] = None,  # The pin measured (`load_pin`); None = the editable checkouts ('live')
) -> Dict[str, Any]:  # {lane, pin, runs: [...], skipped: [...]}
    """Run the canonical verbs against each tier: real CLI invocations with the tier's db,
    journals and config, the lane's ledger, the tier's tags in the environment, under the
    address-space and wall limits; one harness event per invocation beside the CLI's own.

    A spec naming a `driver` runs an app's canonical GESTURES instead (design detail 4389a579):
    the kit's gesture runner in the app's `env`, the driver given the spec's filled `params`
    plus the tier's db, manifests and journals; each gesture is one event from the session's
    own seam (CJM_TIMINGS_PATH names the lane's ledger), the harness's event beside them
    carrying the driver process's wall and peak RSS.

    THE CODE MEASURED (ruling dba3f272): a pin's copied interpreters and manifests (`code_for`),
    every process launched from its tier's directory, every event carrying the pin's id -- or
    'live', the editable checkouts."""
    code = code_for(lane, pin)
    root = lane_dir(settings, lane)
    built = sorted(int(p.name[1:]) for p in root.glob("x*") if p.name[1:].isdigit()
                   and (p / MANIFEST).exists())
    todo = [k for k in built if tiers is None or k in tiers]
    if not todo:
        raise BenchRefusal(f"no built tier to run in {root} -- `bench build` first")
    specs = [s for s in verb_specs(lane) if verbs is None or s["name"] in verbs]
    limit = int(float(settings["memory_limit_gb"]) * 1024 ** 3)
    out: Dict[str, Any] = {"lane": lane["name"], "pin": code["id"], "runs": [], "skipped": []}
    for k in todo:
        tm = _read_json(root / f"x{k}" / MANIFEST)
        tier = inside(root / f"x{k}", root.parent)
        db = tm["db"]
        fams = tm["journals"]
        values: Dict[str, Any] = {**tm.get("placeholders", {}),
                                  **(lane["settings"].get("placeholders") or {}), "db": db}
        # ruling b7b3976f (B): each verb runs COLD at the tier (its first run, after whatever ran
        # before it) and then warm, the repeats at once; perf reads the warm reading
        for spec in specs:
            for cache in ["cold"] + ["warm"] * max(1, repeat):
                # unique per run: a cold and a warm decide in one second must never share a
                # statement, or the warm one measures a dedup no-op
                values["stamp"] = time.strftime("%Y-%m-%dT%H:%M:%S") + f" x{k} {cache} {uuid.uuid4().hex[:8]}"
                if "writes" in fams:
                    values["tail_offset"] = max(0, family_ops(fams["writes"])
                                                - int(settings["replay_tail"]))
                run_id = str(uuid.uuid4())
                env = {**os.environ, **tier_env(tm), "CJM_BENCH_PIN": code["id"], "CJM_RUN_ID": run_id,
                       "CJM_BENCH_TIER": str(k),
                       "CJM_BENCH_LANE": lane["name"], "CJM_ACTOR": "bench:harness",
                       "CJM_BENCH_CACHE": cache, "CJM_T0": repr(time.time())}
                if spec.get("driver"):
                    params = {key: _fill(v, values) if isinstance(v, str) else v
                              for key, v in (spec.get("params") or {}).items()}
                    if any(v is None for v in params.values()):
                        out["skipped"].append({"tier": k, "verb": spec["name"],
                                               "reason": "a placeholder did not resolve"})
                        break  # nor will it on the warm repeat
                    params.update({"db": db, "manifests_dir": code["manifests_dir"],
                                   "journals": {name: fam["live"] for name, fam in fams.items()}})
                    cmd = [code_python(code, spec.get("env")), "-m", "cjm_substrate_qt_kit.gesturebench",
                           spec["driver"], "--params", json.dumps(params)]
                    env["CJM_TIMINGS_PATH"] = ledger
                    what = {"driver": spec["driver"]}
                else:
                    argv = [_fill(a, values) for a in spec["argv"]]
                    if any(a is None for a in argv):
                        out["skipped"].append({"tier": k, "verb": spec["name"],
                                               "reason": "a placeholder did not resolve"})
                        break  # nor will it on the warm repeat
                    cmd = [code["python"], "-m", "cjm_context_graph_projection.cli",
                           "--graph-db-path", db, "--manifests-dir", code["manifests_dir"],
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
                              inside(tier / "runs" / f"{run_id}.out", root.parent), cwd=tier)
                if spec.get("capture") and res["outcome"] == 0:
                    m = UUID_RE.search((tier / "runs" / f"{run_id}.out").read_text(errors="replace"))
                    values[spec["capture"]] = m.group(0) if m else None
                row = {"tier": k, "verb": spec["name"], "run_id": run_id, "cache": cache, **res}
                if res["outcome"] == 0:
                    (tier / "runs" / f"{run_id}.out").unlink()
                else:  # a failed run keeps its output (the traceback is the diagnosis)
                    row["out"] = str(tier / "runs" / f"{run_id}.out")
                out["runs"].append(row)
                _event(ledger, spec["name"], res["wall_s"], lane, k,
                       {"peak_rss_kb": res["peak_rss_kb"], "maxrss_kb": res["maxrss_kb"],
                        "out_bytes": res["out_bytes"], "read_bytes": res["read_bytes"],
                        "write_bytes": res["write_bytes"], "concurrent": res["concurrent"]},
                       attrs={"cache": cache, "pin": code["id"]},
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


def recorded_outcome(
    events: List[Dict[str, Any]],  # The session's live CLI events (the timings ledger's, kind cli, no tier)
    actor: Optional[str],          # The step's recorded actor
    verb: str,                     # The step's verb
    ts: float,                     # The step's recorded ts (its ledger line or journal op, written mid-invocation)
    slack_s: float = 0.25,         # Tolerance on each side of an invocation's span
) -> Optional[Any]:  # The invocation's recorded outcome (0, an exit code, an exception's name), or None
    """A recorded step's OWN OUTCOME, DECLARED DATA (ruling 57f6c2a8 (1), finding 5bd6c25e): the CLI
    event of the same verb and actor whose invocation span holds the step's ts -- a reads-ledger
    line or a journal op is written inside its invocation -- the latest-starting such event when
    several do. An event that names no actor (CJM_ACTOR unset at the time) matches any. None when
    no event holds it: the ledger predates the trace, or the step ran outside the CLI."""
    best = None
    for e in events:
        if e.get("verb") != verb or (e.get("actor") and actor and e["actor"] != actor):
            continue
        start = float(e.get("ts") or 0.0)
        if start - slack_s <= ts <= start + float(e.get("wall_s") or 0.0) + slack_s:
            if best is None or start > float(best.get("ts") or 0.0):
                best = e
    return None if best is None else best.get("outcome")


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
    canonical statement's) so a later step naming it follows the client's own. Each step keeps its
    recorded ts, and the workload its busiest window at the settings' cap (ruling 6f7a4013). Each
    step joined to its live CLI event in the lane's timings ledger carries that invocation's outcome
    as `expected` (`recorded_outcome`, ruling 57f6c2a8 (1)): a step the recording saw fail is
    attributed under load, never counted as the store's. Derived and regenerable; what did not
    invert is counted, never guessed."""
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
    # 57f6c2a8 (1): the session's live invocations, each step's own outcome read off them
    recorded = [e for e in read_journal(str(db.with_name(db.stem + ".timings.jsonl")))
                if e.get("kind") == "cli" and e.get("session") == session
                and not (e.get("attrs") or {}).get("tier")]
    parser = build_parser()
    con = sqlite3.connect(f"file:{root / 'base' / base['db']}?mode=ro", uri=True)
    steps, skipped, dropped, minted = [], {}, {}, set()
    try:
        prev = None
        for kind, verb, request, ts, who in rows:
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
                    "gap_s": round(ts - prev, 3) if prev is not None else 0.0, "ts": ts}
            got = recorded_outcome(recorded, who, verb, ts)
            if got is not None:  # 57f6c2a8 (1): the step's own recorded outcome, declared
                step["expected"] = got
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
           "skipped": skipped, "dropped": dropped, "made_at": time.time(),
           "expected": {"recorded": sum(1 for s in steps if "expected" in s),
                        "failed": sum(1 for s in steps if s.get("expected") not in (None, 0))},
           "window": busiest_window({"steps": steps}, int(ls["steps"]))}  # 6f7a4013: at the settings' cap
    (root / "workloads").mkdir(exist_ok=True)
    _write_json(inside(root / "workloads" / f"{name}.json", root.parent), out)
    return out


def extract_ingest_workload(
    lane: Dict[str, Any],       # `resolve_lane`'s result
    settings: Dict[str, Any],   # `bench_config`'s result
    session: str,               # The recorded session whose transcript pulls to replay
    actor: Optional[str] = None,  # Only this actor's pulls (default: every pull of the session)
    name: Optional[str] = None,   # The workload's name (default: <session>--ingest)
) -> Dict[str, Any]:  # The workload, written to <lane>/workloads/<name>.json
    """A recorded session's transcript INGEST as a workload (design detail 2c4bad75 (3)): its
    pull-transcript ops in ts order, each mapped to the line of the recorded transcript holding
    the op's last message -- a client replays the real pull against its own copy of that
    transcript grown to that line, never a re-applied op. The transcript is the one the pulls
    name (their cc_session_uuid; the most pulled when several), found in the load settings'
    `transcript_dir`; a pulled message the transcript no longer holds is counted, never guessed."""
    root = lane_dir(settings, lane)
    base = _read_json(root / "base" / MANIFEST)
    if not base:
        raise BenchRefusal(f"no snapshot in {root} -- `bench build` first")
    tdir = load_settings(settings).get("transcript_dir")
    if not tdir:
        raise BenchRefusal("an ingest workload reads the recorded transcripts -- set "
                           "`bench.load.transcript_dir` in the graph-sibling config")
    if not lane["journals"].get("writes"):
        raise BenchRefusal(f"lane {lane['name']} has no writes journal to read the pulls from")
    ops = sorted((e for e in read_journal(lane["journals"]["writes"])
                  if e.get("session") == session and e.get("verb") == "pull-transcript"
                  and (actor is None or (e.get("args") or {}).get("actor") == actor)),
                 key=lambda e: e.get("ts") or 0)
    if not ops:
        raise BenchRefusal(f"no pull-transcript op in session {session}"
                           + (f" by {actor}" if actor else ""))
    pulls: Dict[str, int] = {}
    for e in ops:
        cc = str((e.get("args") or {}).get("cc_session_uuid") or "")
        pulls[cc] = pulls.get(cc, 0) + 1
    cc = max(pulls, key=lambda c: pulls[c])
    path = Path(tdir) / f"{cc}.jsonl"
    if not cc or not path.exists():
        raise BenchRefusal(f"the transcript {path} the pulls name is not there")
    line_of: Dict[str, int] = {}
    with open(path, errors="replace") as fh:
        for n, line in enumerate(fh):
            try:
                u = json.loads(line).get("uuid")
            except (ValueError, AttributeError):
                continue
            if u:
                line_of[u] = n
    steps, skipped, prev, upto = [], {}, None, 0
    for e in ops:
        a = e.get("args") or {}
        if str(a.get("cc_session_uuid") or "") != cc:
            skipped["pull-transcript (another transcript)"] = skipped.get("pull-transcript (another transcript)", 0) + 1
            continue
        msgs = a.get("messages") or []
        # a derived message (a thinking summary, a tool parameter: `<record uuid>#...`) sits on
        # its record's line
        lines = [line_of[u] for u in (str(m.get("uuid") or "").split("#")[0] for m in msgs) if u in line_of]
        if len(lines) < len(msgs):
            skipped["messages the transcript no longer holds"] = (
                skipped.get("messages the transcript no longer holds", 0) + len(msgs) - len(lines))
        if not lines:
            continue
        ts = e.get("ts") or 0
        upto = max(upto, max(lines) + 1)
        steps.append({"kind": "write", "verb": "pull-transcript", "upto": upto, "messages": len(msgs),
                      "gap_s": round(ts - prev, 3) if prev is not None else 0.0, "ts": ts})
        prev = ts
    name = name or re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{session}--ingest")
    out = {"name": name, "lane": lane["name"], "base_id": base["base_id"], "kind": "ingest",
           "source": {"session": session, "actor": actor},
           "session_key": str((ops[0].get("args") or {}).get("session_key") or session),
           "transcript": str(path), "steps": steps, "reads": 0, "writes": len(steps),
           "skipped": skipped, "dropped": {}, "made_at": time.time(),
           "window": busiest_window({"steps": steps}, int(load_settings(settings)["steps"]))}  # 6f7a4013
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


def _names_any(
    value: Any,  # A request value (a string, a list, a dict)
    ids: set,    # Ids
) -> bool:  # Whether it names any of them
    if isinstance(value, dict):
        return any(_names_any(v, ids) for v in value.values())
    if isinstance(value, list):
        return any(_names_any(v, ids) for v in value)
    return isinstance(value, str) and any(m.group(0) in ids for m in UUID_RE.finditer(value))


def compose_steps(
    pool: List[Dict[str, Any]],    # The recorded workloads a composed role draws from
    verbs: Optional[List[str]],    # The verbs it draws (None = every verb the pool holds)
    count: int,                    # Steps to draw
    seed: str,                     # The client's seed (its load, role, group and number)
) -> List[Dict[str, Any]]:  # The client's steps: recorded op shapes on a composed schedule
    """A COMPOSED role's steps (ruling 110c662f (6), design detail 2c4bad75 (2)): RECORDED op
    shapes from the pool's workloads -- those of the given verbs, never one naming an id its
    trace minted (only that trace's client could follow it), never an ingest pull (it follows
    its own transcript) -- drawn by a seeded generator, so a client's schedule is reproducible.
    Never a synthetic op; each step is marked composed (its writes take a per-step marker)."""
    import random
    shapes: List[Dict[str, Any]] = []
    for w in pool:
        if w.get("kind") == "ingest":
            continue
        minted = {s["mints"] for s in w["steps"] if s.get("mints")}
        shapes += [{**s, "gap_s": 0.0, "composed": True} for s in w["steps"]
                   if (not verbs or s["verb"] in verbs) and not _names_any(s["request"], minted)]
    if not shapes:
        raise BenchRefusal(f"a composed role draws nothing: no step of {[w['name'] for w in pool]} "
                           f"has the verbs {verbs}")
    rng = random.Random(seed)
    return [dict(rng.choice(shapes)) for _ in range(count)]


def busiest_window(
    w: Dict[str, Any],  # A recorded workload (its steps carry their recorded gaps, and ts when made since 6a2b88c5)
    cap: int,           # Steps per client, at most
) -> Dict[str, Any]:  # {first, last, steps, first_ts, last_ts, measure, peak, peak_writes_per_s, seed}
    """THE SPAN A RECORDED CLIENT REPLAYS (ruling 6f7a4013, design detail 6a2b88c5 (1) / (2)): the
    contiguous cap steps of its trace around the trace's PEAK MINUTE by the floor's own measure --
    its write steps when it holds any, else every step -- the 60 s span starting at a counted step
    that holds the most of them (the earliest on a tie), times read off the recorded gaps. The
    minute's steps sit in the span with the rest of the budget split before and after, clamped to
    the trace; a minute holding more than cap keeps its first cap. The PEAK WRITE RATE (the peak
    write minute's writes over 60 s; zero for a role with none) is what the floor is set from
    (`load_floor`), so the floor and the load name the same span by construction. The SEED is the
    span's prerequisites: the decides before it whose minted ids the span, or a seed decide, names."""
    steps = w.get("steps") or []
    n, t, times = len(steps), 0.0, []
    for s in steps:
        t += float(s.get("gap_s") or 0.0)
        times.append(t)
    writes = [i for i, s in enumerate(steps) if s.get("kind") == "write"]
    counted = writes or list(range(n))
    best, j = (0, 0, -1), 0  # (count, first counted index, last counted index) of the peak minute
    for a in range(len(counted)):
        while times[counted[a]] - times[counted[j]] >= 60.0:
            j += 1
        if a - j + 1 > best[0]:
            best = (a - j + 1, j, a)
    if not counted or cap <= 0:
        lo, hi = 0, min(n, max(cap, 0)) - 1
    else:
        a, b = counted[best[1]], counted[best[2]]
        if b - a + 1 >= cap:
            lo, hi = a, a + cap - 1
        else:
            lo = max(0, a - (cap - (b - a + 1)) // 2)
            hi = min(n - 1, lo + cap - 1)
            lo = max(0, hi - cap + 1)
    span = steps[lo:hi + 1]
    mints = {s["mints"]: i for i, s in enumerate(steps[:lo]) if s.get("mints")}
    need, todo = set(), [s["request"] for s in span if s.get("request")]
    while todo:  # transitively: a seed decide may name an earlier mint
        for m in UUID_RE.findall(json.dumps(todo.pop(), default=str)):
            if m in mints and mints[m] not in need:
                need.add(mints[m])
                todo.append(steps[mints[m]].get("request") or {})
    return {"first": lo, "last": hi, "steps": len(span),
            "first_ts": span[0].get("ts") if span else None, "last_ts": span[-1].get("ts") if span else None,
            "measure": "writes" if writes else "events", "peak": best[0],
            "peak_writes_per_s": (best[0] if writes else 0) / 60.0,
            "seed": sorted(need)}


def windowed(
    c: Dict[str, Any],  # A client: its `steps` become its span, its `seed` the span's prerequisites
    w: Dict[str, Any],  # The recorded workload it replays
    cap: int,           # Steps per client, at most
) -> Dict[str, Any]:  # The client, its span, seed and window set
    """A recorded client's steps as its BUSIEST WINDOW (ruling 6f7a4013, design detail 6a2b88c5
    (2)): the span `busiest_window` picks; the seed its prerequisites -- the decides it names
    minted before the span, or for an ingest client the transcript line the recording had reached
    before the span (its seed pull grows the copy to that line). The window rides on the run's
    event, so every reading names what it replayed."""
    win = busiest_window(w, cap)
    steps = w["steps"]
    c["steps"] = steps[win["first"]:win["last"] + 1]
    c["seed"] = [steps[i] for i in win["seed"]]
    if w.get("kind") == "ingest" and win["first"] > 0:
        c["seed_upto"] = int(steps[win["first"] - 1].get("upto") or 0)
    c["window"] = {"workload": w.get("name"), **{k: v for k, v in win.items() if k != "seed"},
                   "seed": len(win["seed"]) + (1 if c.get("seed_upto") else 0)}
    return c


def load_floor(
    clients: List[Dict[str, Any]],  # A roster's clients (`roster`'s, at scale 1)
    headroom: float = 2.0,          # The rule's multiplier (110c662f (7): the recorded peak x 2)
) -> Dict[str, Any]:  # {writes_per_s, headroom, clients: [{client, role, group, workload, peak_writes_per_s}]}
    """THE WRITE FLOOR'S VALUE, DERIVED (ruling 6f7a4013 (2), design detail 6a2b88c5 (3)): the
    headroom times the sum of the recorded clients' peak write rates, each read by `busiest_window`
    off the span the client replays -- 110c662f (7)'s peak per session x sessions x 2, each group's
    own workloads summed; a composed client contributes nothing (no recorded trace). The unit is
    the reading's: write invocations (one per recorded write step)."""
    rows = [{"client": c["i"], "role": c.get("role"), "group": c.get("group"),
             "workload": c["window"]["workload"], "peak_writes_per_s": c["window"]["peak_writes_per_s"]}
            for c in clients if c.get("window")]
    return {"writes_per_s": headroom * sum(r["peak_writes_per_s"] for r in rows),
            "headroom": headroom, "clients": rows}


def roster(
    plan: Dict[str, Any],      # {key, defs}: the load to run and every load on the graph (perf.load_definitions)
    ls: Dict[str, Any],        # `load_settings`'s result (its `roles`: each role's source)
    root: Path,                # The lane's bench directory (its workloads)
    cap: int,                  # Steps per client, at most
    scale: float = 1.0,        # The role counts' multiplier (7b's sweep)
) -> Dict[str, Any]:  # {clients: [...], composed, loads}
    """A named load's CLIENTS (ruling 110c662f (1), design detail 2c4bad75 (1) / (2)): its role
    counts per group, then every load it runs beside (perf_load_with, depth first, each once),
    the counts multiplied by `scale` (at least one where a role is named). Each role's source is
    the bench config's `load.roles` -- a recorded role's workloads dealt round robin across its
    clients (an ingest workload replays its transcript), each replaying its BUSIEST WINDOW with
    that window's seed (`windowed`, ruling 6f7a4013), a composed role's steps drawn from its
    pool. A role with no source refuses, never guessed. `composed` is derived: any role of the
    roster composed."""
    defs, roles = plan["defs"], ls.get("roles") or {}
    order: List[str] = []

    def walk(key: str) -> None:
        if key in order:
            return
        if key not in defs:
            raise BenchRefusal(f"no perf_load {key!r} on the graph (the loads: {', '.join(sorted(defs)) or 'none'})")
        order.append(key)
        for other in defs[key].get("with") or []:
            walk(other)
    walk(plan["key"])
    cache: Dict[str, Dict[str, Any]] = {}

    def workload(name: str) -> Dict[str, Any]:
        if name not in cache:
            w = _read_json(root / "workloads" / f"{name}.json")
            if not w:
                raise BenchRefusal(f"no workload {name!r} in {root / 'workloads'} -- `bench workload` first")
            cache[name] = w
        return cache[name]
    clients: List[Dict[str, Any]] = []
    composed = False
    for key in order:
        d = defs[key]
        for g in range(int(d.get("groups") or 1)):
            for role, n in d["roles"].items():
                src = roles.get(role)
                if not src:
                    raise BenchRefusal(f"load {key}: role {role!r} has no source -- name its workloads "
                                       "(or its composition) in the bench config's load.roles")
                count = max(1, math.ceil(n * scale)) if n else 0
                for j in range(count):
                    c = {"i": len(clients), "of_load": key, "role": role, "group": g,
                         "interactive": role in (d.get("interactive") or []),
                         "sharing": d.get("sharing") or "shared", "pace": float(d.get("pace") or 0.0)}
                    if src.get("compose"):
                        composed = True
                        spec = src["compose"]
                        c["steps"] = compose_steps([workload(x) for x in spec.get("from") or []],
                                                   spec.get("verbs"), cap, f"{key}:{role}:{g}:{j}")
                        if spec.get("burst"):
                            c["burst"] = (int(spec["burst"]), float(spec.get("idle_s") or 0.0))
                    else:
                        names = src.get("workloads") or []
                        if not names:
                            raise BenchRefusal(f"role {role!r} names no workloads in the bench config's load.roles")
                        w = workload(names[(g * count + j) % len(names)])
                        c["workload"] = w["name"]
                        windowed(c, w, cap)  # 6f7a4013: its busiest window, never its head
                        if w.get("kind") == "ingest":
                            c["ingest"] = {"transcript": w["transcript"], "session_key": w["session_key"]}
                    clients.append(c)
    return {"clients": clients, "composed": composed, "loads": order}


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


def _decide_ts(
    tm: Dict[str, Any],  # The tier's manifest
    load_id: str,        # The load run
) -> Dict[str, float]:  # decision id -> the unix ts its decide op was journaled at
    """When each of the load's marked decides reached the journal (design detail 2c4bad75 (4)):
    the op line's own ts, the start of 7v's reading."""
    from cjm_dev_graph_schema.nodes import DecisionNode
    fam = tm["journals"].get("writes")
    if not fam:
        return {}
    known = {c["name"] for c in fam["cold"]}
    tag = f"[bench load {load_id} "
    out: Dict[str, float] = {}
    for seg in journal_segments(fam["live"]):
        if Path(seg).name in known:
            continue
        for line in read_segment(seg).splitlines():
            if tag not in line:
                continue
            try:
                op = json.loads(line)
            except ValueError:
                continue
            if op.get("verb") == "decide":
                st = str((op.get("args") or {}).get("statement") or "")
                out[DecisionNode(statement=st).id] = float(op.get("ts") or 0)
    return out


class VisibilityProbe:
    """7v's reader (ruling 110c662f (2), design detail 2c4bad75 (4)): ONE read-only connection to
    the tier's db held for the whole run -- a standing reader's shape (an app holds the graph
    open and polls) -- polling every `interval` seconds for the ids clients are about to mint,
    recording when each is first seen. A connection that cannot open yet (a WAL file not there)
    is retried on the next tick; one last poll runs at stop."""

    def __init__(
        self,
        db: str,          # The tier's db
        interval: float,  # Seconds between polls
    ):
        self.db, self.interval = db, interval
        self.pending: Dict[str, bool] = {}
        self.seen: Dict[str, float] = {}  # id -> unix time first seen
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def watch(self, node_id: str) -> None:  # An id a client is about to mint
        with self.lock:
            if node_id not in self.seen:
                self.pending[node_id] = True

    def _poll(self, con: sqlite3.Connection) -> None:
        with self.lock:
            ids = list(self.pending)
        found = []
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            found += [r[0] for r in con.execute(
                f"SELECT id FROM nodes WHERE id IN ({','.join('?' * len(chunk))})", chunk)]
        now = time.time()
        with self.lock:
            for i in found:
                self.seen.setdefault(i, now)
                self.pending.pop(i, None)

    def _run(self) -> None:
        con = None
        while True:
            last = self.done.is_set()
            try:
                if con is None:
                    con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
                self._poll(con)
            except sqlite3.Error:
                if con is not None:
                    con.close()
                con = None
            if last:
                break
            self.done.wait(self.interval)
        if con is not None:
            con.close()

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.done.set()
        self.thread.join()


def _ingest_key(
    key: str,     # The recorded session key (YYYY-MM-DD_HH-MM-SS)
    client: int,  # The ingest client's number
) -> str:  # The client's own session key
    """An ingest client's own spine (design detail 2c4bad75 (3)): the recorded key shifted back by
    the client's number plus one second, so its transcript (booted after the recorded key) still
    matches it and its pulls land on a spine of their own."""
    from datetime import datetime, timedelta
    t = datetime.strptime(key, "%Y-%m-%d_%H-%M-%S") - timedelta(seconds=client + 1)
    return t.strftime("%Y-%m-%d_%H-%M-%S")


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


def _call(
    ctx: Dict[str, Any],       # The run's context (`load`'s: lane, settings, ledger, tm, tier, k, root, limit)
    argv: List[str],           # The verb's argv
    read_only: bool,           # A read (--read-only)
    env: Dict[str, str],       # The client's environment additions
    attrs: Dict[str, Any],     # The call's identifying context (the load, the client, its role)
) -> Dict[str, Any]:  # `measure`'s result
    """One client call: a real CLI invocation against the tier under the harness's limits, its
    own timing event in the ledger and the harness's beside it (design detail 2f5033bf (2))."""
    tm, lane = ctx["tm"], ctx["lane"]
    run_id = str(uuid.uuid4())
    code = ctx["code"]  # ruling dba3f272: the pin measured, or 'live'
    env = {**os.environ, **tier_env(tm), "CJM_BENCH_PIN": code["id"], "CJM_RUN_ID": run_id,
           "CJM_BENCH_TIER": str(ctx["k"]), "CJM_BENCH_LANE": lane["name"], "CJM_T0": repr(time.time()), **env}
    cmd = [code["python"], "-m", "cjm_context_graph_projection.cli", "--graph-db-path", tm["db"],
           "--manifests-dir", code["manifests_dir"], "--timings-path", ctx["ledger"]]
    for fam, flag in (("writes", "--journal-path"), ("source", "--source-journal-path")):
        if fam in tm["journals"]:
            cmd += [flag, tm["journals"][fam]["live"]]
    if read_only:
        cmd.append("--read-only")
    out = inside(ctx["tier"] / "runs" / f"{run_id}.out", ctx["root"].parent)
    res = measure(cmd + argv, env, ctx["limit"], float(ctx["settings"]["timeout_s"]), out, cwd=ctx["tier"])
    if res["outcome"] == 0:
        out.unlink()
    _event(ctx["ledger"], argv[0], res["wall_s"], lane, ctx["k"],
           {"peak_rss_kb": res["peak_rss_kb"], "out_bytes": res["out_bytes"],
            "read_bytes": res["read_bytes"], "write_bytes": res["write_bytes"],
            "concurrent": res["concurrent"]},
           attrs=attrs, run_id=run_id, outcome=res["outcome"], cli_verb=argv[0])
    return res


def step_attrs(
    c: Dict[str, Any],     # The client (its `window`, when it replays a recorded span)
    j: int,                # The step's index within the client's span
    step: Dict[str, Any],  # The step
) -> Dict[str, Any]:  # The call's step identity and declared expectation
    """A load call's STEP IDENTITY and EXPECTED OUTCOME (ruling 57f6c2a8 (1)): the recorded workload
    and the step's index in its trace -- the key the solo pass's reading of the same step is found
    by -- and the step's recorded outcome where its extraction joined one. A composed step carries
    its expectation alone (it holds no trace position)."""
    out: Dict[str, Any] = {}
    win = c.get("window")
    if win and not step.get("composed"):
        out.update({"workload": win["workload"], "step": int(win["first"]) + j})
    if "expected" in step:
        out["expected"] = step["expected"]
    return out


def _drive(
    clients: List[Dict[str, Any]],  # The run's clients: {i, steps, rekey} and, named, {of_load, role, group, interactive, pace, burst, ingest}
    ctx: Dict[str, Any],            # The run's context (`load`'s)
    attrs: Dict[str, Any],          # The run's own identity (its perf_load, scale, solo role, composed label)
) -> Dict[str, Any]:  # The run's summary row
    """ONE load run (design detail 2f5033bf (2), 2c4bad75): every client concurrently, each
    replaying its steps as real CLI invocations -- ids re-keyed into its copy, then any its trace
    minted swapped for its own; free text marked (a composed step per step); an ingest client
    registers its own session, then grows its copy of the recorded transcript to each step's
    line and runs the real pull. A recorded client replays its busiest window (ruling 6f7a4013):
    first its SEED -- the span's prerequisites, an ingest client's spine and its transcript grown
    to the span's start -- each call tagged phase seed and read apart, then every span at once
    behind a barrier (design detail 6a2b88c5 (2)). Throughout: the clients' summed RSS against the machine guard
    (every client killed past it, the abort itself a reading) and the held reader polling for
    each decide about to land (7v). Then the integrity check and the visibility readings; one
    `bench-load` event carries the summary."""
    from .perf import _quantile
    load_id, n = str(uuid.uuid4()), len(clients)
    decided: Dict[int, List[str]] = {}
    rows: List[Dict[str, Any]] = []
    lock = threading.Lock()
    stop, aborted = threading.Event(), [None]
    probe = VisibilityProbe(ctx["tm"]["db"], ctx["probe_s"])
    run_attrs = {"load": load_id, "clients": n, "pin": ctx["code"]["id"], **attrs}

    def record(c: Dict[str, Any], verb: str, res: Dict[str, Any], into: Optional[List] = None) -> None:
        with lock:
            (rows if into is None else into).append({"client": c["i"], "verb": verb, "outcome": res["outcome"],
                                                     "wall_s": res["wall_s"]})

    seeded: List[Dict[str, Any]] = []
    barrier = threading.Barrier(max(1, n))

    def client(c: Dict[str, Any]) -> None:
        i, minted = c["i"], {}
        mine = {**run_attrs, "client": i,
                **{k: c[k] for k in ("of_load", "role", "group", "interactive") if c.get(k) is not None}}
        env = {"CJM_BENCH_LOAD": load_id, "CJM_BENCH_CLIENTS": str(n), "CJM_BENCH_CLIENT": str(i),
               "CJM_ACTOR": f"bench:load:{i}"}
        ing, upto = c.get("ingest"), 0

        def grow(to: int) -> None:  # the client's transcript copy, grown to a recorded line
            nonlocal upto
            with open(dest, "a") as fh:
                fh.writelines(rk(line) for line in src[upto:to])
            upto = max(upto, to)

        def mark(j: int, step: Dict[str, Any]) -> str:
            return f"[bench load {load_id} client {i}" + (f" step {j}]" if step.get("composed") else "]")
        try:  # 6a2b88c5 (2): THE SEED -- the span's prerequisites, before the run's clock
            seed = {**mine, "phase": "seed"}
            if ing:  # 2c4bad75 (3): its own spine, its own re-keyed copy of the recorded transcript
                folder = inside(ctx["tier"] / "runs" / f"ingest-{load_id}-{i}", ctx["root"].parent)
                folder.mkdir(parents=True, exist_ok=True)
                rk = rekeyer(f"ingest:{load_id}:{i}")
                src = Path(ing["transcript"]).read_text(errors="replace").splitlines(keepends=True)
                dest = folder / f"{rk(Path(ing['transcript']).stem)}.jsonl"
                key = _ingest_key(ing["session_key"], i)
                pull = ["pull-transcript", key, "--transcript-dir", str(folder), "--any-boot"]
                record(c, "session", _call(ctx, ["session", key], False, env, seed), seeded)
                if c.get("seed_upto"):
                    grow(int(c["seed_upto"]))
                    record(c, pull[0], _call(ctx, pull, False, env, seed), seeded)
            for step in c.get("seed") or []:
                if stop.is_set():
                    break
                made = _client_argv(ctx["parser"], step, c["rekey"], minted, mark(-1, step))
                if made["argv"] is None:
                    continue
                res = _call(ctx, made["argv"], step["kind"] == "read", env, seed)
                if res["outcome"] == 0 and made["mints"]:
                    with lock:
                        decided.setdefault(i, []).append(made["mints"])
                record(c, made["argv"][0], res, seeded)
        finally:  # every span starts once every seed is done
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
        burst, pace = c.get("burst"), c.get("pace", ctx["pace"])
        for j, step in enumerate(c["steps"]):
            if stop.is_set():
                break
            if burst and j and j % burst[0] == 0:
                time.sleep(burst[1])
            elif pace and step.get("gap_s") and j:
                time.sleep(min(step["gap_s"] * pace, 60.0))
            mints = None
            if ing:
                grow(step["upto"])
                argv, read_only = pull, False
            else:
                made = _client_argv(ctx["parser"], step, c["rekey"], minted, mark(j, step))
                if made["argv"] is None:
                    continue
                argv, read_only, mints = made["argv"], step["kind"] == "read", made["mints"]
                if mints:
                    probe.watch(mints)
            res = _call(ctx, argv, read_only, env, {**mine, **step_attrs(c, j, step)})
            if res["outcome"] == 0 and mints:
                with lock:
                    decided.setdefault(i, []).append(mints)
            record(c, argv[0], res)

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
            if peak[0] > ctx["ceiling"] and not stop.is_set():
                aborted[0] = (f"the clients' summed RSS {peak[0] / 1024 ** 2:.1f} GB passed the "
                              f"{ctx['ceiling'] / 1024 ** 2:.1f} GB ceiling")
                stop.set()
            if stop.is_set():
                _kill_descendants()
            done.wait(0.05)
    from concurrent.futures import ThreadPoolExecutor
    sampler = threading.Thread(target=sample, daemon=True)
    t0 = time.perf_counter()
    sampler.start()
    probe.start()
    with ThreadPoolExecutor(max_workers=max(1, n)) as pool:
        for f in [pool.submit(client, c) for c in clients]:
            f.result()
    done.set()
    sampler.join()
    probe.stop()
    wall = time.perf_counter() - t0
    check = _integrity(ctx["tm"], load_id, decided)
    journaled = _decide_ts(ctx["tm"], load_id)
    lat = sorted(probe.seen[x] - journaled[x] for x in probe.seen if x in journaled)
    landed = [x for ids in decided.values() for x in ids]
    vis = {"n": len(lat), "unseen": sum(1 for x in landed if x not in probe.seen),
           "p50_s": _quantile(lat, 0.5), "p99_s": _quantile(lat, 0.99),
           "min_s": lat[0] if lat else None, "max_s": lat[-1] if lat else None,
           "interval_s": ctx["probe_s"]} if landed else None
    extra = {"peak_rss_kb": peak[0] or None, "integrity": check, "steps": len(rows),
             "workloads": sorted({c["workload"] for c in clients if c.get("workload")}),
             # 6a2b88c5: the seed read apart; each window and the floor they derive name what ran
             "seed": {"calls": len(seeded), "failed": sum(1 for r in seeded if r["outcome"] != 0)},
             "windows": [{"client": c["i"], **c["window"]} for c in clients if c.get("window")],
             "floor": load_floor(clients),
             "roles": sorted({c["role"] for c in clients if c.get("role")}),
             "aborted": aborted[0], "visibility": vis}
    _event(ctx["ledger"], "bench-load", wall, ctx["lane"], ctx["k"], extra, attrs=run_attrs)
    return {"tier": ctx["k"], "clients": n, "load": load_id, "wall_s": wall, "steps": len(rows),
            "failed": sum(1 for r in rows if r["outcome"] != 0), "peak_rss_kb": peak[0] or None,
            "integrity": check, "aborted": aborted[0], "visibility": vis, **attrs}


def load(
    lane: Dict[str, Any],       # `resolve_lane`'s result
    settings: Dict[str, Any],   # `bench_config`'s result
    ledger: str,                # The timings ledger every event lands in
    tiers: Optional[List[int]] = None,    # Tiers (default: a named load's own, else the load settings', those built)
    clients: Optional[List[int]] = None,  # Client counts, unnamed (default: the load settings')
    workloads: Optional[List[str]] = None,  # Workload names, unnamed (default: the settings' set, else all)
    steps: Optional[int] = None,          # Steps per client, at most (default: the settings')
    plan: Optional[Dict[str, Any]] = None,  # {key, defs}: a NAMED load and every load on the graph
    scales: Optional[List[float]] = None,   # A named load's role-count multipliers (default: 1)
    pin: Optional[Dict[str, Any]] = None,   # The pin measured (`load_pin`); None = the editable checkouts ('live')
) -> Dict[str, Any]:  # {lane, pin, loads: [...]}
    """THE CONCURRENT REPLAY MODE (design detail 2f5033bf (2)): at each tier, clients replay
    recorded workloads concurrently as real CLI invocations under the harness's limits -- every
    call a tier-tagged event carrying the run's load id, the client count and the client -- the
    clients' summed RSS sampled throughout, then the integrity check; one `bench-load` event per
    run carries the summary. Writes land in the tier, as the canonical writes do. THE MACHINE
    GUARD: when the clients' summed RSS passes the memory ceiling the run aborts (every client
    killed, no further step) and the event says so -- itself a reading.

    UNNAMED (`clients`): N anonymous clients round robin over the workloads, ids re-keyed into
    copy i mod k (distinct projects; at x1 all share copy 0, the hot spot).

    NAMED (`plan`; ruling 110c662f, design detail 2c4bad75): the clients are the load's ROSTER
    (`roster`: its role counts per group and every load it runs beside, each role fed by the
    bench config's load.roles), ids re-keyed into copy 0 when the client's load is shared, copy i
    mod k when partitioned; at the load's own tier (else `tiers`). A SOLO pass first runs one
    client of each interactive role alone (7i's baseline), then the roster at each scale of
    `scales` (7b's sweep); every run's held reader times each decide's visibility (7v)."""
    from .cli import build_parser
    root = lane_dir(settings, lane)
    ls = load_settings(settings)
    built = sorted(int(p.name[1:]) for p in root.glob("x*") if p.name[1:].isdigit()
                   and (p / MANIFEST).exists())
    cap = int(steps or ls["steps"])
    if plan:
        d = plan["defs"].get(plan["key"])
        if d is None:
            raise BenchRefusal(f"no perf_load {plan['key']!r} on the graph "
                               f"(the loads: {', '.join(sorted(plan['defs'])) or 'none'})")
        if d.get("lane") and d["lane"] != lane["name"]:
            raise BenchRefusal(f"load {plan['key']} runs on lane {d['lane']}, not {lane['name']}")
        want = tiers or ([d["tier"]] if d.get("tier") else [])
        if not want:
            raise BenchRefusal(f"load {plan['key']} names no tier -- assert its perf_load_tier or pass --tier")
        roster(plan, ls, root, cap)  # refuses a role with no source before anything runs
    else:
        names = workloads or ls["workloads"] or sorted(p.stem for p in (root / "workloads").glob("*.json"))
        loaded = [w for w in (_read_json(root / "workloads" / f"{n}.json") for n in names) if w]
        if not loaded:
            raise BenchRefusal(f"no workload in {root / 'workloads'} -- `bench workload --session` first")
        want = tiers or ls["tiers"]
    todo = [k for k in want if k in built]
    if not todo:
        raise BenchRefusal(f"no built tier among {want} in {root}")
    ctx_base = {"lane": lane, "settings": settings, "ledger": ledger, "root": root,
                "parser": build_parser(), "limit": int(float(settings["memory_limit_gb"]) * 1024 ** 3),
                "ceiling": memory_ceiling_kb(ls), "pace": float(ls["pace"]),
                "probe_s": float(ls["probe_interval_s"]),
                "code": code_for(lane, pin)}  # ruling dba3f272: what every client launches
    out: Dict[str, Any] = {"lane": lane["name"], "pin": ctx_base["code"]["id"], "loads": []}
    for k in todo:
        tier = inside(root / f"x{k}", root.parent)
        (tier / "runs").mkdir(exist_ok=True)
        ctx = {**ctx_base, "tm": _read_json(tier / MANIFEST), "tier": tier, "k": k}
        if not plan:
            for n in clients or ls["clients"]:
                cl = [windowed({"i": i, "rekey": rekeyer(i % k)}, loaded[i % len(loaded)], cap)
                      for i in range(n)]  # 6f7a4013: each its workload's busiest window
                out["loads"].append(_drive(cl, ctx, {}))
            continue
        solos, seen = [], set()
        for c in roster(plan, ls, root, cap)["clients"]:
            if c["interactive"] and (c["of_load"], c["role"]) not in seen:
                seen.add((c["of_load"], c["role"]))
                solos.append(c)
        for c in solos:  # 7i's baseline: one client of the role, alone
            out["loads"].append(_drive([{**c, "i": 0, "rekey": rekeyer(0)}], ctx,
                                       {"perf_load": plan["key"], "solo": c["role"]}))
        for scale in scales or [1.0]:
            ros = roster(plan, ls, root, cap, scale)
            for c in ros["clients"]:
                c["rekey"] = rekeyer(c["i"] % k) if c["sharing"] == "partitioned" else rekeyer(0)
            out["loads"].append(_drive(ros["clients"], ctx, {"perf_load": plan["key"], "scale": scale,
                                                             "composed": ros["composed"]}))
    return out


def floor_of(
    lane: Dict[str, Any],       # `resolve_lane`'s result
    settings: Dict[str, Any],   # `bench_config`'s result
    plan: Dict[str, Any],       # {key, defs}: the NAMED load and every load on the graph
    steps: Optional[int] = None,  # Steps per client, at most (default: the settings')
) -> Dict[str, Any]:  # {lane, load, cap, floor, asserted, windows}
    """A named load's write floor as its traces derive it, nothing run (ruling 6f7a4013 (2),
    design detail 6a2b88c5 (3)): its roster at scale 1, each recorded client's busiest window and
    peak write rate, the floor they set, and the floor asserted on the load beside it -- the value
    an assert of perf_load_writes_per_s_min takes, evidenced by the windows' sessions."""
    root = lane_dir(settings, lane)
    ls = load_settings(settings)
    cap = int(steps or ls["steps"])
    d = plan["defs"].get(plan["key"])
    if d is None:
        raise BenchRefusal(f"no perf_load {plan['key']!r} on the graph "
                           f"(the loads: {', '.join(sorted(plan['defs'])) or 'none'})")
    ros = roster(plan, ls, root, cap)
    return {"lane": lane["name"], "load": plan["key"], "cap": cap, "floor": load_floor(ros["clients"]),
            "asserted": d.get("writes_min"), "composed": ros["composed"],
            "windows": [{"client": c["i"], "role": c.get("role"), **c["window"]}
                        for c in ros["clients"] if c.get("window")]}


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
    out = [f"## Bench {action} — lane {res['lane']}" + (f" · pin {res['pin']}" if res.get("pin") else ""), ""]
    if action == "pin":  # ruling dba3f272: the code every later run measures
        if res.get("reused"):
            out.append(f"- the code is already pinned as `{res['id']}` -- now the lane's current pin")
        else:
            out.append(f"- pinned `{res['id']}` in {res['wall_s']:.1f} s: {len(res['dists'])} dist(s) from their "
                       f"HEAD commits, non-editable in copies of {', '.join(sorted(res['envs']))} -- sealed")
        out += [f"- {k} {d['commit'][:12]}" + (" (DIRTY: the uncommitted edit is not measured)" if d["dirty"] else "")
                for k, d in sorted(res["dists"].items())]
        out += [f"- pruned pin {p} (its env copies)" for p in res.get("pruned") or []]
        return "\n".join(out)
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
        if res.get("kind") == "ingest":  # 2c4bad75 (3)
            out.append(f"- the pulls replay the transcript {res['transcript']} onto copies of spine {res['session_key']}")
        out.append(f"- workload `{res['name']}` from session {src['session']}"
                   + (f" (actor {src['actor']})" if src.get("actor") else "")
                   + f": {res['reads']} read(s) · {res['writes']} write(s)")
        out += [f"- skipped {k}: {n}" for k, n in sorted(res["skipped"].items())]
        out += [f"- dropped {k}: {n}" for k, n in sorted(res["dropped"].items())]
        return "\n".join(out)
    if action == "floor":  # ruling 6f7a4013 (2), design detail 6a2b88c5 (3)
        fl, asserted = res["floor"], res.get("asserted")
        out += [f"- load {res['load']}: the traces derive a floor of {fl['writes_per_s']:.3f} writes/s "
                f"({fl['headroom']:g} x the recorded clients' peak write rates, {res['cap']} steps per client)"
                + ("" if asserted is None else f"; asserted {asserted:g}"
                   + (" -- STALE" if abs(asserted - fl["writes_per_s"]) > 1e-3 else " -- current")),
                "", "| client | role | workload | span | recorded | measure | peak / min | peak writes/s | seed |",
                "|---:|---|---|---|---|---|---:|---:|---:|"]
        for w in res["windows"]:
            when = "-" if w.get("first_ts") is None else (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(w["first_ts"])) + " -> "
                + time.strftime("%H:%M:%S", time.localtime(w["last_ts"])))
            out.append(f"| {w['client']} | {w.get('role') or '-'} | {w['workload']} | {w['first']}-{w['last']} | "
                       f"{when} | {w['measure']} | {w['peak']} | {w['peak_writes_per_s']:.3f} | {w['seed']} |")
        return "\n".join(out)
    if action == "load":
        out += ["| tier | run | clients | steps | failed | wall s | summed peak RSS | visibility p99 | integrity |",
                "|---:|---|---:|---:|---:|---:|---:|---:|---|"]
        for r in res["loads"]:
            rss = r["peak_rss_kb"] and r["peak_rss_kb"] * 1024
            check = r["integrity"] or {}
            name = "-"  # design detail 2c4bad75: a named load's run, its solo pass, its scale
            if r.get("perf_load"):
                name = r["perf_load"] + (f" solo {r['solo']}" if r.get("solo") else
                                         f" ×{r.get('scale', 1.0):g}" + (" (composed)" if r.get("composed") else ""))
            vis = (r.get("visibility") or {}).get("p99_s")
            out.append(f"| x{r['tier']} | {name} | {r['clients']} | {r['steps']} | {r['failed']} | "
                       f"{r['wall_s']:.1f} | {_gb(rss)} | {'-' if vis is None else f'{vis:.3f} s'} | "
                       f"{'ok' if check.get('ok') else check} |")
            if r.get("aborted"):
                out.append(f"- **ABORTED**: {r['aborted']}")
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
