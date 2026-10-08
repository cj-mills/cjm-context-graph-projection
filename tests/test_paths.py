"""The path model (design ae698640, the walk 57287d1b, the refactor leg ad9bef5a): the records of
the four Entity sub-kinds checked live with their edges landed from the record, the relations'
endpoint checks, and the DERIVED reads over two fixtures -- walk case (1) (the YOLOX and Barracuda
PoseNet paths) returning the walk's findings, and a two-loop flywheel yielding two kind-level
cycles over acyclic instance lineage; a rebuild from the journal reproduces the graph."""

import asyncio
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from cjm_context_graph_layer.ops import extend_graph
from cjm_dev_graph_schema import predicates as P
from cjm_dev_graph_schema.identity import entity_node_id, note_node_id, section_node_id
from cjm_markdown_decompose_core.extract import note_from_text
from cjm_markdown_decompose_core.ingest import corpus_graph_elements

from cjm_context_graph_projection.coverage import mint_entities, mint_entity, record_verification, validate_entity
from cjm_context_graph_projection.paths import flywheel_cycles, path_reads, record_relation
from cjm_context_graph_projection.render import render
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.write import assert_value
from conftest import commit_all

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()
pytestmark_graph = pytest.mark.skipif(not _HAVE_GRAPH, reason="needs the graph capability")


def A(key):   # an artifact's id
    return entity_node_id("artifact", key)


def N(slug):  # a post's id
    return note_node_id(slug)


def test_record_shapes_are_checked_before_the_graph():
    ok = validate_entity("artifact", "onnx", "ONNX", {"artifact_kind": "exported_model", "target": "hardware:jetson",
                                                     "derived_from": ["ckpt", "weights"]})
    assert ok is None
    assert "needs artifact_kind" in validate_entity("artifact", "onnx", "ONNX", {})
    assert "takes `kind:key`" in validate_entity("artifact", "onnx", "O", {"artifact_kind": "x", "target": "jetson"})
    assert "takes `kind:key`" in validate_entity("artifact", "onnx", "O", {"artifact_kind": "x", "target": "tool:x"})
    assert "cannot name itself" in validate_entity("artifact", "onnx", "O", {"artifact_kind": "x", "derived_from": ["onnx"]})
    assert "repeats a value" in validate_entity("artifact", "onnx", "O", {"artifact_kind": "x", "derived_from": ["a", "a"]})
    assert "must be list" in validate_entity("artifact", "onnx", "O", {"artifact_kind": "x", "derived_from": "a"})
    assert validate_entity("environment", "cuda", "CUDA", {"description": "d", "parts": ["tool:pytorch", "hardware:gpu"],
                                                         "variants": ["Mamba", "Conda"]}) is None
    assert "takes `kind:key`" in validate_entity("environment", "cuda", "C", {"description": "d", "parts": ["model:x"]})
    assert "needs description, not_for, subject" in validate_entity("concept", "tracking", "T", {})
    assert validate_entity("stage", "training", "Training", {"position": 1, "transitions": [
        {"in": ["dataset"], "optional": ["checkpoint"], "out": "checkpoint"}]}) is None
    assert "both `in` and `optional`" in validate_entity("stage", "t", "T", {"position": 1, "transitions": [
        {"in": ["dataset"], "optional": ["dataset"], "out": "checkpoint"}]})
    assert "needs its `out`" in validate_entity("stage", "t", "T", {"position": 1, "transitions": [{"in": []}]})
    assert "carries no to" in validate_entity("stage", "t", "T", {"position": 1, "transitions": [{"out": "x", "to": 1}]})


# --- walk case (1): the vocabulary, the artifacts and the posts --------------------------------

def _crit(desc):
    return {"description": desc, "not_for": "anything else"}


