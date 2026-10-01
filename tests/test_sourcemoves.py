"""A source that moved between two ingests is attributed, never mistaken for drift (design
amendment a9176261 to 19edbe97 (6)): each db records the HEAD of every git source it ingested,
and rebuild-diff lists the rows a moved source's changed paths account for apart from drift —
exactly those rows, by the ingest's own decomposition."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cjm_dev_graph_schema.identity import note_node_id

from cjm_context_graph_projection.devgraph import (ARCHIVE_SOURCE, REPO_SOURCE, notes_corpus_elements,
                                                   repo_entity, repo_map_elements, source_id)
from cjm_context_graph_projection.rebuilddiff import diff_graphs
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS
from cjm_context_graph_projection.sourcemoves import attribute_moved_sources

T1, T2, T3 = 1700000000, 1700100000, 1700200000


def _git(root: Path, *args, when=None) -> str:
    env = dict(os.environ)
    if when is not None:
        env.update(GIT_AUTHOR_DATE=f"@{when} +0000", GIT_COMMITTER_DATE=f"@{when} +0000")
    r = subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _commit(root: Path, when: int) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "x", "--allow-empty", when=when)
    return _git(root, "rev-parse", "HEAD")


def _post(title: str, cats: str, body: str) -> str:
    return f"---\ntitle: \"{title}\"\ndate: 2024-01-01\ncategories: [{cats}]\n---\n\n{body}"


def _site(tmp_path: Path) -> Path:
    site = tmp_path / "site"
    for slug in ("a", "b"):
        (site / "posts" / slug).mkdir(parents=True)
    _git(site, "init", "-q")
    (site / "posts" / "a" / "index.md").write_text(_post("A", "ml", "## One\n\nFirst.\n"))
    (site / "posts" / "b" / "index.md").write_text(_post("B", "ml", "## Only\n\nBody.\n"))
    (site / "styles.css").write_text("body {}\n")
    return site


def _move(site: Path) -> None:
    """Between the two ingests: a body edit to a, a new post c, a file the archive never reads."""
    (site / "posts" / "a" / "index.md").write_text(_post("A", "ml", "## One\n\nFirst, edited.\n"))
    (site / "posts" / "c").mkdir()
    (site / "posts" / "c" / "index.md").write_text(_post("C", "ml, new", "## New\n\nBody.\n"))
    (site / "styles.css").write_text("body { margin: 0 }\n")


def test_a_moved_archive_accounts_for_exactly_its_changed_paths_rows(tmp_path):
    site = _site(tmp_path)
    a = _commit(site, T1)
    posts = str(site / "posts")
    rep_a: dict = {}
    nodes_a, edges_a = notes_corpus_elements(posts, report=rep_a)
    _move(site)
    b = _commit(site, T2)
    rep_b: dict = {}
    nodes_b, edges_b = notes_corpus_elements(posts, report=rep_b)
    sid = source_id(ARCHIVE_SOURCE, posts)
    assert rep_a["sources"] == {sid: a} and rep_b["sources"] == {sid: b}   # the ingest's record entries

    moves = attribute_moved_sources(rep_a["sources"], rep_b["sources"], config={"website_root": str(site)})
    (entry,) = moves["sources"]
    assert entry["status"] == "moved" and (entry["a"], entry["b"]) == (a, b)
    assert entry["paths"] == ["posts/a/index.md", "posts/c/index.md"]     # styles.css is not the archive's
    assert {note_node_id("a"), note_node_id("c")} <= moves["ids"] and note_node_id("b") not in moves["ids"]

    raw = diff_graphs(nodes_a, edges_a, nodes_b, edges_b)
    assert not raw["clean"]                                              # the move reads as drift alone…
    res = diff_graphs(nodes_a, edges_a, nodes_b, edges_b, moved=moves["ids"])
    assert res["clean"] and res["moved"] == raw["nodes"]["differing"] + raw["nodes"]["only_b"]["count"] + \
        raw["edges"]["differing"] + raw["edges"]["only_b"]["count"]   # …and every such row is attributed


def test_attribution_never_hides_drift_outside_the_moved_rows():
    def node(nid, t, **p):
        return {"id": nid, "label": "Note", "properties": p, "sources": [], "created_at": t, "updated_at": t}

    def edge(eid, s, t, ts):
        return {"id": eid, "source_id": s, "target_id": t, "relation_type": "REFERENCES",
                "properties": {}, "created_at": ts, "updated_at": ts}

    na = [node("post", 1.0), node("born", 1.0)]
    nb = [node("post", 2.0), node("born", 3.0)]                          # born drifts on its own
    ea = [edge("out", "post", "born", 1.0), edge("in", "born", "post", 1.0)]
    eb = [edge("out", "post", "born", 2.0), edge("in", "born", "post", 2.0)]
    res = diff_graphs(na, ea, nb, eb, moved={"post"})
    assert not res["clean"]
    assert res["nodes"]["differing"] == 1 and res["nodes"]["moved"]["differing"] == 1
    # an edge the moved row ORIGINATES is its row; an edge pointing INTO it is not
    assert res["edges"]["moved"]["differing"] == 1 and res["edges"]["differing"] == 1
    assert res["edges"]["rows"][0]["samples"][0]["id"] == "in"


def test_a_side_without_a_record_attributes_nothing(tmp_path):
    site = _site(tmp_path)
    b = _commit(site, T1)
    sid = source_id(ARCHIVE_SOURCE, str(site / "posts"))
    for rec_a, rec_b in (({}, {sid: b}), ({sid: b}, {})):
        moves = attribute_moved_sources(rec_a, rec_b, config={"website_root": str(site)})
        assert moves["ids"] == set() and moves["sources"] == []
        assert bool(moves["record"]["a"]) != bool(moves["record"]["b"])


def test_a_commit_the_clone_no_longer_holds_is_reported_unattributed(tmp_path):
    site = _site(tmp_path)
    b = _commit(site, T1)
    sid = source_id(ARCHIVE_SOURCE, str(site / "posts"))
    moves = attribute_moved_sources({sid: "f" * 40}, {sid: b}, config={"website_root": str(site)})
    (entry,) = moves["sources"]
    assert entry["status"] == "unattributed" and "not in" in entry["reason"] and moves["ids"] == set()


def test_a_path_map_carries_a_source_moved_on_disk(tmp_path):
    site = _site(tmp_path)
    a = _commit(site, T1)
    _move(site)
    b = _commit(site, T2)
    posts = str((site / "posts").resolve())
    old = f"{ARCHIVE_SOURCE}:/old/disk/site/posts"
    moves = attribute_moved_sources({old: a}, {source_id(ARCHIVE_SOURCE, posts): b},
                                    path_map=[("/old/disk/site", str(site.resolve()))],
                                    config={"website_root": str(site)})
    (entry,) = moves["sources"]
    assert entry["status"] == "moved" and len(entry["paths"]) == 2


def test_a_moved_repo_accounts_for_its_dependency_edges_and_a_new_repo_for_its_entity(tmp_path):
    app = tmp_path / "cjm-app"
    app.mkdir()
    _git(app, "init", "-q")
    (app / "pyproject.toml").write_text('[project]\nname = "cjm-app"\ndependencies = ["cjm-lib>=0.1"]\n')
    a = _commit(app, T1)
    rep_a: dict = {}
    nodes_a, edges_a = repo_map_elements(str(tmp_path), report=rep_a)
    (app / "pyproject.toml").write_text('[project]\nname = "cjm-app"\ndependencies = ["cjm-lib", "cjm-x"]\n')
    (app / "README.md").write_text("later\n")
    _commit(app, T2)
    lib = tmp_path / "cjm-lib"
    lib.mkdir()
    _git(lib, "init", "-q")
    (lib / "pyproject.toml").write_text('[project]\nname = "cjm-lib"\ndependencies = []\n')
    _commit(lib, T3)
    rep_b: dict = {}
    nodes_b, edges_b = repo_map_elements(str(tmp_path), report=rep_b)
    assert rep_a["sources"] == {source_id(REPO_SOURCE, str(app)): a}

    moves = attribute_moved_sources(rep_a["sources"], rep_b["sources"])
    by = {e["source"]: e for e in moves["sources"]}
    assert by[source_id(REPO_SOURCE, str(app))]["paths"] == ["pyproject.toml"]   # README is not read
    assert by[source_id(REPO_SOURCE, str(lib))]["status"] == "added"
    assert repo_entity(str(lib)).to_graph_node()["id"] in moves["ids"]
    assert not diff_graphs(nodes_a, edges_a, nodes_b, edges_b)["clean"]
    assert diff_graphs(nodes_a, edges_a, nodes_b, edges_b, moved=moves["ids"])["clean"]


def _run(*args):
    return subprocess.run([sys.executable, "-m", "cjm_context_graph_projection.cli", *args],
                          capture_output=True, text=True)


@pytest.mark.skipif(not (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists(),
                    reason=f"graph capability {DEFAULT_GRAPH_ID!r} not installed at {DEFAULT_MANIFESTS}")
def test_two_ingests_across_a_commit_read_clean_with_the_move_attributed(tmp_path):
    site = _site(tmp_path)
    _commit(site, T1)
    cfg = {"notes_corpus": str(site / "posts"), "notes_profile": "quarto_post", "website_root": str(site)}
    dbs = []
    for sub in ("first", "second"):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "graph.config.json").write_text(json.dumps(cfg))
        db = str(tmp_path / sub / "g.db")
        r = _run("--graph-db-path", db, "ingest-notes")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "ingest record: 1 source HEAD(s)" in r.stdout
        dbs.append(db)
        if sub == "first":
            _move(site)
            _commit(site, T2)
    r = _run("--graph-db-path", dbs[0], "--format", "agent", "rebuild-diff", "--against", dbs[1])
    assert r.returncode == 0, r.stdout + r.stderr
    res = json.loads(r.stdout)
    assert res["clean"] and res["moved"] > 0 and res["record"] == {"a": 1, "b": 1}
    (entry,) = res["sources"]
    assert entry["status"] == "moved" and entry["paths"] == ["posts/a/index.md", "posts/c/index.md"]
    human = _run("--graph-db-path", dbs[0], "rebuild-diff", "--against", dbs[1]).stdout
    assert "CLEAN ·" in human and "source moved" in human
