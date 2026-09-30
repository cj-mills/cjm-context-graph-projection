"""The post page's projected parts (design 39c51c15): the author strip and the end matter."""

from cjm_context_graph_projection.postpage import (display_date, end_plan, header_meta, load_strip_copy,
                                                   render_end)

COPY = {"byline": "**A** — a byline.", "links": "[About](/about.html)",
        "pitch": "Hire me for {claims}: [how]({href}).", "questions": "Ask below."}
TYPES = {"t": {"kind": "tutorial"}, "n": {"kind": "notes"}, "p": {"kind": "site"}, "w": {"kind": "work"}}


def test_the_copy_comes_from_the_site_config_and_refuses_when_missing(tmp_path):
    (tmp_path / "_quarto.yml").write_text(
        'author-strip:\n  byline: "B"\n  links: "L"\n  pitch: "P {claims} {href}"\n  questions: "Q"\n')
    assert load_strip_copy(str(tmp_path)) == {"copy": {"byline": "B", "links": "L", "pitch": "P {claims} {href}",
                                                       "questions": "Q"}, "errors": []}
    (tmp_path / "_quarto.yml").write_text('author-strip:\n  byline: "B"\n  links: ""\n')
    err = load_strip_copy(str(tmp_path))["errors"]
    assert err and err[0]["missing"] == ["links", "pitch", "questions"]   # never a fallback text


def test_render_end_variants():
    plain = render_end(COPY)
    assert plain.startswith("::: {.author-strip}\n")
    assert "**A** — a byline.  \n[About](/about.html)" in plain and "Hire" not in plain and "Ask" not in plain
    pitched = render_end(COPY, pitch={"claims": "CV, Data", "href": "/work/"}, questions=True)
    assert "Hire me for CV, Data: [how](/work/)." in pitched
    assert pitched.index("Hire") < pitched.index("[About]") < pitched.index("::: {.comments-invite}")


def test_end_plan_pitches_only_with_a_published_target():
    backing = {"t": ["CV"], "n": []}
    pending = end_plan(COPY, ["t", "n", "p", "w", "x"], TYPES, backing, None)
    assert sorted(pending["ends"]) == ["n", "t", "w"]            # site pages and untyped Notes carry none
    assert pending["counts"] == {"strips": 3, "pitch": 0, "pitch_pending": 1, "questions": 1}
    assert "Hire" not in "".join(pending["ends"].values())       # no target page: no pitch anywhere
    live = end_plan(COPY, ["t", "n"], TYPES, backing, {"title": "Work with me", "href": "/work/"})
    assert "Hire me for CV: [how](/work/)." in live["ends"]["t"] and "Hire" not in live["ends"]["n"]
    assert live["counts"]["pitch"] == 1 and live["counts"]["pitch_pending"] == 0


def test_header_meta_kind_dates_and_drafts():
    # an archive tutorial: the label only; its own date-modified stands against an older revision
    arch = header_meta("tutorial", "archive", {"date-modified": "2024-05-01"}, {"revised": "2023-01-01"})
    assert arch == {"set": {"post-kind": "Tutorial"}, "unset": []}
    newer = header_meta("notes", "archive", {}, {"revised": "2026-09-29"})
    assert newer["set"] == {"post-kind": "Notes", "date-modified": "September 29, 2026"}
    # a born post: its date is its publication's; a draft shows none and says so
    pub = header_meta("notes", "born", {"date": "2026-09-10"}, {"published": "2026-10-02"})
    assert pub["set"]["date"] == "October 2, 2026" and pub["unset"] == []
    draft = header_meta("notes", "born", {"date": "2026-09-10"}, {})
    assert draft == {"set": {"post-kind": "Notes · Draft"}, "unset": ["date"]}
    assert display_date("2026-01-05") == "January 5, 2026"
