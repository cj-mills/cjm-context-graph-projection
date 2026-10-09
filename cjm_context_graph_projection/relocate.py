"""The relocation of removed pages into repositories (design 3d5ee659 for the relocation item
489dc0b1, under amendments cbd5f154 (4) and 6514869f).

A page ruled `removed` leaves the public site for a REPO COPY: its companion repository, or the
dedicated repository for pages with none. The copy is a DERIVATIVE, projected from the page's
source at the website clone's HEAD and never edited by hand: Quarto's own gfm writer, run in an
isolated scratch project (no site filters, profiles or chrome), turns callouts into GitHub
alerts and resolves includes; one Lua filter applies the LINK MAP this module computes (a
site link becomes its absolute URL, a link to another relocated page becomes that page's copy,
a file the copy carries stays relative) and turns a YouTube iframe into a thumbnail linking to
the video. The include shortcodes the config lists as site CHROME are dropped before the render.

Three steps, one verb:
- PLAN (a read): every pending page, the repository its own links name (one per series, the
  canonical name read from GitHub), its copy's place and URL.
- PROJECT (files only): the copies of the named pages into a local clone of their destination,
  with the clone README's pointer block regenerated; no graph write, no network.
- LAND (journaled): once a copy is pushed, the copy on GitHub is read back and compared with the
  clone's; only then the page retires (`retire-source`, the op the ingest restores from) and the
  `relocate` op mints the copy's web Reference and the RELOCATED_TO edge to it -- or, for a page
  a public successor supersedes, the retirement names that successor. A page whose copy is
  missing or differs is refused, so no URL is ever left without a destination.

The git and gh steps between PROJECT and LAND are outward and stay outside the verb."""

import hashlib
import json
import posixpath
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote, urlsplit

import yaml
from cjm_context_graph_layer.ops import extend_graph
from cjm_context_graph_primitives.journal import op_now
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.nodes import ReferenceNode
from cjm_dev_graph_schema.vocab import DevRelations

from . import factlayer as F
from .archive import git_blob, git_head, RETIRE_VERB
from .runtime import GraphHandle
from .sitelinks import site_path_key

RELOCATE_VERB = "relocate"   # The journaled op: a page's repo copy (its web Reference + RELOCATED_TO)
COPY_FILE = "README.md"      # The copy's file: GitHub renders it as the directory's page too
COPY_LABEL = "repo copy"     # The copy Reference's foreign label
BLOCK_START = "<!-- relocated-posts:start (projected by the relocate verb; never edit by hand) -->"
BLOCK_END = "<!-- relocated-posts:end -->"
CONFIG_KEYS = ("retired_repo", "retired_dir", "companion_dir", "chrome_includes")
_INCLUDE = re.compile(r"^[ \t]*\{\{<\s*include\s+(\S+)\s*>\}\}[ \t]*\r?$", re.M)
_RAW_ATTR = re.compile(r"""\s(href|src)=(["'])(.*?)\2""")   # the Lua filter's attribute rule, mirrored
_PAGE_EXT = re.compile(r"\.(md|qmd|ipynb)$")
_INDEX = re.compile(r"(^|/)index\.(md|qmd|ipynb)$")
_GITHUB = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s#?]+?)(?:\.git)?/?$")
_BLOB = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)/" + re.escape(COPY_FILE) + "$")

# The filter Quarto runs over the copy (design 3d5ee659 (1)). It reads the link map the module
# wrote (`relocate-map` in the scratch project's metadata) and only substitutes: every decision
# is the module's, so the rules are tested in Python. The raw-HTML attribute rule is _RAW_ATTR's.
LUA_FILTER = r'''-- The relocation's link map and YouTube embeds (design 3d5ee659 (1)); generated, never edited.
local map = {links = {}, images = {}, raw = {}}

local function load(meta)
  local p = meta["relocate-map"]
  if p then
    local f = io.open(pandoc.utils.stringify(p), "r")
    if f then
      map = pandoc.json.decode(f:read("a"), false)
      f:close()
    end
  end
end

local function attrs(s)
  for _, name in ipairs({"href", "src"}) do
    s = s:gsub("(%s)" .. name .. "=([\"'])(.-)%2", function(sp, q, v)
      local new = map.raw[v]
      if new then return sp .. name .. "=" .. q .. new .. q end
    end)
  end
  return s
end

local function youtube(s)
  return s:match("<iframe[^>]-src=[\"']https?://www%.youtube%.com/embed/([%w_%-]+)")
      or s:match("<iframe[^>]-src=[\"']https?://www%.youtube%-nocookie%.com/embed/([%w_%-]+)")
end

local function video(id)
  local thumb = pandoc.Image({pandoc.Str("Watch on YouTube")}, "https://img.youtube.com/vi/" .. id .. "/hqdefault.jpg")
  return pandoc.Link({thumb}, "https://www.youtube.com/watch?v=" .. id)
end

local function closing(s)
  return s:match("^%s*</iframe>%s*$") ~= nil
end

local function raw_block(el)
  if not el.format:match("^html") then return nil end
  local id = youtube(el.text)
  if id and el.text:gsub("<iframe.->", ""):gsub("</iframe>", ""):match("^%s*$") then
    return pandoc.Para({video(id)})
  end
  if closing(el.text) then return {} end
  el.text = attrs(el.text)
  return el
end

local function raw_inline(el)
  if not el.format:match("^html") then return nil end
  local id = youtube(el.text)
  if id then return video(id) end
  if closing(el.text) then return {} end
  el.text = attrs(el.text)
  return el
end

local function link(el)
  local new = map.links[el.target]
  if new then el.target = new end
  return el
end

local function image(el)
  local new = map.images[el.src]
  if new then el.src = new end
  return el
end

return {{Meta = load}, {RawBlock = raw_block, RawInline = raw_inline, Link = link, Image = image}}
'''


