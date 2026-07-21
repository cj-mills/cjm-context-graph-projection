# cjm-context-graph-projection

<!-- generated from the context graph by `cjm-context-graph readme` — do not edit by hand; edit the graph (the urge to hand-edit = move it on-graph) -->

Projection and navigation core for context graphs: bounded, ranked, provenance-carrying reads (schema / state / relevance / show) over any cjm-substrate context graph, with a CLI driver. The agent-facing read layer of the self-hosting graph arc.

## Modules

- **`cjm_context_graph_projection.__init__`**
- **`cjm_context_graph_projection.authoring`** — The B write surface: AUTHOR a verbatim-text slot on-graph, emit the canonical artifact.
- **`cjm_context_graph_projection.cli`** — The `cjm-context-graph` CLI — first driver of the projection core.
- **`cjm_context_graph_projection.code_edges`** — Orphaned code-target edge detector: journaled links whose endpoint no longer resolves.
- **`cjm_context_graph_projection.cohesion`** — Module cohesion audit over the code graph — the read-only cohesion ORACLE (N+1).
- **`cjm_context_graph_projection.contradictions`** — The standing dedup query: slots whose ACTIVE assertions disagree.
- **`cjm_context_graph_projection.conventions`** — Structural convention audit over the code/notebook graph (the enforcement nbdev lacks).
- **`cjm_context_graph_projection.devgraph`** — Build the dev graph's nodes + edges from its sources (the dev-graph DRIVER).
- **`cjm_context_graph_projection.display`** — Graph-carried display rules: the presentation vocabulary (DEC `16bcd96e`).
- **`cjm_context_graph_projection.explorer_page`** — The graph EXPLORER client page — the first client of the `serve` data API.
- **`cjm_context_graph_projection.factlayer`** — Shared fine-tier reads over the fact-layering schema (slots + assertions).
- **`cjm_context_graph_projection.hybrid_page`** — The HYBRID graph explorer client — GPU physics canvas + DOM overlay (check-in 1233ab46).
- **`cjm_context_graph_projection.journal`** — The write journal: the durable, replayable source of truth for born-on-graph writes.
- **`cjm_context_graph_projection.lens`** — Lenses: graph-carried, parameterized views (DEC `f1b02b95` — tier 2 of the
- **`cjm_context_graph_projection.listing`** — Structured enumeration: every node of a LABEL / assertion of a PREDICATE / edge of a RELATION.
- **`cjm_context_graph_projection.module_ops`** — Module-edit ops — create / rename / delete / regroup a module as graph edge ops.
- **`cjm_context_graph_projection.onboarding`** — Project the MEMORY onboarding surface from the dev graph (the dev driver).
- **`cjm_context_graph_projection.oracle`** — The version oracle: a programmatic Procedure that keeps `version` slots fresh.
- **`cjm_context_graph_projection.projection`** — The projection core: schema / show / relevance / state over a context graph.
- **`cjm_context_graph_projection.readiness`** — The readiness frontier: which work-items are READY vs BLOCKED — derived, never stored.
- **`cjm_context_graph_projection.readme`** — README-as-projection (v1, STRUCTURAL-ONLY): generate a repo's README FROM THE GRAPH.
- **`cjm_context_graph_projection.reconcile`** — M2b shadow-phase RECONCILE — surface + (explicitly) absorb out-of-band `.md` edits.
- **`cjm_context_graph_projection.refactor`** — Refactoring-candidate identification over the code graph (the IDENTIFY half of move).
- **`cjm_context_graph_projection.refactor_ops`** — `move` — relocate a symbol between modules (the EXECUTE half of refactor-candidates).
- **`cjm_context_graph_projection.registers`** — Register drift-check: each hub note's member-cache vs the active `role` assertions.
- **`cjm_context_graph_projection.rename_ops`** — Symbol `rename` — the Ext-B increment: scoped identifier substitution INTO bodies.
- **`cjm_context_graph_projection.render`** — Render projection results for a consumer: agent (JSON) or human (markdown).
- **`cjm_context_graph_projection.runtime`** — Open a context graph for reading/writing (domain-neutral runtime wiring).
- **`cjm_context_graph_projection.seeds`** — Hand-seeded load-bearing slots + the rename-stable repo-key machinery.
- **`cjm_context_graph_projection.serve`** — A served, read-only graph EXPLORER data API over the read verbs — the richer-viz INSTRUMENT.
- **`cjm_context_graph_projection.source_state`** — N+3 Phase 1 (SHADOW): capture a module's canonical source into a SOURCE journal and
- **`cjm_context_graph_projection.structure`** — M2a GRADIENT — structural memory authoring: create a note / add a section, born on-graph.
- **`cjm_context_graph_projection.viz`** — A minimal READ-ONLY visualization: the readiness frontier + its dependency DAG, as HTML.
- **`cjm_context_graph_projection.worklist`** — The propose/confirm worklist: candidate fixes that need a human decision.
- **`cjm_context_graph_projection.write`** — The write surface: `assert` a slot value, `decide` a conclusion.

## API

### `cjm_context_graph_projection.authoring`

- `add_symbol` _function_ — Mint a NEW top-level CodeSymbol into a module, then emit its canonical artifact.
- `add_text` _function_ — Mint a NEW CodeText region (imports/constants/docstring/`__all__`) into a module, then emit.
- `author` _function_ — Author a node's verbatim-text slot, then emit its canonical artifact to disk.
- `emit_artifact` _function_ — Emit a container's canonical artifact FROM THE GRAPH (graph -> .py / .ipynb / .md).
- `file_section_raws` _function_ — Each of a note's sections' `raw` span as the FILE currently decomposes (the other
- `graph_section_raws` _function_ — Each of a note's sections' on-graph `raw` span, keyed by anchor (the divergence/
- `read_node` _function_ — Deliver a node's verbatim CONTENT — the read DUAL of `author`/`emit`.
- `read_slot` _function_ — Read a node's current verbatim-slot text (the `--editor` pop / preview input).
- `section_divergence` _function_ — Read-only: detect, at SECTION grain, where a note's `.md` has drifted from the graph.

### `cjm_context_graph_projection.cli`

- `main` _function_

### `cjm_context_graph_projection.code_edges`

- `classify_orphaned_links` _function_ — Pure: the journaled links the next replay will silently drop.
- `orphaned_edges` _function_ — The derived orphan report over journal `link` ops + the current graph.

### `cjm_context_graph_projection.cohesion`

- `cohesion` _function_ — Audit module cohesion: grab-bag (under_split) + scattered-helper (over_split) candidates.
- `compute_cohesion` _function_ — Compute module cohesion candidates from the code graph slices (pure).

### `cjm_context_graph_projection.contradictions`

- `contradictions` _function_ — All slots whose active assertions form a hard contradiction (optionally scoped).

### `cjm_context_graph_projection.conventions`

- `compute_conventions` _function_ — Compute convention findings from CodeSymbol nodes + the documented-id set (pure).
- `compute_untested` _function_ — The untested-symbol audit (pure): every public top-level PACKAGE symbol (test
- `conventions` _function_ — Audit notebook-sourced symbols for missing prose/docstrings + non-granular cells,

### `cjm_context_graph_projection.devgraph`

- `build_dev_graph_elements` _function_ — Assemble the full dev graph: memory notes (+ refs), the repo map (+ deps),
- `code_elements` _function_ — Decompose each repo's importable package into code nodes + edges.
- `memory_elements` _function_ — Decompose every memory markdown file (except MEMORY.md) into graph elements.
- `notebook_elements` _function_ — Decompose each repo's nbdev notebooks into code/cell nodes + edges.
- `notes_corpus_elements` _function_ — Decompose an arbitrary `<dir>/index.md` markdown corpus into graph elements.
- `repo_map_elements` _function_ — One repo Entity per cjm-* repo (RENAME-STABLE keys) + DEPENDS_ON from pyproject.
- `resolve_corpus_code_edges` _function_ — Resolve CALLS/IMPORTS edges ACROSS the whole code + notebook corpus.
- `resolve_test_edges` _function_ — Resolve TESTS edges across the corpus (the code<->test link).
- `test_elements` _function_ — Decompose each repo's pytest / manual test files into code nodes + edges.

### `cjm_context_graph_projection.display`

- `Displayer` _class_ — The rule interpreter: loads a graph's DisplayRules once, then batch-annotates.
- `annotate_display` _function_ — Load this graph's rules + annotate `nodes` (the one-call seam for read verbs).
- `display_rule_node_id` _function_ — Deterministic DisplayRule id — one rule per kind, so re-authoring converges.
- `first_clause` _function_ — A long statement's leading clause — the Decision-title extractor.
- `node_title` _function_ — Best display label for a node: the stored/cascade tiers of the resolution order.
- `parse_template` _function_ — Parse a display template into literal / property / edge parts.
- `set_display_rule` _function_ — Author/update the graph-carried DisplayRule for a kind (presentation vocabulary).

### `cjm_context_graph_projection.factlayer`

- `active_assertions` _function_ — The active assertions in a slot under append-only supersession.
- `alias_index` _function_ — Build the entity alias index + an id->entity lookup (rename-stable subjects).
- `count_label` _function_ — Count nodes of a label (optionally predicate-filtered) — `NodeQuery(count=True)`.
- `group_by_slot` _function_ — Group assertion nodes by their `slot_id` property.
- `load_assertions` _function_ — All Assertion nodes.
- `load_contradicts` _function_ — All CONTRADICTS pairs already recorded (for write idempotency / reporting).
- `load_edge_pairs` _function_ — All (source, target) pairs for an edge relation type.
- `load_label` _function_ — All nodes of a label (bounded by `limit`).
- `load_label_where` _function_ — Nodes of a label filtered by property predicates, SERVER-SIDE (`NodeQuery.where`).
- `load_nodes` _function_ — Batch-fetch nodes by id in ONE worker round-trip (`NodeQuery.ids`).
- `load_supersedes` _function_ — All SUPERSEDES (superseder, superseded) pairs (the resolve_active input).
- `nid` _function_ — A node's id (typed GraphNode or wire dict).
- `note_alias_map` _function_ — Confirmed note aliases as a {drifted-slug: canonical-slug} map.
- `prop` _function_ — One property value off a node.
- `props` _function_ — A node's properties dict (typed GraphNode or wire dict).

### `cjm_context_graph_projection.journal`

- `journal_sourced_note_paths` _function_ — The memory `.md` files `ingest` must NOT read — they're journal-sourced now.
- `journal_window` _function_ — The journal-window projection: which nodes a window/session touched, when, how.
- `journal_window_view` _function_ — The SESSION LENS read verb: `journal_window` + graph join (title/label per ref).
- `m3_baseline_import` _function_ — One-time M3 GENESIS IMPORT: emit a per-note `new-note` baseline op into the journal.
- `replay_journal` _function_ — Re-apply every journaled write through its core verb (idempotent).
- `touched_node_ids` _function_ — Best-effort node refs a journaled op touched — the session-lens feed (2f51ff5d).

### `cjm_context_graph_projection.lens`

- `apply_lens` _function_ — APPLY a lens: bind params -> run each selection clause through the real
- `bind_params` _function_ — Bind an application's params: defaults + provided, typed, loud on gaps.
- `lens_node_id` _function_ — Deterministic Lens id — one lens per slug, so re-authoring converges.
- `load_lenses` _function_ — Every well-formed Lens on this graph (the shelf feed), slug-sorted.
- `set_lens` _function_ — Author/update a graph-carried Lens (journaled upsert-by-slug).
- `validate_lens_spec` _function_ — Parse-validate a lens spec against the v1 shape; a bad spec NEVER lands.

### `cjm_context_graph_projection.listing`

- `list_graph` _function_ — Enumerate one CLASS of the graph: nodes by label / assertions by predicate / edges
- `parse_where` _function_ — Parse `--where PROP=VALUE` clauses into property predicates (op `eq`, AND).

### `cjm_context_graph_projection.module_ops`

- `delete_module` _function_ — Delete a module — drop its file and its whole graph subtree. Guarded: refuses while
- `flip_notebook_to_py` _function_ — The golden-reference flip, ONE LOUD VERB (DEC b2c5363d): notebook -> plain `.py`.
- `new_module` _function_ — Mint an empty CodeModule node (the target a `regroup`/`move` populates).
- `regroup` _function_ — Gather symbols into a module — the EXECUTE verb for an `under_split` (extract a
- `rename_module` _function_ — Rename a `.py` module — re-emit its content at the new path, drop the old file, and
- `rewrite_module_import` _function_ — Rewrite a module-RENAME across an importer: every `from old import …` and

### `cjm_context_graph_projection.onboarding`

- `project_onboarding` _function_ — Project the onboarding surface from the graph's `Note` nodes + the dev seeds.

### `cjm_context_graph_projection.oracle`

- `procedure_node` _function_ — The oracle's Procedure node (the programmatic value-source for its assertions).
- `read_repo_version` _function_ — Read a repo's version: installed metadata first, else `__version__` on disk.
- `run_version_oracle` _function_ — Refresh `version` slots for repo entities; report what changed.

### `cjm_context_graph_projection.projection`

- `ambiguity_error` _function_ — One-line error naming the candidates, so the caller's next call can be exact.
- `explore` _function_ — Descend into one cluster of a query: its members, BOUNDED, re-faceting if large.
- `find_seeds` _function_ — Find seed nodes by term overlap with their text fields (accept misses).
- `full_graph_view` _function_ — The WHOLE graph as one canvas payload: every node (cheap-title tier) + every edge.
- `get_schema` _function_ — The graph's ontology: node labels, edge types, per-label counts.
- `graph_overview` _function_ — The whole-graph orientation view — the facets of the DEFAULT (empty) query.
- `grep` _function_ — Exact-substring CONTENT search over every node's text fields — the literal third leg.
- `locate` _function_ — Resolve a human HANDLE to node(s) + their on-disk path — the inverse of `show`.
- `node_summary` _function_ — Compact, provenance-carrying summary of a node (the unit of a bounded read).
- `relevant` _function_ — The bounded level-0 pull: the full reached set's SHAPE + a top-k teaser.
- `resolve_node_ref` _function_ — Resolve a node reference: exact id first, then unique id-prefix.
- `show` _function_ — One node in full, with its immediate neighbours + the relation to each.
- `state` _function_ — Graph overview (no subject) or a subject's effective view (`show`).
- `subgraph_view` _function_ — The BULK read verb: a node SET -> nodes + interconnecting edges, batched.

### `cjm_context_graph_projection.readiness`

- `classify_readiness` _function_ — Pure: partition work-items into done / ready / blocked from authored ground truth.
- `readiness` _function_ — The derived ready/blocked/done frontier over authored `task_state` + `GATED_BY` edges.
- `summarize_checks` _function_ — Pure: per-item DoD summary from the checks' own task_states.

### `cjm_context_graph_projection.readme`

- `project_readme` _function_ — Project a repo's README markdown from the graph (structural-only v1).
- `repo_purpose` _function_ — The repo's intro/"why" prose: the active `purpose` assertion on the repo Entity.

### `cjm_context_graph_projection.reconcile`

- `reconcile_memory` _function_ — Report `.md`<->graph section drift across the corpus; optionally absorb hand-edits.

### `cjm_context_graph_projection.refactor`

- `compute_refactor_candidates` _function_ — Compute refactoring candidates from the code graph slices (pure).
- `refactor_candidates` _function_ — Identify relocation / dead-code / consolidation / split candidates over the code graph.

### `cjm_context_graph_projection.refactor_ops`

- `move` _function_ — Relocate a single top-level symbol from its module to another, graph-driven.
- `rewrite_symbol_import` _function_ — Rewrite `from old_module import ... S ...` -> import S from new_module instead.

### `cjm_context_graph_projection.registers`

- `classify_register_drift` _function_ — Pure: reconcile each register's cache against its membership ground truth.
- `register_drift` _function_ — The derived register-cache reconciliation over `role` assertions + hub edges.

### `cjm_context_graph_projection.rename_ops`

- `rename_symbol` _function_ — Rename a top-level free function/class everywhere it is referenced, graph-driven.
- `rewrite_import_for_rename` _function_ — Re-point an importer's `from src_module import old [as a]` at the new name.
- `scoped_rename` _function_ — Rename references to the module-global `old` -> `new`, scope-aware, by exact position.

### `cjm_context_graph_projection.render`

- `render` _function_ — Render a projection result in the requested format.

### `cjm_context_graph_projection.runtime`

- `GraphHandle` _class_ — A live, started graph: the queue + the capability id to address it.
- `open_graph` _function_ — Load the graph-storage capability on `graph_db_path` and yield a started handle.

### `cjm_context_graph_projection.seeds`

- `aliases_for` _function_ — Prior names that should resolve to this repo (empty unless it was renamed).
- `class_subject_elements` _function_ — Class-subject entities + PART_OF-style membership edges (ABOUT member->class).
- `conceptual_key` _function_ — The durable conceptual key for a repo (rename-aware; defaults to the name).
- `rename_contradiction_elements` _function_ — The torch/hf-utils `rename-disposition` slots with BOTH claims active.
- `repo_dir_name` _function_ — The CURRENT repo dir name for a conceptual key (identity unless renamed).
- `seed_elements` _function_ — All hand-seeded elements (rename contradiction + stale version + class subjects).
- `stale_version_seed_elements` _function_ — A `cjm-substrate` version slot seeded BEHIND the real version (oracle bumps it).

### `cjm_context_graph_projection.serve`

- `build_app` _function_ — Build the read-only API app over already-open graph handles.
- `graph_names` _function_ — Derive a stable short name per db (its file stem; collisions suffixed `-2`, `-3`, …).
- `serve_graphs` _function_ — Open every graph once, hold the handles, and serve the API until interrupted.

### `cjm_context_graph_projection.source_state`

- `absorb_authored_text` _function_ — Absorb an `author` edit of a GRAPH-SOURCED module into the source journal.
- `append_register` _function_ — Append a `register` event — repo inventory as JOURNAL DATA (DEC c47912f6).
- `append_retire` _function_ — Append a `retire` op ending a module key's journal life.
- `append_source` _function_ — Append a `source` op, skipping a write identical to the module's current latest state.
- `canonical_emit` _function_ — Decompose source text and re-emit it canonically — the exact graph→`.py` Phase 2 yields.
- `canonical_emit_notebook` _function_ — The notebook analogue of `canonical_emit`: parse to cells, re-render canonically.
- `cutover_module` _function_ — Phase 2: make the JOURNAL the module's source of truth (the persistence flip).
- `emit_source_artifact` _function_ — (Re)generate a module's file artifact from its journaled source (the recovery /
- `flip_module` _function_ — Capture a module's CANONICAL source into the shadow source journal (Phase 1).
- `graph_sourced_modules` _function_ — The modules whose ingest source IS the journal (a `cutover` op exists for them).
- `is_test_module_path` _function_ — Whether a module path denotes TEST source (`tests/` or `tests_manual/`).
- `journaled_emit` _function_ — The ops seam (pillar 1 of DEC 6ee4b4f2): events BEFORE files — THE file-write path.
- `latest_source_ops` _function_ — The LATEST source state per module (last write wins — the 'journal STATE, not diff'
- `notebook_to_py_source` _function_ — Build a plain-`.py` module source from a notebook's EXPORT cells (the flip transform).
- `read_source_journal` _function_ — Read every `source` op (one JSON object per line; missing file = []).
- `source_check` _function_ — The soak instrument: for each shadow-sourced module, check two things.

### `cjm_context_graph_projection.structure`

- `add_section` _function_ — Add a section to an existing note (append, or insert after an anchor), born on-graph.
- `new_note` _function_ — Create a brand-new note, born on-graph (write the `.md` + ingest it this session).
- `reconstruct_note` _function_ — Reconstruct a whole note (Note + ordered Section nodes) FROM JOURNALED text — the M3

### `cjm_context_graph_projection.viz`

- `project_viz` _function_ — Project the readiness frontier into a self-contained interactive HTML page.
- `render_viz_html` _function_ — Render the elements into one self-contained interactive HTML page (Cytoscape + dagre).
- `viz_elements` _function_ — Pure: turn a readiness frontier into Cytoscape elements — the whole data model.

### `cjm_context_graph_projection.worklist`

- `dangling_reference_proposals` _function_ — Referenced `[[slugs]]` with no note, each with a fuzzy suggestion (no auto-fix).
- `dangling_reference_sources` _function_ — The note ids whose `[[wiki-links]]` include `drifted_slug` (alias evidence).
- `worklist` _function_ — Assemble the propose/confirm worklist (graph signals + optional corpus triage).

### `cjm_context_graph_projection.write`

- `add_check` _function_ — Attach a definition-of-done check to a work item (DoD-as-graph-objects).
- `alias` _function_ — Confirm a drifted link slug as an alias OF a real note (the worklist payoff).
- `assert_value` _function_ — Write one value to a `(subject, predicate)` slot, recording any conflict.
- `author_section` _function_ — Apply a memory section's verbatim `raw` STATE to the graph — the born-on-graph leg
- `decide` _function_ — Record a Decision + its `SUPPORTED_BY` premise edges (reasoning substrate).
- `link` _function_ — Mint a deliberate edge between two EXISTING nodes (heterogeneous interlink).
- `register_session` _function_ — Register/update a timestamp-keyed Session node — the session SPINE (DEC 6124d8bf).
- `resolve_subject` _function_ — Resolve a subject to an entity id (rename-stable), minting a `term` entity
- `unlink` _function_ — RETRACT a deliberate edge — the write dual of `link` (finding 2f1d9382).

## Dependencies

**Depends on:** `cjm-context-graph-layer`, `cjm-context-graph-primitives`, `cjm-dev-graph-schema`, `cjm-markdown-decompose-core`, `cjm-notebook-decompose-core`, `cjm-python-decompose-core`, `cjm-substrate`
**Used by:** `cjm-notebook-decompose-core`
