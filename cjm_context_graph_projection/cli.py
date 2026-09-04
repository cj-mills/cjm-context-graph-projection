"""The `cjm-context-graph` CLI — first driver of the projection core.

Read surface: `schema` / `state [subject]` / `relevant <task>` / `show <id>`
(the canonical session-start sequence) + `contradictions` / `worklist`. Write
surface: `assert` / `decide` / `oracle`. Plus `ingest` to build/refresh the dev
graph. `--graph-db-path` is always explicit; `--format agent|human` selects JSON
vs markdown.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from cjm_context_graph_layer.ops import extend_graph
from cjm_context_graph_primitives.journal import append_write, read_journal

from .authoring import add_symbol, author, emit_artifact, emit_post, read_node, read_slot
from .code_edges import orphaned_edges
from .cohesion import cohesion
from .config import load_graph_config
from .contradictions import contradictions
from .conventions import conventions
from .devgraph import build_dev_graph_elements, notes_corpus_elements
from .display import set_display_rule
from .explorer_page import EXPLORER_HTML
from .factlayer import note_alias_map
from .filing import filing, near_duplicates
from .hybrid_page import HYBRID_HTML
from .journal import (journal_sourced_note_paths, journal_window_view, M3_BASELINE_ACTOR,
                      m3_baseline_import, replay_journal)
from .lens import apply_lens, set_lens
from .listing import list_graph
from .module_ops import delete_module, flip_notebook_to_py, new_module, regroup, rename_module
from .onboarding import project_onboarding
from .oracle import run_version_oracle
from .projection import (explore, full_graph_view, get_schema, grep, locate, relevant, show, state,
                         subgraph_view)
from .prose_refs import prose_refs
from .readiness import readiness
from .readme import project_readme
from .reads import configure_reads, record_read
from .reconcile import reconcile_memory
from .refactor import refactor_candidates
from .refactor_ops import move
from .registers import register_drift
from .rename_ops import rename_symbol, rename_symbols
from .render import render as _render_base
from .runtime import DEFAULT_MANIFESTS, open_graph
from .seeds import repo_dir_name
from .serve import serve_graphs
from .source_state import (absorb_authored_text, cutover_module, emit_source_artifact, flip_module,
                           graph_sourced_modules, source_check)
from .structure import add_section, new_note
from .viz import project_viz
from .workbench import anchor_lead_view, portfolio_view, session_feed
from .worklist import dangling_reference_sources, worklist
from .write import (add_check, alias, assert_value, decide, link, register_session, retract_session,
                    unlink)

DEFAULT_MEMORY = ("/home/innom-dt/.claude/projects/"
                  "-mnt-SN850X-8TB-EXT4-Projects-GitHub-cj-mills-cjm-substrate/memory")
DEFAULT_REPOS = "/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills"
# The born-non-nbdev arc libs decomposed as the code source-type by default (the
# code-on-graph corpus); plain `.py`, so the python decomposer applies cleanly.
# The write-journal actor default (per-actor stamping 76397242): CJM_ACTOR
# overrides the baked default so distinct actors (primary agent, named
# sub-agents, the workbench) stamp distinguishable provenance with zero
# per-verb flags; an explicit --actor always wins over the env.
_DEFAULT_ACTOR = os.environ.get("CJM_ACTOR") or "agent:session"

DEFAULT_CODE_LIBS = ("cjm-dev-graph-schema", "cjm-markdown-decompose-core",
                     "cjm-notebook-decompose-core",
                     "cjm-context-graph-projection", "cjm-python-decompose-core",
                     "cjm-substrate-tui-kit",
                     "cjm-transcript-correction-tui", "cjm-transcription-tui",
                     "cjm-transcript-decomp-tui", "cjm-workflow-hub-tui",
                     # Session B.5 born-on-graph additions (user rule: NEW libs
                     # are born-on-graph; off-graph interface libs are a legacy
                     # unlikely-to-edit exception, not a pattern).
                     "cjm-sentence-segmentation-adapter-interface",
                     "cjm-capability-pysbd",
                     # Session D born-on-graph additions (the diarization pair,
                     # DEC 18d7de80): interface + capability, both editable
                     # until their publish slots in the window after 07-28.
                     "cjm-speaker-diarization-adapter-interface",
                     "cjm-capability-pyannote",
                     # Workbench round (833d27e4): the graph-workbench TUI,
                     # born-on-graph 2026-08-11.
                     "cjm-graph-workbench-tui",
                     # Qt pilot (d2a6d8e1, DEC 1e5b9a76): the PySide6 lane's
                     # slab-1 spike, born-on-graph 2026-08-13.
                     "cjm-graph-workbench-qt",
                     # Transcription migration (DEC dcf8a712): the first workflow
                     # TUI on the Qt lane, born-on-graph 2026-08-14.
                     "cjm-transcription-qt",
                     # The shared Qt foundation lib (DEC c4b0d6e5: minted at the
                     # first real duplication), born-on-graph 2026-08-14.
                     "cjm-substrate-qt-kit",
                     # Decomp migration (DEC 6c574c89): the second workflow
                     # TUI on the Qt lane, born-on-graph 2026-08-15.
                     "cjm-transcript-decomp-qt",
                     # Correction migration (DEC 0f11683d): the third workflow
                     # TUI on the Qt lane — the direct port, born-on-graph
                     # 2026-08-15.
                     "cjm-transcript-correction-qt",
                     # Hub migration (DEC 61b46ae8): the Qt front door — the
                     # last migration repo, spawn-not-suspend, born-on-graph
                     # 2026-08-15.
                     "cjm-workflow-hub-qt",
                     # Composition seat v0 (DECs 2a062aff + ea85eab7): the
                     # session scratchpad, born-on-graph 2026-08-19.
                     "cjm-session-scratchpad-qt",
                     # Spine absorption (DEC 12f342f1, 2026-08-19): the workflow
                     # cores absorbed the shells' toolkit-neutral domain modules
                     # (spine/state/runs/segments/candidates/launch…), so the
                     # cores join the ingest list — the moved code keeps its
                     # graph visibility (locate/grep) across the re-homing.
                     "cjm-transcription-core",
                     "cjm-transcript-decomp-core",
                     "cjm-transcript-correction-core")
# Repos whose NOTEBOOKS are the ingest source (cross-cell @patch/incremental methods
# re-attributed to their true classes by the compositor). EMPTY since the 2026-08-13
# audit: every lib the c25780e8/5a7c2af7-era list carried is now graph-sourced .py
# (0 notebooks repo-wide, cjm-substrate included). The five still-notebook adapter
# interfaces (forced-alignment / graph-storage / media-processing / source-separation
# / vad) were never listed and stay out of ingest scope until their on-graph
# transition. The whole DEFAULT_* block migrates to config under a1d965b0.
DEFAULT_NOTEBOOK_LIBS = ()

# Pillar-1 seam registry (DEC 6ee4b4f2): every CLI verb that can MUTATE source files on
# disk, mapped to whether its implementation routes through `journaled_emit` (events
# BEFORE files). `_dispatch` REFUSES an unrouted verb at dispatch time — a mutating verb
# is never 'available but dangerous', and verb N+1 lands here or the conformance test
# fails the build. `readme`/`onboarding`/`viz` are PROJECTORS (their --write targets
# generated surfaces, not journaled source); note-file writes (author on a Section,
# add-section, new-note, reconcile-memory) ride the WRITES-journal domain until the
# pillar-3 content-type unification.
MUTATES_SOURCE: Dict[str, bool] = {
    "author": True, "add-symbol": True, "add-text": True, "emit": True,
    "flip-module": True, "flip-to-py": True, "cutover": True, "emit-artifact": True,
    "move": True, "regroup": True, "new-module": True, "rename-module": True,
    "delete-module": True, "rename-symbol": True,
}

# Id-ref registry (work item b73e7688): every POSITIONAL that accepts a node id —
# and therefore MUST resolve a unique id prefix through the shared seam
# (`resolve_node_ref` / `resolve_subject` / `_resolve_module_ref`) — is declared
# here, verb by verb. The conformance test statically scans the parser source for
# id-shaped positionals and fails the build on any undeclared one, so verb N+1
# cannot ship without prefix support (the flip-module wall: 28 hits / 20 sessions
# before 8bc9abf4). `repo_key` on the three source-state verbs is DUAL: a repo
# slug in the two-positional form, a CodeModule id/prefix in the single-arg form.
# `edit-message.source_uuid` is exempt — a capture-source uuid, not a graph node.
ID_REFS: Dict[str, tuple] = {
    "state": ("subject",), "show": ("node_id",), "read": ("node_id",),
    "locate": ("term",), "lead": ("anchor",), "subgraph": ("refs",),
    "assert": ("subject",), "check": ("item",),
    "link": ("source_id", "target_id"), "unlink": ("source_id", "target_id"),
    "author": ("node_id",), "add-symbol": ("module",), "add-text": ("module",),
    "emit": ("module_id",), "move": ("symbol_id", "target_module_id"),
    "regroup": ("symbol_ids",), "rename-module": ("module_id",),
    "delete-module": ("module_id",), "rename-symbol": ("symbol_id",),
    "flip-module": ("repo_key",), "cutover": ("repo_key",),
    "emit-artifact": ("repo_key",), "emit-post": ("note_id",),
}


def _will_write(args) -> bool:
    """Whether this invocation would MUTATE source files (the seam-gate predicate).

    `emit` is read-only unless --write; every other MUTATES_SOURCE verb writes unless
    --no-write. Previews pass the gate — they touch nothing, so they need no journal."""
    if args.command == "emit":
        return bool(getattr(args, "write", False))
    return not getattr(args, "no_write", False)


async def _resolve_capture(
    gx,                 # The open graph handle
    spec: str,          # The `--capture` spec: seed | deferred | riding:<item-id-or-prefix>
):  # -> (spec {value, rides?} | None, error | None) — no Tuple import in cli
    """Validate a `decide --capture` spec (finding a3d196c6, user-ratified shape (a)).

    A capture's disposition is AUTHORED ground truth at mint — a derived seeds register
    cannot tell a deliberate deferral from a seed. `riding:<ref>` resolves the ridden
    item NOW (a prefix resolves against today's db; the journaled value carries the full
    id) so the capture can be REFERENCES-linked to it and `readiness --captures` can
    show the item's state beside it."""
    from .projection import ambiguity_error, resolve_node_ref
    s = (spec or "").strip()
    if s in ("seed", "deferred"):
        return {"value": s}, None
    if s.startswith("riding:"):
        ref = s[len("riding:"):].strip()
        if not ref:
            return None, "--capture riding:<item> needs an item id (or unique prefix)"
        r = await resolve_node_ref(gx, ref)
        if "candidates" in r:
            return None, ambiguity_error(ref, r["candidates"])
        node = r.get("node")
        if node is None:
            return None, f"--capture riding: no node `{ref}` — mint the item first, then cite it"
        full = node.get("id") if isinstance(node, dict) else getattr(node, "id", None)
        return {"value": f"riding:{full}", "rides": full}, None
    return None, f"--capture expects seed | deferred | riding:<item> (got {spec!r})"


async def _resolve_module_ref(
    gx,          # The open graph handle
    ref: str,    # A CodeModule node id, or a unique id prefix (>= 6 hex chars)
):  # -> ({repo_key, module_path} | None, error | None) — the _resolve_capture shape
    """Resolve a CodeModule id/prefix to (repo_key, module_path) for the source-state
    verbs (work item 8bc9abf4): flip-module / cutover / emit-artifact were the only
    id-taking verbs refusing a node id — the miner corpus's most-repeated wall (28
    hits / 20 sessions). Wrong label, ambiguity, and no-match all fail LOUD; the
    RESOLVED repo_key + module_path are what the source journal records, so replay
    never depends on a rebuildable node id."""
    from .projection import ambiguity_error, resolve_node_ref
    r = await resolve_node_ref(gx, ref)
    if "candidates" in r:
        return None, ambiguity_error(ref, r["candidates"])
    node = r.get("node")
    if node is None:
        return None, (f"no node `{ref}` — pass repo_key + module_path, or a "
                      f"CodeModule id / unique prefix (`locate` the module first)")
    label = node.get("label") if isinstance(node, dict) else getattr(node, "label", None)
    props = (node.get("properties") if isinstance(node, dict)
             else getattr(node, "properties", None)) or {}
    if label != "CodeModule":
        return None, (f"`{ref}` resolves to a {label} node, not a CodeModule — "
                      f"this verb wants the module (`locate <file>` gives its id)")
    repo_key, module_path = props.get("repo_key"), props.get("module_path")
    if not (repo_key and module_path):
        return None, f"CodeModule `{ref}` carries no repo_key/module_path properties"
    return {"repo_key": repo_key, "module_path": module_path}, None


def _editor_pop(
    initial: str,         # The current slot text to seed the buffer with
    suffix: str = ".py",  # Temp-file suffix (editor syntax highlighting)
) -> str:  # The edited buffer
    """Open `$EDITOR` on the current slot text and return the saved buffer.

    The minimal human authoring UI (the `git commit` pattern): zero state, rides the
    CLI, captures the edited verbatim text. `$EDITOR`/`$VISUAL`, else `nano`."""
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
    fd, tmp = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(initial)
        subprocess.run([*editor.split(), tmp], check=True)
        return Path(tmp).read_text()
    finally:
        os.unlink(tmp)


