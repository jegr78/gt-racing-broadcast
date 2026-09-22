# GT Racing Broadcast: Repository

Single-source repo for the GT Racing broadcast producer station.
**Edit only under `src/`.** `dist/` and `runtime/` are generated and gitignored.

📖 **Operator docs & onboarding:** see the [project wiki](https://github.com/jegr78/gt-racing-broadcast/wiki)
(architecture diagrams, setup, runbook, troubleshooting) and the visual
[onboarding decks](https://jegr78.github.io/gt-racing-broadcast/), one short
walkthrough per role.

🛠️ **Building from source or contributing?** See [CONTRIBUTING.md](CONTRIBUTING.md).

## Layout
- `src/`, source of truth: `relay/`, `obs/`, `companion/`, `director/`, `assets/`, `scripts/`, `docs/`, `setup-assets.py`
- `profiles/`: one directory per league (`profiles/<name>/profile.env` + `overlay/`); `profiles/example/` ships as the template
- `.env`: machine-local config (gitignored; copy from `.env.example`)
- `tools/`: maintainer scripts (build, tokenize, sync, helpers), not shipped
- `tests/`: stdlib test suite (run all with `python3 tools/run-tests.py`)
- `runtime/`: cookies/logs/caches + per-profile state (gitignored)
- `dist/`: built distributable + ZIP (gitignored)
- `docs/superpowers/`: specs & plans

## Configuration model: machine vs. league

Two layers, kept deliberately separate so one machine can run several leagues:

- **Machine config: `.env`** (copy from `.env.example`, gitignored, repo root).
  Holds only machine-local values, never league secrets:
  `RACECAST_OBS_WS_PASSWORD`, `RACECAST_COMPANION_EXE`, `RACECAST_UI_PORT`,
  `RACECAST_UI_PASSWORD` (reserved), and `RACECAST_PROFILE` (the default active
  league). Real environment variables take precedence.
  ```bash
  cp .env.example .env
  ```
- **League config: `profiles/<name>/profile.env`.** Each league is a profile
  directory with **un-prefixed** keys: `NAME`, `SHEET_ID`, `SHEET_PUSH_URL`,
  `INTRO_URL`, `OUTRO_URL`, `LOGO`, `OBS_COLLECTION`. The Google Sheet that
  drives the HUD + relay schedule comes from this file's `SHEET_ID`, **not**
  from `.env`. `profiles/example/` is the copy-from template; copy it with
  `racecast profile new <name> --from example`. To **try the toolkit
  immediately**, the shipped `profiles/demo/` league points at a public, read-only
  demo Sheet: `racecast profile use demo` then `racecast graphics && racecast
  relay start` runs out of the box (Sheet layout: [Sheet template wiki page][sheet-tpl]).

[sheet-tpl]: https://github.com/jegr78/gt-racing-broadcast/wiki/Sheet-Template

## Profiles (leagues)

```bash
racecast profile list            # all profiles; marks the active one
racecast profile show [<name>]   # resolved config for a profile (defaults to active)
racecast profile use <name>      # set the active profile (writes runtime/active-profile)
racecast profile new <name> --from example   # copy a profile dir to start a new league
racecast profile export <name>   # export a whole league profile to a portable zip (share with another producer)
racecast profile import <file>   # import a profile bundle on this machine (--force to replace)
racecast --profile <name> <cmd>  # run one command against a non-active profile
racecast sheet open              # open the active league's Google Sheet (built from SHEET_ID); `sheet url` prints it
```

Active-profile precedence: `--profile` > `RACECAST_PROFILE` (in `.env`) >
`runtime/active-profile` pointer > the sole profile if only one exists. Each
league keeps its own OBS scene collection, graphics/media, and HUD overlay, so
switching leagues is a profile switch: no editing of `.env` or the collection.

### Per-league HUD overlays
Each profile can restyle the relay-served overlay pages (the HUD, which includes the
race timer: and the splitscreen) via `profiles/<name>/overlay/hud.css` (plus an optional
`splitscreen.css` and `overlay/fonts/`). These override the bundled defaults per league
and are editable in the Control Center's visual overlay builder.
The first override on a profile whose `overlay/` did not exist when the relay
started needs one `racecast relay restart`; later edits apply live (Apply in OBS).
See the [HUD overlays](https://github.com/jegr78/gt-racing-broadcast/wiki/HUD-Overlays) wiki page.

## Get started: the Control Center

Download the latest release for your platform from
[**GitHub Releases**](https://github.com/jegr78/gt-racing-broadcast/releases/latest)
(`racecast-windows.zip` / `racecast-macos.tar.gz` / `racecast-linux.tar.gz`, plus
`racecast-linux-arm64.tar.gz` for ARM64 Linux) and
extract it into **its own folder**. The archive holds two binaries side by side:
**`racecast`** (the CLI) and **`racecast-ui`** (the Control Center).

**Double-click `racecast-ui`** (`racecast-ui.exe` / `racecast-ui.app`; Linux:
`./racecast-ui`) to open the **Control Center** at `http://127.0.0.1:8089/`, a
local web dashboard that runs the whole station (setup wizard, service control,
logs, the Profile view, and General Settings) from your browser. The first launch
creates a `.env` next to the binaries for your machine config. Full step-by-step:
[Set up the broadcast PC (wiki)](https://github.com/jegr78/gt-racing-broadcast/wiki/Set-up-the-broadcast-PC)
· [The Control Center (wiki)](https://github.com/jegr78/gt-racing-broadcast/wiki/Control-Center).

The **Profile view** switches leagues, copies a profile to create a new one,
edits `profile.env` (incl. `OBS_COLLECTION`), edits the overlay CSS, manages
that profile's graphics/media, and edits the per-league **crew roster** (director/producer
flags, written back to the Sheet's Crew tab). **General Settings** holds the machine
`.env` and cookies.

> **First start:** Windows SmartScreen / macOS Gatekeeper show a one-time warning for
> unsigned binaries: choose "Run anyway" / right-click → Open. On macOS, clearing
> the quarantine once (`xattr -dr com.apple.quarantine racecast racecast-ui.app`)
> also avoids App Translocation (which can break the Control Center's asset
> previews). See the setup guide.

## The CLI (alternative)

Everything the Control Center does is also a `racecast …` command, the terminal
stays a first-class option (and the only one on headless Linux). Run `racecast`
once to create the `.env`, then update later with `racecast update`.

### One-time machine setup
```
racecast init         # guided setup: picks/creates a league profile (fills its
                      # SHEET_ID), installs tools+apps, cookies, graphics, media,
                      # OBS collection, Companion config, preflight: skips what is
                      # already done; re-run any time
```

Or run the steps individually:
```
racecast profile new <name> --from example   # create the league, then fill its SHEET_ID
racecast install-tools     # installs yt-dlp/streamlink/ffmpeg/deno (offers Homebrew setup on a fresh Mac)
racecast install-apps      # optional: installs OBS, Companion, Tailscale
racecast obs-browser       # ARM64 Linux only: build & install OBS's Browser Source plugin (the relay HUD needs it; no prebuilt exists for aarch64)
racecast preflight         # verify the machine is ready
racecast export companion  # write the Companion button config -> runtime/racecast-buttons.companionconfig
```

### One-time / pre-event setup
```
racecast cookies firefox          # refresh YouTube cookies before each event (log into YouTube in Firefox first)
racecast cookies twitch firefox   # refresh Twitch cookies (only if any stint uses a gated Twitch feed)
racecast media             # download the active profile's Intro/Outro clips -> runtime/<profile>/media/
racecast graphics          # download the active profile's broadcast graphics -> runtime/<profile>/graphics/
racecast brands            # download per-league brand-logo overrides -> runtime/<profile>/brands/
racecast setup --out runtime/GT_Racing_Endurance.import.json   # localize OBS assets + inject the profile's Sheet ID
# (default --out is the profile-scoped runtime/<profile>/GT_Racing_Endurance.import.json)
# OBS -> Scene Collection -> Import -> the import JSON above
```

## Run it

```
racecast event start          # bring everything up: Tailscale, Discord, relay, OBS, Companion
racecast event start --stint 4 # take over mid-event (12h/24h): stint 4 is on air now
racecast event start --title "GTEC - 2026 - Round 4 - Nürburgring 24h"  # set the event title (Panel/Cockpit/Discord); also editable live in the Panel
racecast event takeover <A-ip>                       # take over from A over the tailnet (reads on-air stint, pulls chat + console token revocations)
racecast event takeover <A-magicdns-host> --funnel  # same but over A's public Funnel, no Tailscale account on B; needs `racecast funnel on` running on A and the league CONSOLE_SECRET in B's profile
racecast event status         # event-day readiness report (apps, services, cookies, graphics, media, config)
racecast event stop           # stop relay/Companion/streams. OBS & friends keep running
racecast tailscale up         # connect Tailscale (event start does this automatically)
racecast tailscale down       # disconnect Tailscale after the event
racecast preflight            # check tools/hardware
racecast speedtest            # opt-in Ookla bandwidth test; logs locally, preflight warns vs 25/10 Mbps
racecast obs benchmark        # FULL vs ROBUST on the on-air feed with a recording running; logs locally, preflight reports it
racecast relay start          # start the relay (background)
racecast relay logs -f        # watch it live (console + feed_A/B/POV merged)
racecast relay logs --list                  # list available archive dates
racecast relay logs --archive 2026-06-17   # read a past day's rotated log
racecast relay status         # health + tailnet URL
racecast companion enable-control  # Linux only: one-time setup so companion start/stop can control the companion-pi systemd service without a password
racecast companion start      # bind Companion to Tailscale, start it
racecast status               # all services at a glance
racecast relay stop           # stop the relay
racecast freeport             # free a stuck feed port (53001-53003): kills an orphaned holder so a feed can bind (refuses a running relay/streams)
racecast obs refresh          # force-reload the relay-served OBS browser sources (HUD/timer)
racecast obs collection       # check the active OBS scene collection
racecast obs collection set   # switch OBS to this league's scene collection
racecast obs logs             # tail the newest OBS Studio log
racecast tailscale logs       # tail the Tailscale status snapshot log
racecast chat clear           # wipe the crew-chat history on the active relay
racecast chat pull <ip>       # take over another producer's chat history at handover (relay may be running)
racecast chat import <file>   # load a previously exported JSON file into the relay
racecast chat export          # write the current chat history to chat-export.json (or --out PATH)
racecast backup create|list|restore|delete <label>   # named look snapshots (overlay+graphics+media)
racecast links                # print per-person /console launcher links (Crew tab ∪ live Schedule); --post puts them in crew chat
racecast funnel on|off          # public ingress for ONLY /console (crew launcher) via Tailscale Funnel
racecast console setup-funnel   # automate the one-time tailnet prereqs via a Tailscale API token (--apply)
racecast console token revoke <streamer>  # rotate one commentator's link
# Note: the per-league CONSOLE_SECRET is auto-provisioned on first relay start (zero-config)
racecast health export [--from TS] [--out PATH]   # dump health history to JSON Lines
racecast health import <file.jsonl>               # merge a health-history dump (dedup by ts)
racecast health pull <ip> [--port N] [--from TS]  # pull another producer's health history (takeover helper)
racecast report                                   # generate the post-event report (last session) into runtime/<profile>/reports/
racecast report send [FILE]                       # send the newest (or given) report to the league Discord as an attachment
```

For live debugging, run the relay in the foreground: `racecast relay run`.

## Building from source / contributing

Editing the toolkit, running the tests and lint, building the distributable and the
standalone binaries, and the maintainer workflows (OBS-collection round-trip, wiki and
Pages publishing) all live in **[CONTRIBUTING.md](CONTRIBUTING.md)**: with `CLAUDE.md`
as the deep-architecture reference.
