"""Graph-sibling config discovery — the DEFAULT_* hardcodes retired to DATA
(work item a1d965b0; the onboarding.config.json precedent: config = DATA,
never library code).

The config file lives BESIDE the graph db (`<db dir>/graph.config.json`), so
the explicit --graph-db-path doctrine also names the config: whichever graph
you address, its own inventory answers. Keys (all optional): `code_libs`,
`notebook_libs`, `memory_dir`, `repos_dir`, `manifests_dir` — unknown keys
are ignored (forward compat). An ABSENT file falls back to the in-code
DEFAULT_* scaffolding (the 6dfe00e9 class — dev-machine defaults, not
endpoints); a CORRUPT file refuses LOUDLY, because a typo'd inventory
silently falling back would drop repos from ingest (the a7bc1424 class:
rebuilds silently drop repos outside the inventory)."""

import json
from pathlib import Path
from typing import Any, Dict

CONFIG_BASENAME = "graph.config.json"


def load_graph_config(
    graph_db_path,  # The addressed graph db (str or Path); config sits beside it
) -> Dict[str, Any]:  # The config object, or {} when no file exists
    """Read the graph-sibling config. Absent = {} (fallback to DEFAULT_*);
    corrupt or non-object = loud refusal, never a silent fallback."""
    path = Path(graph_db_path).resolve().parent / CONFIG_BASENAME
    if not path.exists():
        return {}
    try:
        cfg = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise SystemExit(
            f"graph config unreadable at {path}: {e} — fix or remove it "
            "(a corrupt inventory must never silently fall back)")
    if not isinstance(cfg, dict):
        raise SystemExit(f"graph config at {path} must be a JSON object")
    return cfg
