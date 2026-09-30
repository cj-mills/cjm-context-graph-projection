"""The Tutorials page (design 7f200ecb): a Lens with the `coverage-matrix` layout projects the
task x stage grid, the learning paths, the tutorials by task and the off-grid list; empty cells
stay blank and only stated verifications render publicly; staging adds the gaps; anchors are
vocabulary keys; a refusal or a mixed collection refuses the page."""

import asyncio
from pathlib import Path

import pytest

from cjm_context_graph_layer.ops import extend_graph
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import note_node_id, series_node_id
from cjm_markdown_decompose_core.extract import note_from_text
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from cjm_context_graph_projection.coverage import mint_entity, project_matrix, record_verification
from cjm_context_graph_projection.lens import apply_lens, lens_node_id, set_lens
from cjm_context_graph_projection.listing import list_graph
from cjm_context_graph_projection.purenotes import mint_deliverable_type
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.series import mint_series, set_series_members
from cjm_context_graph_projection.site import redirect_plan
from cjm_context_graph_projection.sitepages import GENERATED, page_plan
from cjm_context_graph_projection.tutorialspage import anchor, learning_paths, render_body

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()

_T = [{"key": "object-detection", "name": "Object detection", "position": 1},
      {"key": "llm", "name": "LLMs", "position": 7},
      {"key": "general", "name": "General", "position": 8, "cross_task": True},
      {"key": "other", "name": "Other", "position": 9, "off_grid": True}]
_S = [{"key": "setup", "name": "Setup", "position": 0, "cross_task": True},
      {"key": "training", "name": "Training", "position": 1},
      {"key": "deployment", "name": "Deployment", "position": 2}]


def _f(tasks=(), stages=()):
    return {P.TEACHES_TASK: list(tasks), P.TEACHES_STAGE: list(stages)}


def test_the_body_grid_lists_and_profiles():
    m = project_matrix(_T, _S, {i: {"slug": i} for i in ("yolo", "env", "art")},
                       {"yolo": _f(["object-detection"], ["training"]), "env": _f(["general"], ["setup"]),
                        "art": _f(["other"])})
    items = {"yolo": {"title": "Train [YOLOX]", "href": "../../posts/yolo/index.md", "date": "2023-08-21",
                      "description": "A detector.", "marks": ["Tested on RTX 4090, Ubuntu"]},
             "env": {"title": "Setup", "href": "../../posts/env/index.md", "date": "2024-01-01",
                     "description": "", "marks": []},
             "art": {"title": "Blender", "href": "../../posts/art/index.md", "date": "2021-01-01",
                     "description": "", "marks": []}}
    pub = render_body(m, items, "public")
    # The grid: counts link their lists, the covered cell links the general row, empty cells blank
    assert "| Task | Setup | Training | Deployment |" in pub
    assert ("| [Object detection](#object-detection) | [General](#general-setup) | "
            "[1](#object-detection-training) |  |") in pub
    assert "| [General](#general) | [1](#general-setup) |  |  |" in pub
    # An empty row names its task without a link: no section exists for it to land on
    assert "| LLMs | [General](#general-setup) |  |  |" in pub and "(#llm)" not in pub
    assert "#### Training {#object-detection-training}" in pub and "### General {#general}" in pub
    assert ("- [Train \\[YOLOX\\]](../../posts/yolo/index.md) · 2023-08-21 — A detector. "
            "_(Tested on RTX 4090, Ubuntu)_") in pub
    assert "## Other tutorials {#other}" in pub and "[Blender]" in pub
    assert "::: {#learning-paths}" in pub and "## Gaps" not in pub
    stg = render_body(m, items, "staging")
    assert "## Gaps {#gaps}" in stg and "- Object detection × Deployment" in stg
    assert anchor("object-detection", "data-creation") == "object-detection-data-creation"


def test_learning_paths_are_all_tutorial_collections_and_a_mix_refuses():
    types = {"t1": {"kind": "tutorial"}, "t2": {"kind": "tutorial"}, "n1": {"kind": "notes"}}
    planned = [{"source": "series/tutorials/b.qmd", "subject": "B", "listed": ["t1", "t2"]},
               {"source": "series/notes/n.qmd", "subject": "N", "listed": ["n1"]},
               {"source": "series/tutorials/a.qmd", "subject": "A", "listed": ["t2"]}]
    ok = learning_paths(planned, types, "series/tutorials/index.qmd")
    assert ok == {"contents": ["a.qmd", "b.qmd"], "errors": []}
    mixed = learning_paths(planned + [{"source": "series/x.qmd", "subject": "X", "listed": ["t1", "n1"]}],
                           types, "series/tutorials/index.qmd")
    assert [(e["kind"], e["subject"]) for e in mixed["errors"]] == [("learning-path-mixed", "X")]


def _post(title: str, day: str) -> str:
    return f"---\ntitle: \"{title}\"\ndate: {day}\ndescription: \"About {title}.\"\n---\n\n## Overview\n\nBody.\n"


@pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")
def test_the_matrix_lens_projects_the_tutorials_page(tmp_path):
    root = tmp_path / "site"
    posts = {"yolo": ("Train YOLOX", "2023-08-21"), "env": ("Setup Env", "2024-01-01"), "n": ("A Note", "2022-01-01")}
    for s, (t, d) in posts.items():
        (root / "posts" / s).mkdir(parents=True)
        (root / "posts" / s / "index.md").write_text(_post(t, d))

    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            notes = [note_from_text(str(root / "posts" / s / "index.md"), (root / "posts" / s / "index.md").read_text(),
                                    corpus_root=str(root / "posts"), lossless=True) for s in posts]
            nodes, edges = corpus_graph_elements(notes)
            await extend_graph(gx.queue, gx.graph_id, nodes, edges)
            await mint_deliverable_type(gx, "archive-tutorial", title="T", kind="tutorial", origin="archive")
            await mint_deliverable_type(gx, "archive-notes", title="N", kind="notes", origin="archive")
            for s in ("yolo", "env"):
                await _assert(gx, s, "deliverable_type", "archive-tutorial")
            await _assert(gx, "n", "deliverable_type", "archive-notes")
            for t in _T:
                await mint_entity(gx, "task", t["key"], name=t["name"],
                                  fields={k: v for k, v in t.items() if k not in ("key", "name")})
            for s in _S:
                await mint_entity(gx, "stage", s["key"], name=s["name"],
                                  fields={k: v for k, v in s.items() if k not in ("key", "name")})
            await _assert(gx, "yolo", P.TEACHES_TASK, "object-detection")
            await _assert(gx, "yolo", P.TEACHES_STAGE, "training")
            await _assert(gx, "env", P.TEACHES_TASK, "general")
            await _assert(gx, "env", P.TEACHES_STAGE, "setup")
            await mint_entity(gx, "hardware", "rtx-4090", name="RTX 4090", fields={"device_class": "gpu"})
            await record_verification(gx, "yolo", "rtx-4090", os="Ubuntu", date="2023-08-21")
            await record_verification(gx, "env", "rtx-4090", os="Ubuntu", date="2024-01-01", basis="timeline")
            await mint_series(gx, "det", title="Detection series", date="2023-8-21")
            await set_series_members(gx, "det", ["yolo"])
            await _assert(gx, series_node_id("det"), "site_path", "/series/tutorials/det.html", raw=True)
            spec = {"selection": [{"verb": "list", "args": {"label": "Note", "deliverable_kind": "tutorial"}}],
                    "view": {"layout": "coverage-matrix"}}
            await set_lens(gx, "tutorials", spec, title="Tutorials", description="By task and stage.")
            await _assert(gx, lens_node_id("tutorials"), "site_path", "/series/tutorials/", raw=True)
            kinds = await list_graph(gx, label="Note", deliverable_kind="tutorial")
            wrong = await list_graph(gx, label="Series", deliverable_kind="tutorial")
            await set_lens(gx, "cut", {"selection": [{"verb": "list", "args": {"label": "Note", "limit": 1}}]})
            cut = await apply_lens(gx, "cut")
            pages = (await redirect_plan(gx))["pages"]
            pub = await page_plan(gx, str(root), "public", pages, drafts_dir=None)
            stg = await page_plan(gx, str(root), "staging", pages, drafts_dir=None)
            # A tutorial with no task fact refuses the page, never drops from it
            await _assert(gx, "n", "deliverable_type", "archive-tutorial", supersede=["archive-notes"])
            refused = await page_plan(gx, str(root), "public", pages, drafts_dir=None)
            return kinds, wrong, cut, pub, stg, refused
    kinds, wrong, cut, pub, stg, refused = asyncio.run(go())
    assert sorted(r["id"] for r in kinds["rows"]) == sorted([note_node_id("yolo"), note_node_id("env")])
    assert "label mode with --label Note only" in wrong["error"]
    assert "a truncated selection never lands" in cut["error"]
    assert pub["errors"] == []
    page = next(p for p in pub["pages"] if p["source"] == "series/tutorials/index.qmd")
    text = page["text"]
    assert text.startswith(f"---\n{GENERATED}\ntitle: Tutorials\ndescription: By task and stage.\n")
    assert "date-modified: '2024-01-01'" in text and "page-layout: full" in text
    assert "listing:\n  id: learning-paths\n" in text and "  contents:\n  - det.qmd\n" in text
    assert "  categories: false\n" in text   # no sidebar: it read as the grid's filter (15e7b315)
    assert "[1](#object-detection-training)" in text and "[General](#general-setup)" in text
    assert "_(Tested on RTX 4090, Ubuntu)_" in text and "[timeline]" not in text
    assert "About Train YOLOX." in text and "A Note" not in text
    staged = next(p for p in stg["pages"] if p["source"] == "series/tutorials/index.qmd")["text"]
    assert "Tested on RTX 4090, Ubuntu [timeline]" in staged and "## Gaps {#gaps}" in staged
    assert [(e["kind"], e.get("reason")) for e in refused["errors"]] == [("matrix-refusal", "unsurveyed")]


async def _assert(gx, subject, predicate, value, raw=False, supersede=None):
    from cjm_context_graph_projection.write import assert_value
    res = await assert_value(gx, subject if raw else note_node_id(subject), predicate, value, supersede=supersede)
    assert not res.get("error"), res
    return res
