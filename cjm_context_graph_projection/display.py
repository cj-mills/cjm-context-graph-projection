"""Graph-carried display rules: the presentation vocabulary (DEC `16bcd96e`).

A node's meaning can live entirely in its NEIGHBOURS (a FactSlot is `(subject,
predicate)` — zero text properties), so the generic title cascade bottoms out at
a raw UUID exactly where the graph is MOST reified. The fix is not per-type code
in every reader (each read surface accumulating every domain's presentation
knowledge) and not per-node stamped strings (O(nodes) materialization, frozen at
write time): it is a per-KIND rule CARRIED ON THE GRAPH — `DisplayRule` nodes —
interpreted by this one generic engine. Writers own their presentation knowledge
as O(kinds) DATA; every graph arrives self-describing to any rule-aware reader.

Resolution order (per node, per field): explicit stored `display_title`/
`display_gloss` property (hand-crafted or stamped — a stamp is a cached rule
output, tier 1) -> the kind's DisplayRule template -> the generic property
cascade (`node_title`) -> the raw id, last resort.

Two output fields with distinct jobs: `title` (short, stable IDENTITY — canvas
captions, neighbour lines, list rows) and `gloss` (one live line of ORIENTATION —
what it says / points to / its state). Anything richer belongs to `read` / the
detail pane, never a third tier.

The template grammar is FROZEN-SMALL — property refs, ONE-hop edge traversal
(first / count), literals, truncation. No conditionals, no multi-hop, ever.
The boundary principle: a label that seems to need two hops or logic is a
MISSING EDGE/NODE — feed it back as properties-vs-nodes evidence (`a50f3362`),
don't grow the grammar. Grammar:

    {prop}            a node property            {statement|90}  ...truncated
    {->REL}           display title of the first outgoing REL neighbour
    {<-REL}           ...first incoming (neighbour titles resolve via the
                      stored/cascade tiers only — rules never nest)
    {->REL.prop}      a literal property off that neighbour (both directions)
    {#->REL} {#<-REL} neighbour count over REL

Missing values render as ""; whitespace is collapsed. This spec is a WIRE-FORMAT-
GRADE contract between all future writers and readers — change it with the same
discipline as journal events.
"""

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from cjm_context_graph_layer.identity import derive_node_id
from cjm_context_graph_layer.ops import extend_graph, graph_task

from . import factlayer as F
from .runtime import GraphHandle

DISPLAY_RULE_LABEL = "DisplayRule"

# The generic property cascade: first non-empty wins. `display_title` (the explicit
# tier-1 override / rule output stamped by `annotate_display`) outranks everything;
# `subject_label` gives FactSlots a subject-name fallback even without a rule.
_TITLE_FIELDS = ("display_title", "title", "name", "slug", "key",
                 "subject_label", "statement", "value")
