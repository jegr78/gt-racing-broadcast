"""Regression checks for the full-codebase review, without live services."""
import contextlib
import io
import threading
import urllib.error
import urllib.request
from unittest.mock import patch

import test_console_gate as gate
import test_pov as pov
import test_racecast as cli
from test_gt7_fixture import PKT_THROTTLE_HEX


class Process:
    def __init__(self):
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def t_next_preserves_live_process_and_position_until_incoming_is_ready():
    for incoming in ('idle', 'connecting', 'serving'):
        relay = pov._relay(['s1', 's2', 's3', 's4'])
        relay.manual_feed_arm = True
        relay.A.paused = False
        relay.B.paused = incoming == 'serving'  # A paused feed is never ready.
        relay.A.phase, relay.B.phase = 'serving', incoming
        proc = Process()
        relay.A.proc = proc
        before = relay.A.idx, relay.B.idx, relay.on_air_row_idx(), relay.live_feed()
        result = relay.next_auto()
        assert not proc.terminated, incoming
        assert not result['changed'] and not result['obs_cut'], result
        assert before == (relay.A.idx, relay.B.idx, relay.on_air_row_idx(), relay.live_feed())
        relay.B.paused, relay.B.phase = False, 'serving'
        result = relay.next_auto()
        assert result['obs_cut'] and relay.live_feed() == 'B'


def t_next_at_schedule_end_keeps_the_current_stream():
    relay = pov._relay(['s1'])
    relay.A.paused, relay.A.phase = False, 'serving'
    proc = Process()
    relay.A.proc = proc
    result = relay.next_auto()
    assert not proc.terminated
    assert not result['changed'] and not result['obs_cut']
    assert relay.A.idx == 0 and relay.on_air_row_idx() == 0


def t_retargeted_incoming_stream_must_be_ready_even_on_an_immediate_retry():
    relay = pov._relay(['s1', 's2', 's3', 's4'])
    relay.A.phase = relay.B.phase = 'serving'
    relay.A.paused = relay.B.paused = False
    relay.B.idx = 3  # Its old ready stream no longer matches the next stint.
    outgoing, incoming = Process(), Process()
    relay.A.proc, relay.B.proc = outgoing, incoming
    for _ in range(2):
        result = relay.next_auto()
        assert not result['obs_cut'] and not outgoing.terminated
        assert relay.live_feed() == 'A' and relay.on_air_row_idx() == 0
    assert incoming.terminated
    relay.B.phase = 'serving'  # The replacement worker has now delivered video.
    assert relay.next_auto()['obs_cut']


def t_cross_origin_controls_are_rejected_before_any_dispatch():
    calls = []
    with patch.object(gate.m.Relay, 'next_auto', side_effect=lambda **kw: (
            calls.append('next'), {'obs_cut': False})[1]):
        server = gate._serve(browser_hosts=('producer.tail123.ts.net',))
        base = f'http://127.0.0.1:{server.server_port}'
        cookie = f'{gate.m.console_auth.COOKIE_NAME}={gate._tok("bob")}'
        try:
            for method in ('GET', 'POST'):
                for path in ('/', '/status', '/next', '/console/next', '/mode/race', '/console/obs/audio',
                             '/feed/A/deactivate', '/timer/start', '/telemetry/record/start',
                             '/console/buttons/api/location/1/0/0/press'):
                    for foreign in ({'Sec-Fetch-Site': 'cross-site'},
                                    {'Sec-Fetch-Site': 'same-site'},
                                    {'Origin': 'https://foreign.example'},
                                    {'Origin': 'null'},
                                    {'Referer': 'https://foreign.example/page'}):
                        req = urllib.request.Request(base + path, method=method,
                            data=b'{}' if method == 'POST' else None,
                            headers=dict(foreign, Cookie=cookie, **{'Content-Type': 'application/json'}))
                        try:
                            with urllib.request.urlopen(req, timeout=3) as response:
                                code = response.status
                        except urllib.error.HTTPError as exc:
                            code = exc.code
                            exc.close()
                        assert code == 403, (method, path, foreign, code)
            assert calls == []
            for headers in ({}, {'Origin': base, 'Referer': base + '/console/panel',
                                  'Sec-Fetch-Site': 'same-origin'}, {'Sec-Fetch-Site': 'none'}):
                for path in ('/next', '/console/next'):
                    req = urllib.request.Request(base + path, headers=dict(headers, Cookie=cookie))
                    with urllib.request.urlopen(req, timeout=3) as response:
                        assert response.status == 200
            assert len(calls) == 6
            # Same-origin metadata alone is insufficient after DNS rebinding.
            for host in ('attacker.example:8088', 'producer.tail123.ts.net.attacker.example:8088'):
                for path in ('/', '/status', '/next'):
                    code, _ = gate._get(server.server_port, path, headers={
                        'Host': host, 'Origin': 'http://' + host, 'Sec-Fetch-Site': 'same-origin'})
                    assert code == 403, (host, path, code)
            assert len(calls) == 6
            # Funnel preserves the public Host and reports its HTTPS scheme.
            code, _ = gate._get(server.server_port, '/console/next', gate._tok('bob'), headers={
                'Host': 'producer.tail123.ts.net', 'Origin': 'https://producer.tail123.ts.net',
                'X-Forwarded-Proto': 'https', 'Sec-Fetch-Site': 'same-origin'})
            assert code == 200 and len(calls) == 7
            for path in ('/console', '/console/panel', '/console/login', '/console/oauth/callback'):
                code, _ = gate._get(server.server_port, path, gate._tok('bob'),
                                    headers={'Sec-Fetch-Site': 'cross-site', 'Referer': 'https://foreign.example/'})
                assert code == (200 if path in ('/console', '/console/panel') else 404), (path, code)
        finally:
            server.shutdown()
            server.server_close()


