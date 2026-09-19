# Run an event

> New here? Start with the visual [Producer onboarding deck ↗](https://jegr78.github.io/gt-racing-broadcast/producer.html), then come back for the detail below.

The producer's checklist from go-live to wrap. Assumes the machine is already set up —
if not, do [Set up the broadcast PC](Set-up-the-broadcast-PC) first.

## The shape of an event

```mermaid
flowchart LR
  A["Prepare<br/>reboot, update, cookies"] --> B["Go live<br/>start on Standby, cut to Intro"]
  B --> C["Race<br/>stints and driver changes"]
  C --> D["Interviews<br/>at the end"]
  D --> E["Outro<br/>cut to the Outro clip"]
  E --> F["Wrap up<br/>stop stream and relay"]
```

## One-click bring-up

On the event day, open the **[Control Center](Control-Center)** (double-click
`racecast-ui`), confirm the **active league profile** in the sidebar (switch in the
**Profile** view if you run several leagues), and press **Start event** on the **Home**
dashboard.

![The Control Center Home dashboard](images/cc-home.png)

This launches Tailscale, Discord, the relay, OBS and Companion (in that order).
The dashboard then shows each one's state at a glance, plus tiles for Preflight,
Assets and Cookies readiness — so you can see what's still missing and fix it from
the matching view.

> **Page updates:** starting the event re-loads the HUD/overlay browser sources
> automatically when an update changed them. If a page ever looks stale,
> `racecast obs refresh` (or right-click the source → Refresh) forces it.

After the broadcast, press **Stop event** — it stops the relay and Companion;
OBS, Discord and Tailscale stay running. If OBS is still open, the stop also asks
it (via the OBS WebSocket, port 4455) to drop its connections to the dead feeds —
otherwise OBS would pin the feed ports until it restarts and the next preflight
would warn "port in use". The feed sources reconnect automatically the next time
their scene goes active.

> **CLI alternative:** `racecast event start` (bring-up), `racecast event status`
> (readiness report — names the exact fix command for anything missing),
> `racecast event stop` (wind-down). All act on the active league profile; run one
> against another league with `racecast --profile <name> event …`.
>
> Add `--title "GTEC - 2026 - Round 4 - Nürburgring 24h"` to label this round in
> the Director Panel, the Commentator Cockpit and Discord (also editable live in
> the panel; see [Director](Director#event-title)). At a producer takeover, the
> incoming machine pulls the on-air title from producer A automatically.

## Before you go live

Plan **about 30 minutes** for these steps before the broadcast slot. Do them from
the Control Center; the CLI alternative is in italics.

1. **Pick the league.** Confirm the active league in the sidebar; if this machine
   serves several leagues, switch in the **Profile** view. Every following step acts
   on the active league. *CLI: `racecast profile use <name>`.*
2. **Update the tool.** The Control Center flags an available update in the
   sidebar. Apply it (skip if the team froze the version for the event).
   *CLI: `racecast update`.*
3. **Reboot** the PC (frees memory) and close heavy apps.
4. **Tools → Update all.** Outdated tools are the #1 cause of a feed not starting.
   *CLI: `racecast install-tools --update`* (manual: `brew upgrade streamlink yt-dlp` on
   macOS/Linux · `winget upgrade yt-dlp.yt-dlp Streamlink.Streamlink` on Windows).
5. **Assets → Cookies → Refresh** (pick the browser; log into YouTube in it first).
   If any stint uses a gated Twitch feed, also refresh the Twitch login:
   *CLI: `racecast cookies firefox` (YouTube, required) and
   `racecast cookies twitch firefox` (Twitch, if needed).*
6. **Refresh the intro/outro clips** (only if their URLs changed): **Assets →
   Media → Download** — pulls the URLs from the Sheet **Assets** tab into the active
   profile's `runtime/<profile>/media/intro.mp4` / `outro.mp4`. *CLI: `racecast media`.*
7. **Refresh the graphics:** **Assets → Graphics → Download** — pulls every graphic
   from the Sheet **Assets** tab into the active profile's `runtime/<profile>/graphics/`
   (Standings, Schedule, Race/Quali Results, the three weather overlays, Standby, …). Run
   it whenever the sheet graphics changed. The **weather** graphics are then available as
   full-screen toggles during the race (see [Director guide](Director)). *CLI: `racecast graphics`.*
8. **Refresh the brand logos** (only if the league uses per-team logo overrides):
   **Assets → Brands → Download** — pulls the per-league logo overrides from the Sheet
   **Brands** tab into the active profile's `runtime/<profile>/brands/`, where they win
   over the bundled defaults on the HUD. Run it whenever the Brands tab changed. *CLI:
   `racecast brands`.*
9. **Preflight → Run** — fix anything it flags. *CLI: `racecast preflight`.*
10. **Home → Start event** brings up Tailscale, Discord, the relay, OBS and
   Companion in one go. If Tailscale's backend is stopped, this connects it
   automatically — no click in the Tailscale GUI needed. (Or start them individually
   from **Relay** and **Apps**.) Confirm each live feed shows up in OBS. *CLI:
   `racecast event start`, or `racecast relay start` then `racecast companion start`.*
11. On the **Home** dashboard, make sure **Companion** is connected and a director
   can reach the Web Buttons page (`http://<producer-tailscale-ip>:8000/tablet`) (first-time directors:
   [Director setup](Director-Setup)).
12. **Enter the league's stream key** in OBS (**Settings → Stream**).

## Go live

Start OBS on the **Standby** scene, then click **Start Streaming**. From here the
**director runs the show** — you just keep an eye on the machine. The director opens with
the **Intro**: pressing **INTRO** (Companion) plays the looping intro clip with its own
audio. When the field is ready they cut into the race look (**STINT A** / **Splitscreen**).

**You should now see:** OBS sitting on **Standby** with the stream running —
the **Start Streaming** button now reads **Stop Streaming**.

## The director panel (remote control)

Directors without a Stream Deck — or anyone on a tablet — can drive the same
show from the **director panel** the relay serves at
`http://<producer-tailscale-ip>:8088/panel` (`racecast event start` prints both
director URLs ready to forward; first-time directors:
[Director setup](Director-Setup)).

![Director panel](images/director-panel.png)

The page is organized as horizontal busses that mirror the Companion pages,
so the Stream Deck and the panel share one muscle memory:

| Bus | What it does |
|---|---|
| **PGM** | one-press program switches (scene + feed visibility + mutes), identical to the Companion macros — STINT A/B, SPLIT, INTERVIEW, STANDBY, INTRO, OUTRO, RED FLAG. SPLIT also sets Race Control to *Driver Swaps*, STINT A/B clear it, and RED FLAG toggles the Standby Cover together with the *Red Flag* message ([Director guide](Director#the-companion-web-buttons-board)); these Race Control writes need the sheet-write webhook |
| **FEEDS** | relay control: NEXT (driver change — cuts back to Stint and clears Race Control with the cut), feed reloads, POV reload/stop, FEEDS → STINT… |
| **HUD** | the Stint HUD label, Streamer, Session and Race Control dropdowns — changes show on the HUD immediately and are written back to the Setup tab ([Director guide](Director)) |
| **SCN·VIS** | raw scene switches and feed visibility toggles |
| **GFX** | graphics toggles (HUD, Standings, Schedule, results, weather, covers) |
| **TIMER** | the race timer (see [Race Timer](Race-Timer)) |
| **AUDIO** | per-input dB sliders, 0 dB reset and mutes |
| **Schedule** | one collapsible, mode-aware editor. Its header shows the mode (**RACE** / **QUALIFYING**) and a single **switch → QUALIFYING / RACE** button. Race mode: per-stint Streamer + Stint label dropdowns + URL (rows live on a feed marked A/B); qualifying mode: the single Qualifying-tab row served on Feed A (different day). The **POV** URL row shows in both modes. Saves write the sheet; feeds pick changes up on RELOAD/NEXT; on handover the on-air row's Streamer + Stint label auto-fill the HUD. Bring the stack up already in qualifying with `racecast event start --qualifying` ([Director guide](Director#director-panel--qualifying)) |

The status strip at the top shows what is on air, which stint each feed
carries, the POV state and the race timer. **Every control works relay-only** —
scenes, sources and audio included: the panel calls the relay, and the relay
drives the producer's local OBS, so the director needs **no OBS IP, port or
password**. HUD and URLs additionally need the sheet-write webhook (see
[Sheet-Webhook](Sheet-Webhook); without it they are display-only). Because the
panel holds no OBS credential, it also works in full over the public Funnel at
`/console/panel` — see [Remote access](Remote-access).

> **The HUD/PGM "Race Control" here is the on-screen banner** (the `RED FLAG` /
> `Driver Swaps` overlay message, written to the Setup tab) — **not** the read-only
> [Race Control monitoring desk](Console#race-control-read-only-monitoring-desk) crew
> role. Same name, different things: the banner is director-only; the desk only watches.

**Ports at a glance** (the producer's machine; directors just open the links above):

| Port | Surface |
|---|---|
| `8088` | the relay — Director Panel (`/panel`), HUD (`/hud`), `/console`, timer/chat APIs |
| `8000` | Companion — Web Buttons (`/tablet`) |
| `4455` | OBS-WebSocket (local only — never funnelled; the relay uses it to drive OBS) |
| `8089` | the Control Center (`racecast-ui`, local only) |

### Broadcast Parts (Director Panel)

Long races are split into **Parts** (each a separate YouTube broadcast with its
own stream key, from the Sheet **Producer** tab). The Director Panel drives them —
no producer machine access needed:

1. `racecast event start` resets to **Part 1** (offline). Recovery after a mid-event
   restart: `racecast event start --part N`.
2. In the panel, click **Start Part N** → type the confirmation phrase
   (`START PART N`) → the relay sets that Part's stream key and goes live.
3. **End Part N** (type `END PART N`) stops the broadcast. If a next Part exists the
   panel offers **Start Part N+1**; the last (or only) Part just stops.

Every go-live / end requires the typed phrase — a stray tap can't change the live
state. A league with no Producer tab keeps the plain **GO LIVE** button.

## During the race: driver changes

About every two hours the driver/commentator changes. Two feeds take turns so the picture
on air never drops:

```mermaid
flowchart LR
  subgraph nowair["On air"]
    F1["Feed A<br/>current commentator"]
  end
  subgraph ready["Getting ready"]
    F2["Feed B<br/>next commentator"]
  end
  F1 -->|"driver change: press Feeds Next"| F2
  F2 -.->|"next change, roles swap"| F1
```

At each change the director: **arms the incoming feed** a few minutes ahead (its
scheduled link only starts pulling once armed — **ARM A/B** on the panel; wait for
*serving*), cuts to **Splitscreen** (the combo sets **Race Control** to *Driver Swaps*
with it), then — with the incoming feed warm — presses **Feeds Next**. The relay hands
the feed over, cuts the program back to **Stint** on the incoming feed for you (no
**STINT A/B** press needed), **clearing Race Control** with the cut, sets the HUD's
**Stint** label and **Streamer** from the on-air **Schedule row** automatically (sourced
from the Configuration vocab; a blank or off-vocab row leaves the field as-is), **and
stops the outgoing feed's pull** — so only one commentator stream is pulled at a time.
This is the **same workflow whether you produce in the cloud or at home**: the director
arms then cuts, everywhere.
The panel's **HUD row** provides the Stint / Streamer / Race Control dropdowns
as a live correction (the next handover re-asserts the schedule's values); editing
the sheet's Setup tab directly is the equivalent fallback.
Full step-by-step: [Director guide](Director#at-a-driver-change). (Why two feeds:
[Relay — how the feeds work](Relay-Mode).)

## During the race: driver POV (optional)

The director can show a driver's stream as a small PiP in the Stint scene. It needs a
**few minutes of lead time** — the driver goes live, the URL goes into the sheet, the
director presses **POV Reload**, and only once the relay reports the pull as `serving`
is there a picture to show. The director drives all of it; on the producer side nothing
is needed beyond the relay already running. Steps and timing:
[Director guide](Director#showing-a-driver-pov-plan-ahead).

## Producer handover (12h/24h multi-part events)

Long events are split into broadcast parts run by different producers, each on
their own machine with their own stream key. Viewers follow via the channel's
end-of-stream redirect; plan a few minutes of deliberate overlap.

The relay does **not** need the previous producer's Feed A/B order — the
ping-pong works from any starting point. `--stint <N>` simply puts stint N on
Feed A and preloads stint N+1 on Feed B; from there `/next` works as usual.
Which feed carries which stint may therefore differ between the parts — that
is fine.

1. Incoming producer: on the Control Center **Home**, type the stint into the
   field next to **Start event** and press it (*CLI: `racecast event start --stint <N>`*).
   N is the stint **on air right now** (1-based, from the schedule sheet / Discord).
   The **outgoing producer's** panel status strip (or their `/status`) shows the
   stint each feed carries and which is on air — anyone with that panel open can
   read N off it. Taking over right at a stint change (e.g. a part boundary like
   "end of stint 3"): pass the stint that is starting.
2. Verify Feed A shows the expected commentator (`/status` or the OBS
   preview).
3. Start your OBS stream with this part's stream key — the overlap begins.
4. Share your panel/tablet URLs with the directors (`racecast event start` prints
   them — just forward).
5. Outgoing producer: stop the stream (the YouTube redirect takes over), then
   `racecast event stop`.

> **Taking over without a Tailscale account?** If the incoming producer is **not** on
> the tailnet, they can still pull the handover state over the outgoing producer's public
> Funnel: `racecast event takeover <A-magicdns-host> --funnel [--stint N]`. It reads A's
> on-air stint, chat and revocations over `/console` (authenticated with the shared league
> `CONSOLE_SECRET`) and brings the station up at that stint. Feed stream URLs never leave
> A's tailnet. The tailnet form `racecast event takeover <A-tailscale-ip>` is unchanged.
> Details: [Remote access](Remote-access#producer-takeover-over-the-funnel).

> **A local stint cannot be taken over.** A Schedule row marked `local:` is read from the
> outgoing producer's own capture card ([local capture stint](Relay-Mode#local-capture-stint)).
> That picture exists only on that machine: if it dies during the local stint, the stint
> goes down with it and no successor can pull it again. On the incoming machine the same
> row means *its* capture card; without `RACECAST_CAPTURE` in its `.env` the feed stays
> idle with *local capture: RACECAST_CAPTURE is not set in .env*. This is a deliberate
> limit of the local setup, not a takeover fault.

> **Takeover announcement.** A `racecast event takeover` posts a **Discord** alert (with an
> `@here` ping) naming the incoming and outgoing producer and the stint, and drops a
> **takeover** marker on the [Health Monitor](Health-Monitor#events) timeline of the
> incoming machine. Producer names come from the league Sheet's **`Producer`** tab
> (`Part | Producer | MagicDNS`): each machine reverse-resolves its own MagicDNS name to a
> display name, falling back to the hostname when the tab has no matching row. The same
> mechanism also posts a Discord alert + Health-Monitor marker when **OBS starts or stops
> streaming**.

Typo, or forgot `--stint`? Fix it **before going live**:
`http://127.0.0.1:8088/set/stint/<N>` repositions both feeds. Like the other
`/set` endpoints it tears a running feed off its stream — not for mid-program
use.

**Same producer runs the next part:** just stop the OBS stream and start it
again with the next part's stream key — the relay keeps running, no `--stint`
needed.

## Interviews (at the end)

Interviews run at the very end over Discord voice. The producer who is on air for the last
part must **join the Discord "Interviews" voice channel personally, before race end** — the
OBS audio is captured from your local Discord, so the director can't join for you. You stay
muted until the director cuts to the Interview scene, so joining early is harmless. (On
12 h / 24 h events only the final-part producer does this.)

The conversation itself is moderated from **inside the voice channel** by one of its
participants — usually the streamer of the final stint. The director can take that
role, but does not have to.

## Outro &amp; wrap up

When the interviews and the on-air wrap-up are done, the director presses **OUTRO** — the
looping outro clip plays (with its own audio) and stays on air. After that you can **Stop
Streaming** in OBS at any time, then wind everything down with **Stop event**
(`racecast event stop`). (`Ctrl+C` only applies to the foreground `racecast relay run`
debug mode — the normal relay runs as a background service.)

---

Something looks wrong? → [If something goes wrong](If-something-goes-wrong).
