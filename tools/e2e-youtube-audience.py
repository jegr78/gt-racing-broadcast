#!/usr/bin/env python3
"""Exercise the shipped YouTube dialog and cached widgets without provider access."""

import argparse
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import test_racecast as cli
import test_ui_server as ui
import test_console_gate as gate
import test_audience_youtube_connection as fixtures
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
        tempfile.TemporaryDirectory(prefix="youtube-audience-") as td,
        fixtures.fixture() as (service, clock, api, cache, _definitions),
    ):
        original = cli._mk_active_profile(td)
        (Path(td) / "profiles/demo/profile.env").write_text(
            "NAME=Demo Racing\nKIND=endurance\n", encoding="utf-8"
        )
        runtime = gate.m.audience_widgets.Runtime(None, cache=cache)
        original_handler = gate.m.make_handler

        def handler(*a, **kw):
            return original_handler(
                *a, **kw, youtube_service=service, audience_runtime=runtime
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
            audience_youtube=m.audience_youtube_data,
        )
        server, port = ui._serve(ctx)
        try:
            with (
                patch.object(
                    m,
                    "_audience_provider_client",
                    side_effect=lambda profile: audience_proxy.Client(
                        profile, relay.server_address[1]
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
                page.fill("#audience-label", "Community likes")
                assert page.locator("#audience-source").count() == 1, (
                    "Widget source selection is missing"
                )
                page.select_option("#audience-source", "youtube:subscribers")
                assert page.locator("#audience-mode option[value=net]").evaluate(
                    "el=>el.disabled"
                ), (
                    "Rounded subscribers offered exact net mode",
                    errors,
                    page.input_value("#audience-source"),
                )
                page.select_option("#audience-source", "youtube:likes")
                page.fill("#audience-target", "750")
                page.click("#youtube-open")
                page.wait_for_function(
                    "() => youtubeSession && document.getElementById('youtube-status').textContent.includes('Disconnected')"
                )
                page.fill("#youtube-video", "https://youtu.be/" + fixtures.VIDEO)
                page.fill("#youtube-channel", "@Producer")
                page.fill("#youtube-key", fixtures.KEY)
                page.click("#youtube-verify")
                page.wait_for_function(
                    "() => document.getElementById('youtube-status').textContent.includes('pending')"
                )
                assert page.input_value("#youtube-key") == "" and not api.calls
                service.tick()
                page.wait_for_function(
                    "() => !document.getElementById('youtube-activate').disabled"
                )
                assert (
                    page
                    .locator("#youtube-status")
                    .inner_text()
                    .find("Output race " + fixtures.VIDEO)
                    >= 0
                )
                assert not service.status()["connected"], (
                    "Verification activated without confirmation"
                )
                page.fill("#youtube-video", fixtures.OTHER_VIDEO)
                assert page.locator("#youtube-activate").is_disabled(), (
                    "Editing a verified target left old activation enabled"
                )
                page.fill("#youtube-video", fixtures.VIDEO)
                page.fill("#youtube-key", fixtures.KEY)
                page.click("#youtube-verify")
                page.wait_for_function(
                    "() => document.getElementById('youtube-status').textContent.includes('pending')"
                )
                service.tick()
                page.wait_for_function(
                    "() => !document.getElementById('youtube-activate').disabled"
                )
                assert "Video channel:" in page.locator("#youtube-status").inner_text()
                if args.shots:
                    shot(page, "cc-audience-youtube")
                page.click("#youtube-activate")
                page.wait_for_function(
                    "() => document.getElementById('youtube-status').textContent.includes('Connected')"
                )
                service.tick()
                assert service.sample("likes").value == 0
                page.click("#youtube-close")
                page.click("#audience-submit")
                page.wait_for_function("() => ovState.audience.widgets.length === 1")
                assert page.evaluate("ovState.audience.widgets[0].source") == "youtube"
                page.select_option("#audience-mode", "net")
                page.click("#audience-submit")
                page.wait_for_function(
                    "() => ovState.audience.widgets[0].mode === 'net'"
                )
                row = page.locator("#audience-list p").filter(
                    has_text="Community likes"
                )
                row.get_by_role(
                    "button", name="Capture baseline / reset", exact=True
                ).click()
                page.wait_for_function(
                    "() => ovState.audience.widgets[0].baseline !== null"
                )
                assert page.evaluate("ovState.audience.widgets[0].baseline.value") == 0
                page.get_by_role("button", name="New widget", exact=True).click()
                page.fill("#audience-label", "Road to 30K")
                page.select_option("#audience-source", "youtube:subscribers")
                page.fill("#audience-target", "30000")
                page.fill("#audience-decimals", "6")
                page.click("#audience-submit")
                page.wait_for_function("() => ovState.audience.widgets.length === 2")
                subscriber = page.evaluate("ovState.audience.widgets[1].id")
                page.locator("#audience-dialog").get_by_role(
                    "button", name="Close", exact=True
                ).click()
                preview = page.locator("#ov-stage").locator(
                    "#" + subscriber + " .audience-value"
                )
                assert preview.text_content().startswith("≈"), (
                    "Offline preview claimed exact subscriber precision"
                )
                assert not ".000000" in preview.text_content(), (
                    "Offline preview invented decimal precision"
                )
                page.click("#audience-open")
                page.evaluate("audienceEdit(ovState.audience.widgets[1].id)")
                if args.shots:
                    shot(page, "cc-audience-widgets")
                page.click("#youtube-open")
                page.wait_for_function(
                    "() => document.getElementById('youtube-status').textContent.includes('Connected')"
                )
                page.click("#youtube-disconnect")
                page.wait_for_function(
                    "() => document.getElementById('youtube-status').textContent.includes('Disconnected')"
                )
                assert not service.sample("likes").availability == "available"
                assert fixtures.KEY not in page.content(), (
                    "Credential leaked into rendered markup"
                )

                def stale_profile(route):
                    response = route.fetch()
                    page.evaluate(
                        "() => { ovState.active='other'; document.getElementById('youtube-video').value='New profile draft'; document.getElementById('youtube-status').textContent='New profile selected'; }"
                    )
                    route.fulfill(response=response)

                page.route("**/api/audience/youtube", stale_profile)
                page.evaluate("() => youtubePoll(youtubeSession)")
                assert page.input_value("#youtube-video") == "New profile draft"
                assert (
                    page.locator("#youtube-status").inner_text()
                    == "New profile selected"
                ), "Old profile response overwrote the new form"
                page.unroute("**/api/audience/youtube", stale_profile)
                page.evaluate("ovState.active='demo'")
                assert not errors, errors
                browser.close()
                print(
                    "PASS actual YouTube UI: source/precision/verify/activate/zero/net baseline/disconnect"
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
    slides = ROOT / "src/docs/slides/assets/img" / wiki.name
    slides.write_bytes(wiki.read_bytes())


if __name__ == "__main__":
    main()
