"""Render projection results for a consumer: agent (JSON) or human (markdown).

Asymmetric projection (arc plan): the agent form is stable, chainable JSON with
ids explicit; the human form is compact rendered markdown. Same core results,
two surfaces — correctness/chainability over token-economy for v1.
"""

import json
from datetime import datetime
from typing import Any, Dict, List


def _short(text: Any, limit: int = 160) -> str:
    """Cap display text to one bounded line (the full content is a `show`/`read <id>` away).

    The bounded-by-construction invariant applies per-LINE too: a node's title or
    description can be arbitrarily long (a giant heading, a full Decision statement),
    so any single rendered line is capped — no output forces `head`/`tail`."""
    s = " ".join(str(text or "").split())
    return (s[: limit - 1].rstrip() + "…") if len(s) > limit else s


def _line(summary: Dict[str, Any]) -> str:
    """One bounded markdown line for a node summary: title, label, id, optional description.

    A rule-derived `gloss` (the one-line orientation field) fills the description
    slot for kinds that carry no `description` (the relational kinds)."""
    bits = [f"**{_short(summary.get('title'), 140)}**", f"_{summary.get('label')}_",
            f"`{summary.get('id')}`"]
    head = " · ".join(b for b in bits if b)
    desc = summary.get("description") or summary.get("gloss")
    return f"- {head}" + (f" — {_short(desc, 200)}" if desc else "")


def _handle_cmd(handle: Dict[str, Any]) -> str:
    """A descent handle rendered as a copy-pasteable `explore` command (re-runnable)."""
    flags = " ".join(f"--facet {f['axis']}={f['value']}" for f in handle.get("filters", []))
    return f'explore "{handle.get("task")}" {flags}'.rstrip()


def _subgraph_lines(obj: Dict[str, Any]) -> List[str]:
    """The shared body of a subgraph_view result (subgraph AND lens renders):
    the count line, loud missing/ambiguous warnings, node rows, edge rows."""
    nodes, edges = obj.get("nodes", []), obj.get("edges", [])
    expanded = obj.get("expanded_count", 0)
    lines = [(f"_{len(nodes)} node(s) ({obj.get('seed_count', 0)} seed"
              + (f" + {expanded} expanded" if expanded else "") + ")"
              + f" · {len(edges)} interconnecting edge(s)"
              + (" · expansion TRUNCATED at --cap" if obj.get("truncated") else "")
              + "_"), ""]
    for ref in obj.get("missing", []):
        lines.append(f"- ⚠ MISSING `{ref}` — no node resolves")
    for a in obj.get("ambiguous", []):
        cands = "; ".join(f"{c['id']} ({c.get('label')})" for c in a.get("candidates", []))
        lines.append(f"- ⚠ AMBIGUOUS `{a['ref']}` — candidates: {cands}")
    title_of: Dict[str, str] = {}
    for n in nodes:
        title_of[n["id"]] = n.get("title") or n["id"]
        mark = "↳ " if n.get("expanded") else ""
        lines.append(f"- {mark}**{_short(n.get('title'), 110)}** · _{n.get('label')}_ `{n['id']}`")
    if edges:
        lines += ["", "**Edges:**"]
        for e in edges:
            src, tgt = e.get("source_id"), e.get("target_id")
            lines.append(f"- **{_short(title_of.get(src, src), 50)}** "
                         f"—{e.get('relation_type')}→ "
                         f"**{_short(title_of.get(tgt, tgt), 50)}**")
    return lines


