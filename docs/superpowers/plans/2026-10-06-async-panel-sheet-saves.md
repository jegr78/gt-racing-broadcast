# Async Director Panel Sheet Saves Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Schedule, Qualifying and POV saves from the Director Panel answer at once, are live in the relay at once, and reach the sheet in the background; only an outdated script turns the panel red.

**Architecture:** A pure, thread-safe `PendingSaves` store (new `src/scripts/pending_saves.py`) holds one entry per (target, row). `SetupControl` validates a save, puts it into the store and wakes one background worker that pushes entries in order. Each `ScheduleSource` gets the store attached and re-applies the pending fields after every `refresh()`, so the 30 s poller, RELOAD, NEXT and mode switches all see the new value. The #779 recovery tick re-queues saves stuck in `local` once the webhook answers.

**Tech Stack:** Python 3.11+ stdlib only (threading, json, time); vanilla JS in `src/director/director-panel.html`; stdlib test scripts under `tests/` (no pytest).

**Spec:** `docs/superpowers/specs/2026-10-06-async-panel-sheet-saves-design.md`

## Global Constraints

- Edit only under `src/` (plus `tests/`, `docs/`); never `dist/` or `runtime/`.
- English only in code, comments, UI text and docs.
- Outbound HTTP only through the existing `post_webhook` in the relay (it sets the UA); no new `urllib` call.
- Every text-mode `subprocess` call passes `errors="replace"` (none expected here).
- Crew writes (`crew_set`, `crew_delete`) stay synchronous (`_sync_push`, 30 s, delete single-attempt).
- Async save push: attempt timeout `WEBHOOK_ASYNC_SAVE_TIMEOUT_S = 45`, `WEBHOOK_ASYNC_SAVE_ATTEMPTS = 3`, budget `WEBHOOK_ASYNC_SAVE_BUDGET_S = 150.0`.
- A confirmed entry is released when the sheet shows it, or `CONFIRM_GRACE_S = 120.0` after the webhook confirmed it.
- Red banner (`push_status == "failed"`) from a panel save only for `WEBHOOK_OUTDATED_ERROR`; every other failure is the quiet `local` state with its error text.
- No wiki screenshot refresh for this change (maintainer decision).
- Tests: stdlib scripts, `t_` functions, runnable as `python3 tests/<file>.py`; no real IPs or machine paths.
- After any Python change: `python3 tools/lint.py`. Before the PR: `python3 tools/run-tests.py`, `python3 tests/test_pov.py`, `python3 tools/build.py` (exit 0).
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A second save for the same row while the first is in flight: the newer value must be what finally lands and what the relay keeps; the older confirm must not release the entry. (Task 1, `t_put_during_flight_keeps_newer_revision`.)
2. CLEAR URL (empty url) followed by a sheet poll that still shows the old link: the relay must keep the cleared link. (Task 2, `t_pinned_clear_survives_stale_refresh`.)
3. A save for a brand-new row (ADD ROW, row not yet in the sheet) must appear in the relay's schedule at once and stay through polls. (Task 2, `t_pinned_new_row_survives_refresh`.)
4. A failed save whose script answer was `{"ok": false, "error": ...}` must not turn the banner red and must be re-queued by recovery. (Task 4, `t_script_error_is_quiet_and_recovers`.)
5. Validation errors (off-vocab streamer, bad URL, no webhook) must still be answered synchronously and must not create a pending entry. (Task 3, `t_validation_errors_create_no_pending_entry`.)

---

### Task 1: PendingSaves store

**Files:**
- Create: `src/scripts/pending_saves.py`
- Test: `tests/test_pending_saves.py`

**Interfaces:**
- Produces:
  - Constants `SAVING = "saving"`, `LOCAL = "local"`, `CONFIRMED = "confirmed"`, `CONFIRM_GRACE_S = 120.0`.
  - `class PendingSaves(now=time.monotonic)`:
    - `put(target: str, row: int, fields: dict) -> int` (merge field-by-field, bump revision, state `saving`, queue the key; returns the new revision)
    - `next_job() -> tuple | None` returning `(target, row, fields_copy, rev)` for the first queued key not in flight, marking it in flight
    - `done(target, row, rev, ok: bool, err: str | None = None, hold: bool = False) -> str | None` returning the entry's new state (`"confirmed"`, `"saving"`, `"local"`) or `None` if the entry is gone
    - `requeue_local() -> int` (every `local` entry without `hold` back to `saving` and queued)
    - `requeue(target, row) -> bool` (one `local` entry, held or not, back to `saving`)
    - `overlay(target) -> dict[int, dict]` (row -> fields, every live entry of the target)
    - `reconcile(target, sheet_rows: dict[int, tuple]) -> list[int]` (drops `confirmed` entries whose fields match the sheet row, or whose grace expired; returns the dropped rows). `sheet_rows` maps row -> `(url, name, stint)`; a missing row counts as `("", "", "")`.
    - `state(target, row) -> tuple[str | None, str | None]` returning `(sync, err)`: `("saving", None)` for saving/in flight, `("local", err)` for local, `(None, None)` for confirmed or absent
    - `unsynced() -> tuple[int, str | None]` (count of `local` entries, most recent error text)
    - `has_queued() -> bool`, `__len__() -> int`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pending_saves.py`:

```python
#!/usr/bin/env python3
"""Stdlib unit checks for the pending panel-save store.
Run: python3 tests/test_pending_saves.py"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "pending_saves", os.path.join(ROOT, "src", "scripts", "pending_saves.py"))
ps = importlib.util.module_from_spec(spec); spec.loader.exec_module(ps)


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


def _store():
    c = Clock()
    return ps.PendingSaves(now=c), c


def t_put_queues_a_saving_entry():
    s, _c = _store()
    rev = s.put("schedule", 3, {"url": "https://youtu.be/a"})
    assert rev == 1
    assert s.state("schedule", 3) == ("saving", None)
    assert s.next_job() == ("schedule", 3, {"url": "https://youtu.be/a"}, 1)
    assert s.next_job() is None            # in flight, nothing else queued


def t_put_merges_fields_newest_wins():
    s, _c = _store()
    s.put("schedule", 3, {"url": "https://youtu.be/a", "name": "JeGr"})
    s.put("schedule", 3, {"url": ""})
    assert s.next_job() == ("schedule", 3, {"url": "", "name": "JeGr"}, 2)


def t_done_ok_confirms_latest_revision():
    s, _c = _store()
    s.put("pov", 2, {"url": "https://youtu.be/p"})
    _t, _r, _f, rev = s.next_job()
    assert s.done("pov", 2, rev, ok=True) == "confirmed"
    assert s.state("pov", 2) == (None, None)
    assert len(s) == 1                      # kept until the sheet shows it


def t_put_during_flight_keeps_newer_revision():
    s, _c = _store()
    s.put("schedule", 3, {"url": "https://youtu.be/a"})
    _t, _r, _f, rev1 = s.next_job()
    s.put("schedule", 3, {"url": "https://youtu.be/b"})        # arrives mid-flight
    assert s.done("schedule", 3, rev1, ok=True) == "saving"     # older confirm
    assert s.next_job() == ("schedule", 3, {"url": "https://youtu.be/b"}, 2)
    assert s.overlay("schedule") == {3: {"url": "https://youtu.be/b"}}


def t_done_failure_goes_local_with_error():
    s, _c = _store()
    s.put("schedule", 3, {"url": "https://youtu.be/a"})
    _t, _r, _f, rev = s.next_job()
    assert s.done("schedule", 3, rev, ok=False, err="TimeoutError: x") == "local"
    assert s.state("schedule", 3) == ("local", "TimeoutError: x")
    assert s.unsynced() == (1, "TimeoutError: x")
    assert s.next_job() is None             # local is not queued


def t_requeue_local_skips_held_entries():
    s, _c = _store()
    s.put("schedule", 3, {"url": "https://youtu.be/a"})
    s.put("schedule", 4, {"url": "https://youtu.be/b"})
    j = s.next_job(); s.done(j[0], j[1], j[3], ok=False, err="timeout")
    j = s.next_job(); s.done(j[0], j[1], j[3], ok=False, err="outdated", hold=True)
    assert s.requeue_local() == 1
    assert s.state("schedule", 3) == ("saving", None)
    assert s.state("schedule", 4) == ("local", "outdated")
    assert s.requeue("schedule", 4) is True                    # SYNC NOW forces it
    assert s.state("schedule", 4) == ("saving", None)
    assert s.requeue("schedule", 9) is False


def t_reconcile_releases_confirmed_when_sheet_matches():
    s, _c = _store()
    s.put("schedule", 3, {"url": "https://youtu.be/a", "name": "JeGr"})
    j = s.next_job(); s.done(j[0], j[1], j[3], ok=True)
    assert s.reconcile("schedule", {3: ("https://youtu.be/old", "JeGr", "")}) == []
    assert s.reconcile("schedule", {3: ("https://youtu.be/a", "JeGr", "Stint 1")}) == [3]
    assert len(s) == 0


def t_reconcile_never_releases_saving_or_local():
    s, _c = _store()
    s.put("schedule", 3, {"url": "https://youtu.be/a"})
    assert s.reconcile("schedule", {3: ("https://youtu.be/a", "", "")}) == []
    j = s.next_job(); s.done(j[0], j[1], j[3], ok=False, err="x")
    assert s.reconcile("schedule", {3: ("https://youtu.be/a", "", "")}) == []


def t_reconcile_missing_row_counts_as_empty():
    s, _c = _store()
    s.put("schedule", 7, {"url": "", "name": "", "stint": ""})
    j = s.next_job(); s.done(j[0], j[1], j[3], ok=True)
    assert s.reconcile("schedule", {}) == [7]


def t_reconcile_grace_expiry():
    s, c = _store()
    s.put("schedule", 3, {"url": "https://youtu.be/a"})
    j = s.next_job(); s.done(j[0], j[1], j[3], ok=True)
    c.t += ps.CONFIRM_GRACE_S - 1
    assert s.reconcile("schedule", {3: ("https://youtu.be/x", "", "")}) == []
    c.t += 2
    assert s.reconcile("schedule", {3: ("https://youtu.be/x", "", "")}) == [3]


def t_targets_are_separate():
    s, _c = _store()
    s.put("schedule", 2, {"url": "https://youtu.be/a"})
    s.put("qualifying", 2, {"url": "https://youtu.be/q"})
    assert s.overlay("schedule") == {2: {"url": "https://youtu.be/a"}}
    assert s.overlay("qualifying") == {2: {"url": "https://youtu.be/q"}}


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 tests/test_pending_saves.py`
Expected: FAIL (`FileNotFoundError` for `src/scripts/pending_saves.py`).

- [ ] **Step 3: Implement the store**

Create `src/scripts/pending_saves.py`:

```python
"""Pending Director Panel sheet saves (spec 2026-10-06-async-panel-sheet-saves).

