# Race Engineer reference pin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The Race Engineer desk compares against the best lap by default, lets any
engineer pin a lap as the shared reference, keeps private picks, shows how long the
fuel lasts in time, and shows `N/A` for every missing value.

**Architecture:** The relay-side live store (`race_engineer_live.Store`) owns the
pin (one per relay, scoped to the live session) and publishes it, the best lap id and
the average lap time in the existing snapshot that every desk polls twice per second.
A new authenticated `POST /console/race-engineer/pin` sets or clears it. The desk
(`race-engineer.html`) resolves reference A client-side: shared source > personal
pick > pin > best.

**Tech Stack:** Python 3 stdlib (relay, store, tests as runnable scripts), vanilla JS
in a single HTML page (JS checks run under node from Python tests).

**Spec:** `docs/superpowers/specs/2026-10-22-race-engineer-reference-pin-design.md`

## Global Constraints

- Edit only under `src/` (plus `tests/`, `docs/`). English only. No new dependencies.
- Tests run anywhere: no real IPs, no machine paths. TDD: failing test first.
- Every text-mode `subprocess` call passes `errors="replace"`; argparse help stays ASCII.
- Missing values read exactly `N/A` on the desk (producer decision: every value, no exceptions).
- Time left format: `≈ m:ss time left` below one hour, `≈ h:mm:ss time left` from one hour.
- Delta label wording: `Against best · Lap N`, `Against pinned · Lap N`, `Against your pick · Lap N`, `Against <source label> · Lap N`, each optionally followed by ` · through X m`.
- The pin ends with its session; it is never persisted to disk.
- After any Python change: `python3 tools/lint.py`. Before the PR: `python3 tools/run-tests.py` and `python3 tools/build.py`.
- UI changed: run the `ui-visual-verification` skill and refresh the Race Engineer wiki screenshots with the `wiki-screenshots` skill in the same PR.

## Review Focus

- A pinned lap whose detail is gone (evicted, dropped from the summaries, ineligible later): the delta reads `N/A`, the label still says pinned, the note says `Pinned lap detail no longer kept`; it never swaps in another lap. Test: Task 4 step 1 (case `gone`).
- Two engineers pin at nearly the same time: last write wins and both desks converge on the next poll. Test: Task 2 step 1 (`t_pin_last_write_wins`).
- Pinning a lap that is not pinnable (current provisional lap, out/pit lap, another session, a shared `r:` lap, unknown id): rejected with 409 `pin_rejected`, nothing changes; the button is disabled for those in the desk. Tests: Task 2 step 1, Task 3 step 1.
- Replay start/stop or a relay generation change: the pin disappears. Test: Task 2 step 1 (`t_pin_clears_on_new_session_and_generation`).
- The session changes while an engineer holds a personal pick from the old session: the pick is dropped as soon as it is no longer a usable lap for B. Test: Task 4 step 1 (step 8 of the scenario).

---

### Task 1: Average lap time in the snapshot

**Files:**
- Modify: `src/scripts/race_engineer_live.py` (beside `fuel_average`, `_empty`, `_publish`)
- Test: `tests/test_race_engineer_live.py`

**Interfaces:**
- Produces: `lap_time_average(laps, session_id) -> float | None` (rounded to 3 decimals); snapshot key `lap_avg_s` (float or `None`, present in `_empty()` and in compact snapshots).

- [ ] **Step 1: Write the failing test** `t_average_lap_time_uses_completed_eligible_laps_of_the_live_session`

```python
def lap(n, t, sid='A', state='confirmed', eligible=True):
    return {'lap': n, 'session_id': sid, 'state': state, 'comparison_eligible': eligible, 'time_s': t}
rows = [lap(1, 99.0, eligible=False), lap(2, 90.0), lap(3, 92.0), lap(4, None),
        lap(5, 80.0, state='unconfirmed'), lap(6, 70.0, sid='B')]
assert live.lap_time_average(rows, 'A') == 91.0
assert live.lap_time_average(rows, None) is None
assert live.lap_time_average([lap(1, 90.0, eligible=False)], 'A') is None
```

Also extend `t_confirmed_matches_offline_and_provisional_stays_observed`: `assert snap['lap_avg_s'] is not None and snap['lap_avg_s'] > 0`.

