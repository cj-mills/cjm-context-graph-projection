"""A post's categories (design ce17606b (1), (4)): one reader -- the confirmed facets and a
tutorial's teaches_* facts, the matrix's structural rows never -- chips by display name in the
order task, stage, model, tool, subject; a Series page's chips are what most members carry."""

from cjm_dev_graph_schema import predicates as P

from cjm_context_graph_projection.categories import chip_vocab, majority, post_chips


def _e(kind, key, name):
    return {"entity_kind": kind, "key": key, "name": name}


_VOCAB = {"tool:pytorch": _e("tool", "pytorch", "PyTorch"), "tool:onnx": _e("tool", "onnx", "ONNX"),
          "subject:nlp": _e("subject", "nlp", "NLP"), "model:yolox": _e("model", "yolox", "YOLOX"),
          "task:detection": _e("task", "detection", "Object detection"),
          "stage:training": _e("stage", "training", "Training")}


def test_chips_read_the_facets_in_chip_order():
    v = chip_vocab(_VOCAB)
    assert v["errors"] == []
    # task -> stage -> model -> tool -> subject (the user's order); within a kind, the vocabulary's
    assert list(v["rank"]) == ["Object detection", "Training", "YOLOX", "PyTorch", "ONNX", "NLP"]
    facts = {P.USES_TOOL: ["onnx", "pytorch"], P.ABOUT_SUBJECT: ["nlp"], P.USES_MODEL: ["yolox"],
             P.TEACHES_TASK: ["detection", "general"], P.TEACHES_STAGE: ["training"],
             "deliverable_type": ["archive-tutorial"]}
    # a value naming no live facet entry (the matrix's `general` row) and a non-facet fact render nothing
    assert post_chips(facts, v["names"], v["rank"]) == ["Object detection", "Training", "YOLOX", "PyTorch",
                                                        "ONNX", "NLP"]
    assert post_chips({}, v["names"], v["rank"]) == []


def test_two_kinds_sharing_a_display_name_refuse():
    v = chip_vocab({**_VOCAB, "subject:pytorch": _e("subject", "pytorch", "PyTorch")})
    assert [(e["kind"], e["entries"]) for e in v["errors"]] == [("category-name-clash",
                                                                 ["tool:pytorch", "subject:pytorch"])]


def test_a_series_carries_what_most_of_its_members_carry():
    rank = {"Object detection": 0, "YOLOX": 1, "PyTorch": 2}
    members = [["YOLOX", "PyTorch"], ["YOLOX", "Object detection"], ["PyTorch", "YOLOX"], ["Object detection"]]
    assert majority(members, rank) == ["YOLOX"]               # PyTorch 2 of 4 is not more than half
    assert majority(members[:3], rank) == ["YOLOX", "PyTorch"]
    assert majority([], rank) == []
