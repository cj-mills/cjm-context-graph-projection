"""The post page's AGENT LAYER (design 39c51c15 (7), amendment 23a49667, under the redesign build
8079ae0f).

Three surfaces an agent reads, each a projection, nothing stored:

1. the per-page MARKDOWN -- Quarto's own `.llms.md` (`website.llms-txt` in the site config): its
   converter over the build's final render, so every block the derived-blocks filter placed carries
   through. Quarto rewrites only links ending in `.html`, so after the render the build keeps an
   agent IN the markdown layer: an internal link resolving to a rendered page with a `.llms.md` is
   rewritten to it.
2. LLMS.TXT -- written by the build after the render, over Quarto's flat list, from the graph's
   structure: the site pages, the collection pages, one section per series (its page, then its
   members in their authored order), the tutorials, the notes and the paid work in no series, and
   the project logs under `## Optional` (ruling ff12a19a). Every post is listed once, each link a page's `.llms.md` with
   its description; the intro is the author's copy under the site config's `llms-index` (a missing
   summary refuses -- never words the author did not write), and every link must name a `.llms.md`
   the render produced.
3. JSON-LD per post -- TechArticle for a tutorial, BlogPosting for the other post kinds -- carried
   by the derived-blocks filter into the page head and read back after the render against the plan.

The claims guard (49c0f3c7) holds by construction: no claim reaches these builders (a JSON-LD
object carries only JSONLD_KEYS; llms.txt carries titles and descriptions), the `.llms.md` derive
from the guarded page, and the publish guard scans the `.md` and `.txt` outputs."""

import json
import posixpath
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urljoin, urlsplit

import yaml

from .runtime import GraphHandle

INDEX_KEY = "llms-index"            # The site-config key holding llms.txt's intro copy
INDEX_REQUIRED = ("summary",)       # The copy llms.txt cannot be written without
INDEX_OPTIONAL = ("details",)       # Copy rendered when given
LLMS_SUFFIX = ".llms.md"            # Quarto's per-page markdown, beside the page's .html
LLMS_TXT = "llms.txt"
OPTIONAL_KINDS = ("log",)           # Post kinds listed under `## Optional` (amendment 23a49667 (2), ruling ff12a19a)
SECTION_KINDS = ("tutorial", "notes", "work")   # Post kinds a series section lists, in section order
STANDALONE = {"tutorial": "Tutorials", "notes": "Notes", "work": "Work"}   # The section of a post in no series
EXCLUDED_PAGES = ("index.html", "404.html")   # The site root (llms.txt stands in for it) and Quarto's 404
# The JSON-LD type by post kind (amendment 23a49667 (4)), and every key an object may carry
JSONLD_TYPES = {"tutorial": "TechArticle"}
JSONLD_DEFAULT_TYPE = "BlogPosting"
JSONLD_KEYS = ("@context", "@type", "headline", "description", "url", "author", "datePublished",
               "dateModified", "license", "isPartOf")
_JSONLD_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
# A markdown link's target in Pandoc's gfm (`](target)` / `](target "title")`), a fenced code
# line, and an inline code span (links inside code are text, never rewritten)
_LINK_RE = re.compile(r'\]\((<[^>]*>|[^)\s]+)((?:\s+"[^"]*")?)\)')
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_CODE_SPAN_RE = re.compile(r"(`+)(?:.+?)\1")
_SCHEME_RE = re.compile(r"^[A-Za-z][\w+.-]*:|^//")
_MD_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")   # a markdown link or image, its text kept


