"""The projected listing (design 0efb5497 + amendment e66296bd; build 5ad21874): the order, the
filter's kinds, the chips' hrefs and the markup every listing page carries."""

import html
import json
import re

from cjm_dev_graph_schema import predicates as P

from cjm_context_graph_projection.sitelisting import (CHIP_LIMIT, PAGE_SIZE, SCRIPT_FILE, WORDS, chip_hrefs,
                                                      date_text, listing_kinds, listing_script, output_href,
                                                      render_listing, sort_rows)


def _item(title, day="", cats=(), desc="", href=None, updated=""):
    return {"source": f"posts/{title}/index.md", "href": href or f"../posts/{title}/index.html", "title": title,
            "description": desc, "date": day, "updated": updated or day, "categories": list(cats)}


def test_the_listing_order_is_the_lens_sort_with_undated_rows_last():
    rows = [_item("b", "2021-01-01"), _item("a", "2022-05-01"), _item("c"), _item("d", "2022-05-01")]
    got = sort_rows(rows, ["date desc", "title desc"])
    assert [r["title"] for r in got["rows"]] == ["d", "a", "b", "c"]
    # the date-modified key reads a row's updated date (the learning paths' and the logs' order)
    rows = [_item("x", "2020-01-01", updated="2024-01-01"), _item("y", "2023-01-01", updated="2023-01-01")]
    assert [r["title"] for r in sort_rows(rows, ["date-modified desc"])["rows"]] == ["x", "y"]
    # a key the build plans no value for refuses, never guesses
    assert "reading-time" in sort_rows(rows, ["reading-time"])["error"]


def test_the_filter_offers_only_the_categories_the_listing_carries_in_chip_order():
    kinds = {"Object detection": P.ENTITY_TASK, "Training": P.ENTITY_STAGE, "PyTorch": P.ENTITY_TOOL,
             "ONNX": P.ENTITY_TOOL, "Unused": P.ENTITY_MODEL}
    rank = {"Object detection": 0, "Training": 1, "Unused": 2, "PyTorch": 3, "ONNX": 4}
    items = [_item("a", cats=["ONNX", "Object detection"]), _item("b", cats=["PyTorch", "Object detection", "Training"])]
    got = listing_kinds(items, kinds, rank)
    assert got == [{"key": P.ENTITY_TASK, "label": "Tasks", "cats": ["Object detection"]},
                   {"key": P.ENTITY_STAGE, "label": "Stages", "cats": ["Training"]},
                   {"key": P.ENTITY_TOOL, "label": "Tools", "cats": ["PyTorch", "ONNX"]}]
    # a category page leaves its own category out (every item carries it), and an emptied kind goes
    assert [k["key"] for k in listing_kinds(items, kinds, rank, exclude=["Object detection"])] == [
        P.ENTITY_STAGE, P.ENTITY_TOOL]


def test_a_chip_links_its_page_else_the_filtered_listing_and_toggles_in_place_on_the_listing():
    pages = {"PyTorch": "/categories/pytorch/"}
    assert chip_hrefs(["PyTorch", "Object detection"], "/blog.html", pages) == {
        "PyTorch": "/categories/pytorch/", "Object detection": "/blog.html#category=Object%20detection"}
    # on the category listing every chip names the listing filtered to it (the script toggles it)
    assert chip_hrefs(["PyTorch"], "/blog.html", pages, in_place=True) == {"PyTorch": "/blog.html#category=PyTorch"}
    # no category listing and no page: a label
    assert chip_hrefs(["PyTorch"], "", None) == {} and chip_hrefs(["PyTorch"], "", None, in_place=True) == {}


def test_every_item_is_in_the_markup_with_its_data_and_the_script_named_last():
    items = [_item("Two <b>", "2023-08-22", ["YOLOX", "Odd"], desc="See [part 1](../one/index.md#setup)."),
             _item("One", "2023-08-21", ["YOLOX"])]
    kinds = [{"key": P.ENTITY_MODEL, "label": "Models", "cats": ["YOLOX"]}]
    text = render_listing(items, "series/tutorials/yolox.qmd", kinds=kinds,
                          hrefs={"YOLOX": "/categories/yolox/"}, numbered=True, noun="parts")
    cfg = json.loads(html.unescape(re.search(r'data-listing="([^"]+)"', text).group(1)))
    assert cfg == {"kinds": kinds, "noun": ["part", "parts"], "orders": ["Reading order", "Reversed"],
                   "chips": "link", "pageSize": PAGE_SIZE, "chipLimit": CHIP_LIMIT}
    # the authored order, numbered, the date above the title, the title escaped
    assert re.findall(r'data-position="(\d)"', text) == ["1", "2"]
    assert '<p class="listing-date">Part 1 · Aug 22, 2023</p>' in text
    assert '<a href="../posts/Two &lt;b&gt;/index.html">Two &lt;b&gt;</a></h3>' in text
    assert 'data-categories="[&quot;YOLOX&quot;, &quot;Odd&quot;]"' in text
    # a chip with nowhere to link is a label; the rest link
    assert '<a class="listing-category kit-chip" href="/categories/yolox/">YOLOX</a>' in text
    assert '<span class="listing-category kit-chip">Odd</span>' in text
    # the description stays the author's markdown, its relative link rebased from the post onto the page
    assert "\nSee [part 1](../posts/one/index.md#setup).\n" in text
    # an item with no description closes without the description block
    assert text.count('<div class="listing-description">') == 1 and text.count("</li>") == 2
    assert text.rstrip().endswith('<script src="../../js/listing.js" defer></script>\n```')
    # a dated listing's order labels; on the category listing chips filter in place
    dated = render_listing(items, "blog.qmd", kinds=kinds, hrefs={}, in_place=True)
    cfg = json.loads(html.unescape(re.search(r'data-listing="([^"]+)"', dated).group(1)))
    assert cfg["orders"] == ["Newest first", "Oldest first"] and cfg["chips"] == "filter"
    assert '<p class="listing-date">Aug 22, 2023</p>' in dated and 'src="js/listing.js"' in dated


def test_the_site_script_carries_every_word_and_the_hash_contract():
    js = listing_script()
    assert "__WORDS__" not in js and json.dumps(WORDS["search_hint"]) in js
    # the one-category form other pages link (#category=<URI-encoded name>), several categories, the
    # search, the order and the page on the same hash
    assert '"category=" + encodeURIComponent(c)' in js and "order=reversed" in js
    assert "history[how === \"replace\" ? \"replaceState\" : \"pushState\"]" in js
    assert 'addEventListener("popstate"' in js and 'addEventListener("hashchange"' in js
    assert SCRIPT_FILE == "js/listing.js"


def test_hrefs_and_dates_as_the_listing_shows_them():
    assert output_href("posts/x/index.ipynb", "series/tutorials/p.qmd") == "../../posts/x/index.html"
    assert output_href("posts/x/index.md", "blog.qmd") == "posts/x/index.html"
    assert date_text("2025-10-04") == "Oct 4, 2025" and date_text("") == ""
