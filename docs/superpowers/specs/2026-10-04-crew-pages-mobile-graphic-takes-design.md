# Commentator Cockpit and Race Control desk: mobile layout and graphic takes

Parent issue: **#743**. Runs beside the Director Panel redesign (#723) and shares only the relay and the
graphic definitions with it. Two outcomes:

1. Commentators and Race Control can put the broadcast stills on air themselves,
   within limits the league and the director set.
2. `src/cockpit/cockpit.html` and `src/racecontrol/race-control.html` work on a
   phone as well as on a desktop. Neither is the primary device: every crew member
   uses what they have.

## Context

Audit on 2026-10-04 with the demo profile and `tools/obs-sim.py` at 390, 768 and
1280 px:

- Below 820 px the cockpit's crew chat and stream-link submission collapsed to
  2 px, and the header pushed the page to 476 px in a 390 px viewport. Fixed
  separately in #742 (`render_cockpit_phone` in `tools/e2e.py` guards it).
- Touch targets are 17 to 34 px high (audio, refresh, preset chips, chat send).
- On a phone the Race Control desk is 2342 px long. The full schedule (580 px) sits
  above the graphics and above "Note to commentator", its main action.
- The Race Control schedule marks both loaded stints "ON AIR" (A and B), while only
  one is on air.
- Both pages list the graphics as names only, in a 260 px scroll box inside the
  page scroll. A tap opens the image in a new tab, which leaves the page on a phone.
- Desktop at 1280 px works on both pages.

