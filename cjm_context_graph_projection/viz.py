"""A minimal READ-ONLY visualization: the readiness frontier + its dependency DAG, as HTML.

The P1 "see it while the capstone grows" tool of the dogfood-arc roadmap (DEC-ARC `17bf27d4`),
framed the same way as every other output here — as a PROJECTION, not a stored artifact. It reuses
the `onboarding`/`readme` projector discipline (graph -> text/markup, byte-faithful to stdout,
`--write` to a file) and layers zero new truth: it is a thin presentation over the `readiness`
projector, so the picture is DERIVED-BY-CONSTRUCTION and can never drift from the frontier a
`cg-read readiness` prints. Deliberately MINIMAL (P3 is the richer viz): work-items coloured by
ready/blocked/done, `GATED_BY` edges as the dependency DAG, pan/zoom only — no editing, no writes.

Self-contained single HTML file: the element data is inlined as JSON and Cytoscape.js (+ dagre for
the DAG layout) is pulled from a CDN, so the file opens in any browser with no build step. (Vendoring
the JS is a P3 concern; the minimal cut keeps the projector tiny.)
"""

import html
import json
from typing import Any, Dict, List, Optional

from .readiness import readiness
from .runtime import GraphHandle

# Pinned CDN builds — a minimal viz has no bundler; P3 can vendor these.
_CYTOSCAPE_JS = "https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.30.2/cytoscape.min.js"
_DAGRE_JS = "https://cdn.jsdelivr.net/npm/dagre@0.8.5/dist/dagre.min.js"
_CYTOSCAPE_DAGRE_JS = "https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.min.js"

# state -> (fill, human legend label). `prereq` = a gate target that is not itself a classified
# work-item (has no task_state) — surfaced so every edge has both endpoints.
_STATE_STYLE = {
    "done": ("#9aa0a6", "done"),
    "ready": ("#34a853", "ready"),
    "blocked": ("#e37400", "blocked"),
    "prereq": ("#4285f4", "prerequisite (not a work-item)"),
}

_LABEL_MAX = 48  # truncate long decision titles so nodes stay legible (full label is in the tooltip)


def _short(label: str) -> str:
    """Truncate a label for the node face (the full label rides in the node tooltip)."""
    label = " ".join(label.split())
    return label if len(label) <= _LABEL_MAX else label[: _LABEL_MAX - 1] + "…"


def viz_elements(
    frontier: Dict[str, Any],  # A `readiness()` result: {ready, blocked, done, counts}
) -> List[Dict[str, Any]]:  # Cytoscape elements (nodes then edges)
    """Pure: turn a readiness frontier into Cytoscape elements — the whole data model.

    Two passes so state colouring is exact: first every CLASSIFIED work-item carries its true
    done/ready/blocked state, THEN gate edges are added and any gate target that is not itself a
    work-item is minted as a `prereq` node. `add` is idempotent per id, so a work-item referenced
    as another item's gate keeps its real state instead of being overwritten to `prereq`."""
    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []

    def add(nid: str, label: str, state: str) -> None:
        if nid not in nodes:
            nodes[nid] = {"data": {"id": nid, "label": _short(label), "full": label, "state": state}}

    # Pass 1: classified work-items with their derived state.
    for state in ("done", "ready", "blocked"):
        for e in frontier.get(state, []):
            add(e["id"], e["label"], state)
    # Pass 2: GATED_BY edges (item -> prerequisite) + any external gate targets.
    for e in frontier.get("ready", []) + frontier.get("blocked", []):
        for g in e.get("gates", []) + e.get("blocked_by", []):
            add(g["id"], g["label"], "prereq")
            edges.append({"data": {"id": f"{e['id']}|{g['id']}", "source": e["id"], "target": g["id"]}})

    return list(nodes.values()) + edges


