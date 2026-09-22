---
name: companion-screenshots
description: Regenerate the wiki's Companion button-board screenshots (src/docs/wiki/images/companion-pageN-*.png) after a button changes. Use when a Companion button was added/renamed/recolored/moved and the wiki screenshots are stale. Drives the running Companion web-buttons view via the Playwright MCP and crops the grid with ffmpeg. Cross-platform (no macOS-only tools).
---

# Companion button-board screenshots

Recreate the wiki screenshots of the Companion button grid for the GT Racing
Broadcast toolkit. The grid is served by a **running** Companion instance; this skill
screenshots it through a headless browser (Playwright MCP) and crops out the toolbar with
**ffmpeg** (already a project dependency). No pip dependencies, no manual cropping math.

For Control Center / `/console` / cockpit / Director-Panel shots use the sibling
[wiki-screenshots](../wiki-screenshots/SKILL.md) skill instead; this one is **only** for
the Companion button board. Verify the published result with
[wiki-visual-test](../wiki-visual-test/SKILL.md).

## When to use

After a Companion button is added, renamed, recolored, or moved, the wiki images in
`src/docs/wiki/images/` go stale and must be regenerated for the page(s) that changed.

Pages and their files:
- **Page 1** → `companion-page1-show-control.png` (combos, scenes, feeds & POV, graphics)
- **Page 2** → `companion-page2-timer-audio.png` (race timer + mute + per-source volume)
- **Page 3** → `companion-page3-flags.png` (race-condition FLAGS: text row + graphic row)

Only regenerate the page(s) you actually changed.

## Prerequisites

- **Companion is running** with the current config imported (the button you just built
  must be visible in its web UI). If it does not yet show your change, import it, the
  **autonomous, non-destructive recipe is below** (you do NOT need the user to do it).
  **Bind address:** `racecast companion start` binds Companion to this machine's **Tailscale
  IP**, not localhost, so the admin/web-buttons UI is at `http://<tailscale-ip>:8000`, and
  `http://localhost:8000` is often **not** bound. Find the real address with
  `lsof -nP -iTCP:8000 -sTCP:LISTEN` (the `100.64.0.0/10` address) and use it for every
  `browser_navigate` below.
- **Playwright MCP** available (the `mcp__plugin_playwright_playwright__*` tools).
- **ffmpeg** + **ffprobe** on PATH (`ffmpeg -version`). Both ship together; `ffprobe`
  replaces the old macOS-only `sips` dimension check, so this skill runs on Windows too.
- Working directory: the repo root.

## Getting the current config into Companion (autonomous, non-destructive)

When the running Companion shows an **old** version of the page (your button change is not
visible yet), import it yourself via the web UI: **page-scoped, preserving connections**.
This was verified live on Companion **v4.3.4**. It does NOT reset the producer's
connections (OBS-WebSocket etc.) or other pages, only the one destination page's buttons
are replaced.

1. **Export** the repo config: `python3 src/racecast.py export companion` → it writes
   `runtime/<profile>/racecast-buttons.companionconfig` (the importable file). The FLAGS
   button URLs are loopback (`127.0.0.1:8088`), so the active profile doesn't matter for a
   screenshot.
2. `browser_navigate` → `http://<tailscale-ip>:8000/import-export`.
3. Click the **“Import configuration”** control (`browser_snapshot` to get its ref), which
   opens a file chooser (modal state) → `browser_file_upload` with the absolute path to the
   exported `.companionconfig`. (file_upload only works once the chooser modal is open.)
4. In the import wizard, click the **“Buttons”** tab (NOT “Full Import”. Full Import
   replaces connections). The Buttons tab does a single-page import.
5. Set **Source Page** to the page you changed (step the ▶ arrow until the label reads e.g.
   `3 (FLAGS)`) and **Destination Page** to the same live page (step its ▶ arrow; confirm the
   live label matches, e.g. `3 (FLAGS)`, so you replace the right page).
6. The **“Import Connections Behavior”** table defaults each connection to **“Link to …”**
   (links to the existing connection): leave it; that is what preserves the producer's OBS
   connection. Do **not** choose “Create new connection”.
7. Click **“Replace page N with imported page”** (tag it first if the ref is unstable:
   `browser_evaluate` to set a known `id`, then `browser_click` that selector).
