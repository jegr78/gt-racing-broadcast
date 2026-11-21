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


def t_a_finished_car_keeps_its_final_gap_after_its_data_stops():
    race = rd.Race(TRACE, formation=False, finish_mode="laps", lap_count=2)
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 400)
    t = _drive(race, [a, b], [50, 50], 0.0, (2 * L + 5) / 50 + 12)     # both take the flag
    final = _row(race, "B", t)["gap_s"]
    assert _row(race, "B", t)["finished"] and final is not None, race.standings(t)
    t += rd.NO_DATA_S + 5                                                # both senders leave
    row = _row(race, "B", t)
    assert row["status"] == "no_data", row
    assert row["gap_s"] == final, f"the final gap stays after the data stops: {row}"


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


def t_no_gap_turns_negative_when_a_car_ranked_ahead_takes_the_flag_later():
    race = rd.Race(TRACE, formation=False, finish_mode="laps", lap_count=2)
    a, b, c = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 6), Car(race, "C", "c", L - 100)
    t = _drive(race, [a, b, c], [60, 29.5, 50], 0.0, 110)    # B takes the flag before C
    t = _drive(race, [a, b, c], [60, 29.5, 50], t, 30)       # then C, ranked ahead of B
    snap = race.standings(t)
    assert snap["state"] == "finished" and [r["entry"] for r in snap["entries"]] == ["A", "C", "B"], snap
    for r in snap["entries"][1:]:
        for key in ("gap_s", "interval_s", "gap_time_s", "interval_time_s"):
            assert r[key] is None or r[key] >= 0, f"{r['entry']} {key}={r[key]} after the finish"
    assert _row(race, "B", t)["interval_time_s"] > 0, "B reached that lap after C, so it is behind"


def t_after_the_flag_running_cars_on_one_lap_follow_the_road():
    race = rd.Race(TRACE, formation=False, finish_mode="laps", lap_count=2)
    a, b, c = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 100), Car(race, "C", "c", L - 110)
    t = _drive(race, [a, b, c], [80, 50, 50], 0.0, (2 * L + 15) / 80 + 0.5)   # A wins, B led C over the line
    assert race.standings(t)["state"] == "finishing" and _row(race, "A", t)["finished"], race.standings(t)["state"]
    t = _drive(race, [a, b, c], [80, 50, 60], t, 5)                          # C passes B on the last lap
    rb, rc = _row(race, "B", t), _row(race, "C", t)
    assert not rb["finished"] and not rc["finished"] and rc["distance_m"] > rb["distance_m"] + 30, (rb, rc)
    assert _order(race, t) == ["A", "C", "B"], f"C passed B on the road: {_order(race, t)}"
    assert rb["interval_time_s"] > 0.3, f"B trails C: {rb['interval_time_s']}"


def t_after_the_flag_a_lapped_finisher_stays_behind_the_running_cars_of_its_lap():
    race = rd.Race(TRACE, formation=False, finish_mode="laps", lap_count=2)
    a, b, c, f = (Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 100),
                  Car(race, "C", "c", L - 110), Car(race, "F", "f", L - 6))
    t = _drive(race, [a, b, c, f], [80, 50, 50, 28], 0.0, 113)         # A wins, then lapped F finishes
    assert _row(race, "F", t)["finished"] and _row(race, "F", t)["laps"] == _row(race, "B", t)["laps"], \
        race.standings(t)
    t = _drive(race, [a, b, c, f], [80, 50, 60, 28], t, 4)             # C passes B
    assert not _row(race, "B", t)["finished"] and not _row(race, "C", t)["finished"]
    assert _order(race, t) == ["A", "C", "B", "F"], f"B and C reached that lap before F: {_order(race, t)}"


def t_after_the_flag_a_finished_car_does_not_hold_the_field_back_in_time():
    race = rd.Race(TRACE, formation=False, finish_mode="laps", lap_count=2)
    a, b, c = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 100), Car(race, "C", "c", L - 105)
    t = _drive(race, [a, b, c], [80, 50, 50], 0.0, (2 * L + 15) / 80 + 0.5)
    assert _row(race, "A", t)["finished"] and not _row(race, "A", t)["status"] == "no_data"
    while _row(race, "C", t)["distance_m"] < _row(race, "B", t)["distance_m"] + 3:
        t = _drive(race, [a, b, c], [80, 50, 60], t, 0.1)              # C passes B, A still sends
    assert _order(race, t) == ["A", "C", "B"], \
        f"C passed B a moment ago; the winner's frozen history does not count: {_order(race, t)}"


def t_after_the_flag_two_running_cars_compare_at_the_same_moment():
    race = rd.Race(TRACE, formation=False, finish_mode="laps", lap_count=2)
    a, b, c = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 100), Car(race, "C", "c", L - 103)
    t = _drive(race, [a, b, c], [80, 50, 50], 0.0, (2 * L + 15) / 80 + 0.5)
    for _ in range(5):                       # B runs 3 m ahead of C; C's sample is 0.2 s fresher
        t += 0.2
        for car in (a, b, c):
            car.along += 10.0
        a.send(t)
        b.send(t)
        c.along += 10.0
        c.send(t + 0.2)
        c.along -= 10.0
    assert _order(race, t + 0.2) == ["A", "B", "C"], f"B leads C on the road: {_order(race, t + 0.2)}"


def t_after_the_flag_a_runner_passing_an_estimates_reference_keeps_the_gaps_in_order():
    race = rd.Race(TRACE, formation=False, finish_mode="laps", lap_count=2)
    a, b, r, x = (Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 100),
                  Car(race, "R", "r", L - 95), Car(race, "X", "x", L - 130))
    t = _drive(race, [a, b, r, x], [80, 50, 50, 50], 0.0, 60)
    t = _drive(race, [a, b, r], [80, 50, 50], t, rd.NO_DATA_S + 1)     # X goes quiet
    race.estimate("X", "R", 0.5, t)
    t = _drive(race, [a, b, r], [80, 50, 50], t, (2 * L + 15) / 80 + 0.5 - t)
    assert race.standings(t)["state"] == "finishing", race.standings(t)["state"]
    t = _drive(race, [a, b, r], [80, 60, 50], t, 4)                    # B passes R
    rows = race.standings(t)["entries"]
    assert [r_["entry"] for r_ in rows] == ["A", "B", "R", "X"], [r_["entry"] for r_ in rows]
    gaps = [r_["gap_time_s"] for r_ in rows]
    assert all(g is not None for g in gaps) and gaps == sorted(gaps), f"gaps grow down the order: {gaps}"


def t_after_the_flag_running_cars_past_the_count_keep_the_order_they_reached_it():
    race = rd.Race(TRACE, formation=False, multi_lobby=True, finish_mode="laps")
    a, b, c = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 60), Car(race, "C", "c", L - 80)
    t = _drive(race, [a, b, c], [50, 50, 50], 0.0, 20)
    ea, eb, ec = race._entries["A"], race._entries["B"], race._entries["C"]
    ea.distance = L + 900.0                                  # the flag falls off the distance leaders
    race._flag(ea, t)
    eb.distance, ec.distance = 2 * L + 50.0, 2 * L + 60.0
    eb.ts, eb.ds = [t - 30, t - 10, t - 1], [L - 100.0, L + 10.0, eb.distance]
    ec.ts, ec.ds = [t - 30, t - 8, t - 1], [L - 100.0, L + 10.0, ec.distance]
    assert eb.finished_at is None and ec.finished_at is None
    assert _order(race, t) == ["A", "B", "C"], f"B reached the winner's lap count before C: {_order(race, t)}"


def t_after_the_flag_a_running_car_stays_behind_a_car_that_finished_ahead_on_the_road():
    race = rd.Race(TRACE, formation=False, multi_lobby=True, finish_mode="laps")
    a, f, r, s = (Car(race, "A", "a", L - 15), Car(race, "F", "f", L - 60),
                  Car(race, "R", "r", L - 80), Car(race, "S", "s", L - 90))
    t = _drive(race, [a, f, r, s], [50, 50, 50, 50], 0.0, 40)
    ea, ef, er, es = (race._entries[k] for k in "AFRS")
    ea.distance = 2 * L + 900.0                              # the final lobby's line sits 200 m past a race lap
    race._flag(ea, t - 20)
    ef.distance, ef.finished_at = L + 200.0, t - 2           # F crossed the race lap after S, passed both, finished
    ef.ts, ef.ds = [t - 30, t - 10, t - 2], [L - 300.0, L, ef.distance]
    er.distance, es.distance = L + 150.0, L + 140.0
    er.ts, er.ds = [t - 30, t - 5, t - 1], [L - 300.0, L, er.distance]
    es.ts, es.ds = [t - 30, t - 15, t - 1], [L - 300.0, L, es.distance]
    assert _order(race, t) == ["A", "F", "R", "S"], f"F took its flag ahead of R and S on the road: {_order(race, t)}"


def t_after_the_flag_finished_cars_of_one_lap_keep_their_flag_order():
    race = rd.Race(TRACE, formation=False, multi_lobby=True, finish_mode="laps")
    cars = [Car(race, e, e.lower(), L - 15 - 10 * i) for i, e in enumerate(["A", "F1", "F2", "S"])]
    t = _drive(race, cars, [50] * 4, 0.0, 40)
    ea, f1, f2, es = (race._entries[k] for k in ["A", "F1", "F2", "S"])
    ea.distance = 2 * L + 900.0
    race._flag(ea, t - 20)
    f1.distance, f1.finished_at = L + 180.0, t - 4          # the team anchors put F1's line short of F2's
    f1.ts, f1.ds = [t - 30, t - 9, t - 4], [L - 300.0, L, f1.distance]
    f2.distance, f2.finished_at = L + 200.0, t - 2
    f2.ts, f2.ds = [t - 30, t - 8, t - 2], [L - 300.0, L, f2.distance]
    es.distance = L + 120.0
    es.ts, es.ds = [t - 30, t - 15, t - 1], [L - 300.0, L, es.distance]
    assert _order(race, t) == ["A", "F1", "F2", "S"], f"F1 took the flag before F2: {_order(race, t)}"


def t_the_winner_stays_first_when_its_flag_is_off_a_whole_race_lap():
    race = rd.Race(TRACE, formation=False, multi_lobby=True, finish_mode="laps")
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 60)
    t = _drive(race, [a, b], [50, 50], 0.0, 20)
    ea, eb = race._entries["A"], race._entries["B"]
    ea.distance, eb.distance = L + 900.0, L + 850.0          # the final lobby's line sits mid-lap
    race._flag(ea, t)
    eb.ts, eb.ds = [t - 30, t - 10, t - 1], [L - 100.0, L + 10.0, eb.distance]
    order = [r["entry"] for r in race.standings(t)["entries"]]
    assert order == ["A", "B"], f"the car that took the flag first is the winner: {order}"


def t_a_final_lobby_finish_a_few_metres_short_of_its_line_counts_the_lap():
    # These grids put B's (then also A's) finishing sample a few metres short of its own line.
    for grid_a in (L - 15, L - 15.2):
        race = rd.Race(TRACE, formation=False, multi_lobby=True, finish_mode="laps")
        race.final_lobby(3, 0.0)
        a, b = Car(race, "A", "a", grid_a), Car(race, "B", "b", L - 300)
        t = _drive(race, [a, b], [50, 49], 0.0, (3 * L + 400) / 49 + 5)
        assert race.standings(t)["state"] == "finished", race.standings(t)["state"]
        ra, rb = _row(race, "A", t), _row(race, "B", t)
        assert (ra["laps"], rb["laps"]) == (3, 3), \
            f"both crossed the line of GT7's lap 3: {ra['laps']} {rb['laps']} ({ra['distance_m']}, {rb['distance_m']})"
        assert rb["lapped"] == 0 and rb["gap_time_s"] > 5, f"B finished on the lead lap behind A: {rb}"


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


def _batched_finish(multi_lobby, b_first=True):
    """A leads B by 10 m on the road and by 210 m in race distance (as after a keep
    restart); one 0.4 s batch carries both crossings. With `b_first` B's sender posts
    first, so B's crossing reaches the relay before A's; else A's batch arrives first
    and B's sample is stamped earlier than A's."""
    race = rd.Race(TRACE, formation=False, multi_lobby=multi_lobby, finish_mode="time")
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 25)
    t = _drive(race, [a, b], [50, 50], 0.0, 30)
    race.correct("B", -200.0)
    race.final_lap(t)
    while a.along < L - 5:
        step = min(20.0, L - 5 - a.along)
        t += step / 50
        for car in (b, a):
            car.along += step
            car.send(t if car is b else t + 0.01)
    t += 0.4                                                  # A 15 m, B 5 m past the line
    for car in (b, a) if b_first else (a, b):
        car.along += 20.0
        car.send(t if car is b else t + 0.01)
    t += 0.01
    return race, (a, b), t


def t_a_car_crossing_right_after_the_flag_finishes_there_whatever_order_its_data_arrives():
    for multi_lobby in (False, True):
        for b_first in (True, False):
            race, (a, b), t = _batched_finish(multi_lobby, b_first)
            assert b.lap() == 2, "the batch carries both crossings"
            assert _row(race, "A", t)["finished"] and _order(race, t) == ["A", "B"], race.standings(t)
            assert _row(race, "B", t)["finished"] and race.standings(t)["state"] == "finished", \
                f"B crossed the line after A took the flag, so it finishes there " \
                f"(multi_lobby={multi_lobby}, b_first={b_first})"


def t_a_car_crossing_just_before_the_flag_does_not_finish_there():
    race = rd.Race(TRACE, formation=False, multi_lobby=False, finish_mode="time")
    a, c = Car(race, "A", "a", L - 15), Car(race, "C", "c", L - 25)
    t = _drive(race, [a, c], [50, 50], 0.0, 30)
    race.correct("C", -L)
    race.final_lap(t)
    c.along = a.along + 3.0                                  # C a lap down, 3 m ahead on the road
    while a.along < L - 5:
        step = min(20.0, L - 5 - a.along)
        t += step / 50
        for car in (a, c):
            car.along += step
            car.send(t if car is a else t + 0.01)
    t += 0.4
    for car in (a, c):                                       # A's batch first, C's sample stamped later
        car.along += 20.0
        car.send(t if car is a else t + 0.01)
    t += 0.01
    ea, ec = race._entries["A"], race._entries["C"]
    assert _row(race, "A", t)["finished"] and ec.crossed[0] < ea.crossed[0], (ea.crossed, ec.crossed)
    assert not _row(race, "C", t)["finished"], "C crossed the line before A took the flag, so it runs one more lap"
    while not _row(race, "C", t)["finished"]:
        t = _drive(race, [a, c], [50, 50], t, 0.5)
    assert race.standings(t)["state"] == "finished", race.standings(t)


def t_a_late_crossing_short_of_the_cars_line_keeps_its_lap():
    race = rd.Race(TRACE, formation=False, multi_lobby=True, finish_mode="time")
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 25)
    t = _drive(race, [a, b], [50, 50], 0.0, 30)
    race.final_lap(t)
    while a.along < L - 5:
        step = min(20.0, L - 5 - a.along)
        t += step / 50
        for car in (b, a):
            car.along += step
            car.send(t if car is b else t + 0.01)
    car_b = race._cars["b"]
    car_b.line = (car_b.line + 12.0) % race.length      # B's line station 12 m past the real line
    t += 0.4
    for car in (b, a):                                 # B's crossing reaches the relay first
        car.along += 20.0
        car.send(t if car is b else t + 0.01)
    t += 0.01
    ra, rb = _row(race, "A", t), _row(race, "B", t)
    assert rb["finished"] and race._entries["B"].crossed[0] > race._entries["A"].crossed[0], \
        "B finishes as the late car of A's flag"
    assert (rb["laps"], rb["lapped"]) == (ra["laps"], 0), \
        f"B crossed the same line as A: {ra['laps']} {rb['laps']} ({rb['distance_m']})"


def t_a_flag_outside_the_final_lobby_short_of_the_cars_line_keeps_its_lap():
    race = rd.Race(TRACE, formation=False, multi_lobby=True, finish_mode="time")
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 300)
    t = _drive(race, [a, b], [50, 50], 0.0, 30)
    race.final_lap(t)
    car_a = race._cars["a"]
    car_a.line = (car_a.line + 12.0) % race.length       # A's line station 12 m past the real line
    while not _row(race, "A", t)["finished"]:
        t = _drive(race, [a, b], [50, 50], t, 0.1)
    ra = _row(race, "A", t)
    assert ra["laps"] == 1 and race.standings(t)["state"] in ("finishing", "finished"), \
        f"A took the flag at the end of its first lap: {ra['laps']} ({ra['distance_m']})"


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
    assert row["pit_stops"] == 1, f"the driver change at a stint switch is a pit stop: {row}"


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


def _switch_and_box(race, cars, t):
    """A stint switch, then every car in `cars` stands in its box of the old lobby."""
    race.open_change_window(t)
    for car in cars:
        car.box = True
    return _drive(race, cars, [None] * len(cars), t, 3)


def _take_over(race, car, others, speeds, t):
    """`car` starts lap 1 of the next lobby, stops in its box and leaves it."""
    car.box = True
    t = _drive(race, [car] + others, [None] + speeds, t, 3)
    car.box = False
    return _drive(race, [car] + others, [40] + speeds, t, 5)