- [ ] **Step 2: Run** `python3 tests/test_race_engineer_live.py` — Expected: FAIL (`lap_time_average` missing).
- [ ] **Step 3: Implement** `lap_time_average` with the same filter as `fuel_average` (confirmed, comparison-eligible, live session) on a finite positive `time_s`; publish it as `lap_avg_s` in `_publish` and default `None` in `_empty`.
- [ ] **Step 4: Run** `python3 tests/test_race_engineer_live.py` — Expected: `ALL PASS`. Then `python3 tools/lint.py`.
- [ ] **Step 5: Commit** `feat(engineer): publish the average lap time of the live session`

### Task 2: Shared pin and eviction protection in the live store

**Files:**
- Modify: `src/scripts/race_engineer_live.py` (`Store.__init__`, `_empty`, `_publish`, `_reset_generation`, `_trim_details`; new methods)
- Test: `tests/test_race_engineer_live.py`

**Interfaces:**
- Consumes: Task 1 snapshot shape.
- Produces:
  - `class PinError(ValueError)` with attribute `code = "pin_rejected"`; message is operator-safe English (`Only a completed eligible lap of the live session can be pinned`).
  - `Store.set_pin(lap_id: str, by: str) -> dict` returns the new pin; `Store.clear_pin() -> None`.
  - Snapshot keys: `pin` = `None` or `{"lap_id": str, "session_id": str, "lap": int, "time_s": float, "by": str}`; `pin_revision` = int, incremented on every set and every clear (including automatic clears); `best_id` = id of the lap whose time is `best_s` (or `None`).
- Rules: pinnable = a lap in the summaries with `state == "confirmed"`, `comparison_eligible`, and `session_id ==` the published live `session_id`. Any other id raises `PinError`. All pin reads/writes under `self._lock`; `set_pin`/`clear_pin` also patch the current publication (new dict, `pin`, `pin_revision`) so the next `snapshot()` shows it without waiting for a packet. `_publish` clears the pin (revision +1) when a current lap exists and its `session_id` differs from the pin's; `_reset_generation` clears it too. `_trim_details` never evicts the pinned lap id or the published `best_id` (choose the oldest other candidate; if only protected laps remain over the limit, stop evicting).

- [ ] **Step 1: Write the failing tests**

`t_pin_is_shared_validated_and_last_write_wins` (feed `write_circle_recording`, confirmed laps 2, 3, 4):
```python
snap = store.snapshot(); laps = {r['lap']: r for r in snap['laps']}
assert snap['pin'] is None and snap['pin_revision'] == 0
assert next(r for r in snap['laps'] if r['id'] == snap['best_id'])['time_s'] == snap['best_s']
pin = store.set_pin(laps[3]['id'], 'Alex')
assert pin == {'lap_id': laps[3]['id'], 'session_id': snap['session_id'], 'lap': 3,
               'time_s': laps[3]['time_s'], 'by': 'Alex'}
assert store.snapshot()['pin'] == pin and store.snapshot()['pin_revision'] == 1
store.set_pin(laps[4]['id'], 'Sam')                       # last write wins
assert store.snapshot()['pin']['lap'] == 4 and store.snapshot()['pin_revision'] == 2
for bad in (snap['current']['id'], 'nope', 'r:x:1:2'):
    try: store.set_pin(bad, 'Alex'); raise AssertionError(bad)
    except live.PinError as exc: assert exc.code == 'pin_rejected'
assert store.snapshot()['pin_revision'] == 2              # rejected pins change nothing
store.clear_pin(); assert store.snapshot()['pin'] is None and store.snapshot()['pin_revision'] == 3
```
Also reject an ineligible lap (the out lap / first lap if present in `snap['laps']` with `comparison_eligible` False).

`t_pin_clears_on_new_session_and_generation`: reuse the `t_new_session_clears_shifts_even_with_the_same_car_and_lap_number` fixture; after the first laps confirm, pin one; push the `best_ms=-1` packet that starts a new session; `assert store.snapshot()['pin'] is None` and `pin_revision` increased. Then pin again and call `store._reset_generation(new_source=True)` (then `flush()`): pin is `None`.

`t_pinned_and_best_laps_survive_detail_eviction`: `write_circle_recording(d, lap_secs=(15.0,) + tuple(20.0 + i * .1 for i in range(10)), n=100)` with `details_max=2`. Feed packets one at a time until lap 3 is confirmed, `set_pin(<lap 3 id>, 'Alex')`, feed the rest. Assert `store.detail(best_id)['state'] == 'ready'`, `store.detail(<lap 3 id>)['state'] == 'ready'`, another old lap is `'evicted'`, `store.stats()['details'] <= 3`.

