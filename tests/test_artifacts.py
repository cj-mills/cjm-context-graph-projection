"""Observed-source artifacts (design 9a7224a7): a design system's tokens.json captured as
`artifact` records; the fold derives one design_system Entity per slug, its times from the
records, unmoved by a move between repos; drift and uncaptured files are reported; a
malformed file is refused before anything is journaled; the live step equals the fold."""

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from cjm_design_system import systems
from cjm_dev_graph_schema.identity import entity_node_id

from cjm_context_graph_projection import factlayer as F
from cjm_context_graph_projection.artifacts import (ARTIFACT, ARTIFACT_RETIRE, artifact_check,
                                                    capture_artifact, fold_artifacts,
                                                    latest_artifact_ops, uncaptured_artifacts)
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.source_state import read_source_journal

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()
pytestmark_graph = pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")

CLASSICAL = systems.tokens_path("classical").read_text(encoding="utf-8")


def _canon(text: str) -> str:
    return json.dumps(json.loads(text), indent=2, ensure_ascii=False) + "\n"


def _rec(verb, ts, repo, path, text=None):
    args = {"repo_key": repo, "artifact_path": path}
    if verb == ARTIFACT:
        args.update(artifact_kind="design-system", text=text)
    return {"verb": verb, "ts": ts, "args": args}


def test_the_fold_keys_by_slug_and_a_move_keeps_identity():
    renamed = json.loads(CLASSICAL)
    renamed["name"] = "Classical"
    renamed["fonts"]["body"]["size"] = 16
    records = [
        {"verb": "source", "ts": 0.5, "args": {"repo_key": "r", "module_path": "m.py", "text": "x = 1\n"}},
        _rec(ARTIFACT, 1.0, "kit", "systems/classical/tokens.json", _canon(CLASSICAL)),
        _rec(ARTIFACT, 2.0, "ds", "systems/classical/tokens.json", _canon(CLASSICAL)),
    ]
    fold = fold_artifacts(records)
    assert fold.records == 2                       # the code record is not the artifact lane's
    assert len(fold.conflicts()) == 1              # two live files derive one system
    fold.apply(_rec(ARTIFACT_RETIRE, 3.0, "kit", "systems/classical/tokens.json"))
    fold.apply(_rec(ARTIFACT, 4.0, "ds", "systems/classical/tokens.json", _canon(json.dumps(renamed))))
    nodes, edges = fold.elements()
    assert fold.conflicts() == [] and len(nodes) == 1
    n = nodes[0]
    assert n["id"] == entity_node_id("design_system", "classical")
    assert n["created_at"] == 1.0 and n["updated_at"] == 4.0    # one continuous existence
    p = n["properties"]
    assert p["entity_kind"] == "design_system" and p["repo_key"] == "ds"
    assert p["modes"] == ["light", "dark"] and p["scheme"] == {"light": "light", "dark": "dark"}
    assert json.loads(p["tokens"])["fonts"]["body"]["size"] == 16
    assert edges[0]["relation_type"] == "ABOUT" and edges[0]["created_at"] == 4.0
    assert edges[0]["target_id"] == entity_node_id("repo", "ds")
    fold.apply(_rec(ARTIFACT_RETIRE, 5.0, "ds", "systems/classical/tokens.json"))
    assert fold.elements() == ([], [])             # retired: the identity's existence ends
    fold.apply(_rec(ARTIFACT, 6.0, "ds", "systems/classical/tokens.json", _canon(CLASSICAL)))
    assert fold.elements()[0][0]["created_at"] == 6.0   # a revival starts a new existence


def test_an_unknown_kind_or_unreadable_record_is_reported_never_guessed():
    bad = _rec(ARTIFACT, 1.0, "ds", "a.json", "{}")
    fold = fold_artifacts([{**bad, "args": {**bad["args"], "artifact_kind": "palette"}}, bad])
    assert fold.elements() == ([], []) and len(fold.failures) == 2
    assert "unknown artifact kind" in fold.failures[0]["error"]


