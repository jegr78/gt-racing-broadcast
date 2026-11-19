# Multiclass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rank, time and show cars per class when a lobby runs several classes, on every timing screen.

**Architecture:** The roster carries each entry's class. `race_distance.Race` keeps its one overall order and adds class fields to every standings row from a class map `driver_race` hands it each tick. `driver_race` builds a `classes` block for the viewer payload (order, colour, lap counts, fastest lap per class); the OBS tower, the public page, the desks and the recording render from those fields. With fewer than two classes nothing changes.

**Tech Stack:** Python 3 stdlib, vanilla JS pages, stdlib test scripts (`python3 tests/<file>.py`), node for page JS tests where present.

**Spec:** `docs/superpowers/specs/2026-11-19-multiclass-design.md`

## Global Constraints

- Without a `Class` column, or with one class only, every payload and screen stays byte-for-byte as today: no `classes` block entries, no class row fields shown.
- Configuration headers: `Class` and `Class Color` (case-insensitive, header-located like `Cup Team`); the colour passes `sanitize_css_color`.
- Class order: the class of the overall leader first, then by the best overall position each class holds; entries without a class form one group at the end, named "".
- Lap count of the fastest class (the class of the overall leader) = the Schedule's `Laps` cell; other classes = Race Control's entry per stint, pre-filled from an optional Schedule column `Laps <Class>` (e.g. `Laps GT3`).
- Class lap count input: integer 1 to 999; refused otherwise (400).
- Tower: at most 15 rows per page including class header rows; pages switch every 15 s (`CYCLE_MS`), one class per page when all do not fit.
- Fastest-lap box cycles one class per 15 s, labelled with the class.
- Map: team colour fill, ring in the class colour; the EST dashed amber ring wins over the class ring.
- Public view allowlist adds exactly: `class`, `class_color`, `class_pos`, `class_gap_s`, `class_interval_s`, `class_lapped`, and the top-level `classes` (name, color, lap, laps).
- Repo rules: English code and docs, minimal comments, TDD (watch it fail), `python3 tools/run-tests.py`, `python3 tools/lint.py`, `python3 tests/test_pov.py`, `python3 tools/build.py` before the PR; ASCII argparse help; refresh `director-panel-drivers.png`, `console-race-control.png` (+ slides copy) and `driver-dashboard.png` in the change that alters them.

## Review Focus

- A `Class` column where one car's cell is empty: that car forms the "" group at the end, ranked 1 within it; it must not break the other classes (Task 2 test `t_an_entry_without_a_class_forms_its_own_group`).
- A car moved to another class mid-race (Sheet edit): its class position and gaps follow on the next tick, no stale group (Task 3 test `t_a_class_change_in_the_roster_reaches_the_next_tick`).
- A class lap count lower than the class leader's current lap: accepted (Race Control may fix it late), the header then reads e.g. `LAP 55 / 54`; no crash and no finish triggered by it (Task 4 test `t_a_class_lap_count_below_the_leader_lap_is_kept`).
- One class with more than 15 cars: its page shows the first 15 rows of that class, never overflows (Task 6 test `t_a_class_larger_than_a_page_is_cut_at_the_page`).
- A class name with markup (`<b>GT3</b>`): rendered as text everywhere (Task 6 and Task 7 tests assert textContent, Task 1 caps the name at 24 characters).

---

### Task 1: Class columns in the roster

**Files:**
- Modify: `src/relay/racecast-feeds.py` (`CUP_TEAM_HEADERS` area ~2288, `parse_config_roster` ~2374)
- Test: `tests/test_hud.py`

**Interfaces:**
- Produces: roster entries gain optional `"carClass": str` (stripped, max 24 chars) and `"classColor": str` (sanitized, "" when unset); keys absent when the column is missing. Constants `CLASS_HEADERS = ("class",)`, `CLASS_COLOR_HEADERS = ("class color", "class colour")`.

