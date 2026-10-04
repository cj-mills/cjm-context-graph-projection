"""A post's CATEGORIES: the one reader every renderer calls (design ce17606b (1) under the category
model 0f7fcdcb (2) and amendment 3c5cff97).

A post's categories are its confirmed facets (about_task, about_stage, uses_model, uses_tool,
about_subject) and, for a tutorial, its teaches_task / teaches_stage facts -- only values that
name a live facet vocabulary entry, so the Tutorials matrix's structural rows (general, other)
never render as a chip (eefda2dd). A chip is the entry's display name; the chips run in CHIP_ORDER
(most specific first, user ruling of ce17606b), within a kind in the vocabulary's position order.
Two kinds sharing a display name refuse: one chip text would name two categories.

The post header, every projected listing, the feed and the Series pages read this; the archive's
front-matter categories stay the original observation and render nowhere (0f7fcdcb (2))."""

from typing import Any, Dict, List

from cjm_dev_graph_schema import predicates as P

from .runtime import GraphHandle

CHIP_ORDER = (P.ENTITY_TASK, P.ENTITY_STAGE, P.ENTITY_MODEL, P.ENTITY_TOOL, P.ENTITY_SUBJECT)


def chip_vocab(
    vocab: Dict[str, Dict[str, Any]],  # facetjudge.load_facet_vocab: {entry: the live Entity's properties}
) -> Dict[str, Any]:  # {names: {(kind, key): display name}, rank: {display name: chip position}, errors}
    """Each live facet entry's chip text and its place in the chip order."""
    rows = sorted(((CHIP_ORDER.index(v["entity_kind"]), i, v) for i, v in enumerate(vocab.values())),
                  key=lambda r: r[:2])
    names: Dict[Any, str] = {}
    rank: Dict[str, int] = {}
    owner: Dict[str, str] = {}
    errors: List[Dict[str, Any]] = []
    for _, _, v in rows:
        kind, key = str(v["entity_kind"]), str(v["key"])
        name = str(v.get("name") or key)
        entry = f"{kind}:{key}"
        if name in owner:
            errors.append({"kind": "category-name-clash", "name": name, "entries": [owner[name], entry],
                           "why": "two facet entries share one display name; a chip would name both"})
            continue
        owner[name] = entry
        names[(kind, key)] = name
        rank[name] = len(rank)
    return {"names": names, "rank": rank, "errors": errors}


def post_chips(
    facts: Dict[str, List[str]],           # A post's active facet + coverage facts: {predicate: [values]}
    names: Dict[Any, str],                 # chip_vocab's names
    rank: Dict[str, int],                  # chip_vocab's rank
) -> List[str]:  # The post's chips, in chip order
    kinds = {**P.FACET_PREDICATES, **P.COVERAGE_KINDS}
    out = {names[(kinds[pred], v)] for pred, vals in facts.items() if pred in kinds
           for v in vals if (kinds[pred], v) in names}
    return sorted(out, key=rank.__getitem__)


async def load_post_categories(
    gx: GraphHandle,
) -> Dict[str, Any]:  # {posts: {note id: [chips]}, rank: {chip: position}, errors}
    """Every post's chips from the graph (a post with none is absent)."""
    from .coverage import load_coverage_facts
    from .facetjudge import load_facet_vocab
    from .facetreview import load_confirmed
    vocab = chip_vocab(await load_facet_vocab(gx))
    facts: Dict[str, Dict[str, List[str]]] = {}
    for got in (await load_confirmed(gx), await load_coverage_facts(gx)):
        for n, by_pred in got.items():
            for pred, vals in by_pred.items():
                facts.setdefault(n, {}).setdefault(pred, []).extend(vals)
    posts = {n: chips for n, f in facts.items() if (chips := post_chips(f, vocab["names"], vocab["rank"]))}
    return {"posts": posts, "rank": vocab["rank"], "errors": vocab["errors"]}


def majority(
    members: List[List[str]],  # Each listed member's chips
    rank: Dict[str, int],      # load_post_categories' rank
) -> List[str]:  # The chips MORE THAN HALF the members carry, in chip order
    """A collection page's chips (design ce17606b (4)): what most of it is about -- the
    intersection is empty for a varied series, the union a wall of chips."""
    counts: Dict[str, int] = {}
    for chips in members:
        for c in set(chips):
            counts[c] = counts.get(c, 0) + 1
    return sorted((c for c, k in counts.items() if 2 * k > len(members)), key=rank.__getitem__)
