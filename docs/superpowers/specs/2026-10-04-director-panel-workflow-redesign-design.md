# Director Panel: workflow redesign

Parent issue: **#723**. Replaces the single scrolling page of
`src/director/director-panel.html` with a fixed-frame layout: a left navigation by
topic, an always-visible live column, a workspace and an always-visible chat rail.
Covers all three profile kinds (endurance, solo POV, solo commentary).

## Context

Observed during the Interlagos 8h race (2026-10-03) on a ~1900 px window:

- The page caps at 1240 px, so the status pills stack and the sticky header grows
  to ~270 px and covers content (#723).
- Everything sits on one long page. The producer scrolls between preview, feeds,
  HUD, graphics and schedule, and **NEXT was missed at a handover**.
- The crew chat and the broadcast chat are small (at most 440 px high, often
  less) and the sticky header overlaps them while scrolling.
- Scene changes have four paths that behave differently:
  - PGM macros set scene, audio and feed visibility.
  - Raw Scn·Vis keys set the scene only, so a raw STINT leaves audio wrong.
  - NEXT cuts server-side and ignores the armed transition.
  - Source toggles act on OBS directly.

  Some scenes are reachable only one way (INTRO, OUTRO, TRAILER, INTERMISSION
  only as macros in endurance; solo has no macros at all). RED FLAG and STBY
  COVER toggle the same source.
- The panel knows only "solo or not". It cannot tell solo POV from solo
  commentary because `/status` does not carry the template.

## Goals

- No page scroll for the controls a live broadcast needs. Everything that acts on
  air within seconds is always visible.
- Both chats always visible and tall.
- One way per scene, and that way sets scene and audio together.
- A handover the panel walks the producer through: ARM, wait for serving, SPLIT,
  NEXT.
- Use the width of a 1920 px screen; degrade in a defined order on smaller ones.

## Non-goals

- No shared shell for the cockpit, race control or console pages. The building
  blocks are cut so that this stays possible later.
- No change to who may call which endpoint (`console_policy.py`), except hiding a
  control the role cannot use.
- No fixed broadcast procedure beyond the handover steps documented in
  `src/docs/wiki/Director.md` ("At a driver change"). The navigation is grouped by
  topic, not by phase: graphics, HUD and stream controls are used at any time.

## Layout

```
+----------------------------------------------------------------------------------+
| DIRECTOR CONSOLE  [event]   HEALTH  ON AIR  A  B  POV  TIMER      OBS RELAY Help |  one row, 56 px
+-----------+---------------+--------------------------------------+---------------+
| ON AIR    | PROGRAM       | NEXT STEP: <handover guidance>       | CREW CHAT     |
|  Handover | [A] [B] [POV] +--------------------------------------+               |
|  Graphics | on air: STINT |                                      |               |
|  HUD      | scene macros  |   workspace of the selected area     |  (drag)       |
|  Cues     | CUT | FADE    |                                      +---------------+
|  Audio    | ARM -> stint  |                                      | BROADCAST     |
| OPERATIONS| NEXT  RED FLAG|                                      | CHAT          |
|  Broadcast|               |                                      |               |
|  Schedule |               |                                      |               |
|  Setup    |               |                                      |               |
|  Trouble- |               |                                      |               |
|   shoot   |               |                                      |               |
+-----------+---------------+--------------------------------------+---------------+
   220 px       360 px               remaining (~900 px at 1920)          380 px
```

- **Header:** one row at every desktop width. Brand, event title, status pills,
  OBS/relay LEDs, keyboard-shortcut state, version, notes and help. Pills never
  wrap into a second row; at narrow widths the least important pills collapse
  into an overflow first.
- **Navigation:** grouped by topic, no order implied. Each entry shows a state
  dot (neutral, ok, live, warning) and a one-line reason. No "done" marks. The
  selected area is remembered per browser. The panel never switches the area by
  itself; a problem shows as a warning dot plus a hint in the live column.
- **Live column:** fixed width, never shrinks, never scrolls away.
- **Workspace:** the selected area, scrolls on its own if it must.
- **Chat rail:** crew chat above broadcast chat, divider draggable, split
  remembered per browser.

### Navigation areas

| Group | Area | Contents |
|---|---|---|
| On air | **Handover** (default, endurance) | On-air and up-next cards with the ARM state; the next three stints with link state and APPROVE for a pending submission |
| On air | **Graphics** (default, solo) | In-race graphics, flag graphic (one active), pre-race graphics, grid rows |
| On air | **HUD** | Session, stint label, streamer, P1 to P3, race-control flag and message, CLEAR RC, timer |
| On air | **Cues** | Composer with target and presets, sent cues and replies. In solo shown only if a crew member has cockpit access |
| On air | **Audio** | Mixer per input with level and mute; the macros set these too |
| Operations | **Broadcast** | Stream on/off or part start/end with the typed confirmation, substitution note, event title |
| Operations | **Schedule** | All stints with links, pending submissions, qualifying row, POV link. Endurance only except POV |
| Operations | **Setup** | Preflight checks, race/qualifying mode, graphics files (browse, refresh) |
| Operations | **Troubleshoot** | Per feed: quality tier, RELOAD, RESET -> LIVE, STOP; emergency feed switch; raw scenes; OBS refresh, resync, rebuild-guard re-arm, POV reload/stop, FEEDS -> STINT |

### Live column per kind

| | Endurance | Solo POV | Solo commentary |
|---|---|---|---|
| Monitor | Program | Program | Program |
| Tiles | Feed A, Feed B, POV | Capture, Webcam, POV | Capture, Webcam, Tyres/Fuel |
| Extra | | TELEMETRY toggle | |
| Macros | 8 (below) | 10 (below) | 10 (below) |
| Handover | ARM, NEXT | none | none |
| Always | Transition CUT/FADE with duration, RED FLAG | same | same |

Solo has no NEXT and no "next step" strip; RED FLAG takes the freed space.

### Responsive behaviour

| Width | Change |
|---|---|
| >= 1600 px | Full layout |
| < 1600 px | Navigation collapses to a 72 px rail with state dots (also collapsible by hand at any width, remembered per browser) |
| < 1280 px | Chat rail becomes a drawer opened from the header, with an unread count |
| < 900 px | Single column: live column, workspace, chats |

The live column is the last thing to give up space.

## Handover (endurance)

The panel follows the documented procedure in `Director.md` ("At a driver
change"):

1. **ARM -> stint N** arms the off-air feed with the next stint's link. The panel
   picks the off-air feed; the producer never chooses A or B. A second press
   cancels (stops that feed).
2. While the feed starts, NEXT stays dimmed with "arm stint N first" or "starting".
3. When the feed reports **serving**, NEXT turns green. "Next step" says: SPLIT
   covers the swap, then NEXT.
4. NEXT hands over with the armed transition (decision 5) and the cycle starts
   again at ARM.

With `RACECAST_MANUAL_FEED_ARM=0` the ARM key is hidden, NEXT turns green as soon
as the off-air feed is serving, and "next step" skips the ARM step.

**"Next step" and link warnings cover the next stint only.** The relay knows the
order of stints, not their times. A missing link or an unapproved submission for
the next stint is a warning (Schedule dot and "next step"); anything further out
stays neutral so the dot does not glow all event.

## Scene macros

Every scene is a macro that sets scene and audio together. Raw scene keys
(scene only) move to Troubleshoot with a warning that audio stays as it is.

### Endurance

There is one **STINT** macro. It cuts to the Stint scene with the feed the relay
holds as on air; NEXT decides which feed that is. Audio as the current STINT A/B
macros set it for the on-air feed.

| Macro | Scene | Audio |
|---|---|---|
| STINT | Stint | on-air feed (and the local-stint mic) on, the other feed and Discord off |
| SPLIT | Splitscreen | as today (`/obs/split`); sets race control "Driver Swaps" |
| INTERVIEW | Interview | Discord on, Feed A and B off |
| STANDBY, INTRO, OUTRO, TRAILER | same name | Feed A, Feed B and Discord off |
| INTERMISSION | Intermission | as today (scene only) |

Manual Feed A / Feed B on air is an emergency control in Troubleshoot ("Emergency
feed switch", with confirmation), not a scene key.

### Solo

New: solo gets macros. The table is the starting point agreed for this redesign;
correct it if a league runs solo differently.

| Macro | Game | Mic | Discord |
|---|---|---|---|
| PROGRAM | on | on | off |
| INTERVIEW | off | on | on |
| STANDBY, INTERMISSION | off | on | off |
| INTRO, OUTRO, TRAILER | off | off | off |
| WEBCAM, CAPTURE (full frame) | as PROGRAM | as PROGRAM | off |
| DISCORD | off | on | on (added with #729 so no solo scene is reachable only as a raw key) |

### Removed duplicates

- STBY COVER in graphics is removed; RED FLAG toggles the same source and also
  sets the race-control text.
- The "TX: FADE" chip is removed; the transition lives in the live column.
- FEED A / FEED B visibility keys are removed from the live view (Troubleshoot
  keeps the emergency switch).

## Keyboard shortcuts

Off by default, switched on per browser from the header. Only NEXT and the scene
macros get keys (N, 1 to 9), and each needs a confirming second press within a
short window. No global shortcut acts on a single key press.

## Server changes

1. **NEXT honours the armed transition.** Today `next_auto` cuts to Stint with a
   hard cut regardless of the panel's transition. The panel passes the transition
   and duration; the relay uses them for the program cut.
2. **The emergency feed switch updates the relay.** Today `/obs/stint` changes OBS
   only, so the relay keeps the old feed as on air and status, HUD streamer and
   the next NEXT target disagree with the picture. The switch also sets the
   relay's on-air feed.
3. **`/status` carries the template** (`pov` or `commentary`) for a solo relay, so
   the panel can tell the solo kinds apart.
4. **Handover data:** `/status` (or `/schedule/data`) exposes, for the next stint,
   whether a link is present, whether a submission is pending and which feed is
   off air, so "next step" and the Schedule dot need no client-side guessing.
5. **Solo macros** need the audio inputs named in the solo table; the relay-side
   macro path (as `/obs/stint` and `/obs/split` are for endurance) or plain
   `/obs/scene` plus `/obs/audio` calls, decided in the increment.

## Roles

`FEEDS -> STINT` needs producer rights with the step-up header, which the panel
never sends. Over `/console/panel` the control is hidden instead of failing with
403.

## Increments

Each increment ships on its own and keeps the panel usable between two races.
The old page is **replaced**, not kept alongside; increment 1 therefore must
carry every control of the current panel (parity checklist below).

1. **Shell** (includes #723): header, navigation, live column with ARM and NEXT,
   chat rail, all areas wired to the existing endpoints, responsive rules,
   `FEEDS -> STINT` hidden over `/console`. Wiki screenshots and `Director.md`
   rewritten.
2. **Macros:** one STINT, all scenes as macros, solo macros with audio, raw scenes
   and the emergency switch in Troubleshoot, duplicates removed.
3. **Server:** decisions 1 to 4 of "Server changes", and the panel's "next step"
   and Schedule warnings on top of them.
4. **Keyboard shortcuts.**

## Parity checklist (increment 1)

Every control of today's panel must have a place. Ids from the current file; the
new location in brackets.

- Banners: relay, sheet sync, timer sync, cookies, OBS, feed down, desync with
  resync [header pills plus a banner row above the workspace].
- Preview: program monitor, feed tiles with pause and level, program audio
  [live column].
- `#obsStreamBtn`, `#partActionBtn` with `#partModal` [Broadcast].
- `#pgmBus` macros and RED FLAG [live column].
- HUD: `#setupRow`, `#teamRow` with batch and apply, `#condRow`, CLEAR RC [HUD].
- Feeds: NEXT [live], RELOAD ALL/A/B, POV RELOAD/STOP, FEEDS -> STINT, RESET A/B
  -> LIVE, RE-ARM [Troubleshoot], ARM/STOP [live column ARM, Troubleshoot STOP],
  quality tiers and channel preview [Troubleshoot].
- Scn·Vis: scene keys [live macros, raw in Troubleshoot], FEED A/B visibility
  [emergency switch], POV toggle [live column tile], TELEMETRY [live column, solo
  POV].
- Timer: all keys [HUD].
- Action log `#log` [below the workspace, collapsible].
- Cues: target, level, text, presets, recent, replies [Cues].
- Graphics, pre-race, grid, flag graphics [Graphics].
- Audio sliders, 0 dB, mute [Audio].
- Utilities: transition CUT/FADE/STINGER with duration [live column], OBS REFRESH
  [Troubleshoot].
- Schedule: mode switch [Setup], race rows with save/clear/add, qualifying row,
  POV name and URL [Schedule].
- Pending submissions [Schedule, plus APPROVE in Handover for the next stint].
- Substitution note [Broadcast].
- Crew chat, broadcast chat with write link and refresh [chat rail].
- Graphics file browser [Setup].
- Event title editing, notes modal, help [header, Broadcast].

A test keeps the existing "no dropped control ids" guard: every control id of the
current panel either exists in the new one or is listed as intentionally removed
(STBY COVER, TX chip, FEED A/B visibility, raw scene bus, ARM A/B as separate
keys).

## Testing

- `tests/test_director_panel.py` pins the DOM order and CSS strings of the old
  page; it is rewritten for the new structure, keeping its intent: control
  parity, the scene and source names checked against the OBS collections, and
  the solo hide set.
- Server changes get relay tests: NEXT with a transition, the emergency switch
  moving the relay's on-air feed, `template` in `/status`, the next-stint data.
- Each increment is rendered and looked at (`ui-visual-verification`) at 1920,
  1440, 1280 and 390 px, for endurance, solo POV and solo commentary.
- `director-panel.png` and `director-panel-solo.png` are recaptured in the same
  change (`wiki-screenshots`).
