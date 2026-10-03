"""The site's THEME, projected from the design system its profile is bound to (leg C of the
theme half 0858bbd0 under the redesign build 8079ae0f; amendment 4b58c9db).

A site profile is a `site_profile` Entity in the notes graph, keyed `<site>/<profile>` with the
site's repo key (the website checkout's directory name, the convention every repo key follows);
its STYLED_BY edge reaches the dev graph's `design_system` Entity through a cross-graph
Reference (design 9a7224a7). The build:

1. reads the binding -- the profile, its one STYLED_BY Reference, the light / dark pair (the
   profile's design_light_mode / design_dark_mode facts, else the system's scheme map);
2. opens the dev sibling READ-ONLY and renders from the system's LATEST capture (the tokens
   text the Entity carries, never the file); a live hash that differs from the Reference's
   observation is REPORTED with the re-link recipe, never refused (amendment (1));
3. reads the fonts through ONE seam, `system_fonts` -- the files the captured tokens name,
   beside the captured file (the Entity's repo_key + artifact_path under the dev config's
   repos_dir), each with its sha256 for the report (amendment (2)); work item 879816d8 (the
   fonts captured on-graph) changes only this seam;
4. writes `_derived/theme-<light|dark>.scss` (cjm_design_system.web) and the woff2 subsets
   under `fonts/<system>/` -- both git-ignored build outputs. Quarto copies each font a theme
   names beside its compiled stylesheet (`site_libs/bootstrap/fonts/<system>/`), so no
   build-internal path reaches the public site.

The step runs only when the profile's Quarto config names the derived theme files: a site
whose theme is something else keeps it, and the report says so."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from cjm_context_graph_layer.ops import graph_task
from cjm_context_graph_primitives.query import PropertyPredicate
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.nodes import foreign_content_hash
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from . import factlayer as F
from .config import load_graph_config
from .runtime import DEFAULT_MANIFESTS, GraphHandle, open_graph

THEME_FILES = {"light": "_derived/theme-light.scss", "dark": "_derived/theme-dark.scss"}
FONTS_DIR = "fonts"


def profile_key(
    website_root: str,  # The site project root (its directory name is the site's repo key)
    profile: str,       # The Quarto profile
) -> str:  # The site_profile Entity's key, "<site>/<profile>"
    return f"{Path(website_root).resolve().name}{P.PROFILE_KEY_SEP}{profile}"


def theme_wanted(
    theme: Any,  # The profile's `format.html.theme` (from `quarto inspect`)
) -> bool:  # True when the config names the derived theme files this step writes
    return isinstance(theme, dict) and {theme.get("light"), theme.get("dark")} == set(THEME_FILES.values())


async def site_binding(
    gx: GraphHandle,
    key: str,  # The profile's key ("christianjmills/public")
) -> Dict[str, Any]:  # {profile_id, reference: {id, graph, foreign_id, observed_hash, name}, modes: {light?, dark?}} | {error}
    """The profile Entity, its ONE STYLED_BY Reference and its mode facts."""
    found = await F.load_label_where(gx, DevNodeKinds.ENTITY, [
        PropertyPredicate("entity_kind", "eq", P.ENTITY_SITE_PROFILE), PropertyPredicate("key", "eq", key)])
    if len(found) != 1:
        return {"error": f"{len(found)} site_profile Entit(ies) keyed `{key}` -- a site's theme needs exactly one "
                         f"(`cg-write --notes entity site_profile {key} --name <name>`, then "
                         f"`cg-write --notes link <it> STYLED_BY dev:<system id>`)"}
    pid = str(F.nid(found[0]))
    targets = [t for s, t in await F.load_edge_pairs(gx, DevRelations.STYLED_BY) if s == pid]
    if len(targets) != 1:
        return {"error": f"site_profile `{key}` has {len(targets)} STYLED_BY edge(s) -- it needs exactly one"}
    ref = (await F.load_nodes(gx, targets)).get(targets[0])
    if ref is None or F.label(ref) != DevNodeKinds.REFERENCE:
        return {"error": f"site_profile `{key}`'s STYLED_BY target is not a cross-graph Reference"}
    slots = [a for a in await F.load_assertions(gx)
             if F.prop(a, "subject_id") == pid and F.prop(a, "predicate") in (P.DESIGN_LIGHT_MODE, P.DESIGN_DARK_MODE)]
    active = F.active_assertions(slots, await F.load_supersedes(gx)) if slots else []
    modes: Dict[str, List[str]] = {}
    for a in active:
        side = "light" if F.prop(a, "predicate") == P.DESIGN_LIGHT_MODE else "dark"
        modes.setdefault(side, []).append(str(F.prop(a, "value") or ""))
    for side, vals in modes.items():
        if len(vals) != 1:
            return {"error": f"site_profile `{key}` has {len(vals)} active design_{side}_mode values: {sorted(vals)}"}
    return {"profile_id": pid,
            "reference": {"id": str(F.nid(ref)), "graph": str(F.prop(ref, "graph") or ""),
                          "foreign_id": str(F.prop(ref, "foreign_id") or ""),
                          "observed_hash": str(F.prop(ref, "observed_hash") or ""),
                          "name": str(F.prop(ref, "name") or "")},
            "modes": {side: vals[0] for side, vals in modes.items()}}


async def bound_system(
    binding: Dict[str, Any],          # `site_binding` output
    siblings: Dict[str, str],         # {graph key: db path} -- this graph's `sibling_graphs`
    manifests_dir: str = DEFAULT_MANIFESTS,
) -> Dict[str, Any]:  # {id, slug, name, tokens, content_hash, repo_key, artifact_path, repos_dir, stale, live_hash} | {error}
    """The design system the binding names, read from the sibling at its latest capture."""
    from cjm_design_system import tokens as T
    ref = binding["reference"]
    path = siblings.get(ref["graph"])
    if not path:
        return {"error": f"the binding names graph `{ref['graph']}`, which is not in this graph's `sibling_graphs` "
                         f"(keys: {sorted(siblings) or 'none'})"}
    try:
        async with open_graph(path, manifests_dir, readonly=True) as sg:
            node = await graph_task(sg.queue, sg.graph_id, "get_node", node_id=ref["foreign_id"])
    except RuntimeError as e:
        return {"error": f"the sibling graph `{ref['graph']}` cannot be opened: {e}"}
    if node is None:
        return {"error": f"the design system `{ref['graph']}:{ref['foreign_id'][:8]}` ({ref['name']}) is gone "
                         "from the sibling graph"}
    wire = node if isinstance(node, dict) else {"id": ref["foreign_id"], "label": getattr(node, "label", ""),
                                                "properties": getattr(node, "properties", {}) or {}}
    props = wire.get("properties") or {}
    if props.get("entity_kind") != P.ENTITY_DESIGN_SYSTEM:
        return {"error": f"`{ref['graph']}:{ref['foreign_id'][:8]}` is not a design_system Entity"}
    try:
        tokens = json.loads(str(props.get("tokens") or ""))
        T.check(tokens, f"{props.get('key')} (captured)")
    except (ValueError, T.SchemaError) as e:
        return {"error": f"the captured tokens of `{props.get('key')}` do not pass schema v1: {e}"}
    live = foreign_content_hash(wire)
    repos_dir = (load_graph_config(path) or {}).get("repos_dir") or ""
    return {"id": ref["foreign_id"], "slug": str(props.get("key") or T.slug(tokens)), "name": str(props.get("name") or ""),
            "tokens": tokens, "content_hash": str(props.get("content_hash") or ""),
            "repo_key": str(props.get("repo_key") or ""), "artifact_path": str(props.get("artifact_path") or ""),
            "repos_dir": str(repos_dir), "live_hash": live, "stale": live != ref["observed_hash"]}


def system_fonts(
    system: Dict[str, Any],  # `bound_system` output
) -> Dict[str, Any]:  # {dir, files: [Path], hashes: [{file, sha256}]} | {error}
    """THE FONT SEAM (amendment 4b58c9db (2)): the files the captured tokens name, found beside
    the captured tokens file through the system node's own locator. Until work item 879816d8
    captures fonts on-graph, the hashes are read here and reported, never checked."""
    from cjm_design_system import web
    if not (system["repos_dir"] and system["repo_key"] and system["artifact_path"]):
        return {"error": f"design system `{system['slug']}` has no locator (repos_dir / repo_key / artifact_path)"}
    sdir = Path(system["repos_dir"]) / system["repo_key"] / Path(system["artifact_path"]).parent
    if not sdir.is_dir():
        return {"error": f"design system `{system['slug']}`'s directory is missing: {sdir}"}
    files = web.font_files(system["tokens"], sdir)
    return {"dir": str(sdir), "files": files,
            "hashes": [{"file": p.name, "sha256": web.sha256_file(p)} for p in files]}


def mode_pair(
    tokens: Dict[str, Any],        # The system's tokens
    facts: Dict[str, str],         # The profile's mode facts ({light?, dark?})
) -> Dict[str, Any]:  # {light, dark} | {error}
    """The light / dark pair: the profile's facts, else the system's scheme map (9a7224a7 (3))."""
    from cjm_design_system import tokens as T
    pair: Dict[str, Any] = {}
    for side in ("light", "dark"):
        mode = facts.get(side) or T.mode_for_scheme(tokens, side)
        if not mode:
            return {"error": f"{tokens.get('name')} has no scheme map naming a {side} mode -- the profile needs a "
                             f"design_{side}_mode fact (modes: {T.modes(tokens)})"}
        if mode not in (tokens.get("modes") or {}):
            return {"error": f"design_{side}_mode `{mode}` is not a mode of {tokens.get('name')} ({T.modes(tokens)})"}
        pair[side] = mode
    return pair


def _write_if_changed(path: Path, text: str) -> bool:
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


async def project_theme(
    gx: GraphHandle,
    website_root: str,                # The site project root
    profile: str,                     # The Quarto profile being built
    theme: Any,                       # The profile's `format.html.theme` (`quarto inspect`)
    siblings: Optional[Dict[str, str]] = None,  # This graph's `sibling_graphs`
    manifests_dir: str = DEFAULT_MANIFESTS,
) -> Dict[str, Any]:  # {used, key, system, modes, stale?, relink?, fonts, written, errors}
    """Write the profile's theme from its bound design system (the module docstring's steps)."""
    from cjm_design_system import web
    rep: Dict[str, Any] = {"used": theme_wanted(theme), "errors": []}
    if not rep["used"]:
        return rep
    key = rep["key"] = profile_key(website_root, profile)

    def _fail(why: str) -> Dict[str, Any]:
        rep["errors"].append({"kind": "theme", "key": key, "why": why})
        return rep

    binding = await site_binding(gx, key)
    if "error" in binding:
        return _fail(binding["error"])
    system = await bound_system(binding, siblings or {}, manifests_dir)
    if "error" in system:
        return _fail(system["error"])
    pair = mode_pair(system["tokens"], binding["modes"])
    if "error" in pair:
        return _fail(pair["error"])
    rep["system"] = {"slug": system["slug"], "name": system["name"], "id": system["id"],
                     "content_hash": system["content_hash"]}
    rep["modes"] = pair
    if system["stale"]:   # amendment (1): reported, never refused
        rep["stale"] = True
        rep["relink"] = (f"cg-write --notes link {binding['profile_id'][:8]} {DevRelations.STYLED_BY} "
                         f"{binding['reference']['graph']}:{system['id'][:8]}")
    fonts = system_fonts(system)
    if "error" in fonts:
        return _fail(fonts["error"])
    tok = system["tokens"]
    families = {tok["fonts"][s]["family"] for s in ("heading", "body", "mono", "ui")
                if (tok["fonts"].get(s) or {}).get("family")}
    root = Path(website_root)
    fonts_root = root / FONTS_DIR
    out = web.web_fonts(fonts["files"], fonts_root / system["slug"], f"{FONTS_DIR}/{system['slug']}/",
                        families=families)
    if fonts_root.is_dir():   # the fonts tree is wholly the build's: another system's subsets go
        for d in fonts_root.iterdir():
            if d.is_dir() and d.name != system["slug"]:
                for f in d.iterdir():
                    f.unlink()
                d.rmdir()
    named = {f["family"] for f in out["faces"]}
    rep["fonts"] = {"dir": fonts["dir"], "sources": fonts["hashes"], "subsets": len(out["files"]),
                    "bytes": sum(f["bytes"] for f in out["files"]), "converted": out["converted"],
                    "reused": out["reused"], "unmatched": out["unmatched"],
                    "unfound": sorted(families - named)}
    written = []
    for side, rel in THEME_FILES.items():
        if _write_if_changed(root / rel, web.theme_scss(tok, pair[side], out["css"], out["generics"])):
            written.append(rel)
    rep["written"] = written
    return rep
