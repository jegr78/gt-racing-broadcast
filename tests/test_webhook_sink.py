#!/usr/bin/env python3
"""The simulated Discord webhook (#1230). Run: python3 tests/test_webhook_sink.py"""
import importlib.util
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import types
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "event-sim", "fakes"))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import http_util  # noqa: E402
import notify  # noqa: E402
import simulation  # noqa: E402
import webhook_sink  # noqa: E402

for _key in [k for k in os.environ if k.startswith("RACECAST_")]:
    del os.environ[_key]
os.environ["RACECAST_SIMULATION"] = "1"
simulation.install_network_guard()

_spec = importlib.util.spec_from_file_location(
    "relay_feeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

ENTRY = {"streamer_name": "Comms 2", "target_stint": 2, "proposed_url": "sim://comms2"}


class Clock:
    def __init__(self, now=1795000000.0):
        self.now = now

    def __call__(self):
        return self.now


def _relay_post(url, payload):
    """The relay's own best-effort Discord POST, `Relay._discord_post`."""
    m.Relay._discord_post(types.SimpleNamespace(discord_webhook_url=url), payload, "test")


def _post_json(url, payload):
    """(status, body) of a JSON POST shaped like the relay's."""
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "racecast-feeds/1.0"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, resp.read()


def _status(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _banner(proc, timeout=30):
    """The CLI's first stdout line, or an AssertionError once `timeout` passes."""
    lines = queue.Queue()
    threading.Thread(target=lambda: lines.put(proc.stdout.readline()), daemon=True).start()
    try:
        return lines.get(timeout=timeout).decode("utf-8", "replace")
    except queue.Empty:
        raise AssertionError("the CLI printed no banner") from None


def _stop_cli(proc, ready):
    proc.terminate()
    proc.wait(timeout=10)
    proc.stdout.close()
    if os.name != "nt":   # TerminateProcess leaves no chance to clean up
        assert not os.path.exists(ready), "the CLI removes its ready-file on exit"


def t_env_is_a_valid_simulation_endpoint():
    with webhook_sink.WebhookSink().start() as sink:
        env = sink.env()
        assert env == {simulation.FLAG: "1", simulation.DISCORD_WEBHOOK_URL: sink.url}, env
        assert simulation.endpoint(simulation.DISCORD_WEBHOOK_URL, env) == sink.url
        assert simulation.config_overrides(env)["discord_webhook_url"] == sink.url
        assert "/api/webhooks/" in sink.url


def t_records_relay_messages_with_time_and_answers_204():
    clock = Clock()
    with webhook_sink.WebhookSink(clock=clock).start() as sink:
        status, body = _post_json(sink.url, m.cockpit_submission_payload(ENTRY, 1, "Sim Cup"))
        assert (status, body) == (204, b""), "Discord answers a plain webhook POST with 204"
        clock.now += 10
        _relay_post(sink.url, m.cockpit_approval_payload(ENTRY))
        got = sink.messages()
        assert [g["seq"] for g in got] == [1, 2] and got[1]["time"] == clock.now, got
        assert got[0]["content"] == "@here" and got[0]["username"] == "GT Racecast", got[0]
        assert "1 pending" in got[0]["text"], "the text covers the embed footer"
        assert sink.count("approved") == 1 and sink.count("stream-link submission") == 1
        assert sink.count() == 2 and sink.messages(since=1) == got[1:]


def t_counts_embed_fields_and_case_insensitively():
    with webhook_sink.WebhookSink().start() as sink:
        _relay_post(sink.url, m.discord_health_payload("red", ["Feed A stalled"], "green", "Sim Cup", "P1"))
        assert sink.count("feed a stalled") == 1, sink.messages()[0]["text"]


def t_cli_takeover_post_arrives():
    with webhook_sink.WebhookSink().start() as sink:
        racecast = _load_racecast()
        racecast._post_discord_webhook(sink.url, notify.takeover_discord_payload("Producer B", "Producer A", 2, "Feed B", "Sim Cup"))
        assert sink.count("took over the broadcast at stint 2") == 1, sink.messages()
        assert sink.messages()[0]["path"] == sink.path


def _load_racecast():
    sys.path.insert(0, os.path.join(ROOT, "src"))
    import racecast
    return racecast


def t_multipart_report_records_payload_json_and_the_file():
    with webhook_sink.WebhookSink().start() as sink:
        payload = {"username": "GT Racecast", "embeds": [{"title": "Event report"}]}
        http_util.post_multipart(sink.url, fields={"payload_json": json.dumps(payload)},
                                 files=[("files[0]", "report.zip", b"PK\x03\x04data", "application/zip")],
                                 timeout=5)
        msg = sink.messages()[0]
        assert msg["embeds"][0]["title"] == "Event report", msg
        assert msg["files"] == [{"field": "files[0]", "filename": "report.zip",
                                 "content_type": "application/zip", "size": 8}], msg["files"]


def t_rejects_what_discord_rejects_and_records_nothing():
    with webhook_sink.WebhookSink().start() as sink:
        json_ct = {"Content-Type": "application/json"}
        status, body = _status(sink.url, b"{}", json_ct)
        assert (status, json.loads(body)) == (400, {"message": "Cannot send an empty message",
                                                    "code": 50006}), body
        status, body = _status(sink.url, b"{nope", json_ct)
        assert status == 400 and json.loads(body)["code"] == 50109, body
        status, _ = _status(sink.url, json.dumps({"content": "x" * 2001}).encode(), json_ct)
        assert status == 400, "content over 2000 characters"
        status, body = _status(sink.url, b'["hi"]', json_ct)
        assert status == 400 and json.loads(body)["code"] == 50006, "a payload that is no object is empty"
        assert sink.messages() == [] and sink.rejected == 4


def t_refuses_requests_a_web_page_could_send():
    with webhook_sink.WebhookSink().start() as sink:
        body = json.dumps({"content": "hi"}).encode()
        assert _status(sink.url, body, {"Content-Type": "application/json",
                                        "Origin": "http://example.test"})[0] == 403
        assert _status(sink.url, body, {"Content-Type": "application/json",
                                        "Host": "hooks.example.test"})[0] == 403
        assert _status(sink.url, body, {"Content-Type": "text/plain"})[0] == 415
        assert sink.messages() == []


def t_check_endpoint_lists_messages():
    with webhook_sink.WebhookSink().start() as sink:
        _relay_post(sink.url, {"content": "one"})
        _relay_post(sink.url + "?wait=false", {"content": "two"})
        assert _status(sink.http_url + "/_sim/messages", b'{"content": "x"}',
                       {"Content-Type": "application/json"})[0] == 404, "the check path is no webhook"
        got = webhook_sink.fetch(sink.http_url, "/_sim/messages?since=1")
        assert [g["content"] for g in got["messages"]] == ["two"] and got["seq"] == 2, got


def t_cli_writes_a_ready_file():
    with tempfile.TemporaryDirectory() as tmp:
        ready = os.path.join(tmp, "sink.json")
        proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "event-sim", "fakes",
                                                              "webhook_sink.py"),
                                 "--port", "0", "--ready-file", ready],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            assert "webhook-sink" in _banner(proc)
            with open(ready, encoding="utf-8") as fh:
                info = json.load(fh)
            _relay_post(info["url"], {"content": "hello"})
            got = webhook_sink.fetch(info["http_url"], "/_sim/messages")
            assert got["messages"][0]["content"] == "hello", got
            assert info["env"] == {simulation.FLAG: "1", simulation.DISCORD_WEBHOOK_URL: info["url"]}
        finally:
            _stop_cli(proc, ready)