def t_a_driver_staying_on_counts_one_pit_stop():
    race = rd.Race(TRACE, formation=False, multi_lobby=True)
    car, rival = Car(race, "A", "a1", L - 15, "Max"), Car(race, "B", "b1", L - 10, "Ann")
    t = _drive(race, [car, rival], [50, 50], 0.0, 60)
    t = _switch_and_box(race, [car], t)
    car.along = -5.0                                  # the next lobby: same console, counter 0
    t = _take_over(race, car, [rival], [50], t)
    row = _row(race, "A", t)
    assert row["driver"] == "Max" and row["pit_stops"] == 1, \
        f"staying on through a stint switch is one pit stop: {row}"


def t_a_rejoined_team_counts_one_pit_stop():
    race = rd.Race(TRACE, formation=False, multi_lobby=True)
    lead, gone = Car(race, "A", "a1", L - 15, "Max"), Car(race, "B", "b1", L - 115, "Ann")
    t = _drive(race, [lead, gone], [50, 50], 0.0, 30)
    t = _drive(race, [lead], [50], t, 30)                               # B disconnected
    race.rejoin("B", True)
    t = _switch_and_box(race, [lead], t)
    nxt, back = Car(race, "A", "a2", L - 15, "Lea"), Car(race, "B", "b2", L - 15, "Ben")
    t = _drive(race, [nxt, back], [40, 40], t, 2)
    nxt.box = back.box = True
    t = _drive(race, [nxt, back], [None, None], t, 3)
    nxt.box = back.box = False
    t = _drive(race, [nxt, back], [40, 40], t, 5)
    ra, rb = _row(race, "A", t), _row(race, "B", t)
    assert rb["driver"] == "Ben" and rb["pit_stops"] == 1, f"a rejoin is one pit stop: {rb}"
    assert ra["driver"] == "Lea" and ra["pit_stops"] == 1, f"a takeover is one pit stop: {ra}"


def t_two_stint_switches_count_two_pit_stops():
    race = rd.Race(TRACE, formation=False, multi_lobby=True)
    car, rival = Car(race, "A", "a1", L - 15, "Max"), Car(race, "B", "b1", L - 10, "Ann")
    t = _drive(race, [car, rival], [50, 50], 0.0, 60)
    for n, conn in enumerate(("a2", "a3"), start=1):
        t = _switch_and_box(race, [car], t)
        car = Car(race, "A", conn, L - 15, "Driver " + conn)
        t = _drive(race, [car, rival], [40, 50], t, 2)
        t = _take_over(race, car, [rival], [50], t)
        t = _drive(race, [car, rival], [50, 50], t, 60)
        row = _row(race, "A", t)
        assert row["driver"] == "Driver " + conn and row["pit_stops"] == n, (n, row)


def t_a_restart_lobby_after_a_stint_switch_adds_no_pit_stop():
    race = rd.Race(TRACE, formation=False, multi_lobby=True)
    old = Car(race, "A", "a1", L - 15, "Max")
    rivals = [Car(race, e, e.lower() + "1", L - 10 - 20 * i, "Old" + e) for i, e in enumerate("BC")]
    t = _drive(race, [old] + rivals, [50] * 3, 0.0, 60)
    t = _switch_and_box(race, [old], t)
    new = Car(race, "A", "a2", L - 15, "Lea")
    t = _drive(race, [new] + rivals, [40, 50, 50], t, 2)
    t = _take_over(race, new, rivals, [50, 50], t)
    race.red_flag(t, "keep")
    cars = []
    for i, e in enumerate("ABC"):
        car = Car(race, e, e.lower() + "3", 0.0, "Restart " + e)
        _new_lobby(car, L - 300 - 20 * i)
        cars.append(car)
    t = _drive(race, cars, [None] * 3, t, 3)
    for car in cars:
        car.box = False
    t = _drive(race, cars, [40] * 3, t, 5)
    rows = {e: _row(race, e, t) for e in "ABC"}
    assert all(r["driver"] == "Restart " + e for e, r in rows.items()), rows
    assert [rows[e]["pit_stops"] for e in "ABC"] == [1, 0, 0], \
        f"only the driver change counts, not the restart lobby's box exit: {rows}"

def t_a_single_lobby_box_exit_counts_once():
    race = rd.Race(TRACE, formation=False)
    car = Car(race, "A", "a1", L - 15, "Max")
    t = _drive(race, [car], [50], 0.0, 30)
    race.open_change_window(t)
    t = _take_over(race, car, [], [], t)
    assert _row(race, "A", t)["pit_stops"] == 1, "a single lobby counts the plain box stop only"


def t_a_box_exit_that_is_also_a_rejoin_counts_once():
    race = rd.Race(TRACE, formation=False, multi_lobby=True)
    lead, late = Car(race, "A", "a1", L - 15, "Max"), Car(race, "B", "b1", L - 115, "Ann")
    t = _drive(race, [lead, late], [50, 50], 0.0, 20)                   # both still on lap 1
    race.rejoin("B", True)
    race.open_change_window(t)
    t = _take_over(race, late, [lead], [50], t)
    row = _row(race, "B", t)
    assert race.take_used_rejoins() == ["B"], "the box exit took the rejoin"
    assert row["pit_stops"] == 1, f"the counting car's box exit and its rejoin are one stop: {row}"


def t_a_change_override_at_a_stint_switch_counts_one_pit_stop():
    race = rd.Race(TRACE, formation=False, multi_lobby=True)
    old, rival = Car(race, "A", "a1", L - 15, "Max"), Car(race, "B", "b1", L - 10, "Ann")
    t = _drive(race, [old, rival], [50, 50], 0.0, 60)
    t = _switch_and_box(race, [old], t)
    new = Car(race, "A", "a2", L - 15, "Lea")
    t = _drive(race, [new, rival], [40, 50], t, 25)                     # crosses without a box stop
    assert race.override_change("A", "a2", t)
    assert race.override_change("A", "a2", t), "a second override of the same change"
    t = _drive(race, [new, rival], [40, 50], t, 2)
    row = _row(race, "A", t)
    assert row["driver"] == "Lea" and row["pit_stops"] == 1, \
        f"an override for a missed change at a switch counts once: {row}"
    race.rejoin("A", True)                                              # marked by mistake
    late = Car(race, "A", "a4", L - 15, "Kai")
    t = _drive(race, [late, rival], [40, 50], t, 2)
    t = _take_over(race, late, [rival], [50], t)
    row = _row(race, "A", t)
    assert row["driver"] == "Kai" and row["pit_stops"] == 1, \
        f"a rejoin after the override in the same window adds nothing: {row}"
    t = _drive(race, [late, rival], [50, 50], t, 30)
    t = _switch_and_box(race, [late], t)
    nxt = Car(race, "A", "a3", L - 15, "Ben")
    t = _drive(race, [nxt, rival], [40, 50], t, 2)
    t = _take_over(race, nxt, [rival], [50], t)
    assert _row(race, "A", t)["driver"] == "Ben", "the automatic change happened first"
    assert race.override_change("A", "a3", t)
    row = _row(race, "A", t)
    assert row["driver"] == "Ben" and row["pit_stops"] == 2, \
        f"an override after the automatic change adds nothing: {row}"


def t_a_box_stop_in_the_window_and_a_later_override_count_one_pit_stop():
    race = rd.Race(TRACE, formation=False, multi_lobby=True)
    old, rival = Car(race, "A", "a1", L - 15, "Max"), Car(race, "B", "b1", L - 10, "Ann")
    t = _drive(race, [old, rival], [50, 50], 0.0, 60)
    t = _switch_and_box(race, [old], t)
    old.box = False
    t = _drive(race, [old, rival], [40, 50], t, 5)                      # drives on: a plain stop
    assert _row(race, "A", t)["pit_stops"] == 1, _row(race, "A", t)
    new = Car(race, "A", "a2", L - 15, "Lea")
    t = _drive(race, [old, new, rival], [50, 40, 50], t, 25)
    assert race.override_change("A", "a2", t)
    row = _row(race, "A", t)
    assert row["driver"] == "Lea" and row["pit_stops"] == 1, \
        f"one stint switch is at most one pit stop: {row}"


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


def t_order_and_interval_compare_two_cars_at_the_same_moment():
    race = rd.Race(TRACE, formation=False)
    lead, a, b = Car(race, "L", "l", L - 15), Car(race, "A", "a", L - 300), Car(race, "B", "b", L - 302)
    t = _drive(race, [lead, a, b], [50, 50, 50], 0.0, 30)
    for _ in range(5):                       # A runs ahead of B; B's sample is 0.2 s fresher
        t += 0.2
        for car in (lead, a, b):
            car.along += 10.0
        lead.send(t)
        a.send(t)
        b.along += 10.0
        b.send(t + 0.2)
        b.along -= 10.0
    now = t + 0.2
    rows = race.standings(now)["entries"]
    assert [r["entry"] for r in rows] == ["L", "A", "B"], f"A leads B on the road: {[r['entry'] for r in rows]}"
    ra, rb = rows[1], rows[2]
    assert rb["gap_time_s"] > ra["gap_time_s"], f"gaps grow down the order: {ra['gap_time_s']} {rb['gap_time_s']}"
    assert rb["interval_time_s"] > 0 and abs(rb["interval_time_s"] - (rb["gap_time_s"] - ra["gap_time_s"])) <= 0.02, \
        f"B's interval to A is the difference of their gaps: {ra} {rb}"
    assert ra["interval_time_s"] > 5, f"A's interval is to the leader: {ra['interval_time_s']}"


def t_an_estimate_stays_behind_its_reference_when_the_field_compares_back():
    race = rd.Race(TRACE, formation=False)
    ld, a, x = Car(race, "L", "l", L - 15), Car(race, "A", "a", L - 300), Car(race, "X", "x", L - 330)
    t = _drive(race, [ld, a, x], [50, 50, 50], 0.0, 30)
    for _ in range(8 * HZ):                  # X goes quiet; L's samples lag A's by 0.8 s
        t += 1.0 / HZ
        for car in (ld, a):
            car.speed = 50.0
            car.along += 50.0 / HZ
        a.send(t)
        ld.along -= 40.0
        if t - 0.8 > 30:
            ld.send(t - 0.8)
        ld.along += 40.0
    race.estimate("X", "A", 0.5, t)
    rows = race.standings(t)["entries"]
    assert [r["entry"] for r in rows] == ["L", "A", "X"], f"X is estimated behind A: {[r['entry'] for r in rows]}"
    assert abs(rows[2]["interval_time_s"] - 0.5) <= 0.05, f"X is 0.5 s behind A: {rows[2]}"


def t_a_quiet_car_does_not_hold_the_field_back_in_time():
    race = rd.Race(TRACE, formation=False)
    ld, a, b, c = (Car(race, "L", "l", L - 15), Car(race, "A", "a", L - 300),
                   Car(race, "B", "b", L - 310), Car(race, "C", "c", L - 600))
    t = _drive(race, [ld, a, b, c], [50, 50, 50, 50], 0.0, 30)
    t = _drive(race, [ld, a, b], [50, 50, 50], t, rd.NO_DATA_S + 2)      # C's sender drops
    while _row(race, "B", t)["distance_m"] < _row(race, "A", t)["distance_m"] + 5:
        t = _drive(race, [ld, a, b], [50, 50, 60], t, 0.1)               # B passes A
    order = [r["entry"] for r in race.standings(t)["entries"]]
    assert order[:3] == ["L", "B", "A"], f"B passed A half a second ago, every sample is fresh: {order}"


def t_a_car_going_quiet_keeps_its_place_until_it_shows_no_data():
    race = rd.Race(TRACE, formation=False)
    ld, a, b = Car(race, "L", "l", L - 15), Car(race, "A", "a", L - 300), Car(race, "B", "b", L - 400)
    t = _drive(race, [ld, a, b], [50, 50, 50], 0.0, 30)
    t = _drive(race, [ld, b], [50, 50], t, 3.5)                          # A's sender stops
    t = _drive(race, [ld, b], [50, 50], t, 0.1)
    assert b.along > a.along + 50 and _row(race, "A", t)["status"] == "racing", "B drove past A's last point"
    rows = race.standings(t)["entries"]
    assert [r["entry"] for r in rows] == ["L", "A", "B"], f"A has not shown No data yet: {[r['entry'] for r in rows]}"
    assert rows[1]["gap_time_s"] < rows[2]["gap_time_s"], f"gaps grow down the order: {rows}"
    assert abs(rows[2]["interval_time_s"] - 2.0) <= 0.2, f"B ran 2 s behind A: {rows[2]['interval_time_s']}"


def t_a_car_going_quiet_fast_stays_behind_a_car_ahead_that_slows():
    race = rd.Race(TRACE, formation=False)
    ld, f, s, b = (Car(race, "L", "l", L - 15), Car(race, "F", "f", L - 300),
                   Car(race, "S", "s", L - 315), Car(race, "B", "b", L - 330))
    t = _drive(race, [ld, f, s, b], [50, 50, 50, 50], 0.0, 30)
    t = _drive(race, [ld, f, b], [20, 20, 50], t, 3)                     # S's sender stops; L and F slow
    rows = {r["entry"]: r for r in race.standings(t)["entries"]}
    order = [r["entry"] for r in race.standings(t)["entries"]]
    assert order.index("F") < order.index("S"), f"S was behind F when it fell silent: {order}"
    assert rows["F"]["interval_time_s"] > 1, f"F's interval to L stays: {rows['F']['interval_time_s']}"
    assert rows["S"]["interval_time_s"] > 0.1, f"S trails F: {rows['S']['interval_time_s']}"


def t_a_leader_going_quiet_keeps_the_lead():
    race = rd.Race(TRACE, formation=False)
    ld, a = Car(race, "L", "l", L - 15), Car(race, "A", "a", L - 50)
    t = _drive(race, [ld, a], [50, 50], 0.0, 30)
    t = _drive(race, [a], [70], t, 3)                                    # L falls silent, A speeds up
    assert [r["entry"] for r in race.standings(t)["entries"]] == ["L", "A"], "L has not shown No data yet"


def t_gaps_behind_a_dead_sender_follow_the_road_after_no_data():
    race = rd.Race(TRACE, formation=False)
    ld, c = Car(race, "L", "l", L - 15), Car(race, "C", "c", L - 1015)
    t = _drive(race, [ld, c], [50, 50], 0.0, 40)
    t = _drive(race, [c], [50], t, rd.NO_DATA_S + 1)                     # L's sender stops for good
    before = _row(race, "C", t)["gap_s"]
    t = _drive(race, [c], [80], t, 4)
    after = _row(race, "C", t)["gap_s"]
    assert _row(race, "L", t)["status"] == "no_data" and before - after > 1, \
        f"C closes on L's last point, its gap does not freeze at L's silent moment: {before} -> {after}"


def t_a_car_standing_when_its_sender_stops_is_passed():
    for box in (False, True):
        race = rd.Race(TRACE, formation=False)
        ld, a, b = Car(race, "L", "l", L - 15), Car(race, "A", "a", L - 300), Car(race, "B", "b", L - 400)
        t = _drive(race, [ld, a, b], [50, 50, 50], 0.0, 30)
        a.box = box
        t = _drive(race, [ld, a, b], [50, None, 50], t, 1.0)             # A stands, then its sender stops
        t = _drive(race, [ld, b], [50, 50], t, 3.5)
        assert b.along > a.along + 50 and _row(race, "A", t)["status"] in ("racing", "box")
        order = [r["entry"] for r in race.standings(t)["entries"]]
        assert order == ["L", "B", "A"], f"A stood still (box={box}), B drove past it: {order}"


def t_a_car_going_quiet_does_not_hold_the_field_back_in_time():
    race = rd.Race(TRACE, formation=False)
    ld, a, b, c = (Car(race, "L", "l", L - 15), Car(race, "A", "a", L - 300),
                   Car(race, "B", "b", L - 310), Car(race, "C", "c", L - 600))
    t = _drive(race, [ld, a, b, c], [50, 50, 50, 50], 0.0, 30)
    t = _drive(race, [ld, a, b], [50, 50, 50], t, 2)                     # C's sender stops
    while _row(race, "B", t)["distance_m"] < _row(race, "A", t)["distance_m"] + 5:
        t = _drive(race, [ld, a, b], [50, 50, 60], t, 0.1)               # B passes A
    assert _row(race, "C", t)["status"] == "racing", "C has not shown No data yet"
    order = [r["entry"] for r in race.standings(t)["entries"]]
    assert order[:3] == ["L", "B", "A"], f"B passed A half a second ago, a silent car pins no moment: {order}"


def t_gaps_to_a_winner_whose_sender_stops_at_the_flag_stay_current():
    race = rd.Race(TRACE, formation=False, finish_mode="laps", lap_count=2)
    a, b, c = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 100), Car(race, "C", "c", L - 105)
    t = _drive(race, [a, b, c], [80, 50, 50], 0.0, (2 * L + 15) / 80 - 0.5)
    while not _row(race, "A", t)["finished"]:
        t = _drive(race, [a, b, c], [80, 50, 50], t, 0.1)
    t = _drive(race, [b, c], [50, 65], t, 3)                             # A's sender stops; C passes B
    rows = race.standings(t)["entries"]
    assert [r["entry"] for r in rows] == ["A", "C", "B"], [r["entry"] for r in rows]
    assert rows[1]["gap_time_s"] < rows[2]["gap_time_s"], f"C's gap is the current one: {[r['gap_time_s'] for r in rows]}"