_VOCAB = (
    [("artifact_kind", k, {"description": k, "position": i}) for i, k in enumerate(
        ("dataset", "checkpoint", "exported_model", "calibration_data", "compiled_model", "application",
         "predictions", "unity_package"), 1)]
    + [("task", "object-detection", {"position": 1}), ("task", "pose-estimation", {"position": 2}),
       ("stage", "training", {"position": 1, "transitions": [
           {"in": ["dataset"], "optional": ["checkpoint"], "out": "checkpoint"}]}),
       ("stage", "export", {"position": 2, "transitions": [
           {"in": ["checkpoint"], "out": "exported_model"}, {"in": ["exported_model"], "out": "exported_model"}]}),
       ("stage", "optimization", {"position": 3, "transitions": [
           {"in": ["exported_model"], "optional": ["calibration_data"], "out": "compiled_model"},
           {"in": ["exported_model"], "out": "calibration_data"}]}),
       ("stage", "deployment", {"position": 4, "transitions": [
           {"in": ["compiled_model"], "out": "application"}, {"in": ["exported_model"], "out": "application"}]}),
       ("stage", "setup", {"position": 5, "transitions": [{"in": [], "out": "environment"}]})]
    + [("model", m, _crit(m)) for m in ("yolox", "keypoint-rcnn", "posenet")]
    + [("tool", t, _crit(t)) for t in ("pytorch", "tensorrt", "hailo-dfc", "unity")]
    + [("subject", "computer-vision", _crit("cv"))]
    + [("hardware", "jetson-orin-nano", {"device_class": "board"}),
       ("hardware", "raspberry-pi-5", {"device_class": "board"}),
       ("hardware", "desktop-gpu", {"device_class": "gpu"})]
    + [("environment", "nvidia-driver", {"description": "the NVIDIA driver", "parts": ["hardware:desktop-gpu"]}),
       ("environment", "pytorch-cuda", {"description": "PyTorch + CUDA", "parts": ["tool:pytorch", "hardware:desktop-gpu"],
                                        "requires": ["nvidia-driver"], "variants": ["Mamba", "Conda", "Google Colab"]}),
       ("environment", "jetson-jp6", {"description": "a Jetson on JetPack 6",
                                      "parts": ["hardware:jetson-orin-nano", "tool:tensorrt"]}),
       ("environment", "hailo-dfc-x86", {"description": "the Hailo DFC on x86 Linux", "parts": ["tool:hailo-dfc"]}),
       ("environment", "pi-hailo", {"description": "a Pi 5 with the AI Kit", "parts": ["hardware:raspberry-pi-5"]})]
    + [("concept", c, {**_crit(c), "subject": "computer-vision"}) for c in ("object-tracking", "onnx-basics", "cuda-basics")]
    + [("work", "onnx-docs", {"form": "documentation"}), ("work", "cv-course", {"form": "course"}),
       ("unit", "cv-course/tracking", {"position": 1})]
)

_ARTIFACTS = [   # (key, fields) in lineage order
    ("hagrid", {"artifact_kind": "dataset", "locator": "https://github.com/hukenovs/hagrid"}),
    ("yolox-pretrained", {"artifact_kind": "checkpoint", "base_model": "yolox"}),
    ("yolox-hagrid-ckpt", {"artifact_kind": "checkpoint", "task": "object-detection", "base_model": "yolox",
                           "derived_from": ["hagrid", "yolox-pretrained"]}),
    ("yolox-hagrid-onnx", {"artifact_kind": "exported_model", "format": "ONNX", "base_model": "yolox",
                           "task": "object-detection", "derived_from": ["yolox-hagrid-ckpt"]}),
    ("yolox-hagrid-tfjs", {"artifact_kind": "exported_model", "format": "TFJS", "base_model": "yolox",
                           "task": "object-detection", "derived_from": ["yolox-hagrid-ckpt"]}),
    ("yolox-hagrid-calib", {"artifact_kind": "calibration_data", "derived_from": ["yolox-hagrid-onnx"]}),
    ("yolox-hagrid-trt", {"artifact_kind": "compiled_model", "precision": "int8", "base_model": "yolox",
                          "target": "hardware:jetson-orin-nano", "derived_from": ["yolox-hagrid-onnx", "yolox-hagrid-calib"]}),
    ("yolox-hagrid-hef", {"artifact_kind": "compiled_model", "precision": "int8", "format": "HEF", "base_model": "yolox",
                          "target": "hardware:raspberry-pi-5", "derived_from": ["yolox-hagrid-onnx"]}),
    ("pi-demo", {"artifact_kind": "application", "derived_from": ["yolox-hagrid-hef"]}),
    ("hef-app2", {"artifact_kind": "application", "derived_from": ["yolox-hagrid-hef"]}),
    ("keypoint-rcnn-ckpt", {"artifact_kind": "checkpoint", "base_model": "keypoint-rcnn"}),
    ("keypoint-rcnn-onnx", {"artifact_kind": "exported_model", "format": "ONNX", "base_model": "keypoint-rcnn",
                            "derived_from": ["keypoint-rcnn-ckpt"]}),
    ("posenet-tfjs", {"artifact_kind": "exported_model", "format": "TFJS graph model", "base_model": "posenet",
                      "locator": "https://github.com/tensorflow/tfjs-models"}),
    ("posenet-savedmodel", {"artifact_kind": "exported_model", "format": "SavedModel", "base_model": "posenet",
                            "derived_from": ["posenet-tfjs"]}),
    ("posenet-onnx", {"artifact_kind": "exported_model", "format": "ONNX", "base_model": "posenet",
                      "derived_from": ["posenet-savedmodel"]}),
    ("posenet-unity-project", {"artifact_kind": "application", "derived_from": ["posenet-onnx"]}),
]

