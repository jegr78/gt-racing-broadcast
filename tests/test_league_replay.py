#!/usr/bin/env python3
"""tools/league-replay.py: a race recording played against the simulated league service
(#1253). Run: python3 tests/test_league_replay.py"""
import contextlib, importlib.util, io, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "event-sim", "fakes"))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import league_sim  # noqa: E402
import race_recording  # noqa: E402

spec = importlib.util.spec_from_file_location("league_replay", os.path.join(ROOT, "tools", "league-replay.py"))
lr = importlib.util.module_from_spec(spec); spec.loader.exec_module(lr)

KEY = "fixture-league-key-4"


def _recording(d, n=7):
    path = os.path.join(d, "race-recording-20261124-120000.sqlite")
    store = race_recording.Store(path)
    for i in range(n):
        store.add_sample(1000.0 + i, {"state": "racing" if i < n - 1 else "finished", "red_flag": None, "rows": [
            {"pos": 1, "entry": "e1", "driver": "Ann", "laps": i, "gap_time_s": 0.0, "interval_time_s": None,
             "lapped": 0, "status": "racing", "finished": False, "pit_stops": 0, "compound": "RM",
             "correction_m": 0.0},
            {"pos": 2, "entry": "e2", "driver": "Ben", "laps": i, "gap_time_s": 75.0, "interval_time_s": 75.0,
             "lapped": 1, "status": "racing", "finished": False, "pit_stops": 1, "gt7_position": 2}]})
    store.close()
    return path


@contextlib.contextmanager
def _env(**values):
    saved = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _run(argv):
    out, slept = io.StringIO(), []
    code = lr.main(argv, sleep=slept.append, out=out)
    return code, out.getvalue(), slept


def t_a_recording_plays_as_live_state():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim, \
            _env(RACECAST_SIMULATION="1", LEAGUE_SERVICE_KEY=KEY):
        rec = _recording(d, n=8)
        code, out, slept = _run([rec, "--url", sim.url, "--ref", league_sim.DEFAULT_REF, "--race", "leg-2",
                                 "--speed", "2"])
        assert code == 0, out
        calls = sim.calls(call="live")
        assert len(calls) == 5, "one state per 2 s of race time, the last sample always"
        assert slept == [1.0, 1.0, 1.0, 0.5], f"paced by race time over --speed: {slept}"
        body = calls[-1]["body"]
        assert (body["ref"], body["race"], body["state"]) == (league_sim.DEFAULT_REF, "leg-2", "finished")
        ann, ben = body["entries"]
        assert ann["gap_s"] == 0.0 and ben["gap_s"] is None and ben["interval_s"] == 75.0, \
            "a lapped car's race gap is null"
        for banned in ("compound", "correction_m", "gt7_position"):
            assert banned not in ben, banned
        assert "match-score" in out and KEY not in out


def t_errors_are_reported_and_the_key_stays_off_argv():
    with tempfile.TemporaryDirectory() as d, league_sim.LeagueSim(key=KEY) as sim:
        rec = _recording(d, n=3)
        with _env(RACECAST_SIMULATION="1", LEAGUE_SERVICE_KEY=""):
            code, out, _ = _run([rec, "--url", sim.url, "--ref", "R", "--race", "leg-1"])
            assert code == 2 and "KEY" in out, out
        with _env(LEAGUE_SERVICE_KEY=KEY):
            code, out, _ = _run([rec, "--url", sim.url, "--ref", "R", "--race", "leg-1"])
            assert code == 2 and "https" in out, "outside simulation the loopback fake is refused"
        key_file = os.path.join(d, "key")
        with open(key_file, "w", encoding="utf-8") as fh:
            fh.write(KEY + "\n")
        with _env(RACECAST_SIMULATION="1", LEAGUE_SERVICE_KEY=""):
            code, out, _ = _run([rec, "--url", sim.url, "--ref", league_sim.DEFAULT_REF, "--race", "leg-9",
                                 "--key-file", key_file])
        assert code == 1 and "HTTP 400" in out and "2 failed" in out, out
        assert not any(a.startswith("--key") and a != "--key-file" for a in lr.build_parser()._option_string_actions)


def t_dry_run_sends_nothing_and_the_file_stays_unchanged():
    with tempfile.TemporaryDirectory() as base, league_sim.LeagueSim(key=KEY) as sim:
        d = os.path.join(base, "odd #1 ?50%")
        os.makedirs(d)
        rec = _recording(d, n=2)
        before = os.stat(rec).st_mtime_ns
        code, out, _ = _run([rec, "--url", sim.url, "--ref", "R", "--race", "leg-1", "--dry-run"])
        assert code == 0 and '"entries"' in out and sim.calls() == []
        assert os.stat(rec).st_mtime_ns == before, "the recording is opened read-only"
        code, out, _ = _run([os.path.join(d, "missing.sqlite"), "--url", sim.url, "--ref", "R", "--race", "r"])
        assert code == 2 and not os.path.exists(os.path.join(d, "missing.sqlite")), \
            "a missing recording is never created"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