Today the graphics browser is view-only on both pages (`GET /graphics`, Sheet
assets minus internal ones and blank placeholders). Putting a graphic on air is
director-only: `POST /obs/source`, director-gated, and the graphic buses exist only
in the panel's JavaScript (`CONFIG.graphics`, `graphicsPreRace`, `graphicsGrid`,
plus the solo set). The flag graphics already have a relay-side store
(`flag_graphic.py`, `/obs/flag/*`, one active, shown in Stint and Splitscreen,
Program in solo). Race Control is documented as read-only (#244); this work
changes that on purpose.

## Goals

- A league chooses whether its crew may put graphics on air, and how.
- The director keeps the last word and sees every take.
- Cockpit and Race Control desk are usable one-handed on a phone without losing
  any function, and keep today's multi-column layout on a desktop.

## Non-goals

- No change to how the director toggles graphics. Several director graphics at
  once stay possible.
- No Companion changes.
- No auto-hide timer for graphics. Revisit if the crew misses it.
- No new OBS sources or scenes. A graphic shows only where its source already is.

## Take mode

`GRAPHICS_TAKE` in `profiles/<name>/profile.env`, three values:

| Value | Effect |
|---|---|
| `off` | Today's behaviour: view only. Default when the key is missing. |
| `request` | Commentator and Race Control send a request; the director takes or declines it. Race Control flags go on air directly. |
| `direct` | Commentator and Race Control put graphics on air themselves. |

A missing key means `off`, so updating racecast never widens anyone's rights.
`request` becomes selectable only once the panel can show requests (increment 4).

The director can override the mode live, per role (commentator, Race Control),
from the panel. The override lives in the relay and resets to the league value on
relay start. Switching the mode drops open requests.

## Graphic definitions

`src/scripts/graphic_takes.py` holds one definition per collection (endurance,
solo): OBS source (also the Sheet label), scenes, group (editorial or flag), roles.
The editorial list derives from `obs_ws.GRAPHIC_SOURCES` (the Companion graphic
route, #706) minus the director-only sources, and the flags come from
`flag_graphic.FLAG_GRAPHIC_SOURCES`, so no third list exists. A Sheet asset without
an entry stays view-only.

Rights:

- Editorial graphics (standings, schedule, results, weather, weekend and race info,
  next event, starting grid, post-race interviews): commentator and Race Control.
- Flag graphics: Race Control only, through the existing flag store, so one flag
  stays active and the persisted value survives a relay restart.
- Director only: Standby Cover (the panel's RED FLAG) and the grid rows (the
  director's grid sequence). The HUD groups are not graphics.

## Take rules

- From the cockpit and the desk, exactly one editorial graphic is active. A new
  take hides the previous crew take. It never hides a graphic the director set.
- A graphic stays until someone hides it. Anyone with take rights for it, and the
  director, can hide it.
- A take is refused while none of the graphic's scenes is the program scene. The
  button is disabled with the reason ("Stint is not on air"). A request may still be
  sent; the director decides.
- Every take, hide and request posts a system line to the crew chat ("RC 2 put Flag
  Yellow on air") and a relay log entry.
- Rate limit: one take per person per 2 s. No step-up secret. The allowlist, the
  role check and the director's live override bound what a leaked token can do.

Endpoints. The crew routes live under `/cockpit/`, where the relay already resolves
the caller from the token at the tailnet root and under `/console`, because every
`/obs/*` route is director-only in `console_policy`:

- `GET /cockpit/graphic-takes`: mode, program scene and every graphic with its
  state, who set it and whether the caller may take it.
- `POST /cockpit/graphic-takes` `{source, on}`: a take or hide, in direct mode or
  for a Race Control flag.
- `GET /obs/graphics`: the same list without per-caller rights, for the panel (also
  token-less on the tailnet `/panel`).
- Increment 4 adds `POST /cockpit/graphic-takes/request` `{source}` and the
  director-only `POST /obs/graphics/request/<id>` `{action}` and
  `POST /obs/graphics/mode` `{role, mode}`.

## Requests (director side)

Requests appear in the panel's always-visible live column, not in a topic, so they
are seen while another topic is open: "Standings, requested by Comms 1", with Take
and Decline. A request expires after 60 s. Two requests for the same graphic merge.
The requester sees its state: waiting, on air, declined, expired.

## Page frame (cockpit and desk)

One shared frame, served once by the relay as `/console/static/crew.js` and
`crew.css` and used by both pages:

- **Live strip**, always visible, sticky: tally, race timer, critical cue.
- **Desktop (above 820 px):** today's multi-column grid.
- **Phone and portrait tablet (820 px and below):** a bottom tab bar instead of one
  long page. Tabs per role:
  - Commentator: Talk (message director, crew chat, Race Control notes), Program
    (picture, audio, broadcast chat), Graphics, Plan (stint plan, link submission).
  - Race Control: Act (note to commentator, flags), Graphics, Chat (crew,
    broadcast), Program, Schedule.
- Touch targets at least 44 px. No horizontal page scroll at 360 px.

## Graphic card

A grid of tiles with a thumbnail. A tap opens a large preview in the page, not in a
new tab. Each tile shows its state as a frame (on air, requested) and, where the
caller may act, an "On air" or "Request" button depending on the mode. Flags render
as their own row on the desk with one active.

## Race Control schedule

"On air" marks only the feed that is on air. The other loaded stint reads "Next"
with its feed letter.

## Increments

1. **Relay (#744):** graphic definitions per collection, `GET /cockpit/graphic-takes` and `GET /obs/graphics` with state,
   take endpoint with the take rules, `GRAPHICS_TAKE` (`off`, `direct`), crew-chat
   lines, rate limit. Tests for the rules and the gates.
2. **Shared frame (#745):** `crew.js`/`crew.css`, live strip, bottom tabs, touch targets,
   for cockpit and desk; the Race Control schedule fix. Wiki screenshots
   `console-cockpit.png` and `console-race-control.png`.
3. **Graphic card (#746)** with thumbnails, preview and take, on both pages.
4. **Panel (#747)**, after #728 has merged: graphic buses from `GET /obs/graphics`,
   request queue in the live column, live mode override per role; `request`
   becomes a valid `GRAPHICS_TAKE` value.

## Testing

- Unit: take rules (one crew editorial graphic, director graphics untouched, scene
  gate, rate limit), mode resolution (missing key, override, reset on start),
  request expiry and merge, role gates over `/console`.
- e2e: `render_cockpit_phone` style rendered checks for both pages at 390 px (no
  horizontal scroll, every tab reachable). The take round trip runs in the unit
  suite with `_obs_ws` swapped for a fake, since `tools/obs-sim.py` only answers
  the program screenshot calls.
- Visual: both pages at 390, 768 and 1280 px through the `ui-visual-verification`
  skill.