# Fields whose values run long enough to need the first-clause trim (a Decision's
# `statement` is a whole paragraph in house style; an Assertion `value` can be too).
_CLAUSE_FIELDS = ("statement", "value")
_CLAUSE_LIMIT = 110


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Attribute-or-key access (tolerates typed objects and wire dicts)."""
    if hasattr(obj, key):
        return getattr(obj, key)
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default


def first_clause(
    text: str,                    # The long text to trim
    limit: int = _CLAUSE_LIMIT,   # Max chars before trimming kicks in
) -> str:  # The leading clause (or a hard-truncated head when no clause boundary fits)
    """A long statement's leading clause — the Decision-title extractor.

    House-style decisions open with a HEADLINE, sometimes behind a category prefix
    ("BACKLOG (open): THE HEADLINE. detail…"), so the cut is the LONGEST clause
    boundary inside the budget — the earliest would keep the category and drop the
    headline. Falls back to a hard truncate when no boundary fits."""
    s = " ".join(text.split())
    if len(s) <= limit:
        return s
    cuts = [m.start() for m in re.finditer(r"(?::\s|\.\s|\s—\s|;\s)", s[:limit + 1])
            if m.start() >= 15]
    for cut in sorted(cuts, reverse=True):
        head = s[:cut]
        if head.count("(") == head.count(")"):  # never cut inside a parenthetical
            return head.rstrip(".:;— ")
    return s[:limit - 1].rstrip() + "…"


def node_title(node: Any) -> str:
    """Best display label for a node: the stored/cascade tiers of the resolution order.

    `annotate_display` stamps rule output into `display_title` beforehand, so this
    stays a cheap sync property read at every call site; un-annotated nodes fall
    through the generic cascade (long statement-ish winners get the first-clause
    trim), then the raw id."""
    p = F.props(node)
    for f in _TITLE_FIELDS:
        v = p.get(f)
        if isinstance(v, str) and v.strip():
            v = v.strip()
            return first_clause(v) if f in _CLAUSE_FIELDS else v
    return _get(node, "id", "?")


# ── The template grammar ────────────────────────────────────────────────────────
# A parsed template is a list of parts:
#   ("lit", text)
#   ("prop", name, trunc|None)
#   ("edge", is_count, direction, relation, prop|None, trunc|None)
_TOKEN_RE = re.compile(r"\{([^{}]*)\}")
_EDGE_RE = re.compile(r"^(#?)(->|<-)([A-Za-z_][A-Za-z0-9_]*)"
                      r"(?:\.([A-Za-z_][A-Za-z0-9_]*))?(?:\|(\d+))?$")
_PROP_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\|(\d+))?$")


def parse_template(
    template: str,  # A title/gloss template string
) -> List[Tuple]:  # The parsed parts (raises ValueError on a malformed token)
    """Parse a display template into literal / property / edge parts.

    Rejection is part of the contract: a token outside the frozen grammar raises
    (naming the token) rather than rendering garbage — `set_display_rule` calls
    this before writing, so a bad rule never lands on the graph."""
    parts: List[Tuple] = []
    pos = 0
    for m in _TOKEN_RE.finditer(template):
        if m.start() > pos:
            parts.append(("lit", template[pos:m.start()]))
        inner = m.group(1).strip()
        em = _EDGE_RE.match(inner)
        if em:
            is_count, direction, rel, prop, trunc = em.groups()
            if is_count and (prop or trunc):
                raise ValueError(f"bad display token {{{inner}}}: a #count takes no .prop/|trunc")
            parts.append(("edge", bool(is_count), direction, rel, prop,
                          int(trunc) if trunc else None))
        else:
            pm = _PROP_RE.match(inner)
            if not pm:
                raise ValueError(f"bad display token {{{inner}}}: not {{prop}}, {{->REL}}, "
                                 f"{{<-REL}}, {{->REL.prop}}, or {{#->REL}} (|N truncates)")
            parts.append(("prop", pm.group(1), int(pm.group(2)) if pm.group(2) else None))
        pos = m.end()
    if pos < len(template):
        parts.append(("lit", template[pos:]))
    return parts


def _trunc(text: str, limit: Optional[int]) -> str:
    """Cap a rendered value (ellipsis) when the token carried a |N filter."""
    s = " ".join(text.split())
    if limit is None or len(s) <= limit:
        return s
    return s[:limit - 1].rstrip() + "…"


def _template_relations(parts: List[Tuple]) -> Set[Tuple[str, str]]:
    """The (direction, relation) pairs a parsed template traverses."""
    return {(p[2], p[3]) for p in parts if p[0] == "edge"}


class Displayer:
    """The rule interpreter: loads a graph's DisplayRules once, then batch-annotates.

    `annotate` stamps `display_title` / `display_gloss` INTO the node property
    dicts in memory (never written back), so every downstream consumer —
    `node_title`, `node_summary`, render lines, the explorer JSON — picks the
    rule output up through the ordinary property path with zero signature churn.
    Neighbour lookups are batched per relation (one edge-pair load each, node
    fetches deduped), so a page of FactSlots costs a handful of queries."""

    def __init__(self, rules: Dict[str, Dict[str, Any]]):  # for_label -> {title, gloss} parsed
        self.rules = rules

    @classmethod
    async def load(cls, gx: GraphHandle) -> "Displayer":
        """Load + parse the graph's DisplayRules (a malformed stored rule is skipped)."""
        rules: Dict[str, Dict[str, Any]] = {}
        for n in await F.load_label(gx, DISPLAY_RULE_LABEL, limit=1000):
            for_label = F.prop(n, "for_label")
            if not for_label:
                continue
            entry: Dict[str, Any] = {}
            for field, key in (("title", "title_template"), ("gloss", "gloss_template")):
                tpl = F.prop(n, key)
                if isinstance(tpl, str) and tpl.strip():
                    try:
                        entry[field] = parse_template(tpl)
                    except ValueError:
                        continue  # a bad stored template never breaks reads
            if entry:
                rules[for_label] = entry
        return cls(rules)

    async def annotate(
        self,
        gx: GraphHandle,
        nodes: List[Any],  # The nodes about to be summarized/rendered (any labels mixed)
    ) -> None:
        """Stamp rule-derived `display_title`/`display_gloss` onto rule-labelled nodes.

        Per-field: an explicit stored value wins (tier 1 — never overwritten).
        Nodes whose label carries no rule cost nothing."""
        targets = [n for n in nodes if _get(n, "label") in self.rules]
        if not targets:
            return
        # One edge-pair load per relation any applicable template traverses.
        needed: Set[Tuple[str, str]] = set()
        for n in targets:
            for parts in self.rules[_get(n, "label")].values():
                needed |= _template_relations(parts)
        out_idx: Dict[str, Dict[str, List[str]]] = {}  # rel -> src -> [tgt]
        in_idx: Dict[str, Dict[str, List[str]]] = {}   # rel -> tgt -> [src]
        for _, rel in needed:
            if rel in out_idx:
                continue
            o: Dict[str, List[str]] = {}
            i: Dict[str, List[str]] = {}
            for src, tgt in await F.load_edge_pairs(gx, rel):
                o.setdefault(src, []).append(tgt)
                i.setdefault(tgt, []).append(src)
            out_idx[rel], in_idx[rel] = o, i

        def neighbours(node_id: str, direction: str, rel: str) -> List[str]:
            idx = out_idx if direction == "->" else in_idx
            return idx.get(rel, {}).get(node_id, [])

        # Fetch each referenced FIRST neighbour once (deduped across the batch).
        # The batch itself seeds the cache — a `show`/`overview` batch usually already
        # CONTAINS the neighbours — and the misses land in ONE batched `query_nodes`
        # (`NodeQuery.ids`): per-node round-trips serialize through the worker queue,
        # which priced a 100-slot list at ~10s.
        cache: Dict[str, Any] = {F.nid(n): n for n in nodes if F.nid(n)}
        neighbour_ids: Set[str] = set()
        for n in targets:
            nid = F.nid(n)
            for parts in self.rules[_get(n, "label")].values():
                for p in parts:
                    if p[0] == "edge" and not p[1]:
                        ids = neighbours(nid, p[2], p[3])
                        if ids and ids[0] not in cache:
                            neighbour_ids.add(ids[0])
        cache.update(await F.load_nodes(gx, list(neighbour_ids)))

        for n in targets:
            nid = F.nid(n)
            props = F.props(n)
            for field, key in (("title", "display_title"), ("gloss", "display_gloss")):
                parts = self.rules[_get(n, "label")].get(field)
                existing = props.get(key)
                if parts is None or (isinstance(existing, str) and existing.strip()):
                    continue
                rendered = self._render(n, nid, parts, neighbours, cache)
                if rendered:
                    _stamp(n, key, rendered)

    def _render(self, node: Any, node_id: str, parts: List[Tuple],
                neighbours, cache: Dict[str, Any]) -> str:
        """Interpolate one parsed template for one node (missing values -> "")."""
        out: List[str] = []
        for p in parts:
            if p[0] == "lit":
                out.append(p[1])
            elif p[0] == "prop":
                v = F.prop(node, p[1])
                out.append(_trunc(str(v), p[2]) if v not in (None, "") else "")
            else:  # edge
                _, is_count, direction, rel, prop, trunc = p
                ids = neighbours(node_id, direction, rel)
                if is_count:
                    out.append(str(len(ids)))
                elif not ids:
                    out.append("")
                else:
                    nb = cache.get(ids[0])
                    if nb is None:
                        out.append("")
                    elif prop:
                        v = F.prop(nb, prop)
                        out.append(_trunc(str(v), trunc) if v not in (None, "") else "")
                    else:
                        # Neighbour display = stored/cascade tiers only (rules never
                        # nest — the one-hop boundary is structural, not advisory).
                        out.append(_trunc(node_title(nb), trunc))
        return " ".join("".join(out).split())


