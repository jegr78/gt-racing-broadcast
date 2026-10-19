#!/usr/bin/env python3
"""Actual crew/Director/local UI workflows with cached synthetic asset aliases."""

import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import test_racecast as cli  # noqa: E402
import test_ui_server as ui  # noqa: E402
import test_console_gate as gate  # noqa: E402
import asset_metadata as metadata  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", action="store_true")
    parser.add_argument(
        "--companion", help="Full-import into this disposable local Companion URL only"
    )
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(prefix="asset-alias-ui-") as td:
        root = Path(td)
        original = cli._mk_active_profile(td)
        graphics = root / "runtime/demo/graphics"
        media = root / "runtime/demo/media"
        graphics.mkdir(parents=True)
        media.mkdir(parents=True)
        for name in ("Standings", "Schedule", "Race Results", "Overlay"):
            shutil.copy2(
                ROOT / "src/assets/brands/porsche.png", graphics / (name + ".png")
            )
        (media / "trailer.mp4").write_bytes(b"fixture-media")

        def rename(label="Community standings"):
            metadata.refresh(
                graphics,
                [
                    ["Name", "Alias", "URL", "Internal"],
                    ["Standings", label, "", "FALSE"],
                    ["Schedule", "Community schedule", "", "FALSE"],
                    ["Race Results", "Private alias sentinel", "", "TRUE"],
                    ["Overlay", "League frame", "", "FALSE"],
                ],
                "graphics",
            )

        rename()
        metadata.refresh(
            media,
            [["Name", "Alias", "URL"], ["Trailer Video", "League presentation", ""]],
            "media",
        )
        ctx = ui._ctx()
        ctx.update(
            version="dev build",
            status=lambda: {
                "relay": {"alive": False},
                "streams": [],
                "companion": {"running": False},
            },
            asset_files=lambda: cli.m.assets_files_data(
                roots={"graphics": str(graphics), "media": str(media)}, profile="demo"
            ),
            overlay_slots=cli.m.overlay_slots_data,
            overlay_layout_read=cli.m.overlay_layout_read_data,
            overlay_fonts=cli.m.overlay_fonts_list_data,
            overlay_asset_serve=cli.m.overlay_asset_serve,
            asset_roots=lambda: {"graphics": str(graphics), "media": str(media)},
            update_check=lambda force=False: {"ok": True, "note": "development build"},
            profiles=lambda: {
                "ok": True,
                "active": "demo",
                "profiles": [
                    {"name": "demo", "display": "Demo Racing", "kind": "endurance"}
                ],
            },
        )
        local, local_port = ui._serve(ctx)
        relay = gate._serve(graphics_dir=str(graphics), graphics_take="direct")
        relay_port = relay.server_address[1]
        fake_obs = gate._TakeFakeObs()
        try:
            with patch.object(gate.m, "_obs_ws", fake_obs), sync_playwright() as pw:
                browser = pw.chromium.launch()
                errors = []
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda error: errors.append(error.stack))
                page.goto(f"http://127.0.0.1:{local_port}/")
                page.locator("[data-nav=profile]").click()
                page.wait_for_function("() => activeProfile === 'demo'")
                page.evaluate("fetchAssetFiles()")
                assert (
                    "Community standings" in page.locator("#gfx-gallery").inner_text()
                ), "Local gallery must show the cached alias"
                assert (
                    page
                    .locator("#gfx-gallery .cap")
                    .filter(has_text="Community standings")
                    .get_attribute("title")
                    == "Standings.png"
                )
                assert (
                    "League presentation" in page.locator("#media-gallery").inner_text()
                )
                gallery_before = page.locator("#gfx-gallery").inner_text()
                def wrong_profile(route):
                    data = cli.m.assets_files_data(
                        roots={"graphics": str(graphics), "media": str(media)},
                        profile="other",
                    )
                    data["graphics"][0]["displayName"] = "Foreign profile sentinel"
                    route.fulfill(json=data)

                page.route("**/api/assets/files", wrong_profile)
                page.evaluate("fetchAssetFiles()")
                assert page.locator("#gfx-gallery").inner_text() == gallery_before, (
                    "Foreign profile metadata must not replace the active gallery"
                )
                page.unroute("**/api/assets/files", wrong_profile)

                def switched_profile(route):
                    data = cli.m.assets_files_data(
                        roots={"graphics": str(graphics), "media": str(media)},
                        profile="other",
                    )
                    page.evaluate("activeProfile = 'other'")
                    data["graphics"][0]["displayName"] = "Stale response sentinel"
                    route.fulfill(json=data)

                page.route("**/api/assets/files", switched_profile)
                page.evaluate("fetchAssetFiles()")
                assert page.locator("#gfx-gallery").inner_text() == gallery_before, (
                    "A response started under a different active profile must be ignored"
                )
                page.unroute("**/api/assets/files", switched_profile)
                page.evaluate("activeProfile = 'demo'")
                page.locator("#gfx-gallery .thumb").filter(
                    has_text="Community standings"
                ).click()
                assert (
                    page.locator("#lightbox-cap").inner_text() == "Community standings"
                )
                rename("Producer preview rename")
                page.evaluate("fetchAssetFiles()")
                assert (
                    page.locator("#lightbox-cap").inner_text()
                    == "Producer preview rename"
                ), (
                    "An open producer preview must follow the same canonical asset after a rename"
                )
                rename()
                page.evaluate("fetchAssetFiles()")
                page.evaluate("closeLightbox()")
                if args.shots:
                    page.locator("#gfx-gallery").scroll_into_view_if_needed()
                    page.screenshot(
                        path=str(ROOT / "src/docs/wiki/images/cc-profile.png")
                    )
                page.evaluate("loadOverlay()")
                assert page.locator("#ov-frame-label").inner_text() == "Frame: League frame"
                assert page.locator("#ov-frame-label").get_attribute("title") == "Overlay.png"
                if args.shots:
                    page.locator("#overlay-popout").click()
                    page.locator("#ov-modal .ovmodal-card").screenshot(
                        path=str(ROOT / "src/docs/wiki/images/cc-overlay-builder.png")
                    )
                    page.evaluate("closeBuilderModal()")
                for who, path, image in [
                    ("alice", "/console/cockpit", "console-cockpit.png"),
                    ("dave", "/console/race-control", "console-race-control.png"),
                ]:
                    desk = browser.new_page(viewport={"width": 1440, "height": 1000})
                    desk.on("pageerror", lambda error: errors.append(error.stack))
                    desk.goto(f"http://127.0.0.1:{relay_port}{path}?t={gate._tok(who)}")
                    tile = desk.locator(".gtile").filter(has_text="Community standings")
                    tile.wait_for()
                    assert (
                        "Private alias sentinel"
                        not in desk.locator("body").inner_text()
                    )
                    tile.locator(".gthumb").click()
                    assert (
                        desk.locator("dialog .gname").inner_text()
                        == "Community standings"
                    )
                    desk.get_by_role("button", name="Close", exact=True).click()
                    if who == "alice":
                        tile.get_by_role(
                            "button", name="Put on air", exact=True
                        ).click()
                        desk.wait_for_function(
                            "() => !document.querySelector('.gtake')?.disabled"
                        )
                        assert ("item", "Stint", "Standings", True) in fake_obs.calls, (
                            fake_obs.calls
                        )
                    if args.shots:
                        tile.scroll_into_view_if_needed()
                        desk.screenshot(path=str(ROOT / "src/docs/wiki/images" / image))
                    rename("Renamed board")
                    desk.locator(".gtile").filter(has_text="Renamed board").wait_for()
                    rename()
                    desk.locator(".gtile").filter(
                        has_text="Community standings"
                    ).wait_for()
                    if who == "alice":
                        code, body = gate._post(
                            relay_port,
                            "/console/obs/graphics/mode",
                            gate._tok("bob"),
                            body={"role": "commentator", "mode": "request"},
                        )
                        assert code == 200, body
                        tile = desk.locator(".gtile").filter(
                            has_text="Community standings"
                        )
                        tile.get_by_role("button", name="Request", exact=True).click()
                        tile.get_by_role(
                            "button", name="Requested", exact=True
                        ).wait_for()
                        rename("Renamed while pending")
                        desk.wait_for_function(
                            "() => document.querySelector('#gfxMsg').textContent.includes('Community standings')"
                        )
                        code, body = gate._get(
                            relay_port,
                            "/console/obs/graphics/requests",
                            gate._tok("bob"),
                        )
                        request = json.loads(body)["requests"][0]
                        assert (
                            code == 200
                            and request["displayName"] == "Community standings"
                        )
                        # Allow both polling loops to observe the renamed definition.
                        desk.wait_for_timeout(3500)
                        assert (
                            desk
                            .locator(".gtile")
                            .filter(has_text="Community standings")
                            .get_by_role("button", name="Requested", exact=True)
                            .count()
                            == 1
                        )
                        assert (
                            desk
                            .locator(".gtile")
                            .filter(has_text="Renamed while pending")
                            .count()
                            == 0
                        )
                        code, body = gate._post(
                            relay_port,
                            f"/console/obs/graphics/request/{request['id']}",
                            gate._tok("bob"),
                            body={"action": "decline"},
                        )
                        assert code == 200, body
                        desk.locator(".gtile").filter(
                            has_text="Renamed while pending"
                        ).wait_for()
                        gate._post(
                            relay_port,
                            "/console/obs/graphics/mode",
                            gate._tok("bob"),
                            body={"role": "commentator", "mode": "direct"},
                        )
                        rename()
                    desk.close()
                director = browser.new_page(viewport={"width": 1600, "height": 1100})
                director.on("pageerror", lambda error: errors.append(error.stack))
                director.goto(
                    f"http://127.0.0.1:{relay_port}/console/panel?t={gate._tok('bob')}"
                )
                director.locator(".navbtn[data-area=graphics]").click()
                director.locator("#gfxBus button").filter(
                    has_text="Community standings"
                ).wait_for()
                director.locator(".navbtn[data-area=setup]").click()
                director.locator("#gfxList a").filter(
                    has_text="Community standings"
                ).click()
                assert director.locator("#asset-preview[open]").count() == 1, (
                    "Director preview must identify the aliased asset"
                )
                assert (
                    director.locator("#asset-preview .asset-caption").inner_text()
                    == "Community standings"
                )
                rename("Preview rename")
                director.locator("#asset-preview .asset-caption").filter(
                    has_text="Preview rename"
                ).wait_for()
                director.locator("#asset-preview").get_by_role(
                    "button", name="Close", exact=True
                ).click()
                rename()
                director.locator(".navbtn[data-area=graphics]").click()
                director.locator("#gfxBus button").filter(
                    has_text="Community standings"
                ).wait_for()
                assert (
                    director
                    .locator("#pgmBus button")
                    .filter(has_text="League presentation")
                    .count()
                    == 1
                )
                assert (
                    director
                    .locator("#scnBus button")
                    .filter(has_text="League presentation")
                    .count()
                    == 0
                )
                assert (
                    director
                    .locator("#pgmBus button")
                    .filter(has_text="INTERMISSION")
                    .count()
                    == 1
                )
                hostile = '<img src=x onerror="window.aliasExecuted=true">'
                rename(hostile)
                director.locator("#gfxBus button").filter(has_text=hostile).wait_for()
                assert director.locator("#gfxBus img").count() == 0
                assert not director.evaluate("window.aliasExecuted || false")
                rename()
                director.locator("#gfxBus button").filter(
                    has_text="Community standings"
                ).wait_for()
                if args.shots:
                    director.locator(".navbtn[data-area=graphics]").click()
                    director.screenshot(
                        path=str(ROOT / "src/docs/wiki/images/director-panel.png")
                    )
                assert not errors, errors
                browser.close()
                if args.companion:
                    from e2e_alias_companion import run as companion_run

                    companion_run(
                        pw,
                        args.companion.rstrip("/"),
                        relay_port,
                        rename,
                        fake_obs,
                        td,
                        args.shots,
                    )
                if args.shots:
                    for name in (
                        "cc-profile.png", "cc-overlay-builder.png",
                        "console-cockpit.png", "console-race-control.png",
                        "director-panel.png",
                    ):
                        target = ROOT / "src/docs/slides/assets/img" / name
                        if target.exists():
                            shutil.copy2(ROOT / "src/docs/wiki/images" / name, target)
                print(
                    "PASS actual local/crew/Director aliases, live rename, canonical actions, inert text"
                )
        finally:
            relay.shutdown()
            relay.server_close()
            local.shutdown()
            local.server_close()
            cli.m._env_base, cli.m._runtime_base_dir = original


if __name__ == "__main__":
    main()