A panel save is live in the relay at once and reaches the sheet in the
background. This store holds one entry per (target, row) until the sheet shows
the saved value. Pure and thread-safe; the relay owns the worker and the HTTP.
"""
import threading
import time

SAVING = "saving"
LOCAL = "local"
CONFIRMED = "confirmed"
# A confirmed entry stays pinned until the sheet CSV shows it; this bounds the
# wait, so a row cannot stay pinned when the sheet stores a value differently.
CONFIRM_GRACE_S = 120.0

_FIELDS = ("url", "name", "stint")


class _Entry:
    __slots__ = ("fields", "rev", "state", "err", "hold", "confirmed_at")

    def __init__(self):
        self.fields = {}
        self.rev = 0
        self.state = SAVING
        self.err = None
        self.hold = False
        self.confirmed_at = None


class PendingSaves:
    def __init__(self, now=time.monotonic):
        self._now = now
        self._lock = threading.Lock()
        self._entries = {}          # (target, row) -> _Entry
        self._queue = []            # keys waiting for a push, in order
        self._inflight = set()

    def put(self, target, row, fields):
        key = (target, int(row))
        with self._lock:
            e = self._entries.get(key) or _Entry()
            e.fields.update({k: v for k, v in fields.items() if k in _FIELDS})
            e.rev += 1
            e.state, e.err, e.hold, e.confirmed_at = SAVING, None, False, None
            self._entries[key] = e
            if key not in self._queue:
                self._queue.append(key)
            return e.rev

    def next_job(self):
        with self._lock:
            for key in self._queue:
                if key in self._inflight:
                    continue
                self._queue.remove(key)
                self._inflight.add(key)
                e = self._entries[key]
                return key[0], key[1], dict(e.fields), e.rev
            return None

    def done(self, target, row, rev, ok, err=None, hold=False):
        key = (target, int(row))
        with self._lock:
            self._inflight.discard(key)
            e = self._entries.get(key)
            if e is None:
                return None
            if rev != e.rev:
                return e.state            # a newer save is queued; it decides
            if ok:
                e.state, e.err, e.confirmed_at = CONFIRMED, None, self._now()
            else:
                e.state, e.err, e.hold = LOCAL, err, bool(hold)
            return e.state

    def _requeue_locked(self, key, e):
        e.state, e.hold = SAVING, False
        if key not in self._queue:
            self._queue.append(key)

    def requeue_local(self):
        with self._lock:
            n = 0
            for key, e in self._entries.items():
                if e.state == LOCAL and not e.hold:
                    self._requeue_locked(key, e)
                    n += 1
            return n

    def requeue(self, target, row):
        key = (target, int(row))
        with self._lock:
            e = self._entries.get(key)
            if e is None or e.state != LOCAL:
                return False
            self._requeue_locked(key, e)
            return True

    def overlay(self, target):
        with self._lock:
            return {row: dict(e.fields) for (t, row), e in self._entries.items()
                    if t == target}

    def reconcile(self, target, sheet_rows):
        now = self._now()
        dropped = []
        with self._lock:
            for (t, row), e in list(self._entries.items()):
                if t != target or e.state != CONFIRMED:
                    continue
                shown = dict(zip(_FIELDS, sheet_rows.get(row, ("", "", ""))))
                matches = all(shown.get(k, "") == v for k, v in e.fields.items())
                if matches or now - e.confirmed_at >= CONFIRM_GRACE_S:
                    del self._entries[(t, row)]
                    dropped.append(row)
        return sorted(dropped)

    def state(self, target, row):
        with self._lock:
            e = self._entries.get((target, int(row)))
            if e is None or e.state == CONFIRMED:
                return None, None
            if e.state == LOCAL:
                return LOCAL, e.err
            return SAVING, None

    def unsynced(self):
        with self._lock:
            local = [e for e in self._entries.values() if e.state == LOCAL]
            return len(local), (local[-1].err if local else None)

    def has_queued(self):
        with self._lock:
            return bool(self._queue)

    def __len__(self):
        with self._lock:
            return len(self._entries)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 tests/test_pending_saves.py` then `python3 tools/lint.py`
Expected: `ALL PASS`; `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/scripts/pending_saves.py tests/test_pending_saves.py
git commit -m "feat(relay): add a store for pending panel sheet saves

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: ScheduleSource keeps pending values through every refresh

**Files:**
- Modify: `src/relay/racecast-feeds.py` (class `ScheduleSource`: `__init__`, `refresh`, `inject_row`; add `attach_pending`, `apply_pending`, `sync_state`; import `pending_saves` next to `import graphic_takes`)
- Test: `tests/test_setup.py` (new section after the `inject_row` tests near line 110)

**Interfaces:**
- Consumes: `PendingSaves.overlay`, `.reconcile`, `.state` (Task 1).
- Produces on `ScheduleSource`:
  - `attach_pending(store: PendingSaves, target: str) -> None`
  - `apply_pending() -> None` (re-applies the store's overlay for this target onto the in-memory rows)
  - `sync_state(row: int) -> tuple[str | None, str | None]` (`(None, None)` when no store is attached)
  - `refresh()` now calls `reconcile` with the fresh sheet rows, then `apply_pending()`, before returning True.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_setup.py` after `t_inject_row_*` tests:

```python
# Pending panel saves pinned in the source (spec 2026-10-06-async-panel-sheet-saves).

import pending_saves as _ps   # src/scripts is on sys.path once the relay module loaded


def _pinned_source(sheet_rows, target="schedule"):
    s = _sched_with_rows(sheet_rows)
    store = _ps.PendingSaves()
    s.attach_pending(store, target)
    return s, store


def t_pinned_value_survives_refresh():
    s, store = _pinned_source([("https://www.youtube.com/watch?v=old", "JeGr", "Stint 1", 2)])
    store.put("schedule", 2, {"url": "https://www.youtube.com/watch?v=new"})
    s.apply_pending()
    assert s.get() == ["https://www.youtube.com/watch?v=new"]
    assert s.refresh() is True                        # sheet still shows the old link
    assert s.get_rows() == [("https://www.youtube.com/watch?v=new", "JeGr", "Stint 1", 2)]
    assert s.sync_state(2) == ("saving", None)


def t_pinned_clear_survives_stale_refresh():
    s, store = _pinned_source([("https://www.youtube.com/watch?v=old", "JeGr", "Stint 1", 2)])
    store.put("schedule", 2, {"url": ""})
    s.apply_pending(); s.refresh()
    assert s.get_rows() == [("", "JeGr", "Stint 1", 2)]


def t_pinned_new_row_survives_refresh():
    s, store = _pinned_source([("https://www.youtube.com/watch?v=a", "JeGr", "Stint 1", 2)])
    store.put("schedule", 3, {"url": "https://www.youtube.com/watch?v=b",
                              "name": "GT45", "stint": "Stint 2"})
    s.apply_pending(); s.refresh()
    assert [r[3] for r in s.get_rows()] == [2, 3]


def t_confirmed_value_released_once_the_sheet_shows_it():
    s, store = _pinned_source([("https://www.youtube.com/watch?v=old", "JeGr", "", 2)])
    store.put("schedule", 2, {"url": "https://www.youtube.com/watch?v=new"})
    j = store.next_job(); store.done(j[0], j[1], j[3], ok=True)
    s.fetch = lambda timeout=15: [("https://www.youtube.com/watch?v=new", "JeGr", "", 2)]
    s.refresh()
    assert len(store) == 0 and s.sync_state(2) == (None, None)


def t_unattached_source_reports_no_sync_state():
    s = _sched_with_rows([("https://www.youtube.com/watch?v=a", "JeGr", "", 2)])
    assert s.sync_state(2) == (None, None)
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -c "import sys; sys.path.insert(0,'tests'); import test_setup as t; t.t_pinned_value_survives_refresh()"`
Expected: FAIL with `AttributeError: 'ScheduleSource' object has no attribute 'attach_pending'`.

- [ ] **Step 3: Implement**

In `src/relay/racecast-feeds.py`, next to `import graphic_takes`:

```python
import pending_saves   # pending Director Panel sheet saves (src/scripts on sys.path)
```

In `ScheduleSource.__init__`, after `self.last_error = None`:

```python
        # Pending panel saves (spec 2026-10-06): applied after every refresh so a
        # value not yet in the sheet is what the feeds, RELOAD and NEXT see.
        self._pending = None
        self._pending_target = None
```

Split `inject_row` so the merge can run under the lock that `refresh` already holds:

```python
    def inject_row(self, physical_row, url=None, name=None, stint=None):
        """(docstring unchanged)"""
        with self.lock:
            return self._merge_row_locked(physical_row, url, name, stint)

    def _merge_row_locked(self, physical_row, url=None, name=None, stint=None):
        existing = next((r for r in self.rows if r[3] == physical_row), None)
        cur_u, cur_n, cur_s = existing[:3] if existing else ("", "", "")
        new_u = cur_u if url is None else feed_source_value(url)
        new_n = cur_n if name is None else (name or "").strip()
        new_s = cur_s if stint is None else (stint or "").strip()
        if new_u and not (is_feed_source(new_u) if self.allow_local else is_channel(new_u)):
            return False
        rows = [r for r in self.rows if r[3] != physical_row]
        if new_u or new_n or new_s:        # keep planned stints (url may be "")
            rows.append((new_u, new_n, new_s, physical_row))
        rows.sort(key=lambda r: r[3])
        self.rows = rows
        self.items = [u for u, _n, _s, _l in rows]
        return True

    def attach_pending(self, store, target):
        self._pending, self._pending_target = store, target

    def _apply_pending_locked(self):
        if self._pending is None:
            return
        for row, fields in self._pending.overlay(self._pending_target).items():
            self._merge_row_locked(row, fields.get("url"), fields.get("name"),
                                   fields.get("stint"))

    def apply_pending(self):
        with self.lock:
            self._apply_pending_locked()

    def sync_state(self, row):
        if self._pending is None:
            return None, None
        return self._pending.state(self._pending_target, row)
```

In `refresh`, inside the `with self.lock:` block after `self.last_error = None`:

```python
                if self._pending is not None:
                    self._pending.reconcile(self._pending_target,
                                            {l: (u, n, s) for u, n, s, l in rows})
                    self._apply_pending_locked()
```

(The cache file keeps writing the sheet's `rows`, not the overlay; leave that line as is.)

- [ ] **Step 4: Run tests**

Run: `python3 tests/test_setup.py` and `python3 tests/test_pov.py` and `python3 tools/lint.py`
Expected: `ALL PASS` (both), `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/relay/racecast-feeds.py tests/test_setup.py
git commit -m "feat(relay): keep pending panel saves through every schedule refresh

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: SetupControl saves asynchronously

**Files:**
- Modify: `src/relay/racecast-feeds.py` (constants next to `WEBHOOK_SYNC_BUDGET_S`; `SetupControl.__init__`, `_schedule_write`, `pov_set`; new `_save_push`, `_queue_save`, `_ensure_worker`, `_save_worker`, `_run_save_job`, `drain_saves`, `sync_save`; class docstring)
- Test: `tests/test_setup.py` (adjust existing sync tests; add new ones)

**Interfaces:**
- Consumes: `PendingSaves` (Task 1), `ScheduleSource.attach_pending/apply_pending/sync_state` (Task 2).
- Produces on `SetupControl`:
  - attribute `saves: pending_saves.PendingSaves`
  - attribute `autostart_worker: bool = True` (tests set False and call `drain_saves()`)
  - `_save_push(payload: dict, expected_action: str) -> tuple[bool, str | None]` (the webhook call; tests stub it)
  - `drain_saves() -> int` (runs queued jobs in the calling thread; returns the number run)
  - `sync_save(target: str, row: int) -> dict` (`{"ok": True}` or `{"error": ...}`)
  - `schedule_set`/`qualifying_set` return `{"ok": True, "row": row, "pending": True}`; `pov_set` returns `{"ok": True, "pending": True}`
  - module constants `WEBHOOK_ASYNC_SAVE_TIMEOUT_S = 45`, `WEBHOOK_ASYNC_SAVE_ATTEMPTS = 3`, `WEBHOOK_ASYNC_SAVE_BUDGET_S = 150.0`, `POV_SHEET_ROW = 2`
  - targets: `"schedule"`, `"qualifying"`, `"pov"` (POV row is always `POV_SHEET_ROW`)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_setup.py` (new section before `# setup-assets media fill`):

```python
# Async panel saves (spec 2026-10-06-async-panel-sheet-saves).

def _async_ctl(post=None, rows=None):
    hs = _hs_stub()
    s = _sched_with_rows(rows or [("https://www.youtube.com/watch?v=old", "JeGr", "Stint 1", 2)])
    ctl = m.SetupControl("http://push", hs, schedule_source=s)
    ctl.autostart_worker = False
    calls = []
    def fake(payload, expected_action):
        calls.append(payload)
        return post(payload) if post else (True, None)
    ctl._save_push = fake
    return ctl, s, calls


def t_save_answers_pending_before_the_webhook():
    ctl, s, calls = _async_ctl()
    r = ctl.schedule_set(2, url="https://www.youtube.com/watch?v=new")
    assert r == {"ok": True, "row": 2, "pending": True}, r
    assert calls == []                                       # nothing pushed yet
    assert s.get() == ["https://www.youtube.com/watch?v=new"]   # live at once
    assert s.sync_state(2) == ("saving", None)
    assert ctl.drain_saves() == 1
    assert calls[-1] == {"action": "schedule", "row": 2,
                         "url": "https://www.youtube.com/watch?v=new"}
    assert s.sync_state(2) == (None, None)                   # confirmed


def t_validation_errors_create_no_pending_entry():
    ctl, s, calls = _async_ctl()
    assert "error" in ctl.schedule_set(2, name="Nobody")
    assert "error" in ctl.schedule_set(2, url="not a url")
    assert "error" in ctl.pov_set("local:")
    assert len(ctl.saves) == 0 and ctl.drain_saves() == 0
    assert "error" in m.SetupControl(None, _hs_stub()).schedule_set(2, url="")


def t_failed_save_stays_in_relay_quietly():
    ctl, s, calls = _async_ctl(post=lambda p: (False, "TimeoutError: slow"))
    ctl.schedule_set(2, url="https://www.youtube.com/watch?v=new")
    ctl.drain_saves()
    assert s.sync_state(2) == ("local", "TimeoutError: slow")
    assert s.get() == ["https://www.youtube.com/watch?v=new"]   # still live
    assert ctl.push_status != "failed"                          # no red banner
    assert ctl.data()["unsynced"] == 1
    assert ctl.data()["unsynced_error"] == "TimeoutError: slow"


def t_outdated_script_turns_red_and_holds():
    ctl, s, calls = _async_ctl(post=lambda p: (False, m.WEBHOOK_OUTDATED_ERROR))
    ctl.schedule_set(2, url="https://www.youtube.com/watch?v=new")
    ctl.drain_saves()
    assert ctl.push_status == "failed"
    assert ctl.saves.requeue_local() == 0                   # held for a fix


def t_two_saves_for_one_row_push_the_newest():
    ctl, s, calls = _async_ctl()
    ctl.schedule_set(2, url="https://www.youtube.com/watch?v=a")
    ctl.schedule_set(2, name="GT45")
    ctl.drain_saves()
    assert calls == [{"action": "schedule", "row": 2,
                      "url": "https://www.youtube.com/watch?v=a", "name": "GT45"}]


def t_qualifying_save_targets_its_tab_and_source():
    pushes = []
    ctl, qsrc, ssrc, orig = _qctl(pushes)
    ctl.autostart_worker = False
    try:
        r = ctl.qualifying_set(2, url="https://www.youtube.com/watch?v=q",
                               name="GT45", stint="Stint 2")
        assert r.get("pending"), r
        assert qsrc.get() == ["https://www.youtube.com/watch?v=q"] and ssrc.get() == []
        ctl.drain_saves()
        assert pushes[-1]["tab"] == "Qualifying"
    finally:
        m.post_webhook = orig


def t_pov_save_pins_row_two():
    import tempfile
    pov = m.ScheduleSource("http://pov", os.path.join(tempfile.mkdtemp(), "p.txt"),
                           None, allow_local=False)
    pov.fetch = lambda timeout=15: [("https://www.youtube.com/watch?v=old", "Old", "", 2)]
    pov.refresh()
    ctl = m.SetupControl("http://push", _hs_stub(), pov_source=pov)
    ctl.autostart_worker = False
    ctl._save_push = lambda payload, expected_action: (True, None)
    r = ctl.pov_set("https://www.youtube.com/watch?v=p", "A Very Long Driver Name Here")
    assert r == {"ok": True, "pending": True}, r
    assert pov.get_rows() == [("https://www.youtube.com/watch?v=p", "A Very Long Driver N", "", 2)]
    pov.refresh()                                            # stale sheet
    assert pov.get()[0] == "https://www.youtube.com/watch?v=p"


def t_sync_save_requeues_a_local_row():
    results = [(False, "TimeoutError: slow"), (True, None)]
    ctl, s, calls = _async_ctl(post=lambda p: results.pop(0))
    ctl.schedule_set(2, url="https://www.youtube.com/watch?v=new")
    ctl.drain_saves()
    assert ctl.sync_save("schedule", 2) == {"ok": True}
    assert ctl.drain_saves() == 1
    assert s.sync_state(2) == (None, None)
    assert "error" in ctl.sync_save("schedule", 2)            # nothing pending any more
    assert "error" in ctl.sync_save("bogus", 2)


def t_async_save_push_uses_the_long_attempt():
    seen, restore = _record_push_kwargs()
    try:
        ctl = m.SetupControl("http://push", _hs_stub())
        ok, err = ctl._save_push({"action": "schedule", "row": 2}, "schedule")
    finally:
        restore()
    kw = seen[0][2]
    assert kw["timeout"] == m.WEBHOOK_ASYNC_SAVE_TIMEOUT_S == 45
    assert kw["attempts"] == m.WEBHOOK_ASYNC_SAVE_ATTEMPTS == 3
    assert kw["budget_s"] == m.WEBHOOK_ASYNC_SAVE_BUDGET_S


def t_worker_thread_pushes_in_the_background():
    import threading
    gate = threading.Event()
    ctl = m.SetupControl("http://push", _hs_stub(),
                         schedule_source=_sched_with_rows([("", "JeGr", "Stint 1", 2)]))
    def slow(payload, expected_action):
        gate.wait(5)
        return True, None
    ctl._save_push = slow
    r = ctl.schedule_set(2, url="https://www.youtube.com/watch?v=n")   # worker autostarts
    assert r.get("pending")
    gate.set()
    for _ in range(100):
        if ctl.schedule_source.sync_state(2) == (None, None):
            break
        import time as _t; _t.sleep(0.02)
    assert ctl.schedule_source.sync_state(2) == (None, None)
```

Adjust these existing tests so they drain the queue before checking pushes (the assertions stay the same):
- `t_schedule_set_validates_and_pushes`, `t_schedule_set_validates_streamer_and_stint_vocab`, `t_schedule_set_accepts_local_but_pov_does_not`, `t_pov_set_pushes`, `t_pov_set_empty_name_clears`, `t_qualifying_set_targets_qualifying_tab_and_injects_qual_source`, `t_schedule_set_has_no_tab_key`: add `ctl.autostart_worker = False` right after the controller is created and `ctl.drain_saves()` before each `pushes[-1]` assertion.
- `t_schedule_set_clear_reflects_in_source`: add `ctl.autostart_worker = False`; the source check holds right after the call; add `ctl.drain_saves()` before `pushes[-1]`.
- `t_pov_set_with_name_pushes_clamped_and_refreshes`: rename to `t_pov_set_with_name_pushes_clamped`, add `autostart_worker = False` and `drain_saves()`, delete the `_RefreshSpy` assertion and pass `pov_source=None` (the POV overlay is covered by `t_pov_save_pins_row_two`); delete `_RefreshSpy` if unused afterwards.
- `t_schedule_set_injects_on_success`: replace the `ctl._push` stub with `ctl.autostart_worker = False; ctl._save_push = lambda p, e: (True, None)`; assertions unchanged (the value is live at once).
- `t_schedule_set_no_inject_on_push_failure`: rename to `t_schedule_set_keeps_value_when_push_fails`; stub `ctl._save_push = lambda p, e: (False, "boom")`, `autostart_worker = False`; call, then `ctl.drain_saves()`; assert `"pending" in out` and `src.get() == ["s1", "https://www.youtube.com/watch?v=abc"]` and `src.sync_state(2) == ("local", "boom")`. (`src` here has no `attach_pending` until the controller attaches it; the controller attaches in `__init__`.)
- `t_endpoints_post_writes`, `t_endpoints_qualifying_set_post`: after `_ctl`/`_qctl`, set `ctl.autostart_worker = False`; call `ctl.drain_saves()` before each `pushes[-1]` assertion.
- `t_synchronous_sheet_writes_get_one_long_attempt`: keep only the crew calls (`crew_set`, `crew_delete`); expected actions `["crew", "crew"]`.
- `t_synchronous_sheet_writes_run_one_at_a_time`: replace the schedule/POV threads with four `crew_set` threads (`args=(n,), kwargs={"name": "Someone"}` for n in 1..4).

- [ ] **Step 2: Run to verify failure**

Run: `python3 tests/test_setup.py`
Expected: FAIL on `t_async_save_push_uses_the_long_attempt` / `t_save_answers_pending_before_the_webhook` (`AttributeError: ... autostart_worker`/`_save_push`).

- [ ] **Step 3: Implement**

Constants, after `WEBHOOK_SYNC_BUDGET_S`:

```python
# Async panel saves (Schedule/Qualifying/POV, spec 2026-10-06): the answer does
# not wait for the script, so an attempt can outlast its 30-40 s slow phases.
WEBHOOK_ASYNC_SAVE_TIMEOUT_S = 45
WEBHOOK_ASYNC_SAVE_ATTEMPTS = 3
WEBHOOK_ASYNC_SAVE_BUDGET_S = 150.0
POV_SHEET_ROW = 2     # the Apps Script writes the POV tab's row 2 (writePov)
```

`SetupControl.__init__`, after `self._sync_lock = threading.Lock()`:

```python
        # Schedule/Qualifying/POV saves answer at once and are pushed by one
        # background worker, in order (spec 2026-10-06-async-panel-sheet-saves).
        self.saves = pending_saves.PendingSaves()
        self.autostart_worker = True
        self._save_wake = threading.Event()
        self._worker = None
        self._save_sources = {"schedule": schedule_source, "qualifying": qual_source,
                              "pov": pov_source}
        for target, src in self._save_sources.items():
            if src is not None and hasattr(src, "attach_pending"):
                src.attach_pending(self.saves, target)
```

Update the class docstring sentence about synchronous writes to: "Schedule/Qualifying/POV saves are async too: live in the relay at once, pushed by one background worker (spec 2026-10-06-async-panel-sheet-saves); Crew writes stay synchronous."

New methods (place after `_sync_push`):

```python
    def _save_push(self, payload, expected_action):
        """One async-save push: the long attempt, NOT the panel lock's 30 s."""
        ok, err, _body = push_webhook_retrying(
            self.push_url, payload, expected_action,
            timeout=WEBHOOK_ASYNC_SAVE_TIMEOUT_S, attempts=WEBHOOK_ASYNC_SAVE_ATTEMPTS,
            budget_s=WEBHOOK_ASYNC_SAVE_BUDGET_S)
        return ok, err

    def _queue_save(self, target, row, fields):
        self.saves.put(target, row, fields)
        src = self._save_sources.get(target)
        if src is not None and hasattr(src, "apply_pending"):
            src.apply_pending()
        self._ensure_worker()

    def _ensure_worker(self):
        self._save_wake.set()
        if not self.autostart_worker:
            return
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._save_worker,
                                            name="sheet-save", daemon=True)
            self._worker.start()

    def _save_worker(self):
        while True:
            self._save_wake.wait()
            self._save_wake.clear()
            while self._run_save_job():
                pass

    @staticmethod
    def _save_label(target, row):
        return "POV" if target == "pov" else f"{target} row {row}"

    def _run_save_job(self):
        job = self.saves.next_job()
        if job is None:
            return False
        target, row, fields, rev = job
        if target == "pov":
            payload, action = {"action": "pov", **fields}, "pov"
        else:
            payload, action = {"action": "schedule", "row": row}, "schedule"
            if target == "qualifying":
                payload["tab"] = DEFAULT_QUALIFYING_TAB
            payload.update(fields)
        with self._sync_lock:
            ok, err = self._save_push(payload, action)
        key = (target, row)
        if ok:
            self.push_status, self.last_error = "ok", None
            state = self.saves.done(target, row, rev, ok=True)
            if state == pending_saves.CONFIRMED and key in self._was_local:
                self._was_local.discard(key)
                LOG.info("sheet save %s now in sheet", self._save_label(target, row))
        else:
            outdated = webhook_error_permanent(err)
            if outdated:
                self._mark_push_failed(err)
            state = self.saves.done(target, row, rev, ok=False, err=err, hold=outdated)
            if state == pending_saves.LOCAL and key not in self._was_local:
                self._was_local.add(key)
                LOG.warning("sheet save %s kept in relay, not in the sheet yet: %s",
                            self._save_label(target, row), err)
        src = self._save_sources.get(target)
        if src is not None and hasattr(src, "apply_pending"):
            src.apply_pending()
        return True

    def drain_saves(self):
        """Run every queued save in the calling thread (tests, and nothing else)."""
        n = 0
        while self._run_save_job():
            n += 1
        return n

    def sync_save(self, target, row=POV_SHEET_ROW):
        """SYNC NOW: push one `local` save again at once."""
        if target not in self._save_sources:
            return {"error": f"unknown save target: {target!r}"}
        try:
            row = POV_SHEET_ROW if target == "pov" else int(row)
        except (TypeError, ValueError):
            return {"error": "row must be a number (1-based)"}
        if not self.saves.requeue(target, row):
            return {"error": "nothing waiting for the sheet in that row"}
        self._ensure_worker()
        return {"ok": True}