def _absorb_graph_sourced(res, args) -> int:  # 0 = ok (absorbed or not applicable), 1 = loud failure
    """RETIRED IN PLACE (pillar-1 seam, DEC 6ee4b4f2): no call sites — journaling now
    happens INSIDE the authoring verbs via `journaled_emit` (events BEFORE files), so
    this after-the-fact absorb wrapper is dead; it awaits the delete-symbol verb
    (0d8adfac) for physical removal.

    Was: N+3 Phase 2 absorb gate, shared by the module-emitting write verbs (author,
    add-symbol): an edit of a GRAPH-SOURCED module lands in the SOURCE journal (the
    authority), canonicalized, with the artifact file kept in sync. A notebook's
    journal key is its .ipynb source path (what cutover recorded) — NOT the nbdev
    export-target `module_path` the result carries — re-derived from `artifact_path`
    under --repos-dir, loud-fail (the f06ef1a6 lesson: 'written to disk' and
    'journaled' are separate facts)."""
    if not (res.get("artifact") in ("module", "notebook") and args.source_journal_path
            and res.get("written") and not res.get("unchanged")):
        return 0
    src_path = res.get("module_path")
    if res["artifact"] == "notebook":
        try:
            src_path = Path(res["artifact_path"]).relative_to(
                Path(args.repos_dir) / res["repo_key"]).as_posix()
        except (KeyError, TypeError, ValueError):
            print(f"⚠ authored notebook NOT absorbed into the source journal: "
                  f"cannot derive the repo-relative path of "
                  f"{res.get('artifact_path')!r} under {args.repos_dir!r}",
                  file=sys.stderr)
            return 1
    # The node's repo_key is the rename-stable CONCEPTUAL key; the source journal
    # keys by repo DIR name (source_state's space) — denormalize so a renamed
    # repo's authored state still absorbs (finding c89519cd: an unmapped key
    # skipped this gate silently, emitting the file WITHOUT journaling).
    dir_key = repo_dir_name(res.get("repo_key"))
    if ((dir_key, src_path)
            in graph_sourced_modules(args.source_journal_path)):
        ab = absorb_authored_text(args.source_journal_path, dir_key,
                                  src_path, res["artifact_path"],
                                  res["emitted_text"])
        if ab.get("error"):
            print(f"⚠ source-journal absorb FAILED: {ab['error']}", file=sys.stderr)
            return 1
        print(f"  ↳ graph-sourced: authored state journaled"
              f"{' (canonicalized — file rewritten)' if ab.get('canonicalized') else ''}")
    return 0


