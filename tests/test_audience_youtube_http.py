"""Real local relay HTTP provider boundary, with all provider I/O injected."""

from dataclasses import asdict
import json
import threading
import urllib.request
import urllib.error
from unittest.mock import patch

import test_console_gate as gate
import test_audience_youtube_connection as fixture
import audience_proxy
import http_util

REAL_OPEN = http_util.open_url  # The provider fixture blocks it; the local proxy needs it.


def t_relay_admin_keeps_credentials_outside_console_and_overlay_reads():
    with fixture.fixture() as (service, _clock, api, cache, _definitions):
        relay = gate.m.Relay(
            gate._FakeSource(gate._URLS8, []), [53001, 53002], gate.LOGDIR
        )
        handler = gate.m.make_handler(
            relay,
            console_secret=gate.SECRET,
            youtube_service=service,
            audience_runtime=gate.m.audience_widgets.Runtime(None, cache=cache),
        )
        server = gate.m.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_address[1]

        def request(path="/audience/admin/youtube", body=None, headers=None):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}" + path,
                data=None if body is None else json.dumps(body).encode(),
                headers={"Content-Type": "application/json", **(headers or {})},
            )
            try:
                with urllib.request.urlopen(req, timeout=3) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as error:
                return error.code, json.loads(error.read())

        try:
            code, status = request()
            assert code == 200 and status["protocol"] == "racecast-audience-admin-v1", (
                code,
                status,
            )
            assert status["profile"] == "demo" and not api.calls
            for headers in (
                {"Origin": "http://evil.invalid"},
                {"Origin": f"http://127.0.0.1:{port + 1}"},
                {"Host": "evil.invalid"},
                {"Sec-Fetch-Site": "cross-site"},
            ):
                code, result = request(
                    body={"action": "disconnect", "expectedProfile": "demo"},
                    headers=headers,
                )
                assert (
                    code == 403
                    and result["ok"] is False
                    and "local" in result["error"].lower()
                ), result
            code, result = request(
                body={"action": "disconnect", "expectedProfile": "wrong"}
            )
            assert (
                code == 400
                and result["ok"] is False
                and "profile" in result["error"].lower()
            ), result
            for body, headers, expected in (
                ([], {}, 400),
                ({"action": "unknown", "expectedProfile": "demo"}, {}, 400),
                (
                    {"action": "disconnect", "expectedProfile": "demo"},
                    {"Content-Type": "text/plain"},
                    400,
                ),
            ):
                code, result = request(body=body, headers=headers)
                assert (
                    code == expected
                    and result.get("ok") is False
                    and isinstance(result.get("error"), str)
                ), (code, result)
                assert not api.calls, "Rejected setup performed provider I/O"
            code, result = request(
                "/console/audience/admin/youtube", body={"action": "disconnect"}
            )
            assert code in (401, 404) and "error" in result
            code, result = request(
                body={
                    "action": "verify",
                    "expectedProfile": "demo",
                    "video": fixture.VIDEO,
                    "apiKey": fixture.KEY,
                }
            )
            assert code == 200 and result["ok"] and not api.calls, result
            service.tick()
            _, status = request()
            assert status["setup"]["phase"] == "verified" and fixture.KEY not in str(
                status
            )
            code, public = request("/audience/data")
            assert (
                code == 200 and fixture.KEY not in str(public) and "setup" not in public
            )
            _, status = request("/audience/admin/youtube?metric=likes")
            assert status["sample"] == asdict(service.sample("likes"))
        finally:
            server.shutdown()
            server.server_close()



def t_bounded_setup_errors_reach_the_operator_through_the_proxy():
    with fixture.fixture() as (service, _clock, api, cache, _definitions):
        relay = gate.m.Relay(
            gate._FakeSource(gate._URLS8, []), [53001, 53002], gate.LOGDIR
        )
        handler = gate.m.make_handler(
            relay,
            console_secret=gate.SECRET,
            youtube_service=service,
            audience_runtime=gate.m.audience_widgets.Runtime(None, cache=cache),
        )
        server = gate.m.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_address[1]
        client = audience_proxy.Client("demo", port)
        try:
            for fields, expected in (
                (
                    {"video": "https://www.youtube.com/live", "apiKey": fixture.KEY},
                    "Select a concrete video, not a channel/live shortcut",
                ),
                ({"video": fixture.VIDEO, "apiKey": "bad"}, "Enter a valid YouTube Data API key"),
                ({"action": "activate", "attempt": "stale"}, "Verify the selected targets before activation"),
            ):
                body = {"action": "verify", "expectedProfile": "demo", **fields}
                with patch.object(http_util, "open_url", REAL_OPEN):
                    try:
                        client.command(body)
                    except ValueError as error:
                        assert str(error) == expected, (str(error), expected)
                    else:
                        raise AssertionError("Invalid setup accepted")
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/audience/admin/youtube",
                data=b"{not json",
                headers={"Content-Type": "application/json"},
            )
            try:
                urllib.request.urlopen(req, timeout=3)
            except urllib.error.HTTPError as error:
                result = json.loads(error.read())
                assert error.code == 400 and result["error"] == (
                    "Invalid provider command, target or profile; check connection settings"
                ), result
            else:
                raise AssertionError("Malformed JSON accepted")
            assert not api.calls
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    t_relay_admin_keeps_credentials_outside_console_and_overlay_reads()
    print("PASS local YouTube HTTP boundary")
    t_bounded_setup_errors_reach_the_operator_through_the_proxy()
    print("PASS bounded YouTube setup errors reach the operator")
