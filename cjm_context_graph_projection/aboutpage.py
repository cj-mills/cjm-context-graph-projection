"""The ABOUT PAGE: the author's identity, background and how the site is made -- never an offer
(design ff0c6338, amendment 2364f215, under the redesign build 8079ae0f; work item df6b8ae6).

A Lens whose view layout is `about` owns the page: its site_path the location (/about.html) and
its selection the BACKGROUND -- one `subgraph` clause naming one born Note of the page-content
type. Everything around the background is read from the site config's one statement of it, and
nothing is stored:

1. the OPENING LINE is the site's one sentence (`site-summary`, filled from `site-author`), the
   identity the home page and llms.txt read too (ff0c6338 (1));
2. the BACKGROUND is the born Note's prose, rendered on the public profile only under its approval
   (authoring.note_approval: a single active `published` bound to its content hash, the gate
   emit_post enforces); staging renders the draft under a marker, the review surface (68267119);
   the Note is never emitted on its own and holds no site_path (2364f215);
3. HOW TO READ THIS SITE is the site's reading guide (`reading-guide`), which llms.txt's intro
   reads too (ff0c6338 (4));
4. the LINKS are the site's contact links (`site-links`, ff0c6338 (6)), in the marquee's footer;
   the portrait is the site author's (`site-author.image`);
5. the WORK-WITH-ME hand-off renders only once its page is published (postpage.pitch_target, the
   author strip's pitch rule; ff0c6338 (5));
6. the JSON-LD is a ProfilePage whose Person is the site author: name and jobTitle from
   `site-author`, sameAs from the web links of `site-links`, alumniOf from `site-author.alumni-of`
   (ff0c6338 (8)).

The page lists no post, so no post names it as a collection; llms.txt lists it among the site
pages with its Lens's description."""

import posixpath
from typing import Any, Dict, List, Optional

import yaml
from cjm_dev_graph_schema.vocab import DevNodeKinds

from . import factlayer as F
from .runtime import GraphHandle

LAYOUT = "about"   # The Lens view layout this page projects
PAGE_CONTENT_KEY = "page-content"   # The background Note's deliverable type: prose a projected page renders in place
READING_HEADING = "How to read this site"
# The marquee the page renders through: the site's own about template, a round portrait
ABOUT_TEMPLATE = {"template": "custom-marquee.ejs", "image-shape": "round", "image-width": "15em"}
# What staging shows above a background the public build would refuse
DRAFT_MARKER = ("::: {{.callout-warning}}\n## Draft background\nThe background's publish_state is {states}; "
                "the public build refuses this page until it is approved.\n:::\n")


def background_ref(
    selection: List[Dict[str, Any]],  # The About Lens's selection clauses
) -> Dict[str, Any]:  # {ref} | {error}
    """The background: the one ref of the selection's one `subgraph` clause. Any other shape
    names no single background, so it refuses."""
    refs = [str(r) for c in selection if c.get("verb") == "subgraph"
            for r in (c.get("args") or {}).get("refs") or []]
    if len(selection) != 1 or len(refs) != 1:
        return {"error": "the About Lens selects its background with one `subgraph` clause naming one Note"}
    return {"ref": refs[0]}


def body_of(
    text: str,  # A Note's lossless reconstruction (front matter + sections)
) -> str:  # Its body: the text after the front matter
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end >= 0:
            nl = text.find("\n", end + 4)
            return text[nl + 1:].strip() if nl >= 0 else ""
    return text.strip()


