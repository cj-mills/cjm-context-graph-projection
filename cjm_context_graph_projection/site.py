"""The public site's BUILD: one verb, run where the graph is (ruling 941f7f13; DEC 98293e72 (2)).

The site is one Quarto project with two profiles (ruling 4d29dc2e; design 13753cbf (1)):
`public` (the default) renders everything but the git-ignored drafts tree, `staging` adds it
plus the review affordances. The build owns the pipeline and Quarto is one stage of it:

1. generated inputs — the PROJECTED PAGES, every series page from its Series and every topic
   page from its Lens, under either profile (`sitepages`, design e240183f); under `staging`,
   the drafts listings (`staging_index`); and the THEME, from the design system the profile is
   STYLED_BY (`sitetheme`, design 0858bbd0);
2. `quarto render --profile <p>`, then every projected page checked for its rendered page;
3. the REDIRECT PROJECTION — a page's public path is a fact with history (ruling 96aff70e):
   every superseded `site_path` gets a redirect page to its page's active path, written into
   the output byte-identical to Quarto's own alias page. Quarto cannot be handed per-page
   aliases without editing the page's front matter (the source-identity DoD) or leaking
   directory metadata onto nested posts, so the build writes them itself; the front-matter
   aliases stay frozen source, and a page declaring an alias no fact records REFUSES the build;
4. under `public`, the PUBLISH GUARD — no drafts tree and no link into it in the output, and
   no deliverable page whose publish_state is not `published`.

Publishing is this verb, then `quarto publish gh-pages --no-render`: no `post-render` hook in
the site config names a command on this machine."""

import json
import posixpath
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from cjm_context_graph_primitives.query import PropertyPredicate
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.vocab import DevNodeKinds

from . import factlayer as F
from .archive import path_owners
from .runtime import GraphHandle
from .sitelinks import site_path_key

# Quarto's redirect-map.ejs (1.10) as its renderEjs emits it: empty lines dropped, one
# trailing newline, the map interpolated unescaped as compact JSON in insertion order.
_REDIRECT_PAGE = """<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>Redirect</title>
  <script type="text/javascript">
    var redirects = {redirects};
    var hash = window.location.hash.startsWith('#') ? window.location.hash.slice(1) : window.location.hash;
    var redirect = redirects[hash] || redirects[""] || "/";
    window.document.title = 'Redirect to  ' +  redirect;
    if (!redirects[hash]) {{
      redirect = redirect + window.location.hash;
    }}
    redirect = redirect + window.location.search;
    window.location.replace(redirect);
  </script>
</head>
<body>
</body>
</html>
"""

# A link INTO the drafts tree from a public page (href/src attributes, markdown link targets in
# the .llms.md copies, listing and search JSON, the sitemap, llms.txt): the guard's text scan
_DRAFTS_REF = re.compile(r"""(?:href|src)=["'][^"']*\bdrafts/|\]\([^)\s]*\bdrafts/|["'/]drafts/posts/""")
_TEXT_OUTPUTS = (".html", ".json", ".xml", ".md", ".txt")


def output_href(
    path: str,  # A site path ("/posts/x/", "/log/x", "/series/notes/x.html")
) -> str:  # The output file it names, relative to the output dir ("posts/x/index.html")
    """Quarto's alias `fixupHref`: a trailing slash or an extension-less path names the
    directory's index.html, anything else the file itself."""
    href = path.split("#", 1)[0].lstrip("/")
    if not href or href.endswith("/"):
        return href + "index.html"
    if posixpath.splitext(href)[1] == "":
        return href + "/index.html"
    return href


def redirect_page(
    redirects: Dict[str, str],  # {fragment ("" = the page): target href relative to the stub's dir}
) -> str:  # The redirect page, byte-identical to the one Quarto writes for a front-matter alias
    """Quarto's redirect page for one alias location."""
    return _REDIRECT_PAGE.format(redirects=json.dumps(redirects, ensure_ascii=False,
                                                      separators=(",", ":")))


