"""Actual Twitch admin HTTP, Console isolation and public cached widget responses."""

import json
import threading
import urllib.request
import urllib.error
import test_console_gate as gate
import test_audience_twitch_connection as fixture
import audience_widgets as aw


def t_relay_provider_boundary_and_cached_overlay_reads():
    with fixture.fixture() as (service, clock, api, cache, _definitions):
        store = aw.Store(service.files.public.parent / "audience.json")
        for metric in ("viewers", "subscriptions"):
            store.create({"label": metric, "source": "twitch", "metric": metric})
        relay = gate.m.Relay(
            gate._FakeSource(gate._URLS8, [("https://youtu.be/a", "Alice", "1", 2)]),
            [53001, 53002],
            gate.LOGDIR,
        )
        handler = gate.m.make_handler(
            relay,
            console_secret=gate.SECRET,
            crew_source=gate._Crew([("Bob", True, False), ("Carol", False, True)]),
            twitch_service=service,
            audience_runtime=aw.Runtime(store.path, cache=cache, clock=clock.time),
        )
        server = gate.m.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_address[1]

        def request(
            path="/audience/admin/twitch", body=None, headers=None, method=None
        ):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}" + path,
                data=None if body is None else json.dumps(body).encode(),
                headers={"Content-Type": "application/json", **(headers or {})},
                method=method,
            )
            try:
                with urllib.request.urlopen(req, timeout=3) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as error:
                with error:
                    return error.code, json.load(error)

        try:
            code, status = request()
            assert (
                code == 200
                and status["profile"] == "demo"
                and status["provider"] == "twitch"
                and status["protocol"] == "racecast-audience-admin-v1"
            ), (code, status)
            assert not api.calls
            for headers in (
                {"Origin": "http://evil.invalid"},
                {"Host": "evil.invalid"},
                {"Referer": "http://evil.invalid/x"},
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
                ), (code, result)
                code, result = request(headers=headers)
                assert (
                    code == 403
                    and result["ok"] is False
                    and "local" in result["error"].lower()
                ), (code, result)
            for body, headers, expected in (
                ([], {}, 400),
                ({"action": "disconnect", "expectedProfile": "other"}, {}, 400),
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
                    and result["ok"] is False
                    and isinstance(result["error"], str)
                ), (code, result)
                assert result["provider"] == "twitch" and not api.calls
            for user in ("bob", "carol", "alice"):
                token = gate._tok(user)
                code, role_status = request("/console/status?t=" + token)
                assert code == 200, (user, code, role_status)
                for method in ("GET", "POST"):
                    code, result = request(
                        "/console/audience/admin/twitch?t=" + token,
                        body={"action": "disconnect", "expectedProfile": "demo"}
                        if method == "POST"
                        else None,
                        method=method,
                    )
                    assert code == 404 and "error" in result, (
                        user,
                        method,
                        code,
                        result,
                    )
            code, result = request(
                body={
                    "action": "verify",
                    "expectedProfile": "demo",
                    "clientId": fixture.CLIENT,
                    "channel": "racing",
                }
            )
            assert code == 200 and result["ok"] and not api.calls
            service.tick()
            clock.advance(5)
            service.tick()
            service.tick()
            service.tick()
            _, status = request()
            assert status["setup"]["phase"] == "verified" and not status["connected"]
            code, result = request(
                body={
                    "action": "activate",
                    "expectedProfile": "demo",
                    "attempt": status["setup"]["attempt"],
                }
            )
            assert code == 200 and result["ok"]
            service.tick()
            before = len(api.calls)
            for _ in range(3):
                code, data = request("/audience/data")
                assert code == 200 and [row["value"] for row in data["widgets"]] == [
                    0,
                    761,
                ], data
                assert "setup" not in data and all(
                    secret not in str(data)
                    for secret in (fixture.PRIVATE, fixture.ACCESS, fixture.REFRESH)
                )
            assert len(api.calls) == before, "Overlay reader polled Twitch"
            _, status = request("/audience/admin/twitch?metric=subscriptions")
            assert status["sample"]["value"] == 761 and all(
                secret not in str(status)
                for secret in (fixture.PRIVATE, fixture.ACCESS, fixture.REFRESH)
            )

            # Failed durable cleanup must report both HTTP and semantic storage failure.
            def failed_writer(*args, **kwargs):
                raise OSError("fixture-private-disk")

            service.files.writer = failed_writer
            code, result = request(
                body={"action": "disconnect", "expectedProfile": "demo"}
            )
            assert (
                code == 500
                and result["ok"] is False
                and "saved" in result["error"].lower()
            ), (code, result)
            assert (
                not service.status()["connected"] and not service.files.secret.exists()
            )
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    t_relay_provider_boundary_and_cached_overlay_reads()
    print("PASS actual Twitch admin, Console roles and cached overlay boundary")