def t_a_held_car_does_not_hold_the_field_back_in_time():
    race = rd.Race(TRACE, formation=False, multi_lobby=True)
    ld, a, b, c = (Car(race, "L", "l", L - 15), Car(race, "A", "a", L - 300),
                   Car(race, "B", "b", L - 310), Car(race, "C", "c", L - 600))
    t = _drive(race, [ld, a, b, c], [50, 50, 50, 50], 0.0, 30)
    c.along = -50.0                                                      # C's console starts a new session
    t = _drive(race, [ld, a, b, c], [50, 50, 50, None], t, 3)
    assert race._entries["C"].held and not race._quiet(race._entries["C"], t), "C is held but still sends"
    while _row(race, "B", t)["distance_m"] < _row(race, "A", t)["distance_m"] + 5:
        t = _drive(race, [ld, a, b, c], [50, 50, 60, None], t, 0.1)      # B passes A
    order = [r["entry"] for r in race.standings(t)["entries"]]
    assert order[:3] == ["L", "B", "A"], f"B passed A half a second ago; a held car's old history does not count: {order}"


def t_a_car_far_behind_keeps_its_exact_gap_to_the_leader():
    race = rd.Race(TRACE, formation=False)
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 30)
    t = _drive(race, [a, b], [50, 10], 0.0, 1500)         # B falls 20 minutes behind
    row = _row(race, "B", t)
    passed = (row["distance_m"] + 15) / 50                 # when A was where B is now
    assert race._entries["A"].ts[0] > passed, "the gap reaches back past the fine history"
    assert row["gap_time_s"] is not None and abs(row["gap_time_s"] - (t - passed)) < 1.0, \
        (row["gap_time_s"], t - passed)


def t_the_long_history_stays_small():
    race = rd.Race(TRACE, formation=False)
    a = Car(race, "A", "a", L - 15)
    _drive(race, [a], [50], 0.0, rd.HISTORY_S + 120)
    ent = race._entries["A"]
    assert len(ent.ts) <= rd.MAX_HISTORY, len(ent.ts)
    assert len(ent.cts) <= 125, f"older history keeps one point per second: {len(ent.cts)}"


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


def t_a_corrected_name_applies_even_with_a_sample_of_the_same_time():
    race = rd.Race(TRACE, formation=False)
    a = Car(race, "A", "a", L - 15)
    t = _drive(race, [a], [50], 0.0, 3)
    x, z = _pos(a.along)
    race.update("A", "a", "Corrected", {"t": t, "lap": 1, "x": x, "z": z, "speed_kmh": 180.0})
    assert _row(race, "A", t)["driver"] == "Corrected", \
        "the name is the registration's, not the sample's: a coarse clock repeats a time"


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


def t_the_lap_display_shows_race_laps_and_the_final_lobbys_count_in_race_laps():
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
    st = race.standings(t)
    assert st["lap"] == {"leader": 5, "count": 6} and st["lobby_lap"] == new.lap() == 1, \
        f"race lap 5 on the final lobby's lap 1 of 2 ends after race lap 6: {st['lap']} {st['lobby_lap']}"
    t = _drive(race, [new, rival], [50, 40], t, (2 * L) / 50 + 2)
    st = race.standings(t)
    assert st["lap"] == {"leader": 6, "count": 6} and _row(race, "A", t)["laps"] == 6, \
        f"the flag falls on the count, which the desk's laps reach: {st['lap']} {_row(race, 'A', t)['laps']}"
    single = rd.Race(TRACE, formation=True, finish_mode="laps", lap_count=3)
    a = Car(single, "A", "a", L - 15)
    t = _drive(single, [a], [50], 0.0, (1.5 * L) / 50)
    st = single.standings(t)
    assert st["lap"] == {"leader": 1, "count": 2} and st["lobby_lap"] == 2, \
        f"GT7's lap 2 is race lap 1 of 2: the formation lap is no race lap: {st['lap']} {st['lobby_lap']}"
    t = _drive(single, [a], [50], t, (1.5 * L + 15) / 50 + 1)
    assert _row(single, "A", t)["finished"] and single.standings(t)["lap"] == {"leader": 2, "count": 2} \
        and _row(single, "A", t)["laps"] == 2, (single.standings(t)["lap"], _row(single, "A", t)["laps"])


def t_after_a_timed_flag_the_header_reads_the_winners_race_laps():
    race = rd.Race(TRACE, formation=True, finish_mode="time")
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 60)
    t = _drive(race, [a, b], [50, 45], 0.0, 3.5 * L / 50)
    race.final_lap(t)
    t = _drive(race, [a, b], [50, 45], t, 0.6 * L / 50)
    st = race.standings(t)
    lead = st["entries"][0]
    assert st["state"] == "finishing" and lead["finished"] and st["lap"] == {"leader": lead["laps"], "count": None}, \
        f"the winner's race laps, never one more: {st['lap']} {lead['laps']}"


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
    agreed = race._line_of(loner)
    assert agreed is not None, "three counting entries that agree give a line"
    race._cars["a"].line_s, race._cars["b"].line_s = L / 2, L / 4
    assert race._line_of(loner) == agreed, "a later disagreement keeps the line the quorum agreed on"
    race._shared_line = None
    assert race._line_of(loner) is None, "no majority within SNAP_M: no shared line"
    two = rd.Race(TRACE, formation=False)
    pair = [Car(two, e, e.lower(), L - 15 - 10 * i) for i, e in enumerate("AB")]
    _drive(two, pair, [50, 50], 0.0, 3)
    assert two._line_of(loner) is None, "two entries are no quorum"


def t_every_car_leaving_the_next_lobbys_box_takes_over_whatever_the_others_do():
    """The next lobby's cars connect in the box and leave it within seconds of each
    other, before any of them crosses the line: by the last exit no counting car has a
    line station of its own."""
    race = rd.Race(TRACE, formation=False, multi_lobby=True)
    teams = "ABCD"
    old = [Car(race, e, e.lower() + "1", L - 15 - 20 * i, "Old" + e) for i, e in enumerate(teams)]
    t = _drive(race, old, [50] * 4, 0.0, 60)
    race.open_change_window(t)
    for car in old:
        car.box = True
    t = _drive(race, old, [None] * 4, t, 3)
    anchors = {e: _row(race, e, t)["distance_m"] for e in teams}
    new = []
    for i, e in enumerate(teams):
        car = Car(race, e, e.lower() + "2", 0.0, "New" + e)
        _new_lobby(car, L - 300 - 20 * i)
        new.append(car)
    t = _drive(race, new, [None] * 4, t, 3)
    for car in new:
        car.box = False
        t = _drive(race, new, [40 if c.box is False else None for c in new], t, 1)
    t = _drive(race, new, [40] * 4, t, 3)
    rows = {e: _row(race, e, t) for e in teams}
    assert all(rows[e]["driver"] == "New" + e for e in teams), \
        f"every box exit takes its entry over: {[(e, r['driver'], r['status']) for e, r in rows.items()]}"
    assert all(abs(rows[e]["distance_m"] - anchors[e] - 40 * (7 - i)) < 25 for i, e in enumerate(teams)), \
        f"each continues from its anchor: {[(rows[e]['distance_m'], anchors[e]) for e in teams]}"


def t_a_restart_lobby_of_new_connections_resumes_every_car_at_its_box_exit():
    race = rd.Race(TRACE, formation=False)
    teams = "ABC"
    old = [Car(race, e, e.lower() + "1", L - 15 - 20 * i, "Old" + e) for i, e in enumerate(teams)]
    t = _drive(race, old, [50] * 3, 0.0, 40)
    lead = max(_row(race, e, t)["distance_m"] for e in teams)
    race.red_flag(t, "keep")
    new = []
    for i, e in enumerate(teams):
        car = Car(race, e, e.lower() + "2", 0.0, "New" + e)
        _new_lobby(car, L - 300 - 20 * i)
        new.append(car)
    t = _drive(race, new, [None] * 3, t, 3)
    for car in new:
        car.box = False
    t = _drive(race, new, [40] * 3, t, 3)
    rows = {e: _row(race, e, t) for e in teams}
    assert all(r["driver"] == "New" + e and r["status"] == "racing" for e, r in rows.items()) \
        and all(abs(r["distance_m"] - lead - 120) < 15 for r in rows.values()), \
        f"no car of the restart lobby has crossed yet, each resumes from the keep anchor: {rows}"


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
    assert abs(_row(race, "B", t)["distance_m"] - (da + 200)) < 25, \
        f"keep restarts B from A's frozen distance; its later release is its gap: {db}"
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


def t_a_rejoin_starts_where_the_leaders_old_car_stopped_even_after_an_early_switch():
    race = rd.Race(TRACE, formation=False, multi_lobby=True)
    lead, gone = Car(race, "A", "a1", L - 15, "Max"), Car(race, "B", "b1", L - 115, "Ann")
    quiet = Car(race, "X", "x1", L - 65, "Xen")
    t = _drive(race, [lead, gone, quiet], [50, 50, 50], 0.0, 30)
    t = _drive(race, [lead], [50], t, 30)                               # B disconnected, X quiet
    race.estimate("X", "A", 1.0, t)                                     # X follows A's next car too
    race.rejoin("B", True)
    race.open_change_window(t)                                          # the leader is still on its lap
    t = _drive(race, [lead], [50], t, 5)
    lead.box = True
    t = _drive(race, [lead], [None], t, 3)
    stop = _row(race, "A", t)["distance_m"]
    nxt, back = Car(race, "A", "a2", L - 15, "Lea"), Car(race, "B", "b2", L - 15, "Ben")
    t = _drive(race, [nxt, back], [40, 40], t, 2)
    nxt.box = back.box = True
    t = _drive(race, [nxt, back], [None, None], t, 3)
    nxt.box = False
    for _ in range(10):                                                 # B departs 10 s later
        t = _drive(race, [nxt, back], [40, None], t, 1)
        race.standings(t)
    assert _row(race, "X", t)["distance_m"] > stop, "the estimate runs past A's stint end"
    back.box = False
    t = _drive(race, [nxt, back], [40, 40], t, 5)
    rb = _row(race, "B", t)
    assert abs(rb["distance_m"] - (stop + 200)) < 25, f"B starts where A's old car stopped: {rb} {stop}"
    assert abs(rb["gap_s"] - 10.0) < 0.5, f"only the later departure is the deficit: {rb}"


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
    assert row["pit_stops"] == 0, f"an override without a stint switch is no pit stop: {row}"


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
    assert abs(_row(race, "B", t)["gap_time_s"] - 2.0) < 0.2, \
        f"the lobby raced on, so no stoppage leaves the gaps: {_row(race, 'B', t)}"


def _mis_press_with_a_car_that_never_sent():
    race = rd.Race(TRACE, formation=False)
    race.set_roster(["A", "B", "E"])
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 115)
    t = _drive(race, [a, b], [50, 50], 0.0, 20)
    race.estimate("E", "B", 2.0, t)                            # E never sent: no car waits for it
    t = _drive(race, [a, b], [50, 50], t, 2)
    race.red_flag(t, "keep")                                   # pressed by mistake
    t = _drive(race, [a, b], [50, 50], t, 4)
    race.release(t)
    return race, (a, b), _drive(race, [a, b], [50, 50], t, 5)


def t_a_mis_pressed_red_flag_leaves_no_stoppage_behind_a_car_that_never_sent():
    race, (a, b), t = _mis_press_with_a_car_that_never_sent()
    assert abs(_row(race, "B", t)["gap_time_s"] - 2.0) < 0.2, \
        f"the lobby raced on, so no stoppage leaves the gaps: {_row(race, 'B', t)}"
    t = _drive(race, [a, b], [50, 50], t, 30)
    race.red_flag(t, "keep")
    flag = t
    for car in (a, b):
        _new_lobby(car, L - 15)
    t = _drive(race, [a, b], [None, None], t, 20)
    a.box = False
    t = _drive(race, [a, b], [50, None], t, 2)
    b.box = False
    t = _drive(race, [a, b], [50, 50], t, 1)
    race.release(t)
    assert len(race._pauses) == 1 and abs(race._pauses[0][0] - flag) < 0.2, \
        f"only the real red flag's stoppage counts: {race._pauses} from {flag}"
    t = _drive(race, [a, b], [50, 50], t, 5)
    assert abs(_row(race, "B", t)["gap_time_s"] - 2.0) < 0.3, _row(race, "B", t)


def t_a_stoppage_ends_when_its_last_restart_lobby_car_retires_or_the_race_finishes():
    for end in ("retire", "finish"):
        race = rd.Race(TRACE, formation=False)
        a, b, c = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 115), Car(race, "C", "c", L - 215)
        t = _drive(race, [a, b, c], [50, 50, 50], 0.0, 20)
        race.red_flag(t, "keep")
        _new_lobby(c, L - 15)                                  # only C moved to a restart lobby
        t = _drive(race, [a, b, c], [50, 50, None], t, 4)
        race.release(t)                                        # A and B raced on
        if end == "retire":
            race.retire("C", True, t)
        else:
            race.finish(t)
        t = _drive(race, [a, b], [50, 50], t, 3)
        assert race._stopped is None and (end == "finish" or abs(_row(race, "B", t)["gap_time_s"] - 2.0) < 0.2), \
            f"after a {end} no stoppage stays open: {race._stopped} {_row(race, 'B', t)}"


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


def _keep_restart_final_lobby(mode, lap_count=3, quiet_car=False):
    """Endurance final lobby after a keep restart from one box 180 m before the line:
    A leaves the box first, B 2 s later, and Race Control then takes 150 m off B, so B
    trails by 230 m of race distance but only 80 m on the road; C leaves 8 s after B.
    B is faster, so it passes A on the road but not in race distance.
    `quiet_car`: the final lobby runs five laps before the red flag, and a fourth car
    E stops sending shortly before it and never joins the restart lobby."""
    race = rd.Race(TRACE, formation=False, multi_lobby=True, finish_mode=mode)
    a, b, c = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 165), Car(race, "C", "c", L - 600)
    if quiet_car:
        race.final_lobby(lap_count if mode == "laps" else None, 0.0)
        e = Car(race, "E", "e", L - 300)
        t = _drive(race, [a, b, c, e], [50] * 4, 0.0, 5 * L / 50 - 10)
        t = _drive(race, [a, b, c], [50] * 3, t, 10)
    else:
        t = _drive(race, [a, b, c], [50, 50, 50], 0.0, 40)
        race.final_lobby(lap_count if mode == "laps" else None, t)
    race.red_flag(t, "keep")
    for car in (a, b, c):
        _new_lobby(car, L - 180)
    t = _drive(race, [a, b, c], [None] * 3, t, 3)
    a.box = False
    t = _drive(race, [a, b, c], [40, None, None], t, 2)
    b.box = False
    t = _drive(race, [a, b, c], [40, 40, None], t, 8)                  # C released by its larger gap
    c.box = False
    t = _drive(race, [a, b, c], [50, 50.8, 50], t, 3)
    race.release(t)
    race.correct("B", -150.0)
    return race, (a, b, c), t


def _flags(race, cars, t, seconds, speeds=(50, 50.8, 50)):
    """Drive on and record each car's (time, GT7 lap) when it first shows finished."""
    flags = {}
    for _ in range(int(seconds * HZ)):
        t = _drive(race, list(cars), list(speeds), t, 1.0 / HZ)
        for car in cars:
            if car.entry not in flags and _row(race, car.entry, t)["finished"]:
                flags[car.entry] = (t, car.lap())
    return flags, t


def t_after_a_keep_restart_the_first_car_past_the_lobby_count_starts_the_finish():
    race, cars, t = _keep_restart_final_lobby("laps")
    flags, t = _flags(race, cars, t, 1.6 * 3 * L / 50)
    assert {e: lap for e, (_, lap) in flags.items()} == {"A": 4, "B": 4, "C": 4}, \
        f"every car takes the flag on the lobby lap GT7 ends the race: {flags}"
    assert flags["B"][0] < flags["A"][0], f"B leads on the road, so it takes the flag first: {flags}"
    snap = race.standings(t)
    rows = snap["entries"]
    assert snap["state"] == "finished" and [r["entry"] for r in rows] == ["A", "B", "C"], \
        f"race laps, then who completed them first: {[(r['entry'], r['laps']) for r in rows]}"
    assert len({r["laps"] for r in rows}) == 1, [r["laps"] for r in rows]
    gaps = [r["gap_time_s"] for r in rows]
    assert gaps[0] == 0.0 and 0 < gaps[1] < gaps[2] < 30, f"gaps in tower order: {gaps}"


def t_after_a_keep_restart_the_lap_count_in_race_laps_falls_with_the_flag():
    """After the keep restart the race distance's lap lines no longer sit on the lobby's
    line; the count in race laps still meets the leader's laps at the flag."""
    race, (a, b, c), t = _keep_restart_final_lobby("laps")
    counts, over = set(), []
    while race.standings(t)["state"] == "racing":
        t = _drive(race, [a, b, c], [50, 50.8, 50], t, 1.0 / HZ)
        lap = race.standings(t)["lap"]
        counts.add(lap["count"])
        if lap["leader"] > lap["count"]:
            over.append(lap)
    assert len(counts) == 1 and not over, f"one count, never passed: {counts} {over[:2]}"
    t = _drive(race, [a, b, c], [50, 50.8, 50], t, 5)
    st = race.standings(t)
    assert st["lap"]["leader"] == st["lap"]["count"] == st["entries"][0]["laps"], \
        f"the header ends on the leader's race laps: {st['lap']} {st['entries'][0]['laps']}"