- [ ] **Step 2: Run** `python3 tests/test_race_engineer_live.py` — Expected: FAIL (`set_pin` missing).
- [ ] **Step 3: Implement** `PinError`, `set_pin`, `clear_pin`, the snapshot keys and the eviction rule as listed in Interfaces.
- [ ] **Step 4: Run** `python3 tests/test_race_engineer_live.py` — Expected: `ALL PASS`; then `python3 tools/lint.py`.
- [ ] **Step 5: Commit** `feat(engineer): shared reference pin scoped to the live session`

### Task 3: Pin endpoint, policy and display name

**Files:**
- Modify: `src/scripts/race_engineer_api.py` (`Service.pin`)
- Modify: `src/scripts/console_policy.py:115` (add the POST rule)
- Modify: `src/relay/racecast-feeds.py` (`_console_gate` race-engineer block ≈ line 12032; new pure helper beside `cockpit_display_name` ≈ line 6754)
- Test: `tests/test_race_engineer_routes.py`, `tests/test_race_engineer_api.py`

**Interfaces:**
- Consumes: `Store.set_pin`, `Store.clear_pin`, `PinError` (Task 2).
- Produces:
  - `Service.pin(lap_id: str | None, by: str) -> dict` → `{"ok": True, "pin": <pin or None>}`; `None` clears.
  - `engineer_display_name(members, subject) -> str`: the first crew member name whose `asset_key(name) == subject`, else `subject`. `members` is `CrewSource.get_members()` (name at index 0).
  - HTTP: `POST /console/race-engineer/pin`, JSON body `{"lap_id": "<id>"}` or `{"lap_id": null}`. Responses: 200 `{"ok": true, "pin": ...}`; 400 `invalid_body` (not a bounded JSON object ≤ 4096 bytes, `lap_id` neither string nor null); 409 `pin_rejected`; 401/403/503 exactly as the GET routes (identity, `forbidden`, `feature_disabled`, `analysis_unavailable`).
  - `console_policy.decide(roles, ["race-engineer", "pin"], "POST", _)` → ALLOW for race_engineer (and producer, who inherits it); every other POST under race-engineer stays not allowed.
- Rules: the gate reads the body itself (Content-Length bounded, `application/json`) and sets `self._body_consumed = True` so `do_POST` does not drain again. It runs the same `engineer_access._authorized_doc(roles)` check as the GETs before calling `service.pin`. Any other non-GET race-engineer path keeps today's 404 `not_found`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_race_engineer_api.py`, `t_pin_delegates_and_reports_rejection`: a `Service` over a fake live object recording `set_pin`/`clear_pin` calls; `pin('L3', 'Alex')` returns `{'ok': True, 'pin': <fake pin>}`; `pin(None, 'Alex')` calls `clear_pin`; a fake raising `PinError` propagates `PinError`.

In `tests/test_race_engineer_routes.py`, `t_pin_route_is_engineer_gated_and_validated`: build the handler like `t_every_request_checks_identity_current_role_and_enablement`, passing a `telemetry_store=SimpleNamespace(engineer=SimpleNamespace(api=FakeService()))` whose `pin` returns `{'ok': True, 'pin': {'lap_id': lap_id, 'by': by}}` and raises `race_engineer_live.PinError` for `'bad'`. Extend the local `request` helper with a JSON `body`. Assert:
```python
assert request(None, 'pin', 'POST', {'lap_id': 'L3'})[0] == 401
assert request('engineer', 'pin', 'POST', {'lap_id': 'L3'})[1]['error']['code'] == 'feature_disabled'
store.update(enabled=True, expected=0)
status, body = request('engineer', 'pin', 'POST', {'lap_id': 'L3'})
assert status == 200 and body['pin'] == {'lap_id': 'L3', 'by': 'Engineer'}   # crew display name, not the key
assert request('owner', 'pin', 'POST', {'lap_id': None})[0] == 200
assert request('unknown', 'pin', 'POST', {'lap_id': 'L3'})[1]['error']['code'] == 'forbidden'
assert request('engineer', 'pin', 'POST', {'lap_id': 'bad'})[0] == 409
assert request('engineer', 'pin', 'POST', {'lap_id': 7})[0] == 400
assert request('engineer', 'pin', 'POST', ['L3'])[0] == 400
assert request('engineer', 'other', 'POST', {})[1]['error']['code'] == 'not_found'
```
Keep the existing `POST` → 404 assertion on the page route.

Add `t_engineer_display_name_uses_the_crew_name`: `engineer_display_name([('Alex Example', ...)], asset_key('Alex Example')) == 'Alex Example'`; unknown key returns the key.

