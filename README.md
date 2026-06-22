# cjm-context-graph-projection

The **projection and navigation core** for context graphs — the agent-facing
*read* layer of the self-hosting graph arc. Every read is **bounded, ranked, and
provenance-carrying, with drill-down** — never a raw subgraph dump.

It answers four questions an agent (or human) has at the start of a task, against
any [`cjm-substrate`](https://github.com/cj-mills/cjm-substrate) context graph:

- **`schema`** — what kinds of things are in this graph? (entry point for a
  context-free agent)
- **`state [subject]`** — the effective view of a subject (or a graph overview)
- **`relevant <task>`** — the nodes structurally nearest a task, ranked
- **`show <id>`** — one node in full, with its neighbours

A CLI driver (`cjm-context-graph`) is the first consumer; a TUI and an
MCP/agent-tool endpoint are meant to reuse the same core (never parallel impls).

> **Born non-nbdev.** Plain `.py`, `pytest`, fine granularity.
>
> **Domain split:** `runtime` and `projection` are **domain-neutral** (they
> depend only on the substrate runtime + graph layer/primitives and operate on
> generic nodes/edges) — extractable as a pure core if a second graph needs it.
> `devgraph` (ingest of memory / ledgers / repo-map) and the `ingest` CLI command
> are the **dev-graph driver**, the first adopter; they pull in
> `cjm-dev-graph-schema` + `cjm-markdown-decompose-core`.

## Install

```bash
pip install -e .
```

## Usage

Global options (`--graph-db-path`, `--manifests-dir`, `--format`) come **before**
the subcommand:

```bash
# Build / refresh the dev graph (memory corpus + repo map) — explicit db path.
cjm-context-graph --graph-db-path .cjm/dev-graph.db ingest

# The canonical session-start sequence.
cjm-context-graph --graph-db-path .cjm/dev-graph.db schema
cjm-context-graph --graph-db-path .cjm/dev-graph.db state
cjm-context-graph --graph-db-path .cjm/dev-graph.db relevant "self-hosting graph arc"
cjm-context-graph --graph-db-path .cjm/dev-graph.db show <node-id>

# Agent-readable JSON instead of rendered markdown.
cjm-context-graph --graph-db-path .cjm/dev-graph.db --format agent relevant "stage 9 rename"
```

`--graph-db-path` is always explicit (no convenience default-repoints); the
graph-storage capability is loaded from `--manifests-dir`.

## Status

Early — the read surface (`schema`/`state`/`relevant`/`show`) + a dev-graph
`ingest`. Relevance v1 is structural BFS ranked by edge-type weight × recency ×
supersession; smarter seed-finding and the write surface (`assert`/`decide`/…)
arrive in later increments of the arc.
