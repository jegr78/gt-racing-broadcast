#!/usr/bin/env python3
"""Race distance, gaps, driver changes and finish from driver telemetry (#1112).
Run: python3 tests/test_race_distance.py"""
import math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "scripts"))
import race_distance as rd  # noqa: E402

R = 500.0
L = 2 * math.pi * R
TRACE = [{"d": L * i / 720, "x": R * math.cos(2 * math.pi * i / 720),
          "z": R * math.sin(2 * math.pi * i / 720)} for i in range(721)]
HZ = 10


def _pos(station, box=False):
    a = 2 * math.pi * (station % L) / L
    r = R + (25.0 if box else 0.0)          # the pit lane runs 25 m outside the line
    return r * math.cos(a), r * math.sin(a)


class Car:
    """A car in one lobby: `along` is metres past the finish line (negative on the grid);
    GT7's lap counter reads 0 on the grid and steps at every crossing of the line."""

    def __init__(self, race, entry, conn, grid_s, name="Driver"):
        self.race, self.entry, self.conn, self.name = race, entry, conn, name
        self.along = grid_s - L                   # the grid lies behind the line
        self.box = False
        self.speed = 0.0

    def lap(self):
        return max(0, math.floor(self.along / L) + 1)

    def send(self, t, position=None):
        x, z = _pos(self.along, self.box)
        lap = self.lap()
        if lap != getattr(self, "_lap", lap):          # GT7 reports a lap time after the line
            self.last_ms = getattr(self, "forge", None) or (
                round((t - self._line_t) * 1000) if hasattr(self, "_line_t") else -1)
            self._line_t = t
        elif not hasattr(self, "_line_t") and lap >= 1:
            self._line_t = t
        self._lap = lap
        self.race.update(self.entry, self.conn, self.name, {
            "t": t, "lap": lap, "x": x, "z": z, "speed_kmh": self.speed * 3.6,
            "race_position": position, "on_track": True, "paused": False, "loading": False,
            "last_ms": getattr(self, "last_ms", -1), "fuel_l": getattr(self, "fuel", 50.0)})


def _drive(race, cars, speeds, t0, seconds, positions=None):
    """Advance every car at its speed (m/s, or None to stand) for `seconds`."""
    t = t0
    for _ in range(int(seconds * HZ)):
        t += 1.0 / HZ
        for car, v in zip(cars, speeds, strict=True):
            car.speed = v or 0.0
            car.along += (v or 0.0) / HZ
            car.send(t, positions(car) if positions else None)
    return t


def _row(race, entry, now):
    return next(r for r in race.standings(now)["entries"] if r["entry"] == entry)


def t_formation_lap_sets_the_start_and_the_starting_gaps():
    race = rd.Race(TRACE, formation=True)
    a, b, c = (Car(race, e, e.lower(), grid) for e, grid in (("A", L - 20), ("B", L - 40), ("C", L - 60)))
    t = _drive(race, [a, b, c], [20, 20, 20], 0.0, 0.5)
    snap = race.standings(t)
    assert snap["state"] == "waiting" and all(r["distance_m"] is None for r in snap["entries"]), snap
    t = _drive(race, [a, b, c], [40, 40, 40], t, L / 40 + 3)       # the formation lap, then lap 2
    order = [r["entry"] for r in race.standings(t)["entries"]]
    assert order == ["A", "B", "C"], order
    gap_b, gap_c = _row(race, "B", t)["gap_s"], _row(race, "C", t)["gap_s"]
    assert abs(gap_b - 0.5) < 0.15 and abs(gap_c - 1.0) < 0.15, \
        f"20 m apart at 40 m/s: 0.5 s and 1.0 s at the line: {gap_b} {gap_c}"
    assert abs(_row(race, "C", t)["interval_s"] - 0.5) < 0.15
    assert _row(race, "A", t)["gap_s"] == 0 and _row(race, "A", t)["laps"] == 0, \
        "the formation lap is no race lap"


def t_without_formation_the_race_starts_on_lap_one():
    race = rd.Race(TRACE, formation=False)
    a, b = Car(race, "A", "a", L - 10), Car(race, "B", "b", L - 50)
    t = _drive(race, [a, b], [30, 30], 0.0, 2)
    rows = race.standings(t)["entries"]
    assert [r["entry"] for r in rows] == ["A", "B"] and rows[0]["distance_m"] is not None
    assert -15 < rows[0]["distance_m"] < 60, rows[0]["distance_m"]


def t_a_lapped_car_reads_plus_n_laps_and_a_pit_stop_shows_box():
    race = rd.Race(TRACE, formation=False)
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 6)
    t = _drive(race, [a, b], [60, 30], 0.0, L / 30 * 1.2)            # A gains more than a lap
    row = _row(race, "B", t)
    assert row["lapped"] == 1 and row["gap_s"] is None and row["status"] == "racing", row
    b.box = True
    t = _drive(race, [a, b], [60, None], t, 5)
    assert _row(race, "B", t)["status"] == "box", _row(race, "B", t)
    b.box = False
    t = _drive(race, [a, b], [60, 30], t, 2)
    assert _row(race, "B", t)["status"] == "racing" and _row(race, "B", t)["pit_stops"] == 1


def t_a_spun_car_on_the_line_is_not_in_the_box():
    race = rd.Race(TRACE, formation=False)
    a = Car(race, "A", "a", L - 15)
    t = _drive(race, [a], [30], 0.0, 3)
    t = _drive(race, [a], [None], t, 6)                                 # stopped on the racing line
    assert _row(race, "A", t)["status"] == "racing", "standing on track is no box stop"