```

Also add `self._was_local = set()` to `__init__` next to `self._worker = None` (keys whose last push failed, so "kept in relay" and "now in sheet" are each logged once per transition).

`_schedule_write`: keep every validation line unchanged up to building `payload`; replace the tail starting at `ok, err = self._sync_push(payload, "schedule")` with:

```python
        fields = {k: payload[k] for k in ("url", "name", "stint") if k in payload}
        self._queue_save("qualifying" if tab else "schedule", row, fields)
        return {"ok": True, "row": row, "pending": True}
```

and drop the `inject_source` parameter from `_schedule_write` and from its two callers (`schedule_set`, `qualifying_set`): the source now comes from `self._save_sources`.

`pov_set`: keep validation; replace the tail from `ok, err = self._sync_push(payload, "pov")` with:

```python
        fields = {"url": url}
        if "name" in payload:
            fields["name"] = payload["name"]
        self._queue_save("pov", POV_SHEET_ROW, fields)
        return {"ok": True, "pending": True}
```

`data()`: add to the returned dict:

```python
        unsynced, unsynced_error = self.saves.unsynced()
        ...
                "unsynced": unsynced, "unsynced_error": unsynced_error}
```

- [ ] **Step 4: Run tests**

Run: `python3 tests/test_setup.py`, `python3 tests/test_submissions.py`, `python3 tests/test_pov.py`, `python3 tools/lint.py`
Expected: all `ALL PASS`; lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/relay/racecast-feeds.py tests/test_setup.py
git commit -m "feat(relay): answer panel schedule and POV saves at once, push them in the background

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Recovery re-queues saves held in the relay

**Files:**
- Modify: `src/relay/racecast-feeds.py` (`webhook_recovery_tick`; `SetupControl.recover_push` override; `needs_recovery` on `PushHealth` and `SetupControl`; the `run_webhook_recovery` start condition stays)
- Test: `tests/test_setup.py` (recovery section added in #779)

**Interfaces:**
- Consumes: `SetupControl.saves`, `_ensure_worker`, `drain_saves` (Task 3); `PushHealth.recover_push` (#779).
- Produces: `PushHealth.needs_recovery() -> bool` (default: `self.push_status == "failed"`); `SetupControl.needs_recovery()` also true while `saves.unsynced()[0] > 0`; `webhook_recovery_tick` selects stores by `needs_recovery()`.

- [ ] **Step 1: Write the failing tests**

```python
def t_script_error_is_quiet_and_recovers():
    answers = [(False, "webhook did not confirm: '{\"ok\": false, \"error\": \"Exception: Service Spreadsheets timed out\"}'"),
               (True, None)]
    ctl, s, calls = _async_ctl(post=lambda p: answers.pop(0))
    ctl.schedule_set(2, url="https://www.youtube.com/watch?v=new")
    ctl.drain_saves()
    assert ctl.push_status != "failed" and s.sync_state(2)[0] == "local"
    assert m.webhook_recovery_tick("http://push", [ctl], probe=lambda url: True) is True
    assert s.sync_state(2) == ("saving", None)
    ctl.drain_saves()
    assert s.sync_state(2) == (None, None) and ctl.data()["unsynced"] == 0


