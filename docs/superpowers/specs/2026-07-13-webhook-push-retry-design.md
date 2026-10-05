# Webhook push retry with bounded backoff (#490)

**Date:** 2026-07-13
**Issue:** [#490](https://github.com/jegr78/gt-endurance-racing-broadcast/issues/490)
**Scope:** `src/relay/racecast-feeds.py` only (the two `_push` paths + a shared retry
helper + a pure permanent-error predicate). Control-plane only — does NOT touch the live
feeds or the schedule read. Single PR into `main`.

## Problem

The panel→sheet webhook push does **one** synchronous `POST` (`post_webhook`,
`timeout=10s`) with **no retry**: a single transient failure surfaces immediately as the
red **"SHEET SYNC FAILED"** banner and the write is lost. It bites hardest during a feed
switch — a synchronous sheet write on the critical path exactly when the box's outbound
egress is most congested (yt-dlp resolve + streamlink pull, possibly amid 429). Observed
live during the N24 24h race; it self-recovered on the next push. Apps Script is
transient-failure-prone (answers HTTP 200 even on errors, 302-redirects, times out under
load), so a single call is fragile.

## Mechanism (code)

Both push paths are identical: `post_webhook(url, payload)` → `check_webhook_response(body,
expected_action)` → set `push_status`/`last_error`.
- `TimerStore._push` (~line 1801) — pushes in a background thread (`_spawn_push`); no
  `expected_action` (a v1 script keeps the timer working).
- `SetupControl._push` (~line 4617) — some callers async (`set_field` background thread),
  but **schedule/POV URL writes are synchronous** (block the panel's HTTP response); passes
  `expected_action`.
- `check_webhook_response` returns `(False, "webhook did not confirm: …")` for a transient
  hiccup, and `(False, "webhook script outdated (no action echo) — redeploy …")` for a
  permanent v1-script config error.
- The Director-Panel banner fires whenever `/setup/data` reports `push: "failed"`.

## Design

### 1. One shared retry helper (DRY — both `_push` paths use it)

```python
def push_webhook_retrying(url, payload, expected_action=None, *,
                          post=post_webhook, sleep=time.sleep, rand=random.random,
                          attempts=WEBHOOK_RETRY_ATTEMPTS, base_delay=WEBHOOK_RETRY_BASE_S,
                          budget_s=WEBHOOK_RETRY_BUDGET_S, timeout=WEBHOOK_RETRY_TIMEOUT_S,
                          now=time.monotonic):
    """POST with bounded backoff+jitter until the Apps Script confirms {"ok":true},
    a PERMANENT error is hit, or attempts/budget are exhausted. Returns (ok, err,
    body). Retries a network exception and a transient "did not confirm" body; does
    NOT retry a permanent 'script outdated' error (surfaced immediately). Seams
    (post/sleep/rand/now) make the backoff loop deterministic in tests."""
```

Loop, per attempt:
- `body = post(url, payload, timeout=timeout)`; on a network exception → **retryable**.
- Else `ok, err = check_webhook_response(body, expected_action)`.
  - `ok` → return `(True, None, body)`.
  - `not ok` and `webhook_error_permanent(err)` → return `(False, err, body)` **immediately**
    (no retry — a v1-script config error never becomes ok; retrying only delays the
    "redeploy" banner).
  - `not ok` (transient "did not confirm") → **retryable**.
- Before the next attempt: sleep `base_delay * 2**n` plus jitter (`rand()` scaled to
  `base_delay`), **but stop retrying once `now() - start >= budget_s`** so total latency is
  bounded (critical for the synchronous schedule write). After the last attempt (or budget)
  return the final `(False, err_or_exc_text, None_or_body)`.

Constants (module-level, easily tuned):
`WEBHOOK_RETRY_ATTEMPTS = 3`, `WEBHOOK_RETRY_BASE_S = 0.5`, `WEBHOOK_RETRY_BUDGET_S = 10.0`,
`WEBHOOK_RETRY_TIMEOUT_S = 5` (per-attempt; lower than the direct 10 s so a hung attempt
fails fast and the next one runs inside the budget).

> **Revised by #767:** 5 s per attempt was tighter than Apps Script's real latency, and the
> 10 s budget allowed only two attempts. Now `WEBHOOK_RETRY_TIMEOUT_S = 15` and
> `WEBHOOK_RETRY_BUDGET_S = 40.0` (three attempts, worst case ~48 s); see "Revision (#767)"
> below.

### 2. Permanent-vs-transient — a pure predicate

Extract the outdated-script message to a module constant and add:

```python
def webhook_error_permanent(err):
    """True iff *err* is the permanent 'script outdated' config error (never retry).
    Any other non-ok error (a transient 'did not confirm') is retryable. Pure."""
```

`check_webhook_response` uses the same constant so the two never drift.

### 3. Wire both `_push` paths through the helper

`TimerStore._push(payload)` and `SetupControl._push(payload, expected_action)` call
`push_webhook_retrying(...)` instead of `post_webhook` + `check_webhook_response`, and set
`push_status`/`last_error` from its result — so `"failed"` is set (and the banner raised)
**only after** the retries/budget are exhausted; a transient blip that succeeds on a retry
sets `"ok"` and never raises the banner. The banner still clears on the next success
(unchanged). No caller signature changes; the sync/async nature of each caller is unchanged
(only the push's internal robustness changes).

### 4. Latency trade-off (deliberate)

A transient blip now costs up to `WEBHOOK_RETRY_BUDGET_S` (~10 s) on a **synchronous**
schedule write instead of an instant red banner — in exchange the write persists.
Background pushes (timer, setup fields) have no perceptible latency. Typical transient case:
the retry succeeds on attempt 2 (~5.5 s). Constants are tunable.

## Testing (seams already exist)

- `push_webhook_retrying` with an injected `post` that fails N times then returns an ok
  body + a fake `sleep`/`rand`/`now`: retries exactly, returns `(True, …)`; when all
  attempts fail, returns `(False, …)`.
- A permanent "script outdated" error → the helper does **one** `post` call, no retry,
  returns `(False, …)` immediately.
- Budget cap: with a `now` seam advancing past `budget_s`, the loop stops early (fewer than
  `attempts` posts).
- Backoff schedule: assert the `sleep` seam is called with increasing (base·2^n + jitter)
  delays.
- `webhook_error_permanent(err)` pure predicate (permanent → True, transient/None → False).
- `TimerStore._push` / `SetupControl._push`: transient-then-ok via an injected `post`
  → `push_status == "ok"`, no `last_error`; exhausted → `"failed"` with a descriptive
  `last_error`. (These paths already accept seam injection in the existing tests —
  `tests/test_setup.py` for SetupControl, `tests/test_timer.py` for TimerStore.)

## Explicitly out of scope (issue point 3, larger)

Decoupling / de-prioritising the push from the feed-resolve load window (a dedicated egress
queue so the webhook POST never competes with the streamlink/yt-dlp burst). A separate
follow-up; this change is retry + bounded backoff + a lower per-attempt timeout only.

## Revision (#767): fit the timeouts to Apps Script and log every push

Measured on 2026-10-05 against a production webhook (no-op `setup` writes): typical
1.5-4 s, outliers of 9.7 s and 18.9 s, independent of parallel sheet reads; 2 of 6 pushes
through an idle relay ended in the 5 s timeout. With 5 s per attempt and a 10 s budget only
two attempts fit, so a ~10 s slow phase of the script always raised SHEET SYNC FAILED.

- `WEBHOOK_RETRY_TIMEOUT_S = 15`, `WEBHOOK_RETRY_BUDGET_S = 40.0`: three attempts fit (worst
  case ~48 s). Background pushes (setup fields, teams, timer) use this.
- `WEBHOOK_SYNC_BUDGET_S = 15.0` for the writes that run inside the panel's HTTP request
  (schedule, POV, crew): one full attempt, a second only after a fast failure. This replaces
  the latency trade-off in section 4.
- `crew` deletes are never retried (`attempts=1`): a timed-out attempt can still land, and a
  retried delete would then remove the next row. Every other action writes values and is
  safe to repeat.
- `OVERRIDE_TTL` goes from 30 to 60 s, so the HUD's optimistic echo outlives the slowest
  background push instead of flipping back to the old sheet value mid-retry.
- Logging: each failed attempt and the final failure are a WARNING in the relay log
  (`sheet push <action>: attempt n/N failed after X s: <error>`), a success that needed a
  retry or took `WEBHOOK_SLOW_S` (5 s) or longer is an INFO. The webhook URL and its
  `key` are masked in the log and in the panel's `last_error`.

## Acceptance criteria (from #490)

- [ ] A single transient webhook failure no longer raises "SHEET SYNC FAILED" — the push
      retries and succeeds within the budget.
- [ ] The banner appears only after retries/budget are exhausted, with a descriptive
      `last_error`.
- [ ] A permanent "script outdated" error is surfaced immediately (not retried).
- [ ] Panel writes made during a feed switch persist reliably under normal transient
      conditions (bounded, backoff+jitter).
- [ ] Unit coverage for the retry/backoff path, the budget cap, and the permanent-error
      predicate (pure seams: `post`/`sleep`/`rand`/`now`).
