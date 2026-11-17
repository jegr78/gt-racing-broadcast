"""The relay's publisher to the league's timing host (#1161).

One WebSocket per timing link: a state per tick, `end` when the link ends or changes,
`moved` when the producer switches the served path. A dead host never blocks the
caller: every socket call is bounded and failures back off up to BACKOFF_MAX_S.
"""
import logging
import threading
import time

import timing_host
import ws_client

TICK_S = 2.0
ACK_TIMEOUT_S = 15.0
BACKOFF_MIN_S = 2.0
BACKOFF_MAX_S = 30.0
SOCKET_TIMEOUT_S = 5.0
RECV_POLL_S = 0.05

LOG = logging.getLogger("racecast.timing_publish")


class Publisher:
    """settings_fn() -> timing_host.Settings | None; link_fn() -> link id | None;
    body_fn() -> (public JSON bytes, age_s); served_fn() -> "hosted" | "funnel";
    fallback_fn() -> Funnel page URL | None."""

    def __init__(self, settings_fn, link_fn, body_fn, served_fn, fallback_fn,
                 connect=ws_client.WsClient.connect, clock=time.monotonic, wall=time.time,
                 log=LOG):
        self._settings_fn, self._link_fn, self._body_fn = settings_fn, link_fn, body_fn
        self._served_fn, self._fallback_fn = served_fn, fallback_fn
        self._connect, self._clock, self._wall, self._log = connect, clock, wall, log
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._settings = None
        self._reload = True
        self._conn = None
        self._conn_link = None
        self._seq = 0
        self._last_sent = None
        self._last_ack = None
        self._moved = None
        self._backoff = 0.0
        self._next_attempt = 0.0
        self._last_error = None
        self._logged_error = None
        self._viewers = 0
        self._conns_today = 0
        self._msgs_today = 0
        self._day = None

    def reload(self):
        """Re-read the host settings on the next step and run it now."""
        self._reload = True
        self._wake.set()

    def host_url(self):
        """The configured host URL, or None without a host."""
        settings = self._settings
        return settings.url if settings is not None else None

    def status(self):
        with self._lock:
            now = self._clock()
            return {"configured": self._settings is not None,
                    "connected": self._conn is not None,
                    "last_ack_age_s": None if self._conn is None or self._last_ack is None
                    else round(now - self._last_ack, 1),
                    "viewers": self._viewers, "conns_today": self._conns_today,
                    "msgs_today": self._msgs_today, "last_error": self._last_error}

    def run(self, stop):
        while not stop.is_set():
            try:
                wait = self.step()
            except Exception as e:      # a publisher bug must never take the relay down
                self._error(f"publisher: {e!r}")
                wait = TICK_S
            self._wake.wait(wait)
            self._wake.clear()

    def step(self):
        """One iteration; returns the seconds until the next one is due."""
        if self._reload:
            self._reload = False
            settings = self._settings_fn()
            if settings != self._settings:
                self._close()
            with self._lock:
                self._settings = settings
        if self._settings is None:
            return TICK_S
        link = self._link_fn()
        if self._conn is not None and self._conn_link != link:
            self._send(timing_host.end_frame())
            self._close()
        if link is None:
            return TICK_S
        now = self._clock()
        if self._conn is None:
            if now < self._next_attempt:
                return self._next_attempt - now
            if not self._open(link, now):
                return self._backoff
        self._send_moved()
        if self._conn is not None and (self._last_sent is None or now - self._last_sent >= TICK_S):
            self._send_state(now)
        self._read_acks()
        if self._conn is not None and now - self._last_ack > ACK_TIMEOUT_S:
            self._close()
            self._fail(f"no ack from the timing host for {ACK_TIMEOUT_S:.0f} s", now)
        if self._conn is None:
            return max(BACKOFF_MIN_S, self._next_attempt - now)
        if self._last_sent is None:
            return TICK_S
        return max(0.1, self._last_sent + TICK_S - self._clock())

    def _open(self, link, now):
        url = timing_host.publish_url(self._settings.url, link)
        try:
            conn = self._connect(url, {"Authorization": f"Bearer {self._settings.key}"},
                                 timeout=SOCKET_TIMEOUT_S)
        except (OSError, ValueError) as e:
            self._fail(f"cannot reach the timing host: {e}", now)
            return False
        with self._lock:
            self._conn, self._conn_link = conn, link
            self._last_ack, self._last_sent, self._moved = now, None, None
            self._backoff, self._last_error = 0.0, None
        if self._logged_error:
            self._log.info("timing host: connected")
            self._logged_error = None
        return True

    def _fail(self, message, now):
        self._backoff = min(BACKOFF_MAX_S, max(BACKOFF_MIN_S, self._backoff * 2))
        self._next_attempt = now + self._backoff
        self._error(message)

    def _error(self, message):
        with self._lock:
            self._last_error = message
        if message != self._logged_error:
            self._logged_error = message
            self._log.warning("timing host: %s", message)

    def _count(self):
        day = int(self._wall() // 86400)
        with self._lock:
            if day != self._day:
                self._day, self._msgs_today = day, 0
            self._msgs_today += 1

    def _send(self, text):
        if self._conn is None:
            return False
        try:
            self._conn.send_text(text)
        except (OSError, ConnectionError) as e:
            self._close()
            self._fail(f"sending to the timing host failed: {e}", self._clock())
            return False
        self._count()
        return True

    def _send_moved(self):
        served = self._served_fn()
        if served == self._moved:
            return
        # Also on every fresh connection: the room may still hold a redirect from before.
        to = self._fallback_fn() if served == "funnel" else None
        if self._send(timing_host.moved_frame(to)):
            self._moved = served

    def _send_state(self, now):
        try:
            body, age_s = self._body_fn()
        except Exception as e:
            self._error(f"building the public timing data failed: {e!r}")
            return
        if self._send(timing_host.state_frame(self._seq + 1, age_s, body, self._fallback_fn())):
            self._seq += 1
            self._last_sent = now

    def _read_acks(self):
        while self._conn is not None:
            try:
                text = self._conn.recv_text(timeout=RECV_POLL_S)
            except (OSError, ConnectionError) as e:
                self._close()
                self._fail(f"the timing host closed the connection: {e}", self._clock())
                return
            if text is None:
                return
            ack = timing_host.parse_host_frame(text)
            if ack is not None:
                with self._lock:
                    self._last_ack = self._clock()
                    self._viewers, self._conns_today = ack["viewers"], ack["conns_today"]

    def _close(self):
        conn = self._conn
        with self._lock:
            self._conn = self._conn_link = None
            self._viewers = 0
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass  # already gone