def load_index_copy(
    website_root: str,  # The site project root
) -> Dict[str, Any]:  # {copy: {field: text}, errors}
    """llms.txt's intro copy from the site config: a missing key or summary refuses (the build
    never falls back to words the author did not write)."""
    path = Path(website_root) / "_quarto.yml"
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        return {"copy": {}, "errors": [{"kind": "llms-index-copy", "path": str(path), "why": f"unreadable: {e}"}]}
    copy = cfg.get(INDEX_KEY) or {}
    missing = [f for f in INDEX_REQUIRED if not str(copy.get(f) or "").strip()]
    if missing:
        return {"copy": {}, "errors": [{"kind": "llms-index-copy", "path": str(path), "missing": missing,
                                        "why": f"the site config's `{INDEX_KEY}` lacks copy llms.txt renders"}]}
    return {"copy": {f: " ".join(str(copy.get(f) or "").split()) for f in INDEX_REQUIRED + INDEX_OPTIONAL},
            "errors": []}


def llms_path(
    source: str,  # A page's source, relative to the root ("posts/x/index.md", "series/notes/y.qmd")
) -> str:  # Its markdown copy, relative to the output dir ("posts/x/index.llms.md")
    return posixpath.splitext(source)[0] + LLMS_SUFFIX


def directory_author(
    website_root: str,         # The site project root
    source: str,               # The page's source, relative to the root
    metadata: Dict[str, Any],  # The page's front matter (its Note's metadata)
) -> List[str]:  # The author names the page states, in order ([] = none)
    """The page's author as Quarto merges it: its own front matter, else the nearest directory's
    `_metadata.yml` up to the project root."""
    def names(value: Any) -> List[str]:
        items = value if isinstance(value, list) else [value]
        out = []
        for v in items:
            n = v.get("name") if isinstance(v, dict) else v
            if isinstance(n, dict):
                n = n.get("literal") or " ".join(str(n.get(k) or "") for k in ("given", "family")).strip()
            if str(n or "").strip():
                out.append(str(n).strip())
        return out
    if metadata.get("author"):
        return names(metadata["author"])
    root = Path(website_root).resolve()
    d = (root / source).parent
    while True:
        meta = d / "_metadata.yml"
        if meta.is_file():
            try:
                got = (yaml.safe_load(meta.read_text(encoding="utf-8")) or {}).get("author")
            except yaml.YAMLError:
                got = None
            if got:
                return names(got)
        if d == root or root not in d.parents:
            return []
        d = d.parent


def post_jsonld(
    kind: str,                          # The post's deliverable kind
    title: str,
    description: str,
    url: str,                           # The page's absolute URL (site URL + its active site_path)
    dates: Dict[str, str],              # postpage.post_dates' result ({published, updated} ISO days)
    authors: List[str],                 # directory_author's names
    license_url: str,                   # The content license's URL ("" = none)
    series: List[Dict[str, str]],       # [{title, url}] -- every series the post belongs to
    site_url: str,                      # The site URL (an author's url)
) -> Dict[str, Any]:  # The post's JSON-LD object (keys in JSONLD_KEYS order, empties dropped)
    """A post's structured data (39c51c15 (7), amendment 23a49667 (4)): a draft has no
    datePublished, and dateModified shows only beside a publication."""
    obj: Dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": JSONLD_TYPES.get(kind, JSONLD_DEFAULT_TYPE),
        "headline": plain(title),
        "description": plain(description),
        "url": url,
        "author": [{"@type": "Person", "name": n, "url": site_url.rstrip("/") + "/"} for n in authors],
        "datePublished": dates.get("published") or "",
        "dateModified": (dates.get("updated") or "") if dates.get("published") else "",
        "license": license_url,
        "isPartOf": [{"@type": "CreativeWorkSeries", "name": s["title"], "url": s["url"]} for s in series],
    }
    if len(obj["author"]) == 1:
        obj["author"] = obj["author"][0]
    return {k: obj[k] for k in JSONLD_KEYS if obj.get(k) not in ("", [], None)}


def jsonld_script(
    obj: Dict[str, Any],  # post_jsonld's object
) -> str:  # The head element carrying it ('</' escaped, so no value can close the script)
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return f'<script type="application/ld+json">{payload}</script>'


def read_jsonld(
    html: str,  # A rendered page
) -> List[Any]:  # Every JSON-LD object the page carries, parsed (an unparseable one as None)
    out = []
    for m in _JSONLD_RE.finditer(html):
        try:
            out.append(json.loads(m.group(1)))
        except json.JSONDecodeError:
            out.append(None)
    return out


