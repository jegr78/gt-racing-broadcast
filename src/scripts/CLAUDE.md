# CLAUDE.md: `src/scripts/` helpers

Loaded when working under `src/scripts/`.

## Static mode (`src/scripts/`): the simpler alternative
`loopstream.py` keeps one streamlink server alive for one public channel (YouTube or
Twitch); `start-streams.py` / `stop-streams.py` manage a set of them with PID/log files
under `runtime/static/`. This is the fallback for **public** channels only, no yt-dlp
bot-check, no unlisted streams; the real unlisted-stream flow is the relay. YouTube is
served via Streamlink's direct HLS path; Twitch is served via Streamlink's Twitch plugin
(low-latency, same flags as the relay: `STREAMLINK_TWITCH` is **duplicated from
`racecast-feeds.py` and pinned byte-identical by a `getsource` cross-check in
`tests/test_streams.py`** to prevent drift). Gated Twitch feeds use the same machine-level
`twitch-cookies.txt` as the relay. Each feed entry may be a YouTube channel ID (UC…) or
a full `youtube.com`/`twitch.tv` URL; invalid channels are rejected at load time by
`is_channel()` (SSRF guard). Invoke via `racecast streams start/stop`,
`start-streams.py`/`stop-streams.py` are logic modules, not the operator entrypoint.
`stop-streams.py` validates a PID actually belongs to a feed process before killing.