def t_no_data_after_the_sender_goes_quiet():
    race = rd.Race(TRACE, formation=False)
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 50)
    t = _drive(race, [a, b], [30, 30], 0.0, 3)
    t = _drive(race, [a], [30], t, rd.NO_DATA_S + 1)
    row = _row(race, "B", t)
    assert row["status"] == "no_data" and row["gap_s"] is None, row


def t_lap_count_finish_ends_the_leader_then_each_car_at_its_next_crossing():
    race = rd.Race(TRACE, formation=False, finish_mode="laps", lap_count=2)
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 400)
    t = _drive(race, [a, b], [50, 50], 0.0, (2 * L + 5) / 50 + 1)       # A completes lap 2
    snap = race.standings(t)
    assert snap["state"] == "finishing" and _row(race, "A", t)["finished"], snap["state"]
    assert not _row(race, "B", t)["finished"]
    t = _drive(race, [a, b], [50, 50], t, 10)
    snap = race.standings(t)
    assert snap["state"] == "finished" and [r["entry"] for r in snap["entries"]] == ["A", "B"]
    frozen = _row(race, "B", t)["distance_m"]
    t = _drive(race, [a, b], [50, 80], t, 20)
    assert _row(race, "B", t)["distance_m"] == frozen, "a finished car's distance is frozen"


def t_the_corrected_leader_takes_the_flag_first():
    race = rd.Race(TRACE, formation=False, finish_mode="laps", lap_count=2)
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 60)
    t = _drive(race, [a, b], [50, 50], 0.0, 2)
    race.correct("B", 200.0)                                  # Race Control puts B ahead
    t = _drive(race, [a, b], [50, 50], t, (2 * L + 60) / 50)
    assert _row(race, "B", t)["finished"] and not _row(race, "A", t)["finished"], \
        "the car the tower shows in front gets the chequered flag"
    t = _drive(race, [a, b], [50, 50], t, L / 50 + 2)
    assert [r["entry"] for r in race.standings(t)["entries"]] == ["B", "A"], \
        "no car is classified with more laps than the winner took the flag on"


def t_a_lapped_car_taking_the_flag_stays_behind_a_lead_lap_car_still_running():
    race = rd.Race(TRACE, formation=False, finish_mode="laps", lap_count=2)
    a, b, c = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 6), Car(race, "C", "c", L - 100)
    t = _drive(race, [a, b, c], [60, 29.5, 50], 0.0, 110)    # A wins, B (lapped) crosses next
    assert _row(race, "A", t)["finished"] and _row(race, "B", t)["finished"], race.standings(t)
    assert not _row(race, "C", t)["finished"] and _row(race, "C", t)["laps"] == _row(race, "B", t)["laps"]
    order = [r["entry"] for r in race.standings(t)["entries"]]
    assert order == ["A", "C", "B"], f"C reached that lap long before B took the flag: {order}"


def t_the_winner_stays_first_when_its_flag_is_off_a_whole_race_lap():
    race = rd.Race(TRACE, formation=False, multi_lobby=True, finish_mode="laps")
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 60)
    t = _drive(race, [a, b], [50, 50], 0.0, 20)
    ea, eb = race._entries["A"], race._entries["B"]
    ea.distance, eb.distance = L + 900.0, L + 850.0          # the final lobby's line sits mid-lap
    ea.finished_at = race._finishing_at = t
    eb.ts, eb.ds = [t - 30, t - 10, t - 1], [L - 100.0, L + 10.0, eb.distance]
    order = [r["entry"] for r in race.standings(t)["entries"]]
    assert order == ["A", "B"], f"the car that took the flag first is the winner: {order}"


def t_the_lap_count_follows_gt7s_counter_including_the_formation_lap():
    race = rd.Race(TRACE, formation=True, finish_mode="laps", lap_count=3)
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 400)
    t = _drive(race, [a, b], [50, 50], 0.0, (2 * L + 15) / 50 + 1)      # formation + 1 race lap
    assert race.standings(t)["state"] == "racing", "two of GT7's three laps are done"
    t = _drive(race, [a, b], [50, 50], t, L / 50)
    assert race.standings(t)["state"] in ("finishing", "finished") and _row(race, "A", t)["finished"], \
        "GT7's lap 3 completed, the formation lap included"


def t_final_lap_by_time_and_finish_by_hand():
    race = rd.Race(TRACE, formation=False, finish_mode="time")
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 300)
    t = _drive(race, [a, b], [50, 50], 0.0, 30)
    race.final_lap(t)
    assert race.standings(t)["state"] == "final_lap"
    t = _drive(race, [a, b], [50, 50], t, L / 50 + 8)
    assert race.standings(t)["state"] == "finished"
    other = rd.Race(TRACE, formation=False, finish_mode="time")
    c = Car(other, "C", "c", L - 15)
    t = _drive(other, [c], [50], 0.0, 10)
    other.finish(t)
    d = _row(other, "C", t)["distance_m"]
    t = _drive(other, [c], [50], t, 10)
    assert other.standings(t)["state"] == "finished" and _row(other, "C", t)["distance_m"] == d


def t_gt7_position_disagreeing_with_the_distance_is_flagged():
    race = rd.Race(TRACE, formation=False)
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 60)
    t = _drive(race, [a, b], [30, 30], 0.0, 3, positions=lambda car: 2 if car.entry == "A" else 1)
    assert _row(race, "A", t)["position_mismatch"] and _row(race, "B", t)["position_mismatch"]


