# OBS Setup

> Technical reference. The quick version is in [Set up the broadcast PC](Set-up-the-broadcast-PC).

Import the scene collection, confirm the graphics, and wire up Discord audio. Do
[Configuration](Configuration) first so `setup-assets.py` has produced the importable
collection.

## 1. Import the scene collection

1. OBS → **Scene Collection → Import** → select the active profile's
   `runtime/<profile>/GT_Racing_Endurance.import.json` (in the package: `obs/GT_Racing_Endurance.import.json`).
2. Switch to the league's collection (named after the profile's `OBS_COLLECTION`, the
   convention is **GT Racing Endurance: <league>**). Once imported, you can switch the
   active league's collection from the CLI with `racecast obs collection set`, it
   rebuilds every source, so it is always an explicit producer action, never automatic.
3. **Images there?** Overlay / Standings / … should be visible. If not, `setup-assets.py`
   didn't run for this machine: re-run it and re-import (see [Configuration](Configuration)).

> Import the `.import.json`, **not** the `.template.json`. Never move the folder after
> import: OBS stores absolute image paths.

> **Renamed in the rebrand (#308):** the endurance scene collection is now
> **"GT Racing Endurance, <league>"** (was "GT Endurance Racing, <league>").
> Existing installs: run `racecast setup` once to regenerate the import file,
> import it into OBS, then delete the old "GT Endurance Racing: <league>"
> collection. There is no automatic rename.

## 2. The scenes

- **Stint**, the active feed full-screen + HUD overlay (POV PiP lives only here). It also
  holds a hidden **Standby Cover** (the dedicated `Standby Cover.png` graphic, a neutral
  cover, distinct from the Standby scene's `Standby.png` thumbnail) **below the `Stint HUD` group**,
  so showing it hides the feeds and the POV PiP while the Race Control banner and timer stay
  on top. The director toggles it with the Companion **Standby Toggle** button (a
  *Set Source Visibility* toggle on `Stint / Standby Cover`, with a *Source Visible*
  feedback so it lights while active). Re-add the source after a rebuild with
  `python3 tools/add_standby_cover.py src/obs/GT_Racing_Endurance.json`.
- **Splitscreen**, two feeds side by side, for the ~10-minute handover. Its `Split HUD`
  group adds **CURRENT/NEXT** labels above the on-air and waiting feed (the on-air feed reads
  CURRENT); these are relay-driven (`/splitscreen`).
- **Interview**: interview graphic + Discord audio.
- **Standby / BRB**: for breaks.
- **Intro** / **Outro** / **Trailer**: full-screen stream-open, stream-close, and
  standalone-trailer clips that **loop with audio**, played from local files
  (`runtime/media/intro.mp4` / `outro.mp4` / `trailer.mp4`). The director switches to
  them with the Companion **INTRO** / **OUTRO** / **TRAILER** buttons. The file paths are
  tokenised as `__RACECAST_MEDIA__` in the collection and resolved by `setup-assets.py`; download
  or refresh the clips from the Sheet **Assets** tab with `racecast media`
  (see [Configuration](Configuration)). If the clips are missing the scene shows black.
- **Intermission**, three sources in one scene: a full-screen league background graphic
  (`runtime/<profile>/graphics/Intermission.png`, Sheet label **`Intermission`**, downloaded
  by `racecast graphics`); a **looping music** track (`runtime/<profile>/media/intermission.mp3`,
  Sheet label **`Intermission Music`**, downloaded by `racecast media`, a synthetic
  ambient-loop placeholder plays if the file is absent); and a read-only **broadcast-chat
  panel** (a Browser Source pointed at `http://127.0.0.1:8088/intermission`, served by the
  relay: it mirrors the public YouTube/Twitch broadcast chat and auto-scrolls; the relay
  must be running for it to render). The director cuts to this scene with the Companion
  **INTERMISSION** button or the panel's **INTERMISSION** macro; when to use it is up to
  the team. The per-league overlay file `profiles/<name>/overlay/intermission.css` overrides
  the chat panel's appearance (see [HUD overlays](HUD-Overlays)).

> **Broadcast graphics are local files.** The still-graphics image sources. Overlay,
> Standings, Schedule, Race Results, Quali Results, Standby, Standby Cover, the three
> **weather** overlays (**Race Weather 1**, **Race Weather 2**, **Quali Weather**), and the five
> optional **flag-status graphics** (**Flag Green**, **Flag Yellow**, **Flag Red**,
> **Flag Safety Car**, **Flag Virtual Safety Car**): read from
> `runtime/graphics/<Label>.png`. They are tokenised `__RACECAST_GRAPHICS__` in the collection
> and resolved by `setup-assets.py`. Download them from the Sheet **Assets** tab with
> `racecast graphics` (one PNG per Assets row, the Sheet label is the
> filename); a source whose file is missing shows black until you fetch it. The three
> weather graphics are **hidden full-screen overlays in the Stint scene**, each switchable
> by its own Companion toggle (`Weather Race (1) Toggle` / `Weather Race (2) Toggle` / `Weather Quali Toggle`: see
> [Director guide](Director)), exactly like the Standings/Results toggles. The five flag-status
> graphics are **hidden full-screen overlays in the Stint and Splitscreen scenes**, toggled
> mutually exclusively from the panel's **Flag Gfx** row or the Companion **FLAGS** page's
> graphic row: they are the *graphic* parallel to the flag-text chip and are fully optional.

## 3. Media Sources (the feeds)

Each feed is a **Media Source** pointed at a fixed local port: these come with the
collection and never change URL:

| Source | Input |
|--------|-------|
| Feed A | `http://127.0.0.1:53001` |
| Feed B | `http://127.0.0.1:53002` |
| POV    | `http://127.0.0.1:53003` |

If you ever recreate one: uncheck **Local File**, set the input URL, tick **Use hardware
decoding**, set **Network Buffering** to `8`–`16` MB (stacks on top of Streamlink's
buffer) and **Reconnect Delay** to `10` s.

The ports are served by the [Relay](Relay-Mode) (recommended) or by
[Static Mode](Static-Mode).

## 4. HUD &amp; graphics (Browser Sources)

The HUD (with the race timer drawn into it) and the info-graphics are **Browser Sources**
driven live by the relay and the shared Google Sheet. They are left in the scene and
toggled by the director via Companion: no screen-share, no extra latency. The relay-served
overlay sources (`/hud`, `/splitscreen`) use fixed loopback URLs, no per-machine editing
needed.

> **ARM64 Linux: no Browser Source out of the box.** Ubuntu's `obs-studio` package is
> built **without** the Browser Source (no CEF), and there is no prebuilt OBS-with-browser
> for `aarch64` anywhere (the OBS PPA is amd64-only, Flathub has no aarch64 build, no arm64
> snap). If OBS shows **no "Browser" source type**, run **`racecast obs-browser`** once, it
> builds and installs the plugin from source against your distro's OBS (downloads ~340 MB of
> CEF and compiles for several minutes). On a host without a GPU (a VM / headless / no DRM
> render node) also disable **OBS → Settings → Advanced → Browser Source Hardware
> Acceleration**, or CEF's GPU subprocess crashes. x86-64 Linux gets the Browser Source from
> the OBS PPA via `racecast install-apps`, so this step is ARM64-only.

> The HUD and graphics pull from **shared** production resources (the sheet): changes
> affect everyone. The sheet must stay shared.

### The lower-third HUD: one relay-served overlay

The lower-third HUD (streamer, session, round, flag, top-3 teams, race control) is a
**single** Browser Source named **HUD Overlay** pointing at the relay:
`http://127.0.0.1:8088/hud`.

- **The relay must be running** for the HUD to render (it serves `/hud`). See
  [Relay Mode](Relay-Mode).
- **Content comes from the shared sheet**, no manual reloads: the page polls
  the relay every ~2.5 s, and the relay refreshes the sheet every `--hud-poll` seconds
  (default 5). Live values come from the **Overlay** tab; the **Configuration** tab maps
  each team to its manufacturer via a **`Brand Name`** text column (header may also be
  `Brand Key` or `Brand`; the image columns `Brand Logo`/`Brands` are ignored).
- **Flags and brand logos** are bundled assets in `src/assets/flags/` and
  `src/assets/brands/`, resolved by text: the Country text → `flags/<country>.svg`, and a
  team's `Brand Name` → `brands/<key>.png|svg` (lowercase, spaces → `-`). Add a flag/logo by
  dropping a matching `.svg` in those folders.
- Relay flags: `--no-hud` disables it; `--overlay-tab` / `--config-tab` override tab names;
  `--hud-poll` sets the refresh interval.

### The race timer: relay-served countdown

The race-timer clock is **rendered inside the HUD** (`http://127.0.0.1:8088/hud`): there is
no separate timer browser source and no `/timer` page to add. The relay exposes the timer
**state** as JSON at `/timer/data` (the HUD polls it) and the director **controls** under
`/timer/*`. Director controls are on the panel's Race Timer section and Companion page 2.
See [Race-Timer](Race-Timer) for how it works and the optional Apps Script
write-webhook setup.

## 5. Discord audio (interviews)

The source **Discord Audio Capture** comes with the collection. `racecast setup` realizes it
for the importing OS: no manual source-switching needed.

- **macOS:** `App Audio Capture` (ScreenCaptureKit), bound to the Discord app. Grant OBS
  **Screen &amp; System Audio Recording** permission once (System Settings → Privacy &amp;
  Security). Keep Discord **windowed** (not fullscreen): otherwise it is not captured.
- **Windows:** `Application Audio Capture`, bound to `Discord.exe`. Any Discord window
  title works: window titles don't matter. Don't *also* capture Discord via desktop
  audio, or you'll double it.
- **Linux (any architecture):** the capture uses the
  [PipeWire Audio Capture plugin](https://obsproject.com/forum/resources/pipewire-audio-capture.1458/),
  which is **not** part of OBS core, it must be present before importing the collection.
  Install it the way your distribution / OBS build expects (packaging varies per distro);
  the upstream method is to extract the latest `linux-pipewire-audio-*.tar.gz` from the
  plugin's releases into `~/.config/obs-studio/plugins/` and restart OBS. Sandboxed OBS
  builds (Flatpak / Snap) need extra steps for the plugin to reach PipeWire. This applies
  whether Discord runs natively (amd64) or via the browser (the no-native variant below).
  `racecast setup` then sets the capture target automatically.
- **Linux without native Discord (e.g. ARM64):** the official Discord `.deb` is amd64-only,
  so there is no native client to capture. `racecast setup` detects this and points the
  **Discord Audio Capture** source at the browser instead, it stays a PipeWire Application
  Capture source (so the panel/Companion mute &amp; volume controls are unchanged), only its
  target becomes the browser. Open **Discord-web** (<https://discord.com/app>) in that
  browser, join the **Interviews** voice channel before race end, and keep the tab playing.
  Override the auto-detection with `RACECAST_DISCORD_WEB` (`1`/`0`) and the captured
  browser with `RACECAST_DISCORD_WEB_BROWSER` (e.g. `Chromium`) in `.env`. The PipeWire
  capture grabs *all* audio from that browser, so use a browser/profile dedicated to the
  interview if other tabs make sound. As always, confirm the level meter moves in OBS's
  Audio Mixer before going live.
- **Switched production machine or OS?** Re-run `racecast setup` and re-import the collection.

## 6. Stream key

Enter **the league's YouTube channel** stream key in OBS only at event time
(**Settings → Stream**).

Next: [Companion](Companion), then [Relay Mode](Relay-Mode).