def check_jsonld(
    output_dir: str,       # The profile's output dir
    plan: Dict[str, Any],  # derivedblocks.derived_plan's report
) -> Dict[str, Any]:  # {checked, errors}
    """After the render: every planned post page carries exactly one JSON-LD object, equal to the
    plan's -- the filter placed it and nothing else did."""
    out = Path(output_dir)
    checked = 0
    errors: List[Dict[str, Any]] = []
    for src, entry in plan["posts"].items():
        head = entry.get("head") or ""
        page = out / (posixpath.splitext(src)[0] + ".html")
        if not head or not page.exists():
            continue
        checked += 1
        want = read_jsonld(head)
        got = read_jsonld(page.read_text(encoding="utf-8"))
        if len(got) != 1:
            errors.append({"kind": "jsonld-count", "source": src, "count": len(got),
                           "why": "a post page must carry exactly one JSON-LD object"})
        elif got != want:
            errors.append({"kind": "jsonld-mismatch", "source": src,
                           "why": "the page's JSON-LD differs from the plan"})
    return {"checked": checked, "errors": errors}


def plain(
    text: str,  # A title or description as its source states it (markdown allowed)
) -> str:  # One line of plain text: a markdown link keeps its text, whitespace collapses
    """A description in llms.txt or JSON-LD is text: the page's link is the only link there, and a
    relative link inside a description would resolve against the wrong page."""
    return " ".join(_MD_LINK_RE.sub(r"\1", text or "").split())


def _line(title: str, url: str, description: str = "") -> str:
    text = plain(title).replace("[", "(").replace("]", ")")
    desc = plain(description)
    return f"- [{text}]({url})" + (f": {desc}" if desc else "")


def build_lines(
    licenses: List[Dict[str, Any]],  # postpage.post_licenses' results of the listed posts
    optional: bool,                  # Whether the file has an Optional section
) -> List[str]:  # The build's paragraphs under the author's intro (amendment 465ab923 (2))
    """What the build knows and the author never types: how the links work, the licenses from the
    license facts (a relicensing changes them by supersession), and the key to the Optional section."""
    out = ["Each link is a markdown copy of its page; the page itself is at the same address with "
           "`.html` in place of `.llms.md`."]
    if licenses:
        parts = []
        for key, what in (("content", "text"), ("code", "code samples")):
            vals = {tuple(lic[key]) for lic in licenses if key in lic}
            if len(vals) == 1:
                name, url = vals.pop()
                parts.append(f"{what}: {name} ({url})")
            else:
                parts.append(f"{what}: varies by page (each page's Reuse section states its own)")
        out.append("Licenses — " + "; ".join(parts) + ".")
    if optional:
        out.append("The Optional section lists project logs; it can be skipped.")
    return out


