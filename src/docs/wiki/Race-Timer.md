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
