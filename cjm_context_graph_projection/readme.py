"""README-as-projection (v1, STRUCTURAL-ONLY): generate a repo's README FROM THE GRAPH.

A doc projection (read-only — it only READS the graph, no write-back / no persistence flip,
so it is available now). The hand-rolled arc-lib READMEs drift (not updated with commits); a
generated one is always current and never drifts. Designed from the graph's STRENGTHS, not as
a transcription of today's layout ([[true-b-projected-structure-discussion]] README-as-projection):

- **The intro/"why" prose** is an ON-GRAPH repo-purpose assertion on the repo Entity (single
  source of truth, generalizes to any on-graph project) — read here, authored via `assert`.
  Hand-editing the README is the SIGNAL to move that prose on-graph instead.
- **The API surface** is DERIVED from the public top-level CodeSymbols (+ their docstring
  one-liners), grouped by module — always current.
- **The dependency summary** is DERIVED from cross-repo IMPORTS edges (depends-on / used-by).

v1 is **structural-only by design**: it derives ONLY from public code structure + the public
repo-purpose, so it has ZERO leak surface. The killer **decision-provenance** section (Decisions
that shaped the code, with provenance) is a deliberate FAST-FOLLOW gated on the public/private
[[graph-visibility-model]] — it would otherwise leak private planning into a public README.
"""

from collections import defaultdict
from typing import Any, Dict, List, Optional

from cjm_dev_graph_schema.identity import entity_node_id
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations

from . import factlayer as F
from .runtime import GraphHandle
from .write import resolve_subject

_GENERATED_MARKER = ("<!-- generated from the context graph by `cjm-context-graph readme` — "
                     "do not edit by hand; edit the graph (the urge to hand-edit = move it on-graph) -->")


async def repo_purpose(
    gx: GraphHandle,
    repo_key: str,  # The repo's durable conceptual slug
) -> Optional[str]:  # The active on-graph repo-purpose prose, or None
    """The repo's intro/"why" prose: the active `purpose` assertion on the repo Entity.

    Authored on-graph via `assert <repo-entity> purpose "<prose>"` (single source of truth),
    so the README intro never drifts from the project's stated purpose. None if unset.

    Resolves the subject the SAME way `assert` does (so a purpose set against the repo Entity
    id is found even though that Entity is a dangling ABOUT target minted as a `term` subject) —
    read-only: the resolver's `created_node` is never written here."""
    rid = entity_node_id("repo", repo_key)
    subject_id = (await resolve_subject(gx, rid))["subject_id"]
    slot = [a for a in await F.load_assertions(gx)
            if F.prop(a, "predicate") == "purpose" and F.prop(a, "subject_id") == subject_id]
    if not slot:
        return None
    active = F.active_assertions(slot, await F.load_supersedes(gx))
    return str(F.prop(active[-1], "value")) if active else None


async def project_readme(
    gx: GraphHandle,
    repo_key: str,  # The repo to project a README for
) -> Dict[str, Any]:  # {repo_key, markdown, module_count, symbol_count, has_purpose, depends_on, used_by} or {error}
    """Project a repo's README markdown from the graph (structural-only v1).

    Assembles: title + the on-graph repo-purpose (or a placeholder + a hint to author it) +
    a module TOC + a per-module public API surface + a cross-repo dependency summary, under a
    generated-artifact marker. Pure read — never writes."""
    all_modules = await F.load_label(gx, DevNodeKinds.CODE_MODULE)
    repo_modules = [m for m in all_modules if F.prop(m, "repo_key") == repo_key]
    if not repo_modules:
        return {"error": f"no modules for repo `{repo_key}` in the graph (ingest it first?)"}
    mod_ids = {F.nid(m) for m in repo_modules}
    repo_of = {F.nid(m): F.prop(m, "repo_key") for m in all_modules}
    name_of = {F.nid(m): (F.prop(m, "import_name") or F.prop(m, "module_path")) for m in all_modules}

    # Public, top-level symbols grouped by module (the API surface).
    syms_by_mod: Dict[str, List[Any]] = defaultdict(list)
    symbol_count = 0
    for s in await F.load_label(gx, DevNodeKinds.CODE_SYMBOL):
        mid = F.prop(s, "module_id")
        if mid not in mod_ids:
            continue
        qual = F.prop(s, "qualname", "") or ""
        if "." in qual or qual.startswith("_"):
            continue  # nested or private — not part of the public surface
        syms_by_mod[mid].append(s)
        symbol_count += 1

    # Cross-repo dependency summary from IMPORTS edges.
    depends_on, used_by = set(), set()
    for src, tgt in await F.load_edge_pairs(gx, DevRelations.IMPORTS):
        sr, tr = repo_of.get(src), repo_of.get(tgt)
        if sr == repo_key and tr and tr != repo_key:
            depends_on.add(tr)
        if tr == repo_key and sr and sr != repo_key:
            used_by.add(sr)

    purpose = await repo_purpose(gx, repo_key)
    ordered = sorted(repo_modules, key=lambda m: name_of[F.nid(m)])

    lines: List[str] = [f"# {repo_key}", "", _GENERATED_MARKER, ""]
    if purpose:
        lines += [purpose.rstrip(), ""]
    else:
        lines += [f"_No purpose recorded on-graph yet — author it with_ "
                  f"`assert {entity_node_id('repo', repo_key)} purpose \"…\"` "
                  f"_(or by the repo's entity key)._", ""]

    lines += ["## Modules", ""]
    for m in ordered:
        desc = F.prop(m, "description", "") or ""
        lines.append(f"- **`{name_of[F.nid(m)]}`**" + (f" — {desc}" if desc else ""))
    lines.append("")

    lines += ["## API", ""]
    for m in ordered:
        mid = F.nid(m)
        syms = sorted(syms_by_mod.get(mid, []), key=lambda s: F.prop(s, "qualname", ""))
        if not syms:
            continue
        lines += [f"### `{name_of[mid]}`", ""]
        for s in syms:
            kind = F.prop(s, "symbol_kind", "") or ""
            doc = F.prop(s, "description", "") or ""  # symbol docstring first-line (stored as `description`)
            lines.append(f"- `{F.prop(s, 'qualname', '')}` _{kind}_" + (f" — {doc}" if doc else ""))
        lines.append("")

    if depends_on or used_by:
        lines += ["## Dependencies", ""]
        if depends_on:
            lines.append("**Depends on:** " + ", ".join(f"`{r}`" for r in sorted(depends_on)))
        if used_by:
            lines.append("**Used by:** " + ", ".join(f"`{r}`" for r in sorted(used_by)))
        lines.append("")

    markdown = "\n".join(lines).rstrip() + "\n"
    return {"repo_key": repo_key, "markdown": markdown,
            "module_count": len(repo_modules), "symbol_count": symbol_count,
            "has_purpose": purpose is not None,
            "depends_on": sorted(depends_on), "used_by": sorted(used_by)}
