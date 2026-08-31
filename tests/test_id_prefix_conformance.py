"""Uniform id-prefix conformance (work item b73e7688, plan f22b3f34): every id-taking
verb resolves a unique id PREFIX (>= 6 hex chars), fails LOUD on ambiguity, and
refuses a miss — ONE resolve seam, no per-verb whack-a-mole.

Two contracts: (1) STATIC — every id-shaped POSITIONAL in the CLI parser is declared
in `ID_REFS`, so a new verb with an undeclared id positional fails HERE, not in a
sitting three weeks later; (2) FUNCTIONAL — the shared seams behave identically on
prefix / full / ambiguous / missing: `resolve_node_ref` (every read verb and the
cli-side `_resolve_capture`/`_resolve_module_ref`), `resolve_subject` (assert's
never-mint-a-prefix rule), and the module-ref resolver behind the single-arg
source-state verbs. `--supersede` resolution is locked in test_write_supersede."""

import asyncio
import re
from pathlib import Path

import pytest

import cjm_context_graph_projection.cli as cli_mod
from cjm_context_graph_projection.cli import ID_REFS, _resolve_module_ref
from cjm_context_graph_projection.projection import resolve_node_ref
from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.write import resolve_subject

from cjm_context_graph_layer.ops import extend_graph

# Positional names that mean "this takes a node id (or accepts one)". A positional
# with one of these names — or any name ending `_id`/`_ids` — must be declared in
# ID_REFS under its verb. `source_uuid` (edit-message) is deliberately NOT here:
# it is a capture-source uuid, an identity input, never a graph node ref.
_ID_SHAPED_NAMES = {"node_id", "module", "item", "subject", "refs", "term", "anchor"}

# Name-collision exemptions: positionals whose NAME matches the id-shaped set but
# whose semantics are not a node ref. `grep.term` is an exact content substring —
# ids are never resolved there (the id-shaped guard in resolve_node_ref is moot).
_EXEMPT = {("grep", "term")}

_PARSER_RE = re.compile(r'(\w+) = sub\.add_parser\("([a-z0-9-]+)"')
_POSITIONAL_RE = re.compile(r'(\w+)\.add_argument\("([a-z_]+)"')


def _scanned_id_positionals():
    """(verb, arg) pairs for every id-shaped positional found in the parser source."""
    src = Path(cli_mod.__file__).read_text()
    verb_by_var = {}
    for m in _PARSER_RE.finditer(src):
        verb_by_var[m.group(1)] = m.group(2)  # later re-use of a var name rebinds it
    # Walk in order so a re-used parser variable (e.g. p_rs) maps each positional
    # to the verb whose add_parser most recently bound that variable.
    events = sorted(
        [(m.start(), "bind", m.group(1), m.group(2)) for m in _PARSER_RE.finditer(src)]
        + [(m.start(), "arg", m.group(1), m.group(2)) for m in _POSITIONAL_RE.finditer(src)])
    current = {}
    pairs = []
    for _pos, kind, var, val in events:
        if kind == "bind":
            current[var] = val
        elif var in current:
            name = val
            if name.endswith("_id") or name.endswith("_ids") or name in _ID_SHAPED_NAMES:
                pairs.append((current[var], name))
    return pairs


def test_every_id_shaped_positional_is_declared():
    declared = {(verb, arg) for verb, args in ID_REFS.items() for arg in args} | _EXEMPT
    undeclared = [p for p in _scanned_id_positionals() if p not in declared]
    assert not undeclared, (
        "id-shaped positionals with NO ID_REFS declaration (b73e7688: a new "
        f"id-taking verb must resolve prefixes through the shared seam): {undeclared}")


def test_declared_refs_point_at_real_verbs():
    src = Path(cli_mod.__file__).read_text()
    for verb in ID_REFS:
        assert f'add_parser("{verb}"' in src, f"ID_REFS names `{verb}` but no subparser exists"


# --- Functional matrix (drives the real graph-storage worker; skip off-box —
# per-test marks, NOT pytestmark, so the static contracts above always run) ---

