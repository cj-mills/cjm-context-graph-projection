"""Tests for the fact layer's label reads (finding a107e2f3): a whole-label read returns
EVERY row across keyset pages, and a window is ordered in SQL before it is cut."""

import asyncio
from pathlib import Path

import pytest
from cjm_context_graph_layer.ops import extend_graph
from cjm_context_graph_primitives.query import OrderBy, PropertyPredicate
from cjm_context_graph_projection import factlayer as F
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph

pytestmark = pytest.mark.skipif(
    not (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists(),
    reason="needs the graph capability")


def test_whole_label_reads_cross_pages_and_windows_are_ordered(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "_LABEL_PAGE", 3)  # ten rows -> four keyset pages, the last short

    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            nodes = [{"id": f"n{i:02d}", "label": "Doc",
                      "properties": {"rank": i % 4, "parity": "even" if i % 2 == 0 else "odd"}}
                     for i in range(10)]
            await extend_graph(gx.queue, gx.graph_id, nodes, [])
            ids = lambda ns: [F.nid(n) for n in ns]
            return (ids(await F.load_label(gx, "Doc")),
                    ids(await F.load_label_where(gx, "Doc", [PropertyPredicate("parity", "eq", "even")])),
                    ids(await F.load_label_where(gx, "Doc", [], limit=3, offset=2)),
                    ids(await F.load_label_where(gx, "Doc", [], limit=4,
                                                 order_by=OrderBy("rank", descending=True))))

    every, evens, window, ranked = asyncio.run(go())
    assert every == [f"n{i:02d}" for i in range(10)]
    assert evens == ["n00", "n02", "n04", "n06", "n08"]
    assert window == ["n02", "n03", "n04"]
    assert ranked == ["n03", "n07", "n02", "n06"]  # rank 3 then 2, ties broken by the id