- [ ] **Step 1: Write the failing test** `t_config_roster_reads_class_and_class_color`: CSV header `Team,Number,Class,Class Color` with rows `Alpha #1,1,HYPERCAR,#c00` and `Beta #2,2,GT3,notacolor`; assert `roster["Alpha #1"]["carClass"] == "HYPERCAR"`, `["classColor"] == "#c00"`, `roster["Beta #2"]["classColor"] == ""`; a CSV without the columns has no `carClass` key.
- [ ] **Step 2: Run** `python3 tests/test_hud.py` — expect FAIL (KeyError).
- [ ] **Step 3: Implement** in `parse_config_roster`, mirroring `cup_team`.
- [ ] **Step 4: Run** `python3 tests/test_hud.py` — expect ALL PASS.
- [ ] **Step 5: Commit** `feat(timing): read Class and Class Color from the Configuration tab`.

### Task 2: Class standings in race_distance

**Files:**
- Modify: `src/scripts/race_distance.py` (`Race.__init__` ~190, `standings` ~780, lap timing ~608)
- Test: `tests/test_race_distance.py`

**Interfaces:**
- Consumes: nothing from Task 1 (plain dict).
- Produces: `Race.set_classes(classes: dict[str, str]) -> None` (entry -> class name, missing = ""). Every standings row gains `class`, `class_pos` (1-based within class), `class_gap_s`, `class_interval_s`, `class_lapped` (int), computed with `_behind(class_leader, e)` / `_behind(class_car_ahead, e)` exactly like the overall fields, including the EST and finished rules. With fewer than two distinct classes the rows carry `class: None` and the class fields `None`. `Race.fastest_by_class: dict[str, {entry, driver, ms}]` updated next to `self.fastest`. `standings()["classes"]`: ordered list of class names (Global Constraints order).

- [ ] **Step 1: Write the failing tests:**
  - `t_class_positions_and_gaps_follow_the_class`: A (H) leads, B (G) one lap behind A but ahead of C (G). Assert B `class_pos == 1`, `class_gap_s == 0`, `class_lapped == 0` while its overall `lapped == 1`; C `class_pos == 2`, `class_gap_s` equal to C's time behind B within 0.05 s, `class_interval_s` the same.
  - `t_an_entry_without_a_class_forms_its_own_group`: classes `{A: "H", B: "G"}`, C missing; `standings()["classes"] == ["H", "G", ""]`, C `class_pos == 1`.
  - `t_one_class_leaves_the_rows_unchanged`: all "GT3"; every row `class is None`.
  - `t_the_fastest_lap_is_kept_per_class`: two valid laps in two classes; `fastest_by_class` holds both, `fastest` the quicker.
- [ ] **Step 2: Run** `python3 tests/test_race_distance.py` — expect FAIL (`set_classes` missing).
- [ ] **Step 3: Implement** `set_classes`, the class pass after the overall order in `standings`, and `fastest_by_class` beside `self.fastest`; `reset` clears `fastest_by_class`.
- [ ] **Step 4: Run** `python3 tests/test_race_distance.py` — ALL PASS.
- [ ] **Step 5: Commit** `feat(timing): class position, gap and interval in the standings`.

### Task 3: Classes in the viewer payload

**Files:**
- Modify: `src/scripts/driver_race.py` (`_viewer` ~435, `_viewer_entry` ~195, `_PUBLIC_TOP`/`_PUBLIC_ROW` ~203, `tick`, fastest persistence `_saved_fastest`/`_fastest_path` ~185/400)
- Test: `tests/test_driver_race.py`

