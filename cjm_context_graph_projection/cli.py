"""The `cjm-context-graph` CLI — first driver of the projection core.

Read surface: `schema` / `state [subject]` / `relevant <task>` / `show <id>`
(the canonical session-start sequence) + `contradictions` / `worklist`. Write
surface: `assert` / `decide` / `oracle`. Plus `ingest` to build/refresh the dev
graph. `--graph-db-path` is always explicit; `--format agent|human` selects JSON
vs markdown.
"""

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from cjm_context_graph_layer.ops import extend_graph

from .authoring import author, emit_artifact, read_node, read_slot
from .contradictions import contradictions
from .conventions import conventions
from .devgraph import build_dev_graph_elements, notes_corpus_elements
from .factlayer import note_alias_map
from .journal import append_write, replay_journal
from .module_ops import delete_module, new_module, regroup, rename_module
from .oracle import run_version_oracle
from .reconcile import reconcile_memory
from .structure import add_section, new_note
from .projection import explore, get_schema, relevant, show, state
from .onboarding import project_onboarding
from .readme import project_readme
from .rename_ops import rename_symbol
from .source_state import flip_module, source_check
from .cohesion import cohesion
from .refactor import refactor_candidates
from .refactor_ops import move
from .render import render
from .runtime import DEFAULT_MANIFESTS, open_graph
from .worklist import dangling_reference_sources, worklist
from .write import alias, assert_value, decide, link

DEFAULT_MEMORY = ("/home/innom-dt/.claude/projects/"
                  "-mnt-SN850X-8TB-EXT4-Projects-GitHub-cj-mills-cjm-substrate/memory")
DEFAULT_REPOS = "/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills"
# The born-non-nbdev arc libs decomposed as the code source-type by default (the
# code-on-graph corpus); plain `.py`, so the python decomposer applies cleanly.
DEFAULT_CODE_LIBS = ("cjm-dev-graph-schema", "cjm-markdown-decompose-core",
                     "cjm-context-graph-projection", "cjm-python-decompose-core")
# The substrate core is nbdev — ingest its NOTEBOOKS (the source), with cross-cell
# @patch/incremental methods re-attributed to their true classes by the compositor.
DEFAULT_NOTEBOOK_LIBS = ("cjm-substrate",)


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


async def _dispatch(args) -> int:
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
            nodes, edges = build_dev_graph_elements(
                args.memory_dir, None if args.no_repo_map else args.repos_dir,
                seed=not args.no_seed, note_aliases=note_aliases, code_repos=code_repos,
                notebook_repos=notebook_repos)
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
            if args.journal_path:
                append_write(args.journal_path, "assert",
                             {"subject": args.subject, "predicate": args.predicate,
                              "value": args.value, "actor": args.actor,
                              "evidence": args.evidence, "supersede": args.supersede})
            return 2 if res.get("conflict") else 0
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
                               supersedes=args.supersedes, session=args.session)
            print(render("decide", res, args.format))
            if args.journal_path:
                append_write(args.journal_path, "decide",
                             {"statement": args.statement, "actor": args.actor,
                              "supports": args.supports, "supersedes": args.supersedes,
                              "session": args.session})
        elif args.command == "link":
            res = await link(gx, args.source_id, args.target_id, args.relation, actor=args.actor)
            print(render("link", res, args.format))
            if args.journal_path and res.get("written"):
                append_write(args.journal_path, "link",
                             {"source_id": args.source_id, "target_id": args.target_id,
                              "relation": args.relation, "actor": args.actor})
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
            # ingest source for now; the journal shadows + soaks). Code/notebook authoring stays
            # un-journaled (Fork-1(a)). Skip no-op edits; append_write dedups identical states.
            if (res.get("artifact") == "note" and args.journal_path
                    and res.get("written") and not res.get("unchanged")):
                append_write(args.journal_path, "section",
                             {"slug": res.get("note_slug"), "anchor": res.get("anchor"),
                              "raw": res.get("new_text"), "actor": args.actor})
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
            return 1 if res.get("error") else 0
        elif args.command == "new-note":
            content = Path(args.content_file).read_text() if args.content_file else args.content
            res = await new_note(gx, args.path, content, write=not args.no_write)
            print(render("structure", res, args.format))
            return 1 if res.get("error") else 0
        elif args.command == "move":
            res = await move(gx, args.symbol_id, args.target_module_id, write=not args.no_write)
            print(render("move", res, args.format))
            return 1 if res.get("error") else 0
        elif args.command == "new-module":
            res = await new_module(gx, args.repo_key, args.module_path,
                                   import_name=args.import_name, write=not args.no_write)
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
        elif args.command == "source-check":
            if not args.source_journal_path:
                print("error: source-check needs --source-journal-path", file=sys.stderr)
                return 1
            res = source_check(args.source_journal_path, args.repos_dir)
            print(render("source-check", res, args.format))
            return 0
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

    p_or = sub.add_parser("oracle", help="Run the version oracle (refresh version slots)")
    p_or.add_argument("--repos-dir", default=DEFAULT_REPOS)
    p_or.add_argument("--only", action="append", help="Restrict to a repo key/name (repeatable)")

    p_ln = sub.add_parser("link", help="Mint a deliberate edge between two existing nodes")
    p_ln.add_argument("source_id", help="Source node id (must exist)")
    p_ln.add_argument("relation", help="Edge relation (free string; e.g. IMPLEMENTED_BY)")
    p_ln.add_argument("target_id", help="Target node id (must exist)")
    p_ln.add_argument("--actor", default="agent:session")

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

    p_asec = sub.add_parser("add-section",
                            help="M2 gradient: add a section to a note (append, or --after ANCHOR), born on-graph")
    p_asec.add_argument("slug", help="The note to add to (by slug)")
    g_asec = p_asec.add_mutually_exclusive_group(required=True)
    g_asec.add_argument("--content", help="The new section's heading-inclusive text (## H\\n\\n...)")
    g_asec.add_argument("--content-file", help="Read the new section's text from a file")
    p_asec.add_argument("--after", default=None, help="Insert after this anchor (default: append at end)")
    p_asec.add_argument("--no-write", action="store_true", help="Dry run: apply to graph, don't write the .md")

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
    p_fl.add_argument("module_path", help="Repo-relative module path (e.g. pkg/sub.py)")
    p_fl.add_argument("--import-name", help="Dotted import name (derived from module_path if omitted)")
    p_fl.add_argument("--repos-dir", default=DEFAULT_REPOS)

    p_sc = sub.add_parser("source-check",
                          help="N+3 soak: file-drift (membrane) + round-trip fixpoint for shadow-sourced modules")
    p_sc.add_argument("--repos-dir", default=DEFAULT_REPOS)

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

    args = ap.parse_args()
    return asyncio.run(_dispatch(args))


if __name__ == "__main__":
    sys.exit(main())
