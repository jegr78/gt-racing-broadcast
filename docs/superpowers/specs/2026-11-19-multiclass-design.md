# Multiclass events

Status: implemented. Changes against the draft below:

- Class mode needs at least two named classes. A blank `Class` cell never counts toward
  them; with class mode on, blank rows form the "" group at the end, labelled
  "Unclassed" on the public page, the desks, the cockpit and the snapshot page, and
  without a label on the OBS tower.
- The Schedule's `Laps` belongs to the first named class in class order, not to "the
  fastest class" as such.
- A tower page stays for one full GAP and INT cycle (30 s), not 15 s, so every class
  shows both. When the tower pages, the fastest-lap box shows the page's class; it
  cycles every 15 s only when all classes fit on one page.
- A class lap count shows only where the overall count shows: in an endurance event once
  the final lobby's cars run on its counter, so `Laps <Class>` belongs to the final
  lobby's row. A count Race Control cleared stays cleared for that stint.
- Race Control can void or set one class's fastest lap without touching the overall one.
- The CSV adds `class` and `class_pos` only for a recording with class-mode samples.
- The driver change timing hint is not part of this implementation; its spec is still a
  draft.

## Problem

Some leagues run several car classes in one lobby, for example Hypercars and GT3s.
Positions, gaps and lap counts only mean something within a class: a GT3 two laps
behind the overall leader can still lead its class. Today every screen ranks the whole
lobby as one field.

## How a multiclass event runs (league practice, not this design)

- All classes share one lobby. GT7 ends the lobby for everyone once the overall leader
  finishes; every other car finishes when it next crosses the line.
- The lobby's lap count (the Schedule's `Laps` cell) belongs to the fastest class. The
  slower classes drive fewer laps in the same time. Race Control fixes their counts near
  the end of the lobby and announces them, e.g. Hypercars 60 laps with the driver change
  stop on lap 59, GT3 54 laps with the stop on lap 53.

## Class of an entry

- New optional columns in the Configuration tab: `Class` (e.g. `HYPERCAR`, `GT3`) and
  `Class Color`, read by `parse_config_roster` next to `Cup Team` (header-located,
  colour through `sanitize_css_color`).
- Without a `Class` column, or with one class only, every screen stays exactly as
  today. Everything below applies only with two or more classes.
- Class order on every screen: the class of the overall leader first, then by the best
  position each class holds. A row without a class forms its own group at the end.

## Standings per class

- `race_distance` keeps one overall order (it decides who finishes when, as GT7 does).
  Each standings row gains `class`, `class_pos`, and the class view of the gaps:
  `class_gap_s` to the class leader and `class_interval_s` to the class car ahead, plus
  `class_lapped`. They use the same distance history as the overall gaps (`_behind`),
  so a GT3 lapped by Hypercars is not lapped within its class.
- Every screen shows the class values. The overall values stay in the payload for the
  desks and the recording.
- Fastest lap per class: `driver_race` keeps one fastest lap per class (persisted like
  today's, keyed by class).

## Lap count per class

- The fastest class takes the Schedule's `Laps` cell. For every other class Race
  Control enters the lap count on the desk near the end of the lobby (one field per
  class, kept in the actions file per stint, audited with the person). An optional
  Schedule column `Laps <Class>` (e.g. `Laps GT3`) pre-fills it.
- The class lap count shows in the class header ("LAP 48 / 54") and on the desks and the
  public page. Until it is set, the class header shows the class leader's lap only.
- The driver change timing hint (`2026-11-19-driver-change-timing-design.md`) uses the
  car's class lap count: the swap is due after lap `Laps - 1` of the car's class.

## Timing tower (OBS)

- Grouped by class: a class header row (class name in its colour, the class leader's
  lap against the class lap count), then the class's rows with class position, class
  gap, class interval and last lap in the existing cycle.
- At most 15 rows: if all classes fit, all are shown. If not, the tower pages through
  the classes, one class per page, every 15 s.
- The fastest-lap box cycles through the classes, one class per 15 s, labelled with the
  class.

## Track map

Dots keep the team colour; a ring in the class colour marks the class. The EST dashed
ring keeps its amber.

## Public live timing and desks

- One table grouped like the tower, with a class header per group and a filter: All, or
  one class. The public page keeps the filter per browser.
- The desks (Race Control, Director Panel) always show all classes, grouped, with the
  class lap count fields.
- `driver_race.public_view` adds `class`, `class_color`, `class_pos`, `class_gap_s`,
  `class_interval_s` and `class_lapped` to its allowlist.

## Recording

Samples and the CSV carry `class` and `class_pos`; the snapshot page groups like the
desks.

## Unchanged

Disconnect events, EST (any car may be the reference, from any class), red flag,
corrections and driver changes work per car as today.

## Out of scope

- Class-specific finish rules beyond GT7's own (e.g. a class flagged before the
  overall leader).
- Separate lobbies per class.
