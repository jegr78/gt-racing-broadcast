"""Audience state through real profile, layout and local HTTP callbacks."""

import json
import urllib.request
import urllib.error
import tempfile
import test_racecast as cli
import test_console_gate as gate  # installs the real OBS tripwire
import test_ui_server as ui

m = cli.m


def t_shared_values_and_independent_scene_models():
    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        try:
            created = m.audience_write_data({
                "expectedProfile": "demo",
                "action": "create",
                "fields": {
                    "label": "Likes & <b>Subs</b>",
                    "manualValue": 761,
                    "target": 750,
                },
            })
            assert created["ok"], created
            goal = created["widget"]
            ident = goal["id"]
            for page, left in [
                ("hud", 1200),
                ("intermission", 100),
                ("splitscreen", 900),
            ]:
                slots = m.overlay_slots_data(page)
                dynamic = [slot for slot in slots["slots"] if slot["id"] == ident]
                assert len(dynamic) == 1 and dynamic[0]["label"] == goal["label"], slots
                assert slots["body"].count("data-audience-widget") == 1
                layout = {
                    "page": page,
                    "slots": {
                        ident: {
                            "left": left,
                            "presentation": "bar",
                            "showPercent": False,
                        }
                    },
                }
                saved = m.overlay_layout_write_data(page, layout)
                assert saved["ok"], saved
                assert (
                    f"left: {left}px" in saved["css"]
                    and "--audience-percent: none" in saved["css"]
                )
            before = {
                page: m.overlay_layout_read_data(page)["layout"]
                for page in ("hud", "intermission", "splitscreen")
            }
            changed = m.audience_write_data({
                "expectedProfile": "demo",
                "action": "update",
                "id": ident,
                "fields": {"manualValue": 762},
            })
            assert changed["ok"], changed
            assert m.audience_read_data()["widgets"][0]["manualValue"] == 762
            assert all(
                m.overlay_layout_read_data(page)["layout"] == layout
                for page, layout in before.items()
            )
            denied = m.audience_write_data({
                "expectedProfile": "other",
                "action": "create",
                "fields": {"label": "wrong"},
            })
            assert denied == {
                "ok": False,
                "error": "Active profile changed; reopen audience widgets",
            }, denied
            assert len(m.audience_read_data()["widgets"]) == 1
        finally:
            m._env_base, m._runtime_base_dir = original


def t_local_http_validates_origin_profile_and_error_shape():
    ctx = ui._ctx()
    writes = []
    ctx.update(
        audience_read=lambda: {"ok": True, "widgets": []},
        audience_write=lambda body: (
            writes.append(body) or {"ok": False, "error": "fixture invalid range"}
        ),
    )
    server, port = ui._serve(ctx)

    def request(body=None, origin=None):
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/audience",
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
        )
        try:
            response = ui._urlopen(req)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    try:
        code, data = request()
        assert code == 200 and data == {"ok": True, "widgets": []}, (code, data)
        code, data = request({"action": "create", "expectedProfile": "demo"})
        assert code == 400 and data == {
            "ok": False,
            "error": "fixture invalid range",
        }, (code, data)
        assert len(writes) == 1
        code, data = request({"action": "create"}, origin="https://foreign.example")
        assert (
            code == 403
            and data["ok"] is False
            and data["error"] == "cross-origin request blocked"
        ), (code, data)
        assert len(writes) == 1
        code, data = request(origin=f"http://127.0.0.1:{port + 1}")
        assert code == 403 and data == {
            "ok": False,
            "error": "foreign context origin",
        }, (code, data)
        code, data = request(
            {"action": "create"}, origin=f"http://127.0.0.1:{port + 1}"
        )
        assert code == 403 and data == {
            "ok": False,
            "error": "foreign context origin",
        }, (code, data)
        assert len(writes) == 1
    finally:
        server.shutdown()
        server.server_close()


