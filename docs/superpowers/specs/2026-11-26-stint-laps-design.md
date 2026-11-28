# Stint laps: lap counts per stint lobby on the desk, the restart plan after a red flag, and the simulation

Status: agreed 2026-11-26.

## Problem

Every stint of an endurance event runs in its own GT7 lobby with a fixed number of
laps. Race Control sets those counts. The driver change is due at the end of the
second-to-last lap: the outgoing driver boxes after lap `Laps - 1` of the lobby
(see `2026-11-19-driver-change-timing-design.md`).

Today the relay reads only the last Schedule row's `Laps` cell (the final lobby), and
the desk's lap count applies to the final lobby only. The desk does not show how many
laps the lobby on air runs, and nothing helps Race Control after a red flag. The event
clock keeps running while the race is held and the restart is prepared. The restart
lobby therefore usually gets fewer laps, so that the stint keeps its planned length.

The event simulation drives no lap counts at all. Its stints end at scripted times, and
the race ends on the race timer with Final lap pressed by hand.

## What the league defines (not this design)

- The lap count of each stint and of each restart lobby. The desk shows numbers and a
  suggestion; Race Control decides and sets the GT7 lobby.
- The buffer for getting drivers, host and Race Control ready for a restart.

## Stint laps in the relay

- **Source.** Each Schedule row's `Laps` cell is the lap count of that stint's lobby.
  The Sheet is the starting point and the backup, as with the other Schedule fields.
- **Desk edit, cache first.** Race Control sets a stint's laps on the desk. The relay
  applies the value to its Schedule cache at once (`PendingSaves`, its own target
  `schedule_laps` apart from the row's URL, streamer and stint, one entry per row, re-applied after every refresh until the Sheet
  shows it). One `sheet-save` push then writes the cell through the Sheet webhook. A
  failed push stays local and is retried like every Schedule save. Nothing on the desk
  or in the race waits for the Sheet.
- **Webhook.** The Apps Script (v8) writes the Schedule `Laps` cell and refuses a tab
  without a `Laps` column. With an older script or no column the count stays in the
  relay cache, the desk says the Sheet was not updated, and the row's other saves still
  reach the Sheet.
- **Route.** `POST /drivers/race/stint-laps {row, laps}`, 1 to 999, or empty to clear.
  Race data, not a program change: open to `race_control`, audited with the identity.
- **The final lobby.** Every stint has its lap count and the final one needs no
  special case: the last row's count is the race's lap count. The desk's separate lap
  count field goes; the event setting stays the fallback for an empty cell. A count set
  while the final stint is on air applies at once.
- **Desk view.** The Race area lists the stints with their lap count (editable), and the
  live column shows the lobby on air as `Lobby lap 9 / 12`. Overlay, HUD and public
  timing do not change.

## Class lap counts per stint (2026-11-28, #1382)

In a multiclass event every class after the first has its own count in every stint
lobby, the row's `Laps <Class>` cell. It works like `Laps`: the desk's Stint laps table has
one column per class, a change is live in the relay at once (its own pending save per row
and class), and the Apps Script v9 writes the cell. A v8 script or a tab without that
column holds the count in the relay and says so on the desk. The relay passes the class
counts of the row on air to the race on every stint watch tick. Each class shows the crew
its lobby lap (the highest GT7 counter of its cars) against its count. **Take** in the
restart plan sets only the stint's overall count; Race Control sets the class counts of
the restart lobby in their columns.

A single lobby and a solo broadcast keep their class counts as an event setting next to
the lap count (`class_laps` in `driver-race.json`, one class per change, null clears),
set in the desk's Class laps row. Solo has no Schedule tab: it never reads the Schedule
for them, never queues a Sheet write and logs nothing about one.

## The restart plan after a red flag

While the red flag is out, from **RED FLAG** until **Restart released**, the desk shows a
live line under the race controls:

- **Behind plan:** the time since RED FLAG held the race, in minutes and seconds, and
  the same in laps of the lobby's average lap time.
- **Restart laps:** the stint's planned laps minus the laps the leader completed in the
  lobby minus the laps behind plan (rounded up), recomputed every second. A buffer
  field in minutes lets Race Control plan for the time until everyone is ready: the
  suggestion then counts that buffer as time behind plan too. The field starts empty;
  each event decides its buffer.
- **Take** puts the suggestion into the stint's lap count, the same route as a manual
  edit. Race Control can also type a number instead.

The average lap time is the mean of the leader's counted laps in the lobby before the
red flag, without lap 1 and without laps that the race left out. The leader sets the
event's pace and triggers the driver change and the final lap, so the plan follows it.

## The simulation (#1352)

The relay change (#1351) brings only the scenario's `event.stint_laps`, which fills the
Schedule's `Laps` column. The rest follows in #1352:


- The full scenario's Schedule tab gets a `Laps` cell for each stint.
- Race Control sets the stint laps on the desk before the race (a new `stint_laps`
  action), and the scenario checks the relay cache and the Sheet write.
- The simulated field runs each lobby to its count: every car boxes at the end of its
  lap `Laps - 1`. The leader's box entry still triggers the Splitscreen, and NEXT
  follows when every car is in the box or after at most five minutes.
- The red flag in stint 2: Race Control waits for the restart preparation, reads the
  restart plan, takes its suggestion, and the host opens the restart lobby with that
  count (`lobby` with `laps`).
- The final lobby finishes by laps (`finish_mode = "laps"`). Race Control signals Final
  lap with one click when the leader starts it. The race timer is sized so that it runs
  out during the last lap.

## Tests

- Relay: a desk edit shows in `/drivers/race` before the Sheet answers, survives a
  Schedule refresh, and is pushed once; an older script leaves it local.
- The final row's edited count finishes the race at once while the final stint is on air.
- The restart plan: time and laps behind plan and the suggestion for a fixed red flag
  duration and lap times, with and without the buffer.
- Simulation: the lobby ends at `Laps - 1` per car, the restart lobby runs the taken
  count, the race finishes by laps with the timer at zero in the last lap.
