"""The post page's sources (design 39c51c15 (3), amendment 722a8232, under the redesign build
8079ae0f).

A born post derives from SOURCES in a sibling graph (a lecture video, a book chapter, the book
itself) through its Note's DERIVED_FROM edge to a local Reference (0154f5e4). The block lists the
Source / Collection References only -- never the Segments its Points cite nor the Corrections.

THE FACTS ARE OBSERVED, NEVER RETYPED (722a8232 (1), (2)). When a `link` observes a Source or a
Collection in the sibling, it reads two facts off the foreign node with the observation, and the
journaled op carries them, so replay never opens the sibling:
- the LOCATOR -- the source's public URL (`public_url`, with its evidence), and
- the CITATION -- the PARTS of how the source names itself to a reader: a book chapter's work,
  author, part and chapter (its `work_structure`); a Collection's work, when every member Source
  names the same one; else the title a linked source was published under (its URL evidence).
Both land as facts on the Reference with method `observed`. A changed observation supersedes the
observed value it replaces; a value a human authored (a hand locator for a source the sibling
cannot know) is intent, and an observation never supersedes it -- it is reported as held.

THE BLOCK renders each source through its facts: the citation linked by the locator, a citation
alone when there is no locator, the locator under the source's own title when there is no
citation. A source with neither is REPORTED, never rendered as an internal id (87aaa212). So is a
BORN post whose Points derive from the sibling while its Note names no source (722a8232 (3): the
drafting verb mints that link at birth; the report catches any post born before it)."""

from typing import Any, Dict, Iterable, List

from cjm_context_graph_layer.ops import graph_task
from cjm_context_graph_primitives.query import EdgeQuery, PropertyPredicate
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.nodes import ReferenceNode
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from . import factlayer as F
from .runtime import GraphHandle

SOURCE_LABELS = ("Source", "Collection")   # The foreign kinds a sources block lists
OBSERVED = "observed"                      # The method an observed fact carries (an authored one has none)
MEMBER_OF = "PART_OF"                      # A Source's edge to its Collection in the sibling graph
FACT_PREDICATES = (P.LOCATOR, P.CITATION)


def _work_parts(work_structure: Dict[str, Any]) -> Dict[str, Any]:
    """A work structure's citation parts: the work and its author, then the part and chapter."""
    work = (work_structure or {}).get("work") or {}
    if not str(work.get("title") or "").strip():
        return {}
    parts = {"work": str(work["title"]).strip(), "author": str(work.get("author") or "").strip()}
    for k in ("part", "chapter"):
        if work_structure.get(k) not in (None, ""):
            parts[k] = work_structure[k]
    return {k: v for k, v in parts.items() if v not in (None, "")}


def observed_facts(
    node: Dict[str, Any],                         # The foreign node's wire dict ({id, label, properties})
    members: Iterable[Dict[str, Any]] = (),       # A Collection's member Sources' properties (ignored otherwise)
) -> Dict[str, Any]:  # {locator?: {value, evidence}, citation?: parts} -- {} for a node that is no source
    """The facts a source states about itself, read at observation (722a8232 (1), (2))."""
    label = str(node.get("label") or "")
    if label not in SOURCE_LABELS:
        return {}
    props = node.get("properties") or {}
    out: Dict[str, Any] = {}
    url = str(props.get("public_url") or "").strip()
    evidence = props.get("public_url_evidence") or {}
    if url:
        out[P.LOCATOR] = {"value": url, "evidence": evidence}
    parts = _work_parts(props.get("work_structure") or {})
    if not parts and label == "Collection":
        # the Collection IS the work when every member names one and the same
        works = {(w.get("work"), w.get("author")) for w in
                 (_work_parts(m.get("work_structure") or {}) for m in members)}
        if len(works) == 1:
            work, author = next(iter(works))
            parts = {k: v for k, v in (("work", work), ("author", author)) if v}
    if not parts and url and isinstance(evidence, dict) and str(evidence.get("playlist_title") or "").strip():
        parts = {"title": str(evidence["playlist_title"]).strip()}   # the title it was published under
    if parts:
        out[P.CITATION] = dict(sorted(parts.items()))
    return out