def _human(kind: str, obj: Dict[str, Any]) -> str:
    """Render a result dict as markdown, dispatched on the command kind."""
    if kind == "subgraph":
        if obj.get("error"):
            return f"⚠ {obj['error']}"
        return "\n".join(["## Subgraph"] + _subgraph_lines(obj))
    if kind == "export":
        # The human view is the SHAPE summary, never the dump — the full node+edge
        # payload is a canvas/agent feed (`--format agent`) and runs to megabytes.
        by_kind: Dict[str, int] = {}
        for n in obj.get("nodes") or []:
            k = n.get("label") or "?"
            by_kind[k] = by_kind.get(k, 0) + 1
        lines = [f"## Export — {obj.get('node_count', 0)} node(s) · "
                 f"{obj.get('edge_count', 0)} edge(s)",
                 "_full payload rides `--format agent` (the canvas feed)_", ""]
        lines += [f"- **{k}** ×{c}" for k, c in
                  sorted(by_kind.items(), key=lambda kv: kv[1], reverse=True)]
        return "\n".join(lines)
    if kind == "lens":
        if obj.get("error"):
            out = f"⚠ {obj['error']}"
            if obj.get("params"):
                decls = ", ".join(f"{p['name']} ({p.get('type', 'string')}"
                                  + (", required" if p.get("required") else "") + ")"
                                  for p in obj["params"])
                out += f"\n  declares: {decls}"
            return out
        head = [f"## Lens `{obj.get('slug')}` — {obj.get('title')}"]
        if obj.get("description"):
            head.append(f"_{obj['description']}_")
        if obj.get("bound"):
            head.append("params: " + ", ".join(f"{k}={v}" for k, v in obj["bound"].items()))
        if obj.get("clauses"):
            head.append("selection: " + " ∪ ".join(f"{c['verb']}×{c['selected']}"
                                                   for c in obj["clauses"]))
        view = {k: v for k, v in (obj.get("view") or {}).items() if v}
        if view:
            head.append("view: " + ", ".join(f"{k}={v}" for k, v in view.items()))
        return "\n".join(head + _subgraph_lines(obj))
    if kind == "set-lens":
        if obj.get("error"):
            return f"⚠ {obj['error']}"
        verb = "updated" if obj.get("updated") else "authored"
        return (f"**lens {verb}:** `{obj.get('slug')}`\n`{obj.get('lens_id')}`\n"
                f"_apply it: `lens {obj.get('slug')}`_")
    if kind == "session":
        if obj.get("error"):
            return f"⚠ {obj['error']}"
        verb = "updated" if obj.get("updated") else "registered"
        lines = [f"**session {verb}:** `{obj.get('key')}`", f"`{obj.get('session_id')}`"]
        if obj.get("started_at") is not None:
            lines.append(f"started {_fmt_ts(obj['started_at'])}")
        if obj.get("title"):
            lines.append(f"title: {obj['title']}")
        return "\n".join(lines)
    if kind == "journal-window":
        w = obj.get("window", {})
        parts = []
        if w.get("session"):
            parts.append(f"session `{w['session']}`")
        if w.get("start") is not None:
            parts.append(f"from {_fmt_ts(w['start'])}")
        sw = obj.get("session_window")
        if sw and w.get("start") is None and w.get("end") is None:
            parts.append(f"window {_fmt_ts(sw['start'])} -> "
                         + (_fmt_ts(sw["end"]) if sw.get("end") is not None
                            else "NOW (in progress)"))
        else:
            parts.append(f"to {_fmt_ts(w['end'])}" if w.get("end") is not None
                         else "to NOW (open — live)")
        touched = obj.get("touched", [])
        missing = f" · {obj['missing']} missing from graph" if obj.get("missing") else ""
        lines = [f"## Journal window — {' · '.join(parts)}",
                 f"_{obj.get('entries', 0)} journaled op(s) · {len(touched)} node(s) "
                 f"touched{missing}_", ""]
        for t in touched:
            verbs = ", ".join(f"{v}×{n}" for v, n in sorted(t.get("verbs", {}).items()))
            mark = "⚠ MISSING " if t.get("missing") else ""
            title = t.get("title") or t.get("ref")
            label = f" · _{t['label']}_" if t.get("label") else ""
            lines.append(f"- {mark}**{title}**{label} `{t.get('id', t['ref'])}`")
            lines.append(f"    ↳ {t.get('touches', 0)} touch(es): {verbs} · "
                         f"last {_fmt_ts(t.get('last_ts'))}")
        return "\n".join(lines)
    if kind == "schema":
        labels = obj.get("node_labels", [])
        counts = obj.get("counts", {})
        lines = ["## Graph schema", "",
                 "**Node labels:** " + ", ".join(f"{l} ({counts.get(l, '?')})" for l in labels),
                 "**Edge types:** " + ", ".join(obj.get("edge_types", []))]
        return "\n".join(lines)
    if kind == "relevant":
        total = obj.get("total_hits", len(obj.get("results", [])))
        facets = obj.get("facets", {})
        by_kind, by_seed = facets.get("by_kind", []), facets.get("by_seed", [])
        lines = [f"## Relevant to: {obj.get('task')}", "",
                 f"_{total} hits across {len(by_kind)} kinds / {len(by_seed)} seed-clusters — "
                 f"the top matches are a teaser; descend any cluster IN FULL with `explore` "
                 f"(nothing below is silently truncated)._", ""]
        if by_kind:
            lines.append("**By kind:**")
            lines += [f"- **{f['value']}** ×{f['count']} → `{_handle_cmd(f['handle'])}`" for f in by_kind]
            lines.append("")
        if by_seed:
            lines.append("**By seed-cluster:**")
            lines += [f"- “{_short(f.get('title', f['value']), 90)}” ×{f['count']} → `{_handle_cmd(f['handle'])}`"
                      for f in by_seed]
            lines.append("")
        lines.append("**Top matches (teaser):**")
        for r in obj.get("results", []):
            lines.append(_line(r) + f"  \n  ↳ _{_short(r.get('why'), 120)}_ (score {r.get('score')})")
        if not obj.get("results"):
            lines.append("_(no results — try different terms)_")
        return "\n".join(lines)
    if kind == "explore":
        flt = ", ".join(f"{f['axis']}={f['value']}" for f in obj.get("filters", []))
        head = "all shown" if obj.get("complete") else f"showing top {obj.get('shown')} — re-facet below for the rest"
        lines = [f"## Explore: {obj.get('task')}  _[{flt}]_", "",
                 f"_{obj.get('total')} in this cluster ({head})._", ""]
        for m in obj.get("members", []):
            lines.append(_line(m) + (f"  \n  ↳ _{_short(m['why'], 120)}_ (score {m.get('score')})" if m.get("why")
                                     else f"  (score {m.get('score')})"))
        sub = obj.get("subfacets", [])
        if sub:
            lines += ["", f"**Refine (by {sub[0]['axis']}) — descend further:**"]
            lines += [f"- “{_short(f.get('title', f['value']), 90)}” ×{f['count']} → `{_handle_cmd(f['handle'])}`"
                      if f["axis"] == "seed" else
                      f"- **{f['value']}** ×{f['count']} → `{_handle_cmd(f['handle'])}`" for f in sub]
        return "\n".join(lines)
    if kind == "assert":
        if obj.get("error"):
            return f"⚠ {obj['error']}"
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
    if kind == "link":
        if obj.get("error"):
            return f"⚠ {obj['error']}"
        return (f"**linked** `{obj.get('source_id')}` —_{obj.get('relation')}_→ "
                f"`{obj.get('target_id')}` (actor {obj.get('actor')})\n"
                f"`edge {obj.get('edge_id')}`")
    if kind == "unlink":
        if obj.get("error"):
            return f"⚠ {obj['error']}"
        outcome = "retracted" if obj.get("deleted") else "already absent (no-op)"
        return (f"**unlinked** `{obj.get('source_id')}` —_{obj.get('relation')}_→ "
                f"`{obj.get('target_id')}` — edge {outcome}\n"
                f"`edge {obj.get('edge_id')}` (retraction journaled; replay converges without it)")
    if kind == "check":
        if obj.get("error"):
            return f"⚠ {obj['error']}"
        return (f"**check attached** to **{_short(obj.get('item_label', ''), 80)}** "
                f"`{obj.get('item_id')}`\n"
                f"_{obj.get('text')}_\n"
                f"`check {obj.get('check_id')}` (task_state=open; close with "
                f"`assert {obj.get('check_id')} task_state done --evidence <proof>`)")
    if kind == "display-rule":
        if obj.get("error"):
            return f"⚠ {obj['error']}"
        verb = "updated" if obj.get("updated") else "authored"
        lines = [f"**display-rule {verb}** for kind `{obj.get('for_label')}`",
                 f"`rule {obj.get('rule_id')}`"]
        if obj.get("title_template") is not None:
            lines.append(f"  title: `{obj['title_template']}`")
        if obj.get("gloss_template") is not None:
            lines.append(f"  gloss: `{obj['gloss_template']}`")
        return "\n".join(lines)
    if kind == "conventions":
        c = obj.get("counts", {})
        lines = ["## Convention audit (notebook code)",
                 f"_undocumented {c.get('undocumented', 0)} · no-docstring {c.get('no_docstring', 0)} · "
                 f"non-granular cells {c.get('non_granular_cells', 0)} · "
                 f"untested {c.get('untested', 0)}_", ""]
        und = obj.get("undocumented", [])
        if und:
            lines.append("**Undocumented (no adjacent prose cell):**")
            lines += [f"  - `{u.get('qualname')}` _(cell {u.get('cell_key')})_" for u in und[:30]]
        nod = obj.get("no_docstring", [])
        if nod:
            lines.append("**Missing docstring:**")
            lines += [f"  - `{u.get('qualname')}`" for u in nod[:30]]
        ng = obj.get("non_granular_cells", [])
        if ng:
            lines.append("**Non-granular cells (>1 public def):**")
            lines += [f"  - {', '.join('`'+s+'`' for s in g.get('symbols', []))}" for g in ng[:30]]
        unt = obj.get("untested", [])
        if unt:
            lines.append(f"**Untested (no incoming TESTS edge; {len(unt)} total, first 40):**")
            lines += [f"  - `{u.get('qualname')}` _({u.get('module_path')})_" for u in unt[:40]]
        if not (und or nod or ng or unt):
            lines.append("_(no findings)_")
        return "\n".join(lines)
    if kind == "refactor":
        c = obj.get("counts", {})
        lines = ["## Refactoring candidates (propose/confirm)",
                 f"_relocation {c.get('relocation', 0)} · dead-code {c.get('dead_code', 0)} · "
                 f"consolidation {c.get('consolidation', 0)} · split {c.get('split', 0)}_", ""]
        rel = obj.get("relocation", [])
        cycles = [r for r in rel if r.get("cycle")]
        if cycles:
            lines.append("**Relocation — dependency CYCLE (actionable):**")
            for r in cycles[:30]:
                tgt = ", ".join(f"{k} ×{v}" for k, v in r.get("caller_repos", {}).items())
                lines.append(f"  - `{r.get('qualname')}` _in {r.get('home_repo')}_ ↔ {tgt}")
        plain = [r for r in rel if not r.get("cycle")]
        if plain:
            lines.append(f"**Relocation — single-consumer cross-repo ({len(plain)}; expected for a "
                         "foundation lib, low precision on a small corpus):**")
            for r in plain[:15]:
                tgt = ", ".join(f"{k} ×{v}" for k, v in r.get("caller_repos", {}).items())
                lines.append(f"  - `{r.get('qualname')}` _in {r.get('home_repo')}_ → called from {tgt}")
        dc = obj.get("dead_code", [])
        if dc:
            lines.append("**Dead-code (no in-corpus callers — weak; out-of-corpus use possible):**")
            for d in dc[:30]:
                lines.append(f"  - `{d.get('qualname')}` _{d.get('kind')}_ ({d.get('module_path')})")
        con = obj.get("consolidation", [])
        if con:
            lines.append("**Consolidation (same name across repos):**")
            for g in con[:30]:
                lines.append(f"  - `{g.get('name')}` in {', '.join(g.get('repos', []))}")
        sp = obj.get("split", [])
        if sp:
            lines.append("**Split (non-granular cell, divergent neighborhoods):**")
            for g in sp[:30]:
                lines.append(f"  - {', '.join('`' + s + '`' for s in g.get('symbols', []))}")
        if not (rel or dc or con or sp):
            lines.append("_(no candidates)_")
        return "\n".join(lines)
    if kind == "cohesion":
        c = obj.get("counts", {})
        lines = ["## Module cohesion audit (propose/confirm)",
                 f"_under-split (grab-bag) {c.get('under_split', 0)} · "
                 f"over-split (scattered) {c.get('over_split', 0)} · "
                 f"dominant-core damped {c.get('dominant_damped', 0)} · "
                 f"driver-consumer damped {c.get('over_split_driver_damped', 0)}_", ""]
        us = obj.get("under_split", [])
        if us:
            lines.append("**Under-split — module fuses unrelated concerns (split candidate):**")
            for u in us[:15]:
                lines.append(f"  - `{u.get('module_path')}` _({u.get('repo')})_ — "
                             f"{u.get('num_symbols')} symbols / {u.get('num_components')} clusters:")
                for g in u.get("groups", []):
                    lines.append(f"      · {', '.join('`' + s + '`' for s in g)}")
        os_ = obj.get("over_split", [])
        if os_:
            lines.append("**Over-split — helper apart from its only consumer (merge candidate):**")
            for o in os_[:30]:
                lines.append(f"  - `{o.get('qualname')}` _{o.get('home_module')}_ → "
                             f"used only by `{o.get('consumer_module')}` (×{o.get('num_callers')})")
        if not (us or os_):
            lines.append("_(no candidates)_")
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
    if kind == "readiness":
        c = obj.get("counts", {})
        extra = ""
        if c.get("closable"):
            extra += f" · closable {c['closable']}"
        if c.get("drift"):
            extra += f" · DoD-drift {c['drift']}"
        lines = ["## Readiness frontier",
                 f"_ready {c.get('ready', 0)} · blocked {c.get('blocked', 0)} · "
                 f"done {c.get('done', 0)}{extra}_  (ready/blocked are DERIVED, never stored)", ""]
        closable_ids = {e.get("id") for e in obj.get("closable", [])}

        def _dod(e):
            ck = e.get("checks")
            if not ck:
                return ""
            if e.get("id") in closable_ids:
                return f"  🏁 _DoD {ck['done']}/{ck['total']} met — closable_"
            return f"  _[DoD {ck['done']}/{ck['total']}]_"

        ready = obj.get("ready", [])
        if ready:
            lines.append("**Ready (all prerequisites done):**")
            for r in ready:
                gates = r.get("gates", [])
                suffix = f"  _(gated by {len(gates)}, all done)_" if gates else ""
                lines.append(f"  - ✅ **{_short(r.get('label', ''), 100)}** `{r.get('id')}`{suffix}{_dod(r)}")
        blocked = obj.get("blocked", [])
        if blocked:
            lines.append("**Blocked (waiting on prerequisites):**")
            for b in blocked:
                lines.append(f"  - ⛔ **{_short(b.get('label', ''), 100)}** `{b.get('id')}`{_dod(b)}")
                for g in b.get("blocked_by", []):
                    lines.append(f"      ↳ needs _{_short(g.get('label', ''), 80)}_ `{g.get('id')}`")
        drift = obj.get("drift", [])
        if drift:
            lines.append("**DoD drift (marked done, checks still open):**")
            for d in drift:
                lines.append(f"  - ⚠ **{_short(d.get('label', ''), 100)}** `{d.get('id')}`")
                for ck in d.get("open_checks", []):
                    lines.append(f"      ↳ open check _{_short(ck.get('label', ''), 80)}_ `{ck.get('id')}`")
        done = obj.get("done", [])
        if done:
            lines.append("**Done:**")
            for d in done:
                lines.append(f"  - ◾ {_short(d.get('label', ''), 100)} `{d.get('id')}`{_dod(d)}")
        if not (ready or blocked or done):
            lines.append("_(no work-items — author `task_state` to populate)_")
        return "\n".join(lines)
    if kind == "register-drift":
        c = obj.get("counts", {})
        lines = ["## Register drift",
                 f"_registers {c.get('registers', 0)} · in-sync {c.get('in_sync', 0)} · "
                 f"drifting {c.get('drifting', 0)} · hubless {c.get('hubless', 0)}_"
                 "  (cache vs role assertions — propose only)", ""]
        for r in obj.get("registers", []):
            missing, stale = r.get("missing_cache", []), r.get("stale_cache", [])
            mark = "✓" if not (missing or stale) else "✗"
            lines.append(f"- {mark} **{_short(r.get('hub_label', ''), 60)}** "
                         f"(`role={r.get('value')}`): members {r.get('members', 0)} · "
                         f"cached {r.get('cached', 0)} `{r.get('hub_id')}`")
            for m in missing:
                lines.append(f"    ↳ missing from cache: _{_short(m.get('label', ''), 80)}_ `{m.get('id')}`")
            for m in stale:
                lines.append(f"    ↳ stale cache link: _{_short(m.get('label', ''), 80)}_ `{m.get('id')}`")
        for h in obj.get("hubless", []):
            lines.append(f"- ◌ `role={h.get('value')}`: {h.get('members', 0)} member(s), no "
                         f"`{h.get('value')}-register` hub (counts only — a hub is earned, not required)")
        if not (obj.get("registers") or obj.get("hubless")):
            lines.append("_(no role assertions — nothing to reconcile)_")
        return "\n".join(lines)
    if kind == "orphaned-edges":
        c = obj.get("counts", {})
        lines = ["## Orphaned code-target edges",
                 f"_link ops {c.get('link_ops', 0)} · orphaned {c.get('orphaned', 0)} · "
                 f"with proposal {c.get('with_proposal', 0)}_"
                 "  (journal vs current graph — propose only)", ""]
        orphans = obj.get("orphans", [])
        for o in orphans:
            ctx = o.get("source_context") or o.get("target_context") or ""
            lines.append(f"- ✗ _{o.get('relation')}_ edge"
                         + (f" (resolving side: **{_short(ctx, 70)}**)" if ctx else ""))
            for m in o.get("missing", []):
                label = f" — journaled label _{_short(m.get('label') or '', 60)}_" if m.get("label") else " (no journaled label — legacy op)"
                lines.append(f"    ↳ {m.get('side')} `{m.get('id')}` no longer resolves{label}")
                if m.get("proposal"):
                    pr = m["proposal"]
                    lines.append(f"        → propose remap to **{pr.get('name')}** "
                                 f"`{pr.get('id')}` (score {pr.get('score')})")
        if not orphans:
            lines.append("_(clean — every journaled link endpoint resolves; nothing will drop on replay)_")
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
    if kind == "author":
        if obj.get("error"):
            return f"⚠ {obj['error']}"
        status = ("written to" if obj.get("written") else
                  "would write (dry run) to" if obj.get("artifact_path") else "emitted (no path)")
        lines = [f"**authored** `{obj.get('slot')}` on _{obj.get('label')}_ `{obj.get('node_id')}`",
                 f"{status} `{obj.get('artifact_path')}` ({obj.get('emitted_bytes')} bytes, "
                 f"{obj.get('artifact')})"]
        if obj.get("routed_note"):
            lines.insert(1, f"_({obj['routed_note']})_")
        if obj.get("unchanged"):
            lines.append("_(slot text unchanged — no-op edit)_")
        if not obj.get("written") and obj.get("emitted_text"):
            lines += ["", "```", obj["emitted_text"].rstrip("\n"), "```"]
        return "\n".join(lines)
    if kind == "add-symbol":
        if obj.get("error"):
            return f"⚠ {obj['error']}"
        status = "written to" if obj.get("written") else "would write (dry run) to"
        lines = [f"**added** `{obj.get('qualname')}` ({obj.get('symbol_kind')}) as _CodeSymbol_ "
                 f"`{obj.get('symbol_id')}` at order {obj.get('order_index')}",
                 f"{status} `{obj.get('artifact_path')}` ({obj.get('emitted_bytes')} bytes)"]
        if not obj.get("written") and obj.get("emitted_text"):
            lines += ["", "```", obj["emitted_text"].rstrip("\n"), "```"]
        return "\n".join(lines)
    if kind == "add-text":
        if obj.get("error"):
            return f"⚠ {obj['error']}"
        status = "written to" if obj.get("written") else "would write (dry run) to"
        head = (f"**added** `{obj.get('kind')}` region as _CodeText_ "
                f"`{obj.get('text_id')}` at order {obj.get('order_index')}")
        if obj.get("new_import_bindings"):
            head += f" (+{obj.get('new_import_bindings')} import bindings on the module)"
        lines = [head,
                 f"{status} `{obj.get('artifact_path')}` ({obj.get('emitted_bytes')} bytes)"]
        if not obj.get("written") and obj.get("emitted_text"):
            lines += ["", "```", obj["emitted_text"].rstrip("\n"), "```"]
        return "\n".join(lines)
    if kind == "move":
        if obj.get("error"):
            return f"⚠ {obj['error']}"
        status = "moved" if obj.get("written") else "would move (dry run)"
        # Single-symbol `move` carries `symbol`/`from_module`; batch `regroup` carries
        # `symbols`/`from_modules` (+ `created_target`).
        what = obj.get("symbol") or ", ".join(obj.get("symbols", []))
        frm = obj.get("from_module") or ", ".join(obj.get("from_modules", []))
        head = f"**{status}** `{what}`  {frm} → {obj.get('to_module')}"
        if obj.get("created_target"):
            head += "  _(target module created)_"
        lines = [head, f"_files: {len(obj.get('files', []))} re-emitted_"]
        ci = obj.get("caller_imports_rewritten", [])
        lines.append(f"caller imports rewritten: {', '.join(ci) if ci else '(none)'}")
        d = obj.get("diagnostic", {})
        ti, si = d.get("target_imports_synthesized", []), d.get("source_imports_synthesized", [])
        if ti:
            lines.append(f"target imports synthesized from: {', '.join(ti)}")
        if si:
            lines.append(f"source imports synthesized from: {', '.join(si)} (it still uses the moved symbol)")
        if d.get("zero_residual") and not (ti or si):
            lines.append("zero residual: no cross-module imports needed beyond the bindings")
        return "\n".join(lines)
    if kind == "module":
        if obj.get("error"):
            line = f"⚠ {obj['error']}"
            if obj.get("symbols"):
                line += "\n  symbols: " + ", ".join(obj["symbols"])
            return line
        done = obj.get("written")
        # new-module: has module_path; rename-module: has to_path; delete-module: has node_count.
        if "node_count" in obj:
            verb = "deleted" if done else "would delete (dry run)"
            line = (f"**{verb}** module `{obj.get('import_name')}` ({obj.get('node_count')} nodes)"
                    + ("  _[forced]_" if obj.get("forced") else ""))
            return line + f"\n  path: {obj.get('path')}"
        if "to_path" in obj:
            verb = "renamed" if done else "would rename (dry run)"
            ci = obj.get("caller_imports_rewritten", [])
            return "\n".join([
                f"**{verb}** {obj.get('from_path')} → {obj.get('to_path')}",
                f"  import: {obj.get('from_module')} → {obj.get('to_module')}",
                f"  importers rewritten: {', '.join(ci) if ci else '(none)'}",
                f"  _{obj.get('note', '')}_"])
        verb = "created" if done else "would create (dry run)"
        return (f"**{verb}** module `{obj.get('import_name')}` ({obj.get('module_path')})"
                f"\n  {obj.get('note', '')}")
    if kind == "rename":
        if obj.get("error"):
            return f"⚠ {obj['error']}"
        verb = "renamed" if obj.get("written") else "would rename (dry run)"
        mu = obj.get("modules_updated", [])
        lines = [f"**{verb}** {obj.get('symbol_kind')} `{obj.get('old_name')}` → "
                 f"`{obj.get('new_name')}`  _in {obj.get('module')}_",
                 f"  def-site + internal edits: {obj.get('def_site_edits')}; "
                 f"files re-emitted: {len(obj.get('files', []))}",
                 f"  importing modules updated: {', '.join(mu) if mu else '(none)'}"]
        for d in obj.get("diagnostics", []):
            lines.append(f"  ⚠ {d}")
        lines.append(f"  _{obj.get('note', '')}_")
        return "\n".join(lines)
    if kind == "readme":
        if obj.get("error"):
            return f"⚠ {obj['error']}"
        if "drift" in obj:
            status = ("DRIFTED — README.md differs from the graph projection" if obj["drift"]
                      else "in sync with the graph")
            return f"**regen-check** `{obj.get('repo_key')}`: {status}\n  {obj.get('readme_path')}"
        if obj.get("written"):
            return (f"**wrote** README for `{obj.get('repo_key')}` → {obj.get('readme_path')} "
                    f"({obj.get('module_count')} modules / {obj.get('symbol_count')} public symbols, "
                    f"purpose {'on-graph' if obj.get('has_purpose') else 'MISSING'})")
        return (f"README `{obj.get('repo_key')}`: {obj.get('module_count')} modules / "
                f"{obj.get('symbol_count')} public symbols; purpose "
                f"{'on-graph' if obj.get('has_purpose') else 'MISSING'}")
    if kind == "flip":
        if obj.get("error"):
            return f"⚠ {obj['error']}"
        verb = (("absorbed (graph-sourced)" if obj.get("graph_sourced") else "captured (shadow)")
                if obj.get("captured") else "already current (no-op)")
        canon = ("file is already canonical" if obj.get("file_already_canonical")
                 else "⚠ flip implies a one-time canonicalization (e.g. import reorder)")
        return "\n".join([
            f"**{verb}** `{obj.get('import_name')}` ({obj.get('canonical_bytes')} bytes)",
            f"  {canon}",
            f"  _{obj.get('note', '')}_"])
    if kind == "source-check":
        n = obj.get("count", 0)
        gs = obj.get("graph_sourced_count", 0)
        head = (f"**source soak**: {n} module(s) ({gs} graph-sourced / {n - gs} shadow) · "
                f"file-drift {obj.get('file_drift')} · "
                f"round-trip-stable {obj.get('roundtrip_stable')}/{n}"
                + ("  ✓ CLEAN" if obj.get("clean") else "")
                + ("" if obj.get("regen_clean", True) else "  ✗ REGEN GATE FAILED"))
        lines = [head]
        for m in obj.get("modules", []):
            sourced = m.get("graph_sourced")
            flags = []
            if not m.get("file_present"):
                flags.append("artifact MISSING (emit-artifact regenerates it)" if sourced
                             else "file MISSING")
            elif not m.get("file_matches_source"):
                flags.append("ARTIFACT DIVERGED from the journaled source (absorb via "
                             "flip-module or regenerate via emit-artifact)" if sourced
                             else "FILE DRIFTED (out-of-band edit)")
            if not m.get("roundtrip_fixpoint"):
                flags.append("round-trip NOT a fixpoint")
            status = "ok" if not flags else "⚠ " + "; ".join(flags)
            phase = "GRAPH-SOURCED" if sourced else "shadow"
            lines.append(f"  - `{m.get('module')}` [{phase}] — {status}")
        return "\n".join(lines)
    if kind == "flip-to-py":
        lines = []
        if obj.get("error"):
            lines.append(f"⚠ {obj['error']}")
            for b in obj.get("cell_ref_blockers", []):
                lines.append(f"  ✗ journaled `{b.get('verb')}` op #{b.get('op_index')} "
                             f"({b.get('arg_path')}) -> Cell `{b.get('cell_id')}` "
                             f"(surviving symbols: {b.get('surviving_symbols') or 'NONE'})")
            if len(lines) == 1:
                return lines[0]
            return "\n".join(lines)
        head = ("**FLIPPED**" if obj.get("written") else "**flip plan (dry run)**")
        lines = [f"{head} `{obj.get('notebook_path')}` → `{obj.get('module_path')}` "
                 f"({obj.get('export_cells')} export cells → "
                 f"{obj.get('canonical_bytes')} canonical bytes)"]
        md = obj.get("markdown_cells_dropped", [])
        lines.append(f"  markdown cells DROPPED ({len(md)}) — prose triage owns their disposition:")
        lines += [f"    - [{c.get('index')}] {c.get('first_line', '')[:90]}" for c in md]
        nx = obj.get("nonexport_code_cells_dropped", [])
        if nx:
            lines.append(f"  ⚠ NON-EXPORT code cells DROPPED ({len(nx)}) — project their tests FIRST:")
            lines += [f"    - [{c.get('index')}] {c.get('first_line', '')[:90]}" for c in nx]
        if obj.get("dropped_all_dunder"):
            lines.append(f"  __all__ assignment(s) dropped: {obj['dropped_all_dunder']} "
                         "(nbdev star-import scar-repair; arc-lib shape carries none)")
        if obj.get("pruned_imports"):
            lines.append(f"  imports pruned by canonical emit: {', '.join(obj['pruned_imports'])} "
                         "— VERIFY each is dead (comment/docstring refs don't count)")
        for rt in obj.get("cell_refs_retargeted", []):
            lines.append(f"  ↳ re-linked {rt.get('relation')} Cell `{rt.get('replaces_cell')}` "
                         f"→ symbol `{rt.get('surviving_symbol')}`")
        for b in obj.get("cell_refs_dropped", []):
            lines.append(f"  ✗ FORCED DROP: `{b.get('verb')}` op #{b.get('op_index')} "
                         f"({b.get('arg_path')}) -> Cell `{b.get('cell_id')}` orphans on rebuild")
        if obj.get("written"):
            g = obj.get("graph", {})
            lines.append(f"  journal: source+cutover `{obj.get('module_path')}` · "
                         f"RETIRED `{obj.get('notebook_path')}` · notebook file "
                         f"{'deleted' if obj.get('notebook_deleted') else 'ALREADY ABSENT'}")
            lines.append(f"  graph: {g.get('dropped_nodes')} nodes out → "
                         f"{g.get('added_nodes')} in / {g.get('added_edges')} edges")
        if obj.get("note"):
            lines.append(f"  _{obj['note']}_")
        return "\n".join(lines)
    if kind == "cutover":
        if obj.get("error"):
            return f"⚠ {obj['error']}"
        if obj.get("already_graph_sourced"):
            return f"**already graph-sourced** `{obj.get('module_path')}` (no-op)"
        art = " (artifact file regenerated from the journal)" if obj.get("artifact_written") else ""
        return "\n".join([
            f"**CUT OVER** `{obj.get('import_name') or obj.get('module_path')}` — "
            f"the journal is now this module's source of truth{art}",
            f"  {obj.get('file_path')}",
            f"  _{obj.get('note', '')}_"])
    if kind == "emit-artifact":
        if obj.get("error"):
            return f"⚠ {obj['error']}"
        if obj.get("written"):
            return (f"**regenerated** `{obj.get('module_path')}` from the journaled source "
                    f"({obj.get('artifact_bytes')} bytes) → {obj.get('file_path')}")
        state = "would change (drifted)" if obj.get("changed") else "already in sync"
        return f"**artifact** `{obj.get('module_path')}`: {state} ({obj.get('artifact_bytes')} bytes)"
    if kind == "emit":
        if obj.get("error"):
            return f"⚠ {obj['error']}"
        if obj.get("written"):
            return (f"**emitted** `{obj.get('artifact')}` → `{obj.get('artifact_path')}` "
                    f"({obj.get('emitted_bytes')} bytes)")
        return obj.get("text", "")
    if kind == "read":
        if obj.get("error"):
            return f"⚠ {obj['error']}"
        if obj.get("kind") == "nested":
            return f"⚠ {obj.get('hint')} (enclosing module `{obj.get('module_id')}`)"
        return obj.get("text", "")
    if kind == "list":
        if obj.get("error"):
            return f"⚠ {obj['error']}"
        mode, key, rows = obj.get("mode"), obj.get("key"), obj.get("rows", [])
        head = {"label": "Nodes", "predicate": "Assertions", "relation": "Edges"}.get(mode, "List")
        shown, total = obj.get("count", len(rows)), obj.get("total")
        counter = (f"{shown} of {total}" if total is not None and total != shown else f"{shown}")
        where = obj.get("where") or []
        title = f"## {head} · `{key}` ({counter}" \
                + (" — window; page with --offset" if obj.get("truncated") else "") + ")" \
                + ("".join(f"  _[{w.get('prop')}={w.get('value')}]_" for w in where))
        if not rows:
            return f"{title}\n\n_(none)_"
        lines = [title, ""]
        for r in rows:
            if mode == "label":
                line = f"- **{_short(r.get('title', ''), 80)}** `{r.get('id')}`"
                if r.get("path"):
                    line += f"  📄 `{r['path']}`"
                if r.get("gloss"):
                    line += f"\n    ↳ _{_short(r['gloss'], 140)}_"
                lines.append(line)
            elif mode == "predicate":
                lines.append(f"- **{_short(r.get('subject', ''), 70)}** = _{r.get('value')}_ "
                             f"(actor {r.get('actor')}) `{r.get('subject_id')}`")
            else:  # relation
                lines.append(f"- **{_short(r.get('source', ''), 60)}** → "
                             f"**{_short(r.get('target', ''), 60)}**  "
                             f"`{r.get('source_id')}` → `{r.get('target_id')}`")
        return "\n".join(lines)
    if kind == "grep":
        matches = obj.get("matches", [])
        if not matches:
            return (f"## Grep `{obj.get('term')}`\n\n_(no node's text contains that "
                    f"substring — try `relevant` for a ranked term search)_")
        head = f"## Grep `{obj.get('term')}` ({obj.get('count', len(matches))}" \
               + (" — truncated" if obj.get("truncated") else "") + ")"
        lines = [head, ""]
        for m in matches:
            lines.append(f"- **{_short(m.get('title', ''), 80)}** · _{m.get('label')}_ `{m.get('id')}`")
            lines.append(f"    ↳ `{m.get('field')}`: {m.get('snippet')}")
        return "\n".join(lines)
    if kind == "locate":
        matches = obj.get("matches", [])
        if not matches:
            return f"## Locate `{obj.get('term')}`\n\n_(no node matches that handle — try `relevant` for a content search)_"
        head = f"## Locate `{obj.get('term')}` ({obj.get('count', len(matches))}" \
               + (" — truncated" if obj.get("truncated") else "") + ")"
        lines = [head, ""]
        for m in matches:
            lines.append(f"- **{_short(m.get('title', ''), 80)}** · _{m.get('label')}_ `{m.get('id')}`")
            if m.get("path"):
                lines.append(f"    📄 `{m['path']}`")
        return "\n".join(lines)
    if kind in ("show", "state"):
        node = obj.get("node")
        if node is None and "overview" in obj:
            return _human("schema", obj["overview"]) + f"\n\n_{obj.get('hint', '')}_"
        if node is None:
            return f"_{obj.get('error') or obj.get('note') or 'not found'}_"
        lines = [f"## {node.get('title')}  _{node.get('label')}_", f"`{node.get('id')}`", ""]
        path = (obj.get("properties") or {}).get("path")
        if path:
            lines += [f"📄 `{path}`", ""]  # where it lives on disk (the locate-at-a-glance line)
        if node.get("description"):
            lines += [node["description"], ""]
        elif node.get("gloss"):
            lines += [f"_{node['gloss']}_", ""]
        nb = obj.get("neighbours", [])
        if nb:
            lines.append("**Neighbours:**")
            for n in nb:
                arrow = "→" if n["direction"] == "out" else "←"
                lines.append(f"- {arrow} _{n['relation']}_ {_line(n['node'])[2:]}")
        return "\n".join(lines)
    if kind == "reconcile-memory":
        if obj.get("error"):
            return f"**error:** {obj['error']}"
        if obj.get("clean"):
            return "**reconcile-memory:** clean — no `.md`<->graph section drift"
        lines = [f"**reconcile-memory** — {obj['notes_with_drift']} note(s) with drift, "
                 f"{obj['absorbed_count']} section(s) absorbed"]
        for d in obj.get("drift", []):
            lines.append(f"- `{d['slug']}` — changed {[c['anchor'] for c in d['changed']]}"
                         + (f" · added {d['added']}" if d['added'] else "")
                         + (f" · removed {d['removed']}" if d['removed'] else ""))
            for c in d["changed"]:
                lines += [f"    · `{c['anchor']}` graph: {_short(c['graph'], 90)}",
                          f"    · `{c['anchor']}`  file: {_short(c['file'], 90)}"]
        for a in obj.get("absorbed", []):
            lines.append(f"  ↳ absorbed `{a['anchor']}` of `{a['slug']}` "
                         f"({a['prior_bytes']}→{a['new_bytes']} B; backup {a['backup']})")
        return "\n".join(lines)
    if kind == "structure":
        if obj.get("error"):
            return f"⚠ {obj['error']}"
        status = "written" if obj.get("written") else "dry-run"
        if "sections" in obj:  # new-note
            return f"**{status}** created note `{obj['slug']}` ({obj['sections']} sections) → `{obj.get('path')}`"
        bits = [f"`{obj['slug']}`"]
        if obj.get("existing"):
            bits.append(f"section `{obj.get('anchor')}` already exists — no-op")
        if obj.get("added"):
            bits.append(f"+section {obj['added']}")
        if obj.get("updated"):
            bits.append(f"~boundary {obj['updated']}")
        if obj.get("removed"):
            bits.append(f"removed(reported, not applied) {obj['removed']}")
        if obj.get("frontmatter_changed"):
            bits.append("frontmatter~")
        return f"**{status}** " + " · ".join(bits)
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


def _fmt_ts(ts: Any) -> str:  # Local wall-clock string, or the raw value
    """Unix seconds -> human-readable local time (the read-parity floor: raw
    floats are data, humans read wall-clock)."""
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return str(ts)
