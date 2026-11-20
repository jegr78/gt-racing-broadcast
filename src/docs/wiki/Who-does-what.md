# Who does what

> New crew? The [onboarding decks ↗](https://jegr78.github.io/gt-racing-broadcast/) are short visual walkthroughs, one per role.

Three groups make the show happen: the **commentators** who stream each stint, the
**producer** at the PC, and the **director** who chooses what viewers see. An optional
**Race Control** desk raises the red flag and keeps the race data straight. Further
optional roles: a **Race Engineer** who follows a driver's telemetry, **drivers** who send
their own GT7 telemetry, and the **league admin** who prepares the league.

```mermaid
flowchart LR
  subgraph P["At the PC: Producer"]
    P1["Set up and start the PC"]
    P2["Start and stop the broadcast"]
    P3["Join Discord for interviews"]
  end
  subgraph D["Remote: Director"]
    D1["Choose scenes and graphics"]
    D2["ARM, SPLIT, NEXT at driver changes"]
    D3["Cut to interviews<br/>+ broadcast audio"]
  end
  subgraph RC["Remote: Race Control"]
    RC1["Red flag and race data"]
    RC2["Corrections, DNF, estimates"]
    RC3["Crew chat and notes"]
  end
```

## Producer (at the PC)

- Sets the machine up and keeps it healthy: see [Set up the broadcast PC](Set-up-the-broadcast-PC).
- **Starts and stops** the broadcast; everything in between is the director's job.
- Joins the Discord "Interviews" voice channel for the interview segment (last-part
  producer only: see below).
- Keeps the shared Google Sheet intact.

## Director (remote)

- Drives the whole show from a browser (panel or Companion buttons), no
  machine access. First time: [Director setup](Director-Setup); then the
  [Director guide](Director).
- Chooses scenes and graphics and cuts to the interview segment (scene + broadcast
  audio). The interview conversation itself is moderated from inside the Discord voice
  channel by one of its participants, usually the final-stint streamer; the director can
  take that role but doesn't have to.
- At each driver change: **ARM** the off-air feed a few minutes ahead, wait until it is
  serving, cut to **SPLIT**, then press **NEXT** (Companion: **Feeds Next**). Details:
  [At a driver change](Director#at-a-driver-change).
- Keeps every program control Race Control does not have, including putting the driver
  dashboard on air.
- Multiple directors can take turns; the producer can also direct locally.

## Race Control (remote)

An optional **race desk** for a steward or a league official. It runs from the personal
[Console](Console) link the producer sends; the **Race Control** card appears for anyone
flagged for it. No machine access.

- **Red flag.** Its `RED FLAG` raises the Standby Cover, sets the HUD banner *Red Flag -
  Race Suspended* and holds the driver race. **Restart released** lowers the cover and
  clears the banner when this desk raised them. No step-up secret; the relay logs who pressed it.
- **Race data.** It keeps the driver race straight: corrections, rejoin marks, estimated
  positions, retirements (DNF), driver names, tyre compounds, the restart rule, race
  recording and disconnect events. See
  [Driver telemetry](Driver-Telemetry#red-flag-rejoin-and-corrections).
- **Graphics.** When the league sets `GRAPHICS_TAKE`, the desk can put graphics on air or
  request them, and it alone sets the flag graphics. See [Console](Console#graphics).
- **Crew chat** is its working channel: feed the crew the race-control facts as they
  happen, e.g. a **drive-through / DSQ** for a team, a car's **rejoin time**, a team that
  **can't field a driver** for the next stint, or a team **retiring** from the race.
  **Note to commentator** sends a short note to the commentators' cockpits. Messages
  post under the operator's own name.
- Sees a live **program preview**, the **streamer / stint schedule** (stream URLs are
  redacted: they never leave the producer's tailnet), and the **race timer**.
- It switches no scenes or feeds. The director keeps every other program control,
  including the driver dashboard's on-air switch.
- The role is **additive**: the same person can be a director *and* run Race Control.
  The **producer / league admin** flags people for it (you don't flag yourself) with the
  **Race Control** column on the Sheet's Crew tab (or the Control Center
  [crew editor](Control-Center#profile)). See the
  [Console launcher → Race Control](Console#race-control-desk) for the desk in detail.

## Race Engineer (remote, optional)

A crew member who follows the driver's live telemetry on a solo POV profile: live
instruments, lap analysis and references the producer shares. The desk has no broadcast
controls. It needs the **Race Engineer** Crew column and the producer's **Allow Race
Engineer** switch in the Control Center. See [Race Engineer](Race-Engineer) and
[Console → Race Engineer access](Console#race-engineer-access). The AI Race Engineer in
[Driver radio](Driver-Radio) is a different thing.

## Drivers (optional telemetry sender)

A driver can send their own GT7 telemetry to the broadcast. They open the event's invite
link from the producer, pick their entry and run the **sender** on a device on the same
network as the PlayStation. The race order, gaps and the driver dashboard come from this
data. See [Driver telemetry](Driver-Telemetry).

## Commentators / streamers

Each stint's commentator streams the race on **their own channel**. Hand them this:

- **Platform:** your own YouTube (or Twitch), set to **Unlisted**, the **same channel**
  every event.
- **Latency:** **Low** (not Ultra-low): buffering protection lives on the producer side.
- **Resolution:** **1080p** target; if your upload can't hold ~6 Mbps drop to **720p, never
  below**.
- **Bitrate (CBR), 2 s keyframes:** 1080p60 ≈ 8000 · 1080p30 ≈ 6000 · 720p60 ≈ 4500 ·
  720p30 ≈ 3000 kbps. Audio 128–160 kbps AAC.
- **Encoder:** hardware (NVENC / QuickSync / AMF). **No personal overlays**: graphics are
  added centrally.
- **Give the league your Discord handle and channel name** ahead of time, they go into
  the Crew tab so your Console login is recognised and your stints map to you.
- **Send your watch link** before your stint for the schedule
  (YouTube: `https://www.youtube.com/watch?v=…` · Twitch: `https://www.twitch.tv/<your-channel>`).
  **Preferred:** submit it from your **[cockpit](Commentator-Cockpit#submit-your-stream-link)**
  (pick your own stint, paste, submit, the director approves it). **Fallback:** post it in
  the crew **Discord** channel and the producer/director enters it into the sheet.

### Streaming straight from a PlayStation (no PC)

You can broadcast directly from the console, the relay pulls it the same way:

- **PS5:** 1080p60 to YouTube or Twitch, and you can pick **Unlisted** right on the
  console. This meets the targets above on its own.
- **PS4:** **PS4 Pro** can reach 1080p; the **base PS4** tops out at **720p**: still fine
  (never below 720p). Unlisted works here too.
- **Latency: set it up once, beforehand.** The console has **no latency control**, so the
  PS default (Normal, 15–60 s) is too slow for handover. Fix it ahead of the event on the
  channel itself, *not* at stream time:
  - **YouTube:** YouTube Studio → Settings → set stream latency to **Low**.
  - **Twitch:** Creator Dashboard → Stream → keep **Low Latency** mode on (it is the
    default).
- **No CBR/keyframe/encoder knobs** on the console, that is handled for you; just set the
  resolution and the privacy/latency above.

## League Admin / owner (prepares the league)

Often a different person from the event-day producer: the league owner sets up the
league's **data and identity layer** once, then keeps it tidy. Fluent in Google Sheets
and Discord; not necessarily an OBS/relay operator.

- Owns the shared **Google Sheet**: schedule, HUD/overlay data, the **Crew** roster
  (names, roles, Discord handles), and configuration.
- Sets up the **Apps Script write-webhook** so the relay can write back to the Sheet.
- Optionally creates the **Discord OAuth app** (so crew can sign in to the Console with
  Discord) and a **Discord channel webhook** for submission/health pings.
- Optionally designs the **per-league look** (overlay/HUD).
- Full walkthrough: **[League owner setup](League-Owner-Setup)** (and the deck:
  [League Admin onboarding ↗](https://jegr78.github.io/gt-racing-broadcast/league-admin-setup.html)).

## Event sizes

- **8 h** = 1 part = 1 producer (also the one who joins Discord for the interviews).
- **12 h** = 2 parts (~6 h each) = 2 producers.
- **24 h** = 3 parts = 3 producers.

Only the **last-part** producer joins Discord; earlier producers don't use Discord at all.

> The HUD and graphics pull live data from the **shared Google Sheet**. The race timer is
> relay-served; Director controls (start/stop/show/hide/correct) are on the panel's Race
> Timer section and Companion page 2: see [Race-Timer](Race-Timer). Changes to shared
> resources affect everyone, and the sheet must stay shared. The details are in
> [Configuration & secrets](Configuration).
