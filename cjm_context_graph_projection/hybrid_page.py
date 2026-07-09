"""The HYBRID graph explorer client — GPU physics canvas + DOM overlay (check-in 1233ab46).

The foundation-evaluation prototype (session-1 synthesis theme 3, hybrid LOD rendering):
a WebGL physics/points/edges layer (cosmos.gl v3 — GPU force layout, ~140k points at
interactive framerates) under a DOM overlay for screen-space labels, with content CARDS
at near zoom as the next LOD rung. Mounted at `/hybrid` BESIDE the cytoscape explorer
(`/`) so the two foundations stay A/B-comparable over the same live graphs during the
eval; same one-Python-string, pinned-CDN, on-graph discipline as `explorer_page`.

THE GRAMMAR SHIFT vs the neighborhood explorer: the ENTIRE graph is always on the canvas
(the `export` read verb feeds it), physics-settled, and every operation converges on ONE
mechanism — a SELECTION (node set) highlighted into the standing layout:

- marquee (shift+drag) / lasso (toolbar mode)  -> GPU-evaluated set (findPointsInRect/Polygon)
- kind click                                    -> that kind's nodes
- lens apply -> canvas                          -> the lens application's node set
- search "highlight all"                        -> the union of the result lists

A selection is the manual dual of a lens application (DEC 57fc5767) — both yield node
sets; 'save selection as lens' is the deliberate next rung once the write path lands.
The default (empty) view IS the read-parity floor: everything on-graph, visibly.

LOD ladder: far = GPU points/edges only -> mid = screen-space constant-size labels for a
degree-ranked sample of VISIBLE points (`getSampledPoints`) -> near = the focused node's
verbatim content in the detail pane (markdown/code/KaTeX renderer carried over from the
cytoscape page); full DOM content cards materialized over the canvas are deferred to the
first user drive. Physics + readability knobs live in a settings drawer, persisted per
graph in localStorage — the DISCOVERY instrument that teaches which knobs deserve
promotion into graph-carried vocabulary (lens `view` / display rules), not a settled
surface.
"""

# Pinned CDN builds — the explorer_page discipline (vendoring stays a later concern).
# cosmos.gl: UMD global `Cosmos` (`new Cosmos.Graph(container, config)`), MIT, WebGL2.
_COSMOS_JS = "https://cdn.jsdelivr.net/npm/@cosmos.gl/graph@3.2.0/dist/index.min.js"
_MARKED_JS = "https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"
_DOMPURIFY_JS = "https://cdn.jsdelivr.net/npm/dompurify@3.1.6/dist/purify.min.js"
_HLJS_JS = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"
_HLJS_CSS = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css"
_KATEX_JS = "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.11/katex.min.js"
_KATEX_AUTO_JS = "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.11/contrib/auto-render.min.js"
_KATEX_CSS = "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.11/katex.min.css"

