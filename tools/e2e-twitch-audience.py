#!/usr/bin/env python3
"""Exercise the shipped Twitch dialog and cached widgets without provider access."""

import argparse
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import test_racecast as cli
import test_ui_server as ui
import test_console_gate as gate
import test_audience_twitch_connection as fixtures
import audience_proxy
import http_util

LOCAL_OPEN = http_util.open_url


def local_only(url, **kwargs):
    assert url.startswith("http://127.0.0.1:"), "Fixture blocked external network"
    return LOCAL_OPEN(url, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", action="store_true")
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright

    m = cli.m
    with (
        tempfile.TemporaryDirectory(prefix="twitch-audience-") as td,
        fixtures.fixture() as (service, clock, api, cache, _definitions),
    ):
        original = cli._mk_active_profile(td)
        (Path(td) / "profiles/demo/profile.env").write_text(
            "NAME=Demo Racing\nKIND=endurance\n", encoding="utf-8"
        )
        runtime = gate.m.audience_widgets.Runtime(
            Path(td) / "profiles/demo/audience.json", cache=cache, clock=clock.time
        )
        original_handler = gate.m.make_handler

        def handler(*a, **kw):
            return original_handler(
                *a,
                **kw,
                twitch_service=service,
                audience_runtime=runtime,
                overlay_dir=str(Path(td) / "profiles/demo/overlay"),
                hud_path=str(ROOT / "src/obs/hud.html"),
                hud_source=SimpleNamespace(data=lambda: {}),
                intermission_path=str(ROOT / "src/obs/intermission.html"),
            )

        with patch.object(gate.m, "make_handler", side_effect=handler):
            relay = gate._serve()
        ctx = ui._ctx()
        ctx.update(
            version="dev build",
            profiles=lambda: {
                "ok": True,
                "active": "demo",
                "profiles": [
                    {"name": "demo", "display": "Demo Racing", "kind": "endurance"}
                ],
            },
            status=lambda: {
                "relay": {"alive": False},
                "streams": [],
                "companion": {"running": False},
            },
            update_check=lambda force=False: {"ok": True, "note": "development build"},
            overlay_asset_serve=m.overlay_asset_serve,
            overlay_font_serve=m.overlay_font_serve,
            overlay_slots=m.overlay_slots_data,
            overlay_layout_read=m.overlay_layout_read_data,
            overlay_layout_write=m.overlay_layout_write_data,
            overlay_fonts=m.overlay_fonts_list_data,
            overlay_bg=m.overlay_bg_path,
            audience_read=m.audience_read_data,
            audience_write=m.audience_write_data,
            audience_twitch=m.audience_twitch_data,
        )
        server, port = ui._serve(ctx)
        try:
            with (
                patch.object(
                    m,
                    "_audience_provider_client",
                    side_effect=lambda profile, provider="youtube": (
                        audience_proxy.Client(
                            profile, relay.server_address[1], provider=provider
                        )
                    ),
                ),
                patch.object(http_util, "open_url", side_effect=local_only),
                sync_playwright() as pw,
            ):
                browser = pw.chromium.launch()
                page = browser.new_page(viewport={"width": 1600, "height": 1100})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{port}/#profile")
                page.wait_for_function(
                    "() => ovState.layout && ovState.slots.length > 0"
                )
                page.evaluate("openBuilderModal()")
                page.click("#audience-open")
                page.fill("#audience-label", "Active subscriptions")
                page.select_option("#audience-source", "twitch:viewers")
                assert page.locator("#audience-mode option[value=net]").evaluate(
                    "el=>el.disabled"
                ), "Viewers offered net change"
                page.select_option("#audience-source", "twitch:subscriptions")
                assert not page.locator("#audience-mode option[value=net]").evaluate(
                    "el=>el.disabled"
                ), "Active subscriptions cannot use net change"
                assert (
                    "does not count all new-sub"
                    in page.locator("#audience-source-note").inner_text()
                )
                page.fill("#audience-target", "750")
                page.click("#twitch-open")
                page.wait_for_function(
                    "() => twitchSession && document.getElementById('twitch-status').textContent.includes('Disconnected')"
                )
                page.fill("#twitch-client", fixtures.CLIENT)
                page.fill("#twitch-channel", "https://twitch.tv/racing")
                page.click("#twitch-verify")
                page.wait_for_function(
                    "() => document.getElementById('twitch-status').textContent.includes('starting')"
                )
                assert not api.calls
                service.tick()
                page.wait_for_function(
                    "() => !document.getElementById('twitch-approval').hidden"
                )
                assert page.locator("#twitch-code").inner_text() == fixtures.USER
                assert (
                    page.locator("#twitch-approval-link").get_attribute("href")
                    == "https://www.twitch.tv/activate"
                )
                if args.shots:
                    shot(page, "cc-audience-twitch-approval")

                def approve():
                    clock.advance(5)
                    service.tick()
                    service.tick()
                    service.tick()
                    page.wait_for_function(
                        "() => !document.getElementById('twitch-activate').disabled"
                    )

                approve()
                assert not service.status()["connected"], (
                    "Approval activated without explicit confirmation"
                )
                assert all(
                    secret not in page.content()
                    for secret in (fixtures.PRIVATE, fixtures.ACCESS, fixtures.REFRESH)
                ), "Tokens or private device code reached browser markup"
                page.fill("#twitch-channel", "another_channel")
                assert page.locator("#twitch-activate").is_disabled(), (
                    "Edited selection left activation enabled"
                )
                page.evaluate("() => twitchAction('activate')")
                assert not service.status()["connected"], (
                    "Edited selection activated through callback"
                )
                page.evaluate("() => twitchPoll(twitchSession)")
                assert page.locator("#twitch-activate").is_disabled(), (
                    "Poll revived activation for edited selection"
                )
                page.fill("#twitch-channel", "racing")
                page.click("#twitch-verify")
                page.wait_for_function(
                    "() => document.getElementById('twitch-status').textContent.includes('starting')"
                )
                service.tick()
                approve()
                status = page.locator("#twitch-status").inner_text()
                assert (
                    "Output channel: Racing League [12345]" in status
                    and "Authorized account: racing [12345]" in status
                )
                assert "Active subscriptions: permitted" in status
                if args.shots:
                    shot(page, "cc-audience-twitch")
                original_writer = service.files.writer

                def failed_public(path, body, **kwargs):
                    if Path(path) == service.files.public:
                        raise OSError("fixture-private-disk")
                    return original_writer(path, body, **kwargs)

                service.files.writer = failed_public
                before = len(api.calls)
                page.click("#twitch-activate")
                page.wait_for_function(
                    "() => document.getElementById('twitch-status').textContent.includes('Could not save')"
                )
                assert not page.locator("#twitch-activate").is_disabled(), (
                    "Storage failure prevented retrying the verified RAM candidate"
                )
                assert not service.status()["connected"] and len(api.calls) == before, (
                    "Failed activation repeated OAuth work"
                )
                service.files.writer = original_writer
                page.click("#twitch-activate")
                page.wait_for_function(
                    "() => document.getElementById('twitch-status').textContent.includes('Connected')"
                )
                service.tick()
                assert (
                    service.sample("viewers").value == 0
                    and service.sample("subscriptions").value == 761
                )
                page.click("#twitch-close")
                if args.shots:
                    shot(page, "cc-audience-widgets")
                page.click("#audience-submit")
                page.wait_for_function("() => ovState.audience.widgets.length === 1")
                page.select_option("#audience-mode", "net")
                page.fill("#audience-target", "20")
                page.click("#audience-submit")
                page.wait_for_function(
                    "() => ovState.audience.widgets[0].mode === 'net'"
                )
                page.locator("#audience-list p").filter(
                    has_text="Active subscriptions"
                ).get_by_role(
                    "button", name="Capture baseline / reset", exact=True
                ).click()
                page.wait_for_function(
                    "() => ovState.audience.widgets[0].baseline !== null"
                )
                assert (
                    page.evaluate("ovState.audience.widgets[0].baseline.value") == 761
                )
                subscription = page.evaluate("ovState.audience.widgets[0].id")
                page.get_by_role("button", name="New widget", exact=True).click()
                page.fill("#audience-label", "Live viewers")
                page.select_option("#audience-source", "twitch:viewers")
                page.click("#audience-submit")
                page.wait_for_function("() => ovState.audience.widgets.length === 2")
                viewer = page.evaluate("ovState.audience.widgets[1].id")
                page.locator("#audience-dialog").get_by_role(
                    "button", name="Close", exact=True
                ).click()
                page.click("#overlay-save")
                page.wait_for_function(
                    "() => document.getElementById('overlay-save').textContent.includes('Saved')"
                )
                page.select_option("#ov-scene", "intermission")
                page.wait_for_function(
                    "() => ovState.page === 'intermission' && ovState.slots.some(s=>s.id==='ichat')"
                )
                page.click("#audience-open")
                for label in ("Active subscriptions", "Live viewers"):
                    row = page.locator("#audience-list p").filter(has_text=label)
                    row.get_by_role(
                        "button", name="Place in this scene", exact=True
                    ).click()
                    page.wait_for_function(
                        "(id) => Object.hasOwn(ovState.layout.slots,id)",
                        arg=subscription if label == "Active subscriptions" else viewer,
                    )
                page.locator("#audience-dialog").get_by_role(
                    "button", name="Close", exact=True
                ).click()
                page.click("#overlay-save")
                page.wait_for_function(
                    "() => document.getElementById('overlay-save').textContent.includes('Saved')"
                )
                live = []
                for scene in ("hud", "intermission"):
                    view = browser.new_page()
                    response = view.goto(
                        f"http://127.0.0.1:{relay.server_address[1]}/{scene}"
                    )
                    assert response.status == 200, (
                        scene,
                        response.status,
                        view.locator("body").inner_text(),
                    )
                    view.wait_for_function(
                        '(id) => { const node=document.getElementById(id); return node && !node.hidden && node.querySelector(".audience-value").textContent === "0"; }',
                        arg=viewer,
                    )
                    view.wait_for_function(
                        "(id) => !document.getElementById(id).hidden", arg=subscription
                    )
                    live.append(view)
                clock.advance(60)
                api.subscriptions = 758
                service.tick()
                for view in live:
                    view.wait_for_function(
                        '(id) => document.getElementById(id).querySelector(".audience-value").textContent === "-3"',
                        arg=subscription,
                    )
                api.live = False
                clock.advance(30)
                service.tick()
                for view in live:
                    view.wait_for_function(
                        "(id) => document.getElementById(id).hidden", arg=viewer
                    )
                    assert not view.locator("#" + subscription).evaluate(
                        "el=>el.hidden"
                    ), "Offline viewers hid the independent subscription metric"
                api.live = True
                clock.advance(30)
                service.tick()
                for view in live:
                    view.wait_for_function(
                        "(id) => !document.getElementById(id).hidden", arg=viewer
                    )
                page.click("#audience-open")
                page.click("#twitch-open")
                page.wait_for_function(
                    "() => document.getElementById('twitch-status').textContent.includes('Connected')"
                )
                page.click("#twitch-disconnect")
                page.wait_for_function(
                    "() => document.getElementById('twitch-status').textContent.includes('Disconnected')"
                )
                for view in live:
                    for ident in (subscription, viewer):
                        view.wait_for_function(
                            "(id) => document.getElementById(id).hidden", arg=ident
                        )
                    view.close()

                def stale_profile(route):
                    response = route.fetch()
                    page.evaluate(
                        "() => { ovState.active='other'; document.getElementById('twitch-channel').value='New profile draft'; document.getElementById('twitch-status').textContent='New profile selected'; }"
                    )
                    route.fulfill(response=response)

                page.route("**/api/audience/twitch", stale_profile)
                page.evaluate("() => twitchPoll(twitchSession)")
                assert page.input_value("#twitch-channel") == "New profile draft"
                assert (
                    page.locator("#twitch-status").inner_text()
                    == "New profile selected"
                ), "Old profile response overwrote new form"
                page.unroute("**/api/audience/twitch", stale_profile)
                page.evaluate("ovState.active='demo'")
                assert not errors, errors
                browser.close()
                print(
                    "PASS actual Twitch UI, public approval, edited selection, explicit activation, exact net baseline, live HUD/Intermission zero/offline/negative change/disconnect and profile races"
                )
        finally:
            server.shutdown()
            server.server_close()
            relay.shutdown()
            relay.server_close()
            m._env_base, m._runtime_base_dir = original


def shot(page, name):
    wiki = ROOT / "src/docs/wiki/images" / (name + ".png")
    page.screenshot(path=str(wiki))
    (ROOT / "src/docs/slides/assets/img" / wiki.name).write_bytes(wiki.read_bytes())


if __name__ == "__main__":
    main()
