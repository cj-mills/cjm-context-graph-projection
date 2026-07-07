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
- **`cjm_context_graph_projection.journal`** — The write journal: the durable, replayable source of truth for born-on-graph writes.
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
- **`tests.test_add_symbol_router`** — add-symbol (the authoring CREATE leg) + the nested-symbol edit router, over a fake
- **`tests.test_authoring_units`** — Pure authoring helpers — slot routing + the apply split (no graph needed).
- **`tests.test_cli_m3_smoke`** — CLI dispatch smoke for `m3-baseline` — guards the import wiring the unit tests can't see.
- **`tests.test_code_edges`** — Orphaned code-target edges: pure classification + render (no graph needed).
- **`tests.test_cohesion`** — Module cohesion audit (pure compute over code-graph slices).
- **`tests.test_conventions_resolve`** — Cross-corpus CALLS/IMPORTS resolution + the structural convention audit (pure cores).
- **`tests.test_devgraph`** — Repo-map extraction: pyproject dep parsing + Entity nodes / DEPENDS_ON edges.
- **`tests.test_display`** — The display seam: template grammar, first-clause extractor, cascade, rule rendering.
- **`tests.test_flip_to_py`** — The golden-reference flip (notebook -> plain .py, ONE LOUD VERB): retire-op journal
- **`tests.test_journal`** — The write journal: append (with de-dup) + read, the pure half (no graph).
- **`tests.test_listing`** — Structured enumeration: the `list` mode-selection guard + its render (no graph needed).
- **`tests.test_module_ops`** — Pure-helper tests for the module-edit ops (rewrite_module_import + import-name derivation).
- **`tests.test_projection_units`** — Pure projection helpers + rendering (no graph needed).
- **`tests.test_readiness`** — The readiness frontier: pure derivation (classify) + its render (no graph needed).
- **`tests.test_readme`** — README-as-projection v1 (structural-only): the API surface, deps, and on-graph purpose.
- **`tests.test_refactor`** — Refactoring-candidate identification (pure compute over code-graph slices).
- **`tests.test_refactor_ops`** — Import rewriting for the `move` op (the caller `from A import S` -> `from B import S`).
- **`tests.test_registers`** — Register drift-check: pure reconciliation (classify) + its render (no graph needed).
- **`tests.test_rename_ops`** — Scope-aware identifier rename (the Ext-B crux) + the importer-rewrite helper.
- **`tests.test_seeds_render`** — Pure pieces of the Inc 3 surface: rename-stable keys, seed shape, render.
- **`tests.test_source_state`** — N+3 source-state: canonical emit fixpoint + the shadow source journal + soak check
- **`tests_manual.authoring`** — Authoring-on-graph dogfood: the B write surface (the make-or-break increment).
- **`tests_manual.code_on_graph`** — Code-on-graph dogfood: decompose the arc libs' own `.py` and prove the cross-link.
- **`tests_manual.divergence_probe`** — Section-grain divergence probe — the read-only down-payment toward the file<->graph
- **`tests_manual.inc3_first_slice`** — Inc 3 DoD: the first-slice-complete cut, validated end-to-end through real storage.
- **`tests_manual.inc4_dogfood`** — Inc 4 dogfood loop: confirm an alias -> heal the reference -> resurface, end-to-end.
- **`tests_manual.journal_rebuild`** — Migration discipline: the db is a REBUILDABLE PROJECTION of (corpus+repo+seeds+journal).
- **`tests_manual.m2b_shadow`** — M2b phase-1 (SHADOW) dogfood: the `section` journal verb + `reconcile-memory`.
- **`tests_manual.m3_cutover`** — M3 CUTOVER thin-slice harness — demonstrate the 6-item cutover DoD (decision 2f5222ab).
- **`tests_manual.module_ops`** — Module-edit-ops dogfood: create / regroup / rename / delete a module as graph edge ops.
- **`tests_manual.move`** — `move` dogfood: relocate a symbol across modules as a graph-driven refactor.
- **`tests_manual.rename`** — Symbol-`rename` dogfood: the Ext-B increment — scoped identifier substitution INTO bodies.
- **`tests_manual.session_start_sequence`** — Inc 2 DoD: the canonical session-start sequence orients on a real task.
- **`tests_manual.structure`** — M2 GRADIENT dogfood: structural memory authoring (`new-note` + `add-section`).
- **`tests_manual.viz`** — Minimal read-only VIZ projector — the readiness frontier + dependency DAG as self-contained HTML.