def render_viz_html(
    elements: List[Dict[str, Any]],  # Cytoscape elements from `viz_elements`
    counts: Dict[str, int],          # {ready, blocked, done} tallies for the header
    scope: Optional[str] = None,     # The scope term used (for the header), if any
) -> str:  # A self-contained HTML document
    """Render the elements into one self-contained interactive HTML page (Cytoscape + dagre)."""
    legend = "".join(
        f'<span class="chip"><i style="background:{fill}"></i>{html.escape(name)}</span>'
        for _, (fill, name) in _STATE_STYLE.items())
    style_json = json.dumps([
        {"selector": "node",
         "style": {"label": "data(label)", "background-color": "#4285f4", "color": "#111",
                   "font-size": 10, "text-wrap": "wrap", "text-max-width": 150,
                   "text-valign": "bottom", "text-halign": "center", "width": 20, "height": 20,
                   "text-margin-y": 5, "border-width": 1, "border-color": "#0003",
                   "text-background-color": "#fff", "text-background-opacity": 0.7,
                   "text-background-padding": 1}},
        *[{"selector": f'node[state = "{s}"]', "style": {"background-color": fill}}
          for s, (fill, _) in _STATE_STYLE.items()],
        {"selector": "edge",
         "style": {"width": 1.5, "line-color": "#bbb", "target-arrow-color": "#bbb",
                   "target-arrow-shape": "triangle", "curve-style": "bezier",
                   "arrow-scale": 0.9}},
    ])
    scope_note = f' · scope <code>{html.escape(scope)}</code>' if scope else ""
    header = (f'{counts.get("ready", 0)} ready · {counts.get("blocked", 0)} blocked · '
              f'{counts.get("done", 0)} done{scope_note}')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Readiness frontier</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="{_CYTOSCAPE_JS}"></script>
<script src="{_DAGRE_JS}"></script>
<script src="{_CYTOSCAPE_DAGRE_JS}"></script>
<style>
  html,body{{margin:0;height:100%;font:13px/1.4 system-ui,sans-serif;color:#222}}
  #bar{{padding:8px 12px;border-bottom:1px solid #ddd;display:flex;gap:16px;align-items:center;flex-wrap:wrap}}
  #bar b{{font-size:14px}}
  .chip{{display:inline-flex;align-items:center;gap:5px;color:#444}}
  .chip i{{width:11px;height:11px;border-radius:50%;display:inline-block;border:1px solid #0002}}
  #cy{{position:absolute;top:41px;left:0;right:0;bottom:0}}
  .hint{{color:#888;margin-left:auto}}
</style></head><body>
<div id="bar"><b>Readiness frontier</b><span>{header}</span>{legend}
  <span class="hint">read-only · scroll to zoom, drag to pan · hover a node for its full label</span></div>
<div id="cy"></div>
<script>
  const elements = {json.dumps(elements)};
  const cy = cytoscape({{
    container: document.getElementById('cy'),
    elements: elements,
    style: {style_json},
    layout: {{ name: 'dagre', rankDir: 'BT', nodeSep: 170, rankSep: 90, edgeSep: 12, fit: true, padding: 30 }},
    minZoom: 0.15, maxZoom: 3, wheelSensitivity: 0.3,
  }});
  // Canvas nodes can't carry a native title tooltip; show the full label in a lightweight popup.
  let tip;
  cy.on('mouseover', 'node', e => {{
    tip = document.createElement('div');
    tip.textContent = e.target.data('full') + '  [' + e.target.data('state') + ']';
    Object.assign(tip.style, {{position:'fixed',background:'#111',color:'#fff',padding:'4px 7px',
      borderRadius:'4px',font:'12px system-ui',maxWidth:'420px',zIndex:9,pointerEvents:'none'}});
    document.body.appendChild(tip);
  }});
  cy.on('mousemove', 'node', e => {{ if(tip){{ tip.style.left=(e.originalEvent.clientX+12)+'px';
    tip.style.top=(e.originalEvent.clientY+12)+'px'; }} }});
  cy.on('mouseout', 'node', () => {{ if(tip){{ tip.remove(); tip=null; }} }});
</script></body></html>
"""


async def project_viz(
    gx: GraphHandle,
    scope: Optional[str] = None,  # Restrict to work-items whose label matches (passed to `readiness`)
) -> Dict[str, Any]:  # {html, elements, counts, node_count, edge_count}
    """Project the readiness frontier into a self-contained interactive HTML page.

    Read-only and derived: calls `readiness`, converts to Cytoscape elements, renders the page.
    The same {ready, blocked, done} truth `cg-read readiness` prints, drawn as a dependency DAG."""
    frontier = await readiness(gx, scope, state="all")  # the DAG needs every bucket
    elements = viz_elements(frontier)
    node_count = sum(1 for e in elements if "source" not in e["data"])
    edge_count = len(elements) - node_count
    return {"html": render_viz_html(elements, frontier["counts"], scope),
            "elements": elements, "counts": frontier["counts"],
            "node_count": node_count, "edge_count": edge_count}