def t_recovery_leaves_local_saves_while_the_probe_fails():
    ctl, s, calls = _async_ctl(post=lambda p: (False, "TimeoutError: slow"))
    ctl.schedule_set(2, url="https://www.youtube.com/watch?v=new")
    ctl.drain_saves()
    assert m.webhook_recovery_tick("http://push", [ctl], probe=lambda url: False) is False
    assert s.sync_state(2)[0] == "local"


def t_probe_alone_does_not_clear_a_red_status_held_by_a_save():
    ctl, s, calls = _async_ctl(post=lambda p: (False, m.WEBHOOK_OUTDATED_ERROR))
    ctl.schedule_set(2, url="https://www.youtube.com/watch?v=new")
    ctl.drain_saves()
    m.webhook_recovery_tick("http://push", [ctl], probe=lambda url: True)
    assert ctl.push_status == "failed"
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 tests/test_setup.py`
Expected: FAIL in `t_script_error_is_quiet_and_recovers` (the tick ignores a store whose status is not `failed`).

- [ ] **Step 3: Implement**

In `PushHealth`:

```python
    def needs_recovery(self):
        return self.push_status == "failed"
```

In `webhook_recovery_tick`, replace the `failed = [...]` line:

```python
    failed = [s for s in stores if s is not None and s.needs_recovery()]