async def _dispatch(args) -> int:
    # The pillar-1 seam gate (DEC 6ee4b4f2): a source-mutating invocation is refused
    # OUTRIGHT when its verb isn't routed through journaled_emit, or when no source
    # journal is given for events to land in BEFORE files. Dispatch-time, not deep in
    # the op — never 'available but dangerous'.
    if args.command in MUTATES_SOURCE and _will_write(args):
        if not MUTATES_SOURCE[args.command]:
            print(f"error: `{args.command}` mutates source files but is NOT yet routed "
                  "through the journaled_emit seam (journal-first) — refusing; preview "
                  "with --no-write, or wire the verb through the seam first",
                  file=sys.stderr)
            return 1
        if not args.source_journal_path:
            print(f"error: `{args.command}` mutates source state — pass "
                  "--source-journal-path so events land in the journal BEFORE files "
                  "(cg-write bakes it)", file=sys.stderr)
            return 1
    if args.command == "serve":
        # The long-lived read-only explorer: opens N graphs itself (primary + --also),
        # so it doesn't ride the single-graph context below.
        await serve_graphs([args.graph_db_path, *(args.also or [])], host=args.host,
                           port=args.port, manifests_dir=args.manifests_dir,
                           index_html=EXPLORER_HTML, hybrid_html=HYBRID_HTML)
        return 0
    async with open_graph(args.graph_db_path, args.manifests_dir) as gx:
        if args.command == "ingest":
            note_aliases = await note_alias_map(gx)  # confirmed link aliases heal drifted refs
            code_repos = None
            if not args.no_code:
                libs = args.code_lib or list(DEFAULT_CODE_LIBS)
                code_repos = [str(Path(args.repos_dir) / name) for name in libs]
            notebook_repos = None
            if not args.no_notebooks:
                nb_libs = args.notebook_lib or list(DEFAULT_NOTEBOOK_LIBS)
                notebook_repos = [str(Path(args.repos_dir) / n) for n in nb_libs]
            # Authority flip: notes with a genesis `new-note` op (migrated OR born on-graph)
            # are reconstructed from the journal during replay, so don't read their `.md` here.
            skip_memory_paths = journal_sourced_note_paths(args.journal_path) if args.journal_path else None
            nodes, edges = build_dev_graph_elements(
                args.memory_dir, None if args.no_repo_map else args.repos_dir,
                seed=not args.no_seed, note_aliases=note_aliases, code_repos=code_repos,
                notebook_repos=notebook_repos, skip_memory_paths=skip_memory_paths,
                source_journal_path=args.source_journal_path)
            res = await extend_graph(gx.queue, gx.graph_id, nodes, edges)
            print(f"ingested: {res.nodes_added} nodes added / {res.nodes_verified} verified, "
                  f"{res.edges_added} edges added / {res.edges_existing} existing")
            if args.journal_path:
                # Replay born-on-graph writes on top of the fresh projection so
                # `rm db && ingest` fully reconstructs the graph (the migration story).
                rc = await replay_journal(gx, args.journal_path)
                print(f"replayed journal: {rc}")
            return 0
        if args.command == "ingest-notes":
            if not args.notes_corpus:
                print("error: ingest-notes needs --notes-corpus or a `notes_corpus` key in the "
                      "graph-sibling graph.config.json beside --graph-db-path", file=sys.stderr)
                return 1
            nodes, edges = notes_corpus_elements(args.notes_corpus, args.profile or "quarto_post")
            res = await extend_graph(gx.queue, gx.graph_id, nodes, edges)
            print(f"ingested notes: {res.nodes_added} nodes added / {res.nodes_verified} verified, "
                  f"{res.edges_added} edges added / {res.edges_existing} existing")
            if args.journal_path:
                rc = await replay_journal(gx, args.journal_path)
                print(f"replayed journal: {rc}")
            return 0
        if args.command == "m3-baseline":
            if not args.journal_path:
                print("error: m3-baseline needs --journal-path (the genesis ops are journaled)",
                      file=sys.stderr)
                return 1
            if not args.all and not args.slug:
                print("error: m3-baseline needs --slug SLUG (repeatable) or --all", file=sys.stderr)
                return 1
            res = m3_baseline_import(args.memory_dir, args.journal_path,
                                     slugs=args.slug, all_notes=args.all)
            print(f"m3-baseline: imported {res['imported_count']} note(s) "
                  f"(actor {M3_BASELINE_ACTOR}); skipped_existing={res['skipped_existing']} "
                  f"unknown={res['unknown']} corpus={res['corpus_notes']}")
            for it in res["imported"]:
                print(f"  + {it['slug']} ({it['bytes']} bytes) <- {it['path']}")
            print("NEXT -> rebuild (cg-rebuild): ingest now SKIPS these .md; replay reconstructs them.")
            return 1 if res["unknown"] else 0
        if args.command == "replay":
            if not args.journal_path:
                print("error: replay needs --journal-path", file=sys.stderr)
                return 1
            rc = await replay_journal(gx, args.journal_path, offset=args.offset)
            print(f"replayed journal: {rc}")
            return 0
        if args.command == "schema":
            print(render("schema", await get_schema(gx), args.format))
        elif args.command == "state":
            print(render("state", await state(gx, args.subject), args.format))
        elif args.command == "relevant":
            print(render("relevant", await relevant(gx, args.task, depth=args.depth, k=args.k),
                         args.format))
        elif args.command == "explore":
            filters = []
            for f in (args.facet or []):
                if "=" not in f:
                    print(f"error: --facet expects axis=value (got '{f}')", file=sys.stderr)
                    return 2
                axis, value = f.split("=", 1)
                filters.append({"axis": axis, "value": value})
            res = await explore(gx, args.task, filters, depth=args.depth, budget=args.budget)
            print(render("explore", res, args.format))
        elif args.command == "show":
            jp = [p for p in (args.journal_path, args.source_journal_path) if p]
            print(render("show", await show(gx, args.node_id, depth=args.depth,
                                            journal_paths=jp), args.format))
        elif args.command == "locate":
            print(render("locate", await locate(gx, args.term, limit=args.limit), args.format))
        elif args.command == "grep":
            jp = [p for p in (args.journal_path, args.source_journal_path) if p]
            print(render("grep", await grep(gx, args.term, limit=args.limit,
                                            context=args.context, labels=args.label,
                                            session=args.session, journal_paths=jp),
                         args.format))
        elif args.command == "read":
            if args.session:
                # 1d8d4486: a spine's Message bodies in chain order (role-filtered).
                from .scratchpad_export import read_session_messages
                res = await read_session_messages(gx, args.session, role=args.role,
                                                  include_superseded=args.superseded,
                                                  include_parts=args.parts)
            elif not args.node_id:
                print("error: read needs node id(s) or --session <key>", file=sys.stderr)
                return 2
            elif len(args.node_id) > 1:
                # 1d8d4486: N ids in ONE subprocess — a delimited block per node; one
                # unresolvable id is reported in its block, never fails the batch.
                items = [await read_node(gx, nid) for nid in args.node_id]
                res = {"kind": "batch", "items": items, "count": len(items),
                       "errors": sum(1 for it in items if it.get("error"))}
            else:
                res = await read_node(gx, args.node_id[0])
            out = render("read", res, args.format)
            # Content delivery: print the verbatim text exactly (a note body already ends
            # with its file's trailing newline) so `read > file` is byte-faithful; status/
            # JSON lines (errors, nested-symbol hints) get the usual newline.
            if (args.format == "human" and not res.get("error")
                    and res.get("kind") not in ("nested", "batch", "messages")):
                sys.stdout.write(out)
            else:
                print(out)
            return 1 if res.get("error") else 0
        elif args.command == "contradictions":
            print(render("contradictions", await contradictions(gx, args.scope), args.format))
        elif args.command == "readiness":
            res = await readiness(gx, args.scope or args.contains,
                                  state=("captures" if args.captures else args.state),
                                  limit=args.limit, offset=args.offset,
                                  anchor=args.anchor, where=args.where)
            print(render("readiness", res, args.format))
        elif args.command == "register-drift":
            print(render("register-drift", await register_drift(gx), args.format))
        elif args.command == "prose-refs":
            print(render("prose-refs", await prose_refs(gx, limit=args.limit), args.format))
        elif args.command == "filing":
            print(render("filing", await filing(gx, top_k=args.top_k), args.format))
        elif args.command == "orphaned-edges":
            if not args.journal_path:
                print("⚠ orphaned-edges needs --journal-path (the link ops to audit)")
                return 1
            print(render("orphaned-edges", await orphaned_edges(gx, args.journal_path), args.format))
        elif args.command == "journal-window":
            paths = [p for p in (args.journal_path, args.source_journal_path) if p]
            if not paths:
                print("error: journal-window needs --journal-path (and usually "
                      "--source-journal-path — code touches live there)", file=sys.stderr)
                return 1
            res = await journal_window_view(gx, paths, start=_parse_ts(args.start),
                                            end=_parse_ts(args.end), session=args.session,
                                            verbs=args.verb, exclude_verbs=args.exclude_verb,
                                            labels=args.label)
            print(render("journal-window", res, args.format))
        elif args.command == "subgraph":
            res = await subgraph_view(gx, args.refs, hops=args.hops,
                                      relations=args.relation, cap=args.cap)
            print(render("subgraph", res, args.format))
        elif args.command == "portfolio":
            paths = [p for p in (args.journal_path, args.source_journal_path) if p]
            print(render("portfolio", await portfolio_view(gx, journal_paths=paths),
                         args.format))
        elif args.command == "lead":
            print(render("lead", await anchor_lead_view(gx, args.anchor), args.format))
        elif args.command == "feed":
            paths = [p for p in (args.journal_path, args.source_journal_path) if p]
            if not paths:
                print("error: feed needs --journal-path (and usually "
                      "--source-journal-path — code touches live there)", file=sys.stderr)
                return 1
            res = await session_feed(gx, paths, session=args.session,
                                     since=_parse_ts(args.since), limit=args.limit)
            print(render("feed", res, args.format))
        elif args.command == "export":
            res = await full_graph_view(gx)
            print(render("export", res, args.format))
        elif args.command == "list":
            res = await list_graph(gx, label=args.label, predicate=args.predicate,
                                   relation=args.relation, limit=args.limit,
                                   offset=args.offset, contains=args.contains,
                                   where=args.where, value=args.value, full=args.full)
            print(render("list", res, args.format))
            return 1 if res.get("error") else 0
        elif args.command == "conventions":
            print(render("conventions", await conventions(gx, args.scope), args.format))
        elif args.command == "refactor-candidates":
            print(render("refactor", await refactor_candidates(gx, args.scope), args.format))
        elif args.command == "cohesion":
            print(render("cohesion", await cohesion(gx, args.scope), args.format))
        elif args.command == "worklist":
            print(render("worklist", await worklist(gx, args.memory_dir), args.format))
        elif args.command == "assert":
            res = await assert_value(gx, args.subject, args.predicate, args.value,
                                     actor=args.actor, evidence=args.evidence,
                                     supersede=args.supersede)
            print(render("assert", res, args.format))
            # Never journal a REFUSED write (ambiguous/typo'd id-shaped subject) —
            # replay must not re-attempt it.
            if args.journal_path and not res.get("error"):
                append_write(args.journal_path, "assert",
                             {"subject": args.subject, "predicate": args.predicate,
                              "value": args.value, "actor": args.actor,
                              "evidence": args.evidence, "supersede": args.supersede})
            return 1 if res.get("error") else (2 if res.get("conflict") else 0)
        elif args.command == "alias":
            actor = f"agent:session:{args.session}" if args.session else args.actor
            evidence = (args.evidence
                        or (dangling_reference_sources(args.memory_dir, args.drifted)
                            if args.memory_dir else None))
            res = await alias(gx, args.drifted, args.canonical, actor=actor, evidence=evidence)
            print(render("alias", res, args.format))
            if args.journal_path and not res.get("error"):
                append_write(args.journal_path, "alias",
                             {"drifted": args.drifted, "canonical": args.canonical,
                              "actor": actor, "evidence": evidence})
            return 1 if res.get("error") else 0
        elif args.command == "decide":
            # decide safety (build 1b109ddc): shell-proof statement input + debris
            # refusal + unknown-id citation warning — a Decision statement has NO
            # repair path, so mangling must be caught BEFORE the mint.
            import re as _re
            statement = args.statement
            if getattr(args, "statement_file", None):
                if statement is not None:
                    print("error: pass a positional statement OR --statement-file, "
                          "not both", file=sys.stderr)
                    return 2
                statement = Path(args.statement_file).read_text().strip()
            if not statement:
                print("error: no statement — pass it positionally or via "
                      "--statement-file", file=sys.stderr)
                return 2
            debris = next((m for m in (": command not found",
                                       ": No such file or directory")
                           if m in statement), None)
            if debris is not None:
                print(f"error: the statement carries shell-mangling debris ({debris!r})"
                      " — a backtick inside a double-quoted statement is command-"
                      "substituted BEFORE the CLI sees it. Re-mint via "
                      "--statement-file.", file=sys.stderr)
                return 2
            from .projection import resolve_node_ref as _rnr
            unknown = []
            for tok in dict.fromkeys(_re.findall(r"\b[0-9a-f]{6,40}\b", statement)):
                if not _re.search(r"[a-f]", tok) or len(unknown) >= 8:
                    continue  # all-digit tokens are usually dates/counts, not citations
                if not await _rnr(gx, tok):
                    unknown.append(tok)
            if unknown:
                print("⚠ statement cites id(s) that resolve to no node: "
                      + ", ".join(f"`{t}`" for t in unknown)
                      + " — forward-written id? Mint first, cite after (the citation "
                        "stands verbatim in an unrepairable statement).", file=sys.stderr)
            if args.state and args.capture:
                print("error: a capture is not a work item — pass --state OR --capture",
                      file=sys.stderr)
                return 2
            capture = None
            if args.capture:
                # Validate + resolve BEFORE minting, so a bad spec never leaves a stray
                # unstated decision behind (mint-first would strand it).
                capture, cerr = await _resolve_capture(gx, args.capture)
                if cerr:
                    print(f"error: {cerr}", file=sys.stderr)
                    return 1
            res = await decide(gx, statement, actor=args.actor, supports=args.supports,
                               supersedes=args.supersedes, session=args.session,
                               title=args.title)
            # Mint-time near-duplicate surfacing (ff4e275e shape (a)): propose-only —
            # the mint always lands; the fresh decision carries no task_state yet so
            # it never matches itself. Replay bypasses this branch (rebuilds stay flat).
            if not res.get("error"):
                res["near_duplicates"] = await near_duplicates(gx, statement)
            print(render("decide", res, args.format))
            if args.journal_path:
                append_write(args.journal_path, "decide",
                             {"statement": statement, "actor": args.actor,
                              "supports": args.supports, "supersedes": args.supersedes,
                              "session": args.session, "title": args.title})
            # --state open: the frontier-visibility enforcement — a freshly minted work
            # item is INVISIBLE to readiness until task_state is asserted, so mint +
            # assert land in ONE invocation (explicit flag, not title-pattern magic).
            if args.state and not res.get("error"):
                st = await assert_value(gx, res["decision_id"], "task_state", args.state,
                                        actor=args.actor)
                print(render("assert", st, args.format))
                if args.journal_path and not st.get("error"):
                    append_write(args.journal_path, "assert",
                                 {"subject": res["decision_id"], "predicate": "task_state",
                                  "value": args.state, "actor": args.actor,
                                  "evidence": None, "supersede": False})
            # --capture: the AUTHORED capture_state (a3d196c6 shape (a)) lands beside the
            # mint like --state does, and a riding capture REFERENCES the item it rides —
            # both journaled, so a rebuild keeps the capture visible to `readiness --captures`.
            if capture and not res.get("error"):
                st = await assert_value(gx, res["decision_id"], "capture_state",
                                        capture["value"], actor=args.actor)
                print(render("assert", st, args.format))
                if args.journal_path and not st.get("error"):
                    append_write(args.journal_path, "assert",
                                 {"subject": res["decision_id"], "predicate": "capture_state",
                                  "value": capture["value"], "actor": args.actor,
                                  "evidence": None, "supersede": False})
                if capture.get("rides"):
                    lk = await link(gx, res["decision_id"], capture["rides"], "REFERENCES",
                                    actor=args.actor)
                    print(render("link", lk, args.format))
                    if args.journal_path and lk.get("written"):
                        append_write(args.journal_path, "link",
                                     {"source_id": lk["source_id"], "target_id": lk["target_id"],
                                      "relation": "REFERENCES", "actor": args.actor,
                                      "source_label": lk.get("source_label"),
                                      "target_label": lk.get("target_label")})
        elif args.command == "uncaptured":
            from .source_state import uncaptured_modules
            cfg = load_graph_config(args.graph_db_path)
            libs = args.repo or cfg.get("code_libs") or list(DEFAULT_CODE_LIBS)
            if not args.source_journal_path:
                print("error: uncaptured needs --source-journal-path (cg-write bakes it)",
                      file=sys.stderr)
                return 1
            repos_dir = cfg.get("repos_dir") or args.repos_dir
            rows = uncaptured_modules(args.source_journal_path, libs, repos_dir)
            if not rows:
                print(f"every .py in {len(libs)} code_libs repo(s) is journal-captured ✓")
                return 0
            total = sum(len(v) for v in rows.values())
            print(f"## Uncaptured modules ({total} file(s) across {len(rows)} repo(s))")
            for key in sorted(rows):
                print(f"- **{key}** ({len(rows[key])}):")
                for m in rows[key]:
                    print(f"    {m}")
            print("_capture: `flip-module <repo_key> <module_path>` then `cutover` — "
                  "tests/ included (user rule ac3d52f4)_")
            return 1
        elif args.command == "display-rule":
            res = await set_display_rule(gx, args.for_label, args.title, args.gloss,
                                         actor=args.actor)
            print(render("display-rule", res, args.format))
            # Presentation vocabulary is journal-sourced like every born-on-graph write:
            # the last display-rule op per kind wins on replay (deterministic-id upsert).
            if args.journal_path and res.get("written"):
                append_write(args.journal_path, "display-rule",
                             {"for_label": args.for_label, "title_template": args.title,
                              "gloss_template": args.gloss, "actor": args.actor})
            return 1 if res.get("error") else 0
        elif args.command == "set-lens":
            if args.spec_file:
                spec_text = Path(args.spec_file).read_text()
            elif args.spec:
                spec_text = args.spec
            else:
                print("error: set-lens needs --spec '<json>' or --spec-file", file=sys.stderr)
                return 1
            try:
                spec = json.loads(spec_text)
            except json.JSONDecodeError as e:
                print(f"error: spec is not valid JSON: {e}", file=sys.stderr)
                return 1
            res = await set_lens(gx, args.slug, spec, title=args.title,
                                 description=args.description, actor=args.actor)
            print(render("set-lens", res, args.format))
            # Lens vocabulary is journal-sourced like display-rule: the last
            # set-lens op per slug wins on replay (deterministic-id upsert).
            if args.journal_path and res.get("written"):
                append_write(args.journal_path, "set-lens",
                             {"slug": args.slug, "spec": spec, "title": args.title,
                              "description": args.description, "actor": args.actor})
            return 1 if res.get("error") else 0
        elif args.command == "lens":
            params: Dict[str, str] = {}
            for kv in (args.param or []):
                if "=" not in kv:
                    print(f"error: --param wants NAME=VALUE (got {kv!r})", file=sys.stderr)
                    return 1
                k, v = kv.split("=", 1)
                params[k] = v
            paths = [p for p in (args.journal_path, args.source_journal_path) if p]
            res = await apply_lens(gx, args.slug, params, journal_paths=paths or None)
            print(render("lens", res, args.format))
            return 1 if res.get("error") else 0
        elif args.command == "session":
            started = _parse_ts(args.started_at)
            if started is None:
                # A timestamp-form key IS its own start time (the scratchpad convention).
                try:
                    started = datetime.strptime(args.key, "%Y-%m-%d_%H-%M-%S").timestamp()
                except ValueError:
                    started = None
            res = await register_session(gx, args.key, started_at=started,
                                         title=args.title, actor=args.actor)
            print(render("session", res, args.format))
            if args.journal_path and res.get("written"):
                append_write(args.journal_path, "session",
                             {"key": args.key, "started_at": started, "title": args.title,
                              "actor": args.actor})
            return 1 if res.get("error") else 0
        elif args.command == "retract-session":
            # Emptiness guard CALLER-side (the verb + replay apply unconditionally):
            # any journaled op attributed to the key besides its own registrations
            # means the session is real history — refuse without --force.
            paths = [p for p in (args.journal_path, args.source_journal_path) if p]
            foreign = [
                op for p in paths for op in read_journal(p)
                if (op.get("session") or (op.get("args") or {}).get("session")) == args.key
                and not (op.get("verb") == "session"
                         and (op.get("args") or {}).get("key") == args.key)]
            if foreign and not args.force:
                print(f"error: session '{args.key}' has {len(foreign)} journaled op(s) "
                      f"attributed to it — not an empty mint. Re-run with --force to "
                      f"retract anyway.", file=sys.stderr)
                return 1
            res = await retract_session(gx, args.key, actor=args.actor)
            print(render("retract-session", res, args.format))
            if args.journal_path and res.get("written"):
                append_write(args.journal_path, "retract-session",
                             {"key": args.key, "actor": args.actor})
            return 1 if res.get("error") else 0
        elif args.command == "pull-transcript":
            # Scratchpad-v2 pull (fc6a0cdc): mint the active path's user-facing
            # messages onto the session spine. Journal-first with the NEW-message
            # PAYLOAD (transcripts are prunable external files — the journal must
            # reconstruct alone); nothing new = nothing journaled, the
            # watcher-cadence guarantee.
            from .pull_transcript import pull_transcript
            res = await pull_transcript(gx, args.key,
                                        str(Path(args.transcript_dir).expanduser()),
                                        require_signal=not args.any_boot,
                                        actor=args.actor)
            if res.get("error"):
                print(f"error: {res['error']}", file=sys.stderr)
                return 1
            print(f"**pulled** `{args.key}` <- transcript `{res['cc_session_uuid']}`: "
                  f"{res['messages_new']} new / {res['messages_total']} total message(s) "
                  f"({res['nodes_added']} node(s), {res['edges_added']} edge(s) added)")
            if res.get("other_candidates"):
                print(f"  other candidate transcript(s): {res['other_candidates']}")
            if args.journal_path and res.get("new_messages"):
                append_write(args.journal_path, "pull-transcript",
                             {"session_key": args.key,
                              "cc_session_uuid": res.get("cc_session_uuid", ""),
                              "messages": res["new_messages"], "actor": args.actor})
            # Chain re-link (finding e358fe97): a wider extraction that inserts
            # a message MID-chain leaves the stale prev->next edge beside the
            # new ones; the pull retracts it and reports it here — journal the
            # compensating `unlink` ops AFTER the pull op so a rebuild converges
            # (independent of new_messages: a repair pull may mint nothing).
            for e in res.get("retracted_edges") or []:
                print(f"  re-linked: retracted stale NEXT "
                      f"{e['source_id'][:8]} -> {e['target_id'][:8]}")
                if args.journal_path:
                    append_write(args.journal_path, "unlink",
                                 {"source_id": e["source_id"], "target_id": e["target_id"],
                                  "relation": e["relation"], "actor": args.actor})
            return 0
        elif args.command == "edit-message":
            # In-place Message edit (DEC 91c47b4a pt 3, CLI dual of the
            # scratchpad's journaled op; widened for the 47b83adb retro-sweep):
            # journal-first body + optional --set property updates (role/source
            # facet corrections), last-op-wins on replay.
            from .pull_transcript import edit_message
            if (args.text is None) == (args.text_file is None):
                print("error: exactly one of --text / --text-file is required",
                      file=sys.stderr)
                return 1
            text = (Path(args.text_file).expanduser().read_text()
                    if args.text_file else args.text)
            props: Dict[str, str] = {}
            for kv in args.set or []:
                if "=" not in kv:
                    print(f"error: --set expects KEY=VALUE, got {kv!r}", file=sys.stderr)
                    return 1
                k, v = kv.split("=", 1)
                props[k] = v
            res = await edit_message(gx, args.source_uuid, text,
                                     properties=props or None, actor=args.actor)
            if res.get("error"):
                print(f"error: {res['error']}", file=sys.stderr)
                return 1
            print(f"**edited** Message `{res['message_id'][:8]}` (source uuid "
                  f"`{args.source_uuid}`"
                  + (f"; set {', '.join(sorted(props))}" if props else "") + ")")
            if args.journal_path:
                op = {"source_uuid": args.source_uuid,
                      "text": text, "actor": args.actor}
                if props:
                    op["properties"] = props
                append_write(args.journal_path, "edit-message", op)
            return 0
        elif args.command == "export-session":
            # The exporter lens (5ab24c57): read-only projection — no journal op.
            import json as _json
            from .scratchpad_export import DEFAULT_CONFIG, export_session_markdown
            config = dict(DEFAULT_CONFIG)
            if args.config_file:
                config.update(_json.loads(Path(args.config_file).expanduser().read_text()))
            if args.include_superseded:
                config["superseded"] = True
            if args.lanes:
                config["lanes"] = args.lanes
            res = await export_session_markdown(gx, args.key, config)
            if res.get("error"):
                print(f"error: {res['error']}", file=sys.stderr)
                return 1
            if args.out:
                out = Path(args.out).expanduser()
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(res["text"], encoding="utf-8")
                print(f"**exported** `{args.key}` → {out} ({res['messages']} transcript "
                      f"message(s) + {res['parts']} part(s))")
            else:
                print(res["text"])
            return 0
        elif args.command == "oracle":
            # Journal-first (b744b28e): the Procedure mint + every changed assertion ride
            # the writes journal so a rebuild keeps the oracle's facts (cg-write bakes it).
            res = await run_version_oracle(gx, repos_dir=args.repos_dir, only=args.only,
                                           journal_path=args.journal_path)
            print(render("oracle", res, args.format))
            return 0
        elif args.command == "link":
            res = await link(gx, args.source_id, args.target_id, args.relation, actor=args.actor)
            print(render("link", res, args.format))
            if args.journal_path and res.get("written"):
                # Endpoint labels are AUDIT-ONLY (replay ignores them): they are what
                # lets the orphaned-edge detector propose a remap after a code rename
                # deletes the deterministic old id. Journal the RESOLVED ids (a prefix
                # resolves against TODAY's db; replay must land on the same nodes).
                append_write(args.journal_path, "link",
                             {"source_id": res["source_id"], "target_id": res["target_id"],
                              "relation": args.relation, "actor": args.actor,
                              "source_label": res.get("source_label"),
                              "target_label": res.get("target_label")})
            return 1 if res.get("error") else 0
        elif args.command == "unlink":
            if args.journal_path and not args.force:
                # PRE-FLIGHT (before anything is deleted): retraction is scoped to
                # DELIBERATE links — an ingest-derived edge (CONTAINS/CALLS/...) has
                # no journaled link op, and retracting one would make the unlink
                # replay a standing structural override. Journaled ops carry FULL
                # resolved ids, so a caller's unique prefix matches by startswith;
                # --force acknowledges the structural-override intent.
                journaled = any(
                    o.get("verb") == "link"
                    and str((o.get("args") or {}).get("source_id", "")).startswith(args.source_id)
                    and str((o.get("args") or {}).get("target_id", "")).startswith(args.target_id)
                    and (o.get("args") or {}).get("relation") == args.relation
                    for o in read_journal(args.journal_path))
                if not journaled:
                    print(f"⚠ no journaled link op matches "
                          f"`{args.source_id}` —{args.relation}→ `{args.target_id}` — "
                          f"this looks like an ingest-derived (structural) edge; "
                          f"pass --force to retract it anyway (nothing deleted)")
                    return 1
            res = await unlink(gx, args.source_id, args.target_id, args.relation,
                               actor=args.actor)
            print(render("unlink", res, args.format))
            if args.journal_path and res.get("written"):
                append_write(args.journal_path, "unlink",
                             {"source_id": res["source_id"], "target_id": res["target_id"],
                              "relation": res["relation"], "actor": args.actor})
            return 1 if res.get("error") else 0
        elif args.command == "check":
            res = await add_check(gx, args.item, args.text, actor=args.actor)
            print(render("check", res, args.format))
            # Journal the RESOLVED item id (a prefix resolves against TODAY's db;
            # replay must land on the same node regardless of future prefix collisions).
            if args.journal_path and res.get("written"):
                append_write(args.journal_path, "check",
                             {"item_id": res["item_id"], "text": args.text,
                              "actor": args.actor})
            return 1 if res.get("error") else 0
        elif args.command == "author":
            replace, edit = None, None
            if args.editor:
                cur = await read_slot(gx, args.node_id)
                if cur.get("error"):
                    print(render("author", cur, args.format))
                    return 1
                replace = _editor_pop(cur["text"])
            elif args.replace_file:
                replace = Path(args.replace_file).read_text()
            elif args.replace is not None:
                replace = args.replace
            elif args.edit:
                edit = (args.edit[0], args.edit[1])
            res = await author(gx, args.node_id, replace=replace, edit=edit,
                               actor=args.actor, write=not args.no_write,
                               source_journal_path=args.source_journal_path,
                               repos_dir=args.repos_dir)
            print(render("author", res, args.format))
            _cli_import_smoke(res)
            # M2b shadow: a memory-section author also journals its raw STATE (the .md stays the
            # ingest source for now; the journal shadows + soaks). NON-cut-over code/notebook
            # authoring stays un-journaled (Fork-1(a)); GRAPH-SOURCED modules/notebooks land in
            # the SOURCE journal below. Skip no-op edits; append_write dedups identical states.
            if (res.get("artifact") == "note" and args.journal_path
                    and res.get("written") and not res.get("unchanged")):
                append_write(args.journal_path, "section",
                             {"slug": res.get("note_slug"), "anchor": res.get("anchor"),
                              "raw": res.get("new_text"), "actor": args.actor})
            return 1 if res.get("error") else 0
        elif args.command == "add-symbol":
            body = Path(args.body_file).read_text() if args.body_file else args.body
            res = await add_symbol(gx, args.module, body, actor=args.actor,
                                   write=not args.no_write,
                                   source_journal_path=args.source_journal_path,
                                   repos_dir=args.repos_dir, after=args.after)
            print(render("add-symbol", res, args.format))
            _cli_import_smoke(res)
            return 1 if res.get("error") else 0
        elif args.command == "add-text":
            # Local import: adding a name to this module's import line is the open
            # binding-table gap (47b256de) — stay self-contained until it closes.
            from .authoring import add_text
            body = Path(args.body_file).read_text() if args.body_file else args.body
            # Multi-region AUTO-SPLIT (build a1a48c70, the 13/12 miner wall): a body
            # decomposing into N regions lands as N sequential ops — text regions via
            # add-text, def/class regions via add-symbol — instead of a refusal that
            # made the caller do exactly this split by hand.
            try:
                from cjm_python_decompose_core.parse import parse_regions
                regions = parse_regions(body)
            except SyntaxError:
                regions = []
            if len(regions) > 1:
                print(f"body decomposed into {len(regions)} regions — auto-splitting "
                      "into one op per region:")
                for reg in regions:
                    if reg.kind == "symbol":
                        r = await add_symbol(gx, args.module, reg.text, actor=args.actor,
                                             write=not args.no_write,
                                             source_journal_path=args.source_journal_path,
                                             repos_dir=args.repos_dir)
                        print(render("add-symbol", r, args.format))
                    else:
                        r = await add_text(gx, args.module, reg.text, actor=args.actor,
                                           write=not args.no_write,
                                           source_journal_path=args.source_journal_path,
                                           repos_dir=args.repos_dir)
                        print(render("add-text", r, args.format))
                    if r.get("error"):
                        return 1
                _cli_import_smoke(r)
                return 0
            res = await add_text(gx, args.module, body, actor=args.actor,
                                 write=not args.no_write,
                                 source_journal_path=args.source_journal_path,
                                 repos_dir=args.repos_dir)
            print(render("add-text", res, args.format))
            _cli_import_smoke(res)
            return 1 if res.get("error") else 0
        elif args.command == "reconcile-memory":
            res = await reconcile_memory(gx, note_slug=args.note, absorb_anchors=args.absorb,
                                         absorb_all=args.absorb_all, journal_path=args.journal_path,
                                         backup_dir=args.backup_dir)
            print(render("reconcile-memory", res, args.format))
            return 1 if res.get("error") else 0
        elif args.command == "add-section":
            raw = Path(args.content_file).read_text() if args.content_file else args.content
            slug = args.slug
            # Accept a Note id / unique prefix too (build a1a48c70, the 6/6 wall):
            # id-shaped input resolves to the note's slug; a non-Note fails loud.
            import re as _re2
            if _re2.fullmatch(r"[0-9a-f][0-9a-f-]{5,39}", slug or ""):
                from .projection import ambiguity_error, resolve_node_ref
                r = await resolve_node_ref(gx, slug)
                if "candidates" in r:
                    print(ambiguity_error(slug, r["candidates"]), file=sys.stderr)
                    return 1
                rn = r.get("node")
                if rn is not None:
                    if (rn.get("label") if isinstance(rn, dict) else "") != "Note":
                        print(f"error: `{slug}` is not a Note — add-section targets a "
                              "note slug or Note id", file=sys.stderr)
                        return 1
                    slug = (rn.get("properties") or {}).get("slug") or slug
            res = await add_section(gx, slug, raw, after=args.after, write=not args.no_write)
            print(render("structure", res, args.format))
            # M3 structural journaling: a live add-section is journal-sourced too — record the
            # add-section op (slug/raw/after) so a rebuild re-splices the section on-graph (the
            # `.md` is a generated backup, skipped by ingest for journal-sourced notes). Only on a
            # REAL add (`added` non-empty): the anchor-exists no-op and every dry-run change
            # nothing. append_write dedups an identical op on re-run.
            if (args.journal_path and res.get("added") and not res.get("error")
                    and not args.no_write):
                append_write(args.journal_path, "add-section",
                             {"slug": slug, "raw": res.get("section_raw"),
                              "after": res.get("after"), "actor": args.actor})
            return 1 if res.get("error") else 0
        elif args.command == "new-note":
            content = Path(args.content_file).read_text() if args.content_file else args.content
            # Born POST lane (a42c0f97): --slug names the permalink; the file lands as
            # <emit_root>/<slug>/index.md (emit_root from the flag or the notes db's sibling
            # config), identity = the slug relative to the emit root, harvest = the profile
            # (flag, else the config's notes_profile). --path alone = the memory lane as before.
            slug = corpus_root = None
            path = args.path
            if args.slug:
                if not args.emit_root:
                    print("error: new-note --slug needs an emit root (--emit-root, or `emit_root` "
                          "in the graph-sibling graph.config.json)", file=sys.stderr)
                    return 1
                slug = args.slug.strip("/")
                corpus_root = str(Path(args.emit_root).resolve())
                path = str(Path(corpus_root) / slug / "index.md")
                if args.path and str(Path(args.path).resolve()) != path:
                    print(f"error: --path {args.path} conflicts with --slug {slug} "
                          f"(a born post lands at {path})", file=sys.stderr)
                    return 1
                if not args.no_write:
                    Path(path).parent.mkdir(parents=True, exist_ok=True)
            elif not path:
                print("error: new-note needs --path (memory note) or --slug (born post)",
                      file=sys.stderr)
                return 1
            res = await new_note(gx, path, content, write=not args.no_write,
                                 profile=args.profile, corpus_root=corpus_root, slug=slug)
            print(render("structure", res, args.format))
            # Born on-graph from BIRTH: journal a `new-note` genesis op (actor agent:session,
            # NOT the m3-baseline provenance) capturing the exact written bytes — so the note is
            # journal-sourced immediately (its `.md` is skipped on the next ingest, reconstructed
            # by replay) with no post-hoc m3-baseline needed. Journal the on-disk bytes for
            # byte-faithful round-trip; dedups on re-run via append_write. A born post's
            # profile + slug ride the op so replay reproduces the same identity + harvest.
            if args.journal_path and res.get("written") and not res.get("error"):
                abspath = str(Path(path).resolve())
                op = {"path": abspath, "content": Path(path).read_text(), "actor": "agent:session"}
                if args.profile:
                    op["profile"] = args.profile
                if slug:
                    op["slug"] = slug
                append_write(args.journal_path, "new-note", op)
            # DRAFT AT BIRTH (user ruling 793f025e): a born POST is a public-facing
            # deliverable, so it carries publish_state=draft from the SAME invocation that
            # minted it — never a public-facing node without a publish state. Promotion is a
            # human assertion (draft < reviewed < published, ordered so it auto-supersedes)
            # and the website emit (`emit-post`) gates on `published`. The memory lane
            # (--path) is private planning and carries no publish state.
            if slug and res.get("written") and not res.get("error"):
                st = await assert_value(gx, res["note_id"], "publish_state", "draft",
                                        actor=_DEFAULT_ACTOR)
                print(render("assert", st, args.format))
                if args.journal_path and not st.get("error"):
                    append_write(args.journal_path, "assert",
                                 {"subject": res["note_id"], "predicate": "publish_state",
                                  "value": "draft", "actor": _DEFAULT_ACTOR,
                                  "evidence": None, "supersede": False})
            return 1 if res.get("error") else 0
        elif args.command == "emit-post":
            # The outward leg of draft-at-birth (793f025e; item 6eba8815): refuses anything
            # but a single active publish_state=published, then writes the lossless graph
            # reconstruction to <website_root>/posts/<slug>/index.md. Not journaled — the
            # publish_state fact is the truth; this projection is deterministic from it.
            if not args.website_root:
                print("error: emit-post needs --website-root (or `website_root` in the "
                      "graph-sibling graph.config.json)", file=sys.stderr)
                return 1
            res = await emit_post(gx, args.note_id, args.website_root, write=not args.no_write)
            print(render("structure", res, args.format))
            return 1 if res.get("error") else 0
        elif args.command == "move":
            res = await move(gx, args.symbol_id, args.target_module_id, write=not args.no_write,
                             source_journal_path=args.source_journal_path)
            print(render("move", res, args.format))
            return 1 if res.get("error") else 0
        elif args.command == "new-module":
            res = await new_module(gx, args.repo_key, args.module_path,
                                   import_name=args.import_name, repo_root=args.repo_root,
                                   write=not args.no_write,
                                   source_journal_path=args.source_journal_path)
            print(render("module", res, args.format))
            return 1 if res.get("error") else 0
        elif args.command == "regroup":
            res = await regroup(gx, args.repo_key, args.target_module_path, args.symbol_ids,
                                import_name=args.import_name, write=not args.no_write,
                                source_journal_path=args.source_journal_path)
            print(render("move", res, args.format))
            return 1 if res.get("error") else 0
        elif args.command == "rename-module":
            res = await rename_module(gx, args.module_id, args.new_module_path,
                                      new_import_name=args.import_name, write=not args.no_write,
                                      source_journal_path=args.source_journal_path)
            print(render("module", res, args.format))
            return 1 if res.get("error") else 0
        elif args.command == "delete-module":
            res = await delete_module(gx, args.module_id, force=args.force,
                                      write=not args.no_write,
                                      source_journal_path=args.source_journal_path)
            print(render("module", res, args.format))
            return 1 if res.get("error") else 0
        elif args.command == "rename-symbol":
            # b73e7688 ID_REFS conformance (finding 889b3025 gap 2): the positional takes
            # a full id OR unique prefix; ambiguity and no-match fail loud. Extra
            # SYMBOL_ID NEW_NAME pairs route through the batch engine — one snapshot,
            # one emit, so rename k cannot revert rename j.
            from .projection import ambiguity_error, resolve_node_ref
            extra = list(getattr(args, "more", None) or [])
            if len(extra) % 2:
                print("error: rename-symbol takes SYMBOL_ID NEW_NAME pairs — odd "
                      "trailing argument", file=sys.stderr)
                return 1
            pairs = []
            for ref, nn in [(args.symbol_id, args.new_name)] + list(zip(extra[::2], extra[1::2])):
                r = await resolve_node_ref(gx, ref)
                if "candidates" in r:
                    print(ambiguity_error(ref, r["candidates"]), file=sys.stderr)
                    return 1
                node = r.get("node")
                if node is None:
                    print(f"error: no node `{ref}` — `locate` the symbol first",
                          file=sys.stderr)
                    return 1
                pairs.append((node.get("id") if isinstance(node, dict)
                              else getattr(node, "id", ref), nn))
            if len(pairs) == 1:
                res = await rename_symbol(gx, pairs[0][0], pairs[0][1],
                                          write=not args.no_write,
                                          source_journal_path=args.source_journal_path)
            else:
                res = await rename_symbols(gx, pairs, write=not args.no_write,
                                           source_journal_path=args.source_journal_path)
            print(render("rename", res, args.format))
            return 1 if res.get("error") else 0
        elif args.command == "flip-module":
            if not args.source_journal_path:
                print("error: flip-module needs --source-journal-path", file=sys.stderr)
                return 1
            repo_key, module_path = args.repo_key, args.module_path
            if module_path is None:
                # Single-arg form (8bc9abf4): the positional is a CodeModule id/prefix.
                spec, merr = await _resolve_module_ref(gx, args.repo_key)
                if merr:
                    print(f"error: {merr}", file=sys.stderr)
                    return 1
                repo_key, module_path = spec["repo_key"], spec["module_path"]
            res = flip_module(args.source_journal_path, args.repos_dir, repo_key,
                              module_path, import_name=args.import_name,
                              write=not args.no_write)
            print(render("flip", res, args.format))
            return 1 if res.get("error") else 0
        elif args.command == "flip-to-py":
            if not (args.source_journal_path and args.journal_path):
                print("error: flip-to-py needs --source-journal-path AND --journal-path "
                      "(it re-keys the source stream and re-targets write-journal links)",
                      file=sys.stderr)
                return 1
            doc = Path(args.docstring_file).read_text().strip() if args.docstring_file \
                else args.docstring
            res = await flip_notebook_to_py(
                gx, args.source_journal_path, args.journal_path, args.repos_dir,
                args.repo_key, args.notebook_path, docstring=doc,
                force_drop_cell_refs=args.force_drop_cell_refs, write=not args.no_write)
            print(render("flip-to-py", res, args.format))
            return 1 if res.get("error") else 0
        elif args.command == "source-check":
            if not args.source_journal_path:
                print("error: source-check needs --source-journal-path", file=sys.stderr)
                return 1
            res = source_check(args.source_journal_path, args.repos_dir)
            print(render("source-check", res, args.format))
            # Shadow drift is informational (the soak); a GRAPH-SOURCED module failing
            # the regen gate is an error (the artifact diverged from its source).
            return 0 if res.get("regen_clean", True) else 1
        elif args.command == "cutover":
            if not args.source_journal_path:
                print("error: cutover needs --source-journal-path", file=sys.stderr)
                return 1
            repo_key, module_path = args.repo_key, args.module_path
            if module_path is None:
                # Single-arg form (8bc9abf4): the positional is a CodeModule id/prefix.
                spec, merr = await _resolve_module_ref(gx, args.repo_key)
                if merr:
                    print(f"error: {merr}", file=sys.stderr)
                    return 1
                repo_key, module_path = spec["repo_key"], spec["module_path"]
            res = cutover_module(args.source_journal_path, args.repos_dir,
                                 repo_key, module_path,
                                 write=not args.no_write)
            print(render("cutover", res, args.format))
            return 1 if res.get("error") else 0
        elif args.command == "emit-artifact":
            if not args.source_journal_path:
                print("error: emit-artifact needs --source-journal-path", file=sys.stderr)
                return 1
            repo_key, module_path = args.repo_key, args.module_path
            if module_path is None:
                # Single-arg form (8bc9abf4): the positional is a CodeModule id/prefix.
                spec, merr = await _resolve_module_ref(gx, args.repo_key)
                if merr:
                    print(f"error: {merr}", file=sys.stderr)
                    return 1
                repo_key, module_path = spec["repo_key"], spec["module_path"]
            res = emit_source_artifact(args.source_journal_path, args.repos_dir,
                                       repo_key, module_path,
                                       write=not args.no_write)
            print(render("emit-artifact", res, args.format))
            return 1 if res.get("error") else 0
        elif args.command == "readme":
            res = await project_readme(gx, args.repo_key)
            if res.get("error"):
                print(render("readme", res, args.format))
                return 1
            path = Path(args.repos_dir) / args.repo_key / "README.md"
            if args.check:
                cur = path.read_text() if path.exists() else None
                res["drift"] = cur != res["markdown"]
                res["present"], res["readme_path"] = cur is not None, str(path)
                print(render("readme", res, args.format))
                return 1 if res["drift"] else 0
            if args.write:
                path.write_text(res["markdown"])
                res["written"], res["readme_path"] = True, str(path)
                print(render("readme", res, args.format))
                return 0
            # Default: print the markdown verbatim (the viewer — `readme R > README.md` is faithful).
            if args.format == "human":
                sys.stdout.write(res["markdown"])
            else:
                print(render("readme", res, args.format))
            return 0
        elif args.command == "emit":
            res = await emit_artifact(gx, args.module_id, write=args.write,
                                      source_journal_path=args.source_journal_path,
                                      repos_dir=args.repos_dir)
            out = render("emit", res, args.format)
            # The stdout viewer prints the artifact text verbatim (it already ends with a
            # newline) so `emit > file` is byte-faithful; status/JSON lines get a newline.
            if args.format == "human" and not res.get("written") and not res.get("error"):
                sys.stdout.write(out)
            else:
                print(out)
            return 1 if res.get("error") else 0
        elif args.command == "onboarding":
            res = await project_onboarding(gx, config_path=args.config, anchor=args.anchor)
            # --out is the canonical surface; mirror_paths (config) are kept in sync
            # too (the M3 cutover: the auto-loaded MEMORY.md is a generated mirror).
            targets = [Path(args.out)] + [Path(p) for p in res.get("mirror_paths", [])]
            if args.check:
                drift = any((t.read_text() if t.exists() else None) != res["markdown"]
                            for t in targets)
                present = all(t.exists() for t in targets)
                print(f"onboarding: drift={drift} present={present} "
                      f"anchor={res['anchor']!r} missing_refs={res['missing_refs']} "
                      f"-> {', '.join(str(t) for t in targets)}")
                return 1 if drift else 0
            if args.write:
                for t in targets:
                    t.write_text(res["markdown"])
                print(f"onboarding: wrote {len(res['markdown'].encode())} bytes "
                      f"anchor={res['anchor']!r} missing_refs={res['missing_refs']} "
                      f"-> {', '.join(str(t) for t in targets)}")
                return 0
            # Default: print the surface verbatim (the viewer — `onboarding > file` is faithful).
            sys.stdout.write(res["markdown"])
            return 0
        elif args.command == "viz":
            res = await project_viz(gx, args.scope)
            if args.write:
                Path(args.out).write_text(res["html"])
                print(f"viz: wrote {len(res['html'].encode())} bytes "
                      f"({res['node_count']} nodes / {res['edge_count']} edges; "
                      f"{res['counts']['ready']} ready · {res['counts']['blocked']} blocked · "
                      f"{res['counts']['done']} done) -> {args.out}", file=sys.stderr)
                return 0
            # Default: print the HTML verbatim (the viewer — `viz > graph.html` is byte-faithful).
            sys.stdout.write(res["html"])
            return 0
        return 0


