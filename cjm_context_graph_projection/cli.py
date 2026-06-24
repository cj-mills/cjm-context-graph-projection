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

from .authoring import author, emit_artifact, read_slot
from .contradictions import contradictions
from .conventions import conventions
from .devgraph import build_dev_graph_elements
from .factlayer import note_alias_map
from .journal import append_write, replay_journal
from .oracle import run_version_oracle
from .projection import get_schema, relevant, show, state
from .refactor import refactor_candidates
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
            notebook_repos = ([str(Path(args.repos_dir) / n) for n in args.notebook_lib]
                              if args.notebook_lib else None)
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
        elif args.command == "show":
            print(render("show", await show(gx, args.node_id, depth=args.depth), args.format))
        elif args.command == "contradictions":
            print(render("contradictions", await contradictions(gx, args.scope), args.format))
        elif args.command == "conventions":
            print(render("conventions", await conventions(gx, args.scope), args.format))
        elif args.command == "refactor-candidates":
            print(render("refactor", await refactor_candidates(gx, args.scope), args.format))
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
            return 1 if res.get("error") else 0
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
                            "(the source for nbdev libs); repeatable. Use this, not --code-lib, for "
                            "nbdev libs (ingest the notebook source, not the generated .py).")

    sub.add_parser("replay", help="Replay the write journal onto the db (needs --journal-path)")

    sub.add_parser("schema", help="Node labels, edge types, counts")

    p_state = sub.add_parser("state", help="Graph overview, or a subject's effective view")
    p_state.add_argument("subject", nargs="?", default=None, help="Node id or subject term")

    p_rel = sub.add_parser("relevant", help="Nodes structurally nearest a task, ranked")
    p_rel.add_argument("task", help="Task / query text")
    p_rel.add_argument("--depth", type=int, default=2)
    p_rel.add_argument("--k", type=int, default=12)

    p_show = sub.add_parser("show", help="One node in full + its neighbours")
    p_show.add_argument("node_id")
    p_show.add_argument("--depth", type=int, default=1)

    p_conv = sub.add_parser("conventions",
                            help="Audit notebook code conventions (undocumented / no-docstring / non-granular)")
    p_conv.add_argument("scope", nargs="?", default=None, help="Restrict to a notebook module id")

    p_ref = sub.add_parser("refactor-candidates",
                           help="Identify relocation / dead-code / consolidation / split candidates")
    p_ref.add_argument("scope", nargs="?", default=None, help="Restrict to a repo key")

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
                          help="Author a node's verbatim slot (CodeSymbol body / CodeText / Cell), emit the .py/.ipynb")
    p_au.add_argument("node_id", help="The CodeSymbol / CodeText / Cell node id to author")
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

    p_em = sub.add_parser("emit",
                          help="Emit a module/notebook's canonical artifact FROM THE GRAPH (graph -> .py/.ipynb)")
    p_em.add_argument("module_id", help="The CodeModule id (a .py module or a notebook)")
    p_em.add_argument("--write", action="store_true",
                      help="Write to the module's path (else print to stdout — the round-trip viewer)")

    args = ap.parse_args()
    return asyncio.run(_dispatch(args))


if __name__ == "__main__":
    sys.exit(main())