def _stamp(node: Any, key: str, value: str) -> None:
    """Set an in-memory display property on a typed GraphNode or wire dict."""
    p = getattr(node, "properties", None)
    if p is None and isinstance(node, dict):
        p = node.setdefault("properties", {})
    if isinstance(p, dict):
        p[key] = value


async def annotate_display(
    gx: GraphHandle,
    nodes: List[Any],  # The nodes about to be summarized/rendered
) -> None:
    """Load this graph's rules + annotate `nodes` (the one-call seam for read verbs).

    Rule loading is one bounded label query per verb call — cheap, and always
    fresh (a long-lived `serve` sees a new rule on its next request, no restart)."""
    d = await Displayer.load(gx)
    await d.annotate(gx, nodes)


def display_rule_node_id(for_label: str) -> str:
    """Deterministic DisplayRule id — one rule per kind, so re-authoring converges."""
    return derive_node_id("display-rule", for_label)


async def set_display_rule(
    gx: GraphHandle,
    for_label: str,                        # The node label (kind) the rule renders
    title_template: Optional[str] = None,  # The `title` template (identity, ~60 chars)
    gloss_template: Optional[str] = None,  # The `gloss` template (one orientation line)
    *,
    actor: str = "agent:session",          # Who authored the vocabulary
) -> Dict[str, Any]:  # The write result (incl. error on a malformed template)
    """Author/update the graph-carried DisplayRule for a kind (presentation vocabulary).

    Validates both templates against the frozen grammar BEFORE writing (a bad rule
    never lands). Deterministic id = one rule per kind: a re-author UPDATES the
    existing node (update_node — a changed template must not trip the re-extend
    content-hash guard), so the journal's last `display-rule` op wins on replay."""
    if not (title_template or gloss_template):
        return {"error": "a display rule needs --title and/or --gloss", "for_label": for_label,
                "written": False}
    try:
        for tpl in (title_template, gloss_template):
            if tpl:
                parse_template(tpl)
    except ValueError as e:
        return {"error": str(e), "for_label": for_label, "written": False}

    node_id = display_rule_node_id(for_label)
    props: Dict[str, Any] = {"for_label": for_label, "actor": actor}
    if title_template is not None:
        props["title_template"] = title_template
    if gloss_template is not None:
        props["gloss_template"] = gloss_template

    existing = await graph_task(gx.queue, gx.graph_id, "get_node", node_id=node_id)
    if existing is not None:
        await graph_task(gx.queue, gx.graph_id, "update_node", node_id=node_id, properties=props)
        return {"rule_id": node_id, "for_label": for_label, "updated": True, "written": True,
                "title_template": title_template, "gloss_template": gloss_template}
    node = {"id": node_id, "label": DISPLAY_RULE_LABEL, "properties": props, "sources": []}
    await extend_graph(gx.queue, gx.graph_id, [node], [])
    return {"rule_id": node_id, "for_label": for_label, "updated": False, "written": True,
            "title_template": title_template, "gloss_template": gloss_template}