```

In `SetupControl` (after `sync_save`):

```python
    def needs_recovery(self):
        return PushHealth.needs_recovery(self) or self.saves.unsynced()[0] > 0

    def recover_push(self, probe):
        """#779 recovery plus the panel saves held in the relay: once the webhook
        answers, every `local` save is pushed again. The status itself only turns
        ok through a real push, and an outdated script stays red."""
        cleared = False
        if not self._push_held_by_outdated_save():
            cleared = PushHealth.recover_push(self, probe)
        if self.saves.unsynced()[0] and probe():
            if self.saves.requeue_local():
                self._ensure_worker()
                cleared = True
        return cleared

    def _push_held_by_outdated_save(self):
        return webhook_error_permanent(getattr(self, "_push_err", None))
```

(`PushHealth.recover_push` already refuses to clear an outdated error; the helper keeps the intent explicit and avoids a probe call for it.)

- [ ] **Step 4: Run tests**

Run: `python3 tests/test_setup.py`, `python3 tools/lint.py`
Expected: `ALL PASS`; lint clean.

- [ ] **Step 5: Commit**

```bash
git add src/relay/racecast-feeds.py tests/test_setup.py
git commit -m "feat(relay): push panel saves held in the relay again once the webhook answers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: HTTP surface (sync fields, SYNC NOW routes, stop warning)

