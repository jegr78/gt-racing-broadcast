---
name: wiki-screenshots
description: Regenerate the wiki/slide screenshots of the relay- and UI-served pages — the Control Center (cc-*.png), the /console pages (launcher, cockpit, race-control, director panel) and the commentator cockpit. Use when a Control Center view, a /console page, the Director Panel, or the Commentator Cockpit changed and its image under src/docs/wiki/images/ (and src/docs/slides/assets/img/) is stale. Covers HOW to populate the pages with reproducible fake content via the demo profile + the obs-sim OBS stand-in (no real OBS, no real league). NOT for Companion buttons — see companion-screenshots.
---

# Wiki / slide screenshots (Control Center + /console pages)

Regenerate the screenshots of the **relay-served** and **Control-Center-served** pages for
the GT Racing Broadcast wiki and onboarding slides. This skill's real value is the
**reproducible fake-content recipe**: how to make the pages show a believable broadcast
(program monitor, schedule, on-air tally, chat) **without** a real OBS, real cookies, or a
real league — using the shipped **`demo`** profile and the **`tools/obs-sim.py`** OBS
stand-in.

Sibling skills: [companion-screenshots](../companion-screenshots/SKILL.md) for the Companion
button board (different running service — not covered here), and
[wiki-visual-test](../wiki-visual-test/SKILL.md) to verify the published result renders.

## Scope — which image goes with which surface

Every visible change to one of these surfaces makes its image stale. Capture from a
**local dev build** (run from `src/`, no `VERSION` file) so the version badge stays the
uniform "dev build" across all shots.

| Surface | URL (dev build) | Image file(s) |
|---|---|---|
| Control Center views | `racecast ui` → `/#<view>` | `src/docs/wiki/images/cc-<view>.png` (home, relay, streams, logs, profile, overlay-builder, crew-console, crew-editor, settings, setup, tools, apps, preflight, help) |
| Director Panel | `/panel` or `/console/panel` | `director-panel.png` |
| Commentator Cockpit | `/cockpit?t=<token>` | `console-cockpit.png` |
| Crew Console launcher | `/console?t=<token>` | `console-landing.png` |
| Discord login page | `/console/login` | `console-login.png` |
| Race Control desk | `/console/race-control?t=<token>` | `console-race-control.png` |

Wiki screenshots live in **`src/docs/wiki/images/<name>.png`**. The onboarding slides reuse
the **same images** in **`src/docs/slides/assets/img/<name>.png`** — when a shot is used by a
slide deck, write the **identical** file to both paths. Only refresh the surface(s) you
changed.

> The CLAUDE.md "refresh the screenshot in the same change" hard rule is blocking for the
> **Control Center**, **Director Panel** and **Companion** surfaces. The commentator **Cockpit**
> and **Race Control** pages are not covered by that rule — refresh them as good practice,
> not as a release blocker.

## Prerequisites

- **Playwright MCP** available (the `mcp__plugin_playwright_playwright__*` tools) — element
  and full-page screenshots are taken through it.
- Working directory: the repo root. Run everything from `src/` (`python3 src/racecast.py …`)
  so the version badge reads **dev build**.
- For the relay-served pages (`/console/*`, `/cockpit`, `/panel`, `/hud/preview`): the
  **`demo`** profile and `tools/obs-sim.py` (both shipped/maintainer-side). No real OBS,
  Tailscale, cookies, or Sheet write access needed.

---

## Part A — Control Center views (`cc-*.png`)

These are the local web UI; most need only the UI server, but views that embed relay data
(e.g. **Crew Console**, **Relay**, **Setup**) also want a running demo relay (Part B) so the
cards show live content instead of "relay offline".

1. Start the dev-build Control Center on a **free** port (the real instance often owns 8089;
   `ui` on a taken port opens *that* instance, not the dev build):
   ```bash
   python3 src/racecast.py profile use demo
   RACECAST_UI_PORT=8090 python3 src/racecast.py ui --no-browser   # pick any free port
   ```
2. Drive it with the Playwright MCP: `browser_navigate` → `http://127.0.0.1:8090/`, switch to
   the view, then **element-screenshot the card/modal** (not a full-window grab) so the
   framing matches the existing images — e.g. the overlay builder modal:
   `browser_take_screenshot` with `element` ref for `#ov-modal .ovmodal-card`.
3. Save into `src/docs/wiki/images/cc-<view>.png` (and the slides copy if the deck uses it).
4. Stop the UI: `pkill -f "racecast.py ui"`.

---

## Part B — relay-served pages + the fake-content recipe (the important part)

`/console/*`, `/cockpit`, `/panel` and `/hud/preview` are served by the **relay**. To make
them show a believable broadcast we run the relay on the **demo** profile (its public,
read-only Sheet supplies a real schedule + HUD values) and point it at **obs-sim** (a fake
OBS that returns a fixed program still). This is fully reproducible and touches nothing real.