def load_site_config(
    website_root: str,  # The site project root
) -> Dict[str, Any]:  # The site config, parsed ({} when unreadable: the key readers report that)
    from pathlib import Path
    try:
        return yaml.safe_load((Path(website_root) / "_quarto.yml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def load_author_extras(
    website_root: str,  # The site project root
) -> Dict[str, str]:  # {image, alumni-of} -- the site author's optional fields, "" when unstated
    from .postpage import SITE_AUTHOR_KEY
    got = load_site_config(website_root).get(SITE_AUTHOR_KEY) or {}
    return {k: " ".join(str(got.get(k) or "").split()) for k in ("image", "alumni-of")}


def about_body(
    summary: str,                       # The site's one sentence
    background: str,                    # The background's body ("" = none)
    guide: str,                         # The site's reading guide
    marker: str = "",                   # The staging draft marker ("" = none)
    band: Optional[Dict[str, str]] = None,  # The Work-with-me page {title, href}, once published
) -> str:  # The page's body
    lines = [summary, ""]
    if marker:
        lines += [marker, ""]
    if background:
        lines += [background, ""]
    lines += [f"## {READING_HEADING}", "", guide, ""]
    if band:
        lines += [f"## [{band['title']}]({band['href']})", ""]
    return "\n".join(lines)


async def plan_about_page(
    gx: GraphHandle,
    node: Any,                               # The About Lens node
    page: Dict[str, Any],                    # {source, href}
    website_root: str,                       # The site project root (the site config's copy)
    profile: str,                            # public | staging
    planned: List[Dict[str, Any]],           # Every other planned page (the hand-off reads them)
) -> Dict[str, Any]:  # {page: planned entry} | {errors}
    """Plan the About page: the opening line, the background under its approval, the reading
    guide, the links, the hand-off, the JSON-LD."""
    from .agentlayer import jsonld_script, profile_jsonld
    from .authoring import note_approval
    from .postpage import load_reading_guide, load_site_author, load_site_links, load_site_summary, pitch_target
    from .purenotes import note_deliverable_type
    from .sitepages import page_source, render_page
    sid, key, src = str(F.nid(node)), str(F.prop(node, "key") or ""), page["source"]
    errors: List[Dict[str, Any]] = []
    summary = load_site_summary(website_root)
    links = load_site_links(website_root)
    guide = load_reading_guide(website_root)
    author = load_site_author(website_root)
    errors += summary["errors"] + links["errors"] + guide["errors"] + author["errors"]
    site_url = str((load_site_config(website_root).get("website") or {}).get("site-url") or "").strip().rstrip("/")
    if not guide["errors"] and not guide["text"]:
        errors.append({"kind": "about-reading-guide", "subject": sid,
                       "why": "the site config states no `reading-guide`; the About page renders it"})
    if not site_url:
        errors.append({"kind": "site-url", "subject": sid, "why": "the About page's JSON-LD needs website.site-url"})
    ref = background_ref(list(F.prop(node, "selection") or []))
    if ref.get("error"):
        errors.append({"kind": "about-selection", "subject": sid, "why": ref["error"]})
    if errors:
        return {"errors": errors}
    note = (await F.load_nodes(gx, [ref["ref"]])).get(ref["ref"])
    if note is None or F.label(note) != DevNodeKinds.NOTE:
        return {"errors": [{"kind": "about-background", "subject": sid, "ref": ref["ref"],
                            "why": "the About Lens's background names no Note"}]}
    bid = str(F.nid(note))
    if await note_deliverable_type(gx, bid) != PAGE_CONTENT_KEY:
        return {"errors": [{"kind": "about-background", "subject": sid, "ref": bid,
                            "why": f"the background is not a `{PAGE_CONTENT_KEY}` Note (prose a page renders in place)"}]}
    approval = await note_approval(gx, note)
    marker = ""
    if not approval["approved"]:
        states = "/".join(approval["states"]) or "ABSENT"
        if approval["states"] == ["published"]:
            states += " (its approval binds other content: re-review and re-assert)"
        if profile == "public":
            return {"errors": [{"kind": "about-unapproved", "subject": sid, "ref": bid, "states": approval["states"],
                                "why": f"the About background is not approved (publish_state {states}); "
                                       "the public page renders it only once published (2364f215)"}]}
        marker = DRAFT_MARKER.format(states=states)
    band = pitch_target(planned)
    if band:
        band = {"title": band["title"], "href": posixpath.relpath(page_source(band["href"]) or band["href"],
                                                                  posixpath.dirname(src) or ".")}
    body = about_body(summary["text"], body_of(approval["text"]), guide["text"], marker, band)
    extras = load_author_extras(website_root)
    person = profile_jsonld(author["author"], site_url + page["href"], links["links"],
                            site_url + "/" + extras["image"] if extras["image"] else "", extras["alumni-of"])
    front: Dict[str, Any] = {"title": str(F.prop(node, "title") or key)}
    if F.prop(node, "description"):
        front["description-meta"] = str(F.prop(node, "description"))
    if extras["image"]:
        front["image"] = extras["image"]
    front["about"] = {**ABOUT_TEMPLATE,
                      "links": [{"icon": l["icon"], "text": l["text"], "href": l["href"]} for l in links["links"]]}
    front["include-in-header"] = [{"text": jsonld_script(person)}]
    return {"page": {"source": src, "kind": "Lens", "key": key, "subject": sid, "categories": [],
                     "members": 0, "updated": None, "listed": [], "href": page["href"],
                     "title": front["title"], "layout": LAYOUT, "background": bid,
                     "approved": approval["approved"], "jsonld": person,
                     "description": str(F.prop(node, "description") or ""),
                     "text": render_page(front, body=body)}}
