#!/usr/bin/env python3
"""Stdlib checks for the Help-page Markdown renderer (src/ui/mdrender.py).
Run: python3 tests/test_mdrender.py"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "ui"))
import mdrender as md


def t_headings():
    assert md.render("# Title") == "<h1>Title</h1>"
    assert md.render("### Sub") == "<h3>Sub</h3>"


def t_inline_formatting():
    h = md.render("Use **bold**, *italic* and `code` here.")
    assert "<strong>bold</strong>" in h
    assert "<em>italic</em>" in h
    assert "<code>code</code>" in h


def t_inline_escapes_html_but_keeps_snake_case():
    h = md.render("a < b and file_name_here stays")
    assert "&lt; b" in h                                   # escaped
    assert "file_name_here" in h and "<em>" not in h      # underscores not italic


def t_links():
    h = md.render("See [the wiki](https://example/wiki).")
    assert '<a href="https://example/wiki" target="_blank" rel="noopener">the wiki</a>' in h


def t_autolink_angle_brackets():
    # The bundled operator docs link via <https://...> autolinks, which must become a
    # clickable anchor rather than literal &lt;...&gt; text. (#48)
    h = md.render("Open <https://example.com/wiki/Run-an-event> now.")
    assert ('<a href="https://example.com/wiki/Run-an-event" target="_blank" '
            'rel="noopener">https://example.com/wiki/Run-an-event</a>') in h
    assert "&lt;https" not in h and "&gt;" not in h


def t_autolink_mailto():
    h = md.render("Mail <mailto:crew@example.com> please.")
    assert '<a href="mailto:crew@example.com"' in h


def t_link_javascript_scheme_dropped():
    # Untrusted input (e.g. a release note) must not yield a javascript: anchor.
    h = md.render("[click](javascript:alert(1))")
    assert "javascript:" not in h
    assert "<a " not in h                 # unsafe URL -> link text only, no anchor
    assert "click" in h


def t_link_data_scheme_dropped():
    h = md.render("[x](data:text/html,<script>alert(1)</script>)")
    assert "data:" not in h
    assert "<a " not in h


def t_link_quote_in_href_cannot_break_out_of_attribute():
    # A double-quote in the URL must be escaped so it cannot inject an attribute.
    h = md.render('[x](https://e"onmouseover="alert(1))')
    assert 'onmouseover="' not in h       # no raw attribute injection
    assert "&quot;" in h                  # the quote was neutralised


def t_link_relative_and_anchor_preserved():
    assert '<a href="#section"' in md.render("[s](#section)")
    assert '<a href="docs/x.md"' in md.render("[d](docs/x.md)")


def t_fenced_code_is_escaped_verbatim():
    h = md.render("```\nracecast relay start <x>\n```")
    assert "<pre><code>" in h and "racecast relay start &lt;x&gt;" in h
    assert "<em>" not in h                                 # no inline formatting in code


def t_unordered_list():
    h = md.render("- one\n- two")
    assert h == "<ul><li>one</li><li>two</li></ul>"


def t_ordered_list():
    h = md.render("1. first\n2. second")
    assert h.startswith("<ol>") and "<li>first</li>" in h and "<li>second</li>" in h


def t_nested_list():
    h = md.render("- parent\n  - child\n- sibling")
    assert "<ul><li>parent<ul><li>child</li></ul></li><li>sibling</li></ul>" == h


def t_table_with_alignment():
    src = ("| Component | Min | Rec |\n"
           "|:--|:-:|--:|\n"
           "| RAM | 8 GB | 16 GB |")
    h = md.render(src)
    assert "<table>" in h and "<thead>" in h
    assert "<th>Component</th>" in h
    assert 'style="text-align:center"' in h and 'style="text-align:right"' in h
    assert "<td>RAM</td>" in h and "<td" in h and "16 GB" in h


def t_blockquote():
    h = md.render("> note here")
    assert "<blockquote>" in h and "note here" in h


def t_paragraph_joins_wrapped_lines():
    h = md.render("one line\ncontinues here\n\nnew para")
    assert "<p>one line continues here</p>" in h and "<p>new para</p>" in h


def t_page_is_self_contained():
    doc = md.page("My Doc", "<p>hi</p>")
    assert doc.startswith("<!doctype html>")
    assert "<title>My Doc</title>" in doc and "<style>" in doc
    assert "<main><p>hi</p></main>" in doc


def t_renders_real_docs_without_crashing():
    # The bundled docs must render cleanly: a heading, a list and no leftover code
    # placeholders.
    for rel in ("docs/README_SETUP.md", "docs/Broadcast_Setup_Guide.md"):
        with open(os.path.join(ROOT, "src", rel), encoding="utf-8") as fh:
            h = md.render(fh.read())
        assert "<h1>" in h and "<li>" in h
        assert "\x00" not in h                             # all code placeholders restored
        assert '<a href="https://' in h                    # wiki autolinks are clickable (#48)
        assert "&lt;https" not in h                        # not shown as literal <https...>


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")