_POSTS = ("yolox-training", "onnx-export", "tfjs-export", "bytetrack", "ubuntu-quantization", "jetson", "pi",
          "hailo-app2", "setup-jetson", "keypoint-onnx-export", "tfjs-to-savedmodel", "savedmodel-to-onnx",
          "barracuda-posenet-1", "barracuda-walkthrough", "barracuda-log")

_RELATIONS = [   # (relation, source, target, strength)
    ("REQUIRES", "yolox-training", "artifact:hagrid", ""),
    ("REQUIRES", "yolox-training", "artifact:yolox-pretrained", ""),
    ("REQUIRES", "yolox-training", "environment:pytorch-cuda", ""),
    ("PRODUCES", "yolox-training", "artifact:yolox-hagrid-ckpt", ""),
    ("REQUIRES", "onnx-export", "artifact:yolox-hagrid-ckpt", ""),
    ("ASSUMES", "onnx-export", "concept:onnx-basics", "recommended"),
    ("PRODUCES", "onnx-export", "artifact:yolox-hagrid-onnx", ""),
    ("REQUIRES", "tfjs-export", "artifact:yolox-hagrid-ckpt", ""),
    ("PRODUCES", "tfjs-export", "artifact:yolox-hagrid-tfjs", ""),
    ("REQUIRES", "bytetrack", "artifact:yolox-hagrid-onnx", ""),
    ("TEACHES", "bytetrack", "concept:object-tracking", ""),
    ("REQUIRES", "ubuntu-quantization", "artifact:yolox-hagrid-onnx", ""),
    ("PRODUCES", "ubuntu-quantization", "artifact:yolox-hagrid-calib", ""),
    ("REQUIRES", "jetson", "artifact:yolox-hagrid-onnx", ""),
    ("REQUIRES", "jetson", "artifact:yolox-hagrid-calib", ""),
    ("REQUIRES", "jetson", "environment:jetson-jp6", ""),
    ("ASSUMES", "jetson", "concept:object-tracking", ""),
    ("ASSUMES", "jetson", "concept:cuda-basics", "recommended"),
    ("PRODUCES", "jetson", "artifact:yolox-hagrid-trt", ""),
    ("REQUIRES", "pi", "artifact:yolox-hagrid-onnx", ""),
    ("REQUIRES", "pi", "environment:hailo-dfc-x86", ""),
    ("REQUIRES", "pi", "environment:pi-hailo", ""),
    ("ASSUMES", "pi", "concept:object-tracking", ""),
    ("PRODUCES", "pi", "artifact:yolox-hagrid-hef", ""),
    ("PRODUCES", "pi", "artifact:pi-demo", ""),
    ("REQUIRES", "hailo-app2", "artifact:yolox-hagrid-hef", ""),
    ("PRODUCES", "hailo-app2", "artifact:hef-app2", ""),
    ("REQUIRES", "keypoint-onnx-export", "artifact:keypoint-rcnn-ckpt", ""),
    ("PRODUCES", "keypoint-onnx-export", "artifact:keypoint-rcnn-onnx", ""),
    ("REQUIRES", "tfjs-to-savedmodel", "artifact:posenet-tfjs", ""),
    ("PRODUCES", "tfjs-to-savedmodel", "artifact:posenet-savedmodel", ""),
    ("REQUIRES", "savedmodel-to-onnx", "artifact:posenet-savedmodel", ""),
    ("PRODUCES", "savedmodel-to-onnx", "artifact:posenet-onnx", ""),
    ("REQUIRES", "barracuda-posenet-1", "artifact:posenet-onnx", ""),
    ("PRODUCES", "barracuda-posenet-1", "artifact:posenet-unity-project", ""),
    ("EXPLAINS", "barracuda-walkthrough", "artifact:posenet-unity-project", ""),
    ("EXPLAINS", "barracuda-log", "barracuda-posenet-1", ""),
    ("COVERS", "work:onnx-docs", "concept:onnx-basics", ""),
    ("COVERS", "unit:cv-course/tracking", "concept:object-tracking", ""),
]


