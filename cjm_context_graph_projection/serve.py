"""A served, read-only graph EXPLORER data API over the read verbs — the richer-viz INSTRUMENT.

The pre-P2 enabling instrument of the dogfood arc (DEC `836318fc`, resequenced from P3):
a long-lived local server that opens N graphs ONCE (vs the per-CLI-call capability-load +
queue-start cost) and maps the existing read verbs to JSON endpoints, so a browser client
can do focus+context over live graphs of any size. Three properties are load-bearing:

- **Explorer-agnostic.** This module is the DATA API layer only: graph-session management
  (open/hold/close handles) + verb->endpoint mapping + timing. The client page is passed IN
  as opaque data (`index_html`); nothing here knows what renders the JSON. That boundary is
  what lets this layer later found other graph-based UIs (and be extracted) without rework.
- **Hard READ-ONLY.** Imports only read verbs; exposes no write path. Writes belong to the
  journaled `cg-write` discipline and are a different design problem — keeping them out is
  the guarantee that lets this point at live graphs carelessly.
- **Perf-instrumented, representatively.** Every call goes through the SAME read verbs every
  other consumer uses (bespoke fast-path SQL would profile the viz, not the substrate) and
  returns + logs its `elapsed_ms` — the live probe of the read layer as the dbs grow.

Graph-agnostic like every read verb: takes arbitrary db paths, discovers kinds/relations via
`get_schema`, bakes in no ontology. FastAPI/uvicorn are imported lazily (inside the entry
points) so the CLI keeps working in environments without them.
"""

import sys
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Awaitable, Dict, List, Optional

from .authoring import read_node
from .listing import list_graph
from .projection import get_schema, graph_overview, grep, locate, relevant, show
from .runtime import DEFAULT_MANIFESTS, GraphHandle, open_graph


def graph_names(
    db_paths: List[str],  # Graph db paths, primary first
) -> Dict[str, str]:  # {short-name: path}, insertion-ordered, names deduped
    """Derive a stable short name per db (its file stem; collisions suffixed `-2`, `-3`, …)."""
    names: Dict[str, str] = {}
    for p in db_paths:
        base = Path(p).stem or "graph"
        name, i = base, 2
        while name in names:
            name, i = f"{base}-{i}", i + 1
        names[name] = str(p)
    return names


async def _timed(
    graph: str,                # Graph short-name (for the envelope + log line)
    verb: str,                 # Read-verb name (for the envelope + log line)
    coro: Awaitable[Any],      # The pending verb call
) -> Dict[str, Any]:  # {graph, verb, elapsed_ms, result}
    """Run one verb call inside the timing envelope: measure, log to stderr, wrap.

    The perf-probe seam: elapsed_ms rides every response (the client's readout) AND lands
    on stderr (the recorded signal), so read-layer strain is felt + captured per interaction."""
    t0 = time.perf_counter()
    result = await coro
    ms = round((time.perf_counter() - t0) * 1000.0, 1)
    print(f"[graph-serve] graph={graph} verb={verb} ms={ms}", file=sys.stderr)
    return {"graph": graph, "verb": verb, "elapsed_ms": ms, "result": result}


def build_app(
    handles: Dict[str, GraphHandle],   # Live graph handles by short-name (held for app lifetime)
    paths: Dict[str, str],             # Short-name -> db path (the /api/graphs listing)
    index_html: Optional[str] = None,  # The client page served at `/` (opaque to this layer)
):  # The FastAPI app
    """Build the read-only API app over already-open graph handles.

    Endpoints map 1:1 onto read verbs under `/api/g/{graph}/…`, each wrapped in the timing
    envelope. `overview` composes `get_schema` (the discovered ontology: labels, relations,
    counts) with `graph_overview` hubs over ALL discovered labels — the generic boot view
    (the curated Note-only hub view stays `onboarding`'s business)."""
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import HTMLResponse

    app = FastAPI(title="cjm-context-graph explorer", docs_url=None, redoc_url=None)

    def _gx(name: str) -> GraphHandle:
        if name not in handles:
            raise HTTPException(404, f"unknown graph {name!r} (serving: {sorted(handles)})")
        return handles[name]

    @app.get("/api/graphs")
    async def api_graphs() -> List[Dict[str, str]]:
        return [{"name": n, "path": paths[n]} for n in handles]

    @app.get("/api/g/{name}/overview")
    async def api_overview(name: str) -> Dict[str, Any]:
        gx = _gx(name)

        async def go() -> Dict[str, Any]:
            schema = await get_schema(gx)
            over = await graph_overview(gx, hub_labels=tuple(schema.get("node_labels") or ()))
            return {"schema": schema, "hubs": over["hubs"]}

        return await _timed(name, "overview", go())

    @app.get("/api/g/{name}/show/{node_id}")
    async def api_show(name: str, node_id: str, depth: int = 1) -> Dict[str, Any]:
        return await _timed(name, "show", show(_gx(name), node_id, depth=depth))

    @app.get("/api/g/{name}/read/{node_id}")
    async def api_read(name: str, node_id: str) -> Dict[str, Any]:
        return await _timed(name, "read", read_node(_gx(name), node_id))

    @app.get("/api/g/{name}/relevant")
    async def api_relevant(name: str, task: str, depth: int = 2, k: int = 12) -> Dict[str, Any]:
        return await _timed(name, "relevant", relevant(_gx(name), task, depth=depth, k=k))

    @app.get("/api/g/{name}/locate")
    async def api_locate(name: str, term: str, limit: int = 25) -> Dict[str, Any]:
        return await _timed(name, "locate", locate(_gx(name), term, limit=limit))

    @app.get("/api/g/{name}/grep")
    async def api_grep(name: str, term: str, limit: int = 25) -> Dict[str, Any]:
        return await _timed(name, "grep", grep(_gx(name), term, limit=limit))

    @app.get("/api/g/{name}/list")
    async def api_list(name: str, label: str, limit: int = 100, offset: int = 0,
                       contains: Optional[str] = None) -> Dict[str, Any]:
        return await _timed(name, "list", list_graph(_gx(name), label=label, limit=limit,
                                                     offset=offset, contains=contains))

    if index_html is not None:
        @app.get("/", response_class=HTMLResponse)
        async def index() -> str:
            return index_html

    return app


async def serve_graphs(
    db_paths: List[str],                     # Graph dbs to serve (primary first; each opened read-path-only)
    host: str = "127.0.0.1",                 # Bind address (local instrument: loopback by default)
    port: int = 8766,                        # Bind port
    manifests_dir: str = DEFAULT_MANIFESTS,  # Graph-storage capability manifests (as `open_graph`)
    index_html: Optional[str] = None,        # The client page for `/` (wired by the caller)
) -> None:
    """Open every graph once, hold the handles, and serve the API until interrupted.

    The long-lived dual of the one-shot CLI dispatch: `open_graph` per db on an exit stack
    (so shutdown unwinds queues/capabilities cleanly), then uvicorn on the built app."""
    import uvicorn

    names = graph_names(db_paths)
    async with AsyncExitStack() as stack:
        handles: Dict[str, GraphHandle] = {}
        for name, path in names.items():
            handles[name] = await stack.enter_async_context(open_graph(path, manifests_dir))
        app = build_app(handles, names, index_html)
        print(f"[graph-serve] read-only explorer on http://{host}:{port} "
              f"({len(handles)} graph(s))", file=sys.stderr)
        for n, p in names.items():
            print(f"[graph-serve]   {n} <- {p}", file=sys.stderr)
        await uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning")).serve()
