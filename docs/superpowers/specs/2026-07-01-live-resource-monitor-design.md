# Live Resource Monitor — design

**Date:** 2026-07-01
**Status:** approved (brainstorming) → ready for implementation plan
**Persona:** Producer — watches the producer machine's health live during an event

## Problem

During a broadcast the producer's machine runs OBS + the relay (multiple `streamlink`/
`yt-dlp` pulls) + Companion + the Control Center. If CPU, RAM, or the network saturates,
the stream degrades — but today nothing surfaces the *machine's* own resource use. The
health monitor tracks OBS-process and OBS-output stats (`obs_cpu_pct`, `stream_kbps`, …),
never the box itself. The producer needs a live at-a-glance view of CPU / RAM / Net / Disk
while producing, plus history to correlate a mid-event problem afterwards.

## Scope

In scope:
- A stdlib-only per-OS reader for machine CPU %, RAM used/total, network up/down throughput,
  and free disk.
- A **live "System" card** on the Control Center Home dashboard (color-coded).
- **History**: the same metrics sampled into the health-DB and charted in the existing
  `/health-monitor` uPlot dashboard.

Explicitly **out of scope** (YAGNI):
- No `psutil` or any new dependency — stdlib only.
- No per-core CPU, no per-process breakdown, no GPU (no stdlib reader exists).
- No alerting/notifications on threshold breach — the card color is the signal.
- No configurable-thresholds UI, no new CLI command, no new `.env` knob.

## Key decisions (from brainstorming)

1. **Both surfaces** — a live Control Center card **and** health-DB history/charts.
2. **Metrics** — CPU % (overall), RAM used/total + %, Net up + down, Disk free.
3. **Thresholds (color)** — CPU: green <75, yellow 75–90, red ≥90. RAM %: green <80,
   yellow 80–92, red ≥92. Disk: reuse `preflight.classify_disk`. **Net: no color** (uplink
   capacity is unknown — a threshold would be invented); shown as informative Mbps only.
4. **Two independent samplers** sharing one reader: the UI process (live card, fast) and the
   relay heartbeat (history, 30 s). The live card works even when the relay is down.

## Architecture

### Reader + sampler — `src/scripts/resources.py` (new, stdlib only)

Mirrors the per-OS branching in `preflight.py` (`read_ram_bytes`/`read_swap_used_bytes`) and
the injectable-seam testability of `speedtest.py` (`runner`/`which`).

- **Pure parsers** (no I/O — unit-tested with fixture strings):
  - `parse_proc_stat_cpu(text) -> (busy, total)` — Linux `/proc/stat` first `cpu ` line
    (sum of fields; `total` = all, `busy` = total − idle − iowait).
  - `parse_proc_net_dev(text) -> (rx_bytes, tx_bytes)` — Linux `/proc/net/dev`, summed over
    non-loopback interfaces.
  - `parse_netstat_ib(text) -> (rx_bytes, tx_bytes)` — macOS `netstat -ib`, summed over
    non-`lo0` interfaces (dedup per interface — `netstat -ib` prints one row per address).
  - `parse_top_cpu(text) -> busy_pct | None` — macOS `top -l2 -s1 -n0` "CPU usage" line →
    `100 − idle%` (the second sample).
  - **Delta math** (pure): `cpu_pct_from_delta(busy0, total0, busy1, total1) -> pct|None`
    (None when `total` delta ≤ 0); `rate_from_delta(bytes0, bytes1, dt) -> bytes_per_s|None`
    (None when `dt ≤ 0` or counter went backwards).