def _post(slug):
    body = "## Setup\n\nFlash the board.\n\n## Steps\n\nDo it.\n" if slug == "setup-jetson" else "## Overview\n\nBody.\n"
    return f"---\ntitle: \"{slug}\"\ndate: 2024-01-01\n---\n\n{body}"


async def _walk(gx):
    notes = [note_from_text(f"/c/posts/{s}/index.md", _post(s), corpus_root="/c/posts", lossless=True) for s in _POSTS]
    nodes, edges = corpus_graph_elements(notes)
    await extend_graph(gx.queue, gx.graph_id, nodes, edges)
    res = await mint_entities(gx, [{"kind": k, "key": key, "name": key, "fields": f} for k, key, f in _VOCAB], apply=True)
    assert not res["errors"], res["errors"]
    for key, fields in _ARTIFACTS:
        r = await mint_entity(gx, "artifact", key, name=key, fields=fields)
        assert r["written"], (key, r)
    for rel, src, tgt, strength in _RELATIONS:
        r = await record_relation(gx, rel, src, tgt, strength=strength)
        assert r["written"], (rel, src, tgt, r)
    # the setup guide's setup-role SECTION produces its environment (ad9bef5a (1))
    sec = section_node_id(N("setup-jetson"), "setup")
    r = await record_relation(gx, "PRODUCES", sec, "environment:jetson-jp6")
    assert r["written"], r
    jp6 = entity_node_id("environment", "jetson-jp6")
    await assert_value(gx, jp6, P.ENVIRONMENT_VERSIONS, '{"jetpack": "6.0", "tensorrt": "10.3"}')
    up = await assert_value(gx, jp6, P.ENVIRONMENT_VERSIONS, '{"tensorrt": "10.3", "jetpack": "6.1"}',
                            supersede=['{"jetpack":"6.0","tensorrt":"10.3"}'])
    assert up.get("written", True) and not up.get("conflict"), up
    await record_verification(gx, "jetson", "jetson-orin-nano", os="Ubuntu 22.04", date="2024-09-01",
                              versions={"jetpack": "6.0", "tensorrt": "10.3"})