## API

### `cjm_context_graph_projection.authoring`

- `add_symbol` _function_ — Mint a NEW top-level CodeSymbol into a module, then emit its canonical artifact.
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

- `append_write` _function_ — Append one write op (skipping an exact (verb,args) duplicate).
- `journal_sourced_note_paths` _function_ — The memory `.md` files `ingest` must NOT read — they're journal-sourced now.
- `m3_baseline_import` _function_ — One-time M3 GENESIS IMPORT: emit a per-note `new-note` baseline op into the journal.
- `read_journal` _function_ — Read every journaled write op (one JSON object per line; missing file = []).
- `replay_journal` _function_ — Re-apply every journaled write through its core verb (idempotent).

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
- `get_schema` _function_ — The graph's ontology: node labels, edge types, per-label counts.
- `graph_overview` _function_ — The whole-graph orientation view — the facets of the DEFAULT (empty) query.
- `grep` _function_ — Exact-substring CONTENT search over every node's text fields — the literal third leg.
- `locate` _function_ — Resolve a human HANDLE to node(s) + their on-disk path — the inverse of `show`.
- `node_summary` _function_ — Compact, provenance-carrying summary of a node (the unit of a bounded read).
- `relevant` _function_ — The bounded level-0 pull: the full reached set's SHAPE + a top-k teaser.
- `resolve_node_ref` _function_ — Resolve a node reference: exact id first, then unique id-prefix.
- `show` _function_ — One node in full, with its immediate neighbours + the relation to each.
- `state` _function_ — Graph overview (no subject) or a subject's effective view (`show`).

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
- `seed_elements` _function_ — All hand-seeded elements (rename contradiction + stale version + class subjects).
- `stale_version_seed_elements` _function_ — A `cjm-substrate` version slot seeded BEHIND the real version (oracle bumps it).

### `cjm_context_graph_projection.serve`

- `build_app` _function_ — Build the read-only API app over already-open graph handles.
- `graph_names` _function_ — Derive a stable short name per db (its file stem; collisions suffixed `-2`, `-3`, …).
- `serve_graphs` _function_ — Open every graph once, hold the handles, and serve the API until interrupted.

### `cjm_context_graph_projection.source_state`

- `absorb_authored_text` _function_ — Absorb an `author` edit of a GRAPH-SOURCED module into the source journal.
- `append_retire` _function_ — Append a `retire` op ending a module key's journal life.
- `append_source` _function_ — Append a `source` op, skipping a write identical to the module's current latest state.
- `canonical_emit` _function_ — Decompose source text and re-emit it canonically — the exact graph→`.py` Phase 2 yields.
- `canonical_emit_notebook` _function_ — The notebook analogue of `canonical_emit`: parse to cells, re-render canonically.
- `cutover_module` _function_ — Phase 2: make the JOURNAL the module's source of truth (the persistence flip).
- `emit_source_artifact` _function_ — (Re)generate a module's file artifact from its journaled source (the recovery /
- `flip_module` _function_ — Capture a module's CANONICAL source into the shadow source journal (Phase 1).
- `graph_sourced_modules` _function_ — The modules whose ingest source IS the journal (a `cutover` op exists for them).
- `is_test_module_path` _function_ — Whether a module path denotes TEST source (`tests/` or `tests_manual/`).
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
- `resolve_subject` _function_ — Resolve a subject to an entity id (rename-stable), minting a `term` entity

### `tests.test_add_symbol_router`