def t_endurance_change_continues_from_the_old_cars_anchor():
    race = rd.Race(TRACE, formation=False, multi_lobby=True)
    old, rival = Car(race, "A", "a1", L - 15, "Max"), Car(race, "B", "b1", L - 10, "Ann")
    t = _drive(race, [old, rival], [50, 50], 0.0, 60)
    race.open_change_window(t)
    old.box = True
    t = _drive(race, [old, rival], [None, 50], t, 3)
    anchor = _row(race, "A", t)["distance_m"]
    new = Car(race, "A", "a2", L - 15, "Lea")                           # the next lobby's car
    t = _drive(race, [new, rival], [40, 50], t, 4)                     # lap 1 of the new lobby
    assert _row(race, "A", t)["distance_m"] == anchor, "lap 1 of a follow-up lobby never counts"
    new.box = True
    t = _drive(race, [new, rival], [None, 50], t, 3)
    new.box = False
    t = _drive(race, [new, rival], [40, 50], t, 10)
    row = _row(race, "A", t)
    assert row["driver"] == "Lea" and abs(row["distance_m"] - (anchor + 400)) < 25, (row, anchor)
    assert row["pit_stops"] == 0, "a driver change is no pit stop"


def t_a_driver_staying_on_continues_into_the_next_lobby():
    race = rd.Race(TRACE, formation=False, multi_lobby=True)
    car, rival = Car(race, "A", "a1", L - 15, "Max"), Car(race, "B", "b1", L - 10, "Ann")
    t = _drive(race, [car, rival], [50, 50], 0.0, 60)
    race.open_change_window(t)
    car.box = True
    t = _drive(race, [car, rival], [None, 50], t, 3)
    anchor = _row(race, "A", t)["distance_m"]
    car.along = -5.0                                  # the next lobby: same console, counter 0
    t = _drive(race, [car, rival], [None, 50], t, 3)
    car.box = False
    t = _drive(race, [car, rival], [40, 50], t, 10)
    row = _row(race, "A", t)
    assert row["driver"] == "Max" and abs(row["distance_m"] - (anchor + 400)) < 25, (row, anchor)


def t_a_box_stop_outside_the_change_window_changes_nobody():
    race = rd.Race(TRACE, formation=False, multi_lobby=True)
    old = Car(race, "A", "a1", L - 15, "Max")
    t = _drive(race, [old], [50], 0.0, 30)
    old.box = True
    t = _drive(race, [old], [None], t, 3)
    other = Car(race, "A", "a2", L - 15, "Lea")
    other.box = True
    t = _drive(race, [other], [None], t, 3)
    other.box = False
    t = _drive(race, [other], [40], t, 5)
    assert _row(race, "A", t)["driver"] == "Max", "no change without the stint switch"


def t_distance_stays_continuous_when_the_line_starts_mid_lap():
    shifted = [{"d": p["d"], "x": q["x"], "z": q["z"]}
               for p, q in zip(TRACE, TRACE[240:] + TRACE[1:241], strict=True)]
    race = rd.Race(shifted, formation=False)
    a = Car(race, "A", "a", L - 15)
    t, last = 0.0, None
    for _ in range(int(2.5 * L / 50)):
        t = _drive(race, [a], [50], t, 1)
        d = _row(race, "A", t)["distance_m"]
        assert last is None or 40 < d - last < 60, f"one second at 50 m/s moved {d - last} m"
        last = d


def t_implausible_samples_change_nothing():
    race = rd.Race(TRACE, formation=False, finish_mode="laps", lap_count=3)
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 50)
    t = _drive(race, [a, b], [30, 30], 0.0, 3)
    before = _row(race, "B", t)
    x, z = _pos(b.along)
    bad = [{"x": float("nan"), "z": z}, {"x": float("inf"), "z": z}, {"x": 1e9, "z": 1e9},
           {"x": x, "z": z, "speed_kmh": float("nan")}, {"x": x, "z": z, "lap": 32767},
           {"x": x, "z": z, "t": t - 30}, {"x": x, "z": z, "lap": 2.5}]
    for over in bad:
        sample = dict({"t": t + 0.05, "lap": b.lap(), "speed_kmh": 108.0, "race_position": None},
                      **over)
        race.update("B", "b", "Driver", sample)
        row = _row(race, "B", t)
        assert row["distance_m"] == before["distance_m"] and race.standings(t)["state"] == "racing", \
            f"{over} must be dropped: {row}"
    assert race._cars["b"].lap == b.lap(), "a dropped sample leaves the lap counter alone"
    waiting = rd.Race(TRACE, formation=True)
    c = Car(waiting, "C", "c", L - 30)
    t = _drive(waiting, [c], [20], 0.0, 1)
    x, z = _pos(c.along)
    waiting.update("C", "c", "Driver", {"t": t - 1, "lap": c.lap(), "x": x, "z": z,
                                        "speed_kmh": 1.0, "race_position": 9})
    assert waiting._cars["c"].position is None and waiting._cars["c"].speed > 15, \
        "a sample older than the car's last one changes nothing"


