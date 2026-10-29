#!/usr/bin/env python3
"""Broadcast-end alert regressions using a loopback server and fake OBS/Discord."""
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
spec = importlib.util.spec_from_file_location("shutdown_relay", ROOT / "src/relay/racecast-feeds.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
import _obs_guard  # noqa: E402

_obs_guard.install(m)


@contextlib.contextmanager
def _event(*, qualifying=False, count=1):
    original_tailscale = m.detect_tailscale_ip
    m.detect_tailscale_ip = lambda: "100.64.0.9"
    with tempfile.TemporaryDirectory() as td:
        source = m.ScheduleSource(None, os.path.join(td, "schedule.txt"), None)
        relay = m.Relay(source, [53001, 53002], td)
        relay.mode = "qualifying" if qualifying else "race"
        relay.obs_reachable = True
        relay.obs_stats = {"stream_active": True}
        relay.stream_expected = True
        relay._stream_started_ts = 1000
        relay._notified_level = "green"
        for name in ("_maybe_probe_obs", "_sample_connectivity", "_sample_inbound_gaps",
                     "_sample_consumer_backlogs", "_backlog_shed_tick", "_maybe_auto_failover",
                     "_record_render_counts", "_record_consumer_overflows"):
            setattr(relay, name, lambda *a: None)
        relay._health_snapshot = lambda now: {"health_level": relay.health_level}
        relay.health_store = m.HealthStore(os.path.join(td, "health.db"))
        posts, teardown = [], []
        relay._discord_post = lambda payload, what: posts.append((what, payload))
        relay._spawn_event_stop = lambda: teardown.append("report+teardown")
        parts = m.PartStore(os.path.join(td, "part.json"))
        parts.mark_live(1)
        rows = ([{"part": "Q", "producer": "Producer"}] if qualifying else
                [{"part": f"Part {i + 1}", "producer": "Producer"} for i in range(count)])
        obs = types.SimpleNamespace(result=(True, ""), effect=lambda on: None, calls=[])

        def set_stream(on):
            obs.calls.append(on)
            obs.effect(on)
            return obs.result

        relay._obs = types.SimpleNamespace(set_stream=set_stream)
        handler = m.make_handler(relay, producer_source=types.SimpleNamespace(get=lambda: rows),
                                 part_store=parts, health_store=relay.health_store)
        server = m.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            yield relay, obs, posts, teardown, parts, server.server_address[1]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            relay.health_store.close()
            m.detect_tailscale_ip = original_tailscale
            assert not thread.is_alive()
            assert not _obs_guard.CALLS, "No test may reach a real OBS"


def _post(port, path, body):
    request = urllib.request.Request(f"http://127.0.0.1:{port}/{path}",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        response = urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, json.loads(response.read())


def _off_air(relay):
    relay.obs_stats = {"stream_active": False}
    relay._heartbeat_tick(2000)


def t_final_end_keeps_stop_marker_and_report_without_late_health_pings():
    with _event() as (relay, obs, posts, teardown, parts, port):
        def stop(on):
            assert on is False
            relay._on_stream_transition("stopped", 2000)
            _off_air(relay)  # The heartbeat can observe OBS stopping before the request returns.
        obs.effect = stop
        code, reply = _post(port, "parts/end", {"intent": "END PART 1"})
        assert code == 200 and reply["final"] is True, (code, reply)
        _off_air(relay)  # Report/voice leave/Windows teardown may span further heartbeats.
        relay._send_health_webhook("red", ["OBS is not streaming. Broadcast is off air"], "green")
        relay._send_health_webhook("green", [], "red")  # A stale recovery must not page either.
        assert [kind for kind, _ in posts] == ["obs-stream"], posts
        assert posts[0][1]["content"] == "@here", "Keep the regular stream-stop announcement"
        assert teardown == ["report+teardown"]
        assert parts.get()["live"] is False
        events = relay.health_store.events(0, 1e12)
        assert [event["type"] for event in events] == ["obs_stream_stop", "part_end"]


def t_failed_final_stop_restores_alarm_and_does_not_consume_the_transition():
    with _event() as (relay, obs, posts, teardown, parts, port):
        obs.result = (False, "OBS refused stop")
        def stop(on):
            _off_air(relay)
        obs.effect = stop
        code, reply = _post(port, "parts/end", {"intent": "END PART 1"})
        assert code == 503 and reply["error"] == "OBS refused stop"
        _off_air(relay)
        health = [payload for kind, payload in posts if kind == "health"]
        assert len(health) == 1 and "CRITICAL" in health[0]["embeds"][0]["title"], posts
        assert parts.get()["live"] is True and not teardown


def t_rejected_end_and_unexpected_stream_loss_still_page():
    with _event() as (relay, obs, posts, teardown, parts, port):
        code, _ = _post(port, "parts/end", {"intent": "wrong confirmation"})
        assert code == 403 and not obs.calls and not teardown
        _off_air(relay)
        assert len(posts) == 1 and posts[0][0] == "health", posts
        assert "CRITICAL" in posts[0][1]["embeds"][0]["title"]
        assert parts.get()["live"] is True


def t_new_explicit_start_rearms_health_after_a_completed_final_stop():
    with _event() as (relay, obs, posts, teardown, parts, port):
        code, _ = _post(port, "parts/end", {"intent": "END PART 1"})
        assert code == 200
        code, _ = _post(port, "obs/stream", {"on": True})
        assert code == 200
        _off_air(relay)
        assert [kind for kind, _ in posts] == ["health"], posts


def t_qualifying_end_also_finishes_health_notifications():
    with _event(qualifying=True) as (relay, obs, posts, teardown, parts, port):
        def stop(on):
            _off_air(relay)
        obs.effect = stop
        code, reply = _post(port, "parts/end", {"intent": "END PART Q"})
        assert code == 200 and reply["final"] is True
        _off_air(relay)
        assert not posts and teardown == ["report+teardown"]


def t_nonfinal_part_keeps_event_health_alerts_enabled():
    with _event(count=2) as (relay, obs, posts, teardown, parts, port):
        code, reply = _post(port, "parts/end", {"intent": "END PART 1"})
        assert code == 200 and not reply.get("final") and not teardown
        relay._send_health_webhook("red", ["Feed A down"], "green")
        assert [kind for kind, _ in posts] == ["health"]
        assert parts.get()["index"] == 2


def t_raising_stop_restores_event_alerts():
    with _event() as (relay, obs, posts, teardown, parts, port):
        def fail(on):
            _off_air(relay)
            raise RuntimeError("OBS request interrupted")
        obs.effect = fail
        try:
            relay._set_obs_stream(False, final_part=True)
        except RuntimeError as exc:
            assert str(exc) == "OBS request interrupted"
        else:
            raise AssertionError("Expected the OBS exception")
        _off_air(relay)
        assert [kind for kind, _ in posts] == ["health"]


def t_inflight_health_send_finishes_before_the_final_stop_can_run():
    with _event() as (relay, obs, posts, teardown, parts, port):
        entered, release = threading.Event(), threading.Event()
        order = []

        def post(payload, what):
            entered.set()
            assert release.wait(5), "The fake Discord send must be released"
            order.append("health-delivered")
        relay._discord_post = post
        obs.effect = lambda on: order.append("obs-stop")
        sender = threading.Thread(target=relay._send_health_webhook,
                                  args=("red", ["Feed A down"], "green"))
        stopper = threading.Thread(target=relay._set_obs_stream,
                                   args=(False,), kwargs={"final_part": True})
        sender.start()
        try:
            assert entered.wait(5)
            stopper.start()
            deadline = time.monotonic() + 5
            while not relay._stream_control_lock.locked() and time.monotonic() < deadline:
                time.sleep(0.001)
            assert relay._stream_control_lock.locked(), "The stop must reach its send fence"
            assert order == [], "OBS must wait for the already admitted health post"
        finally:
            release.set()
            sender.join(5)
            if stopper.ident is not None:
                stopper.join(5)
        assert not sender.is_alive() and not stopper.is_alive()
        assert order == ["health-delivered", "obs-stop"], order


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")