- **`ResourceSampler`** — stateful, owns the previous cumulative CPU + net counters and the
  last timestamp. `sample(now) -> dict`:
  `{cpu_pct, mem_used, mem_total, mem_pct, net_up_bps, net_down_bps, disk_free, ts}`.
  - CPU %: cumulative-counter delta between calls (**Linux** `/proc/stat`; **Windows** ctypes
    `GetSystemTimes` idle/kernel/user — no subprocess, no console flash; **macOS** the
    `top -l2` subprocess, which self-contains its own two-sample delta). Net: cumulative-byte
    delta between calls (**Linux** `/proc/net/dev`; **macOS** `netstat -ib`; **Windows**
    iphlpapi `GetIfTable2`, hardware NICs only, filter drivers excluded; perf-counter paths
    are localized and fail on German Windows, #722). RAM: `preflight.read_ram_bytes()` for total + per-OS used
    (`/proc/meminfo` MemAvailable on Linux; ctypes `ullAvailPhys` on Windows;
    `vm_stat`/`sysctl` on macOS). Disk: `shutil.disk_usage(path).free`.
  - **First call** (no previous counters) → `cpu_pct`/`net_*` are `None`; RAM/disk are
    immediate. Subsequent calls fill them in.
  - **Never raises** — any per-metric read failure yields `None` for that metric only.
  - Constructed with an injectable reader map (default = the real per-OS readers) so tests
    drive it with synthetic counter sequences and assert the computed %/bps — no real OS
    calls, runs in CI on any OS.
- Any subprocess uses the repo's `no_window_kwargs()` (Windows console-less requirement).

### Live card — Control Center

- **`resources_data()`** in `src/racecast.py` — returns the latest snapshot from a
  process-wide `ResourceSampler` plus the derived color levels (`cpu_level`, `mem_level`,
  `disk_level` ∈ {green,yellow,red}; net has none). Never raises (returns
  `{available: false}` if the sampler is unavailable).
- A background **sampler thread** is started when the Control Center boots (`racecast_ui` /
  the UI server startup), ticking ~2 s so deltas stay recent. `resources_data()` reads the
  cached latest — **no per-request subprocess**.
- Registered in the `ctx` dict as `"resources"`; **route** `GET /api/resources` in
  `src/ui/ui_server.py` (mirrors the `/api/status` provider pattern).
- **Front-end**: a "System" card in the Home view of `src/ui/control-center.html`, polled on
  its own ~2 s interval (`/api/resources`), rendered with `textContent` (numbers only), each
  metric tinted by its `*_level`; a `None` metric shows "—". This is a **screenshot-blocking
  Control Center view** → `src/docs/wiki/images/cc-home.png` refreshed in the same PR.

### History — health-DB + charts

- **Five new columns** in `src/scripts/health_store.py`, `sys_`-prefixed to avoid collision
  with the OBS `obs_*` columns: `sys_cpu_pct`, `sys_mem_pct`, `sys_net_up_kbps`,
  `sys_net_down_kbps`, `sys_disk_free_mb` (all `REAL`). Added to `COLUMNS`, `_CREATE` (fresh
  DBs), a new `_V5_COLUMNS` `ALTER TABLE` list (lossless upgrade of pre-v5 DBs), and
  `NUMERIC_FIELDS`. **`SCHEMA_VERSION` 4 → 5.**
- The **relay heartbeat** (`_health_snapshot` in `src/relay/racecast-feeds.py`, every 30 s)
  owns a relay-side `ResourceSampler`; each tick merges its snapshot (converted to the column
  units — % and kbps and MB) into the sample row.
- **v12 (#722):** `sys_net_down_min_kbps`, the smallest download rate within the heartbeat
  interval. A relay-side `NetDownFloor` samples the net counter every 2 s on its own thread;
  the heartbeat takes and resets it, the same contract as the feeds' interval max gap. The
  30 s mean hides a short dip; the floor shows it next to `feed_*_max_gap_s` in one row.
- The generic `numeric_series` → uPlot pipeline charts the new fields automatically; the
  `health-monitor.html` `NUMERIC_FIELDS` array gains a **"System (machine)"** group. Numbers
  only → redaction-safe, so it flows over Funnel and the takeover health-pull unchanged.

## Edge cases & failure modes

- Unreadable metric on an OS / transient error → `None`; card shows "—", the chart series
  just has no point for that tick. Never raises, never blocks the heartbeat or a request.
- First sampler tick has no previous counters → CPU %/net `None` until the next tick (~2 s);
  documented, not an error.
- Pre-v5 health DBs migrate losslessly (empty `sys_*` until the new relay writes them); the
  new charts render empty for pre-upgrade history.
- Two processes sampling concurrently (UI + relay) is intentional and independent; the
  lightweight overhead is acceptable and the live card never depends on the relay.
- Windows: ctypes for CPU (no console window); any subprocess uses `no_window_kwargs`.

## Testing

- **`tests/test_resources.py`** — the pure parsers (`/proc/stat`, `/proc/net/dev`,
  `netstat -ib`, `top -l2` fixture strings), the delta math (two synthetic CPU snapshots →
  expected %, two net snapshots + dt → expected bps; `None` on non-positive dt / counter
  reset), `ResourceSampler` first-tick `None` then a computed second tick (driven by an
  injected reader), and `None`-on-reader-failure. No real OS calls → CI-safe on all three
  OSes.
- **`tests/test_health_store.py`** — the five new columns present, v4→v5 migration is
  idempotent and lossless, and the new fields round-trip through `numeric_series`.
- **`tests/test_ui_server.py` / `tests/test_racecast.py`** — `GET /api/resources` returns the
  sampler's latest snapshot + levels; `resources_data()` degrades to `{available:false}`
  cleanly.

## Docs & screenshots

- **Control Center Home changed ⇒ screenshot-blocking** (CLAUDE.md hard rule): regenerate
  `src/docs/wiki/images/cc-home.png` from a local dev build in the same PR (the System card
  reads real local machine values — that is fine and reproducible; no Tailscale IP is in the
  card).
- Document the System card on the Control-Center wiki page and the new chart group on the
  Health-Monitor wiki page. No README command (no CLI verb), no `.env` knob.

## Out-of-scope follow-ups (not this PR)

- Threshold breach → Discord/`@here` alert (reuse the health-alert poster) if "tell me when
  the box is in trouble" becomes a real need.
- Per-process or per-core drill-down; GPU utilization (needs a non-stdlib path).
- Feeding the System metrics into the Post-Event Report's quality section.
