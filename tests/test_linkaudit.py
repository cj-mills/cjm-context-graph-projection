"""Tests for cjm_context_graph_projection.linkaudit — the external-link liveness
audit (finding 5761f954). Pure: extraction, domain rule, classification over
synthetic probes, and the Wayback bracket over a synthetic CDX payload. No network."""

from cjm_context_graph_projection.linkaudit import (bracket_from_cdx, classify_link,
                                                    extract_external_links, registrable_domain)


def test_extract_external_links_three_shapes_dedup_and_trailing_punctuation():
    text = ("See [the post](https://example.com/a) and <https://example.com/b>, or "
            "https://example.com/c. Again https://example.com/a! A wiki page "
            "(https://en.wikipedia.org/wiki/Foo_(bar)) and a relative [x](/posts/y/) "
            "plus [mail](mailto:me@example.com).")
    assert extract_external_links(text) == [
        "https://example.com/a", "https://example.com/b", "https://example.com/c",
        "https://en.wikipedia.org/wiki/Foo_(bar)"]
    assert extract_external_links("") == []


def test_registrable_domain_handles_www_subdomains_and_two_level_suffixes():
    assert registrable_domain("https://www.afabrega.com/my-blog/x") == "afabrega.com"
    assert registrable_domain("https://newsletter.afabrega.com/p/x") == "afabrega.com"
    assert registrable_domain("https://www.blog.example.co.uk/a") == "example.co.uk"
    assert registrable_domain("http://localhost:8080/") == "localhost"


def test_classify_link_states():
    ok = {"url": "https://a.org/x", "chain": [["https://a.org/x", 200]],
          "final_url": "https://a.org/x", "final_code": 200, "error": None}
    assert classify_link(ok) == "ok"
    # scheme / www / trailing-slash normalization is still ok
    www = {"url": "http://a.org/x", "chain": [["http://a.org/x", 301], ["https://www.a.org/x/", 200]],
           "final_url": "https://www.a.org/x/", "final_code": 200, "error": None}
    assert classify_link(www) == "ok"
    moved = {"url": "https://a.org/old", "chain": [["https://a.org/old", 301], ["https://a.org/new", 200]],
             "final_url": "https://a.org/new", "final_code": 200, "error": None}
    assert classify_link(moved) == "moved"
    # the hijack signature: a hop leaves the registrable domain (whatever the end status)
    hijacked = {"url": "https://afabrega.com/my-blog/x",
                "chain": [["https://afabrega.com/my-blog/x", 301], ["https://www.shadesofmoss.com/", 301],
                          ["https://tototogelwow.com/", 403]],
                "final_url": "https://tototogelwow.com/", "final_code": 403, "error": None}
    assert classify_link(hijacked) == "offsite"
    dead = {"url": "https://a.org/gone", "chain": [["https://a.org/gone", 404]],
            "final_url": "https://a.org/gone", "final_code": 404, "error": None}
    assert classify_link(dead) == "dead"
    unreachable = {"url": "https://a.org/x", "chain": [], "final_url": "https://a.org/x",
                   "final_code": None, "error": "URLError: timed out"}
    assert classify_link(unreachable) == "dead"


def test_bracket_from_cdx_last_good_and_first_bad():
    rows = [["timestamp", "statuscode"], ["20220521033149", "200"], ["20250701033407", "200"],
            ["20260211085753", "200"]]
    b = bracket_from_cdx(rows)
    assert b == {"last_good": "20260211085753", "first_bad": None, "captures": 3}
    rows2 = rows + [["20260601000000", "301"], ["20260701000000", "301"]]
    b2 = bracket_from_cdx(rows2)
    assert (b2["last_good"], b2["first_bad"], b2["captures"]) == ("20260211085753", "20260601000000", 5)
    assert bracket_from_cdx([["timestamp", "statuscode"]]) == {"last_good": None, "first_bad": None, "captures": 0}
