# Public Timing Byte Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The public live timing page serves any number of viewers within a fixed upload budget by stretching its poll interval, instead of rejecting viewers with 429.

**Architecture:** A new stdlib helper `src/scripts/timing_feed.py` holds two pure, clock-injectable classes: `TickBody` (one JSON + gzip body with ETag per 2 s tick) and `Budget` (estimates viewers from served requests and returns the next poll interval). The relay's `_timing` handler in `src/relay/racecast-feeds.py` uses both and nothing else changes; `src/console/timing.html` follows the server's interval, revalidates with ETag, counts `remaining` down locally and shows the data age.

**Tech Stack:** Python 3 stdlib (`gzip`, `hashlib`, `threading`), plain JS in `timing.html`, stdlib test scripts under `tests/`.

**Spec:** `docs/superpowers/specs/2026-11-17-public-timing-byte-budget-design.md`

## Global Constraints

- Only `/console/timing` and `/console/timing/data` change behaviour. `driver_race` (snapshot cadence 0.5 s, delay, `dashboard()`) and every `/drivers/*` route stay byte-for-byte as they are.
- `RACECAST_TIMING_BUDGET_KBPS` replaces `RACECAST_TIMING_MAX_RPS` (removed, never released). Default `1000` (kbit/s), clamped to 50..100000.
- Tick length 2 s; minimum poll interval 2 s; maximum 30 s.
- No SSE, no WebSocket, no long-held connections.
- English only; argparse/help ASCII; comments one sentence per reason (CLAUDE.md).
- Every touched Python file passes `python3 tools/lint.py`.

## Review Focus

1. A client without `Accept-Encoding: gzip` (curl, old browsers) gets plain JSON with the same ETag semantics. Test in Task 2.
2. Tailscale Funnel passes `Accept-Encoding` and `Content-Encoding` through unchanged. Live check in Task 4.
3. The link ends (`event stop`) while viewers hold an ETag: they get 403, never a stale 304. Test in Task 2.
4. Qualifying payloads and the empty "no snapshot" payload go through the same tick cache. Test in Task 1.
5. A sudden crowd (many first requests at once) cannot push the interval estimate below 2 s or above 30 s, and the interval falls back to 2 s once the crowd leaves. Test in Task 1.

---

### Task 1: `timing_feed` helper (tick body + budget)

**Files:**
- Create: `src/scripts/timing_feed.py`
- Create: `tests/test_timing_feed.py`
- Modify: `tools/build-binary.py` (add `"--hidden-import", "timing_feed"` next to `timing_link` at line 126; add `"gzip"` to `HIDDEN_STDLIB` under the `racecast-feeds.py` comment)

**Interfaces:**
- Produces:
  - `TICK_S = 2.0`, `MIN_INTERVAL_S = 2.0`, `MAX_INTERVAL_S = 30.0`, `NOT_MODIFIED_BYTES = 200`
  - `class TickBody(build, clock=time.monotonic, tick_s=TICK_S)`; `build: Callable[[], dict]`. Method `get() -> tuple[str, bytes, bytes]` returning `(etag, raw_json, gzip_json)`. Rebuilds at most once per `tick_s`; ETag is `'"' + sha1(raw)[:16] + '"'`; unchanged content keeps the same ETag across ticks. Thread-safe (one lock). JSON is compact, `ensure_ascii=False`, UTF-8.
  - `class Budget(kbps, clock=time.monotonic, window_s=10.0)`. Method `served(nbytes: int, interval_s: float) -> None` records one response. Method `interval() -> float`: viewers ≈ sum of `interval_s` of responses in the last `window_s` divided by `window_s`; mean bytes from the same window; result = viewers × mean_bytes / (kbps × 125), clamped to [`MIN_INTERVAL_S`, `MAX_INTERVAL_S`], rounded up to 0.5 s. Empty window returns `MIN_INTERVAL_S`. Thread-safe.
  - `budget_kbps(raw) -> int`: parses the env value, default 1000, clamp 50..100000.

- [ ] **Step 1: Write the failing tests** in `tests/test_timing_feed.py` (runnable script, `t_*` functions, same runner footer as `tests/test_driver_routes.py`), with a fake clock:
  - `t_one_build_per_tick`: a counting `build`; 50 `get()` calls at the same clock → build called once; advance 2.0 → called twice.
  - `t_etag_stable_for_same_content_and_changes_with_it`: same dict across ticks → same ETag; changed dict → different ETag; `gzip.decompress(gz) == raw`.
  - `t_qualifying_and_empty_payloads_pass`: `build` returning `{"v":1,"mode":"race","entries":[]}` and one with `"qualifying"` → valid JSON round-trip.
  - `t_interval_is_minimum_when_idle`: fresh `Budget(1000)` → `interval() == 2.0`.
  - `t_interval_grows_with_viewers`: 1000 kbit/s, 5000 responses of 700 bytes at interval 2.0 inside 10 s (1000 viewers; 1000 × 700 / 125000 = 5.6, rounded up) → `interval() == 6.0`.
  - `t_interval_clamps_and_recovers`: huge load → `30.0`; advance clock past `window_s` with no traffic → `2.0`.
  - `t_budget_kbps_parses_and_clamps`: `None`/`"x"` → 1000, `"10"` → 50, `"999999"` → 100000, `"2500"` → 2500.