**Files:**
- Modify: `src/relay/racecast-feeds.py` (`/schedule/data` and `/qualifying/data` row dicts; the two `out["pov"]` blocks in `Relay.status`/solo status; POST routes; `shutdown()` in `main`)
- Modify: `src/scripts/console_policy.py:96` (director-gate the sync routes)
- Test: `tests/test_setup.py`, `tests/test_console_gate.py`

**Interfaces:**
- Consumes: `ScheduleSource.sync_state` (Task 2), `SetupControl.sync_save`, `.saves` (Task 3).
- Produces:
  - module helper `sync_fields(source, line) -> dict` returning `{}` or `{"sync": s}` plus `"sync_error"` for `local`.
  - routes `POST /schedule/sync {row}`, `POST /qualifying/sync {row}`, `POST /pov/sync {}` -> `setup_ctl.sync_save(...)`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_setup.py`:

```python
def t_sync_fields_helper():
    class Src:
        def sync_state(self, row):
            return {2: ("saving", None), 3: ("local", "TimeoutError: x")}.get(row, (None, None))
    assert m.sync_fields(Src(), 2) == {"sync": "saving"}
    assert m.sync_fields(Src(), 3) == {"sync": "local", "sync_error": "TimeoutError: x"}
    assert m.sync_fields(Src(), 4) == {}
    assert m.sync_fields(object(), 2) == {}          # stub sources without sync_state


