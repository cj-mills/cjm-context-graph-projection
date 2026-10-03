"""The site's theme from its bound design system (leg C of design 0858bbd0; amendment 4b58c9db):
the profile's STYLED_BY Reference reaches the dev graph's captured system; the build renders the
LATEST capture and reports a stale observation rather than refusing; the fonts come through the
node's own locator, one seam, each hash reported; a config naming another theme keeps it."""

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from cjm_design_system import systems
from cjm_dev_graph_schema.identity import entity_node_id
from cjm_dev_graph_schema.vocab import DevRelations

from cjm_context_graph_projection.artifacts import capture_artifact
from cjm_context_graph_projection.config import CONFIG_BASENAME
from cjm_context_graph_projection.coverage import mint_entity
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.sitetheme import (THEME_FILES, mode_pair, profile_key, project_theme,
                                                    theme_wanted)
from cjm_context_graph_projection.write import link

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()
needs_graph = pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")


def test_the_profile_key_is_the_site_repo_key_and_the_profile():
    assert profile_key("/x/y/christianjmills", "staging") == "christianjmills/staging"


def test_the_step_runs_only_for_a_config_naming_the_derived_theme():
    assert theme_wanted(dict(THEME_FILES))
    assert not theme_wanted({"light": "custom-flatly.scss", "dark": "custom-darkly.scss"})
    assert not theme_wanted("cosmo") and not theme_wanted(None)


def test_the_mode_pair_is_the_facts_else_the_scheme_map():
    classical = json.loads(systems.tokens_path("classical").read_text())
    netrunner = json.loads(systems.tokens_path("netrunner").read_text())
    assert mode_pair(classical, {}) == {"light": "light", "dark": "dark"}
    assert mode_pair(classical, {"dark": "light"}) == {"light": "light", "dark": "light"}
    if not netrunner.get("scheme"):
        assert "design_light_mode" in mode_pair(netrunner, {})["error"]
    assert mode_pair(netrunner, {"light": "white", "dark": "red"}) == {"light": "white", "dark": "red"}
    assert "not a mode" in mode_pair(classical, {"light": "red"})["error"]


@needs_graph
def test_the_theme_renders_the_latest_capture_and_reports_a_stale_observation(tmp_path):
    pytest.importorskip("fontTools")
    repos = tmp_path / "repos"
    sysdir = repos / "cjm-x" / "cjm_x" / "systems" / "classical"
    (sysdir / "fonts").mkdir(parents=True)
    shutil.copy(systems.tokens_path("classical"), sysdir / "tokens.json")
    shutil.copy(systems.locate("classical") / "fonts" / "Lora[wght].ttf", sysdir / "fonts")   # one family only
    rel = "cjm_x/systems/classical/tokens.json"
    dev_db, notes_db = tmp_path / "dev" / "dev.db", tmp_path / "notes" / "notes.db"
    dev_db.parent.mkdir()
    notes_db.parent.mkdir()
    (dev_db.parent / CONFIG_BASENAME).write_text(json.dumps({"repos_dir": str(repos)}))
    siblings = {"dev": str(dev_db)}
    site = tmp_path / "mysite"
    site.mkdir()
    sj = str(tmp_path / "source.jsonl")

    async def go():
        async with open_graph(str(dev_db)) as dev:
            cap = await capture_artifact(dev, "cjm-x", rel, source_journal_path=sj, repos_dir=str(repos))
            assert cap["captured"]
        sys_id = entity_node_id("design_system", "classical")
        async with open_graph(str(notes_db)) as gx:
            prof = await mint_entity(gx, "site_profile", "mysite/public", name="mysite public")
            assert "error" not in prof
            # No binding yet: the step refuses with the recipe
            none = await project_theme(gx, str(site), "staging", dict(THEME_FILES), siblings, DEFAULT_MANIFESTS)
            assert none["errors"] and "site_profile" in none["errors"][0]["why"]
            ln = await link(gx, prof["entity_id"], f"dev:{sys_id}", DevRelations.STYLED_BY, siblings=siblings)
            assert ln.get("written"), ln

            first = await project_theme(gx, str(site), "public", dict(THEME_FILES), siblings, DEFAULT_MANIFESTS)
            assert first["errors"] == [] and first["used"] and not first.get("stale")
            assert first["system"]["slug"] == "classical" and first["modes"] == {"light": "light", "dark": "dark"}
            assert sorted(first["written"]) == sorted(THEME_FILES.values())
            fonts = first["fonts"]
            assert [s["file"] for s in fonts["sources"]] == ["Lora[wght].ttf"] and fonts["converted"] > 0
            assert fonts["unfound"] == ["Cormorant Garamond"]   # the family with no file, reported
            light = (site / THEME_FILES["light"]).read_text()
            assert 'url("fonts/classical/lora-normal-latin.woff2")' in light and "googleapis" not in light
            assert (site / "fonts" / "classical" / "lora-normal-latin.woff2").is_file()

            again = await project_theme(gx, str(site), "public", dict(THEME_FILES), siblings, DEFAULT_MANIFESTS)
            assert again["written"] == [] and again["fonts"]["converted"] == 0

            # A re-capture after the link: rendered from the LATEST capture, the observation reported stale
            tok = json.loads((sysdir / "tokens.json").read_text())
            tok["reading"]["measure"] = 72
            (sysdir / "tokens.json").write_text(json.dumps(tok))
            async with open_graph(str(dev_db)) as dev:
                assert (await capture_artifact(dev, "cjm-x", rel, source_journal_path=sj,
                                               repos_dir=str(repos)))["captured"]
            moved = await project_theme(gx, str(site), "public", dict(THEME_FILES), siblings, DEFAULT_MANIFESTS)
            assert moved["errors"] == [] and moved["stale"] and f"dev:{sys_id[:8]}" in moved["relink"]
            assert "--measure: 72ch;" in (site / THEME_FILES["dark"]).read_text()

            # A config naming its own theme keeps it: nothing written, nothing refused
            own = await project_theme(gx, str(site), "public", {"light": "a.scss", "dark": "b.scss"}, siblings)
            assert own == {"used": False, "errors": []}

    asyncio.run(go())
