# If something goes wrong

Problem → fix. When in doubt, run `racecast preflight` first — it catches
most setup problems (tools, ports, cookies) before they bite you live.

## A feed won't show

| Problem | Fix |
|---------|-----|
| Feed says *"Sign in to confirm you're not a bot"* | Refresh cookies (`racecast cookies firefox`) and make sure **deno** is installed — the feeds need both. |
| Feed says *"Requested format is not available … the YouTube cookie jar has no login"* | The cookie jar was exported from a browser that is not logged in to YouTube, so yt-dlp finds no playable format. Log in to YouTube in that browser and re-run `racecast cookies firefox`. The relay also logs this as a WARNING when it starts. Without the cookie hint, a *(live_status post_live)* or *(live_status was_live)* suffix means the broadcast has ended. |
| Cookie export from Chrome/Edge/Brave says FAILED | On **Windows** these browsers cannot be exported at all — their cookies are app-bound encrypted (Chrome 127+). Log into YouTube in **Firefox** and run `racecast cookies firefox` (works even while Firefox is running). On macOS/Linux: close the browser completely first — it locks its cookie database while running — then re-run. |
| A feed just won't appear | First: is the commentator actually live right now? If their stream is live but the feed stays *connecting* or its log shows `403 Forbidden`, see [Keep the broadcast up when a feed fails mid-event](#keep-the-broadcast-up-when-a-feed-fails-mid-event). **Do not** `racecast install-tools --update` during or just before an event — a tool version bump right before going live has 403'd a YouTube feed through a whole production; update only with ≥ 48 h to re-test. |
| Nothing happens when you open a feed's address in a browser | That's normal — each feed serves only OBS, not browsers. Not a fault. |
| A feed stays *connecting* and its log says *"Address already in use"* | A stale/orphaned process is holding that feed port. Run `racecast freeport` (or **Free feed ports** in the Control Center's Relay view) to clear ports 53001–53003, then start the relay. It refuses to touch a *running* relay/streams, so it's safe to run anytime. |
| The handover didn't switch feeds | Press **Feeds Next** once **after** cutting to the new feed; the off-air feed only advances on Feeds Next, never mid-stint. |
| POV PiP is black after **POV Toggle** | Shown too early — the pull wasn't ready yet. Open `http://<producer-tailscale-ip>:8088/status`: the `pov` block must say `serving` (`connecting` = still resolving, or the driver isn't live yet — the relay retries every 15 s on its own). Hide the PiP, wait for `serving`, toggle again. Full timing: [Director guide](Director#showing-a-driver-pov-plan-ahead). |
| `racecast update` says binaries are still building | The release was just cut and CI is still uploading — retry in a few minutes. |

## Keep the broadcast up when a feed fails mid-event

The single most important rule: **never restart the whole relay to fix one feed.**
Feed A (on air) and Feed B (preparing the next stint) are independent — a dead Feed B
does not touch what is on air. You have time. A panic relay-restart took down the first
live production that the failing feed alone would not have.

**What you control is the producer side, not the streamer.** You usually cannot tell a
commentator to switch platform or change their encoder, and a spare stream rarely
exists — so those are not real contingencies. The realistic levers are all on your side:

1. **Leave the on-air feed alone.** It keeps serving regardless of the other feed's state.
2. **Give the stuck feed time.** The relay re-resolves and retries on its own (escalating
   backoff). A transient YouTube hiccup often clears within a minute or two with no action.
3. **Reload, don't restart.** Press **Feeds Reload** (panel) / hit `/reload`: it re-reads
   the schedule and re-resolves the stuck feed *without* disturbing the on-air feed.
   A whole-relay restart is almost never the right move.
4. **Cut to Standby for dead air.** If a stint genuinely cannot be pulled, switch OBS to
   your **Standby** scene instead of showing a frozen/black feed — that keeps the stream
   alive and buys time. Hold the previous commentator longer if they are willing.
5. **Last resort — relay really wedged.** Only if the relay *process* itself is
   unresponsive: `racecast relay restart`. If it then reports **control port 8088 already
   in use**, run `racecast freeport 8088`, then `racecast relay start`. (Free the feed
   ports separately with `racecast freeport`.)

> You cannot always rescue one specific feed in the moment — but you can always keep the
> rest of the broadcast on air. Decide fast: rescue, or route around it.

### A feed loads in testing but 403s live (YouTube)

Symptom: the feed resolves, then the log shows `403 Client Error: Forbidden` on every
attempt — on a stream that worked in your tests. This is a YouTube-side rejection, **not**
a network or bandwidth problem (a Twitch feed on the same line is unaffected, because it
never goes through yt-dlp).

- **Do not update tools on event day.** A `yt-dlp` version change minutes before going
  live is what turned a working YouTube path into a wall of 403s. Freeze your toolchain
  and only run `racecast install-tools --update` with **≥ 48 h** to re-test afterwards.
- **For a stream you control yourself** (e.g. your own backup re-stream from a second PC):
  a **1080p60** source forces YouTube's `itag 301` live manifest, the most fragile
  rendition. Set that encoder to **1080p30 or 720p** to avoid it. This lever only applies
  to *your own* source — you cannot impose it on a guest commentator.
- Confirm cookies are fresh (`racecast cookies firefox`); a stale jar fails YouTube
  specifically. (The relay now hands streamlink the same User-Agent + cookies yt-dlp used,
  which closes the most common cause of this 403 — but the freeze rule above still stands.)

## The HUD / overlay is blank or stale

| Problem | Fix |
|---------|-----|
| HUD is blank in OBS | The relay draws the HUD — make sure it's running (`racecast relay start`). |
| **ARM64 Linux:** OBS has **no "Browser" source type** (can't add the HUD at all) | The distro OBS ships without it and no prebuilt one exists for `aarch64`. Run **`racecast obs-browser`** once to build & install it. [Details](OBS-Setup). |
| **ARM64 Linux / VM:** Browser Source stays black or OBS crashes on it | No-GPU host — CEF's GPU subprocess can't open a DRM render node. Turn off **OBS → Settings → Advanced → Browser Source Hardware Acceleration**. |
| HUD text isn't updating | It updates within a few seconds. Check you're editing the **Overlay** tab of the sheet and that the sheet is still shared. |
| A flag or team logo is missing | The image file's name must match the text in the sheet (lowercase, spaces become `-`). Run `python3 tools/fetch-flags.py` to fetch any missing flags. Full detail: [OBS & scenes](OBS-Setup). |

## The director can't connect

| Problem | Fix |
|---------|-----|
| First triage | Director-side checks (right `/console` link, Funnel on, Tailscale app for the tailnet path) are on [Director setup → If you cannot connect](Director-Setup#if-you-cannot-connect) — have the director run through those while you check below. The director never enters an OBS password; the relay holds it. |
| Director can't reach the buttons | Run `racecast tailscale status` — the process icon alone says nothing about being connected; the backend must be `Running`. If it shows `Stopped`, run `racecast tailscale up` first. Then check: Tailscale connected on both machines? Companion running with **GUI Interface = All Interfaces**? Using the **Tailscale** address (`100.x.y.z`), not a local one? |
| **Linux:** Connect says `Access denied: prefs write access denied` | `tailscale up`/`down` need root. Run **once**: `sudo tailscale set --operator=$USER` — afterwards the Control Center Connect/Disconnect buttons work without `sudo`. |
| **Linux:** status says `NeedsLogin` / Connect says *logged out* | There's no Tailscale app to "open" on Linux — sign in from a terminal: `sudo tailscale up`, then open the printed `https://login.tailscale.com/…` URL in a browser (one-time). See [Set up the broadcast PC → step 8](Set-up-the-broadcast-PC#8--connect-remote-directors-tailscale). |
| Buttons load but OBS shows disconnected | OBS open with the WebSocket server on (port `4455`) and the **same password** entered in Companion? |

## No Discord audio (interviews)

| Problem | Fix |
|---------|-----|
| No interview audio | Discord must run **windowed** (not fullscreen), and the producer must have **joined the voice channel locally**. |
| Discord source dead after switching machine/OS | `racecast setup` localizes the capture source per OS (macOS *App Audio Capture* · Windows *Application Audio Capture* on `Discord.exe` · Linux [PipeWire Audio Capture plugin](https://obsproject.com/forum/resources/pipewire-audio-capture.1458/)) — re-run `racecast setup` and re-import. |
| **Linux:** no *Discord Audio Capture* / OBS has no PipeWire Application Capture source type | That plugin is **not** part of OBS core — install it for your distro/OBS first (packaging varies). The upstream way: extract the latest `linux-pipewire-audio-*.tar.gz` from the [plugin's releases](https://obsproject.com/forum/resources/pipewire-audio-capture.1458/) into `~/.config/obs-studio/plugins/`, then restart OBS. Sandboxed OBS (Flatpak/Snap) needs extra steps. |
| Interview audio doubled / echo | Capture Discord only through that audio-capture source — not *also* via desktop audio. |
| **ARM64 / no native Discord:** audio is silent or the capture source shows no target | Interview audio is captured from **Discord-web in a browser** on this platform. Make sure the browser is the one named by `RACECAST_DISCORD_WEB_BROWSER` (default Firefox), that Discord-web is open and in the voice channel, and that the **Discord Audio Capture** source's *TargetName* matches the browser's PipeWire node (check it in OBS → the source's properties). If still silent, try the other match (`RACECAST_DISCORD_WEB_BROWSER=Chromium`) or confirm the `obs-pipewire-audio-capture` plugin is installed. |

## Everything is laggy

| Problem | Fix |
|---------|-----|
| General lag / stutter | Memory is the usual limit (16 GB) — **reboot before the event**, close other apps, run preflight. Make sure OBS uses your GPU to encode. |

## The "?" help button in the panel/cockpit opens a 404

An older build. The repo rename moved the onboarding decks to
<https://jegr78.github.io/gt-racing-broadcast/>; GitHub redirects repository URLs
permanently but **not** Pages sites, so the link baked into older binaries is dead.
`racecast update` fixes it — self-update itself still works, it goes through the
redirected API. Until then open the decks directly, or use the Control Center, whose
links follow the new name.

---

Deeper diagnostics for developers: [Architecture](Architecture),
[Relay — how the feeds work](Relay-Mode).