- [ ] **Step 2: Run** `python3 tests/test_race_engineer_routes.py` and `python3 tests/test_race_engineer_api.py` — Expected: FAIL.
- [ ] **Step 3: Implement** `Service.pin`, the policy rule, `engineer_display_name`, and the POST branch in the gate per Interfaces.
- [ ] **Step 4: Run** both test files plus `python3 tests/test_console.py` — Expected: `ALL PASS`; then `python3 tools/lint.py`.
- [ ] **Step 5: Commit** `feat(engineer): authenticated pin route for the shared reference`

### Task 4: Desk reference precedence, pin controls and delta label

**Files:**
- Modify: `src/console/race-engineer.html` (`selections`, `showDelta`, Lap A handlers, markup next to `#lap-a`; remove `AUTO_A_WAIT_MS` and `state.autoA`)
- Test: `tests/test_engineer_live_trend.py` (replace `t_follow_live_compares_each_lap_with_the_lap_just_completed`; update `_HARNESS` state)

**Interfaces:**
- Consumes: snapshot `pin`, `pin_revision`, `best_id` (Task 2); `POST /race-engineer/pin` via the existing `request(route, query, method, body)` (Task 3).
- Produces (page globals the tests drive):
  - `state.pick`: `null` or `{id, best, pinRev}` (the best lap id and `pin_revision` at pick time).
  - `state.aKind`: `''`, `'best'`, `'pinned'`, `'pick'`, `'source'`.
  - `selections()` resolves A: (1) `state.refSource` set → keep today's source behaviour, kind `source`; (2) `state.pick` kept only while its id is still a usable lap for B, the fastest usable id still equals `pick.best`, and `pick.pinRev === snapshot.pin_revision`, kind `pick`; otherwise `state.pick = null`; (3) pin applies when B's `session_id === pin.session_id`: A = `pin.lap_id` even if the lap is no longer usable, kind `pinned`; (4) fastest usable lap, kind `best`; none → `''`.
  - `referenceText()` → the delta label prefix per Global Constraints (Lap number from the resolved row or the pin).
  - `pinControls()` renders `<button id="pin-toggle">` (`Pin lap A` / `Unpin lap N`, disabled unless A is a confirmed eligible lap of the live session or a pin exists) and `<p id="pin-note">` (`Pinned by <by> · Lap N`, or `Pinned lap detail no longer kept` when the pinned lap's summary is missing or `detail_state !== 'ready'`, or `Pin could not be saved` after a failed POST).
  - Lap A `change`: empty value → `state.pick = null`; otherwise `state.pick = {id, best: <fastest usable id>, pinRev: snapshot.pin_revision}`.

- [ ] **Step 1: Write the failing test** `t_reference_prefers_source_pick_pin_then_best` (harness `state` gains `pick: null, aKind: ''`; drop `autoA`; snapshots carry `pin`/`pin_revision`). Scenario, printing `state.a + '/' + state.aKind` after each `selections()`:

1. laps L3 90, L4 92; live 5; no pin → `L3/best`
2. pick L4 (`state.pick={id:'L4',best:'L3',pinRev:0}`) → `L4/pick`
3. L5 95 arrives, live 6 → `L4/pick`
4. L6 88 arrives (new best), live 7 → `L6/best`
5. pin `{lap_id:'L4',session_id:'S',lap:4}`, `pin_revision:1` → `L4/pinned`
6. L7 85 arrives (new best), live 8 → `L4/pinned`
7. pick L5 with `pinRev:1` → `L5/pick`; then `pin:null, pin_revision:2` → `L7/best`
8. follow off, session `S0` with laps `S0L1` 80 and `S0L2` 79 (same car/track), B = `S0L1`, pick `{id:'L5',…}` from session S → `S0L2/best`
9. `refSource='R'`, `a='r:x'`, then a new best arrives → `r:x/source`

Expected: `L3/best|L4/pick|L4/pick|L6/best|L4/pinned|L4/pinned|L5/pick|L7/best|S0L2/best|r:x/source`.

Add case `gone`: pin on `L2` which is not in `state.laps` → `L2/pinned` and `referenceText()` is `Against pinned · Lap 2`.

Add `t_delta_label_names_the_reference`: `showDelta(1.234, 430)` with kind `pinned` on lap 17 → `#delta-note` = `Against pinned · Lap 17 · through 430 m`; kind `best` lap 23 with `showDelta(null)` → `Against best · Lap 23`; kind `source` → `Against ref · Lap 3` (harness `referenceLabel` returns `ref`).

Update `t_delta_trend_arrow_follows_the_hud_rule` only where it touched `autoA`.

- [ ] **Step 2: Run** `python3 tests/test_engineer_live_trend.py` — Expected: FAIL (requires node; if node is absent install it before continuing, the JS checks are the test).
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** `python3 tests/test_engineer_live_trend.py` and every `tests/test_engineer_*.py` — Expected: `ALL PASS`.
- [ ] **Step 5: Commit** `feat(engineer): best-lap default, shared pin and private pick on the desk`

### Task 5: Fuel time left and N/A everywhere on the desk

**Files:**
- Modify: `src/console/race-engineer.html` (markup of the fuel tile, `num`, `lapTime`, `instruments`, `renderShifts`, `cellValue` callers, `stop`, every `Unavailable` value literal)
- Test: `tests/test_engineer_live_trend.py` and every test asserting the desk's `Unavailable` (grep `tests/` for `Unavailable` near `race-engineer`)

**Interfaces:**
- Consumes: snapshot `lap_avg_s` (Task 1).
- Produces: `const NA='N/A'`; `duration(s) -> string` (`m:ss` / `h:mm:ss`, whole seconds rounded, non-finite or negative → `NA`); new `<small id="fuel-time-note">` directly under `#fuel-avg-note`.
- Rules: `#fuel-time-note` = `≈ ${duration(left * lap_avg_s)} time left` when laps left and `lap_avg_s` are finite, else `N/A`. The fuel note `Fuel level unavailable` becomes `N/A`; `No eligible lap with fuel use` stays (explains a state). Unit hiding (`data-unit-for`) compares against `NA`.

- [ ] **Step 1: Write the failing tests**

Extend `t_average_fuel_estimates_the_laps_left_on_the_current_fuel` with `lap_avg_s` and `#fuel-time-note`:
```
[3.0612, 12, 85.7, 90]  -> '3.06/≈ 28.0 laps left/≈ 42:00 time left'
[3.0612, 12, null, 90]  -> '3.06/N/A/N/A'
[null, 0, 40, 90]       -> 'N/A/No eligible lap with fuel use/N/A'
[1.0, 3, 100, 90]       -> '1.00/≈ 100.0 laps left/≈ 2:30:00 time left'
[2.5, 3, 40, null]      -> '2.50/≈ 16.0 laps left/N/A'
```
Change the expected `Unavailable` to `N/A` in `t_lap_clocks_use_minutes_and_round_across_boundaries`, `t_steering_reads_degrees_with_direction`, the shift-points test and the harness `num`.

Add `t_desk_shows_na_for_every_missing_value`: `assert 'Unavailable' not in html` except inside the prose strings `Shared reference unavailable`, `Live analysis unavailable`, `Analysis temporarily unavailable`, `Telemetry source unavailable`, `Program unavailable`, `Crew chat temporarily unavailable` (strip those, then assert no `Unavailable`/`'Unavailable'` remains); and `assert html.count('>N/A<') >= 14` for the static placeholders.

- [ ] **Step 2: Run** `python3 tests/test_engineer_live_trend.py` — Expected: FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** `python3 tests/test_engineer_live_trend.py` and all `tests/test_engineer_*.py` — Expected: `ALL PASS`.
- [ ] **Step 5: Commit** `feat(engineer): fuel time left and N/A for missing values`

### Task 6: Docs, screenshots, visual check, gates

**Files:**
- Modify: `src/docs/wiki/Race-Engineer.md` (reference paragraph incl. Follow live, pin, time left, `N/A` wording)
- Modify: `src/docs/wiki/images/console-race-engineer-desktop.png`, `console-race-engineer-replay.png` (and their copies under `src/docs/slides/assets/img/` if present)

- [ ] **Step 1:** Rewrite the wiki text: default A is the best lap; the shared pin (who can set it, ends with the session, `Pinned by`); private pick until the next best lap or a pin change; Follow live no longer sets A to the previous lap; the delta label wording; time left; missing values read `N/A`. Mechanism only, no crew procedure.
- [ ] **Step 2:** Run the `ui-visual-verification` skill against the desk with a pinned and an unpinned state; look at both.
- [ ] **Step 3:** Recapture the two screenshots with the `wiki-screenshots` skill (demo profile, dev build).
- [ ] **Step 4:** Run `python3 tools/run-tests.py`, `python3 tools/lint.py`, `python3 tools/build.py` — Expected: all green.
- [ ] **Step 5: Commit** `docs(engineer): reference pin, time left and N/A on the desk`
