# Async Director Panel sheet saves

Date: 2026-10-06. Status: implemented (#783).
Follows #778/#779 (banner recovery) and #780/#781 (serialized 30 s panel saves).

## Problem

Schedule, Qualifying and POV saves from the Director Panel are synchronous: the
relay answers only after the Apps Script webhook confirms the write. The webhook's
latency varies between 1.6 s and 30-40 s, measured on 2026-10-06 against two
leagues' scripts, with or without the relay in the path. A slow phase therefore
turns a save, and its RETRY, into a timeout error although the value usually lands
in the sheet anyway. During an event that reads as a fault and makes the director
doubt whether a link is in place.

Raising the timeout further is not a fix: the director would stare at a save for a
minute.

## Goal

A panel save is done for the director at once, and the relay uses the new value at
once (RELOAD, NEXT, stint and mode changes). The sheet catches up in the
background. Red only appears when someone has to act.

## Scope

In scope, made asynchronous:

- `POST /schedule/set` (`SetupControl.schedule_set`)
- `POST /qualifying/set` (`SetupControl.qualifying_set`)
- `POST /pov/set` (`SetupControl.pov_set`)
- `/submissions/approve`, which calls `schedule_set`/`qualifying_set` and so follows
  automatically.

Out of scope, unchanged:

- Crew writes (`crew_set`, `crew_delete`) stay synchronous with the #781 30 s
  attempt. Crew rows are positional, a delete must never be retried, and crew
  edits happen before an event, not live.
- Setup HUD fields and teams keep their existing async override pattern.
- The race timer push.
- No wiki screenshot refresh for this change (decided by the maintainer for this
  change only; the CLAUDE.md rule otherwise stands).

## Decisions

| Question | Decision |
|---|---|
| A save never lands after all attempts | Keep the value in the relay and re-push it automatically once the webhook answers again. |
| Where pending saves live | Relay memory only (approach A). A relay restart drops them; the sheet keeps its old value. |
| Red banner | Only for real errors (see below). A save that is only in the relay is not an error. |
| Conflicting direct sheet edit while a save is pending | The panel save wins; its push overwrites the cell, as a synchronous save does today. |

## Design

### PendingWrites

A small thread-safe store owned by `SetupControl`.

- Key: `(target, row)`. `target` is `schedule`, `qualifying` or `pov`; `row` is the
  physical sheet row for schedule/qualifying and a fixed key for POV.
- Value: the fields to write (`url`, `name`, `stint`; POV: `url`, `name`), the
  state, a monotonically increasing revision, and timestamps.
- States:
  - `saving`: queued or in flight.
  - `local`: every attempt failed transiently; the value lives only in the relay.
  - `confirmed`: the webhook confirmed the latest revision; the entry waits for the
    sheet CSV to show it.
- A second save for the same key merges field by field (newest wins) and bumps the
  revision. If the previous revision is in flight, the merged value is pushed right
  after it.

### Immediate answer

The endpoint validates exactly as today (webhook configured, row, URL via
`is_feed_source`/`is_channel`, vocabulary for streamer and stint). A validation
failure is still answered at once with `{"error": ...}`. A valid save is put into
`PendingWrites`, applied to the source (below), handed to the worker, and answered
with `{"ok": true, "row": n, "pending": true}` (POV: no `row`).

### The value is live in the relay at once

`ScheduleSource` (race, qualifying and POV instances) gains an overlay hook: after
every `refresh()` that replaced its rows, and on the save itself, it applies the
pending fields of its target via the existing `inject_row` semantics (a `None`
field stays untouched, an empty URL clears the link, a fully empty row is dropped).
Because RELOAD, NEXT, `set_stint`, `resync_to_stint`, `set_mode` and `pov_reload`
all go through `refresh()`, every one of them sees the pending value. The 30 s
poller does too.

POV currently confirms a save with `pov_source.refresh()`; it now applies the
overlay instead, like the schedule.

An entry leaves `PendingWrites` when it is `confirmed` and a `refresh()` returns the
row with the pushed fields (compared after the same normalisation the save used).
A safety limit drops a `confirmed` entry 120 s after the webhook confirmed it, so a
row can never stay pinned because the sheet stores a value differently.

### Background worker

One daemon thread per `SetupControl` drains a queue of keys in order, so writes
reach the script one at a time (the #781 property).

- Each push uses `push_webhook_retrying` with a 45 s attempt timeout and 3 attempts
  (constants `WEBHOOK_ASYNC_SAVE_TIMEOUT_S`, `WEBHOOK_ASYNC_SAVE_ATTEMPTS`). A save
  rewrites its own row, so a retry after a timeout that actually landed is
  harmless.
- On success: the entry becomes `confirmed` for the pushed revision (or stays
  `saving` if a newer revision arrived meanwhile and is queued).
- On any failure except an outdated script: the entry becomes `local`, keeps the
  last error text, and the push status is not touched (no red). This includes an
  explicit script error (`{"ok": false, "error": ...}`): the script's `catch`
  reports every exception that way, Google's own "Service Spreadsheets timed out"
  included, and a test save on 2026-10-06 got exactly such an answer after 34 s and
  went through seconds later. So it is treated as transient.
- On an outdated script (`WEBHOOK_OUTDATED_ERROR`): the entry becomes `local` and
  the push status becomes `failed` (red banner). It is not retried automatically.

### Catching up

`webhook_recovery_tick` (from #779) also runs while `PendingWrites` holds `local`
entries. When the probe answers, every `local` entry goes back to `saving` and is
re-queued, except one held by an outdated script, which needs a fix first.

`SetupControl.recover_push` keeps its #779 behaviour for the HUD/team pushes. The
push status turns `ok` only through successful pushes; a probe alone never clears a
status that a `local` save holds red.

A manual "SYNC NOW" in the panel re-queues one `local` entry at once
(`POST /schedule/sync`, `/qualifying/sync`, `/pov/sync`, body `{row}` where
applicable; director-gated like the save routes, so `src/scripts/console_policy.py`
lists them next to `schedule/set` and `qualifying/set`).

### Red banner rule

The panel banner (`push === "failed"`) appears only for:

- an outdated Apps Script,
- HUD field and team pushes, as today: their override expires after 60 s, so a
  failed push really loses the value (with #779 recovery).

Timeouts, network errors, Google HTTP errors and script error answers on a panel
save only produce the quiet `local` state. The last error text of a `local` row is
shown in the row's tooltip and, for the most recent one, in the HUD info line, so a
lasting configuration problem (e.g. a missing tab) stays visible.

### API additions

- `/schedule/data` rows and the qualifying data gain `"sync": "saving" | "local"`
  (absent when nothing is pending).
- `/status` POV block gains the same `sync` field.
- `/setup/data` gains `"unsynced": <count of local entries>` and
  `"unsynced_error": <last error text of a local entry, or null>`.
- Rows in the `local` state also carry `"sync_error"` (the last error text).

## Director Panel

Per row (Schedule, Qualifying, POV), from the `sync` field:

| State | Display |
|---|---|
| `saving` | Button "SYNCING…", subtle amber; inputs stay usable |
| `local` | Small amber badge "IN RELAY" (tooltip: not in the sheet yet, retried automatically, plus the last error text) and a "SYNC NOW" button; never red |
| confirmed (sync field gone after a save) | "SAVED ✓" for 4 s, then "SAVE" |
| validation error | Toast with the message as today; button back to "SAVE" (not "RETRY") |

Panel log lines:

- On save, at once: `Schedule row N saved: feed X picks it up on RELOAD X / NEXT`.
- Once, when a row turns `local`: `Schedule row N: sheet write delayed, kept in relay`.
- Once, when it lands: `Schedule row N now in sheet`.

The HUD info line shows `N changes not in the sheet yet, retried automatically`
while `unsynced > 0`, followed by `unsynced_error` when present.

Approving a commentator submission removes it from the list at once; the schedule
row then shows its own sync state. UI text stays English.

## Edge cases

- **Timed-out save that landed anyway:** the retry rewrites the same value; the
  sheet poll then confirms it.
- **CLEAR URL:** a save with an empty URL; it is pinned like any other value so the
  old link cannot reappear on RELOAD.
- **Mode switch:** pending entries are bound to their target tab and survive it.
- **Producer takeover on another machine:** saves that are only in the relay are
  not on the new machine. The panel counter shows it; the takeover section of the
  wiki states the mechanism (take over once the counter is 0). This is a statement
  of the mechanism, not a crew rule.
- **Relay stop with pending saves:** logged as a WARNING with the number of dropped
  saves.
- **No webhook configured:** rejected at once, as today.

## Testing

Stdlib tests, failing first, mostly in `tests/test_setup.py`:

- A save answers `pending` without waiting for a blocking fake webhook.
- A pending value survives `ScheduleSource.refresh()` (poller, RELOAD, NEXT paths)
  and leaves only when the sheet shows it, or after the 120 s safety limit once
  confirmed.
- Timeouts, network errors and an explicit `ok: false` script error lead to
  `local` without a red status; an outdated script leads to red and is not
  re-queued by recovery.
- The recovery tick re-queues `local` saves; the status turns `ok` only after the
  real push.
- Merging two saves for one row, including one in flight; one worker writes in
  order; CLEAR URL stays pinned.
- POV overlay replaces the post-save `refresh()`.
- HTTP endpoint tests for the immediate answer and the new `sync` fields;
  adjusted synchronous tests; the submission approve test.
- The panel row states verified rendered (`ui-visual-verification`).

## Docs

- Wiki `Director.md`: row states and the banner rule.
- Wiki `Sheet-Webhook.md`: timing for panel saves.
- Wiki `Relay-Mode.md` takeover section: the pending-saves counter.
- `src/relay/CLAUDE.md`: the sheet-controls paragraph.
