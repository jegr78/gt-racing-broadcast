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
        self.warnings, self.infos = [], []

    def warning(self, msg, *a):
        self.warnings.append(msg % a if a else msg)

    def info(self, msg, *a):
        self.infos.append(msg % a if a else msg)


class _AckingConn(_Conn):
    """Acks every state it receives, like the room."""

    def __init__(self, url, acks):
        super().__init__(url, [])

    def send_text(self, text):
        super().send_text(text)
        frame = json.loads(text)
        if frame["t"] == "state":
            self._acks.append(json.dumps({"t": "ack", "seq": frame["seq"], "viewers": 1,
                                          "requests_today": 2}))


SETTINGS = th.Settings("https://h.example.workers.dev", "key")


class _Rig:
    def __init__(self, settings=SETTINGS, fail=None, conn=_Conn, brands=None):
        self.clock, self.log, self.conn_cls = _Clock(), _Log(), conn
        self.brands = brands if brands is not None else {}
        self.settings, self.link, self.served = settings, LINK_A, "hosted"
        self.conns, self.fail, self.acks = [], fail, []
        self.pub = tp.Publisher(lambda: self.settings, lambda: self.link,
                                lambda: (b'{"v":1}', 0.5), lambda: self.served,
                                lambda: FALLBACK, brands_fn=lambda: self.brands,
                                connect=self._connect, clock=self.clock,
                                wall=lambda: 86400 * 3 + self.clock.t, log=self.log)

    def _connect(self, url, headers, timeout):
        if self.fail:
            raise OSError(self.fail)
        assert headers == {"Authorization": "Bearer key"}, headers
        c = self.conn_cls(url, self.acks)
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


PNG = b"\x89PNG\r\n\x1a\n"


def _brands(conn):
    return [f["key"] for f in conn.sent if f["t"] == "brand"]


def t_brand_logos_follow_the_first_state_one_per_step():
    r = _Rig(brands={"bmw": PNG + b"1", "audi": PNG + b"2"})
    wait = r.step()
    kinds = [f["t"] for f in r.conns[0].sent]
    assert kinds.index("state") < kinds.index("brand"), f"viewers get timing before logos: {kinds}"
    assert _brands(r.conns[0]) == ["audi"] and wait < 1.0, \
        f"one logo per step, the next one soon after: {kinds}, {wait}"
    r.step(wait)
    r.step(2.0)
    assert _brands(r.conns[0]) == ["audi", "bmw"], "each logo once per connection"
    assert r.pub.status()["msgs_today"] == len(r.conns[0].sent), "the quota estimate counts every logo"


def t_no_logo_goes_before_the_first_state():
    r = _Rig(brands={"bmw": PNG + b"1"})
    r.pub._body_fn = lambda: 1 / 0
    r.step()
    r.step(0.2)
    assert _brands(r.conns[0]) == [], "a room without a state is not live and would drop the logo"


class _BrandFailConn(_AckingConn):
    attempts = 0

    def send_text(self, text):
        if json.loads(text)["t"] == "brand":
            _BrandFailConn.attempts += 1
            raise ConnectionError("uplink too slow")
        super().send_text(text)


def t_a_failed_logo_pauses_logos_while_states_flow():
    _BrandFailConn.attempts = 0
    r = _Rig(brands={"bmw": PNG + b"1"}, conn=_BrandFailConn)
    r.step()
    for _ in range(10):
        r.step(2.0)
    assert len(r.conns) == 2, f"one reconnect, then the logos wait: {len(r.conns)} connections"
    assert len(r.states(r.conns[-1])) >= 8, "the timing keeps flowing"
    assert _BrandFailConn.attempts == 1, "no logo upload loop on a slow uplink"
    r.step(tp.BRAND_PAUSE_S)
    assert _BrandFailConn.attempts == 2, "after the pause the logo is tried again"


def t_brand_logos_go_again_on_a_new_connection_or_a_change():
    r = _Rig(brands={"bmw": PNG + b"1"})
    r.step()
    r.brands = {"bmw": PNG + b"1b", "ford": PNG + b"3"}
    r.step(2.0)
    r.step(0.2)
    assert sorted(_brands(r.conns[0])) == ["bmw", "bmw", "ford"], \
        f"a changed logo and a new brand go out: {_brands(r.conns[0])}"
    r.conns[0].closed = True
    r.step(2.0)
    for _ in range(4):
        r.step(2.0)
    assert sorted(_brands(r.conns[-1])) == ["bmw", "ford"], "a new connection sends every logo again"


def t_body_failure_skips_the_tick():
    r = _Rig()
    r.pub._body_fn = lambda: (_ for _ in ()).throw(RuntimeError("no snapshot"))
    r.step()
    assert r.states(r.conns[0]) == [] and not r.conns[0].closed


def _replaced(timeout):
    raise ConnectionError("closed by the peer: replaced by a newer publisher")


def t_a_stable_connection_after_a_replacement_recovers():
    r = _Rig(conn=_AckingConn)
    r.step()
    r.conns[-1].recv_text = _replaced
    r.step(2.0)
    r.step(tp.BACKOFF_MAX_S)
    assert len(r.conns) == 2 and not r.conns[-1].closed, "reconnected"
    for _ in range(40):
        r.step(2.0)
    assert r.pub._backoff == 0.0, "a connection that held clears the replacement backoff"
    assert r.log.infos == ["timing host: connected"], r.log.infos
    r.fail = "host down"

    def gone(timeout):
        raise ConnectionError("connection closed")
    r.conns[-1].recv_text = gone
    assert r.step(2.0) == tp.BACKOFF_MIN_S, "a later failure starts the backoff from the bottom"
    assert any("closed" in w for w in r.log.warnings[1:]), r.log.warnings