def _apply_graph_config(args) -> None:
    """Overlay the graph-sibling config onto parsed args (config = DATA,
    a1d965b0): an EXPLICIT flag always wins; a value still at its baked
    scaffolding default is replaced by the config's answer. code_libs /
    notebook_libs feed ingest's repo inventory; the DEFAULT_* constants
    remain only the absent-config fallback."""
    if not getattr(args, "graph_db_path", None):
        return
    cfg = load_graph_config(args.graph_db_path)
    if not cfg:
        return
    # notes_corpus / notes_profile (81a02642): the notes graph's corpus root + harvest
    # profile are DATA in ITS sibling config, so `ingest-notes` needs no repeated
    # --notes-corpus on every rebuild (the wrapper stays a thin lane).
    for key, attr, baked in (("code_libs", "code_lib", None),
                             ("notebook_libs", "notebook_lib", None),
                             ("memory_dir", "memory_dir", DEFAULT_MEMORY),
                             ("repos_dir", "repos_dir", DEFAULT_REPOS),
                             ("manifests_dir", "manifests_dir", DEFAULT_MANIFESTS),
                             ("notes_corpus", "notes_corpus", None),
                             ("notes_profile", "profile", None),
                             ("emit_root", "emit_root", None),
                             ("website_root", "website_root", None)):
        if key in cfg and hasattr(args, attr):
            current = getattr(args, attr)
            if (not current) if baked is None else (current == baked):
                setattr(args, attr, cfg[key])


