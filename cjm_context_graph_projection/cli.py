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
from typing import Dict, Optional

from cjm_context_graph_layer.ops import extend_graph
from cjm_context_graph_primitives.journal import append_write, read_journal

from .authoring import add_symbol, author, emit_artifact, read_node, read_slot
from .code_edges import orphaned_edges
from .cohesion import cohesion
from .contradictions import contradictions
from .conventions import conventions
from .devgraph import build_dev_graph_elements, notes_corpus_elements
from .display import set_display_rule
from .explorer_page import EXPLORER_HTML
from .factlayer import note_alias_map
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
from .readiness import readiness
from .readme import project_readme
from .reconcile import reconcile_memory
from .refactor import refactor_candidates
from .refactor_ops import move
from .registers import register_drift
from .rename_ops import rename_symbol
from .render import render
from .runtime import DEFAULT_MANIFESTS, open_graph
from .seeds import repo_dir_name
from .serve import serve_graphs
from .source_state import (absorb_authored_text, cutover_module, emit_source_artifact, flip_module,
                           graph_sourced_modules, source_check)
from .structure import add_section, new_note
from .viz import project_viz
from .worklist import dangling_reference_sources, worklist
from .write import add_check, alias, assert_value, decide, link, register_session, unlink

DEFAULT_MEMORY = ("/home/innom-dt/.claude/projects/"
                  "-mnt-SN850X-8TB-EXT4-Projects-GitHub-cj-mills-cjm-substrate/memory")
DEFAULT_REPOS = "/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills"
# The born-non-nbdev arc libs decomposed as the code source-type by default (the
# code-on-graph corpus); plain `.py`, so the python decomposer applies cleanly.
DEFAULT_CODE_LIBS = ("cjm-dev-graph-schema", "cjm-markdown-decompose-core",
                     "cjm-notebook-decompose-core",
                     "cjm-context-graph-projection", "cjm-python-decompose-core",
                     "cjm-substrate-tui-kit",
                     "cjm-transcript-correction-tui", "cjm-transcription-tui")
