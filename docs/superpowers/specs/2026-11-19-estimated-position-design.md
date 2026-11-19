# Estimated position for a car without data (EST)

Status: approved in conversation on 2026-11-19.

## Why

A driver's sender can fail while the car keeps racing in the lobby. Today the car's
distance freezes, it shows "No data", it has no gap and it drops down the order as the
others drive on. A distance correction places it for a moment only. Race Control needs a
way to keep such a car in the order with a rough gap until its data returns.

## Decisions

- **Attach to a reference car.** Race Control (or the director) states "entry X runs
  about S seconds behind entry Y". S is a number of seconds, 0 or more; Y is another entry
  with data. Both desks offer it; the `race_control` role reaches it (race data, no
  program change).
- **How it moves.** While X is quiet, its shown distance is Y's shown distance S seconds
  ago, read from Y's distance history. X moves with Y and keeps its place in the order.
- **What it shows.** X's status is `est` (it wins over `no_data` and `dc`). Its gap to
  the leader is Y's gap plus S; its interval follows from the gaps of X and the car ahead.
  Every screen marks it as an estimate with the label **EST**: desks and Director Panel
  (pill), timing tower (the value column shows the gap with an EST tag), cockpit, public
  timing page, the recording, the frozen standings page and the CSV (status `est`).
- **Ending.** The estimate ends by itself the moment X sends data again; X continues from
  its real distance. Race Control can change S or Y at any time, or clear it.
- **Audit.** Setting, changing and clearing are written to the audit log with the
  person's identity, the entries and S.
- **Not persisted.** An estimate lives in memory; a relay restart drops it, and Race
  Control sets it again.
- **Limits.** No estimate under a red flag (the race is held) and none after the car or the
  reference took the flag; an estimate then stops moving and keeps its last distance.
  The reference must be a car with data; an estimate on a car that sends data has no
  effect until it goes quiet. An estimated car never takes the chequered flag by itself:
  Race Control finishes the classification with the existing tools.

## Not in scope

- No automatic estimate. Race Control always decides.
- No change to the DC events: a quiet car still raises its suspicion, and the desk keeps
  the Suspected / Confirmed state in the Events list.