def main() -> int:
    ap = argparse.ArgumentParser(prog="cjm-context-graph",
                                 description="Projection/navigation + write surface over a context graph.")
    ap.add_argument("--graph-db-path", required=True, help="Explicit sqlite db path (no default)")
    ap.add_argument("--journal-path", default=None,
                    help="Explicit write-journal path (JSONL). Given: write verbs append to it + "
                         "`ingest` replays it (the db becomes a rebuildable projection). No default.")
    ap.add_argument("--manifests-dir", default=DEFAULT_MANIFESTS,
                    help="Dir with the graph-storage capability manifest")
    ap.add_argument("--source-journal-path", default=None,
                    help="Explicit SOURCE-journal path (JSONL) for the N+3 persistence flip "
                         "(shadow): a SEPARATE stream from --journal-path (public code source "
                         "state vs private planning). Used by flip-module / source-check. No default.")
    ap.add_argument("--format", choices=("human", "agent"), default="human")
    ap.add_argument("--reads-path", default=None,
                    help="Content-access READS ledger (JSONL): given, every rendered read "
                         "appends {verb, delivered ids, n, request, session} — a SEPARATE "
                         "prunable telemetry stream, never the write journal (DEC 45df767d). "
                         "No default (cg-read bakes it).")
    sub = ap.add_subparsers(dest="command", required=True)

    p_ing = sub.add_parser("ingest", help="Build/refresh the dev graph (idempotent)")
    p_ing.add_argument("--memory-dir", default=DEFAULT_MEMORY)
    p_ing.add_argument("--repos-dir", default=DEFAULT_REPOS)
    p_ing.add_argument("--no-repo-map", action="store_true", help="Skip the repo map")
    p_ing.add_argument("--no-seed", action="store_true", help="Skip the hand-seeded slots")
    p_ing.add_argument("--code-lib", action="append", default=None,
                       help="Repo dir name (under --repos-dir) to decompose as code; repeatable. "
                            "Omit for the arc libs; --no-code to skip code entirely.")
    p_ing.add_argument("--no-code", action="store_true", help="Skip code decomposition")
    p_ing.add_argument("--notebook-lib", action="append", default=None,
                       help="Repo dir name (under --repos-dir) whose nbdev NOTEBOOKS to decompose "
                            "(the source for nbdev libs); repeatable. Omit for the default nbdev libs; "
                            "use this, not --code-lib, for nbdev libs (the notebook source, not the .py).")
    p_ing.add_argument("--no-notebooks", action="store_true", help="Skip notebook decomposition")

    p_inn = sub.add_parser("ingest-notes",
                           help="Ingest an arbitrary markdown notes corpus into the "
                                "(separate) --graph-db-path — the federation seam: a "
                                "second self-contained persistent graph, kept distinct "
                                "from the private dev/planning graph (a public corpus).")
    p_inn.add_argument("--notes-corpus", default=None,
                       help="Root dir of the markdown corpus (every <dir>/index.md becomes a Note). "
                            "Falls back to the `notes_corpus` key of the graph-sibling "
                            "graph.config.json (81a02642: the corpus root is DATA beside the notes db).")
    p_inn.add_argument("--profile", default=None,
                       help="Relationship-harvest profile (see the markdown core's PROFILES); default = "
                            "the sibling config's `notes_profile`, else quarto_post.")

    p_rp = sub.add_parser("replay", help="Replay the write journal onto the db (needs --journal-path)")
    p_rp.add_argument("--offset", type=int, default=0,
                      help="Skip the first N ops — the swap-rebuild delta lane (over-inclusion is safe; replay is idempotent)")

    p_m3 = sub.add_parser("m3-baseline",
                          help="M3 genesis import: journal a per-note baseline `new-note` op "
                               "(actor import:m3-baseline) so ingest stops reading its .md "
                               "(the authority flip; needs --journal-path)")
    p_m3.add_argument("--memory-dir", default=DEFAULT_MEMORY)
    p_m3.add_argument("--slug", action="append", default=None,
                      help="Note slug to import (repeatable); the thin-slice selector")
    p_m3.add_argument("--all", action="store_true",
                      help="Import the WHOLE corpus (slice->corpus widening; mechanical)")

    sub.add_parser("schema", help="Node labels, edge types, counts")

    p_state = sub.add_parser("state", help="Graph overview, or a subject's effective view")
    p_state.add_argument("subject", nargs="?", default=None, help="Node id or subject term")

    p_rel = sub.add_parser("relevant",
                           help="Level-0 pull: the result set's SHAPE (total + facets + descend handles) + a top-k teaser")
    p_rel.add_argument("task", help="Task / query text")
    p_rel.add_argument("--depth", type=int, default=2)
    p_rel.add_argument("--k", type=int, default=12)

    p_exp = sub.add_parser("explore",
                           help="Descend a facet of a `relevant` query in full (bounded; re-facets if large)")
    p_exp.add_argument("task", help="The original query text (must match the `relevant` call)")
    p_exp.add_argument("--facet", action="append", metavar="AXIS=VALUE",
                       help="Filter by kind=<label> or seed=<seed-id> (repeatable; compose = AND)")
    p_exp.add_argument("--depth", type=int, default=2)
    p_exp.add_argument("--budget", type=int, default=15, help="Max members before re-faceting")

    p_show = sub.add_parser("show", help="One node in full + its neighbours")
    p_show.add_argument("node_id")
    p_show.add_argument("--depth", type=int, default=1)

    p_loc = sub.add_parser("locate",
                           help="Resolve a name / file / slug / id to node(s) + on-disk path")
    p_loc.add_argument("term", help="A node id, or a name/title/slug/key/module-path/file-path substring")
    p_loc.add_argument("--limit", type=int, default=25)

    p_gr = sub.add_parser("grep",
                          help="Exact-substring CONTENT search over node text fields "
                               "(the literal complement of locate/relevant)")
    p_gr.add_argument("term", help="The exact substring / phrase (case-insensitive; a term "
                                   "starting with '-' goes after a bare `--`)")
    p_gr.add_argument("--limit", type=int, default=25)
    p_gr.add_argument("--label", action="append", default=None,
                      help="Keep only nodes of this label (repeatable; e.g. Decision — keeps "
                           "Message hits from swamping a phrase)")
    p_gr.add_argument("--session", default=None,
                      help="Keep only nodes this session's journal window touched")
    p_gr.add_argument("--context", type=int, default=60,
                      help="Snippet context chars either side of the hit (default 60)")

    p_read = sub.add_parser("read",
                            help="Deliver a node's verbatim CONTENT (Note body / Section / "
                                 "CodeSymbol body / CodeText / Cell / module) — the read dual of "
                                 "author/emit; N ids = one delimited block each; --session = a "
                                 "spine's Message bodies in chain order")
    p_read.add_argument("node_id", nargs="*",
                        help="Node id(s) / unique prefixes — several = one block per node (1d8d4486)")
    p_read.add_argument("--session", default=None,
                        help="Deliver this session key's Message bodies in chain order instead")
    p_read.add_argument("--role", default=None, choices=("user", "assistant", "harness"),
                        help="--session: keep only this role")
    p_read.add_argument("--superseded", action="store_true",
                        help="--session: include off-active-path (rewound) branches")
    p_read.add_argument("--parts", action="store_true",
                        help="--session: include composer parts (drafts) beside sent messages")

    p_conv = sub.add_parser("conventions",
                            help="Audit notebook code conventions (undocumented / no-docstring / non-granular)")
    p_conv.add_argument("scope", nargs="?", default=None, help="Restrict to a notebook module id")

    p_ref = sub.add_parser("refactor-candidates",
                           help="Identify relocation / dead-code / consolidation / split candidates")
    p_ref.add_argument("scope", nargs="?", default=None, help="Restrict to a repo key")

    p_coh = sub.add_parser("cohesion",
                           help="Module cohesion audit (grab-bag under-split / scattered-helper over-split)")
    p_coh.add_argument("scope", nargs="?", default=None, help="Restrict to a repo key")

    p_con = sub.add_parser("contradictions", help="Slots whose active assertions disagree")
    p_con.add_argument("scope", nargs="?", default=None, help="Restrict to a subject/predicate term")

    p_rd = sub.add_parser("readiness",
                          help="Derived ready/blocked/done work-item frontier (task_state + GATED_BY)")
    p_rd.add_argument("scope", nargs="?", default=None, help="Restrict to work-items whose label matches")
    p_rd.add_argument("--state", choices=("ready", "blocked", "done", "all"), default=None,
                      help="One bucket, paged (`all` = the full legacy dump); default = bounded "
                           "summary (blocked + ready top-K by last touch, Done as a count)")
    p_rd.add_argument("--contains", default=None,
                      help="Substring filter on item labels (alias of the scope positional)")
    p_rd.add_argument("--limit", type=int, default=15, help="Page size (default 15)")
    p_rd.add_argument("--offset", type=int, default=0, help="Page start within the selected bucket")
    p_rd.add_argument("--captures", action="store_true",
                      help="Enumerate CAPTURES (Decisions carrying an authored capture_state: "
                           "seed / deferred / riding:<item>) instead of the work-item frontier "
                           "(a3d196c6 shape (a); --where capture_state=seed narrows)")
    p_rd.add_argument("--anchor", default=None,
                      help="Restrict to items filed PART_OF this program anchor (id prefix or "
                           "label substring)")
    p_rd.add_argument("--where", action="append", metavar="PRED=VALUE",
                      help="Active-fact filter on items (repeatable, ANDed — e.g. "
                           "priority=awaiting-user)")

    p_rg = sub.add_parser("register-drift",
                          help="Reconcile each <value>-register hub's REFERENCES cache against "
                               "the active role assertions (propose/confirm, never auto-fix)")

    p_pr = sub.add_parser("prose-refs",
                          help="Audit id-shaped tokens in asserted prose vs the edge layer: "
                               "prose-only refs (propose link REFERENCES), unresolvable tokens, "
                               "degree-zero asserted nodes (propose/confirm, never auto-fix)")
    p_pr.add_argument("--limit", type=int, default=30,
                      help="Cap each reported bucket (counts stay true totals)")

    p_oe = sub.add_parser("orphaned-edges",
                          help="Journaled link ops whose endpoint no longer resolves (the set "
                               "replay silently drops after a code rename) + fuzzy remap "
                               "proposals where a label was journaled")

    p_fi = sub.add_parser("filing",
                          help="Unfiled work items + PART_OF program-anchor proposals scored "
                               "from each item's REFERENCES/SHAPES neighborhood (propose/"
                               "confirm, never auto-fix; confirm = link <item> PART_OF <anchor>)")
    p_fi.add_argument("--top-k", type=int, default=3,
                      help="Proposals kept per unfiled item (default 3)")

    p_jw = sub.add_parser("journal-window",
                          help="The session lens: touched-node set for a time window or session "
                               "key (journal-derived — TOUCHES, not creations; open end = live)")
    p_jw.add_argument("--start", default=None,
                      help="Window start (unix ts, YYYY-MM-DD_HH-MM-SS, or YYYY-MM-DD)")
    p_jw.add_argument("--end", default=None,
                      help="Window end (same forms; omit = OPEN — the in-progress live window)")
    p_jw.add_argument("--session", default=None,
                      help="Filter by session key instead of/alongside time bounds")
    p_jw.add_argument("--verb", action="append", default=None,
                      help="Keep only these op verbs (repeatable; e.g. decide, source)")
    p_jw.add_argument("--exclude-verb", action="append", default=None,
                      help="Drop these op verbs (repeatable; e.g. unlink for a retraction sweep)")
    p_jw.add_argument("--label", action="append", default=None,
                      help="Keep only touched nodes of this label (repeatable)")

    p_pf = sub.add_parser("portfolio",
                          help="Workbench front door: every role-asserted anchor + lock lead "
                               "line + derived vitals (per-anchor frontier counts, pins, "
                               "last touch) + anchor-to-anchor links")
    p_ld = sub.add_parser("lead",
                          help="One anchor's lead as STRUCTURE: lock body + role-typed pins "
                               "+ registers expanded (the navigable pin tree)")
    p_ld.add_argument("anchor", help="Anchor slug, full id, or id prefix (>= 6 hex chars)")
    p_fd = sub.add_parser("feed",
                          help="The two-zoom session feed: op ledger + touched-node cards "
                               "(open end = live; poll by re-passing the printed cursor)")
    p_fd.add_argument("--session", default=None, help="Session key filter")
    p_fd.add_argument("--since", default=None,
                      help="EXCLUSIVE cursor: unix ts, YYYY-MM-DD_HH-MM-SS, or YYYY-MM-DD "
                           "— only ops strictly after it")
    p_fd.add_argument("--limit", type=int, default=200,
                      help="Ledger rows returned, newest kept (default 200)")

    p_sg = sub.add_parser("subgraph",
                          help="BULK read: a node SET (ids/prefixes) -> nodes + interconnecting "
                               "edges in a handful of batched queries (the lens/canvas primitive; "
                               "unresolvable refs stay visible)")
    p_sg.add_argument("refs", nargs="+", help="Node ids or unique id prefixes")
    p_sg.add_argument("--hops", type=int, default=0,
                      help="Expand the set N neighbourhood hops (default 0 = exactly the given set)")
    p_sg.add_argument("--relation", action="append", default=None,
                      help="Expansion relation filter (repeatable; default = every relation)")
    p_sg.add_argument("--cap", type=int, default=500,
                      help="Expansion node budget — the given refs are never dropped (default 500)")

    sub.add_parser("export",
                   help="WHOLE-graph read: every node (cheap-title tier) + every edge — "
                        "the hybrid canvas feed (human view = shape summary; "
                        "--format agent = the full payload)")

    p_le = sub.add_parser("lens",
                          help="APPLY a graph-carried lens: bind params, union its selection "
                               "clauses through the real read verbs, project via the bulk "
                               "subgraph read (READ verb; author lenses with set-lens)")
    p_le.add_argument("slug", help="The lens's durable key (list them: list --label Lens)")
    p_le.add_argument("--param", action="append", metavar="NAME=VALUE",
                      help="Bind a declared param (repeatable; timestamp params take unix "
                           "seconds, YYYY-MM-DD_HH-MM-SS, or YYYY-MM-DD)")

    p_sle = sub.add_parser("set-lens",
                           help="Author/update a graph-carried Lens (journaled upsert-by-slug, "
                                "parse-validated v1 shape: params/selection/expand/view — a bad "
                                "spec never lands)")
    p_sle.add_argument("slug", help="The lens's durable key (consumers bind THIS, never the title)")
    p_sle.add_argument("--spec", default=None,
                       help="JSON: {params:[{name,type,required?,default?}], "
                            "selection:[{verb,args}], expand:{hops,relations?}?, view:{...}?}")
    p_sle.add_argument("--spec-file", default=None, help="Read the spec JSON from a file")
    p_sle.add_argument("--title", default=None, help="Display title (presentation only)")
    p_sle.add_argument("--description", default=None, help="One orientation line for the shelf")
    p_sle.add_argument("--actor", default=_DEFAULT_ACTOR)

    p_ls = sub.add_parser("list",
                          help="Enumerate a class: nodes by --label / assertions by --predicate / edges by --relation")
    g_ls = p_ls.add_mutually_exclusive_group(required=True)
    g_ls.add_argument("--label", help="All nodes carrying this label (e.g. Decision, CodeModule)")
    g_ls.add_argument("--predicate", help="All active assertions of this predicate (e.g. task_state)")
    g_ls.add_argument("--relation", help="All edges of this relation type (e.g. GATED_BY)")
    p_ls.add_argument("--limit", type=int, default=50)
    p_ls.add_argument("--offset", type=int, default=0,
                      help="Label mode: window start (page through a big kind)")
    p_ls.add_argument("--contains", default=None,
                      help="Label mode: case-insensitive title substring filter")
    p_ls.add_argument("--where", action="append", metavar="PROP=VALUE",
                      help="Label mode: property equality filter, server-side (repeatable, "
                           "ANDed; dotted PROP paths descend nested JSON — e.g. "
                           "--where note_type=feedback)")
    p_ls.add_argument("--value", default=None,
                      help="Predicate mode: keep only assertions with this value (the register "
                           "read — e.g. --predicate role --value north-star)")
    p_ls.add_argument("--full", action="store_true",
                      help="Label mode: untruncated title/gloss + each node's body text "
                           "(statement/description) — the batch body read (1d8d4486)")

    p_wl = sub.add_parser("worklist", help="Propose/confirm queue (dangling refs, soft conflicts)")
    p_wl.add_argument("--memory-dir", default=DEFAULT_MEMORY,
                      help="Corpus dir for dangling-reference triage")

    p_as = sub.add_parser("assert", help="Claim a value for a (subject, predicate) slot")
    p_as.add_argument("subject")
    p_as.add_argument("predicate")
    p_as.add_argument("value")
    p_as.add_argument("--actor", default=_DEFAULT_ACTOR)
    p_as.add_argument("--evidence", action="append", help="Supporting node id (repeatable)")
    p_as.add_argument("--supersede", action="append", help="Prior assertion id OR value to supersede (repeatable)")

    p_al = sub.add_parser("alias", help="Confirm a drifted link slug as an alias of a real note")
    p_al.add_argument("drifted", help="The drifted `[[wiki-link]]` slug (resolves to no note)")
    p_al.add_argument("canonical", help="The real note slug it means (frontmatter `name`)")
    p_al.add_argument("--actor", default=_DEFAULT_ACTOR)
    p_al.add_argument("--session", default=None, help="Session key (actor becomes agent:session:<key>)")
    p_al.add_argument("--memory-dir", default=DEFAULT_MEMORY,
                      help="Corpus dir to auto-discover the source notes as evidence")
    p_al.add_argument("--evidence", action="append",
                      help="Override evidence: a source-note id (repeatable)")

    p_de = sub.add_parser("decide", help="Record a decision + its premise edges")
    p_de.add_argument("statement", nargs="?", default=None,
                      help="The decision statement. Prefer --statement-file for anything "
                           "with backticks/quotes — bash mangles a double-quoted statement "
                           "SILENTLY and a Decision statement has no repair path")
    p_de.add_argument("--statement-file", default=None, metavar="PATH",
                      help="Read the statement from a file (build 1b109ddc): the "
                           "shell-proof input — no quoting hazards, no command substitution")
    p_de.add_argument("--actor", default=_DEFAULT_ACTOR)
    p_de.add_argument("--supports", action="append", help="Premise assertion id (repeatable)")
    p_de.add_argument("--supersedes", action="append", help="Prior decision id (repeatable)")
    p_de.add_argument("--session", default=None, help="Session key this was decided in")
    p_de.add_argument("--title", default=None,
                      help="Explicit display title (tier-1 override; else the statement's "
                           "first clause is extracted)")
    p_de.add_argument("--capture", default=None, metavar="seed|deferred|riding:<item>",
                      help="Assert capture_state on the new decision in the same invocation "
                           "(finding a3d196c6, shape (a)): a CAPTURE is not a work item — "
                           "seed / deferred / riding:<item-id-or-prefix> (resolved and "
                           "REFERENCES-linked) — so `readiness --captures` renders it; "
                           "mutually exclusive with --state")
    p_de.add_argument("--state", default=None, type=_decide_state,
                      help="Assert task_state on the new decision in the same invocation — "
                           "a work item/finding is invisible to `readiness` until its "
                           "task_state lands, so mint WORK ITEMs with `--state open`")

    p_ck = sub.add_parser("check",
                          help="Attach a definition-of-done check to a work item (Check node + "
                               "CHECKS edge + task_state=open, journaled). Close it later with "
                               "`assert <check-id> task_state done --evidence <proof>`; readiness "
                               "derives closable/drift from it")
    p_ck.add_argument("item", help="The work item (node id, or a unique id prefix)")
    p_ck.add_argument("text", help="The check statement")
    p_ck.add_argument("--actor", default=_DEFAULT_ACTOR)

    p_un = sub.add_parser("uncaptured",
                          help="Audit: .py files in code_libs repos with NO source-journal "
                               "capture (file-sourced — their edits are unjournaled plain "
                               "writes); capture each with flip-module + cutover (a6453f70)")
    p_un.add_argument("--repo", action="append", default=None,
                      help="Limit to this repo key (repeatable; default: every code_libs repo)")
    p_un.add_argument("--repos-dir", default=DEFAULT_REPOS)

    p_dr = sub.add_parser("display-rule",
                          help="Author/update the graph-carried DisplayRule for a node kind — "
                               "the presentation vocabulary (templates: {prop}, {->REL}, "
                               "{<-REL.prop}, {#<-REL}, |N truncates; one-hop, frozen-small)")
    p_dr.add_argument("for_label", help="The node label (kind) the rule renders (e.g. FactSlot)")
    p_dr.add_argument("--title", default=None,
                      help="Title template: short stable identity (~60 chars)")
    p_dr.add_argument("--gloss", default=None,
                      help="Gloss template: one orientation line (what it says/points to/state)")
    p_dr.add_argument("--actor", default=_DEFAULT_ACTOR)

    p_sn = sub.add_parser("session",
                          help="Register/update a timestamp-keyed Session node (the session spine; "
                               "journaled upsert — end-of-session naming = re-register with --title)")
    p_sn.add_argument("key", help="Stable session key (the start-time timestamp, e.g. 2026-07-08_10-58-13)")
    p_sn.add_argument("--started-at", default=None,
                      help="Unix start ts (default: parsed from a timestamp-form key)")
    p_sn.add_argument("--title", default=None,
                      help="Human-friendly name (typically asserted at session END)")
    p_sn.add_argument("--actor", default=_DEFAULT_ACTOR)

    p_rs = sub.add_parser("retract-session",
                          help="RETRACT a Session spine node (journaled compensating op — the write "
                               "dual of `session`; refuses a non-empty session unless --force)")
    p_rs.add_argument("key", help="The session key to retract (e.g. a key-repeat empty double-mint)")
    p_rs.add_argument("--force", action="store_true",
                      help="Retract even when journaled ops are attributed to the key")
    p_rs.add_argument("--actor", default=_DEFAULT_ACTOR)

    p_pt = sub.add_parser("pull-transcript",
                          help="Pull a harness transcript's user-facing messages onto the "
                               "session spine (Message nodes + spine edges; journals the "
                               "NEW-message payload, so a quiet pull journals nothing)")
    p_pt.add_argument("key", help="The session key to pull for (e.g. 2026-08-20_17-05-20)")
    p_pt.add_argument("--transcript-dir", required=True,
                      help="The harness project transcript dir holding the *.jsonl files "
                           "(e.g. ~/.claude/projects/<munged-project-path>)")
    p_pt.add_argument("--any-boot", action="store_true",
                      help="Also match transcripts whose boot prompt lacks the "
                           "minted-in-workbench signal (resumed/manually booted sessions)")
    p_pt.add_argument("--actor", default=_DEFAULT_ACTOR)

    p_em = sub.add_parser("edit-message",
                          help="In-place edit of a Message node's body (+ optional --set "
                               "property updates, e.g. role/source facet corrections — the "
                               "47b83adb retro-sweep verb); journal-first, last-op-wins "
                               "on replay")
    p_em.add_argument("source_uuid", help="The message's capture-source uuid (identity input)")
    p_em.add_argument("--text", default=None, help="The replacement body")
    p_em.add_argument("--text-file", default=None,
                      help="Read the replacement body from a file")
    p_em.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                      help="Extra property update (repeatable), e.g. --set role=harness")
    p_em.add_argument("--actor", default=_DEFAULT_ACTOR)

    p_es = sub.add_parser("export-session",
                          help="Project a session's scratchpad message graph to portable "
                               "markdown (the exporter lens, item 5ab24c57 — one projection "
                               "among N; read-only, journals nothing; an edited export is a "
                               "fork, never a sync)")
    p_es.add_argument("key", help="The session key to export (e.g. 2026-08-21_11-26-36)")
    p_es.add_argument("--config-file", default=None,
                      help="JSON overriding DEFAULT_CONFIG (config-as-data controls: "
                           "transcript/composition/superseded/lanes/ids/timestamps)")
    p_es.add_argument("--include-superseded", action="store_true",
                      help="Include off-active-path transcript branches (annotated)")
    p_es.add_argument("--lanes", choices=["interleaved", "separate"], default=None,
                      help="Override the lane presentation")
    p_es.add_argument("--out", default=None,
                      help="Write the .md here (default: print to stdout)")

    p_or = sub.add_parser("oracle", help="Run the version oracle (refresh version slots)")
    p_or.add_argument("--repos-dir", default=DEFAULT_REPOS)
    p_or.add_argument("--only", action="append", help="Restrict to a repo key/name (repeatable)")

    p_ln = sub.add_parser("link", help="Mint a deliberate edge between two existing nodes")
    p_ln.add_argument("source_id", help="Source node id (must exist)")
    p_ln.add_argument("relation", help="Edge relation (free string; e.g. IMPLEMENTED_BY)")
    p_ln.add_argument("target_id", help="Target node id (must exist)")
    p_ln.add_argument("--actor", default=_DEFAULT_ACTOR)

    p_ul = sub.add_parser("unlink",
                          help="RETRACT a deliberate edge (journaled compensating op — the write dual of link)")
    p_ul.add_argument("source_id", help="Source node id / unique prefix")
    p_ul.add_argument("relation", help="Edge relation of the edge to retract")
    p_ul.add_argument("target_id", help="Target node id / unique prefix")
    p_ul.add_argument("--force", action="store_true",
                      help="Retract even without a matching journaled link op (structural override)")
    p_ul.add_argument("--actor", default=_DEFAULT_ACTOR)

    p_au = sub.add_parser("author",
                          help="Author a node's verbatim slot (CodeSymbol body / CodeText / Cell / memory Section), emit the .py/.ipynb/.md")
    p_au.add_argument("node_id", help="The CodeSymbol / CodeText / Cell / Section node id to author")
    g_au = p_au.add_mutually_exclusive_group(required=True)
    g_au.add_argument("--replace", help="Full replacement text for the slot (the Write analogue)")
    g_au.add_argument("--replace-file", help="Read the full replacement text from a file")
    g_au.add_argument("--edit", nargs=2, metavar=("OLD", "NEW"),
                      help="Unique-match OLD->NEW splice within the slot (the targeted Edit analogue)")
    g_au.add_argument("--editor", action="store_true",
                      help="Open $EDITOR on the current slot text (the minimal human authoring UI)")
    p_au.add_argument("--no-write", action="store_true",
                      help="Dry run: emit + print the artifact, don't touch disk")
    p_au.add_argument("--actor", default=_DEFAULT_ACTOR)
    p_au.add_argument("--repos-dir", default=DEFAULT_REPOS,
                      help="Repos root — derives a notebook's repo-relative source-journal key")

    p_asym = sub.add_parser("add-symbol",
                            help="Mint a NEW top-level symbol into a .py module (the authoring "
                                 "CREATE leg; lands before any trailing text run so a __main__ "
                                 "dispatch stays last, emits the artifact, absorbs into "
                                 "the source journal when graph-sourced)")
    p_asym.add_argument("module", help="The CodeModule node id to add the symbol to")
    p_asym.add_argument("--after", default=None,
                        help="Placement anchor: insert immediately after this top-level "
                             "qualname or region node id (default: before the trailing text run)")
    g_asym = p_asym.add_mutually_exclusive_group(required=True)
    g_asym.add_argument("--body", help="The symbol's verbatim source (exactly ONE top-level def/class)")
    g_asym.add_argument("--body-file", help="Read the symbol's verbatim source from a file")
    p_asym.add_argument("--no-write", action="store_true",
                        help="Dry run: emit + print the artifact, don't touch graph or disk")
    p_asym.add_argument("--actor", default=_DEFAULT_ACTOR)
    p_asym.add_argument("--repos-dir", default=DEFAULT_REPOS,
                        help="Repos root (parity with author; the absorb gate reads it)")

    p_atxt = sub.add_parser("add-text",
                            help="Mint a NEW CodeText region (imports/constants/docstring) "
                                 "into a .py module (the non-symbol CREATE leg; appends at "
                                 "end, emits the artifact, absorbs into the source journal "
                                 "when graph-sourced; import lines also merge their bindings "
                                 "into the module node — the fresh-module bootstrap)")
    p_atxt.add_argument("module", help="The CodeModule node id to add the region to")
    g_atxt = p_atxt.add_mutually_exclusive_group(required=True)
    g_atxt.add_argument("--body", help="The region's verbatim source (NO top-level def/class)")
    g_atxt.add_argument("--body-file", help="Read the region's verbatim source from a file")
    p_atxt.add_argument("--no-write", action="store_true",
                        help="Dry run: emit + print the artifact, don't touch graph or disk")
    p_atxt.add_argument("--actor", default=_DEFAULT_ACTOR)
    p_atxt.add_argument("--repos-dir", default=DEFAULT_REPOS,
                        help="Repos root (parity with author; the absorb gate reads it)")

    p_asec = sub.add_parser("add-section",
                            help="M2 gradient: add a section to a note (append, or --after ANCHOR), born on-graph")
    p_asec.add_argument("slug", help="The note to add to (by slug)")
    g_asec = p_asec.add_mutually_exclusive_group(required=True)
    g_asec.add_argument("--content", help="The new section's heading-inclusive text (## H\\n\\n...)")
    g_asec.add_argument("--content-file", help="Read the new section's text from a file")
    p_asec.add_argument("--after", default=None, help="Insert after this anchor (default: append at end)")
    p_asec.add_argument("--no-write", action="store_true", help="Dry run: apply to graph, don't write the .md")
    p_asec.add_argument("--actor", default=_DEFAULT_ACTOR)

    p_nn = sub.add_parser("new-note",
                          help="M2 gradient: create a new note, born on-graph — a memory note by "
                               "--path, or a POST by --slug under the notes graph's emit root (a42c0f97)")
    p_nn.add_argument("--path", default=None,
                      help="Where to write the new .md (memory profile, or an explicit location)")
    p_nn.add_argument("--slug", default=None,
                      help="The born post's permalink relative to the emit root: lands as "
                           "<emit_root>/<slug>/index.md, identity = the slug (the notes_corpus_elements convention)")
    p_nn.add_argument("--profile", default=None,
                      help="Relationship-harvest profile for the born note (default: the sibling "
                           "config's notes_profile, else auto-detect from the frontmatter)")
    p_nn.add_argument("--emit-root", default=None,
                      help="Where born posts land (default: the sibling config's emit_root)")

    p_ep = sub.add_parser("emit-post",
                          help="Emit a born post to the PUBLIC website clone — GATED on a single active "
                               "publish_state=published (ruling 793f025e; item 6eba8815)")
    p_ep.add_argument("note_id", help="The born post's Note id (or unique prefix)")
    p_ep.add_argument("--website-root", default=None,
                      help="The website clone root (default: the sibling config's website_root); "
                           "the post lands at <root>/posts/<slug>/index.md")
    p_ep.add_argument("--no-write", action="store_true",
                      help="Report the gate verdict + target path without writing")
    g_nn = p_nn.add_mutually_exclusive_group(required=True)
    g_nn.add_argument("--content", help="The full note text (frontmatter + body)")
    g_nn.add_argument("--content-file", help="Read the full note text from a file")
    p_nn.add_argument("--no-write", action="store_true", help="Dry run: parse + report, don't write/ingest")

    p_rc = sub.add_parser("reconcile-memory",
                          help="M2b soak: report (dry-run) or --absorb out-of-band .md section drift")
    p_rc.add_argument("--note", default=None, help="Restrict to one note (by slug); else the whole corpus")
    p_rc.add_argument("--absorb", nargs="*", metavar="ANCHOR", default=None,
                      help="Absorb these changed anchors into the journal (file-wins); needs --journal-path")
    p_rc.add_argument("--absorb-all", action="store_true",
                      help="Absorb ALL changed sections in scope (needs --journal-path)")
    p_rc.add_argument("--backup-dir", default=None,
                      help="Snapshot affected .md files here before absorbing (default: alongside the file)")

    p_em = sub.add_parser("emit",
                          help="Emit a container's canonical artifact FROM THE GRAPH (graph -> .py/.ipynb/.md)")
    p_em.add_argument("module_id", help="The CodeModule id (a .py module / notebook) or a Note id")
    p_em.add_argument("--write", action="store_true",
                      help="Write to the container's path (else print to stdout — the round-trip viewer)")
    p_em.add_argument("--repos-dir", default=DEFAULT_REPOS,
                      help="Repos root (a notebook emission's journal key derives under it)")

    p_mv = sub.add_parser("move",
                          help="Relocate a top-level symbol to another module (re-emit both + rewrite caller imports)")
    p_mv.add_argument("symbol_id", help="The top-level CodeSymbol id to move")
    p_mv.add_argument("target_module_id", help="The CodeModule id to move it into (same repo)")
    p_mv.add_argument("--no-write", action="store_true", help="Dry run: report the plan, don't touch disk")

    p_nm = sub.add_parser("new-module", help="Mint an empty CodeModule node (a regroup/move target)")
    p_nm.add_argument("repo_key", help="The repo's durable conceptual slug")
    p_nm.add_argument("module_path", help="Repo-relative path of the new module (e.g. pkg/sub.py)")
    p_nm.add_argument("--import-name", help="Dotted import name (derived from module_path if omitted)")
    p_nm.add_argument("--repo-root", default=None,
                      help="Absolute repo root — anchors the FIRST module of a fresh repo "
                           "(otherwise derived from an existing sibling module)")
    p_nm.add_argument("--no-write", action="store_true", help="Dry run: report the plan, don't add the node")

    p_rg = sub.add_parser("regroup",
                          help="Gather symbols into a module (create if absent) — the under/over-split executor")
    p_rg.add_argument("repo_key", help="The repo the symbols + target live in (same-repo)")
    p_rg.add_argument("target_module_path", help="Repo-relative path of the module to gather into")
    p_rg.add_argument("symbol_ids", nargs="+", help="The top-level CodeSymbol ids to relocate")
    p_rg.add_argument("--import-name", help="Target's dotted import name (derived if omitted)")
    p_rg.add_argument("--no-write", action="store_true", help="Dry run: report the plan, don't touch disk")

    p_rn = sub.add_parser("rename-module",
                          help="Rename a .py module (re-emit at the new path + rewrite importer imports)")
    p_rn.add_argument("module_id", help="The CodeModule id to rename")
    p_rn.add_argument("new_module_path", help="Its new repo-relative path")
    p_rn.add_argument("--import-name", help="New dotted import name (derived if omitted)")
    p_rn.add_argument("--no-write", action="store_true", help="Dry run: report the plan, don't touch disk")

    p_dm = sub.add_parser("delete-module", help="Delete a module's file + its graph subtree (guarded)")
    p_dm.add_argument("module_id", help="The CodeModule id to delete")
    p_dm.add_argument("--force", action="store_true", help="Delete even if it still defines symbols (dead module)")
    p_dm.add_argument("--no-write", action="store_true", help="Dry run: report the plan, don't touch disk")

    p_rs = sub.add_parser("rename-symbol",
                          help="Rename a top-level function/class everywhere (def + refs + importer imports)")
    p_rs.add_argument("symbol_id", help="The top-level CodeSymbol id (or unique id prefix) to rename")
    p_rs.add_argument("new_name", help="Its new bare name")
    p_rs.add_argument("more", nargs="*", metavar="SYMBOL_ID NEW_NAME",
                      help="Additional SYMBOL_ID NEW_NAME pairs — the batch applies to ONE "
                           "wire snapshot with one emit, so rename k cannot revert rename j "
                           "(sequential single renames on a module clobber; finding 889b3025)")
    p_rs.add_argument("--no-write", action="store_true", help="Dry run: report the plan, don't touch disk")

    p_fl = sub.add_parser("flip-module",
                          help="N+3 Phase 1 (SHADOW): capture a module's canonical source into the source journal")
    p_fl.add_argument("repo_key", help="The repo's durable conceptual slug — or a CodeModule "
                                       "id / unique id prefix (the single-arg form, 8bc9abf4)")
    p_fl.add_argument("module_path", nargs="?", default=None,
                      help="Repo-relative source path (e.g. pkg/sub.py, or "
                           "nbs/core/mod.ipynb for a notebook-sourced module); omit it "
                           "when the first positional is a CodeModule id/prefix")
    p_fl.add_argument("--import-name", help="Dotted import name (derived from module_path if omitted)")
    p_fl.add_argument("--no-write", action="store_true",
                      help="Dry run: report what a flip would capture, journal nothing")
    p_fl.add_argument("--repos-dir", default=DEFAULT_REPOS)

    p_fp = sub.add_parser("flip-to-py",
                          help="Golden-reference flip, ONE LOUD VERB: a graph-sourced notebook's "
                               "export cells -> plain .py source state (arc-lib shape, no __all__); "
                               "journals source+cutover under the .py key, RETIRES the .ipynb key, "
                               "re-targets write-journal Cell links, writes the .py, deletes the notebook")
    p_fp.add_argument("repo_key", help="The repo's durable conceptual slug")
    p_fp.add_argument("notebook_path", help="Repo-relative .ipynb path (the retiring source-journal key)")
    p_fp.add_argument("--docstring", help="Module docstring (the prose-triage fold), verbatim")
    p_fp.add_argument("--docstring-file", help="Read the module docstring from a file instead")
    p_fp.add_argument("--force-drop-cell-refs", action="store_true",
                      help="Proceed past un-retargetable Cell-id write ops (they orphan on rebuild — LOUD)")
    p_fp.add_argument("--no-write", action="store_true",
                      help="Dry run: report the full flip plan, touch nothing")
    p_fp.add_argument("--repos-dir", default=DEFAULT_REPOS)

    p_sc = sub.add_parser("source-check",
                          help="N+3 soak: file-drift (membrane) + round-trip fixpoint for shadow-sourced modules; "
                               "exit 1 if a GRAPH-SOURCED module fails the regen gate")
    p_sc.add_argument("--repos-dir", default=DEFAULT_REPOS)

    p_co = sub.add_parser("cutover",
                          help="N+3 Phase 2: make the journal a module's source of truth "
                               "(guarded — requires a clean shadow); the file becomes a generated committed artifact")
    p_co.add_argument("repo_key", help="The repo's durable conceptual slug — or a CodeModule "
                                       "id / unique id prefix (the single-arg form, 8bc9abf4)")
    p_co.add_argument("module_path", nargs="?", default=None,
                      help="Repo-relative source path (.py or nbs/*.ipynb); omit it "
                           "when the first positional is a CodeModule id/prefix")
    p_co.add_argument("--no-write", action="store_true",
                      help="Dry run: run every cutover guard, flip nothing")
    p_co.add_argument("--repos-dir", default=DEFAULT_REPOS)

    p_ea = sub.add_parser("emit-artifact",
                          help="(Re)generate a module's file from its journaled source (the journal is authoritative)")
    p_ea.add_argument("repo_key", help="The repo's durable conceptual slug — or a CodeModule "
                                       "id / unique id prefix (the single-arg form, 8bc9abf4)")
    p_ea.add_argument("module_path", nargs="?", default=None,
                      help="Repo-relative source path (.py or nbs/*.ipynb); omit it "
                           "when the first positional is a CodeModule id/prefix")
    p_ea.add_argument("--repos-dir", default=DEFAULT_REPOS)
    p_ea.add_argument("--no-write", action="store_true", help="Dry run: report drift, don't touch the file")

    p_rm = sub.add_parser("readme",
                          help="Project a repo's README from the graph (structural v1; read-only)")
    p_rm.add_argument("repo_key", help="The repo to project a README for")
    p_rm.add_argument("--write", action="store_true", help="Write README.md to the repo (a generated artifact)")
    p_rm.add_argument("--check", action="store_true",
                      help="Regen-check: compare the on-disk README.md to the graph projection")
    p_rm.add_argument("--repos-dir", default=DEFAULT_REPOS)

    p_ob = sub.add_parser("onboarding",
                          help="Project the MEMORY onboarding surface from the graph's ASSERTED "
                               "lead structure (locks + pins + priority facts + derived sections; "
                               "read-only)")
    p_ob.add_argument("--out", default=f"{DEFAULT_REPOS}/cjm-substrate/.cjm/onboarding-surface.md",
                      help="Where to write/compare the surface")
    p_ob.add_argument("--config", default=f"{DEFAULT_REPOS}/cjm-substrate/.cjm/onboarding.config.json",
                      help="JSON config (REQUIRED keys: active_anchor + how_to_query; optional "
                           "mirror_paths) — no in-code fallback, fails loud (axis F)")
    p_ob.add_argument("--anchor", default=None,
                      help="Override the config's active_anchor (anchor slug or node id) — "
                           "topic selection precedes session start")
    p_ob.add_argument("--write", action="store_true", help="Write the surface to --out")
    p_ob.add_argument("--check", action="store_true",
                      help="Regen-check: compare --out to the projection (drift)")

    p_sv = sub.add_parser("serve",
                          help="Serve the read-only graph EXPLORER (the richer viz instrument): "
                               "opens --graph-db-path (+ each --also) once and maps the read "
                               "verbs to timed JSON endpoints + a browser client")
    p_sv.add_argument("--also", action="append", default=None, metavar="DB_PATH",
                      help="Additional graph db to serve alongside --graph-db-path (repeatable; "
                           "the multi-graph corpus one switcher-click apart)")
    p_sv.add_argument("--host", default="127.0.0.1", help="Bind address (default loopback)")
    p_sv.add_argument("--port", type=int, default=8766)

    p_vz = sub.add_parser("viz",
                          help="Project the readiness frontier + dependency DAG to a self-contained "
                               "interactive HTML page (read-only; another graph projection)")
    p_vz.add_argument("--scope", default=None,
                      help="Restrict to work-items whose label matches this term (substring)")
    p_vz.add_argument("--out", default=f"{DEFAULT_REPOS}/cjm-substrate/.cjm/graph-viz.html",
                      help="Where to write the HTML (with --write)")
    p_vz.add_argument("--write", action="store_true", help="Write the HTML to --out")

    args = ap.parse_args()
    _apply_graph_config(args)
    configure_reads(args.reads_path, request=_read_request(args))
    return asyncio.run(_dispatch(args))


