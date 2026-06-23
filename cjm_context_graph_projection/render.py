"""Render projection results for a consumer: agent (JSON) or human (markdown).

Asymmetric projection (arc plan): the agent form is stable, chainable JSON with
ids explicit; the human form is compact rendered markdown. Same core results,
two surfaces — correctness/chainability over token-economy for v1.
"""

import json
from typing import Any, Dict, List


def _line(summary: Dict[str, Any]) -> str:
    """One markdown line for a node summary: title, label, id, optional description."""
    bits = [f"**{summary.get('title')}**", f"_{summary.get('label')}_", f"`{summary.get('id')}`"]
    head = " · ".join(b for b in bits if b)
    desc = summary.get("description")
    return f"- {head}" + (f" — {desc}" if desc else "")


def _human(kind: str, obj: Dict[str, Any]) -> str:
    """Render a result dict as markdown, dispatched on the command kind."""
    if kind == "schema":
        labels = obj.get("node_labels", [])
        counts = obj.get("counts", {})
        lines = ["## Graph schema", "",
                 "**Node labels:** " + ", ".join(f"{l} ({counts.get(l, '?')})" for l in labels),
                 "**Edge types:** " + ", ".join(obj.get("edge_types", []))]
        return "\n".join(lines)
    if kind == "relevant":
        lines = [f"## Relevant to: {obj.get('task')}", "",
                 "_seeds:_ " + ", ".join(s.get("title", "?") for s in obj.get("seeds", [])) or "_(none)_",
                 ""]
        for r in obj.get("results", []):
            lines.append(_line(r) + f"  \n  ↳ _{r.get('why')}_ (score {r.get('score')})")
        if not obj.get("results"):
            lines.append("_(no results — try different terms)_")
        return "\n".join(lines)
    if kind == "assert":
        head = (f"**asserted** `{obj.get('predicate')}` = _{obj.get('value')}_ "
                f"on **{obj.get('subject')}** (actor {obj.get('actor')})")
        lines = [head, f"`slot {obj.get('slot_id')}`", f"`assertion {obj.get('assertion_id')}`"]
        if obj.get("created_subject"):
            lines.append("_minted a new `term` entity for the subject_")
        if obj.get("superseded"):
            lines.append(f"⤵ superseded {len(obj['superseded'])} prior assertion(s)")
        if obj.get("born_superseded"):
            lines.append("⚠ born superseded (an existing value is newer)")
        if obj.get("conflict"):
            lines.append("⚠ **CONFLICT (recorded, not blocked):** also active —")
            for c in obj["conflict"]:
                lines.append(f"  - _{c.get('value')}_ (actor {c.get('actor')}) `{c.get('assertion_id')}`")
        elif obj.get("soft_conflict"):
            lines.append("• soft conflict on an untyped predicate → see `worklist`")
        return "\n".join(lines)
    if kind == "alias":
        if obj.get("error"):
            return f"⚠ {obj['error']}"
        lines = [f"**aliased** `[[{obj.get('drifted')}]]` → **{obj.get('canonical')}** "
                 f"(actor {obj.get('actor')})",
                 f"`assertion {obj.get('assertion_id')}`"]
        ev = obj.get("evidence") or []
        if ev:
            lines.append(f"_evidence: {len(ev)} source note(s) carried the broken link_")
        lines.append("_re-`ingest` to heal the references; the link drops off `worklist`_")
        return "\n".join(lines)
    if kind == "decide":
        lines = [f"**decided:** {obj.get('statement')}", f"`{obj.get('decision_id')}`",
                 f"_actor {obj.get('actor')}_"]
        if obj.get("supports"):
            lines.append(f"supported by {len(obj['supports'])} premise(s)")
        if obj.get("session"):
            lines.append(f"decided in session `{obj['session']}`")
        return "\n".join(lines)
    if kind == "contradictions":
        cs = obj.get("contradictions", [])
        if not cs:
            return "## Contradictions\n\n_(none)_"
        lines = [f"## Contradictions ({obj.get('count', len(cs))})", ""]
        for c in cs:
            lines.append(f"- **{c.get('subject')}** · _{c.get('predicate')}_ `{c.get('slot_id')}`")
            for a in c.get("assertions", []):
                lines.append(f"    - _{a.get('value')}_ (actor {a.get('actor')}) `{a.get('assertion_id')}`")
        return "\n".join(lines)
    if kind == "oracle":
        c = obj.get("counts", {})
        lines = ["## Version oracle",
                 f"_bumped {c.get('bumped', 0)} · first-seen {c.get('first_seen', 0)} · "
                 f"unchanged {c.get('unchanged', 0)} · skipped {c.get('skipped', 0)}_", ""]
        for b in obj.get("bumped", []):
            lines.append(f"- ⬆ **{b.get('repo')}** → {b.get('version')} "
                         f"(superseded {len(b.get('superseded', []))})")
        for b in obj.get("first_seen", []):
            lines.append(f"- ✦ **{b.get('repo')}** = {b.get('version')} (first seen)")
        return "\n".join(lines)
    if kind == "worklist":
        c = obj.get("counts", {})
        lines = [f"## Worklist", f"_dangling refs {c.get('dangling_references', 0)} · "
                 f"soft conflicts {c.get('soft_conflicts', 0)} · "
                 f"untyped predicates {c.get('untyped_predicates', 0)}_", ""]
        dr = obj.get("dangling_references", [])
        if dr:
            lines.append("**Dangling references (propose/confirm):**")
            for d in dr[:30]:
                sug = f" → maybe `{d['suggestion']}` ({d['score']})" if d.get("suggestion") else " → (no match)"
                lines.append(f"  - `{d.get('from')}` links `[[{d.get('missing')}]]`{sug}")
        sc = obj.get("soft_conflicts", [])
        if sc:
            lines.append("**Soft conflicts (untyped slots):**")
            for s in sc:
                lines.append(f"  - _{s.get('predicate')}_ on `{s.get('subject_id')}`: {s.get('values')}")
        up = obj.get("untyped_predicates", [])
        if up:
            lines.append("**Untyped predicates in use (typing candidates):**")
            for u in up:
                lines.append(f"  - _{u.get('predicate')}_ ({u.get('slots')} slot[s])")
        return "\n".join(lines)
    if kind in ("show", "state"):
        node = obj.get("node")
        if node is None and "overview" in obj:
            return _human("schema", obj["overview"]) + f"\n\n_{obj.get('hint', '')}_"
        if node is None:
            return f"_{obj.get('error') or obj.get('note') or 'not found'}_"
        lines = [f"## {node.get('title')}  _{node.get('label')}_", f"`{node.get('id')}`", ""]
        if node.get("description"):
            lines += [node["description"], ""]
        nb = obj.get("neighbours", [])
        if nb:
            lines.append("**Neighbours:**")
            for n in nb:
                arrow = "→" if n["direction"] == "out" else "←"
                lines.append(f"- {arrow} _{n['relation']}_ {_line(n['node'])[2:]}")
        return "\n".join(lines)
    return json.dumps(obj, indent=2, default=str)


def render(
    kind: str,         # Command kind: schema | state | relevant | show
    obj: Dict[str, Any],  # The projection result
    fmt: str = "human",   # "agent" (JSON) or "human" (markdown)
) -> str:  # Rendered string
    """Render a projection result in the requested format."""
    if fmt == "agent":
        return json.dumps(obj, indent=2, default=str)
    return _human(kind, obj)
