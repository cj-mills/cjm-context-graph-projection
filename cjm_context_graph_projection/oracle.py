"""The version oracle: a programmatic Procedure that keeps `version` slots fresh.

Oracle-backed slots are values that are programmatically retrievable, so they get
a refresh Procedure (actor=programmatic, evidence=the read) instead of a hand
assertion — self-maintaining + mechanically re-verifiable, the dev-graph analogue
of the transcript content-hash `verify()`. Trust gradient programmatic > human >
LLM: the oracle wins its contradiction class by being the ground truth.

It reads each repo entity's version (installed metadata first, else the package
`__version__` on disk), then asserts it through the normal write surface. Because
`version` is semver-ORDERED, a bump auto-supersedes the prior value (never a
contradiction) and an unchanged read is an idempotent no-op.
"""

import importlib.metadata
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from cjm_context_graph_layer.identity import derive_node_id
from cjm_context_graph_layer.ops import extend_graph
from cjm_dev_graph_schema.vocab import DevNodeKinds

from . import factlayer as F
from .runtime import GraphHandle
from .write import assert_value

ORACLE_METHOD = "version-oracle/v1"
ORACLE_ACTOR = "procedure:version-oracle/v1"
_VERSION_RE = re.compile(r"""__version__\s*=\s*['"]([^'"]+)['"]""")


def procedure_node() -> Dict[str, Any]:
    """The oracle's Procedure node (the programmatic value-source for its assertions)."""
    return {
        "id": derive_node_id("procedure", ORACLE_METHOD),
        "label": DevNodeKinds.PROCEDURE,
        "properties": {"name": "version oracle", "method": ORACLE_METHOD,
                       "actor": "programmatic", "root_kind": "asserted"},
        "sources": [],
    }


def read_repo_version(
    repo_name: str,                 # The repo / distribution name (e.g. "cjm-substrate")
    repos_dir: Optional[str] = None,  # Active repos dir (for the on-disk fallback)
) -> Optional[str]:  # The version string, or None when unreadable (degrade, don't guess)
    """Read a repo's version: installed metadata first, else `__version__` on disk."""
    try:
        return importlib.metadata.version(repo_name)
    except importlib.metadata.PackageNotFoundError:
        pass
    if repos_dir:
        init = Path(repos_dir) / repo_name / repo_name.replace("-", "_") / "__init__.py"
        if init.exists():
            m = _VERSION_RE.search(init.read_text())
            if m:
                return m.group(1)
    return None


async def run_version_oracle(
    gx: GraphHandle,
    repos_dir: Optional[str] = None,  # Active repos dir (for the on-disk version fallback)
    only: Optional[List[str]] = None,  # Restrict to these repo keys/names (None = every repo entity)
) -> Dict[str, Any]:  # {procedure_id, bumped, first_seen, unchanged, skipped}
    """Refresh `version` slots for repo entities; report what changed.

    Each repo gets one `version` assertion (actor=the oracle, evidence=the
    Procedure). Semver ordering means a bump auto-supersedes the old value and a
    same-version read is a verified no-op."""
    proc = procedure_node()
    await extend_graph(gx.queue, gx.graph_id, [proc], [])
    proc_id = proc["id"]

    entities = await F.load_label(gx, DevNodeKinds.ENTITY)
    repos = [e for e in entities if F.prop(e, "entity_kind") == "repo"]
    only_l = {o.lower() for o in only} if only else None

    bumped, first_seen, unchanged, skipped = [], [], [], []
    now = time.time()
    for e in repos:
        key, name = F.prop(e, "key"), F.prop(e, "name")
        if only_l and key.lower() not in only_l and (name or "").lower() not in only_l:
            continue
        version = read_repo_version(name or key, repos_dir)
        if not version:
            skipped.append({"repo": name or key})
            continue
        res = await assert_value(gx, F.nid(e), "version", version, actor=ORACLE_ACTOR,
                                 method=ORACLE_METHOD, asserted_at=now, evidence=[proc_id])
        rec = {"repo": name or key, "version": version, "assertion_id": res["assertion_id"]}
        if res["born_superseded"]:
            skipped.append({**rec, "reason": "older than current active"})
        elif res["superseded"]:
            bumped.append({**rec, "superseded": res["superseded"]})
        elif res["nodes_added"]:
            first_seen.append(rec)
        else:
            unchanged.append(rec)

    return {"procedure_id": proc_id, "bumped": bumped, "first_seen": first_seen,
            "unchanged": unchanged, "skipped": skipped,
            "counts": {"bumped": len(bumped), "first_seen": len(first_seen),
                       "unchanged": len(unchanged), "skipped": len(skipped)}}
