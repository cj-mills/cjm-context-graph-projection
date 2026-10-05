"""Category pages (design a62f2499): every category's URL is a path fact on its Entity, drawn from
its display name in one flat namespace; a chip links its category's page where one exists, else
the category listing filtered to it; an entry below the threshold serves a redirect stub into
that filtered listing; the index lists the earned pages by kind in the chip order."""

from cjm_context_graph_projection.categorypages import _stub, category_slug, index_body
from cjm_context_graph_projection.postpage import category_links, header_meta


def test_a_slug_is_drawn_from_the_display_name():
    assert category_slug("conda / Mamba") == "conda-mamba"
    assert category_slug("LLM (CLI)") == "llm-cli"
    assert category_slug("Hugging Face Transformers") == "hugging-face-transformers"
    assert category_slug("C++") == "c" and category_slug("!!") == ""


def test_a_chip_links_its_page_else_the_filtered_listing():
    pages = {"PyTorch": "/categories/pytorch/"}
    assert category_links(["PyTorch", "Object detection"], "/blog.html", pages) == [
        {"name": "PyTorch", "href": "/categories/pytorch/"},
        {"name": "Object detection", "href": "/blog.html#category=Object%20detection"}]
    # no listing: only a category with a page links; neither: nothing links
    assert category_links(["PyTorch", "ONNX"], "", pages) == [{"name": "PyTorch", "href": "/categories/pytorch/"}]
    assert category_links(["PyTorch"], "", None) == [] and category_links([], "/blog.html", pages) == []
    # the post header's links read the same encoder
    meta = header_meta("notes", "archive", {}, {}, "/blog.html", categories=["PyTorch", "ONNX"], pages=pages)
    assert meta["set"]["category-links"] == [{"name": "PyTorch", "href": "/categories/pytorch/"},
                                             {"name": "ONNX", "href": "/blog.html#category=ONNX"}]


def test_a_stub_redirects_relative_to_its_own_dir():
    assert _stub("/categories/vision/", "/blog.html#category=Vision", "s") == {
        "stub": "categories/vision/index.html", "alias": "/categories/vision/", "subject": "s",
        "target": "../../blog.html#category=Vision"}
    assert _stub("/categories/old/", "/categories/", "s")["target"] == "../index.html"


def test_the_index_groups_earned_pages_by_kind_in_chip_order():
    def e(kind, name, n, desc=""):
        return {"entry": {"entity_kind": kind, "description": desc}, "name": name, "ids": list(range(n)),
                "source": f"categories/{category_slug(name)}/index.qmd"}
    body = index_body([e("task", "Object detection", 17, "Boxes."), e("subject", "History", 1),
                       e("tool", "PyTorch", 42)], "categories/index.qmd")
    assert body.split("\n") == [
        "## Tasks", "", "- [Object detection](object-detection/index.qmd) · 17 posts · Boxes.", "",
        "## Tools", "", "- [PyTorch](pytorch/index.qmd) · 42 posts", "",
        "## Subjects", "", "- [History](history/index.qmd) · 1 post", ""]
