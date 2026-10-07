"""The Tutorials page: a Lens whose view layout is `coverage-matrix` projects the task x stage
grid, the learning paths, the tutorials by task and the off-grid list (design 7f200ecb under
the redesign build 8079ae0f; the page spec 903bc108 (1); the coverage model de808eae (1)).

The Lens owns the page: its selection is the population (every tutorial-kind deliverable), its
site_path the location, its title / description / date the page's. Everything else is derived
at build and nothing is stored: the grid from the `teaches_*` facts (coverage.project_matrix),
the learning paths from the projected collection pages whose members are all tutorials, the
hardware marks from the VERIFIED_ON edges. Under the public profile an empty cell stays blank
and only STATED verifications render (60681b4f: an attribution never reads as a statement);
staging adds the timeline-attributed marks and the gap list. Anchors are vocabulary keys,
never node ids (13753cbf (6)). Any refusal of the matrix, or a collection mixing tutorials with
other kinds, refuses the page (6752db0a (9))."""

import posixpath
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from cjm_dev_graph_schema import predicates as P

from . import factlayer as F
from .runtime import GraphHandle

LAYOUT = "coverage-matrix"   # The Lens view layout this page projects
TUTORIAL_KIND = "tutorial"
# The learning paths are the projected listing (sitelisting.py, design 0efb5497 (2)), newest
# update first; the Tutorials structure itself waits on 78e5b21b
PATHS_SORT = ["date-modified desc"]
# The grid collapses to the per-task lists on narrow screens (903bc108 (1)). Quarto's bundle sets
# `.table a { word-break: break-word }`, which with auto column widths broke the grid's links
# mid-word (finding 15e7b315): the page class keeps link text whole and sizes the task column to
# its names, ready to move into the design system's stylesheet (39c51c15 (8))
STYLE = ("```{=html}\n<style>\n"
         ".tutorials-matrix table { font-size: 0.9rem; }\n"
         ".tutorials-matrix .table a { word-break: normal; overflow-wrap: normal; }\n"
         ".tutorials-matrix th, .tutorials-matrix td { text-align: center; }\n"
         ".tutorials-matrix th:first-child, .tutorials-matrix td:first-child { text-align: left; white-space: nowrap; }\n"
         ".tutorials-matrix .covered-mark { color: var(--bs-secondary-color); text-decoration: none; }\n"
         "@media (max-width: 991.98px) { .tutorials-matrix { display: none; } }\n"
         "</style>\n```\n")


def anchor(*keys: str) -> str:
    """A section anchor from vocabulary keys (never a node id)."""
    return "-".join(re.sub(r"[^a-z0-9-]+", "-", k.lower()).strip("-") for k in keys)


def _md_text(s: Any) -> str:
    """One line of inline markdown: whitespace collapsed, link-breaking brackets escaped."""
    return re.sub(r"\s+", " ", str(s or "")).strip().replace("[", r"\[").replace("]", r"\]")


_LINK = re.compile(r"(!?\[[^\]]*\]\()([^)\s]+)(\))")   # an inline link or image: its target the second group


def _md_inline(
    s: Any,     # Authored inline markdown (a post's description)
    base: str,  # The authoring post's href from the page that renders the line
) -> str:  # One line of it, its links live: whitespace collapsed, every relative target rebased
    """A description is the author's markdown, never plain text: its links stay links. A relative
    target is relative to the POST, so it is rebased through the post's href onto the page the
    line renders on (a bare fragment names the post's own anchor); a site-absolute path, a URL
    with a scheme and a protocol-relative URL stand as written."""
    def rebase(m: "re.Match") -> str:
        target = m.group(2)
        if target.startswith(("/", "//")) or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
            return m.group(0)
        path, frag = (target.split("#", 1) + [""])[:2]
        if not path:   # the post's own anchor
            return f"{m.group(1)}{base.split('#')[0]}#{frag}{m.group(3)}"
        new = posixpath.normpath(posixpath.join(posixpath.dirname(base), path))
        new += "/" if path.endswith("/") and not new.endswith("/") else ""
        return f"{m.group(1)}{new}{'#' + frag if frag else ''}{m.group(3)}"
    return _LINK.sub(rebase, re.sub(r"\s+", " ", str(s or "")).strip())