# The __main__ dispatch was demoted from here to a TAIL region (2026-08-13): under
# `python -m` regions execute in slot order, so the dispatch must follow every def —
# this module's tail symbols post-date the region and a mid-file region can't be reordered.


def _parse_ts(value: Optional[str]) -> Optional[float]:  # Unix seconds, or None
    """Parse a window bound: unix seconds, the session-key timestamp form
    (YYYY-MM-DD_HH-MM-SS, LOCAL time — the scratchpad convention DEC 6124d8bf),
    or a bare date YYYY-MM-DD (local midnight)."""
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d_%H-%M-%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).timestamp()
        except ValueError:
            continue
    raise SystemExit(f"error: can't parse time '{value}' "
                     "(unix seconds, YYYY-MM-DD_HH-MM-SS, or YYYY-MM-DD)")


def render(kind: str, data, fmt: str) -> str:
    """The read-delivery seam: tap the reads ledger, then delegate to the real
    renderer. Shadows the imported render ON PURPOSE — every verb branch that
    renders routes through here BY CONSTRUCTION (the 6124d8bf coverage doctrine:
    no per-verb call to forget), so a future verb is recorded the day it lands."""
    record_read(kind, data)
    return _render_base(kind, data, fmt)


def _read_request(args) -> Dict[str, str]:
    """The identifying request params for a read event — what was asked for
    (the ledger's `ids` say what was delivered)."""
    keys = ("node_id", "term", "task", "subject", "scope", "session", "state",
            "contains", "refs", "slug", "anchor", "label", "predicate", "relation")
    out = {}
    for k in keys:
        v = getattr(args, k, None)
        if v not in (None, "", []):
            out[k] = v if isinstance(v, str) else json.dumps(v, default=str)
    return out


