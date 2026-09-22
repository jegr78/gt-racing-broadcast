#!/usr/bin/env python3
"""Stdlib unit checks for the intra-wiki link checker (tools/check-wiki-links.py)
plus the integration run over the real src/docs/wiki/ pages.
Run: python3 tests/test_wiki.py"""
import importlib.util, os, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "check_wiki_links", os.path.join(ROOT, "tools", "check-wiki-links.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def _write(directory, name, text):
    with open(os.path.join(directory, name), "w", encoding="utf-8") as fh:
        fh.write(text)


def t_anchor_basic():
    assert m.github_anchor("Run an event") == "run-an-event"
    # Em-dash, dots, backticks and parens all drop.
    assert m.github_anchor("4 — Add your secrets (`.env`)") == "4--add-your-secrets-env"


def t_anchor_double_dash_from_dropped_plus():
    # The removed '+' sits between two spaces, which yields a double dash.
    assert (m.github_anchor("Through the broadcast (scene + sheet cues)")
            == "through-the-broadcast-scene--sheet-cues")


def t_anchor_duplicate_headings_get_suffixes():
    seen = {}
    assert m.github_anchor("Setup", seen) == "setup"
    assert m.github_anchor("Setup", seen) == "setup-1"
    assert m.github_anchor("Setup", seen) == "setup-2"


def t_anchor_unescapes_html_entities():
    # OBS-Setup.md writes '&amp;' literally; GitHub renders '&', then drops it.
    assert m.github_anchor("4. HUD &amp; graphics (Browser Sources)") \
        == "4-hud--graphics-browser-sources"


def t_headings_skip_fenced_code():
    md = "# Real\n```bash\n# not a heading\n```\n## Also real\n~~~\n# nope\n~~~\n"
    assert m.extract_headings(md) == ["Real", "Also real"]


def t_extract_links_targets_and_lines():
    md = ("See [guide](Director) and [step](Director-Setup#step-1).\n"
          "```\n[in a fence](Ignored)\n```\n"
          "[same page](#local) and ![image](images/p.png) embed.\n")
    links = m.extract_links(md)
    assert (1, "Director") in links
    assert (1, "Director-Setup#step-1") in links
    assert all(t != "Ignored" for _, t in links)          # fences skipped
    assert (5, "#local") in links
    assert all("images/p.png" != t for _, t in links)     # image embed skipped


def t_check_wiki_reports_missing_page_and_anchor():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "Home.md",
               "# Home\n[ok](Page)\n[ok2](Page#a-section)\n[bad](Missing)\n"
               "[badanchor](Page#nope)\n[ext](https://example.com/x)\n"
               "[file](images/p.png)\n")
        _write(d, "Page.md", "# Page\n## A section\n")
        errors = m.check_wiki(d)
        assert len(errors) == 2, errors
        assert any("Missing" in e for e in errors)
        assert any("nope" in e for e in errors)


def t_check_wiki_same_page_anchor():
    with tempfile.TemporaryDirectory() as d:
        _write(d, "Solo.md", "# Solo\n## Deep dive\n[jump](#deep-dive)\n[bad](#none)\n")
        errors = m.check_wiki(d)
        assert len(errors) == 1 and "#none" in errors[0], errors


def t_check_wiki_real_pages_are_clean():
    errors = m.check_wiki(os.path.join(ROOT, "src", "docs", "wiki"))
    assert errors == [], "\n".join(errors)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
