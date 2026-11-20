# Commentator Cockpit

> New here? Start with the visual [Commentator onboarding deck ↗](https://jegr78.github.io/gt-racing-broadcast/commentator.html), then come back for the detail below.

![Commentator Cockpit: the on-air tally, live program monitor, race timer, stream-link submission, crew chat and the graphics browser](images/console-cockpit.png)

A **commentator-facing** page served by the relay, the counterpart to the Director
Panel, but for the **commentators**. Each commentator opens the Console and signs in
(with Discord) to get a self-contained cockpit:

- **Event title**, the round's free-text title (e.g. `GTEC - 2026 - Round 4 -
  Nürburgring 24h`) in the header, the same label the director and Discord see.
  Read-only here; the director sets it (see [Director](Director#event-title)).
- **Live program monitor**, the actual broadcast output (low-bandwidth JPEG stills).
  If the producer's machine falls behind, a yellow note under it says
  `Program is 12 s behind live. The delay is on the producer side, not your stream.`
  It appears only when the delay is past the relay's threshold. When the relay offers
  program audio, an **Audio** button and a volume slider under the picture play the
  on-air sound.
- **Broadcast chat**, a read-only mirror of the public YouTube or Twitch chat. **Write in
  YouTube chat** (or Twitch) opens the platform's chat in a pop-up window.
- **Tally**, a large **YOU ARE ON AIR** indicator plus an **UP NEXT · stint N · in
  X handovers** cue (and, while on air, their next own stint).
- **Crew chat**, the same chat as the Director Panel, attributed to the commentator.
- **Message director**: send the director a short note, by one tap on a preset (`Ready`,
  `Need 2 min`, `Come to me`, `Wrap me up?`) or as free text (see below).
- **Race Control** card: notes Race Control sends to the commentators (see below).
- **Race timer**, the live remaining-time clock.
- **Timing**, the driver race when drivers send telemetry: the same delayed data as the
  on-air driver dashboard, with every column at once and the brand logos. The card stays
  hidden while there is no data. See
  [Driver telemetry](Driver-Telemetry#on-air-and-in-the-cockpit).
- **Stint plan**, every stint with its streamer; your own stints are marked and the stint
  on air is highlighted.
- **Submit a stream link**: propose the stream URL for one of *their own* stints; it
  lands as a pending request the director approves before it can go on air (see below).
- **Graphics**: browse the league's broadcast graphics; when the league allows it, put
  one on air or request it (see below).
- **Notes**: the header button opens the league owner's notes and document links, when
  the Sheet has any ([Sheet template](Sheet-Template#event-notes-tab)).

You reach your cockpit from the **[Console](Console)** page: open the link your producer
shares and **sign in with Discord**; the cockpit appears as one of its role-gated cards.
(Leagues without Discord login send you a personal sign-in link instead, same result.)

## Director cues

Directors can send short text cues from the panel directly to your cockpit, a
text-only stand-in for an earpiece. Two levels:

- **Info**: appears as a brief auto-fading toast at the top of the cockpit and
  disappears after 30 seconds on its own.
- **Critical**, a large sticky banner that stays on screen until you click
  **Acknowledge**. Once you do, the director's panel shows a **✓ seen** stamp with
  the time.

Cues are **visual only: there is no sound or pop-up alert**. Keep the cockpit open on a
screen you can glance at so you don't miss a critical banner.

You only receive cues addressed to you by name or to **All commentators**: cues sent to
other individual commentators are never shown in your cockpit.

## Message director

The **Message director** card sends a short note the other way, from you to the director.
Tap a preset to send it at once, or type up to 200 characters and press **Send**. The
director sees it in the Director Panel with your name; the relay sets the name from your
sign-in, so nobody can send under another name.

## Race Control notes

When the league runs a [Race Control desk](Console#race-control-desk), its **Note to
commentator** messages land in the cockpit's **Race Control** card. They stay in the card
as a list with their time, and a new note also flashes briefly at the top of the page.

## Submit your stream link

Sending your watch link is the same task described in
[Who does what → commentators](Who-does-what#commentators--streamers): the **preferred
way is from the cockpit**; posting it in the crew **Discord** is the fallback when you
can't reach the cockpit.

From the cockpit you propose the URL for one of **your own** stints: pick the stint, paste
your YouTube/Twitch link, and submit. It is **not** live immediately, it lands as a
*pending request* your director approves (or rejects) first; once approved it goes on air
at the next handover/reload for that feed. You can only ever touch your own stints, never
anyone else's.

> Producer/admin side (approval flow, storage, security): see
> [Console & cockpit setup → stream-link submissions](Console-Setup#commentator-stream-link-submissions-director-approved).

## Graphics

The cockpit lists the league's **broadcast graphics**, the same still graphics the
director puts on air (standings, schedule, results, weather, …). Tap a tile to see the
graphic large in the page; a tile marked **ON AIR** is in the broadcast right now. Use
**Refresh** if the producer re-downloads the graphics during the event.

Whether you may put a graphic on air is the league's choice (`GRAPHICS_TAKE`). It is off
unless the league sets it, and then the tiles are for reference only. With it set you get
**Put on air** or **Request** on each graphic. Commentators never set the flag graphics.
The rules are in [Console → Graphics](Console#graphics).

## On a phone

On a narrow screen (820 px and below) a tab bar at the bottom shows one part of the
cockpit at a time: **Talk** (message the director, crew chat, Race Control notes),
**Program** (picture, audio, broadcast chat), **Graphics** and **Plan** (timing, stint
plan, stream-link submission). The on-air banner, the race timer and a critical director
cue stay at the top while you scroll. See [Console → On a phone](Console#on-a-phone).

## Setup & administration (producers)

Provisioning the cockpit secret, issuing and revoking per-person links, publishing the
public Funnel, and handling stream-link submissions are all on the
[Console & cockpit setup](Console-Setup) page. The access model and the security boundary
(identity vs authorization, the step-up secret, what stays tailnet-only) are in
[Remote access & the Funnel](Remote-access).
