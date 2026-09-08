"""Cross-graph references (finding 0154f5e4): a deliverable's edge to a node in a SIBLING
graph lands on a local Reference stand-in; the review frontier follows it into the sibling."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cjm_context_graph_primitives.journal import read_journal
from cjm_context_graph_projection.config import sibling_graphs
from cjm_context_graph_projection.review import classify_reference_change, reference_baseline
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS
from cjm_dev_graph_schema.nodes import ReferenceNode, foreign_content_hash, parse_foreign_ref

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()


def _run(*args):
    return subprocess.run([sys.executable, "-m", "cjm_context_graph_projection.cli", *args],
                          capture_output=True, text=True)


def test_reference_baseline_prefers_the_last_observation_at_or_before_approval():
    obs = [(30.0, "sha256:c"), (10.0, "sha256:a"), (20.0, "sha256:b")]
    assert reference_baseline(obs, 25.0, "sha256:stored")[0] == "sha256:b"
    assert reference_baseline(obs, 5.0, "sha256:stored")[0] == "sha256:a"      # first after approval
    assert reference_baseline([], 5.0, "sha256:stored") == ("sha256:stored", "stored observation")
    assert reference_baseline([(10.0, "")], 5.0, "sha256:stored")[0] == "sha256:stored"  # blank rows ignored


def test_classify_reference_change_moved_gone_unchanged():
    assert classify_reference_change("sha256:a", "sha256:a") is None
    assert classify_reference_change("sha256:b", "sha256:a") == "content"
    assert classify_reference_change(None, "sha256:a") == "content"


def test_parse_foreign_ref_and_observation_round_trip():
    assert parse_foreign_ref("transcription:03dbd521-209e-4b94-8ce5-1a744965de15") == (
        "transcription", "03dbd521-209e-4b94-8ce5-1a744965de15")
    assert parse_foreign_ref("transcription:03dbd5") == ("transcription", "03dbd5")  # a prefix resolves sibling-side
    assert parse_foreign_ref("03dbd521-209e-4b94-8ce5-1a744965de15") is None       # a bare local id
    assert parse_foreign_ref("REFERENCES") is None
    node = {"id": "f-1", "label": "Correction", "properties": {"payload": {"category": "quotation"}, "status": "applied"}}
    ref = ReferenceNode.observe("transcription", node, observed_at=100.0)
    assert ref.id == ReferenceNode(graph="transcription", foreign_id="f-1").id  # identity = the address
    assert ref.observed_hash == foreign_content_hash(node)
    # the hash covers label + properties; timestamps/sources do not move it
    same = {**node, "created_at": 1.0, "sources": [{"x": 1}]}
    assert foreign_content_hash(same) == ref.observed_hash
    moved = {**node, "properties": {"payload": {"category": "quotation"}, "status": "superseded"}}
    assert foreign_content_hash(moved) != ref.observed_hash
    # the journaled observation rebuilds the same wire dict (replay never opens the sibling)
    assert ReferenceNode.from_observation(ref.observation()).to_graph_node() == ref.to_graph_node()
    wire = ref.to_graph_node()
    assert wire["label"] == "Reference" and wire["properties"]["title"].endswith("@ transcription")


def test_sibling_graphs_config_is_data_and_refuses_malformed():
    assert sibling_graphs({}) == {}
    assert sibling_graphs({"sibling_graphs": {"tx": "/x/y.db"}}) == {"tx": "/x/y.db"}
    with pytest.raises(SystemExit):
        sibling_graphs({"sibling_graphs": ["/x/y.db"]})


@pytest.mark.skipif(not _HAVE_GRAPH, reason=f"graph capability {DEFAULT_GRAPH_ID!r} not installed (CI)")
def test_cli_cross_graph_link_observes_frontier_follows_replay_needs_no_sibling(tmp_path):
    # Two graphs: the SIBLING (holds the upstream content) and the PRIMARY (holds the
    # deliverable). The primary's config names the sibling as DATA.
    sib_dir, pri_dir = tmp_path / "sib", tmp_path / "pri"
    sib_dir.mkdir(); pri_dir.mkdir()
    (sib_dir / "graph.config.json").write_text(json.dumps({"emit_root": str(sib_dir / "staging")}))
    sdb, sj = str(sib_dir / "sib.db"), str(sib_dir / "writes.jsonl")
    pdb, pj = str(pri_dir / "pri.db"), str(pri_dir / "writes.jsonl")
    (pri_dir / "graph.config.json").write_text(json.dumps(
        {"emit_root": str(pri_dir / "staging"), "sibling_graphs": {"tx": sdb}}))
    sbase = ("--graph-db-path", sdb, "--journal-path", sj, "--source-journal-path", str(sib_dir / "source.jsonl"))
    pbase = ("--graph-db-path", pdb, "--journal-path", pj, "--source-journal-path", str(pri_dir / "source.jsonl"))
    # sibling: a note whose SECTION is the foreign upstream node
    r = _run(*sbase, "new-note", "--slug", "stratum", "--content",
             "---\nname: stratum\ndescription: s\n---\n\nIntro.\n\n## Span\n\nOne.\n")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run("--graph-db-path", sdb, "--format", "agent", "locate", "stratum")
    sib_note = [m for m in json.loads(r.stdout)["matches"] if m.get("label") == "Note"][0]["id"]
    r = _run("--graph-db-path", sdb, "--format", "agent", "show", sib_note)
    sib_section = [n["node"] for n in json.loads(r.stdout)["neighbours"]
                   if n.get("node", {}).get("label") == "Section"
                   and str(n["node"].get("title", "")).lower() == "span"][0]["id"]
    # primary: a born post
    r = _run(*pbase, "new-note", "--slug", "post", "--content",
             "---\ntitle: \"Post\"\ndate: 2026-09-07\ncategories: [notes]\n---\n\nBody.\n\n## Body\n\nText.\n")
    assert r.returncode == 0, r.stderr or r.stdout
    post_id = [o for o in read_journal(pj) if o["verb"] == "assert"][-1]["args"]["subject"]
    # an unknown key and an unresolvable foreign id refuse loudly; nothing is journaled
    r = _run(*pbase, "link", post_id, "DERIVED_FROM", f"nope:{sib_section}")
    assert r.returncode != 0 and "no sibling graph `nope`" in (r.stdout + r.stderr)
    r = _run(*pbase, "link", post_id, "DERIVED_FROM", "tx:deadbeef-0000")
    assert r.returncode != 0 and "no node" in (r.stdout + r.stderr)
    assert not [o for o in read_journal(pj) if o["verb"] == "link"]
    # the cross-graph link: a Reference minted from a read-only observation, edge on it
    r = _run(*pbase, "link", post_id, "DERIVED_FROM", f"tx:{sib_section[:8]}")  # a PREFIX resolves sibling-side
    assert r.returncode == 0 and "reference minted" in r.stdout and f"tx:{sib_section}" in r.stdout, r.stderr or r.stdout
    op = [o for o in read_journal(pj) if o["verb"] == "link"][-1]["args"]
    ref_id = op["target_id"]
    assert op["observation"]["graph"] == "tx" and op["observation"]["foreign_id"] == sib_section
    assert op["observation"]["observed_hash"].startswith("sha256:")
    r = _run("--graph-db-path", pdb, "--format", "agent", "show", ref_id)
    assert r.returncode == 0 and json.loads(r.stdout)["node"]["label"] == "Reference"
    # re-link with nothing moved: idempotent, no re-observation
    r = _run(*pbase, "link", post_id, "DERIVED_FROM", f"tx:{sib_section}")
    assert r.returncode == 0 and "reference unchanged" in r.stdout
    # approve; nothing moved in the sibling -> EMPTY frontier
    r = _run(*pbase, "assert", post_id, "publish_state", "published")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run(*pbase, "review-frontier")
    assert r.returncode == 0 and "nothing stale" in r.stdout, r.stderr or r.stdout
    # the foreign section changes IN THE SIBLING -> the primary's frontier names the chain
    r = _run(*sbase, "author", sib_note, "--edit", "One.", "Two, re-accepted.")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run(*pbase, "review-frontier")
    assert r.returncode == 0 and "stale 1" in r.stdout and "[content]" in r.stdout, r.stderr or r.stdout
    assert "changed since observation" in r.stdout and "_Reference_" in r.stdout
    assert f"chain: `{post_id[:8]}` → DERIVED_FROM → `{ref_id[:8]}`" in r.stdout
    # re-observing refreshes the stand-in (journaled), the approval-time baseline still stands
    r = _run(*pbase, "link", post_id, "DERIVED_FROM", f"tx:{sib_section}")
    assert r.returncode == 0 and "re-observed" in r.stdout, r.stderr or r.stdout
    assert len([o for o in read_journal(pj) if o["verb"] == "link"]) == 2  # a NEW observation lands
    r = _run(*pbase, "review-frontier")
    assert "stale 1" in r.stdout
    # acknowledge at the live hash -> silent
    key = [tok for tok in r.stdout.split() if tok.startswith(ref_id[:8] + "@")][0].rstrip("`")
    r = _run(*pbase, "assert", post_id, "review_verdict", key)
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run(*pbase, "review-frontier")
    assert "nothing stale" in r.stdout and "acknowledged 1" in r.stdout
    # the sibling moves AGAIN -> a new key, re-surfaces
    r = _run(*sbase, "author", sib_note, "--edit", "Two, re-accepted.", "Three.")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run(*pbase, "review-frontier")
    assert "stale 1" in r.stdout
    # REPLAY onto a fresh db in a dir with NO config: the Reference rebuilds from the journaled
    # observation (the sibling is never opened) and the frontier marks it unverifiable — never
    # silently unchanged, never a crash.
    rep_dir = tmp_path / "rep"; rep_dir.mkdir()
    rdb = str(rep_dir / "rep.db")
    r = _run("--graph-db-path", rdb, "--journal-path", pj, "replay")
    assert r.returncode == 0, r.stderr or r.stdout
    r = _run("--graph-db-path", rdb, "--format", "agent", "show", ref_id)
    assert r.returncode == 0 and json.loads(r.stdout)["node"]["label"] == "Reference", r.stderr or r.stdout
    r = _run("--graph-db-path", rdb, "--journal-path", pj, "--format", "agent", "review-frontier")
    assert r.returncode == 0, r.stderr or r.stdout
    assert json.loads(r.stdout)["counts"]["unverifiable"] == 1 and json.loads(r.stdout)["counts"]["stale"] == 0
    # with the sibling named, the replayed graph agrees with the live one
    (rep_dir / "graph.config.json").write_text(json.dumps({"sibling_graphs": {"tx": sdb}}))
    r = _run("--graph-db-path", rdb, "--journal-path", pj, "review-frontier")
    assert r.returncode == 0 and "stale 1" in r.stdout and "changed since observation" in r.stdout, r.stderr or r.stdout