@pytestmark_graph
def test_walk_case_one_derives_the_walks_findings(tmp_path):
    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            await _walk(gx)
            return (await path_reads(gx, "all"), await path_reads(gx, "step", node="pi"),
                    await path_reads(gx, "step", node="barracuda-posenet-1"),
                    await path_reads(gx, "prepares", node="work:cv-course"),
                    await path_reads(gx, "prepares", node="work:onnx-docs"))
    res, pi, pt1, course, docs = asyncio.run(go())
    cont = {(c["step"], c["from"], c["via"]) for c in res["continues"]}
    # the ONNX export continues from training; the converters chain into the PoseNet series
    assert (N("onnx-export"), N("yolox-training"), A("yolox-hagrid-ckpt")) in cont
    assert (N("savedmodel-to-onnx"), N("tfjs-to-savedmodel"), A("posenet-savedmodel")) in cont
    assert (N("barracuda-posenet-1"), N("savedmodel-to-onnx"), A("posenet-onnx")) in cont
    # Jetson continues from the setup guide through its setup SECTION's environment, and from quantization
    assert (N("jetson"), N("setup-jetson"), entity_node_id("environment", "jetson-jp6")) in cont
    assert (N("jetson"), N("ubuntu-quantization"), A("yolox-hagrid-calib")) in cont
    # the ONNX / TFJS exports are alternatives at the checkpoint
    alts = {(a["input"], a["kind"]): a for a in res["alternatives"]}
    assert {m["step"] for m in alts[(A("yolox-hagrid-ckpt"), "exported_model")]["steps"]} == {N("onnx-export"), N("tfjs-export")}
    # Jetson and the Pi are alternatives at the ONNX model with different prerequisites
    edge = alts[(A("yolox-hagrid-onnx"), "compiled_model")]
    differs = {m["step"]: set(m["differs"]) for m in edge["steps"]}
    assert set(differs) == {N("jetson"), N("pi")}
    assert differs[N("jetson")] == {A("yolox-hagrid-calib"), entity_node_id("environment", "jetson-jp6")}
    assert differs[N("pi")] == {entity_node_id("environment", "hailo-dfc-x86"), entity_node_id("environment", "pi-hailo")}
    # the Pi post is a split candidate at the HEF (another step starts from it) and its stage is ambiguous
    assert res["splits"] == [{"step": N("pi"), "at": A("yolox-hagrid-hef"), "then": [A("pi-demo")],
                              "also_from": [N("hailo-app2")]}]
    st = res["stages"]
    assert st[N("pi")]["refusal"] == "ambiguous" and {r["step"] for r in res["refusals"]} == {N("pi")}
    # born-step stages and tasks derive; ByteTrack produces nothing, so it has no stage
    assert (st[N("yolox-training")]["stage"], st[N("yolox-training")]["task"]) == ("training", "object-detection")
    assert st[N("onnx-export")]["stage"] == "export" and st[N("jetson")]["stage"] == "optimization"
    assert st[N("ubuntu-quantization")]["stage"] == "optimization" and st[N("hailo-app2")]["stage"] == "deployment"
    assert st[N("setup-jetson")]["stage"] == "setup" and st[N("bytetrack")]["stage"] is None
    assert st[N("tfjs-to-savedmodel")]["stage"] == "export" and st[N("tfjs-to-savedmodel")]["transition"] == 1
    # analogues: the same export transition with another base model
    ana = {(a["stage"], a["transition"]): {m["step"]: m["base_models"] for m in a["steps"]} for a in res["analogues"]}
    assert ana[("export", 0)] == {N("onnx-export"): ["yolox"], N("tfjs-export"): ["yolox"],
                                  N("keypoint-onnx-export"): ["keypoint-rcnn"]}
    # gaps: CUDA basics is assumed and nothing teaches or covers it; tracking is taught, ONNX covered
    assert [(g["concept"], [a["step"] for a in g["assumed_by"]]) for g in res["gaps"]] == [
        (entity_node_id("concept", "cuda-basics"), [N("jetson")])]
    # staleness: Jetson was verified on JetPack 6.0, the environment is on 6.1
    stale = res["stale"]["stale"]
    assert [(s["step"], s["components"]) for s in stale] == [
        (N("jetson"), [{"component": "jetpack", "verified": "6.0", "current": "6.1"}])]
    assert res["cycles"]["cycles"] == []
    # one step in context: the Pi's alternatives and its split; Pt. 1's companions
    v = pi["step_view"]
    assert [a["kind"] for a in v["alternatives"]] == ["compiled_model"]
    assert {c["step"] for c in v["continued_by"]} == {N("hailo-app2")}
    w = pt1["step_view"]
    assert set(w["explained_by"]) == {N("barracuda-walkthrough"), N("barracuda-log")}
    assert [c["from"] for c in w["continues_from"]] == [N("savedmodel-to-onnx")]
    # prepares-you-for: a work through its unit's COVERS, a work through its own
    assert {r["step"] for r in course["prepares"][0]["prepares"]} == {N("jetson"), N("pi")}
    assert [r["step"] for r in docs["prepares"][0]["prepares"]] == [N("onnx-export")]
    out = render("paths", res)
    assert "Split candidates — 1" in out and "⚠ **ambiguous** pi" in out and "jetpack 6.0 < 6.1" in out