def render_body(
    matrix: Dict[str, Any],                  # coverage.project_matrix's result over the listed population
    items: Dict[str, Dict[str, Any]],        # {note id: {title, href, date, description, marks}}
    profile: str,                            # public | staging
    paths: str = "",                         # The learning paths' listing markup (sitelisting.render_listing)
) -> str:  # The page body (markdown)
    """The grid, the learning paths, the tutorials by task, the off-grid list, and (staging)
    the gaps. Pure: every value comes from the arguments."""
    tasks, stages, cells = matrix["tasks"], matrix["stages"], matrix["cells"]
    names = {t["key"]: t["name"] for t in tasks} | {t["key"]: t["name"] for t in matrix["off_grid_tasks"]}
    snames = {s["key"]: s["name"] for s in stages}
    covered = {(c["task"], c["stage"]): c["by"] for c in matrix["covered"]}
    out = [STYLE, "::: {.tutorials-matrix}",
           "| Task | " + " | ".join(s["name"] for s in stages) + " |",
           "|:--|" + ":-:|" * len(stages)]
    for t in tasks:
        # A row whose cells are all empty has no section below, so its name links nowhere
        has_section = any(cells.get(f"{t['key']}|{s['key']}") for s in stages)
        row = [f"[{t['name']}](#{anchor(t['key'])})" if has_section else t["name"]]
        for s in stages:
            ids = cells.get(f"{t['key']}|{s['key']}")
            if ids:
                row.append(f"[{len(ids)}](#{anchor(t['key'], s['key'])})")
            elif (t["key"], s["key"]) in covered:
                # A quiet mark in the cell; the cover is named once, under the grid (39c51c15 (8))
                by = covered[(t["key"], s["key"])][0]
                row.append(f"[[↓]{{aria-hidden=\"true\"}}[covered by {names[by]}]{{.visually-hidden}}]"
                           f"(#{anchor(by, s['key'])}){{.covered-mark}}")
            else:
                row.append("")
        out.append("| " + " | ".join(row) + " |")
    covers = sorted({names[b[0]] for b in covered.values()})
    if covers:
        out += ["", ": ↓ = covered by the " + " / ".join(covers) + " tutorials of that stage"]
    out += [":::", "", "## Learning paths", ""] + ([paths.rstrip("\n"), ""] if paths.strip() else []) + ["## By task", ""]

    def lines(ids: List[str]) -> List[str]:
        rows = sorted(ids, key=lambda i: (items[i].get("date") or "", items[i].get("title") or ""), reverse=True)
        res = []
        for i in rows:
            it = items[i]
            line = f"- [{_md_text(it['title'])}]({it['href']})"
            if it.get("date"):
                line += f" · {it['date']}"
            if it.get("description"):
                line += f" — {_md_inline(it['description'], it['href'])}"
            if it.get("marks"):
                line += f" _({'; '.join(it['marks'])})_"
            res.append(line)
        return res

    for t in tasks:
        filled = [s for s in stages if cells.get(f"{t['key']}|{s['key']}")]
        if not filled:
            continue
        out += [f"### {t['name']} {{#{anchor(t['key'])}}}", ""]
        for s in filled:
            out += [f"#### {s['name']} {{#{anchor(t['key'], s['key'])}}}", ""]
            out += lines(cells[f"{t['key']}|{s['key']}"]) + [""]
    other = [i for ids in matrix["off_grid"].values() for i in ids]
    if other:
        out += ["## Other tutorials {#other}", ""] + lines(sorted(set(other))) + [""]
    if profile == "staging" and matrix["gaps"]:
        out += ["## Gaps {#gaps}", "", "_Staging only: the cells no tutorial fills yet._", ""]
        out += [f"- {names[g['task']]} × {snames[g['stage']]}" for g in matrix["gaps"]] + [""]
    return "\n".join(out).rstrip("\n") + "\n"


def learning_paths(
    planned: List[Dict[str, Any]],           # page_plan's other planned pages (Series + Lens)
    types: Dict[str, Dict[str, Any]],        # note_types' map
    page_src: str,                           # The Tutorials page's source (relative to the root)
) -> Dict[str, Any]:  # {contents, pages: the planned pages, same order, errors}
    """The collection pages whose listed members are ALL tutorials; a collection mixing
    tutorials with other kinds refuses (a wrong exclusion hides content, 6752db0a (9)). A page
    with its own layout (the category listing, design ce17606b (3)) is no collection."""
    contents, pages, errors = [], [], []
    for p in sorted(planned, key=lambda p: p["source"]):
        if p.get("layout"):
            continue
        kinds = {(types.get(i) or {}).get("kind") for i in p["listed"]}
        if TUTORIAL_KIND not in kinds:
            continue
        if kinds != {TUTORIAL_KIND}:
            errors.append({"kind": "learning-path-mixed", "subject": p["subject"], "source": p["source"],
                           "kinds": sorted(str(k) for k in kinds),
                           "why": "a collection mixes tutorials with other kinds; the build never guesses "
                                  "whether it is a learning path"})
            continue
        contents.append(posixpath.relpath(p["source"], posixpath.dirname(page_src) or "."))
        pages.append(p)
    return {"contents": contents, "pages": pages, "errors": errors}


async def hardware_marks(
    gx: GraphHandle,
    profile: str,  # public | staging
) -> Dict[str, List[str]]:  # {deliverable id: ["Tested on <device> (<os>)", ...]}
    """The verification marks per deliverable: STATED evidence under every profile, the
    timeline-attributed evidence only under staging (60681b4f)."""
    from .coverage import load_hardware, load_verifications
    devices = {d["id"]: d for d in (await load_hardware(gx)).values()}
    out: Dict[str, List[str]] = {}
    for e in sorted(await load_verifications(gx), key=lambda e: str(e["id"])):
        props = e.get("properties") or {}
        basis = props.get("basis") or "stated"
        if basis != "stated" and profile != "staging":
            continue
        dev = devices.get(str(e["target_id"]))
        if dev is None:
            continue
        mark = f"Tested on {dev.get('name')}" + (f", {props['os']}" if props.get("os") else "")
        if basis != "stated":
            mark += f" [{basis}]"
        out.setdefault(str(e["source_id"]), []).append(mark)
    return out