def t_endpoints_sync_routes():
    ctl, s, calls = _async_ctl(post=lambda p: (False, "TimeoutError: slow"))
    srv, get, post = _client(ctl)
    try:
        assert post("/schedule/set", {"row": 2, "url": "https://youtu.be/x"}).get("pending")
        ctl.drain_saves()
        assert post("/schedule/sync", {"row": 2}) == {"ok": True}
        assert "error" in post("/schedule/sync", {"row": 9})
        assert "error" in post("/qualifying/sync", {"row": 2})
        assert "error" in post("/pov/sync", {})
    finally:
        srv.shutdown()
```

In `tests/test_console_gate.py`, next to the existing director-route checks for `schedule/set` (find with `grep -n "schedule/set" tests/test_console_gate.py`), add a test in the same style:

```python
def t_sync_routes_are_director_only():
    for path in (["schedule", "sync"], ["qualifying", "sync"], ["pov", "sync"]):
        req = cp.requirement_for(path)
        assert req is not None and req.role == cp.DIRECTOR, (path, req)
```

(Before writing it, read how existing tests in that file call the policy function and use the same function name and attribute names; adapt `requirement_for`/`.role` to what the file uses.)

- [ ] **Step 2: Run to verify failure**

Run: `python3 tests/test_setup.py`, `python3 tests/test_console_gate.py`
Expected: FAIL (`AttributeError: module ... has no attribute 'sync_fields'`; the policy test fails for `schedule/sync`).

- [ ] **Step 3: Implement**

Module helper (place right after `webhook_recovery_tick`):

```python
def sync_fields(source, line):
    """The panel's per-row save state for /schedule/data, /qualifying/data and
    the POV status: {} when nothing is pending (or the source has no store)."""
    state = getattr(source, "sync_state", None)
    if state is None:
        return {}
    sync, err = state(line)
    if not sync:
        return {}
    return {"sync": sync, "sync_error": err} if sync == "local" else {"sync": sync}
```

`/schedule/data` row dict: append `**sync_fields(relay.race_source if hasattr(relay, "race_source") else relay.source, line)`. Read `schedule_rows(relay)` first to see which source it returns rows for and use that same source object; do not guess.

`/qualifying/data` row dict: append `**sync_fields(qs, line)`.

Both `out["pov"] = {...}` blocks: append `**sync_fields(self.pov_source, POV_SHEET_ROW)` inside the dict.

POST routes, next to `["pov", "set"]`:

```python
                if p == ["schedule", "sync"]:
                    return self._send(setup_ctl.sync_save("schedule", body.get("row")))
                if p == ["qualifying", "sync"]:
                    return self._send(setup_ctl.sync_save("qualifying", body.get("row")))
                if p == ["pov", "sync"]:
                    return self._send(setup_ctl.sync_save("pov"))
```

`console_policy.py` line with `schedule/set`:

```python
    if p in (["schedule", "set"], ["qualifying", "set"],
             ["schedule", "sync"], ["qualifying", "sync"]):
        return Requirement(DIRECTOR, False)
