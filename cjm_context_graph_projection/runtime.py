"""Open a context graph for reading/writing (domain-neutral runtime wiring).

Wraps the cjm-substrate runtime recipe — discover the graph-storage capability
from its manifest, load it onto an explicit db path, start a JobQueue — as one
`async with open_graph(...) as gx:` context manager, so every driver (CLI, TUI,
MCP, harness) opens a graph the same way instead of re-deriving the dance.

Depends only on the substrate runtime; carries NO dev/domain dependency.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Optional

from cjm_substrate.core.manager import CapabilityManager
from cjm_substrate.core.queue import JobQueue

DEFAULT_GRAPH_ID = "cjm-capability-graph-sqlite"  # The graph-storage capability instance id
# Where the graph-storage capability + adapter manifests live (override with --manifests-dir).
DEFAULT_MANIFESTS = ("/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills/"
                     "cjm-transcript-correction-core/.cjm/manifests")


@dataclass
class GraphHandle:
    """A live, started graph: the queue + the capability id to address it."""
    queue: JobQueue   # Started job queue (the task channel)
    graph_id: str     # Graph-storage capability instance id


@asynccontextmanager
async def open_graph(
    graph_db_path: str,                       # Explicit sqlite db path (no convenience default-repoint)
    manifests_dir: str = DEFAULT_MANIFESTS,   # Dir holding the graph-storage capability manifest
    graph_id: str = DEFAULT_GRAPH_ID,         # Capability instance id
) -> AsyncIterator[GraphHandle]:  # The live graph handle
    """Load the graph-storage capability on `graph_db_path` and yield a started handle.

    The db path is taken verbatim and explicit on purpose (dev `.cjm/` locations
    are scaffolding, not a final corpus endpoint — keeping it explicit avoids
    false assumptions forward). Cleans up the queue + capability on exit."""
    manager = CapabilityManager(search_paths=[Path(manifests_dir)])
    manager.discover_manifests()
    by_name = {m.name: m for m in manager.discovered}
    if graph_id not in by_name:
        raise RuntimeError(f"graph capability {graph_id!r} not found in {manifests_dir} "
                           f"(discovered: {sorted(by_name)})")
    if not manager.load_capability(by_name[graph_id], config={"db_path": str(graph_db_path)}):
        raise RuntimeError(f"failed to load {graph_id} on {graph_db_path}")
    queue = JobQueue(deps=manager)
    await queue.start()
    try:
        yield GraphHandle(queue=queue, graph_id=graph_id)
    finally:
        await queue.stop()
        manager.unload_capability(graph_id)
