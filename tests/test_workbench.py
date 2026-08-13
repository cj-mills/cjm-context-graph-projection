"""Workbench lens layer, the pure half: op ledger, arg glosses, lock lead lines,
and the three render profiles over hand-built view dicts (no graph — the async
views verify live, per the family's pilot-probe craft)."""

from cjm_context_graph_primitives.journal import append_write
from cjm_context_graph_projection.render import render
from cjm_context_graph_projection.workbench import _first_lock_line, _op_summary, journal_ops


def test_journal_ops_ledger_order_and_shape(tmp_path):
    p = str(tmp_path / "writes.jsonl")
    append_write(p, "link", {"source_id": "a" * 36, "target_id": "b" * 36,
                             "relation": "REFERENCES", "actor": "human"})
    append_write(p, "decide", {"statement": "pick the lens", "actor": "agent:session"})
    rows = journal_ops([p])
    assert [r["verb"] for r in rows] == ["link", "decide"]
    assert rows == sorted(rows, key=lambda r: r["ts"])  # ledger order: ts ascending
    assert rows[0]["refs"] == ["a" * 36, "b" * 36]      # link touches both endpoints
    assert rows[0]["actor"] == "human"
    assert rows[1]["summary"] == "pick the lens"


def test_journal_ops_start_cursor_is_exclusive(tmp_path):
    p = str(tmp_path / "writes.jsonl")
    append_write(p, "decide", {"statement": "first", "actor": "a"})
    append_write(p, "decide", {"statement": "second", "actor": "a"})
    rows = journal_ops([p])
    assert len(rows) == 2
    # Poll with the last-seen row's ts: only STRICTLY newer ops come back, so a
    # feed poll never re-delivers its cursor row.
    assert journal_ops([p], start=rows[0]["ts"]) == [rows[1]]
    assert journal_ops([p], start=rows[1]["ts"]) == []


def test_journal_ops_session_tag_filter(tmp_path):
    p = str(tmp_path / "writes.jsonl")
    append_write(p, "decide", {"statement": "mine", "actor": "a", "session": "2026-01-01_00-00-00"})
    append_write(p, "decide", {"statement": "other", "actor": "a", "session": "2026-01-02_00-00-00"})
    rows = journal_ops([p], session="2026-01-01_00-00-00")
    assert [r["summary"] for r in rows] == ["mine"]
    assert rows[0]["session"] == "2026-01-01_00-00-00"


def test_op_summary_prefers_slot_then_text_then_paths():
    assert _op_summary({"predicate": "task_state", "value": "done"}) == "task_state = done"
    assert _op_summary({"statement": "  spaced   out  "}) == "spaced out"
    # A source snapshot glosses by PATH, never by its (huge) module text.
    assert _op_summary({"module_path": "pkg/mod.py", "text": '"""huge module body"""'}) \
        == "module_path=pkg/mod.py"
    assert _op_summary({"statement": "x" * 300}).endswith("…")
    assert _op_summary({}) == ""


def test_first_lock_line_strips_frontmatter_stays_unbounded():
    body = "---\nname: x\n---\n\n**LOCK** the narrative lead\nsecond line"
    assert _first_lock_line(body) == "**LOCK** the narrative lead"
    # UNBOUNDED by default — bounding is a render-profile concern (a baked-in
    # cap gated the TUI's side-scroll, field round 1); opt-in via `limit`.
    assert _first_lock_line("---\nname: x\n---\n\n" + "y" * 500) == "y" * 500
    assert _first_lock_line("y" * 500, limit=240).endswith("…")
    assert _first_lock_line("") == ""


def test_render_portfolio_human():
    obj = {"counts": {"ready": 2, "blocked": 1, "done": 3, "closable": 0, "unfiled": 0},
           "anchors": [
               {"id": "p" * 36, "role": "program", "slug": "prog-a", "title": "Program A",
                "lock": {"id": "l" * 36, "lead": "**LOCK** the lead line"},
                "pins": 2, "vitals": {"ready": 1, "blocked": 0, "closable": 0, "findings": 1},
                "last_touch": 1786500000.0},
               {"id": "q" * 36, "role": "portfolio", "slug": "portfolio", "title": "Portfolio",
                "lock": None, "pins": 6,
                "vitals": {"ready": 0, "blocked": 0, "closable": 0, "findings": 0},
                "last_touch": None}],
           "links": [{"source": "q" * 36, "target": "p" * 36, "relation": "REFERENCES"}]}
    out = render("portfolio", obj, "human")
    # The portfolio anchor sorts FIRST; a lockless anchor is loud; links use slugs.
    assert out.index("`portfolio`") < out.index("`prog-a`")
    assert "⚠ NO LOCK" in out and "**LOCK** the lead line" in out
    assert "findings 1" in out and "portfolio —REFERENCES→ prog-a" in out
    assert "↳ `lead prog-a`" in out


def test_render_lead_human_missing_pin_stays_loud():
    obj = {"anchor": {"id": "a" * 36, "role": "program", "slug": "prog-a", "title": "Program A"},
           "lock": {"id": "l" * 36, "body": "**LOCK** full body"},
           "pins": [{"role": "design", "id": "d" * 36, "gloss": "the spec", "title": "A DEC"},
                    {"role": "rule", "id": "e" * 36, "gloss": "gone", "missing": True}],
           "registers": [{"id": "r" * 36, "slug": "model-register", "title": "Models",
                          "members": [{"id": "m" * 36, "title": "v1.6", "status": "candidate"}]}]}
    out = render("lead", obj, "human")
    assert "**LOCK** full body" in out
    assert "`[design]` **A DEC** — the spec" in out
    assert "⚠ MISSING pin target" in out          # never silently dropped
    assert "v1.6 (candidate)" in out
    assert render("lead", {"error": "no such anchor"}, "human").startswith("⚠")


def test_render_feed_human_two_zooms():
    obj = {"window": {"session": "2026-01-01_00-00-00", "since": None,
                      "cursor": 1786500000.5, "total_ops": 2, "shown": 2},
           "ops": [{"ts": 1786400000.0, "verb": "decide", "actor": "agent:session",
                    "session": "2026-01-01_00-00-00", "summary": "pick the lens",
                    "refs": [{"ref": "a" * 36, "id": "a" * 36, "label": "Decision",
                              "title": "pick the lens"}]},
                   {"ts": 1786400001.0, "verb": "link", "actor": None, "summary": "",
                    "refs": [{"ref": "gone00", "missing": True}]}],
           "cards": [{"ref": "a" * 36, "id": "a" * 36, "label": "Decision",
                      "title": "pick the lens", "touches": 2,
                      "verbs": {"decide": 1, "link": 1},
                      "first_ts": 1786400000.0, "last_ts": 1786400001.0}],
           "missing": 1}
    out = render("feed", obj, "human")
    assert "session `2026-01-01_00-00-00`" in out and "2 of 2 op(s)" in out
    assert "cursor `1786500000.5`" in out         # raw float: pass it back to --since
    assert "**decide** agent:session — pick the lens" in out
    assert "⚠ gone00" in out                      # a dead ref stays visible in the ledger
    assert "### Touched (1, ⚠ 1 missing)" in out
    assert "decide×1 link×1" in out
