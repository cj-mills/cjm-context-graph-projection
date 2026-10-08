# cjm-context-graph-projection

<!-- generated from the context graph by `cjm-context-graph readme` — do not edit by hand; edit the graph (the urge to hand-edit = move it on-graph) -->

Projection and navigation core for context graphs: bounded, ranked, provenance-carrying reads (schema / state / relevance / show) over any cjm-substrate context graph, with a CLI driver. The agent-facing read layer of the self-hosting graph arc.

## Modules

- **`cjm_context_graph_projection.__init__`**
- **`cjm_context_graph_projection.aboutpage`** — The ABOUT PAGE: the author's identity, background and how the site is made -- never an offer
- **`cjm_context_graph_projection.agentlayer`** — The post page's AGENT LAYER (design 39c51c15 (7), amendment 23a49667, under the redesign build
- **`cjm_context_graph_projection.archive`** — Retiring an archive source, restoring it from git, and moving a page's path between holders
- **`cjm_context_graph_projection.artifacts`** — Observed-source ARTIFACTS (design 9a7224a7, work item 4765b699): files the graph VERSIONS
- **`cjm_context_graph_projection.authoring`** — The B write surface: AUTHOR a verbatim-text slot on-graph, emit the canonical artifact.
- **`cjm_context_graph_projection.categories`** — A post's CATEGORIES: the one reader every renderer calls (design ce17606b (1) under the category
- **`cjm_context_graph_projection.categorypages`** — CATEGORY PAGES (design a62f2499 under the category model 0f7fcdcb (5), work item 779c7a79).
- **`cjm_context_graph_projection.claims`** — The claims the site may make about the user's work, and the deliverables that back them
- **`cjm_context_graph_projection.cli`** — The `cjm-context-graph` CLI — first driver of the projection core.
- **`cjm_context_graph_projection.code_edges`** — Orphaned code-target edge detector: journaled links whose endpoint no longer resolves.
- **`cjm_context_graph_projection.codefold`** — The code lane as a FOLD over the source journal (design amendment 2cc81d3b to 8f6f2343).
- **`cjm_context_graph_projection.cohesion`** — Module cohesion audit over the code graph — the read-only cohesion ORACLE (N+1).
- **`cjm_context_graph_projection.comments`** — The post page's comments (design 39c51c15 (1), rulings 98d33f9e (2) + 86f4a34d, under the
- **`cjm_context_graph_projection.config`** — Graph-sibling config discovery — the DEFAULT_* hardcodes retired to DATA
- **`cjm_context_graph_projection.contradictions`** — The standing dedup query: slots whose ACTIVE assertions disagree.
- **`cjm_context_graph_projection.conventions`** — Structural convention audit over the code/notebook graph (the enforcement nbdev lacks).
- **`cjm_context_graph_projection.coverage`** — The Tutorials matrix: its vocabulary as graph data and the task x stage projection
- **`cjm_context_graph_projection.derivedblocks`** — Derived blocks leave the RENDER; the post navigation replaces them (design 253ac996, amendment
- **`cjm_context_graph_projection.devgraph`** — Build the dev graph's nodes + edges from its sources (the dev-graph DRIVER).
- **`cjm_context_graph_projection.display`** — Graph-carried display rules: the presentation vocabulary (DEC `16bcd96e`).
- **`cjm_context_graph_projection.explorer_page`** — The graph EXPLORER client page — the first client of the `serve` data API.
- **`cjm_context_graph_projection.facetjudge`** — The facet judge (design eefda2dd under the category model 0f7fcdcb and amendment 3c5cff97).
- **`cjm_context_graph_projection.facetreview`** — The facet review and the public build's facet gate (design eefda2dd (5), (7)).
- **`cjm_context_graph_projection.factlayer`** — Shared fine-tier reads over the fact-layering schema (slots + assertions).
- **`cjm_context_graph_projection.filing`** — Filing reconciler: propose PART_OF program anchors for unfiled work items.
- **`cjm_context_graph_projection.gitfold`** — Ingested sources' times from git history (design amendment 19edbe97 to 8f6f2343; leg C 7ddcea72).
- **`cjm_context_graph_projection.homepage`** — The HOME PAGE: a projected map of the site under its role and what it holds (design e55201e2,
- **`cjm_context_graph_projection.hybrid_page`** — The HYBRID graph explorer client — GPU physics canvas + DOM overlay (check-in 1233ab46).
- **`cjm_context_graph_projection.journal`** — The write journal: the durable, replayable source of truth for born-on-graph writes.
- **`cjm_context_graph_projection.judgeengine`** — The judge verb family's shared engine (design eefda2dd (8), capture e0b6f945 condition 1).
- **`cjm_context_graph_projection.judging`** — Judged related posts (design e09e262b, answering ruling 98d33f9e (1); the spike 245fb5b3).
- **`cjm_context_graph_projection.lens`** — Lenses: graph-carried, parameterized views (DEC `f1b02b95` — tier 2 of the
- **`cjm_context_graph_projection.library`** — The Library's provenance (design 5de7fae9, design leg 4a4ef27e).
- **`cjm_context_graph_projection.librarypage`** — The Library's pages: the index a Lens with the `library` view layout projects, and a work page
- **`cjm_context_graph_projection.linkaudit`** — Link liveness audit — the derived worklist for the EXTERNAL links a note carries
- **`cjm_context_graph_projection.listing`** — Structured enumeration: every node of a LABEL / assertion of a PREDICATE / edge of a RELATION.
- **`cjm_context_graph_projection.module_ops`** — Module-edit ops — create / rename / delete / regroup a module as graph edge ops.
- **`cjm_context_graph_projection.notes_outline`** — The OUTLINE PASS of the notes lane — the first of the standalone lecture resource's passes
- **`cjm_context_graph_projection.notes_place`** — The PLACEMENT PASS of the notes lane — the second of the standalone lecture resource's
- **`cjm_context_graph_projection.onboarding`** — Project the MEMORY onboarding surface from the graph's ASSERTED lead structure.
- **`cjm_context_graph_projection.oracle`** — The version oracle: a programmatic Procedure that keeps `version` slots fresh.
- **`cjm_context_graph_projection.paths`** — The path model (design ae698640, the walk 57287d1b, the refactor leg ad9bef5a): artifacts,
- **`cjm_context_graph_projection.postpage`** — The post page's projected parts (the post page of de808eae (2); its rest designed as 39c51c15
- **`cjm_context_graph_projection.projection`** — The projection core: schema / show / relevance / state over a context graph.
- **`cjm_context_graph_projection.propose`** — Triage proposals: an agent DRAFTS the update for a stale deliverable (work item bb015d12).
- **`cjm_context_graph_projection.prose_refs`** — Prose-ref drift: id-shaped tokens in asserted prose vs the edge layer.
- **`cjm_context_graph_projection.pull_transcript`** — The transcript pull verb: harness-transcript messages onto the session spine.
- **`cjm_context_graph_projection.purenotes`** — The pure-notes lane (ruling a7262fe7; work item fdafeed9): a typed deliverable whose
- **`cjm_context_graph_projection.readiness`** — The readiness frontier: which work-items are READY vs BLOCKED — derived, never stored.
- **`cjm_context_graph_projection.readme`** — README-as-projection (v1, STRUCTURAL-ONLY): generate a repo's README FROM THE GRAPH.
- **`cjm_context_graph_projection.reads`** — The content-access READS ledger: which nodes each read delivered into context.
- **`cjm_context_graph_projection.rebuilddiff`** — A live graph against its own rebuild, property for property (design 8f6f2343, finding fbce0173).
- **`cjm_context_graph_projection.reconcile`** — M2b shadow-phase RECONCILE — surface + (explicitly) absorb out-of-band `.md` edits.
- **`cjm_context_graph_projection.refactor`** — Refactoring-candidate identification over the code graph (the IDENTIFY half of move).
- **`cjm_context_graph_projection.refactor_ops`** — `move` — relocate a symbol between modules (the EXECUTE half of refactor-candidates).
- **`cjm_context_graph_projection.registers`** — Register drift-check: each hub note's member-cache vs the active `role` assertions.
- **`cjm_context_graph_projection.relive`** — The LIVE half of the code fold (design amendment 2cc81d3b, build B2 of leg B 0e3508fd).
- **`cjm_context_graph_projection.rename_ops`** — Symbol `rename` — the Ext-B increment: scoped identifier substitution INTO bodies.
- **`cjm_context_graph_projection.render`** — Render projection results for a consumer: agent (JSON) or human (markdown).
- **`cjm_context_graph_projection.review`** — The review frontier: which APPROVED deliverables have stale upstream — derived, never stored.
- **`cjm_context_graph_projection.runtime`** — Open a context graph for reading/writing (domain-neutral runtime wiring).
- **`cjm_context_graph_projection.scratchpad_export`** — Scratchpad session .md exporter — the projection lens (increment iv of the
- **`cjm_context_graph_projection.seeds`** — Hand-seeded load-bearing slots + the rename-stable repo-key machinery.
- **`cjm_context_graph_projection.series`** — Series born on-graph: the node, its membership and its ORDER as journaled intent
- **`cjm_context_graph_projection.serve`** — A served, read-only graph EXPLORER data API over the read verbs — the richer-viz INSTRUMENT.
- **`cjm_context_graph_projection.site`** — The public site's BUILD: one verb, run where the graph is (ruling 941f7f13; DEC 98293e72 (2)).
- **`cjm_context_graph_projection.sitefeeds`** — The site's FEEDS, written by the build after the render (design 0efb5497 (3b) under the web
- **`cjm_context_graph_projection.sitelinks`** — In-body site links, RESOLVED through site_path facts (DEC 72d669c5 (1); the step, 9ee4e346).
- **`cjm_context_graph_projection.sitelisting`** — The site's PROJECTED LISTING: one component for every listing (design 0efb5497 + amendment
- **`cjm_context_graph_projection.sitepages`** — The site's PROJECTED pages: every series page from its Series, every topic page from its Lens
- **`cjm_context_graph_projection.sitetheme`** — The site's THEME, projected from the design system its profile is bound to (leg C of the
- **`cjm_context_graph_projection.source_state`** — N+3 Phase 1 (SHADOW): capture a module's canonical source into a SOURCE journal and
- **`cjm_context_graph_projection.sourcemoves`** — Which rows a moved source accounts for (design amendment a9176261 to 19edbe97 (6)).
- **`cjm_context_graph_projection.sources`** — The post page's sources (design 39c51c15 (3), amendment 722a8232, under the redesign build
- **`cjm_context_graph_projection.structure`** — M2a GRADIENT — structural memory authoring: create a note / add a section, born on-graph.
- **`cjm_context_graph_projection.tutorialspage`** — The Tutorials page: a Lens whose view layout is `coverage-matrix` projects the task x stage
- **`cjm_context_graph_projection.viz`** — A minimal READ-ONLY visualization: the readiness frontier + its dependency DAG, as HTML.
- **`cjm_context_graph_projection.workbench`** — Workbench lens layer: the front-door / pin-tree / session-feed derived views.
- **`cjm_context_graph_projection.worklist`** — The propose/confirm worklist: candidate fixes that need a human decision.
- **`cjm_context_graph_projection.write`** — The write surface: `assert` a slot value, `decide` a conclusion.

## API

### `cjm_context_graph_projection.aboutpage`

- `about_body` _function_
- `background_ref` _function_ — The background: the one ref of the selection's one `subgraph` clause. Any other shape
- `body_of` _function_
- `load_author_extras` _function_
- `load_portrait` _function_ — The displayed portrait (`site-author.portrait`, 2fba772c (4)): a derivative of the photo,
- `load_site_config` _function_
- `plan_about_page` _function_ — Plan the About page: the opening, the portrait, the background under its approval, the

### `cjm_context_graph_projection.agentlayer`

- `agent_plan` _function_ — The agent layer's plan: each licensed post's JSON-LD and llms.txt. A page's title and
- `based_on` _function_ — What the post draws on, as structured data (design 37f82f72 (5)): a work typed by its form,
- `build_lines` _function_ — What the build knows and the author never types: how the links work, the licenses from the
- `check_jsonld` _function_ — After the render: every planned post page carries exactly one JSON-LD object, equal to the
- `check_llms_fragments` _function_ — After the anchors (design b82d2a98 (2)), failing closed: every fragment a `.llms.md` links
- `directory_author` _function_ — The page's author as Quarto merges it: its own front matter, else the nearest directory's
- `jsonld_script` _function_
- `llms_index` _function_ — llms.txt (amendment 23a49667 (2), 465ab923): the author's intro, the build's own lines,
- `llms_path` _function_
- `load_index_copy` _function_ — llms.txt's intro copy: the summary is the site's one sentence (`site-summary`, amendment
- `map_outside_code` _function_ — The ONE walker of the markdown layer's code regions (design b82d2a98 (3)), shared by the
- `plain` _function_ — A description in llms.txt or JSON-LD is text: the page's link is the only link there, and a
- `post_jsonld` _function_ — A post's structured data (39c51c15 (7), amendment 23a49667 (4)): a draft has no
- `profile_jsonld` _function_ — The About page's ProfilePage, its Person the site author (design ff0c6338 (8)): the name and
- `read_jsonld` _function_
- `restore_llms_anchors` _function_ — The markdown layer carries the ids its page carries (design b82d2a98 (1)): each anchor
- `rewrite_llms_links` _function_ — Keep an agent in the markdown layer (amendment 23a49667 (3)): every internal link in a
- `rewrite_markdown_links` _function_ — Rewrite each link target `resolve` maps, outside fenced code and inline code spans.
- `state_category_links` _function_ — The markdown layer STATES a category, never links the category listing's filtered view
- `write_llms_txt` _function_ — Write llms.txt over Quarto's flat list -- only when every link names a `.llms.md` the

### `cjm_context_graph_projection.archive`

- `git_blob` _function_
- `git_head` _function_
- `is_retired` _function_ — A retired node is never listed, linked to or rendered, under any profile.
- `path_owners` _function_ — The page each site_path value now belongs to: follow the supersession chain UP from the
- `restore_retired` _function_ — Rebuild each retired archive source from git, under its original path and slug. A missing
- `retire_source` _function_ — Retire an archive source (journaled `retire-source`). LIVE, the source must be committed
- `retired_sources` _function_ — What the ingest restores before replay: every archive source a retire op names.
- `transfer_site_path` _function_ — Move a page's ACTIVE site_path to another node (journaled `transfer-path`): the target

### `cjm_context_graph_projection.artifacts`

- `ArtifactFold` _class_ — The artifact lane folded over the source journal: one node per live artifact identity,
- `ArtifactKind` _class_ — One kind of observed artifact: where its files sit, how a file reads canonically (and
- `append_artifact` _function_ — Append an `artifact` record, skipping a capture identical to the key's latest state.
- `append_artifact_retire` _function_ — Append an `artifact-retire` record ending a key's life.
- `apply_artifacts_live` _function_ — THE SAME FOLD a rebuild runs, committed in place: artifact nodes the fold no longer
- `artifact_check` _function_ — Every live artifact against its file: DRIFT = the file's canonical text is not the
- `capture_artifact` _function_ — Capture the file's current version (validated, canonical) or retire its key, then the
- `fold_artifacts` _function_
- `latest_artifact_ops` _function_ — The LATEST capture per live key (last record wins; a retire ends the key).
- `uncaptured_artifacts` _function_ — Every file a kind's pattern matches, in every on-graph repo, that no record holds.

### `cjm_context_graph_projection.authoring`

- `add_symbol` _function_ — Mint a NEW top-level CodeSymbol into a module, then emit its canonical artifact.
- `add_text` _function_ — Mint a NEW CodeText region (imports/constants/docstring/`__all__`) into a module, then emit.
- `author` _function_ — Author a node's verbatim-text slot, then emit its canonical artifact to disk.
- `emit_artifact` _function_ — Emit a container's canonical artifact FROM THE GRAPH (graph -> .py / .ipynb / .md).
- `emit_post` _function_ — Emit a born post to the PUBLIC website clone — GATED on publish_state=published.
- `file_section_raws` _function_ — Each of a note's sections' `raw` span as the FILE currently decomposes (the other
- `graph_section_raws` _function_ — Each of a note's sections' on-graph `raw` span, keyed by anchor (the divergence/
- `note_approval` _function_ — A born Note's approval, the one reading every public consumer of a born Note shares
- `read_node` _function_ — Deliver a node's verbatim CONTENT — the read DUAL of `author`/`emit`.
- `read_slot` _function_ — Read a node's current verbatim-slot text (the `--editor` pop / preview input).
- `reharvest_note_relations` _function_ — Re-run the relationship harvest on an EDITED note and apply the edge DIFF (finding cbde404c).
- `section_divergence` _function_ — Read-only: detect, at SECTION grain, where a note's `.md` has drifted from the graph.

### `cjm_context_graph_projection.categories`

- `chip_vocab` _function_ — Each live facet entry's chip text and its place in the chip order.
- `load_post_categories` _function_ — Every post's chips from the graph (a post with none is absent), each chip's place in the
- `majority` _function_ — A collection page's chips (design ce17606b (4)): what most of it is about -- the
- `post_chips` _function_

### `cjm_context_graph_projection.categorypages`

- `category_page_min` _function_ — The index Lens's active `category_page_min` (a62f2499 (3)): one positive integer.
- `category_slug` _function_
- `describe_categories` _function_ — The description verb (amendment e38d403c (5)): the document, or one landed review -- the
- `description_basis` _function_
- `description_criteria` _function_
- `description_document` _function_
- `description_stale` _function_
- `description_state` _function_ — Every category page the PUBLIC build earns, in the index's order, with the posts it lists
- `entry_record` _function_
- `index_body` _function_
- `index_hubs` _function_ — The category pages the index links, in its order (the home page's map entry, 5c3c2662 (5)).
- `parse_descriptions` _function_ — Each section's entry, basis and description (whitespace collapsed to single spaces). A
- `plan_category_pages` _function_ — Plan the category index and every category page, and the redirect stub of every entry
- `plan_category_paths` _function_ — The `site_path` each live facet entry should hold (a62f2499 (1)): `/categories/<slug>/`,
- `plan_descriptions` _function_ — Check every section against the graph (a live facet entry, its record unchanged since the

### `cjm_context_graph_projection.claims`

- `claims_report` _function_ — The claims over the live graph: every claim Entity with its state and backing.
- `load_claim_states` _function_ — Every claim's active state(s); more than one is a conflict the report refuses.
- `load_claims` _function_ — The claim Entities in display order.
- `load_supports` _function_ — The support edges (deliverable -> claim) with their kind and note.
- `project_claims` _function_ — The pure projection (no graph access): see the module docstring for the rules.
- `public_view` _function_ — The public profile's filter (676bac8e (4)): building and retired claims, and supports
- `record_support` _function_ — Write one SUPPORTS edge with its kind (journaled `supports`), or retract it. A pair has

### `cjm_context_graph_projection.cli`

- `main` _function_
- `render` _function_ — The read-delivery seam: tap the reads ledger, then delegate to the real

### `cjm_context_graph_projection.code_edges`

- `classify_orphaned_links` _function_ — Pure: the journaled links the next replay will silently drop.
- `orphaned_edges` _function_ — The derived orphan report over journal `link` ops + the current graph.

### `cjm_context_graph_projection.codefold`

- `CodeFold` _class_ — The projected code corpus as a fold over source-journal records (see module docstring).
- `fold_source_journal` _function_ — Fold every record of the source journal, in append order (the rebuild's code lane).

### `cjm_context_graph_projection.cohesion`

- `cohesion` _function_ — Audit module cohesion: grab-bag (under_split) + scattered-helper (over_split) candidates.
- `compute_cohesion` _function_ — Compute module cohesion candidates from the code graph slices (pure).

### `cjm_context_graph_projection.comments`

- `apply_harvest` _function_ — Land one harvest: each Note's winning thread stands (superseding a different active
- `fetch_threads` _function_ — Every comment thread on the repo: the utterances issues, and the discussions of the
- `gh_graphql` _function_ — One GraphQL request through the GitHub CLI (its own authentication).
- `harvest_discussions` _function_ — The harvest verb: read every comment thread, map it to a Note, land the facts. All or
- `load_comments_config` _function_ — The widget's settings from the site config (`post-comments`) and the site's title (the
- `load_threads` _function_ — Every Note's comment threads: the active discussion fact and the superseded ones.
- `map_threads` _function_ — Each thread's Note: an authored map wins; a path-era title by the page's site_path
- `page_comments` _function_ — What a page's block loads: its one active thread, else its canonical path as the term.
- `path_key` _function_ — A pathname (utterances, 'posts/x/' or 'posts/x/index') or a giscus term ('/posts/x/')
- `plan_threads` _function_ — Per Note, the thread with the MOST comments stands; the others are earlier threads
- `render_comments` _function_ — The comments block: the questions line, the earlier threads, and the giscus widget --
- `thread_url` _function_ — A thread's page: comment threads are discussions once the publish converts them.

### `cjm_context_graph_projection.config`

- `load_graph_config` _function_ — Read the graph-sibling config. Absent = {} (fallback to DEFAULT_*);
- `sibling_graphs` _function_ — The `sibling_graphs` registry as DATA: which other graphs a `<key>:<id>` reference

### `cjm_context_graph_projection.contradictions`

- `contradictions` _function_ — All slots whose active assertions form a hard contradiction (optionally scoped).

### `cjm_context_graph_projection.conventions`

- `compute_conventions` _function_ — Compute convention findings from CodeSymbol nodes + the documented-id set (pure).
- `compute_untested` _function_ — The untested-symbol audit (pure): every public top-level PACKAGE symbol (test
- `conventions` _function_ — Audit notebook-sourced symbols for missing prose/docstrings + non-granular cells,

### `cjm_context_graph_projection.coverage`

- `check_coverage_value` _function_ — A coverage value must name a live vocabulary entry of the predicate's kind, so a
- `coverage_matrix` _function_ — The Tutorials matrix over the graph: every deliverable whose type's kind is
- `field_format_error` _function_ — The formats some fields carry beyond their type (design leg 4a4ef27e): `published` is an
- `load_coverage_facts` _function_ — Every subject's ACTIVE coverage values (both predicates, supersession applied).
- `load_hardware` _function_ — Every hardware Entity with its ACTIVE standing (None if never asserted; two active
- `load_verifications` _function_ — The verification edges (deliverable -> device) with their evidence.
- `load_vocab` _function_ — The live (unretired) vocabulary of both axes, each entry its properties plus `id`.
- `mint_entities` _function_ — A vocabulary batch (amendment 3c5cff97), checked WHOLE before anything lands, as the
- `mint_entity` _function_ — Mint or update a typed Entity from its WHOLE record (journaled `entity`; upsert by
- `project_matrix` _function_ — The pure projection (no graph access): see the module docstring for the rules. A filter
- `record_verification` _function_ — Write one VERIFIED_ON edge with its evidence (journaled `verified-on`), or retract it.
- `resolve_deliverable` _function_ — Resolve a deliverable argument to its Note id (an id first, then a slug).
- `validate_entity` _function_ — Check one `entity` record against its kind's declared fields.

### `cjm_context_graph_projection.derivedblocks`

- `check_derived` _function_ — After the render: every planned post reported, each named block dropped exactly once, and
- `check_end_placement` _function_ — After the render: every post's end matter -- the author strip, and the comments and
- `derived_plan` _function_ — Plan every rendered post's drops and navigation. A derived block with no link target
- `render_nav` _function_ — The post navigation: the series position, then the collections line, as simple callouts.
- `write_derived` _function_ — Write the data file and the filter (only what changed) and clear the report.

### `cjm_context_graph_projection.devgraph`

- `ArchiveSource` _class_ — The archive's kept paths and their decomposition — ONE definition for the ingest that
- `build_dev_graph_elements` _function_ — Assemble the full dev graph: memory notes (+ refs), the repo map (+ deps),
- `memory_elements` _function_ — Decompose every memory markdown file (except MEMORY.md) into graph elements.
- `notes_corpus_elements` _function_ — Decompose a git-held `<dir>/index.md` / `index.qmd` markdown corpus into graph elements.
- `parse_source_id` _function_
- `repo_entity` _function_
- `repo_map_elements` _function_ — One repo Entity per cjm-* repo (RENAME-STABLE keys) + DEPENDS_ON from pyproject.
- `site_page_slug` _function_ — A site page's slug — "about.qmd" -> "about", "series/notes/index.md" -> "series/notes";
- `source_id` _function_ — An ingested git source's id (DEC a9176261): its kind and where it lives — the root a
- `stamp_note_profile` _function_ — Record the relationship-harvest profile on every Note wire dict (in place).

### `cjm_context_graph_projection.display`

- `Displayer` _class_ — The rule interpreter: loads a graph's DisplayRules once, then batch-annotates.
- `annotate_display` _function_ — Load this graph's rules + annotate `nodes` (the one-call seam for read verbs).
- `display_rule_node_id` _function_ — Deterministic DisplayRule id — one rule per kind, so re-authoring converges.
- `first_clause` _function_ — A long statement's leading clause — the Decision-title extractor.
- `node_title` _function_ — Best display label for a node: the stored/cascade tiers of the resolution order.
- `parse_template` _function_ — Parse a display template into literal / property / edge parts.
- `set_display_rule` _function_ — Author/update the graph-carried DisplayRule for a kind (presentation vocabulary).

### `cjm_context_graph_projection.facetjudge`

- `applies` _function_ — Task and stage entries are asked of non-tutorial posts only (eefda2dd (4)).
- `apply_facet_run` _function_ — Land one facet run: each asked pair's standing judgment is replaced by the run's (a post
- `code_signals` _function_ — What the post's code says about its tools, read from its fenced blocks.
- `criteria_hash` _function_ — The hash of an entry's Noul as asked: the entry's fields and its kind's text together.
- `entry_id` _function_
- `entry_question` _function_ — One entry's Noul: its kind's instructions naming it, its description and not-for line as criteria.
- `facet_view` _function_ — What the facet judge sees of a post -- and what its staleness is measured against.
- `facets_stale` _function_ — The public posts with missing or stale facet judgments.
- `is_facet_entry` _function_ — A live entry of a facet kind that is not a matrix-structural task row: the cross-task row
- `judge_facets` _function_ — The facet judge verb: find the stale pairs, ask one request per post, land the run.
- `load_facet_judgments` _function_ — Every stored facet judgment.
- `load_facet_records` _function_ — What each judged post's facet judgments were made against (`_value` = the fact's text).
- `load_facet_views` _function_ — The facet judge's state of every public post, from the graph (the audience rule: only
- `load_facet_vocab` _function_ — Every entry of the five facet kinds the judge asks (is_facet_entry).
- `measure` _function_ — The measuring pass's report (eefda2dd (6)), from the stored judgments: per entry the posts
- `opening_prose` _function_ — The post's opening prose, whitespace collapsed.
- `post_outline` _function_ — A post's section outline for a judged state: each content section's title, links reduced
- `record_value` _function_ — The canonical JSON of a post's record (key-sorted, compact).
- `request_body` _function_
- `review_basis` _function_ — A pair's basis: the post's judged state and the entry's criteria. A mark whose basis is
- `run_facets` _function_ — One request per post with a Noul per entry; a response missing an asked entry is a failure.
- `stale_pairs` _function_ — A pair is stale when its post has no record, the post's state moved, or the record holds

### `cjm_context_graph_projection.facetreview`

- `apply_review` _function_ — Land one review: assert each confirmation (checked at write time like any facet), then set
- `facet_gate` _function_ — The public build's facet gate (eefda2dd (7)): the public posts with stale pairs, the ones
- `load_confirmed` _function_ — Every post's ACTIVE confirmed facets (supersession applied).
- `parse_review` _function_ — The document's rows; a repeated pair is an error (the file is refused whole).
- `plan_review` _function_ — Check every row against the graph (its pair fresh, its basis current, its edge present) and
- `review_document` _function_ — The review document, grouped by entry, each entry's rows by p (module docstring).
- `review_facets` _function_ — The review verb: the document (counts and stale pairs alongside), or one landed review.
- `review_state` _function_ — Every fresh pair's review row (module docstring), the stale pairs apart, and the reads

### `cjm_context_graph_projection.factlayer`

- `active_assertions` _function_ — The active assertions in a slot under append-only supersession.
- `alias_index` _function_ — Build the entity alias index + an id->entity lookup (rename-stable subjects).
- `count_label` _function_ — Count nodes of a label (optionally predicate-filtered) — `NodeQuery(count=True)`.
- `group_by_slot` _function_ — Group assertion nodes by their `slot_id` property.
- `label` _function_ — A node's label / kind (typed GraphNode or wire dict) — the sibling of `nid`/`props`.
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

### `cjm_context_graph_projection.filing`

- `classify_filing` _function_ — Pure: partition open items into filed/unfiled and score anchor proposals.
- `derive_anchors` _function_ — The program-anchor set: subjects whose ACTIVE `role` is one of ANCHOR_ROLES.
- `filing` _function_ — The derived filing report over task_state subjects + PART_OF/REFERENCES/SHAPES edges.
- `near_duplicate_scores` _function_ — IDF-weighted token-set cosine between a new statement and existing items.
- `near_duplicates` _function_ — Mint-time near-duplicate proposals over the OPEN work-item population.

### `cjm_context_graph_projection.gitfold`

- `ElementFold` _class_ — Element times over file versions: runs by holder count, content changes by wire.
- `FoldedHistory` _class_ — One work tree's kept paths folded to HEAD: the HEAD-state per-path payloads + the times.
- `blobs_at` _function_ — Several paths' contents at one commit through one ls-tree + one cat-file batch.
- `cjm_dep_names` _function_ — The cjm-* dependencies a pyproject declares (an unparseable file declares none).
- `commit_exists` _function_
- `fold_history` _function_ — Walk the kept paths' versions, decompose each, fold the element times, check the end
- `git_toplevel` _function_ — The work tree `path` belongs to (None = no git history: no durable time).
- `git_versions` _function_ — Every commit in HEAD's ancestry, reverse topological order, with the kept paths it set.
- `head_commit` _function_
- `head_tree` _function_ — Every file HEAD holds.
- `paths_between` _function_ — What moved between two commits of one source (renames are a delete + an add: identity
- `read_blobs` _function_ — Many blobs through one `git cat-file --batch`.
- `root_commit_time` _function_ — When a repo began: its root commit (the earliest, when several histories were joined).
- `unquote_path` _function_ — Undo git's C-style path quoting (core.quotePath=false leaves UTF-8 bare, not `"` / `\`).
- `worktree_changes` _function_ — What HEAD does not carry: reported by the ingest, never read from the tree.

### `cjm_context_graph_projection.homepage`

- `entry_anchor` _function_
- `entry_refs` _function_ — The map's entries in their order: the refs of the selection's `subgraph` clauses, in turn.
- `home_body` _function_
- `map_entries` _function_ — Each named page as a map entry (the module docstring's (2)). An entry's `form` names its
- `plan_home_page` _function_ — Plan the home page: the identity, the map, the jump links, the band.
- `post_line` _function_
- `recent_posts` _function_

### `cjm_context_graph_projection.journal`

- `journal_sourced_note_paths` _function_ — The memory `.md` files `ingest` must NOT read — they're journal-sourced now.
- `journal_touch_rows` _function_ — A journal's touch rows across its whole segment family (cold first,
- `journal_window` _function_ — The journal-window projection: which nodes a window/session touched, when, how.
- `journal_window_view` _function_ — The SESSION LENS read verb: `journal_window` + graph join (title/label per ref).
- `m3_baseline_import` _function_ — One-time M3 GENESIS IMPORT: emit a per-note `new-note` baseline op into the journal.
- `node_journal_trace` _function_ — One node's journal TRACE: created/updated + session keys + actors (axis D).
- `replay_journal` _function_ — Re-apply every journaled write through its core verb (idempotent).
- `touched_node_ids` _function_ — Best-effort node refs a journaled op touched — the session-lens feed (2f51ff5d).

### `cjm_context_graph_projection.judgeengine`

- `digest` _function_ — The hash staleness is keyed by: key-sorted JSON, so equal content hashes equal.
- `http_ask` _function_ — The HTTP judge: one POST per request, retried with backoff on overload and network faults.
- `normalized` _function_ — The journal writes ops `sort_keys`, so replay rebuilds every dict key-sorted: live and
- `post_sections` _function_ — Every Note's sections in order (HAS_SECTION), for a family's judged state to read from
- `public_posts` _function_ — The posts a judge may be shown (the audience rule e1fd4d64: only public posts are sent).
- `read_key` _function_ — The key from KEY_ENV, else KEY_FILE -- never printed, never journaled.
- `resolve_ask` _function_ — The judge a run asks: the one given, else HTTP with the key -- or the reason there is none.
- `run_requests` _function_ — Ask every request on a worker pool; every answer is kept (the family applies its floor).

### `cjm_context_graph_projection.judging`

- `apply_judgments` _function_ — Land one judge run: every stored judgment touching a re-judged post is replaced by the
- `judge_pairs` _function_ — Both directions for every stale post, against every other judged post.
- `judge_related` _function_ — The judge verb: find the stale posts, judge every pair touching them, land the run.
- `judgment_of` _function_ — The judgment as the edge stores it, from the service's typed answers.
- `load_judged` _function_ — Every stored judgment.
- `load_post_views` _function_ — The judged state of every public post (the audience rule: only public posts are sent).
- `load_records` _function_ — What each judged post's judgments were made against.
- `post_view` _function_ — What the judge sees of a post -- and what its staleness is measured against. An empty
- `question_hash` _function_ — The questions' identity: a changed question makes every judgment stale.
- `related_stale` _function_ — The public posts whose related judgments are missing or stale -- the build's report.
- `run_judge` _function_ — Ask the judge about every pair; every answer is kept (the caller applies the floor).
- `stale_posts` _function_ — A post is stale when it has no record or its record names another state or question.
- `state_hash` _function_ — A post's judged-state identity.

### `cjm_context_graph_projection.lens`

- `apply_lens` _function_ — APPLY a lens: bind params -> run each selection clause through the real
- `bind_params` _function_ — Bind an application's params: defaults + provided, typed, loud on gaps.
- `lens_count` _function_ — A number a page projected from a Lens reads off its Lens (a62f2499 (3), 5c3c2662 (5)):
- `lens_node_id` _function_ — Deterministic Lens id — one lens per slug, so re-authoring converges.
- `load_lenses` _function_ — Every well-formed Lens on this graph (the shelf feed), slug-sorted.
- `set_lens` _function_ — Author/update a graph-carried Lens (journaled upsert-by-slug).
- `validate_lens_spec` _function_ — Parse-validate a lens spec against the v1 shape; a bad spec NEVER lands.

### `cjm_context_graph_projection.library`

- `check_survey_targets` _function_ — The graph half of the survey check, run before anything is written: each row's
- `library_index` _function_ — Load what the Library derives from and project it: the Library entities, the asserted
- `load_library_entities` _function_ — Every work, unit and output-class Entity on the graph, keyed by sub-kind then key.
- `plan_survey` _function_ — The pure plan of a survey batch: the works and units to mint and the provenance edge per
- `project_library` _function_ — The Library as data, deriving everything and storing nothing (design 5de7fae9 (3), leg
- `read_survey` _function_ — Read the reviewed survey table.
- `record_provenance` _function_ — Write an ARCHIVE deliverable's one DERIVED_FROM edge to its work or unit (journaled
- `record_work_member` _function_ — Place a metabolized source in its work (journaled `work-member`): a Source Reference
- `source_entity_id` _function_ — The Entity a provenance key names: a key carrying the unit separator is a unit's.

### `cjm_context_graph_projection.librarypage`

- `form_name` _function_ — A form key as a reader reads it ("lecture-series" -> "lecture series").
- `plan_library_pages` _function_ — Plan the Library index and every work page it links: the Library under the profile, a
- `render_index` _function_ — The topic line, then one section per form: each work's name (linked to its work page when
- `render_work` _function_ — The card, the units grouped by part, and the outputs on the whole work. A unit with one
- `topic_pages` _function_ — The topical pages: a SUBJECT's category page listing at least one notes post (the topic
- `unit_anchor` _function_
- `work_description` _function_
- `year` _function_

### `cjm_context_graph_projection.linkaudit`

- `bracket_from_cdx` _function_ — Bracket the rot from a Wayback capture list: the last capture that answered
- `classify_link` _function_ — Classify a probe: a hop leaving the registrable domain is `offsite` (the hijack
- `extract_external_links` _function_ — Pull the external URLs out of markdown text.
- `link_audit` _function_ — The audit: enumerate Notes (frontmatter + every Section's raw), extract the
- `probe_url` _function_ — Fetch a URL following redirects ONE HOP AT A TIME (GET with a browser-like
- `registrable_domain` _function_ — The registrable (owner-level) domain of a URL — the unit a hijack crosses.
- `wayback_bracket` _function_ — Ask the Wayback CDX index for the URL's capture history and bracket the rot.

### `cjm_context_graph_projection.listing`

- `list_graph` _function_ — Enumerate one CLASS of the graph: nodes by label / assertions by predicate / edges
- `parse_where` _function_ — Parse `--where PROP=VALUE` clauses into property predicates (op `eq`, AND).

### `cjm_context_graph_projection.module_ops`

- `delete_module` _function_ — Delete a module — retire its journal key, drop its file, and let the code fold's step
- `flip_notebook_to_py` _function_ — The golden-reference flip, ONE LOUD VERB (DEC b2c5363d): notebook -> plain `.py`.
- `new_module` _function_ — Mint an EMPTY module, graph-sourced from birth (the target add-text / add-symbol /
- `regroup` _function_ — Gather symbols into a module — the EXECUTE verb for an `under_split` (extract a
- `rename_module` _function_ — Rename a `.py` module — re-emit its content at the new path, drop the old file, and
- `rewrite_module_import` _function_ — Rewrite a module-RENAME across an importer: every `from old import …` and

### `cjm_context_graph_projection.notes_outline`

- `apply_outline` _function_ — Turn the outline pass's answers into STRUCTURE rows on a proposal SET (ruling bc62c727 (A);
- `apply_outline_plan` _function_ — Land a plan as the journaled ops it names, in an order every step of which stands on its
- `outline_of` _function_ — The draft's STANDING outline as rows in the pass's own contract, so a reader can keep,
- `plan_outline` _function_ — The DRAFT mode's plan (81d6e669 (3); rulings 96be1528 (11) — a pass is a proposal set and a
- `render_outline_brief` _function_ — The brief of the whole-source OUTLINE PASS (ruling bc62c727 (A); parents per 776c13d3 (a)):

### `cjm_context_graph_projection.notes_place`

- `apply_placement_plan` _function_ — Land a plan as the journaled ops it names: each role as ONE `assert` of `point_role` on
- `gx_note_missing` _function_ — Whether the draft exists — the one graph read the apply makes before its first op.
- `plan_placement` _function_ — The pass's plan, mutating nothing: each row resolved to a point key and compared with what
- `render_place_brief` _function_ — The brief of the PLACEMENT PASS (rulings 96be1528 (1)/(3)): the keyed points laid out

### `cjm_context_graph_projection.onboarding`

- `project_onboarding` _function_ — Project the onboarding surface by WALKING the asserted lead structure.
- `surface_budget` _function_ — Pure: the lock budget MEASURED from this projection, never remembered.

### `cjm_context_graph_projection.oracle`

- `procedure_node` _function_ — The oracle's Procedure node (the programmatic value-source for its assertions).
- `read_repo_version` _function_ — Read a repo's version: installed metadata first, else `__version__` on disk.
- `run_version_oracle` _function_ — Refresh `version` slots for repo entities; report what changed.

### `cjm_context_graph_projection.paths`

- `alternatives` _function_ — alternatives: steps sharing an input ARTIFACT and producing one artifact KIND -- [{input,
- `analogues` _function_ — analogues (ad9bef5a (2)): steps making the SAME stage transition with DIFFERENT base models
- `check_record_refs` _function_ — Every Entity a record names is live (or earlier in its batch) and unretired, and its
- `continues_from` _function_ — continues-from: a step's REQUIRES matched to another step's PRODUCES -- [{step, from, via,
- `endpoint_kind` _function_ — What a node is, in RELATION_ENDPOINTS' vocabulary.
- `flywheel_cycles` _function_ — Flywheel paths (ae698640 (2)): a flywheel is a CYCLE AT THE KIND LEVEL over ACYCLIC instance
- `gaps` _function_ — gaps: an ASSUMED concept that no deliverable TEACHES and no Library unit or work COVERS --
- `land_record_edges` _function_ — Reconcile the edges a record owns with the record: the ones it no longer implies go, the new
- `load_path_graph` _function_ — The path model's slice of the graph as plain data: the Entities it names (by id: their
- `path_reads` _function_ — The `paths` read verb: one derived read, or all of them, with every node it mentions named.
- `prepares_for` _function_ — What a work, a unit or a deliverable PREPARES YOU FOR (e2b3a414): the deliverables that
- `record_edges` _function_ — The edges a record lands: an artifact's lineage, an environment's parts and requirements,
- `record_field_error` _function_ — The SHAPE of a path-model record field (pure; the live references are checked by
- `record_refs` _function_ — The Entities a record names (its references), from its fields.
- `record_relation` _function_ — Write one path-model relation (journaled `relate`), or retract it. The endpoints' kinds must
- `resolve_endpoint` _function_ — Resolve a relation endpoint argument: an Entity by `kind:key`, else a deliverable by id or
- `split_candidates` _function_ — SPLIT CANDIDATES (ae698640 (6)): a post turning A into B and B into C -- it PRODUCES B and C,
- `stage_matches` _function_ — A step's DERIVED stage and task (ae698640 (2)): its input artifact kinds and output kinds
- `staleness` _function_ — Environment staleness (ae698640 (3)): a deliverable that requires or produces an environment
- `step_view` _function_ — One step in context: its relations, what it continues from and what continues from it, its
- `steps_of` _function_ — Every deliverable with relations, as a step: what it requires (artifact / environment ->
- `transitions_error` _function_ — A stage's transitions (ae698640 (2)): each {in: [kinds], optional: [kinds], out: kind}, the

### `cjm_context_graph_projection.postpage`

- `category_links` _function_ — Each category as a link to its category page where one exists (design a62f2499 (7)), else
- `display_date` _function_ — Quarto formats a page's dates BEFORE user filters run, so a date the filter sets must
- `end_plan` _function_ — Every post's end matter. A Note of no post kind (a site page, an untyped Note) has none.
- `fill_copy` _function_ — Copy names the site author through {name} / {role} (the summary its holds clause through
- `header_facts` _function_ — The header's dated facts (39c51c15 (2)): when a deliverable's `published` state was asserted,
- `header_meta` _function_ — The header's projected metadata (39c51c15 (2)): the kind label; a born post's date is its
- `holds_line` _function_
- `is_category_link` _function_ — A link to the category listing opened filtered to a category (category_links' form,
- `license_facts` _function_ — The license facts (39c51c15 (6)): per deliverable class on the DeliverableType, per
- `links_line` _function_
- `load_category_listing` _function_ — The listing a post's categories link into (`category-listing` in the site config: a listing
- `load_holder` _function_ — The copyright holder the footer names (`copyright-holder` in the site config): a missing
- `load_reading_guide` _function_ — The site's one statement of how to read it (`reading-guide` in the site config, design
- `load_site_author` _function_ — The site's one statement of its author (`site-author` in the site config, amendment
- `load_site_holds` _function_ — What the site holds (`site-holds` in the site config, design 8b4f15d0 (3)): the second part
- `load_site_links` _function_ — The site's one statement of its contact links (`site-links` in the site config, design
- `load_site_nav` _function_ — The navbar's own entries (`site-nav` in the site config, finding c6befeb6): each names its
- `load_site_summary` _function_ — The site's one sentence of who and what (`site-summary` in the site config, amendment
- `load_strip_copy` _function_ — The strip's copy from the site config: a missing key or field refuses (the build never
- `nav_entries` _function_ — Resolve each navbar entry to what Quarto links (finding c6befeb6): a page path to the ONE
- `offered_backing` _function_ — The posts that back an OFFERED claim through a backing kind, read through the public
- `pitch_target` _function_ — The page the pitch points at: the Work-with-me page (903bc108 (5)). It is not on-graph
- `post_dates` _function_ — A post's public dates (39c51c15 (2)): an archive post's own date, a born post's publication;
- `post_licenses` _function_ — A post's licenses: its own override, else its class's. A post with none, or with an id
- `related_context` _function_ — The relations related posts rank by (39c51c15 (4), amendment e09e262b): post-to-post links
- `related_posts` _function_ — Related posts by relation, each with its reason (39c51c15 (4), amendment e09e262b):
- `render_end` _function_ — What the post draws on, related posts, the author strip (with the pitch line when given) and the comments block.
- `render_related` _function_ — Related posts, each with its reason, as a plain classed div (styled by the site).
- `render_reuse` _function_ — The license line, as Quarto's own Reuse section (a filter-set `license` never reaches
- `site_footer` _function_ — The footer, derived (39c51c15 (6)): the copyright years run from the first publication to
- `site_nav` _function_ — The navbar's right side: its own entries in their stated order (finding c6befeb6), then the

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

### `cjm_context_graph_projection.propose`

- `draft_code_block_update` _function_ — Pure: re-render the fenced code block that carries `name`'s body from the live body.
- `propose_updates` _function_ — Draft a proposal for every actionable, unacknowledged, not-yet-proposed change on the
- `symbol_baseline_body` _function_ — The approval-time body: the module's last `source` snapshot at/before T (else the

### `cjm_context_graph_projection.prose_refs`

- `extract_id_tokens` _function_ — Id-shaped tokens in prose: 8-hex prefixes / full UUIDs, ordered, deduped.
- `prose_refs` _function_ — The prose-ref drift audit over asserted Decisions + Notes (pure read).

### `cjm_context_graph_projection.pull_transcript`

- `build_derived_edges` _function_ — The pure aggregation-seam assembly: sent Message DERIVED_FROM each part,
- `build_mint_batch` _function_ — The pure node/edge assembly the live mint AND replay share.
- `build_pull_payload` _function_ — The journalable payload for an extraction sequence.
- `derive_message` _function_ — The compose-send aggregation seam (DEC fc6a0cdc pt 5): the sent message
- `edit_message` _function_ — In-place body edit of a Message — the journaled edit-op half of the
- `mint_pulled_messages` _function_ — Land pulled messages on the spine — the code path live pull AND replay share.
- `pull_transcript` _function_ — The live pull: derive the mapping, extract the active path, mint the delta.
- `stale_next_edges` _function_ — The chain re-link plan (finding e358fe97) — pure, so the live pull and

### `cjm_context_graph_projection.purenotes`

- `accept_point` _function_ — Land ONE accepted point: the Point node, its segment References (from observations —
- `apply_judgements` _function_ — THE FOLD (ruling 1798a796 (3)): apply a judge's answers mechanically, loud on the first
- `born_notes_by_unit` _function_ — Which source UNITS carry a born deliverable on this graph, with its state and synopsis:
- `build_notes_pack` _function_ — Apply the type's INFORMATION POLICY (a stratum query read as ROLES — ruling e1e096fa)
- `build_point_tree` _function_ — Nest by `parent_key` (second-read ruling (4): depth TWO in practice, the tree is generic).
- `choose_spine` _function_ — Pick the SKELETON spine to read (the correction core's `spine_where_for` rule, pure):
- `close_open_refs` _function_ — Apply a reconciler's answers to a set's hinted references — mechanically checked, loud
- `coverage_gaps` _function_ — Pure: the unreferenced-lines query — every content line the type includes that no
- `date_phrase` _function_ — A date as the card prints it, at the precision it is KNOWN to (ruling de9c4cda (H7)): `day`
- `deliverable_owns` _function_ — Ruling 96be1528 (P): a source's points are the source's points — a SUBSTANCE point is
- `derive_frontmatter` _function_ — The type may OWN the title and the description — the rest of the authored frontmatter
- `derive_work_frontmatter` _function_ — The work-page type OWNS the title and description: `notes-on-work` = "Notes on *<work>*"
- `derived_description` _function_ — What the unit CONTAINS, from data the rendering already uses: the work (when the
- `edit_point` _function_ — Edit an accepted point IN PLACE — the per-point repair the ch. 2 staging read demanded
- `effective_roles` _function_ — Each point's role (ruling 96be1528 (1)): its own fact, else its parent's effective
- `elided_point` _function_ — A point the TYPE elides by kind (ruling 15657521 (2)): the standalone lecture resource
- `ensure_point_set` _function_ — Mint the (Source, unit)'s PointSet on first use and assert the Note RENDERS it — both
- `extra_list` _function_ — Every EXTRA still pending in a set, keyed `x001`… in the set's order — the ids the judge
- `group_points` _function_ — The page's sections. SYNTHESIZED (ruling bc62c727 (A)): when the points include
- `judge_points` _function_ — Apply an overlap judge's answers to an ACCEPTED draft (ruling 1798a796; work item
- `key_owner` _function_ — Cross-point keys name SUBSTANCE points: for a set-owned point that is its own set; for
- `lecture_title` _function_ — The title a reader knows the lecture by. The URL binding kept the playlist row's title as
- `load_deliverable_type` _function_ — Read a DeliverableType profile off the graph (None = `notes-type <key>` first).
- `load_notes_propsets` _function_ — Every notes proposal set under `root` (optionally for one source), newest first.
- `load_owned_points` _function_ — The points ONE owner holds (HAS_POINT owner -> point), in source order — the owner's
- `load_placements` _function_ — The deliverable's PER-POINT OVERLAY (ruling 96be1528 (3)/(7)): every PLACED edge from a
- `load_point_roles` _function_ — The `point_role` facts on the draft's points (ruling 96be1528 (1)): the active value per
- `load_points` _function_ — What a Note RENDERS (ruling 96be1528 (P)): its OWN points (sections, research) plus the
- `merge_point_blocks` _function_ — The BLOCK merge (ruling 1798a796, fork 1; work item 1561551e): the unit of agreement is
- `merge_point_proposals` _function_ — The ROW-LEVEL merge (work item 3a2c94eb (2); the filter lane's `merge_filter_proposals`
- `mint_deliverable_type` _function_ — UPSERT a DeliverableType by slug (the display-rule pattern: last journaled op wins).
- `nest_points` _function_ — The one-level view of `build_point_tree` (kept for callers that only need parent -> children).
- `note_deliverable_type` _function_ — The Note's bound type slug (the active `deliverable_type` fact).
- `note_publish_states` _function_ — The publish_state facts as a map: every deliverable's active values. One value is the
- `note_types` _function_ — Every typed Note's active type with that type's kind and origin (design amendment
- `observe_segments` _function_ — Observe each segment in the sibling READ-ONLY (label + properties hash + title) — the
- `open_reference_list` _function_ — Every hinted reference still open in a set, keyed `r01`… in the order of `points_index`
- `overlapping_points` _function_ — Pure: the duplication candidates — two points deriving from a shared segment — and
- `pack_digest` _function_ — Digest the READ content (source binding + numbered lines + headers) — what a proposal
- `pick_propset` _function_ — Choose a proposal set: newest by default, else the unique id/prefix match.
- `place_point` _function_ — Land the deliverable's overlay on ONE point (ruling 96be1528 (3)/(7)): ONE `PLACED` edge
- `plan_notes_blocks` _function_ — Cut the whole pack into BLOCKS (ruling 1798a796, fork 1): a block runs between COMMON
- `plan_notes_windows` _function_ — Cut a whole-unit pack into `count` windows of near-equal line count at MECHANICAL
- `point_check` _function_ — The CHECK review: one point beside its segments' LIVE text from the sibling — the
- `point_coverage` _function_ — The COVERAGE review: re-read the unit per the Note's type policy and list the content
- `point_from_args` _function_ — The op-args -> PointNode mapping the live accept, the replay AND the re-home share.
- `point_set_of` _function_ — The PointSet a unit snapshot addresses (ruling 96be1528 (P)): identity = (sibling graph
- `points_as_proposals` _function_ — An accepted draft read as a proposal set — a point's key IS its accepted proposal id, so
- `points_index` _function_ — Key a set of proposal rows for a reader that cannot see their lines: `p001`… in source
- `proposals_from_point_rows` _function_ — Resolve validated rows to proposal rows: a minted proposal id (the point's future
- `public_deliverables` _function_ — The public rule the publish guard enforces, as a set: a Note is public when its active
- `pure_notes_type` _function_ — The pure-notes profile as data: information policy = a stratum query, presentation
- `read_source_facts` _function_ — The Source-level facts a rendering's title and card read LIVE from the sibling (finding
- `read_source_references` _function_ — The Source's human-added resource links (`Reference` nodes minted by the transcription
- `read_source_unit` _function_ — Read one source unit: the EFFECTIVE spine (layer-0 + applied corrections, via the
- `read_work_structure` _function_ — The WORK as the sibling holds it: every Source whose structure map names the work, in
- `rehead_points` _function_ — Re-derive every Point's captured heading / heading_index from its segment run against a
- `rehome_points` _function_ — THE RE-HOME (ruling 96be1528 (P); the migration of a deliverable born before PointSets):
- `render_judge_brief` _function_ — The brief of the bounded JUDGEMENT (ruling 1798a796 (2)): ONE whole-source reader works
- `render_notes` _function_ — Derive the Note's body from its Points and APPLY it: the authored preamble stays, the
- `render_notes_pack` _function_ — Render a pack as the brief a proposer reads: the unit, the kind slate, the headers
- `render_pairs_brief` _function_ — The brief of the judgement over what standing detection flags (ruling 1798a796 (1)-(2)):
- `render_points` _function_ — Render the body from the Points — deterministic, so a replayed `render-notes` derives
- `render_points_index` _function_ — The index as a reader sees it: `p017 [claim] 12:03  **lead** — text`, nested by depth.
- `render_reconcile_brief` _function_ — The brief of the pass that closes hinted references (work item 3a2c94eb (3)): a
- `render_source_card` _function_ — The reader-facing provenance (second-read ruling (1)): a derived one-line callout under
- `render_work_card` _function_ — The work page's reader-facing card (check 2d01fe1e): the work-level content — never
- `render_work_chapters` _function_ — The TOC that is also the executive summary (checks 2d01fe1e + 34f73e46): the units in
- `render_work_page` _function_ — Derive the WORK PAGE's body and APPLY it (item ebb77107; ruling a7ca900d (2)): the
- `render_works_table` _function_ — Pure: the per-work promotion condition as the staging site's works table (item
- `rendered_sets` _function_ — The Note's RENDERS edges (ruling 96be1528 (P)) — where its substance lives. A node that
- `renderers_of` _function_ — The inverse of `rendered_sets`: every deliverable sharing the set's substance — what a
- `resolve_references` _function_ — Resolve human-added links for rendering (ae103970): a cross-work link naming a
- `resolve_sibling_source` _function_ — Resolve a Source in the sibling graph: id prefix first (the shared seam), then a
- `retract_note_points` _function_ — Retract EVERY point a Note renders (the re-drive's clean slate — ruling e1fd4d64 (5)):
- `retract_point` _function_ — Retract a point: delete the node (its edges cascade). The compensating op of accept —
- `speaker_labels` _function_ — Ruling bc62c727 (B): a speaker reads as its NAME, else its ROLE in the role's own words
- `staging_index` _function_ — Project the staging site's LISTINGS from the publish_state facts (item 140981e9 (b)):
- `stratum_role_policy` _function_ — Read a type's stratum policy as ROLES (ruling e1e096fa). `stratum_roles` is the one
- `synopsis_of` _function_
- `unit_label` _function_ — The short unit handle the title carries (second-read ruling (1): the short shape).
- `unit_title_header` _function_ — Ruling e1fd4d64 (C): the first read-aloud header of a chapter file is the chapter's own
- `unjudged_pairs` _function_ — STANDING DETECTION (ruling 1798a796 (1)): the pairs of points that leave a draft
- `validate_point_rows` _function_ — Validate + normalize proposer rows against their pack — loud on the first bad row.
- `with_points_index` _function_ — The SEQUENTIAL arm's pack (design 6752db0a (11)): the window pack plus a running index
- `work_of_note` _function_ — Which WORK a typed deliverable belongs to — read from its Points' unit (the structure
- `work_page_notes` _function_ — The WORK PAGES on this graph: every Note bound to the work-page type, keyed by the work
- `work_page_type` _function_ — The WORK PAGE profile as data: one page per Source work, the directory index above its
- `work_promotion_status` _function_ — The WORK-PAGE promotion condition (ruling a7ca900d (1)/(4); item 140981e9 (c)) as a
- `work_reference_of_note` _function_ — The work a WORK PAGE stands for, read off its edges: the page is linked DERIVED_FROM a
- `write_notes_propset` _function_ — Write one notes proposal set: `<out_root>/<set_id>/manifest.json` + `proposals.jsonl`

### `cjm_context_graph_projection.readiness`

- `anchor_matches` _function_ — Pure: does an `--anchor` query name this anchor? id prefix, title substring, or slug.
- `classify_readiness` _function_ — Pure: partition work-items into done / ready / blocked from authored ground truth.
- `honored_closable` _function_ — Pure: open items a DONE Decision points at via EVIDENCE_FOR / SUPERSEDES — closable
- `readiness` _function_ — The derived ready/blocked/done frontier over authored `task_state` + `GATED_BY` edges.
- `summarize_checks` _function_ — Pure: per-item DoD summary from the checks' own task_states.

### `cjm_context_graph_projection.readme`

- `project_readme` _function_ — Project a repo's README markdown from the graph (structural-only v1).
- `repo_purpose` _function_ — The repo's intro/"why" prose: the active `purpose` assertion on the repo Entity.

### `cjm_context_graph_projection.reads`

- `append_read` _function_ — Append one read event; `ts`/`session` stamping rides `append_op`.
- `configure_reads` _function_ — Arm (or disarm, path=None) read recording for this process.
- `delivered_ids` _function_ — Node ids a rendered result delivered into the consumer's context.
- `record_read` _function_ — The render-boundary tap: no-op unarmed, FAIL-OPEN armed.

### `cjm_context_graph_projection.rebuilddiff`

- `diff_graphs` _function_ — The pure comparison: node and edge id sets, then every field on each shared id.
- `parse_path_map` _function_ — Parse `--path-map OLD=NEW` flags; a pair without `=` is refused loudly.
- `rebuild_diff` _function_ — Compare two graph dbs property for property — the live-versus-rebuild standing check.
- `render_rebuild_diff` _function_ — Markdown by class and field (the drift map), or the report as JSON for agents. Rows a

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

### `cjm_context_graph_projection.relive`

- `apply_live` _function_ — Apply the code fold's step to the db for the records a live verb just appended.

### `cjm_context_graph_projection.rename_ops`

- `rename_symbol` _function_ — Rename a top-level free function/class everywhere it is referenced, graph-driven.
- `rename_symbols` _function_ — Batch top-level renames in ONE emit set (finding 889b3025).
- `rewrite_import_for_rename` _function_ — Re-point an importer's `from src_module import old [as a]` at the new name.
- `scoped_rename` _function_ — Rename references to the module-global `old` -> `new`, scope-aware, by exact position.

### `cjm_context_graph_projection.render`

- `render` _function_ — Render a projection result in the requested format.

### `cjm_context_graph_projection.review`

- `approvals_of` _function_ — Pure: the ACTIVE approval-class assertions (the roots the frontier walks from).
- `change_key` _function_ — The change KEY an acknowledgment binds to: `<upstream 8>@<token 12>` (a hash token
- `classify_reference_change` _function_ — Pure: a foreign node changed when its live hash is not the observed one; a foreign
- `classify_text_change` _function_ — Pure, revert-aware: compare the live content against its approval-time baseline.
- `reference_baseline` _function_ — Pure: the observation an approval at T saw — the last journaled observation at or
- `review_frontier` _function_ — The derived review frontier: approved deliverables whose upstream changed since approval.
- `walk_upstream` _function_ — Pure: BFS upstream from the deliverable's components along the dependency edges.

### `cjm_context_graph_projection.runtime`

- `GraphHandle` _class_ — A live, started graph: the queue + the capability id to address it.
- `open_graph` _function_ — Load the graph-storage capability on `graph_db_path` and yield a started handle.

### `cjm_context_graph_projection.scratchpad_export`

- `derive_entries` _function_ — Chronological entries with derived `on_active_path` (transcript tip
- `export_session_markdown` _function_ — Gather the session's message graph and render the .md projection.
- `read_session_messages` _function_ — A session spine's Message BODIES in chain order — the `read --session` verb.
- `render_session_markdown` _function_ — The pure renderer: one portable markdown document from derived entries.

### `cjm_context_graph_projection.seeds`

- `aliases_for` _function_ — Prior names that should resolve to this repo (empty unless it was renamed).
- `class_subject_elements` _function_ — Class-subject entities + PART_OF-style membership edges (ABOUT member->class).
- `conceptual_key` _function_ — The durable conceptual key for a repo (rename-aware; defaults to the name).
- `rename_contradiction_elements` _function_ — The torch/hf-utils `rename-disposition` slots with BOTH claims active.
- `repo_dir_name` _function_ — The CURRENT repo dir name for a conceptual key (identity unless renamed).
- `seed_elements` _function_ — All hand-seeded elements (rename contradiction + stale version + class subjects).
- `stale_version_seed_elements` _function_ — A `cjm-substrate` version slot seeded BEHIND the real version (oracle bumps it).

### `cjm_context_graph_projection.series`

- `mint_series` _function_ — Mint or update a Series from its page's record (journaled `series`; upsert by key).
- `order_members` _function_ — Walk the `after` chain from the head; report forks and unreached members.
- `place_in_series` _function_ — Splice ONE member (journaled `place-in-series`): insert, move, or remove.
- `series_order` _function_ — The series in its AUTHORED order, with whatever breaks the chain (the read verb).
- `set_series_members` _function_ — Set a series' WHOLE ordered membership (journaled `series-members`).

### `cjm_context_graph_projection.serve`

- `build_app` _function_ — Build the read-only API app over already-open graph handles.
- `graph_names` _function_ — Derive a stable short name per db (its file stem; collisions suffixed `-2`, `-3`, …).
- `serve_graphs` _function_ — Open every graph once, hold the handles, and serve the API until interrupted.

### `cjm_context_graph_projection.site`

- `check_rendered_site` _function_ — Every rendered page, read whole (findings c6befeb6 + d807a18f): each internal href / src
- `check_source_aliases` _function_ — Every front-matter alias must be a superseded `site_path` of the page that declares it:
- `output_href` _function_ — Quarto's alias `fixupHref`: a trailing slash or an extension-less path names the
- `page_outputs` _function_
- `publish_guard` _function_ — Refuse public output that carries a draft (design 13753cbf (1)): no drafts tree, no link
- `quarto_inspect` _function_ — The profile's output dir and input documents, read from Quarto itself.
- `redirect_page` _function_ — Quarto's redirect page for one alias location.
- `redirect_plan` _function_ — The redirect projection from the `site_path` facts: one stub per superseded path, to
- `site_build` _function_ — Build the site under one profile: generated inputs, render, the redirect projection,
- `stated` _function_ — The ONE reader of what a post's page states (finding 12d98020): its front matter first,
- `write_redirects` _function_ — Write each redirect page; a stub landing on a rendered page is an error (Quarto skips it

### `cjm_context_graph_projection.sitefeeds`

- `absolute_url` _function_
- `day_time` _function_
- `escape` _function_
- `feed_image_size` _function_
- `feed_order` _function_ — prepareItems: an undated item sorts last, equal dates keep the order given.
- `highlight_styles` _function_ — Quarto's defaultSyntaxHighlightingClassMap: each text style of the default theme as the
- `item_image` _function_ — A site-absolute image stands; a relative one is relative to the post's folder.
- `math_image_url` _function_
- `png_size` _function_
- `render_feed` _function_
- `rendered_contents` _function_ — readRenderedContents under kFeedOptions, on the page as rendered. The authors are the
- `rss_date` _function_
- `write_feeds` _function_ — Write every feed whose text changed. An item is {output, date, categories, image, authors?}:

### `cjm_context_graph_projection.sitelinks`

- `resolve_site_links` _function_ — Reconcile the `site_link` REFERENCES edges of the scoped notes against the facts.
- `site_link_window` _function_ — Observe the block's writes, then run the step once at its close (a live write window —
- `site_path_holders` _function_ — Every site_path value on the graph, keyed for resolution, plus each page's ACTIVE path.
- `site_path_key` _function_ — The key two URLs of one page share under Quarto's URL rules (the facts stay verbatim).
- `step_site_links` _function_ — THE STEP (design amendment 9ee4e346): one whole-graph resolve at a window's close, run
- `touches_inputs` _function_ — Whether a window moved an input of the resolve (design amendment 9ee4e346 (3)).

### `cjm_context_graph_projection.sitelisting`

- `check_listing_chips` _function_ — After the render, with a category listing named: every chip on a projected page is a link
- `chip_hrefs` _function_ — Every chip's href from the one encoder (postpage.category_links); on the category listing a
- `date_text` _function_
- `feed_header` _function_
- `feed_plan` _function_ — A listing's feed (design 0efb5497 (3b)): the page's output and the feed beside it (the page's
- `listing_kinds` _function_ — The filter's kinds (e66296bd (1)): only the categories this listing's items carry, so a
- `listing_script` _function_ — The site script, its words baked in from WORDS.
- `load_site_title` _function_
- `note_item` _function_ — A post as a listing shows it: what its page states (site.stated, the one reader).
- `output_href` _function_
- `page_item` _function_ — A collection page as a listing shows it (the logs index's series, the learning paths): its
- `render_listing` _function_ — The listing's markup: a container carrying the filter's data, an ordered list of the items,
- `sort_rows` _function_ — The rows in the order a listing shows them, each term applied as a stable sort from the last
- `title_html` _function_ — A title is the author's markdown: Quarto rendered a listed title through Pandoc (its smart

### `cjm_context_graph_projection.sitepages`

- `check_category_listing` _function_ — The category listing a Lens projects (design ce17606b (3)) is the page every chip links
- `check_page_outputs` _function_ — Every Series or Lens page the build projected has its rendered page (the guard's check,
- `group_through_series` _function_ — A Lens with view.group_by "series" lists a member THROUGH its Series page (design 7657c4a5
- `hub_order` _function_ — The pages a listing links in the order the listing SHOWS them: its sort applied to what the
- `is_generated` _function_
- `is_public` _function_ — A publish_state decides (public only when `published`); with none, a Note of an ARCHIVE
- `member_updated` _function_ — `last-modified` is a file mtime (a checkout time, not an edit), so it never counts.
- `page_plan` _function_ — Plan every projected page: a Series or Lens with an active site_path gets one; a broken
- `page_source` _function_ — The source a page's output is rendered from: Quarto's output file with `.qmd`.
- `parse_date` _function_
- `project_pages` _function_ — Write the planned pages into the source tree (only those whose text changed), remove
- `render_page` _function_
- `unlisted_posts` _function_ — The posts a page meant to cover every post leaves out (the category listing, ce17606b (3);

### `cjm_context_graph_projection.sitetheme`

- `bound_system` _function_ — The design system the binding names, read from the sibling at its latest capture.
- `mode_pair` _function_ — The light / dark pair: the profile's facts, else the system's scheme map (9a7224a7 (3)).
- `profile_key` _function_
- `project_theme` _function_ — Write the profile's theme from its bound design system (the module docstring's steps).
- `site_binding` _function_ — The profile Entity, its ONE STYLED_BY Reference and its mode facts.
- `system_fonts` _function_ — THE FONT SEAM (amendment 4b58c9db (2)): the files the captured tokens name, found beside
- `theme_wanted` _function_

### `cjm_context_graph_projection.source_state`

- `IdentityWalk` _class_ — The identity map's walk ONE RECORD AT A TIME — the step `symbol_identity_map` folds
- `SymbolIdentity` _class_ — Container-independent CodeSymbol identity, DERIVED from the source journal (36f649d3).
- `absorb_authored_text` _function_ — Absorb an `author` edit of a GRAPH-SOURCED module into the source journal.
- `append_register` _function_ — Append a `register` event — repo inventory as JOURNAL DATA (DEC c47912f6).
- `append_retire` _function_ — Append a `retire` op ending a module key's journal life.
- `append_source` _function_ — Append a `source` op, skipping a write identical to the module's current latest state.
- `canonical_emit` _function_ — Decompose source text and re-emit it canonically — the exact graph→`.py` Phase 2 yields.
- `canonical_emit_notebook` _function_ — The notebook analogue of `canonical_emit`: parse to cells, re-render canonically.
- `collect_appends` _function_ — Collect every source-journal record appended inside the block, as it landed on disk.
- `cutover_module` _function_ — Phase 2: make the JOURNAL the module's source of truth (the persistence flip).
- `emit_source_artifact` _function_ — (Re)generate a module's file artifact from its journaled source (the recovery /
- `flip_module` _function_ — Capture a module's CANONICAL source into the shadow source journal (Phase 1).
- `graph_sourced_modules` _function_ — The modules whose ingest source IS the journal (a `cutover` op exists for them).
- `is_test_module_path` _function_ — Whether a module path denotes TEST source (`tests/` or `tests_manual/`).
- `journal_repos` _function_ — The on-graph repos — DERIVED from the journal (finding 7a2d54ae): a repo is on-graph
- `journaled_emit` _function_ — The ops seam (pillar 1 of DEC 6ee4b4f2): events BEFORE files — THE file-write path.
- `latest_source_ops` _function_ — The LATEST source state per module (last write wins — the 'journal STATE, not diff'
- `notebook_to_py_source` _function_ — Build a plain-`.py` module source from a notebook's EXPORT cells (the flip transform).
- `read_source_journal` _function_ — Read every `source` op across the rotated SEGMENT FAMILY (one JSON object per
- `source_check` _function_ — The soak instrument: for each shadow-sourced module, check two things.
- `symbol_identity_map` _function_ — Derive the container-independent symbol identity map from the source journal (36f649d3).
- `uncaptured_modules` _function_ — The uncaptured-module audit (build a6453f70) — the ac3d52f4 recipe as a verb.

### `cjm_context_graph_projection.sourcemoves`

- `attribute_moved_sources` _function_ — Every source whose HEAD differs between the two records, with the element ids its moved
- `moved_element_ids` _function_ — Decompose every kept path that changed between `a` and `b`, at both versions; a source
- `source_lane` _function_ — The ingest's own view of one source: which paths it reads and what each version of a

### `cjm_context_graph_projection.sources`

- `apply_source_facts` _function_ — Land an observation's facts on its Reference -- live and replay alike. An unchanged value
- `citation_text` _function_ — A citation's parts as one line, in the order a reader reads them.
- `collection_members` _function_ — A Collection's member Sources in the sibling, read with the observation.
- `draws_plan` _function_ — Each rendered post's draws-on block; every source a post derives from that the Library
- `load_sources` _function_ — Every Note's sources -- its DERIVED_FROM References to a Source or a Collection, each
- `observed_facts` _function_ — The facts a source states about itself, read at observation (722a8232 (1), (2)); its
- `render_draws` _function_ — What the post draws on (design 37f82f72 (2)): each work or unit named from the Library's
- `source_text` _function_

### `cjm_context_graph_projection.structure`

- `add_section` _function_ — Add a section to an existing note (append, or insert after an anchor), born on-graph.
- `born_post_path` _function_ — Where a born post's file lives — DERIVED from the config's emit root and the slug, never
- `new_note` _function_ — Create a brand-new note, born on-graph (write the `.md` + ingest it this session).
- `reconstruct_note` _function_ — Reconstruct a whole note (Note + ordered Section nodes) FROM JOURNALED text — the M3

### `cjm_context_graph_projection.tutorialspage`

- `anchor` _function_ — A section anchor from vocabulary keys (never a node id).
- `hardware_marks` _function_ — The verification marks per deliverable: STATED evidence under every profile, the
- `learning_paths` _function_ — The collection pages whose listed members are ALL tutorials; a collection mixing
- `plan_matrix_page` _function_ — Plan the Tutorials page: the listed population under the profile, the matrix over it,
- `render_body` _function_ — The grid, the learning paths, the tutorials by task, the off-grid list, and (staging)

### `cjm_context_graph_projection.viz`

- `project_viz` _function_ — Project the readiness frontier into a self-contained interactive HTML page.
- `render_viz_html` _function_ — Render the elements into one self-contained interactive HTML page (Cytoscape + dagre).
- `viz_elements` _function_ — Pure: turn a readiness frontier into Cytoscape elements — the whole data model.

### `cjm_context_graph_projection.workbench`

- `anchor_lead_view` _function_ — One anchor's LEAD as STRUCTURE (DEC ee9e9be6): the navigable pin tree.
- `journal_ops` _function_ — The feed's OP-LEDGER zoom (DEC ee9e9be6): one row per journaled op.
- `portfolio_view` _function_ — The workbench FRONT DOOR (DEC ee9e9be6): every role-asserted anchor, one row.
- `session_feed` _function_ — The TWO-ZOOM session feed (DEC ee9e9be6): op ledger + touched-node cards.

### `cjm_context_graph_projection.worklist`

- `dangling_reference_proposals` _function_ — Referenced `[[slugs]]` with no note, each with a fuzzy suggestion (no auto-fix).
- `dangling_reference_sources` _function_ — The note ids whose `[[wiki-links]]` include `drifted_slug` (alias evidence).
- `worklist` _function_ — Assemble the propose/confirm worklist (graph signals + optional corpus triage).

### `cjm_context_graph_projection.write`

- `add_check` _function_ — Attach a definition-of-done check to a work item (DoD-as-graph-objects).
- `alias` _function_ — Confirm a drifted link slug as an alias OF a real note (the worklist payoff).
- `assert_value` _function_ — Write one value to a `(subject, predicate)` slot, recording any conflict.
- `author_section` _function_ — Apply a memory section's verbatim `raw` STATE to the graph — the born-on-graph leg
- `confirm_proposal` _function_ — CONFIRM a proposal: apply its drafted section raw to the deliverable, then re-assert
- `content_hash_of` _function_ — The content an approval binds to (design 40622922): a Note hashes as its lossless
- `decide` _function_ — Record a Decision + its `SUPPORTED_BY` premise edges (reasoning substrate).
- `link` _function_ — Mint a deliberate edge between two EXISTING nodes (heterogeneous interlink).
- `mint_procedure` _function_ — Upsert a Procedure node by deterministic (method) id — the programmatic value-source
- `mint_proposal` _function_ — Mint a PROPOSAL — an agent-drafted update for ONE section of a stale deliverable
- `observe_foreign` _function_ — Open the sibling graph READ-ONLY, resolve the foreign node, and take the observation.
- `register_session` _function_ — Register/update a timestamp-keyed Session node — the session SPINE (DEC 6124d8bf).
- `resolve_subject` _function_ — Resolve a subject to an entity id (rename-stable), minting a `term` entity
- `retract_session` _function_ — RETRACT a Session spine node — the write dual of `register_session`, on
- `unlink` _function_ — RETRACT a deliberate edge — the write dual of `link` (finding 2f1d9382).

## Dependencies

**Depends on:** `beautifulsoup4`, `cjm-context-graph-layer`, `cjm-context-graph-primitives`, `cjm-design-system`, `cjm-dev-graph-schema`, `cjm-harness-transcripts`, `cjm-markdown-decompose-core`, `cjm-notebook-decompose-core`, `cjm-python-decompose-core`, `cjm-substrate`, `pyyaml`
**Used by:** `cjm-graph-workbench-qt`, `cjm-notebook-decompose-core`, `cjm-session-scratchpad-qt`, `cjm-substrate`
