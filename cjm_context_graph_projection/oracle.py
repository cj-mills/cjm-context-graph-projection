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
from pathlib import Path
from typing import Any, Dict, List, Optional

from cjm_context_graph_layer.identity import derive_node_id
from cjm_dev_graph_schema.vocab import DevNodeKinds

from . import factlayer as F
from .runtime import GraphHandle
from .write import assert_value, mint_procedure

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
    writes: Any,                      # The invocation's writes-journal unit of work (writes.InvocationWrites)
    repos_dir: Optional[str] = None,  # Active repos dir (for the on-disk version fallback)
    only: Optional[List[str]] = None,  # Restrict to these repo keys/names (None = every repo entity)
) -> Dict[str, Any]:  # {procedure_id, bumped, first_seen, unchanged, skipped, journaled, counts}
    """Refresh `version` slots for repo entities; report what changed.

    Each repo gets one `version` assertion (actor=the oracle, evidence=the
    Procedure). Semver ordering means a bump auto-supersedes the old value and a
    same-version read is a verified no-op.

    THE CHAIN (ruling 547f4123 (1) + (4)): the Procedure op, then one `assert` op per repo
    whose reading changes the graph, each planned against the db the previous op left, then
    appended and applied through the replay's applier (finding b744b28e's journal-first, now
    by construction). A same-version read plans nothing; a reading OLDER than the active value
    is reported and never written (ruling 547f4123 (3)) -- the rebuild never held it."""
    proc = procedure_node()
    props = proc["properties"]
    await writes.begin()
    journaled = 0
    plan = await mint_procedure(gx, ORACLE_METHOD, props["name"], actor=props["actor"],
                                root_kind=props["root_kind"], write=False)
    proc_id = plan["procedure_id"]
    if plan["op"]:
        await writes.write("procedure", plan["op"])
        journaled += 1

    entities = await F.load_label(gx, DevNodeKinds.ENTITY)
    repos = [e for e in entities if F.prop(e, "entity_kind") == "repo"]
    only_l = {o.lower() for o in only} if only else None

    bumped, first_seen, unchanged, skipped = [], [], [], []
    for e in repos:
        key, name = F.prop(e, "key"), F.prop(e, "name")
        if only_l and key.lower() not in only_l and (name or "").lower() not in only_l:
            continue
        version = read_repo_version(name or key, repos_dir)
        if not version:
            skipped.append({"repo": name or key})
            continue
        res = await assert_value(gx, F.nid(e), "version", version, actor=ORACLE_ACTOR,
                                 method=ORACLE_METHOD, evidence=[proc_id], write=False)
        rec = {"repo": name or key, "version": version, "assertion_id": res["assertion_id"]}
        if res["born_superseded"]:
            skipped.append({**rec, "reason": "older than current active"})
            continue
        if res["op"] is None:
            unchanged.append(rec)
            continue
        await writes.write("assert", res["op"])
        journaled += 1
        if res["superseded"]:
            bumped.append({**rec, "superseded": res["superseded"]})
        else:
            first_seen.append(rec)

    return {"procedure_id": proc_id, "bumped": bumped, "first_seen": first_seen,
            "unchanged": unchanged, "skipped": skipped, "journaled": journaled,
            "counts": {"bumped": len(bumped), "first_seen": len(first_seen),
                       "unchanged": len(unchanged), "skipped": len(skipped),
                       "journaled": journaled}}