8. The live page is updated instantly: proceed to capture (the `/tablet` view reflects it).

The `.companionconfig` page indices are 1-based and match the page numbers
(`1 PAGE 1`, `2 PAGE 2`, `3 FLAGS`).

## The grid geometry (measured, stable)

In the web-buttons view at viewport **1280×720**:
- The top toolbar (fullscreen + configure icons) occupies **y = 0…~56**.
- The button tiles (`.button-control.clickable`) start at **y = 56**, **row pitch ≈ 157 px**,
  full width **1280** (8 columns, 12 px side padding included).
- So **N populated rows** crop to height `N*157 + 4`, starting at `y = 54` (≈2 px top margin).
  The board has **4 rows** per page → crop height **632**.

Do **not** rely on the tile **count** (`.button-control` lazy-renders extra empty rows at
taller viewports): rely on the fixed top (56) and pitch (157), and the known **4 rows**.

## Procedure

Do this per page that changed. Example shown for **page 1**.

1. **Navigate** to the web-buttons view:
   - Page 1: `mcp__…__browser_navigate` → `http://localhost:8000/tablet`
   - Page N>1: `…/tablet?pages=N`  *(caveat below)*

2. **Resize** the viewport so all 4 rows render:
   `mcp__…__browser_resize` → `width: 1280, height: 720`

3. *(Optional sanity check)* confirm the grid top + pitch with
   `mcp__…__browser_evaluate`:
   ```js
   () => { const t=[...document.querySelectorAll('.button-control.clickable')];
     let y0=1e9; t.forEach(e=>y0=Math.min(y0,e.getBoundingClientRect().top));
     return JSON.stringify({top:Math.round(y0), n:t.length, vp:[innerWidth,innerHeight]}); }
   ```
   Expect `top ≈ 56`.

4. **Screenshot** the viewport (not an element: `.buttons-holder` has zero height and is
   not screenshot-able):
   `mcp__…__browser_take_screenshot` → `type: png`, `filename: companion-vp.png`
   (it lands in the repo root / CWD).

5. **Crop** the toolbar off with ffmpeg, `crop=W:H:X:Y`:
   ```bash
   ffmpeg -y -loglevel error -i companion-vp.png \
     -vf "crop=1280:632:0:54" \
     src/docs/wiki/images/companion-page1-show-control.png
   ```
   (For a page with a different row count R, use `H = R*157 + 4`.)

6. **Verify** visually: Read the output PNG. Check the changed button shows correctly, the
   four rows are fully visible, top/bottom margins look even. Confirm the size is
   `1280×632` cross-platform with ffprobe:
   ```bash
   ffprobe -v error -select_streams v:0 -show_entries stream=width,height \
     -of csv=p=0 src/docs/wiki/images/companion-page1-show-control.png   # -> 1280,632
   ```

7. **Clean up** the scratch viewport PNG(s) from the repo root:
   `rm -f companion-vp.png`

8. **Commit** the updated image(s):
   ```bash
   git add src/docs/wiki/images/companion-page1-show-control.png
   git commit -m "docs(companion): refresh page-1 button-board screenshot"
   ```

9. **Publish** the wiki only on the user's go-ahead: `python3 tools/sync-wiki.py`
   (preview first with `--dry-run`), then verify with the
   [wiki-visual-test](../wiki-visual-test/SKILL.md) skill.

## Caveat: page 2+ has a navigation column

`/tablet` (no params) shows **page 1** with real buttons in column 0 (no nav). The nav-column
behaviour depends on the URL form and Companion version, so the rule is: **match the framing
of the existing image for that page.** Observed forms:
- `companion-page2-timer-audio.png` was shot with a left **UP / PAGE n / DOWN** nav column
  (buttons shifted one column right).
- `?pages=3` on Companion **v4.3.4** renders the single page **without** a nav column (buttons
  start in column 0), which matches the existing `companion-page3-flags.png` framing, so it
  is the correct URL for page 3.

Before re-shooting a page, `Read` its existing image and reproduce the same framing (nav
column or not, buttons start column) and the same `1280×632` crop.

## Notes

- All three pages are shot with this recipe at `1280×632`.
- Companion renders buttons as positioned `div`s (not `<img>`/`<canvas>`), and the
  `.buttons-holder` wrapper has height 0: that's why we screenshot the **viewport** and
  crop, rather than screenshotting an element.
