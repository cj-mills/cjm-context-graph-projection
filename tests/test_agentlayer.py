"""The post page's agent layer (design 39c51c15 (7), amendment 23a49667): JSON-LD per post,
llms.txt from the graph's structure, links kept in the markdown layer."""

import inspect

from cjm_context_graph_projection.agentlayer import (_line, build_lines, check_jsonld, directory_author, JSONLD_KEYS,
                                                     jsonld_script, llms_index, load_index_copy, plain,
                                                     post_jsonld, read_jsonld, rewrite_llms_links,
                                                     rewrite_markdown_links, write_llms_txt)
from cjm_context_graph_projection.site import _DRAFTS_REF

DATES = {"published": "2024-05-01", "updated": "2024-06-02"}


def test_post_jsonld_by_kind_and_draft():
    lic = "https://creativecommons.org/licenses/by/4.0/"
    s = [{"title": "S", "url": "https://x.org/series/s.html"}]
    t = post_jsonld("tutorial", "T", "  Two\n lines ", "https://x.org/posts/t/", DATES, ["A"], lic, s, "https://x.org")
    assert list(t) == [k for k in JSONLD_KEYS if k in t]          # the fixed key order
    assert t["@type"] == "TechArticle" and t["description"] == "Two lines"
    assert t["author"] == {"@type": "Person", "name": "A", "url": "https://x.org/"}
    # the site author's role rides their Person (amendment fe6f0fb7); another author's never does
    two = post_jsonld("notes", "T", "", "u", DATES, ["A", "B"], "", [], "https://x.org", {"A": "Role"})
    assert two["author"][0] == {"@type": "Person", "name": "A", "jobTitle": "Role", "url": "https://x.org/"}
    assert "jobTitle" not in two["author"][1]
    assert t["isPartOf"] == [{"@type": "CreativeWorkSeries", "name": "S", "url": "https://x.org/series/s.html"}]
    assert (t["datePublished"], t["dateModified"], t["license"]) == ("2024-05-01", "2024-06-02", lic)
    # Notes are BlogPostings; a draft carries no dates; empty fields are dropped; two authors list
    n = post_jsonld("notes", "N", "", "https://x.org/d/", {"published": "", "updated": "2024-06-02"},
                    ["A", "B"], "", [], "https://x.org")
    assert n["@type"] == "BlogPosting" and "datePublished" not in n and "dateModified" not in n
    assert not {"description", "license", "isPartOf"} & set(n) and len(n["author"]) == 2


def test_jsonld_carries_no_claim_by_construction():
    # The claims guard (49c0f3c7): no claim reaches the builder, and nothing outside the keys leaves it
    assert not [p for p in inspect.signature(post_jsonld).parameters if "claim" in p]
    obj = post_jsonld("log", "L", "d", "u", DATES, ["A"], "l", [], "https://x.org")
    assert set(obj) <= set(JSONLD_KEYS)


def test_jsonld_script_round_trip_and_check(tmp_path):
    obj = post_jsonld("notes", "Bad </script> title", "", "u", DATES, [], "", [], "https://x.org")
    head = jsonld_script(obj)
    assert "</script>" not in head[:-len("</script>")]           # a value can never close the script
    assert read_jsonld(head) == [obj]
    for slug, page in (("ok", f"<head>{head}</head>"), ("two", f"<head>{head}{head}</head>"),
                       ("off", "<head>" + jsonld_script({**obj, "headline": "x"}) + "</head>")):
        (tmp_path / "posts" / slug).mkdir(parents=True)
        (tmp_path / "posts" / slug / "index.html").write_text(page)
    plan = {"posts": {f"posts/{s}/index.md": {"head": head} for s in ("ok", "two", "off", "gone")}}
    res = check_jsonld(str(tmp_path), plan)
    assert res["checked"] == 3
    assert sorted((e["kind"], e["source"]) for e in res["errors"]) == [
        ("jsonld-count", "posts/two/index.md"), ("jsonld-mismatch", "posts/off/index.md")]


def test_directory_author(tmp_path):
    (tmp_path / "posts" / "x").mkdir(parents=True)
    (tmp_path / "posts" / "_metadata.yml").write_text("author: 'Dir Author'\n")
    assert directory_author(str(tmp_path), "posts/x/index.md", {}) == ["Dir Author"]
    assert directory_author(str(tmp_path), "posts/x/index.md", {"author": [{"name": "Own"}, "Two"]}) == ["Own", "Two"]
    assert directory_author(str(tmp_path), "drafts/y/index.md", {}) == []