def llms_index(
    site_title: str,                     # The profile's website title
    site_url: str,                       # The site URL
    copy: Dict[str, str],                # load_index_copy's copy
    site_pages: List[Dict[str, str]],    # [{title, source, description}] -- the site's own pages
    collections: List[Dict[str, str]],  # [{title, source, description}] -- the topic pages
    series: List[Dict[str, Any]],        # [{title, source, description, updated, members: [note id]}]
    posts: Dict[str, Dict[str, Any]],    # {note id: {title, source, description, kind, date}} -- the rendered posts
    licenses: Optional[List[Dict[str, Any]]] = None,  # postpage.post_licenses' results of the listed posts
) -> Dict[str, Any]:  # {text, links: [.llms.md paths relative to the output dir], counts}
    """llms.txt (amendment 23a49667 (2), 465ab923): the author's intro, the build's own lines,
    then the site pages and the collections, a section per series headed by its kind (newest-updated
    first, in the kind order), the posts in no series by kind (Tutorials, Notes, Work), and
    `## Optional` for the project logs. Each post is listed once: in the first series that lists
    it, else in its kind's section."""
    base = site_url.rstrip("/") + "/"
    links: List[str] = []

    def entry(item: Dict[str, Any]) -> str:
        path = llms_path(item["source"])
        links.append(path)
        return _line(item["title"], base + path, item.get("description") or "")

    lines = [f"# {site_title}", "", f"> {copy['summary']}", ""]
    if copy.get("details"):
        lines += [copy["details"], ""]
    sections: List[tuple] = []
    if site_pages:
        sections.append(("Site", [entry(p) for p in sorted(site_pages, key=lambda p: p["title"])]))
    if collections:
        sections.append(("Collections", [entry(c) for c in sorted(collections, key=lambda c: c["title"])]))
    placed: set = set()
    optional: List[str] = []

    def series_kind(s: Dict[str, Any]) -> Optional[str]:
        kinds = [posts[m]["kind"] for m in s["members"] if m in posts]
        for k in SECTION_KINDS:
            if k in kinds:
                return k
        return None
    ordered = sorted(series, key=lambda s: (s["title"],))
    ordered = sorted(ordered, key=lambda s: s.get("updated") or "", reverse=True)
    for kind in SECTION_KINDS:
        for s in ordered:
            if series_kind(s) != kind:
                continue
            members = [m for m in s["members"] if m in posts and m not in placed
                       and posts[m]["kind"] not in OPTIONAL_KINDS]
            if not members:
                continue
            placed.update(members)
            # the heading names the kind, so guidance scoped to a kind reaches every page of it (465ab923 (3))
            sections.append((f"{STANDALONE[kind]}: {s['title']}", [entry(s)] + [entry(posts[m]) for m in members]))
    by_date = sorted(posts, key=lambda n: (posts[n]["title"],))
    by_date = sorted(by_date, key=lambda n: posts[n].get("date") or "", reverse=True)
    for kind in SECTION_KINDS:
        rows = [entry(posts[n]) for n in by_date if n not in placed and posts[n]["kind"] == kind]
        placed.update(n for n in by_date if posts[n]["kind"] == kind)
        if rows:
            sections.append((STANDALONE[kind], rows))
    # The project logs: their series' pages first (in series order), then every log
    for s in ordered:
        if series_kind(s) is None and any(m in posts for m in s["members"]):
            optional.append(entry(s))
    optional += [entry(posts[n]) for n in by_date if n not in placed and posts[n]["kind"] in OPTIONAL_KINDS]
    if optional:
        sections.append(("Optional", optional))
    lines += [ln for b in build_lines(licenses or [], bool(optional)) for ln in (b, "")]
    for head, rows in sections:
        lines += [f"## {head}", ""] + rows + [""]
    counts = {"llms_sections": len(sections), "llms_links": len(links)}
    return {"text": "\n".join(lines), "links": links, "counts": counts}


