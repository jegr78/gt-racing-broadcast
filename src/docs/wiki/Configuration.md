# Configuration

> Technical reference. The setup steps are in [Set up the broadcast PC](Set-up-the-broadcast-PC).

Config splits in two and is **never** hardcoded or committed:

- **League config**, the **Google Sheet ID** (schedule + HUD data), the optional
  **sheet-write webhook URL**, and the league's intro/outro/logo. This lives **per league**
  in `profiles/<name>/profile.env`, not in `.env`. One machine can hold several leagues.
- **Machine config**, a handful of optional, machine-only switches (OBS-WebSocket
  password, Control Center port, the Windows Companion path). This lives in a gitignored
  `.env` at the repo (or package) root.

Then `setup-assets.py` localizes the OBS collection for this machine using the **active**
profile's values.

## League profiles (`profiles/<name>/profile.env`)

Each league is a folder under `profiles/` with a `profile.env`. `profiles/example/` is the
template: copy it (or use `racecast profile new`) and fill in your values. For the wider
profile model (machine vs. league config, the active-profile rules, adding a second league),
see [League profiles](Profiles); for per-league overlay styling see [HUD overlays](HUD-Overlays).

```bash
racecast profile new myleague --from example   # copies profiles/example/ -> profiles/myleague/
racecast profile use myleague                  # make it the active league
racecast profile list                          # which leagues exist (★ = active)
racecast profile show                          # the active league's resolved config
```

`profiles/<name>/profile.env` uses **un-prefixed** keys (the file *is* the league: real
environment variables and the machine `.env` do **not** override these):

```ini
# profiles/myleague/profile.env  (gitignored values you fill in)
NAME=My League
SHEET_ID=your_google_sheet_id_here
SHEET_PUSH_URL=https://script.google.com/macros/s/…/exec?key=your_secret
INTRO_URL=
OUTRO_URL=
TRAILER_URL=
LOGO=
OBS_COLLECTION=
# optional Discord integration
DISCORD_CLIENT_ID=
DISCORD_CLIENT_SECRET=
DISCORD_WEBHOOK_URL=
```

- **`NAME`**: display name shown in the CLI / Control Center / docs (not the HUD).
- **`SHEET_ID`**, the long ID from your HUD/schedule sheet URL:
  `https://docs.google.com/spreadsheets/d/`**`<THIS>`**`/edit`. Drives the relay:
  the schedule, the POV tab, and the HUD overlay (Overlay + Configuration tabs, served
  at `/hud`). The full tab/column layout the relay expects is in
  [Sheet template](Sheet-Template). Once set, reopen the sheet anytime with
  **Open Sheet ↗** (Profile view) or `racecast sheet open`, both rebuild the link from
  this `SHEET_ID`.
- **`SHEET_PUSH_URL`** *(optional)*, the Apps Script write webhook shared by the
  relay-hosted race timer **and** the director panel's sheet controls. The race timer uses
  it to sync start/stop/show/hide/correct actions to the Sheet's `Timer` tab (so a second
  producer machine takes over with the same countdown). The panel's **HUD row**
  (Stint label / Streamer / Session / Race Control) and **URLs section** (Schedule + POV URL)
  use it to write changes back to the sheet: without it those panel controls are read-only.
  Unset = timer works on this machine only (no sheet sync); panel sheet controls become read-only. See [Sheet-Webhook](Sheet-Webhook)
  for setup.
- **`INTRO_URL` / `OUTRO_URL` / `TRAILER_URL`** *(optional)*: override the
  Intro/Outro/Trailer clip URLs that normally come from the Sheet **Assets** tab
  (used by `racecast media`).
- **`LOGO`** *(optional)*, a logo image (relative to the profile dir) for the Control Center.
- **`OBS_COLLECTION`** *(optional)*, the OBS scene-collection name this league uses, so
  several leagues can keep separate collections in OBS on one machine. `racecast setup`
  writes this name into the import JSON; blank = the per-league convention
  `GT Racing Endurance: <league>`.