def relocation_config(
    cfg: Dict[str, Any],  # The notes graph's sibling config
) -> Dict[str, Any]:  # {config, errors}
    """The relocation's settings (DATA beside the notes db, design 3d5ee659 (2)/(3)): the
    repository for pages with no repo of their own, the copy directories, the chrome includes."""
    rel = dict(cfg.get("relocation") or {})
    errors = [f"relocation.{k} is missing" for k in ("retired_repo",) if not rel.get(k)]
    out = {"retired_repo": str(rel.get("retired_repo") or ""), "retired_dir": str(rel.get("retired_dir") or "posts"),
           "companion_dir": str(rel.get("companion_dir") or "retired-post"),
           "chrome_includes": [str(c) for c in rel.get("chrome_includes") or []]}
    return {"config": out, "errors": errors}


# ---------------------------------------------------------------------------------------------
# PURE -- the plan's candidates, the copy's place, the link map, the header and the pointer block


def github_repo(
    url: str,  # A git remote URL or a GitHub repository URL
) -> Optional[str]:  # owner/name, or None
    m = _GITHUB.search(url.strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def linked_repos(
    text: str,   # A page's source
    owner: str,  # The account whose repositories count
) -> Counter:  # {repo name lowercased: links}
    """The owner's repositories a page links, counted (a clone URL counts as its repository)."""
    pat = re.compile(r"github\.com/" + re.escape(owner) + r"/([A-Za-z0-9_.-]+)", re.I)
    out: Counter = Counter()
    for name in pat.findall(text):
        name = re.sub(r"\.git$", "", name.rstrip("."))
        if name:
            out[name.lower()] += 1
    return out


def repo_candidates(
    posts: List[Dict[str, Any]],  # [{id, slug, text}]
    repos: Dict[str, str],        # {repo name lowercased: canonical owner/name} -- the owner's repositories
    owner: str,                   # The owner whose repositories count
    exclude: Iterable[str] = (),  # Repositories never a companion (the site's own repository)
) -> Dict[str, Dict[str, Any]]:  # {id: {group, candidate, counts}}
    """Each page's candidate companion repository: the one its SERIES links most (a series =
    the pages sharing a slug's first segment), among repositories that exist; ties break by
    name. The candidate is a proposal the user confirms (design 3d5ee659 (5))."""
    skip = {e.lower() for e in exclude}
    groups: Dict[str, Counter] = {}
    own: Dict[str, Counter] = {}
    for p in posts:
        c = Counter({k: v for k, v in linked_repos(p["text"], owner).items() if k in repos and k not in skip})
        own[p["id"]] = c
        groups.setdefault(p["slug"].split("/")[0], Counter()).update(c)
    out: Dict[str, Dict[str, Any]] = {}
    for p in posts:
        g = p["slug"].split("/")[0]
        ranked = sorted(groups[g].items(), key=lambda kv: (-kv[1], kv[0]))
        out[p["id"]] = {"group": g, "candidate": repos[ranked[0][0]] if ranked else None,
                        "counts": {repos[k]: v for k, v in sorted(own[p["id"]].items(), key=lambda kv: (-kv[1], kv[0]))}}
    return out


def copy_dir(
    slug: str,                 # The page's slug
    repo: str,                 # owner/name of the destination
    config: Dict[str, Any],    # relocation_config's config
) -> str:  # The copy's directory in the repository
    """posts/<slug> in the dedicated repository, retired-post/<slug> in a companion repository:
    the whole slug, so two series sharing a repository never collide."""
    base = config["retired_dir"] if repo.lower() == config["retired_repo"].lower() else config["companion_dir"]
    return f"{base}/{slug}"


def copy_url(
    repo: str,     # owner/name
    branch: str,   # The default branch
    directory: str,  # The copy's directory
    raw: bool = False,  # The raw file (the land check reads it) rather than the rendered page
) -> str:
    if raw:
        return f"https://raw.githubusercontent.com/{repo}/{branch}/{directory}/{COPY_FILE}"
    return f"https://github.com/{repo}/blob/{branch}/{directory}/{COPY_FILE}"


def parse_copy_url(
    url: str,  # A copy's URL (copy_url's form)
) -> Optional[Dict[str, str]]:  # {repo, branch, dir}, or None for another URL
    m = _BLOB.match(url)
    return {"repo": f"{m.group(1)}/{m.group(2)}", "branch": m.group(3), "dir": m.group(4)} if m else None


def copy_targets(
    cur: Dict[str, str],               # The copy being projected: {repo, branch, dir}
    copies: List[Dict[str, Any]],      # Every known copy: {keys: [site path keys], repo, branch, dir}
) -> Dict[str, Dict[str, str]]:  # {site path key: {page, files, raw}}
    """Where a link to a relocated page goes from this copy: relative inside one repository,
    the GitHub URL across repositories (a link to a file under it: blob for a link, raw for an
    image)."""
    out: Dict[str, Dict[str, str]] = {}
    for c in copies:
        if c["repo"].lower() == cur["repo"].lower():
            rel = posixpath.relpath(c["dir"], cur["dir"])
            ref = {"page": posixpath.join(rel, COPY_FILE), "files": rel, "raw": rel}
        else:
            base = f"https://github.com/{c['repo']}"
            ref = {"page": copy_url(c["repo"], c["branch"], c["dir"]), "files": f"{base}/blob/{c['branch']}/{c['dir']}",
                   "raw": f"{base}/raw/{c['branch']}/{c['dir']}"}
        for k in c["keys"]:
            out[k] = ref
    return out


def resolve_target(
    target: str,                       # A link target as the source writes it
    *,
    source_dir: str,                   # The source's directory as a site path ("/posts/x/part-1/")
    local: Iterable[str],              # The files the copy carries, relative to its directory
    site_url: str,                     # The site's URL (no trailing slash)
    copies: Dict[str, Dict[str, str]], # copy_targets' map
    image: bool = False,               # An image's source (a raw URL across repositories)
) -> Optional[str]:  # The target in the copy, or None to keep it as written
    """One link target in the copy (design 3d5ee659 (1)). A fragment, a mail link or another
    host stays; a file the copy carries stays relative; a page that is relocated (or a file
    under one) goes to its copy; any other site link becomes its absolute URL on the site. A
    relative target resolves against the SOURCE's directory, as Quarto resolves it."""
    t = target.strip()
    if not t or t.startswith("#"):
        return None
    parts = urlsplit(t)
    host = urlsplit(site_url).netloc.lower()
    absolute = bool(parts.scheme or parts.netloc)
    if absolute:
        if parts.scheme not in ("http", "https") or parts.netloc.lower() not in (host, f"www.{host}"):
            return None
        path = parts.path or "/"
    else:
        path = unquote(parts.path)
        if not path:
            return None
        if not path.startswith("/"):
            if posixpath.normpath(path) in set(local):
                return None
            joined = posixpath.normpath(posixpath.join(source_dir, path))
            path = joined + ("/" if path.endswith("/") and joined != "/" else "")
    path = _INDEX.sub(lambda m: m.group(1), path)
    path = _PAGE_EXT.sub(".html", path)
    frag = f"#{parts.fragment}" if parts.fragment else ""
    key = site_path_key(path)
    if key in copies:
        return copies[key]["page"] + frag
    for k, ref in copies.items():
        if key and key.startswith(k.rstrip("/") + "/"):
            return posixpath.join(ref["raw" if image else "files"], key[len(k.rstrip("/")) + 1:]) + frag
    if absolute:
        return None
    if not posixpath.splitext(path)[1] and not path.endswith("/"):
        path += "/"
    return site_url + path + (f"?{parts.query}" if parts.query else "") + frag


def strip_chrome(
    text: str,             # A page's source
    chrome: Iterable[str], # The include paths that are site chrome
) -> Tuple[str, List[str]]:  # (the source without them, the includes dropped)
    drop = {c.lstrip("/") for c in chrome}
    dropped: List[str] = []

    def keep(m: "re.Match") -> str:
        if m.group(1).lstrip("/") in drop:
            dropped.append(m.group(1))
            return ""
        return m.group(0)

    return _INCLUDE.sub(keep, text), dropped


def with_lead(
    text: str,         # A page's source
    description: str,  # Its front-matter description ("" = none)
) -> str:  # The source with the description as its opening paragraph
    """The description opens the copy's body, so its links pass through the link map like any
    other (front matter is markdown too)."""
    if not description.strip():
        return text
    lead = f"*{description.strip()}*" if "*" not in description else description.strip()
    end = text.find("\n---", 3) if text.startswith("---") else -1
    if end < 0:
        return f"{lead}\n\n{text}"
    cut = text.find("\n", end + 4)
    cut = len(text) if cut < 0 else cut + 1
    return text[:cut] + f"\n{lead}\n\n" + text[cut:]


def include_paths(
    text: str,  # A page's source (chrome already dropped)
) -> List[str]:  # The include paths it names, as written
    return [m.group(1) for m in _INCLUDE.finditer(text)]


def ast_targets(
    ast: Dict[str, Any],  # A Pandoc JSON document
) -> Dict[str, set]:  # {links, images, raw}: every link target the document writes
    out: Dict[str, set] = {"links": set(), "images": set(), "raw": set()}

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            t, c = x.get("t"), x.get("c")
            if t == "Link":
                out["links"].add(c[2][0])
            elif t == "Image":
                out["images"].add(c[2][0])
            elif t in ("RawBlock", "RawInline") and str(c[0]).startswith("html"):
                out["raw"].update(m.group(3) for m in _RAW_ATTR.finditer(" " + c[1]))
            walk(c)
        elif isinstance(x, list):
            for y in x:
                walk(y)

    walk(ast.get("blocks", []))
    return out


def link_map(
    targets: Dict[str, set],  # ast_targets' sets
    **ctx: Any,               # resolve_target's keyword arguments, image excepted
) -> Dict[str, Dict[str, str]]:  # {links, images, raw}: {target as written: target in the copy}
    """The map the Lua filter applies. A raw-HTML attribute resolves as a link unless it names
    an image file the copy does not carry (a `src`); it is the rare case, so the rule stays one."""
    out: Dict[str, Dict[str, str]] = {"links": {}, "images": {}, "raw": {}}
    for kind in ("links", "images", "raw"):
        for t in sorted(targets.get(kind) or ()):
            new = resolve_target(t, image=(kind == "images"), **ctx)
            if new is not None and new != t:
                out[kind][t] = new
    return out


def copy_header(
    *,
    title: str,              # The page's title
    retired_on: str,         # The retire date (ISO)
    published: str,          # The first-published date (ISO, "" = unknown)
    page_url: str,           # The page's URL on the site (now a redirect)
    site_url: str,           # The site's URL
    source_url: str,         # The source at its commit
    source_label: str,       # The source's path at its commit, as the link's text
    thread_url: str = "",    # The page's comment thread ("" = none)
) -> str:
    """The copy's head (design 3d5ee659 (4)): the title, a note naming where the page lived and
    since when it lives here, and the source it was projected from."""
    host = urlsplit(site_url).netloc
    first = f"first published on {published} at <{page_url}>" if published else f"first published at <{page_url}>"
    lines = [f"# {title}", "",
              "> [!NOTE]",
              f"> **Retired from [{host}]({site_url}) on {retired_on}.** This post was {first}, which now "
              "redirects here. It is kept as it was written; its tools and steps may be out of date.",
              ">",
              f"> Source: [`{source_label}`]({source_url})" + (f" · Comments: <{thread_url}>" if thread_url else ""),
              ""]
    return "\n".join(lines)


def pointer_block(
    entries: List[Dict[str, str]],  # [{title, path, date}] -- path relative to the README
    *,
    dedicated: bool,                # The dedicated repository (its README is the index)
    site_url: str,
) -> str:
    """The README's projected block: every copy the repository holds, by date."""
    host = urlsplit(site_url).netloc
    head = (["## Posts", ""] if dedicated else
            ["## Retired posts", "", f"The posts that accompanied this repository were retired from "
             f"[{host}]({site_url}) and now live here:", ""])
    rows = [f"- [{e['title']}]({e['path']}) ({e['date']})" if e.get("date") else f"- [{e['title']}]({e['path']})"
            for e in sorted(entries, key=lambda e: (e.get("date") or "", e["path"]))]
    return "\n".join([BLOCK_START, *head, *rows, BLOCK_END])


def merge_readme(
    existing: Optional[str],  # The README as it stands (None = none)
    block: str,               # pointer_block's block
    *,
    title: str,               # The heading a new README opens with
    preamble: str = "",       # A new README's opening paragraph
) -> str:
    """The README with the block replaced in place, else appended; a new README is the title,
    the preamble and the block."""
    if existing is None:
        parts = [f"# {title}", ""] + ([preamble, ""] if preamble else []) + [block, ""]
        return "\n".join(parts)
    start, end = existing.find(BLOCK_START), existing.find(BLOCK_END)
    if start >= 0 and end > start:
        return existing[:start] + block + existing[end + len(BLOCK_END):]
    return existing.rstrip("\n") + "\n\n" + block + "\n"


def front_matter(
    text: str,  # A page's source
) -> Dict[str, Any]:  # Its YAML front matter ({} when none parses)
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    try:
        return (yaml.safe_load(text[3:end]) or {}) if end > 0 else {}
    except yaml.YAMLError:
        return {}


# ---------------------------------------------------------------------------------------------
# THE RENDER -- Quarto's gfm writer in a scratch project


def render_copy(
    text: str,                     # The page's source, chrome dropped
    files: Dict[str, bytes],       # The files beside it ({path relative to its directory: bytes})
    includes: Dict[str, bytes],    # The includes it names ({path as written: bytes})
    maps: Dict[str, Dict[str, str]],  # link_map's map
    *,
    quarto: str = "quarto",
) -> Dict[str, Any]:  # {body} | {error}
    """The page's body as GitHub markdown: Quarto renders it in a scratch project of its own,
    with only the filter and a body-only template, so nothing of the site's configuration
    reaches the copy."""
    with tempfile.TemporaryDirectory(prefix="relocate-") as tmp:
        root = Path(tmp)
        post = root / "post"
        post.mkdir()
        (post / "index.md").write_text(text, encoding="utf-8")
        for rel, data in files.items():
            (post / rel).parent.mkdir(parents=True, exist_ok=True)
            (post / rel).write_bytes(data)
        for rel, data in includes.items():
            dest = (root / rel.lstrip("/")) if rel.startswith("/") else (post / rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        (root / "relocate.lua").write_text(LUA_FILTER, encoding="utf-8")
        (root / "body.md").write_text("$body$\n", encoding="utf-8")
        (root / "map.json").write_text(json.dumps(maps), encoding="utf-8")
        (root / "_quarto.yml").write_text(yaml.safe_dump({
            "project": {"type": "default"},
            "relocate-map": str(root / "map.json"),
            "format": {"gfm": {"wrap": "none", "template": str(root / "body.md"),
                               "filters": [str(root / "relocate.lua")]}}}), encoding="utf-8")
        r = subprocess.run([quarto, "render", "post/index.md", "--to", "gfm", "--output", "copy.md"],
                           cwd=root, capture_output=True, text=True)
        out = next((p for p in (post / "copy.md", root / "copy.md") if p.exists()), None)
        if r.returncode != 0 or out is None:
            return {"error": f"quarto render failed: {(r.stderr or r.stdout).strip()[-600:]}"}
        return {"body": out.read_text(encoding="utf-8")}


def source_targets(
    texts: List[str],  # The source and its includes
    *,
    quarto: str = "quarto",
) -> Dict[str, Any]:  # ast_targets' sets | {error}
    """Every link target the source writes, read by Pandoc (the same reader the render uses)."""
    out: Dict[str, set] = {"links": set(), "images": set(), "raw": set()}
    for text in texts:
        r = subprocess.run([quarto, "pandoc", "-f", "markdown", "-t", "json"], input=text,
                           capture_output=True, text=True)
        if r.returncode != 0:
            return {"error": f"quarto pandoc failed: {r.stderr.strip()[-400:]}"}
        got = ast_targets(json.loads(r.stdout))
        for k in out:
            out[k] |= got[k]
    return out


# ---------------------------------------------------------------------------------------------
# GIT -- the website clone (the source) and the destination clone


def _git(root: str, *args: str) -> Optional[str]:
    r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def clone_repo(
    root: str,  # A local clone
) -> Dict[str, Any]:  # {repo, branch} | {error}
    """The repository a clone pushes to (its origin) and the branch it holds."""
    url = _git(root, "remote", "get-url", "origin")
    repo = github_repo(url or "")
    branch = _git(root, "symbolic-ref", "--short", "HEAD")   # names an unborn branch too
    if not repo or not branch:
        return {"error": f"{root} is no clone of a GitHub repository on a branch (origin {url!r}, branch {branch!r})"}
    return {"repo": repo, "branch": branch}


def source_bundle(
    website_root: str,  # The website clone
    rel_path: str,      # The page's source under it
    commit: str,        # The commit to read
) -> Dict[str, Any]:  # {text, files: {rel: bytes}} | {error}
    """The source and every file in its directory at the commit, nested pages left out (a
    subdirectory holding its own index is another page)."""
    blob = git_blob(website_root, commit, rel_path)
    if blob is None:
        return {"error": f"{rel_path} is not at {commit[:12]}"}
    d = posixpath.dirname(rel_path)
    listing = _git(website_root, "ls-tree", "-r", "--name-only", commit, "--", d + "/") or ""
    names = [n for n in listing.splitlines() if n and n != rel_path]
    nested = {posixpath.dirname(n) for n in names if _INDEX.search(n)}
    files: Dict[str, bytes] = {}
    for n in names:
        if any(n == x or n.startswith(x + "/") for x in nested):
            continue
        data = git_blob(website_root, commit, n)
        if data is not None:
            files[posixpath.relpath(n, d)] = data
    return {"text": blob.decode("utf-8"), "files": files}


def gh_repos(
    owner: str,  # The account
) -> Dict[str, str]:  # {name lowercased: canonical owner/name}
    """The owner's repositories, read from GitHub through `gh` (its own auth)."""
    r = subprocess.run(["gh", "repo", "list", owner, "--limit", "1000", "--json", "nameWithOwner"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gh repo list {owner} failed: {r.stderr.strip()}")
    return {row["nameWithOwner"].split("/", 1)[1].lower(): row["nameWithOwner"] for row in json.loads(r.stdout)}


def http_fetch(
    url: str,  # A URL
) -> Tuple[int, bytes]:  # (status, body) -- status 0 when unreachable
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Cache-Control": "no-cache"}),
                                    timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except (urllib.error.URLError, OSError):
        return 0, b""


# ---------------------------------------------------------------------------------------------
# THE GRAPH -- the pages, their paths and destinations


async def load_pages(
    gx: GraphHandle,
    website_root: str,  # The website clone
    journal_path: Optional[str] = None,  # The notes journal (a retired page's commit and path)
) -> Dict[str, Any]:  # {rows: {id: row}, retired: {id: retire op args}, retired_on: {id: ISO date}, threads}
    """Every page leaving or gone (the standing rows), with its paths, its source, its retire
    record and its comment thread."""
    from .archive import retired_sources
    from .comments import load_threads
    from .site import redirect_plan
    from .sitepages import parse_date
    from .standing import load_standing
    standing = await load_standing(gx)
    plan = await redirect_plan(gx)
    retired = {r["note"]: r for r in retired_sources(journal_path)} if journal_path else {}
    slot = [a for a in await F.load_assertions(gx) if F.prop(a, "predicate") == P.PUBLISH_STATE
            and F.prop(a, "value") == P.PUBLISH_RETIRED]
    retired_on: Dict[str, str] = {}
    for a in slot:
        at = F.prop(a, "asserted_at")
        if at is not None:
            day = datetime.fromtimestamp(float(at), tz=timezone.utc).date().isoformat()
            sid = str(F.prop(a, "subject_id"))
            retired_on[sid] = min(retired_on.get(sid, day), day)
    rows: Dict[str, Dict[str, Any]] = {}
    # a page retired to its successor stays while it holds a path (its redirect is the build's)
    ids = [r["id"] for r in standing["rows"] if r["pending"] or r["relocated_to"]
           or (r["standing"] == P.PUBLISH_RETIRED and r["superseded_by"] and r["id"] in plan["pages"])]
    nodes = await F.load_nodes(gx, ids) if ids else {}
    root = Path(website_root).resolve()
    for nid in ids:
        r, node = standing["by_id"][nid], nodes.get(nid)
        page = plan["pages"].get(nid) or {}
        src = Path(str(F.prop(node, "path") or "")).resolve() if node is not None else None
        rel = (retired.get(nid) or {}).get("path") or (src.relative_to(root).as_posix()
                                                       if src and src.is_relative_to(root) else "")
        md = F.prop(node, "metadata") or {}
        published = parse_date(md.get("date"))
        rows[nid] = {**r, "rel_path": rel, "active": page.get("active", ""),
                     "paths": [p for p in [page.get("active")] + list(page.get("superseded") or []) if p],
                     "published": published.isoformat() if published else ""}
    return {"rows": rows, "retired": retired, "retired_on": retired_on, "threads": await load_threads(gx)}


def _site_url(website_root: str) -> str:
    from .aboutpage import load_site_config
    return str((load_site_config(website_root).get("website") or {}).get("site-url") or "").strip().rstrip("/")


def _pick(
    rows: Dict[str, Dict[str, Any]],  # load_pages' rows
    posts: List[str],                 # Slugs or ids (unique prefixes)
) -> Tuple[List[str], List[str]]:  # (ids, the refs matching no row)
    by_slug = {r["slug"]: i for i, r in rows.items()}
    ids, missing = [], []
    for p in posts:
        hit = by_slug.get(p) or (p if p in rows else None)
        if hit is None:
            pre = [i for i in rows if i.startswith(p)] if len(p) >= 6 else []
            hit = pre[0] if len(pre) == 1 else None
        (ids.append(hit) if hit else missing.append(p))
    return ids, missing


async def relocation_plan(
    gx: GraphHandle,
    *,
    website_root: str,
    config: Dict[str, Any],             # relocation_config's config
    journal_path: Optional[str] = None,
    repos: Optional[Dict[str, str]] = None,  # gh_repos' map (None = ask GitHub)
) -> Dict[str, Any]:  # {rows, counts, errors}
    """The worklist as a read (design 3d5ee659 (6)): every page pending relocation with its
    candidate repository and copy, and every page already relocated with its copy."""
    owner = config["retired_repo"].split("/")[0]
    loaded = await load_pages(gx, website_root, journal_path)
    rows = loaded["rows"]
    head = git_head(website_root) or ""
    posts, errors = [], []
    for nid, r in sorted(rows.items(), key=lambda kv: kv[1]["slug"]):
        if not r["pending"]:
            continue
        blob = git_blob(website_root, head, r["rel_path"]) if r["rel_path"] else None
        if blob is None:
            errors.append({"id": nid, "slug": r["slug"], "why": f"no source at HEAD ({r['rel_path'] or 'no path'})"})
            continue
        posts.append({"id": nid, "slug": r["slug"], "text": blob.decode("utf-8")})
    site_repo = github_repo(_git(website_root, "remote", "get-url", "origin") or "") or ""
    repos = repos if repos is not None else gh_repos(owner)
    cands = repo_candidates(posts, repos, owner, exclude=[site_repo.split("/")[-1]] if site_repo else [])
    out = []
    for nid, r in sorted(rows.items(), key=lambda kv: kv[1]["slug"]):
        row = {"id": nid, "slug": r["slug"], "title": r["title"], "standing": r["standing"],
               "paths": r["paths"], "relocated_to": r["relocated_to"],
               "successor": [s["slug"] for s in r["superseded_by"]]}
        if nid in cands:
            c = cands[nid]
            dest = "successor" if r["superseded_by"] else (c["candidate"] or config["retired_repo"])
            row.update({"group": c["group"], "candidate": c["candidate"], "counts": c["counts"], "destination": dest,
                        "copy_dir": None if dest == "successor" else copy_dir(r["slug"], dest, config)})
        out.append(row)
    counts = Counter("relocated" if r["relocated_to"] else "successor" if r.get("destination") == "successor"
                     else "companion" if r.get("candidate") else "dedicated" if r.get("destination") else "other"
                     for r in out)
    return {"rows": out, "counts": dict(counts), "errors": errors}


async def project_copies(
    gx: GraphHandle,
    posts: List[str],                   # The pages to project (slugs or ids)
    *,
    website_root: str,
    into: str,                          # A local clone of their destination
    config: Dict[str, Any],
    journal_path: Optional[str] = None,
    today: Optional[str] = None,        # The retire date of a page not yet retired (ISO; None = today)
    planned: Optional[Dict[str, str]] = None,  # {slug: owner/name[@branch]} -- ruled destinations of pages not yet landed
    quarto: str = "quarto",
) -> Dict[str, Any]:  # {repo, branch, copies: [...], readme, errors}
    """Project each page's copy into the clone (design 3d5ee659 (1)-(4)) and regenerate the
    README's pointer block over every copy the repository holds. Files only. A page not yet landed
    whose destination is ruled (`planned`) is linked at its copy directly, so a copy never waits on
    another's landing to link it; its README entry waits for its own projection."""
    from .comments import load_comments_config
    dest = clone_repo(into)
    if dest.get("error"):
        return {"errors": [{"why": dest["error"]}], "copies": []}
    repo, branch = dest["repo"], dest["branch"]
    site_url = _site_url(website_root)
    if not site_url:
        return {"errors": [{"why": "the site config names no website.site-url"}], "copies": []}
    loaded = await load_pages(gx, website_root, journal_path)
    rows = loaded["rows"]
    ids, missing = _pick(rows, posts)
    errors = [{"ref": m, "why": "no page pending relocation or relocated"} for m in missing]
    head = git_head(website_root) or ""
    site_repo = github_repo(_git(website_root, "remote", "get-url", "origin") or "") or ""
    comments_repo = (load_comments_config(website_root, required=("repo",)).get("config") or {}).get("repo") or ""
    # Every known copy: this run's, every landed one (its RELOCATED_TO URL), every planned one
    known: List[Dict[str, Any]] = []
    for nid, r in rows.items():
        keys = sorted({site_path_key(p) for p in r["paths"]} | {site_path_key("/" + posixpath.dirname(r["rel_path"]))})
        if nid in ids:
            known.append({"id": nid, "keys": keys, "repo": repo, "branch": branch,
                          "dir": copy_dir(r["slug"], repo, config)})
        else:
            for url in r["relocated_to"]:
                c = parse_copy_url(url)
                if c:
                    known.append({"id": nid, "keys": keys, **c})
            if not r["relocated_to"] and not r["superseded_by"] and r["slug"] in (planned or {}):
                prepo, _, pbranch = planned[r["slug"]].partition("@")
                known.append({"id": nid, "keys": keys, "repo": prepo, "branch": pbranch or "main",
                              "dir": copy_dir(r["slug"], prepo, config), "planned": True})
    copies = []
    today = today or date.today().isoformat()
    for nid in ids:
        r = rows[nid]
        if r["superseded_by"]:
            errors.append({"ref": r["slug"], "why": "a public successor supersedes it: it retires to the successor, no copy"})
            continue
        if not r["paths"]:
            errors.append({"ref": r["slug"], "why": "it holds no site_path: the copy's header and redirect need one"})
            continue
        op = loaded["retired"].get(nid) or {}
        commit = op.get("commit") or head
        bundle = source_bundle(website_root, r["rel_path"], commit)
        if bundle.get("error"):
            errors.append({"ref": r["slug"], "why": bundle["error"]})
            continue
        fm = front_matter(bundle["text"])
        text, dropped = strip_chrome(bundle["text"], config["chrome_includes"])
        text = with_lead(text, str(fm.get("description") or ""))
        includes: Dict[str, bytes] = {}
        for inc in include_paths(text):
            src = inc.lstrip("/") if inc.startswith("/") else posixpath.join(posixpath.dirname(r["rel_path"]), inc)
            data = git_blob(website_root, commit, posixpath.normpath(src))
            if data is None:
                errors.append({"ref": r["slug"], "why": f"its include {inc} is not at {commit[:12]}"})
                break
            includes[inc] = data
        else:
            cur_dir = copy_dir(r["slug"], repo, config)
            targets = source_targets([text] + [d.decode("utf-8") for d in includes.values()], quarto=quarto)
            if targets.get("error"):
                errors.append({"ref": r["slug"], "why": targets["error"]})
                continue
            maps = link_map(targets, source_dir="/" + posixpath.dirname(r["rel_path"]) + "/",
                            local=list(bundle["files"]), site_url=site_url,
                            copies=copy_targets({"repo": repo, "branch": branch, "dir": cur_dir},
                                                [k for k in known if k["id"] != nid]))
            body = render_copy(text, bundle["files"], includes, maps, quarto=quarto)
            if body.get("error"):
                errors.append({"ref": r["slug"], "why": body["error"]})
                continue
            threads = (loaded["threads"].get(nid) or {}).get("active") or []
            header = copy_header(
                title=str(fm.get("title") or r["title"]),
                retired_on=loaded["retired_on"].get(nid) or today,
                published=r["published"], page_url=site_url + (r["active"] or r["paths"][0]),
                site_url=site_url, source_url=f"https://github.com/{site_repo}/blob/{commit}/{r['rel_path']}",
                source_label=f"{r['rel_path']}@{commit[:7]}",
                thread_url=f"https://github.com/{comments_repo}/issues/{threads[0]}" if threads and comments_repo else "")
            out_dir = Path(into) / cur_dir
            if out_dir.exists():
                shutil.rmtree(out_dir)
            for rel, data in bundle["files"].items():
                (out_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                (out_dir / rel).write_bytes(data)
            out_dir.mkdir(parents=True, exist_ok=True)
            content = header + "\n" + body["body"].lstrip("\n")
            (out_dir / COPY_FILE).write_text(content, encoding="utf-8")
            copies.append({"id": nid, "slug": r["slug"], "dir": cur_dir, "url": copy_url(repo, branch, cur_dir),
                           "files": len(bundle["files"]), "dropped": dropped,
                           "links": sum(len(v) for v in maps.values()), "title": str(fm.get("title") or r["title"]),
                           "date": r["published"]})
    readme = None
    if copies:
        held = {c["id"]: c for c in copies}
        for k in known:   # landed copies in this repository keep their place in the block
            if k["repo"].lower() == repo.lower() and k["id"] not in held and k["id"] in rows and not k.get("planned"):
                held[k["id"]] = {"dir": k["dir"], "title": rows[k["id"]]["title"], "date": rows[k["id"]]["published"]}
        dedicated = repo.lower() == config["retired_repo"].lower()
        entries = [{"title": c["title"], "path": f"{c['dir']}/{COPY_FILE}", "date": c.get("date") or ""}
                   for c in held.values()]
        path = Path(into) / COPY_FILE
        existing = path.read_text(encoding="utf-8") if path.exists() else None
        host = urlsplit(site_url).netloc
        merged = merge_readme(existing, pointer_block(entries, dedicated=dedicated, site_url=site_url),
                              title="Retired posts" if dedicated else repo.split("/")[1],
                              preamble=(f"Posts retired from [{host}]({site_url}) that have no repository of their "
                                        "own. Each is projected from the post's source and kept as it was written; "
                                        "its old URL redirects here.") if dedicated else "")
        path.write_text(merged, encoding="utf-8")
        readme = {"path": COPY_FILE, "entries": len(entries), "created": existing is None}
    return {"repo": repo, "branch": branch, "copies": copies, "readme": readme, "errors": errors}


async def apply_relocation(
    gx: GraphHandle,
    note: str,                 # The page's id
    url: str,                  # Its copy's URL
    *,
    observed_hash: str = "",   # The copy's content hash as the land check read it
    observed_at: Optional[float] = None,  # When it was read
    title: str = "",           # The copy's display title
    actor: str = "agent:session",
) -> Dict[str, Any]:  # record_relation's result + {reference_id}
    """The copy's web Reference (what was read at landing rides the op) and the page's
    RELOCATED_TO edge to it -- the journaled `relocate` op, replayed as written."""
    from .paths import record_relation
    ref = ReferenceNode(graph=ReferenceNode.WEB, foreign_id=url, foreign_label=COPY_LABEL, title=title or url,
                        observed_hash=observed_hash, observed_at=observed_at)
    await extend_graph(gx.queue, gx.graph_id, [ref.to_graph_node()], [])
    res = await record_relation(gx, DevRelations.RELOCATED_TO, note, ref.id, actor=actor)
    return {**res, "reference_id": ref.id}


async def land_relocations(
    gx: GraphHandle,
    posts: List[str],                    # The pages to land (slugs or ids)
    *,
    website_root: str,
    config: Dict[str, Any],
    into: Optional[str] = None,          # The clone their copies were projected into (None = successor pages only)
    journal_path: Optional[str] = None,
    journal: Optional[Callable[[str, Dict[str, Any]], None]] = None,  # Appends one op (verb, args)
    fetch: Callable[[str], Tuple[int, bytes]] = http_fetch,
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {landed: [...], errors}
    """Land each page's destination (design 3d5ee659 (6)): a page a public successor supersedes
    retires to it; any other page's copy is read back from GitHub and must equal the clone's,
    then the page retires and the `relocate` op lands its copy. Each op is journaled right after
    its write; a refusal leaves nothing behind for that page."""
    from .archive import retire_source
    loaded = await load_pages(gx, website_root, journal_path)
    rows = loaded["rows"]
    ids, missing = _pick(rows, posts)
    errors = [{"ref": m, "why": "no page pending relocation or relocated"} for m in missing]
    dest = clone_repo(into) if into else None
    if dest and dest.get("error"):
        return {"landed": [], "errors": errors + [{"why": dest["error"]}]}
    landed = []
    for nid in ids:
        r = rows[nid]
        if not r["pending"]:
            errors.append({"ref": r["slug"], "why": f"not pending relocation (standing {r['standing']})"})
            continue
        if r["superseded_by"]:
            succ = r["superseded_by"][0]
            if len(r["superseded_by"]) > 1:
                errors.append({"ref": r["slug"], "why": "two public successors supersede it -- name one"})
                continue
            res = await retire_source(gx, nid, reason=f"superseded by its public successor {succ['slug']}",
                                      successor=succ["id"], website_root=website_root, actor=actor)
            if res.get("error"):
                errors.append({"ref": r["slug"], "why": res["error"]})
                continue
            if journal:
                journal(RETIRE_VERB, {"note": res["note_id"], "slug": res["slug"], "path": res["path"],
                                      "commit": res["commit"], "reason": res["reason"],
                                      "successor": res["successor_id"], "actor": actor})
            landed.append({"id": nid, "slug": r["slug"], "destination": "successor", "to": succ["slug"],
                           "source": posixpath.dirname(res["path"])})
            continue
        if dest is None:
            errors.append({"ref": r["slug"], "why": "its copy needs --into (the clone it was projected into)"})
            continue
        cdir = copy_dir(r["slug"], dest["repo"], config)
        local = Path(into) / cdir / COPY_FILE
        if not local.exists():
            errors.append({"ref": r["slug"], "why": f"no projected copy at {local} -- project it first"})
            continue
        status, body = fetch(copy_url(dest["repo"], dest["branch"], cdir, raw=True))
        if status != 200:
            errors.append({"ref": r["slug"], "why": f"its copy is not live on GitHub (HTTP {status}) -- push it first"})
            continue
        if body != local.read_bytes():
            errors.append({"ref": r["slug"], "why": "the copy on GitHub differs from the projection in the clone -- "
                                                    "push the clone's copy (or re-project)"})
            continue
        url = copy_url(dest["repo"], dest["branch"], cdir)
        res = await retire_source(gx, nid, reason=f"relocated to its repo copy {url}", website_root=website_root,
                                  actor=actor)
        if res.get("error"):
            errors.append({"ref": r["slug"], "why": res["error"]})
            continue
        if journal:
            journal(RETIRE_VERB, {"note": res["note_id"], "slug": res["slug"], "path": res["path"],
                                  "commit": res["commit"], "reason": res["reason"], "successor": None,
                                  "actor": actor})
        obs = {"observed_hash": "sha256:" + hashlib.sha256(body).hexdigest(), "observed_at": op_now(),
               "title": r["title"]}
        rel = await apply_relocation(gx, nid, url, actor=actor, **obs)
        if rel.get("error"):
            errors.append({"ref": r["slug"], "why": f"retired, but its RELOCATED_TO refused: {rel['error']}"})
            continue
        if journal:
            journal(RELOCATE_VERB, {"note": nid, "url": url, **obs, "actor": actor})
        landed.append({"id": nid, "slug": r["slug"], "destination": "repo", "to": url,
                       "source": posixpath.dirname(res["path"])})
    return {"landed": landed, "errors": errors}
