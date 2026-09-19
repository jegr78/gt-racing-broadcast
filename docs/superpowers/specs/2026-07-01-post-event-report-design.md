# Post-Event-Report — design

**Date:** 2026-07-01
**Status:** approved (brainstorming) → ready for implementation plan
**Persona:** League Owner (generates + shares the report) — data drawn from the relay's Health DB

## Problem

After an event, a league owner wants a readable, shareable summary of how the broadcast
went — total uptime, per-feed reliability, what went wrong and when, and (editorially) how
long each commentator was on air. Today that information only exists as a live time-series
in the per-profile Health DB (`runtime/<profile>/health-history.db`) and the live
health-monitor page; there is no way to produce a post-event artifact. The owner should be
able to generate the report from the CLI, view it in the Control Center, and manually send
it to the league's Discord channel.

## Scope

In scope:
- A pure aggregation + rendering module over the Health DB.
- A `racecast report` CLI that writes a self-contained HTML report file, plus a
  `racecast report send` that posts it to Discord as an attachment (manual only).
- A Control Center "Report" view that renders the report and offers Download + Send-to-Discord.
- Correct behaviour across producer handovers (the merged, multi-machine DB).

Explicitly **out of scope** (YAGNI):
- No PDF engine / no new rendering dependency (resolved below — self-contained HTML instead).
- No automatic/scheduled report generation or auto-send. Every action is operator-initiated.
- No new persisted event-boundary state and **no Health-DB schema change** (handover
  handling uses the existing `takeover` event markers).
- No per-commentator scoring/ranking or historical cross-event trends.

## Key decisions (from brainstorming)

1. **Surface — the full program:** a CLI generator is the testable core; a Control Center
   view wraps it; a manual Discord send attaches the artifact. No automatic trigger.
2. **Artifact format — self-contained HTML** (not PDF). A `.html` with all CSS inline, a
   system-font stack, and no external `<img>`/JS/font requests renders identically anywhere
   it is opened, offline, and needs no new engine. It is what the Control Center displays and
   what is attached to Discord. (A Discord `.html` attachment is downloaded and opened in a
   browser — it is not previewed inline; that is the accepted trade for having no PDF
   dependency.)
3. **Event window — auto last session + override:** default to the most recent contiguous
   session, split by a **coarse** inactivity gap; `--from`/`--to`/`--session-gap` override.
4. **Name attribution — resolve now + caveat:** the Health DB stores only stint indices, so
   names are joined from the *current* schedule at report time, with an explicit caveat line.
5. **Producer handover — marker-segmented + disclosed:** use the `takeover` event markers as
   the authoritative handover timeline; de-duplicate concurrent multi-machine samples to one
   per `SAMPLE_INTERVAL_S` bucket; annotate handovers; footnote the overlap window as
   approximate. No schema change.

## Producer-handover model (why this works)

Producer B runs its own relay and mirrors A **before** pressing takeover, so B's local
timeline is continuous across the switch (no relay-down gap). `event takeover` pulls A's
health history and merges it into B's DB (dedup by `ts`) over **both** transports — tailnet
(`/health/raw`) and Funnel (`/console/takeover/health`, step-up secret) — and records a
`takeover` event marker (`ts`, `producer=<B>`, `metadata={"from": <A>, "stint": N}`,
`racecast.py:2587`). A chain A→B→C accumulates: the final producer's DB is the union of the
whole event.

Consequence the report must handle: within the mirror **overlap window** the merged DB holds
two machines' samples concurrently, and samples carry **no source tag** (only the `takeover`
*event* rows name a producer). Naive aggregation would double-count and would surface B's
pre-air mirror drops as incidents.

Handling:
- **Bucket de-duplication:** collapse samples to one per `SAMPLE_INTERVAL_S`-wide time bucket
  (keep the last by `ts`). For single-producer stretches (samples ~30 s apart) this is
  effectively identity; during the overlap it halves the density. This feeds every
  time-based aggregation.
- **Handover annotation:** the `takeover` markers within the window are rendered as
  "Producer handover HH:MM — A → B" lines and segment the session into producer-owned ranges
  for display.
- **Disclosure:** a footnote states that metrics inside a handover-overlap window are
  approximate (concurrent, source-untagged samples).

## Architecture

Follows the established pure-logic-per-feature pattern (`event_notes.py`, `cue_admin.py`):
pure module + thin wrappers in the CLI, Control Center, and HTTP helper.

### Pure core — `src/scripts/report_build.py` (new, stdlib only, no I/O)

- `SESSION_GAP_S` — default coarse session gap (**1800 s = 30 min**). Distinct from
  `health_store.GAP_S` (95 s, band continuity); this one only separates *distinct events* in
  a long-retention DB, and is large enough that a producer handover never splits an event.
- `select_session(sample_ts, gap_s=SESSION_GAP_S) -> (from_ts, to_ts)` — the last contiguous
  run of sample timestamps (a gap larger than `gap_s` ends a session). `(None, None)` for an
  empty list.