- [ ] **Step 2: Run to verify they fail**

Run: `python3 tests/test_timing_feed.py`
Expected: `ModuleNotFoundError: No module named 'timing_feed'`

- [ ] **Step 3: Implement `src/scripts/timing_feed.py`** with the interfaces above. Use a `collections.deque` of `(at, nbytes, interval_s)` pruned on each call.

- [ ] **Step 4: Run to verify they pass**

Run: `python3 tests/test_timing_feed.py && python3 tests/test_build_binary.py && python3 tools/lint.py`
Expected: all pass, lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/scripts/timing_feed.py tests/test_timing_feed.py tools/build-binary.py
git commit -m "feat(timing): tick body and byte budget helper for the public timing page (#1128)"
```

### Task 2: Relay timing route uses tick body, gzip, ETag and the budget

**Files:**
- Modify: `src/relay/racecast-feeds.py` (`_timing_max_rps`/`TIMING_MAX_RPS` at ~724-732; state at ~12024-12028; `_timing` handler at ~14186-14219; import next to `import timing_link` at 205)
- Modify: `tests/test_driver_routes.py` (`t_the_public_timing_link_reaches_timing_data_and_nothing_else` and a new test)
- Modify: `.env.example:213-215`

**Interfaces:**
- Consumes: `timing_feed.TickBody`, `timing_feed.Budget`, `timing_feed.budget_kbps`, `timing_feed.NOT_MODIFIED_BYTES` from Task 1.
- Produces: module constant `TIMING_BUDGET_KBPS = timing_feed.budget_kbps(os.environ.get("RACECAST_TIMING_BUDGET_KBPS"))`; `TIMING_ABUSE_RPS = 300`. Response header `X-Poll-After: <seconds>` (one decimal) on every 200 and 304 from `/console/timing/data`. Task 3 reads it.

Behaviour of `/console/timing/data` after link check (order matters):
1. Abuse cap: `RateLimiter(limit=TIMING_ABUSE_RPS * 10, window_s=10)` on key `"all"` → 429 with `Retry-After: 5` as today.
2. `etag, raw, gz = tick_body.get()` where `build` is `lambda: driver_race.public_view(driver_race_svc.dashboard() if driver_race_svc else None)`.
3. `interval = budget.interval()`.
4. `If-None-Match` equals `etag` → 304 with `ETag`, `X-Poll-After`, `Cache-Control: no-cache`, no body; `budget.served(NOT_MODIFIED_BYTES, interval)`.
5. Else `gzip` in `Accept-Encoding` → body `gz` with `Content-Encoding: gzip`, otherwise `raw`; headers `Content-Type: application/json; charset=utf-8`, `Content-Length`, `ETag`, `Vary: Accept-Encoding`, `Cache-Control: no-cache`, `X-Poll-After`; `budget.served(len(body), interval)`.

`/console/timing` (the page): build the substituted page bytes once per process via `_send_page`'s substitution logic (extract the substitution into `_page_bytes(path, api_base) -> bytes` used by both), cache `(etag, raw, gzip)` for it, answer `If-None-Match` with 304, gzip when accepted, `Cache-Control: no-cache`. The page is not counted in the budget.

- [ ] **Step 1: Write the failing tests** in `tests/test_driver_routes.py`. Replace the `TIMING_MAX_RPS` patching in `t_the_public_timing_link_reaches_timing_data_and_nothing_else` (the "12 requests get a 429" assertion goes) and add:
  - `t_timing_data_is_gzipped_revalidated_and_paced`: `Accept-Encoding: gzip` → `Content-Encoding == "gzip"`, `json.loads(gzip.decompress(body))["v"] == 1`, `ETag` present, `float(X-Poll-After) >= 2.0`; same request with `If-None-Match: <etag>` → 304 with `X-Poll-After`; without `Accept-Encoding` → plain JSON, same ETag. (`_req` decodes as text; add a bytes variant `_req_raw` returning `(status, bytes, headers)`.)
  - `t_no_viewer_is_turned_away_by_load`: 60 sequential data requests → no 429, all 200/304.
  - `t_ended_link_beats_a_held_etag`: fetch ETag, `timing_link.clear(link_path)`, request with `If-None-Match` → 403.
  - `t_timing_page_is_gzipped_and_revalidated`: page with gzip → `Content-Encoding: gzip` and decompressed text contains `"Live timing"`; `If-None-Match` → 304.
  - `t_internal_dashboard_routes_ignore_the_timing_budget`: monkeypatch `g.m.TIMING_BUDGET_KBPS = 50` before `_serve`, fire 60 timing data requests, then `race.tick()` twice with a fresh sample in between: `/drivers/dashboard` returns the newest snapshot each time (compare to `race.dashboard()`), and `/drivers/dashboard`, `/drivers/race`, `/drivers/data` never return 429 and never carry `X-Poll-After` or `ETag`.

- [ ] **Step 2: Run to verify they fail**

Run: `python3 tests/test_driver_routes.py`
Expected: the new tests FAIL (no `Content-Encoding`, no `X-Poll-After`); `AttributeError` for `TIMING_BUDGET_KBPS`.

- [ ] **Step 3: Implement** the route behaviour above, remove `_timing_max_rps`, `TIMING_MAX_RPS` and `_timing_body`, and replace the `.env.example` block with:

```
# Public live timing (#1128): upload budget in kbit/s for all viewers together.
# More viewers poll less often; nobody is turned away.
RACECAST_TIMING_BUDGET_KBPS=1000
```

- [ ] **Step 4: Run to verify they pass**

Run: `python3 tests/test_driver_routes.py && python3 tests/test_timing_feed.py && python3 tools/lint.py && grep -rln "TIMING_MAX_RPS" src tests tools .env.example .github`
Expected: tests pass, lint clean; grep lists only `src/relay/CLAUDE.md` and `src/docs/wiki/Driver-Telemetry.md`, which Task 4 rewrites.

- [ ] **Step 5: Commit**

```bash
git add src/relay/racecast-feeds.py tests/test_driver_routes.py .env.example
git commit -m "feat(timing): gzip, ETag and an upload budget instead of a viewer cap (#1128)"
```

### Task 3: Timing page follows the server's pace

**Files:**
- Modify: `src/console/timing.html` (`poll()` at ~205-214, `remaining` render at ~149, a new data-age element in the header)

**Interfaces:**
- Consumes: `X-Poll-After` and `ETag` headers from Task 2.

Behaviour:
- `poll()` sends `If-None-Match` with the last ETag (keep `cache: "no-store"` so the browser cache never answers instead of the server); on 200 render and store ETag; on 304 keep the current data; next wait = `X-Poll-After` × 1000 (fallback 2000; 5000 after a network error or 429). 403 handling stays.
- A 1 s local timer re-renders the remaining clock as `remaining - secondsSinceReceived` (never below 0, frozen while `red_flag` is set) and an age label "updated N s ago" next to it; at age > 3 × the current interval the label gets the existing warning colour token.

- [ ] **Step 1: Implement the page changes.**
- [ ] **Step 2: Visual check** with the `ui-visual-verification` skill on the demo profile (relay with obs-sim env, see the `wiki-screenshots` skill): phone width 390 px and desktop; remaining counts down between polls; age label visible and turns to the warning colour when the relay is stopped.
- [ ] **Step 3: Refresh** `src/docs/wiki/images/public-timing.png` with the `wiki-screenshots` skill (same rule as every UI surface).
- [ ] **Step 4: Run** `python3 tests/test_driver_routes.py && python3 tests/test_wiki.py`. Expected: pass.
- [ ] **Step 5: Commit**

```bash
git add src/console/timing.html src/docs/wiki/images/public-timing.png
git commit -m "feat(timing): page follows the server's poll interval and shows the data age (#1128)"
```

### Task 4: Docs, Funnel live check, issue

**Files:**
- Modify: `src/relay/CLAUDE.md:385` (the public live timing paragraph: replace the cap sentence and "The cap is a setting until the probe..." with the budget, `X-Poll-After`, tick body, gzip/ETag, and the explicit boundary that `/drivers/*` is never paced)
- Modify: `src/docs/wiki/Driver-Telemetry.md:183-185`

- [ ] **Step 1: Rewrite both doc passages.** Wiki text: viewers share an upload budget (`RACECAST_TIMING_BUDGET_KBPS` in the machine `.env`, default 1000 kbit/s); with more viewers the page refreshes less often (at least every 30 s); nobody is turned away; Race Control, the desks and the stream overlay are never slowed.
- [ ] **Step 2: Run** `python3 tests/test_wiki.py && python3 tools/run-tests.py && python3 tools/build.py`. Expected: all pass, build verify OK.
- [ ] **Step 3: Funnel live check** (Review Focus 2): demo relay with Funnel on, then `curl -sS -o /dev/null -D - -H 'Accept-Encoding: gzip' "https://<funnel-host>/console/timing/data?l=<id>"` shows `content-encoding: gzip`, `etag` and `x-poll-after`; repeating with `-H 'If-None-Match: <etag>'` inside one tick returns `304`. Record the output in the PR description.
- [ ] **Step 4: Commit**

```bash
git add src/relay/CLAUDE.md src/docs/wiki/Driver-Telemetry.md
git commit -m "docs(timing): upload budget for the public timing page (#1128)"
```

- [ ] **Step 5:** In the PR body, propose the #1128 criterion change ("scales through the poll interval, no viewer limit"); the issue edit and close happen after merge, with the R2 + CDN spike noted as the follow-up.