def t_two_relays_with_acks_still_duel_quietly():
    r = _Rig(conn=_AckingConn)
    r.step()
    for _ in range(4):
        for _ in range(int(tp.BACKOFF_MAX_S / 2)):
            r.step(2.0)
        r.conns[-1].recv_text = _replaced
        r.step(2.0)
        r.step(tp.BACKOFF_MAX_S)
    assert len(r.log.warnings) == 1 and r.log.infos == [], (r.log.warnings, r.log.infos)
    assert r.pub._backoff == tp.BACKOFF_MAX_S


def t_link_change_while_disconnected_ends_the_old_room():
    r = _Rig()
    r.step()
    r.conns[0].closed = True
    r.step(2.0)                       # the send fails: disconnected, backing off
    r.link = LINK_B
    r.step(2.0)
    old = [c for c in r.conns[1:] if c.url.endswith(f"/t/{LINK_A}/publish?v=1")]
    assert old and old[0].sent == [{"t": "end"}] and old[0].closed, \
        [(c.url, c.sent) for c in r.conns]


def t_link_cleared_while_disconnected_ends_the_old_room_once():
    r = _Rig()
    r.step()
    r.conns[0].closed = True
    r.step(2.0)
    r.link = None
    r.step(2.0)
    r.step(2.0)
    ends = [c for c in r.conns[1:] if c.sent == [{"t": "end"}]]
    assert len(ends) == 1 and ends[0].url.endswith(f"/t/{LINK_A}/publish?v=1"), \
        [(c.url, c.sent) for c in r.conns]
    assert r.pub.status()["room"] is False


def t_the_room_stays_owed_while_its_end_is_on_the_way():
    r = _Rig()
    r.step()
    r.conns[0].closed = True
    r.step(2.0)
    r.link = None
    seen = []
    connect = r.pub._connect

    def watching(url, headers, timeout):
        seen.append(r.pub.status()["room"])
        return connect(url, headers, timeout=timeout)
    r.pub._connect = watching
    r.step(2.0)
    assert seen == [True], "event stop waits on status()['room'] until the end went out"
    assert r.pub.status()["room"] is False


def t_an_unreachable_old_room_is_tried_once():
    r = _Rig()
    r.step()
    r.conns[0].closed = True
    r.step(2.0)
    r.link = None
    r.fail = "host down"
    r.step(2.0)
    r.step(2.0)
    r.fail = None
    r.step(2.0)
    assert len(r.conns) == 1, "one bounded attempt; the room ends itself after 30 min"
    assert r.pub.status()["room"] is False


def t_status_shows_an_open_room():
    r = _Rig()
    r.step()
    assert r.pub.status()["room"] is True
    r.link = None
    r.step(2.0)
    assert r.pub.status()["room"] is False



def _ack(requests):
    return json.dumps({"t": "ack", "seq": 1, "viewers": 3, "requests_today": requests})


def _near_quota(r):
    r.acks.append(_ack(tp.QUOTA_GUARD_REQUESTS))
    r.step()
    r.step(2.0)


def t_a_nearly_used_quota_moves_hosted_viewers_to_funnel_once():
    r = _Rig()
    _near_quota(r)
    for _ in range(3):
        r.step(2.0)
    st = r.pub.status()
    assert st["quota_low"] is True, st
    moved = [f for f in r.conns[0].sent if f["t"] == "moved"]
    assert moved == [{"t": "moved", "to": None}, {"t": "moved", "to": FALLBACK}], moved
    quota = [w for w in r.log.warnings if "quota" in w]
    assert quota == ["timing host: daily request quota nearly used, viewers stay on Funnel"], \
        r.log.warnings


def t_the_quota_estimate_counts_the_relays_messages():
    r = _Rig()
    r.acks.append(_ack(tp.QUOTA_GUARD_REQUESTS - 1))
    r.step()
    for _ in range(20):
        r.step(2.0)
    assert r.pub.status()["quota_low"] is True, "requests_today plus messages / 20"


def t_a_nearly_used_quota_without_funnel_moves_nobody():
    r = _Rig()
    r.pub._fallback_fn = lambda: None
    _near_quota(r)
    assert r.pub.status()["quota_low"] is True
    assert not [f for f in r.conns[0].sent if f["t"] == "moved" and f["to"]], \
        "never move viewers to a Funnel that is off"


def t_the_quota_guard_resets_on_a_new_day():
    r = _Rig()
    _near_quota(r)
    r.step(86400.0)
    st = r.pub.status()
    assert st["quota_low"] is False, st
    assert r.conns[-1].sent and {"t": "moved", "to": None} in r.conns[-1].sent[-3:], \
        "the hosted link is served again"


def t_the_quota_guard_holds_across_a_renewed_link():
    r = _Rig()
    _near_quota(r)
    r.link = LINK_B
    r.step(2.0)
    r.acks.append(_ack(5))
    r.step(2.0)
    st = r.pub.status()
    assert st["quota_low"] is True, "the quota is per account: a new room does not free it"
    assert st["requests_today"] == tp.QUOTA_GUARD_REQUESTS + 5, st
    assert {"t": "moved", "to": FALLBACK} in r.conns[-1].sent, "the new room's viewers go to Funnel too"
    r.step(86400.0)
    assert r.pub.status()["quota_low"] is False, "only a new UTC day starts the count again"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