def t_after_a_keep_restart_a_timed_finish_ends_at_the_first_lead_lap_crossing():
    race, cars, t = _keep_restart_final_lobby("time")
    t = _drive(race, list(cars), [50, 50.8, 50], t, 100)
    race.final_lap(t)
    called = t
    flags, t = _flags(race, cars, t, 1.6 * L / 50)
    assert set(flags) == {"A", "B", "C"} and max(f[0] for f in flags.values()) - called < L / 50, \
        f"every car takes the flag within one lap of the call: {flags}"
    assert min(flags, key=lambda e: flags[e][0]) == "B", f"B crosses first on the lead lap: {flags}"
    rows = race.standings(t)["entries"]
    assert [r["entry"] for r in rows] == ["A", "B", "C"] and len({r["laps"] for r in rows}) == 1, \
        [(r["entry"], r["laps"]) for r in rows]


def t_after_a_keep_restart_a_lapped_finisher_leaves_the_leaders_their_laps():
    race, cars, t = _keep_restart_final_lobby("laps")
    race.correct("C", -L)                                     # Race Control takes a lap off C
    flags, t = _flags(race, cars, t, 1.6 * 3 * L / 50)
    rows = race.standings(t)["entries"]
    assert set(flags) == {"A", "B", "C"} and [r["entry"] for r in rows] == ["A", "B", "C"], (flags, rows)
    assert rows[0]["laps"] == rows[1]["laps"] == rows[2]["laps"] + 1, \
        f"a lapped car's flag does not take a lap off the leaders: {[(r['entry'], r['laps']) for r in rows]}"
    assert rows[1]["lapped"] == 0 and 0 < rows[1]["gap_time_s"] < 5, rows[1]


def t_a_running_car_keeps_its_gap_and_lap_against_a_finished_car():
    """After a keep restart C and D left their box 100 m before A's, so their race
    distance reaches A's frozen finish distance 100 m before their own line."""
    race = rd.Race(TRACE, formation=False, multi_lobby=True, finish_mode="time")
    a, c, d = Car(race, "A", "a", L - 15), Car(race, "C", "c", L - 65), Car(race, "D", "d", L - 115)
    t = _drive(race, [a, c, d], [50, 50, 50], 0.0, 40)
    race.final_lobby(None, t)
    race.red_flag(t, "keep")
    _new_lobby(a, L - 180)
    _new_lobby(c, L - 280)
    _new_lobby(d, L - 280)
    t = _drive(race, [a, c, d], [None] * 3, t, 3)
    a.box = False
    t = _drive(race, [a, c, d], [50, None, None], t, 3)
    c.box = False
    t = _drive(race, [a, c, d], [50, 50, None], t, 3)
    d.box = False
    t = _drive(race, [a, c, d], [50, 50, 50], t, 3)
    race.correct("D", -L)                                       # Race Control takes a lap off D
    t = _drive(race, [a, c, d], [50, 50, 50], t, 60)
    while not a.lap() == c.lap() == d.lap():
        t = _drive(race, [a, c, d], [50, 50, 50], t, 1.0 / HZ)
    race.final_lap(t)
    seen = []
    while not _row(race, "D", t)["finished"] and len(seen) < 3 * L / 50 * HZ:
        t = _drive(race, [a, c, d], [50, 50, 50], t, 1.0 / HZ)
        rc, rd_ = _row(race, "C", t), _row(race, "D", t)
        if _row(race, "A", t)["finished"]:
            seen.append((rc["finished"], rc["gap_time_s"], rc["interval_time_s"], rd_["lapped"]))
    running = [s for s in seen if not s[0]]
    assert running and all(g is not None and abs(g - 3.0) < 0.2 for _, g, _, _ in running), \
        f"a running car keeps its 3 s behind a finished one, never 0.0: {[s[1] for s in running]}"
    assert all(i is not None and abs(i - 3.0) < 0.2 for _, _, i, _ in running), \
        f"and its interval: {[s[2] for s in running]}"
    assert seen and all(s[3] == 1 for s in seen), \
        f"a lapped car stays lapped until and after its flag: {[s[3] for s in seen]}"


def t_a_quiet_car_does_not_hold_back_a_timed_finish_after_a_keep_restart():
    race, cars, t = _keep_restart_final_lobby("time", quiet_car=True)
    t = _drive(race, list(cars), [50, 50.8, 50], t, 100)
    race.final_lap(t)
    called = t
    flags, t = _flags(race, cars, t, 1.6 * L / 50)
    assert set(flags) == {"A", "B", "C"} and max(f[0] for f in flags.values()) - called < L / 50, \
        f"E's counter from before the red flag is no lead lap: {flags}"


def t_an_endurance_winner_stays_first_when_its_race_lap_ends_past_the_line():
    race = rd.Race(TRACE, formation=False, multi_lobby=True, finish_mode="laps")
    race.final_lobby(2, 0.0)
    x, y = Car(race, "X", "x", L - 25), Car(race, "Y", "y", L - 15)
    t = _drive(race, [x, y], [50, 50], 0.0, 5)
    race.correct("X", -5.0)                                   # X's race lap ends 5 m past the line
    race.correct("Y", 5.0)                                    # Y's 5 m before it
    flags, t = _flags(race, [x, y], t, 2 * L / 50 + 10, speeds=(51, 50))
    rows = race.standings(t)["entries"]
    assert set(flags) == {"X", "Y"} and flags["X"][0] < flags["Y"][0], flags
    assert [r["entry"] for r in rows] == ["X", "Y"] and rows[1]["laps"] <= rows[0]["laps"], \
        f"X passed Y and took the flag first: {[(r['entry'], r['laps']) for r in rows]}"


def t_a_car_sent_round_again_shows_the_winners_laps_and_its_gap_at_that_lap():
    race = rd.Race(TRACE, formation=False, finish_mode="laps", lap_count=2)
    a, b, c = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 60), Car(race, "C", "c", L - 800)
    t = _drive(race, [a, b, c], [50, 50, 50], 0.0, 2)
    race.correct("B", 200.0)                                  # Race Control puts B ahead
    t = _drive(race, [a, b, c], [50, 50, 50], t, (2 * L + 60) / 50)
    t = _drive(race, [a, b, c], [50, 50, 50], t, L / 50 + 2)  # A goes round again
    ra, rb, rc = (_row(race, e, t) for e in "ABC")
    assert ra["finished"] and ra["laps"] == rb["laps"], f"no more laps than the winner: {ra} {rb}"
    assert ra["gap_time_s"] is not None and 0 < ra["gap_time_s"] < rc["gap_time_s"], \
        f"A's gap is measured where it completed the winner's laps: {ra['gap_time_s']} {rc['gap_time_s']}"


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


def t_lapped_car_keeps_an_exact_time_to_the_leader():
    race = rd.Race(TRACE, formation=False)
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 6)
    t = _drive(race, [a, b], [60, 30], 0.0, L / 30 * 1.2)
    ra, rb = _row(race, "A", t), _row(race, "B", t)
    assert rb["lapped"] == 1 and rb["gap_s"] is None, rb
    expected = (ra["distance_m"] - rb["distance_m"]) / 60
    assert expected > L / 60, expected
    assert abs(rb["gap_time_s"] - expected) < 0.5, (rb["gap_time_s"], expected)
    assert rb["interval_time_s"] == rb["gap_time_s"], rb


def t_leader_has_zero_gap_time_and_no_interval():
    race = rd.Race(TRACE, formation=False)
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 50)
    t = _drive(race, [a, b], [30, 30], 0.0, 3)
    row = _row(race, "A", t)
    assert row["gap_time_s"] == 0.0 and row["interval_time_s"] is None, row


def t_quiet_car_reports_its_silence_and_box():
    race = rd.Race(TRACE, formation=False)
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 50)
    t = _drive(race, [a, b], [30, 30], 0.0, 3)
    b.box = True
    t = _drive(race, [a, b], [30, None], t, 6)
    t = _drive(race, [a], [30], t, rd.NO_DATA_S + 1)
    row = _row(race, "B", t)
    assert row["status"] == "no_data" and row["quiet_s"] >= 6.0, row
    assert row["in_box"] is True and row["gap_time_s"] is None, row
    assert _row(race, "A", t)["quiet_s"] < 1.0


def t_standings_report_the_change_window():
    race = rd.Race(TRACE, formation=False, multi_lobby=True)
    a = Car(race, "A", "a", L - 15)
    t = _drive(race, [a], [30], 0.0, 2)
    assert race.standings(t)["change_window"] is False
    race.open_change_window(t)
    assert race.standings(t)["change_window"] is True
    assert race.standings(t + rd.CHANGE_WINDOW_S + 1)["change_window"] is False


def t_rows_mark_entries_waiting_for_their_restart():
    race = rd.Race(TRACE, formation=False)
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 115)
    t = _drive(race, [a, b], [50, 50], 0.0, 40)
    assert not _row(race, "A", t)["waiting"], "a racing car waits for nothing"
    race.red_flag(t, "keep")
    for car in (a, b):
        _new_lobby(car, L - 15)
    t = _drive(race, [a, b], [None, None], t, 3)
    race.release(t)
    assert race.standings(t)["red_flag"] is None
    assert _row(race, "A", t)["waiting"] and _row(race, "B", t)["waiting"], \
        "after the release the cars in the restart lobby still wait for their restart"
    a.box = False
    t = _drive(race, [a, b], [40, None], t, 5)
    assert not _row(race, "A", t)["waiting"], "A restarted"
    assert _row(race, "B", t)["waiting"], "B still stands in the box"


def _staggered_keep_restart():
    """A leads B by 2 s, C runs more than a lap down. After a minute's red flag Race
    Control releases the restart lobby by the gaps: A, B 2 s later, C 3 s after A."""
    race = rd.Race(TRACE, formation=False)
    a, b, c = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 115), Car(race, "C", "c", L - 215)
    t = _drive(race, [a, b, c], [50, 50, 20], 0.0, 140)
    before = {e: _row(race, e, t) for e in "ABC"}
    assert before["C"]["lapped"] == 1 and abs(before["B"]["gap_time_s"] - 2.0) < 0.1, before
    race.red_flag(t, "keep")
    for car in (a, b, c):
        _new_lobby(car, L - 15)
    t = _drive(race, [a, b, c], [None] * 3, t, 60)
    a.box = False
    t = _drive(race, [a, b, c], [50, None, None], t, 2)
    b.box = False
    t = _drive(race, [a, b, c], [50, 50, None], t, 1)
    c.box = False
    t = _drive(race, [a, b, c], [50, 50, 50], t, 0.5)
    race.release(t)
    return race, (a, b, c), t, before


def t_a_keep_restart_released_by_the_gaps_counts_each_gap_once():
    race, cars, t, before = _staggered_keep_restart()
    lead = before["A"]["distance_m"]
    for _ in range(100):
        rb, rc = _row(race, "B", t), _row(race, "C", t)
        assert abs(rb["gap_time_s"] - 2.0) < 0.3 and abs(rb["gap_s"] - 2.0) < 0.3, \
            f"B left 2 s after A from A's distance: its gap is the stagger, once: {rb}"
        assert rc["lapped"] == 1 and abs(rc["gap_time_s"] - (L / 50 + 3.0)) < 0.5, \
            f"C keeps its lap down and reads A's lap plus its 3 s, without the stoppage: {rc}"
        t = _drive(race, list(cars), [50, 50, 50], t, 0.5)
    ra, rb = _row(race, "A", t), _row(race, "B", t)
    assert abs(ra["distance_m"] - rb["distance_m"] - 100) < 15, \
        f"B resumed from A's frozen distance {lead}, 2 s later: {ra['distance_m']} {rb['distance_m']}"
    assert _row(race, "C", t)["laps"] == _row(race, "A", t)["laps"] - 1, "C keeps its whole lap behind"
    race.finish(t)
    rc = _row(race, "C", t)
    assert rc["finished"] and abs(rc["gap_time_s"] - (L / 50 + 3.0)) < 0.5, f"C's final gap: {rc}"


def _pit_lane_restart(mode="keep", lead_speed=50, seconds=200, on_step=None):
    """As `_staggered_keep_restart`, but every car leaves the restart box down a 150 m
    pit lane at 15 m/s; A and B then run at `lead_speed`. `on_step(race, cars, step, t)`
    runs after each tick from the last release on; returns its results."""
    race = rd.Race(TRACE, formation=False)
    cars = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 115), Car(race, "C", "c", L - 215)
    t = _drive(race, list(cars), [50, 50, 20], 0.0, 140)
    race.red_flag(t, mode)
    for car in cars:
        _new_lobby(car, L - 15)
    t = _drive(race, list(cars), [None] * 3, t, 60)
    out, seen = {}, []
    for step in range(int(seconds * HZ)):
        for car, at in zip(cars, (0, 20, 30), strict=True):
            if car.entry not in out and step >= at:
                car.box, out[car.entry] = False, car.along
        speeds = [None if car.box else 15 if car.along - out[car.entry] < 150 else v
                  for car, v in zip(cars, (lead_speed, lead_speed, 50), strict=True)]
        t = _drive(race, list(cars), speeds, t, 1.0 / HZ)
        if step >= 30 and on_step is not None:
            seen.append(on_step(race, cars, step, t))
    return race, [x for x in seen if x is not None]


def _plain_gap(race, ahead, entry, now):
    """`entry`'s time behind `ahead` from `ahead`'s pass of the same distance."""
    a, e = race._entries[ahead], race._entries[entry]
    reached = race._time_at(a, e.distance + e.adjust - a.adjust)
    return race._racing(race._cars[e.active].t) - race._racing(reached)


def t_a_lapped_car_released_at_its_gap_keeps_it_through_the_pit_lane():
    def sample(race, cars, step, t):
        if step % 5 == 0:
            rb, rc = _row(race, "B", t), _row(race, "C", t)
            return rb["gap_time_s"], rc["gap_time_s"], rc["interval_time_s"], rc["lapped"]
        return None
    _, seen = _pit_lane_restart(on_step=sample)
    assert all(abs(g - 2.0) < 0.3 for g, _, _, _ in seen), f"B's gap: {sorted({s[0] for s in seen})}"
    assert all(lap == 1 and abs(g - (L / 50 + 3.0)) < 0.5 for _, g, _, lap in seen), \
        f"C reads A's lap plus its 3 s from the box exit on: {sorted({s[1] for s in seen})}"
    assert all(abs(i - (L / 50 + 1.0)) < 0.5 for _, _, i, _ in seen), \
        f"and B's lap plus 1 s behind B: {sorted({s[2] for s in seen})}"


def t_a_correction_after_a_keep_restart_moves_the_gaps_once():
    def sample(race, cars, step, t):
        if step == 40:
            race.correct("A", 100.0)                      # 2 s at 50 m/s
        if step > 40 and step % 5 == 0:
            return _row(race, "B", t)["gap_time_s"], _row(race, "C", t)["gap_time_s"]
        return None
    _, seen = _pit_lane_restart(on_step=sample)
    assert all(abs(b - 4.0) < 0.3 for b, _ in seen), f"B's 2 s plus A's 100 m: {sorted({s[0] for s in seen})}"
    assert all(abs(c - (L / 50 + 5.0)) < 0.5 for _, c in seen), \
        f"C's lap and 3 s plus A's 100 m, counted once: {sorted({s[1] for s in seen})}"


def t_a_neutralised_restart_keeps_the_lapped_gap_through_the_pit_lane():
    def sample(race, cars, step, t):
        if step % 5 == 0:
            return _row(race, "C", t)["gap_time_s"]
        return None
    _, seen = _pit_lane_restart(mode="neutralise", on_step=sample)
    assert max(seen) - min(seen) < 0.5, f"C's gap holds from its box exit on: {min(seen)} to {max(seen)}"


def t_a_lapped_car_hands_back_to_the_plain_gap_a_lap_past_the_leaders_restart():
    """A and B run 55 m/s after the restart: past the window the gap is the plain one."""
    def sample(race, cars, step, t):
        a, c = race._entries["A"], race._entries["C"]
        past = c.distance + c.adjust - (a.resumed[2] + a.adjust) - L
        if past > 0:
            return past, _row(race, "C", t)["gap_time_s"], _plain_gap(race, "A", "C", t)
        return None
    _, seen = _pit_lane_restart(lead_speed=55, seconds=260, on_step=sample)
    assert seen and all(abs(g - plain) < 0.05 for _, g, plain in seen), \
        f"a lap past A's restart point C reads its plain gap: {seen[:3]}"


def t_a_second_red_flag_resets_the_restart_reference():
    """After the first restart a second red flag sends only C to a restart lobby; A
    and B race on. C's restart point and A's then come from different red flags."""
    def sample(race, cars, step, t):
        a, b, c = cars
        if step == 60:
            race.red_flag(t, "keep")
            _new_lobby(c, L - 15)
        if step == 70:
            race.release(t)
        if step == 90:
            c.box = False
        if step >= 120 and step % 5 == 0 and race._entries["C"].resumed[0] == 2:
            return _row(race, "C", t)["gap_time_s"], _plain_gap(race, "A", "C", t)
        return None
    _, seen = _pit_lane_restart(seconds=60, on_step=sample)
    assert seen and all(abs(g - plain) < 0.05 for g, plain in seen), \
        f"no reference across two red flags: {seen[:3]}"


def t_a_finish_by_hand_after_a_keep_restart_leaves_out_the_stoppage():
    race = rd.Race(TRACE, formation=False)
    a, c = Car(race, "A", "a", L - 15), Car(race, "C", "c", L - 25)
    t = _drive(race, [a, c], [50, 15], 0.0, 1.9 * L / 50)            # C a lap down, still on lap 0
    race.red_flag(t, "keep")
    for car in (a, c):
        _new_lobby(car, L - 15)
    t = _drive(race, [a, c], [None, None], t, 60)
    a.box = False
    t = _drive(race, [a, c], [50, None], t, 3)
    c.box = False
    t = _drive(race, [a, c], [50, 50], t, 2)
    race.finish(t)
    rc = _row(race, "C", t)
    assert rc["finished"] and rc["laps"] == 0 and abs(rc["gap_time_s"] - (L / 50 + 3.0)) < 0.5, \
        f"C's final gap is A's lap plus the 3 s stagger, without the stoppage: {rc}"


