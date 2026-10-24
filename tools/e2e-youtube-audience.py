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
    # Fake Sheet Channel tab: no tab first (fixed video only), rows for follow mode.
    sheet = fixtures.Sheet(rows=None)
    with (
        tempfile.TemporaryDirectory(prefix="youtube-audience-") as td,
        fixtures.fixture(**sheet.options()) as (service, clock, api, cache, _defs),
    ):
        api.starts.update(fixtures.STARTS)
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
            docs=m.docs_data,
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
                assert page.locator("#youtube-mode-follow").is_disabled(), (
                    "Follow mode offered without a Sheet Channel tab"
                )
                assert page.locator("#youtube-follow-note").is_visible()
                assert page.locator("#youtube-mode-video").is_checked()
                assert page.locator("#youtube-video").is_visible()
                page.wait_for_function(
                    "() => document.getElementById('youtube-key-help').href"
                    ".endsWith('/wiki/HUD-Overlays#youtube-data-api-key')"
                )
                assert page.locator("#youtube-key-help").is_visible(), (
                    "Key how-to link stayed hidden with a wiki URL"
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
                page.evaluate("() => youtubeAction('activate')")
                assert not service.status()["connected"], (
                    "Edited target was activated through the action callback"
                )
                page.evaluate("() => youtubePoll(youtubeSession)")
                assert page.locator("#youtube-activate").is_disabled(), (
                    "Status polling revived activation for edited targets"
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
                original_writer = service.files.writer

                def failed_public(path, body, **kwargs):
                    if Path(path) == service.files.public:
                        raise OSError("fixture-private-disk")
                    return original_writer(path, body, **kwargs)

                service.files.writer = failed_public
                before = len(api.calls)
                page.click("#youtube-activate")
                page.wait_for_function(
                    "() => document.getElementById('youtube-status').textContent.includes('Could not save')"
                )
                assert not page.locator("#youtube-activate").is_disabled(), (
                    "YouTube storage failure prevented retrying the verified RAM candidate"
                )
                assert not service.status()["connected"] and len(api.calls) == before, (
                    "YouTube failed activation repeated verification"
                )
                service.files.writer = original_writer
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
                assert "≈" not in preview.text_content(), (
                    "Subscribers show the plain number, without an approximate marker"
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
                follow_pass(page, service, clock, api, sheet, args.shots)

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
                    "PASS actual YouTube UI: source/precision/verify/activate/zero/net baseline/disconnect/follow live"
                )
        finally:
            server.shutdown()
            server.server_close()
            relay.shutdown()
            relay.server_close()
            m._env_base, m._runtime_base_dir = original


def status_has(page, text):
    page.wait_for_function(
        "t => document.getElementById('youtube-status').textContent.includes(t)",
        arg=text,
    )


def note_has(page, text):
    page.wait_for_function(
        "t => document.getElementById('youtube-follow-note').textContent.includes(t)",
        arg=text,
    )


def follow_verify(page, service):
    page.fill("#youtube-key", fixtures.KEY)
    page.click("#youtube-verify")
    status_has(page, "pending")
    service.tick()
    page.wait_for_function(
        "() => !document.getElementById('youtube-activate').disabled"
    )


def follow_pass(page, service, clock, api, sheet, shots):
    """Follow the Sheet channel's live broadcast through every dialog state."""
    second = "@Second"
    # A Channel tab without YouTube rows offers no follow mode.
    sheet.rows = []
    note_has(page, "No YouTube channel in the Sheet Channel tab")
    assert page.locator("#youtube-follow-note").is_visible()
    assert page.locator("#youtube-mode-follow").is_disabled()
    sheet.rows = [fixtures.LEAGUE, second]
    sheet.live = [fixtures.VID_A, fixtures.VID_B]
    page.wait_for_function(
        "() => !document.getElementById('youtube-mode-follow').disabled"
    )
    assert not page.locator("#youtube-follow-note").is_visible()
    page.check("#youtube-mode-follow")
    assert page.locator("#youtube-follow-channel").is_visible()
    assert not page.locator("#youtube-video").is_visible(), "Fixed fields in follow"
    options = page.locator("#youtube-follow-channel option").all_inner_texts()
    assert options == [fixtures.LEAGUE, second], options
    assert page.input_value("#youtube-follow-channel") == fixtures.LEAGUE
    follow_verify(page, service)
    setup = service.status()["setup"]
    assert setup["targets"]["follow"]["channel"] == fixtures.LEAGUE, setup
    text = page.locator("#youtube-status").inner_text()
    assert "Following: Producer channel [" + fixtures.CHANNEL + "]" in text, text
    newer = "Output race " + fixtures.VID_B + " [" + fixtures.VID_B + "]"
    assert "Live: " + newer in text, ("Newer live video not shown", text)
    page.select_option("#youtube-follow-channel", second)
    assert page.locator("#youtube-activate").is_disabled(), (
        "Changing the Channel tab choice left activation enabled"
    )
    page.select_option("#youtube-follow-channel", fixtures.LEAGUE)
    page.check("#youtube-mode-video")
    assert page.locator("#youtube-activate").is_disabled()
    page.check("#youtube-mode-follow")
    follow_verify(page, service)
    if shots:
        shot(page, "cc-audience-youtube")
    page.click("#youtube-activate")
    status_has(page, "Connected")
    assert service.status()["mode"] == "follow"
    service.tick()
    status_has(page, "Live: " + newer)
    # Reopened right after a relay start: the Channel tab is not read yet, so
    # the stored row must survive until its rows arrive (in another order).
    page.click("#youtube-close")
    sheet.rows, sheet.loaded = [], False
    page.click("#youtube-open")
    note_has(page, "Loading the Sheet Channel tab")
    assert page.locator("#youtube-mode-follow").is_checked()
    page.evaluate("() => youtubeAction('verify')")
    status_has(page, "Loading the Sheet Channel tab")
    text = page.locator("#youtube-status").inner_text()
    assert "Choose a YouTube channel" not in text, ("Choose shown while loading", text)
    assert service.status()["setup"]["phase"] == "idle", "Empty choice was posted"
    # A Channel tab read that keeps failing shows its error, not a forever note.
    sheet.error = "HTTPError: HTTP Error 404: Not Found"
    failed = "Could not read the Sheet Channel tab (HTTPError: HTTP Error 404: Not Found); retrying"
    note_has(page, failed)
    status_has(page, failed)   # the loading guard line follows the note
    text = page.locator("#youtube-status").inner_text()
    assert "Loading the Sheet Channel tab" not in text, ("Stale guard line", text)
    page.evaluate("() => youtubeAction('verify')")
    status_has(page, failed)
    sheet.error = None
    sheet.rows, sheet.loaded = [second, fixtures.LEAGUE], True
    page.wait_for_function(
        "() => document.getElementById('youtube-follow-channel').options.length === 2"
    )
    assert page.input_value("#youtube-follow-channel") == fixtures.LEAGUE, (
        "The stored follow row was replaced by the first Channel tab row"
    )
    assert not page.locator("#youtube-follow-note").is_visible()
    page.wait_for_function(
        "() => !document.getElementById('youtube-status').textContent"
        ".includes('Could not read the Sheet Channel tab')"
    )
    # Cached rows stand in for a failing Sheet read: say so, keep follow usable.
    sheet.stale = "HTTPError: HTTP Error 404: Not Found"
    note_has(page, "Sheet read failed (HTTPError: HTTP Error 404: Not Found); "
             "using the cached Channel tab")
    assert page.locator("#youtube-follow-note").is_visible()
    assert not page.locator("#youtube-mode-follow").is_disabled()
    sheet.stale = None
    page.wait_for_function(
        "() => document.getElementById('youtube-follow-note').hidden"
    )
    for live, rows, line in (
        ([], None, "Not live right now"),
        ([fixtures.VID_A], None, "Live: Output race " + fixtures.VID_A),
        (None, None, "Lookup failed; keeping the last video"),
        ([fixtures.VID_A], ["@Other"], "Channel tab changed; verify again"),
    ):
        sheet.live = live
        if rows is not None:
            sheet.rows = rows
        clock.advance(30)
        service.tick()
        status_has(page, line)
    # A live video without a title shows just its ID.
    untitled = page.evaluate(
        "() => youtubeFollowLines('live', {id: 'vidCCCCCCCC', title: ''})"
    )
    assert untitled == ["Live: [vidCCCCCCCC]"], untitled
    sheet.rows = None
    clock.advance(30)
    service.tick()
    status_has(page, "Follow mode needs the Sheet Channel tab")
    assert page.locator("#youtube-mode-follow").is_disabled()
    assert page.locator("#youtube-mode-follow").is_checked(), (
        "The active follow connection lost its mode in the dialog"
    )
    page.click("#youtube-disconnect")
    status_has(page, "Disconnected")
    assert not any(c[0] == "search" for c in api.calls)


def shot(page, name):
    wiki = ROOT / "src/docs/wiki/images" / (name + ".png")
    page.screenshot(path=str(wiki))
    slides = ROOT / "src/docs/slides/assets/img" / wiki.name
    slides.write_bytes(wiki.read_bytes())


if __name__ == "__main__":
    main()
