#!/usr/bin/env python3
"""Vendor the slide-deck runtime: a pinned Reveal.js dist subset + the two deck
webfonts, both committed under src/docs/slides/ so Pages/build need no network.

Downloads a pinned, SHA-256-verified GitHub source zip of Reveal.js, extracts only
the dist + plugin files the decks load, and fetches the deck webfonts (Saira
Condensed + IBM Plex Mono) as woff2.

Maintainer tool, not shipped. Run after bumping REVEAL_TAG; a first run with an
empty REVEAL_SHA256 prints the computed digest to paste back in, then commit.

Usage:
  python3 tools/fetch-reveal.py            # vendor reveal + fonts
  python3 tools/fetch-reveal.py --print-sha  # just print the release digest
"""
import argparse, hashlib, io, os, re, sys, zipfile
from urllib.request import Request, urlopen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import overlay_build as ob   # google_font_css_url (DRY with fetch-fonts.py)

REVEAL_TAG = "5.2.1"
# Filled after the first run prints it; empty means "print, don't verify".
REVEAL_SHA256 = "ad6fe79a57309a80a09a7ea7fa1d8cb260caf045567cb2198d70c0c896336257"
FONT_FAMILIES = ["Saira Condensed", "IBM Plex Mono"]

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
_WANTED = (
    "dist/reveal.js", "dist/reveal.css",
    "plugin/markdown/markdown.js", "plugin/notes/notes.js",
    "plugin/highlight/highlight.js", "plugin/highlight/monokai.css",
)


def _http(url, binary=True, timeout=60):
    req = Request(url, headers={"User-Agent": _UA})
    with urlopen(req, timeout=timeout) as r:    # noqa: S310 (fixed GitHub/gstatic hosts)
        data = r.read()
    return data if binary else data.decode("utf-8", "replace")


def reveal_subset(prefix):
    """Map {zip member path -> dest path under vendor/reveal} for the wanted files."""
    return {prefix + w: w for w in _WANTED}


def verify_sha256(data, expected):
    got = hashlib.sha256(data).hexdigest()
    if expected and got != expected:
        raise SystemExit(f"fetch-reveal: sha256 mismatch (got {got}, want {expected})")
    return got


def extract_subset(zip_bytes, prefix, dest_dir, fetch=None):
    """Write the wanted subset from the in-memory zip into dest_dir. Returns dest rels."""
    sub = reveal_subset(prefix)
    written = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for member, dest_rel in sub.items():
            data = z.read(member)
            out = os.path.join(dest_dir, dest_rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as fh:
                fh.write(data)
            written.append(dest_rel)
    return written


def fetch_font(name, css_fetch=None, bin_fetch=None):
    css_fetch = css_fetch or (lambda u: _http(u, binary=False))
    bin_fetch = bin_fetch or (lambda u: _http(u, binary=True))
    for url in (ob.google_font_css_url(name), ob.google_font_css_url(name, weight=None)):
        try:
            css = css_fetch(url)
        except Exception:
            continue
        m = re.search(r"url\((https://fonts\.gstatic\.com/[^)]+\.woff2)\)", css or "")
        if m:
            return bin_fetch(m.group(1))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--print-sha", action="store_true")
    a = ap.parse_args()
    url = f"https://github.com/hakimel/reveal.js/archive/refs/tags/{REVEAL_TAG}.zip"
    zip_bytes = _http(url)
    digest = verify_sha256(zip_bytes, "" if a.print_sha else REVEAL_SHA256)
    if a.print_sha:
        print(digest); return
    if not REVEAL_SHA256:
        print(f"REVEAL_SHA256 not set; computed {digest}. Paste it in and re-run.")
        return
    vendor = os.path.join(ROOT, "src", "docs", "slides", "vendor", "reveal")
    written = extract_subset(zip_bytes, f"reveal.js-{REVEAL_TAG}/", vendor)
    print(f"vendored reveal {REVEAL_TAG}: {len(written)} files -> {vendor}")
    fonts_dir = os.path.join(ROOT, "src", "docs", "slides", "assets", "fonts")
    os.makedirs(fonts_dir, exist_ok=True)
    for fam in FONT_FAMILIES:
        data = fetch_font(fam)
        if not data:
            print(f"WARNING: no woff2 for {fam}", file=sys.stderr); continue
        fn = ob.google_font_filename(fam)
        with open(os.path.join(fonts_dir, fn), "wb") as fh:
            fh.write(data)
        print(f"vendored font {fam} -> {fn}")


if __name__ == "__main__":
    main()
