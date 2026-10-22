# Race Engineer: best-lap reference, shared pin, fuel time left

Date: 2026-10-22 · Surface: Race Engineer desk (`src/console/race-engineer.html`,
`src/scripts/race_engineer_api.py`, `src/scripts/console_policy.py`, relay routing),
wiki `src/docs/wiki/Race-Engineer.md`.

## Intent

The Race Engineer watches the live delta while the driver laps. Today, with
**Follow live**, reference A is always the lap just completed, and a manually chosen A
only holds until the next lap starts. That makes the delta jump around and ties it to
the previous lap, which is rarely the lap the engineer wants to measure against.

The producer wants:

1. A **stable, meaningful default reference**: the best lap so far.
2. A way for the engineer to **pin** a lap as the shared reference so it is not
   replaced by later best laps.
3. Still the freedom to look at **any past lap** as a reference, privately.
4. A **fuel time-left** estimate next to the fuel laps-left estimate: how long the
   fuel would last at the current pace.
5. **"N/A"** instead of **"Unavailable"** for missing values on the desk.

## Goals

- Reference A resolves in this precedence (highest first):
  1. **Personal pick**: a lap the engineer selected in Lap A. Browser-local, affects
     only their own view. It holds until the driver sets a **new best lap** in the
     current session (or a pin change, see below), then the view falls back to (2)/(3).
  2. **Shared pin**: a lap pinned for **all** engineers of this relay. Stored
     server-side, survives page reloads, and is not replaced by later best laps. Only an
     explicit **Unpin** (by any engineer) removes it. The pin **ends with its session**:
     when the live session changes, the pin no longer applies and is cleared.
  3. **Best lap**: the fastest comparison-eligible completed lap in the same pool the
     **Best completed** tile already uses (same session, same car, same track; out, pit,
     first-of-stint and incomplete laps excluded). A new best automatically becomes A.
- The delta tile names which reference is active, e.g. `Against best · Lap 23`,
  `Against pinned · Lap 17`, `Against your pick · Lap 9`, replacing today's
  `Against A · through 430 m` wording (the distance stays, e.g.
  `Against pinned · Lap 17 · through 430 m`).
- Pin / Unpin controls sit next to the Lap A selector. Pinning pins the lap currently
  shown as A. Every connected desk reflects a pin or unpin on its next snapshot poll
  (desk already polls about twice per second).
- Under `≈ 0.6 laps left` in the **Avg fuel · L/lap** tile, a second line
  `≈ 0:43 time left`.
- Every user-visible `Unavailable` on the Race Engineer desk becomes `N/A`, including
  the wiki page text and the desk screenshot(s).

## Non-goals

- No change to the producer-shared **Reference source** mechanism: selecting a shared
  reference source stays a personal view choice, as today.
- No change to lap B / Follow live semantics for B: Follow live keeps B on the
  current lap.
- No "N/A" rename in other consoles (Telemetry workspace, cockpit, race control,
  health monitor, HUD).
- No race-time / stint-time remaining; "time left" is fuel endurance only.
- No pin history, no multiple pins, no per-driver pins.

## Anti-goals

- The delta must never silently compare against a reference other than the one the
  label names.
- A pin must never outlive its session and leak into a different session, car or track.
- No new unauthenticated write path: the pin endpoint goes through the console auth
  like every `/console` route.
- Time left must not show a guessed value: if laps left or the average lap time is
  missing, it reads `N/A`.

## Details

### Fuel time left

- `time left = laps left × average lap time`.
- **Average lap time** uses the same laps as **Avg fuel · L/lap**: completed,
  comparison-eligible laps of the live session (out, pit, partial laps excluded).
  A lap without a time is left out, never counted as zero.
- Format `m:ss` below one hour, `h:mm:ss` from one hour on. Prefix `≈`.
- Missing input → `N/A` (e.g. `Fuel level unavailable` cases keep their existing
  explanatory note, the time line reads `N/A`).

### Shared pin

- One pin per relay at a time, identified by lap id plus the session it belongs to.
- Who may set/clear it: everyone with Race Engineer access (Race Engineer crew
  checkbox; Producers inherit). Last write wins.
