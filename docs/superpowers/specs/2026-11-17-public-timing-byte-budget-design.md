# Public live timing: byte budget on the Funnel path

Issue: #1128. Follows the page and link from #1148.

## Intent

The public live timing page (`/console/timing`, served through Funnel from the producer's
machine) must not need a viewer count estimate. Viewer numbers vary per event and cannot be
measured in advance. Today a request cap (`RACECAST_TIMING_MAX_RPS`, 40/s) turns viewer 81 away
with 429. After this change every viewer is served, the page refreshes more slowly under load,
and the producer's upload spent on the page stays below a fixed budget.

This is the producer-side part. Offloading viewers to external hosting (R2 + CDN or a per-league
publish target) comes after a separate spike and is not part of this design.

## Goals

- Bytes per viewer as small as possible: one pre-encoded body per tick, gzip, 304 when unchanged.
- A byte budget instead of a request cap: the relay counts bytes it sent on the timing data route
  and tells each client when to poll next (2 s minimum, longer when the budget is used up).
- No viewer is rejected for load. A hard request cap remains only as abuse protection.
- The page counts `remaining` down locally between polls and shows the age of its data, so a
  longer interval does not look like a stall.

## Hard boundary (non-goals)

- Budget, tick cache, gzip/ETag and the server-set interval apply only to `/console/timing` and
  `/console/timing/data`.
- `driver_race` stays unchanged: snapshot cadence (0.5 s), delay and `dashboard()`. The timing
  route only reads the newest delayed snapshot; it never triggers or slows anything upstream.
- The internal routes (`/drivers/dashboard` for the stream overlay, `/drivers/race`,
  `/drivers/data` and the other `/drivers/*` routes for Race Control and the desks) get no
  throttle, no tick cache and no interval hint. They keep updating exactly as now.
- No SSE or WebSocket: each would hold a relay thread per viewer in the process that serves the
  feeds.

## Approaches considered

- **Byte budget with a server-set interval (picked).** Bounds the upload, serves everyone, keeps
  short requests.
- **SSE push.** Saves empty polls but holds a thread per viewer in the relay. Rejected.
- **Byte diet only (gzip and 304, keep the request cap).** About five times more viewers, but
  viewer N+1 is still rejected and the count still has to be guessed. Rejected.
- **External fan-out (R2 + CDN, Worker, VPS) and VDO.Ninja.** VDO.Ninja is peer-to-peer and
  uploads one copy per viewer from the producer, so it does not help. External fan-out is the
  next step, after a spike.

## Decided

- `RACECAST_TIMING_BUDGET_KBPS` replaces `RACECAST_TIMING_MAX_RPS`. The old variable was never
  released (merged in #1148, release 1.16.0 still open), so it is removed, not deprecated.
  Default 1000 kbit/s: a small share of a 21 Mbit/s DSL upload that also carries a 6 to 8 Mbit/s
  stream.
- The body is built at most once per 2 s tick, as JSON and gzip, with an ETag derived from its
  content. All viewers within a tick get the same bytes.
- The page HTML is served with gzip and an ETag.
- A test proves the boundary: with the timing budget exhausted, `/drivers/dashboard` still returns
  every new snapshot immediately, and no `/drivers/*` route ever answers 429 or carries an
  interval hint.
- Docs: `src/relay/CLAUDE.md` and the wiki's Funnel page describe the budget. The open criterion
  in #1128 changes to "scales through the poll interval, no viewer limit".

## Left to the builder

- How the interval reaches the client (response header or a field in the JSON) and the exact
  formula from measured bytes per window to the next interval.
- The size of the abuse cap and the measurement window.
- Whether 304 responses count toward the budget with their header size.