def t_a_reference_inside_the_restart_jump_never_lands_in_the_stoppage():
    """B stood 300 m behind A at the red flag, so keep moves its anchor 300 m ahead; E
    ran 50 m behind B and Race Control takes 100 m off E after the restart. E's
    restart point then lies inside B's jump from its frozen distance to its anchor."""
    race = rd.Race(TRACE, formation=False)
    cars = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 315), Car(race, "E", "e", L - 365)
    t = _drive(race, list(cars), [50] * 3, 0.0, 40)
    race.red_flag(t, "keep")
    for car in cars:
        _new_lobby(car, L - 15)
    t = _drive(race, list(cars), [None] * 3, t, 60)
    seen = []
    for step in range(int(40 * HZ)):
        for car, at in zip(cars, (0, 60, 70), strict=True):
            if step == at:
                car.box = False
        t = _drive(race, list(cars), [None if c.box else 50 for c in cars], t, 1.0 / HZ)
        if step == 80:
            race.correct("E", -100.0)                       # 2 s at 50 m/s
        if step > 80 and step % 5 == 0:
            re = _row(race, "E", t)
            seen.append((re["gap_time_s"], re["interval_time_s"]))
    assert all(abs(g - 9.0) < 0.3 for g, _ in seen), f"E's 7 s release plus 2 s: {sorted({s[0] for s in seen})}"
    assert all(abs(i - 3.0) < 0.3 for _, i in seen), \
        f"1 s after B plus 2 s, B's way to its anchor on the racing clock: {sorted({s[1] for s in seen})}"


def t_a_lapped_finish_before_its_restart_point_compares_the_laps_it_drove_before():
    """C finishes right after its keep restart, so its lap count lies behind its restart
    point: both cars passed it before the red flag, A at changing pace."""
    race = rd.Race(TRACE, formation=False)
    a, c = Car(race, "A", "a", L - 15), Car(race, "C", "c", L - 25)
    t = _drive(race, [a, c], [50, 22], 0.0, 1.75 * L / 50)
    t = _drive(race, [a, c], [40, 22], t, 0.75 * L / 40)
    race.red_flag(t, "keep")
    for car in (a, c):
        _new_lobby(car, L - 15)
    t = _drive(race, [a, c], [None, None], t, 60)
    a.box = False
    t = _drive(race, [a, c], [50, None], t, 3)
    c.box = False
    t = _drive(race, [a, c], [50, 50], t, 2)
    race.finish(t)
    rc, ea, ec = _row(race, "C", t), race._entries["A"], race._entries["C"]
    plain = race._racing(race._time_at(ec, L)) - race._racing(race._time_at(ea, L))
    assert rc["finished"] and rc["laps"] == 1 and abs(rc["gap_time_s"] - plain) < 0.05, \
        f"C's final gap at its lap count: {rc['gap_time_s']} vs {plain}"


def t_a_quiet_car_ahead_gives_no_exact_gap():
    race = rd.Race(TRACE, formation=False)
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 50)
    t = _drive(race, [a, b], [30, 30], 0.0, 3)
    t = _drive(race, [b], [2], t, rd.NO_DATA_S + 1)
    row = _row(race, "B", t)
    assert row["gap_time_s"] is None and row["interval_time_s"] is None, \
        f"the quiet leader's stale history gives no exact time: {row}"
    assert row["gap_s"] is not None, f"gap_s keeps its meaning: {row}"



def _quiet_c(seconds=1.0, **kw):
    """A, B, C and D at 40 m/s, C one second behind B; C's sender stops and Race
    Control puts it `seconds` behind B."""
    race = rd.Race(TRACE, formation=False, **kw)
    a, b, c, d = (Car(race, e, e.lower(), L - g) for e, g in (("A", 10), ("B", 50), ("C", 90), ("D", 170)))
    t = _drive(race, [a, b, c, d], [40, 40, 40, 40], 0.0, 5)
    t = _drive(race, [a, b, d], [40, 40, 40], t, rd.NO_DATA_S + 1)
    race.estimate("C", "B", seconds, t)
    return race, (a, b, c, d), t



def t_an_estimated_car_moves_on_the_map_with_its_reference():
    race, (a, b, c, d), t = _quiet_c()
    t = _drive(race, [a, b, d], [40, 40, 40], t, 30)
    rc = _row(race, "C", t)
    x, z = _pos(b.along - 40)                              # where B was a second ago
    assert math.hypot(rc["x"] - x, rc["z"] - z) < 5, \
        f"C's map dot follows B: {(rc['x'], rc['z'])} vs {(x, z)}"

def t_an_estimate_moves_a_quiet_car_with_its_reference():
    race, (a, b, c, d), t = _quiet_c()
    t = _drive(race, [a, b, d], [40, 40, 40], t, 30)
    rb, rc = _row(race, "B", t), _row(race, "C", t)
    assert rc["status"] == "est", rc
    assert abs(rc["distance_m"] - (rb["distance_m"] - 40)) < 3, \
        f"C is where B was a second ago: {rc['distance_m']} vs {rb['distance_m']}"
    assert [r["entry"] for r in race.standings(t)["entries"]] == ["A", "B", "C", "D"], \
        "the quiet car keeps its place ahead of D"
    assert abs(rc["gap_s"] - (rb["gap_s"] + 1.0)) < 0.02, (rb["gap_s"], rc["gap_s"])
    assert abs(rc["gap_time_s"] - (rb["gap_time_s"] + 1.0)) < 0.02, (rb, rc)
    assert abs(rc["interval_s"] - 1.0) < 0.02 and abs(rc["interval_time_s"] - 1.0) < 0.02, rc
    rd_ = _row(race, "D", t)
    assert abs(rd_["interval_time_s"] - (rd_["gap_time_s"] - rc["gap_time_s"])) < 0.02, \
        f"the car behind takes its interval from the gaps: {rd_}"
    assert rc["estimate"] == {"ref": "B", "seconds": 1.0, "ahead": False}, rc["estimate"]


def t_an_estimate_ends_when_the_car_sends_again():
    race, (a, b, c, d), t = _quiet_c()
    t = _drive(race, [a, b, d], [40, 40, 40], t, 10)
    assert _row(race, "C", t)["status"] == "est"
    c.along = b.along - 300                               # it really fell back
    t = _drive(race, [a, b, c, d], [40, 40, 40, 40], t, 2)
    rc = _row(race, "C", t)
    assert rc["status"] == "racing" and rc["estimate"] is None, rc
    assert race.take_ended_estimates() == ["C"] and race.take_ended_estimates() == []
    assert [r["entry"] for r in race.standings(t)["entries"]] == ["A", "B", "D", "C"], \
        "C continues from its real distance"
    t = _drive(race, [a, b, d], [40, 40, 40], t, rd.NO_DATA_S + 1)
    assert _row(race, "C", t)["status"] == "no_data", "an ended estimate does not come back"


def t_an_estimate_on_a_live_car_waits_until_it_goes_quiet():
    race = rd.Race(TRACE, formation=False)
    a, b = Car(race, "A", "a", L - 10), Car(race, "B", "b", L - 50)
    t = _drive(race, [a, b], [40, 40], 0.0, 5)
    race.estimate("B", "A", 2.0, t)
    t = _drive(race, [a, b], [40, 40], t, 5)
    assert _row(race, "B", t)["status"] == "racing", "a car with data shows its own distance"
    t = _drive(race, [a], [40], t, rd.NO_DATA_S + 1)
    rb, ra = _row(race, "B", t), _row(race, "A", t)
    assert rb["status"] == "est" and abs(rb["distance_m"] - (ra["distance_m"] - 80)) < 4, (ra, rb)
    assert rb["gap_s"] == 2.0, rb


def t_an_estimate_holds_under_a_red_flag_and_after_the_flag():
    race, (a, b, c, d), t = _quiet_c()
    t = _drive(race, [a, b, d], [40, 40, 40], t, 5)
    race.red_flag(t, "keep")
    held = _row(race, "C", t)["distance_m"]
    t = _drive(race, [a, b, d], [40, 40, 40], t, 10)
    rc = _row(race, "C", t)
    assert rc["distance_m"] == held and rc["status"] == "est", f"no movement under a red flag: {held} {rc}"
    assert abs(race._entries["C"].frozen - held) < 0.5, "the restart anchor is the estimate"
    try:
        race.estimate("C", "A", 1.0, t)
        raise AssertionError("no estimate is set under a red flag")
    except ValueError:
        pass  # expected
    race2, (a, b, c, d), t = _quiet_c()
    t = _drive(race2, [a, b, d], [40, 40, 40], t, 5)
    race2.finish(t)
    held = _row(race2, "C", t)["distance_m"]
    t = _drive(race2, [a, b, d], [40, 40, 40], t, 10)
    assert _row(race2, "C", t)["distance_m"] == held, "no movement after the flag"
    try:
        race2.estimate("C", "B", 1.0, t)
        raise AssertionError("no estimate is set after the flag")
    except ValueError:
        pass  # expected



def t_an_estimate_behind_follows_racing_time_across_a_keep_restart():
    race, (a, b, c, d), t = _quiet_c(seconds=3.0)
    t = _drive(race, [a, b, d], [40, 40, 40], t, 5)
    race.red_flag(t, "keep")
    held = _row(race, "C", t)["distance_m"]
    for car in (a, b, d):
        _new_lobby(car, L - 15)
    t = _drive(race, [a, b, d], [None] * 3, t, 30)
    b.box = False
    t = _drive(race, [a, b, d], [None, 40, None], t, 0.5)
    race.release(t)
    t = _drive(race, [a, b, d], [None, 40, None], t, 0.5)
    assert abs(_row(race, "C", t)["distance_m"] - (held + 40)) < 5, \
        f"C shows where B was three racing seconds ago, not inside the stoppage: {_row(race, 'C', t)} {held}"


def t_an_estimate_ends_when_the_car_returns_on_another_connection():
    race, (a, b, c, d), t = _quiet_c()
    t = _drive(race, [a, b, d], [40, 40, 40], t, 5)
    race.red_flag(t, "keep")
    c2 = Car(race, "C", "c2", L - 15)
    for car in (a, b, d, c2):
        _new_lobby(car, L - 15)
    t = _drive(race, [a, b, d, c2], [None] * 4, t, 3)
    for car in (a, b, d, c2):
        car.box = False
    t = _drive(race, [a, b, d, c2], [40] * 4, t, 5)
    race.release(t)
    rc = _row(race, "C", t)
    assert rc["status"] == "racing" and rc["estimate"] is None, rc
    assert race.take_ended_estimates() == ["C"], "the returning car ends the estimate"
    t = _drive(race, [a, b, d], [40] * 3, t, rd.NO_DATA_S + 1)
    assert _row(race, "C", t)["status"] == "no_data", "an ended estimate does not come back"


def t_an_estimated_car_keeps_its_place_when_the_winner_takes_the_flag():
    race, (a, b, c, d), t = _quiet_c(finish_mode="laps", lap_count=2)
    while _row(race, "A", t)["finished"] is False:
        t = _drive(race, [a, b, d], [40, 40, 40], t, 0.1)
    order = [r["entry"] for r in race.standings(t)["entries"]]
    assert order == ["A", "B", "C", "D"], f"C stays between B and D after A's flag: {order}"


def t_an_estimate_that_reaches_before_the_history_holds_at_its_start():
    race, _cars, t = _quiet_c()
    race.clear_estimate("C")
    race.estimate("C", "B", 3000.0, t)
    rc = _row(race, "C", t)
    assert rc["status"] == "est" and abs(rc["distance_m"] - race._entries["B"].ds[0]) < 0.01, rc


def t_an_estimate_needs_two_cars_and_a_reference_with_data():
    race, (a, b, c, d), t = _quiet_c()
    for args in (("C", "C", 1.0), ("C", "Nobody", 1.0), ("Nobody", "B", 1.0)):
        try:
            race.estimate(*args, t)
        except ValueError:
            continue
        raise AssertionError(f"{args} must be refused")
    t = _drive(race, [a, b], [40, 40], t, rd.NO_DATA_S + 1)
    try:
        race.estimate("C", "D", 1.0, t)
        raise AssertionError("a quiet reference must be refused")
    except ValueError:
        pass  # expected
    assert race.clear_estimate("C") is True and race.clear_estimate("C") is False
    assert _row(race, "C", t)["status"] == "no_data", "a cleared estimate leaves the car without data"


def t_an_estimate_reads_the_coarse_history_too():
    race = rd.Race(TRACE, formation=False)
    a, b = Car(race, "A", "a", L - 10), Car(race, "B", "b", L - 50)
    t = _drive(race, [a, b], [40, 40], 0.0, rd.HISTORY_S + 200)
    t = _drive(race, [a], [40], t, rd.NO_DATA_S + 1)
    race.estimate("B", "A", rd.HISTORY_S + 100, t)
    ent = race._entries["A"]
    assert ent.ts[0] > t - rd.HISTORY_S - 100, "the lookup reaches back past the fine history"
    rb, ra = _row(race, "B", t), _row(race, "A", t)
    assert abs(rb["distance_m"] - (ra["distance_m"] - 40 * (rd.HISTORY_S + 100))) < 45, (ra, rb)


def _order(race, t):
    return [r["entry"] for r in race.standings(t)["entries"]]


def _raises(fn, why):
    try:
        fn()
    except ValueError:
        return
    raise AssertionError(why)


def _field(roster=("A", "B", "X"), **kw):
    """A and B at 40 m/s, B one second behind; X is in the roster and never sends."""
    race = rd.Race(TRACE, formation=False, **kw)
    race.set_roster(list(roster))
    a, b = Car(race, "A", "a", L - 10), Car(race, "B", "b", L - 50)
    t = _drive(race, [a, b], [40, 40], 0.0, 12)
    return race, (a, b), t


def t_a_roster_entry_without_a_sender_stands_at_the_end_as_no_sender():
    race, _cars, t = _field(roster=("X", "A", "B", "Y"))
    assert _order(race, t) == ["A", "B", "X", "Y"], _order(race, t)
    rx = _row(race, "X", t)
    assert rx["status"] == "no_sender" and rx["gap_s"] is None and rx["distance_m"] is None, rx
    assert rx["driver"] == "" and rx["quiet_s"] is None and rx["x"] is None and rx["laps"] == 0, rx
    assert not rx["finished"] and rx["estimate"] is None, rx


def t_a_sender_that_connects_later_takes_over_the_roster_entry():
    race, (a, b), t = _field()
    x = Car(race, "X", "x", L - 100)
    x.along = b.along - 100
    t = _drive(race, [a, b, x], [40, 40, 40], t, L / 40 + 2)       # its own crossing gives its line
    rx = _row(race, "X", t)
    assert rx["status"] == "racing" and rx["distance_m"] is not None and rx["driver"] == "Driver", rx


def t_an_estimate_places_a_car_that_never_sent():
    race, (a, b), t = _field()
    race.estimate("X", "B", 1.0, t)
    t = _drive(race, [a, b], [40, 40], t, 10)
    rb, rx = _row(race, "B", t), _row(race, "X", t)
    assert rx["status"] == "est" and abs(rx["distance_m"] - (rb["distance_m"] - 40)) < 3, (rb, rx)
    assert abs(rx["gap_s"] - (rb["gap_s"] + 1.0)) < 0.02 and abs(rx["interval_s"] - 1.0) < 0.02, rx
    assert rx["estimate"] == {"ref": "B", "seconds": 1.0, "ahead": False} and rx["quiet_s"] is None, rx
    assert _order(race, t) == ["A", "B", "X"], _order(race, t)


def t_an_estimated_never_seen_car_can_sit_between_two_cars_with_data():
    race = rd.Race(TRACE, formation=False)
    race.set_roster(["A", "B", "C", "X"])
    a, b, c = Car(race, "A", "a", L - 10), Car(race, "B", "b", L - 50), Car(race, "C", "c", L - 170)
    t = _drive(race, [a, b, c], [40, 40, 40], 0.0, 12)
    race.estimate("X", "B", 1.0, t)
    t = _drive(race, [a, b, c], [40, 40, 40], t, 5)
    assert _order(race, t) == ["A", "B", "X", "C"], _order(race, t)
    rx, rc = _row(race, "X", t), _row(race, "C", t)
    assert abs(rc["interval_s"] - (rc["gap_s"] - rx["gap_s"])) < 0.02, (rx, rc)


def t_an_estimate_needs_a_roster_entry_and_leaves_no_row_when_refused():
    race, _cars, t = _field()
    _raises(lambda: race.estimate("Nobody", "B", 1.0, t), "an entry outside the roster is refused")
    race.red_flag(t, "keep")
    _raises(lambda: race.estimate("X", "B", 1.0, t), "no estimate under a red flag")
    assert "X" not in race._entries and _row(race, "X", t)["status"] == "no_sender", \
        "a refused estimate creates no car"


