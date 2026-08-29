"""Scratchpad session .md exporter — the projection lens (increment iv of the
e8b2f397 rung plan; item 5ab24c57).

The .md DEMOTED to an exportable projection at the v2 cutover (DEC 91c47b4a
pt 5): there is no real-time mirror — export is ONE-WAY, an edited export is
a fork, never a sync hazard. One exporter with CONFIG-AS-DATA controls (the
onboarding --write pattern): inclusion (which chains, superseded branches),
presentation (interleaved vs separate lanes, ids, timestamps) — the .md is
one POSSIBLE projection among N. Read-only: an export journals nothing.
Mixing workbench session-feed context alongside the message chains is the
reserved next control (a config key, not a new exporter) — landed when a
projection first needs it.

The chain derivation mirrors the scratchpad app's timeline module (active
path = tip ancestry over NEXT, forks are outbound, composer parts always
live) — kept independently small here so the exporter stays Qt-free."""

from datetime import datetime, tzinfo
from typing import Any, Dict, List, Optional, Sequence, Tuple

from cjm_context_graph_layer.ops import graph_task
from cjm_context_graph_primitives.query import EdgeQuery, PropertyPredicate
from cjm_dev_graph_schema.identity import session_node_id

from .factlayer import load_label_where
from .pull_transcript import MESSAGE_SOURCE_COMPOSER
from .runtime import GraphHandle

DEFAULT_CONFIG: Dict[str, Any] = {
    "transcript": True,      # include the pulled transcript chain
    "composition": True,     # include composer parts
    "superseded": False,     # include off-active-path transcript branches (annotated)
    "lanes": "interleaved",  # "interleaved" | "separate"
    "ids": True,             # short node id on each message header
    "timestamps": True,      # capture clock on each message header
}

_GLYPHS = {"user": "YOU", "assistant": "CLAUDE", "harness": "HARNESS"}


def derive_entries(
    messages: Sequence[Dict[str, Any]],        # Message property dicts (incl. "id")
    next_pairs: Sequence[Tuple[str, str]],     # NEXT edges (source_id, target_id)
) -> List[Dict[str, Any]]:
    """Chronological entries with derived `on_active_path` (transcript tip
    ancestry; composer parts are always live)."""
    transcript = [m for m in messages if m.get("source") != MESSAGE_SOURCE_COMPOSER]
    active: set = set()
    if transcript:
        ids = {m["id"] for m in transcript}
        tip = max(transcript, key=lambda m: str(m.get("timestamp") or ""))["id"]
        pred = {dst: src for src, dst in next_pairs if dst in ids and src in ids}
        node: Optional[str] = tip
        while node is not None and node not in active:
            active.add(node)
            node = pred.get(node)
    out = []
    for m in messages:
        composer = m.get("source") == MESSAGE_SOURCE_COMPOSER
        out.append({**m, "on_active_path": True if composer else (m["id"] in active)})
    out.sort(key=lambda m: str(m.get("timestamp") or ""))
    return out


def _local_stamp(ts: Any, tz: Optional[tzinfo] = None) -> str:
    """A stored UTC-Z stamp rendered in LOCAL time for the reading projection.

    Display-only: sorting and identity stay on the raw ISO string; tz overrides
    the machine zone for tests. Odd shapes pass through verbatim."""
    try:
        local = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(tz)
    except ValueError:
        return str(ts)
    return local.strftime("%Y-%m-%d %H:%M:%S")


def _header_line(m: Dict[str, Any], sent_ids: set, config: Dict[str, Any]) -> str:
    composer = m.get("source") == MESSAGE_SOURCE_COMPOSER
    glyph = "PART" if composer else _GLYPHS.get(str(m.get("role")), str(m.get("role")).upper())
    bits = [f"**{glyph}**"]
    if config.get("timestamps") and m.get("timestamp"):
        bits.append(_local_stamp(m["timestamp"]))
    if config.get("ids"):
        bits.append(f"`{str(m['id'])[:8]}`")
    if composer and m["id"] in sent_ids:
        bits.append("sent ✓")
    if not m.get("on_active_path", True):
        bits.append("_(superseded)_")
    return " · ".join(bits)


