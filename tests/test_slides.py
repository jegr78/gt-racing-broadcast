#!/usr/bin/env python3
"""Stdlib structural/link checks for the onboarding slide decks.
Run: python3 tests/test_slides.py"""
import importlib.util, io, os, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SLIDES = os.path.join(ROOT, "src", "docs", "slides")


def _load(modpath, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, modpath))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


fr = _load(os.path.join("tools", "fetch-reveal.py"), "fetch_reveal")


def t_reveal_subset_maps_dist_and_plugins():
    sub = fr.reveal_subset("reveal.js-5.2.1/")
    # Every wanted file is mapped, rebased under vendor/reveal/.
    assert sub["reveal.js-5.2.1/dist/reveal.js"] == "dist/reveal.js"
    assert sub["reveal.js-5.2.1/dist/reveal.css"] == "dist/reveal.css"
    assert sub["reveal.js-5.2.1/plugin/markdown/markdown.js"] == "plugin/markdown/markdown.js"
    assert sub["reveal.js-5.2.1/plugin/notes/notes.js"] == "plugin/notes/notes.js"
    assert sub["reveal.js-5.2.1/plugin/highlight/highlight.js"] == "plugin/highlight/highlight.js"
    assert sub["reveal.js-5.2.1/plugin/highlight/monokai.css"] == "plugin/highlight/monokai.css"


def t_verify_sha256_raises_on_mismatch():
    raised = False
    try:
        fr.verify_sha256(b"hello", "0" * 64)
    except SystemExit:
        raised = True
    assert raised


def t_extract_subset_writes_only_wanted_files(tmp=None):
    import tempfile
    dest = tempfile.mkdtemp(prefix="reveal-test-")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("reveal.js-5.2.1/dist/reveal.js", "// reveal")
        z.writestr("reveal.js-5.2.1/dist/reveal.css", ".reveal{}")
        z.writestr("reveal.js-5.2.1/plugin/markdown/markdown.js", "// md")
        z.writestr("reveal.js-5.2.1/plugin/notes/notes.js", "// notes")
        z.writestr("reveal.js-5.2.1/plugin/highlight/highlight.js", "// hl")
        z.writestr("reveal.js-5.2.1/plugin/highlight/monokai.css", ".hl{}")
        z.writestr("reveal.js-5.2.1/README.md", "ignored")  # not in subset
    written = fr.extract_subset(buf.getvalue(), "reveal.js-5.2.1/", dest)
    assert os.path.isfile(os.path.join(dest, "dist", "reveal.js"))
    assert os.path.isfile(os.path.join(dest, "plugin", "markdown", "markdown.js"))
    assert not os.path.exists(os.path.join(dest, "README.md"))
    assert "dist/reveal.js" in written


bd = _load(os.path.join("tools", "build-diagrams.py"), "build_diagrams")


def t_slug_is_filesystem_safe():
    assert bd.slug("Director Event Flow") == "director-event-flow"


def t_harness_embeds_source_and_pinned_libs_and_seed():
    html = bd.harness_html("flowchart LR\n A-->B")
    assert "flowchart LR" in html
    assert bd.MERMAID_TO_EXCALIDRAW in html
    assert bd.EXCALIDRAW in html
    assert "seed" in html and str(bd.SEED) in html, \
        "the pinned seed is what keeps a regenerated SVG stable"


def t_discover_mmd_finds_sources(tmp=None):
    import tempfile
    d = tempfile.mkdtemp(prefix="mmd-test-")
    os.makedirs(os.path.join(d, "diagrams"))
    with open(os.path.join(d, "diagrams", "x.mmd"), "w") as fh:
        fh.write("flowchart LR\n A-->B")
    found = bd.discover_mmd(d)
    assert found == [("x", os.path.join(d, "diagrams", "x.mmd"))]


import re as _re

_WIKI = os.path.join(ROOT, "src", "docs", "wiki")
_WIKI_LINK = _re.compile(r"github\.com/[^/]+/[^/]+/wiki/([A-Za-z0-9_\-]+)")
_LOCAL_ASSET = _re.compile(r'(?:href|src)="((?:assets|vendor)/[^"]+)"')
_MD_IMG = _re.compile(r"!\[[^\]]*\]\((assets/[^)]+)\)")


# Every role/setup deck and the data-role its <body> must carry.
_DECK_ROLES = {
    "director.html": "director",
    "producer.html": "producer",
    "commentator.html": "commentator",
    "race-control.html": "race-control",
    "producer-setup.html": "producer",
    "league-admin-setup.html": "league-admin",
    "overlay-designer.html": "overlay-designer",
}


def _decks():
    # The role decks plus the landing page, for the wiki-link and asset scans.
    return [os.path.join(SLIDES, f) for f in (*_DECK_ROLES, "index.html")]