- **`CONSOLE_SECRET`** *(auto-managed, do not set by hand)*, the per-league HMAC secret
  that signs the `/console` identity tokens (commentator/director/producer) and acts as the
  step-up secret for irreversible producer ops. It is **generated automatically** on the
  first `racecast relay start` / `event start` and travels with `racecast profile export`/
  import, so every producer of a league shares one secret. Keep it gitignored like the rest
  of `profile.env`; if it leaks, rotate it (delete the line and restart to regenerate, then
  re-export to the other producers). See [Remote access](Remote-access#one-identity-every-role).
- **`DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET`** *(optional)*, the per-league Discord
  **OAuth app** credentials. When both are set, the relay activates `/console/login` +
  `/console/oauth/callback`, so crew can sign in to the [Console](Console) with Discord
  (matched against the Crew tab's `Discord` column). When absent, OAuth is off and the
  signed `racecast links` are the only entry path. Setup: [Console & cockpit setup](Console-Setup).
- **`DISCORD_WEBHOOK_URL`** *(optional)*, a Discord **channel webhook** (not the OAuth app).
  When set, the relay posts commentator stream-link submissions and health alerts to that
  channel; when absent those notifications are simply no-ops. Setup:
  [Console & cockpit setup](Console-Setup).

**Which profile is active** (resolution order): a global `--profile <name>` flag wins;
then the machine `RACECAST_PROFILE` (or `.env`) value; then the `runtime/active-profile`
pointer (set by `racecast profile use`); and if you keep exactly one profile, it is
selected implicitly. Run one command against a non-active league with
`racecast --profile <name> <command>`.

## The machine `.env` file

At the repository (or package) root there is a tracked template, `.env.example`. Copy
it and fill in only what you need, all keys are optional:

```bash
cp .env.example .env
```

```ini
# .env  (gitignored: never commit this; league config is NOT here)
RACECAST_OBS_WS_PASSWORD=
RACECAST_COMPANION_EXE=
RACECAST_UI_PORT=
RACECAST_PROFILE=
```

- **`RACECAST_OBS_WS_PASSWORD`** *(optional)*. OBS-WebSocket password for the feed-port
  release on `racecast … stop`. Normally **not** needed: auto-read from OBS's own
  obs-websocket config; set it only for portable / non-standard OBS installs.
- **`RACECAST_COMPANION_EXE`** *(optional, Windows)*: full path to `Companion.exe` for
  `racecast companion start/stop`. Only needed when Companion sits in a non-standard
  location; the standard install paths are found automatically, e.g. the
  winget / `racecast install-apps` default:
  `RACECAST_COMPANION_EXE=C:\Program Files\Companion\Companion.exe`
- **`RACECAST_UI_PORT`** *(optional)*: port of the local Control Center web app
  (`racecast ui`); set only when another app already occupies the default `8089`.
- **`RACECAST_PROFILE`** *(optional)*, the default active league when neither `--profile`
  nor the `runtime/active-profile` pointer applies. Leave unset if you keep one profile.
- **`RACECAST_AUTO_FAILOVER`** *(optional, default off)*: when set to `1`, the relay
  automatically switches OBS to the **Intermission** scene if the **on-air** feed stays
  down past the red grace window, so viewers see a clean holding card instead of a frozen
  or black frame. It fires **once** and pings Discord (`@here`); the **return is manual**,
  the producer/director re-takes the feed when it recovers. It only fires while OBS is still
  on the on-air feed scene (it never yanks a program you already cut to Intermission/Intro).
- **`RACECAST_CAPTURE`**, **`RACECAST_CAPTURE_AUDIO`**, **`RACECAST_MIC`** *(optional)*,
  the capture card, its game-audio override and the commentary microphone of this
  machine. Solo profiles use the capture card and mic for their OBS device sources; on an
  endurance machine all three drive a
  [local capture stint](Relay-Mode#local-capture-stint). `racecast device-scan` writes
  the capture card and the mic.
- **`RACECAST_MIC_NAME`** *(optional)*: the microphone's device name, written together
  with `RACECAST_MIC` by `racecast device-scan --mic` and the Control Center. When the
  operating system gives the same microphone a new id (common for USB mics without a
  serial number on Windows), the relay uses the name to find it again
  ([details](Relay-Mode#the-commentary-microphone)).
- **`RACECAST_UI_PASSWORD`** *(reserved)*: for the future Control-Center-over-Tailscale
  feature; not read by any current version, leave commented out.

Real environment variables take precedence over `.env`. The loader only reads a `.env`
from the script directory or the project root (marked by `.git` / `.env.example`),
never an unrelated parent directory.

> **Security:** `.env` and `profiles/<name>/profile.env` are gitignored and must stay
> that way. A profile's `SHEET_PUSH_URL` contains a shared secret: if it ever leaks,
> redeploy the Apps Script with a new key and update the URL in that league's
> `profile.env` on every producer machine.

## Localize the OBS collection (`setup-assets.py`)

The OBS scene collection in git is deliberately **path- and secret-free**: it stores
tokens instead of real paths and URLs. `setup-assets.py` injects the real values for the
**active** profile and writes an importable collection (per-league, under
`runtime/<profile>/`, named after the profile's `OBS_COLLECTION`):

```bash
racecast setup --out runtime/GT_Racing_Endurance.import.json
```

The tokens in the collection:

| Token | Resolves to |
|-------|-------------|
| `__RACECAST_GRAPHICS__` | the active profile's `runtime/<profile>/graphics/` (package: `graphics/`), the Sheet-driven broadcast graphics, `__RACECAST_GRAPHICS__/<Label>.png` |
| `__RACECAST_MEDIA__` | the active profile's `runtime/<profile>/media/`, the Intro/Outro clips |

(The HUD overlay and the race timer are both served by the relay at fixed loopback URLs,
the collection embeds neither the sheet ID nor any external service URL; no token is
needed for them.)

So `setup-assets.py`:
- rewrites the broadcast-graphic image paths (`__RACECAST_GRAPHICS__`) to the active
  profile's graphics folder. Those PNGs are **not** committed: download them first with
  `racecast graphics` (see [Sheet-driven graphics](#sheet-driven-graphics)
  below); a graphic still missing prints a warning and OBS shows that source black.

(The HUD overlay and race timer need no injection, both are served by the relay;
the profile's `SHEET_ID` is read by the relay, not the collection.)

> `src/assets/` holds **only** the bundled HUD `flags/` + `brands/` logos, they stay
> committed and are served by the relay HUD, not by the OBS collection.

You can override the sheet per-run without touching the profile: `--sheet-id <ID>`.

> **Import the `.import.json`, not the `.template.json`.** And **do not move the folder
> after importing into OBS**. OBS stores absolute image paths. If you move it, re-run
> `setup-assets.py` and re-import.

## Google Sheet: Configuration tab columns

The relay reads the sheet's **Configuration** tab for the vocabulary that populates
the Director Panel dropdowns and the HUD (Streamer names, Session labels, Race
Control messages, team names, brand text). Most of these columns come pre-populated
by the league sheet template. The following column is optional and specific to the
cue channel:

### `Cue Preset` column

Add a column headed **`Cue Preset`** in the Configuration tab to provide quick-cue
buttons in the Director Panel's **Cues** section. Each non-empty cell in that column
becomes one preset button: clicking it fills the cue text field instantly. The column
is **optional**: leave it out (or keep it empty) and directors compose all cues as free
text. Add, remove, or rename presets by editing the column in the sheet; the panel
picks up the change on the next poll without any relay restart.

This is the same admin-managed, read-only-in-the-panel model as the **Race Control**
column. For the full list of Configuration tab columns (Streamer, Session, Race
Control, Teams, Brand) and how the relay validates values against them, see
[Sheet-Webhook](Sheet-Webhook#configuration-tab-team-name-and-number-columns).

## Sheet-driven graphics

The broadcast still-graphics (Overlay, Standings, Schedule, Race/Quali Results, the three
weather overlays, Standby, …) are **pure-runtime**: they are driven from the Google Sheet
**Assets** tab and **never committed**, the same model as the Intro/Outro clips. Each
Assets row that points at a graphic is downloaded as the active profile's
`runtime/<profile>/graphics/<Label>.png`: the Sheet label *is* the filename (no mapping
table; the Intro/Outro YouTube rows are skipped):

```bash
racecast graphics       # -> the active profile's runtime/<profile>/graphics/<Label>.png
```

Run it before `setup-assets.py` (and again before an event when the sheet graphics
changed). `setup-assets.py` then resolves `__RACECAST_GRAPHICS__` to this folder; a graphic
that is still missing only prints a warning (it never fails) and OBS shows that source
black until you fetch it.

Next: [OBS Setup](OBS-Setup).