async def collection_members(
    sg: GraphHandle,      # The sibling graph (read-only)
    collection_id: str,
) -> List[Dict[str, Any]]:  # The member Sources' properties
    """A Collection's member Sources in the sibling, read with the observation."""
    res = await graph_task(sg.queue, sg.graph_id, "query_edges",
                           query=EdgeQuery(target_ids=[collection_id], relation_type=MEMBER_OF,
                                           project=["source_id"]).to_dict())
    ids = sorted({str(r["source_id"]) for r in (res.rows or []) if r.get("source_id")})
    nodes = await F.load_nodes(sg, ids)
    return [dict(F.props(n)) for n in nodes.values() if F.label(n) == "Source"]


def _fact_value(predicate: str, fact: Any) -> str:
    if predicate == P.LOCATOR:
        return str(fact["value"])
    return P.citation_value(fact)


async def _slot(
    gx: GraphHandle,
    subject_id: str,
    predicate: str,
) -> List[Any]:  # The ACTIVE assertions on (subject, predicate)
    rows = await F.load_label_where(gx, DevNodeKinds.ASSERTION,
                                    [PropertyPredicate("subject_id", "eq", subject_id),
                                     PropertyPredicate("predicate", "eq", predicate)])
    return F.active_assertions(rows, await F.load_supersedes(gx)) if rows else []


async def apply_source_facts(
    gx: GraphHandle,
    reference_id: str,         # The local Reference the facts are about
    facts: Dict[str, Any],     # observed_facts' result (the op carries it)
    *,
    actor: str = "agent:session",
) -> Dict[str, Any]:  # {asserted: [predicate], held: [{predicate, value, held_by}]}
    """Land an observation's facts on its Reference -- live and replay alike. An unchanged value
    is a no-op; a changed one supersedes the OBSERVED value it replaces; an authored value holds."""
    from .write import assert_value
    asserted: List[str] = []
    held: List[Dict[str, Any]] = []
    for predicate in FACT_PREDICATES:
        if predicate not in facts:
            continue
        value = _fact_value(predicate, facts[predicate])
        active = await _slot(gx, reference_id, predicate)
        if any(str(F.prop(a, "value") or "") == value for a in active):
            continue
        authored = [a for a in active if F.prop(a, "method") != OBSERVED]
        if authored:
            held.append({"predicate": predicate, "value": value,
                         "held_by": str(F.prop(authored[0], "value") or "")})
            continue
        res = await assert_value(gx, reference_id, predicate, value, actor=actor, method=OBSERVED,
                                 supersede=[F.nid(a) for a in active] or None)
        if res.get("error"):
            raise RuntimeError(f"{predicate} on {reference_id}: {res['error']}")
        asserted.append(predicate)
    return {"asserted": asserted, "held": held}


async def _derived_from(gx: GraphHandle) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for s, t in await F.load_edge_pairs(gx, DevRelations.DERIVED_FROM):
        out.setdefault(s, []).append(t)
    return out


async def load_sources(
    gx: GraphHandle,
) -> Dict[str, Any]:  # {posts: {note id: [source]}, derived: {note ids whose Points derive from a sibling}}
    """Every Note's sources -- its DERIVED_FROM References to a Source or a Collection, each
    with its active locator and citation -- and the Notes whose Points derive from a sibling."""
    refs = {F.nid(r): r for r in await F.load_label(gx, DevNodeKinds.REFERENCE)}
    edges = await _derived_from(gx)
    facts: Dict[str, Dict[str, str]] = {}
    supers = await F.load_supersedes(gx)
    for predicate in FACT_PREDICATES:
        rows = await F.load_label_where(gx, DevNodeKinds.ASSERTION, [PropertyPredicate("predicate", "eq", predicate)])
        for a in F.active_assertions(rows, supers) if rows else []:
            facts.setdefault(str(F.prop(a, "subject_id")), {})[predicate] = str(F.prop(a, "value") or "")
    posts: Dict[str, List[Dict[str, Any]]] = {}
    for src, targets in edges.items():
        for t in targets:
            r = refs.get(t)
            if r is None or F.prop(r, "foreign_label") not in SOURCE_LABELS:
                continue
            f = facts.get(t, {})
            posts.setdefault(src, []).append({
                "id": t, "kind": str(F.prop(r, "foreign_label")),
                "title": _plain_title(r), "locator": f.get(P.LOCATOR, ""),
                "citation": P.citation_parts(f[P.CITATION]) if P.CITATION in f else {}})
    # A Note's Points: its own, and those of the sets it RENDERS (96be1528 (P))
    owners: Dict[str, str] = {}
    for note, ps in await F.load_edge_pairs(gx, DevRelations.RENDERS):
        owners[ps] = note
    derived = set()
    for pt in await F.load_label(gx, DevNodeKinds.POINT):
        owner = str(F.prop(pt, "owner_id") or "")
        note = owners.get(owner, owner)
        if note in derived:
            continue
        # a sibling's content (a Segment), never a captured web page (a research point's)
        if any(t in refs and F.prop(refs[t], "graph") != ReferenceNode.WEB
               and F.prop(refs[t], "foreign_label") not in SOURCE_LABELS for t in edges.get(F.nid(pt), [])):
            derived.add(note)
    return {"posts": {n: sorted(s, key=lambda x: (x["kind"] != "Collection", source_text(x))) for n, s in posts.items()},
            "derived": derived}


