"""Ingested sources' times from git history (design amendment 19edbe97; leg C 7ddcea72): the
archive and the repo map take their times from the commits that made each element, at the
element grain, read from HEAD; the seeds take authored constants."""

import os
import subprocess
from pathlib import Path

import pytest

from cjm_dev_graph_schema.identity import note_node_id

from cjm_context_graph_projection.devgraph import notes_corpus_elements, repo_map_elements
from cjm_context_graph_projection.gitfold import ElementFold, fold_history, unquote_path
from cjm_context_graph_projection.seeds import (CLASS_SUBJECTS_AUTHORED, RENAME_ALIASES_AUTHORED,
                                                RENAME_CONTRADICTION_AUTHORED, STALE_VERSION_AUTHORED,
                                                seed_elements)

T1, T2, T3, T4 = 1700000000, 1700100000, 1700200000, 1700300000


def _git(root: Path, *args, when=None) -> str:
    env = dict(os.environ)
    if when is not None:
        env.update(GIT_AUTHOR_DATE=f"@{when} +0000", GIT_COMMITTER_DATE=f"@{when} +0000")
    r = subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _commit(root: Path, when: int, msg: str = "x") -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", msg, "--allow-empty", when=when)
    return _git(root, "rev-parse", "HEAD")


def _post(title: str, cats: str, body: str) -> str:
    return f"---\ntitle: \"{title}\"\ndate: 2024-01-01\ncategories: [{cats}]\n---\n\n{body}"


def _w(eid: str, v: str = "a") -> dict:
    return {"id": eid, "label": "X", "properties": {"v": v}, "sources": [{"h": v}]}


def test_element_fold_runs_changes_moves_and_shared_holders():
    f = ElementFold()
    f.commit(1, {"a.md": {"x": _w("x"), "t": _w("t")}})
    f.commit(2, {"a.md": {"x": _w("x", "b"), "t": _w("t")}})         # x's content changes
    assert f.times("x") == (1, 2) and f.times("t") == (1, 1)
    f.commit(3, {"b.md": {"t": _w("t")}})                             # a second holder of t
    f.commit(4, {"a.md": None})                                       # a leaves: t still held by b
    assert f.times("x") is None and f.times("t") == (1, 1)
    f.commit(5, {"b.md": None, "c.md": {"t": _w("t")}})               # a move inside one commit keeps the run
    assert f.times("t") == (1, 1)
    f.commit(6, {"c.md": None})
    f.commit(7, {"d.md": {"t": _w("t")}})                             # a new run after a gap
    assert f.times("t") == (7, 7)
    f.commit(8, {"d.md": {"t": {**_w("t"), "sources": [{"h": "moved"}]}}})   # sources are provenance
    assert f.times("t") == (7, 7)


def test_unquote_path_undoes_git_c_quoting():
    assert unquote_path('"check\\""') == 'check"'
    assert unquote_path('"a\\303\\251b"') == "aéb"
    assert unquote_path("plain/path.md") == "plain/path.md"


def test_archive_times_at_the_element_grain_read_from_head(tmp_path):
    site = tmp_path / "site"
    posts = site / "posts"
    (posts / "a").mkdir(parents=True)
    _git(site, "init", "-q")
    a = posts / "a" / "index.md"
    a.write_text(_post("A", "ml", "## One\n\nFirst.\n\n## Two\n\nSecond.\n"))
    _commit(site, T1)
    a.write_text(_post("A", "ml", "## One\n\nFirst.\n\n## Two\n\nSecond, edited.\n"))
    _commit(site, T2)
    (posts / "b").mkdir()
    (posts / "b" / "index.md").write_text(_post("B", "ml, git", "## Only\n\nBody.\n"))
    _commit(site, T3)
    # Uncommitted and untracked changes never reach the graph: HEAD is the archive
    a.write_text(_post("A", "ml", "## One\n\nFirst.\n\n## Two\n\nDirty.\n"))
    (posts / "c").mkdir()
    (posts / "c" / "index.md").write_text(_post("C", "ml", "## New\n\nUntracked.\n"))
    report = {}
    nodes, edges = notes_corpus_elements(str(posts), report=report)
    assert report["uncommitted"] == ["posts/a/index.md"] and report["untracked"] == ["posts/c/index.md"]
    assert report["head"] == _git(site, "rev-parse", "HEAD") and report["versions"] == 3
    by = {(w["label"], w["properties"].get("title") or w["properties"].get("key")): w for w in nodes}
    assert ("Note", "C") not in by                                    # untracked: absent
    assert "Dirty" not in "".join(w["properties"].get("raw", "") for w in nodes)
    note_a = next(w for w in nodes if w["id"] == note_node_id("a"))
    # The Note holds the front matter; a body edit is its Section's change alone
    assert (note_a["created_at"], note_a["updated_at"]) == (T1, T1)
    secs = {w["properties"]["title"]: w for w in nodes
            if w["label"] == "Section" and w["properties"]["note_id"] == note_node_id("a")}
    assert (secs["One"]["created_at"], secs["One"]["updated_at"]) == (T1, T1)   # untouched by the edit
    assert (secs["Two"]["created_at"], secs["Two"]["updated_at"]) == (T1, T2)
    assert (by[("Topic", "ml")]["created_at"], by[("Topic", "ml")]["updated_at"]) == (T1, T1)
    assert by[("Topic", "git")]["created_at"] == T3
    assert all(w["created_at"] <= w["updated_at"] for w in nodes + edges)
    # Every element's times are a function of the history: a second read is identical
    again, again_e = notes_corpus_elements(str(posts))
    assert [(w["id"], w["created_at"], w["updated_at"]) for w in again + again_e] == \
           [(w["id"], w["created_at"], w["updated_at"]) for w in nodes + edges]