def t_all_decks_scaffolded():
    for fname, role in _DECK_ROLES.items():
        path = os.path.join(SLIDES, fname)
        assert os.path.isfile(path), f"missing deck {fname}"
        with open(path, encoding="utf-8") as fh:
            html = fh.read()
        assert f'data-role="{role}"' in html, f"{fname} missing data-role={role}"
        assert 'assets/deck.css' in html and 'assets/deck.js' in html, fname
        assert 'vendor/reveal/dist/reveal.js' in html, fname
        assert 'data-markdown' in html, fname


def t_accent_defined_for_every_deck_role():
    with open(os.path.join(SLIDES, "assets", "deck.css"), encoding="utf-8") as fh:
        css = fh.read()
    for role in set(_DECK_ROLES.values()):
        assert f'body[data-role="{role}"]' in css, f"deck.css missing accent for {role}"


def t_landing_links_every_deck():
    with open(os.path.join(SLIDES, "index.html"), encoding="utf-8") as fh:
        html = fh.read()
    for fname in _DECK_ROLES:
        assert f'href="{fname}"' in html, f"index.html does not link {fname}"


def t_cheatsheet_present_and_linked():
    # The cheat sheet lives only in the published Pages root, src/docs/slides.
    # The landing page links it and the Control Center Help page points here
    # rather than serving a local copy.
    assert os.path.isfile(os.path.join(SLIDES, "cheat_sheets.html")), \
        "missing slides/cheat_sheets.html"
    # The old duplicate source must be gone.
    assert not os.path.isfile(os.path.join(ROOT, "src", "docs", "cheat_sheets.html")), \
        "src/docs/cheat_sheets.html should be removed — the slides copy is canonical"
    with open(os.path.join(SLIDES, "index.html"), encoding="utf-8") as fh:
        assert 'href="cheat_sheets.html"' in fh.read(), "landing page does not link the cheat sheet"


def t_favicon_present_and_linked():
    for f in ("favicon.svg", "apple-touch-icon.png"):
        assert os.path.isfile(os.path.join(SLIDES, f)), f"missing {f}"
    for fname in (*_DECK_ROLES, "index.html"):
        with open(os.path.join(SLIDES, fname), encoding="utf-8") as fh:
            html = fh.read()
        assert 'href="favicon.svg"' in html, f"{fname} does not link the favicon"


def t_outbound_wiki_links_resolve():
    for deck in _decks():
        with open(deck, encoding="utf-8") as fh:
            html = fh.read()
        for page in _WIKI_LINK.findall(html):
            assert os.path.isfile(os.path.join(_WIKI, page + ".md")), \
                f"{os.path.basename(deck)} -> missing wiki page {page}"


def t_referenced_local_assets_exist():
    for deck in _decks():
        with open(deck, encoding="utf-8") as fh:
            html = fh.read()
        for rel in _LOCAL_ASSET.findall(html) + _MD_IMG.findall(html):
            assert os.path.isfile(os.path.join(SLIDES, rel)), \
                f"{os.path.basename(deck)} -> missing asset {rel}"


def t_every_mmd_has_committed_svg():
    for name, _ in bd.discover_mmd(SLIDES):
        svg = os.path.join(SLIDES, "assets", "img", "diagrams", name + ".svg")
        assert os.path.isfile(svg), f"missing generated SVG for {name}"


def t_no_shell_scripts_in_slides():
    for _, _, files in os.walk(SLIDES):
        assert not any(f.endswith((".sh", ".bat")) for f in files)


cs = _load(os.path.join("tools", "check-slides.py"), "check_slides")


def t_overflow_findings_flags_content_and_media():
    measures = [
        {"deck": "director.html", "slide": "0", "cw": 1200, "ch": 700,
         "media": []},                                  # fits
        {"deck": "director.html", "slide": "3", "cw": 1300, "ch": 700,
         "media": []},                                  # content too wide
        {"deck": "director.html", "slide": "5", "cw": 1000, "ch": 700,
         "media": [{"src": "x.svg", "w": 1400, "h": 600}]},  # media too wide
    ]
    found = cs.overflow_findings(measures)
    flagged = {(f["slide"], f["kind"]) for f in found}
    assert ("3", "content") in flagged
    assert ("5", "media") in flagged
    assert ("0", "content") not in flagged


def t_reveal_decks_only_returns_reveal_pages():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        # Two real decks carrying the Reveal root, and two non-deck pages that
        # would otherwise hang wait_for_function('Reveal.isReady()').
        with open(os.path.join(d, "director.html"), "w") as fh:
            fh.write('<!doctype html><div class="reveal"><div class="slides"></div></div>')
        with open(os.path.join(d, "commentator.html"), "w") as fh:
            fh.write('<div class="reveal"><div class="slides"></div></div>')
        with open(os.path.join(d, "index.html"), "w") as fh:
            fh.write("<!doctype html><h1>landing</h1>")
        with open(os.path.join(d, "cheat_sheets.html"), "w") as fh:
            fh.write("<!doctype html><table><tr><td>printable cheat sheet</td></tr></table>")
        decks = cs.reveal_decks(d)
        assert decks == ["commentator.html", "director.html"], decks  # sorted, decks only


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