async def redirect_plan(
    gx: GraphHandle,
) -> Dict[str, Any]:  # {stubs: [{stub, target, alias, subject}], pages, errors}
    """The redirect projection from the `site_path` facts: one stub per superseded path, to
    its page's ACTIVE path. A value belongs to the page its supersession chain ends at
    (`archive.path_owners`): the page's own prior paths, and every path TRANSFERRED to it
    from another holder (design amendment e916a4b9 (4)). A page with no single active path, a
    value reaching two pages, or two superseded paths landing on one stub, is an error row —
    the build refuses, never guesses."""
    assertions = await F.load_label_where(
        gx, DevNodeKinds.ASSERTION, [PropertyPredicate("predicate", "eq", P.SITE_PATH)])
    supers = await F.load_supersedes(gx) if assertions else []
    stubs: List[Dict[str, Any]] = []
    owners, errors = path_owners(assertions, supers)
    pages: Dict[str, Dict[str, Any]] = {}
    standing_ids = {F.nid(a) for a in F.active_assertions(assertions, supers)}
    owned: Dict[str, List[Any]] = {}
    for a in assertions:
        owner = owners.get(str(F.nid(a)))
        if owner:
            owned.setdefault(owner, []).append(a)
    for subject, group in sorted(owned.items()):
        standing = [a for a in group if F.nid(a) in standing_ids]
        values = [str(F.prop(a, "value") or "") for a in standing]
        if len(standing) != 1:
            errors.append({"kind": "active", "subject": subject, "active": sorted(values),
                           "why": "a page's site_path needs exactly one active value"})
            continue
        active = values[0]
        prior = sorted({str(F.prop(a, "value") or "") for a in group
                        if F.nid(a) not in standing_ids} - {active})
        pages[subject] = {"active": active, "superseded": prior}
        target = output_href(active)
        for alias in prior:
            stub = output_href(alias)
            stubs.append({"stub": stub, "alias": alias, "subject": subject,
                          "target": posixpath.relpath(target, posixpath.dirname(stub) or ".")})
    seen: Dict[str, str] = {}
    for s in stubs:
        if s["stub"] in seen and seen[s["stub"]] != s["subject"]:
            errors.append({"kind": "collision", "stub": s["stub"], "subjects": [seen[s["stub"]], s["subject"]],
                           "why": "two pages' superseded paths land on one redirect page"})
        seen[s["stub"]] = s["subject"]
    return {"stubs": sorted(stubs, key=lambda s: s["stub"]), "pages": pages, "errors": errors}


def _front_matter(text: str) -> Dict[str, Any]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    try:
        return yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        return {}


def check_source_aliases(
    website_root: str,              # The site project root
    inputs: List[str],              # The profile's input documents (absolute paths, from `quarto inspect`)
    pages: Dict[str, Dict[str, Any]],  # redirect_plan's {subject: {active, superseded}}
    drafts_dir: Optional[str] = None,  # The drafts tree (relative to the root): its pages have no public path yet
) -> Dict[str, Any]:  # {checked, declared, errors}
    """Every front-matter alias must be a superseded `site_path` of the page that declares it:
    the facts are the redirect authority, the front matter frozen source that must agree.
    A DRAFT is skipped: it holds no public path until it is published, and its aliases are the
    publication's intent (a born page replacing an archive post claims that post's URL), which
    the path transfer at publish records as facts."""
    by_key = {site_path_key(p["active"]): p for p in pages.values()}
    root = Path(website_root).resolve()
    drafts = (root / drafts_dir).resolve() if drafts_dir else None
    errors: List[Dict[str, Any]] = []
    checked = declared = 0
    for doc in inputs:
        if drafts is not None and Path(doc).resolve().is_relative_to(drafts):
            continue
        aliases = _front_matter(Path(doc).read_text(encoding="utf-8")).get("aliases")
        if not aliases:
            continue
        checked += 1
        rel = Path(doc).resolve().relative_to(root).as_posix()
        url = "/" + posixpath.splitext(rel)[0] + ".html"
        page = by_key.get(site_path_key(url))
        known = {site_path_key(v) for v in (page or {}).get("superseded", [])}
        for alias in aliases if isinstance(aliases, list) else [aliases]:
            declared += 1
            if page is None or site_path_key(str(alias)) not in known:
                errors.append({"kind": "alias", "doc": rel, "alias": str(alias),
                               "why": "a front-matter alias no site_path fact records"
                                      if page else "the page declaring it has no site_path fact"})
    return {"checked": checked, "declared": declared, "errors": errors}