def t_a_cleared_estimate_leaves_a_never_seen_car_without_a_sender():
    race, (a, b), t = _field(roster=("A", "B", "X", "Y"))
    race.estimate("Y", "B", 1.0, t)
    assert race.clear_estimate("Y")
    t = _drive(race, [a, b], [40, 40], t, 2)
    assert _row(race, "Y", t)["status"] == "no_sender" and _order(race, t) == ["A", "B", "X", "Y"], \
        f"cars without a sender keep the roster order: {_order(race, t)}"
    race.finish(t)
    assert not _row(race, "Y", t)["finished"], "a car without a sender takes no flag"


def t_real_data_ends_the_estimate_of_a_never_seen_car():
    race, (a, b), t = _field()
    race.estimate("X", "B", 1.0, t)
    t = _drive(race, [a, b], [40, 40], t, 3)
    x = Car(race, "X", "x", L - 300)
    x.along = b.along - 300
    t = _drive(race, [a, b, x], [40, 40, 40], t, 3)
    rx = _row(race, "X", t)
    assert rx["status"] == "waiting" and rx["estimate"] is None and rx["quiet_s"] == 0.0, \
        f"its own sender ends the estimate: {rx}"
    assert race.take_ended_estimates() == ["X"]


def t_an_estimate_at_the_start_holds_at_the_references_first_distance():
    race = rd.Race(TRACE, formation=False)
    race.set_roster(["A", "B", "X"])
    a, b = Car(race, "A", "a", L - 10), Car(race, "B", "b", L - 50)
    t = _drive(race, [a, b], [40, 40], 0.0, 3)
    first = race._entries["B"].ds[0]
    race.estimate("X", "B", 5.0, t)
    rb, rx = _row(race, "B", t), _row(race, "X", t)
    assert rx["status"] == "est" and abs(rx["distance_m"] - first) < 0.01, (first, rx)
    assert _order(race, t) == ["A", "B", "X"], _order(race, t)
    lag = t - race._entries["B"].ts[0]
    assert abs(rx["gap_s"] - (rb["gap_s"] + lag)) < 0.05, f"the hold's gap is the time since then: {rx} {lag}"
    t = _drive(race, [a, b], [40, 40], t, 1)
    assert abs(_row(race, "X", t)["distance_m"] - first) < 0.01, "it holds until the history covers S"
    t = _drive(race, [a, b], [40, 40], t, 10)
    rb, rx = _row(race, "B", t), _row(race, "X", t)
    assert abs(rx["distance_m"] - (rb["distance_m"] - 200)) < 5, f"then it follows 5 s behind: {rb} {rx}"
    assert abs(rx["gap_s"] - (rb["gap_s"] + 5.0)) < 0.02, rx


def _retire_field():
    """A, B, C and D at 40 m/s, a second apart."""
    race = rd.Race(TRACE, formation=False)
    race.set_roster(["A", "B", "C", "D", "X"])
    cars = [Car(race, e, e.lower(), L - g) for e, g in (("A", 10), ("B", 50), ("C", 90), ("D", 130))]
    t = _drive(race, cars, [40] * 4, 0.0, 12)
    return race, cars, t


def t_a_retired_car_sits_below_every_running_car_and_stops_counting():
    race, (a, b, c, d), t = _retire_field()
    race.retire("A", True, t)
    held = _row(race, "A", t)["distance_m"]
    t = _drive(race, [a, b, c, d], [40] * 4, t, 5)
    ra = _row(race, "A", t)
    assert _order(race, t) == ["B", "C", "D", "X", "A"], _order(race, t)
    assert ra["status"] == "retired" and ra["distance_m"] == held, ra
    assert ra["gap_s"] is None and ra["interval_s"] is None and ra["x"] is None and ra["fuel_l"] is None, ra
    assert _row(race, "B", t)["gap_s"] == 0, "the next car leads"


def t_retired_cars_rank_among_themselves_by_distance():
    race, (a, b, c, d), t = _retire_field()
    race.set_roster(["A", "B", "C", "D", "AA"])
    race.retire("D", True, t)
    t = _drive(race, [a, b, c], [40] * 3, t, 5)
    race.retire("B", True, t)
    race.retire("AA", True, t)
    assert _order(race, t) == ["A", "C", "B", "D", "AA"], _order(race, t)
    assert _row(race, "AA", t)["status"] == "retired"


def t_undoing_a_retirement_counts_on_from_the_last_distance():
    race, (a, b, c, d), t = _retire_field()
    race.retire("B", True, t)
    held = _row(race, "B", t)["distance_m"]
    t = _drive(race, [a, b, c, d], [40] * 4, t, 5)
    race.retire("B", False, t)
    t = _drive(race, [a, b, c, d], [40] * 4, t, 2)
    rb = _row(race, "B", t)
    assert rb["status"] == "racing" and abs(rb["distance_m"] - (held + 80)) < 5, (held, rb)


def t_undoing_a_retirement_under_a_red_flag_waits_for_the_restart_like_every_car():
    for multi_lobby in (False, True):
        race = rd.Race(TRACE, formation=False, multi_lobby=multi_lobby)
        a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 25)
        t = _drive(race, [a, b], [50, 50], 0.0, 90)
        race.retire("B", True, t)
        t = _drive(race, [a, b], [50, 50], t, 2)
        db = _row(race, "B", t)["distance_m"]
        race.red_flag(t, "keep")
        race.retire("B", False, t)
        t = _drive(race, [a, b], [50, 50], t, 2)
        assert _row(race, "B", t)["distance_m"] == db, "the undone car stays frozen while the red flag holds"
        for car in (a, b):
            _new_lobby(car, L - 15)
        t = _drive(race, [a, b], [None, None], t, 3)
        assert _row(race, "B", t)["distance_m"] == db, \
            f"the restart lobby keeps the undone car's laps: {_row(race, 'B', t)}"
        a.box = b.box = False
        t = _drive(race, [a, b], [40, 40], t, 5)
        assert _row(race, "B", t)["distance_m"] > db, f"B resumes from its anchor: {_row(race, 'B', t)}"


def t_an_undone_retirement_under_a_mis_pressed_red_flag_counts_on_from_the_undo():
    for multi_lobby in (False, True):
        race = rd.Race(TRACE, formation=False, multi_lobby=multi_lobby)
        a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 25)
        t = _drive(race, [a, b], [50, 50], 0.0, 90)
        race.retire("B", True, t)
        t = _drive(race, [a, b], [50, 50], t, 2)
        db = _row(race, "B", t)["distance_m"]
        race.red_flag(t, "keep")
        race.retire("B", False, t)
        t = _drive(race, [a, b], [50, 50], t, 2)
        race.release(t)
        t = _drive(race, [a, b], [50, 50], t, 4)
        rb = _row(race, "B", t)
        assert abs(rb["distance_m"] - (db + 300)) < 30, \
            f"B counts the metres from the undo on, none driven while retired: {db} {rb['distance_m']}"


def t_an_undone_retirement_of_a_finished_car_under_a_red_flag_stays_finished():
    race = rd.Race(TRACE, formation=False, finish_mode="time")
    a, b, c = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 25), Car(race, "C", "c", L - 300)
    t = _drive(race, [a, b, c], [50, 50, 50], 0.0, 30)
    race.final_lap(t)
    while not _row(race, "B", t)["finished"]:
        t = _drive(race, [a, b, c], [50, 50, 50], t, 0.2)
    race.retire("B", True, t)
    race.red_flag(t, "keep")
    race.retire("B", False, t)
    rb = _row(race, "B", t)
    assert rb["finished"] and not rb["waiting"] and rb["status"] != "red_flag", \
        f"a finished car does not wait for the restart: {rb}"


def _undo_in_the_restart_lobby(new_console):
    """B retires, the red flag falls, B's console reaches the restart lobby while
    retired (the same console, or `new_console` connecting there), then Undo DNF."""
    race = rd.Race(TRACE, formation=False)
    a, b, c = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 25), Car(race, "C", "c", L - 35)
    t = _drive(race, [a, b, c], [50, 50, 50], 0.0, 90)
    race.retire("B", True, t)
    t = _drive(race, [a, b, c], [50, 50, 50], t, 2)
    db = _row(race, "B", t)["distance_m"]
    race.red_flag(t, "keep")
    if new_console:
        b = Car(race, "B", "b2", 0.0)
    for car in (a, b, c):
        _new_lobby(car, L - 15)
    t = _drive(race, [a, b, c], [None] * 3, t, 3)
    race.retire("B", False, t)
    a.box = b.box = c.box = False
    t = _drive(race, [a, b, c], [40] * 3, t, 5)
    ra, rb = _row(race, "A", t), _row(race, "B", t)
    assert rb["status"] == "racing" and abs(rb["distance_m"] - ra["distance_m"]) < 15, \
        f"B restarts from the keep anchor beside A, not from {db}: {ra['distance_m']} {rb}"
    race.release(t)
    t = _drive(race, [a, b, c], [40] * 3, t, 5)
    gap = _row(race, "A", t)["distance_m"] - _row(race, "B", t)["distance_m"]
    assert abs(gap) < 15, f"B keeps racing beside A after the release: {gap}"


def t_an_undone_retirement_counts_from_a_restart_box_exit_made_while_retired():
    for multi_lobby in (False, True):
        race = rd.Race(TRACE, formation=False, multi_lobby=multi_lobby)
        cars = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 25), Car(race, "C", "c", L - 35)
        t = _drive(race, list(cars), [50] * 3, 0.0, 90)
        race.retire("B", True, t)
        t = _drive(race, list(cars), [50] * 3, t, 2)
        race.red_flag(t, "keep")
        for car in cars:
            _new_lobby(car, L - 15)
        t = _drive(race, list(cars), [None] * 3, t, 3)
        for car in cars:
            car.box = False
        t = _drive(race, list(cars), [40] * 3, t, 3)
        race.retire("B", False, t)
        ra, rb = _row(race, "A", t), _row(race, "B", t)
        assert rb["status"] == "racing" and abs(rb["distance_m"] - ra["distance_m"]) < 15, \
            f"B counts from its box exit beside A: {ra['distance_m']} {rb}"
        race.release(t)
        t = _drive(race, list(cars), [40] * 3, t, 10)
        gap = _row(race, "A", t)["distance_m"] - _row(race, "B", t)["distance_m"]
        assert abs(gap) < 15, f"B keeps racing beside A after the release: {gap}"


def t_an_undone_retirement_restarts_from_a_restart_lobby_it_joined_while_retired():
    _undo_in_the_restart_lobby(new_console=False)


def t_an_undone_retirement_restarts_with_a_console_that_joined_the_restart_lobby_while_retired():
    _undo_in_the_restart_lobby(new_console=True)


def t_a_retired_car_keeps_its_distance_when_its_console_leaves_the_session():
    race, (a, b, c, d), t = _retire_field()
    race.retire("B", True, t)
    held = _row(race, "B", t)["distance_m"]
    _new_lobby(b, L - 15)
    t = _drive(race, [a, b, c, d], [40, None, 40, 40], t, 3)
    assert _row(race, "B", t)["distance_m"] == held, "a new session does not wipe a retired car"


def t_a_retirement_ends_its_estimate_and_it_is_no_reference():
    race, (a, b, c, d), t = _retire_field()
    t = _drive(race, [a, b, d], [40] * 3, t, rd.NO_DATA_S + 1)
    race.estimate("C", "B", 1.0, t)
    race.retire("C", True, t)
    assert _row(race, "C", t)["status"] == "retired" and _row(race, "C", t)["estimate"] is None
    race.retire("C", False, t)
    race.retire("B", True, t)
    _raises(lambda: race.estimate("C", "B", 1.0, t), "a retired car is no reference")
    _raises(lambda: race.estimate("B", "A", 1.0, t), "a retired car takes no estimate")


def t_an_estimate_ends_when_its_reference_retires():
    race, (a, b, c, d), t = _retire_field()
    t = _drive(race, [a, b, d], [40] * 3, t, rd.NO_DATA_S + 1)
    race.estimate("C", "B", 1.0, t)
    t = _drive(race, [a, b, d], [40] * 3, t, 1)
    assert race.retire("B", True, t) == ["C"], "the retirement names the estimate it ended"
    rc = _row(race, "C", t)
    assert rc["status"] == "no_data" and rc["estimate"] is None, f"Race Control re-attaches it: {rc}"
    assert race.retire("B", False, t) == []
    assert _row(race, "C", t)["estimate"] is None, "undoing the DNF does not bring the estimate back"


def t_a_retired_leader_does_not_hold_back_the_flag():
    race = rd.Race(TRACE, formation=False, finish_mode="laps", lap_count=2)
    race.set_roster(["A", "B", "X"])
    a, b = Car(race, "A", "a", L - 10), Car(race, "B", "b", L - 50)
    t = _drive(race, [a, b], [40, 40], 0.0, 12)
    race.retire("A", True, t)
    t = _drive(race, [a, b], [40, 40], t, 2 * L / 40 + 2)
    snap = race.standings(t)
    assert snap["state"] == "finished", f"B wins once the leader retired: {snap['state']}"
    assert _row(race, "B", t)["finished"] and not _row(race, "A", t)["finished"]
    assert _row(race, "A", t)["status"] == "retired" and _order(race, t) == ["B", "X", "A"]


def t_a_finish_by_hand_leaves_a_retired_car_unclassified():
    race, (a, b, c, d), t = _retire_field()
    race.retire("D", True, t)
    race.finish(t)
    rd_ = _row(race, "D", t)
    assert rd_["status"] == "retired" and not rd_["finished"], rd_
    held = _row(race, "D", t)["distance_m"]
    race.retire("D", False, t)
    t = _drive(race, [a, b, c, d], [40] * 4, t, 3)
    rd_ = _row(race, "D", t)
    assert rd_["finished"] and rd_["distance_m"] == held, f"undone after the flag it is classified where it stopped: {rd_}"
    assert _order(race, t)[3] == "D", _order(race, t)
    race.retire("A", True, t)
    assert not _row(race, "A", t)["finished"], "a DNF after the flag reads DNF, not finished"


def t_a_retired_leader_does_not_hold_back_the_final_lap():
    race = rd.Race(TRACE, formation=False, finish_mode="time")
    a, b = Car(race, "A", "a", L - 10), Car(race, "B", "b", L - 50)
    t = _drive(race, [a, b], [80, 40], 0.0, 60)
    race.retire("A", True, t)
    race.final_lap(t)
    t = _drive(race, [a, b], [80, 40], t, 25)                      # B crosses well short of A
    assert _row(race, "B", t)["finished"], "B takes the flag at its next crossing"


def t_race_control_overrides_the_name_of_one_driver_of_an_entry():
    race, _cars, t = _field()
    race.set_driver_name("X", "", "Kim Berg")
    race.set_driver_name("A", "Driver", "Ann")
    race.set_driver_name("B", "Somebody Else", "Zed")
    assert _row(race, "X", t)["driver"] == "Kim Berg", "a car without a sender shows the name"
    assert _row(race, "A", t)["driver"] == "Ann" and _row(race, "A", t)["driver_registered"] == "Driver", \
        "a sender's name is overridden, its registered name kept for the desks"
    assert _row(race, "B", t)["driver"] == "Driver", "another driver of the entry keeps their own name"
    assert race.display_name("A", "Driver") == "Ann" and race.display_name("A", "Other") == "Other"
    race.set_driver_name("X", "", "")
    assert _row(race, "X", t)["driver"] == ""


def t_the_change_window_closes_without_waiting_for_retired_or_senderless_cars():
    race = rd.Race(TRACE, formation=False, multi_lobby=True)
    race.set_roster(["A", "B", "C", "X"])
    a, b, c = (Car(race, e, e.lower(), L - g) for e, g in (("A", 10), ("B", 60), ("C", 110)))
    t = _drive(race, [a, b, c], [50] * 3, 0.0, 60)
    race.retire("C", True, t)
    race.estimate("X", "B", 1.0, t)
    race.open_change_window(t)
    for car in (a, b):
        car.box = True
    t = _drive(race, [a, b, c], [None, None, 50], t, 3)
    for car in (a, b):
        car.along = -5.0
    t = _drive(race, [a, b, c], [None, None, 50], t, 3)
    for car in (a, b):
        car.box = False
    t = _drive(race, [a, b, c], [40, 40, 50], t, 5)
    assert race.standings(t)["change_window"] is False, "every team that can change has changed"


def t_a_retired_car_still_sending_does_not_hold_back_the_finish():
    race = rd.Race(TRACE, formation=False, finish_mode="laps", lap_count=2)
    a, b, c = (Car(race, e, e.lower(), L - g) for e, g in (("A", 10), ("B", 50), ("C", 90)))
    t = _drive(race, [a, b, c], [40] * 3, 0.0, 12)
    race.retire("C", True, t)
    t = _drive(race, [a, b, c], [40] * 3, t, 2 * L / 40 + 3)
    assert race.standings(t)["state"] == "finished", race.standings(t)["state"]


def t_a_retired_car_does_not_level_a_neutralised_restart():
    race = rd.Race(TRACE, formation=False)
    a, b = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 115)
    t = _drive(race, [a, b], [50, 50], 0.0, 30)
    db = _row(race, "B", t)["distance_m"]
    race.retire("A", True, t)
    race.red_flag(t, "neutralise")
    for car in (a, b):
        _new_lobby(car, L - 15)
    t = _drive(race, [a, b], [None, None], t, 3.1)
    b.box = False
    t = _drive(race, [a, b], [None, 40], t, 1)
    assert abs(_row(race, "B", t)["distance_m"] - (db + 40)) < 10, \
        f"B restarts from its own distance, not the retired car's: {db} {_row(race, 'B', t)}"