# The substrate core is nbdev — ingest its NOTEBOOKS (the source), with cross-cell
# @patch/incremental methods re-attributed to their true classes by the compositor.
DEFAULT_NOTEBOOK_LIBS = ("cjm-substrate", "cjm-transcription-core",
                         "cjm-transcript-decomp-core",
                         "cjm-transcript-correction-core",
                         # c25780e8 bulk sweep (DEC 5a7c2af7): the in-scope
                         # still-nbdev ecosystem deps, transitioning.
                         "cjm-capability-demucs", "cjm-capability-ffmpeg",
                         "cjm-capability-graph-sqlite",
                         "cjm-capability-monitor-nvidia",
                         "cjm-capability-primitives",
                         "cjm-capability-qwen3-forced-aligner",
                         "cjm-capability-silero-vad",
                         "cjm-capability-voxtral-hf", "cjm-capability-whisper",
                         "cjm-context-graph-layer",
                         "cjm-context-graph-primitives",
                         "cjm-substrate-hf-utils", "cjm-substrate-torch-utils",
                         "cjm-transcript-graph-schema",
                         "cjm-transcription-adapter-interface")


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
    """N+3 Phase 2 absorb gate, shared by the module-emitting write verbs (author,
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
            nodes, edges = notes_corpus_elements(args.notes_corpus, args.profile)
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
            rc = await replay_journal(gx, args.journal_path)
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
            print(render("show", await show(gx, args.node_id, depth=args.depth), args.format))
        elif args.command == "locate":
            print(render("locate", await locate(gx, args.term, limit=args.limit), args.format))
        elif args.command == "grep":
            print(render("grep", await grep(gx, args.term, limit=args.limit), args.format))
        elif args.command == "read":
            res = await read_node(gx, args.node_id)
            out = render("read", res, args.format)
            # Content delivery: print the verbatim text exactly (a note body already ends
            # with its file's trailing newline) so `read > file` is byte-faithful; status/
            # JSON lines (errors, nested-symbol hints) get the usual newline.
            if (args.format == "human" and not res.get("error")
                    and res.get("kind") != "nested"):
                sys.stdout.write(out)
            else:
                print(out)
            return 1 if res.get("error") else 0
        elif args.command == "contradictions":
            print(render("contradictions", await contradictions(gx, args.scope), args.format))
        elif args.command == "readiness":
            print(render("readiness", await readiness(gx, args.scope), args.format))
        elif args.command == "register-drift":
            print(render("register-drift", await register_drift(gx), args.format))
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
                                            end=_parse_ts(args.end), session=args.session)
            print(render("journal-window", res, args.format))
        elif args.command == "subgraph":
            res = await subgraph_view(gx, args.refs, hops=args.hops,
                                      relations=args.relation, cap=args.cap)
            print(render("subgraph", res, args.format))
        elif args.command == "export":
            res = await full_graph_view(gx)
            print(render("export", res, args.format))
        elif args.command == "list":
            res = await list_graph(gx, label=args.label, predicate=args.predicate,
                                   relation=args.relation, limit=args.limit,
                                   offset=args.offset, contains=args.contains,
                                   where=args.where, value=args.value)
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
            res = await decide(gx, args.statement, actor=args.actor, supports=args.supports,
                               supersedes=args.supersedes, session=args.session,
                               title=args.title)
            print(render("decide", res, args.format))
            if args.journal_path:
                append_write(args.journal_path, "decide",
                             {"statement": args.statement, "actor": args.actor,
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
        elif args.command == "oracle":
            res = await run_version_oracle(gx, repos_dir=args.repos_dir, only=args.only)
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
                               actor=args.actor, write=not args.no_write)
            print(render("author", res, args.format))
            # M2b shadow: a memory-section author also journals its raw STATE (the .md stays the
            # ingest source for now; the journal shadows + soaks). NON-cut-over code/notebook
            # authoring stays un-journaled (Fork-1(a)); GRAPH-SOURCED modules/notebooks land in
            # the SOURCE journal below. Skip no-op edits; append_write dedups identical states.
            if (res.get("artifact") == "note" and args.journal_path
                    and res.get("written") and not res.get("unchanged")):
                append_write(args.journal_path, "section",
                             {"slug": res.get("note_slug"), "anchor": res.get("anchor"),
                              "raw": res.get("new_text"), "actor": args.actor})
            # N+3 Phase 2: an author edit of a GRAPH-SOURCED module lands in the source
            # journal (the authority), canonicalized; the artifact file is kept in sync.
            if _absorb_graph_sourced(res, args) != 0:
                return 1
            return 1 if res.get("error") else 0
        elif args.command == "add-symbol":
            body = Path(args.body_file).read_text() if args.body_file else args.body
            res = await add_symbol(gx, args.module, body, actor=args.actor,
                                   write=not args.no_write)
            print(render("add-symbol", res, args.format))
            # The CREATE leg shares the author verb's absorb gate: a new symbol in a
            # GRAPH-SOURCED module must land in the source journal, not just on disk.
            if _absorb_graph_sourced(res, args) != 0:
                return 1
            return 1 if res.get("error") else 0
        elif args.command == "add-text":
            # Local import: adding a name to this module's import line is the open
            # binding-table gap (47b256de) — stay self-contained until it closes.
            from .authoring import add_text
            body = Path(args.body_file).read_text() if args.body_file else args.body
            res = await add_text(gx, args.module, body, actor=args.actor,
                                 write=not args.no_write)
            print(render("add-text", res, args.format))
            # Same absorb gate: a new region in a GRAPH-SOURCED module must land in
            # the source journal, not just on disk.
            if _absorb_graph_sourced(res, args) != 0:
                return 1
            return 1 if res.get("error") else 0
        elif args.command == "reconcile-memory":
            res = await reconcile_memory(gx, note_slug=args.note, absorb_anchors=args.absorb,
                                         absorb_all=args.absorb_all, journal_path=args.journal_path,
                                         backup_dir=args.backup_dir)
            print(render("reconcile-memory", res, args.format))
            return 1 if res.get("error") else 0
        elif args.command == "add-section":
            raw = Path(args.content_file).read_text() if args.content_file else args.content
            res = await add_section(gx, args.slug, raw, after=args.after, write=not args.no_write)
            print(render("structure", res, args.format))
            # M3 structural journaling: a live add-section is journal-sourced too — record the
            # add-section op (slug/raw/after) so a rebuild re-splices the section on-graph (the
            # `.md` is a generated backup, skipped by ingest for journal-sourced notes). Only on a
            # REAL add (`added` non-empty): the anchor-exists no-op and every dry-run change
            # nothing. append_write dedups an identical op on re-run.
            if (args.journal_path and res.get("added") and not res.get("error")
                    and not args.no_write):
                append_write(args.journal_path, "add-section",
                             {"slug": args.slug, "raw": res.get("section_raw"),
                              "after": res.get("after"), "actor": args.actor})
            return 1 if res.get("error") else 0
        elif args.command == "new-note":
            content = Path(args.content_file).read_text() if args.content_file else args.content
            res = await new_note(gx, args.path, content, write=not args.no_write)
            print(render("structure", res, args.format))
            # Born on-graph from BIRTH: journal a `new-note` genesis op (actor agent:session,
            # NOT the m3-baseline provenance) capturing the exact written bytes — so the note is
            # journal-sourced immediately (its `.md` is skipped on the next ingest, reconstructed
            # by replay) with no post-hoc m3-baseline needed. Journal the on-disk bytes for
            # byte-faithful round-trip; dedups on re-run via append_write.
            if args.journal_path and res.get("written") and not res.get("error"):
                abspath = str(Path(args.path).resolve())
                append_write(args.journal_path, "new-note",
                             {"path": abspath, "content": Path(args.path).read_text(),
                              "actor": "agent:session"})
            return 1 if res.get("error") else 0
        elif args.command == "move":
            res = await move(gx, args.symbol_id, args.target_module_id, write=not args.no_write)
            print(render("move", res, args.format))
            return 1 if res.get("error") else 0
        elif args.command == "new-module":
            res = await new_module(gx, args.repo_key, args.module_path,
                                   import_name=args.import_name, repo_root=args.repo_root,
                                   write=not args.no_write)
            print(render("module", res, args.format))
            return 1 if res.get("error") else 0
        elif args.command == "regroup":
            res = await regroup(gx, args.repo_key, args.target_module_path, args.symbol_ids,
                                import_name=args.import_name, write=not args.no_write)
            print(render("move", res, args.format))
            return 1 if res.get("error") else 0
        elif args.command == "rename-module":
            res = await rename_module(gx, args.module_id, args.new_module_path,
                                      new_import_name=args.import_name, write=not args.no_write)
            print(render("module", res, args.format))
            return 1 if res.get("error") else 0
        elif args.command == "delete-module":
            res = await delete_module(gx, args.module_id, force=args.force, write=not args.no_write)
            print(render("module", res, args.format))
            return 1 if res.get("error") else 0
        elif args.command == "rename-symbol":
            res = await rename_symbol(gx, args.symbol_id, args.new_name, write=not args.no_write)
            print(render("rename", res, args.format))
            return 1 if res.get("error") else 0
        elif args.command == "flip-module":
            if not args.source_journal_path:
                print("error: flip-module needs --source-journal-path", file=sys.stderr)
                return 1
            res = flip_module(args.source_journal_path, args.repos_dir, args.repo_key,
                              args.module_path, import_name=args.import_name)
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
            res = cutover_module(args.source_journal_path, args.repos_dir,
                                 args.repo_key, args.module_path)
            print(render("cutover", res, args.format))
            return 1 if res.get("error") else 0
        elif args.command == "emit-artifact":
            if not args.source_journal_path:
                print("error: emit-artifact needs --source-journal-path", file=sys.stderr)
                return 1
            res = emit_source_artifact(args.source_journal_path, args.repos_dir,
                                       args.repo_key, args.module_path,
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
            res = await emit_artifact(gx, args.module_id, write=args.write)
            out = render("emit", res, args.format)
            # The stdout viewer prints the artifact text verbatim (it already ends with a
            # newline) so `emit > file` is byte-faithful; status/JSON lines get a newline.
            if args.format == "human" and not res.get("written") and not res.get("error"):
                sys.stdout.write(out)
            else:
                print(out)
            return 1 if res.get("error") else 0
        elif args.command == "onboarding":
            res = await project_onboarding(gx, config_path=args.config)
            # --out is the canonical surface; mirror_paths (config) are kept in sync
            # too (the M3 cutover: the auto-loaded MEMORY.md is a generated mirror).
            targets = [Path(args.out)] + [Path(p) for p in res.get("mirror_paths", [])]
            if args.check:
                drift = any((t.read_text() if t.exists() else None) != res["markdown"]
                            for t in targets)
                present = all(t.exists() for t in targets)
                print(f"onboarding: drift={drift} present={present} "
                      f"notes={res['note_count']} missing_push={res['missing_push']} "
                      f"-> {', '.join(str(t) for t in targets)}")
                return 1 if drift else 0
            if args.write:
                for t in targets:
                    t.write_text(res["markdown"])
                print(f"onboarding: wrote {len(res['markdown'].encode())} bytes "
                      f"notes={res['note_count']} missing_push={res['missing_push']} "
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
    p_inn.add_argument("--notes-corpus", required=True,
                       help="Root dir of the markdown corpus (every <dir>/index.md becomes a Note).")
    p_inn.add_argument("--profile", default="quarto_post",
                       help="Relationship-harvest profile (default quarto_post; see the markdown core's PROFILES).")

    sub.add_parser("replay", help="Replay the write journal onto the db (needs --journal-path)")

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
    p_gr.add_argument("term", help="The exact substring / phrase (case-insensitive)")
    p_gr.add_argument("--limit", type=int, default=25)

    p_read = sub.add_parser("read",
                            help="Deliver a node's verbatim CONTENT (Note body / Section / "
                                 "CodeSymbol body / CodeText / Cell / module) — the read dual of author/emit")
    p_read.add_argument("node_id")

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

    p_rg = sub.add_parser("register-drift",
                          help="Reconcile each <value>-register hub's REFERENCES cache against "
                               "the active role assertions (propose/confirm, never auto-fix)")

    p_oe = sub.add_parser("orphaned-edges",
                          help="Journaled link ops whose endpoint no longer resolves (the set "
                               "replay silently drops after a code rename) + fuzzy remap "
                               "proposals where a label was journaled")

    p_jw = sub.add_parser("journal-window",
                          help="The session lens: touched-node set for a time window or session "
                               "key (journal-derived — TOUCHES, not creations; open end = live)")
    p_jw.add_argument("--start", default=None,
                      help="Window start (unix ts, YYYY-MM-DD_HH-MM-SS, or YYYY-MM-DD)")
    p_jw.add_argument("--end", default=None,
                      help="Window end (same forms; omit = OPEN — the in-progress live window)")
    p_jw.add_argument("--session", default=None,
                      help="Filter by session key instead of/alongside time bounds")

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
    p_sle.add_argument("--actor", default="agent:session")

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

    p_wl = sub.add_parser("worklist", help="Propose/confirm queue (dangling refs, soft conflicts)")
    p_wl.add_argument("--memory-dir", default=DEFAULT_MEMORY,
                      help="Corpus dir for dangling-reference triage")

    p_as = sub.add_parser("assert", help="Claim a value for a (subject, predicate) slot")
    p_as.add_argument("subject")
    p_as.add_argument("predicate")
    p_as.add_argument("value")
    p_as.add_argument("--actor", default="agent:session")
    p_as.add_argument("--evidence", action="append", help="Supporting node id (repeatable)")
    p_as.add_argument("--supersede", action="append", help="Prior assertion id OR value to supersede (repeatable)")

    p_al = sub.add_parser("alias", help="Confirm a drifted link slug as an alias of a real note")
    p_al.add_argument("drifted", help="The drifted `[[wiki-link]]` slug (resolves to no note)")
    p_al.add_argument("canonical", help="The real note slug it means (frontmatter `name`)")
    p_al.add_argument("--actor", default="agent:session")
    p_al.add_argument("--session", default=None, help="Session key (actor becomes agent:session:<key>)")
    p_al.add_argument("--memory-dir", default=DEFAULT_MEMORY,
                      help="Corpus dir to auto-discover the source notes as evidence")
    p_al.add_argument("--evidence", action="append",
                      help="Override evidence: a source-note id (repeatable)")

    p_de = sub.add_parser("decide", help="Record a decision + its premise edges")
    p_de.add_argument("statement")
    p_de.add_argument("--actor", default="agent:session")
    p_de.add_argument("--supports", action="append", help="Premise assertion id (repeatable)")
    p_de.add_argument("--supersedes", action="append", help="Prior decision id (repeatable)")
    p_de.add_argument("--session", default=None, help="Session key this was decided in")
    p_de.add_argument("--title", default=None,
                      help="Explicit display title (tier-1 override; else the statement's "
                           "first clause is extracted)")
    p_de.add_argument("--state", default=None, choices=["open"],
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
    p_ck.add_argument("--actor", default="agent:session")

    p_dr = sub.add_parser("display-rule",
                          help="Author/update the graph-carried DisplayRule for a node kind — "
                               "the presentation vocabulary (templates: {prop}, {->REL}, "
                               "{<-REL.prop}, {#<-REL}, |N truncates; one-hop, frozen-small)")
    p_dr.add_argument("for_label", help="The node label (kind) the rule renders (e.g. FactSlot)")
    p_dr.add_argument("--title", default=None,
                      help="Title template: short stable identity (~60 chars)")
    p_dr.add_argument("--gloss", default=None,
                      help="Gloss template: one orientation line (what it says/points to/state)")
    p_dr.add_argument("--actor", default="agent:session")

    p_sn = sub.add_parser("session",
                          help="Register/update a timestamp-keyed Session node (the session spine; "
                               "journaled upsert — end-of-session naming = re-register with --title)")
    p_sn.add_argument("key", help="Stable session key (the start-time timestamp, e.g. 2026-07-08_10-58-13)")
    p_sn.add_argument("--started-at", default=None,
                      help="Unix start ts (default: parsed from a timestamp-form key)")
    p_sn.add_argument("--title", default=None,
                      help="Human-friendly name (typically asserted at session END)")
    p_sn.add_argument("--actor", default="agent:session")

    p_or = sub.add_parser("oracle", help="Run the version oracle (refresh version slots)")
    p_or.add_argument("--repos-dir", default=DEFAULT_REPOS)
    p_or.add_argument("--only", action="append", help="Restrict to a repo key/name (repeatable)")

    p_ln = sub.add_parser("link", help="Mint a deliberate edge between two existing nodes")
    p_ln.add_argument("source_id", help="Source node id (must exist)")
    p_ln.add_argument("relation", help="Edge relation (free string; e.g. IMPLEMENTED_BY)")
    p_ln.add_argument("target_id", help="Target node id (must exist)")
    p_ln.add_argument("--actor", default="agent:session")

    p_ul = sub.add_parser("unlink",
                          help="RETRACT a deliberate edge (journaled compensating op — the write dual of link)")
    p_ul.add_argument("source_id", help="Source node id / unique prefix")
    p_ul.add_argument("relation", help="Edge relation of the edge to retract")
    p_ul.add_argument("target_id", help="Target node id / unique prefix")
    p_ul.add_argument("--force", action="store_true",
                      help="Retract even without a matching journaled link op (structural override)")
    p_ul.add_argument("--actor", default="agent:session")

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
    p_au.add_argument("--actor", default="agent:session")
    p_au.add_argument("--repos-dir", default=DEFAULT_REPOS,
                      help="Repos root — derives a notebook's repo-relative source-journal key")

    p_asym = sub.add_parser("add-symbol",
                            help="Mint a NEW top-level symbol into a .py module (the authoring "
                                 "CREATE leg; appends at end, emits the artifact, absorbs into "
                                 "the source journal when graph-sourced)")
    p_asym.add_argument("module", help="The CodeModule node id to add the symbol to")
    g_asym = p_asym.add_mutually_exclusive_group(required=True)
    g_asym.add_argument("--body", help="The symbol's verbatim source (exactly ONE top-level def/class)")
    g_asym.add_argument("--body-file", help="Read the symbol's verbatim source from a file")
    p_asym.add_argument("--no-write", action="store_true",
                        help="Dry run: emit + print the artifact, don't touch graph or disk")
    p_asym.add_argument("--actor", default="agent:session")
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
    p_atxt.add_argument("--actor", default="agent:session")
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
    p_asec.add_argument("--actor", default="agent:session")

    p_nn = sub.add_parser("new-note", help="M2 gradient: create a new memory note, born on-graph")
    p_nn.add_argument("--path", required=True, help="Where to write the new .md")
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
    p_rs.add_argument("symbol_id", help="The top-level CodeSymbol id to rename")
    p_rs.add_argument("new_name", help="Its new bare name")
    p_rs.add_argument("--no-write", action="store_true", help="Dry run: report the plan, don't touch disk")

    p_fl = sub.add_parser("flip-module",
                          help="N+3 Phase 1 (SHADOW): capture a module's canonical source into the source journal")
    p_fl.add_argument("repo_key", help="The repo's durable conceptual slug")
    p_fl.add_argument("module_path", help="Repo-relative source path (e.g. pkg/sub.py, or "
                                          "nbs/core/mod.ipynb for a notebook-sourced module)")
    p_fl.add_argument("--import-name", help="Dotted import name (derived from module_path if omitted)")
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
    p_co.add_argument("repo_key", help="The repo's durable conceptual slug")
    p_co.add_argument("module_path", help="Repo-relative source path (.py or nbs/*.ipynb)")
    p_co.add_argument("--repos-dir", default=DEFAULT_REPOS)

    p_ea = sub.add_parser("emit-artifact",
                          help="(Re)generate a module's file from its journaled source (the journal is authoritative)")
    p_ea.add_argument("repo_key", help="The repo's durable conceptual slug")
    p_ea.add_argument("module_path", help="Repo-relative source path (.py or nbs/*.ipynb)")
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
                          help="Project the MEMORY onboarding surface from the graph "
                               "(minimal resident core + landmark map + how-to-pull; read-only)")
    p_ob.add_argument("--out", default=f"{DEFAULT_REPOS}/cjm-substrate/.cjm/onboarding-surface.md",
                      help="Where to write/compare the surface")
    p_ob.add_argument("--config", default=f"{DEFAULT_REPOS}/cjm-substrate/.cjm/onboarding.config.json",
                      help="JSON overriding the dev seeds (push_slugs / landmarks / arc_lead); "
                           "absent -> built-in defaults. The promotion loop edits this.")
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
    return asyncio.run(_dispatch(args))


if __name__ == "__main__":
    sys.exit(main())


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
