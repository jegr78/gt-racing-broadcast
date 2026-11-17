#!/usr/bin/env python3
"""The relay's timing host publisher (#1161).
Run: python3 tests/test_timing_publish.py"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "scripts"))
import timing_host as th  # noqa: E402
import timing_publish as tp  # noqa: E402

LINK_A = "linkAAAAAAAAAAAAAAAAAAAA"
LINK_B = "linkBBBBBBBBBBBBBBBBBBBB"
FALLBACK = "https://host.example.ts.net/console/timing?l=" + LINK_A


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class _Conn:
    def __init__(self, url, acks):
        self.url, self.sent, self.closed, self._acks = url, [], False, acks

    def send_text(self, text):
        if self.closed:
            raise ConnectionError("closed")
        self.sent.append(json.loads(text))

    def recv_text(self, timeout):
        if self._acks:
            return self._acks.pop(0)
        return None

    def close(self):
        self.closed = True


class _Log:
    def __init__(self):
        self.warnings = []

    def warning(self, msg, *a):
        self.warnings.append(msg % a if a else msg)

    def info(self, *a):
        pass


SETTINGS = th.Settings("https://h.example.workers.dev", "key")


class _Rig:
    def __init__(self, settings=SETTINGS, fail=None):
        self.clock, self.log = _Clock(), _Log()
        self.settings, self.link, self.served = settings, LINK_A, "hosted"
        self.conns, self.fail, self.acks = [], fail, []
        self.pub = tp.Publisher(lambda: self.settings, lambda: self.link,
                                lambda: (b'{"v":1}', 0.5), lambda: self.served,
                                lambda: FALLBACK, connect=self._connect, clock=self.clock,
                                wall=lambda: 86400 * 3 + self.clock.t, log=self.log)

    def _connect(self, url, headers, timeout):
        if self.fail:
            raise OSError(self.fail)
        assert headers == {"Authorization": "Bearer key"}, headers
        c = _Conn(url, self.acks)
        self.conns.append(c)
        return c

    def step(self, advance=0.0):
        self.clock.t += advance
        return self.pub.step()

    def states(self, conn):
        return [f for f in conn.sent if f["t"] == "state"]


def t_sends_one_state_per_tick():
    r = _Rig()
    r.step()
    r.step(2.0)
    seqs = [f["seq"] for f in r.states(r.conns[0])]
    assert seqs == [1, 2], seqs
    first = r.states(r.conns[0])[0]
    assert first["data"] == {"v": 1} and first["age_s"] == 0.5 and first["fallback"] == FALLBACK
    assert r.conns[0].url == th.publish_url("https://h.example.workers.dev", LINK_A)


def t_no_second_state_within_a_tick():
    r = _Rig()
    r.step()
    r.step(0.5)
    assert len(r.states(r.conns[0])) == 1


def t_no_settings_no_connection():
    r = _Rig(settings=None)
    r.step()
    assert r.conns == [] and r.pub.status()["configured"] is False


def t_ack_updates_status():
    r = _Rig()
    r.acks.append('{"t":"ack","seq":1,"viewers":7,"requests_today":30}')
    r.step()
    st = r.pub.status()
    assert st["connected"] and st["viewers"] == 7 and st["requests_today"] == 30, st


def t_ack_timeout_reconnects():
    r = _Rig()
    r.step()
    for _ in range(8):
        r.step(2.0)
    assert r.conns[0].closed, "15 s without an ack closes the connection"
    r.step(2.0)
    assert len(r.conns) == 2, "and the next step reconnects"


def t_link_change_ends_old_room():
    r = _Rig()
    r.step()
    r.link = LINK_B
    r.step(2.0)
    assert r.conns[0].sent[-1] == {"t": "end"} and r.conns[0].closed
    assert r.conns[1].url.endswith(f"/t/{LINK_B}/publish?v=1"), r.conns[1].url


def t_link_cleared_ends_room():
    r = _Rig()
    r.step()
    r.link = None
    r.step(2.0)
    r.step(2.0)
    assert r.conns[0].sent[-1] == {"t": "end"} and r.conns[0].closed
    assert len(r.conns) == 1, "no reconnect without a link"


def t_settings_removed_closes_without_end():
    r = _Rig()
    r.step()
    r.settings = None
    r.pub.reload()
    r.step(2.0)
    assert r.conns[0].closed and {"t": "end"} not in r.conns[0].sent, \
        "removing the host must not end the event's room"


def t_served_funnel_sends_moved_once_and_clears_on_return():
    r = _Rig()
    r.step()
    r.served = "funnel"
    r.step(2.0)
    r.step(2.0)
    moved = [f for f in r.conns[0].sent if f["t"] == "moved"]
    assert moved == [{"t": "moved", "to": None}, {"t": "moved", "to": FALLBACK}], \
        "a fresh connection clears any old redirect, the switch sends one"
    r.served = "hosted"
    r.step(2.0)
    moved = [f for f in r.conns[0].sent if f["t"] == "moved"]
    assert moved[-1] == {"t": "moved", "to": None}, "switching back clears the room's redirect"


def t_moved_resent_after_reconnect():
    r = _Rig()
    r.served = "funnel"
    r.step()
    r.conns[0].closed = True
    r.step(2.0)
    r.step(2.0)
    assert {"t": "moved", "to": FALLBACK} in r.conns[-1].sent, "a new connection repeats the switch"


def t_moved_waits_for_a_funnel_url():
    r = _Rig()
    url = [None]
    r.pub._fallback_fn = lambda: url[0]
    r.step()
    r.served = "funnel"
    r.step(2.0)
    assert not [f for f in r.conns[0].sent if f["t"] == "moved" and f["to"]], "no target, no move"
    url[0] = FALLBACK
    r.step(2.0)
    assert {"t": "moved", "to": FALLBACK} in r.conns[0].sent, "the move goes out once Funnel is known"


def t_refused_upgrades_name_the_fix():
    for status, words in (("410 Gone", "event timing --new"), ("401 Unauthorized", "timing-host deploy")):
        r = _Rig()
        r.fail = None
        r.pub._connect = lambda *a, s=status, **k: (_ for _ in ()).throw(
            ValueError(f"WebSocket upgrade refused: HTTP/1.1 {s}"))
        r.step()
        assert words in r.pub.status()["last_error"], (status, r.pub.status())


def t_a_replacing_publisher_backs_off_quietly():
    r = _Rig()
    r.step()

    def replaced(timeout):
        raise ConnectionError("closed by the peer: replaced by a newer publisher")
    for _ in range(3):
        r.conns[-1].recv_text = replaced
        r.step(100.0)
        r.step(100.0)
    assert len(r.log.warnings) == 1 and "another relay" in r.log.warnings[0], r.log.warnings
    assert r.pub._backoff == tp.BACKOFF_MAX_S


def t_connect_failure_backs_off_and_logs_once():
    r = _Rig(fail="name resolution failed")
    waits = [r.step(100.0) for _ in range(8)]
    assert waits == sorted(waits) and max(waits) == tp.BACKOFF_MAX_S, waits
    assert len(r.log.warnings) == 1, r.log.warnings
    assert r.pub.status()["last_error"], "the error is visible to the producer"


def t_send_failure_reconnects():
    r = _Rig()
    r.step()
    r.conns[0].closed = True
    r.step(2.0)
    r.step(2.0)
    assert len(r.conns) == 2


def t_msgs_today_counts_sent_frames_and_resets_daily():
    r = _Rig()
    r.step()
    r.step(2.0)
    assert r.pub.status()["msgs_today"] == 3, "moved:null on connect plus two states"
    r.step(86400.0)
    assert r.pub.status()["msgs_today"] == 1, "a new UTC day starts the count again"


def t_body_failure_skips_the_tick():
    r = _Rig()
    r.pub._body_fn = lambda: (_ for _ in ()).throw(RuntimeError("no snapshot"))
    r.step()
    assert r.states(r.conns[0]) == [] and not r.conns[0].closed


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