## Companion remote-access helpers (`src/scripts/`)
`companion_common.py` (tests `tests/test_companion.py`) contains the pure logic that binds
**Bitfocus Companion**'s admin/web-buttons server to this machine's Tailscale IP so a tablet
can open `http://<tailscale-ip>:<port>/tablet` over the tailnet, same plug-&-play model as
the relay's `--bind auto`, and likewise **not** the LAN. It auto-detects the Tailscale IP
(Tailscale detection/control lives in `src/scripts/tailscale.py`; its `detect_tailscale_ip` is duplicated in the standalone relay: keep those two in sync), and, only while Companion
is stopped and with a `.racecast-bak` backup, sets `bind_ip` in Companion's `config.json`
(`~/Library/Application Support/companion/config.json` on macOS; the GUI launcher reads
it as `--admin-address`). Windows + macOS automated (Windows: Companion.exe discovery +
`RACECAST_COMPANION_EXE` override in `.env`); native Linux: companion-pi **systemd
service**, controlled by `companion_linux.py`: `racecast companion start/stop` invoke
`systemctl` via a root bind helper that pins `--admin-address` to the Tailscale IP, or
`127.0.0.1` when the tailnet is down (never `0.0.0.0`, matching the relay's `--bind
auto` rule). This requires a one-time `racecast companion enable-control` (installs a
systemd `ExecStart` drop-in, the `/usr/local/sbin/racecast-companion-bind` root helper,
and a visudo-validated NOPASSWD sudoers rule); `install-apps` runs it automatically
after a Linux Companion install. Re-run `enable-control` after a structural
`sudo companion-update` that changes the node launch line. Other Linux setups
(WSL/Docker on the host, manual AppImage) keep the manual path. Tests:
`tests/test_companion_linux.py`. Invoke via `racecast companion start/stop`. **Important:**
binding only controls *where* Companion listens. Companion serves `/tablet` and the admin
GUI on one port + one shared socket API (its admin password is a casual deterrent, not a
boundary), so isolating the admin from directors is a **Tailscale-ACL** job (restrict who
reaches the port), not something these scripts can do. Editing `config.json` is
unsupported-but-stable; re-check after Companion upgrades.

## Profiles + config (`src/scripts/config.py`), the multi-profile model
Config comes from **two layers** and one resolver:
- **Machine `.env`** (gitignored, repo root or next to the binary; template
  `.env.example`) holds ONLY machine-local knobs, never league secrets:
  `RACECAST_OBS_WS_PASSWORD`, `RACECAST_COMPANION_EXE`, `RACECAST_UI_PORT`,
  `RACECAST_UI_PASSWORD` (reserved/unused), and `RACECAST_PROFILE` (default active
  profile when no `--profile` is given).
- **`profiles/<name>/profile.env`** is the **league**: un-prefixed keys `NAME`,
  `SHEET_ID` (Google Sheet driving schedule + HUD), `SHEET_PUSH_URL` (optional Apps
  Script webhook that lets the relay write to the Sheet: race-timer state + the
  panel's HUD/Schedule/POV controls), `INTRO_URL`, `OUTRO_URL`, `TRAILER_URL`, `LOGO`,
  `OBS_COLLECTION`, `CONSOLE_SECRET` (signs per-person console tokens; auto-provisioned
  on first relay start), and optionally `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET`
  (per-league Discord OAuth app: when present, `/console/login` + `/console/oauth/callback`
  are activated; when absent, OAuth is off and signed links remain the only entry path).
  The shipped `profiles/example/` is a template, excluded from the usable-league list.
  One install hosts several leagues this way.

`src/scripts/config.py` is the resolver: it parses the machine `.env` + the selected
`profiles/<name>/profile.env`, picks the active profile (precedence: `--profile` >
`RACECAST_PROFILE` env > `runtime/active-profile` pointer file > the sole profile when
exactly one exists), and returns a `ResolvedConfig`. The CLI then **injects** the
active league's values into child processes as **prefixed** env vars
(`RACECAST_SHEET_ID`, `RACECAST_SHEET_PUSH_URL`, `RACECAST_INTRO_URL`,
`RACECAST_OUTRO_URL`, `RACECAST_TRAILER_URL`, `RACECAST_OBS_COLLECTION`: see `_profile_env_vars` /
`_apply_active_profile_env` in `src/racecast.py`), so the relay and the asset
downloaders read a flat environment and stay profile-agnostic. `racecast profile
list|show|use|new|export|import [--from/--no-assets/--out/--force]` manages profiles;
global `--profile NAME` runs one command against a non-active profile. `racecast
profile export NAME` packages the entire `profiles/<name>/` tree (including
`SHEET_PUSH_URL` in `profile.env`) plus the optional runtime `graphics/`, `media/`, and `brands/`
into a single zip that can be imported on another machine with `racecast profile
import FILE`: this is the onboarding path for handing a league to a new producer.
This is distinct from `racecast backup …` (`backup_admin.py`), which is a
profile-internal named snapshot of the overlay CSS + graphics + media only and never
crosses machines.

A small bounded `load_dotenv()` is **duplicated** in the five self-contained scripts
that can run standalone: `src/relay/racecast-feeds.py`, `src/setup-assets.py`,
`src/relay/get-media.py`, `src/relay/get-graphics.py`, and `src/relay/get-brands.py`
(`get-brands.py` also duplicates `asset_key`): reading a `.env` only from
the script dir or the project root (marker: `.git`/`.env.example`), never an unrelated
parent; real environment variables take precedence. These deliberately do NOT import
`config.py` (the relay stays dependency-light), but the canonical loader for everything
else is `src/scripts/config.py`. Keep the five `load_dotenv` copies in sync if you
touch one.

## Unified `racecast` CLI (`src/racecast.py`)
`src/racecast.py` is the single shipped entrypoint for operators. It resolves the
active profile (via `src/scripts/config.py`) and injects its league values into the
environment before dispatching to:
- **`src/scripts/services.py`**, daemon helper for the relay and static-streams: spawns
  subprocesses, writes PID + log files under `runtime/`, and provides start/stop/restart/
  status/logs for both. `racecast relay run` is the foreground/debug mode (no daemon).
- **Companion adapter** (over `src/scripts/companion_common.py`): `racecast companion
  start/stop/restart/status/logs` wraps the Companion bind logic (Windows + macOS
  automated; native Linux companion-pi systemd service controlled; other Linux setups
  (WSL, Docker, manual AppImage) are manual).
- **`src/scripts/obs_ws.py`**: minimal obs-websocket v5 client (stdlib only). After
  `relay stop`/`streams stop` kill the feeds, `_release_obs_feeds()` re-applies the
  feed media inputs' own settings via `SetInputSettings`, the one request that makes
  OBS rebuild the ffmpeg source and close its socket (media STOP/RESTART actions are
  ignored for inactive sources). Without it OBS pins the feed ports in FIN_WAIT_1
  until it restarts and preflight warns "port in use". Must run AFTER the kill (a
  rebuild against a live relay would just reconnect). Password auto-discovered from
  OBS's obs-websocket config.json (`RACECAST_OBS_WS_PASSWORD` in `.env` overrides). Fully
  best-effort: any failure prints one notice and the stop continues.
  It also exposes a scene-collection check/switch (`GetSceneCollectionList` / `SetCurrentSceneCollection`): `racecast obs collection [set]`, a warning during `racecast event start`, a line in `racecast event status`, and the Control Center's OBS row. `racecast event start` auto-switches OBS to the active profile's collection by default (`RACECAST_OBS_COLLECTION_SWITCH=0` restores the old warn-only behaviour), safe because OBS refuses a switch while an output is active and none is active during bring-up; `event takeover` inherits it. The switch runs before the page-refresh hook (it rebuilds every source). The manual `racecast obs collection set` and the Control Center OBS-row button remain the explicit fallback. `racecast event start` also parks OBS on the **Standby** scene after the collection check and the forced page-refresh (Director Guide: start on Standby, then Start Streaming), default-on (`RACECAST_OBS_STANDBY_ON_START=0` opts out); it is best-effort and **never cuts a live program**, the switch is skipped when OBS output is already active (`obs_ws.switch_to_scene_if_idle`), which also makes a mid-event `event takeover` onto a streaming OBS a no-op. The canonical product name is `EXPECTED_SCENE_COLLECTION` (`GT Racing Endurance`), which mirrors the `name` field of `src/obs/GT_Racing_Endurance.json`; a localized per-league collection defaults to `GT Racing Endurance — <league>` (`PRODUCT_COLLECTION_PREFIX` + the profile name, unless the profile sets `OBS_COLLECTION`), so several leagues keep separate collections in one OBS. `racecast obs collection set` switches to the active profile's expected name.

## Console text encoding (full background, moved from the root CLAUDE.md)
- **Console text encoding: the test matrix is not enough, the producer host is German
  Windows.** Python picks the *locale* codepage for text I/O, which is `cp1252` there
  (and plain ASCII under `LANG=C` on Linux). Two failure modes, both of which have cost
  a live debugging session, twice within two days:
  - **Encoding ours.** One non-ASCII character in an argparse `help=` string kills
    `--help` with `UnicodeEncodeError` *before the program does anything*: argparse
    assembles the whole text first. Keep help strings ASCII (`->`, not `→`); a guard test
    enforces it.
  - **Decoding a child's.** A text-mode `subprocess` call without `errors=` dies on the
    first byte cp1252 cannot decode, and because subprocess reads pipes in a THREAD the
    exception never reaches the caller's `except`: you get a traceback and silently lose
    the output. Every text-mode call under `src/` must pass `errors="replace"`.

  The net under both is `logsetup.harden_stdio()` (issue #24's `_force_utf8_io`, moved
  out of `racecast.py` so the relay and the scripts can reach it: living there is why
  only the CLI was protected). It reconfigures stdout/stderr to `utf-8`/`replace`. UTF-8
  rather than the console's own encoding because a Control Center job's stdout is a PIPE
  whose bytes are rendered in a UTF-8 web UI, and sets `PYTHONIOENCODING` so **every
  child inherits the leniency whatever spawn site created it**. That one env var is what
  covers all 17 entrypoints and the 124 `LOG`/`print` lines carrying non-ASCII without
  editing any of them. Call it first in any new entrypoint's `main()`.

  Three guard tests live in `tests/test_logs.py` and scan the whole of `src/`. An earlier
  version covered only `racecast-feeds.py` and only `subprocess.run`; five call sites in
  three other files slipped through and one bit again the next day. **Do not narrow
  their scope.**

## CLI behaviour `--help` does not tell you (full text)
```bash
# Non-obvious bits that `--help` does NOT tell you:
# - cookies: Firefox is the recommended browser. Windows Chrome/Edge exports are blocked
#   by app-bound encryption. `cookies twitch <browser>` is only needed for gated
#   (sub/follower-only) Twitch feeds.
# - install-tools: deno has no apt package, so it is a pinned, SHA-256-verified
#   GitHub-release download into runtime/bin (racecast adds that to PATH); brew is
#   bootstrapped on macOS; Linux apt runs via sudo.
# - obs-browser: Linux/aarch64 only: no distro/PPA ships obs-browser there and the relay
#   HUD/timer need a Browser Source. Pins CEF per OBS version (obs_browser_linux.py).
#   On no-GPU/VM hosts also disable OBS Browser Source Hardware Acceleration.
# - freeport: refuses a running relay/streams (it would cut a live feed) unless --force.
#   Per-process kill (NOT the session-group kill of #133's stop path); port→PID lookup is
#   cross-platform in src/scripts/ports.py.
# - console: the per-league CONSOLE_SECRET is auto-provisioned on first relay start
#   (zero-config): there is no enable/disable command.
# - event takeover --funnel: authenticated with the shared league CONSOLE_SECRET (step-up
#   X-Console-Secret header); /console/takeover/status is REDACTED: feed stream URLs
#   never leave the tailnet.
# - --profile NAME runs ONE command against a non-active profile.
```
