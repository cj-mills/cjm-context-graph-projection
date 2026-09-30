"""The dev graph's file-read sources: the notes corpus, the repo map (pyproject deps ->
Entity nodes / DEPENDS_ON edges) and the site pages. The code lane is the source journal's
fold — tests/test_codefold.py."""

from cjm_dev_graph_schema.identity import entity_node_id, note_node_id, topic_node_id
from cjm_dev_graph_schema.vocab import DevNodeKinds, DevRelations
from cjm_context_graph_projection.devgraph import (_cjm_dep_keys, notes_corpus_elements,
                                                   repo_map_elements)

PYPROJECT = """\
[project]
name = "cjm-bar"
dependencies = ['cjm-foo>=0.0.1', "cjm-context-graph-layer>=0.0.7", 'numpy>=1.0', 'cjm-baz']
"""


def _make_repo(root, name, deps_toml):
    d = root / name
    d.mkdir()
    (d / "pyproject.toml").write_text(deps_toml)
    return d


def _post(root, slug, body, categories=None, series_link=None):
    d = root / slug
    d.mkdir()
    fm = ["---", f'title: "{slug}"', "date: 2024-1-1"]
    if categories:
        fm.append(f"categories: [{', '.join(categories)}]")
    fm.append("---")
    lines = list(fm) + ["# Overview", "", body]  # a heading -> one Section node
    if series_link:
        lines.append(f"Part of the [series]({series_link}).")
    (d / "index.md").write_text("\n".join(lines) + "\n")


def test_notes_corpus_elements_permalink_identity_and_facets(tmp_path):
    posts = tmp_path / "posts"
    posts.mkdir()
    _post(posts, "the-learning-game-book-notes", "Body.", categories=["education", "history"],
          series_link="/series/notes/education-notes.html")
    _post(posts, "dumbing-us-down-book-notes",
          "See [other](/posts/the-learning-game-book-notes/).",
          categories=["education", "history"], series_link="/series/notes/education-notes.html")

    nodes, edges = notes_corpus_elements(str(posts))
    labels = [n["label"] for n in nodes]
    # Permalink identity: each post is its own Note (no `index` collision).
    note_ids = {n["id"] for n in nodes if n["label"] == DevNodeKinds.NOTE}
    assert note_node_id("the-learning-game-book-notes") in note_ids
    assert note_node_id("dumbing-us-down-book-notes") in note_ids
    # Shared Topics deduped across the two posts; the series-page link mints no Series and
    # no IN_SERIES (a Series is born by a journaled op, DEC 72d669c5) — it rides site_refs.
    assert labels.count(DevNodeKinds.TOPIC) == 2
    assert labels.count(DevNodeKinds.SERIES) == 0
    assert not [e for e in edges if e["relation_type"] == DevRelations.IN_SERIES]
    notes = [n for n in nodes if n["label"] == DevNodeKinds.NOTE]
    # every in-body site link rides site_refs for the one post-replay resolver (ruling d31e9ba7):
    # the post link is no ingest edge
    refs = {n["properties"]["slug"]: n["properties"]["site_refs"] for n in notes}
    assert refs["the-learning-game-book-notes"] == ["/series/notes/education-notes.html"]
    assert "/posts/the-learning-game-book-notes/" in refs["dumbing-us-down-book-notes"]
    assert any(e["relation_type"] == DevRelations.TAGGED
               and e["target_id"] == topic_node_id("education") for e in edges)
    assert not any(e["relation_type"] == DevRelations.REFERENCES for e in edges)
    # The notes corpus decomposes bodies into Section nodes (opt-in, posts only).
    assert labels.count(DevNodeKinds.SECTION) >= 1
    assert any(e["relation_type"] == DevRelations.HAS_SECTION for e in edges)
    # Lossless (24825bd6): every Section carries its heading-inclusive `raw` span + `order`
    # and the Note carries `frontmatter_raw` — `read` reconstructs the post byte-for-byte.
    secs = [n for n in nodes if n["label"] == DevNodeKinds.SECTION]
    assert all(n["properties"].get("raw", "").startswith("# Overview") for n in secs)
    assert all("order" in n["properties"] for n in secs)
    assert all(n["properties"].get("frontmatter_raw")
               for n in nodes if n["label"] == DevNodeKinds.NOTE)


