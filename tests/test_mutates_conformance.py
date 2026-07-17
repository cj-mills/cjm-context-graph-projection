"""Pillar-1 seam conformance (DEC 6ee4b4f2): verb N+1 cannot ship unjournaled.

Three contracts: (1) the MUTATES_SOURCE registry is fully ROUTED (dispatch never offers
an 'available but dangerous' verb) and every registered verb exists as a CLI subparser;
(2) no function in the op modules touches disk directly outside the sanctioned seam set
(an AST scan — adding a `write_text`/`unlink` to a new verb fails HERE, not in review);
(3) every mutating verb has the uniform --no-write preview (`emit` inverts to --write)."""

import ast
from pathlib import Path

import cjm_context_graph_projection.cli as cli_mod
from cjm_context_graph_projection.cli import MUTATES_SOURCE


def test_registry_fully_routed_and_every_verb_parseable():
    # Nothing 'available but dangerous': an unrouted entry would make _dispatch refuse
    # the verb, so shipping one is a build failure here, not a runtime surprise.
    unrouted = [v for v, routed in MUTATES_SOURCE.items() if not routed]
    assert not unrouted, f"mutating verbs not routed through journaled_emit: {unrouted}"
    src = Path(cli_mod.__file__).read_text()
    for verb in MUTATES_SOURCE:
        assert f'add_parser("{verb}"' in src, f"registry names `{verb}` but no subparser exists"
    # And the gate itself is wired at dispatch.
    assert "MUTATES_SOURCE[args.command]" in src


def test_no_direct_file_mutation_outside_the_seam():
    # The sanctioned direct-disk set, enumerated and NOT growable without editing this
    # test: journaled_emit (THE seam — events land first inside it), emit_source_artifact
    # + cutover_module (replay-direction: journal -> file), absorb_authored_text (retired
    # in place; journal-then-rewrite order), and the authoring verbs' enumerated
    # TRANSITIONAL bare-path/note branches (source_journal_path=None, writes-journal
    # domain). structure.py (memory notes) rides the writes-journal domain entirely.
    allowed = {"journaled_emit", "emit_source_artifact", "cutover_module",
               "absorb_authored_text", "author", "add_symbol", "add_text",
               "emit_artifact"}
    pkg = Path(cli_mod.__file__).parent
    offenders = []
    for name in ("authoring", "refactor_ops", "module_ops", "rename_ops", "source_state"):
        tree = ast.parse((pkg / f"{name}.py").read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if fn.name in allowed:
                continue
            for call in ast.walk(fn):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr in ("write_text", "write_bytes", "unlink",
                                               "rmdir")):
                    offenders.append(f"{name}.{fn.name}:{call.lineno} .{call.func.attr}()")
    assert not offenders, ("direct file mutation OUTSIDE the journaled_emit seam "
                           f"(journal-first, DEC 6ee4b4f2): {offenders}")


def test_every_mutating_verb_has_a_uniform_preview_flag():
    # --no-write everywhere; `emit` is the deliberate inversion (read-only unless
    # --write). Scan each verb's parser block (up to the next add_parser) for the flag.
    src = Path(cli_mod.__file__).read_text()
    missing = []
    for verb in MUTATES_SOURCE:
        if verb == "emit":
            continue
        start = src.find(f'add_parser("{verb}"')
        assert start != -1, verb
        end = src.find("add_parser(", start + 1)
        block = src[start:end if end != -1 else len(src)]
        if "--no-write" not in block:
            missing.append(verb)
    assert not missing, f"mutating verbs without a --no-write preview: {missing}"