class Sleeps(list):
    def __call__(self, seconds):
        self.append(seconds)


def t_injected_faults_answer_instead_of_recording():
    sleeps = Sleeps()
    with webhook_sink.WebhookSink(sleep=sleeps).start() as sink:
        sink.fail("hook", status=429, times=1)
        try:
            _post_json(sink.url, {"content": "one"})
        except urllib.error.HTTPError as exc:
            assert exc.code == 429, exc.code
        else:
            raise AssertionError("the injected 429 must reach the poster")
        sink.fail("path:" + sink.path, error="Unknown Webhook", delay=3)
        status, body = _status(sink.url, b'{"content": "two"}', {"Content-Type": "application/json"})
        assert (status, json.loads(body)) == (400, {"message": "Unknown Webhook", "code": 0}), body
        assert sleeps == [3] and sink.messages() == [], "a faulted message is not recorded"
        sink.clear_faults()
        webhook_sink.fetch(sink.http_url, "/_sim/fail", {"target": "hook", "status": 500})
        assert _status(sink.url, b'{"content": "x"}', {"Content-Type": "application/json"})[0] == 500
        req = urllib.request.Request(sink.http_url + "/_sim/fail", data=b"", method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5).close()
        assert _post_json(sink.url, {"content": "three"})[0] == 204 and sink.count("three") == 1
        assert sink.snapshot()["faults_hit"] == 3, sink.snapshot()


def t_shapes_discord_refuses_are_400_invalid_form_body():
    with webhook_sink.WebhookSink().start() as sink:
        json_ct = {"Content-Type": "application/json"}
        for bad in ({"content": 5}, {"embeds": "x"}, {"embeds": [1]}, {"embeds": [{}] * 11},
                    {"content": "x", "username": 5}):
            status, body = _status(sink.url, json.dumps(bad).encode(), json_ct)
            assert status == 400 and json.loads(body)["code"] == 50035, (bad, body)
        assert sink.messages() == [] and sink.rejected == 5


def t_a_report_upload_above_the_old_limit_arrives_and_one_above_discords_is_refused():
    with webhook_sink.WebhookSink().start() as sink:
        payload = json.dumps({"content": "report"})
        http_util.post_multipart(sink.url, fields={"payload_json": payload},
                                 files=[("files[0]", "big.zip", b"z" * (12 << 20), "application/zip")],
                                 timeout=30)
        assert sink.messages()[0]["files"][0]["size"] == 12 << 20
        try:
            http_util.post_multipart(sink.url, fields={"payload_json": payload},
                                     files=[("files[0]", "huge.zip", b"z" * (21 << 20), "application/zip")],
                                     timeout=30)
        except urllib.error.HTTPError as exc:
            assert exc.code == 413 and json.loads(exc.read())["code"] == 40005
        else:
            raise AssertionError("a file over the per-file limit must be refused")
        assert sink.count() == 1


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")