@pytestmark_graph
def test_capture_check_uncaptured_retire_and_the_live_step(tmp_path):
    repos = tmp_path / "repos"
    sysdir = repos / "cjm-x" / "cjm_x" / "systems"
    for slug in ("classical", "netrunner"):
        (sysdir / slug).mkdir(parents=True)
        shutil.copy(systems.tokens_path(slug), sysdir / slug / "tokens.json")
    sj = str(tmp_path / "source.jsonl")
    rel = "cjm_x/systems/classical/tokens.json"
    fp = repos / "cjm-x" / rel

    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            kw = dict(source_journal_path=sj, repos_dir=str(repos))
            dry = await capture_artifact(gx, "cjm-x", rel, write=False, **kw)
            assert dry["preview"] == "would capture" and not Path(sj).exists()
            cap = await capture_artifact(gx, "cjm-x", rel, **kw)
            assert cap["captured"] and cap["entity"]["key"] == "classical"
            assert cap["artifacts_live"]["nodes_added"] == 1 and cap["artifacts_live"]["records"] == 1
            nid = entity_node_id("design_system", "classical")
            node = (await F.load_nodes(gx, [nid]))[nid]
            assert F.prop(node, "name") == "Classical" and F.prop(node, "tokens") == _canon(CLASSICAL)
            again = await capture_artifact(gx, "cjm-x", rel, **kw)
            assert again.get("unchanged") and len(read_source_journal(sj)) == 1

            # A formatting-only edit is no new version; a real edit is drift until captured
            fp.write_text(json.dumps(json.loads(CLASSICAL), indent=4), encoding="utf-8")
            assert artifact_check(sj, str(repos))["clean"]
            tok = json.loads(CLASSICAL)
            tok["reading"]["measure"] = 72
            fp.write_text(json.dumps(tok), encoding="utf-8")
            chk = artifact_check(sj, str(repos))
            assert chk["drift"] == [f"cjm-x/{rel}"] and not chk["clean"]
            upd = await capture_artifact(gx, "cjm-x", rel, **kw)
            assert upd["captured"] and upd["artifacts_live"]["nodes_updated"] == 1
            assert artifact_check(sj, str(repos))["clean"]

            # A malformed system is refused before anything is journaled
            del tok["modes"]["dark"]["danger"]
            fp.write_text(json.dumps(tok), encoding="utf-8")
            n_before = len(read_source_journal(sj))
            refused = await capture_artifact(gx, "cjm-x", rel, **kw)
            assert "modes.dark.danger" in refused["error"] and len(read_source_journal(sj)) == n_before
            assert artifact_check(sj, str(repos))["invalid"][0]["artifact"] == f"cjm-x/{rel}"

            # The uncaptured audit walks the repos it is given
            un = uncaptured_artifacts(sj, str(repos), repos=["cjm-x"])
            assert un == {"cjm-x": ["design-system: cjm_x/systems/netrunner/tokens.json"]}

            # The live step equals the fold (the rebuild's lane)
            nodes, _ = fold_artifacts(read_source_journal(sj)).elements()
            held = (await F.load_nodes(gx, [nid]))[nid]
            assert F.prop(held, "tokens") == nodes[0]["properties"]["tokens"]

            ret = await capture_artifact(gx, "cjm-x", rel, retire=True, **kw)
            assert ret["retired"] and ret["artifacts_live"]["nodes_removed"] == 1
            assert latest_artifact_ops(sj) == {} and not await F.load_nodes(gx, [nid])
            gone = await capture_artifact(gx, "cjm-x", rel, retire=True, **kw)
            assert "nothing to retire" in gone["error"]

    asyncio.run(go())


def test_a_site_profile_is_declared_and_a_design_system_is_derived_only():
    from cjm_context_graph_projection.coverage import validate_entity
    assert validate_entity("site_profile", "christianjmills/public", "Public", {}) is None
    assert "`<site>/<profile>`" in validate_entity("site_profile", "christianjmills", "P", {})
    assert "`<site>/<profile>`" in validate_entity("site_profile", "a/b/c", "P", {})
    assert "not declared" in validate_entity("design_system", "classical", "Classical", {})