### B1. Boot a demo relay against the OBS stand-in

```bash
# 1) Pull the demo graphics (gives us a real-looking still to use as the program image)
python3 src/racecast.py --profile demo graphics      # -> runtime/demo/graphics/*.png

# 2) The relay refuses to boot without a cookie file; a stub is enough. Keep it in the
#    demo runtime dir, NEVER at the shared runtime/yt-cookies.txt (see the warning below)
mkdir -p runtime/demo && printf '# Netscape HTTP Cookie File\n' > runtime/demo/stub-cookies.txt

# 3) Start obs-sim serving a fixed program still (any demo graphic works)
python3 tools/obs-sim.py --image runtime/demo/graphics/Standby.png --port 4466 &

# 4) Start the relay pointed at obs-sim so /cockpit/program + the panel monitor render,
#    and at the stub jar so it never reads or rewrites the real one
RACECAST_OBS_WS_HOST=127.0.0.1 RACECAST_OBS_WS_PORT=4466 \
  python3 src/racecast.py --profile demo relay start --cookies "$PWD/runtime/demo/stub-cookies.txt"
```

> ⚠️ **The shared jar `runtime/yt-cookies.txt` is the real YouTube login** on a machine that
> also produces broadcasts. Never write a stub there and never clean it up. The CLI always
> passes it to the relay as `--cookies`, and yt-dlp saves its cookie jar back to that file
> on every resolve, so the demo relay must get its own `--cookies` on **every** `relay
> start` / `relay restart`, or it runs on the real login and rewrites it. Checked by
> `tests/test_skill_recipes.py` (#617).

`tools/obs-sim.py` speaks just enough obs-websocket v5 (no-auth handshake +
`GetCurrentProgramScene` + `GetSourceScreenshot`) to answer the relay's
`get_program_screenshot` with that fixed image — so the program monitor in the cockpit /
panel / race-control pages shows the still you passed, with **no real OBS running**.

### B2. Mint a console token (cockpit / launcher / race-control need auth)

The `/console/*` and `/cockpit` pages are token-gated. Starting the demo relay
auto-provisions a `CONSOLE_SECRET` (see the gotcha below); mint a token for a **real
schedule streamer** so the on-air tally lights up:

```bash
python3 - <<'PY'
import importlib.util, pathlib
ca_path = pathlib.Path("src/scripts/console_auth.py")
spec = importlib.util.spec_from_file_location("console_auth", ca_path)
ca = importlib.util.module_from_spec(spec); spec.loader.exec_module(ca)
# read the freshly-provisioned secret from the demo profile
secret = ""
for ln in pathlib.Path("profiles/demo/profile.env").read_text().splitlines():
    if ln.startswith("CONSOLE_SECRET="):
        secret = ln.split("=", 1)[1].strip()
name = "PICK_A_STINT1_STREAMER"          # from the demo Schedule tab; stint-1 => "ON AIR"
key = ca.streamer_key(name)              # canonical key fn (NOT asset_key)
print(ca.mint_token(secret, key))        # -> <streamer_key>.<version>.<sig>
PY
```
Get a real streamer name from `http://127.0.0.1:8088/schedule/data`; a **stint-1** streamer
yields a "YOU ARE ON AIR" tally. For the **Race Control** page mint a token for a crew member
flagged Race Control (the demo Crew tab "RC 2" has Race Control = TRUE).

### B3. (Optional) seed chat for a fuller shot

- **Crew chat:** `POST` a few lines so the chat card isn't empty. The endpoint wants a
  **JSON body** with a **`user`** key (NOT a form field, NOT `name=`); the author shows in
  the chat exactly as `user`:
  ```bash
  curl -s -X POST "http://127.0.0.1:8088/chat/send" -H "Content-Type: application/json" \
       -d '{"user":"Director","text":"Welcome to the demo broadcast"}' >/dev/null
  ```
  (A form-encoded `name=…` is ignored and the message is stored as the generic "Crew".)
- **Broadcast chat (read-only mirror):** there is **no write endpoint** — the store is
  in-memory and read-only. The demo `Channel` tab is `@LofiGirl` (always live), so a live
  relay would fill the card with **real third-party viewers** — off-theme and inappropriate to
  commit. To seed fictional race-themed messages instead, add a **temporary** env-guarded
  block in `src/relay/racecast-feeds.py` right after `broadcast_chat_store = BroadcastChatStore()`
  that, under `RACECAST_SEED_BROADCAST=1`, sets `channel_source = None` (keeps the live reader
  OFF) and seeds the store:
  ```python
  if os.environ.get("RACECAST_SEED_BROADCAST") == "1":
      channel_source = None
      broadcast_chat_store = BroadcastChatStore()
      import time as _t; now = _t.time()
      broadcast_chat_store.add_many("yt-DEMO0001", [
          {"id": "d1", "ts": now-180, "user": "SimRacerTom",  "text": "What a move into T1!"},
          {"id": "d2", "ts": now-120, "user": "GTfan_Mia",    "text": "P3 closing fast"},
          {"id": "d3", "ts": now-60,  "user": "PitWallPete",  "text": "Box this lap?"},
          {"id": "d4", "ts": now-20,  "user": "Lena_K",       "text": "Great commentary!"},
      ])
  ```
  Run the relay with `RACECAST_SEED_BROADCAST=1`, capture, then **revert the seed block
  only** — delete exactly the lines you added (surgically, with an editor/`Edit`), NOT with
  a whole-file `git checkout`. (`add_many` takes `{id, ts, user, text}` dicts; ts as
  `time.time() - N`.)

  > ⚠️ **Never `git checkout -- src/relay/racecast-feeds.py` to drop this block.** A
  > whole-file checkout discards **every** uncommitted change in that file — if you are
  > also editing the relay (the common case: you changed a relay surface and are now
  > refreshing its screenshot), it silently destroys that work. Branch + commit before you
  > start so any slip is recoverable, and remove the temporary block with a targeted edit.

### B4. Capture

Load each page in the Playwright MCP and **full-page** screenshot:
- Cockpit:  `http://127.0.0.1:8088/cockpit?t=<token>`        → `console-cockpit.png`
- Launcher: `http://127.0.0.1:8088/console?t=<token>`        → `console-landing.png`
- Race Ctrl:`http://127.0.0.1:8088/console/race-control?t=<token>` → `console-race-control.png`
- Login:    `http://127.0.0.1:8088/console/login`            → `console-login.png`
- Panel:    `http://127.0.0.1:8088/panel` (or `/console/panel?t=<token>`) → `director-panel.png`

Write each to `src/docs/wiki/images/<name>.png` and, if a slide deck uses it, the identical
copy to `src/docs/slides/assets/img/<name>.png`. Read the PNG back and eyeball it.

> The HTML pages (`cockpit.html`, `director-panel.html`, `race-control.html`,
> `console.html`, `hud.html`) are read **per request** from disk — a browser reload picks up
> an edit without restarting the relay. A change to relay **Python**, though, needs a
> `relay stop` and the full B1 step 4 again (obs-sim env **and** the stub `--cookies`).

---

## Cleanup & revert (do not skip — these touch git-tracked / shared state)

```bash
RACECAST_OBS_WS_HOST=127.0.0.1 RACECAST_OBS_WS_PORT=4466 python3 src/racecast.py relay stop  # sim env, or stop talks to a real OBS on 4455
pkill -f "obs-sim.py" ; pkill -f "racecast.py ui"
rm -f runtime/demo/stub-cookies.txt                # the stub jar only, never the shared one
# Seed block (B3): delete ONLY the lines you added — surgically, with an editor/Edit.
# Do NOT `git checkout -- src/relay/racecast-feeds.py`: it wipes ALL uncommitted changes
# in that file, including any relay edit you are screenshotting (see the ⚠️ in B3).
git checkout -- profiles/demo/profile.env          # CONSOLE_SECRET gotcha (see below)
```
(`git checkout -- profiles/demo/profile.env` is safe — that file is config you never edit
here; the relay only injects a secret into it. The relay **source** file is the one to
revert surgically.)

- **`profiles/demo/profile.env` is git-tracked and must ship secret-free.** Starting the demo
  relay makes `_ensure_active_cockpit_secret` write a real `CONSOLE_SECRET` into it;
  `tests/test_config.py::t_shipped_demo_profile_env_is_complete_and_secret_free` then fails and
  the secret would be committed. **Always** `git checkout -- profiles/demo/profile.env` before
  committing.
- The scratch `cc-*`/`console-*` viewport PNGs the MCP may drop in the repo root are not wiki
  content — `rm` them; only the files under `src/docs/...` are committed.

## Commit & publish

```bash
git add src/docs/wiki/images/<name>.png src/docs/slides/assets/img/<name>.png
git commit -m "docs: refresh <surface> screenshot"
```
Publish the wiki only on the user's go-ahead: `python3 tools/sync-wiki.py` (preview with
`--dry-run`), then verify the render with [wiki-visual-test](../wiki-visual-test/SKILL.md).

## Notes

- **Why obs-sim, not real OBS:** the producer's real OBS may not be running, and even if it
  is, its program is whatever they happen to have on screen — not reproducible. obs-sim pins a
  fixed program still so the same shot regenerates byte-stably on any machine (incl. Windows).
- **Element vs full-page:** Control Center cards/modals → **element** screenshot (match the
  existing tight framing). The standalone `/console`/`/cockpit`/`/panel` pages → **full-page**.
- **Always the dev build** for Control Center (`cc-*`) shots so the version badge is uniform;
  a real version baked into one shot goes stale at the next release.
