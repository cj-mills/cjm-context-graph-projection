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