```

(`/pov/*` is already director-gated by the `p[0] == "pov"` rule; the policy test confirms it.)

`shutdown()` in `main`, before `LOG.info("Stopping feeds…")`:

```python
        if setup_ctl is not None and len(setup_ctl.saves):
            LOG.warning("relay stopping with %d panel save(s) not confirmed in the "
                        "sheet; they are dropped", len(setup_ctl.saves))
```

- [ ] **Step 4: Run tests**

Run: `python3 tests/test_setup.py`, `python3 tests/test_console_gate.py`, `python3 tests/test_pov.py`, `python3 tools/lint.py`
Expected: `ALL PASS`; lint clean. `t_endpoints_schedule_data_marks_live` still passes unchanged (no `sync` key when nothing is pending).

- [ ] **Step 5: Commit**

```bash
git add src/relay/racecast-feeds.py src/scripts/console_policy.py tests/test_setup.py tests/test_console_gate.py
git commit -m "feat(relay): report each panel save's sheet state and add SYNC NOW routes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Director Panel row states

**Files:**
- Modify: `src/director/director-panel.html` (CSS near the `.pending` style at line ~213; `schedRow`, `schedSave`, `#povSave` handler, `schedPoll`, `qualSave`, `qualPoll`, `setupPoll`'s `#setupInfo` text; a small markup addition for POV and qualifying badges)

**Interfaces:**
- Consumes: row `sync`/`sync_error` (Task 5), `/status` `pov.sync`/`pov.sync_error`, `/setup/data` `unsynced`/`unsynced_error` (Task 3), `POST /schedule/sync|/qualifying/sync|/pov/sync` (Task 5).

- [ ] **Step 1: Add the shared helpers and CSS**

CSS (next to the existing `.pending` rule):

```css
  .syncbadge{display:none;margin-left:6px;padding:0 5px;border:1px solid #b8892a;
             border-radius:3px;color:#e0b24a;font-size:10px;letter-spacing:.06em;cursor:help}
  .syncbadge.on{display:inline-block}
  button.syncing{color:#e0b24a;border-color:#b8892a}
```

JS, right after `rowBusy`:

```js
// Sheet state of a saved row (spec 2026-10-06): "saving" while the relay pushes it,
// "local" while it lives only in the relay. Never red: the relay already uses it.
const syncSeen = {};   // key -> last sync state, for one log line per transition
function applySync(key, label, sync, err, btn, badge, syncBtn){
  const prev = syncSeen[key];
  syncSeen[key] = sync || "";
  if (badge){
    badge.classList.toggle("on", sync === "local");
    badge.title = sync === "local"
      ? "Not in the sheet yet, retried automatically." + (err ? " Last error: " + err : "")
      : "";
  }
  if (syncBtn) syncBtn.hidden = sync !== "local";
  if (btn && !btn.disabled){
    if (sync === "saving"){ btn.textContent = "SYNCING…"; btn.classList.add("syncing"); btn.classList.remove("ok"); }
    else if (btn.classList.contains("syncing")){
      btn.classList.remove("syncing");
      btn.textContent = sync === "local" ? "SAVE" : "SAVED ✓";
      if (!sync){ btn.classList.add("ok");
        setTimeout(()=>{ if (btn.textContent === "SAVED ✓"){ btn.textContent = "SAVE"; btn.classList.remove("ok"); } }, 4000); }
    }
  }
  if (sync === "local" && prev !== "local") log(label + ": sheet write delayed, kept in relay");
  if (!sync && prev === "local") log(label + " now in sheet");
}
async function syncNow(path, body, label){
  try{
    const r = await fetch(path, {method:"POST", cache:"no-store",
      headers:{"Content-Type":"application/json"}, body: JSON.stringify(body)});
    const d = await r.json();
    if (d.error) toast(label + ": " + d.error);
  }catch(e){ toast(label + ": relay unreachable"); }
}
```

- [ ] **Step 2: Schedule rows**

In `schedRow`, add the badge into the row-number cell and a SYNC NOW button into the action cell:

```js
  tr.innerHTML = `<td class="rn">${i}<span class="livebadge"></span><span class="syncbadge">IN RELAY</span></td>
    ...
    <td class="act"><button class="save">SAVE</button><button class="syncnow" hidden>SYNC NOW</button><button class="clear">CLEAR URL</button></td>`;
  ...
  tr.querySelector(".syncnow").addEventListener("click", ()=>
    syncNow("/schedule/sync", {row: Number(tr.dataset.sheetRow || i)}, "Schedule row " + i));
```

In `schedSave`, change the error branch to show `SAVE` (a validation error is not retried by pressing again) and the success branch to hand over to the poll:

```js
    if (d.error){ log("Schedule row " + row + ": " + d.error, "err"); toast("Schedule row " + row + ": " + d.error); btn.textContent = "SAVE"; return; }
    delete tr.dataset.dirty; tr.dataset.saved = Date.now();
    btn.textContent = "SYNCING…"; btn.classList.add("syncing");
    const live = tr.querySelector(".livebadge").textContent;
    log("Schedule row " + row + " saved" +
        (live ? `: feed ${live} picks it up on RELOAD ${live} / NEXT` : ""));
```

Keep the `catch` branch's `"RETRY"` (that is the relay itself being unreachable). Remove the `finally` block's SAVED ✓ timer (`applySync` owns it now) but keep `btn.disabled = false;`.

In `schedPoll`, inside `d.rows.forEach`, before `const busy = rowBusy(tr);`:

```js
      applySync("sched:" + row.sheetRow, "Schedule row " + row.row, row.sync, row.sync_error,
                tr.querySelector(".save"), tr.querySelector(".syncbadge"), tr.querySelector(".syncnow"));
```

- [ ] **Step 3: POV and qualifying**

Markup: next to `#povSave` add `<button id="povSyncNow" hidden>SYNC NOW</button><span id="povSync" class="syncbadge">IN RELAY</span>`; next to `#qualSave` add `<button id="qualSyncNow" hidden>SYNC NOW</button><span id="qualSync" class="syncbadge">IN RELAY</span>`. (Find both buttons with `grep -n 'id="povSave"\|id="qualSave"' src/director/director-panel.html`.)

`#povSave` handler: error branch `btn.textContent = "SAVE"`; success branch `btn.textContent = "SYNCING…"; btn.classList.add("syncing");` and log `"POV name + URL saved: name applies now, URL on POV RELOAD."`; drop the SAVED ✓ timer from `finally`.

```js
$("#povSyncNow").addEventListener("click", ()=>syncNow("/pov/sync", {}, "POV"));
$("#qualSyncNow").addEventListener("click", ()=>
  syncNow("/qualifying/sync", {row: Number($("#qualRow").dataset.sheetRow || 2)}, "Qualifying row"));
```

In `schedPoll`'s `/status` block, after reading `d`:

```js
    if (d.pov) applySync("pov", "POV", d.pov.sync, d.pov.sync_error,
                         $("#povSave"), $("#povSync"), $("#povSyncNow"));
```

`qualSave`: same three changes as `schedSave` (error → `SAVE`, success → `SYNCING…`, no SAVED ✓ timer). In `qualPoll`, after `const row = ...`:

```js
    applySync("qual", "Qualifying row", row && row.sync, row && row.sync_error,
              $("#qualSave"), $("#qualSync"), $("#qualSyncNow"));
```

- [ ] **Step 4: HUD info line**

In `setupPoll`, extend the `#setupInfo` text:

```js
  const unsynced = d.unsynced ? ` · ${d.unsynced} change${d.unsynced === 1 ? "" : "s"} not in the sheet yet, retried automatically` +
                                (d.unsynced_error ? ` (${d.unsynced_error})` : "") : "";
  $("#setupInfo").textContent = "HUD: " + (/* existing expression unchanged */) + unsynced;
```

- [ ] **Step 5: Verify rendered**

Invoke the `ui-visual-verification` skill. Serve the panel against a demo relay (wiki-screenshots Part B: demo profile + `tools/obs-sim.py`). Force the states with a stub webhook that fails: set `RACECAST_SHEET_PUSH_URL` to a local `python3 -m http.server`-style stub that answers `{"ok": false, "error": "test"}` for `schedule`, or temporarily point it at an unreachable `http://127.0.0.1:9/`. Save a schedule row and screenshot the row in `saving` and then `local` (badge "IN RELAY", SYNC NOW visible, nothing red), the POV row, the qualifying row and the HUD info line. Read each PNG back and check theme fit, alignment and that no banner appears. Record the marker: `python3 .claude/hooks/record_ui_verified.py src/director/director-panel.html`.

- [ ] **Step 6: Commit**

```bash
git add src/director/director-panel.html
git commit -m "feat(panel): show each save's sheet state instead of waiting for the webhook

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Docs and full gates

**Files:**
- Modify: `src/docs/wiki/Director.md` (banner table row from #779; schedule editor description), `src/docs/wiki/Sheet-Webhook.md` (timing paragraph), `src/docs/wiki/Relay-Mode.md` (takeover section near line 166), `src/relay/CLAUDE.md` (sheet-controls paragraph)

- [ ] **Step 1: Update the docs**

- `Director.md` banner row: SHEET SYNC FAILED now means an outdated Apps Script, or a HUD field/team write that did not land; a schedule/POV/qualifying save never raises it. Add a short paragraph under the schedule editor: SAVE answers at once and the relay uses the value at once; the button shows SYNCING… until the sheet has it; an amber IN RELAY badge means the sheet has not got it yet, the relay retries automatically and SYNC NOW retries at once; the HUD line counts such rows.
- `Sheet-Webhook.md`: replace the #781 sentences about panel saves with: a Schedule/Qualifying/POV save is pushed in the background (45 s per attempt, 3 attempts, then retried whenever the webhook answers again); Crew rows keep one 30 s attempt.
- `Relay-Mode.md` takeover section: one sentence that saves still marked IN RELAY are not in the sheet, so the HUD line's counter shows whether the sheet is complete before a takeover.
- `src/relay/CLAUDE.md`: in the sheet-controls paragraph, replace "Schedule/POV URL writes are synchronous" and the #780 clause with: Schedule/Qualifying/POV saves are async (`pending_saves.PendingSaves`, pinned in `ScheduleSource` through every refresh, one `sheet-save` worker, 45 s × 3, recovery re-queues `local` saves, red only for an outdated script); Crew writes stay synchronous via `_sync_push`.

- [ ] **Step 2: Full gates**

Run:
```bash
python3 tools/run-tests.py
python3 tests/test_pov.py
python3 tools/lint.py
python3 tools/build.py; echo "build exit $?"
```
Expected: `ALL TEST FILES PASS`, `ALL PASS`, `All checks passed!`, `build exit 0`. If `build.py` lists shipped scripts explicitly and fails on `pending_saves.py`, add it where the other `src/scripts/*.py` modules are listed and re-run.

- [ ] **Step 3: Commit**

```bash
git add src/docs/wiki/Director.md src/docs/wiki/Sheet-Webhook.md src/docs/wiki/Relay-Mode.md src/relay/CLAUDE.md
git commit -m "docs: describe async panel saves and the narrower banner

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