async def agent_plan(
    gx: GraphHandle,
    website_root: str,                      # The site project root
    site: Dict[str, Any],                   # The profile's `website` config (site-url, title)
    src_of: Dict[str, str],                 # {note id: source} -- every Note the profile renders
    notes: Dict[str, Any],                  # {note id: Note node}
    types: Dict[str, Dict[str, Any]],       # note_types' map
    posts: Dict[str, Dict[str, Any]],       # {note id: {title, href, kind, date}} -- the rendered posts
    licenses: Dict[str, Dict[str, Any]],    # {note id: post_licenses' result}
    facts: Dict[str, Dict[str, str]],       # postpage.header_facts' map
    planned_pages: List[Dict[str, Any]],    # page_plan's pages
) -> Dict[str, Any]:  # {heads: {note id: head element}, llms: llms_index's result or None, errors}
    """The agent layer's plan: each licensed post's JSON-LD and llms.txt. A page's title and
    description are what it states (its front matter), else its Note's."""
    from cjm_dev_graph_schema.vocab import DevNodeKinds
    from . import factlayer as F
    from .postpage import post_dates
    errors: List[Dict[str, Any]] = []
    site_url = str(site.get("site-url") or "").strip().rstrip("/")
    if not site_url:
        return {"heads": {}, "llms": None,
                "errors": [{"kind": "site-url", "why": "the site config names no website.site-url "
                                                       "(the JSON-LD urls and llms.txt's links need it)"}]}

    def stated(nid: str, key: str) -> str:
        # The page's front matter first: a born Note's own title and description are its working
        # ones, while the page states what its front matter says
        from .site import _front_matter
        front = _front_matter(str(F.prop(notes[nid], "frontmatter_raw") or ""))
        md = F.prop(notes[nid], "metadata") or {}
        return str(front.get(key) or md.get(key) or F.prop(notes[nid], key) or "")
    in_series: Dict[str, List[Dict[str, str]]] = {}
    for page in planned_pages:
        if page["kind"] == DevNodeKinds.SERIES:
            for m in page["listed"]:
                in_series.setdefault(m, []).append({"title": page["title"], "url": site_url + page["href"]})
    heads: Dict[str, str] = {}
    dates: Dict[str, Dict[str, str]] = {}
    for nid in sorted(licenses):
        t = types.get(nid) or {}
        md = F.prop(notes[nid], "metadata") or {}
        dates[nid] = post_dates(t.get("origin", ""), md, facts.get(nid, {}))
        obj = post_jsonld(t.get("kind", ""), stated(nid, "title"), stated(nid, "description"),
                          site_url + posts[nid]["href"], dates[nid],
                          directory_author(website_root, src_of[nid], md),
                          licenses[nid]["content"][1], in_series.get(nid, []), site_url)
        heads[nid] = jsonld_script(obj)
    copy = load_index_copy(website_root)
    errors += copy["errors"]
    if copy["errors"]:
        return {"heads": heads, "llms": None, "errors": errors}
    page_nodes = await F.load_nodes(gx, sorted({p["subject"] for p in planned_pages}))

    def page_item(p: Dict[str, Any]) -> Dict[str, Any]:
        return {"title": p["title"], "source": p["source"], "updated": p.get("updated") or "",
                "description": str(F.prop(page_nodes.get(p["subject"]), "description") or ""),
                "members": list(p["listed"])}
    site_pages = [{"title": stated(n, "title"), "source": s, "description": stated(n, "description")}
                  for n, s in sorted(src_of.items(), key=lambda x: x[1])
                  if (types.get(n) or {}).get("kind") == "site"
                  and posixpath.splitext(s)[0] + ".html" not in EXCLUDED_PAGES]
    index = llms_index(
        str(site.get("title") or ""), site_url, copy["copy"], site_pages,
        [page_item(p) for p in planned_pages if p["kind"] != DevNodeKinds.SERIES],
        [page_item(p) for p in planned_pages if p["kind"] == DevNodeKinds.SERIES],
        {n: {"title": stated(n, "title"), "source": src_of[n], "description": stated(n, "description"),
             "kind": posts[n]["kind"], "date": (dates.get(n) or {}).get("published") or ""}
         for n in posts},
        licenses=[licenses[n] for n in sorted(posts) if n in licenses])
    return {"heads": heads, "llms": index, "errors": errors}


def write_llms_txt(
    output_dir: str,          # The profile's output dir
    index: Dict[str, Any],    # llms_index's result
) -> Dict[str, Any]:  # {written, errors}
    """Write llms.txt over Quarto's flat list -- only when every link names a `.llms.md` the
    render produced (a missing one means `website.llms-txt` is off, or a page did not render)."""
    out = Path(output_dir)
    missing = sorted({p for p in index["links"] if not (out / p).is_file()})
    if missing:
        return {"written": False, "errors": [{"kind": "llms-missing", "count": len(missing), "detail": missing[:10],
                                              "why": "llms.txt would link a page with no .llms.md "
                                                     "(is website.llms-txt on in the site config?)"}]}
    dest = out / LLMS_TXT
    text = index["text"].rstrip("\n") + "\n"
    if dest.exists() and dest.read_text(encoding="utf-8") == text:
        return {"written": False, "errors": []}
    dest.write_text(text, encoding="utf-8")
    return {"written": True, "errors": []}


