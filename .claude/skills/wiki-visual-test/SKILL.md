---
name: wiki-visual-test
description: Visually verify the live GitHub wiki rendered correctly after publishing (tools/sync-wiki.py). Use after a wiki sync to confirm Mermaid diagrams render, embedded screenshots load, and changed content is present on the real wiki pages. Drives the published wiki pages through the Playwright MCP and reads the screenshots back to eyeball them. Complements the raw-content (curl) smoke test with an actual rendered view.
---

# Wiki visual test

Confirm the **published** GitHub wiki actually *renders* correctly, not just that the
raw markdown was pushed. A `curl` of the raw `.md` proves the text is there; it does **not**
prove GitHub rendered the Mermaid diagrams or that the embedded PNGs resolve. This skill
opens the live wiki pages in a headless browser (Playwright MCP), checks the render
programmatically, and screenshots them so you can eyeball the result.

Pairs with [companion-screenshots](../companion-screenshots/SKILL.md) and
[wiki-screenshots](../wiki-screenshots/SKILL.md) (which *regenerate* the images) and
`tools/sync-wiki.py` (which *publishes* the wiki). Run this **after** a sync.

## When to use

After `python3 tools/sync-wiki.py` has pushed wiki changes: especially when the change
touched a **Mermaid diagram**, an **embedded image** (e.g. a refreshed screenshot), or any
page whose visual layout matters. Only check the page(s) you changed.

## Prerequisites

- The wiki was **published** (sync-wiki ran; the change is live on GitHub).
- **Playwright MCP** available (the `mcp__plugin_playwright_playwright__*` tools).
- The repo is **public** (GitHub wikis of public repos render without login).
- Working directory: the repo root (so scratch PNGs land somewhere known and you can clean
  them up).

## URL shapes

Derive everything from the `origin` remote, **never hardcode owner/repo** (the project has
been renamed before):
```bash
git remote get-url origin    # -> https://github.com/<owner>/<repo>.git  (strip the .git)
```

- **Rendered page:** `https://github.com/<owner>/<repo>/wiki/<Page>`
  (e.g. `.../wiki/Run-an-event`: the page name is the file name without `.md`).
- **Raw markdown** (for the curl smoke test): `https://raw.githubusercontent.com/wiki/<owner>/<repo>/<Page>.md`
- **Embedded wiki image** (as the rendered page references it):
  `https://github.com/<owner>/<repo>/wiki/images/<file>.png`

## Procedure

Do this per changed page. Examples shown for `Run-an-event` (a Mermaid page) and `Director`
(an embedded-image page).

1. **Navigate** to the rendered page:
   `mcp__…__browser_navigate` → `https://github.com/<owner>/<repo>/wiki/<Page>`

2. **Resize** tall enough to capture the region you care about (GitHub has a header + the
   content column): `mcp__…__browser_resize` → `width: 1280, height: 1700`.

3. **Check the render programmatically** with `mcp__…__browser_evaluate`.

   For a **Mermaid** page: confirm GitHub turned the code fence into a diagram (the raw
   `flowchart`/`sequenceDiagram` text must be **gone**, a rendered container/SVG present):
   ```js
   () => {
     const body = (document.querySelector('.markdown-body')||document.body).innerText;
     const mer = document.querySelector('.markdown-body .mermaid, [data-type="mermaid"], .markdown-body svg[id^="mermaid"]');
     return JSON.stringify({
       hasMermaidContainer: !!mer,
       rawCodeStillVisible: /flowchart |sequenceDiagram|graph (TD|LR)/.test(body), // want FALSE
       textHits: ['Standby','Intro','Outro','INTRO','OUTRO'].filter(w => body.includes(w))
     });
   }
   ```
   Pass = `hasMermaidContainer:true`, `rawCodeStillVisible:false`, expected words present.
   (Mermaid renders node labels inside `<foreignObject>`/`<text>`, so an SVG's `textContent`
   is often empty: don't assert on it; rely on "raw code gone" + the screenshot.)

   For an **embedded-image** page: confirm each image actually loaded (decoded, non-zero
   natural size) and scroll the one you changed into view:
   ```js
   () => {
     const imgs = [...document.querySelectorAll('.markdown-body img')];
     const info = imgs.map(im => ({ src: im.currentSrc||im.src,
       loaded: im.complete && im.naturalWidth>0, nat: im.naturalWidth+'x'+im.naturalHeight }));
     const target = imgs.find(im => /companion-page1/.test(im.src)); // the changed image
     if (target) target.scrollIntoView({block:'center'});
     return JSON.stringify({images: info, scrolled: !!target});
   }
   ```
   Pass = the changed image has `loaded:true` and the expected `nat` dimensions
   (a broken/missing wiki image reports `loaded:false` / `0x0`).

4. **Screenshot the viewport** and save to the repo root:
   `mcp__…__browser_take_screenshot` → `type: png`, `filename: wiki-<page>.png`
   (the MCP writes it to the CWD).

5. **Read the PNG back** (`Read` tool) and eyeball it: the Mermaid diagram shows the new
   nodes/flow, or the embedded image shows the new content; layout/margins look sane.

6. **Clean up** the scratch PNG(s) from the repo root: `rm -f wiki-*.png`. They are not wiki
   content: never commit them.

## Optional: pair with the raw smoke test

Before (or alongside) the visual test, a quick non-browser check that the push landed and
the bytes are right:
```bash
RAW="https://raw.githubusercontent.com/wiki/<owner>/<repo>"
curl -s -o /dev/null -w "HTTP %{http_code}\n" "$RAW/<Page>.md"               # page text
curl -s -o /dev/null -w "HTTP %{http_code} %{content_type} %{size_download}\n" \
  "$RAW/images/<file>.png"                                                    # embedded image
```
A `200` + the expected byte count (matches the committed file size) confirms the asset is
live; the browser pass then confirms it actually *renders*.

## Notes / caveats

- **Console errors are usually benign.** GitHub pages emit telemetry/CSP console errors that
  have nothing to do with your content, don't treat `Console: N errors` as a failure. Judge
  by the render checks and the screenshot.
- **Don't screenshot an element** for Mermaid/markdown: screenshot the **viewport** after
  resizing/scrolling. The markdown wrappers can have odd computed heights.
- **Wiki propagation is fast but not instant.** If a just-synced change isn't visible,
  re-navigate after a few seconds (GitHub may serve a cached render briefly).
- **Only check changed pages.** Re-rendering every page wastes time; the sync output lists
  exactly which pages/images changed.