def test_a_shared_topic_runs_while_any_post_tags_it(tmp_path):
    posts = tmp_path / "posts"
    for d in ("a", "b"):
        (posts / d).mkdir(parents=True)
    _git(tmp_path, "init", "-q")
    (posts / "a" / "index.md").write_text(_post("A", "ml", "Body.\n"))
    _commit(tmp_path, T1)
    (posts / "b" / "index.md").write_text(_post("B", "ml", "Body.\n"))
    _commit(tmp_path, T2)
    (posts / "a" / "index.md").write_text(_post("A", "other", "Body.\n"))   # a drops the tag, b holds it
    _commit(tmp_path, T3)
    note_a = next(w for w in notes_corpus_elements(str(posts))[0] if w["id"] == note_node_id("a"))
    assert (note_a["created_at"], note_a["updated_at"]) == (T1, T3)        # a front-matter change
    nodes, _ = notes_corpus_elements(str(posts))
    topic = next(w for w in nodes if w["label"] == "Topic" and w["properties"]["key"] == "ml")
    assert (topic["created_at"], topic["updated_at"]) == (T1, T1)
    (posts / "b" / "index.md").unlink()
    _commit(tmp_path, T4 - 50)                                              # no holder: the run ends
    (posts / "a" / "index.md").write_text(_post("A", "ml", "Body.\n"))      # a new run after the gap
    _commit(tmp_path, T4)
    nodes, _ = notes_corpus_elements(str(posts))
    topic = next(w for w in nodes if w["label"] == "Topic" and w["properties"]["key"] == "ml")
    assert topic["created_at"] == T4


def test_a_corpus_under_no_git_history_is_refused(tmp_path):
    (tmp_path / "posts" / "a").mkdir(parents=True)
    (tmp_path / "posts" / "a" / "index.md").write_text(_post("A", "ml", "Body.\n"))
    with pytest.raises(ValueError, match="no git history"):
        notes_corpus_elements(str(tmp_path / "posts"))


def test_a_frozen_path_stops_at_its_commit(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "p.md").write_text("one\n")
    _commit(tmp_path, T1)
    (tmp_path / "p.md").write_text("two\n")
    c2 = _commit(tmp_path, T2)
    (tmp_path / "p.md").unlink()
    _commit(tmp_path, T3)

    def decompose(path, data):
        return data.decode(), [{"id": "e", "label": "X", "properties": {"text": data.decode()}}]

    hist = fold_history(str(tmp_path), lambda p: False, decompose, freeze={"p.md": c2})
    assert hist.payloads["p.md"] == "two\n" and hist.fold.times("e") == (T1, T2)
    with pytest.raises(ValueError, match="not in HEAD's ancestry"):
        fold_history(str(tmp_path), lambda p: False, decompose, freeze={"p.md": "0" * 40})


def test_repo_map_times_from_git(tmp_path):
    lib, app, loose = tmp_path / "cjm-lib", tmp_path / "cjm-app", tmp_path / "cjm-loose"
    for d in (lib, app, loose):
        d.mkdir()
    _git(lib, "init", "-q")
    (lib / "pyproject.toml").write_text('[project]\nname = "cjm-lib"\ndependencies = []\n')
    _commit(lib, T1)
    _git(app, "init", "-q")
    (app / "pyproject.toml").write_text('[project]\nname = "cjm-app"\ndependencies = ["numpy"]\n')
    _commit(app, T2)
    (app / "pyproject.toml").write_text('[project]\nname = "cjm-app"\ndependencies = ["cjm-lib>=0.1"]\n')
    _commit(app, T3)
    (app / "pyproject.toml").write_text('[project]\nname = "cjm-app"\ndependencies = ["cjm-lib>=0.2", "x"]\n')
    _commit(app, T4)                                          # a pin bump keeps the dependency's run
    (app / "README.md").write_text("later\n")
    _commit(app, T4 + 50)                                     # an unrelated commit moves nothing
    report = {}
    nodes, edges = repo_map_elements(str(tmp_path), report=report)
    assert report["no_history"] == ["cjm-loose"]
    ents = {w["properties"]["name"]: w for w in nodes}
    assert set(ents) == {"cjm-lib", "cjm-app"}
    assert (ents["cjm-app"]["created_at"], ents["cjm-app"]["updated_at"]) == (T2, T2)
    (dep,) = edges
    assert (dep["created_at"], dep["updated_at"]) == (T3, T3)


def test_a_renamed_repo_entity_takes_its_aliases_authored_time(tmp_path):
    d = tmp_path / "cjm-substrate-torch-utils"
    d.mkdir()
    _git(d, "init", "-q")
    (d / "pyproject.toml").write_text('[project]\nname = "x"\n')
    _commit(d, T1)
    (w,) = repo_map_elements(str(tmp_path))[0]
    assert w["properties"]["aliases"] and (w["created_at"], w["updated_at"]) == (T1, RENAME_ALIASES_AUTHORED)


def test_every_seed_element_carries_its_authored_time():
    nodes, edges = seed_elements()
    authored = {RENAME_CONTRADICTION_AUTHORED, STALE_VERSION_AUTHORED, CLASS_SUBJECTS_AUTHORED}
    for w in nodes + edges:
        assert w.get("created_at") in authored and w["updated_at"] == w["created_at"], w["id"]
    for w in nodes:
        if w["label"] == "Assertion":
            assert w["properties"]["asserted_at"] == w["created_at"], w["id"]