def test_build_lines_state_the_licenses_and_the_key():
    by, mit, sa = (("CC BY 4.0", "https://c/by"), ("MIT License", "https://o/mit"), ("CC BY-SA 4.0", "https://c/sa"))
    shared = build_lines([{"content": by, "code": mit}] * 2, optional=True)
    assert shared[1] == "Licenses — text: CC BY 4.0 (https://c/by); code samples: MIT License (https://o/mit)."
    assert shared[2] == "The Optional section lists project logs; it can be skipped."
    mixed = build_lines([{"content": by, "code": mit}, {"content": sa, "code": mit}], optional=False)
    assert mixed[1].startswith("Licenses — text: varies by page") and mixed[1].endswith("MIT License (https://o/mit).")
    assert len(mixed) == 2 and len(build_lines([], optional=False)) == 1


def test_descriptions_are_plain_text():
    desc = "Modify the [fastai-to-unity tutorial](../../f/part-1/) to use\n [LibTorch](https://p.org)."
    assert plain(desc) == "Modify the fastai-to-unity tutorial to use LibTorch."
    assert _line("T", "https://x.org/t.llms.md", desc).endswith(": Modify the fastai-to-unity tutorial to use LibTorch.")
    assert post_jsonld("notes", "T", desc, "u", DATES, [], "", [], "https://x.org")["description"] == plain(desc)


def test_index_copy_refuses_without_a_summary(tmp_path):
    who = 'site-author:\n  name: "N"\n  role: "R"\n'
    (tmp_path / "_quarto.yml").write_text("llms-index:\n  details: d\n" + who)
    assert load_index_copy(str(tmp_path))["errors"][0]["missing"] == ["summary"]
    (tmp_path / "_quarto.yml").write_text("llms-index:\n  summary: |\n    {name},   {role}: the site.\n" + who)
    assert load_index_copy(str(tmp_path)) == {"copy": {"summary": "N, R: the site.", "details": ""}, "errors": []}
    (tmp_path / "_quarto.yml").write_text("llms-index:\n  summary: S\n  details: '{nope}'\n" + who)
    assert load_index_copy(str(tmp_path))["errors"][0]["field"] == "details"   # an unknown placeholder refuses
    (tmp_path / "_quarto.yml").write_text("llms-index:\n  summary: S\n")
    assert load_index_copy(str(tmp_path))["errors"][0]["kind"] == "site-author"


def _p(title, kind, date, src=None):
    return {"title": title, "kind": kind, "date": date, "description": f"{title} desc",
            "source": src or f"posts/{title.lower()}/index.md"}


def test_llms_index_structure():
    posts = {"t1": _p("T1", "tutorial", "2024-01-01"), "t2": _p("T2", "tutorial", "2024-02-01"),
             "t3": _p("T3", "tutorial", "2023-01-01"), "n1": _p("N1", "notes", "2022-01-01"),
             "l1": _p("L1", "log", "2021-01-01"), "l2": _p("L2", "log", "2025-01-01"),
             "w1": _p("W1", "work", "2020-01-01")}
    series = [{"title": "Old", "source": "series/old.qmd", "description": "", "updated": "2023-01-01",
               "members": ["t3", "t1"]},
              {"title": "New", "source": "series/new.qmd", "description": "The new one.", "updated": "2024-02-01",
               "members": ["t2", "t1"]},   # t1 in two series: listed once, in the first section
              {"title": "Logs", "source": "series/logs.qmd", "description": "", "updated": "2025-01-01",
               "members": ["l1", "l2"]}]
    idx = llms_index("Site", "https://x.org/", {"summary": "S.", "details": "D."},
                     [{"title": "About", "source": "about.qmd", "description": "Me."}],
                     [{"title": "Topic", "source": "series/topic.qmd", "description": ""}], series, posts)
    txt = idx["text"]
    heads = [ln for ln in txt.splitlines() if ln.startswith("## ")]
    assert heads == ["## Site", "## Collections", "## Tutorials: New", "## Tutorials: Old", "## Notes", "## Work",
                     "## Optional"]
    assert "## Work\n\n- [W1](https://x.org/posts/w1/index.llms.md): W1 desc\n" in txt   # paid work, ff12a19a
    assert txt.startswith("# Site\n\n> S.\n\nD.\n\nEach link is a markdown copy of its page;")
    assert "it can be skipped.\n\n## Site\n\n- [About](https://x.org/about.llms.md): Me.\n" in txt
    new = txt.split("## Tutorials: New\n\n")[1].split("\n\n")[0].splitlines()
    assert new == ["- [New](https://x.org/series/new.llms.md): The new one.",
                   "- [T2](https://x.org/posts/t2/index.llms.md): T2 desc",
                   "- [T1](https://x.org/posts/t1/index.llms.md): T1 desc"]
    assert txt.count("posts/t1/index.llms.md") == 1 and "## Tutorials\n" not in txt
    optional = txt.split("## Optional\n\n")[1].strip().splitlines()
    assert [ln.split("]")[0][3:] for ln in optional] == ["Logs", "L2", "L1"]   # series page, then newest first
    assert len(idx["links"]) == 1 + 1 + 3 + 2 + 1 + 4
    # a tutorial in no series has its own section
    alone = llms_index("Site", "https://x.org", {"summary": "S."}, [], [], [], {"t1": posts["t1"]})
    assert "## Tutorials\n\n- [T1]" in alone["text"]