_needs_graph = pytest.mark.skipif(
    not (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists(),
    reason=f"graph capability {DEFAULT_GRAPH_ID!r} not installed at {DEFAULT_MANIFESTS}",
)


def _nid(n):
    """Node id, worker-object or wire-dict shaped."""
    return n["id"] if isinstance(n, dict) else getattr(n, "id", None)


A1 = "abcdef01-0000-5000-8000-000000000001"   # shares a 7-hex prefix with A2
A2 = "abcdef02-0000-5000-8000-000000000002"
B = "12345678-0000-5000-8000-000000000003"    # unique prefix
MOD = "99999999-0000-5000-8000-000000000004"  # a CodeModule with repo_key/module_path


async def _build(db):
    nodes = [
        {"id": A1, "label": "Decision", "properties": {"title": "Alpha-1"}, "sources": []},
        {"id": A2, "label": "Decision", "properties": {"title": "Alpha-2"}, "sources": []},
        {"id": B, "label": "Decision", "properties": {"title": "Beta"}, "sources": []},
        {"id": MOD, "label": "CodeModule",
         "properties": {"repo_key": "demo-repo", "module_path": "pkg/mod.py",
                        "module_name": "pkg.mod", "path": "/tmp/demo/pkg/mod.py"},
         "sources": []},
    ]
    async with open_graph(db) as gx:
        await extend_graph(gx.queue, gx.graph_id, nodes, [])


@_needs_graph
def test_resolve_node_ref_prefix_full_ambiguous_missing(tmp_path):
    db = str(tmp_path / "g.db")

    async def go():
        await _build(db)
        async with open_graph(db) as gx:
            full = await resolve_node_ref(gx, B)
            uniq = await resolve_node_ref(gx, "123456")
            ambi = await resolve_node_ref(gx, "abcdef0")
            miss = await resolve_node_ref(gx, "deadbeef")
            term = await resolve_node_ref(gx, "not-an-id")
            return full, uniq, ambi, miss, term

    full, uniq, ambi, miss, term = asyncio.run(go())
    assert _nid(full["node"]) == B
    assert _nid(uniq["node"]) == B          # unique prefix resolves
    assert {c["id"] for c in ambi["candidates"]} == {A1, A2}  # ambiguity fails LOUD
    assert miss == {} and term == {}        # miss and non-id ref never guess


@_needs_graph
def test_resolve_module_ref_prefix_wrong_label_ambiguous_missing(tmp_path):
    db = str(tmp_path / "g.db")

    async def go():
        await _build(db)
        async with open_graph(db) as gx:
            ok, err_ok = await _resolve_module_ref(gx, "999999")
            wrong, err_wrong = await _resolve_module_ref(gx, B)
            ambi, err_ambi = await _resolve_module_ref(gx, "abcdef0")
            miss, err_miss = await _resolve_module_ref(gx, "deadbeef")
            return ok, err_ok, wrong, err_wrong, ambi, err_ambi, miss, err_miss

    ok, err_ok, wrong, err_wrong, ambi, err_ambi, miss, err_miss = asyncio.run(go())
    assert err_ok is None and ok == {"repo_key": "demo-repo", "module_path": "pkg/mod.py"}
    assert wrong is None and "not a CodeModule" in err_wrong
    assert ambi is None and "ambiguous" in err_ambi
    assert miss is None and "no node" in err_miss


@_needs_graph
def test_resolve_subject_prefix_resolves_and_never_mints(tmp_path):
    db = str(tmp_path / "g.db")

    async def go():
        await _build(db)
        async with open_graph(db) as gx:
            uniq = await resolve_subject(gx, "123456")
            ambi = await resolve_subject(gx, "abcdef0")
            miss = await resolve_subject(gx, "deadbeef")
            return uniq, ambi, miss

    uniq, ambi, miss = asyncio.run(go())
    assert uniq["subject_id"] == B
    # Ambiguity is an ERROR, never a guess and never a phantom `term` mint.
    assert ambi["subject_id"] is None and "ambiguous" in ambi["error"]
    # A prefix-shaped miss refuses instead of minting a hex-named term entity.
    assert miss["subject_id"] is None and "matches no node" in miss["error"]