def _plain_title(ref: Any) -> str:
    """The Reference's display title without its graph suffix (`<title> @ <graph>`)."""
    t = str(F.prop(ref, "title") or "")
    g = str(F.prop(ref, "graph") or "")
    return t[: -len(f" @ {g}")] if g and t.endswith(f" @ {g}") else t


def citation_text(
    parts: Dict[str, Any],  # A citation's parts
) -> str:  # How the block names the source ('The Learning Game, Ana Lorena Fábrega, Part 1, Chapter 2')
    """A citation's parts as one line, in the order a reader reads them."""
    bits = [str(parts[k]) for k in ("title", "work", "author") if parts.get(k)]
    bits += [f"{k.capitalize()} {parts[k]}" for k in ("part", "chapter") if parts.get(k) not in (None, "")]
    return ", ".join(bits)


def source_text(
    source: Dict[str, Any],  # load_sources' entry
) -> str:  # Its name: the citation, else the Reference's own title
    return citation_text(source.get("citation") or {}) or str(source.get("title") or "")


def renderable(
    source: Dict[str, Any],  # load_sources' entry
) -> bool:
    """A source renders through a locator or a citation -- never through an internal id alone."""
    return bool(source.get("locator") or source.get("citation"))


def _md(text: str) -> str:
    """Link text safe inside markdown brackets."""
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def render_sources(
    sources: List[Dict[str, Any]],  # The post's renderable sources (load_sources' entries)
) -> str:  # The sources block (markdown), "" when there are none
    """The post's sources: each named by its citation, linked by its locator."""
    lines = []
    for s in sources:
        text = _md(source_text(s))
        lines.append(f"- [{text}]({s['locator']})" if s.get("locator") else f"- {text}")
    if not lines:
        return ""
    head = "Source" if len(lines) == 1 else "Sources"
    return "::: {.post-sources}\n**" + head + "**\n\n" + "\n".join(lines) + "\n:::\n"


def source_plan(
    loaded: Dict[str, Any],                  # load_sources' result
    rendered: Dict[str, Dict[str, Any]],     # {note id: {title, origin}} -- the posts this profile renders
) -> Dict[str, Any]:  # {blocks: {note id: markdown}, unrendered: [...], missing: [...], counts}
    """Each rendered post's sources block, the sources it cannot render, and the born posts that
    derive from a sibling while naming no source."""
    blocks: Dict[str, str] = {}
    unrendered: List[Dict[str, str]] = []
    missing: List[Dict[str, str]] = []
    counts = {"sources": 0, "sources_linked": 0, "sources_cited": 0}
    for nid in sorted(rendered):
        srcs = loaded["posts"].get(nid) or []
        ok = [s for s in srcs if renderable(s)]
        unrendered += [{"post": rendered[nid]["title"], "reference": s["id"], "title": s["title"]}
                       for s in srcs if not renderable(s)]
        if ok:
            blocks[nid] = render_sources(ok)
            counts["sources"] += 1
            counts["sources_linked"] += sum(1 for s in ok if s.get("locator"))
            counts["sources_cited"] += sum(1 for s in ok if not s.get("locator"))
        if rendered[nid].get("origin") == "born" and nid in loaded["derived"] and not srcs:
            missing.append({"id": nid, "title": rendered[nid]["title"]})
    counts["sources_unrendered"] = len(unrendered)
    counts["sources_missing"] = len(missing)
    return {"blocks": blocks, "unrendered": unrendered, "missing": missing, "counts": counts}
