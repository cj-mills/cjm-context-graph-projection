"""README-as-projection v1 (structural-only): the API surface, deps, and on-graph purpose."""

import asyncio
import tempfile
from pathlib import Path

from cjm_context_graph_layer.ops import extend_graph
from cjm_dev_graph_schema.identity import entity_node_id

import pytest

from cjm_context_graph_projection.readme import project_readme
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph

# These drive the real graph-storage worker capability via open_graph().
# Skip wherever its manifest isn't discoverable (e.g. CI).
pytestmark = pytest.mark.skipif(
    not (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists(),
    reason=f"graph capability {DEFAULT_GRAPH_ID!r} not installed at {DEFAULT_MANIFESTS}",
)
from cjm_context_graph_projection.write import assert_value
from cjm_python_decompose_core.extract import decompose_paths
from cjm_python_decompose_core.ingest import corpus_graph_elements

LIB = {
    "lib/api.py": ('"""The public API."""\n\n\ndef run(x):\n    """Run the thing."""\n    return _helper(x)\n\n\n'
                   'def _helper(x):\n    """Private helper."""\n    return x\n\n\n'
                   'class Engine:\n    """The engine."""\n    pass\n'),
    "lib/util.py": '"""Utilities."""\n\n\ndef tidy(s):\n    """Tidy a string."""\n    return s.strip()\n',
}
CONSUMER = {"app/main.py": '"""App."""\nfrom lib.api import run\n\n\ndef go():\n    return run(1)\n'}


async def _build(root, db):
    for rel, src in {**{f"lib_repo/{k}": v for k, v in LIB.items()},
                     **{f"app_repo/{k}": v for k, v in CONSUMER.items()}}.items():
        f = Path(root) / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(src)
    # The lib repo's pyproject: "Depends on" derives from THIS file, not from
    # graph IMPORTS edges (soak finding 582c2405) — names normalized to hyphens,
    # version specs / markers / extras stripped.
    (Path(root) / "lib_repo" / "pyproject.toml").write_text(
        "[project]\nname = 'lib'\n"
        "dependencies = ['cjm_substrate>=0.0.51', \"tomli>=2 ; python_version < '3.11'\"]\n")
    # Decompose BOTH repos into ONE corpus so the cross-repo IMPORTS map spans them
    # (exactly what `ingest` does across DEFAULT_CODE_LIBS — separate calls wouldn't resolve
    # `from lib.api import run` to lib's module).
    decs = []
    for repo, sub in (("lib", "lib_repo"), ("app", "app_repo")):
        decs += decompose_paths(repo, [str(p) for p in (Path(root) / sub).rglob("*.py")],
                                str(Path(root) / sub))
    nodes, edges = corpus_graph_elements(decs)
    async with open_graph(db) as gx:
        await extend_graph(gx.queue, gx.graph_id, nodes, edges)


def test_readme_projection_structural():
    async def run():
        with tempfile.TemporaryDirectory() as root:
            db = str(Path(root) / "g.db")
            await _build(root, db)
            async with open_graph(db) as gx:
                res = await project_readme(gx, "lib")
                md = res["markdown"]
                # title + generated marker
                assert md.startswith("# lib\n") and "generated from the context graph" in md
                # public API present, private/nested excluded
                assert "`run` _function_ — Run the thing." in md
                assert "`Engine` _class_ — The engine." in md
                assert "`tidy` _function_ — Tidy a string." in md
                assert "_helper" not in md  # private symbol excluded
                # dependency summary: "Depends on" comes from lib_repo's pyproject
                # (names hyphen-normalized, specs/markers stripped — 582c2405);
                # "Used by" stays graph-derived: app imports lib.
                assert "**Depends on:** `cjm-substrate`, `tomli`" in md
                assert "**Used by:** `app`" in md
                # purpose absent -> placeholder hint
                assert not res["has_purpose"] and "No purpose recorded on-graph" in md
                assert res["symbol_count"] == 3 and res["module_count"] == 2
        return True
    assert asyncio.run(run())


def test_readme_uses_on_graph_purpose():
    async def run():
        with tempfile.TemporaryDirectory() as root:
            db = str(Path(root) / "g.db")
            await _build(root, db)
            async with open_graph(db) as gx:
                rid = entity_node_id("repo", "lib")
                await assert_value(gx, rid, "purpose", "A tiny library that runs things.")
                res = await project_readme(gx, "lib")
                assert res["has_purpose"]
                assert "A tiny library that runs things." in res["markdown"]
                assert "No purpose recorded" not in res["markdown"]
        return True
    assert asyncio.run(run())