**Interfaces:**
- Consumes: Task 1 roster keys, Task 2 `set_classes`, row class fields, `fastest_by_class`, `standings()["classes"]`.
- Produces: `tick` and `_viewer` call `race.set_classes({entry: info.get("carClass", "")})` from the roster. Viewer rows copy the five class fields plus `class_color`. Top-level `classes`: list of `{"name", "color", "lap", "laps", "fastest"}` in class order (`lap` = class leader's laps, `laps` = class lap count from Task 4, `fastest` = `{entry, driver, team, ms}` or None); `[]` with fewer than two classes. The fastest-lap file stores `by_class` next to the overall entry and reloads it per track. `public_view` passes the allowlisted fields of Global Constraints.

- [ ] **Step 1: Write the failing tests:**
  - `t_the_dashboard_carries_classes_in_order`: roster with H and G; `dashboard()["classes"]` names `["H", "G"]`, colours from `classColor`, rows carry `class_pos`.
  - `t_a_class_change_in_the_roster_reaches_the_next_tick`: move B from G to H in the roster, tick; B's `class` and `class_pos` follow.
  - `t_the_public_view_keeps_the_class_fields`: `public_view` keeps the six row fields and `classes`, drops anything else.
  - `t_a_single_class_payload_is_unchanged`: one class; `classes == []` and no row has a non-None `class_pos`.
  - `t_class_fastest_laps_survive_a_restart`: persisted `by_class` reloads on the same track, not on another.
- [ ] **Step 2: Run** `python3 tests/test_driver_race.py` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `python3 tests/test_driver_race.py` and `python3 tests/test_driver_routes.py` — ALL PASS.
- [ ] **Step 5: Commit** `feat(timing): classes in the dashboard and public payload`.

### Task 4: Class lap counts

**Files:**
- Modify: `src/scripts/driver_race.py` (actions file, new `set_class_laps`), `src/relay/racecast-feeds.py` (`ScheduleSource._parse_laps` ~7432, `stint_switch_watch` ~15400, POST dispatch ~14870), `src/scripts/console_policy.py` (`_DRIVER_DESK_POST`)
- Test: `tests/test_driver_race.py`, `tests/test_driver_routes.py`, `tests/test_console.py`

**Interfaces:**
- Produces: `Service.set_class_laps(who, cls: str, laps: int | None, stint: str | None = None) -> dict` stores `class_laps[stint][cls]` in the actions file (None clears), audits `{"action": "class_laps", ...}`; `Service.class_laps(stint) -> dict[str, int]`. Route `POST /drivers/race/class-laps {class, laps}` for both desks (`("race", "class-laps")` in `_DRIVER_DESK_POST`), answering 400 for an unknown class or laps outside 1 to 999. `ScheduleSource` parses `Laps <Class>` headers into `self.class_laps[line] = {cls: n}`; `stint_switch_watch` passes the new on-air row's map as defaults (`Service.stint_switch(..., class_laps=...)`), which never overwrite a desk value. The fastest class's `laps` in `classes` = the existing lap count.

- [ ] **Step 1: Write the failing tests:**
  - `t_race_control_sets_a_class_lap_count` (driver_race): `set_class_laps("race_control:rc", "GT3", 54)` → `classes` entry GT3 `laps == 54`; audit line written.
  - `t_a_class_lap_count_below_the_leader_lap_is_kept`: GT3 leader on lap 55, set 54 → stored, `state` unchanged.
  - `t_class_laps_route_refuses_bad_input` (routes): 400 for `laps: 0`, `laps: "x"`, unknown class; 200 for both desks' paths.
  - `t_schedule_class_laps_prefill_the_desk`: a `Laps GT3` column pre-fills GT3 = 54 at the stint switch; a desk value set earlier stays.
- [ ] **Step 2: Run** the three test files — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the three test files and `python3 tests/test_pov.py` — ALL PASS.
- [ ] **Step 5: Commit** `feat(timing): class lap counts set by Race Control`.

### Task 5: Recording and snapshot page

**Files:**
- Modify: `src/scripts/race_recording.py` (sample row fields ~19, `observe` ~291, CSV columns), `src/console/race-snapshot.html`
- Test: `tests/test_race_recording.py`

**Interfaces:**
- Consumes: viewer row `class`, `class_pos`.
- Produces: sample rows and CSV carry `class` and `class_pos` (empty with one class); the snapshot page groups rows under class header rows when the sample has two or more classes.

- [ ] **Step 1: Write the failing test** `t_samples_and_csv_carry_the_class`: two classes; a stored sample row has `class == "GT3"`, `class_pos == 1`; the CSV header contains `class,class_pos`.
- [ ] **Step 2: Run** `python3 tests/test_race_recording.py` — FAIL.
- [ ] **Step 3: Implement**, including the snapshot grouping (class header row: name in its colour, rows below; textContent only).
- [ ] **Step 4: Run** `python3 tests/test_race_recording.py` — ALL PASS. Render the snapshot page with a two-class sample (Playwright) and look at it.
- [ ] **Step 5: Commit** `feat(timing): the recording keeps each car's class`.

### Task 6: OBS tower and map

**Files:**
- Modify: `src/obs/drivers.html`
- Test: `tests/test_driver_tower.py`

**Interfaces:**
- Consumes: payload `classes`, row class fields.
- Produces: `pages(entries, classes, max) -> [[{header: cls} | entry, ...], ...]` (pure, testable under node); the tower renders `pages[(Math.floor(Date.now()/CYCLE_MS)) % pages.length]`; class header row `.dd-class` with the class colour as left border, name, `LAP <lap> / <laps>` (or `LAP <lap>` without a count). In class mode the row position is `class_pos` and the value column uses `class_gap_s` / `class_interval_s` / `class_lapped` in the existing phases. The FL box shows `classes[k].fastest` with the class name, `k` cycling per `CYCLE_MS`. Map dots get `stroke` = class colour (width 0.8) unless EST.

- [ ] **Step 1: Write the failing tests:**
  - `t_classes_fit_on_one_page`: 6 H + 6 G → one page `[H header, 6 rows, G header, 6 rows]`.
  - `t_classes_page_when_they_do_not_fit`: 10 H + 10 G → two pages, one class each, header first.
  - `t_a_class_larger_than_a_page_is_cut_at_the_page`: 20 G → page of 15 entries (header + 14 rows).
  - `t_class_rows_show_class_position_and_gap`: a G row with `pos 7`, `class_pos 1`, `class_gap_s 0` reads position 1 and `Leader`.
  - `t_class_names_are_text`: a class `<b>GT3</b>` header has textContent `<b>GT3</b>` and no child element.
  - `t_one_class_renders_as_today`: `classes == []` → same rows as before (no `.dd-class`).
- [ ] **Step 2: Run** `python3 tests/test_driver_tower.py` — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `python3 tests/test_driver_tower.py` — ALL PASS; render tower and map with two classes at 1920x1080 and look; record the UI marker.
- [ ] **Step 5: Commit** `feat(timing): the tower groups and pages by class`.

### Task 7: Public timing page, desks and cockpit

**Files:**
- Modify: `src/console/timing.html`, `src/console/driver-desk.js`, `src/cockpit/cockpit.html`
- Test: `tests/test_driver_tower.py` (public page block), `tests/test_console.py` (desk source checks), `tests/timing-host/page_test.js` if the transport block changes (it should not)

**Interfaces:**
- Consumes: payload `classes`, row class fields, Task 4 route.
- Produces: public table grouped with class header rows and a filter select (All + each class) kept in `localStorage` (try/catch); desks render grouped and show one lap count input per class except the fastest (POST `/drivers/race/class-laps`); the cockpit timing card shows class position and class gap.

- [ ] **Step 1: Write the failing tests:**
  - `t_the_public_page_groups_by_class_and_filters`: grouped render has two class headers; filter "GT3" leaves only GT3 rows; names are textContent.
  - `t_the_desk_offers_a_lap_count_per_slower_class`: desk source posts `/drivers/race/class-laps` and renders one input per class after the first.
- [ ] **Step 2: Run** the test files — FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the test files — ALL PASS; render the public page (1000 and 390 px), both desks and the cockpit with two classes and look; record UI markers.
- [ ] **Step 5: Commit** `feat(timing): classes on the public page, the desks and the cockpit`.

Not in this plan: the driver change timing hint is not built yet; when it is, it takes `Laps - 1` from `Service.class_laps` for the car's class (spec section "Lap count per class").

### Task 8: Docs and screenshots

**Files:**
- Modify: `src/docs/wiki/Driver-Telemetry.md`, `src/docs/wiki/Sheet-Template.md` (Class, Class Color, `Laps <Class>`), `src/relay/CLAUDE.md` (driver dashboard paragraph), `src/docs/wiki/images/{director-panel-drivers,console-race-control,driver-dashboard}.png` and the slides copy of `console-race-control.png`

- [ ] **Step 1:** Document the columns, the class lap entry and the grouped screens.
- [ ] **Step 2:** Recapture the three images with the `wiki-screenshots` skill from a demo relay with two classes (demo profile, `tools/driver-replay.py`); restore the demo profile afterwards.
- [ ] **Step 3: Run** `python3 tools/run-tests.py`, `python3 tools/lint.py`, `python3 tests/test_pov.py`, `python3 tools/build.py` — all green.
- [ ] **Step 4: Commit** `docs(timing): multiclass in the wiki`.
