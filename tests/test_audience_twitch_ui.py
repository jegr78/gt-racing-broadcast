"""Profile-safe Control Center callbacks consume relay cache only."""

from pathlib import Path
import tempfile
from unittest.mock import patch
import test_racecast as cli
import audience_widgets as aw

m = cli.m


def t_automatic_capture_uses_fresh_cache_and_rejects_profile_change_during_rpc():
    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        try:
            store = aw.Store(Path(td) / "profiles/demo/audience.json")
            goal = store.create({
                "label": "Active subscriptions",
                "source": "twitch",
                "metric": "subscriptions",
                "mode": "net",
            })
            sample = aw.Sample(
                18,
                "available",
                100,
                identity="twitch:channel:12345:subscriptions",
                age_seconds=0,
            )
            with patch.object(
                m, "_audience_provider_sample", return_value=sample, create=True
            ):
                result = m.audience_write_data({
                    "expectedProfile": "demo",
                    "action": "capture",
                    "id": goal["id"],
                })
            assert result["ok"] and result["widget"]["baseline"]["value"] == 18, result
            with (
                patch.object(
                    m, "_audience_provider_sample", return_value=sample, create=True
                ),
                patch.object(m, "_active_profile_name", side_effect=["demo", "other"]),
            ):
                result = m.audience_write_data({
                    "expectedProfile": "demo",
                    "action": "capture",
                    "id": goal["id"],
                })
            assert not result["ok"] and "profile" in result["error"].lower(), result
        finally:
            m._env_base, m._runtime_base_dir = original


def t_provider_preflight_rejects_absent_process_and_display_name_as_profile_slug():
    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        try:
            for pid, stamp in ((None, "demo"), (123, "Demo Racing")):
                with (
                    patch.object(m.sv, "read_pid", return_value=pid),
                    patch.object(m, "_running_relay_profile", return_value=stamp),
                    patch.object(
                        m.http_util,
                        "open_url",
                        side_effect=AssertionError(
                            "Rejected preflight contacted a port"
                        ),
                    ),
                ):
                    result = m.audience_twitch_data()
                    assert not result["ok"] and "profile" in result["error"].lower(), (
                        result
                    )
        finally:
            m._env_base, m._runtime_base_dir = original


def t_actual_ui_twitch_route_rejects_foreign_origins_before_callbacks():
    import json
    import urllib.request
    import urllib.error
    import test_ui_server as ui

    calls = []
    ctx = ui._ctx()
    ctx["audience_twitch"] = lambda body=None: (
        calls.append(body) or {"ok": True, "provider": "twitch"}
    )
    server, port = ui._serve(ctx)

    def request(body=None, headers=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/audience/twitch",
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.load(error)

    try:
        for headers in (
            {"Origin": "http://evil.invalid"},
            {"Referer": "http://evil.invalid/x"},
            {"Sec-Fetch-Site": "cross-site"},
        ):
            for body in (None, {"action": "disconnect", "expectedProfile": "demo"}):
                code, result = request(body, headers)
                assert (
                    code == 403
                    and result.get("ok") is False
                    and isinstance(result.get("error"), str)
                ), (code, result)
                assert not calls, "Rejected UI request reached provider callback"
        code, result = request()
        assert code == 200 and result["provider"] == "twitch" and calls == [None]
        code, result = request({"action": "disconnect", "expectedProfile": "demo"})
        assert code == 200 and result["ok"] and len(calls) == 2
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    for name, fn in tuple(globals().items()):
        if name.startswith("t_"):
            fn()
            print("PASS", name)
