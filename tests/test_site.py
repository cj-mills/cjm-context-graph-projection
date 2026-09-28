"""The site build (DEC 98293e72 (2)): the redirect projection from site_path facts, the
front-matter alias check, the publish guard, and the two Quarto profiles end to end."""

import asyncio
import shutil
from pathlib import Path

import pytest

from cjm_context_graph_projection.runtime import DEFAULT_GRAPH_ID, DEFAULT_MANIFESTS, open_graph
from cjm_context_graph_projection.site import output_href, publish_guard, redirect_page, site_build
from cjm_context_graph_projection.write import assert_value, decide

_HAVE_GRAPH = (Path(DEFAULT_MANIFESTS) / f"{DEFAULT_GRAPH_ID}.json").exists()
_HAVE_QUARTO = shutil.which("quarto") is not None

# The page Quarto 1.10 writes for `aliases: [/Notes-on-Advanced-Git-Tools/]` on
# posts/advanced-git-tools-notes/index.md (the public site's render, 2026-09-28)
_QUARTO_STUB = """<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  <title>Redirect</title>
  <script type="text/javascript">
    var redirects = {"":"../posts/advanced-git-tools-notes/index.html"};
    var hash = window.location.hash.startsWith('#') ? window.location.hash.slice(1) : window.location.hash;
    var redirect = redirects[hash] || redirects[""] || "/";
    window.document.title = 'Redirect to  ' +  redirect;
    if (!redirects[hash]) {
      redirect = redirect + window.location.hash;
    }
    redirect = redirect + window.location.search;
    window.location.replace(redirect);
  </script>
</head>
<body>
</body>
</html>
"""


def test_output_href_and_redirect_page_are_quartos():
    assert output_href("/Notes-on-X/") == "Notes-on-X/index.html"
    assert output_href("/log/2020/09/21/X") == "log/2020/09/21/X/index.html"   # no extension = a dir
    assert output_href("/series/notes/x.html") == "series/notes/x.html"
    assert redirect_page({"": "../posts/advanced-git-tools-notes/index.html"}) == _QUARTO_STUB


def _site(root: Path) -> None:
    (root / "posts" / "a").mkdir(parents=True)
    (root / "drafts" / "posts" / "d").mkdir(parents=True)
    (root / "_quarto.yml").write_text(
        "project:\n  type: website\nprofile:\n  default: public\n  group:\n    - [public, staging]\n"
        "website:\n  title: t\n")
    (root / "_quarto-public.yml").write_text(
        'project:\n  render:\n    - "**/*.qmd"\n    - "**/*.md"\n    - "!drafts/"\n')
    (root / "_quarto-staging.yml").write_text(
        'project:\n  output-dir: _site-staging\n  render:\n    - "**/*.qmd"\n    - "**/*.md"\n')
    (root / "index.md").write_text("---\ntitle: Home\n---\n\nHome.\n")
    (root / "posts" / "a" / "index.md").write_text(
        "---\ntitle: A\naliases:\n- /Old-A/\n---\n\nPost A.\n")
    # A draft's alias is publication intent (it holds no public path yet): the check skips it
    (root / "drafts" / "posts" / "d" / "index.md").write_text(
        "---\ntitle: D\naliases:\n- /Draft-Old/\n---\n\nA draft.\n")


@pytest.mark.skipif(not (_HAVE_GRAPH and _HAVE_QUARTO), reason="needs the graph capability and quarto")
def test_site_build_projects_redirects_checks_aliases_and_guards(tmp_path):
    # The facts are the redirect authority: a superseded path the front matter never declared
    # still gets its page (byte-identical to Quarto's), a front-matter alias no fact records
    # refuses the build before anything renders, the public output carries no draft, and the
    # staging profile renders the drafts into its own output.
    site = tmp_path / "site"
    _site(site)

    async def go():
        async with open_graph(str(tmp_path / "g.db")) as gx:
            page = (await decide(gx, "PAGE: post A (site build fixture)"))["decision_id"]
            await assert_value(gx, page, "site_path", "/posts/a/")
            for prior in ("/Old-A/", "/Older-A.html"):
                await assert_value(gx, page, "site_path", prior, superseded_by=["/posts/a/"])
            rep = await site_build(gx, str(site), "public")
            assert rep["ok"], rep
            out = site / "_site"
            assert (out / "Old-A" / "index.html").read_text() == redirect_page({"": "../posts/a/index.html"})
            assert (out / "Older-A.html").read_text() == redirect_page({"": "posts/a/index.html"})
            assert rep["redirects"]["stubs"] == 2 and rep["aliases"]["declared"] == 1
            assert not (out / "drafts").exists()
            # A leak into the public output fails the guard
            shutil.copytree(site / "drafts", out / "drafts")
            guard = await publish_guard(gx, str(out), "drafts")
            assert {e["kind"] for e in guard["errors"]} == {"drafts-tree"}
            shutil.rmtree(out / "drafts")
            # Staging: the drafts render beside the public pages, into the staging output
            rep = await site_build(gx, str(site), "staging")
            assert rep["ok"], rep
            assert (site / "_site-staging" / "drafts" / "posts" / "d" / "index.html").exists()
            assert (site / "_site-staging" / "Old-A" / "index.html").exists()
            # A front-matter alias the facts do not record refuses before any render
            (site / "posts" / "a" / "index.md").write_text(
                "---\ntitle: A\naliases:\n- /Old-A/\n- /Stray/\n---\n\nPost A.\n")
            rep = await site_build(gx, str(site), "public")
            assert not rep["ok"] and "render" not in rep
            assert [(e["kind"], e["alias"]) for e in rep["errors"]] == [("alias", "/Stray/")]

    asyncio.run(go())