def test_write_llms_txt_needs_every_page(tmp_path):
    idx = {"text": "# S\n", "links": ["a.llms.md", "b/index.llms.md"]}
    (tmp_path / "a.llms.md").write_text("a")
    res = write_llms_txt(str(tmp_path), idx)
    assert not res["written"] and res["errors"][0]["detail"] == ["b/index.llms.md"]
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "index.llms.md").write_text("b")
    assert write_llms_txt(str(tmp_path), idx) == {"written": True, "errors": []}
    assert (tmp_path / "llms.txt").read_text() == "# S\n"
    assert write_llms_txt(str(tmp_path), idx) == {"written": False, "errors": []}   # unchanged


def test_links_kept_in_the_markdown_layer(tmp_path):
    for p in ("posts/a/index.llms.md", "posts/b/index.llms.md", "about.llms.md", "index.llms.md"):
        (tmp_path / p).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / p).write_text("")
    text = "\n".join([
        "[B](../b/) and [B again](../b/#part \"t\") and [abs](/posts/b/)",
        "[about](../../about.llms.md) [home](../../) [ext](https://x.org/posts/b/) [img](../b/fig.png)",
        "[gone](../zzz/) [out](../../../up/) `[code](../b/)`",
        "```md", "[fenced](../b/)", "```",
        "<a>[after](../b/index.html)</a>",
    ])
    (tmp_path / "posts/a/index.llms.md").write_text(text)
    res = rewrite_llms_links(str(tmp_path))
    got = (tmp_path / "posts/a/index.llms.md").read_text().splitlines()
    assert got[0] == ('[B](../b/index.llms.md) and [B again](../b/index.llms.md#part "t") and '
                      '[abs](/posts/b/index.llms.md)')
    assert got[1] == ("[about](../../about.llms.md) [home](../../index.llms.md) [ext](https://x.org/posts/b/) "
                      "[img](../b/fig.png)")
    assert got[2] == "[gone](../zzz/) [out](../../../up/) `[code](../b/)`"
    assert got[3:6] == ["```md", "[fenced](../b/)", "```"]
    assert got[6] == "<a>[after](../b/index.llms.md)</a>"
    assert res == {"files": 4, "rewritten": 5, "changed": 1}
    assert rewrite_llms_links(str(tmp_path))["rewritten"] == 0     # idempotent


def test_a_link_resolves_as_a_browser_resolves_it(tmp_path):
    # A root page's `../x/` lands on /x/ in a browser (a `..` never climbs above the root), and
    # a percent-encoded path names its file
    for p in ("index.llms.md", "series/s/index.llms.md", "posts/a b/index.llms.md"):
        (tmp_path / p).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / p).write_text("")
    (tmp_path / "index.llms.md").write_text("[S](../series/s/) [A](posts/a%20b/) [gone](../../posts/zzz/)")
    rewrite_llms_links(str(tmp_path))
    assert (tmp_path / "index.llms.md").read_text() == (
        "[S](series/s/index.llms.md) [A](posts/a%20b/index.llms.md) [gone](../../posts/zzz/)")


def test_rewrite_resolver_is_only_asked_outside_code():
    asked = []
    new, n = rewrite_markdown_links("~~~\n[x](a)\n~~~\n[y](b) `[z](c)`", lambda t: asked.append(t))
    assert asked == ["b"] and n == 0 and new.endswith("[y](b) `[z](c)`")


def test_publish_guard_scans_markdown_links_into_drafts():
    # the .llms.md copies and llms.txt are public surfaces (23a49667 (5))
    assert _DRAFTS_REF.search("[x](../../drafts/posts/y/index.llms.md)")
    assert _DRAFTS_REF.search("- [Y](https://x.org/drafts/posts/y/index.llms.md): d")
    assert not _DRAFTS_REF.search("[x](../posts/y/index.llms.md) on drafting")