def t_history_and_quiet_connections_stay_bounded():
    race = rd.Race(TRACE, formation=False)
    a = Car(race, "A", "a", L - 15)
    t = 0.0
    for _ in range(rd.MAX_HISTORY + 300):            # the first 100 stand on the grid
        t += 0.001                                   # a burst far denser than HISTORY_S trims
        a.along += 0.05
        a.speed = 50
        a.send(t)
    assert len(race._entries["A"].ts) == rd.MAX_HISTORY, len(race._entries["A"].ts)
    for i in range(30):
        race.update("A", f"gone{i}", "X", {"t": t, "lap": 1, "x": 0.0, "z": 0.0, "speed_kmh": 0})
    _drive(race, [a], [50], t, rd.CAR_FORGET_S + rd.PRUNE_EVERY_S + 1)
    assert set(race._cars) == {"a"}, f"quiet connections no entry follows are dropped: {set(race._cars)}"


def t_a_long_gap_mid_lap_keeps_every_lap():
    race = rd.Race(TRACE, formation=False)
    a = Car(race, "A", "a", L - 15)
    t = _drive(race, [a], [50], 0.0, 10)
    a.speed = 5.0
    a.send(t)                                          # a slow last sample before the outage
    a.along += 0.8 * L - 400                           # 0.67 laps unseen, landing mid-lap
    t += (0.8 * L - 400) / 50
    a.speed = 50.0
    a.send(t)
    assert abs(_row(race, "A", t)["distance_m"] - a.along) < 5, (_row(race, "A", t), a.along)


def t_practice_before_the_race_restarts_the_entry():
    race = rd.Race(TRACE, formation=False)
    a = Car(race, "A", "a", L - 15)
    t = _drive(race, [a], [50], 0.0, L / 50 * 2.5)      # free laps in the lobby
    assert _row(race, "A", t)["laps"] == 2
    a.along = -20.0                                     # the race grid: the counter drops to 0
    t = _drive(race, [a], [None], t, 1)
    assert _row(race, "A", t)["distance_m"] is None, _row(race, "A", t)
    t = _drive(race, [a], [50], t, 3)
    assert 0 < _row(race, "A", t)["distance_m"] < 200 and _row(race, "A", t)["laps"] == 0


def t_an_implausible_step_keeps_the_entry_live():
    race = rd.Race(TRACE, formation=False)
    a = Car(race, "A", "a", L - 15)
    t = _drive(race, [a], [50], 0.0, 3)
    d = _row(race, "A", t)["distance_m"]
    for _ in range(int(rd.NO_DATA_S * HZ) + 10):
        t += 1.0 / HZ
        x, z = _pos(a.along)
        race.update("A", "a", "Driver", {"t": t, "lap": 40, "x": x, "z": z, "speed_kmh": 180.0})
    row = _row(race, "A", t)
    assert row["distance_m"] == d and row["status"] == "racing", row


def t_the_final_lobby_count_waits_for_the_final_lobbys_car():
    race = rd.Race(TRACE, formation=False, multi_lobby=True, finish_mode="laps")
    old, rival = Car(race, "A", "a1", L - 15, "Max"), Car(race, "B", "b1", L - 60, "Ann")
    t = _drive(race, [old, rival], [50, 50], 0.0, 3 * L / 50)       # lap counters near 4
    race.open_change_window(t)
    race.final_lobby(2, t)
    t = _drive(race, [old, rival], [50, 40], t, L / 50 + 2)          # the old cars' in-laps
    assert race.standings(t)["state"] == "racing", "the old lobby's counters are not the final lobby's"
    old.box = True
    t = _drive(race, [old, rival], [None, 40], t, 3)
    new = Car(race, "A", "a2", L - 15, "Lea")
    t = _drive(race, [new, rival], [40, 40], t, 2)                   # lap 1 of the final lobby
    new.box = True
    t = _drive(race, [new, rival], [None, 40], t, 3)
    new.box = False
    t = _drive(race, [new, rival], [50, 40], t, (2 * L) / 50 + 2)    # its lobby counter passes 2
    assert race.standings(t)["state"] in ("finishing", "finished") and _row(race, "A", t)["finished"]


def t_the_lap_display_follows_the_lobby_that_counts_and_stops_at_the_count():
    race = rd.Race(TRACE, formation=False, multi_lobby=True, finish_mode="laps")
    old, rival = Car(race, "A", "a1", L - 15, "Max"), Car(race, "B", "b1", L - 60, "Ann")
    t = _drive(race, [old, rival], [50, 50], 0.0, 3 * L / 50)
    race.open_change_window(t)
    race.final_lobby(2, t)
    t = _drive(race, [old, rival], [50, 40], t, L / 50 + 2)
    assert race.standings(t)["lap"] == {"leader": 5, "count": None}, \
        f"before the final lobby's car counts, race laps show without the count: {race.standings(t)['lap']}"
    old.box = True
    t = _drive(race, [old, rival], [None, 40], t, 3)
    new = Car(race, "A", "a2", L - 15, "Lea")
    t = _drive(race, [new, rival], [40, 40], t, 2)
    new.box = True
    t = _drive(race, [new, rival], [None, 40], t, 3)
    new.box = False
    t = _drive(race, [new, rival], [50, 40], t, 20)
    assert race.standings(t)["lap"] == {"leader": 1, "count": 2}, race.standings(t)["lap"]
    t = _drive(race, [new, rival], [50, 40], t, (2 * L) / 50 + 2)
    assert race.standings(t)["lap"] == {"leader": 2, "count": 2}, \
        f"the final lobby's lap, never past the count: {race.standings(t)['lap']}"
    single = rd.Race(TRACE, formation=True, finish_mode="laps", lap_count=3)
    a = Car(single, "A", "a", L - 15)
    t = _drive(single, [a], [50], 0.0, (3 * L + 15) / 50 + 1)
    assert _row(single, "A", t)["finished"] and single.standings(t)["lap"] == {"leader": 3, "count": 3}, \
        single.standings(t)["lap"]


