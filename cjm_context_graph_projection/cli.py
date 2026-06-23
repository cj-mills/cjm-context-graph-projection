"""The `cjm-context-graph` CLI — first driver of the projection core.

Read surface: `schema` / `state [subject]` / `relevant <task>` / `show <id>`
(the canonical session-start sequence) + `contradictions` / `worklist`. Write
surface: `assert` / `decide` / `oracle`. Plus `ingest` to build/refresh the dev
graph. `--graph-db-path` is always explicit; `--format agent|human` selects JSON
vs markdown.
"""

import argparse
import asyncio
import sys

from cjm_context_graph_layer.ops import extend_graph

from .contradictions import contradictions
from .devgraph import build_dev_graph_elements
from .oracle import run_version_oracle
from .projection import get_schema, relevant, show, state
from .render import render
from .runtime import DEFAULT_MANIFESTS, open_graph
from .worklist import worklist
from .write import assert_value, decide

DEFAULT_MEMORY = ("/home/innom-dt/.claude/projects/"
                  "-mnt-SN850X-8TB-EXT4-Projects-GitHub-cj-mills-cjm-substrate/memory")
DEFAULT_REPOS = "/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills"


async def _dispatch(args) -> int:
    async with open_graph(args.graph_db_path, args.manifests_dir) as gx:
        if args.command == "ingest":
            nodes, edges = build_dev_graph_elements(
                args.memory_dir, None if args.no_repo_map else args.repos_dir,
                seed=not args.no_seed)
            res = await extend_graph(gx.queue, gx.graph_id, nodes, edges)
            print(f"ingested: {res.nodes_added} nodes added / {res.nodes_verified} verified, "
                  f"{res.edges_added} edges added / {res.edges_existing} existing")
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
        elif args.command == "worklist":
            print(render("worklist", await worklist(gx, args.memory_dir), args.format))
        elif args.command == "assert":
            res = await assert_value(gx, args.subject, args.predicate, args.value,
                                     actor=args.actor, evidence=args.evidence,
                                     supersede=args.supersede)
            print(render("assert", res, args.format))
            return 2 if res.get("conflict") else 0
        elif args.command == "decide":
            res = await decide(gx, args.statement, actor=args.actor, supports=args.supports,
                               supersedes=args.supersedes, session=args.session)
            print(render("decide", res, args.format))
        elif args.command == "oracle":
            res = await run_version_oracle(gx, args.repos_dir, only=args.only)
            print(render("oracle", res, args.format))
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="cjm-context-graph",
                                 description="Projection/navigation + write surface over a context graph.")
    ap.add_argument("--graph-db-path", required=True, help="Explicit sqlite db path (no default)")
    ap.add_argument("--manifests-dir", default=DEFAULT_MANIFESTS,
                    help="Dir with the graph-storage capability manifest")
    ap.add_argument("--format", choices=("human", "agent"), default="human")
    sub = ap.add_subparsers(dest="command", required=True)

    p_ing = sub.add_parser("ingest", help="Build/refresh the dev graph (idempotent)")
    p_ing.add_argument("--memory-dir", default=DEFAULT_MEMORY)
    p_ing.add_argument("--repos-dir", default=DEFAULT_REPOS)
    p_ing.add_argument("--no-repo-map", action="store_true", help="Skip the repo map")
    p_ing.add_argument("--no-seed", action="store_true", help="Skip the hand-seeded slots")

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

    p_de = sub.add_parser("decide", help="Record a decision + its premise edges")
    p_de.add_argument("statement")
    p_de.add_argument("--actor", default="agent:session")
    p_de.add_argument("--supports", action="append", help="Premise assertion id (repeatable)")
    p_de.add_argument("--supersedes", action="append", help="Prior decision id (repeatable)")
    p_de.add_argument("--session", default=None, help="Session key this was decided in")

    p_or = sub.add_parser("oracle", help="Run the version oracle (refresh version slots)")
    p_or.add_argument("--repos-dir", default=DEFAULT_REPOS)
    p_or.add_argument("--only", action="append", help="Restrict to a repo key/name (repeatable)")

    args = ap.parse_args()
    return asyncio.run(_dispatch(args))


if __name__ == "__main__":
    sys.exit(main())
