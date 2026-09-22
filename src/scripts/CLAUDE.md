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
