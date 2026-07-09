"""The graph EXPLORER client page — the first client of the `serve` data API.

One self-contained HTML page (Cytoscape.js from pinned CDNs, no build step), held as a
Python string so the page itself lives ON-GRAPH like every other arc-lib artifact (a
`.html` asset would be invisible to the code decomposer). Deliberately a CLIENT: it knows
the `/api/…` shapes and nothing else; all graph truth arrives from the read verbs.

Increment 1 (neighborhood explorer): graph switcher (the multi-graph corpus one click
apart) -> `overview` boot view (discovered kinds as a colored legend + hub anchors) ->
tap a hub to render its `show` depth-1 neighborhood (focus centered, nodes colored by
discovered kind, relation-labeled edges) -> tap any node to re-focus on IT. A perf readout
(server `elapsed_ms` + round-trip) rides every interaction — the felt probe. Kind colors
are derived (name-hash -> HSL), never configured: no ontology baked in.
"""

# Pinned CDN builds — same discipline as the minimal viz (vendoring stays a later concern).
# fcose (force-directed, space-efficient on large neighborhoods — user feedback on the
# concentric hub-and-spoke) needs its two base libs loaded first; the UMD self-registers.
_CYTOSCAPE_JS = "https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.30.2/cytoscape.min.js"
_LAYOUT_BASE_JS = "https://cdn.jsdelivr.net/npm/layout-base@2.0.1/layout-base.js"
_COSE_BASE_JS = "https://cdn.jsdelivr.net/npm/cose-base@2.2.0/cose-base.js"
_FCOSE_JS = "https://cdn.jsdelivr.net/npm/cytoscape-fcose@2.2.0/cytoscape-fcose.js"
# Detail-pane rendering: markdown (marked, sanitized by DOMPurify) + code highlighting
# + LaTeX (KaTeX auto-render — the posts corpus carries math).
_MARKED_JS = "https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"
_DOMPURIFY_JS = "https://cdn.jsdelivr.net/npm/dompurify@3.1.6/dist/purify.min.js"
_HLJS_JS = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"
_HLJS_CSS = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css"
_KATEX_JS = "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.11/katex.min.js"
_KATEX_AUTO_JS = "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.11/contrib/auto-render.min.js"
_KATEX_CSS = "https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.11/katex.min.css"