async def plan_matrix_page(
    gx: GraphHandle,
    node: Any,                               # The Lens node
    members: List[Any],                      # Its selection's Note nodes
    page: Dict[str, Any],                    # {source, href}
    root: Path,                              # The site project root (resolved)
    profile: str,                            # public | staging
    states: Dict[str, List[str]],
    types: Dict[str, Dict[str, Any]],
    drafts: Optional[Path],
    planned: List[Dict[str, Any]],           # The other planned pages (the learning-path candidates)
    listing_href: str = "",                  # The category listing's page path ("" = none: chips are labels)
    category_pages: Optional[Dict[str, str]] = None,  # chip -> its category page's path (design a62f2499 (7))
    chips: Optional[Dict[str, Any]] = None,  # categories.load_post_categories' result (the filter's kinds)
) -> Dict[str, Any]:  # {page: planned entry} | {errors}
    """Plan the Tutorials page: the listed population under the profile, the matrix over it,
    the learning paths, the page text."""
    from .coverage import load_coverage_facts, load_vocab, project_matrix
    from .sitelisting import chip_hrefs, listing_kinds, page_item, render_listing, sort_rows, title_html
    from .sitepages import GENERATED, _listed, member_updated
    import yaml
    sid, key, src = str(F.nid(node)), str(F.prop(node, "key") or ""), page["source"]
    listed = _listed(members, src, root, profile, states, types, drafts, sid)
    errors = list(listed["errors"])
    by_id = {str(F.nid(n)): n for n in members}
    kinds = {i: (types.get(i) or {}).get("kind") for i in listed["ids"]}
    stray = sorted(i for i, k in kinds.items() if k != TUTORIAL_KIND)
    if stray:
        errors.append({"kind": "matrix-population", "subject": sid, "members": stray,
                       "why": "the coverage-matrix Lens selected a non-tutorial"})
    vocab = await load_vocab(gx)
    from .site import stated
    tutorials = {i: {"title": stated(by_id[i], "title"), "slug": F.prop(by_id[i], "slug") or ""}
                 for i in listed["ids"]}
    matrix = project_matrix(vocab[P.ENTITY_TASK], vocab[P.ENTITY_STAGE], tutorials,
                            await load_coverage_facts(gx))
    for r in matrix["refusals"]:
        errors.append({"kind": "matrix-refusal", "subject": sid, "member": r["id"], "reason": r["reason"],
                       "why": f"the matrix refuses a tutorial ({r['reason']}: {r['detail']})"})
    paths = learning_paths(planned, types, src)
    errors += paths["errors"]
    # the learning paths in the order the page's listing shows them (the home page's map, 5c3c2662 (5))
    ordered = sort_rows([page_item(p, src) for p in paths["pages"]], PATHS_SORT)
    if ordered.get("error"):
        errors.append({"kind": "lens-hubs", "subject": sid, "key": key, "why": ordered["error"]})
    if errors:
        return {"errors": errors}
    marks = await hardware_marks(gx, profile)
    items = {}
    for i, href in zip(listed["ids"], listed["contents"]):
        n = by_id[i]
        d = member_updated(n)
        md = F.prop(n, "metadata") or {}
        from .sitepages import parse_date
        born = parse_date(md.get("date"))
        items[i] = {"title": stated(n, "title") or F.prop(n, "slug"), "href": href,
                    "date": born.isoformat() if born else (d.isoformat() if d else ""),
                    "description": stated(n, "description"), "marks": marks.get(i, [])}
    front: Dict[str, Any] = {"title": F.prop(node, "title") or key}
    for k in ("description", "date"):
        if F.prop(node, k):
            front[k] = F.prop(node, k)
    if listed["updated"]:
        front["date-modified"] = listed["updated"]
    front["page-layout"] = "full"
    # The learning paths: the projected listing, their chips linking like every listing's (a62f2499 (7))
    paths_items = ordered["rows"]
    chips = chips or {"kinds": {}, "rank": {}}
    markup = render_listing(paths_items, src, kinds=listing_kinds(paths_items, chips["kinds"], chips["rank"]),
                            hrefs=chip_hrefs(sorted({c for it in paths_items for c in it["categories"]}),
                                             listing_href, category_pages), noun="paths",
                            titles=title_html([it["title"] for it in paths_items]))
    head = yaml.safe_dump(front, sort_keys=False, allow_unicode=True, width=10_000)
    text = f"---\n{GENERATED}\n{head}---\n\n" + render_body(matrix, items, profile, paths=markup)
    return {"page": {"source": src, "kind": "Lens", "key": key, "subject": sid,
                     "members": len(listed["contents"]), "updated": listed["updated"],
                     "listed": listed["ids"], "href": page["href"], "title": front["title"],
                     "layout": LAYOUT, "paths": len(paths["contents"]),
                     "hubs": [{"title": it["title"], "source": it["source"]} for it in paths_items], "text": text}}