- `FakeGraph` _class_ — In-memory stand-in for the graph capability (the ops the authoring verbs use).
- `test_add_symbol_appends_emits_and_links` _function_
- `test_add_symbol_binds_refs_to_available_imports` _function_
- `test_add_symbol_dry_run_touches_nothing` _function_
- `test_add_symbol_keeps_test_module_imports_verbatim` _function_
- `test_add_symbol_refuses_bad_bodies_and_duplicates` _function_
- `test_add_symbol_refuses_notebook_modules` _function_
- `test_nested_symbol_edit_routes_through_owning_class` _function_
- `test_nested_symbol_replace_is_refused_with_guidance` _function_
- `test_nested_symbol_routes_to_owning_notebook_cell` _function_

### `tests.test_authoring_units`

- `test_apply_replace_and_targeted_edit_on_a_section_span` _function_
- `test_code_and_cell_slots_still_route` _function_
- `test_non_authorable_node_returns_none` _function_
- `test_section_routes_by_inference_without_a_surfaced_label` _function_
- `test_section_routes_to_raw_note_slot_by_label` _function_

### `tests.test_cli_m3_smoke`

- `test_decide_state_open_mints_and_asserts_in_one_invocation` _function_
- `test_link_resolves_id_prefixes_and_journals_resolved_ids` _function_
- `test_m3_baseline_cli_dispatches_and_journals` _function_
- `test_m3_baseline_cli_requires_journal` _function_
- `test_new_note_cli_journals_natively` _function_

### `tests.test_code_edges`

- `test_classify_dedups_identical_ops` _function_
- `test_classify_labeled_orphan_gets_fuzzy_remap_proposal` _function_
- `test_classify_low_similarity_yields_no_proposal` _function_
- `test_classify_missing_endpoint_is_orphaned_even_without_label` _function_
- `test_classify_resolving_endpoints_are_clean` _function_
- `test_render_orphaned_edges_clean` _function_
- `test_render_orphaned_edges_names_proposal_and_context` _function_

### `tests.test_cohesion`

- `test_cohesive_module_not_flagged` _function_
- `test_dominant_core_plus_satellites_is_damped` _function_
- `test_name_family_is_not_flagged` _function_
- `test_over_split_damps_driver_consumer` _function_
- `test_over_split_excludes_multi_module_and_cross_repo_callers` _function_
- `test_over_split_helper_used_only_by_one_other_module` _function_
- `test_scope_restricts_to_one_repo` _function_
- `test_tokens_snake_and_camel` _function_
- `test_under_split_flags_disconnected_grabbag` _function_
- `test_under_split_respects_min_symbols` _function_

### `tests.test_conventions_resolve`

- `test_ambiguous_call_name_is_not_resolved` _function_
- `test_conventions_flags_undocumented_no_docstring_and_non_granular` _function_
- `test_conventions_ignores_non_notebook_symbols_and_respects_scope` _function_
- `test_resolve_cross_module_calls_and_imports` _function_

### `tests.test_devgraph`

- `test_cjm_dep_keys_strips_specifiers_and_filters` _function_
- `test_code_elements_ingests_graph_sourced_modules_from_the_journal` _function_ — N+3 Phase 2: a cut-over module's text comes from the SOURCE journal, not the file
- `test_compute_untested_flags_unlinked_public_symbols` _function_ — The untested audit: public package symbols without an incoming TESTS edge are
- `test_notebook_elements_decomposes_repo_notebooks` _function_
- `test_notebook_elements_ingests_graph_sourced_notebooks_from_the_journal` _function_ — The notebook authority flip: a cut-over notebook's cells come from the SOURCE
- `test_notebook_elements_scans_only_nbs_when_present` _function_ — quarto's `_proc` copies (and `dist/` etc.) share export targets with the real
- `test_notes_corpus_elements_permalink_identity_and_facets` _function_
- `test_repo_map_elements_entities_and_depends_on` _function_
- `test_test_elements_and_tests_edges` _function_ — Stage 1 of tests-on-graph: tests/ decomposes with repo-relative module identity,
- `test_test_elements_ingests_graph_sourced_test_module_from_journal` _function_ — Stage 2 of tests-on-graph: a cut-over test module ingests from the SOURCE journal

### `tests.test_display`