def write_redirects(
    output_dir: str,          # The profile's output dir
    stubs: List[Dict[str, Any]],  # redirect_plan's stubs
    page_outputs: List[str],  # Output files the render produced from input documents (relative)
) -> Dict[str, Any]:  # {written, unchanged, errors}
    """Write each redirect page; a stub landing on a rendered page is an error (Quarto skips it
    with a warning — here the facts contradict the site and the build says so)."""
    out = Path(output_dir)
    real = set(page_outputs)
    written = unchanged = 0
    errors: List[Dict[str, Any]] = []
    for s in stubs:
        if s["stub"] in real:
            errors.append({"kind": "overwrite", "stub": s["stub"], "subject": s["subject"],
                           "why": "a superseded path names a page the site still renders"})
            continue
        dest = out / s["stub"]
        text = redirect_page({"": s["target"]})
        if dest.exists() and dest.read_text(encoding="utf-8") == text:
            unchanged += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        written += 1
    return {"written": written, "unchanged": unchanged, "errors": errors}


async def publish_guard(
    gx: GraphHandle,
    output_dir: str,   # The PUBLIC profile's output dir
    drafts_dir: str,   # The drafts tree, relative to the project root ("drafts")
) -> Dict[str, Any]:  # {scanned, errors}
    """Refuse public output that carries a draft (design 13753cbf (1)): no drafts tree, no link
    into it, and no deliverable page whose active publish_state is not `published`."""
    from .purenotes import note_publish_states   # the one reader of the publish_state facts
    out = Path(output_dir)
    errors: List[Dict[str, Any]] = []
    if (out / drafts_dir).exists():
        errors.append({"kind": "drafts-tree", "path": drafts_dir,
                       "why": "the drafts tree is in the public output"})
    scanned = 0
    for p in out.rglob("*"):
        if p.suffix not in _TEXT_OUTPUTS or not p.is_file() or "site_libs" in p.parts:
            continue
        scanned += 1
        m = _DRAFTS_REF.search(p.read_text(encoding="utf-8", errors="replace"))
        if m:
            errors.append({"kind": "drafts-link", "path": p.relative_to(out).as_posix(), "match": m.group(0),
                           "why": "a public page links into the drafts tree"})
    states = await note_publish_states(gx)
    notes = await F.load_nodes(gx, sorted(states))
    for nid, vals in sorted(states.items()):
        if vals == [P.PUBLISH_PUBLISHED]:
            continue
        slug = str(F.prop(notes.get(nid), "slug") or "")
        if slug and (out / "posts" / slug / "index.html").exists():
            errors.append({"kind": "unpublished", "slug": slug, "publish_state": vals,
                           "why": "a deliverable not published has a public page"})
    # Design amendment c64e07e7: a post page with no publish_state is public only because its
    # Note is of an ARCHIVE type — an untyped Note, or one of a born type, has no such standing
    from .purenotes import note_types
    types = await note_types(gx)
    for n in await F.load_label(gx, DevNodeKinds.NOTE):
        nid, slug = str(F.nid(n)), str(F.prop(n, "slug") or "")
        if not slug or nid in states or not (out / "posts" / slug / "index.html").exists():
            continue
        t = types.get(nid) or {}
        if t.get("origin") != P.ORIGIN_ARCHIVE:
            errors.append({"kind": "unstanding", "slug": slug, "type": t.get("type"),
                           "why": "a public post page whose Note has no publish_state and no archive type"})
    return {"scanned": scanned, "errors": errors}


def quarto_inspect(
    website_root: str,  # The site project root
    profile: str,       # The Quarto profile
) -> Dict[str, Any]:  # {output_dir, inputs, website, theme} — the profile's own config, never re-typed here
    """The profile's output dir and input documents, read from Quarto itself."""
    r = subprocess.run(["quarto", "inspect", "--profile", profile], cwd=website_root,
                       capture_output=True, text=True, check=True)
    info = json.loads(r.stdout)
    root = Path(info.get("dir") or website_root)
    out = info["config"]["project"].get("output-dir") or "_site"
    # `format` may be a bare name ("html") and its html entry a bare string: a theme only where both are maps
    fmt = info["config"].get("format")
    html = fmt.get("html") if isinstance(fmt, dict) else None
    return {"output_dir": str(root / out), "inputs": list(info["files"]["input"]),
            "website": info["config"].get("website") or {},
            "theme": html.get("theme") if isinstance(html, dict) else None}