def render_session_markdown(
    session_key: str,
    entries: Sequence[Dict[str, Any]],          # derive_entries output
    derived_pairs: Sequence[Tuple[str, str]],   # DERIVED_FROM edges (sent_id, part_id)
    config: Optional[Dict[str, Any]] = None,
    title: str = "",
    exported_at: Optional[str] = None,
) -> str:
    """The pure renderer: one portable markdown document from derived entries.

    Bodies embed VERBATIM (they are markdown source already); message headers
    are bold lines, never headings, so body headings keep their levels."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    sent_ids = {part for _sent, part in derived_pairs}

    def keep(m: Dict[str, Any]) -> bool:
        composer = m.get("source") == MESSAGE_SOURCE_COMPOSER
        if composer:
            return bool(cfg.get("composition"))
        if not cfg.get("transcript"):
            return False
        return bool(m.get("on_active_path", True) or cfg.get("superseded"))

    kept = [m for m in entries if keep(m)]
    transcript_n = sum(1 for m in kept if m.get("source") != MESSAGE_SOURCE_COMPOSER)
    parts_n = len(kept) - transcript_n
    stamp = exported_at or datetime.now().astimezone().isoformat(timespec="seconds")
    head = f"# Session scratchpad — {session_key}" + (f" — {title}" if title else "")
    lines = [head, "",
             f"_exported {stamp} · {transcript_n} transcript message(s) · "
             f"{parts_n} composition part(s) · one projection among N — "
             f"an edited export is a fork, never a sync_", ""]

    def emit(block: Sequence[Dict[str, Any]]) -> None:
        for m in block:
            lines.append(_header_line(m, sent_ids, cfg))
            lines.append("")
            lines.append(str(m.get("text") or "").rstrip())
            lines.append("")
            lines.append("---")
            lines.append("")

    if cfg.get("lanes") == "separate":
        transcript = [m for m in kept if m.get("source") != MESSAGE_SOURCE_COMPOSER]
        parts = [m for m in kept if m.get("source") == MESSAGE_SOURCE_COMPOSER]
        if transcript:
            lines += ["## Transcript", ""]
            emit(transcript)
        if parts:
            lines += ["## Composition", ""]
            emit(parts)
    else:
        emit(kept)
    while lines and lines[-1] in ("", "---"):
        lines.pop()
    return "\n".join(lines) + "\n"


async def export_session_markdown(
    gx: GraphHandle,
    session_key: str,                       # The session spine key to project
    config: Optional[Dict[str, Any]] = None,  # Overrides on DEFAULT_CONFIG
) -> Dict[str, Any]:  # {text, session_key, messages, parts} or {error}
    """Gather the session's message graph and render the .md projection."""
    loaded = await _load_session_messages(gx, session_key)
    if loaded.get("error"):
        return loaded
    messages = loaded["messages"]
    entries = derive_entries(messages, loaded["next_pairs"])
    text = render_session_markdown(session_key, entries, loaded["derived_pairs"],
                                   config=config, title=loaded["title"])
    transcript_n = sum(1 for m in messages if m.get("source") != MESSAGE_SOURCE_COMPOSER)
    return {"text": text, "session_key": session_key, "title": loaded["title"],
            "messages": transcript_n, "parts": len(messages) - transcript_n}


async def _load_session_messages(
    gx: GraphHandle,
    session_key: str,  # The session spine key
) -> Dict[str, Any]:  # {messages, next_pairs, derived_pairs, title} or {error}
    """Gather a session's Message property dicts + NEXT / DERIVED_FROM pairs + the
    spine's display title — the shared load behind the exporter and `read --session`."""
    nodes = await load_label_where(
        gx, "Message", [PropertyPredicate("session_key", "eq", session_key)],
        limit=100000)
    messages: List[Dict[str, Any]] = []
    for n in nodes:
        props = dict((n.get("properties") if isinstance(n, dict)
                      else getattr(n, "properties", None)) or {})
        props["id"] = n.get("id") if isinstance(n, dict) else getattr(n, "id", None)
        messages.append(props)
    if not messages:
        return {"error": f"session {session_key} has no Message nodes on the spine "
                         f"(nothing pulled or composed yet)", "session_key": session_key}
    ids = [m["id"] for m in messages if m.get("id")]

    async def pairs(relation: str, **endpoint) -> List[Tuple[str, str]]:
        q = EdgeQuery(relation_type=relation, project=["id"], **endpoint)
        res = await graph_task(gx.queue, gx.graph_id, "query_edges", query=q.to_dict())
        return [(r["source_id"], r["target_id"]) for r in (res.rows or [])]

    next_pairs = await pairs("NEXT", source_ids=ids)
    derived_pairs = await pairs("DERIVED_FROM", target_ids=ids)
    sess = await graph_task(gx.queue, gx.graph_id, "get_node",
                            node_id=session_node_id(session_key))
    title = ""
    if sess is not None:
        sprops = (sess.get("properties") if isinstance(sess, dict)
                  else getattr(sess, "properties", None)) or {}
        title = str(sprops.get("display_title") or "")
    return {"messages": messages, "next_pairs": next_pairs, "derived_pairs": derived_pairs,
            "title": title}


async def read_session_messages(
    gx: GraphHandle,
    session_key: str,                 # The session spine key
    role: Optional[str] = None,       # Keep only this role (user | assistant | harness); None = every role
    include_superseded: bool = False, # Include off-active-path transcript branches (annotated)
    include_parts: bool = False,      # Include composer parts (drafts) beside sent transcript messages
) -> Dict[str, Any]:  # {kind: "messages", session_key, role, count, items:[{id, role, timestamp, source, on_active_path, text}]} or {error}
    """A session spine's Message BODIES in chain order — the `read --session` verb.

    The batch-read precondition for transcript mining at scale (finding 1d8d4486: a
    survey read 1,573 bodies at one subprocess each; `journal-window` never saw a
    Message). Plain bodies, no markdown dressing — the exporter lens is the presentation
    projection; this is the delivery one. Default = the ACTIVE transcript path only (a
    superseded branch is a rewind the reader did not take), sent messages only (a
    composer part is a draft of a message that is already on the spine)."""
    loaded = await _load_session_messages(gx, session_key)
    if loaded.get("error"):
        return loaded
    entries = derive_entries(loaded["messages"], loaded["next_pairs"])
    items = []
    for m in entries:
        if m.get("source") == MESSAGE_SOURCE_COMPOSER and not include_parts:
            continue
        if not m.get("on_active_path", True) and not include_superseded:
            continue
        if role and str(m.get("role") or "") != role:
            continue
        items.append({"id": m.get("id"), "role": m.get("role"), "timestamp": m.get("timestamp"),
                      "source": m.get("source"), "on_active_path": m.get("on_active_path", True),
                      "text": str(m.get("text") or "")})
    return {"kind": "messages", "session_key": session_key, "title": loaded["title"],
            "role": role, "count": len(items), "items": items}