def t_a_timed_endurance_keeps_race_laps_on_the_display_in_the_final_lobby():
    race = rd.Race(TRACE, formation=False, multi_lobby=True, finish_mode="time")
    old, rival = Car(race, "A", "a1", L - 15, "Max"), Car(race, "B", "b1", L - 60, "Ann")
    t = _drive(race, [old, rival], [50, 50], 0.0, 3 * L / 50)
    race.open_change_window(t)
    race.final_lobby(None, t)
    old.box = True
    t = _drive(race, [old, rival], [None, 5], t, 3)
    new = Car(race, "A", "a2", L - 15, "Lea")
    t = _drive(race, [new, rival], [40, 5], t, 2)                     # lap 1 of the final lobby
    new.box = True
    t = _drive(race, [new, rival], [None, 5], t, 3)
    new.box = False
    t = _drive(race, [new, rival], [50, 5], t, 10)
    assert _row(race, "A", t)["driver"] == "Lea" and race.standings(t)["entries"][0]["entry"] == "A"
    lap = race.standings(t)["lap"]
    assert lap["count"] is None and lap["leader"] >= 4, f"without a count the race laps stay: {lap}"


def t_one_sender_cannot_move_the_line_or_jump_the_field():
    race = rd.Race(TRACE, formation=False)
    x0, z0 = _pos(L / 2)
    for i, lap in enumerate((0, 1, 2)):                # a forged crossing in mid-lap
        race.update("X", "x", "Forger", {"t": 0.1 * (i + 1), "lap": lap, "x": x0, "z": z0,
                                          "speed_kmh": 0.0})
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 50)
    t = _drive(race, [a, b], [50, 50], 1.0, 20)
    assert abs(_row(race, "A", t)["distance_m"] - a.along) < 5, (_row(race, "A", t), a.along)
    late = Car(race, "C", "c", L - 15)
    late.along = L + 100                               # claims lap 2 while the field is on lap 1
    _drive(race, [late], [50], t, 2)
    assert _row(race, "C", t)["distance_m"] is None, "a start ahead of the leader's lap is refused"


def t_the_line_medoid_needs_a_quorum_of_counting_entries():
    race = rd.Race(TRACE, formation=False)
    cars = [Car(race, e, e.lower(), L - 15 - 10 * i) for i, e in enumerate("ABC")]
    _drive(race, cars, [50, 50, 50], 0.0, 3)
    loner = rd._Car("z", "Z")
    assert race._line_of(loner) is not None, "three counting entries that agree give a line"
    race._cars["a"].line_s, race._cars["b"].line_s = L / 2, L / 4
    assert race._line_of(loner) is None, "no majority within SNAP_M: no shared line"
    two = rd.Race(TRACE, formation=False)
    pair = [Car(two, e, e.lower(), L - 15 - 10 * i) for i, e in enumerate("AB")]
    _drive(two, pair, [50, 50], 0.0, 3)
    assert two._line_of(loner) is None, "two entries are no quorum"


def t_a_change_needs_a_fresh_lobby_counter():
    race = rd.Race(TRACE, formation=False, multi_lobby=True)
    old = Car(race, "A", "a1", L - 15, "Max")
    t = _drive(race, [old], [50], 0.0, 30)
    race.open_change_window(t)
    old.box = True
    t = _drive(race, [old], [None], t, 3)
    forged = Car(race, "A", "a2", L - 15, "Lea")
    forged.along, forged.box = 40 * L - 300, True       # leaves a box on "lap 40"
    t = _drive(race, [forged], [None], t, 3)
    forged.box = False
    t = _drive(race, [forged], [40], t, 3)
    assert _row(race, "A", t)["driver"] == "Max", "a car past lap 2 of its lobby takes over nobody"


def t_a_reassigned_sender_counts_for_its_new_entry():
    race = rd.Race(TRACE, formation=False)
    a = Car(race, "A", "a", L - 15)
    t = _drive(race, [a], [50], 0.0, 5)
    a.entry = "B"                                      # the producer corrected the registration
    t = _drive(race, [a], [50], t, 1)
    assert [r["entry"] for r in race.standings(t)["entries"]] == ["B"], race.standings(t)["entries"]


def t_moving_a_teams_only_car_away_and_back_keeps_the_team():
    for multi in (False, True):
        race = rd.Race(TRACE, formation=False, multi_lobby=multi)
        a = Car(race, "A", "a", L - 15)
        t = _drive(race, [a], [50], 0.0, 30)
        race.correct("A", 500.0, pit_stops=1)
        a.entry = "X"                                      # moved to the wrong entry by mistake
        t = _drive(race, [a], [50], t, 2)
        race.correct("A", 20.0)                            # Race Control corrects it meanwhile
        a.entry = "A"
        t = _drive(race, [a], [50], t, 2)
        row = _row(race, "A", t)
        assert row["correction_m"] == 520.0 and row["pit_stops"] == 1, (multi, row)
        assert abs(row["distance_m"] - (a.along + 520.0)) < 5, \
            f"the team counts on from its own laps: {multi} {row} {a.along}"


