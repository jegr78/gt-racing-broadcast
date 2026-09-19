---
name: companion-buttons
description: Add or change Companion buttons in src/companion/racecast-buttons.companionconfig and deploy+validate them autonomously — author the button JSON, export, import into a running Companion via Playwright, and click-test. Use when a relay control needs a Stream-Deck button. Pairs with companion-screenshots (screenshot geometry) — do the import here, then screenshot there.
---

# Companion buttons — author, import, validate

Add/modify buttons on the Companion board **from the repo source** and get them live in a
running Companion **without hand-clicking the UI**, then prove they fire. Learned the hard
way on Companion **v4.3.4** with the **Playwright Python** library (`.venv-pw`), not the MCP.

The source of truth is `src/companion/racecast-buttons.companionconfig` (126k JSON). `racecast
export companion` writes the importable copy to `runtime/<profile>/racecast-buttons.companionconfig`.

## Design the endpoint for a GET button first

Every existing relay-control button is a generic-http **GET** with the whole request in the
URL path (`/reload/A`, `/set/A/3`, `/feed/A/activate`). **Make the button hit a GET route** —
do NOT try to send a JSON body from a generic-http POST action. The generic-http v3 POST
action's body-option schema is not reliably clonable by hand; a POST button imports and
renders fine but silently sends an empty body (the relay gets `{}` → 400 → no-op). If the
relay only has a POST form (e.g. a panel endpoint), add a **loopback GET form** that carries
the parameters in the path, e.g. `GET /feed/<A|B>/quality/<tier>`, and gate it to
loopback/tailnet (NOT the `/console` mount) so it never leaves the tailnet. Companion runs on
the same box, so loopback is exactly right.

## 1. Author the button(s) in the config JSON

Structure: `pages[P].controls[ROW][COL] = button`; grid is 8 cols (0–7) × 4 rows (0–3). Clone
a known-good GET button (e.g. page 1 "Feed A Reload") and change `style.text` + the action
`options.url.value`. The generic-http connection id is `label:"http"` in `d["instances"]`
(find it, don't hardcode across machines). A minimal GET action:

```json
{"id": "<21-char nanoid>", "definitionId": "get", "connectionId": "<http-conn-id>",
 "options": {"url": {"value": "http://127.0.0.1:8088/feed/A/quality/robust", "isExpression": false},
             "header": {"isExpression": false, "value": ""},
             "jsonResultDataVariable": {"isExpression": false},
             "result_stringify": {"isExpression": false, "value": true},
             "statusCodeVariable": {"isExpression": false}}, "upgradeIndex": 1, "type": "action"}
```

**CRITICAL — preserve the file's formatting or the diff explodes.** The file is **1-space
indent** (`json.dumps(d, indent=1)`), keeps its trailing newline, and json.load preserves key
order. Re-dumping with tabs reformats all 5000+ lines. Always: read raw → `json.loads` →
insert your page/button → `json.dumps(d, indent=1)` (+ trailing "\n" if the original had one).
Then `git diff --stat` must show **insertions only** (~60 lines/button). Generate unique ids
with `secrets` (21 URL-safe chars). Keep `style.size` ≤ **14** for two-word labels like
"FEED A\nROBUST" — size 18 clips the last character ("ROBUST"→"ROBUS") in a tile.

Verify: `json.load` the result, then `racecast --profile <p> export companion` and confirm the
exported file lists your new page/buttons.

## 2. Import into a running Companion (Playwright Python)

Companion binds to the **Tailscale IP** from its `config.json` (`bind_ip`), e.g.
`http://100.x.y.z:8000` — **not** localhost. Launch it with `open -a Companion` and poll that
address. Use a **persistent** context (`launch_persistent_context(user_data_dir=…)`) so the
"What's New" modal stays dismissed. The working recipe:

1. `goto <UI>/import-export`; if a "What's New in Companion" modal is present, press **Escape**
   a few times (it's a full-screen modal over the page).
2. The **"Import configuration"** control is a `<label>` wrapping a **hidden `<input type=file>`**
   — it is NOT a `<button>`, so text/role locators miss it. Set the file **directly on the input**:
   `pg.locator("input[type=file]").first.set_input_files(EXPORT)` (no chooser dance).
3. The import wizard opens on the **Full Import** tab. Click the green
   **"Import Preserving Unselected"** button. This imports all button pages (including new
   ones) while **resetting only the selected components** (Buttons/Surfaces) and **preserving
   the rest** (Settings) and **linking to existing connections** ("Link to …" is the default —
   leave it). This is simpler and more robust than the Buttons-tab page-mapping.
4. The page is live instantly. Verify: `goto <UI>/buttons` and read the Pages panel's name
   inputs — your new page (e.g. "FEED QUALITY") must appear.

## 3. Screenshot the page (for the wiki)

Use **companion-screenshots** for the crop geometry. In short: `goto <UI>/tablet?pages=<N>`,
viewport 1280×720 — the tiles render as **bitmaps** (element `innerText` is empty; that is
normal, the buttons ARE there), then
`ffmpeg -i vp.png -vf "crop=1280:632:0:54" src/docs/wiki/images/companion-page<N>-<slug>.png`
(1280×632, 4 rows). Read the PNG back and confirm the labels are complete (no clipping).

## 4. Validate the button actually fires (do NOT skip)

Rendering ≠ working. Start the relay the button targets (the demo relay as in
[wiki-screenshots](../wiki-screenshots/SKILL.md) B1, with its stub `--cookies`), reset the
target state, click the tile, and read the relay back:

```
pg.locator(".button-control.clickable").nth(0).click()     # tile index = row*8 + col
```

then `curl <relay>/status` and assert the state changed (e.g. `feeds.A.profile == "robust"`).
A POST-body button passes rendering but fails here — that is the tell to use a GET route (see top).

## Cleanup
`racecast relay stop`; `pkill -f obs-sim.py`; `rm -f runtime/demo/stub-cookies.txt` (the shared jar stays);
`git checkout -- profiles/<demo>/profile.env` (relay start writes CONSOLE_SECRET). Ask the
operator before leaving/closing their Companion — you launched it.

## Gotchas (all hit live)
- POST generic-http action with a JSON body → silently empty body → 400. Use a **GET** route.
- Tab-reindent reformats the whole 126k file. **1-space indent, insertions-only diff.**
- "Import configuration" is a label+hidden file input → `set_input_files`, not a button click.
- "What's New" modal re-appears per fresh browser context → persistent context + Escape.
- `/tablet?pages=N` tiles are bitmaps → judge by the screenshot, not element text.
- Companion listens on the **Tailscale IP**, not localhost.