def page_outputs(
    website_root: str,  # The site project root
    inputs: List[str],  # Input documents (absolute)
) -> List[str]:  # Their output files, relative to the output dir
    root = Path(website_root).resolve()
    return [posixpath.splitext(Path(d).resolve().relative_to(root).as_posix())[0] + ".html" for d in inputs]


async def site_build(
    gx: GraphHandle,
    website_root: str,              # The site project root (the public repo clone)
    profile: str = "public",        # The Quarto profile to build
    *,
    render: bool = True,            # Run `quarto render` (False = project onto the existing output)
    drafts_dir: str = "drafts",     # The drafts tree relative to the project root
    staging_index_fn: Optional[Any] = None,  # Awaitable () -> report: regenerate the drafts listings (staging)
    siblings: Optional[Dict[str, str]] = None,  # This graph's sibling_graphs (the theme's design system lives in one)
    manifests_dir: Optional[str] = None,        # The graph-storage capability manifests (default: the runtime's)
) -> Dict[str, Any]:  # {profile, output_dir, theme, render, staging_index, aliases, redirects, guard, errors, ok}
    """Build the site under one profile: generated inputs, render, the redirect projection,
    and (public) the publish guard. `ok` is False on any error row — the output is then not
    fit to publish, and the report names why."""
    from .agentlayer import (check_jsonld, check_llms_fragments, restore_llms_anchors, rewrite_llms_links,
                             write_llms_txt)
    from .derivedblocks import check_derived, check_end_placement, derived_plan, write_derived
    from .runtime import DEFAULT_MANIFESTS
    from .sitepages import check_listing_chips, check_page_outputs, project_pages
    from .sitetheme import project_theme
    rep: Dict[str, Any] = {"profile": profile, "errors": []}
    plan = await redirect_plan(gx)
    rep["errors"] += plan["errors"]
    if rep["errors"]:
        rep["ok"] = False
        return rep
    # The projected pages are inputs: written before Quarto is asked what the inputs are
    pages = await project_pages(gx, website_root, profile, plan["pages"], drafts_dir=drafts_dir)
    rep["pages"] = {k: v for k, v in pages.items() if k not in ("errors", "sources", "plan")}
    rep["errors"] += pages["errors"]
    if rep["errors"]:
        rep["ok"] = False
        return rep
    info = quarto_inspect(website_root, profile)
    rep["output_dir"] = info["output_dir"]
    aliases = check_source_aliases(website_root, info["inputs"], plan["pages"], drafts_dir)
    rep["aliases"] = {k: v for k, v in aliases.items() if k != "errors"}
    rep["errors"] += aliases["errors"]
    if rep["errors"]:   # the facts and the source disagree: nothing is rendered or written
        rep["ok"] = False
        return rep
    # The derived blocks leave the render and the post navigation replaces them (design
    # 253ac996): the plan names each post's blocks and navigation for the one Lua filter
    derived = await derived_plan(gx, website_root, pages["plan"], plan["pages"], info["inputs"],
                                 site=info["website"])
    rep["derived"] = derived["counts"]
    rep["errors"] += derived["errors"]
    # Related posts rank by stored judgments (amendment e09e262b): a public build with a stale
    # post is not fit to publish -- the publish step runs `judge-related` first
    # The born posts that derive from a sibling while naming no source (722a8232): reported
    if derived.get("sources_missing"):
        rep["sources_report"] = {"missing": derived.get("sources_missing") or []}
    stale = derived.get("related_stale") or []
    if profile == "public" and stale:
        rep["errors"].append({"kind": "related-stale",
                              "why": f"{len(stale)} public post(s) have missing or stale related judgments "
                                     "-- run judge-related",
                              "detail": [s["title"] for s in stale[:10]]})
    if rep["errors"]:
        rep["ok"] = False
        return rep
    # The theme from the profile's bound design system (leg C of 0858bbd0; amendment 4b58c9db),
    # written before the render like the derived blocks; a config naming another theme keeps it
    theme = await project_theme(gx, website_root, profile, info.get("theme"), siblings,
                                manifests_dir or DEFAULT_MANIFESTS)
    rep["theme"] = {k: v for k, v in theme.items() if k != "errors"}
    rep["errors"] += theme["errors"]
    if rep["errors"]:
        rep["ok"] = False
        return rep
    write_derived(website_root, derived)
    if profile == "staging" and staging_index_fn is not None:
        si = await staging_index_fn()
        rep["staging_index"] = {"counts": si.get("counts"), "written": len(si.get("written") or [])}
    if render:
        r = subprocess.run(["quarto", "render", "--profile", profile], cwd=website_root,
                           capture_output=True, text=True)
        rep["render"] = {"returncode": r.returncode,
                         "tail": [ln for ln in (r.stdout + r.stderr).splitlines() if "ERROR" in ln][-5:]}
        if r.returncode != 0:
            rep["errors"].append({"kind": "render", "why": "quarto render failed",
                                  "detail": rep["render"]["tail"]})
            rep["ok"] = False
            return rep
    rep["errors"] += check_page_outputs(info["output_dir"], pages["sources"])
    if render and pages.get("category_listing"):   # every projected chip a link (design a7224060)
        rep["errors"] += check_listing_chips(info["output_dir"], pages["sources"])
    if render:   # the filter reports only when it ran: a projection onto old output has nothing to check
        chk = check_derived(website_root, derived)
        rep["derived"]["reported"] = chk["reported"]
        rep["errors"] += chk["errors"]
        placed = check_end_placement(info["output_dir"], derived)
        rep["derived"]["end_placed"] = placed["checked"]
        rep["errors"] += placed["errors"]
        jl = check_jsonld(info["output_dir"], derived)
        rep["derived"]["jsonld_checked"] = jl["checked"]
        rep["errors"] += jl["errors"]
    # The agent layer (amendment 23a49667 (2), (3)): links kept in the markdown layer, the page's
    # ids carried into it and every fragment checked (design b82d2a98), then the projected
    # llms.txt over Quarto's flat list
    if derived.get("llms"):
        rew = rewrite_llms_links(info["output_dir"], pages.get("category_listing") or "")
        anc = restore_llms_anchors(info["output_dir"])
        frag = check_llms_fragments(info["output_dir"])
        wr = write_llms_txt(info["output_dir"], derived["llms"])
        rep["agent"] = {"llms_md": rew["files"], "links_rewritten": rew["rewritten"],
                        "categories_stated": rew["stated"], "anchors": anc["anchors"],
                        "fragments_checked": frag["checked"], "fragments_dead": len(frag["dead"]),
                        "llms_txt_written": wr["written"], **derived["llms"]["counts"]}
        if frag["dead"]:   # a source defect the page shares, reported (b82d2a98 (2))
            rep["agent"]["dead_fragments"] = frag["dead"]
        rep["errors"] += frag["errors"] + wr["errors"]
    red = write_redirects(info["output_dir"], plan["stubs"], page_outputs(website_root, info["inputs"]))
    rep["redirects"] = {"stubs": len(plan["stubs"]), "written": red["written"], "unchanged": red["unchanged"]}
    rep["errors"] += red["errors"]
    if profile == "public":
        guard = await publish_guard(gx, info["output_dir"], drafts_dir)
        rep["guard"] = {"scanned": guard["scanned"]}
        rep["errors"] += guard["errors"]
    rep["ok"] = not rep["errors"]
    return rep


def stated(
    note: Any,  # A Note node (None reads as nothing)
    key: str,   # A front-matter field: "title", "description", "subtitle"
) -> str:  # What the page states for it ("" when nothing does)
    """The ONE reader of what a post's page states (finding 12d98020): its front matter first,
    then its metadata, then the Note's own property. A born Note's title and description are
    its working ones while its page states its front matter's (the type may derive them at
    render), so every leg that names a post -- its navigation, related posts, the Library, the
    Tutorials matrix, the claims report, the agent layer, the judge's view -- reads it here and
    a post is named one way everywhere. An archive Note's property came from its front matter
    at ingest, so it reads the same either way."""
    if note is None:
        return ""
    front = _front_matter(str(F.prop(note, "frontmatter_raw") or ""))
    md = F.prop(note, "metadata") or {}
    value = (front.get(key) if isinstance(front, dict) else None) or md.get(key) or F.prop(note, key)
    return str(value or "").strip()   # a page states no edge whitespace (ingest stripped it too)