def t_no_line_means_no_distance():
    race = rd.Race(None, formation=False)
    a = Car(race, "A", "a", 0)
    t = _drive(race, [a], [30], 0.0, 2)
    snap = race.standings(t)
    assert snap["state"] == "no_line" and snap["entries"][0]["distance_m"] is None, snap


def _new_lobby(car, grid):
    """The car's console moves to the restart lobby: it stands in the box, counter 0."""
    car.along, car.box = grid - L, True


def t_red_flag_anchors_and_each_car_continues_from_its_anchor():
    race = rd.Race(TRACE, formation=False)
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 115)
    t = _drive(race, [a, b], [50, 50], 0.0, 40)
    da, db = _row(race, "A", t)["distance_m"], _row(race, "B", t)["distance_m"]
    race.red_flag(t, "keep")
    t = _drive(race, [a, b], [30, 30], t, 5)                         # still moving in the old lobby
    assert race.standings(t)["red_flag"]["mode"] == "keep"
    assert _row(race, "A", t)["distance_m"] == da, "a red flag freezes the distance"
    for car in (a, b):
        _new_lobby(car, L - 15)
    t = _drive(race, [a, b], [None, None], t, 3)
    a.box = False
    t = _drive(race, [a, b], [40, None], t, 5)                        # A leaves the box first
    assert abs(_row(race, "A", t)["distance_m"] - (da + 200)) < 25, _row(race, "A", t)
    assert _row(race, "B", t)["distance_m"] == db and _row(race, "B", t)["status"] == "box"
    b.box = False
    t = _drive(race, [a, b], [40, 40], t, 5)
    assert abs(_row(race, "B", t)["distance_m"] - (db + 200)) < 25
    assert _row(race, "A", t)["pit_stops"] == 0 and _row(race, "B", t)["pit_stops"] == 0, \
        "a red flag restart is no pit stop"
    race.release(t)
    assert race.standings(t)["red_flag"] is None


def t_a_box_stop_in_the_old_lobby_is_no_restart():
    race = rd.Race(TRACE, formation=False)
    a = Car(race, "A", "a", L - 15)
    t = _drive(race, [a], [50], 0.0, 20)
    da = _row(race, "A", t)["distance_m"]
    race.red_flag(t, "keep")
    a.box = True
    t = _drive(race, [a], [None], t, 3)
    a.box = False
    t = _drive(race, [a], [40], t, 5)
    row = _row(race, "A", t)
    assert row["distance_m"] == da and row["status"] == "red_flag", \
        f"only the restart lobby's box exit resumes: {row}"


def t_a_neutralised_restart_levels_cars_on_the_same_lap():
    race = rd.Race(TRACE, formation=False)
    a, b, c = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 115), Car(race, "C", "c", L - 25)
    t = _drive(race, [a, b, c], [50, 50, 25], 0.0, 160)               # C is a lap down
    da, dc = _row(race, "A", t)["distance_m"], _row(race, "C", t)["distance_m"]
    assert _row(race, "C", t)["lapped"] == 1
    race.red_flag(t, "keep")
    race.set_restart_mode("neutralise")
    for car in (a, b, c):
        _new_lobby(car, L - 15)
    t = _drive(race, [a, b, c], [None, None, None], t, 3)
    t = _drive(race, [a, b, c], [None, None, None], t, 0.1)
    for car in (a, b, c):
        car.box = False
    t = _drive(race, [a, b, c], [40, 40, 40], t, 1)
    ra, rb, rc = (_row(race, e, t) for e in "ABC")
    assert abs(ra["distance_m"] - rb["distance_m"]) < 1, "cars on the same lap start level"
    assert abs(ra["distance_m"] - (da + 40)) < 10 and abs(rc["distance_m"] - (dc + 40)) < 10, \
        "a lapped car keeps its own lap"
    assert [r["entry"] for r in race.standings(t)["entries"]][-1] == "C"


def t_a_neutralised_restart_levels_the_laps_the_tower_shows():
    race = rd.Race(TRACE, formation=False)
    a, c = Car(race, "A", "a", L - 15), Car(race, "C", "c", L - 25)
    t = _drive(race, [a, c], [50, 25], 0.0, 160)                      # C is a lap down on the road
    race.correct("C", L)                                              # Race Control gives the lap back
    da = _row(race, "A", t)["distance_m"]
    race.red_flag(t, "keep")
    race.set_restart_mode("neutralise")
    for car in (a, c):
        _new_lobby(car, L - 15)
    t = _drive(race, [a, c], [None, None], t, 3)
    t = _drive(race, [a, c], [None, None], t, 0.1)
    for car in (a, c):
        car.box = False
    t = _drive(race, [a, c], [40, 40], t, 1)
    ra, rc = _row(race, "A", t), _row(race, "C", t)
    assert abs(ra["distance_m"] - rc["distance_m"]) < 1 and abs(ra["distance_m"] - (da + 40)) < 10, \
        f"cars the tower shows on the same lap start level: {ra['distance_m']} {rc['distance_m']}"


def t_corrections_move_order_gaps_and_pit_stops():
    race = rd.Race(TRACE, formation=False)
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 115)
    t = _drive(race, [a, b], [50, 50], 0.0, 30)
    race.correct("B", 150.0, pit_stops=2)
    rows = race.standings(t)["entries"]
    assert [r["entry"] for r in rows] == ["B", "A"], rows
    assert rows[1]["gap_s"] is not None and abs(rows[1]["gap_s"] - 1.0) < 0.15, \
        f"A is 50 m behind the corrected B: {rows[1]}"
    assert rows[0]["pit_stops"] == 2 and rows[0]["correction_m"] == 150.0
    race.correct("B", -150.0, pit_stops=-2)
    assert [r["entry"] for r in race.standings(t)["entries"]] == ["A", "B"]