@pytestmark_graph
def test_write_time_checks_refuse_and_records_reconcile(tmp_path):
    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            await _walk(gx)
            out = {
                # lineage closing a cycle, an unknown or retired reference, a wrong endpoint kind
                "cycle": await mint_entity(gx, "artifact", "hagrid", name="h",
                                           fields={"artifact_kind": "dataset", "derived_from": ["pi-demo"]}),
                "env_cycle": await mint_entity(gx, "environment", "nvidia-driver", name="d",
                                               fields={"description": "d", "requires": ["pytorch-cuda"]}),
                "no_kind": await mint_entity(gx, "artifact", "x", name="x", fields={"artifact_kind": "weights"}),
                "no_subject": await mint_entity(gx, "concept", "c", name="c",
                                                fields={**_crit("c"), "subject": "nope"}),
                "bad_target": await record_relation(gx, "TEACHES", "bytetrack", "artifact:pi-demo"),
                "bad_source": await record_relation(gx, "COVERS", "bytetrack", "concept:onnx-basics"),
                "env_source": await record_relation(gx, "REQUIRES", "environment:pytorch-cuda",
                                                    "environment:nvidia-driver"),
                "section_req": await record_relation(gx, "REQUIRES", section_node_id(N("setup-jetson"), "setup"),
                                                     "artifact:hagrid"),
                "strength": await record_relation(gx, "PRODUCES", "pi", "artifact:pi-demo", strength="required"),
                "self": await record_relation(gx, "EXPLAINS", "pi", "pi"),
                "versions_subject": await assert_value(gx, A("hagrid"), P.ENVIRONMENT_VERSIONS, '{"a": "1"}'),
                "versions_value": await assert_value(gx, entity_node_id("environment", "pi-hailo"),
                                                     P.ENVIRONMENT_VERSIONS, "hailort 4.18"),
            }
            # a restated strength replaces the edge; a retraction removes it
            first = await record_relation(gx, "ASSUMES", "pi", "concept:cuda-basics")
            again = await record_relation(gx, "ASSUMES", "pi", "concept:cuda-basics", strength="recommended")
            gone = await record_relation(gx, "ASSUMES", "pi", "concept:cuda-basics", retract=True)
            # a re-mint reconciles the record's edges: the TFJS model now derives from the ONNX one only
            remint = await mint_entity(gx, "artifact", "yolox-hagrid-tfjs", name="t",
                                       fields={"artifact_kind": "exported_model", "derived_from": ["yolox-hagrid-onnx"]})
            await mint_entity(gx, "artifact_kind", "unity_package", name="u",
                              fields={"description": "u", "retired": True})
            retired = await mint_entity(gx, "artifact", "pkg", name="p", fields={"artifact_kind": "unity_package"})
            graph = await path_reads(gx, "all")
            return out, first, again, gone, remint, retired, graph
    out, first, again, gone, remint, retired, graph = asyncio.run(go())
    assert "close a cycle" in out["cycle"]["error"] and "close a cycle" in out["env_cycle"]["error"]
    assert "names no artifact_kind `weights`" in out["no_kind"]["error"]
    assert "names no subject `nope`" in out["no_subject"]["error"]
    assert "TEACHES runs to concept, never to a artifact" in out["bad_target"]["error"]
    assert "COVERS runs from unit | work" in out["bad_source"]["error"]
    assert "are its record's" in out["env_source"]["error"]
    assert "REQUIRES runs from deliverable | environment, never from a section" in out["section_req"]["error"]
    assert "carries no strength" in out["strength"]["error"] and "never one to itself" in out["self"]["error"]
    assert "belong to an environment Entity" in out["versions_subject"]["error"]
    assert "a JSON object of component versions" in out["versions_value"]["error"]
    assert first["edge_id"] == again["edge_id"] and again["replaced"] and again["strength"] == "recommended"
    assert gone["retracted"]
    assert remint["record_edges"] == {"landed": 1, "removed": 1}
    assert "which is retired" in retired["error"]
    # alternatives read the steps' relations, never the artifacts' lineage: the exports still pair
    assert any(a["kind"] == "exported_model" for a in graph["alternatives"])
    # the artifact naming a retired kind never landed, so the Pi's ambiguous stage is the one refusal
    assert [r["reason"] for r in graph["refusals"]] == ["ambiguous"]


# --- the two-loop flywheel ---------------------------------------------------------------------

_FLY = [
    ("ds-v1", {"artifact_kind": "dataset"}),
    ("ckpt-v1", {"artifact_kind": "checkpoint", "derived_from": ["ds-v1"]}),
    # loop 1, right after training: the model's predictions on its own data flag mislabeled samples
    ("preds-audit", {"artifact_kind": "predictions", "derived_from": ["ckpt-v1", "ds-v1"]}),
    ("ds-v2", {"artifact_kind": "dataset", "derived_from": ["ds-v1", "preds-audit"]}),
    # a class extension: trained from an earlier checkpoint on the corrected data
    ("ckpt-v2", {"artifact_kind": "checkpoint", "derived_from": ["ds-v2", "ckpt-v1"]}),
    ("onnx-v2", {"artifact_kind": "exported_model", "derived_from": ["ckpt-v2"]}),
    # loop 2, after deployment: inference predictions on new samples, reviewed and corrected
    ("field-samples", {"artifact_kind": "dataset"}),
    ("preds-field", {"artifact_kind": "predictions", "derived_from": ["onnx-v2", "field-samples"]}),
    ("ds-v3", {"artifact_kind": "dataset", "derived_from": ["ds-v2", "preds-field"]}),
]