- `bucket_samples(samples, bucket_s) -> list` — one sample per `floor(ts / bucket_s)` bucket,
  keeping the last by `ts`. Deterministic.
- `build_report(samples, events, name_for_stint, event_title, window, now) -> dict` — the
  aggregator. `window=(from_ts, to_ts)`. Produces a structured dict with:
  - `header`: `event_title`, `date`, `start`, `end`, `duration_s`, `uptime_pct`.
  - `on_air`: `[{name, seconds, stints}]` — per resolved commentator name, on-air seconds and
    number of distinct on-air stints; plus `handovers` (count) and `resolved_at` (for the
    caveat).
  - `feeds`: per `A`/`B`/`POV` — `{drops, downtime_s, longest_outage_s}`.
  - `incidents`: `[{start, end, level, reasons}]` — collapsed yellow/red episodes.
  - `quality`: `{stream_kbps_avg, stream_kbps_peak, dropped_pct_avg, dropped_pct_peak,
    congestion_avg, obs_cpu_avg, obs_cpu_peak, obs_fps_avg, render_skipped_pct_peak}` or
    `None` when those columns are entirely empty (older DBs) → section omitted.
  - `handovers`: `[{ts, from, to, stint}]` from the `takeover` event rows in-window.
  - `overlap_approximate`: bool — true when ≥1 handover marker falls in the window (drives the
    footnote).
- `render_html(report_dict) -> str` — one self-contained HTML document: `<!doctype html>` +
  a single inline `<style>` (system-font stack, accent colors, table styling), the header,
  each section as a table, the handover annotations, the caveat + overlap footnote, and a
  small **inline-SVG** strip visualizing the on-air feed + health bands across the session
  (generated as SVG rects — no external chart lib, no JS). Contains **no** `http://` /
  `https://` / external-asset references.
- `render_summary_text(report_dict) -> str` — a short plaintext headline block (title,
  duration, uptime %, top-line feed/incident counts) the CLI prints to stdout.
- `report_filename(event_title, date) -> str` — `<YYYY-MM-DD>-<slug>.html` (slug via the
  existing `asset_key` normalization style; empty title → `<date>-report.html`).

Band derivation for `live_feed`/`live_stint` (not in `health_store.BAND_FIELDS`) and for
`feed_*_state`/`health_level` reuses the same collapse-into-intervals approach as
`health_store.collapse_bands`; the report module calls the store's helper where it fits and
otherwise collapses locally. Feed "drops" are counted from `feed_x_down` transitions
(0→1) and outages from the down intervals; uptime % is green-level wall-clock over total
session wall-clock, with sample gaps > `health_store.GAP_S` counted as down.

### CLI — `report_cmd(rest)` in `src/racecast.py`

Wired parallel to `health` (route branch near `racecast.py:942`; `main()` dispatch near
`:5598`; `USAGE` entry). Verbs:

- `racecast report` (default = `generate`): flags `--from TS`, `--to TS`, `--session-gap S`,
  `--out PATH`. Opens the DB (`hsmod.open_db(_health_db_path())` + `hsmod.migrate`), selects
  the window (explicit `--from/--to` else `select_session`), loads samples + `takeover`
  events in-window, buckets, builds `name_for_stint` (below), calls `build_report` +
  `render_html`, writes the file under `runtime/<profile>/reports/`, prints the path and
  `render_summary_text` to stdout. No data in window → a clear "no health data for that
  window" message, nothing written, non-zero exit.
- `racecast report send [FILE]`: FILE defaults to the newest file in the reports dir.
  Resolves the webhook via `_active_discord_webhook()`; if none, exits with a clear
  "No DISCORD_WEBHOOK_URL configured for this league" message. Uploads via
  `http_util.post_multipart` with a one-line `content` ("📊 Post-event report — <title> · <uptime>% uptime")
  and the `.html` as `files[0]`. Manual only.

**Name resolution (`name_for_stint`)** — produced by `report_cmd`, passed into the pure
builder so the builder stays I/O-free:
1. If a relay is running locally, GET `http://127.0.0.1:8088/schedule/data` (loopback) and
   build `{stint_index: streamer_name}` — **dropping the `url` field** (redaction).
2. Else fetch the schedule Sheet CSV directly from the active profile's `SHEET_ID` +
   schedule tab (the same gviz `tqx=out:csv` URL the relay uses) via `http_util.get_bytes`
   and parse name/stint (URL column ignored).
3. On any failure → empty mapping; the report falls back to "Feed A / Stint N" and the
   caveat notes that name resolution was unavailable.
The mapping is the single event-wide schedule (stint→name is stable across producers), so a
handover does not change attribution.

### HTTP helper — `http_util.post_multipart(url, fields, files, *, timeout)` (new)

Builds a `multipart/form-data` body (generated boundary) with text `fields` (Discord's
`payload_json`) and `files` (`(field_name, filename, content_bytes, content_type)`), sets
the racecast `User-Agent` (Discord is Cloudflare-fronted and 403s the default urllib UA —
this is exactly why covered-side outbound HTTP must go through `http_util`), and POSTs.
Returns the response. Unit-tested for boundary framing, part ordering, and the UA header.