def t_a_rejoin_starts_the_team_at_the_leaders_distance():
    race = rd.Race(TRACE, formation=False, multi_lobby=True)
    lead, gone = Car(race, "A", "a1", L - 15, "Max"), Car(race, "B", "b1", L - 115, "Ann")
    t = _drive(race, [lead, gone], [50, 50], 0.0, 30)
    t = _drive(race, [lead], [50], t, 30)                               # B disconnected
    race.rejoin("B", True)
    race.open_change_window(t)
    lead.box = True
    t = _drive(race, [lead], [None], t, 3)
    start = _row(race, "A", t)["distance_m"]
    nxt, back = Car(race, "A", "a2", L - 15, "Lea"), Car(race, "B", "b2", L - 15, "Ben")
    t = _drive(race, [nxt, back], [40, 40], t, 2)                       # lap 1 of the next lobby
    nxt.box = back.box = True
    t = _drive(race, [nxt, back], [None, None], t, 3)
    nxt.box = False
    t = _drive(race, [nxt, back], [40, None], t, 10)                   # B departs 10 s later
    back.box = False
    t = _drive(race, [nxt, back], [40, 40], t, 5)
    rb = _row(race, "B", t)
    assert rb["driver"] == "Ben" and abs(rb["distance_m"] - (start + 200)) < 25, (rb, start)
    assert abs(rb["gap_s"] - 10.0) < 0.5, f"the later departure is the deficit: {rb}"
    assert race.take_used_rejoins() == ["B"] and race.take_used_rejoins() == [], \
        "a consumed rejoin is reported once, so the service can drop the saved mark"


def t_a_change_override_switches_the_team_now():
    race = rd.Race(TRACE, formation=False, multi_lobby=True)
    old = Car(race, "A", "a1", L - 15, "Max")
    t = _drive(race, [old], [50], 0.0, 20)
    new = Car(race, "A", "a2", L - 15, "Lea")
    t = _drive(race, [old, new], [50, 40], t, 2)
    before = _row(race, "A", t)["distance_m"]
    assert race.override_change("A", "a2", t)
    t = _drive(race, [old, new], [50, 40], t, 5)
    row = _row(race, "A", t)
    assert row["driver"] == "Lea" and abs(row["distance_m"] - (before + 200)) < 25, (row, before)
    assert not race.override_change("A", "nobody", t), "only a connection of the team"


def t_a_change_override_without_a_known_line_changes_nothing():
    race = rd.Race(TRACE, formation=False, multi_lobby=True)
    old = Car(race, "A", "a1", L - 15, "Max")
    t = _drive(race, [old], [50], 0.0, 20)
    new = Car(race, "A", "a2", L - 15, "Lea")
    new.along = -L / 2                                    # mid-lap, no crossing of its own yet
    t = _drive(race, [old, new], [50, 40], t, 2)
    before = _row(race, "A", t)["distance_m"]
    assert not race.override_change("A", "a2", t), "no line station for the new car: refused"
    t = _drive(race, [old, new], [50, 40], t, 2)
    row = _row(race, "A", t)
    assert row["driver"] == "Max" and abs(row["distance_m"] - (before + 100)) < 10, \
        f"the old car keeps counting after a refused override: {row} {before}"


def t_a_release_in_the_same_lobby_drives_on_from_where_the_cars_are():
    race = rd.Race(TRACE, formation=False)
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 115)
    t = _drive(race, [a, b], [50, 50], 0.0, 20)
    race.red_flag(t, "keep")                                   # pressed by mistake
    t = _drive(race, [a, b], [50, 50], t, 4)
    race.release(t)
    t = _drive(race, [a, b], [50, 50], t, 4)
    for car, entry in ((a, "A"), (b, "B")):
        row = _row(race, entry, t)
        assert row["status"] == "racing" and abs(row["distance_m"] - (car.along + 15 - 15)) < 5, (row, car.along)


def t_a_lap_count_finish_after_a_red_flag_restart_counts_the_whole_race():
    race = rd.Race(TRACE, formation=False, finish_mode="laps", lap_count=3)
    a = Car(race, "A", "a", L - 15)
    t = _drive(race, [a], [50], 0.0, (L + 30) / 50)            # 1 lap and a bit
    frozen = _row(race, "A", t)["distance_m"]
    race.red_flag(t, "keep")
    _new_lobby(a, L / 2)                                        # the restart box sits mid-lap
    t = _drive(race, [a], [None], t, 3)
    a.box = False
    t = _drive(race, [a], [50], t, (3 * L - frozen) / 50 - 3)   # just short of 3 race laps
    assert race.standings(t)["state"] == "racing", race.standings(t)["entries"][0]
    t = _drive(race, [a], [50], t, 0.2 * L / 50)
    assert _row(race, "A", t)["finished"], "3 laps of race distance finish it, whatever the restart lobby counts"