def t_start_reaps_a_slow_attempt_before_retry_and_cleans_the_final_failure():
    for can_stop in (True, False):
        m = cli.m
        restore, calls = cli._relay_spawn_stubs([False, False])
        events = []
        def spawn(*args, events=events, calls=calls, **kwargs):
            events.append('spawn')
            calls['spawn'] += 1
            return 4242
        def stop(pid, events=events, can_stop=can_stop, **kwargs):
            events.append('stop')
            return can_stop
        try:
            with patch.object(m.sv, 'start_detached', side_effect=spawn), \
                    patch.object(m.sv, 'pid_alive', side_effect=bool), \
                    patch.object(m.sv, 'stop_pid', side_effect=stop), \
                    patch.object(m, '_relay_boot_log_size', return_value=0), \
                    patch.object(m, '_relay_boot_log_since', return_value=''), \
                    contextlib.redirect_stdout(io.StringIO()):
                m._spawn_relay_verified(['fake-relay'])
            assert events == (['spawn', 'stop', 'spawn', 'stop'] if can_stop else ['spawn', 'stop']), events
        finally:
            restore()


def t_start_does_not_accept_an_http_response_for_an_exited_child():
    m = cli.m
    restore, _calls = cli._relay_spawn_stubs([True])
    try:
        with patch.object(m.sv, 'pid_alive', return_value=False), \
                patch.object(m, '_relay_boot_log_size', return_value=0), \
                patch.object(m, '_relay_boot_log_since', return_value=''), \
                contextlib.redirect_stdout(io.StringIO()):
            assert m._spawn_relay_verified(['fake-relay'], attempts=1) is None
    finally:
        restore()


def t_udp_discovery_ignores_invalid_packets_and_then_pins_the_valid_console():
    m = pov.m
    stop = threading.Event()
    good = bytes.fromhex(PKT_THROTTLE_HEX)
    source, other = '192.0.2.20', '192.0.2.10'
    class Socket:
        def __init__(self):
            self.packets = iter([(b'invalid', other), (good, source), (good, other), (good, source)])
            self.received = 0
        def setsockopt(self, *args): pass
        def bind(self, *args): pass
        def settimeout(self, *args): pass
        def sendto(self, *args): pass
        def recvfrom(self, size):
            data, ip = next(self.packets)
            self.received += 1
            if self.received == 4:
                stop.set()
            return data, (ip, 33739)
    store = m.gt7_telemetry.TelemetryStore(None)
    recorded = []
    with patch.object(m.socket, 'socket', return_value=Socket()), \
            patch.object(store, 'record', side_effect=lambda *args: recorded.append(args)):
        m._telemetry_loop(store, None, stop, listen=('127.0.0.1', 0))
    assert store.data()['source'] == source
    assert store.data()['speed'] > 0
    assert len(recorded) == 2, len(recorded)


if __name__ == '__main__':
    for name, fn in sorted(globals().copy().items()):
        if name.startswith('t_') and callable(fn):
            fn()
            print('PASS', name, flush=True)
