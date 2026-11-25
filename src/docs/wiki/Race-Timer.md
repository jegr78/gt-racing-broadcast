# Race Timer

The remaining-race-time clock is part of the relay-served **HUD** overlay
(`http://127.0.0.1:8088/hud`): there is no separate timer browser source or
`/timer` page to add. The relay exposes the timer **state** as JSON at
`/timer/data` (which the HUD polls) and the director **controls** under
`/timer/*`. No external service is involved.

## How it works

The timer is **one absolute end timestamp** plus a duration, a visibility
flag, and, while paused, a frozen remainder. The browser source computes
`end − now` locally and ticks smoothly; the state lives in three places:

| Layer | Purpose |
|---|---|
| Relay memory + `runtime/timer.json` | survives a relay/OBS restart on this machine |
| Sheet tab **Timer** | survives a producer-machine handover: every relay reads the same anchor |
| Apps Script webhook | lets Director buttons write that Sheet tab |

Newest write wins (both sides carry an `Updated (UTC)` stamp), so a stale
Sheet never reverts a fresh local action. Machines must have normal NTP clock
sync (macOS/Windows default).

## Director controls

Available on the **director panel** (`http://<producer-tailscale-ip>:8088/panel`,
Race Timer section) and as **Companion buttons** (page 2, top row):

The buttons follow **stopwatch semantics**:

| Action | Endpoint | Effect |
|---|---|---|
| Start | `/timer/start` | starts (end = now + duration): or **resumes** a paused timer |
| Stop (pause) | `/timer/stop` | pauses: freezes the remaining time on screen |
| Reset | `/timer/reset` | back to "not started" (shows the full duration) |
| Show / Hide | `/timer/show`, `/timer/hide` | blanks/unblanks the overlay on every producer machine |
| Set duration | `/timer/set/6:00:00` | race length for the next start |
| Correct | `/timer/adjust/-60` | ± seconds on whatever is relevant: the running countdown, a paused remainder, or, before start, the duration |

**Automatic start.** Race Control or the director press **Race start** on the desk
once the cars stand on the grid ([Driver Telemetry](Driver-Telemetry)). The relay then
starts the timer by itself on the first race lap, after the formation lap when the
event has one. Without **Race start** nothing starts by itself, so lobby laps before
the press, qualifying and a relay restart mid-race never start the timer. Lobby laps
still running after the press count as the race and start it. The arm is used up by
that first lap. **Reset** of the timer does not arm it again, pressing **Race start**
again does. The automatic start acts only on a timer that never started. A running,
paused or finished timer stays as it is, so **Start** pressed earlier wins. A red flag,
its restart lobby and the release are no new start. The arm lives in the relay's memory
like the race: a relay restart between **Race start** and the first lap drops it, and
the desk shows the race as not armed. Until a Sheet-synced relay has read the Sheet
once, the automatic start waits. The desk shows "Race started: the timer starts once
the Timer tab is readable", and after 30 seconds the relay health names the wait. A
running timer another producer machine started then wins. The timer counts from the
relay tick that saw the first race lap, at most half a second after that lap's data
arrived. A new event, a race reset or a relay restart drops a waiting start; then
press **Start** by hand. Without driver telemetry nothing starts by itself.

**Race start** is locked so that a click mid-race does nothing. The desk greys it out
with the reason and the relay refuses it in three cases:

- The race timer has started. The reason says whether it runs, is paused or has
  finished, so a timer left over from the last event reads as finished. **Reset** of
  the timer releases this lock, and so does the director's unlock.
- A Sheet-synced relay has not read the Timer tab yet ("the race timer is not known
  yet"), so a timer another producer machine started cannot be missed. The Sheet
  answering releases it, or the director's unlock with **Timer keeps running**; the
  timer then needs **Start** by hand. **Reset** does not release it, and the relay
  refuses an unlock with **Timer starts fresh** there, because a reset stamped now
  could outrank another machine's running timer. The desk then offers only keep.
- A race armed by **Race start** counts. "A race is under way" holds even after a
  timer **Reset** and on a relay with `--no-timer`. A new event, the director's
  unlock or a race reset from the tailnet releases it. The relay saves this lock with
  the race actions under `runtime/<profile>/`, so a relay restart keeps it. A
  takeover machine does not have that file; there the timer lock from the Sheet holds.

When **Race start** is open but the race already counts (lobby laps, or a race that
never had a **Race start**), the first click shows a warning on the desk: "The race is
counting: Race start drops all counted laps, corrections, retirements and the fastest
lap. Only press it with the cars on the grid." A finished race gets the same warning
naming the race result. The confirming click proceeds.

The director can unlock **Race start** once at any time, for example for a restart
after a red flag or a server crash. **Unlock Race start** sits next to the locked
button on the director's driver desk, takes two clicks and is logged with the
person's identity (`tailnet` when the panel is opened on the tailnet). Race Control
cannot unlock. The director picks what the timer does:

- **Timer starts fresh** resets the timer now. After the next **Race start** it starts
  again with the full duration on the first race lap.
- **Timer keeps running** leaves the timer as it is. The next **Race start** passes the
  timer lock, rebuilds the race and never starts the timer, also when it never
  started. The desk then says that Race start does not touch the race timer.

Race Control then presses **Race start** as usual. The unlock holds until the new race
counts, so a false start before the first lap can be pressed again. Once the new
race counts the button locks again. The unlock is saved like the race lock, and a new
event or a race reset drops it.

Without a formation lap, on a layout whose grid lies past the finish line, GT7 shows
lap 1 on the grid and the relay counts the cars at once. There Race Control presses
**Race start** at the green flag, not before.

Display behaviour: before start → static full duration; running → countdown;
paused → frozen remaining time; at zero → blank (no `0:00:00` left standing);
hidden → blank.

## One-time setup: the write webhook (optional but recommended)

Without it the timer still works on the producer machine, but Director actions
cannot sync to the Sheet: a takeover machine would not pick up the running
countdown. The timer shares the sheet-write webhook with the panel's
Setup/Schedule/POV controls, set it up once per sheet: see
**[Sheet-Webhook](Sheet-Webhook)**.

## Producer handover

Nothing to do: the new machine's relay reads the Sheet anchor on startup and
shows the identical countdown. If the webhook was never set up, set the
remaining time manually after takeover: `/timer/set/<H:MM:SS>` + `/timer/start`
won't match mid-race: instead start, then `/timer/adjust/±seconds` until it
matches the official clock.