- `test_annotate_stamps_rule_output_but_never_overwrites_tier_one` _function_
- `test_first_clause_cuts_a_house_style_headline` _function_
- `test_first_clause_hard_truncates_when_no_boundary_fits` _function_
- `test_first_clause_short_text_passes_through` _function_
- `test_node_title_display_title_outranks_everything` _function_
- `test_node_title_falls_back_to_id` _function_
- `test_node_title_statement_gets_the_first_clause_trim` _function_
- `test_node_title_subject_label_rescues_a_factslot` _function_
- `test_parse_template_literals_props_edges` _function_
- `test_parse_template_neighbour_prop` _function_
- `test_parse_template_rejects_outside_the_frozen_grammar` _function_
- `test_render_composes_props_neighbour_titles_and_counts` _function_
- `test_render_display_rule_result` _function_
- `test_render_list_rows_show_gloss` _function_
- `test_render_missing_values_collapse_cleanly` _function_
- `test_set_display_rule_rejects_a_malformed_template_before_writing` _function_
- `test_set_display_rule_requires_at_least_one_template` _function_

### `tests.test_flip_to_py`

- `FakeGraph` _class_ — In-memory stand-in for the graph ops the flip verb touches.
- `test_a_later_source_op_revives_a_retired_key` _function_
- `test_flip_blocks_on_an_unretargetable_cell_ref_and_force_drops_loudly` _function_
- `test_flip_dry_run_touches_nothing` _function_
- `test_flip_refuses_a_nonexporting_notebook` _function_
- `test_flip_refuses_an_unsourced_notebook` _function_
- `test_flip_replays_curation_links_severed_by_the_subtree_swap` _function_
- `test_flip_retargets_a_link_onto_the_surviving_symbol` _function_
- `test_flip_to_py_end_to_end` _function_
- `test_import_clauses_see_same_root_submodule_imports` _function_ — The prune-report universe is per-CLAUSE: dropping one of two `import urllib.X`
- `test_notebook_to_py_source_keeps_exports_and_reports_the_rest` _function_
- `test_notebook_to_py_source_reports_a_nonexporting_notebook` _function_
- `test_retire_ends_a_source_key_and_source_check_forgets_it` _function_
- `test_retire_noops_on_an_unknown_key` _function_

### `tests.test_journal`

- `test_append_and_read_roundtrip` _function_
- `test_append_skips_exact_duplicate` _function_
- `test_journal_lines_are_valid_jsonl` _function_
- `test_journal_sourced_note_paths_matches_any_actor` _function_
- `test_link_append_roundtrip` _function_
- `test_link_verb_is_journaled` _function_
- `test_m3_baseline_import_emits_baseline_and_is_idempotent` _function_
- `test_new_note_verb_is_journaled` _function_
- `test_read_missing_journal_is_empty` _function_
- `test_render_link_human_and_error` _function_
- `test_render_reconcile_memory_human` _function_
- `test_render_structure_human` _function_
- `test_section_append_roundtrip_carries_replaces` _function_
- `test_section_verb_is_journaled` _function_

### `tests.test_listing`

- `test_list_graph_requires_exactly_one_mode` _function_
- `test_parse_where_and_true_total_render` _function_
- `test_render_list_error_and_empty` _function_
- `test_render_list_label_mode_with_paths` _function_
- `test_render_list_predicate_mode_shows_subject_value_actor` _function_
- `test_render_list_relation_mode_shows_src_target` _function_

### `tests.test_module_ops`

- `test_derive_import_name_strips_py_and_dots_path` _function_
- `test_rewrite_from_import_preserves_aliases` _function_
- `test_rewrite_from_import_repoints_module_keeping_names` _function_
- `test_rewrite_invalid_python_is_a_noop` _function_
- `test_rewrite_leaves_submodule_and_other_modules_alone` _function_
- `test_rewrite_parenthesized_from_import` _function_
- `test_rewrite_plain_import_and_alias` _function_

### `tests.test_projection_units`