def t_a_rejoin_before_the_leader_stops_starts_at_the_leaders_distance_then():
    race = rd.Race(TRACE, formation=False, multi_lobby=True)
    out = Car(race, "R", "r1", L - 12, "Rex")
    lead, gone = Car(race, "A", "a1", L - 15, "Max"), Car(race, "B", "b1", L - 115, "Ann")
    t = _drive(race, [out, lead, gone], [60, 50, 50], 0.0, 30)
    t = _drive(race, [out, lead], [60, 50], t, 30)
    race.retire("R", True, t)
    race.rejoin("B", True)
    race.open_change_window(t)
    back = Car(race, "B", "b2", L - 15, "Ben")
    t = _drive(race, [lead, back], [50, 40], t, 2)                     # lap 1 of the next lobby
    back.box = True
    t = _drive(race, [lead, back], [50, None], t, 3)
    back.box = False
    t = _drive(race, [lead, back], [50, 40], t, 1)
    ra, rb = _row(race, "A", t), _row(race, "B", t)
    assert abs(rb["distance_m"] - (ra["distance_m"] - 10)) < 15, \
        f"B joins at A's distance when it leaves the box, not R's: {ra} {rb}"


def t_corrections_reach_a_car_without_a_sender():
    race, _cars, t = _field()
    race.correct("X", 0.0, pit_stops=1)
    race.rejoin("X", True)
    rx = _row(race, "X", t)
    assert rx["pit_stops"] == 1 and rx["rejoin"] is True, f"a pit stop correction shows on a car without a sender: {rx}"


def t_a_parked_entry_can_be_estimated():
    race = rd.Race(TRACE, formation=False)
    race.set_roster(["A", "B", "C"])
    a, b, c = Car(race, "A", "a", L - 10), Car(race, "B", "b", L - 50), Car(race, "C", "c", L - 90)
    t = _drive(race, [a, b, c], [40] * 3, 0.0, 12, positions=lambda car: "ABC".index(car.entry) + 1)
    b.entry = "A"                                          # the producer moves B's only car to A
    t = _drive(race, [a, b, c], [40] * 3, t, rd.NO_DATA_S + 1)
    race.estimate("B", "C", 1.0, t)
    rb = _row(race, "B", t)
    assert rb["status"] == "est" and rb["distance_m"] is not None, rb


def _mid_lap_start():
    """Three cars cross the line and give the medoid; A and B, 200 m apart in mid lap,
    then start from where they are, so A's history begins ahead of B."""
    race = rd.Race(TRACE, formation=False)
    race.set_roster(["A", "B", "Q1", "Q2", "Q3", "X"])
    qs = [Car(race, "Q%d" % i, "q%d" % i, L - 5 * i) for i in (1, 2, 3)]
    t = _drive(race, qs, [20] * 3, 0.0, 2)
    a, b = Car(race, "A", "a", 1.5 * L), Car(race, "B", "b", 1.5 * L - 200)
    t = _drive(race, qs + [a, b], [20, 20, 20, 40, 40], t, 1)
    return race, (a, b, *qs), t


def t_a_running_car_has_a_gap_before_the_car_ahead_has_history_there():
    race, cars, t = _mid_lap_start()
    rb = _row(race, "B", t)
    assert rb["status"] == "racing" and rb["gap_s"] is not None and abs(rb["gap_s"] - 5.0) < 0.5, \
        f"200 m at 40 m/s is about 5 s while A's history starts ahead of B: {rb}"
    assert rb["interval_s"] is not None and rb["gap_time_s"] is not None, rb


def t_a_start_hold_estimate_shows_its_reference_gap_plus_the_lag():
    race, cars, t = _mid_lap_start()
    race.estimate("X", "B", 30.0, t)
    rb, rx = _row(race, "B", t), _row(race, "X", t)
    lag = t - race._entries["B"].ts[0]
    assert rx["status"] == "est" and rx["gap_s"] is not None and abs(rx["gap_s"] - (rb["gap_s"] + lag)) < 0.05, \
        (rb, rx)


def t_class_positions_and_gaps_follow_the_class():
    race = rd.Race(TRACE, formation=False)
    race.set_classes({"A": "H", "B": "G", "C": "G"})
    a, b, c = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 60), Car(race, "C", "c", L - 120)
    t = _drive(race, [a, b, c], [60, 30, 30], 0.0, L / 30 * 1.2)      # A laps both
    rb, rc = _row(race, "B", t), _row(race, "C", t)
    assert rb["lapped"] == 1 and rb["class"] == "G", rb
    assert rb["class_pos"] == 1 and rb["class_gap_s"] == 0 and rb["class_lapped"] == 0, rb
    behind = (rb["distance_m"] - rc["distance_m"]) / 30
    assert rc["class_pos"] == 2 and rc["class_lapped"] == 0 and abs(rc["class_gap_s"] - behind) < 0.05, (behind, rc)
    assert abs(rc["class_interval_s"] - behind) < 0.05 and rb["class_interval_s"] is None, (rb, rc)
    ra = _row(race, "A", t)
    assert ra["class_pos"] == 1 and ra["class_gap_s"] == 0, ra


def t_a_class_count_shows_only_where_the_overall_count_shows():
    race = rd.Race(TRACE, formation=False, multi_lobby=True, finish_mode="laps")
    race.set_classes({"A": "H", "B": "G"})
    old_a, old_b = Car(race, "A", "a1", L - 15, "Max"), Car(race, "B", "b1", L - 60, "Ann")
    t = _drive(race, [old_a, old_b], [50, 40], 0.0, 3 * L / 50)
    race.open_change_window(t)
    race.final_lobby(9, t)
    t = _drive(race, [old_a, old_b], [50, 40], t, 2)
    assert race.standings(t)["class_counts"] == {"H": False, "G": False}, \
        f"before the final lobby's cars count, no class shows a count: {race.standings(t)['class_counts']}"
    old_a.box = old_b.box = True
    t = _drive(race, [old_a, old_b], [None, None], t, 3)
    new_a, new_b = Car(race, "A", "a2", L - 15, "Lea"), Car(race, "B", "b2", L - 60, "Bo")
    t = _drive(race, [new_a, new_b], [40, 40], t, 2)
    new_a.box = new_b.box = True
    t = _drive(race, [new_a, new_b], [None, None], t, 3)
    new_a.box = new_b.box = False
    t = _drive(race, [new_a, new_b], [50, 40], t, 20)
    st = race.standings(t)
    assert st["lap"]["count"] is not None and st["class_counts"] == {"H": True, "G": True}, \
        f"with the final lobby on the line every class shows its count: {st['lap']} {st['class_counts']}"
    single = rd.Race(TRACE, formation=False, finish_mode="laps")
    single.set_classes({"A": "H", "B": "G"})
    cars = [Car(single, e, e.lower(), L - g) for e, g in (("A", 10), ("B", 50))]
    t = _drive(single, cars, [40] * 2, 0.0, 12)
    assert single.standings(t)["class_counts"] == {"H": False, "G": False}, \
        "a single lobby without a lap count shows no class count either"
    single.lap_count = 20
    assert single.standings(t)["class_counts"] == {"H": True, "G": True}, "a single lobby with a count shows them"
    timed = rd.Race(TRACE, formation=False, finish_mode="time", lap_count=20)
    timed.set_classes({"A": "H", "B": "G"})
    cars = [Car(timed, e, e.lower(), L - g) for e, g in (("A", 10), ("B", 50))]
    t = _drive(timed, cars, [40] * 2, 0.0, 12)
    assert timed.standings(t)["class_counts"] == {"H": False, "G": False}, "a timed race shows no class count"
    plain = rd.Race(TRACE, formation=False)
    assert plain.standings(0.0)["class_counts"] == {}, "outside class mode there is none"


def t_a_car_lapped_in_its_class_keeps_the_time_to_its_class_leader():
    race = rd.Race(TRACE, formation=False)
    race.set_classes({"A": "H", "B": "G", "C": "G"})
    a, b, c = Car(race, "A", "a", L - 15), Car(race, "B", "b", L - 60), Car(race, "C", "c", L - 120)
    t = _drive(race, [a, b, c], [60, 40, 20], 0.0, L / 20 * 1.2)      # B laps C, A laps both
    rb, rc = _row(race, "B", t), _row(race, "C", t)
    assert rc["class_lapped"] == 1 and rc["class_gap_s"] is None, rc
    behind = (rb["distance_m"] - rc["distance_m"]) / 40
    assert rc["class_gap_time_s"] is not None and abs(rc["class_gap_time_s"] - behind) < 0.2, \
        f"a car lapped in its class keeps the time its class leader took to its distance: {behind} {rc}"
    assert rb["class_gap_time_s"] == 0 and _row(race, "A", t)["class_gap_time_s"] == 0, (rb, _row(race, "A", t))
    assert rc["class_interval_s"] is None and rc["class_interval_time_s"] is not None \
        and abs(rc["class_interval_time_s"] - behind) < 0.2, \
        f"a car a lap behind the class car ahead keeps the exact interval: {behind} {rc}"
    assert rb["class_interval_time_s"] is None, f"a class leader has no interval: {rb}"


def _class_flag_race(class_laps):
    """H's A takes the flag on lap 3 of 3; G's B (40 m/s) and C (35 m/s) run on."""
    race = rd.Race(TRACE, formation=False, finish_mode="laps", lap_count=3)
    race.set_classes({"A": "H", "B": "G", "C": "G"})
    race.set_class_laps(class_laps)
    cars = [Car(race, e, e.lower(), L - g) for e, g in (("A", 10), ("B", 30), ("C", 50))]
    return race, cars


def t_a_class_lap_stops_at_its_class_count_once_the_flag_is_out():
    race, cars = _class_flag_race({"G": 1, "H": 1})
    t = _drive(race, cars, [60, 40, 35], 0.0, 3 * L / 60 - 1)
    st = race.standings(t)
    assert st["state"] == "racing" and st["class_lap"]["G"] == 2, \
        f"before the flag a class lap may pass its count: {st['class_lap']}"
    t = _drive(race, cars, [60, 40, 35], t, 2)
    st = race.standings(t)
    assert st["state"] == "finishing" and st["class_lap"]["G"] == 1, \
        f"after the flag a class lap never passes its class count: {st['state']} {st['class_lap']}"
    assert st["class_lap"]["H"] == 3, f"the first class follows the overall count, not a saved one: {st['class_lap']}"
    plain, plain_cars = _class_flag_race({})
    t = _drive(plain, plain_cars, [60, 40, 35], 0.0, 3 * L / 60 + 1)
    other = plain.standings(t)
    assert other["class_lap"]["G"] == 2 == _row(plain, "B", t)["laps"] and _row(plain, "B", t)["finished"], \
        f"without a class count the finished class leader reads its own race laps: {other['class_lap']}"
    assert [r["finished"] for r in st["entries"]] == [r["finished"] for r in other["entries"]], \
        "a class count never finishes a car"


def t_a_class_count_reads_race_laps_like_the_overall_one():
    race = rd.Race(TRACE, formation=True, finish_mode="laps", lap_count=4)
    race.set_classes({"A": "H", "B": "G"})
    race.set_class_laps({"G": 3})
    cars = [Car(race, e, e.lower(), L - g) for e, g in (("A", 10), ("B", 30))]
    t = _drive(race, cars, [60, 40], 0.0, 4 * L / 60 + 1)
    st = race.standings(t)
    assert st["state"] == "finishing" and st["class_count"] == {"H": 3, "G": 2}, \
        f"GT7's counts less the formation lap: {st['class_count']}"
    assert st["class_lap"] == {"H": 3, "G": 2}, f"each class lap in race laps, capped: {st['class_lap']}"


def t_a_lap_count_of_one_never_reads_zero():
    race = rd.Race(TRACE, formation=True, finish_mode="laps", lap_count=1)
    race.set_classes({"A": "H", "B": "G"})
    race.set_class_laps({"G": 1})
    cars = [Car(race, e, e.lower(), L - g) for e, g in (("A", 10), ("B", 30))]
    t = _drive(race, cars, [60, 40], 0.0, L / 60 + 1)
    st = race.standings(t)
    assert st["lap"]["count"] == 1 and st["class_count"]["G"] == 1, \
        f"a race of one lap reads 1, not 0 / 0: {st['lap']} {st['class_count']}"


def t_a_finisher_with_fewer_laps_reads_lapped_overall_and_in_its_class():
    race = rd.Race(TRACE, formation=False, finish_mode="laps", lap_count=6)
    pack = ["G%d" % i for i in range(1, 9)]
    race.set_classes({"X": "H", **{e: "G" for e in pack}})
    race.set_class_laps({"G": 5})
    cars = [Car(race, e, e.lower(), L - 10 - 7 * i) for i, e in enumerate(["X"] + pack)]
    t = _drive(race, cars, [60, 50.5, 50.4, 50.3, 50.2, 50.1, 49.9, 49.8, 49.7], 0.0, 6 * L / 49 + 20)
    st = race.standings(t)
    rows = st["entries"]
    assert st["state"] == "finished" and st["class_lap"]["G"] == 5, (st["state"], st["class_lap"])
    lead_g = next(r for r in rows if r["class"] == "G" and r["class_pos"] == 1)
    short = [r for r in rows if r["class"] == "G" and r["laps"] < lead_g["laps"]]
    assert short and any(r["distance_m"] % L > lead_g["distance_m"] % L for r in short), \
        "a lapped finisher crossed its line further past it than the class leader did"
    for r in rows:
        down = rows[0]["laps"] - r["laps"]
        assert r["lapped"] == down and (r["gap_s"] is None) == bool(down), \
            f"{r['entry']} finished {down} lap(s) down overall: {r}"
        down = lead_g["laps"] - r["laps"] if r["class"] == "G" else 0
        assert r["class_lapped"] == down and (r["class_gap_s"] is None) == bool(down), \
            f"{r['entry']} finished {down} lap(s) down in its class, the class count notwithstanding: {r}"


def t_an_estimated_car_finished_a_lap_down_reads_lapped_overall_and_in_its_class():
    race = rd.Race(TRACE, formation=False, finish_mode="laps", lap_count=2)
    race.set_classes({"A": "H", "B": "G", "C": "G", "D": "G"})
    a, b, c, d = (Car(race, e, e.lower(), L - g) for e, g in (("A", 10), ("B", 50), ("C", 90), ("D", 170)))
    t = _drive(race, [a, b, c, d], [45, 40, 40, 40], 0.0, 40)
    t = _drive(race, [a, b, d], [45, 40, 40], t, rd.NO_DATA_S + 1)
    race.estimate("C", "B", 20.0, t)
    for _ in range(4):                                     # the estimate moves on each standings read
        t = _drive(race, [a, b, d], [45, 40, 40], t, L / 80)
        race.standings(t)
    assert all(_row(race, e, t)["finished"] for e in "ABD"), race.standings(t)
    race.finish(t)
    rb, rc = _row(race, "B", t), _row(race, "C", t)
    assert rc["status"] == "est" and rc["laps"] == rb["laps"] - 1 \
        and rc["distance_m"] % L > rb["distance_m"] % L, (rb, rc)
    assert rc["lapped"] == 1 and rc["gap_s"] is None, f"C finished a lap down overall: {rc}"
    assert rc["class_lapped"] == 1 and rc["class_gap_s"] is None, f"C finished a lap down in its class: {rc}"


def t_a_hidden_class_count_never_caps_the_class_lap():
    race = rd.Race(TRACE, formation=False, finish_mode="time")
    race.set_classes({"A": "H", "B": "G", "C": "G"})
    race.set_class_laps({"G": 1})
    cars = [Car(race, e, e.lower(), L - g) for e, g in (("A", 10), ("B", 30), ("C", 50))]
    t = _drive(race, cars, [60, 40, 35], 0.0, 3 * L / 60 - 1)
    race.final_lap(t)
    t = _drive(race, cars, [60, 40, 35], t, 2)
    st = race.standings(t)
    assert st["state"] == "finishing" and st["class_counts"]["G"] is False, (st["state"], st["class_counts"])
    assert st["class_lap"]["G"] == 2 == _row(race, "B", t)["laps"], \
        f"a class count that does not show caps nothing below the class leader's laps: {st['class_lap']}"


def t_an_entry_without_a_class_forms_its_own_group():
    race = rd.Race(TRACE, formation=False)
    race.set_classes({"A": "H", "B": "G"})
    cars = [Car(race, e, e.lower(), L - g) for e, g in (("A", 10), ("B", 50), ("C", 90))]
    t = _drive(race, cars, [40] * 3, 0.0, 12)
    st = race.standings(t)
    assert st["classes"] == ["H", "G", ""], st["classes"]
    rc = _row(race, "C", t)
    assert rc["class"] == "" and rc["class_pos"] == 1 and rc["class_gap_s"] == 0, rc


def t_the_unclassified_group_stays_last_when_it_leads():
    race = rd.Race(TRACE, formation=False)
    race.set_classes({"B": "G", "C": "H"})
    cars = [Car(race, e, e.lower(), L - g) for e, g in (("A", 10), ("B", 50), ("C", 90))]
    t = _drive(race, cars, [40] * 3, 0.0, 12)
    assert race.standings(t)["classes"] == ["G", "H", ""], race.standings(t)["classes"]


def t_one_named_class_and_blank_cells_is_no_class_mode():
    race = rd.Race(TRACE, formation=False)
    race.set_classes({"A": "GT3", "B": ""})
    race.set_roster(["A", "B", "X"])
    a, b = Car(race, "A", "a", L - 10), Car(race, "B", "b", L - 50)
    t = _drive(race, [a, b], [40, 40], 0.0, 12)
    st = race.standings(t)
    assert st["classes"] == [], f"blank Class cells never make a second class: {st['classes']}"
    assert all(r["class"] is None and r["class_pos"] is None for r in st["entries"]), st["entries"]