EXPLORER_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Context-graph explorer</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="__CYTOSCAPE_JS__"></script>
<script src="__LAYOUT_BASE_JS__"></script>
<script src="__COSE_BASE_JS__"></script>
<script src="__FCOSE_JS__"></script>
<script src="__MARKED_JS__"></script>
<script src="__DOMPURIFY_JS__"></script>
<script src="__HLJS_JS__"></script>
<script src="__KATEX_JS__"></script>
<script src="__KATEX_AUTO_JS__"></script>
<link rel="stylesheet" href="__HLJS_CSS__">
<link rel="stylesheet" href="__KATEX_CSS__">
<style>
  html,body{margin:0;height:100%;font:13px/1.45 system-ui,sans-serif;color:#222;overflow:hidden}
  #bar{padding:8px 12px;border-bottom:1px solid #ddd;display:flex;gap:14px;align-items:center}
  #bar b{font-size:14px}
  #bar select{font:inherit;padding:2px 4px}
  #bar .ro{color:#888;margin-left:auto}
  #lcfg{font:13px system-ui;border:1px solid #ccc;border-radius:4px;background:#fff;
        cursor:pointer;padding:1px 7px}
  #lpanel{display:none;position:fixed;top:42px;left:340px;background:#fff;border:1px solid #ddd;
          border-radius:6px;padding:8px 12px;z-index:11;box-shadow:0 2px 8px #0002}
  #lpanel.open{display:block}
  #lpanel label{display:flex;align-items:center;gap:8px;padding:2px 0;color:#444}
  #lpanel span{width:34px;text-align:right;color:#888}
  #wrap{display:flex;position:absolute;top:41px;left:0;right:0;bottom:0}
  #side{width:300px;min-width:300px;border-right:1px solid #ddd;overflow-y:auto;padding:10px 12px;box-sizing:border-box}
  #side h3{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#777;margin:14px 0 6px}
  #side h3:first-child{margin-top:2px}
  .kind{display:flex;align-items:center;gap:6px;padding:1px 0;color:#333}
  .kind i{width:10px;height:10px;border-radius:50%;display:inline-block;border:1px solid #0002;flex:none}
  .kind .n{color:#999;margin-left:auto}
  .hub{padding:4px 6px;margin:2px -6px;border-radius:5px;cursor:pointer}
  .hub:hover{background:#f0f4ff}
  .hub .t{display:block}
  .hub .m{color:#999;font-size:11px}
  .kind.click{cursor:pointer;border-radius:4px}
  .kind.click:hover{background:#f0f4ff}
  #rels label{display:flex;align-items:center;gap:6px;padding:1px 0;color:#333;cursor:pointer}
  #rels input{margin:0}
  #rels .n{color:#999;margin-left:auto}
  #q{width:100%;box-sizing:border-box;font:inherit;padding:4px 6px;margin:0 0 4px;
     border:1px solid #ccc;border-radius:5px}
  #results .back{color:#36c;cursor:pointer;display:inline-block;margin-bottom:6px}
  #results input{width:100%;box-sizing:border-box;font:inherit;padding:3px 6px;margin:4px 0;
                 border:1px solid #ccc;border-radius:5px}
  #results .nav{display:flex;gap:12px;margin-top:6px}
  .snip{display:block;color:#777;font-size:11px;margin-top:1px}
  #dragbar{width:5px;cursor:col-resize;background:#eee;display:none;flex:none}
  #dragbar.open{display:block}
  .mainprop{white-space:pre-wrap}
  #cy{flex:1;overflow:hidden;position:relative}
  #detail{width:420px;min-width:280px;border-left:1px solid #ddd;overflow-y:auto;
          padding:10px 14px;box-sizing:border-box;display:none}
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
  #perf{position:fixed;right:10px;bottom:8px;background:#fff;border:1px solid #ddd;border-radius:5px;
        padding:3px 8px;color:#555;font-size:12px;box-shadow:0 1px 3px #0001}
  #err{position:fixed;left:320px;bottom:8px;color:#b00;font-size:12px;max-width:50%}
</style></head><body>
<div id="bar"><b>Context-graph explorer</b>
  <label>graph <select id="graphs"></select></label>
  <label>layout <select id="layout">
    <option value="fcose">fcose</option><option value="concentric">concentric</option>
  </select></label>
  <button id="lcfg" title="layout tuning">⚙</button>
  <div id="lpanel">
    <label>spacing ×<input type="range" id="cfg-spacing" min="0.3" max="3" step="0.1"><span></span></label>
    <label>repulsion ×<input type="range" id="cfg-repulsion" min="0.3" max="3" step="0.1"><span></span></label>
    <label>separation <input type="range" id="cfg-separation" min="40" max="400" step="10"><span></span></label>
  </div>
  <span id="focus" style="color:#666"></span>
  <span class="ro">read-only · tap = focus · shift+tap = expand in place · scroll to zoom</span></div>
<div id="wrap">
  <div id="side">
    <input id="q" placeholder="search — relevant + locate (Enter)">
    <div id="ov"><h3 id="relsh3" style="display:none">Relations</h3><div id="rels"></div>
      <h3>Kinds</h3><div id="kinds"></div><h3>Hubs</h3><div id="hubs"></div>
      <h3>Session window</h3><div id="sess">
        <select id="sess-pick" style="width:100%;margin:2px 0"><option value="">— pick a session —</option></select>
        <input id="sess-start" placeholder="start (YYYY-MM-DD[_HH-MM-SS] | unix)" style="width:100%;margin:2px 0">
        <input id="sess-end" placeholder="end (blank = open/live)" style="width:100%;margin:2px 0">
        <div style="display:flex;gap:8px;align-items:center;margin:2px 0">
          <button id="sess-go">window</button>
          <label style="font-size:12px;color:#555;display:flex;gap:4px;align-items:center">
            <input type="checkbox" id="sess-live"> live (5s)</label>
        </div></div></div>
    <div id="results" style="display:none"></div>
  </div>
  <div id="cy"></div>
  <div id="dragbar"></div>
  <div id="detail">
    <div id="dhead"><b id="dtitle"></b><span class="m" id="dmeta"></span>
      <button id="draw">raw</button><button id="dclose">✕</button></div>
    <div id="dbody"></div>
  </div>
</div>
<div id="perf">–</div>
<div id="err"></div>
<script>
  const $ = id => document.getElementById(id);
  let graph = null, cy = null;

  // Derived kind color: stable name-hash -> HSL. No configured palette, no ontology.
  // (Comma syntax: Cytoscape's color parser rejects modern space-separated hsl().)
  const kindColor = k => { let h = 0; for (const c of String(k)) h = (h * 31 + c.charCodeAt(0)) >>> 0;
                           return 'hsl(' + (h % 360) + ',62%,60%)'; };
  // 90 chars ≈ 2-3 wrapped canvas lines (text-max-width 150 @ font-size 10) — sized for
  // the display-rule composed titles ("task_state @ FINDING (…)"), not bare slugs.
  const short = s => { s = String(s || '').replace(/\s+/g, ' ');
                       return s.length > 91 ? s.slice(0, 90) + '…' : s; };

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

  function renderOverview(ov) {
    const counts = (ov.schema && ov.schema.counts) || {};
    $('kinds').innerHTML = Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([k, n]) =>
      '<div class="kind click" data-k="' + esc(k) + '"><i style="background:' + kindColor(k) + '"></i>' + esc(k)
      + '<span class="n">' + n + '</span></div>').join('') || '<div class="kind">none</div>';
    for (const el of $('kinds').children)
      if (el.dataset.k) el.onclick = () => doList(el.dataset.k);
    const hubs = ov.hubs || [];
    $('hubs').innerHTML = hubs.map(h =>
      '<div class="hub" data-id="' + h.id + '"><span class="t">' + short(h.title) + '</span>'
      + '<span class="m">' + (h.kind || '?') + ' · degree ' + h.degree + '</span></div>').join('')
      || '<div class="hub">none</div>';
    for (const el of $('hubs').children)
      if (el.dataset.id) el.onclick = () => focusNode(el.dataset.id);
  }

  function neighborhoodElements(res) {
    const nodes = new Map(), edges = new Map();
    const add = (n, focus) => { if (!nodes.has(n.id))
      nodes.set(n.id, { data: { id: n.id, label: short(n.title), full: n.title,
                                kind: n.label || '?', color: kindColor(n.label || '?'),
                                focus: focus ? 1 : 0 } }); };
    add(res.node, true);
    for (const nb of res.neighbours || []) {
      add(nb.node, false);
      const [s, t] = nb.direction === 'out' ? [res.node.id, nb.node.id] : [nb.node.id, res.node.id];
      const eid = s + '|' + nb.relation + '|' + t;
      if (!edges.has(eid)) edges.set(eid, { data: { id: eid, source: s, target: t, rel: nb.relation } });
    }
    return [...nodes.values(), ...edges.values()];
  }

  // fcose tuning: user-adjustable multipliers over the size-derived base (⚙ panel),
  // persisted in localStorage so a preferred feel survives reloads.
  const fcfg = Object.assign({ spacing: 1, repulsion: 1, separation: 150 },
                             JSON.parse(localStorage.getItem('viz.fcose') || '{}'));

  function runLayout(incremental = false) {
    // Spacing scales with neighborhood size: a layout bigger than the viewport is fine
    // (fit sets the initial zoom; pan/zoom absorbs the rest) — squished is what's unreadable.
    // `incremental` (expand-in-place) keeps existing positions as the starting point.
    const n = cy.nodes().length;
    const opts = $('layout').value === 'fcose'
      ? { name: 'fcose', animate: false, quality: 'proof', randomize: !incremental,
          idealEdgeLength: Math.min(900, Math.round((70 + n * 2) * fcfg.spacing)),
          nodeRepulsion: Math.min(300000, Math.round((4500 + n * 400) * fcfg.repulsion)),
          nodeSeparation: +fcfg.separation }
      : { name: 'concentric', concentric: n => n.data('focus') ? 2 : 1,
          levelWidth: () => 1, minNodeSpacing: 28 };
    Object.assign(opts, { fit: true, padding: 40 });
    try { cy.layout(opts).run(); }
    catch (e) { cy.layout({ name: 'concentric', concentric: n => n.data('focus') ? 2 : 1,
                            levelWidth: () => 1, minNodeSpacing: 28, fit: true, padding: 40 }).run();
                fail(e); }
  }

  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  let lastRead = null;  // The current detail node's read result (for the raw toggle)

  function openDetail() { $('detail').classList.add('open'); $('dragbar').classList.add('open'); cy.resize(); }
  function closeDetail() { $('detail').classList.remove('open'); $('dragbar').classList.remove('open'); cy.resize(); }

  // Per-neighborhood relation filter: derived from what's on the canvas, no ontology.
  // Unchecked relations stay unchecked across focuses/expands (sticky while browsing).
  function renderRelFilter() {
    const prevOff = new Set([...$('rels').querySelectorAll('input')]
      .filter(cb => !cb.checked).map(cb => cb.dataset.rel));
    const counts = {};
    cy.edges().forEach(e => { const r = e.data('rel') || '?'; counts[r] = (counts[r] || 0) + 1; });
    const div = $('rels');
    div.innerHTML = Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([r, c]) =>
      '<label><input type="checkbox" ' + (prevOff.has(r) ? '' : 'checked')
      + ' data-rel="' + esc(r) + '">' + esc(r)
      + '<span class="n">' + c + '</span></label>').join('');
    $('relsh3').style.display = div.innerHTML ? '' : 'none';
    for (const cb of div.querySelectorAll('input')) cb.onchange = applyRelFilter;
    applyRelFilter();
  }
  function applyRelFilter() {
    const off = new Set([...$('rels').querySelectorAll('input')]
      .filter(cb => !cb.checked).map(cb => cb.dataset.rel));
    cy.edges().forEach(e => e.style('display', off.has(e.data('rel') || '?') ? 'none' : 'element'));
    cy.nodes().forEach(nd => { if (nd.data('focus')) return;
      const vis = nd.connectedEdges().some(e => e.style('display') !== 'none');
      nd.style('display', vis ? 'element' : 'none'); });
  }

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
      // Code rendering ONLY for known code kinds; every other text kind (note, section,
      // decision `statement`, future kinds) renders as wrapping markdown — a long decision
      // in a no-wrap <pre> was a horizontal-scroll wall.
      if (!['slot', 'module', 'notebook', 'nested'].includes(rd.kind)) {
        // A note's leading frontmatter renders as an explicit metadata table, not prose.
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
    // No verbatim content (Entity / FactSlot / Transcript / …): show the node's properties.
    // A dominant long text property (e.g. Transcript.text) is promoted out of the table
    // to a full-width block; long cells expand/collapse on click.
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

  async function loadDetail(id, showRes) {
    openDetail();
    $('dtitle').textContent = short(showRes.node.title);
    $('dmeta').textContent = (showRes.node.label || '?') + ' · ' + id;
    $('dbody').innerHTML = '<span style="color:#999">reading…</span>';
    let rd = null;
    try { rd = await api('/api/g/' + graph + '/read/' + encodeURIComponent(id), 'read'); }
    catch (e) { rd = { error: e.message || String(e) }; }
    lastRead = { rd, props: showRes.properties || {}, raw: false };
    renderDetail(rd, lastRead.props, false);
  }

  async function focusNode(id, push = true) {
    clearErr();
    try {
      const res = await api('/api/g/' + graph + '/show/' + encodeURIComponent(id) + '?depth=1', 'show');
      if (res.error) throw new Error(res.error);
      id = res.node.id || id;  // canonicalize (a prefix ref resolves once, not per follow-up call)
      $('focus').textContent = short(res.node.title) + ' · ' + (res.neighbours || []).length + ' neighbours';
      // Each focus pushes history, so browser back/forward walks the traversal.
      if (push) history.pushState(null, '', '?g=' + graph + '&focus=' + encodeURIComponent(id));
      // Open the pane BEFORE layout so fit() sees the shrunken canvas width.
      openDetail();
      cy.elements().remove();
      cy.add(neighborhoodElements(res));
      runLayout();
      renderRelFilter();
      await loadDetail(id, res);
    } catch (e) { fail(e); }
  }

  // Expand-in-place (shift+tap): pull a node's neighborhood ONTO the canvas instead of
  // replacing it — the accumulate move. Layout runs incrementally (positions kept), the
  // relation filter re-derives, and the URL stays on the focused node (an accumulated
  // canvas isn't deep-linkable — by design, for now).
  async function expandNode(id) {
    clearErr();
    try {
      const res = await api('/api/g/' + graph + '/show/' + encodeURIComponent(id) + '?depth=1', 'show');
      if (res.error) throw new Error(res.error);
      const fresh = neighborhoodElements(res).filter(e => cy.getElementById(e.data.id).empty());
      cy.getElementById(id).data('expanded', 1);
      if (fresh.length) { cy.add(fresh); runLayout(true); }
      renderRelFilter();
      $('focus').textContent = short(res.node.title) + ' expanded (+' + fresh.length + ') · '
        + cy.nodes().length + ' nodes on canvas';
      await loadDetail(id, res);
    } catch (e) { fail(e); }
  }

  function resultRow(r, extra, snip) {
    return '<div class="hub" data-id="' + r.id + '"><span class="t">' + esc(short(r.title)) + '</span>'
      + '<span class="m">' + esc(r.label || '?') + (extra ? ' · ' + esc(extra) : '') + '</span>'
      + (snip ? '<span class="snip">' + esc(snip) + '</span>' : '') + '</div>';
  }

  function wireResults(out) {
    const b = out.querySelector('.back');
    if (b) b.onclick = () => { out.style.display = 'none'; $('ov').style.display = 'block'; };
    for (const el of out.querySelectorAll('.hub'))
      if (el.dataset.id) el.onclick = () => focusNode(el.dataset.id);
  }

  async function doSearch(q) {
    clearErr();
    $('ov').style.display = 'none';
    const out = $('results');
    out.style.display = 'block';
    out.innerHTML = '<span style="color:#999">searching…</span>';
    try {
      const [loc, gr, rel] = await Promise.all([
        api('/api/g/' + graph + '/locate?term=' + encodeURIComponent(q), 'locate'),
        api('/api/g/' + graph + '/grep?term=' + encodeURIComponent(q), 'grep'),
        api('/api/g/' + graph + '/relevant?task=' + encodeURIComponent(q), 'relevant')]);
      out.innerHTML = '<span class="back">← overview</span>'
        + '<h3>Locate (' + loc.count + ')</h3>'
        + (loc.matches || []).map(m => resultRow(m, (m.path || '').split('/').slice(-1)[0])).join('')
        + '<h3>Content — grep (' + gr.count + (gr.truncated ? '+' : '') + ')</h3>'
        + (gr.matches || []).map(m => resultRow(m, m.field, m.snippet)).join('')
        + '<h3>Relevant (' + rel.total_hits + ' reached)</h3>'
        + (rel.results || []).map(r => resultRow(r, 'score ' + r.score)).join('');
      wireResults(out);
    } catch (e) { out.innerHTML = ''; fail(e); }
  }

  async function doList(label, offset = 0, contains = '') {
    clearErr();
    $('ov').style.display = 'none';
    const out = $('results');
    out.style.display = 'block';
    out.innerHTML = '<span style="color:#999">listing…</span>';
    try {
      const res = await api('/api/g/' + graph + '/list?label=' + encodeURIComponent(label)
        + '&offset=' + offset + (contains ? '&contains=' + encodeURIComponent(contains) : ''), 'list');
      if (res.error) throw new Error(res.error);
      const from = res.count ? offset + 1 : 0;
      out.innerHTML = '<span class="back">← overview</span>'
        + '<h3>' + esc(label) + ' · ' + from + '–' + (offset + res.count)
        + (res.truncated ? '+' : '') + (contains ? ' · “' + esc(contains) + '”' : '') + '</h3>'
        + '<input id="lf" placeholder="filter titles… (Enter)" value="' + esc(contains) + '">'
        + (res.rows || []).map(r => resultRow({ id: r.id, title: r.title, label: label },
                                              (r.path || '').split('/').slice(-1)[0])).join('')
        + '<div class="nav">' + (offset > 0 ? '<span class="back" id="lprev">← prev</span>' : '')
        + (res.truncated ? '<span class="back" id="lnext">next →</span>' : '') + '</div>';
      wireResults(out);
      $('lf').onkeydown = e => { if (e.key === 'Enter') doList(label, 0, $('lf').value.trim()); };
      if ($('lprev')) $('lprev').onclick = () => doList(label, Math.max(0, offset - 100), contains);
      if ($('lnext')) $('lnext').onclick = () => doList(label, offset + 100, contains);
    } catch (e) { out.innerHTML = ''; fail(e); }
  }

  // --- Session lens (journal-window): the journal is the data path, not created_at ---
  let liveTimer = null;
  const parseTs = s => {
    s = (s || '').trim();
    if (!s) return null;
    if (/^\d+(\.\d+)?$/.test(s)) return parseFloat(s);
    const m = s.match(/^(\d{4})-(\d{2})-(\d{2})(?:_(\d{2})-(\d{2})-(\d{2}))?$/);
    if (!m) throw new Error('bad time: ' + s + ' (want YYYY-MM-DD[_HH-MM-SS] or unix seconds)');
    return new Date(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0)).getTime() / 1000;
  };
  const fmtTs = t => t == null ? '' : new Date(t * 1000).toLocaleString();
  const verbsStr = x => x.touches + '× · '
    + Object.entries(x.verbs || {}).map(([v, n]) => v + '×' + n).join(', ')
    + ' · last ' + fmtTs(x.last_ts);

  async function loadSessions() {
    try {
      const res = await api('/api/g/' + graph + '/list?label=Session&limit=100', 'list');
      const rows = (res.rows || []).slice()
        .sort((a, b) => String(b.title).localeCompare(String(a.title)));
      $('sess-pick').innerHTML = '<option value="">— pick a session —</option>'
        + rows.map(r => '<option>' + esc(r.title) + '</option>').join('');
    } catch (e) { /* a graph without Session nodes is fine — the picker stays empty */ }
  }

  async function doWindow() {
    clearErr();
    const qs = [];
    try {
      const st = parseTs($('sess-start').value), en = parseTs($('sess-end').value);
      if (st != null) qs.push('start=' + st);
      if (en != null) qs.push('end=' + en);
    } catch (e) { fail(e); return; }
    const key = $('sess-pick').value;
    if (key) qs.push('session=' + encodeURIComponent(key));
    if (!qs.length) { fail(new Error('session window: pick a session or give a start time')); return; }
    $('ov').style.display = 'none';
    const out = $('results');
    out.style.display = 'block';
    try {
      const res = await api('/api/g/' + graph + '/journal-window?' + qs.join('&'), 'journal-window');
      const t = res.touched || [];
      out.innerHTML = '<span class="back">← overview</span>'
        + '<h3>Window · ' + res.entries + ' op(s) · ' + t.length + ' node(s)'
        + (res.missing ? ' · ⚠ ' + res.missing + ' missing' : '') + '</h3>'
        + '<div class="m" style="padding:2px 0 6px">' + esc(fmtTs(res.window.start) || 'journal dawn')
        + ' → ' + esc(res.window.end != null ? fmtTs(res.window.end) : 'now (open — live)')
        + (res.window.session ? ' · ' + esc(res.window.session) : '') + '</div>'
        + t.map(x => x.missing
            ? '<div class="hub"><span class="t">⚠ missing: ' + esc(x.ref) + '</span>'
              + '<span class="m">' + esc(verbsStr(x)) + '</span></div>'
            : resultRow({ id: x.id, title: x.title, label: x.label }, verbsStr(x))).join('');
      wireResults(out);
    } catch (e) { out.innerHTML = ''; fail(e); }
  }

  async function loadGraph(name, push = true) {
    graph = name; clearErr();
    $('graphs').value = name;
    $('focus').textContent = ''; cy.elements().remove();
    closeDetail();
    $('relsh3').style.display = 'none'; $('rels').innerHTML = '';
    $('results').style.display = 'none'; $('ov').style.display = 'block'; $('q').value = '';
    if (push) history.pushState(null, '', '?g=' + name);
    try { renderOverview(await api('/api/g/' + name + '/overview', 'overview')); }
    catch (e) { fail(e); }
    loadSessions();
  }

  async function boot() {
    cy = cytoscape({
      container: $('cy'),
      style: [
        { selector: 'node', style: {
            label: 'data(label)', 'background-color': 'data(color)',
            color: '#111', 'font-size': 10, 'text-wrap': 'wrap', 'text-max-width': 150,
            'text-valign': 'bottom', 'text-halign': 'center', width: 18, height: 18,
            'text-margin-y': 4, 'border-width': 1, 'border-color': '#0003',
            'text-background-color': '#fff', 'text-background-opacity': 0.75,
            'text-background-padding': 1 } },
        { selector: 'node[expanded = 1]', style: { 'border-width': 2.5,
            'border-style': 'dashed', 'border-color': '#555' } },
        { selector: 'node[focus = 1]', style: { width: 34, height: 34, 'border-width': 3,
            'border-color': '#333', 'font-size': 12 } },
        { selector: 'edge', style: {
            width: 1.2, 'line-color': '#ccc', 'target-arrow-color': '#ccc',
            'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'arrow-scale': 0.8,
            label: 'data(rel)', 'font-size': 7, color: '#999',
            'text-rotation': 'autorotate', 'text-background-color': '#fff',
            'text-background-opacity': 0.7, 'text-background-padding': 1 } },
      ],
      minZoom: 0.1, maxZoom: 4, wheelSensitivity: 0.3,
    });
    cy.on('tap', 'node', e =>
      (e.originalEvent && e.originalEvent.shiftKey) ? expandNode(e.target.id())
                                                    : focusNode(e.target.id()));
    // Full-title tooltip (canvas nodes carry no native title attribute).
    let tip;
    cy.on('mouseover', 'node', e => {
      tip = document.createElement('div');
      tip.textContent = e.target.data('full') + '  [' + e.target.data('kind') + ']';
      Object.assign(tip.style, { position: 'fixed', background: '#111', color: '#fff',
        padding: '4px 7px', borderRadius: '4px', font: '12px system-ui', maxWidth: '440px',
        zIndex: 9, pointerEvents: 'none' });
      document.body.appendChild(tip);
    });
    cy.on('mousemove', 'node', e => { if (tip) { tip.style.left = (e.originalEvent.clientX + 12) + 'px';
      tip.style.top = (e.originalEvent.clientY + 12) + 'px'; } });
    cy.on('mouseout', 'node', () => { if (tip) { tip.remove(); tip = null; } });

    try {
      const graphs = await api('/api/graphs', 'graphs');
      $('graphs').innerHTML = graphs.map(g =>
        '<option value="' + g.name + '" title="' + g.path + '">' + g.name + '</option>').join('');
      $('graphs').onchange = () => loadGraph($('graphs').value);
      $('layout').onchange = () => { if (cy.elements().length) runLayout(); };
      $('lcfg').onclick = () => $('lpanel').classList.toggle('open');
      $('sess-go').onclick = () => doWindow();
      $('sess-live').onchange = () => {
        if (liveTimer) { clearInterval(liveTimer); liveTimer = null; }
        if ($('sess-live').checked)
          liveTimer = setInterval(() => {
            // Re-evaluate only an OPEN window (live mode = declarative re-evaluation)
            if ($('results').style.display !== 'none' && !$('sess-end').value.trim()) doWindow();
          }, 5000);
      };
      for (const k of ['spacing', 'repulsion', 'separation']) {
        const el = $('cfg-' + k), lab = el.nextElementSibling;
        el.value = fcfg[k]; lab.textContent = fcfg[k];
        el.oninput = () => { fcfg[k] = +el.value; lab.textContent = el.value;
          localStorage.setItem('viz.fcose', JSON.stringify(fcfg));
          if (cy.elements().length) runLayout(); };
      }
      $('q').onkeydown = e => { if (e.key === 'Enter' && $('q').value.trim()) doSearch($('q').value.trim()); };
      $('dclose').onclick = closeDetail;
      $('draw').onclick = () => { if (lastRead) { lastRead.raw = !lastRead.raw;
        renderDetail(lastRead.rd, lastRead.props, lastRead.raw); } };
      // Resizable detail pane: drag the divider (the canvas re-fits its container live).
      $('dragbar').onmousedown = e => {
        e.preventDefault();
        const move = ev => { $('detail').style.width = Math.max(280,
          Math.min(window.innerWidth - 520, window.innerWidth - ev.clientX)) + 'px'; cy.resize(); };
        const up = () => { removeEventListener('mousemove', move); removeEventListener('mouseup', up); };
        addEventListener('mousemove', move); addEventListener('mouseup', up);
      };
      // Browser back/forward replays the traversal (each focus/graph-switch pushes state).
      window.onpopstate = async () => {
        const q2 = new URLSearchParams(location.search);
        const g2 = q2.get('g') || graphs[0].name;
        if (g2 !== graph) await loadGraph(g2, false);
        if (q2.get('focus')) await focusNode(q2.get('focus'), false);
        else { cy.elements().remove(); closeDetail(); $('focus').textContent = '';
               $('relsh3').style.display = 'none'; $('rels').innerHTML = ''; }
      };
      // Deep-link support: ?g=<graph>&focus=<node-id> boots straight into a neighborhood.
      const q = new URLSearchParams(location.search);
      const start = graphs.find(g => g.name === q.get('g')) || graphs[0];
      if (start) {
        await loadGraph(start.name, false);
        if (q.get('focus')) await focusNode(q.get('focus'), false);
        history.replaceState(null, '', '?g=' + start.name
          + (q.get('focus') ? '&focus=' + encodeURIComponent(q.get('focus')) : ''));
      }
    } catch (e) { fail(e); }
  }
  boot();
</script></body></html>
""".replace("__CYTOSCAPE_JS__", _CYTOSCAPE_JS).replace("__LAYOUT_BASE_JS__", _LAYOUT_BASE_JS) \
   .replace("__COSE_BASE_JS__", _COSE_BASE_JS).replace("__FCOSE_JS__", _FCOSE_JS) \
   .replace("__MARKED_JS__", _MARKED_JS).replace("__DOMPURIFY_JS__", _DOMPURIFY_JS) \
   .replace("__HLJS_JS__", _HLJS_JS).replace("__HLJS_CSS__", _HLJS_CSS) \
   .replace("__KATEX_JS__", _KATEX_JS).replace("__KATEX_AUTO_JS__", _KATEX_AUTO_JS) \
   .replace("__KATEX_CSS__", _KATEX_CSS)
