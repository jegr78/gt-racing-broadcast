# Who does what

> New crew? The [onboarding decks ↗](https://jegr78.github.io/gt-racing-broadcast/) are short visual walkthroughs, one per role.

Three groups make the show happen: the **commentators** who stream each stint, the
**producer** at the PC, and the **director** who chooses what viewers see. An optional
**Race Control** desk can watch along **read-only**, no broadcast actions.

```mermaid
flowchart LR
  subgraph P["At the PC: Producer"]
    P1["Set up and start the PC"]
    P2["Start and stop the broadcast"]
    P3["Join Discord for interviews"]
  end
  subgraph D["Remote: Director"]
    D1["Choose scenes and graphics"]
    D2["Press Feeds Next at driver changes"]
    D3["Cut to interviews<br/>+ broadcast audio"]
  end
  subgraph RC["Remote: Race Control (read-only)"]
    RC1["Watch program + schedule"]
    RC2["Monitor the race timer"]
    RC3["No broadcast actions"]
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
- Chooses scenes and graphics, presses **Feeds Next** at each driver change, and cuts
  to the interview segment (scene + broadcast audio). The interview conversation itself
  is moderated from inside the Discord voice channel by one of its participants,
  usually the final-stint streamer; the director can take that role but doesn't have to.
- Multiple directors can take turns; the producer can also direct locally.

## Race Control (remote, read-only)

An optional **monitoring desk** for someone who needs to keep an eye on the race
without touching the broadcast: e.g. a steward or a league official.

- **Read-only on the broadcast.** It triggers no scenes, graphics or feeds; the
  **director keeps full control** of the show.
- Sees a live **program preview**, the **streamer / stint schedule** (stream URLs are
  redacted: they never leave the producer's tailnet), and the **race timer**.
- **Posts to the crew chat** (its working channel, chat is two-way): feed the crew the
  race-control facts as they happen, e.g. a **drive-through / DSQ** for a team, a car's
  **rejoin time**, a team that **can't field a driver** for the next stint, or a team
  **retiring** from the race, and **direct the commentators** through it (e.g. "cut to
  car #7, P3"). Messages post under the operator's own name.
- Drives nothing else from a browser, like the director: open the personal
  [Console](Console) link the producer sends; the **Race Control** card appears for
  anyone flagged for it. No machine access.
- The role is **additive**: the same person can be a director *and* run Race Control.
  The **producer / league admin** flags people for it (you don't flag yourself) with the
  **Race Control** column on the Sheet's Crew tab (or the Control Center
  [crew editor](Control-Center#profile)). See the
  [Console launcher → Race Control](Console#race-control-read-only-monitoring-desk) for
  the desk in detail.

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