def t_lap_times_fuel_and_the_fastest_race_lap():
    race = rd.Race(TRACE, formation=True)
    a, b = Car(race, "A", "a", L - 15, "Lena"), Car(race, "B", "b", L - 60, "Marco")
    t = _drive(race, [a, b], [50, 50], 0.0, 1.5 * L / 50)          # formation lap, half of race lap 1
    assert _row(race, "A", t)["last_lap_ms"] is None, "the formation lap is no race lap"
    t = _drive(race, [a, b], [50, 50], t, 0.5 * L / 50 + 2)
    t = _drive(race, [a, b], [50, 60], t, L / 50 + 1)                # B laps faster
    ra, rb = _row(race, "A", t), _row(race, "B", t)
    assert abs(ra["last_lap_ms"] - L / 50 * 1000) < 150, ra
    assert abs(rb["last_lap_ms"] - L / 60 * 1000) < 150 or rb["last_lap_ms"] is None, rb
    t = _drive(race, [a, b], [50, 60], t, L / 50 + 1)
    fl = race.standings(t)["fastest"]
    assert fl["entry"] == "B" and fl["driver"] == "Marco" and abs(fl["ms"] - L / 60 * 1000) < 150, fl
    ra = _row(race, "A", t)
    assert ra["fuel_l"] == 50.0 and ra["stint_laps"] == ra["laps"] == 3, ra


def t_a_lap_time_that_disagrees_with_the_relays_clock_is_ignored():
    race = rd.Race(TRACE, formation=False)
    a = Car(race, "A", "a", L - 15)
    t = _drive(race, [a], [50], 0.0, L / 50 - 2)            # just before the end of race lap 1
    a.forge = 1000                                            # GT7 reports that lap as 1 s
    t = _drive(race, [a], [50], t, 5)
    assert a.last_ms == 1000, "the forged time reached the race"
    assert race.standings(t)["fastest"] is None, race.standings(t)["fastest"]


def t_a_lap_faster_than_the_line_allows_never_counts():
    race = rd.Race(TRACE, formation=False)
    a = Car(race, "A", "a", L - 15, "Mallory")
    t = _drive(race, [a], [50], 0.0, 1.0)
    for _ in range(int(L / 30) + 2):            # a whole lap at 300 m/s, inside the step check
        a.along += 30
        t = round(t + 0.1, 3)
        a.send(t)
    t = _drive(race, [a], [50], t, 2)
    assert race.standings(t)["fastest"] is None, race.standings(t)["fastest"]
    before = _row(race, "A", t)["distance_m"]
    a.send(t)                                   # the same timestamp again
    a.along += 10
    a.send(t)
    assert _row(race, "A", t)["distance_m"] == before, "a sample without new time never moves a car"


def t_a_lap_with_a_red_flag_or_a_restart_never_sets_the_fastest_lap():
    race = rd.Race(TRACE, formation=False)
    a = Car(race, "A", "a", L - 15)
    t = _drive(race, [a], [50], 0.0, 10)
    race.red_flag(t, "keep")
    t = _drive(race, [a], [50], t, 5)
    race.release(t)
    t = _drive(race, [a], [50], t, L / 50)                  # completes the interrupted lap
    assert race.standings(t)["fastest"] is None, "the lap with the red flag in it waits"


def t_a_race_built_mid_race_counts_the_cars_from_their_own_laps():
    race = rd.Race(TRACE, formation=False)                  # a relay restart late in lap 5
    a, b = Car(race, "A", "a", 5 * L - 60), Car(race, "B", "b", 5 * L - 120)
    t = _drive(race, [a, b], [50, 50], 0.0, 4)              # both cross into lap 6
    ra, rb = _row(race, "A", t), _row(race, "B", t)
    assert race.standings(t)["state"] == "racing", race.standings(t)
    assert ra["laps"] == 4 and abs(ra["distance_m"] - a.along) < 10, "the same distance as a race counted from the start"
    assert rb["laps"] == 4 and ra["distance_m"] > rb["distance_m"], (ra, rb)
    c = Car(race, "C", "c", 9 * L + 10)                       # a later sender still meets the bound
    t = _drive(race, [a, b, c], [50, 50, 50], t, 2)
    assert _row(race, "C", t)["distance_m"] is None, "a late sender cannot claim more laps than the field"


def t_one_sender_cannot_lift_the_start_bound_of_a_fresh_race():
    race = rd.Race(TRACE, formation=False)
    liar = Car(race, "A", "a", 40 * L - 60)                   # alone, claiming lap 40
    t = _drive(race, [liar], [50], 0.0, 4)
    assert _row(race, "A", t)["distance_m"] is None, "a lone sender past the start lap waits for a second car"
    twin = Car(race, "A", "a2", 40 * L - 30)                  # a second connection of the same entry
    t = _drive(race, [liar, twin], [50, 50], t, 2)
    assert _row(race, "A", t)["distance_m"] is None, "an entry's own connections never vouch for it"
    b, c = Car(race, "B", "b", L - 15), Car(race, "C", "c", L - 40)
    t = _drive(race, [liar, twin, b, c], [50, 50, 50, 50], t, 4)
    assert _row(race, "A", t)["distance_m"] is None, "the field's lap bounds the claim"
    assert _row(race, "B", t)["distance_m"] is not None, "the honest cars start"


def t_one_other_entry_cannot_vouch_alone_when_more_are_known():
    race = rd.Race(TRACE, formation=False)
    cars = [Car(race, "B", "b", 100), Car(race, "C", "c", 60),       # still on the grid
            Car(race, "A", "a", 40 * L - 60), Car(race, "D", "d", 40 * L - 30)]
    t = _drive(race, cars, [50] * 4, 0.0, 4)
    assert _row(race, "A", t)["distance_m"] is None and _row(race, "D", t)["distance_m"] is None, \
        "two entries out of four do not set the field's lap"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