- *Builder's call / my call:* the snapshot (or access) response carries the pin, so
  existing polling distributes it; a new authenticated `POST` under
  `/console/race-engineer/pin` (set, body: lap id) and a clear action set/clear it.
  `console_policy.py` currently only allows `GET` for race-engineer routes, so the
  policy needs an explicit `POST` rule with the `RACE_ENGINEER` requirement.
- Only a lap from the **current live session** and comparison-eligible may be pinned.
  Shared-reference laps (`r:` ids) cannot be pinned.
- Persistence: in relay memory is enough to survive page reloads. *My call:* a relay
  restart starts a new live generation (new session ids), so the pin ending there is
  consistent with "ends with its session"; no disk persistence.
- The pin is cleared when the live session id changes (also on replay start/stop,
  which already starts a separate pool).
- *My call:* the pin response records who pinned it (console subject display name),
  shown as a small note, e.g. `Pinned by Alex`.

### Personal pick vs. pin

- A personal pick overrides the pin in that browser only.
- A personal pick is dropped when a new best lap appears in the session, and also
  when the pin changes (set or cleared), so a fresh shared decision is visible to
  everyone. *My call.*
- Choosing "No reference" / the default entry in Lap A clears the personal pick.

- *My call:* the personal pick lives in page memory only, as today's selections do; a
  page reload drops it and the view shows the pin or the best lap.

### Producer-shared Reference source

- *My call:* a selected shared Reference source is its own layer **above** the
  personal pick and the pin, exactly as today: while a source is selected, A comes from
  that source's laps, Follow live does not change A, and neither a new best lap nor a
  pin change drops it. Clearing the source returns to the precedence above.
- Delta label for it: `Against <source label> · Lap N`.

### Lap B outside the live session

- *My call:* the pin and the live best apply only while lap B belongs to the live
  session. When the engineer inspects a B from another (older) session, the default A
  is the best eligible lap of B's own session (today's behaviour), and the delta label
  says `Against best · Lap N` for that session. A new best in the live session does not
  drop a personal pick made in another session.

### Retention of the pinned and best lap

- The live store keeps only `DETAILS_MAX = 16` detailed laps and evicts the oldest
  lap of the current session first (`race_engineer_live.py`). *My call:* the pinned
  lap and the current best lap of the live session are **protected from eviction**
  (eviction skips them), so the delta keeps working for a pin set early in a long
  stint. Tests cover this.
- If a referenced lap's detail is nevertheless unavailable, the delta shows `N/A`
  and the note says why (e.g. `Pinned lap detail no longer kept`). It never falls
  back silently to another lap under the same label.

### Follow live

- Follow live keeps updating B to the live lap. It no longer sets A to the previous
  lap; A follows the precedence above. The current automatic-previous-lap logic
  (`state.autoA`, `AUTO_A_WAIT_MS`) is replaced.
- When the active A is not comparable with B (different car/track/cohort), the
  existing "not comparable" messages apply unchanged.

### "N/A"

- Producer decision: **every value** on the desk that is missing shows `N/A`, no
  exceptions: headline and live tiles, support line (fuel, theoretical best, tyres),
  table cells, section cards, A/B time strings (`A N/A · B 1:11.544`), shift-point
  values and the delta tile. Value-like notes under a tile that only report a missing
  value (e.g. `Fuel level unavailable` under the fuel tile) become `N/A` as well.
  Status sentences that explain a state rather than stand in for a value (e.g.
  `Preparing shared reference`, `Selected laps are outside the comparable pool`)
  stay prose.
- Update tests that assert the placeholder (e.g. `tests/test_engineer_live_trend.py`)
  and the wiki text "say **Unavailable**".

## Constraints

- Repo rules apply: edit only under `src/`, English only, stdlib only, TDD, no
  machine paths in tests.
- Changed UI surface: refresh the Race Engineer wiki screenshot(s)
  (`console-race-engineer-*.png`) in the same change (wiki-screenshots skill), and run
  the ui-visual-verification skill.
- Console security boundaries in `CLAUDE.md` / `src/relay/CLAUDE.md` hold: the pin
  endpoint is authenticated, carries no stream URLs, is not destructive beyond the pin
  itself.

## Left to the builder

- Exact endpoint shape (single `POST` with action vs. `POST`/`DELETE`), as long as it
  passes `console_policy` and the Funnel/auth rules.
- Exact placement and styling of the Pin/Unpin button and the "pinned by" note.
- Where in `race_engineer_api.py` / the relay the pin state lives.
