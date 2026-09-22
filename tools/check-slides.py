#!/usr/bin/env python3
"""Visual overflow guard for the onboarding decks.

Reveal slides have a FIXED logical size (1280x720; see assets/deck.js). Text,
images or the Excalidraw SVGs silently overflow that box. This loads each deck in
a headless browser, walks every slide (including vertical sub-slides), measures the
content + each image/SVG against the box, and reports offenders with a screenshot.

Maintainer pre-publish gate, not CI. Needs the Playwright venv (see the
racecast-e2e skill).

Usage:
  python3 tools/check-slides.py [--shots DIR]
"""
import argparse, glob, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLIDES = os.path.join(ROOT, "src", "docs", "slides")
SLIDE_W, SLIDE_H = 1280, 720


def reveal_decks(slides_dir):
    """Basenames of the actual Reveal decks in slides_dir, sorted.

    Filters by the `class="reveal"` root marker rather than by name: the slides
    dir also holds non-deck pages (index.html, the printable cheat_sheets.html)
    that carry no Reveal runtime, so `wait_for_function("Reveal.isReady()")` would
    hang on them for the full timeout and abort the whole run.
    """
    out = []
    for p in sorted(glob.glob(os.path.join(slides_dir, "*.html"))):
        try:
            with open(p, encoding="utf-8") as fh:
                html = fh.read()
        except OSError:
            continue
        if 'class="reveal"' in html:
            out.append(os.path.basename(p))
    return out


def overflow_findings(measurements, tol=2):
    """Pure: turn per-slide measurements into overflow findings."""
    out = []
    for m in measurements:
        if m["cw"] > SLIDE_W + tol or m["ch"] > SLIDE_H + tol:
            out.append({"deck": m["deck"], "slide": m["slide"], "kind": "content",
                        "detail": f'{m["cw"]}x{m["ch"]} > {SLIDE_W}x{SLIDE_H}'})
        for media in m.get("media", []):
            if media["w"] > SLIDE_W + tol or media["h"] > SLIDE_H + tol:
                out.append({"deck": m["deck"], "slide": m["slide"], "kind": "media",
                            "detail": f'{media["src"]} {media["w"]}x{media["h"]}'})
    return out


_MEASURE_JS = """() => {
  const s = Reveal.getCurrentSlide();
  const media = [...s.querySelectorAll('img,svg')].map(e => ({
    src: e.getAttribute('src') || 'svg', w: e.scrollWidth, h: e.scrollHeight }));
  return { cw: s.scrollWidth, ch: s.scrollHeight, media };
}"""


def check(slides_dir, deck_files, shots_dir=None):              # pragma: no cover
    from playwright.sync_api import sync_playwright
    if shots_dir is not None:
        os.makedirs(shots_dir, exist_ok=True)
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch()
        try:
            findings = []
            for deck in deck_files:
                stem = os.path.splitext(deck)[0]
                page = browser.new_page(viewport={"width": SLIDE_W, "height": SLIDE_H})
                page.goto("file://" + os.path.join(slides_dir, deck))
                page.wait_for_function("window.Reveal && Reveal.isReady()")
                total = page.evaluate("Reveal.getTotalSlides()")
                measures = []
                for _ in range(total):
                    idx = page.evaluate("Reveal.getIndices()")
                    h, v = idx["h"], idx.get("v", 0)
                    m = page.evaluate(_MEASURE_JS)
                    m.update({"deck": deck, "slide": f"{h}.{v}"})
                    slide_findings = overflow_findings([m])
                    if slide_findings and shots_dir is not None:
                        shot = os.path.join(shots_dir, f"{stem}-slide-{h}.{v}.png")
                        page.screenshot(path=shot)
                    measures.append(m)
                    page.evaluate("Reveal.next()")
                findings += overflow_findings(measures)
            return findings
        finally:
            browser.close()
    finally:
        pw.stop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    args = ap.parse_args()
    decks = reveal_decks(SLIDES)
    findings = check(SLIDES, decks, shots_dir=args.shots)
    for f in findings:
        print(f'OVERFLOW {f["deck"]} slide {f["slide"]} [{f["kind"]}]: {f["detail"]}')
    if findings:
        sys.exit(f"{len(findings)} overflowing slide(s)")
    print(f"OK, no overflow in {len(decks)} deck(s)")


if __name__ == "__main__":
    main()