@pytestmark_graph
def test_a_two_loop_flywheel_is_two_kind_level_cycles(tmp_path):
    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            res = await mint_entities(gx, [{"kind": "artifact_kind", "key": k, "name": k,
                                            "fields": {"description": k, "position": i}}
                                           for i, k in enumerate(("dataset", "checkpoint", "exported_model",
                                                                  "predictions"), 1)], apply=True)
            assert not res["errors"]
            for key, fields in _FLY:
                assert (await mint_entity(gx, "artifact", key, name=key, fields=fields))["written"]
            loop = await mint_entity(gx, "artifact", "ds-v1", name="ds-v1",
                                     fields={"artifact_kind": "dataset", "derived_from": ["ds-v3"]})
            return await path_reads(gx, "cycles"), loop
    res, loop = asyncio.run(go())
    assert "close a cycle" in loop["error"]   # instance lineage stays acyclic
    cycles = res["cycles"]["cycles"]
    assert [c["kinds"] for c in cycles] == [["dataset", "checkpoint", "predictions"],
                                            ["dataset", "checkpoint", "exported_model", "predictions"]]
    assert [A("ds-v1"), A("ckpt-v1"), A("preds-audit"), A("ds-v2")] in cycles[0]["chains"]
    assert [A("ds-v2"), A("ckpt-v2"), A("onnx-v2"), A("preds-field"), A("ds-v3")] in cycles[1]["chains"]
    # the shortcut through multi-parent lineage (dataset -> predictions -> dataset) is never its own cycle
    assert all([A("ds-v1"), A("preds-audit"), A("ds-v2")] != ch for c in cycles for ch in c["chains"])
    assert res["ok"] and "lineage_cycle" not in res["cycles"]


def test_a_lineage_cycle_is_refused_never_walked():
    g = {"entities": {A(k): {"entity_kind": "artifact", "artifact_kind": "dataset", "key": k} for k in ("a", "b")},
         "lineage": [(A("a"), A("b")), (A("b"), A("a"))], "relations": [], "deliverables": {}}
    out = flywheel_cycles(g, {})
    assert out["cycles"] == [] and set(out["lineage_cycle"]) == {A("a"), A("b")}


# --- the journal reproduces it -----------------------------------------------------------------

def _run(*args):
    return subprocess.run([sys.executable, "-m", "cjm_context_graph_projection.cli", *args],
                          capture_output=True, text=True)


def _ids(db):
    con = sqlite3.connect(str(db))
    try:
        return (sorted(r[0] for r in con.execute("select id from nodes")),
                sorted(r[0] for r in con.execute("select id from edges")))
    finally:
        con.close()