def _decide_state(value: str) -> str:
    """`decide --state` validator (build a1a48c70): only `open` mints here. A closing
    value names the ACTUAL recipe instead of argparse's bare invalid-choice line —
    the 4/4 miner wall was agents reaching for `decide --state done` when closing an
    item is an assert."""
    if value == "open":
        return value
    raise argparse.ArgumentTypeError(
        f"decide only mints task_state=open (got {value!r}). To close or advance an "
        "item: `assert <item-id> task_state done --evidence <dec-id>` "
        "(or task_state in_progress) — the assert verb, not a decide flag")


def _cli_import_smoke(res: Dict[str, Any]) -> None:
    """Post-write CLI health probe (build a6453f70, the 11c981b7 / ops.py class).

    A write landing in a package THIS CLI imports at startup can brick every
    subsequent cg invocation at import time — tests stay green, the next command
    dies. A fresh-subprocess import right after such a write turns the silent brick
    into an immediate loud warning carrying the recovery recipe. Probes only the
    load-bearing repos, so ordinary writes pay nothing."""
    if not (res or {}).get("written"):
        return
    if str(res.get("repo_key") or "") not in {
            "cjm-context-graph-projection", "cjm-context-graph-layer",
            "cjm-context-graph-primitives", "cjm-dev-graph-schema"}:
        return
    try:
        probe = subprocess.run([sys.executable, "-c",
                                "import cjm_context_graph_projection.cli"],
                               capture_output=True, text=True, timeout=60)
    except Exception as e:  # a failed probe must never mask the landed write
        print(f"⚠ CLI import smoke inconclusive ({e})", file=sys.stderr)
        return
    if probe.returncode != 0:
        tail = (probe.stderr or "").strip().splitlines()
        print("⚠ CLI IMPORT SMOKE FAILED after this write — the NEXT cg invocation "
              f"will brick at import time ({tail[-1] if tail else 'no stderr'}). "
              "Recovery (11c981b7): direct-edit the FILE so the CLI imports again, "
              "then re-land the SAME OLD->NEW through cg-write author — the "
              "convergent repair the stale-wires guard allows; never flip-module "
              "for this class.", file=sys.stderr)


if __name__ == "__main__":  # runtime-order: must trail every def (python -m executes in slot order)
    sys.exit(main())
