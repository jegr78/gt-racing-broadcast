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
        self.race.update(self.entry, self.conn, self.name, {
            "t": t, "lap": self.lap(), "x": x, "z": z, "speed_kmh": self.speed * 3.6,
            "race_position": position, "on_track": True, "paused": False, "loading": False})


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


def t_no_line_means_no_distance():
    race = rd.Race(None, formation=False)
    a = Car(race, "A", "a", 0)
    t = _drive(race, [a], [30], 0.0, 2)
    snap = race.standings(t)
    assert snap["state"] == "no_line" and snap["entries"][0]["distance_m"] is None, snap


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
