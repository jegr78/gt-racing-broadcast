#!/usr/bin/env python3
"""Stdlib unit checks for the POV additions. Run: python3 tests/test_pov.py"""
import importlib.util, json, os, tempfile, time
import threading, urllib.request, urllib.error

# Default flipped to manual-arm ON (#492 follow-up). These relay tests exercise the
# legacy auto-pull machinery (index/dedup/qualifying); pin them to the opt-out path so
# they stay focused. Tests that need manual mode set r.manual_feed_arm/paused explicitly.
os.environ.setdefault("RACECAST_MANUAL_FEED_ARM", "0")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# Feed opens a per-feed log at construction (configure_logging). Point every
# Feed/Relay logdir at a throwaway temp dir so the suite never writes feed_*.log
# into the repo tree.
LOGDIR = tempfile.mkdtemp(prefix="racecast-test-logs-")
spec = importlib.util.spec_from_file_location(
    "irofeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


class _FakeSource:
    def __init__(self, items): self.items = list(items)
    def get(self): return self.items
    def get_rows(self): return [(u, "", "", i + 1) for i, u in enumerate(self.items)]
    def refresh(self, timeout=None): pass
    def health(self): return {"ok": True}


_URLS8 = [f"https://www.youtube.com/watch?v=stint{i}" for i in range(1, 9)]


def _serve(relay):
    srv = m.ThreadingHTTPServer(("127.0.0.1", 0), m.make_handler(relay))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def t_benign_client_disconnect_classifies_aborts():
    # A browser source / panel tab that closes mid-response trips one of these;
    # they are the client's doing, never a relay fault, so they must be swallowed
    # (ConnectionAbortedError == WinError 10053 from issue #25).
    assert m._benign_client_disconnect(ConnectionAbortedError())
    assert m._benign_client_disconnect(ConnectionResetError())
    assert m._benign_client_disconnect(BrokenPipeError())
    # real faults must still surface (a generic OSError is NOT a ConnectionError).
    assert not m._benign_client_disconnect(ValueError("real bug"))
    assert not m._benign_client_disconnect(OSError("disk full"))
    assert not m._benign_client_disconnect(None)


def t_no_window_kwargs_per_os():
    # The relay daemon runs DETACHED (no console), so its yt-dlp/streamlink/
    # tailscale children would each pop a terminal window on Windows (issue #30).
    # CREATE_NO_WINDOW only on Windows; a no-op (empty kwargs) everywhere else so
    # the same spawn site stays cross-platform.
    assert m._no_window_kwargs("nt") == {"creationflags": 0x08000000}
    assert m._no_window_kwargs("posix") == {}
    assert m._no_window_kwargs("java") == {}


def t_parse_one_url():
    items = m.ScheduleSource._parse_csv("url\nhttps://www.youtube.com/watch?v=abc123\n")
    assert items == ["https://www.youtube.com/watch?v=abc123"], items


def t_parse_empty_is_none():
    assert m.ScheduleSource._parse_csv("url\n\n") is None


def t_preview_source_onair_uses_obs():
    keys = {"A", "B"}
    assert m.preview_source("A", "A", False, keys) == ("obs", "Feed A")
    assert m.preview_source("B", "B", False, keys) == ("obs", "Feed B")


def t_preview_source_offair_uses_pull():
    keys = {"A", "B"}
    assert m.preview_source("B", "A", False, keys) == ("pull", "B")
    assert m.preview_source("A", "B", False, keys) == ("pull", "A")


def t_preview_source_pov_active_vs_paused():
    keys = {"A", "B", "POV"}
    assert m.preview_source("POV", "A", True, keys) == ("obs", "Feed POV")
    assert m.preview_source("POV", "A", False, keys) == ("placeholder", "pov off")


def t_preview_source_unconfigured_feed_is_placeholder():
    assert m.preview_source("B", "A", False, {"A"}) == ("placeholder", "feed off")


def t_preview_source_unknown_target_is_placeholder():
    assert m.preview_source("X", "A", False, {"A", "B"}) == ("placeholder", "unknown feed")


def t_feed_paused_returns_none():
    f = m.Feed("POV", 53003, 0, lambda: ["https://youtu.be/x"], LOGDIR)
    f.paused = True
    assert f.current_channel() == (None, 0)
    f.paused = False
    ch, i = f.current_channel()
    assert ch == "https://youtu.be/x" and i == 0


def t_feed_has_fmt_attr():
    f = m.Feed("POV", 53003, 0, lambda: [], LOGDIR, fmt="b[height<=720]/b")
    assert f.fmt == "b[height<=720]/b"


def t_feed_fast_exit_error_flags_immediate_bind_failure():
    # #143: streamlink that dies almost instantly with a non-zero code = a failed
    # --player-external-http bind (orphan holds the port). Surface it as last_error.
    msg = m.feed_fast_exit_error(0.2, 1)
    assert msg and "port in use" in msg


def t_feed_fast_exit_error_ignores_clean_and_long_exits():
    # The normal serving path must NEVER set a spurious error.
    assert m.feed_fast_exit_error(0.2, 0) is None       # clean exit (stream ended)
    assert m.feed_fast_exit_error(0.2, None) is None    # never finished / unknown rc
    assert m.feed_fast_exit_error(120.0, 1) is None     # served a while, then died
    assert m.feed_fast_exit_error(None, 1) is None      # no timing available


def t_status_surfaces_feed_last_error():
    # Once Feed.last_error is set, /status (per-feed payload) shows it so the panel
    # stops displaying a silent 'connecting' (the #133 mystery).
    r = _relay(["a", "b"])
    r.A.last_error = m.feed_fast_exit_error(0.2, 1)
    assert r.status()["feeds"]["A"]["last_error"] == "feed exited immediately — port in use? see feed log"


def t_status_exposes_feed_source_state():
    rows = [("uA", "A", "S1", 1), ("uB", "B", "S2", 2)]
    r = m.Relay(_StubSource(["uA", "uB"], rows), (53001, 53002), LOGDIR)
    assert r.A.source_state is None
    st = r.status()
    assert st["feeds"]["A"]["source_state"] is None
    r.A.source_state = "not_live_yet"
    assert r.status()["feeds"]["A"]["source_state"] == "not_live_yet"


def t_serve_exit_is_drop():
    # A serving feed's process exited. It's an unexpected DROP (lost live picture)
    # only when the exit was NOT intentional — not a relay stop, not a handover/
    # reload (advance). This keeps the panel alert off during normal handovers.
    assert m.serve_exit_is_drop(stopped=False, advancing=False) is True
    assert m.serve_exit_is_drop(stopped=True, advancing=False) is False   # relay stopping
    assert m.serve_exit_is_drop(stopped=False, advancing=True) is False   # handover/reload
    assert m.serve_exit_is_drop(stopped=True, advancing=True) is False


def t_dead_serve_backoff_escalates_and_caps():
    # base, 2x, 4x, 8x with the default base (RETRY_SLEEP = 10)
    assert m.dead_serve_backoff(1) == 10
    assert m.dead_serve_backoff(2) == 20
    assert m.dead_serve_backoff(3) == 40
    assert m.dead_serve_backoff(4) == 80
    # capped at DEAD_SERVE_BACKOFF_CAP (300)
    assert m.dead_serve_backoff(6) == 300
    assert m.dead_serve_backoff(100) == 300
    # count below 1 falls back to the base (defensive)
    assert m.dead_serve_backoff(0) == 10
    # explicit base/cap honoured
    assert m.dead_serve_backoff(3, base=5, cap=15) == 15   # 5*4=20 -> capped to 15


def t_should_idle_dead_serves_at_limit():
    assert m.should_idle_dead_serves(4) is False
    assert m.should_idle_dead_serves(5) is True            # DEAD_SERVE_IDLE_AFTER == 5
    assert m.should_idle_dead_serves(6) is True
    assert m.should_idle_dead_serves(2, limit=2) is True   # custom limit
    assert m.should_idle_dead_serves(1, limit=2) is False


def t_feed_starts_not_dropped():
    f = m.Feed("A", 53001, 0, lambda: ["a"], LOGDIR)
    assert f.dropped is False


def t_status_surfaces_feed_down():
    # Once a feed is marked dropped, /status flags it down so the panel can raise
    # a distinct alarm; a paused/stopped feed is never 'down' (intentional).
    r = _relay(["a", "b"])
    assert r.status()["feeds"]["A"]["down"] is False
    r.A.dropped = True
    assert r.status()["feeds"]["A"]["down"] is True
    assert r.status()["feeds"]["B"]["down"] is False
    r.A.paused = True                          # paused beats dropped -> not an alarm
    assert r.status()["feeds"]["A"]["down"] is False


def t_reload_and_set_index_clear_dropped():
    # Director intervention (reload / reposition) acknowledges the drop: the alarm
    # clears, and re-fires only if the feed drops again.
    f = m.Feed("A", 53001, 0, lambda: ["a", "b"], LOGDIR)
    f.dropped = True
    f.reload()
    assert f.dropped is False
    f.dropped = True
    f.set_index(1)
    assert f.dropped is False


def t_current_channel_idles_past_end():
    # idx beyond the schedule -> idle (None), NOT a clamp onto the last stint
    f = m.Feed("B", 53002, 1, lambda: ["https://youtu.be/only"], LOGDIR)
    assert f.current_channel() == (None, 1)        # one link, B on slot 2 -> idle
    f2 = m.Feed("B", 53002, 0, lambda: ["https://youtu.be/only"], LOGDIR)
    assert f2.current_channel() == ("https://youtu.be/only", 0)


def t_set_index_allows_one_past_end_for_idle():
    f = m.Feed("A", 53001, 0, lambda: ["a", "b"], LOGDIR)
    assert f.set_index(2) is True                  # len 2 -> idle slot 2 is reachable
    assert f.idx == 2
    assert f.current_channel() == (None, 2)        # idles
    f2 = m.Feed("A", 53001, 0, lambda: ["a", "b"], LOGDIR)
    assert f2.set_index(99) is True                # clamps to len (idle sentinel) from idx 0
    assert f2.idx == 2
    assert f2.set_index(99) is False               # already at the sentinel -> no-op


class _StubSource:
    def __init__(self, items, rows=None):
        self._items = list(items)
        # rows parallel to items: (url, streamer, stint, line). Default: bare.
        self._rows = list(rows) if rows is not None else [
            (u, "", "", i + 1) for i, u in enumerate(items)]
    def get(self): return list(self._items)
    def get_rows(self): return list(self._rows)
    def refresh(self, timeout=6): return True
    def health(self): return {"count": len(self._items), "last_ok_age_s": 0, "last_error": None}
    def add(self, url):
        self._items.append(url)
        self._rows.append((url, "", "", len(self._items)))


def _relay(items):
    r = m.Relay(_StubSource(items), (53001, 53002), LOGDIR)
    r._reflect = lambda live, cut: None        # isolate index logic from OBS I/O
    r._reflect_pov = lambda shown: None        # isolate POV toggle from OBS I/O
    return r


def t_next_new_live_is_the_non_advanced_feed():
    r = _relay(["s1", "s2", "s3", "s4"])
    assert (r.A.idx, r.B.idx) == (0, 1)
    assert r.live_after_next() == "B"          # B (stint2) is pre-warmed -> next live
    r.next_auto()
    assert (r.A.idx, r.B.idx) == (2, 1)        # A advanced to stint3; B now live
    assert r.live_feed() == "B"
    r.next_auto()
    assert (r.A.idx, r.B.idx) == (2, 3)        # B advanced to stint4; A now live
    assert r.live_feed() == "A"


def t_cold_start_one_link_then_add_second():
    r = _relay(["s1"])                         # start with ONE link
    assert (r.A.idx, r.B.idx) == (0, 1)
    assert r.B.current_channel() == (None, 1)  # B idles (black) on the empty slot 2
    r.source.add("s2")                         # link entered mid-event
    assert r.live_after_next() == "B"
    r.next_auto()
    assert r.live_feed() == "B" and r.B.current_channel() == ("s2", 1)


def t_next_reflects_only_when_incoming_serving():
    r = _relay(["s1", "s2", "s3", "s4"])
    calls = []
    r._reflect = lambda live, cut: calls.append((live, cut))
    r.feeds["B"].phase = "idle"                 # incoming feed not yet serving
    out = r.next_auto()
    assert out["obs_cut"] is False
    assert calls == []                          # no visibility/audio flip onto a non-serving feed
    r.feeds["A"].phase = "serving"              # next incoming feed is live
    out2 = r.next_auto()
    assert out2["obs_cut"] is True
    assert calls == [("A", True)]


def t_next_auto_stops_freed_feed_on_cut():
    r = _relay(["s1", "s2", "s3", "s4"])
    r.manual_feed_arm = True
    r.feeds["A"].phase = "serving"      # outgoing serving
    r.feeds["B"].phase = "serving"      # incoming armed + serving -> cut
    r.A.paused = False; r.B.paused = False
    out = r.next_auto()                 # live_after_next=B, freed=A
    assert out["obs_cut"] is True
    assert r.A.paused is True           # freed feed auto-stopped
    assert r.B.paused is False          # incoming stays live/armed


def t_next_auto_keeps_freed_when_no_cut():
    r = _relay(["s1", "s2", "s3", "s4"])
    r.manual_feed_arm = True
    r.feeds["A"].phase = "serving"      # outgoing serving (still on air)
    r.feeds["B"].phase = "idle"         # incoming NOT serving -> no cut
    r.A.paused = False; r.B.paused = False
    out = r.next_auto()
    assert out["obs_cut"] is False
    assert r.A.paused is False          # freed NOT stopped -> live picture preserved


def t_next_auto_freed_disarmed_at_next_slot():
    r = _relay(["s1", "s2", "s3", "s4"])
    r.manual_feed_arm = True
    r.feeds["A"].phase = "serving"; r.feeds["B"].phase = "serving"
    r.A.paused = False; r.B.paused = False
    r.next_auto()                       # cut to B; freed A re-indexed to stint3 (idx2) + stopped
    assert r.A.idx == 2 and r.A.paused is True


def t_next_auto_legacy_no_autostop():
    r = _relay(["s1", "s2", "s3", "s4"])
    r.manual_feed_arm = False           # legacy auto pre-roll opt-out
    r.feeds["A"].phase = "serving"; r.feeds["B"].phase = "serving"
    r.A.paused = False; r.B.paused = False
    r.next_auto()
    assert r.A.paused is False          # legacy: freed keeps pre-rolling, no auto-stop


def t_set_stint_reflects_live_feed_without_cut():
    r = _relay(["s1", "s2", "s3", "s4"])
    calls = []
    r._reflect = lambda live, cut: calls.append((live, cut))
    r.set_stint(3)                              # stint 3 on air -> A=2, B=3, live=A
    assert (r.A.idx, r.B.idx) == (2, 3)
    assert calls == [("A", False)]


def t_set_stint_slot_aware_on_back_to_back():
    rows = [("uA", "A", "Stint 1", 1), ("uB", "B", "Stint 2", 2),
            ("uB", "B", "Stint 3", 3), ("uD", "D", "Stint 4", 4)]
    r = m.Relay(_StubSource(["uA", "uB", "uB", "uD"], rows), (53001, 53002), LOGDIR)
    r._reflect = lambda live, cut: None
    # Takeover: stint 3 (second half of B's back-to-back) is on air NOW.
    r.set_stint(3)
    # Feed A parks on the slot head (row1 = the single uB pull), B preloads uD (row3).
    assert (r.A.idx, r.B.idx) == (1, 3)
    assert r.A.current_channel()[0] == "uB" and r.B.current_channel()[0] == "uD"
    # ...but the DISPLAY shows stint 3.
    assert r.on_air_row_idx() == 2
    assert r.live_schedule_row() == {"streamer": "B", "stint": "Stint 3"}


def t_race_control_map_follows_displayed_stint_on_continuation():
    rows = [("uA", "A", "Stint 1", 1), ("uB", "B", "Stint 2", 2),
            ("uB", "B", "Stint 3", 3), ("uD", "D", "Stint 4", 4)]
    r = m.Relay(_StubSource(["uA", "uB", "uB", "uD"], rows), (53001, 53002), LOGDIR)
    r._reflect = lambda live, cut: None
    for f in r.feeds.values(): f.phase = "serving"
    r.next_auto()                       # stint 2 (B on air, uB pull on B row1)
    r.next_auto()                       # stint 3 continuation: label at row2, pull at row1
    assert r.on_air_row_idx() == 2
    live = r.live_row_map()
    # the RC/stint-plan highlight is on the DISPLAYED stint (row2), not the pull row (1)
    sched = m.race_control_schedule(rows, live)
    assert sched[2]["live"] == "B"      # stint 3 marked live
    assert sched[1]["live"] is None     # stint 2 no longer highlighted


def t_live_schedule_row_pure():
    rows = [("https://youtu.be/a", "JeGr", "Stint 1", 1),
            ("https://youtu.be/b", "GT45", "Stint 2", 2)]
    assert m.live_schedule_row(rows, 0) == {"streamer": "JeGr", "stint": "Stint 1"}
    assert m.live_schedule_row(rows, 1) == {"streamer": "GT45", "stint": "Stint 2"}
    assert m.live_schedule_row(rows, 2) is None        # idles past the end
    assert m.live_schedule_row(rows, -1) is None       # never wraps to the last row
    assert m.live_schedule_row([], 0) is None
    assert m.live_schedule_row(rows, None) is None


def t_relay_live_schedule_row_tracks_on_air_feed():
    rows = [("s1", "JeGr", "Stint 1", 1), ("s2", "GT45", "Stint 2", 2),
            ("s3", "Ann", "Stint 3", 3), ("s4", "Ben", "Stint 4", 4)]
    r = m.Relay(_StubSource(["s1", "s2", "s3", "s4"], rows), (53001, 53002), LOGDIR)
    r._reflect = lambda live, cut: None
    assert r.live_feed() == "A"                        # A on stint 1
    assert r.live_schedule_row() == {"streamer": "JeGr", "stint": "Stint 1"}
    r.next_auto()                                      # B (stint 2) now on air
    assert r.live_feed() == "B"
    assert r.live_schedule_row() == {"streamer": "GT45", "stint": "Stint 2"}


def t_on_air_row_tracks_feed_in_normal_operation():
    r = _relay(["s1", "s2", "s3", "s4"])
    assert r.on_air_row_idx() == 0                       # stint 1
    assert r.live_row_map() == {0: "A", 1: "B"}          # on-air A row0, off-air B row1
    r.next_auto()                                        # B (stint 2) on air
    assert r.on_air_row_idx() == 1
    assert r.live_feed() == "B"
    assert r.live_row_map() == {1: "B", 2: "A"}          # on-air B row1, off-air A row2
    # the HUD row follows the displayed on-air row
    rows = r.source.get_rows()
    assert m.live_schedule_row(rows, r.on_air_row_idx())["stint"] == r.live_schedule_row()["stint"]


def t_back_to_back_no_dup_pull_no_cut():
    rows = [("uA", "A", "Stint 1", 1), ("uB", "B", "Stint 2", 2),
            ("uB", "B", "Stint 3", 3), ("uD", "D", "Stint 4", 4)]
    r = m.Relay(_StubSource(["uA", "uB", "uB", "uD"], rows), (53001, 53002), LOGDIR)
    r._reflect = lambda live, cut: None
    # make both feeds report "serving" so cut/continuation is exercised
    for f in r.feeds.values(): f.phase = "serving"

    def pulled():
        return {k: f.current_channel()[0] for k, f in r.feeds.items()}

    # start: A=uA on air (row0), B preloads uB (row1)
    assert r.on_air_row_idx() == 0 and r.live_feed() == "A"
    assert pulled() == {"A": "uA", "B": "uB"}

    # Next -> stint 2 (real handover): B(uB) on air; freed A must skip the
    # duplicate uB run and preload uD -> NO second uB pull anywhere.
    out1 = r.next_auto()
    assert out1["continuation"] is False and out1["obs_cut"] is True
    assert r.on_air_row_idx() == 1 and r.live_feed() == "B"
    assert pulled() == {"A": "uD", "B": "uB"}
    assert list(pulled().values()).count("uB") == 1        # no duplicate uB

    # Next -> stint 3 (continuation): same feed, same pull, NO cut, label advances
    out2 = r.next_auto()
    assert out2["continuation"] is True and out2["obs_cut"] is False
    assert r.on_air_row_idx() == 2 and r.live_feed() == "B"
    assert pulled() == {"A": "uD", "B": "uB"}               # untouched
    assert r.live_schedule_row() == {"streamer": "B", "stint": "Stint 3"}

    # Next -> stint 4 (real handover): cut to A(uD)
    out3 = r.next_auto()
    assert out3["continuation"] is False and out3["obs_cut"] is True
    assert r.on_air_row_idx() == 3 and r.live_feed() == "A"
    assert r.live_schedule_row() == {"streamer": "D", "stint": "Stint 4"}


def t_back_to_back_manual_arm_full_sequence():
    # The DEFAULT workflow (manual feed arm ON): director arms the incoming feed,
    # then /next. Scenario: stint 1 = K1 (uA); stints 2 AND 3 = K2 on ONE stream
    # (uB, back-to-back); stint 4 = K4 (uD). Walk the whole sequence asserting the
    # ARM state (paused) of BOTH feeds at every step — the same-URL continuation
    # must be a pure label advance that touches NO feed (no arm, no stop, no cut),
    # so only ONE feed ever pulls uB (the #491/#505 single-puller invariant).
    rows = [("uA", "K1", "Stint 1", 1), ("uB", "K2", "Stint 2", 2),
            ("uB", "K2", "Stint 3", 3), ("uD", "K4", "Stint 4", 4)]
    r = m.Relay(_StubSource(["uA", "uB", "uB", "uD"], rows), (53001, 53002), LOGDIR)
    r._reflect = lambda live, cut: None
    r.manual_feed_arm = True
    r.A.paused = True; r.B.paused = True          # both start disarmed (as __init__ would)

    # Slot map: uA | uB uB | uD -> slots [0,1,1,2]; index placement A=0, B=next slot=1.
    assert (r.A.idx, r.B.idx) == (0, 1)
    assert r.on_air_row_idx() == 0 and r.live_feed() == "A"
    assert r.A.current_channel() == (None, 0)     # disarmed -> no pull, even with a URL present

    # --- Stint 1 goes live: director ARMS Feed A --------------------------------
    r.A.paused = False; r.A.phase = "serving"
    assert r.A.current_channel() == ("uA", 0)
    assert r.B.current_channel() == (None, 1)     # B still disarmed -> no second pull

    # --- Handover stint 1 -> 2 (real, different URL): arm B, then /next ----------
    r.B.paused = False; r.B.phase = "serving"     # pre-roll uB on the off-air feed
    out1 = r.next_auto()
    assert out1["continuation"] is False and out1["obs_cut"] is True
    assert r.on_air_row_idx() == 1 and r.live_feed() == "B"
    # freed A is AUTO-STOPPED (disarmed) and skips the uB continuation run to uD (idx3).
    assert r.A.paused is True and r.A.idx == 3
    assert r.B.paused is False and r.B.current_channel() == ("uB", 1)
    assert r.A.current_channel() == (None, 3)     # disarmed -> no pull (single puller: only B on uB)

    # --- Continuation stint 2 -> 3 (SAME URL): director just presses /next -------
    # No arm, no stop, no cut — the LABEL advances and BOTH feeds are untouched.
    out2 = r.next_auto()
    assert out2["continuation"] is True and out2["obs_cut"] is False
    assert r.on_air_row_idx() == 2 and r.live_feed() == "B"
    assert r.B.paused is False and r.B.idx == 1 and r.B.current_channel() == ("uB", 1)
    assert r.A.paused is True and r.A.idx == 3     # freed A stays disarmed & parked on uD
    assert r.live_schedule_row() == {"streamer": "K2", "stint": "Stint 3"}

    # --- Handover stint 3 -> 4 (real, different URL): arm A (already on uD), /next
    r.A.paused = False; r.A.phase = "serving"      # arm A on its parked uD slot
    out3 = r.next_auto()
    assert out3["continuation"] is False and out3["obs_cut"] is True
    assert r.on_air_row_idx() == 3 and r.live_feed() == "A"
    assert r.A.paused is False and r.A.current_channel() == ("uD", 3)
    # freed B is auto-stopped and parked on the idle sentinel (one past the last row).
    assert r.B.paused is True and r.B.idx == len(rows)
    assert r.live_schedule_row() == {"streamer": "K4", "stint": "Stint 4"}


def t_back_to_back_leading_manual_arm_no_predecessor():
    # LEADING back-to-back: stints 1 AND 2 share ONE stream (uA, same commentator)
    # right at the start. The very first /next therefore has NO predecessor feed to
    # stop — and because it is a same-URL continuation it never even reaches the
    # handover/STOP branch. This asserts nothing breaks: the first /next is a pure
    # label advance, and the off-air feed is slot-parked past the uA run from t=0.
    rows = [("uA", "K1", "Stint 1", 1), ("uA", "K1", "Stint 2", 2),
            ("uC", "K3", "Stint 3", 3), ("uD", "K4", "Stint 4", 4)]
    r = m.Relay(_StubSource(["uA", "uA", "uC", "uD"], rows), (53001, 53002), LOGDIR)
    r._reflect = lambda live, cut: None
    r.manual_feed_arm = True
    r.A.paused = True; r.B.paused = True

    # Slot map: uA uA | uC | uD -> slots [0,0,1,2]. B preloads the NEXT slot (uC,
    # row2) from the start, NOT the continuation row -> no leading double-pull.
    assert (r.A.idx, r.B.idx) == (0, 2)
    assert r.on_air_row_idx() == 0 and r.live_feed() == "A"

    # Stint 1 live: arm A.
    r.A.paused = False; r.A.phase = "serving"
    assert r.A.current_channel() == ("uA", 0)
    assert r.B.current_channel() == (None, 2)     # B disarmed, parked on uC

    # --- First /next: stint 1 -> 2 is a CONTINUATION (same uA) ------------------
    # No predecessor to stop, no cut, no feed touched: it returns before the branch
    # that would ever call STOP. The "STOP finds nothing" case simply never runs.
    out1 = r.next_auto()
    assert out1["continuation"] is True and out1["obs_cut"] is False
    assert r.on_air_row_idx() == 1 and r.live_feed() == "A"
    assert r.A.paused is False and r.A.idx == 0 and r.A.current_channel() == ("uA", 0)
    assert r.B.paused is True and r.B.idx == 2     # untouched, still parked on uC
    assert r.live_schedule_row() == {"streamer": "K1", "stint": "Stint 2"}

    # --- Second /next: stint 2 -> 3 (real handover, uA -> uC): NOW arm B + cut ---
    r.B.paused = False; r.B.phase = "serving"
    out2 = r.next_auto()
    assert out2["continuation"] is False and out2["obs_cut"] is True
    assert r.on_air_row_idx() == 2 and r.live_feed() == "B"
    assert r.B.current_channel() == ("uC", 2)
    # freed A is auto-stopped and advanced to the next distinct slot (uD, row3).
    assert r.A.paused is True and r.A.idx == 3
    assert r.live_schedule_row() == {"streamer": "K3", "stint": "Stint 3"}


def t_next_auto_real_handover_stop_freed_never_armed_is_noop():
    # Robustness for the general case: even a REAL handover where the freed feed was
    # never armed/serving must not break — STOP just disarms it and reload()->
    # _kill_proc() is a no-op when there is no process. (All-distinct URLs, no
    # continuation involved.)
    rows = [("s1", "A", "Stint 1", 1), ("s2", "B", "Stint 2", 2),
            ("s3", "C", "Stint 3", 3), ("s4", "D", "Stint 4", 4)]
    r = m.Relay(_StubSource(["s1", "s2", "s3", "s4"], rows), (53001, 53002), LOGDIR)
    r._reflect = lambda live, cut: None
    r.manual_feed_arm = True
    r.A.paused = False; r.A.phase = "serving"      # on air
    r.B.paused = False; r.B.phase = "serving"      # incoming armed -> cut fires
    assert r.A.proc is None                        # freed feed has NO process to kill
    out = r.next_auto()                            # live_after_next=B, freed=A
    assert out["obs_cut"] is True                  # did not raise
    assert r.A.paused is True                       # freed disarmed cleanly


def t_status_live_stint_reports_display_row_on_continuation():
    # Normal (all-distinct) schedule: /status live.stint == the physical pull index.
    r = _relay(["s1", "s2", "s3", "s4"])
    assert r.status()["live"]["stint"] == r.on_air_row_idx() + 1 == 1
    r.next_auto()                                       # stint 2, real handover
    assert r.status()["live"]["stint"] == r.on_air_row_idx() + 1 == 2

    # Back-to-back continuation: the DISPLAY stint is one ahead of the still-parked
    # physical pull — /status must report the display stint (issue: takeover/health
    # monitor must not resume/show one stint behind).
    rows = [("uA", "A", "Stint 1", 1), ("uB", "B", "Stint 2", 2),
            ("uB", "B", "Stint 3", 3), ("uD", "D", "Stint 4", 4)]
    rc = m.Relay(_StubSource(["uA", "uB", "uB", "uD"], rows), (53001, 53002), LOGDIR)
    rc._reflect = lambda live, cut: None
    for f in rc.feeds.values(): f.phase = "serving"
    rc.next_auto()                                       # stint 2, real handover
    rc.next_auto()                                       # stint 3, continuation
    assert rc.on_air_row_idx() == 2                       # display row = stint 3
    physical_idx = rc.feeds[rc.live_feed()].idx
    assert physical_idx != rc.on_air_row_idx()            # the divergence this fix targets
    assert rc.status()["live"]["stint"] == rc.on_air_row_idx() + 1 == 3


def t_health_snapshot_live_stint_is_display_row_on_continuation():
    # Same-URL back-to-back: the DISPLAY stint (on_air_row_idx) is one ahead of the
    # still-parked physical pull. _health_snapshot must sample the display stint so the
    # report counts the continuation as a distinct stint (#500).
    rows = [("uA", "A", "Stint 1", 1), ("uB", "B", "Stint 2", 2),
            ("uB", "B", "Stint 3", 3), ("uD", "D", "Stint 4", 4)]
    rc = m.Relay(_StubSource(["uA", "uB", "uB", "uD"], rows), (53001, 53002), LOGDIR)
    rc._reflect = lambda live, cut: None
    for f in rc.feeds.values():
        f.phase = "serving"
    rc.next_auto()                                   # stint 2, real handover
    rc.next_auto()                                   # stint 3, continuation
    assert rc.on_air_row_idx() == 2                   # display row = stint 3
    assert rc.feeds[rc.live_feed()].idx != rc.on_air_row_idx()   # the divergence
    snap = rc._health_snapshot(123.0)
    assert snap["live_stint"] == rc.on_air_row_idx() + 1 == 3, snap["live_stint"]


def t_should_push_live_schedule_fires_on_cut_or_continuation():
    # A real cut (obs_cut) always advances the HUD label; a same-URL continuation
    # advances the DISPLAY stint without a cut, so the HUD must advance too — only
    # a plain idle over-press (neither) must be a no-op.
    assert m.should_push_live_schedule({"obs_cut": True})
    assert m.should_push_live_schedule({"continuation": True, "obs_cut": False})
    assert not m.should_push_live_schedule({"obs_cut": False})
    assert not m.should_push_live_schedule({})


def _relay_q(items, qual_items, qual_rows=None, mode="race"):
    race = _StubSource(items)
    qual = _StubSource(qual_items, qual_rows)
    r = m.Relay(race, (53001, 53002), LOGDIR, qual_source=qual, mode=mode)
    r._reflect = lambda live, cut: None
    return r


def t_default_mode_is_race():
    r = _relay_q(["s1", "s2"], ["q1"])
    assert r.mode == "race"
    assert r.source is r.race_source
    assert r.A.current_channel() == ("s1", 0)


def t_start_in_qualifying_mode():
    r = _relay_q(["s1", "s2"], ["q1"], mode="qualifying")
    assert r.mode == "qualifying"
    assert r.source is r.qual_source
    assert r.A.current_channel() == ("q1", 0)       # single qualifying stream on Feed A
    assert r.B.current_channel() == (None, 1)       # Feed B idles (one stream only)


def t_set_mode_switches_active_source_and_feeds():
    qrows = [("q1", "GT45", "Qualifying", 2)]
    r = _relay_q(["s1", "s2", "s3"], ["q1"], qual_rows=qrows)
    assert r.A.current_channel() == ("s1", 0)
    out = r.set_mode("qualifying")
    assert out["mode"] == "qualifying"
    assert r.source is r.qual_source
    assert (r.A.idx, r.B.idx) == (0, 1)             # stint 1: A serves q1, B idles
    assert r.A.current_channel() == ("q1", 0)
    assert r.live_schedule_row() == {"streamer": "GT45", "stint": "Qualifying"}
    r.set_mode("race")                              # back to the race schedule
    assert r.mode == "race" and r.source is r.race_source
    assert (r.A.idx, r.B.idx) == (0, 1)
    assert r.A.current_channel() == ("s1", 0)


def t_set_mode_qualifying_unavailable_without_source():
    r = _relay(["s1", "s2"])                        # no qual_source
    assert r.qual_source is None
    out = r.set_mode("qualifying")
    assert "error" in out and r.mode == "race"      # stays race


def t_set_mode_rejects_unknown():
    r = _relay_q(["s1"], ["q1"])
    assert "error" in r.set_mode("warmup")
    assert r.mode == "race"


def t_status_reports_mode_and_qualifying():
    r = _relay_q(["s1", "s2"], ["q1"])
    st = r.status()
    assert st["mode"] == "race"
    assert st["qualifying"]["active"] is False
    r.set_mode("qualifying")
    assert r.status()["qualifying"]["active"] is True


def t_next_past_end_is_idle_no_cut():
    r = _relay(["s1", "s2"])
    r.feeds["B"].phase = "serving"
    r.next_auto()                               # the one real handover: B (s2) on air
    assert r.live_feed() == "B"
    out = r.next_auto()                         # over-press past the last stint
    assert out["obs_cut"] is False              # incoming feed idle -> no cut, no crash


def t_splitscreen_state_maps_live_feed_to_current():
    r = _relay(["s1", "s2", "s3", "s4"])
    assert r.splitscreen_state() == {"current": "A", "next_active": True, "mode": "race"}
    r.next_auto()                                  # B becomes the on-air feed
    assert r.splitscreen_state()["current"] == "B"


def t_splitscreen_state_hides_next_in_qualifying():
    r = _relay_q(["s1", "s2"], ["q1"], mode="qualifying")
    st = r.splitscreen_state()
    assert st == {"current": "A", "next_active": False, "mode": "qualifying"}


def t_pov_active_tracks_pov_shown():
    r = _relay(["s1", "s2"])
    assert r.pov_active() is False               # pov_shown defaults False
    assert r.set_pov_shown(True) == {"shown": True}
    assert r.pov_active() is True
    assert r.pov_toggle() == {"shown": False}    # flips back
    assert r.pov_active() is False
    assert r.pov_toggle() == {"shown": True}     # and forward again
    assert r.pov_active() is True


def t_pov_name_reads_pov_source_row():
    r = _relay(["s1", "s2"])
    assert r.pov_name() == ""                        # no pov_source
    r.pov_source = _StubSource(["https://youtu.be/p"],
                               rows=[("https://youtu.be/p", "JeGr", "", 2)])
    assert r.pov_name() == "JeGr"
    r.pov_source = _StubSource([], rows=[])          # source but no row
    assert r.pov_name() == ""


def t_hud_page_has_pov_name_slot_and_gating():
    import os as _os
    path = _os.path.join(ROOT, "src", "obs", "hud.html")
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    # The name slot exists and is a builder slot (data-edit marker).
    assert 'id="pov-name"' in html
    assert 'data-edit="POV name"' in html
    # tick() hides the whole POV box (frame) when the POV feed is off.
    assert "povActive" in html
    assert 'getElementById("pov").classList.toggle("empty"' in html


def t_preview_program_endpoint_serves_jpeg():
    r = m.Relay(_FakeSource(_URLS8), [53001, 53002], LOGDIR)

    class FakeObs:
        def get_program_screenshot(self, **kw): return (b"\xff\xd8PGM\xff\xd9", "")
        def get_source_screenshot(self, *a, **kw): return (None, "n/a")

    old = m._obs_ws; m._obs_ws = FakeObs(); srv = _serve(r)
    try:
        port = srv.server_address[1]
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/preview/program", timeout=5)
        body = resp.read()
        assert resp.headers["Content-Type"] == "image/jpeg"
        assert resp.headers["Cache-Control"] == "no-store"
        assert body == b"\xff\xd8PGM\xff\xd9"
    finally:
        srv.shutdown(); m._obs_ws = old


def t_preview_program_503_when_obs_down():
    r = m.Relay(_FakeSource(_URLS8), [53001, 53002], LOGDIR)
    old = m._obs_ws; m._obs_ws = None; srv = _serve(r)
    try:
        port = srv.server_address[1]
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/preview/program", timeout=5)
            raise AssertionError("expected HTTP 503")
        except urllib.error.HTTPError as e:
            assert e.code == 503
    finally:
        srv.shutdown(); m._obs_ws = old


def t_obs_stream_endpoint_starts_and_validates():
    r = m.Relay(_FakeSource(_URLS8), [53001, 53002], LOGDIR)

    class FakeObs:
        def __init__(self): self.calls = []
        def set_stream(self, active, **kw):
            self.calls.append(active); return (True, "")

    fo = FakeObs(); old = m._obs_ws; m._obs_ws = fo; srv = _serve(r)
    try:
        port = srv.server_address[1]
        url = f"http://127.0.0.1:{port}/obs/stream"
        req = urllib.request.Request(
            url, data=b'{"on": true}',
            headers={"Content-Type": "application/json"}, method="POST")
        body = urllib.request.urlopen(req, timeout=5).read()
        assert json.loads(body)["ok"] is True
        assert fo.calls == [True]
        # Missing "on" -> 400
        req = urllib.request.Request(
            url, data=b'{}', headers={"Content-Type": "application/json"},
            method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("expected HTTP 400")
        except urllib.error.HTTPError as e:
            assert e.code == 400
    finally:
        srv.shutdown(); m._obs_ws = old


def t_director_panel_has_stream_button():
    path = os.path.join(ROOT, "src", "director", "director-panel.html")
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    assert 'id="obsStreamBtn"' in html
    assert 'obsPost("stream"' in html
    assert "End the live broadcast" in html          # Stop is confirm-guarded


def t_director_panel_chat_rail_fixed_height():
    # The desktop right-rail stacks the crew chat over the read-only broadcast chat.
    # Both logs must have a *fixed* height (so the crew box never grows with messages
    # and pushes into the broadcast box), and the two boxes must not flex-shrink
    # (shrinking made the crew box spill its content over the broadcast box). Two
    # 38vh logs + chrome also overflowed the rail and produced a second scrollbar.
    path = os.path.join(ROOT, "src", "director", "director-panel.html")
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    # The unbounded grow-to-38vh cap is gone (it was the only 38vh in the file).
    assert "38vh" not in html
    # Logs get a stable, viewport-aware fixed height (clamped), not max-height growth.
    assert "details.chat#chatBox .chatlog,details.chat#bchatBox .chatlog{height:clamp(" in html
    # Boxes do not shrink below their content (flex:0 0 auto, not 0 1 auto).
    assert "details.chat#chatBox,details.chat#bchatBox{margin-bottom:0;min-height:0;flex:0 0 auto;" in html
    assert "flex:0 1 auto" not in html


def t_obs_stream_503_when_obs_down():
    r = m.Relay(_FakeSource(_URLS8), [53001, 53002], LOGDIR)
    old = m._obs_ws; m._obs_ws = None; srv = _serve(r)
    try:
        port = srv.server_address[1]
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/obs/stream", data=b'{"on": true}',
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("expected HTTP 503")
        except urllib.error.HTTPError as e:
            assert e.code == 503
    finally:
        srv.shutdown(); m._obs_ws = old


def _serve_mgr(relay, mgr):
    """Serve relay + preview_manager; return the running server."""
    srv = m.ThreadingHTTPServer(("127.0.0.1", 0),
                                m.make_handler(relay, preview_manager=mgr))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def t_preview_feed_onair_uses_obs_not_grab():
    # On-air feed: PreviewManager uses OBS screenshot (obs path, not pull).
    import logging
    r = m.Relay(_FakeSource(_URLS8), [53001, 53002], LOGDIR)   # A on air (idx 0)

    class FakeObs:
        def __init__(self): self.name = None
        def get_source_screenshot(self, name, **kw):
            self.name = name; return (b"\xff\xd8ONAIR\xff\xd9", "")

    fo = FakeObs()
    lg = logging.getLogger("test.pov.onair"); lg.addHandler(logging.NullHandler())
    mgr = m.PreviewManager(r, lambda: fo, lg)
    srv = _serve_mgr(r, mgr)
    try:
        port = srv.server_address[1]
        body = urllib.request.urlopen(f"http://127.0.0.1:{port}/preview/feed/A", timeout=5).read()
        assert body == b"\xff\xd8ONAIR\xff\xd9" and fo.name == "Feed A"
    finally:
        srv.shutdown()


def t_preview_feed_offair_uses_grab_not_obs():
    # Off-air feed, DIRECT-SERVE mode (fan-out off): PreviewManager uses the pull
    # worker (not OBS); worker returns a frame. (Fan-out on routes to the ring tap —
    # preview_source routing is covered in test_feed_preview.py.)
    import logging
    r = m.Relay(_FakeSource(_URLS8), [53001, 53002], LOGDIR)   # B off air (idx 1)
    r.fanout = False                                           # pin the direct-serve pull path
    calls = {}

    def fake_factory(target, channel, cookies, log):
        class _W:
            ok = True
            def __init__(self): self.target = target
            def stop(self): pass
            def latest_frame(self): calls["target"] = target; return b"\xff\xd8GRB\xff\xd9"
            def latest_level(self): return 0.5
        return _W()

    lg = logging.getLogger("test.pov.offair"); lg.addHandler(logging.NullHandler())
    mgr = m.PreviewManager(r, lambda: None, lg, worker_factory=fake_factory)
    srv = _serve_mgr(r, mgr)
    try:
        port = srv.server_address[1]
        body = urllib.request.urlopen(f"http://127.0.0.1:{port}/preview/feed/B", timeout=5).read()
        assert body == b"\xff\xd8GRB\xff\xd9"
        assert calls.get("target") == "B"
    finally:
        srv.shutdown()


def t_preview_feed_pov_paused_is_503():
    # No POV configured: preview_source returns placeholder → still() → (None, "pov off") → 503.
    import logging
    r = m.Relay(_FakeSource(_URLS8), [53001, 53002], LOGDIR)   # no POV configured
    lg = logging.getLogger("test.pov.pov"); lg.addHandler(logging.NullHandler())
    mgr = m.PreviewManager(r, lambda: None, lg)
    srv = _serve_mgr(r, mgr)
    try:
        port = srv.server_address[1]
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/preview/feed/POV", timeout=5)
            raise AssertionError("expected HTTP 503")
        except urllib.error.HTTPError as e:
            assert e.code == 503
    finally:
        srv.shutdown()


def t_preview_feed_unknown_is_404():
    r = m.Relay(_FakeSource(_URLS8), [53001, 53002], LOGDIR)
    srv = _serve(r)
    try:
        port = srv.server_address[1]
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/preview/feed/Z", timeout=5)
            raise AssertionError("expected HTTP 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.shutdown()


def t_preview_feed_grab_failure_is_503():
    # still() returning None yields 503; preview_manager=None (disabled) yields 404.
    import logging
    r = m.Relay(_FakeSource(_URLS8), [53001, 53002], LOGDIR)   # B off air
    lg = logging.getLogger("test.pov.preview"); lg.addHandler(logging.NullHandler())

    class _StillNoneManager:
        relay = r
        def still(self, target): return (None, "unavailable")
        def levels(self): return {}

    srv_mgr = m.ThreadingHTTPServer(("127.0.0.1", 0),
                                    m.make_handler(r, preview_manager=_StillNoneManager()))
    threading.Thread(target=srv_mgr.serve_forever, daemon=True).start()
    try:
        port = srv_mgr.server_address[1]
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/preview/feed/B", timeout=5)
            raise AssertionError("expected HTTP 503")
        except urllib.error.HTTPError as e:
            assert e.code == 503
        # preview_manager=None -> 404 "preview disabled"
        srv_off = m.ThreadingHTTPServer(("127.0.0.1", 0), m.make_handler(r))
        threading.Thread(target=srv_off.serve_forever, daemon=True).start()
        try:
            port2 = srv_off.server_address[1]
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port2}/preview/feed/B", timeout=5)
                raise AssertionError("expected HTTP 404")
            except urllib.error.HTTPError as e:
                assert e.code == 404
        finally:
            srv_off.shutdown()
    finally:
        srv_mgr.shutdown()


def t_aggregate_stream_not_active_is_red():
    # Off-air only escalates once OBS has streamed at least once (stream_expected
    # latch) — a live broadcast that drops off air pages.
    h = m.aggregate_health({"obs_reachable": True, "stream_active": False,
                            "stream_expected": True})
    assert h["level"] == "red"
    assert any("not streaming" in r.lower() or "off air" in r.lower() for r in h["reasons"])


def t_aggregate_stream_not_active_pre_show_is_green():
    # OBS reachable, not streaming, but never streamed this session (no latch) ->
    # NO alarm, so a pre-show relay start never fires a CRITICAL ping.
    assert m.aggregate_health({"obs_reachable": True, "stream_active": False})["level"] == "green"
    assert m.aggregate_health({"obs_reachable": True, "stream_active": False,
                               "stream_expected": False})["level"] == "green"


def t_aggregate_stream_active_unknown_is_green():
    # OBS reachable but stream_active not sampled (None) -> no alarm.
    assert m.aggregate_health({"obs_reachable": True})["level"] == "green"
    assert m.aggregate_health({"obs_reachable": True, "stream_active": None})["level"] == "green"
    # OBS not reachable -> existing yellow path, stream_active ignored.
    assert m.aggregate_health({"obs_reachable": False, "stream_active": False,
                               "stream_expected": True})["level"] == "yellow"


def t_aggregate_yellow_signals():
    for key in ("stream_reconnecting", "funnel_down", "sheet_push_failing"):
        h = m.aggregate_health({"obs_reachable": True, "stream_active": True, key: True})
        assert h["level"] == "yellow", (key, h)
    # observational signals never escalate
    assert m.aggregate_health({"obs_reachable": True, "stream_active": True,
                               "tailscale_up": False, "companion_ok": False})["level"] == "green"


def t_parse_stream_quality():
    assert m.parse_stream_quality("[cli][info] Opening stream: 720p (hls)") == "720p"
    assert m.parse_stream_quality("[cli][info] Opening stream: source (hls)") == "source"
    assert m.parse_stream_quality("[cli][info] Opening stream: 1080p60 (muxed-stream)") == "1080p60"
    assert m.parse_stream_quality("[download] Written 5 MB") is None
    assert m.parse_stream_quality("") is None


def t_parse_ytdlp_quality():
    # yt-dlp `--print "rcq %(height)s %(fps)s"` output (verified shape: rcq line, then the -g URL).
    assert m.parse_ytdlp_quality("rcq 854 30.0\nhttps://manifest.googlevideo.com/x") == "854p30"
    assert m.parse_ytdlp_quality("rcq 1080 60.0") == "1080p60"
    assert m.parse_ytdlp_quality("rcq 720 60") == "720p60"
    assert m.parse_ytdlp_quality("rcq 480 NA") == "480p"        # fps unknown -> height only
    assert m.parse_ytdlp_quality("rcq 480 ") == "480p"
    assert m.parse_ytdlp_quality("rcq NA 30") is None           # no height -> no useful quality
    assert m.parse_ytdlp_quality("https://only.example/x") is None
    assert m.parse_ytdlp_quality("") is None


def t_quality_token_is_useful():
    # YouTube's HLS variant name "live" carries no resolution -> ignore it so the yt-dlp
    # resolution wins; real streamlink labels (Twitch) are kept.
    assert m.quality_token_is_useful("720p60") is True
    assert m.quality_token_is_useful("source") is True
    assert m.quality_token_is_useful("1080p") is True
    assert m.quality_token_is_useful("live") is False
    assert m.quality_token_is_useful("") is False
    assert m.quality_token_is_useful(None) is False


def t_sample_connectivity_sets_state_and_expected():
    r = m.Relay(_FakeSource(_URLS8), [53001, 53002], LOGDIR)
    # Monkeypatch the module-level references that _sample_connectivity uses.
    orig_funnel = m.tailscale.funnel_on
    orig_backend = m.tailscale.tailscale_backend
    orig_reachable = m.companion_common.companion_reachable
    try:
        # Never-expected case: funnel down before ever seen up -> neutral (None).
        r2 = m.Relay(_FakeSource(_URLS8), [53001, 53002], LOGDIR)
        m.tailscale.funnel_on = lambda *a, **k: False
        m.tailscale.tailscale_backend = lambda *a, **k: ("ts", "Running", "100.64.0.1")
        m.companion_common.companion_reachable = lambda *a, **k: True
        r2._sample_connectivity()
        assert r2.conn_state["funnel_ok"] is None    # neutral, not red
        assert r2.funnel_expected is False

        m.tailscale.funnel_on = lambda *a, **k: True
        m.tailscale.tailscale_backend = lambda *a, **k: ("ts", "Running", "100.64.0.1")
        m.companion_common.companion_reachable = lambda *a, **k: False
        r._sample_connectivity()
        assert r.conn_state["funnel_ok"] is True
        assert r.conn_state["tailscale_up"] is True
        assert r.conn_state["companion_ok"] is False
        assert r.funnel_expected is True           # latched once seen up
        # Funnel later down, but expected stays latched -> funnel_down derivable.
        m.tailscale.funnel_on = lambda *a, **k: False
        r._sample_connectivity()
        assert r.conn_state["funnel_ok"] is False  # real regression: was expected
        assert r.funnel_expected is True
    finally:
        m.tailscale.funnel_on = orig_funnel
        m.tailscale.tailscale_backend = orig_backend
        m.companion_common.companion_reachable = orig_reachable


def _make_min_relay():
    """Minimal Relay for snapshot/facts tests — two stints, temp log dir."""
    return m.Relay(_FakeSource(_URLS8), [53001, 53002], LOGDIR)


def t_health_snapshot_carries_desync_active():
    r = _make_min_relay()
    r._desync = {"active": True, "since_s": 20.0}
    assert r._health_snapshot(123.0)["desync_active"] == 1
    r._desync = {"active": False}
    assert r._health_snapshot(123.0)["desync_active"] == 0


def t_check_render_drift_fires_on_sustained_skip_then_cooldown():
    # #488: the GetStats render-skip rate over successive polls, debounced + cooldown-gated,
    # rebuilds the on-air feed's OBS input. This is now the FALLBACK path (freeze detector
    # off); the default cursor-progress detector supersedes it — see the suppression test.
    r = _make_min_relay()
    r._freeze_detect = False            # exercise the renderSkip fallback (detector disabled)
    r.auto_resync = True
    r._autoresync_skip_rate = 0.02
    r._autoresync_cooldown = 60.0
    r._prev_render_counts = None; r._render_drift_streak = 0; r._last_autoresync_ts = None
    calls = []
    live = r.live_feed()
    r.feeds[live]._obs_reconnect = lambda: calls.append(1)
    # poll 1: baseline (no prev -> no rate yet)
    r.obs_stats = {"obs_render_skipped_frames": 0, "obs_render_total_frames": 1000}
    r._check_render_drift(100.0)
    # poll 2: +50/+1000 = 5% > 2% -> streak 1 (< debounce 2) -> no fire
    r.obs_stats = {"obs_render_skipped_frames": 50, "obs_render_total_frames": 2000}
    r._check_render_drift(130.0)
    assert calls == [], "one over-threshold poll must not fire (debounce)"
    # poll 3: another 5% -> streak 2 == AUTORESYNC_DEBOUNCE_POLLS -> fires on the on-air feed
    r.obs_stats = {"obs_render_skipped_frames": 100, "obs_render_total_frames": 3000}
    r._check_render_drift(160.0)
    assert calls == [1], "sustained over-threshold must rebuild the on-air OBS input"
    # cooldown: two more over-threshold polls, but within 60 s -> suppressed
    r.obs_stats = {"obs_render_skipped_frames": 200, "obs_render_total_frames": 4000}
    r._check_render_drift(170.0)   # streak 1
    r.obs_stats = {"obs_render_skipped_frames": 300, "obs_render_total_frames": 5000}
    r._check_render_drift(180.0)   # streak 2 but since=20 s < 60 s cooldown -> no fire
    assert calls == [1], "within cooldown -> no second resync"


def t_check_render_drift_quiet_when_healthy_or_disabled():
    r = _make_min_relay()
    r._freeze_detect = False            # fallback path under test (detector disabled)
    r._autoresync_skip_rate = 0.02
    r._autoresync_cooldown = 60.0
    calls = []
    live = r.live_feed()
    r.feeds[live]._obs_reconnect = lambda: calls.append(1)
    # healthy: ~0.5 % skip rate per interval, well below 2 % -> never fires
    r.auto_resync = True
    r._prev_render_counts = None; r._render_drift_streak = 0; r._last_autoresync_ts = None
    for i in range(1, 7):
        r.obs_stats = {"obs_render_skipped_frames": 5 * i, "obs_render_total_frames": 1000 * i}
        r._check_render_drift(100.0 + 30 * i)
    assert calls == [], "healthy low skip-rate must never trigger a resync"
    # kill-switch: even a huge sustained spike does nothing when auto_resync is off
    r.auto_resync = False
    r._prev_render_counts = None; r._render_drift_streak = 0
    for skip, tot, t in [(0, 1000, 400.0), (900, 2000, 430.0), (1800, 3000, 460.0)]:
        r.obs_stats = {"obs_render_skipped_frames": skip, "obs_render_total_frames": tot}
        r._check_render_drift(t)
    assert calls == [], "kill-switch (auto_resync=False) disables the auto-resync"


def t_check_render_drift_action_suppressed_when_freeze_detect_on():
    # #488 new default: the cursor-progress freeze detector owns auto-recovery, so the (blind)
    # render-skip auto-resync must NOT fire — but the rate is still recorded for the chart.
    r = _make_min_relay()
    r._freeze_detect = True             # the default
    r.auto_resync = True
    r._autoresync_skip_rate = 0.02
    r._prev_render_counts = None; r._render_drift_streak = 0; r._last_autoresync_ts = None
    calls = []
    live = r.live_feed()
    r.feeds[live]._obs_reconnect = lambda: calls.append(1)
    # a sustained, well-over-threshold render-skip spike that WOULD fire the fallback
    for skip, tot, t in [(0, 1000, 100.0), (500, 2000, 130.0), (1000, 3000, 160.0)]:
        r.obs_stats = {"obs_render_skipped_frames": skip, "obs_render_total_frames": tot}
        r._check_render_drift(t)
    assert calls == [], "freeze detector on -> render-skip action is suppressed"
    # the rate is still recorded (prev advanced) so the health chart keeps working
    assert r._prev_render_counts == (1000, 3000)


def t_health_snapshot_carries_render_skip_rate():
    # #488: the per-interval render-skip rate (delta, not the flat cumulative pct) is written
    # into the health snapshot for the Health-Monitor chart + box-event validation.
    r = _make_min_relay()
    r._prev_render_counts = None
    r.obs_stats = {"obs_render_skipped_frames": 10, "obs_render_total_frames": 1000}
    assert r._health_snapshot(1.0)["obs_render_skip_rate_pct"] is None   # no prev yet
    r._prev_render_counts = (10, 1000)
    r.obs_stats = {"obs_render_skipped_frames": 60, "obs_render_total_frames": 2000}  # +50/+1000
    assert r._health_snapshot(2.0)["obs_render_skip_rate_pct"] == 5.0    # 5% per interval


def t_health_snapshot_carries_new_fields():
    relay = _make_min_relay()
    relay.obs_stats = {"obs_cpu_pct": 10.0, "obs_fps": 60.0, "stream_active": True,
                       "stream_reconnecting": False, "stream_congestion": 0.1,
                       "stream_dropped_pct": 0.0, "stream_kbps": 6000.0,
                       "obs_mem_mb": 900.0, "obs_disk_free_mb": 5000.0,
                       "obs_render_skipped_pct": 0.0}
    relay.conn_state = {"funnel_ok": True, "tailscale_up": True, "companion_ok": False}
    relay.funnel_expected = True
    relay.feeds["A"].quality = "720p"
    snap = relay._health_snapshot(123.0)
    assert snap["obs_cpu_pct"] == 10.0 and snap["stream_active"] == 1
    assert snap["funnel_ok"] == 1 and snap["companion_ok"] == 0
    assert snap["feed_a_quality"] == "720p"
    # All COLUMNS keys present (record() tolerates missing, but emit them explicitly).
    hs = m.health_store
    for col in hs.COLUMNS:
        if col not in ("kind",):
            assert col in snap, col


def t_health_facts_gate_funnel_and_push():
    relay = _make_min_relay()
    relay.obs_reachable = True
    relay.obs_stats = {"stream_active": True, "stream_reconnecting": True}
    relay.conn_state = {"funnel_ok": False, "tailscale_up": True, "companion_ok": True}
    relay.funnel_expected = True                  # funnel was up, now down -> funnel_down
    facts = relay._health_facts(1.0)
    assert facts["stream_reconnecting"] is True
    assert facts["funnel_down"] is True
    # funnel never seen up -> not a fault
    relay.funnel_expected = False
    assert relay._health_facts(1.0)["funnel_down"] is False


def t_health_facts_stream_expected_gates_off_air():
    # _health_facts must propagate the stream_expected latch (off until OBS has
    # streamed, then on). The aggregate LEVEL mapping is covered by the
    # t_aggregate_stream_not_active_* tests with EXPLICIT facts — we don't assert it
    # via _health_facts here, because _health_facts also samples ambient signals
    # (tailscale_present etc.) that would make a level assertion env-dependent.
    relay = _make_min_relay()
    relay.obs_reachable = True
    relay.obs_stats = {"stream_active": False}     # OBS reachable but not streaming
    relay.stream_expected = False
    facts = relay._health_facts(1.0)
    assert facts["stream_expected"] is False
    assert facts["stream_active"] is False
    # once OBS has streamed, the latch flips the gate fact on
    relay.stream_expected = True
    assert relay._health_facts(1.0)["stream_expected"] is True


def t_pull_slots_basic():
    def rows(urls): return [(u, "", "", i + 1) for i, u in enumerate(urls)]
    assert m.pull_slots(rows(["a", "b", "b", "d"])) == [0, 1, 1, 2]   # back-to-back b
    assert m.pull_slots(rows(["a", "b", "a"])) == [0, 1, 2]           # non-consecutive != run
    assert m.pull_slots(rows(["b", "b", "b"])) == [0, 0, 0]           # three in a row
    assert m.pull_slots(rows(["", ""])) == [0, 1]                     # blanks never merge
    assert m.pull_slots(rows(["a", "", "a"])) == [0, 1, 2]            # blank breaks the run
    assert m.pull_slots([]) == []


def t_slot_row_helpers():
    slots = [0, 1, 1, 2]
    assert m.slot_first_row(slots, 1) == 1
    assert m.slot_first_row(slots, 2) == 3
    assert m.slot_first_row(slots, 9) is None
    # off-air preload / freed-feed target skips the same-URL run:
    assert m.next_slot_first_row(slots, 0) == 1      # after slot0 -> row1 (b)
    assert m.next_slot_first_row(slots, 1) == 3      # after slot1 (b,b) -> row3 (d), NOT row2
    assert m.next_slot_first_row(slots, 3) == 4      # after last slot -> idle sentinel (len)
    assert m.next_slot_first_row([], 0) == 0
    # continuation detection:
    assert m.is_continuation(slots, 2) is True       # row2 continues row1 (same b)
    assert m.is_continuation(slots, 1) is False      # row1 is a new slot
    assert m.is_continuation(slots, 0) is False      # no row -1
    assert m.is_continuation(slots, 4) is False      # past the end


def t_slot_start_indices():
    def rows(urls): return [(u, "", "", i + 1) for i, u in enumerate(urls)]
    # normal schedule: identical to stint_start_indices (every row its own slot)
    assert m.slot_start_indices(3, rows(["a", "b", "c", "d"])) == (2, 3)
    # takeover onto the SECOND row of a back-to-back (stint 3 = second b):
    # Feed A parks on the slot HEAD (row1), Feed B preloads the next slot (row3)
    assert m.slot_start_indices(3, rows(["a", "b", "b", "d"])) == (1, 3)
    # takeover onto the FIRST b (stint 2): A row1, B skips the duplicate -> row3
    assert m.slot_start_indices(2, rows(["a", "b", "b", "d"])) == (1, 3)
    # empty schedule falls back
    assert m.slot_start_indices(1, []) == (0, 1)


def t_dedupe_pull_index():
    rows = [("uA", "A", "S1", 1), ("uB", "B", "S2", 2),
            ("uB", "B", "S3", 3), ("uD", "D", "S4", 4)]
    # No collision: different URLs -> unchanged.
    assert m.dedupe_pull_index(1, 0, rows) == (1, False)
    # Collision (contiguous same-URL slot): target row2 uB vs other row1 uB
    # -> next distinct slot (row3 uD).
    assert m.dedupe_pull_index(2, 1, rows) == (3, True)
    # Collision at the slot head: target row1 uB vs other row2 uB -> row3.
    assert m.dedupe_pull_index(1, 2, rows) == (3, True)
    # Idle/blank target (idx == len) never collides.
    assert m.dedupe_pull_index(4, 0, rows) == (4, False)
    # Other feed idle -> no collision.
    assert m.dedupe_pull_index(1, 4, rows) == (1, False)
    # Non-contiguous repeated URL: loop past it.
    rows2 = [("uA", "A", "S1", 1), ("uB", "B", "S2", 2),
             ("uC", "C", "S3", 3), ("uB", "B", "S4", 4)]
    # target row3 uB vs other row1 uB -> no safe later slot -> idle sentinel (4).
    assert m.dedupe_pull_index(3, 1, rows2) == (4, True)
    # target row1 uB vs other row3 uB -> next distinct slot row2 (uC).
    assert m.dedupe_pull_index(1, 3, rows2) == (2, True)


def t_is_substitution():
    assert m.is_substitution("uA", 1, "uB", 1) is True      # same stint, new URL
    assert m.is_substitution("uA", 1, "uA", 1) is False     # same URL -> reconnect, not a swap
    assert m.is_substitution("uA", 1, "uB", 2) is False     # different stint -> handover, not a swap
    assert m.is_substitution("", 1, "uB", 1) is False       # no prior served URL
    assert m.is_substitution("uA", 1, "", 1) is False       # cleared URL, not a swap


def t_sanitize_reason():
    assert m.sanitize_reason("  stream A dropped  ") == "stream A dropped"
    assert m.sanitize_reason("line1\nline2\tx") == "line1 line2 x"   # control chars -> single spaces
    assert m.sanitize_reason(None) == "" and m.sanitize_reason(123) == ""
    assert len(m.sanitize_reason("x" * 500)) == m.SUBSTITUTION_REASON_MAX


def t_reload_records_substitution_on_url_swap():
    import tempfile
    # a stub whose refresh() applies a staged URL change (mimics the operator
    # editing the on-air row's URL then pressing Reload)
    class _Staged(_StubSource):
        def __init__(self, items):
            super().__init__(items)
            self._pending = None
        def stage(self, idx, url): self._pending = (idx, url)
        def refresh(self, timeout=6):
            if self._pending:
                i, u = self._pending
                self._items[i] = u
                self._rows[i] = (u,) + tuple(self._rows[i][1:])
                self._pending = None
            return True

    td = tempfile.mkdtemp()
    r = m.Relay(_Staged(["uA", "uB"]), (53001, 53002), LOGDIR)
    r._reflect = lambda live, cut: None
    posts = []
    r._discord_post = lambda payload, what: posts.append((what, payload))
    r.health_store = m.HealthStore(os.path.join(td, "h.db"))
    try:
        assert r.live_feed() == "A"                    # A (uA, idx0) on air
        r.source.stage(0, "uALT")                      # on-air row gets a new URL
        r.reload()                                     # operator force-reload
        subs = [e for e in r.health_store.events(0, 1e12) if e["type"] == "feed_substitution"]
        assert len(subs) == 1
        assert subs[0]["metadata"] == {"feed": "A", "stint": 1}   # feed+stint only, NO url
        assert posts and posts[0][0] == "feed-substitution"       # Discord fired
        # a same-URL reload records nothing more
        r.reload()
        subs2 = [e for e in r.health_store.events(0, 1e12) if e["type"] == "feed_substitution"]
        assert len(subs2) == 1
    finally:
        r.health_store.close()


def t_latest_and_annotate_substitution():
    import tempfile
    td = tempfile.mkdtemp()
    r = _relay(["uA", "uB"])
    r.health_store = m.HealthStore(os.path.join(td, "h.db"))
    try:
        assert r.latest_substitution() is None
        assert r.annotate_substitution_reason("x")["error"]        # nothing to annotate
        r.health_store.record_event(111.0, "feed_substitution", metadata={"feed": "A", "stint": 1})
        assert r.latest_substitution() == {"ts": 111.0, "feed": "A", "stint": 1, "reason": ""}
        out = r.annotate_substitution_reason("  stream A\ndropped  ")
        assert out["reason"] == "stream A dropped"                  # sanitized
        assert r.latest_substitution()["reason"] == "stream A dropped"
    finally:
        r.health_store.close()


def t_should_obs_reconnect_only_on_fanout_drop():
    # OBS reconnect fires ONLY in fan-out mode AND after a real drop — never on the
    # first serve or a seamless handover (both have dropped=False).
    assert m.should_obs_reconnect(True, True) is True       # fan-out + drop-recovery
    assert m.should_obs_reconnect(True, False) is False     # fan-out, first serve / handover
    assert m.should_obs_reconnect(False, True) is False     # direct-serve: OBS reconnects itself
    assert m.should_obs_reconnect(False, False) is False


def t_obs_reconnect_rebuilds_only_this_feeds_port():
    # The recovery rebuild must be SCOPED to this feed's port (not all feeds), so a
    # drop on Feed B never flickers the on-air Feed A.
    f = m.Feed("A", 53001, 0, lambda: [], LOGDIR)
    calls = []
    class _FakeObs:
        def release_feed_inputs(self, ports=None, **k):
            calls.append(ports); return (["Feed A"], "")
    old = m._obs_ws
    m._obs_ws = _FakeObs()
    try:
        names = f._obs_reconnect_now()
    finally:
        m._obs_ws = old
    assert calls == [[53001]], calls
    assert names == ["Feed A"]


def t_obs_reconnect_is_noop_without_obs():
    f = m.Feed("A", 53001, 0, lambda: [], LOGDIR)
    old = m._obs_ws
    m._obs_ws = None
    try:
        assert f._obs_reconnect_now() == []      # no OBS -> silent no-op, never raises
    finally:
        m._obs_ws = old


def t_queue_deadline_args_picks_flag_by_capability():
    # C, version-safe: prefer the modern flag (streamlink 8.1.0+), fall back to the old
    # one, and OMIT when neither exists — an unknown flag would abort streamlink and the
    # feed would never serve (the concern that motivated this).
    new_help = "  --stream-segmented-queue-deadline FACTOR\n  --hls-live-edge NUM\n"
    old_help = "  --hls-segment-queue-threshold FACTOR\n  --hls-live-edge NUM\n"
    assert m.queue_deadline_args(new_help) == ["--stream-segmented-queue-deadline", "5"]
    assert m.queue_deadline_args(old_help) == ["--hls-segment-queue-threshold", "5"]
    assert m.queue_deadline_args("  --hls-live-edge NUM\n") == []   # neither -> omit
    assert m.queue_deadline_args("") == []                          # help probe failed -> omit
    assert m.queue_deadline_args(new_help, factor="7") == ["--stream-segmented-queue-deadline", "7"]


def t_feed_recovery_churn():
    now = 1000.0
    # 3 recoveries within the 300 s window -> churn (would ping @here)
    assert m.feed_recovery_churn([1000.0, 900.0, 800.0], now) is True
    # only 2 within window (600 is 400 s ago) -> not churn
    assert m.feed_recovery_churn([1000.0, 950.0, 600.0], now) is False
    assert m.feed_recovery_churn([], now) is False
    assert m.feed_recovery_churn([None, 1000.0], now) is False           # None ts ignored
    # threshold override
    assert m.feed_recovery_churn([1000.0, 990.0, 980.0, 970.0], now, threshold=5) is False
    assert m.feed_recovery_churn([1000.0, 990.0, 980.0, 970.0], now, threshold=4) is True


class _FakeHS:
    def __init__(self): self.rows = []
    def record_event(self, ts, etype, producer="", metadata=None):
        self.rows.append({"ts": ts, "type": etype, "producer": producer,
                          "metadata": metadata or {}})
    def events(self, frm, to):
        return [e for e in self.rows if frm <= e["ts"] <= to]


def t_feed_recovery_records_always_and_pings_only_on_churn():
    # ALWAYS record (report/health/log); Discord @here ONLY on churn (>=3 in the window),
    # then de-duped by the cooldown so a flapping feed doesn't spam.
    r = _relay(["a", "b"])
    r.health_store = _FakeHS()
    r._event_title = lambda: ""
    posts = []
    r._discord_post = lambda payload, what: posts.append(what)
    r._record_feed_recovery("A", 1, 5.0)
    r._record_feed_recovery("A", 1, 6.0)
    assert len([e for e in r.health_store.rows if e["type"] == "feed_recovery"]) == 2
    assert posts == []                              # below threshold -> silent
    r._record_feed_recovery("A", 1, 7.0)            # 3rd within window -> churn
    assert posts == ["feed-recovery-churn"]
    r._record_feed_recovery("A", 1, 8.0)            # still recorded, but NOT re-pinged
    assert len([e for e in r.health_store.rows if e["type"] == "feed_recovery"]) == 4
    assert posts == ["feed-recovery-churn"]         # cooldown de-dupes


def t_feed_reset_target_validates_feed_key():
    # D: /obs/feed-reset accepts only a real feed key (case/space-insensitive), else None
    # -> 400. Never lets an arbitrary string through to release_feed_inputs.
    feeds = {"A": object(), "B": object()}
    assert m.feed_reset_target("A", feeds) == "A"
    assert m.feed_reset_target(" b ", feeds) == "B"
    assert m.feed_reset_target("POV", feeds) is None
    assert m.feed_reset_target("", feeds) is None
    assert m.feed_reset_target(None, feeds) is None


def t_streamlink_serve_tolerates_brief_gaps():
    # The give-up flag pushes streamlink's stop past the relay's 8 s watchdog. Hermetic:
    # drive the capability probe with a fixed modern help text.
    old = m._streamlink_help
    m._streamlink_help = lambda: "  --stream-segmented-queue-deadline FACTOR\n"
    try:
        cmd = m.streamlink_fanout_cmd("https://youtu.be/x", "youtube")
    finally:
        m._streamlink_help = old
    assert "--stream-segmented-queue-deadline" in cmd
    assert int(cmd[cmd.index("--stream-segmented-queue-deadline") + 1]) >= 5


def t_qualifying_downgrade_note_warns_only_on_silent_downgrade():
    # --qualifying requested but no qualifying source -> LOUD warning (the relay would
    # otherwise silently run race mode and push the wrong Producer stream key).
    note = m.qualifying_downgrade_note(True, False, "Qualifying")
    assert note is not None and "RACE mode" in note and "Qualifying" in note
    # No warning when qualifying was not requested, or a qual source exists.
    assert m.qualifying_downgrade_note(False, False, "Qualifying") is None
    assert m.qualifying_downgrade_note(True, True, "Qualifying") is None
    assert m.qualifying_downgrade_note(False, True, "Qualifying") is None


def t_set_index_guards_same_url_double_pull():
    rows = [("uA", "A", "S1", 1), ("uB", "B", "S2", 2),
            ("uB", "B", "S3", 3), ("uD", "D", "S4", 4)]
    r = m.Relay(_StubSource(["uA", "uB", "uB", "uD"], rows), (53001, 53002), LOGDIR)
    r._reflect = lambda live, cut: None
    for f in r.feeds.values():
        f.phase = "serving"
    r.next_auto()                                  # stint 2: B(uB) on air, A freed -> uD
    assert r.live_feed() == "B" and r.B.current_channel()[0] == "uB"
    # Operator directly activates stint 3 (row2, the SAME uB) on feed A.
    out = r.set_index("A", 2)
    assert out["redirected"] is True
    urls = {k: f.current_channel()[0] for k, f in r.feeds.items()}
    assert list(urls.values()).count("uB") == 1    # single uB pull, no duplicate
    assert urls == {"A": "uD", "B": "uB"}          # A stayed on the next distinct slot
    assert r.on_air_row_idx() == 2                  # display advanced to stint 3
    assert r.live_feed() == "B"                     # B (lower idx) stays on air


def t_reload_guards_same_url_double_pull():
    rows = [("uA", "A", "S1", 1), ("uB", "B", "S2", 2),
            ("uX", "X", "S3", 3), ("uD", "D", "S4", 4)]
    src = _StubSource(["uA", "uB", "uX", "uD"], rows)
    r = m.Relay(src, (53001, 53002), LOGDIR)
    r._reflect = lambda live, cut: None
    for f in r.feeds.values():
        f.phase = "serving"
    # Start: A row0 uA on air, B preloaded row1 uB. The operator edits the sheet
    # so row1's URL becomes uA (== the on-air feed's stream) and reloads.
    src._items[1] = "uA"
    src._rows[1] = ("uA", "A", "S2", 2)
    r.reload()
    urls = {k: f.current_channel()[0] for k, f in r.feeds.items()}
    assert list(urls.values()).count("uA") == 1    # on-air uA not duplicated
    assert r.live_feed() == "A"                     # on-air feed unchanged (no cut)
    assert r.A.current_channel()[0] == "uA"
    assert r.B.current_channel()[0] != "uA"         # off-air B re-pointed off the dup


def t_advance_guards_same_url_double_pull():
    rows = [("uA", "A", "S1", 1), ("uB", "B", "S2", 2),
            ("uB", "B", "S3", 3), ("uD", "D", "S4", 4)]
    r = m.Relay(_StubSource(["uA", "uB", "uB", "uD"], rows), (53001, 53002), LOGDIR)
    r._reflect = lambda live, cut: None
    for f in r.feeds.values():
        f.phase = "serving"
    # A row0 uA on air, B row1 uB. Nudge A by +2 -> row2 (uB) would duplicate B.
    out = r.advance("A", +2)
    assert out["redirected"] is True
    urls = {k: f.current_channel()[0] for k, f in r.feeds.items()}
    assert list(urls.values()).count("uB") == 1     # no duplicate uB
    assert r.A.current_channel()[0] == "uD"          # A bumped to next distinct slot


def t_ping_pong_desynced_pure():
    # On-air feed not delivering while the off-air feed is -> desynced.
    assert m.ping_pong_desynced(live_serving=False, off_serving=True) is True
    # On-air fine -> never desynced.
    assert m.ping_pong_desynced(live_serving=True, off_serving=True) is False
    assert m.ping_pong_desynced(live_serving=True, off_serving=False) is False
    # On-air down but nothing better to show (off not serving) -> a plain drop,
    # a health condition, NOT a desync.
    assert m.ping_pong_desynced(live_serving=False, off_serving=False) is False


def t_desync_settled_debounce():
    # Not raw -> inactive, timer cleared.
    assert m.desync_settled(False, 100.0, 200.0, 15) == (False, None)
    # Raw first seen -> timer starts, not yet active (0 < settle).
    assert m.desync_settled(True, None, 100.0, 15) == (False, 100.0)
    # Raw, still within the settle window -> not active, timer preserved.
    assert m.desync_settled(True, 100.0, 110.0, 15) == (False, 100.0)
    # Raw, past the settle window -> active, timer preserved.
    assert m.desync_settled(True, 100.0, 116.0, 15) == (True, 100.0)


def t_relay_status_exposes_desync_block():
    rows = [("uA", "A", "S1", 1), ("uB", "B", "S2", 2),
            ("uC", "C", "S3", 3), ("uD", "D", "S4", 4)]
    r = m.Relay(_StubSource(["uA", "uB", "uC", "uD"], rows), (53001, 53002), LOGDIR)
    r._reflect = lambda live, cut: None
    # Simulate: on-air feed A (idx0) dropped, off-air feed B (idx1) serving.
    r.A.phase = "connecting"; r.A.dropped = True
    r.B.phase = "serving"; r.B.dropped = False
    # Force the settle to have already elapsed.
    r._desync_since = time.time() - 20
    d = r.status()["desync"]
    assert d["active"] is True
    assert d["serving_feed"] == "B"
    assert d["suggested_stint"] == 2          # B is on row1 -> stint 2
    # Healthy: both serving -> inactive block.
    r.A.dropped = False; r.A.phase = "serving"
    assert r.status()["desync"]["active"] is False


def t_resync_to_stint_keeps_serving_feed_no_cut():
    # Slot-parity desync: stint 3 legitimately runs on Feed B (idx2), Feed A is the
    # dropped ex-on-air feed stuck at a low idx (idx0). set_stint(3) would be
    # A-centric and cut B; resync must keep B on air and move A.
    rows = [("uA", "A", "S1", 1), ("uB", "B", "S2", 2),
            ("uC", "C", "S3", 3), ("uD", "D", "S4", 4)]
    r = m.Relay(_StubSource(["uA", "uB", "uC", "uD"], rows), (53001, 53002), LOGDIR)
    r._reflect = lambda live, cut: None
    r.A.set_index(0); r.B.set_index(2)             # A idx0 (dropped), B idx2 serving uC
    for f in r.feeds.values(): f.phase = "serving"
    r.A.dropped = True
    b_idx_before = r.B.idx
    b_proc_before = r.B.proc                        # anchor's process must be untouched
    r.resync_to_stint(3)
    assert r.B.idx == b_idx_before                  # anchor (B) NOT moved -> no cut
    assert r.B.proc is b_proc_before
    assert r.on_air_row_idx() == 2                  # display stint 3
    assert r.live_feed() == "B"                     # B is now the lower-or-equal? -> serving anchor
    assert r.A.idx > r.B.idx                        # A moved forward off the low idx
    assert r.A.current_channel()[0] != "uC"         # A not duplicating B's stream


def t_resync_to_stint_no_op_when_no_feed_serves():
    # No feed serves stint 4's URL -> a director-tier resync must NOT perform the
    # producer-gated set_stint cut; it returns an error and mutates nothing.
    rows = [("uA", "A", "S1", 1), ("uB", "B", "S2", 2),
            ("uC", "C", "S3", 3), ("uD", "D", "S4", 4)]
    r = m.Relay(_StubSource(["uA", "uB", "uC", "uD"], rows), (53001, 53002), LOGDIR)
    r._reflect = lambda live, cut: None
    r.A.set_index(0); r.B.set_index(1)
    for f in r.feeds.values(): f.phase = "idle"     # nothing serving uD
    a_before, b_before = r.A.idx, r.B.idx
    res = r.resync_to_stint(4)
    assert "error" in res                            # no takeover from a director-tier resync
    assert (r.A.idx, r.B.idx) == (a_before, b_before)   # nothing moved (no set_stint cut)


def t_manual_feed_arm_enabled():
    # Default-ON now: absent/empty ⇒ manual arm on.
    assert m.manual_feed_arm_enabled({}) is True
    assert m.manual_feed_arm_enabled({"RACECAST_MANUAL_FEED_ARM": ""}) is True
    for v in ("1", "true", "yes", "on", "TRUE", "On"):
        assert m.manual_feed_arm_enabled({"RACECAST_MANUAL_FEED_ARM": v}) is True, v
    for v in ("0", "false", "no", "off", "OFF", "No"):
        assert m.manual_feed_arm_enabled({"RACECAST_MANUAL_FEED_ARM": v}) is False, v


def t_relay_manual_arm_starts_feeds_disarmed():
    rows = [("uA", "A", "S1", 1), ("uB", "B", "S2", 2)]
    entry = os.environ.get("RACECAST_MANUAL_FEED_ARM")   # capture true entry state
    try:
        # Opt-out (flag "0"): legacy auto-pull, feeds armed.
        os.environ["RACECAST_MANUAL_FEED_ARM"] = "0"
        r0 = m.Relay(_StubSource(["uA", "uB"], rows), (53001, 53002), LOGDIR)
        assert r0.manual_feed_arm is False
        assert r0.A.paused is False and r0.B.paused is False
        assert r0.status()["feeds"]["A"]["armed"] is True
        # New DEFAULT (flag absent): manual arm on, feeds disarmed.
        os.environ.pop("RACECAST_MANUAL_FEED_ARM", None)
        rd = m.Relay(_StubSource(["uA", "uB"], rows), (53005, 53006), LOGDIR)
        assert rd.manual_feed_arm is True
        assert rd.A.paused is True and rd.B.paused is True
        assert rd.status()["manual_feed_arm"] is True
        assert rd.status()["feeds"]["A"]["armed"] is False
        # Explicit "1": same as default (disarmed).
        os.environ["RACECAST_MANUAL_FEED_ARM"] = "1"
        r2 = m.Relay(_StubSource(["uA", "uB"], rows), (53003, 53004), LOGDIR)
        assert r2.manual_feed_arm is True and r2.A.paused is True
    finally:
        # Restore the exact entry state (symmetric — not the post-del state).
        if entry is None:
            os.environ.pop("RACECAST_MANUAL_FEED_ARM", None)
        else:
            os.environ["RACECAST_MANUAL_FEED_ARM"] = entry


def t_feed_activate_deactivate_manual_mode():
    rows = [("uA", "A", "S1", 1), ("uB", "B", "S2", 2)]
    r = m.Relay(_StubSource(["uA", "uB"], rows), (53001, 53002), LOGDIR)
    r._reflect = lambda live, cut: None
    # Force manual mode + disarmed, as Relay.__init__ would with the flag on.
    r.manual_feed_arm = True
    r.A.paused = True; r.B.paused = True
    # URL present at the index but paused -> NO pull (current_channel gates on paused).
    assert r.A.current_channel() == (None, 0)
    # Arm A: unpaused; the URL is now pullable.
    r.feed_activate("A")
    assert r.A.paused is False
    assert r.A.current_channel() == ("uA", 0)
    # A deactivated feed reports "stopped", never "down" (no health alarm).
    r.feed_deactivate("A")
    assert r.A.paused is True
    fa = r.status()["feeds"]["A"]
    assert fa["state"] == "stopped" and fa["down"] is False and fa["armed"] is False


def t_feed_arm_disabled_in_auto_mode():
    rows = [("uA", "A", "S1", 1), ("uB", "B", "S2", 2)]
    r = m.Relay(_StubSource(["uA", "uB"], rows), (53001, 53002), LOGDIR)
    assert r.manual_feed_arm is False
    before = r.A.paused
    res = r.feed_activate("A")
    assert "error" in res
    assert r.A.paused == before          # mutated nothing
    assert "error" in r.feed_deactivate("B")


def t_feed_arm_unknown_feed():
    rows = [("uA", "A", "S1", 1)]
    r = m.Relay(_StubSource(["uA"], rows), (53001, 53002), LOGDIR)
    r.manual_feed_arm = True
    assert "error" in r.feed_activate("Z")


def t_desync_suppressed_in_manual_mode():
    rows = [("uA", "A", "S1", 1), ("uB", "B", "S2", 2),
            ("uC", "C", "S3", 3), ("uD", "D", "S4", 4)]
    r = m.Relay(_StubSource(["uA", "uB", "uC", "uD"], rows), (53001, 53002), LOGDIR)
    r._reflect = lambda live, cut: None
    # Construct a would-be desync: on-air feed A dropped, off-air feed B serving,
    # past the settle window — in AUTO mode this fires the desync flag.
    r.A.phase = "connecting"; r.A.dropped = True
    r.B.phase = "serving"; r.B.dropped = False
    r._desync_since = time.time() - 20
    assert r.status()["desync"]["active"] is True          # auto mode: fires
    # Manual mode: the same feed state must NOT raise a desync (intentional disarm).
    r.manual_feed_arm = True
    assert r.status()["desync"]["active"] is False


def t_feed_activate_refuses_same_url_as_other_armed_feed():
    # Both feed indices resolve to the SAME url; arming the second while the first
    # is armed on that url must be refused (no #491 double-pull on the arm path).
    rows = [("uX", "A", "S1", 1), ("uX", "B", "S2", 2)]
    r = m.Relay(_StubSource(["uX", "uX"], rows), (53001, 53002), LOGDIR)
    r._reflect = lambda live, cut: None
    r.manual_feed_arm = True
    r.A.paused = True; r.B.paused = True
    r.A.set_index(0); r.B.set_index(1)          # both resolve to uX
    r.feed_activate("A")                          # A armed on uX
    assert r.A.paused is False
    res = r.feed_activate("B")                    # would be a 2nd uX puller -> refuse
    assert "error" in res
    assert r.B.paused is True                     # B stayed disarmed


def t_quality_ytdlp_fmt():
    assert m.quality_ytdlp_fmt("full") == "b[height<=1080]/b"
    assert m.quality_ytdlp_fmt("robust") == "b[height<=720]/b"
    assert m.quality_ytdlp_fmt("emergency") == "b[height<=480]/w"


def t_quality_twitch_selector():
    assert m.quality_twitch_selector("full") == "best"
    assert m.quality_twitch_selector("robust") == "720p60,720p"
    assert m.quality_twitch_selector("emergency") == "480p,360p,worst"


def t_streamlink_flags_per_tier():
    assert m.streamlink_serve_flags("full") == m.STREAMLINK_SERVE
    assert m.streamlink_serve_flags("robust") == m.STREAMLINK_SERVE_ROBUST
    assert m.streamlink_serve_flags("emergency") == m.STREAMLINK_SERVE_ROBUST
    assert m.streamlink_twitch_flags("full") == m.STREAMLINK_TWITCH
    assert m.streamlink_twitch_flags("robust") == m.STREAMLINK_TWITCH_ROBUST


def t_parse_quality_tier():
    for v in ("full", "robust", "emergency", "auto"):
        assert m.parse_quality_tier(v) == v
    assert m.parse_quality_tier("FULL") == "full"       # case-insensitive
    assert m.parse_quality_tier("  robust ") == "robust"
    assert m.parse_quality_tier("1080p") is None
    assert m.parse_quality_tier("") is None
    assert m.parse_quality_tier(None) is None


def t_quality_height():
    assert m.quality_height("720p60") == 720
    assert m.quality_height("1080p") == 1080
    assert m.quality_height("480p") == 480
    assert m.quality_height("best") is None
    assert m.quality_height("audio_only") is None
    assert m.quality_height(None) is None


def t_quality_step_down_due():
    # fires: unpinned, FULL, live-but-degraded, enough dead serves
    assert m.quality_step_down_due("full", False, 2, None) is True
    assert m.quality_step_down_due("full", False, 5, None) is True
    # not yet enough dead serves
    assert m.quality_step_down_due("full", False, 1, None) is False
    # pinned suppresses auto
    assert m.quality_step_down_due("full", True, 9, None) is False
    # already below full -> never auto-descend further
    assert m.quality_step_down_due("robust", False, 9, None) is False
    assert m.quality_step_down_due("emergency", False, 9, None) is False
    # offline / ended source -> stepping quality cannot help
    assert m.quality_step_down_due("full", False, 9, "not_live_yet") is False
    assert m.quality_step_down_due("full", False, 9, "ended") is False


def t_streamlink_serve_cmd_tier():
    # YouTube robust: robust flags, positional stays "best" (yt-dlp already capped the rendition)
    yt = m.streamlink_serve_cmd("http://h/x.m3u8", 53001, "youtube", tier="robust")
    assert "128M" in yt and yt[-1] == "best"
    # Twitch robust: robust flags + the capped quality positional
    tw = m.streamlink_serve_cmd("https://twitch.tv/x", 53001, "twitch", tier="robust")
    assert "128M" in tw and tw[-1] == "720p60,720p"
    # Twitch full: unchanged default
    tw_full = m.streamlink_serve_cmd("https://twitch.tv/x", 53001, "twitch")
    assert tw_full[-1] == "best"


def t_streamlink_fanout_cmd_tier():
    tw = m.streamlink_fanout_cmd("https://twitch.tv/x", "twitch", tier="emergency")
    assert "128M" in tw and tw[-1] == "480p,360p,worst"


def _mk_feed():
    return m.Feed("A", 53001, 0, provider=lambda: [], logdir=tempfile.mkdtemp())


def t_feed_quality_defaults():
    f = _mk_feed()
    assert f.quality_tier == "full" and f.quality_pinned is False


def t_feed_set_quality_pins():
    f = _mk_feed()
    f.set_quality("robust", True)
    assert f.quality_tier == "robust" and f.quality_pinned is True


def t_feed_maybe_step_down_fires_once():
    f = _mk_feed()
    f.dead_serves = 2
    assert f.maybe_step_down() == ("full", "robust")
    assert f.quality_tier == "robust" and f.quality_pinned is False
    # already robust -> no further auto step
    f.dead_serves = 9
    assert f.maybe_step_down() is None


def t_feed_maybe_step_down_respects_pin_and_state():
    f = _mk_feed(); f.set_quality("full", True); f.dead_serves = 9
    assert f.maybe_step_down() is None            # pinned
    g = _mk_feed(); g.dead_serves = 9; g.source_state = "ended"
    assert g.maybe_step_down() is None            # offline/ended


def t_feed_new_source_resets_quality():
    # _mk_feed's schedule is empty, so set_index clamps to the same idle idx (0->0)
    # and short-circuits before the reset; use a real multi-stint schedule so the
    # index actually moves and the managed-state reset fires.
    f = m.Feed("A", 53001, 0, provider=lambda: ["a", "b", "c", "d", "e"],
               logdir=tempfile.mkdtemp())
    f.set_quality("emergency", True)
    f.set_index(4)
    assert f.quality_tier == "full" and f.quality_pinned is False


def t_set_feed_quality_applies_and_releases():
    r = _relay(["a", "b"])
    res = r.set_feed_quality("A", "emergency")
    assert r.A.quality_tier == "emergency" and r.A.quality_pinned is True
    assert res == {"feed": "A", "profile": "emergency", "pinned": True}
    res = r.set_feed_quality("A", "auto")               # release back to managed FULL
    assert r.A.quality_tier == "full" and r.A.quality_pinned is False
    assert res == {"feed": "A", "profile": "full", "pinned": False}
    before_tier, before_pinned = r.A.quality_tier, r.A.quality_pinned
    res = r.set_feed_quality("A", "bogus")
    assert "error" in res
    assert r.A.quality_tier == before_tier and r.A.quality_pinned == before_pinned
    assert "error" in r.set_feed_quality("Z", "robust")  # unknown feed


def t_discord_step_down_payload():
    p = m.discord_step_down_payload("A", 3, "full", "robust",
                                     event_title="N24", producer="Box")
    assert p["content"] == "@here"                       # actionable
    assert p["allowed_mentions"]["parse"] == ["everyone"]
    body = json.dumps(p)
    assert "robust" in body.lower() and "Feed A" in body


def t_record_feed_step_down_records_event_and_pings():
    # Auto step-down is ALWAYS actionable (unlike drop-recovery churn): every call
    # records a feed_step_down health event AND fires a Discord @here.
    r = _relay(["a", "b"])
    r.health_store = _FakeHS()
    r._event_title = lambda: ""
    posts = []
    r._discord_post = lambda payload, what: posts.append((what, payload))
    r._record_feed_step_down("A", 3, "full", "robust")
    rows = [e for e in r.health_store.rows if e["type"] == "feed_step_down"]
    assert len(rows) == 1
    assert rows[0]["metadata"] == {"feed": "A", "stint": 3, "from": "full", "to": "robust"}
    assert len(posts) == 1 and posts[0][0] == "feed-step-down"
    assert posts[0][1]["content"] == "@here"


def t_redact_console_status_role_gates_feed_urls():
    # #493: the Preview button needs feed URLs over the Funnel — director/producer keep
    # feeds[*].channel (+ pov.url + sheet_id); every other role has them stripped.
    full = {"feeds": {"A": {"channel": "https://youtube.com/live/x", "stint": 1,
                            "profile": "full", "pinned": False},
                      "B": {"channel": "https://twitch.tv/y", "stint": 2}},
            "pov": {"url": "https://youtube.com/live/p", "shown": True},
            "league": {"sheet_id": "SHEET123", "name": "Demo"}}
    d = m.redact_console_status(full, ["director"])
    assert d["feeds"]["A"]["channel"] == "https://youtube.com/live/x"
    assert d["feeds"]["B"]["channel"] == "https://twitch.tv/y"
    assert d["pov"]["url"] == "https://youtube.com/live/p"
    assert d["league"]["sheet_id"] == "SHEET123"
    assert m.redact_console_status(full, ["producer"])["feeds"]["A"]["channel"]
    for roles in (["commentator"], ["race_control"], []):
        c = m.redact_console_status(full, roles)
        assert "channel" not in c["feeds"]["A"], roles
        assert "channel" not in c["feeds"]["B"], roles
        assert c["feeds"]["A"]["stint"] == 1 and c["feeds"]["A"]["profile"] == "full"  # non-URL kept
        assert "url" not in c["pov"] and c["pov"]["shown"] is True
        assert "sheet_id" not in c["league"] and c["league"]["name"] == "Demo"


def t_auto_cover_enabled_default_on_optout():
    assert m.auto_cover_enabled({}) is True
    assert m.auto_cover_enabled({"RACECAST_OBS_AUTO_COVER": "1"}) is True
    assert m.auto_cover_enabled({"RACECAST_OBS_AUTO_COVER": "0"}) is False
    assert m.auto_cover_enabled({"RACECAST_OBS_AUTO_COVER": "off"}) is False


def t_auto_cover_action_raises_on_offline_past_settle():
    # ended source, offline 20s (> settle 12), cover hidden, not fired, on Stint -> raise
    assert m.auto_cover_action(True, "ended", 100.0, 120.0, 12,
                               False, False, False, "Stint") == "raise"
    # not_live_yet raises identically
    assert m.auto_cover_action(True, "not_live_yet", 100.0, 120.0, 12,
                               False, False, False, "Stint") == "raise"


def t_auto_cover_action_waits_for_settle():
    # offline only 5s (< settle 12) -> no raise yet
    assert m.auto_cover_action(True, "ended", 100.0, 105.0, 12,
                               False, False, False, "Stint") is None


def t_auto_cover_action_fires_once_per_outage():
    # already fired this outage -> no re-raise (even though still offline & hidden)
    assert m.auto_cover_action(True, "ended", 100.0, 200.0, 12,
                               False, False, True, "Stint") is None


def t_auto_cover_action_skips_when_cover_already_shown():
    # a cover is already up (manual) -> pure fn does not raise
    assert m.auto_cover_action(True, "ended", 100.0, 200.0, 12,
                               True, False, False, "Stint") is None


def t_auto_cover_action_scene_guard():
    # OBS is on Intermission (not the on-air scene) -> never raise
    assert m.auto_cover_action(True, "ended", 100.0, 200.0, 12,
                               False, False, False, "Intermission") is None


def t_auto_cover_action_disabled_never_raises():
    assert m.auto_cover_action(False, "ended", 100.0, 200.0, 12,
                               False, False, False, "Stint") is None


def t_auto_cover_action_lowers_owned_cover_on_recovery():
    # source recovered (None), cover shown, auto owns it -> lower
    assert m.auto_cover_action(True, None, None, 300.0, 12,
                               True, True, True, "Stint") == "lower"


def t_auto_cover_action_lowers_even_when_disabled():
    # cleanup: flag flipped off mid-outage must not strand an auto-owned cover
    assert m.auto_cover_action(False, None, None, 300.0, 12,
                               True, True, True, "Stint") == "lower"


def t_auto_cover_action_never_lowers_manual_cover():
    # cover shown but auto does NOT own it (director raised it) -> no lower
    assert m.auto_cover_action(True, None, None, 300.0, 12,
                               True, False, False, "Stint") is None


def t_feed_offline_since_stamps_and_clears():
    f = m.Feed("A", 53001, 0, lambda: ["a"], LOGDIR)
    assert f.offline_since is None
    f._set_source_state("not_live_yet")
    t0 = f.offline_since
    assert t0 is not None and f.source_state == "not_live_yet"
    f._set_source_state("ended")          # same outage -> keep the original stamp
    assert f.offline_since == t0 and f.source_state == "ended"
    f._set_source_state(None)             # recovered -> cleared
    assert f.offline_since is None and f.source_state is None


def t_status_exposes_auto_cover_active():
    r = _relay(["a", "b"])
    assert r.status()["auto_cover_active"] is False
    r._cover_auto_owned = True
    assert r.status()["auto_cover_active"] is True


def t_maybe_auto_cover_no_obs_is_noop():
    # Best-effort: with no obs-ws bound the tick must return without raising and
    # must not falsely latch the outage flags.
    saved = m._obs_ws
    m._obs_ws = None
    try:
        r = _relay(["a", "b"])
        r.A._set_source_state("ended")
        r.A.offline_since = 100.0
        r._maybe_auto_cover(200.0)          # 100s offline, but no OBS -> no-op
        assert r._cover_fired is False
        assert r._cover_auto_owned is False
    finally:
        m._obs_ws = saved


class _FakeObsWs:
    """Fake _obs_ws for auto-cover tests: tracks a single Standby-Cover visibility flag."""
    STINT_SCENE = "Stint"

    def __init__(self, cover=False, scene="Stint"):
        self.cover = cover
        self.scene = scene

    def read_obs_state(self, sources, inputs):
        state = {"scene": self.scene,
                  "sources": [{"scene": s, "source": src, "enabled": self.cover}
                              for (s, src) in sources]}
        return state, ""

    def set_scene_item_enabled(self, scene, source, enabled):
        self.cover = enabled
        return True, ""


def t_maybe_auto_cover_raise_then_recovery_lowers():
    saved = m._obs_ws
    try:
        fake = _FakeObsWs(cover=False)
        m._obs_ws = fake
        r = _relay(["a", "b"])
        r.A._set_source_state("ended")
        r.A.offline_since = 900.0
        r._maybe_auto_cover(1000.0)
        assert fake.cover is True
        assert r._cover_fired is True
        assert r._cover_auto_owned is True

        r.A._set_source_state(None)             # source recovered (clears offline_since)
        r._maybe_auto_cover(1005.0)
        assert fake.cover is False
        assert r._cover_auto_owned is False
        assert r._cover_fired is False
    finally:
        m._obs_ws = saved


def t_maybe_auto_cover_manual_lower_not_re_raised():
    saved = m._obs_ws
    try:
        fake = _FakeObsWs(cover=False)
        m._obs_ws = fake
        r = _relay(["a", "b"])
        r.A._set_source_state("ended")
        r.A.offline_since = 900.0
        r._maybe_auto_cover(1000.0)              # raise: cover up, owned
        assert fake.cover is True
        assert r._cover_fired is True
        assert r._cover_auto_owned is True

        fake.cover = False                       # director manually lowers mid-outage
        r._maybe_auto_cover(1010.0)               # source still offline
        assert fake.cover is False                # NOT re-raised
        assert r._cover_fired is True

        r.A._set_source_state(None)               # recovery
        r._maybe_auto_cover(1015.0)
        assert r._cover_auto_owned is False
        assert r._cover_fired is False
    finally:
        m._obs_ws = saved


def t_maybe_auto_cover_never_lowers_manual_cover():
    saved = m._obs_ws
    try:
        fake = _FakeObsWs(cover=True)             # director raised it on a healthy source
        m._obs_ws = fake
        r = _relay(["a", "b"])
        assert r.A.source_state is None
        assert r._cover_auto_owned is False
        r._maybe_auto_cover(1000.0)
        assert fake.cover is True                 # auto did NOT lower a manually-raised cover
        assert r._cover_auto_owned is False
    finally:
        m._obs_ws = saved


# --- #592: the `local:` schedule token (a capture device as a feed) ---

def t_local_source_token_predicates():
    for v in ("local:", " LOCAL: ", "Local:"):
        assert m.is_local_source(v) is True, v
        assert m.is_feed_source(v) is True, v
    for v in ("local", "local:elgato", "local:/dev/video0", "", None, "https://youtu.be/x"):
        assert m.is_local_source(v) is False, v
    assert m.is_feed_source("https://youtu.be/x") is True
    # The commentator submit + POV guards stay on is_channel: a commentator must never
    # be able to point a feed at the producer's capture card.
    assert m.is_channel("local:") is False


def t_local_token_routes_to_the_local_platform():
    assert m.feed_url(" Local: ") == "local:"         # never wrapped into a YouTube URL
    assert m.feed_url("UCabcdefghijklmnopqrstuv") == m.channel_url("UCabcdefghijklmnopqrstuv")
    assert m.feed_platform(m.feed_url("local:")) == "local"
    assert m.feed_platform("https://www.twitch.tv/x") == "twitch"
    assert m.feed_platform("https://youtu.be/x") == "youtube"


def t_schedule_parse_keeps_and_normalises_local_rows():
    text = ("URL,Streamer,Stint\n"
            "https://youtu.be/a,Alice,Stint 1\n"
            "LOCAL:,Jens,Stint 2\n"
            "local:,Jens,Stint 3\n"
            "https://youtu.be/b,Bob,Stint 4\n")
    rows = m.ScheduleSource._parse_rows(text)
    assert [r[0] for r in rows] == ["https://youtu.be/a", "local:", "local:", "https://youtu.be/b"]
    # two back-to-back local stints are ONE continuous capture (one slot)
    assert m.pull_slots(rows) == [0, 1, 1, 2]


def t_schedule_parse_positional_counts_local_rows():
    rows = m.ScheduleSource._parse_rows("local:,Jens\nhttps://youtu.be/a,Alice\n")
    assert [(r[0], r[1]) for r in rows] == [("local:", "Jens"), ("https://youtu.be/a", "Alice")]


def t_pov_source_never_accepts_local():
    # The POV tab reads through ScheduleSource too; it must not name the capture card,
    # or a POV pull would take it from the on-air A/B feed.
    text = "URL,Streamer,Stint\nlocal:,Jens,\nhttps://youtu.be/p,Bob,\n"
    rows = m.ScheduleSource._parse_rows(text, allow_local=False)
    assert [r[0] for r in rows] == ["", "https://youtu.be/p"]      # kept as not-yet-filled
    assert m.ScheduleSource._parse_rows("local:,Jens\n", allow_local=False) is None
    pov = m.ScheduleSource("http://pov", os.path.join(LOGDIR, "pov-local.txt"), None,
                           allow_local=False)
    assert pov.inject_row(2, url="local:") is False

def t_relay_builds_the_pov_source_without_local():
    # The wiring main() uses: the POV tab never names the capture card, the
    # qualifying tab (same structure as the Schedule) may.
    pov = m.pov_schedule_source("SHEETID", "POV", LOGDIR)
    assert pov.allow_local is False
    assert "sheet=POV" in pov.csv_url and pov.cache_path.endswith("pov.cache.txt")
    qual = m.qualifying_schedule_source("SHEETID", "Qualifying", LOGDIR)
    assert qual.allow_local is True
    assert "sheet=Qualifying" in qual.csv_url

def t_inject_row_accepts_local_and_normalises():
    s = m.ScheduleSource("http://sched", os.path.join(LOGDIR, "sched-local.txt"), None)
    assert s.inject_row(3, url="Local:", name="Jens") is True
    assert s.get_rows() == [("local:", "Jens", "", 3)]
    assert s.inject_row(4, url="file:///etc/passwd") is False


def _local_feed(sched):
    return m.Feed("A", 53001, 0, lambda: list(sched), LOGDIR)


def t_local_feed_never_steps_down_quality():
    # A device-busy fast exit counts as a dead serve; the #493 FULL->ROBUST step-down is
    # meaningless for a capture card and would page a nonsense @here.
    f = _local_feed(["local:"])
    f.dead_serves = 99
    assert f.maybe_step_down() is None and f.quality_tier == "full"
    g = _local_feed(["https://youtu.be/x"])
    g.dead_serves = 99
    assert g.maybe_step_down() == ("full", "robust")


def t_local_feed_quality_click_does_not_restart_the_capture():
    # A tier click on a local feed would restart ffmpeg (a black gap on air) for a
    # setting a capture card ignores; the tier is kept for the next remote stint.
    f = _local_feed(["local:"])
    f.set_quality("robust", True)
    assert not f.advance.is_set()
    assert (f.quality_tier, f.quality_pinned) == ("robust", True)
    g = _local_feed(["https://youtu.be/x"])
    g.set_quality("robust", True)
    assert g.advance.is_set()                       # a remote feed re-resolves at once

def _run_feed_until(f, cond, timeout=5.0):
    t = threading.Thread(target=f.run, daemon=True)
    t.start()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not cond():
        time.sleep(0.02)
    f.stop = True
    f.advance.set()
    t.join(timeout=3)
    return cond()


def t_local_feed_refuses_direct_serve():
    # Without fan-out (ring None) there is no stdout reader: the feed must idle with a
    # clear reason, never hand `local:` to streamlink or yt-dlp.
    f = _local_feed(["local:"])
    spawned = []
    orig = m.subprocess.Popen
    m.subprocess.Popen = lambda *a, **k: spawned.append(a) or (_ for _ in ()).throw(AssertionError("spawned"))
    try:
        ok = _run_feed_until(f, lambda: f.phase == "idle" and f.last_error)
    finally:
        m.subprocess.Popen = orig
    assert ok, (f.phase, f.last_error)
    assert "RACECAST_FEED_FANOUT" in f.last_error and spawned == []


class _FakeProc:
    def __init__(self, cmd, payload=b"\x47" * 188 * 4):
        import io as _io
        self.cmd = cmd
        self.stdout = _io.BufferedReader(_io.BytesIO(payload))
        self.stderr = _io.BytesIO(b"")
        self.returncode = 0
    def poll(self): return self.returncode
    def terminate(self): pass
    def kill(self): pass
    def wait(self, timeout=None): return 0


def t_local_feed_serves_ffmpeg_into_the_ring():
    f = _local_feed(["local:"])
    f.ring = m.FeedRing(m.FANOUT_RING_BYTES)
    f.quality = "1080p60"                     # left over from a previous remote stint
    cmds = []
    orig_popen, orig_env = m.subprocess.Popen, dict(os.environ)
    m.subprocess.Popen = lambda cmd, **k: cmds.append(cmd) or _FakeProc(cmd)
    os.environ["RACECAST_CAPTURE"] = "/dev/video9"
    os.environ.pop("RACECAST_CAPTURE_AUDIO", None)
    orig_enc, m._LOCAL_ENCODER = m._LOCAL_ENCODER, "x264"
    orig_scan = m.scan_capture_audio
    m.scan_capture_audio = lambda platform, video: ("Card Audio", None)
    try:
        ok = _run_feed_until(f, lambda: f.ring.live_offset() > 0)
    finally:
        m.subprocess.Popen = orig_popen
        m._LOCAL_ENCODER = orig_enc
        m.scan_capture_audio = orig_scan
        os.environ.clear(); os.environ.update(orig_env)
    assert ok
    assert cmds and cmds[0][0] == "ffmpeg" and cmds[0][-3:] == ["-f", "mpegts", "-"]
    assert all(c[0] == "ffmpeg" for c in cmds)       # no yt-dlp / streamlink hop
    assert f.quality is None                         # no stale remote resolution shown



def t_local_feed_idles_with_the_device_hint():
    # Five immediate ffmpeg exits (card held by another program) idle the feed; the
    # paused message must still name the device, not just "source unavailable".
    f = _local_feed(["local:"])
    f.ring = m.FeedRing(m.FANOUT_RING_BYTES)

    def busy(cmd, **k):
        p = _FakeProc(cmd, payload=b"")
        p.returncode = 1
        return p
    orig = (m.subprocess.Popen, m.dead_serve_backoff, m._LOCAL_ENCODER, dict(os.environ))
    m.subprocess.Popen, m.dead_serve_backoff, m._LOCAL_ENCODER = busy, (lambda n: 0), "x264"
    orig_scan, m.scan_capture_audio = m.scan_capture_audio, (lambda platform, video: (None, None))
    os.environ["RACECAST_CAPTURE"] = "/dev/video9"
    try:
        ok = _run_feed_until(f, lambda: f.phase == "idle" and "paused" in (f.last_error or ""))
    finally:
        m.subprocess.Popen, m.dead_serve_backoff, m._LOCAL_ENCODER = orig[:3]
        m.scan_capture_audio = orig_scan
        os.environ.clear(); os.environ.update(orig[3])
    assert ok, (f.phase, f.last_error)
    assert m.LOCAL_DEVICE_BUSY in f.last_error, f.last_error


# --- #593: the commentary mic follows the on-air local slot ---
MIC = "Commentary Mic Device"


def t_obs_audio_plan_gives_the_mic_to_the_local_feed():
    r = _relay(["https://youtu.be/a", "local:", "https://youtu.be/c"])
    orig = dict(os.environ)
    os.environ["RACECAST_CAPTURE"] = "/dev/video9"
    try:
        audio, extra = r.obs_audio_plan()
    finally:
        os.environ.clear(); os.environ.update(orig)
    assert audio == {"A": ["Feed A"], "B": ["Feed B", MIC]}, audio
    assert extra == [MIC]


def t_obs_audio_plan_leaves_the_mic_alone_without_a_capture_card():
    r = _relay(["https://youtu.be/a", "local:"])
    orig = dict(os.environ)
    os.environ.pop("RACECAST_CAPTURE", None)
    try:
        audio, extra = r.obs_audio_plan()
    finally:
        os.environ.clear(); os.environ.update(orig)
    assert audio == {"A": ["Feed A"], "B": ["Feed B"]} and extra == [], (audio, extra)


def t_reflect_snapshots_the_mic_before_the_freed_feed_advances():
    # Stint 1 is local on A. At the handover the freed feed A is re-indexed to
    # stint 3 right after _reflect; the OBS thread, started only once next_auto()
    # has returned, must still see A as the local slot it just cut away from.
    r = m.Relay(_StubSource(["local:", "https://youtu.be/b", "https://youtu.be/c"]),
                (53001, 53002), LOGDIR)
    r._reflect_pov = lambda shown: None
    seen, deferred = [], []

    class FakeObs:
        def reflect_feed_state(self, live, cut, audio=None, extra_mute=()):
            seen.append((live, cut, audio, list(extra_mute)))
            return [], ""

    class _Deferred:                        # holds _reflect's thread until we run it
        def __init__(self, target=None, daemon=None, **_k):
            self.target = target
        def start(self):
            deferred.append(self.target)

    class _Threading:
        Thread = _Deferred
        def __getattr__(self, name):
            return getattr(threading, name)

    orig_obs, orig_thr, orig_env = m._obs_ws, m.threading, dict(os.environ)
    m._obs_ws = FakeObs()
    os.environ["RACECAST_CAPTURE"] = "/dev/video9"
    try:
        r.feeds["B"].phase = "serving"
        m.threading = _Threading()
        r.next_auto()
        m.threading = orig_thr
        assert r.A.current_channel()[0] != "local:"   # A has already moved on
        for run in deferred:
            run()
    finally:
        m._obs_ws, m.threading = orig_obs, orig_thr
        os.environ.clear(); os.environ.update(orig_env)
    live, cut, audio, extra = seen[0]
    assert (live, cut) == ("B", True)
    assert audio == {"A": ["Feed A", MIC], "B": ["Feed B"]}, audio
    assert m._OBS_WS_MODULE.feed_state_intents(live, cut, audio=audio, extra_mute=extra)[3:5] \
        == [("mute", "Feed A"), ("mute", MIC)]

if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            # Re-pin the legacy-path guard before each test: some tests temporarily
            # mutate RACECAST_MANUAL_FEED_ARM to exercise the absent/default and the
            # explicit-value cases (they restore their own entry state in a finally).
            # This setdefault is a defensive net — it only re-adds the guard when the
            # var is actually missing, so it never clobbers a test that set an
            # explicit value on purpose.
            os.environ.setdefault("RACECAST_MANUAL_FEED_ARM", "0")
            fn(); print("ok", name)
    print("ALL PASS")
