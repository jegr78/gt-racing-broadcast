#!/usr/bin/env python3
"""The event simulation's driver library (#1233): the synthetic track, the cars and their
senders on simulated time, checked against the relay's own race engine. Nothing is
spawned and no socket is opened. Run: python3 tests/test_event_sim_drive.py"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SIM = os.path.join(ROOT, "tools", "event-sim")
for _p in (SIM, os.path.join(ROOT, "src", "scripts")):
    sys.path.insert(0, _p)
import drive  # noqa: E402
import gt7_crypto  # noqa: E402
import gt7_data  # noqa: E402
import gt7_telemetry  # noqa: E402
import gt7_tracks  # noqa: E402
import race_distance  # noqa: E402
import telemetry_probe  # noqa: E402

drive.PACKETS_PER_TICK = 1   # the cipher is pure Python: one packet a tick keeps this quick
TRACK = "6e378b"
LENGTH = 1200.0
TEAMS = drive.roster(4)


class Wire:
    """Collects what the senders post, decoded: {code: [sample, ...]}."""

    def __init__(self):
        self.samples = {}
        self.now = 0.0

    def __call__(self, code, body):
        for raw in telemetry_probe.decode_batch(body):
            kind, plain = gt7_crypto.decrypt_typed(raw)
            pkt = gt7_telemetry.parse_packet(plain)
            self.samples.setdefault(code, []).append(
                {"t": self.now, "lap": pkt.lap, "speed_kmh": pkt.speed_mps * 3.6, "x": pkt.pos_x,
                 "z": pkt.pos_z, "last_ms": pkt.last_ms, "best_ms": pkt.best_ms, "pid": pkt.packet_id,
                 "kind": kind, "fuel": pkt.fuel_level, "position": pkt.race_position,
                 "cars": pkt.car_count})

    def last(self, code):
        return self.samples[code][-1]

    def count(self, code):
        return len(self.samples.get(code, []))


def field(wire, cars=4, seed=7, stints=2):
    f = drive.Field(drive.trace(drive.synthetic_path(LENGTH)), wire, seed=seed)
    for k, entry in enumerate(TEAMS[:cars]):
        f.add(entry, [f"D{k}S{s}" for s in range(stints)])
        for s in range(stints):
            f.set_code(entry, s, f"c{k}s{s}")
    return f


def run(f, wire, seconds, until=None, dt=drive.TICK_S):
    """Step simulated time; returns True when `until` came true."""
    for _ in range(int(seconds / dt)):
        wire.now += dt
        f.step(wire.now, dt)
        if until is not None and until():
            return True
    return until is None


def lateral(f, sample):
    """Metres between a sample and the racing line."""
    best = None
    for a, b in zip(f.line.trace, f.line.trace[1:], strict=False):
        dx, dz = b["x"] - a["x"], b["z"] - a["z"]
        u = ((sample["x"] - a["x"]) * dx + (sample["z"] - a["z"]) * dz) / ((dx * dx + dz * dz) or 1.0)
        u = max(0.0, min(1.0, u))
        d = ((sample["x"] - a["x"] - u * dx) ** 2 + (sample["z"] - a["z"] - u * dz) ** 2) ** 0.5
        best = d if best is None else min(best, d)
    return best


def t_the_synthetic_track_is_data_the_relay_accepts():
    path = drive.synthetic_path(LENGTH)
    doc = drive.signatures_doc(TRACK, path)
    assert gt7_data.validate("signatures.json", json.dumps(doc).encode()) >= gt7_data.MIN_SIGNATURES, \
        "the relay's own validator must accept the simulated signatures"
    tmp = tempfile.mkdtemp(prefix="racecast-drive-test-")
    try:
        drive.write_track_data(tmp, TRACK, LENGTH)
        ref = gt7_tracks.TrackDB.load(tmp, os.path.join(ROOT, "src", "assets", "gt7")).reference_trace(TRACK)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    assert ref, "the relay finds a downloaded line for the picked layout"
    mine = drive.trace(path)
    assert abs(ref[-1]["d"] - mine[-1]["d"]) < 0.01, "the drivers and the relay agree on the lap length"
    assert abs(mine[-1]["d"] - LENGTH) < LENGTH * 0.02, f"the lap is about {LENGTH} m, not {mine[-1]['d']}"
    assert (mine[0]["x"], mine[0]["z"]) == (mine[-1]["x"], mine[-1]["z"]), "the line is closed"


def _host(tmp, track, line, raw=None):
    """A host runtime whose downloaded signatures.json holds `line` for `track`."""
    host = os.path.join(tmp, "host-" + track + str(len(line)))
    os.makedirs(gt7_data.data_dir(host))
    with open(os.path.join(gt7_data.data_dir(host), "signatures.json"), "w", encoding="utf-8") as fh:
        doc = drive.signatures_doc(track, line)
        doc["signatures"][0]["provenance"] = "survey"     # as a downloaded file names it
        fh.write(raw if raw is not None else json.dumps(doc))
    return host


def t_the_hosts_line_is_driven_when_it_fits_the_scenarios_lap():
    tmp = tempfile.mkdtemp(prefix="racecast-drive-test-")
    try:
        line = [[x + 500.0, z - 300.0] for x, z in drive.synthetic_path(2000.0)]   # away from the stadium's spot
        host = _host(tmp, TRACK, line)
        home = os.path.join(tmp, "home")
        drive.write_track_data(home, TRACK, 2000.0, host=host)
        with open(os.path.join(gt7_data.data_dir(home), "signatures.json"), "rb") as fh, \
                open(os.path.join(gt7_data.data_dir(host), "signatures.json"), "rb") as src:
            assert fh.read() == src.read(), "the host's file is copied, so the relay reads the same line"
        ref = drive.home_trace(home, TRACK)
        assert (ref[0]["x"], ref[0]["z"]) == tuple(line[0]), f"the drivers drive the host's line, not {ref[0]}"

        def stadium(length, host_dir):
            target = os.path.join(tmp, f"home-{length}-{os.path.basename(host_dir)}")
            drive.write_track_data(target, TRACK, length, host=host_dir)
            first = drive.home_trace(target, TRACK)[0]
            return (first["x"], first["z"]) == tuple(drive.synthetic_path(length)[0])
        assert stadium(1200.0, host), "a host line of another lap length keeps the scenario's synthetic lap"
        assert stadium(2000.0, _host(tmp, "aaaaaa", line)), "a host without the layout's line: the synthetic lap"
        assert stadium(2000.0, _host(tmp, TRACK, line[:3], raw="{broken")), "an invalid host file: the synthetic lap"
        assert stadium(2000.0, os.path.join(tmp, "nothing")), "no host file: the synthetic lap"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t_the_line_note_says_where_the_line_came_from():
    tmp = tempfile.mkdtemp(prefix="racecast-drive-test-")
    try:
        line = [[x + 500.0, z] for x, z in drive.synthetic_path(2000.0)]
        hosted = os.path.join(tmp, "hosted")
        drive.write_track_data(hosted, TRACK, 2000.0, host=_host(tmp, TRACK, line))
        note = drive.line_note(hosted, TRACK)
        assert note.startswith("host signatures.json (") and note.endswith(" m)"), note
        plain = os.path.join(tmp, "plain")
        drive.write_track_data(plain, TRACK, 1200.0)
        assert drive.line_note(plain, TRACK).startswith("simulated lap ("), drive.line_note(plain, TRACK)
        assert drive.line_note(os.path.join(tmp, "none"), TRACK) == "none", "no line, no note"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t_a_track_must_be_long_enough_for_its_pit_lane():
    try:
        drive.synthetic_path(300.0)
    except ValueError as exc:
        assert "at least" in str(exc), exc
    else:
        raise AssertionError("a lap shorter than the pit lane must be refused")


def t_the_roster_is_unique_team_labels_with_numbers():
    teams = drive.roster(15)
    assert len(set(teams)) == 15 and all("#" in t for t in teams), teams
    for bad in (0, 16):
        try:
            drive.roster(bad)
        except ValueError:
            continue
        raise AssertionError(f"roster({bad}) must be refused")


def t_the_same_seed_drives_the_same_race():
    def batches(seed):
        sent = []
        f = drive.Field(drive.trace(drive.synthetic_path(LENGTH)), lambda code, body: sent.append((code, body)),
                        seed=seed)
        for k, entry in enumerate(TEAMS):
            f.add(entry, ["a"])
            f.set_code(entry, 0, f"c{k}")
        f.start()
        for i in range(300):
            f.step(i * drive.TICK_S, drive.TICK_S)
        return sent
    assert batches(7) == batches(7), "a seed must fix every packet"
    assert batches(7) != batches(8), "another seed must drive another race"


def t_the_lap_counter_reads_0_on_the_grid_and_steps_at_the_line():
    wire = Wire()
    f = field(wire, cars=1)
    run(f, wire, 2.0)
    grid = wire.last("c0s0")
    assert grid["lap"] == 0 and grid["speed_kmh"] == 0, f"a car on the grid stands on lap 0: {grid}"
    f.start()
    assert run(f, wire, 20.0, lambda: wire.last("c0s0")["lap"] == 1), "the first crossing starts lap 1"
    assert wire.last("c0s0")["last_ms"] == -1, "no lap time before a complete lap"
    assert run(f, wire, 60.0, lambda: wire.last("c0s0")["lap"] == 2), "the second crossing starts lap 2"
    first_ms = wire.last("c0s0")["last_ms"]
    assert first_ms > 0 and wire.last("c0s0")["best_ms"] == -1, "GT7 marks lap 1 red: no best lap"
    assert run(f, wire, 60.0, lambda: wire.last("c0s0")["lap"] == 3), "the third crossing starts lap 3"
    lap_ms = wire.last("c0s0")["last_ms"]
    pace = f.car(TEAMS[0]).base
    assert abs(lap_ms / 1000 - f.line.length / pace) < 1.0, f"a flying lap took {lap_ms} ms at {pace:.1f} m/s"
    assert wire.last("c0s0")["best_ms"] == lap_ms, "the first valid lap is the best lap"
    assert run(f, wire, 60.0, lambda: wire.last("c0s0")["lap"] == 4), "the fourth crossing starts lap 4"
    assert wire.last("c0s0")["best_ms"] == min(lap_ms, wire.last("c0s0")["last_ms"]), "the best lap is the quicker one"
    pids = [s["pid"] for s in wire.samples["c0s0"]]
    assert pids == sorted(set(pids)), "packet ids rise, one per packet"


def t_gt7_positions_are_opt_in_and_follow_the_running_order():
    wire = Wire()
    plain = field(wire, cars=2)
    run(plain, wire, 1.0)
    assert wire.last("c0s0")["position"] is None, "without positions the field leaves 0x84 empty"
    wire = Wire()
    f = drive.Field(drive.trace(drive.synthetic_path(LENGTH)), wire, seed=7, positions=True)
    for k, entry in enumerate(TEAMS[:3]):
        f.add(entry, [f"D{k}"])
        f.set_code(entry, 0, f"c{k}s0")
    f.start()
    run(f, wire, 30.0)
    order = sorted(range(3), key=lambda k: -f.cars[k].odo)
    got = [wire.last(f"c{k}s0")["position"] for k in order]
    assert got == [1, 2, 3], f"0x84 names each car's place on the road: {got}"
    assert {wire.last(f"c{k}s0")["cars"] for k in range(3)} == {3}, "and the cars in the session"


def t_a_sender_without_a_code_sends_nothing():
    wire = Wire()
    f = drive.Field(drive.trace(drive.synthetic_path(LENGTH)), wire, seed=7)
    f.add(TEAMS[0], ["a"])
    f.start()
    run(f, wire, 3.0)
    assert wire.samples == {}, "an unregistered driver has no code to send with"


def t_a_pit_stop_stands_off_the_line_and_then_leaves():
    wire = Wire()
    f = field(wire, cars=1)
    f.start()
    run(f, wire, 5.0)
    f.pit(TEAMS[0], seconds=4.0)
    car = f.car(TEAMS[0])
    assert run(f, wire, 60.0, lambda: car.mode == "stand"), "the car reaches its box"
    run(f, wire, 1.0)
    s = wire.last("c0s0")
    assert s["speed_kmh"] == 0 and lateral(f, s) > race_distance.BOX_LATERAL_M, \
        f"a boxed car stands more than {race_distance.BOX_LATERAL_M} m off the line: {lateral(f, s):.1f} m"
    assert run(f, wire, 30.0, lambda: car.mode == "drive"), "the car leaves after its stop"
    assert lateral(f, wire.last("c0s0")) < 1.0, "back on the line"
    try:
        f.pit("Nobody #0", seconds=1.0)
    except drive.DriveError as exc:
        assert "Nobody #0" in str(exc)
    else:
        raise AssertionError("an unknown entry must be refused")


def t_box_holds_every_car_until_release_and_the_next_lobby_takes_over():
    wire = Wire()
    f = field(wire, cars=3)
    f.start()
    run(f, wire, 5.0)
    f.box([TEAMS[1], TEAMS[0], TEAMS[2]])
    assert run(f, wire, 80.0, lambda: all(c.mode == "stand" for c in f.cars)), "every car stands in the box"
    run(f, wire, 20.0)
    assert all(c.mode == "stand" for c in f.cars), "a boxed field waits for its release"
    spot = {c.entry: c.odo % f.line.length for c in f.cars}
    assert spot[TEAMS[1]] > spot[TEAMS[0]] > spot[TEAMS[2]], f"the box order is the order asked for: {spot}"
    assert wire.count("c0s1") == 0, "the next stint's driver sends nothing before its lobby opens"
    f.lobby(1)
    run(f, wire, 2.0)
    assert wire.last("c0s1")["lap"] == 0 and wire.last("c0s1")["speed_kmh"] == 0, \
        "the next lobby's driver stands in the box on lap 0"
    before = wire.count("c0s0")
    run(f, wire, 1.0)
    assert wire.count("c0s0") > before, "the old lobby's driver still sends until the car leaves"
    f.release()
    assert run(f, wire, 60.0, lambda: all(c.mode == "drive" for c in f.cars)), "every car left the box"
    old = wire.count("c0s0")
    run(f, wire, 2.0)
    assert wire.count("c0s0") == old, "the old lobby's sender is silent after the change"
    assert wire.last("c0s1")["speed_kmh"] > 0 and f.car(TEAMS[0]).driver == "D0S1", "the new driver is in the car"
    order = sorted(f.cars, key=lambda c: c.left_at)
    assert [c.entry for c in order] == TEAMS[:3], "cars leave in the order they arrived, each by its gap"


def t_a_keep_restart_releases_each_car_by_its_given_gap():
    wire = Wire()
    f = field(wire, cars=3)
    f.start()
    run(f, wire, 5.0)
    f.box([TEAMS[2], TEAMS[0], TEAMS[1]])
    assert run(f, wire, 80.0, lambda: all(c.mode == "stand" for c in f.cars))
    f.release({TEAMS[0]: 0.0, TEAMS[2]: 9.0})
    assert run(f, wire, 60.0, lambda: all(c.mode == "drive" for c in f.cars))
    left = {c.entry: c.left_at for c in f.cars}
    assert abs(left[TEAMS[1]] - left[TEAMS[0]] - 9.0) <= drive.TICK_S + 1e-9, \
        f"a car without a gap leaves with the last: {left}"
    assert abs(left[TEAMS[2]] - left[TEAMS[0]] - 9.0) <= drive.TICK_S + 1e-9, \
        f"each car leaves its gap after the first, whatever the box order: {left}"
    try:
        f.release({"Nobody #0": 1.0})
    except drive.DriveError as exc:
        assert "Nobody #0" in str(exc)
    else:
        raise AssertionError("a gap for a car not on the grid must be refused")


def t_release_right_when_a_car_stops_or_before_it_stops_does_not_crash():
    wire = Wire()
    f = field(wire, cars=3)
    f.start()
    run(f, wire, 5.0)
    f.box()
    assert run(f, wire, 80.0, lambda: f.cars[0].mode == "stand"), "the first car stops in its box"
    assert f.cars[0].pit["stood"] is None, "its stop time is set one tick later"
    assert any(c.mode == "pit_in" for c in f.cars) or any(c.mode == "drive" for c in f.cars), \
        "the others are still on their way"
    f.release()
    assert all("delay" in c.pit for c in f.cars if c.pit), "every held car got a release delay"
    assert run(f, wire, 90.0, lambda: all(c.mode == "drive" and c.left_at is not None for c in f.cars)), \
        f"every car stops and leaves again: {f.snapshot()}"


def t_a_step_posts_outside_the_runners_lock():
    held = []
    runner = None

    def send(code, body):
        free = runner.lock.acquire(blocking=False)
        held.append(not free)
        if free:
            runner.lock.release()
    f = drive.Field(drive.trace(drive.synthetic_path(LENGTH)), send, seed=7)
    f.add(TEAMS[0], ["a"])
    f.set_code(TEAMS[0], 0, "c")
    f.start()
    t = [0.0]

    def sleep(s):
        t[0] += drive.TICK_S
        if t[0] > 1.0:
            runner.halt.set()
    runner = drive.Runner(f, clock=lambda: t[0], sleep=sleep)
    runner.loop()
    assert held and not any(held), "a slow relay must not block an instruction waiting for the lock"
    assert drive.Client("http://127.0.0.1:5").ingest_timeout <= 3.0, "one stuck post is bounded"


def t_a_driver_staying_on_keeps_the_code_and_restarts_the_counter():
    wire = Wire()
    f = drive.Field(drive.trace(drive.synthetic_path(LENGTH)), wire, seed=7)
    f.add(TEAMS[0], ["Same", "Same"])
    f.set_code(TEAMS[0], 0, "one")
    f.set_code(TEAMS[0], 1, "one")
    f.start()
    assert run(f, wire, 60.0, lambda: wire.last("one")["lap"] == 2)
    f.box()
    assert run(f, wire, 80.0, lambda: f.cars[0].mode == "stand")
    f.lobby(1)
    run(f, wire, 1.0)
    assert wire.last("one")["lap"] == 0, "the same connection starts over in the new lobby"
    f.release()
    assert run(f, wire, 30.0, lambda: f.cars[0].mode == "drive")
    assert set(wire.samples) == {"one"}, "one driver, one code"


def t_a_drop_goes_silent_and_a_rejoin_starts_a_new_session_in_the_box():
    wire = Wire()
    f = field(wire, cars=2)
    f.start()
    assert run(f, wire, 60.0, lambda: wire.last("c0s0")["lap"] == 2)
    f.drop(TEAMS[0])
    n = wire.count("c0s0")
    run(f, wire, 5.0)
    assert wire.count("c0s0") == n, "a dropped driver sends nothing"
    assert wire.count("c1s0") > 0 and f.car(TEAMS[1]).mode == "drive", "the others drive on"
    f.rejoin(TEAMS[0], seconds=3.0)
    run(f, wire, 1.0)
    s = wire.last("c0s0")
    assert s["lap"] == 0 and s["speed_kmh"] == 0 and lateral(f, s) > race_distance.BOX_LATERAL_M, \
        f"a rejoining driver stands in the box of a new session: {s}"
    assert run(f, wire, 30.0, lambda: f.car(TEAMS[0]).mode == "drive"), "and drives out again"
    try:
        f.rejoin(TEAMS[1])
    except drive.DriveError as exc:
        assert "not dropped" in str(exc)
    else:
        raise AssertionError("only a dropped car rejoins")


def t_stop_ends_the_senders():
    wire = Wire()
    f = field(wire, cars=2)
    f.start()
    run(f, wire, 3.0)
    f.stop([TEAMS[0]])
    n0, n1 = wire.count("c0s0"), wire.count("c1s0")
    run(f, wire, 3.0)
    assert wire.count("c0s0") == n0 and wire.count("c1s0") > n1, "only the stopped car goes quiet"
    f.stop()
    assert all(row["mode"] == "off" for row in f.snapshot()), f.snapshot()


def t_the_relays_race_engine_reads_the_field_as_a_race():
    wire = Wire()
    f = field(wire, cars=4, stints=1)
    race = race_distance.Race(f.line.trace, formation=False)
    race.set_roster(TEAMS)
    f.start()
    fed = {}
    box_seen = False
    f.pit(TEAMS[3], seconds=6.0)
    for _ in range(int(150 / drive.TICK_S)):
        wire.now += drive.TICK_S
        f.step(wire.now, drive.TICK_S)
        for k, entry in enumerate(TEAMS):
            code = f"c{k}s0"
            for s in wire.samples.get(code, [])[fed.get(code, 0):]:
                race.update(entry, code, f"D{k}S0", s)
            fed[code] = wire.count(code)
        rows = {r["entry"]: r for r in race.standings(wire.now)["entries"]}
        box_seen = box_seen or rows[TEAMS[3]].get("status") == "box"
    out = race.standings(wire.now)
    rows = out["entries"]
    assert out["state"] == "racing", out["state"]
    assert [r["entry"] for r in rows][:3] == TEAMS[:3], \
        f"the grid order holds for cars of falling pace: {[r['entry'] for r in rows]}"
    assert rows[0]["laps"] >= 5, f"the leader completes laps: {rows[0]['laps']}"
    assert box_seen, "the engine saw the pit stop as a car in the box"
    assert rows[-1]["entry"] == TEAMS[3] and rows[-1]["pit_stops"] == 1, \
        f"the stop costs the car its place and counts once: {rows[-1]}"


def t_scenario_checks_read_only_fields_a_race_row_has():
    import re
    import tomllib
    wire = Wire()
    f = field(wire, cars=1, stints=1)
    race = race_distance.Race(f.line.trace, formation=False)
    race.set_roster(TEAMS[:1])
    f.start()
    for _ in range(int(5 / drive.TICK_S)):
        wire.now += drive.TICK_S
        f.step(wire.now, drive.TICK_S)
    for s in wire.samples.get("c0s0", []):
        race.update(TEAMS[0], "c0s0", "D0S0", s)
    known = set(race.standings(wire.now)["entries"][0]) | {"brand", "brand_name"}   # driver_race.with_brands
    reads = re.compile(r"row\(race\.entries, *'entry', *'[^']+'\)\.(\w+)")
    folder = os.path.join(SIM, "scenarios")
    unknown = set()
    for name in sorted(os.listdir(folder)):
        with open(os.path.join(folder, name), "rb") as fh:
            steps = tomllib.load(fh).get("step", [])
        unknown |= {(name, m) for st in steps for e in st.get("expect", []) + st.get("soft", [])
                    for m in reads.findall(e) if m not in known}
    assert unknown == set(), f"/drivers/race rows have no such field, the check could never pass: {sorted(unknown)}"


def t_the_runner_steps_on_its_clock_and_stops():
    wire = Wire()
    f = field(wire, cars=1)
    f.start()
    ticks = []

    def sleep(s):
        ticks.append(s)
        clock.t += drive.TICK_S
        if len(ticks) == 5:
            runner.halt.set()

    class clock:  # noqa: N801  a callable namespace
        t = 50.0

    runner = drive.Runner(f, clock=lambda: clock.t, sleep=sleep)
    runner.loop()
    assert len(ticks) == 5 and wire.count("c0s0") == 5 * drive.PACKETS_PER_TICK, \
        "one batch per car and tick until halted"
    runner.close()


def slow_runner(f, tick_cost, loops):
    """A runner whose every tick costs `tick_cost` wall seconds; halts after `loops`."""
    clock = [50.0]
    done = []

    def sleep(s):
        clock[0] += tick_cost
        done.append(s)
        if len(done) == loops:
            runner.halt.set()
    runner = drive.Runner(f, clock=lambda: clock[0], sleep=sleep)
    return runner


def t_a_slow_tick_catches_up_the_wall_clock():
    loops = 30
    f = field(Wire(), cars=1)
    f.start()
    slow_runner(f, 1.0, loops).loop()
    ref = field(Wire(), cars=1)
    ref.start()
    simulated = drive.TICK_S + (loops - 1) * 1.0
    for i in range(round(simulated / drive.TICK_S)):
        ref.step(i * drive.TICK_S, drive.TICK_S)
    got, want = f.cars[0].odo, ref.cars[0].odo
    assert abs(got - want) < 0.03 * want, f"the field keeps wall time: {got:.0f} m against {want:.0f} m"


def t_a_catch_up_posts_only_the_newest_batch_per_sender():
    loops = 10
    wire = Wire()
    f = field(wire, cars=2)
    f.start()
    slow_runner(f, 1.0, loops).loop()
    for code in ("c0s0", "c1s0"):
        assert wire.count(code) == loops * drive.PACKETS_PER_TICK, \
            f"{code}: one batch per loop, not one per catch-up step ({wire.count(code)} samples)"
        pids = [s["pid"] for s in wire.samples[code]]
        assert pids == list(range(pids[0], pids[0] + len(pids))), f"{code}: a catch-up skips no packet id: {pids}"


def t_a_long_stall_catches_up_only_a_bounded_time():
    f = field(Wire(), cars=1)
    f.start()
    steps = []
    step = f.step

    def counted(now, dt, flush=True, post=True):
        steps.append(dt)
        step(now, dt, flush, post)
    f.step = counted
    slow_runner(f, 3600.0, 2).loop()
    bound = getattr(drive, "MAX_CATCH_UP_S", 0.0)
    assert sum(steps) <= drive.TICK_S + bound + 1e-6, \
        f"a stall replays at most MAX_CATCH_UP_S, not {sum(steps):.0f} s"
    assert sum(steps) >= drive.TICK_S + bound - 1e-6, f"a stall still replays the bound, not {sum(steps):.1f} s"
    assert max(steps) <= drive.TICK_S + 1e-9, f"every step stays a tick, not {max(steps)} s"


def t_a_step_that_raises_ends_the_runner_with_its_reason():
    f = field(Wire(), cars=1)
    f.step = lambda now, dt, flush=True, post=True: 1 / 0
    runner = drive.Runner(f, clock=lambda: 1.0, sleep=lambda s: None)
    runner.loop()
    assert runner.error.startswith("ZeroDivisionError"), f"the loop ends and says why: {runner.error!r}"


def t_the_runner_ends_at_its_deadline():
    wire = Wire()
    f = field(wire, cars=1)
    t = [10.0]

    def sleep(s):
        t[0] += drive.TICK_S
    drive.Runner(f, clock=lambda: t[0], sleep=sleep).loop(until=11.0)
    assert 10.9 < t[0] < 11.3, f"the loop ends at its deadline, not at {t[0]}"


def t_the_client_talks_to_loopback_unless_told_otherwise():
    for bad in ("http://192.0.2.7:8088", "https://127.0.0.1:1", "127.0.0.1:1"):
        try:
            drive.Client(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad} must be refused")
    assert drive.Client("http://192.0.2.7:8088", loopback_only=False).host == "192.0.2.7"
    assert drive.Client("http://127.0.0.1:5").port == 5


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")
