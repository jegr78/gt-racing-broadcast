# Driver dashboard layout: survey and pick

The survey #1115 asks for before the layout of the OBS overlay and the public live
timing page (#1128). It compares the references the issue collected and picks what
fits GT7's data and our viewers. The data model is `race_distance` (#1112) and the
viewer payload of `driver_race.Service.dashboard`.

## What each reference shows

| Reference | Header | Per row | Extras |
|---|---|---|---|
| GT World Series (GT7) | `LAP 3 / 20` | position, logo, driver, compound letter in a coloured circle, pit stop count `P`, interval | a stop extends the row with pit time, compound change `H > S` and fuel |
| GT7 Timing Monitor | none | position, driver, car, tyre `RH`, gap, last lap, best lap | fastest lap in its own `FL` row |
| Le Mans Ultimate endurance | remaining time, session, track, lap | number, surname under a small first name, interval, last and best lap (overall best purple) | columns alternate (interval/lap times, interval/tyre/energy); track map with the focused car; focus card for a car or a battle |
| LMU qualifying | session, time | position, logo, number, surname, pole time and gaps | nothing else |
| GT3 tower (another sim broadcast) | `LAP 11 / 13`, timer | position change, gap, interval, last and best lap | none |
| PXLCast live timing (LMU) | session | a full table with switchable columns: last, best, gap, laps, position change, pit stops, pit time, tyre | event feed (new fastest lap and the like) |

## What our data can carry

- **Reliable on every row:** order, entry name and number, driver name, gap, interval, `+N L`, status (`Box`, `No data`, `Red flag`), pit stops, race laps (and in endurance the laps of the current stint lobby), last completed lap time, fuel.
- **Set by the crew, not by telemetry:** the tyre compound. GT7 sends none, so the letter appears only once Race Control or the director set it.
- **One per race:** the fastest race lap with entry and driver; remaining time from the race timer; the leader's lap against the lap count.
- **Not available:** sector times of other cars, position changes from an official classification, logos per driver. These stay out of the layout.

## Pick

**Overlay tower: the GT World Series layout.** GT7 viewers already read it, and every
column it needs exists in our data except the compound, which is optional by design.

- Header: `LAP n / N` from the leader (or the remaining time in a timed race) and the
  session line (track, stint label in endurance).
- Rows: position, number on the entry's tile colour (`BG Color`), driver surname with
  the entry or cup team small above, interval (the leader shows the gap column's
  title), pit stops `P`, and the optional compound letter.
- Alternating columns as in LMU, every ~15 s: interval, then last lap, then fuel.
  A row in the box shows `Box` instead.
- Footer row `FL` with the fastest lap's entry, driver and time, as in GT7's monitor.
- Track map beside the tower: the picked layout's line, a dot per car with its number
  on the tile colour, the car ahead drawn on top.

**Qualifying: the LMU qualifying tower.** Position, number, driver, best lap and gap
to pole, nothing else.

**Commentator Cockpit:** the same payload as a plain table (all columns at once), since
the commentator reads it, not the viewer.

**Public live timing (#1128): the PXLCast table.** All columns with switches, the
fastest lap, and later an event feed. Same delayed payload as the overlay.

## Decisions taken here

- The Configuration column that names a driver's team in team-vs-team leagues is
  **`Cup Team`**. Its colour is the entry row's `BG Color`, which such leagues already
  set per team; no second colour column.
- The overlay shows as many rows as fit the tower's box (up to 20 by default); the track
  map draws every car with data.
- Gaps show to a tenth of a second, lap times to the thousandth.
