"""The `cjm-context-graph` CLI — first driver of the projection core.

Read surface: `schema` / `state [subject]` / `relevant <task>` / `show <id>`
(the canonical session-start sequence). Plus `ingest` to build/refresh the dev
graph. `--graph-db-path` is always explicit; `--format agent|human` selects JSON
vs markdown. The write surface (`assert`/`decide`/…) arrives in a later increment.
"""

import argparse
import asyncio
import sys

from cjm_context_graph_layer.ops import extend_graph

from .devgraph import build_dev_graph_elements
from .projection import get_schema, relevant, show, state
from .render import render
from .runtime import DEFAULT_MANIFESTS, open_graph

DEFAULT_MEMORY = ("/home/innom-dt/.claude/projects/"
                  "-mnt-SN850X-8TB-EXT4-Projects-GitHub-cj-mills-cjm-substrate/memory")
DEFAULT_REPOS = "/mnt/SN850X_8TB_EXT4/Projects/GitHub/cj-mills"


async def _dispatch(args) -> int:
    async with open_graph(args.graph_db_path, args.manifests_dir) as gx:
        if args.command == "ingest":
            nodes, edges = build_dev_graph_elements(
                args.memory_dir, None if args.no_repo_map else args.repos_dir)
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
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="cjm-context-graph",
                                 description="Projection/navigation reads over a context graph.")
    ap.add_argument("--graph-db-path", required=True, help="Explicit sqlite db path (no default)")
    ap.add_argument("--manifests-dir", default=DEFAULT_MANIFESTS,
                    help="Dir with the graph-storage capability manifest")
    ap.add_argument("--format", choices=("human", "agent"), default="human")
    sub = ap.add_subparsers(dest="command", required=True)

    p_ing = sub.add_parser("ingest", help="Build/refresh the dev graph (idempotent)")
    p_ing.add_argument("--memory-dir", default=DEFAULT_MEMORY)
    p_ing.add_argument("--repos-dir", default=DEFAULT_REPOS)
    p_ing.add_argument("--no-repo-map", action="store_true", help="Skip the repo map")

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

    args = ap.parse_args()
    return asyncio.run(_dispatch(args))


if __name__ == "__main__":
    sys.exit(main())