- `test_facet_axis_value_kind_and_seed` _function_
- `test_facet_breakdown_counts_sorted_with_compound_handles` _function_
- `test_fine_tier_content_is_titled_and_searchable` _function_
- `test_load_seeds_how_to_query_overrides_per_key` _function_
- `test_locate_row_carries_id_label_title_path` _function_
- `test_node_summary_carries_description_and_kind` _function_
- `test_node_title_prefers_title_then_name_then_slug_then_id` _function_
- `test_render_coverage_by_kind_and_hub_handles` _function_
- `test_render_explore_complete_vs_refacet` _function_
- `test_render_locate_lists_matches_with_path_and_handles_empty` _function_
- `test_render_relevant_facets_bounded_even_with_giant_content` _function_
- `test_render_relevant_human_lists_results` _function_
- `test_render_schema_human_and_agent` _function_
- `test_render_show_surfaces_on_disk_path` _function_
- `test_section_body_is_searchable_and_summarised` _function_
- `test_short_caps_long_text_and_collapses_whitespace` _function_
- `test_terms_distinct_lowercase_len_gt_2` _function_

### `tests.test_readiness`

- `test_classify_gate_without_task_state_counts_as_unmet` _function_
- `test_classify_hidden_check_satisfies_gate_but_never_partitions` _function_
- `test_classify_hidden_open_check_still_blocks_a_gate` _function_
- `test_classify_open_with_done_gate_is_ready` _function_
- `test_classify_open_with_unmet_gate_is_blocked` _function_
- `test_classify_transitive_unlock_is_pure_recompute` _function_
- `test_classify_ungated_open_item_is_ready` _function_
- `test_render_readiness_caps_giant_decision_labels` _function_
- `test_render_readiness_drift_section_names_open_checks` _function_
- `test_render_readiness_empty` _function_
- `test_render_readiness_human_groups_and_flags_derived` _function_
- `test_render_readiness_marks_closable_and_dod_progress` _function_
- `test_render_readiness_without_checks_is_unchanged_shape` _function_
- `test_summarize_checks_all_done_is_closable_shape` _function_
- `test_summarize_checks_counts_and_absence_is_open` _function_

### `tests.test_readme`

- `test_readme_projection_structural` _function_
- `test_readme_uses_on_graph_purpose` _function_

### `tests.test_refactor`

- `test_consolidation_same_name_across_repos` _function_
- `test_dead_code_flags_uncalled_public_excludes_dunder_private_init` _function_
- `test_mutual_import_marks_relocation_cycle` _function_
- `test_same_name_within_one_repo_is_not_consolidation` _function_
- `test_scope_restricts_to_one_repo` _function_
- `test_single_consumer_cross_repo_is_relocation_not_cycle` _function_
- `test_split_divergent_vs_shared_neighborhood` _function_
- `test_within_repo_caller_is_not_relocation` _function_

### `tests.test_refactor_ops`

- `test_no_match_unchanged` _function_
- `test_other_module_import_untouched` _function_
- `test_rewrite_parenthesized_import` _function_
- `test_rewrite_preserves_alias` _function_
- `test_rewrite_sole_import` _function_
- `test_rewrite_splits_multi_name_import` _function_

### `tests.test_registers`

- `test_classify_active_member_without_cache_edge_is_missing` _function_
- `test_classify_contextual_reference_is_not_drift` _function_
- `test_classify_in_sync_register_reports_no_drift` _function_
- `test_classify_role_value_without_hub_is_hubless_counts_only` _function_
- `test_classify_superseded_member_still_cached_is_stale` _function_
- `test_render_register_drift_empty` _function_
- `test_render_register_drift_marks_sync_and_names_drift` _function_

### `tests.test_rename_ops`

- `test_decorator_reference_renamed` _function_
- `test_global_declaration_reaches_module_binding` _function_
- `test_import_rewrite_aliased_keeps_alias_and_signals_no_body_edit` _function_
- `test_import_rewrite_flags_qualified_use` _function_
- `test_import_rewrite_unaliased_reports_local_name_old` _function_
- `test_method_named_old_not_treated_as_module_global` _function_
- `test_nonascii_elsewhere_on_line_keeps_alignment` _function_
- `test_renames_class_site_and_base_and_annotation` _function_
- `test_renames_def_site_and_callers` _function_
- `test_self_recursion_renamed` _function_
- `test_skips_attribute_access` _function_
- `test_skips_comprehension_target_shadow` _function_
- `test_skips_keyword_argument_name_but_renames_value` _function_
- `test_skips_local_shadow_param_and_assignment` _function_
- `test_strings_and_comments_untouched` _function_
- `test_unrelated_name_untouched_byte_exact` _function_