def t_export_import_and_look_restore_keep_live_values():
    import profile_io
    import backup_admin
    from pathlib import Path
    import audience_widgets as aw

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        profile = base / "profiles/demo"
        overlay = profile / "overlay"
        overlay.mkdir(parents=True)
        (profile / "profile.env").write_text("NAME=Demo\n", encoding="utf-8")
        store = aw.Store(profile / "audience.json", clock=lambda: 100)
        goal = store.create({
            "label": "Challenge",
            "mode": "net",
            "manualValue": 18,
            "target": 24,
        })
        store.reset(goal["id"])
        (overlay / "layout-hud.json").write_text(
            json.dumps({"slots": {goal["id"]: {"left": 50}}}), encoding="utf-8"
        )
        sources = {"overlay": str(overlay), "backups": str(base / "backups")}
        backup = backup_admin.create_backup("look", sources, "demo")
        store.update(goal["id"], {"manualValue": 19})
        backup_admin.restore_backup(backup, sources)
        saved = store.read()["widgets"][0]
        assert saved["manualValue"] == 19 and saved["baseline"]["value"] == 18
        exported = profile_io.export_profile(
            "demo", {"profile_dir": str(profile)}, False, str(base / "bundle.zip")
        )
        imported = profile_io.import_profile(
            exported,
            {
                "profiles_root": str(base / "other/profiles"),
                "runtime_root": str(base / "other/runtime"),
            },
        )
        assert imported["name"] == "demo"
        assert (
            aw.Store(base / "other/profiles/demo/audience.json").read() == store.read()
        )
        assert aw.Store(base / "profiles/other/audience.json").read()["widgets"] == []
        store.delete(goal["id"])
        backup_admin.restore_backup(backup, sources)
        assert store.read()["widgets"] == []
        live = aw.live_html(
            "<html><style></style><body></body></html>", str(overlay), "hud"
        )
        assert "data-audience-widget" not in live, (
            "Look restore must not resurrect a deleted shared goal"
        )


def t_profile_write_path_uses_checked_profile_snapshot():
    from pathlib import Path
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as td:
        original = cli._mk_active_profile(td)
        try:
            with patch.object(
                m, "_active_profile_name", side_effect=["demo", "other", "other"]
            ):
                result = m.audience_write_data({
                    "expectedProfile": "demo",
                    "action": "create",
                    "fields": {"label": "checked"},
                })
            assert result["ok"], result
            assert (Path(td) / "profiles/demo/audience.json").is_file()
            assert not (Path(td) / "profiles/other/audience.json").exists()
        finally:
            m._env_base, m._runtime_base_dir = original


def t_relay_pages_share_cached_samples_without_outbound_requests():
    from pathlib import Path
    from unittest.mock import patch
    import audience_widgets as aw

    with tempfile.TemporaryDirectory() as td:
        directory = Path(td) / "profile/overlay"
        directory.mkdir(parents=True)
        store = aw.Store(directory.parent / "audience.json")
        manual = store.create({"label": "Offline manual", "manualValue": 18})
        automatic = store.create({
            "label": "Likes",
            "source": "youtube",
            "metric": "likes",
            "target": 750,
        })
        now = [100]
        cache = aw.MetricCache(clock=lambda: now[0])
        cache.publish(
            "youtube", "likes", aw.Sample(0, "available", 100, identity="video:fixture")
        )
        runtime = aw.Runtime(store.path, cache=cache, clock=lambda: now[0])
        original = gate.m.make_handler

        def factory(*args, **kwargs):
            kwargs.update(audience_runtime=runtime, overlay_dir=str(directory))
            return original(*args, **kwargs)

        with patch.object(gate.m, "make_handler", side_effect=factory):
            relay = gate._serve()

        def data():
            with ui._urlopen(
                f"http://127.0.0.1:{relay.server_address[1]}/audience/data"
            ) as response:
                assert response.status == 200
                return json.load(response)

        try:
            with patch.object(
                m.http_util,
                "open_url",
                side_effect=AssertionError("Audience data must not fetch providers"),
            ):
                for _ in range(3):
                    rows = {row["id"]: row for row in data()["widgets"]}
                    assert (
                        rows[manual["id"]]["value"] == 18
                        and rows[automatic["id"]]["value"] == 0
                    )
                cache.publish(
                    "youtube",
                    "likes",
                    aw.Sample(761, "available", 100, identity="video:fixture"),
                )
                rows = {row["id"]: row for row in data()["widgets"]}
                assert (
                    rows[automatic["id"]]["value"] == 761
                    and rows[automatic["id"]]["fill"] == 100
                )
                now[0] = 220
                rows = {row["id"]: row for row in data()["widgets"]}
                assert (
                    rows[automatic["id"]]["availability"] == "stale"
                    and not rows[automatic["id"]]["available"]
                )
                assert (
                    rows[manual["id"]]["available"]
                    and rows[manual["id"]]["value"] == 18
                )
        finally:
            relay.shutdown()
            relay.server_close()


if __name__ == "__main__":
    for name, fn in tuple(globals().items()):
        if name.startswith("t_"):
            fn()
            print("PASS", name)