def t_one_class_leaves_the_rows_unchanged():
    for classes in ({"A": "GT3", "B": "GT3", "X": "GT3"}, {}, {"A": "", "B": ""}):
        race = rd.Race(TRACE, formation=False)
        race.set_classes(classes)
        race.set_roster(["A", "B", "X"])
        a, b = Car(race, "A", "a", L - 10), Car(race, "B", "b", L - 50)
        t = _drive(race, [a, b], [40, 40], 0.0, 12)
        st = race.standings(t)
        assert st["classes"] == [], (classes, st["classes"])
        for r in st["entries"]:
            assert r["class"] is None and r["class_pos"] is None and r["class_gap_s"] is None \
                and r["class_interval_s"] is None and r["class_lapped"] is None \
                and r["class_gap_time_s"] is None and r["class_interval_time_s"] is None, (classes, r)


def t_the_fastest_lap_is_kept_per_class():
    race = rd.Race(TRACE, formation=True)
    race.set_classes({"A": "H", "B": "G"})
    a, b = Car(race, "A", "a", L - 60, "Lena"), Car(race, "B", "b", L - 15, "Marco")
    _drive(race, [a, b], [50, 60], 0.0, 3 * L / 50 + 2)       # B leads and sets the faster laps first
    fa, fb = race.fastest_by_class.get("H"), race.fastest_by_class.get("G")
    assert fa and fa["entry"] == "A" and fa["driver"] == "Lena" and abs(fa["ms"] - L / 50 * 1000) < 150, fa
    assert fb and fb["entry"] == "B" and abs(fb["ms"] - L / 60 * 1000) < 150, fb
    assert race.fastest == fb, race.fastest


def t_a_rejected_lap_sets_no_class_fastest_lap():
    race = rd.Race(TRACE, formation=False)
    race.set_classes({"A": "H", "B": "G"})
    a = Car(race, "A", "a", L - 15)
    t = _drive(race, [a], [50], 0.0, L / 50 - 2)
    a.forge = 1000
    _drive(race, [a], [50], t, 5)
    assert race.fastest_by_class == {}, race.fastest_by_class


def t_retired_and_no_sender_cars_hold_a_class_place_but_no_class_gap():
    race, (a, b, c, d), t = _retire_field()
    race.set_classes({"A": "H", "C": "H", "B": "G", "D": "G", "X": "G"})
    race.retire("B", True, t)
    t = _drive(race, [a, b, c, d], [40] * 4, t, 3)
    rows = {r["entry"]: r for r in race.standings(t)["entries"]}
    assert [rows[e]["class_pos"] for e in "DXB"] == [1, 2, 3], rows
    assert rows["D"]["class_gap_s"] == 0 and rows["D"]["class_interval_s"] is None, \
        f"a retired car is no class reference: {rows['D']}"
    for e in "XB":
        assert rows[e]["class_gap_s"] is None and rows[e]["class_interval_s"] is None \
            and rows[e]["class_lapped"] is None and rows[e]["class_gap_time_s"] is None \
            and rows[e]["class_interval_time_s"] is None, rows[e]
    assert abs(rows["C"]["class_gap_s"] - 2.0) < 0.05, rows["C"]


def t_an_estimated_car_takes_its_class_gap_from_its_reference():
    race, (a, b), t = _field()
    race.set_classes({"A": "H", "B": "G", "X": "G"})
    race.estimate("X", "B", 1.0, t)
    t = _drive(race, [a, b], [40, 40], t, 5)
    rx = _row(race, "X", t)
    assert rx["status"] == "est" and rx["class_pos"] == 2 and rx["class_lapped"] == 0, rx
    assert abs(rx["class_gap_s"] - 1.0) < 0.02 and abs(rx["class_interval_s"] - 1.0) < 0.02, rx
    assert rx["class_gap_time_s"] is not None and abs(rx["class_gap_time_s"] - 1.0) < 0.02, \
        f"the exact class gap follows the reference too: {rx}"
    assert rx["class_interval_time_s"] is not None and abs(rx["class_interval_time_s"] - 1.0) < 0.02, \
        f"the exact class interval follows the reference too: {rx}"


def t_an_estimated_class_leader_behind_another_class_leads_its_class():
    race = rd.Race(TRACE, formation=False)
    race.set_roster(["A", "B", "C", "X"])
    race.set_classes({"A": "H", "B": "H", "C": "G", "X": "G"})
    a, b, c = Car(race, "A", "a", L - 10), Car(race, "B", "b", L - 50), Car(race, "C", "c", L - 170)
    t = _drive(race, [a, b, c], [40, 40, 40], 0.0, 12)
    race.estimate("X", "B", 1.0, t)
    t = _drive(race, [a, b, c], [40, 40, 40], t, 5)
    rx, rc = _row(race, "X", t), _row(race, "C", t)
    assert rx["class_pos"] == 1 and rx["class_gap_s"] == 0 and rx["class_lapped"] == 0 \
        and rx["class_interval_s"] is None, rx
    assert rc["class_pos"] == 2 and abs(rc["class_gap_s"] - 2.0) < 0.05 \
        and abs(rc["class_interval_s"] - 2.0) < 0.05, rc


def _quiet_a(seconds=1.0, **kw):
    """A, B, C and D at 40 m/s, A one second ahead of B; A's sender stops and Race
    Control puts it `seconds` ahead of B."""
    race = rd.Race(TRACE, formation=False, **kw)
    a, b, c, d = (Car(race, e, e.lower(), L - g) for e, g in (("A", 10), ("B", 50), ("C", 90), ("D", 170)))
    t = _drive(race, [a, b, c, d], [40, 40, 40, 40], 0.0, 5)
    t = _drive(race, [b, c, d], [40, 40, 40], t, rd.NO_DATA_S + 1)
    race.estimate("A", "B", seconds, t, ahead=True)
    return race, (a, b, c, d), t


def t_an_estimate_ahead_puts_a_quiet_leader_in_front_of_its_reference():
    race, (a, b, c, d), t = _quiet_a()
    t = _drive(race, [b, c, d], [40, 40, 40], t, 30)
    snap = race.standings(t)
    ra, rb, rc, rd_ = (_row(race, e, t) for e in "ABCD")
    assert ra["status"] == "est" and abs(ra["distance_m"] - (rb["distance_m"] + 40)) < 3, \
        f"A is where B will be in a second: {ra['distance_m']} vs {rb['distance_m']}"
    assert [r["entry"] for r in snap["entries"]] == ["A", "B", "C", "D"], snap["entries"]
    assert ra["gap_s"] == 0 and ra["gap_time_s"] == 0.0 and ra["interval_s"] is None, ra
    assert abs(rb["gap_s"] - 1.0) < 0.02 and abs(rb["gap_time_s"] - 1.0) < 0.02, rb
    assert abs(rb["interval_s"] - 1.0) < 0.02 and abs(rb["interval_time_s"] - 1.0) < 0.02, rb
    assert abs(rc["gap_s"] - 2.0) < 0.05 and abs(rc["gap_time_s"] - 2.0) < 0.05, \
        f"C's gap is its gap to B plus the seconds: {rc}"
    assert abs(rd_["gap_time_s"] - 4.0) < 0.05 and abs(rd_["interval_time_s"] - 2.0) < 0.05, rd_
    assert ra["estimate"] == {"ref": "B", "seconds": 1.0, "ahead": True}, ra["estimate"]
    assert snap["lap"]["leader"] == math.floor(ra["distance_m"] / L) + 1, snap["lap"]


def t_an_estimated_car_ahead_moves_on_the_map_ahead_of_its_reference():
    race, (a, b, c, d), t = _quiet_a()
    t = _drive(race, [b, c, d], [40, 40, 40], t, 30)
    ra = _row(race, "A", t)
    x, z = _pos(b.along + 40)
    assert math.hypot(ra["x"] - x, ra["z"] - z) < 5, f"A's dot runs a second ahead of B: {(ra['x'], ra['z'])}"


def t_an_estimate_ahead_adds_the_distance_the_reference_covered_in_the_seconds():
    race, (a, b, c, d), t = _quiet_a(seconds=2.0)
    t = _drive(race, [b, c, d], [60, 60, 60], t, 10)
    ra, rb = _row(race, "A", t), _row(race, "B", t)
    assert abs(ra["distance_m"] - (rb["distance_m"] + 120)) < 3, \
        f"two seconds of B's own recent pace: {ra['distance_m']} vs {rb['distance_m']}"


def t_an_estimate_ahead_before_the_history_covers_the_seconds_uses_what_it_has():
    race, _cars, t = _quiet_a()
    race.clear_estimate("A")
    race.estimate("A", "B", 3000.0, t, ahead=True)
    ent = race._entries["B"]
    first_t, first_d = ent.ts[0], ent.ds[0]
    ra, rb = _row(race, "A", t), _row(race, "B", t)
    assert abs(ra["distance_m"] - (2 * rb["distance_m"] - first_d)) < 0.1, (ra, rb, first_d)
    assert abs(rb["gap_time_s"] - (t - first_t)) < 0.02, f"B's gap is the time its history covers: {rb}"


def t_an_estimate_ahead_of_zero_seconds_wins_the_tie():
    race, (a, b, c, d), t = _quiet_a(seconds=0.0)
    t = _drive(race, [b, c, d], [40, 40, 40], t, 2)
    assert _order(race, t)[:2] == ["A", "B"], _order(race, t)


def t_an_estimate_ahead_can_place_a_car_in_the_middle_of_the_field():
    race, (a, b, c, d), t = _quiet_c()
    race.clear_estimate("C")
    race.estimate("C", "D", 1.0, t, ahead=True)
    t = _drive(race, [a, b, d], [40, 40, 40], t, 10)
    rc, rd_ = _row(race, "C", t), _row(race, "D", t)
    assert _order(race, t) == ["A", "B", "C", "D"], _order(race, t)
    assert abs(rc["gap_s"] - (rd_["gap_s"] - 1.0)) < 0.02, (rc, rd_)
    assert abs(rd_["interval_s"] - 1.0) < 0.02 and abs(rd_["interval_time_s"] - 1.0) < 0.02, rd_


def t_an_estimate_ahead_holds_under_a_red_flag():
    race, (a, b, c, d), t = _quiet_a()
    t = _drive(race, [b, c, d], [40, 40, 40], t, 5)
    race.red_flag(t, "keep")
    held = _row(race, "A", t)["distance_m"]
    t = _drive(race, [b, c, d], [40, 40, 40], t, 10)
    assert _row(race, "A", t)["distance_m"] == held, "no movement under a red flag"
    assert abs(race._entries["A"].frozen - held) < 0.5, "the restart anchor is the estimate"


def _until_finished(race, cars, entry, t, limit):
    end = t + limit
    while not _row(race, entry, t)["finished"]:
        assert t < end, f"{entry} never takes the flag: {race.standings(t)['entries']}"
        t = _drive(race, cars, [40] * len(cars), t, 0.1)
    return t


def t_an_estimated_leader_never_blocks_the_flag():
    race, (a, b, c, d), t = _quiet_a(finish_mode="laps", lap_count=2)
    t = _until_finished(race, [b, c, d], "B", t, 2 * L / 40)
    dist = _row(race, "B", t)["distance_m"]
    assert 2 * L - rd.SNAP_M <= dist < 2 * L + 10, f"B takes the flag at its crossing of the count: {dist}"
    t = _until_finished(race, [b, c, d], "D", t, 10)
    rows = race.standings(t)["entries"]
    assert [r["entry"] for r in rows] == ["A", "B", "C", "D"], f"A keeps its estimated place: {rows}"
    assert not rows[0]["finished"] and rows[0]["status"] == "est", "the estimate takes no flag by itself"


def t_an_estimated_leader_never_blocks_the_flag_in_the_final_lobby():
    race = rd.Race(TRACE, formation=False, multi_lobby=True, finish_mode="laps")
    race.final_lobby(2, 0.0)
    a, b, c = (Car(race, e, e.lower(), L - g) for e, g in (("A", 10), ("B", 50), ("C", 90)))
    t = _drive(race, [a, b, c], [40, 40, 40], 0.0, 5)
    t = _drive(race, [b, c], [40, 40], t, rd.NO_DATA_S + 1)
    race.estimate("A", "B", 1.0, t, ahead=True)
    assert race.standings(t)["lap"]["count"] == 2, "the header keeps the final lobby's count"
    t = _until_finished(race, [b, c], "B", t, 2 * L / 40)
    assert b.lap() == 3, f"B takes the flag past the lobby count: {b.lap()}"
    t = _until_finished(race, [b, c], "C", t, 10)
    assert _order(race, t) == ["A", "B", "C"], _order(race, t)


def t_an_estimate_ahead_keeps_its_place_when_a_car_between_takes_the_flag():
    race, (a, b, c, d), t = _quiet_c(finish_mode="laps", lap_count=2)
    race.clear_estimate("C")
    race.estimate("C", "D", 4.5, t, ahead=True)
    t = _drive(race, [a, b, d], [40, 40, 40], t, 1)
    assert _order(race, t) == ["C", "A", "B", "D"], _order(race, t)
    t = _until_finished(race, [a, b, d], "A", t, 2 * L / 40)
    assert _order(race, t) == ["C", "A", "B", "D"], f"C stays ahead of A after A's flag: {_order(race, t)}"


def t_an_estimate_ahead_places_a_never_seen_car_in_the_lead():
    race, (a, b), t = _field()
    race.estimate("X", "A", 1.0, t, ahead=True)
    t = _drive(race, [a, b], [40, 40], t, 5)
    snap = race.standings(t)
    rx, ra, rb = (_row(race, e, t) for e in "XAB")
    assert _order(race, t) == ["X", "A", "B"] and rx["status"] == "est", snap["entries"]
    assert abs(ra["gap_s"] - 1.0) < 0.02 and abs(rb["gap_s"] - 2.0) < 0.05, (ra, rb)
    assert snap["lap"]["leader"] is not None, f"the header reads the estimated leader's lap: {snap['lap']}"


def t_a_never_seen_estimated_leader_keeps_the_final_lobby_header():
    race = rd.Race(TRACE, formation=False, multi_lobby=True, finish_mode="laps")
    race.set_roster(["A", "B", "X"])
    race.final_lobby(3, 0.0)
    a, b = Car(race, "A", "a", L - 10), Car(race, "B", "b", L - 50)
    t = _drive(race, [a, b], [40, 40], 0.0, 12)
    race.estimate("X", "A", 1.0, t, ahead=True)
    snap = race.standings(t)
    assert _order(race, t)[0] == "X" and snap["lap"] == {"leader": _row(race, "A", t)["laps"] + 1, "count": 3}, \
        f"the header reads the race lap; a final lobby that started with the race counts race laps: {snap['lap']}"

def t_an_estimated_leader_past_the_count_never_lifts_the_lap_header_past_it():
    race, (a, b, c, d), t = _quiet_a(seconds=2.0, finish_mode="laps", lap_count=2)
    seen = []
    while not _row(race, "B", t)["finished"]:
        t = _drive(race, [b, c, d], [40, 40, 40], t, 0.1)
        snap = race.standings(t)
        if snap["state"] == "racing" and _row(race, "A", t)["distance_m"] >= 2 * L:
            seen.append(snap["lap"]["leader"])
    assert seen and set(seen) == {2}, f"the header stays on the count before the flag: {sorted(set(seen))}"


def t_an_estimated_class_leader_never_lifts_its_class_header_past_the_class_count():
    race, (a, b, c, d), t = _quiet_c(finish_mode="laps", lap_count=5)
    race.set_classes({"A": "H", "B": "H", "C": "G", "D": "G"})
    race.set_class_laps({"G": 1})
    race.clear_estimate("C")
    race.estimate("C", "D", 2.0, t, ahead=True)
    seen = set()
    while _row(race, "D", t)["distance_m"] < L:
        t = _drive(race, [a, b, d], [40, 40, 40], t, 0.5)
        if _row(race, "C", t)["distance_m"] >= L:
            seen.add(race.standings(t)["class_lap"]["G"])
    assert seen == {1}, f"the class header stays on its count before the flag: {seen}"


def t_an_estimated_leader_ahead_of_a_lapped_reference_still_sets_every_gap():
    race = rd.Race(TRACE, formation=False)
    a, b, c, d = (Car(race, e, e.lower(), L - g) for e, g in (("A", 10), ("B", 50), ("C", 90), ("D", 170)))
    t = _drive(race, [a, b, c, d], [40, 40, 40, 40], 0.0, 120)
    t = _drive(race, [b, c, d], [40, 40, 40], t, rd.NO_DATA_S + 1)
    race.correct("D", -L)                                   # D a lap down: its gap_s is empty
    race.estimate("A", "D", 82.5, t, ahead=True)
    t = _drive(race, [b, c, d], [40, 40, 40], t, 5)
    rb, rc, rd_ = (_row(race, e, t) for e in "BCD")
    assert _order(race, t) == ["A", "B", "C", "D"] and rd_["gap_s"] is None, (_order(race, t), rd_)
    assert abs(rb["gap_s"] - 0.97) < 0.1 and abs(rc["gap_s"] - 1.97) < 0.1, \
        f"the gaps count from the estimated leader, not from B: {rb['gap_s']} {rc['gap_s']}"
    assert abs(rb["gap_time_s"] - 0.97) < 0.1, rb

if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