def test_notes_corpus_elements_ingests_qmd_posts_losslessly(tmp_path):
    # A Quarto `.qmd` post (the archive's carry Graphviz `{dot}` cells) is a Note like any
    # `index.md` post: permalink identity and a byte-exact reconstruction (finding 87b88ea3).
    posts = tmp_path / "posts"
    posts.mkdir()
    _post(posts, "md-post", "Body.")
    _post(posts, "qmd-post", "```{dot}\ndigraph G { a -> b }\n```")
    qmd_file = posts / "qmd-post" / "index.qmd"
    (posts / "qmd-post" / "index.md").rename(qmd_file)

    nodes, edges = notes_corpus_elements(str(posts))
    by_id = {n["id"]: n for n in nodes}
    qid = note_node_id("qmd-post")
    assert {qid, note_node_id("md-post")} <= set(by_id)
    assert by_id[qid]["properties"]["path"] == str(qmd_file)
    secs = sorted((by_id[e["target_id"]] for e in edges
                   if e["relation_type"] == DevRelations.HAS_SECTION and e["source_id"] == qid),
                  key=lambda n: n["properties"]["order"])
    rebuilt = by_id[qid]["properties"]["frontmatter_raw"] + "".join(
        n["properties"]["raw"] for n in secs)
    assert rebuilt == qmd_file.read_text()


def test_cjm_dep_keys_strips_specifiers_and_filters(tmp_path):
    py = tmp_path / "pyproject.toml"
    py.write_text(PYPROJECT)
    keys = _cjm_dep_keys(py)
    assert keys == ["cjm-foo", "cjm-context-graph-layer", "cjm-baz"]  # numpy filtered out


def test_repo_map_elements_entities_and_depends_on(tmp_path):
    _make_repo(tmp_path, "cjm-foo", '[project]\nname = "cjm-foo"\ndependencies = []\n')
    _make_repo(tmp_path, "cjm-bar", PYPROJECT)
    (tmp_path / "not-a-cjm-repo").mkdir()  # ignored
    nodes, edges = repo_map_elements(str(tmp_path))

    assert {n["properties"]["key"] for n in nodes} == {"cjm-foo", "cjm-bar"}
    assert all(n["label"] == DevNodeKinds.ENTITY for n in nodes)
    # cjm-bar DEPENDS_ON cjm-foo (and the layer + baz); self-dep excluded.
    bar_id = entity_node_id("repo", "cjm-bar")
    dep_edges = [e for e in edges if e["source_id"] == bar_id]
    assert all(e["relation_type"] == DevRelations.DEPENDS_ON for e in dep_edges)
    assert entity_node_id("repo", "cjm-foo") in {e["target_id"] for e in dep_edges}


def test_compute_untested_flags_unlinked_public_symbols():
    """The untested audit: public package symbols without an incoming TESTS edge are
    flagged; private symbols and test-module symbols are not audited."""
    from cjm_context_graph_projection.conventions import compute_untested

    mods = [{"id": "m1", "properties": {"module_path": "pkg/a.py"}},
            {"id": "mt", "properties": {"module_path": "tests/test_a.py"}}]
    syms = [{"id": "s1", "properties": {"qualname": "fa", "module_id": "m1"}},
            {"id": "s2", "properties": {"qualname": "fb", "module_id": "m1"}},
            {"id": "s3", "properties": {"qualname": "_private", "module_id": "m1"}},
            {"id": "st", "properties": {"qualname": "test_fa", "module_id": "mt"}}]
    out = compute_untested(syms, mods, {"s1"})
    assert [u["qualname"] for u in out] == ["fb"]


def test_notes_corpus_elements_ingests_site_pages_by_their_path(tmp_path):
    # The site's own pages ride the posts' ingest as archive Sources (ruling 96aff70e; user,
    # 2026-09-28): identity = the path under the site root, a directory index named by its
    # directory, the root index kept as "index"; lossless; a clash with a post refuses.
    import pytest
    from cjm_context_graph_projection.devgraph import site_page_slug
    assert site_page_slug("about.qmd") == "about"
    assert site_page_slug("index.qmd") == "index"
    assert site_page_slug("series/notes/index.md") == "series/notes"
    site = tmp_path / "site"
    posts = site / "posts"
    posts.mkdir(parents=True)
    _post(posts, "a-post", "Body.")
    (site / "about.qmd").write_text("---\ntitle: About\naliases:\n- /services\n---\n\nHello.\n")
    (site / "series" / "notes").mkdir(parents=True)
    (site / "series" / "notes" / "index.md").write_text("---\ntitle: Notes\n---\n")
    nodes, edges = notes_corpus_elements(str(posts), site_root=str(site),
                                         site_pages=["about.qmd", "series/notes/index.md"])
    by_id = {n["id"]: n for n in nodes}
    about = note_node_id("about")
    assert {about, note_node_id("series/notes"), note_node_id("a-post")} <= set(by_id)
    secs = sorted((by_id[e["target_id"]] for e in edges
                   if e["relation_type"] == DevRelations.HAS_SECTION and e["source_id"] == about),
                  key=lambda n: n["properties"]["order"])
    assert (by_id[about]["properties"]["frontmatter_raw"] + "".join(n["properties"]["raw"] for n in secs)
            == (site / "about.qmd").read_text())
    (site / "a-post.qmd").write_text("---\ntitle: Clash\n---\n")
    with pytest.raises(ValueError, match="collide with posts: a-post"):
        notes_corpus_elements(str(posts), site_root=str(site), site_pages=["a-post.qmd"])
