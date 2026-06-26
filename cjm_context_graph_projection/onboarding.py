"""Project the MEMORY onboarding surface from the dev graph (the dev driver).

The MEMORY.md-as-projection reframe: the always-loaded artifact stops being an
ENUMERATION index and becomes a radically-minimal resident PUSH core + a landmark
MAP of how to PULL the rest from the graph on demand (`relevant`/`show`). The
domain-neutral assembly lives in `cjm_markdown_decompose_core.project.render_onboarding_surface`;
THIS module holds the substrate-specific seeds (the push allowlist, the landmark
map, the live arc lead) + the substrate "how to query" prose (the `cg-read`
wrapper + the journal guardrails). Seeds are hand-tuned and evolve via the
promotion loop — overridable by a JSON config (the loop edits data, not code).
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cjm_context_graph_layer.ops import graph_task
from cjm_context_graph_primitives.query import NodeQuery
from cjm_markdown_decompose_core.project import note_view_from_graph_node, render_onboarding_surface

# --- Substrate dev seeds (PROVISIONAL — start minimal-and-promote) -----------
# Radically-minimal PUSH core: only what must be resident BEFORE any query. The
# promotion loop adds a slug here when a real session traces a failure to a
# missing resident fact (the dogfood is the classifier).
DEFAULT_PUSH_SLUGS: List[str] = [
    "explicit-graph-db-path",               # always pass --graph-db-path
    "self-hosting-graph-arc-dev-graph-db",  # rm db freely / never delete journal
    "dev-graph-write-journal-durability",   # the journal is source of truth
]
# Landmark coverage map (a thin SWAPPABLE seam): (label, query hint). NOT an
# enumeration — enough to know what KINDS of things are queryable. The
# stages/levels "guided exploration vs full dump" structure is held for the
# user's design + real-use evidence.
DEFAULT_LANDMARKS: List[Tuple[str, str]] = [
    ("Substrate overhaul (Path C / Option C)", "substrate Option C capability adapter stage 9"),
    ("Self-hosting graph arc + the spiral", "self-hosting graph arc projection code-on-graph"),
    ("Substrate design dialect", "design dialect derive from code generalize seam"),
    ("Cascade / release discipline", "cascade pins release publish migration"),
    ("Testing + verification discipline", "stress suite wire-format reconfigure test"),
    ("nbdev workflow (transitional)", "nbdev workflow export test cell split"),
    ("Notes corpus / posts (public graph)", "notes corpus posts federation section topic series"),
    ("Graph visibility / audience model", "graph visibility public private audience projection"),
    ("Memory-as-onboarding (this lead)", "memory files retirement onboarding surface push pull"),
]
DEFAULT_ARC_LEAD = (
    "ACTIVE LEAD: the MEMORY.md-as-ONBOARDING-SURFACE reframe "
    "(see `relevant \"memory onboarding surface push pull\"`). Increment-5 API-drift oracle scoped + parked."
)
# Substrate-specific "how to query" prose (injected into the neutral core).
SUBSTRATE_HOW_TO_QUERY = (
    "## The graphs & how to query\n"
    "- **dev-graph.db** — private planning / decisions / code (this arc).  "
    "**notes-graph.db** — public posts corpus (separate graph).\n"
    "- Query (read-only) via `cg-read`:\n"
    "  `/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills/cjm-substrate/.cjm/bin/cg-read relevant \"<task>\"` "
    "— ranks nearby nodes; `cg-read show <id>` to drill in; `cg-read state` for an overview. "
    "Prepend `--notes` (e.g. `cg-read --notes relevant ...`) to target the public notes graph.\n"
    "- **Guardrails:** `cg-read` is read-only and handles the db path. For planning WRITES use the full "
    "`cjm-context-graph` CLI with an explicit `--graph-db-path` AND `--journal-path .cjm/dev-graph.writes.jsonl`. "
    "The dbs are rebuildable projections — `rm` them freely; **NEVER delete the journals** "
    "(`*.writes.jsonl` / `*.source.jsonl`)."
)


def _load_seeds(
    config_path: Optional[str],  # JSON config overriding the dev seeds (else built-in defaults)
) -> Tuple[List[str], List[Tuple[str, str]], str]:  # (push_slugs, landmarks, arc_lead)
    """Load the onboarding seeds, an optional JSON config overriding the dev defaults.

    The promotion loop edits the JSON (data), not this module (code): keys
    `push_slugs` / `landmarks` (list of [label, hint]) / `arc_lead`, each optional."""
    if config_path and Path(config_path).exists():
        cfg = json.loads(Path(config_path).read_text())
        return (cfg.get("push_slugs", DEFAULT_PUSH_SLUGS),
                [tuple(x) for x in cfg.get("landmarks", DEFAULT_LANDMARKS)],
                cfg.get("arc_lead", DEFAULT_ARC_LEAD))
    return DEFAULT_PUSH_SLUGS, DEFAULT_LANDMARKS, DEFAULT_ARC_LEAD


async def project_onboarding(
    gx: Any,                              # The open graph context (gx.queue / gx.graph_id)
    config_path: Optional[str] = None,   # Optional JSON seed override (promotion-loop data)
) -> Dict[str, Any]:  # {markdown, note_count, missing_push}
    """Project the onboarding surface from the graph's `Note` nodes + the dev seeds.

    Queries every `Note` node (the coverage map), renders the minimal surface, and
    flags any push slug absent on-graph (a stale allowlist entry — a promotion-loop
    signal)."""
    push_slugs, landmarks, arc_lead = _load_seeds(config_path)
    res = await graph_task(gx.queue, gx.graph_id, "query_nodes",
                           query=NodeQuery(label="Note").to_dict())
    notes = [note_view_from_graph_node(n) for n in (res.nodes or [])]
    markdown = render_onboarding_surface(
        notes, push_slugs, landmarks, arc_lead, how_to_query=SUBSTRATE_HOW_TO_QUERY)
    present = {n.slug for n in notes}
    return {
        "markdown": markdown,
        "note_count": len(notes),
        "missing_push": [s for s in push_slugs if s not in present],
    }