### `tests.test_seeds_render`

- `test_conceptual_key_is_rename_stable` _function_
- `test_render_assert_conflict_warns` _function_
- `test_render_contradictions_human` _function_
- `test_seed_has_both_rename_contradictions_with_two_active_claims` _function_
- `test_term_slug` _function_

### `tests.test_source_state`

- `test_absorb_authored_text_canonicalizes_and_keeps_file_in_sync` _function_
- `test_append_source_dedups_identical_latest_state` _function_
- `test_canonical_emit_is_a_fixpoint` _function_
- `test_canonical_emit_notebook_is_a_fixpoint_and_strips_derived_state` _function_
- `test_cutover_flips_and_is_idempotent` _function_
- `test_cutover_refuses_a_drifted_file` _function_
- `test_cutover_regenerates_a_missing_artifact` _function_
- `test_cutover_requires_a_shadow_state` _function_
- `test_emit_source_artifact_restores_the_file_from_the_journal` _function_
- `test_flip_notebook_journals_canonical_cell_state` _function_
- `test_flip_notebook_rejects_malformed_json` _function_
- `test_flip_then_source_check_clean_when_file_canonical` _function_
- `test_notebook_walk_flip_emit_cutover_then_regen_gate` _function_
- `test_source_check_flags_out_of_band_file_drift` _function_
- `test_source_check_reports_phase_and_the_regen_gate` _function_
- `test_test_module_block_nested_closure_import_survives` _function_
- `test_test_module_canonical_emit_keeps_fixture_import_verbatim` _function_
- `test_test_module_flip_cutover_roundtrip_is_byte_clean` _function_

### `tests_manual.authoring`

- `main` _function_
- `part_a_b` _function_
- `part_c` _function_
- `part_d` _function_
- `part_e` _function_ — E. ROUND-TRIP a real memory note from the graph (read-only, real corpus).
- `part_f` _function_ — F. AUTHOR a memory section + EMIT to a temp `.md` (the SAME verb on a Section slot).
- `part_g` _function_ — G. Section-slot guards: refuse a non-lossless note (would truncate); OLD-match safety.

### `tests_manual.code_on_graph`

- `gx_schema` _function_
- `main` _function_

### `tests_manual.divergence_probe`

- `main` _function_

### `tests_manual.inc3_first_slice`

- `check` _function_
- `main` _function_
- `run` _function_

### `tests_manual.inc4_dogfood`

- `check` _function_
- `main` _function_
- `run` _function_

### `tests_manual.journal_rebuild`

- `check` _function_
- `main` _function_
- `run` _function_

### `tests_manual.m2b_shadow`

- `main` _function_
- `part_a` _function_
- `part_bcd` _function_

### `tests_manual.m3_cutover`

- `check` _function_
- `main` _function_
- `run` _function_

### `tests_manual.module_ops`

- `main` _function_
- `scenario_delete` _function_
- `scenario_regroup` _function_
- `scenario_rename` _function_

### `tests_manual.move`

- `main` _function_

### `tests_manual.rename`

- `main` _function_
- `scenario_rename_class` _function_
- `scenario_rename_function` _function_

### `tests_manual.session_start_sequence`

- `check` _function_
- `main` _function_
- `run` _function_

### `tests_manual.structure`

- `main` _function_
- `part_a` _function_
- `part_b` _function_
- `part_c` _function_
- `part_d` _function_
- `part_e` _function_ — The DURABILITY fix (approach A): a journaled `add-section` op is re-spliced on rebuild, so

### `tests_manual.viz`

- `main` _function_
- `part_a` _function_
- `part_b` _function_
- `part_c` _function_

## Dependencies

**Depends on:** `cjm-dev-graph-schema`, `cjm-markdown-decompose-core`, `cjm-notebook-decompose-core`, `cjm-python-decompose-core`, `cjm-substrate`
**Used by:** `cjm-notebook-decompose-core`
