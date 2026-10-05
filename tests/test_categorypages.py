"""Category pages (design a62f2499): every category's URL is a path fact on its Entity, drawn from
its display name in one flat namespace; a chip links its category's page where one exists, else
the category listing filtered to it; an entry below the threshold serves a redirect stub into
that filtered listing; the index lists the earned pages by kind in the chip order."""

from cjm_context_graph_projection.categorypages import (_stub, category_slug, description_basis, description_criteria,
                                                       description_stale, entry_record, index_body, parse_descriptions)
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
    def e(kind, name, n, desc=""):   # the line reads the page's description, never the entry's criteria
        return {"entry": {"entity_kind": kind, "description": "criteria"}, "name": name, "ids": list(range(n)),
                "source": f"categories/{category_slug(name)}/index.qmd", "description": desc}
    body = index_body([e("task", "Object detection", 17, "Boxes."), e("subject", "History", 1),
                       e("tool", "PyTorch", 42)], "categories/index.qmd")
    assert body.split("\n") == [
        "## Tasks", "", "- [Object detection](object-detection/index.qmd) · 17 posts · Boxes.", "",
        "## Tools", "", "- [PyTorch](pytorch/index.qmd) · 42 posts", "",
        "## Subjects", "", "- [History](history/index.qmd) · 1 post", ""]


def test_a_description_document_parses_whole_or_refuses():
    # Amendment e38d403c (5): each section's description runs from its label to the next blank
    # line, whitespace collapsed; a blank one stays unwritten; a missing label or a repeated
    # entry refuses the file
    doc = "\n".join([
        "# Category descriptions", "", "Write on the `Page description:` line.", "",
        "## PyTorch · tool `pytorch` · 2 posts <!-- category tool:pytorch aaa -->", "",
        "Page description: Posts that build",
        "  with PyTorch.", "", "_Criteria: x_", "", "<details><summary>2 posts</summary>", "", "- A", "</details>", "",
        "## Vision · subject `vision` · 1 post <!-- category subject:vision bbb -->", "", "Page description:", ""])
    assert parse_descriptions(doc) == {"rows": [
        {"entry": "tool:pytorch", "basis": "aaa", "text": "Posts that build with PyTorch."},
        {"entry": "subject:vision", "basis": "bbb", "text": ""}], "errors": []}
    bad = doc.replace("Page description:\n", "No label\n") + "\n## Again <!-- category tool:pytorch ccc -->\n\nPage description: y\n"
    assert [e.split(": ", 1)[1] for e in parse_descriptions(bad)["errors"]] == [
        "the entry tool:pytorch appears twice", "subject:vision has no 'Page description:' line"]


def test_a_description_is_part_of_the_record_never_the_criteria():
    # Amendment e38d403c (1): the whole record carries page_description, so the document's basis
    # moves with it; the judge's criteria (entry_question) never read it
    from cjm_context_graph_projection.facetjudge import criteria_hash, entry_question
    v = {"entity_kind": "tool", "key": "pytorch", "name": "PyTorch", "description": "PyTorch as the tool",
         "not_for": "a mention", "retired": None, "id": "x"}
    described = {**v, "page_description": "Posts that build with PyTorch."}
    assert entry_record(v) == {"kind": "tool", "key": "pytorch", "name": "PyTorch",
                               "fields": {"description": "PyTorch as the tool", "not_for": "a mention"}}
    assert entry_record(described)["fields"]["page_description"] == "Posts that build with PyTorch."
    assert description_basis(v) != description_basis(described)
    assert criteria_hash(entry_question(v)) == criteria_hash(entry_question(described))
    # ... and it is stamped with the criteria it was written against: a criteria change re-surfaces it
    stamped = {**described, "page_description_basis": description_criteria(v)}
    assert not description_stale(stamped) and not description_stale(v)
    assert description_stale({**stamped, "not_for": "a mention; LibTorch"})
    assert description_stale(described)   # never stamped: nothing says what it answers to
