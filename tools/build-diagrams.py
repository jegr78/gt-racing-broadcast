#!/usr/bin/env python3
"""Convert deck Mermaid sources to committed Excalidraw-look static SVGs.

Each src/docs/slides/diagrams/<name>.mmd is rendered, build-time, by a headless
browser (Playwright) over a tiny harness page that loads the pinned
@excalidraw/mermaid-to-excalidraw + @excalidraw/excalidraw libs (esm.sh, build-time
only), converts the Mermaid to Excalidraw elements, PINS every element seed so the
hand-drawn 'rough' look is reproducible (quiet git diffs), and exports to SVG. The
SVG is committed to assets/img/diagrams/<name>.svg; the shipped/published site stays
JS-free for diagrams. Maintainer tool, not shipped.

Prereq: a venv with Playwright + chromium (see the racecast-e2e skill):
  python3 -m venv .venv-pw && . .venv-pw/bin/activate
  pip install playwright && playwright install chromium

Usage:
  python3 tools/build-diagrams.py            # render every .mmd
"""
import argparse, glob, os, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLIDES = os.path.join(ROOT, "src", "docs", "slides")
MERMAID_TO_EXCALIDRAW = "https://esm.sh/@excalidraw/mermaid-to-excalidraw@1.1.2"
EXCALIDRAW = "https://esm.sh/@excalidraw/excalidraw@0.18.0"
SEED = 12345


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


def discover_mmd(slides_dir):
    out = []
    for p in sorted(glob.glob(os.path.join(slides_dir, "diagrams", "*.mmd"))):
        out.append((os.path.splitext(os.path.basename(p))[0], p))
    return out


def harness_html(mermaid_src):
    """Full HTML for the headless converter page. Sets window.__svg (or __err)."""
    src_js = mermaid_src.replace("\\", "\\\\").replace("`", "\\`")
    return f"""<!doctype html><html><head><meta charset="utf-8"></head><body>
<script type="module">
import {{ parseMermaidToExcalidraw }} from "{MERMAID_TO_EXCALIDRAW}";
import {{ convertToExcalidrawElements, exportToSvg }} from "{EXCALIDRAW}";
try {{
  const {{ elements }} = await parseMermaidToExcalidraw(`{src_js}`);
  const els = convertToExcalidrawElements(elements);
  // Deterministic look: pin every element seed so regeneration is reproducible.
  els.forEach((e, i) => {{ e.seed = {SEED} + i; e.versionNonce = {SEED} + i; }});
  const svg = await exportToSvg({{ elements: els, files: null,
    appState: {{ exportBackground: false, exportWithDarkMode: false }} }});
  window.__svg = svg.outerHTML;
}} catch (err) {{ window.__err = String(err); }}
</script></body></html>"""


def render_all(slides_dir=SLIDES, page_factory=None):
    """Render every .mmd to assets/img/diagrams/<name>.svg. Returns written paths."""
    if page_factory is None:                       # pragma: no cover (needs browser)
        from playwright.sync_api import sync_playwright

        def _default(html):
            pw = sync_playwright().start()
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.set_content(html, wait_until="networkidle")
            page.wait_for_function("window.__svg !== undefined || window.__err !== undefined",
                                   timeout=60000)
            err = page.evaluate("window.__err")
            svg = page.evaluate("window.__svg")
            browser.close(); pw.stop()
            if err:
                raise SystemExit(f"build-diagrams: convert failed: {err}")
            return svg
        page_factory = _default

    out_dir = os.path.join(slides_dir, "assets", "img", "diagrams")
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for name, path in discover_mmd(slides_dir):
        with open(path, encoding="utf-8") as fh:
            svg = page_factory(harness_html(fh.read()))
        dest = os.path.join(out_dir, f"{name}.svg")
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(svg)
        written.append(dest); print(f"rendered {name} -> {dest}")
    return written


def main():
    argparse.ArgumentParser().parse_args()
    render_all()


if __name__ == "__main__":
    main()