@pytestmark_graph
def test_a_rebuild_reproduces_the_path_model(tmp_path):
    corpus = tmp_path / "posts"
    for s in ("train", "export", "deploy", "setup"):
        (corpus / s).mkdir(parents=True)
        (corpus / s / "index.md").write_text(_post("setup-jetson" if s == "setup" else s))
    commit_all(corpus)
    journal = str(tmp_path / "writes.jsonl")
    for sub in ("live", "fresh"):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "graph.config.json").write_text(json.dumps(
            {"notes_corpus": str(corpus), "notes_profile": "quarto_post"}))
    live = str(tmp_path / "live" / "g.db")
    base = ["--graph-db-path", live, "--journal-path", journal]
    assert _run(*base, "ingest-notes").returncode == 0
    env = entity_node_id("environment", "jetson")
    sec = section_node_id(note_node_id("setup"), "setup")
    for cmd in (["entity", "artifact_kind", "dataset", "--name", "Dataset", "--description", "d", "--position", "1"],
                ["entity", "artifact_kind", "checkpoint", "--name", "Checkpoint", "--description", "c", "--position", "2"],
                ["entity", "artifact_kind", "exported_model", "--name", "Exported", "--description", "e", "--position", "3"],
                ["entity", "stage", "training", "--name", "Training", "--position", "1", "--transitions",
                 '[{"in": ["dataset"], "optional": ["checkpoint"], "out": "checkpoint"}]'],
                ["entity", "stage", "export", "--name", "Export", "--position", "2", "--transitions",
                 '[{"in": ["checkpoint"], "out": "exported_model"}]'],
                ["entity", "subject", "cv", "--name", "CV", "--description", "cv", "--not-for", "x"],
                ["entity", "tool", "tensorrt", "--name", "TensorRT", "--description", "t", "--not-for", "x"],
                ["entity", "hardware", "jetson-orin-nano", "--name", "Jetson", "--device-class", "board"],
                ["entity", "concept", "onnx-basics", "--name", "ONNX", "--description", "o", "--not-for", "x",
                 "--subject", "cv"],
                ["entity", "environment", "jetson-driver", "--name", "Driver", "--description", "d"],
                ["entity", "environment", "jetson", "--name", "Jetson JP6", "--description", "j",
                 "--part-of-env", "hardware:jetson-orin-nano", "--part-of-env", "tool:tensorrt",
                 "--requires", "jetson-driver", "--variant", "SDK Manager", "--variant", "SD card"],
                ["entity", "artifact", "data", "--name", "Data", "--artifact-kind", "dataset"],
                ["entity", "artifact", "ckpt", "--name", "Ckpt", "--artifact-kind", "checkpoint", "--derived-from", "data"],
                ["entity", "artifact", "onnx", "--name", "ONNX", "--artifact-kind", "exported_model",
                 "--format", "ONNX", "--target", "environment:jetson", "--derived-from", "ckpt"],
                # a re-mint drops a part (the record's edges reconcile on replay too)
                ["entity", "environment", "jetson", "--name", "Jetson JP6", "--description", "j",
                 "--part-of-env", "hardware:jetson-orin-nano", "--requires", "jetson-driver"],
                ["relate", "REQUIRES", "train", "artifact:data"],
                ["relate", "PRODUCES", "train", "artifact:ckpt"],
                ["relate", "REQUIRES", "export", "artifact:ckpt"],
                ["relate", "PRODUCES", "export", "artifact:onnx"],
                ["relate", "ASSUMES", "export", "concept:onnx-basics", "--strength", "recommended"],
                ["relate", "REQUIRES", "deploy", "artifact:onnx"],
                ["relate", "REQUIRES", "deploy", "environment:jetson"],
                ["relate", "ASSUMES", "deploy", "concept:onnx-basics"],
                ["relate", "ASSUMES", "deploy", "concept:onnx-basics", "--retract"],
                ["relate", "PRODUCES", sec, "environment:jetson", "--note", "the setup section"],
                ["assert", env, P.ENVIRONMENT_VERSIONS, '{"jetpack": "6.0"}'],
                ["assert", env, P.ENVIRONMENT_VERSIONS, '{"jetpack": "6.1"}', "--supersede", '{"jetpack":"6.0"}'],
                ["verified-on", "deploy", "jetson-orin-nano", "--date", "2024-05-01", "--version", "jetpack=6.0"]):
        r = _run(*base, *cmd)
        assert r.returncode == 0, (cmd, r.stdout, r.stderr)
    refused = _run(*base, "relate", "TEACHES", "train", "artifact:data")
    assert refused.returncode == 1 and "TEACHES runs to concept" in refused.stdout
    verbs = [json.loads(line)["verb"] for line in Path(journal).read_text().splitlines()]
    assert verbs.count("relate") == 10                                   # the refusal journaled nothing
    report = _run("--graph-db-path", live, "paths")
    assert report.returncode == 0, report.stdout + report.stderr
    assert "export `" in report.stdout and "jetpack 6.0 < 6.1" in report.stdout
    assert "Continues from — 3" in report.stdout

    fresh = str(tmp_path / "fresh" / "g.db")
    r = _run("--graph-db-path", fresh, "--journal-path", journal, "ingest-notes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _ids(fresh) == _ids(live)
    assert _run("--graph-db-path", fresh, "paths").stdout == report.stdout
    step = [_run("--graph-db-path", db, "paths", "step", "deploy").stdout for db in (live, fresh)]
    assert step[0] == step[1] and "continues from" in step[0]