def _page_target(
    out: Path,       # The output dir
    md_dir: str,     # The markdown file's dir, relative to the output dir ("" = the root)
    target: str,     # A link target, fragment and query stripped
) -> Optional[str]:  # The target rewritten to the page's .llms.md, or None (not a rendered page)
    """The target resolved as a browser resolves it against the page's URL (a `..` never climbs
    above the site root), then Quarto's alias rules (site.output_href): a trailing slash or an
    extension-less path names the directory's index, a `.html` path the page itself."""
    if not target or _SCHEME_RE.match(target) or target.endswith(LLMS_SUFFIX):
        return None
    absolute = target.startswith("/")
    path = urlsplit(urljoin("http://site/" + (md_dir + "/" if md_dir else ""), target)).path
    rel = path.strip("/")
    if path.endswith("/") or rel == "":
        page = posixpath.join(rel, "index") if rel else "index"
    elif rel.endswith(".html"):
        page = rel[:-len(".html")]
    elif posixpath.splitext(rel)[1] == "":
        page = posixpath.join(rel, "index")
    else:
        return None
    if not (out / unquote(page + LLMS_SUFFIX)).is_file():
        return None
    if absolute:
        return "/" + page + LLMS_SUFFIX
    return posixpath.relpath(page + LLMS_SUFFIX, md_dir or ".")


def rewrite_markdown_links(
    text: str,                      # A .llms.md file's text
    resolve: Any,                   # (target) -> rewritten target or None
) -> tuple:  # (text, rewritten count)
    """Rewrite each link target `resolve` maps, outside fenced code and inline code spans."""
    out: List[str] = []
    fence: Optional[str] = None
    count = 0

    def link(m: "re.Match") -> str:
        nonlocal count
        raw = m.group(1)
        inner = raw[1:-1] if raw.startswith("<") else raw
        cut = min([i for i in (inner.find("#"), inner.find("?")) if i >= 0] or [len(inner)])
        new = resolve(inner[:cut])
        if new is None:
            return m.group(0)
        count += 1
        target = new + inner[cut:]
        return f"]({'<' + target + '>' if raw.startswith('<') else target}{m.group(2)})"
    for line in text.split("\n"):
        f = _FENCE_RE.match(line)
        if fence is not None:
            if f and f.group(1)[0] == fence[0] and len(f.group(1)) >= len(fence) and not line.strip()[len(f.group(1)):].strip():
                fence = None
            out.append(line)
            continue
        if f:
            fence = f.group(1)
            out.append(line)
            continue
        parts, pos = [], 0
        for c in _CODE_SPAN_RE.finditer(line):
            parts.append(_LINK_RE.sub(link, line[pos:c.start()]))
            parts.append(c.group(0))
            pos = c.end()
        parts.append(_LINK_RE.sub(link, line[pos:]))
        out.append("".join(parts))
    return "\n".join(out), count


def rewrite_llms_links(
    output_dir: str,  # The profile's output dir
) -> Dict[str, Any]:  # {files, rewritten, changed}
    """Keep an agent in the markdown layer (amendment 23a49667 (3)): every internal link in a
    `.llms.md` that resolves to a rendered page with a `.llms.md` is rewritten to it. Idempotent."""
    out = Path(output_dir)
    files = rewritten = changed = 0
    for p in sorted(out.rglob("*" + LLMS_SUFFIX)):
        if "site_libs" in p.parts:
            continue
        files += 1
        md_dir = p.parent.relative_to(out).as_posix()
        md_dir = "" if md_dir == "." else md_dir
        text = p.read_text(encoding="utf-8")
        new, n = rewrite_markdown_links(text, lambda t: _page_target(out, md_dir, t))
        rewritten += n
        if new != text:
            p.write_text(new, encoding="utf-8")
            changed += 1
    return {"files": files, "rewritten": rewritten, "changed": changed}