### Control Center — `src/ui/` + `src/racecast_ui.py`/`ui_server.py`

- A new **Report** view: a "Generate report" action → runs the generator (op registry /
  job), then renders the produced HTML inline (iframe `srcdoc` of the self-contained
  document), with **Download .html** and **Send to Discord** buttons.
- Routes: `/api/report/generate` (produces + returns the HTML + path) and
  `/api/report/send` (invokes the send path; surfaces the "no webhook" error verbatim).
- Reuses the existing structured-provider / op-registry pattern; no new auth surface.

## Edge cases & failure modes

- Empty DB / no samples in the window → "no health data for that window", nothing written.
- Explicit `--from/--to` with no samples → same clean message.
- Schedule unreachable (relay down + Sheet fetch fails) → names fall back to `Feed A/B ·
  Stint N`; the caveat states resolution was unavailable.
- Quality columns entirely empty (pre-v3 DB) → the Stream & OBS quality section is omitted.
- Handover overlap present → handover annotations rendered + overlap footnote shown.
- No Discord webhook configured → `send` refuses with the standard message (never silently
  no-ops).
- Discord upload HTTP/network error → the error is reported; generation is unaffected
  (the file already exists on disk).

## Testing

Pure (`tests/test_report_build.py`, runnable-script style like the other `tests/*.py`):
- `select_session`: single run, two runs split by a > `gap` gap (returns the last),
  handover-length gaps < `gap` stay one session, empty → `(None, None)`.
- `bucket_samples`: single-producer input is ~identity; interleaved two-machine input
  collapses to one per bucket (deterministic last-by-ts).
- `build_report`: uptime %, per-feed drops/outage/longest, on-air seconds per name via a
  supplied `name_for_stint`, incident collapsing, quality present vs `None`,
  `handovers`/`overlap_approximate` from seeded `takeover` events, name fallback when the
  mapping is empty.
- `render_html`: contains each expected section heading and the caveat, and asserts the
  document is **self-contained** — no `http://`/`https://` substrings and no external
  `src=`/`href=` (mirrors the redaction-style assertion used elsewhere; use a dotless marker
  to avoid the CodeQL incomplete-URL-substring rule that has bitten test asserts before).
- `render_summary_text`: headline fields present.
- `report_filename`: slug + date; empty-title fallback.

CLI / integration:
- `report_cmd` generate against a seeded temp DB writes a file and prints a path (patch
  `_health_db_path`, as `test_racecast`/health tests do).
- `report send` with a fake webhook asserts a `multipart/form-data` body carrying the file
  part and `payload_json`; with no webhook asserts the clean refusal.
- `http_util.post_multipart` unit test: boundary present, both parts present, racecast UA set.

## Docs & screenshots

- **New Control Center view ⇒ screenshot-blocking** (CLAUDE.md hard rule): capture
  `src/docs/wiki/images/cc-report.png` from a **local dev build** in the same change, via the
  `wiki-screenshots` skill (demo profile + `obs-sim`; seed a short health history / use the
  demo DB so the report shows content). Add the view to the Control-Center wiki page.
- Document `racecast report` / `racecast report send` in the operator docs / CLI reference
  (README command list + the relevant wiki page), Control-Center-first per the docs ordering
  convention.
- Note the `DISCORD_WEBHOOK_URL` profile key as the prerequisite for `report send` (already
  documented for health alerts — cross-reference, don't duplicate).
- No machine `.env` knob; nothing to add to `.env.example`.

## Out-of-scope follow-ups (not this PR)

- Producer source-tagging of samples (Health-DB schema v5) for exact per-producer attribution
  in the overlap window, if "approximate overlap" ever proves insufficient.
- Cross-event trends / a season dashboard aggregating multiple reports.
- A PDF export (would reopen the rendering-engine question — deliberately deferred).

## Update: windowed render metric and the finding (#586)

- **Render skipped** is the per-interval rate `obs_render_skip_rate_pct` averaged over the
  on-air samples. The cumulative `obs_render_skipped_pct` runs from OBS start and was
  diluted 13-fold on 2026-08-28 (1.8% shown, 23.5% real). The other quality metrics are
  instantaneous or reset with the output, so only this one changed.
- **OBS FPS** is shown against the configured rate (`GetVideoSettings`, stored as
  health-store v10 `obs_fps_target`) and flagged when the average is more than 2% below
  it (`FPS_LOW_RATIO`). A DB without the column shows the bare average and says so.
- **Finding** (`build_report(..., prebuffer_s, backlog_warn_s)` -> `report["finding"]`):
  the on-air feed's backlog first (`health_store.feed_backlog_degraded`, the relay's own
  rule), the frame rate as the cause, and the number of handovers, since each one clears a
  backlog. Shown first in the HTML, in the CLI summary and as the Discord embed
  description. No mode-dependent thresholds.