HYBRID_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Context-graph hybrid explorer</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="__COSMOS_JS__"></script>
<script src="__MARKED_JS__"></script>
<script src="__DOMPURIFY_JS__"></script>
<script src="__HLJS_JS__"></script>
<script src="__KATEX_JS__"></script>
<script src="__KATEX_AUTO_JS__"></script>
<link rel="stylesheet" href="__HLJS_CSS__">
<link rel="stylesheet" href="__KATEX_CSS__">
<style>
  html,body{margin:0;height:100%;font:13px/1.45 system-ui,sans-serif;color:#222;overflow:hidden}
  #bar{padding:8px 12px;border-bottom:1px solid #ddd;display:flex;gap:12px;align-items:center;background:#fff}
  #bar b{font-size:14px}
  #bar select,#bar button{font:inherit;padding:2px 6px}
  #bar button{border:1px solid #ccc;border-radius:4px;background:#fff;cursor:pointer}
  #bar button.on{background:#e4ecff;border-color:#88a}
  #bar .ro{color:#888;margin-left:auto;font-size:12px}
  #cfgpanel{display:none;position:fixed;top:42px;right:10px;background:#fff;border:1px solid #ddd;
            border-radius:6px;padding:10px 14px;z-index:11;box-shadow:0 2px 8px #0002;width:280px}
  #cfgpanel.open{display:block}
  #cfgpanel h4{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:#777;margin:8px 0 4px}
  #cfgpanel h4:first-child{margin-top:0}
  #cfgpanel label{display:flex;align-items:center;gap:8px;padding:2px 0;color:#444;font-size:12px}
  #cfgpanel label>span:first-child{width:88px;flex:none}
  #cfgpanel input[type=range]{flex:1;min-width:0}
  #cfgpanel .val{width:44px;text-align:right;color:#888;flex:none}
  #wrap{display:flex;position:absolute;top:41px;left:0;right:0;bottom:0}
  #side{width:300px;min-width:300px;border-right:1px solid #ddd;overflow-y:auto;padding:10px 12px;
        box-sizing:border-box;background:#fff}
  #side h3{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#777;margin:14px 0 6px}
  #side h3:first-child{margin-top:2px}
  .kind{display:flex;align-items:center;gap:6px;padding:1px 0;color:#333}
  .kind i{width:10px;height:10px;border-radius:50%;display:inline-block;border:1px solid #0002;flex:none}
  .kind .n{color:#999;margin-left:auto}
  .kind.click{cursor:pointer;border-radius:4px}
  .kind.click:hover{background:#f0f4ff}
  .hub{padding:4px 6px;margin:2px -6px;border-radius:5px;cursor:pointer}
  .hub:hover{background:#f0f4ff}
  .hub .t{display:block}
  .hub .m{color:#999;font-size:11px}
  .snip{display:block;color:#777;font-size:11px;margin-top:1px}
  #q{width:100%;box-sizing:border-box;font:inherit;padding:4px 6px;margin:0 0 4px;
     border:1px solid #ccc;border-radius:5px}
  #results .back,.lnk{color:#36c;cursor:pointer;display:inline-block}
  #results .back{margin-bottom:6px}
  #selbox{display:none;border:1px solid #cdd8f0;background:#f4f7ff;border-radius:6px;
          padding:6px 8px;margin:6px 0}
  #selbox.open{display:block}
  #selbox b{font-size:12px}
  #selbox .m{color:#778;font-size:11px;display:block;margin:1px 0 4px}
  #selbox .lnk{margin-right:12px;font-size:12px}
  #main{flex:1;position:relative;overflow:hidden;background:#20242c}
  #cy{position:absolute;inset:0}
  #labels{position:absolute;inset:0;pointer-events:none;overflow:hidden}
  .lbl{position:absolute;transform:translate(-50%,6px);font-size:11px;color:#dde3ee;
       background:rgba(24,27,34,.72);padding:0 4px;border-radius:3px;white-space:nowrap;
       max-width:260px;overflow:hidden;text-overflow:ellipsis;pointer-events:none}
  .lbl.focus{color:#fff;background:rgba(40,60,120,.9);font-size:12px}
  .llbl{position:absolute;font-size:10px;color:#8f9ab5;background:rgba(32,36,44,.8);
        padding:0 3px;border-radius:2px;white-space:nowrap;pointer-events:none}
  .llbl.hl{color:#cdd7f0;background:rgba(40,60,120,.85)}
  #lassocv{position:absolute;inset:0;pointer-events:none}
  #marq{position:absolute;border:1px dashed #9db4ff;background:rgba(120,150,255,.12);
        display:none;pointer-events:none}
  #tip{position:fixed;display:none;background:#111;color:#fff;padding:4px 7px;border-radius:4px;
       font:12px system-ui;max-width:440px;z-index:9;pointer-events:none}
  #dragbar{width:5px;cursor:col-resize;background:#eee;display:none;flex:none}
  #dragbar.open{display:block}
  #detail{width:420px;min-width:280px;border-left:1px solid #ddd;overflow-y:auto;
          padding:10px 14px;box-sizing:border-box;display:none;background:#fff}
  #detail.open{display:block}
  #dhead{display:flex;gap:8px;align-items:baseline;flex-wrap:wrap;margin-bottom:4px}
  #dhead b{font-size:14px}
  #dhead .m{color:#999;font-size:11px;word-break:break-all}
  #dhead button{font:11px system-ui;padding:1px 7px;border:1px solid #ccc;border-radius:4px;
                background:#fff;cursor:pointer;margin-left:auto}
  #dbody{font-size:13px}
  #dbody pre{background:#f6f8fa;padding:8px;border-radius:6px;overflow-x:auto}
  #dbody code{font:12px ui-monospace,SFMono-Regular,Menlo,monospace}
  #dbody img{max-width:100%}
  #dbody h1,#dbody h2,#dbody h3{font-size:15px;margin:14px 0 6px}
  #dbody table{border-collapse:collapse}
  #dbody td,#dbody th{border:1px solid #ddd;padding:2px 6px;vertical-align:top}
  #dbody blockquote{border-left:3px solid #ddd;margin:6px 0;padding:2px 10px;color:#555}
  #dbody .props td:first-child{color:#777;white-space:nowrap}
  .mainprop{white-space:pre-wrap}
  #dnb h3{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#777;margin:14px 0 4px}
  #perf{position:fixed;right:10px;bottom:8px;background:#fff;border:1px solid #ddd;border-radius:5px;
        padding:3px 8px;color:#555;font-size:12px;box-shadow:0 1px 3px #0001;z-index:8}
  #err{position:fixed;left:320px;bottom:8px;color:#f88;font-size:12px;max-width:50%;z-index:8}
</style></head><body>
<div id="bar"><b>Hybrid explorer</b>
  <label>graph <select id="graphs"></select></label>
  <button id="btn-pause" title="pause / resume the simulation">pause</button>
  <button id="btn-heat" title="re-heat the simulation (restart layout energy)">re-heat</button>
  <button id="btn-fit" title="fit the whole graph (or the selection) in view">fit</button>
  <button id="btn-lasso" title="lasso-select mode: draw a loop around nodes (esc cancels)">lasso</button>
  <button id="btn-cfg" title="physics + readability settings">⚙</button>
  <span id="focus" style="color:#666"></span>
  <span class="ro">click = focus · shift+drag = marquee · drag node = move · drag bg = pan</span></div>
<div id="cfgpanel">
  <h4>Physics</h4>
  <label><span>repulsion</span><input type="range" data-k="repulsion" min="0" max="2" step="0.05"><span class="val"></span></label>
  <label><span>link spring</span><input type="range" data-k="spring" min="0" max="3" step="0.05"><span class="val"></span></label>
  <label><span>link distance</span><input type="range" data-k="distance" min="1" max="30" step="1"><span class="val"></span></label>
  <label><span>gravity</span><input type="range" data-k="gravity" min="0" max="1" step="0.02"><span class="val"></span></label>
  <label><span>friction</span><input type="range" data-k="friction" min="0" max="1" step="0.05"><span class="val"></span></label>
  <h4>Readability</h4>
  <label><span>point scale</span><input type="range" data-k="psize" min="0.3" max="4" step="0.1"><span class="val"></span></label>
  <label><span>link width</span><input type="range" data-k="lwidth" min="0.3" max="4" step="0.1"><span class="val"></span></label>
  <label><span>link opacity</span><input type="range" data-k="lopacity" min="0" max="1" step="0.05"><span class="val"></span></label>
  <label><span>labels</span><input type="range" data-k="labelcap" min="0" max="400" step="10"><span class="val"></span></label>
  <label><span>link labels</span><input type="range" data-k="linklabelcap" min="0" max="300" step="10"><span class="val"></span></label>
  <label><span>curved links</span><input type="checkbox" data-k="curved"></label>
  <label><span>arrows</span><input type="checkbox" data-k="arrows"></label>
  <label><span>drag nodes</span><input type="checkbox" data-k="drag"></label>
  <label><span>zoom scaling</span><input type="checkbox" data-k="zoomscale"></label>
  <div style="margin-top:8px"><button id="cfg-reset" style="font:12px system-ui;padding:2px 8px;
    border:1px solid #ccc;border-radius:4px;background:#fff;cursor:pointer">reset to defaults</button></div>
</div>
<div id="wrap">
  <div id="side">
    <input id="q" placeholder="search — relevant + locate + grep (Enter)">
    <div id="selbox"><b>Selection</b><span class="m" id="selmeta"></span>
      <span class="lnk" id="sel-fit">fit</span><span class="lnk" id="sel-list">list</span>
      <span class="lnk" id="sel-clear">clear</span></div>
    <div id="ov"><h3>Kinds</h3><div id="kinds"></div><h3>Lenses</h3><div id="lenses"></div></div>
    <div id="results" style="display:none"></div>
  </div>
  <div id="main">
    <div id="cy"></div>
    <div id="labels"></div>
    <canvas id="lassocv"></canvas>
    <div id="marq"></div>
  </div>
  <div id="dragbar"></div>
  <div id="detail">
    <div id="dhead"><b id="dtitle"></b><span class="m" id="dmeta"></span>
      <button id="draw">raw</button><button id="dclose">✕</button></div>
    <div id="dbody"></div>
    <div id="dnb"></div>
  </div>
</div>
<div id="tip"></div>
<div id="perf">–</div>
<div id="err"></div>
<script>
  const $ = id => document.getElementById(id);
  let graph = null;        // current graph short-name
  let cosmos = null;       // the cosmos.gl Graph instance
  let nodes = [];          // export payload nodes, array order = cosmos point indices
  let idToIdx = new Map(); // node id -> point index
  let deg = null;          // Uint32Array degree per point (drives size + label ranking)
  let linkSrc = null;      // Uint32Array link source index per link
  let linkTgt = null;      // Uint32Array link target index per link
  let linkRels = null;     // relation_type per link (edge labels)
  let selection = null;    // {indices:[...], source:'marquee'|'kind: X'|'lens: y'|...}
  let focusIdx = null;     // clicked node's index (detail pane subject)
  let hlLinks = null;      // Set of highlighted link indices (selection interconnect / focus
                           // neighborhood) — also FILTERS the edge labels to the active set
  let resultsOwner = null; // shared results pane ownership token (the a928a0d8 lesson)

  // Derived kind color: stable name-hash -> HSL — the SAME hash as the cytoscape page,
  // so a kind keeps its color across both explorers. No configured palette, no ontology.
  const kindHue = k => { let h = 0; for (const c of String(k)) h = (h * 31 + c.charCodeAt(0)) >>> 0;
                         return h % 360; };
  const kindColor = k => 'hsl(' + kindHue(k) + ',62%,60%)';
  const kindRgba = k => { // cosmos wants normalized [r,g,b,a] quads
    const h = kindHue(k) / 360, s = 0.62, l = 0.60;
    const f = n => { const t = (n + h * 12) % 12, a = s * Math.min(l, 1 - l);
                     return l - a * Math.max(-1, Math.min(t - 3, 9 - t, 1)); };
    return [f(0), f(8), f(4), 1];
  };
  const short = s => { s = String(s || '').replace(/\s+/g, ' ');
                       return s.length > 91 ? s.slice(0, 90) + '…' : s; };
  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

  async function api(path, verbHint) {
    const t0 = performance.now();
    const r = await fetch(path);
    if (!r.ok) throw new Error(path + ' -> HTTP ' + r.status + ': ' + await r.text());
    const j = await r.json();
    if (j.elapsed_ms !== undefined)
      $('perf').textContent = (j.verb || verbHint) + ' · server ' + j.elapsed_ms + ' ms · round-trip '
                              + Math.round(performance.now() - t0) + ' ms';
    return j.result !== undefined ? j.result : j;
  }
  const fail = e => { $('err').textContent = e.message || String(e); console.error(e); };
  const clearErr = () => { $('err').textContent = ''; };

  // --- Settings: per-graph persisted knobs (localStorage) — the discovery instrument.
  // Anything the user keeps reaching for here is a promotion candidate into
  // graph-carried vocabulary (lens `view` / display rules), not a settled UI surface.
  // Defaults follow the quick-start story's tuning (near-inert friction, curved links,
  // zoom scaling, drag on) with a touch of gravity to bound disconnected components.
  // `friction` here is INTUITIVE (0 = none, 1 = frozen) — cosmos's simulationFriction
  // is velocity RETENTION (1 = keeps moving), so the projection inverts it; the storage
  // namespace is versioned (cfg1) because that semantic flip orphans old stored values.
  const CFG_DEFAULTS = { repulsion: 0.5, spring: 1.0, distance: 4, gravity: 0.1,
                         friction: 0.9, psize: 1.2, lwidth: 1.0, lopacity: 0.5,
                         labelcap: 120, linklabelcap: 60,
                         curved: true, arrows: false, drag: true, zoomscale: true };
  let cfg = { ...CFG_DEFAULTS };
  const cfgKey = () => 'hybrid.cfg1.' + graph;
  function loadCfg() {
    cfg = { ...CFG_DEFAULTS, ...JSON.parse(localStorage.getItem(cfgKey()) || '{}') };
    for (const el of document.querySelectorAll('#cfgpanel [data-k]')) {
      if (el.type === 'checkbox') el.checked = !!cfg[el.dataset.k];
      else { el.value = cfg[el.dataset.k]; el.parentElement.querySelector('.val').textContent = cfg[el.dataset.k]; }
    }
  }
  function cosmosCfg() { // the cfg -> cosmos config projection (live via setConfigPartial)
    return { simulationRepulsion: cfg.repulsion, simulationLinkSpring: cfg.spring,
             simulationLinkDistance: cfg.distance, simulationGravity: cfg.gravity,
             simulationFriction: 1 - cfg.friction,  // intuitive -> cosmos retention
             pointSizeScale: cfg.psize, linkWidthScale: cfg.lwidth,
             linkOpacity: cfg.lopacity, curvedLinks: cfg.curved,
             linkDefaultArrows: cfg.arrows, linkArrowsSizeScale: 2.5,
             enableDrag: cfg.drag, scalePointsOnZoom: cfg.zoomscale };
  }
  function applyCfg(k) {
    localStorage.setItem(cfgKey(), JSON.stringify(cfg));
    if (!cosmos) return;
    cosmos.setConfigPartial(cosmosCfg());
    if (['repulsion', 'spring', 'distance', 'gravity', 'friction'].includes(k) && paused === false)
      cosmos.start(0.35); // physics knobs re-heat gently so the change is FELT
    labelsDirty = true;
  }
  function resetCfg() {
    localStorage.removeItem(cfgKey());
    loadCfg();
    if (cosmos) { cosmos.setConfigPartial(cosmosCfg()); if (!paused) cosmos.start(0.35); }
    labelsDirty = true;
  }

  // --- Canvas boot / graph load -------------------------------------------------
  let paused = false;
  function setPaused(p) { paused = p; $('btn-pause').textContent = p ? 'resume' : 'pause';
                          if (cosmos) p ? cosmos.pause() : cosmos.unpause(); }

  async function loadGraph(name, push = true) {
    graph = name; clearErr();
    $('graphs').value = name;
    focusIdx = null; selection = null; resultsOwner = null;
    $('selbox').classList.remove('open');
    $('results').style.display = 'none'; $('ov').style.display = 'block'; $('q').value = '';
    closeDetail(); clearLabels();
    if (push) history.pushState(null, '', '?g=' + name);
    loadCfg();
    if (cosmos) { cosmos.destroy(); cosmos = null; $('cy').innerHTML = ''; }
    $('focus').textContent = 'loading…';
    let res;
    try { res = await api('/api/g/' + name + '/export', 'export'); }
    catch (e) { fail(e); $('focus').textContent = ''; return; }
    const t0 = performance.now();

    nodes = res.nodes || [];
    idToIdx = new Map(nodes.map((n, i) => [n.id, i]));
    // Drop edges with a dangling endpoint (an edge can outlive its node) — LOUDLY.
    const rawEdges = res.edges || [];
    const edges = rawEdges.filter(e => idToIdx.has(e.source_id) && idToIdx.has(e.target_id));
    if (edges.length < rawEdges.length)
      fail(new Error((rawEdges.length - edges.length) + ' edge(s) reference missing nodes (dropped)'));

    const n = nodes.length;
    deg = new Uint32Array(n);
    linkSrc = new Uint32Array(edges.length);
    linkTgt = new Uint32Array(edges.length);
    linkRels = new Array(edges.length);
    hlLinks = null;
    const links = new Float32Array(edges.length * 2);
    edges.forEach((e, i) => {
      const s = idToIdx.get(e.source_id), t = idToIdx.get(e.target_id);
      links[i * 2] = s; links[i * 2 + 1] = t;
      linkSrc[i] = s; linkTgt[i] = t; linkRels[i] = e.relation_type || '?';
      deg[s]++; deg[t]++;
    });
    const space = (window.Cosmos && Cosmos.defaultConfigValues
                   && Cosmos.defaultConfigValues.spaceSize) || 4096;
    const positions = new Float32Array(n * 2);
    for (let i = 0; i < n * 2; i++) positions[i] = Math.random() * space;
    const colors = new Float32Array(n * 4);
    const sizes = new Float32Array(n);
    const rgbaOf = {};
    for (let i = 0; i < n; i++) {
      const k = nodes[i].label || '?';
      const c = rgbaOf[k] || (rgbaOf[k] = kindRgba(k));
      colors.set(c, i * 4);
      sizes[i] = 2 + 1.6 * Math.log2(1 + deg[i]); // hubs read at a glance
    }

    cosmos = new Cosmos.Graph($('cy'), {
      backgroundColor: '#20242c',
      enableSimulation: true,
      showFPSMonitor: true,           // the foundation eval wants the framerate FELT + read
      renderHoveredPointRing: true,
      hoveredPointRingColor: '#8fb4ff',
      focusedPointRingColor: '#ffd166',
      pointGreyoutOpacity: 0.12,
      linkDefaultColor: '#6b7694',
      linkGreyoutOpacity: 0.04,
      linkVisibilityDistanceRange: [40, 300], // far zoom: links fade, structure stays
      simulationDecay: 3000,
      fitViewOnInit: true, fitViewDelay: 600,
      ...cosmosCfg(),
      onPointClick: (idx) => focusNode(idx),
      onBackgroundClick: () => { if (!selection) clearHighlight(); hideTip(); },
      onPointMouseOver: (idx, pos, ev) => { showTip(idx, ev); hoverPreview(idx); },
      onPointMouseOut: () => { hideTip(); clearHoverPreview(); },
      onZoom: () => { labelsDirty = true; },
      onSimulationTick: () => { labelsDirty = true; },
      onSimulationEnd: () => { labelsDirty = true; },
    });
    cosmos.setPointPositions(positions);
    cosmos.setPointColors(colors);
    cosmos.setPointSizes(sizes);
    cosmos.setLinks(links);
    cosmos.render(1);
    setPaused(false);
    $('focus').textContent = n + ' nodes · ' + edges.length + ' edges · build '
                             + Math.round(performance.now() - t0) + ' ms';
    renderKinds(res);
    loadLenses();
    labelsDirty = true;
  }

  function renderKinds(res) {
    const counts = {};
    for (const nd of nodes) { const k = nd.label || '?'; counts[k] = (counts[k] || 0) + 1; }
    $('kinds').innerHTML = Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([k, c]) =>
      '<div class="kind click" data-k="' + esc(k) + '"><i style="background:' + kindColor(k) + '"></i>'
      + esc(k) + '<span class="n">' + c + '</span></div>').join('') || '<div class="kind">none</div>';
    for (const el of $('kinds').children)
      if (el.dataset.k) el.onclick = () => {
        const idxs = [];
        nodes.forEach((nd, i) => { if ((nd.label || '?') === el.dataset.k) idxs.push(i); });
        setSelection(idxs, 'kind: ' + el.dataset.k);
      };
  }

  // --- Selection: the ONE mechanism every set-producing gesture converges on ----
  // A selection highlights its nodes AND its interconnecting links (the subgraph_view
  // semantics on-canvas): everything else greys to near-invisible, so the member set
  // reads at a glance — the first-drive distinguishability finding.
  function interconnectLinks(indices) {
    const inSet = new Uint8Array(nodes.length);
    for (const i of indices) inSet[i] = 1;
    const out = [];
    for (let li = 0; li < linkSrc.length; li++)
      if (inSet[linkSrc[li]] && inSet[linkTgt[li]]) out.push(li);
    return out;
  }
  function setSelection(indices, source, fit = false) {
    if (!cosmos) return;
    if (!indices.length) { fail(new Error(source + ': empty selection')); return; }
    const links = interconnectLinks(indices);
    selection = { indices, source };
    hlLinks = new Set(links);
    cosmos.setConfigPartial({ highlightedPointIndices: indices,
                              highlightedLinkIndices: links,
                              outlinedPointIndices: undefined,
                              linkGreyoutOpacity: 0.04,
                              focusedPointIndex: undefined });
    $('selbox').classList.add('open');
    $('selmeta').textContent = indices.length + ' node(s) · ' + links.length
                               + ' link(s) · ' + source;
    if (fit) cosmos.fitViewByPointIndices(indices, 350, 0.2);
    labelsDirty = true;
  }
  function clearSelection() {
    selection = null;
    $('selbox').classList.remove('open');
    clearHighlight();
  }
  function clearHighlight() {
    if (cosmos) cosmos.setConfigPartial({ highlightedPointIndices: undefined,
                                          highlightedLinkIndices: undefined,
                                          outlinedPointIndices: undefined,
                                          focusedPointIndex: undefined });
    focusIdx = null;
    hlLinks = null;
    labelsDirty = true;
  }

  // Hover preview (the explore-connections idiom): outline the neighborhood + surface
  // its links, WITHOUT touching an active selection/focus — a soft look-ahead only.
  function hoverPreview(idx) {
    if (!cosmos || selection || focusIdx != null || drag) return;
    const nb = cosmos.getNeighboringPointIndices(idx) || [];
    cosmos.setConfigPartial({ outlinedPointIndices: [idx, ...nb],
                              highlightedLinkIndices: cosmos.getConnectedLinkIndices(idx),
                              linkGreyoutOpacity: 0.3 });
  }
  function clearHoverPreview() {
    if (!cosmos || selection || focusIdx != null) return;
    cosmos.setConfigPartial({ outlinedPointIndices: undefined,
                              highlightedLinkIndices: undefined,
                              linkGreyoutOpacity: 0.04 });
  }
  function listSelection() {
    if (!selection) return;
    resultsOwner = 'selection';
    $('ov').style.display = 'none';
    const out = $('results');
    out.style.display = 'block';
    const rows = selection.indices.slice(0, 500);
    out.innerHTML = '<span class="back">← overview</span>'
      + '<h3>Selection · ' + selection.indices.length + ' node(s) · ' + esc(selection.source)
      + (selection.indices.length > 500 ? ' · first 500' : '') + '</h3>'
      + rows.map(i => resultRow(nodes[i], null)).join('');
    wireResults(out);
  }

  // --- Focus: click a point -> ring + neighbor highlight (sans selection) + detail
  async function focusNode(idx, push = true) {
    if (!cosmos || idx == null || !nodes[idx]) return;
    clearErr();
    focusIdx = idx;
    const id = nodes[idx].id;
    if (!selection) {
      const nb = cosmos.getNeighboringPointIndices(idx) || [];
      const neighborhood = [idx, ...nb];
      const links = cosmos.getConnectedLinkIndices(neighborhood) || [];
      hlLinks = new Set(links);
      cosmos.setConfigPartial({ highlightedPointIndices: neighborhood,
                                highlightedLinkIndices: links,
                                outlinedPointIndices: undefined,
                                linkGreyoutOpacity: 0.04,
                                focusedPointIndex: idx });
    } else {
      cosmos.setConfigPartial({ focusedPointIndex: idx });
    }
    labelsDirty = true;
    if (push) history.pushState(null, '', '?g=' + graph + '&focus=' + encodeURIComponent(id));
    await loadDetail(id);
  }
  const focusById = id => { const i = idToIdx.get(id);
    if (i === undefined) { fail(new Error('node not on canvas: ' + id)); return; }
    cosmos.zoomToPointByIndex(i, 500, 6); focusNode(i); };

  // --- Labels overlay: constant-size DOM labels for a degree-ranked sample of
  // VISIBLE points (getSampledPoints = cosmos's evenly-distributed screen sample),
  // plus the focused point always. Updated via one rAF loop gated on a dirty flag
  // (zoom/tick/config), so an idle settled canvas costs nothing.
  let labelsDirty = true, lastLabelTs = 0;
  const labelPool = [];
  const llblPool = [];  // link (relation) labels — same pooling, rotated placement
  function clearLabels() { for (const el of labelPool) el.style.display = 'none';
                           for (const el of llblPool) el.style.display = 'none'; }
  function updateLabels(ts) {
    requestAnimationFrame(updateLabels);
    if (!cosmos || !labelsDirty || ts - lastLabelTs < 90) return;
    labelsDirty = false; lastLabelTs = ts;
    const cap = cfg.labelcap;
    let picked = [];
    if (cap > 0) {
      const s = cosmos.getSampledPoints();
      const idxs = s.indices || [];
      const pos = s.positions || [];
      const byDeg = idxs.map((p, j) => [p, pos[j * 2], pos[j * 2 + 1]])
                        .sort((a, b) => deg[b[0]] - deg[a[0]]).slice(0, cap);
      picked = byDeg;
    }
    if (focusIdx != null) {
      const tracked = cosmos.getPointPositions();
      if (tracked && tracked.length >= focusIdx * 2 + 2
          && !picked.some(p => p[0] === focusIdx))
        picked.push([focusIdx, tracked[focusIdx * 2], tracked[focusIdx * 2 + 1]]);
    }
    const box = $('main').getBoundingClientRect();
    let li = 0;
    for (const [p, x, y] of picked) {
      const [sx, sy] = cosmos.spaceToScreenPosition([x, y]);
      if (sx < -40 || sy < -20 || sx > box.width + 40 || sy > box.height + 20) continue;
      let el = labelPool[li];
      if (!el) { el = document.createElement('div'); el.className = 'lbl';
                 $('labels').appendChild(el); labelPool.push(el); }
      el.style.display = 'block';
      el.style.left = sx + 'px';
      el.style.top = sy + 'px';
      el.className = p === focusIdx ? 'lbl focus' : 'lbl';
      el.textContent = nodes[p] ? short(nodes[p].title).slice(0, 60) : '';
      li++;
    }
    for (; li < labelPool.length; li++) labelPool[li].style.display = 'none';

    // Link (relation) labels: the sampled VISIBLE links (evenly distributed, one per
    // screen cell), midpoint-anchored and rotated along the link (never upside down —
    // the link-sampling story's normalization). An active highlight set FILTERS the
    // labels to its own links, so a selection/lens/focus names its relations and the
    // rest of the graph stays quiet.
    let lj = 0;
    if (cfg.linklabelcap > 0 && linkRels) {
      const s = cosmos.getSampledLinks();
      const idxs = s.indices || [], pos = s.positions || [], angs = s.angles || [];
      let list = [];
      for (let j = 0; j < idxs.length; j++)
        list.push([idxs[j], pos[j * 2], pos[j * 2 + 1], angs[j]]);
      if (hlLinks) list = list.filter(x => hlLinks.has(x[0]));
      list = list.slice(0, cfg.linklabelcap);
      for (const [l, x, y, a] of list) {
        const [sx, sy] = cosmos.spaceToScreenPosition([x, y]);
        if (sx < -40 || sy < -20 || sx > box.width + 40 || sy > box.height + 20) continue;
        let el = llblPool[lj];
        if (!el) { el = document.createElement('div'); el.className = 'llbl';
                   $('labels').appendChild(el); llblPool.push(el); }
        let d = (a || 0) * 180 / Math.PI;
        while (d > 90) d -= 180;
        while (d <= -90) d += 180;
        el.style.display = 'block';
        el.style.left = sx + 'px';
        el.style.top = sy + 'px';
        el.style.transform = 'translate(-50%,-50%) rotate(' + d.toFixed(1) + 'deg)';
        el.className = hlLinks ? 'llbl hl' : 'llbl';
        el.textContent = linkRels[l];
        lj++;
      }
    }
    for (; lj < llblPool.length; lj++) llblPool[lj].style.display = 'none';
  }
  requestAnimationFrame(updateLabels);

  // --- Hover tooltip (full title + kind — labels truncate) ----------------------
  function showTip(idx, ev) {
    if (!nodes[idx]) return;
    const tip = $('tip');
    tip.textContent = nodes[idx].title + '  [' + (nodes[idx].label || '?') + ']';
    tip.style.display = 'block';
    if (ev && ev.clientX !== undefined) {
      tip.style.left = (ev.clientX + 12) + 'px'; tip.style.top = (ev.clientY + 12) + 'px';
    }
  }
  function hideTip() { $('tip').style.display = 'none'; }

  // --- Marquee (shift+drag) + lasso (toolbar mode): screen region -> node set.
  // findPointsInRect/Polygon are GPU-evaluated and take canvas pixel coords, so both
  // handlers only translate pointer events. Handlers run in CAPTURE phase and stop
  // propagation so cosmos's own pan/zoom never sees a selection drag.
  let lassoMode = false;
  function setLasso(on) { lassoMode = on;
    $('btn-lasso').classList.toggle('on', on);
    $('main').style.cursor = on ? 'crosshair' : ''; }
  $('btn-lasso').onclick = () => setLasso(!lassoMode);
  addEventListener('keydown', e => { if (e.key === 'Escape') setLasso(false); });

  const mainEl = $('main');
  let drag = null; // {mode:'marq'|'lasso', x0,y0, path:[[x,y],...]}
  const localXY = ev => { const r = mainEl.getBoundingClientRect();
                          return [ev.clientX - r.left, ev.clientY - r.top]; };
  mainEl.addEventListener('pointerdown', ev => {
    if (!cosmos) return;
    const wantMarq = ev.shiftKey && !lassoMode;
    if (!wantMarq && !lassoMode) return;
    ev.preventDefault(); ev.stopPropagation();
    const [x, y] = localXY(ev);
    drag = lassoMode ? { mode: 'lasso', path: [[x, y]] } : { mode: 'marq', x0: x, y0: y };
    mainEl.setPointerCapture(ev.pointerId);
    if (drag.mode === 'lasso') {
      const cv = $('lassocv');
      cv.width = mainEl.clientWidth; cv.height = mainEl.clientHeight;
    }
  }, true);
  mainEl.addEventListener('pointermove', ev => {
    if (!drag) return;
    ev.preventDefault(); ev.stopPropagation();
    const [x, y] = localXY(ev);
    if (drag.mode === 'marq') {
      const m = $('marq');
      m.style.display = 'block';
      m.style.left = Math.min(drag.x0, x) + 'px'; m.style.top = Math.min(drag.y0, y) + 'px';
      m.style.width = Math.abs(x - drag.x0) + 'px'; m.style.height = Math.abs(y - drag.y0) + 'px';
    } else {
      drag.path.push([x, y]);
      const ctx = $('lassocv').getContext('2d');
      ctx.clearRect(0, 0, $('lassocv').width, $('lassocv').height);
      ctx.strokeStyle = '#9db4ff'; ctx.lineWidth = 1.5; ctx.setLineDash([5, 4]);
      ctx.beginPath();
      ctx.moveTo(drag.path[0][0], drag.path[0][1]);
      for (const [px, py] of drag.path) ctx.lineTo(px, py);
      ctx.stroke();
    }
  }, true);
  mainEl.addEventListener('pointerup', ev => {
    if (!drag) return;
    ev.preventDefault(); ev.stopPropagation();
    const [x, y] = localXY(ev);
    try {
      if (drag.mode === 'marq') {
        $('marq').style.display = 'none';
        const rect = [[Math.min(drag.x0, x), Math.min(drag.y0, y)],
                      [Math.max(drag.x0, x), Math.max(drag.y0, y)]];
        if (Math.abs(x - drag.x0) > 4 && Math.abs(y - drag.y0) > 4)
          setSelection(cosmos.findPointsInRect(rect), 'marquee');
      } else {
        const cv = $('lassocv');
        cv.getContext('2d').clearRect(0, 0, cv.width, cv.height);
        if (drag.path.length >= 3)
          setSelection(cosmos.findPointsInPolygon(drag.path), 'lasso');
        setLasso(false); // one-shot: draw a loop, get a set
      }
    } catch (e) { fail(e); }
    drag = null;
  }, true);

  // --- Lens shelf (ported): apply -> results lists; apply -> canvas SELECTS the
  // lens application's node set into the standing layout (highlight + fit) — the
  // full-graph grammar's lens move (vs the cytoscape page's element replacement).
  async function loadLenses() {
    try {
      const res = await api('/api/g/' + graph + '/lenses', 'lenses');
      const lenses = res.lenses || [];
      $('lenses').innerHTML = lenses.map((l, i) =>
        '<div class="hub" style="cursor:default">'
        + '<span class="t">' + esc(l.title) + '</span>'
        + (l.description ? '<span class="m">' + esc(l.description) + '</span>' : '')
        + (l.params || []).map(p =>
            '<input style="width:100%;box-sizing:border-box;font:inherit;padding:2px 4px;'
            + 'margin:2px 0" data-lens="' + i + '" data-p="' + esc(p.name) + '" placeholder="'
            + esc(p.name + (p.type && p.type !== 'string' ? ' (' + p.type + ')' : '')
                  + (p.required ? ' *' : '')) + '">').join('')
        + '<div style="display:flex;gap:12px;margin-top:4px">'
        + '<span class="lnk" data-apply="' + i + '">apply → results</span>'
        + '<span class="lnk" data-canvas="' + i + '">apply → canvas</span></div>'
        + '</div>').join('') || '<div class="hub">none authored yet (cg-write set-lens)</div>';
      const paramsOf = i => {
        const ps = {};
        for (const inp of $('lenses').querySelectorAll('input[data-lens="' + i + '"]'))
          if (inp.value.trim()) ps[inp.dataset.p] = inp.value.trim();
        return ps;
      };
      for (const el of $('lenses').querySelectorAll('[data-apply]'))
        el.onclick = () => doLens(lenses[+el.dataset.apply].slug, paramsOf(+el.dataset.apply), false);
      for (const el of $('lenses').querySelectorAll('[data-canvas]'))
        el.onclick = () => doLens(lenses[+el.dataset.canvas].slug, paramsOf(+el.dataset.canvas), true);
    } catch (e) { $('lenses').innerHTML = ''; }
  }

  async function doLens(slug, params, toCanvas) {
    clearErr();
    const qs = Object.entries(params || {})
      .map(([k, v]) => encodeURIComponent(k) + '=' + encodeURIComponent(v)).join('&');
    try {
      const res = await api('/api/g/' + graph + '/lens/' + encodeURIComponent(slug)
                            + (qs ? '?' + qs : ''), 'lens');
      if (toCanvas) {
        const idxs = [];
        const offCanvas = [];
        for (const nd of res.nodes || []) {
          const i = idToIdx.get(nd.id);
          if (i === undefined) offCanvas.push(nd.id); else idxs.push(i);
        }
        if (offCanvas.length) // read-parity: a lens hit missing from the canvas is LOUD
          fail(new Error(offCanvas.length + ' lens node(s) not on canvas (stale export? reload)'));
        setSelection(idxs, 'lens: ' + slug, true);
        return;
      }
      resultsOwner = 'lens';
      $('ov').style.display = 'none';
      const out = $('results');
      out.style.display = 'block';
      out.innerHTML = '<span class="back">← overview</span>'
        + '<h3>Lens · ' + esc(res.title || slug) + ' · ' + (res.nodes || []).length + ' node(s)'
        + (res.missing && res.missing.length ? ' · ⚠ ' + res.missing.length + ' missing' : '')
        + '</h3>'
        + '<div class="m" style="padding:2px 0 6px">'
        + esc((res.clauses || []).map(c => c.verb + '×' + c.selected).join(' ∪ '))
        + '</div>'
        + (res.nodes || []).map(nd => resultRow(nd, nd.expanded ? 'expanded' : 'selected')).join('');
      wireResults(out);
    } catch (e) { fail(e); }
  }

  // --- Search (ported) + "highlight all" -> selection ---------------------------
  function resultRow(r, extra, snip) {
    return '<div class="hub" data-id="' + r.id + '"><span class="t">' + esc(short(r.title)) + '</span>'
      + '<span class="m">' + esc(r.label || '?') + (extra ? ' · ' + esc(extra) : '') + '</span>'
      + (snip ? '<span class="snip">' + esc(snip) + '</span>' : '') + '</div>';
  }
  function wireResults(out) {
    const b = out.querySelector('.back');
    if (b) b.onclick = () => { out.style.display = 'none'; $('ov').style.display = 'block'; };
    for (const el of out.querySelectorAll('.hub'))
      if (el.dataset.id) el.onclick = () => focusById(el.dataset.id);
  }

  async function doSearch(q) {
    clearErr();
    resultsOwner = 'search';
    $('ov').style.display = 'none';
    const out = $('results');
    out.style.display = 'block';
    out.innerHTML = '<span style="color:#999">searching…</span>';
    try {
      const [loc, gr, rel] = await Promise.all([
        api('/api/g/' + graph + '/locate?term=' + encodeURIComponent(q), 'locate'),
        api('/api/g/' + graph + '/grep?term=' + encodeURIComponent(q), 'grep'),
        api('/api/g/' + graph + '/relevant?task=' + encodeURIComponent(q), 'relevant')]);
      const all = new Set();
      for (const m of (loc.matches || [])) all.add(m.id);
      for (const m of (gr.matches || [])) all.add(m.id);
      for (const r of (rel.results || [])) all.add(r.id);
      out.innerHTML = '<span class="back">← overview</span> '
        + '<span class="lnk" id="selall">⦿ select all (' + all.size + ')</span>'
        + '<h3>Locate (' + loc.count + ')</h3>'
        + (loc.matches || []).map(m => resultRow(m, (m.path || '').split('/').slice(-1)[0])).join('')
        + '<h3>Content — grep (' + gr.count + (gr.truncated ? '+' : '') + ')</h3>'
        + (gr.matches || []).map(m => resultRow(m, m.field, m.snippet)).join('')
        + '<h3>Relevant (' + rel.total_hits + ' reached)</h3>'
        + (rel.results || []).map(r => resultRow(r, 'score ' + r.score)).join('');
      wireResults(out);
      $('selall').onclick = () => {
        const idxs = [...all].map(id => idToIdx.get(id)).filter(i => i !== undefined);
        setSelection(idxs, 'search: ' + q, true);
      };
    } catch (e) { out.innerHTML = ''; fail(e); }
  }

  // --- Detail pane (renderer carried over from the cytoscape page) --------------
  let lastRead = null;
  function openDetail() { $('detail').classList.add('open'); $('dragbar').classList.add('open'); }
  function closeDetail() { $('detail').classList.remove('open'); $('dragbar').classList.remove('open'); }

  function fmTable(fm) {
    return '<table class="props">' + fm.split('\n').filter(l => l.trim()).map(l => {
      const i = l.indexOf(':');
      const k = i < 0 ? '' : l.slice(0, i), v = i < 0 ? l : l.slice(i + 1);
      return '<tr><td>' + esc(k.trim()) + '</td><td>' + esc(v.trim()) + '</td></tr>';
    }).join('') + '</table>';
  }

  function renderDetail(rd, props, raw) {
    const body = $('dbody');
    if (rd && !rd.error && rd.text) {
      if (raw) { body.innerHTML = '<pre class="mainprop"><code></code></pre>';
                 body.querySelector('code').textContent = rd.text; return; }
      if (!['slot', 'module', 'notebook', 'nested'].includes(rd.kind)) {
        let text = rd.text, fm = null;
        const m = rd.kind === 'note' && text.match(/^---\n([\s\S]*?)\n---\n?/);
        if (m) { fm = m[1]; text = text.slice(m[0].length); }
        body.innerHTML = (fm ? fmTable(fm) : '') + DOMPurify.sanitize(marked.parse(text));
        if (window.renderMathInElement) renderMathInElement(body, { throwOnError: false,
          delimiters: [{left: '$$', right: '$$', display: true},
                       {left: '\\[', right: '\\]', display: true},
                       {left: '\\(', right: '\\)', display: false},
                       {left: '$', right: '$', display: false}],
          ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code'] });
      } else {
        let hl; try { hl = hljs.highlight(rd.text, { language: 'python' }).value; }
        catch (e) { hl = hljs.highlightAuto(rd.text).value; }
        body.innerHTML = '<pre><code class="hljs">' + hl + '</code></pre>';
      }
      return;
    }
    const note = rd && rd.error ? '<p class="m" style="color:#999">' + esc(rd.error) + '</p>' : '';
    const str = v => typeof v === 'string' ? v : JSON.stringify(v);
    const entries = Object.entries(props || {});
    let main = null;
    for (const [k, v] of entries)
      if (typeof v === 'string' && v.length > 600 && (!main || v.length > main[1].length)) main = [k, v];
    const rows = entries.filter(([k]) => !main || k !== main[0]);
    body.innerHTML = note + '<table class="props">' + rows.map(([k, v], i) => {
      const s = str(v);
      return '<tr><td>' + esc(k) + '</td><td class="v" data-i="' + i + '">'
        + esc(s.length > 300 ? s.slice(0, 299) + '…' : s) + '</td></tr>';
    }).join('') + '</table>'
      + (main ? '<h3>' + esc(main[0]) + '</h3><pre class="mainprop"></pre>' : '');
    if (main) body.querySelector('.mainprop').textContent = main[1];
    for (const td of body.querySelectorAll('td.v')) {
      const s = str(rows[+td.dataset.i][1]);
      if (s.length <= 300) continue;
      td.style.cursor = 'pointer'; td.title = 'click to expand/collapse';
      td.onclick = () => { const x = td.dataset.x === '1';
        td.textContent = x ? s.slice(0, 299) + '…' : s; td.dataset.x = x ? '0' : '1'; };
    }
  }

  async function loadDetail(id) {
    openDetail();
    $('dtitle').textContent = '…'; $('dmeta').textContent = id;
    $('dbody').innerHTML = '<span style="color:#999">reading…</span>'; $('dnb').innerHTML = '';
    try {
      const res = await api('/api/g/' + graph + '/show/' + encodeURIComponent(id) + '?depth=1', 'show');
      if (res.error) throw new Error(res.error);
      $('dtitle').textContent = short(res.node.title);
      $('dmeta').textContent = (res.node.label || '?') + ' · ' + res.node.id;
      let rd = null;
      try { rd = await api('/api/g/' + graph + '/read/' + encodeURIComponent(id), 'read'); }
      catch (e) { rd = { error: e.message || String(e) }; }
      lastRead = { rd, props: res.properties || {}, raw: false };
      renderDetail(rd, lastRead.props, false);
      // Neighbour rows: the canvas shows the neighborhood highlighted IN PLACE;
      // the pane names each neighbour + relation and clicks through (navigation
      // without ever tearing down the standing layout).
      const nbs = res.neighbours || [];
      $('dnb').innerHTML = nbs.length
        ? '<h3>Neighbours (' + nbs.length + ')</h3>'
          + nbs.map(nb => resultRow(nb.node,
              (nb.direction === 'out' ? '—' + nb.relation + '→' : '←' + nb.relation + '—'))).join('')
        : '';
      wireResults($('dnb'));
    } catch (e) { $('dbody').innerHTML = ''; fail(e); }
  }

  // --- Boot ----------------------------------------------------------------------
  async function boot() {
    try {
      const graphs = await api('/api/graphs', 'graphs');
      $('graphs').innerHTML = graphs.map(g =>
        '<option value="' + g.name + '" title="' + g.path + '">' + g.name + '</option>').join('');
      $('graphs').onchange = () => loadGraph($('graphs').value);
      $('btn-pause').onclick = () => setPaused(!paused);
      $('btn-heat').onclick = () => { if (cosmos) { setPaused(false); cosmos.start(0.8); } };
      $('btn-fit').onclick = () => { if (!cosmos) return;
        selection ? cosmos.fitViewByPointIndices(selection.indices, 350, 0.2)
                  : cosmos.fitView(350); };
      $('btn-cfg').onclick = () => $('cfgpanel').classList.toggle('open');
      $('cfg-reset').onclick = resetCfg;
      for (const el of document.querySelectorAll('#cfgpanel [data-k]')) {
        const k = el.dataset.k;
        el.oninput = () => {
          cfg[k] = el.type === 'checkbox' ? el.checked : +el.value;
          const v = el.parentElement.querySelector('.val');
          if (v) v.textContent = cfg[k];
          applyCfg(k);
        };
      }
      $('sel-fit').onclick = () => selection && cosmos.fitViewByPointIndices(selection.indices, 350, 0.2);
      $('sel-list').onclick = listSelection;
      $('sel-clear').onclick = clearSelection;
      $('q').onkeydown = e => { if (e.key === 'Enter' && $('q').value.trim()) doSearch($('q').value.trim()); };
      $('dclose').onclick = closeDetail;
      $('draw').onclick = () => { if (lastRead) { lastRead.raw = !lastRead.raw;
        renderDetail(lastRead.rd, lastRead.props, lastRead.raw); } };
      $('dragbar').onmousedown = e => {
        e.preventDefault();
        const move = ev => { $('detail').style.width = Math.max(280,
          Math.min(window.innerWidth - 520, window.innerWidth - ev.clientX)) + 'px'; };
        const up = () => { removeEventListener('mousemove', move); removeEventListener('mouseup', up); };
        addEventListener('mousemove', move); addEventListener('mouseup', up);
      };
      window.onpopstate = async () => {
        const q2 = new URLSearchParams(location.search);
        const g2 = q2.get('g') || graphs[0].name;
        if (g2 !== graph) await loadGraph(g2, false);
        if (q2.get('focus') && idToIdx.has(q2.get('focus'))) focusById(q2.get('focus'));
      };
      const q = new URLSearchParams(location.search);
      const start = graphs.find(g => g.name === q.get('g')) || graphs[0];
      if (start) {
        await loadGraph(start.name, false);
        // A focus deep-link waits for the layout to breathe before zooming in.
        if (q.get('focus') && idToIdx.has(q.get('focus')))
          setTimeout(() => focusById(q.get('focus')), 900);
        history.replaceState(null, '', '?g=' + start.name
          + (q.get('focus') ? '&focus=' + encodeURIComponent(q.get('focus')) : ''));
      }
    } catch (e) { fail(e); }
  }
  boot();
</script></body></html>
""".replace("__COSMOS_JS__", _COSMOS_JS) \
   .replace("__MARKED_JS__", _MARKED_JS).replace("__DOMPURIFY_JS__", _DOMPURIFY_JS) \
   .replace("__HLJS_JS__", _HLJS_JS).replace("__HLJS_CSS__", _HLJS_CSS) \
   .replace("__KATEX_JS__", _KATEX_JS).replace("__KATEX_AUTO_JS__", _KATEX_AUTO_JS) \
   .replace("__KATEX_CSS__", _KATEX_CSS)
