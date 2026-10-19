#!/usr/bin/env python3
"""Exercise audience management, scene drafts and actual live pages using fixtures."""

import argparse
import json
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


def renderer_contracts():
    from playwright.sync_api import sync_playwright

    A = "audience-" + "a" * 32
    B = "audience-" + "b" * 32

    def markup(ident):
        return f'<div id="{ident}" data-audience-widget hidden><span class="audience-label"></span><span class="audience-value"></span><span class="audience-target"></span><span class="audience-unit"></span><div class="audience-track"><span class="audience-fill"></span></div><span class="audience-percent"></span></div>'

    def row(ident=A, **patch):
        return {
            "id": ident,
            "label": "Likes",
            "unit": "likes",
            "available": True,
            "value": 0,
            "target": 750,
            "fill": 0,
            "percentage": 0,
            "decimals": 0,
            "precision": {"exact": True},
            "holdFor": None,
            **patch,
        }

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_content(
            "<style>[hidden]{display:none!important}[data-audience-widget]{width:400px}.audience-track{width:100%;height:20px}.audience-fill{display:block;height:100%;background:green}</style>"
            + markup(A)
            + markup(B)
        )
        page.add_script_tag(path=str(ROOT / "src/ui/audience.js"))

        def render(rows, now=0):
            return page.evaluate(
                "([widgets,now])=>RacecastAudience.render(document,{widgets},now)",
                [rows, now],
            )

        assert render([row()]) is True
        assert page.locator("#" + A).is_visible()
        assert page.locator("#" + A + " .audience-value").text_content() == "0"
        assert render([row(value=761, fill=100, percentage=101.466)])
        assert page.locator("#" + A + " .audience-value").text_content() == "761"
        track = page.locator("#" + A + " .audience-track").bounding_box()
        fill = page.locator("#" + A + " .audience-fill").bounding_box()
        assert abs(track["width"] - fill["width"]) < 0.1
        assert page.locator("#" + A + " .audience-percent").text_content() == "101%"
        malicious = '<img src=x onerror="window.pwned=true">'
        render([row(label=malicious, unit="</span><script>bad</script>")])
        assert page.locator("#" + A + " .audience-label").text_content() == malicious
        assert page.locator("#" + A + " img").count() == 0 and not page.evaluate(
            "window.pwned||false"
        )
        render([row(value=29500, target=30000, precision={"exact": False})])
        assert page.locator("#" + A + " .audience-value").text_content() == "≈29,500"
        page.locator("#" + A).evaluate("el=>el.style.display='none'")
        render([row()])
        assert not page.locator("#" + A).is_visible(), (
            "Data refresh must not override operator CSS hiding"
        )
        page.locator("#" + A).evaluate("el=>el.style.display=''")
        render([row(holdFor=1), row(B, holdFor=None)], now=100)
        page.evaluate("RacecastAudience.expire(document,1101)")
        assert (
            not page.locator("#" + A).is_visible()
            and page.locator("#" + B).is_visible()
        )
        assert not page.evaluate(
            'RacecastAudience.render(document,{error:"offline"},1200)'
        )
        assert page.locator("#" + B).is_visible()
        render([])
        assert not page.locator("#" + B).is_visible(), (
            "Deleting shared definition must remove old on-air data"
        )
        assert not errors, errors
        browser.close()
    print(
        "PASS actual renderer: zero/exceeded/clamped bar/XSS/precision/CSS visibility/deadline/invalid data/delete"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", action="store_true")
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright

    renderer_contracts()
    m = cli.m
    with tempfile.TemporaryDirectory(prefix="audience-builder-") as td:
        original = cli._mk_active_profile(td)
        profile = Path(td) / "profiles/demo/profile.env"
        profile.write_text("NAME=Demo Racing\nKIND=endurance\n", encoding="utf-8")
        overlay = profile.parent / "overlay"
        ctx = ui._ctx()
        ctx.update(
            version="dev build",
            status=lambda: {
                "relay": {"alive": False},
                "streams": [],
                "companion": {"running": False},
            },
            profiles=lambda: {
                "ok": True,
                "active": "demo",
                "profiles": [
                    {"name": "demo", "display": "Demo Racing", "kind": "endurance"}
                ],
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
        )
        server, port = ui._serve(ctx)
        original_handler = gate.m.make_handler

        def handler(*a, **kw):
            kw.update(
                overlay_dir=str(overlay),
                hud_source=SimpleNamespace(data=lambda: {}),
                hud_path=str(ROOT / "src/obs/hud.html"),
                intermission_path=str(ROOT / "src/obs/intermission.html"),
                splitscreen_path=str(ROOT / "src/obs/splitscreen.html"),
            )
            return original_handler(*a, **kw)

        with patch.object(gate.m, "make_handler", side_effect=handler):
            relay = gate._serve()
        relay_port = relay.server_address[1]
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page(viewport={"width": 1600, "height": 1100})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{port}/#profile")
                page.wait_for_function(
                    "() => ovState.layout && ovState.slots.length > 0"
                )
                reload_requests = []

                def stale_profile_once(route):
                    response = route.fetch()
                    data = response.json()
                    reload_requests.append(data)
                    if len(reload_requests) == 1:
                        data["audience"]["active"] = "other"
                    route.fulfill(response=response, json=data)

                page.route("**/api/overlay/slots?page=*", stale_profile_once)
                page.evaluate("loadOverlay()")
                assert len(reload_requests) == 2, (
                    "Mixed-profile slot/layout reads must retry before rendering"
                )
                assert page.evaluate("ovState.audience.active === ovState.active")
                page.unroute("**/api/overlay/slots?page=*", stale_profile_once)
                assert not page.evaluate(
                    "ovState.slots.some(s=>s.id.startsWith('audience-'))"
                )
                page.evaluate("openBuilderModal()")
                page.click("#audience-open")
                page.fill("#audience-label", "Likes & <b>Subs</b>")
                page.fill("#audience-value", "761")
                page.fill("#audience-target", "750")
                page.click("#audience-submit")
                page.wait_for_function(
                    "() => ovState.audience.widgets.length === 1 && document.getElementById('audience-status').textContent.startsWith('Created')"
                )
                ident = page.evaluate("ovState.audience.widgets[0].id")
                page.locator("#audience-dialog").get_by_role(
                    "button", name="Close", exact=True
                ).click()
                widget = page.locator("#ov-stage").locator("#" + ident)
                assert widget.locator(".audience-value").text_content() == "18", (
                    "Preview must be sample, not live 761"
                )
                assert (
                    widget.locator(".audience-label").text_content()
                    == "Likes & <b>Subs</b>"
                )
                assert widget.locator("b").count() == 0
                page.select_option("#ov-slotpick", ident)
                page.evaluate(
                    "() => { const f=ovState.fields.left; f.value=1000; f.dispatchEvent(new Event('input')); f.dispatchEvent(new Event('change')); }"
                )
                page.evaluate("saveOverlay()")
                page.wait_for_function(
                    "() => document.getElementById('overlay-save').textContent.includes('Saved')"
                )
                page.evaluate(
                    "() => { const f=ovState.fields.presentation; f.value='number'; f.dispatchEvent(new Event('change')); ovReloadCanvasFromLayout(); }"
                )
                assert (
                    widget.locator(".audience-target").evaluate(
                        "el=>getComputedStyle(el).display"
                    )
                    == "none"
                ), "Saved presentation must survive a canvas rebuild"
                page.evaluate(
                    "() => { const f=ovState.fields.presentation; f.value='bar'; f.dispatchEvent(new Event('change')); }"
                )
                page.evaluate("saveOverlay()")
                page.wait_for_function(
                    "() => document.getElementById('overlay-save').textContent.includes('Saved')"
                )
                hud = json.loads(
                    (overlay / "layout-hud.json").read_text(encoding="utf-8")
                )
                assert hud["slots"][ident]["left"] == 1000
                live = []
                views = []
                for scene, left, path in [
                    ("intermission", 100, "/intermission"),
                    ("splitscreen", 500, "/splitscreen/hud"),
                ]:
                    page.select_option("#ov-scene", scene)
                    page.wait_for_function(
                        "(scene) => ovState.page === scene", arg=scene
                    )
                    if not page.evaluate(
                        "(id) => Object.hasOwn(ovState.layout.slots,id)", ident
                    ):
                        page.click("#audience-open")
                        page.get_by_role(
                            "button", name="Place in this scene", exact=True
                        ).click()
                        page.locator("#audience-dialog").get_by_role(
                            "button", name="Close", exact=True
                        ).click()
                    page.select_option("#ov-slotpick", ident)
                    page.evaluate(
                        "(left) => { const f=ovState.fields.left; f.value=left; f.dispatchEvent(new Event('input')); f.dispatchEvent(new Event('change')); }",
                        left,
                    )
                    page.evaluate("saveOverlay()")
                    page.wait_for_function(
                        "() => document.getElementById('overlay-save').textContent.includes('Saved')"
                    )
                    live.append((path, left))
                for path, left in [("/hud", 1000)] + live:
                    view = browser.new_page(viewport={"width": 1920, "height": 1080})
                    view.on("pageerror", lambda error: errors.append(str(error)))
                    view.goto(f"http://127.0.0.1:{relay_port}{path}")
                    view.wait_for_function(
                        '(id) => document.querySelector("#"+id+" .audience-value").textContent === "761"',
                        arg=ident,
                    )
                    assert view.locator("[data-audience-widget]").count() == 1
                    assert view.locator("#" + ident).bounding_box()["x"] == left
                    assert (
                        view.locator("#" + ident + " .audience-percent").text_content()
                        == "101%"
                    )
                    views.append(view)
                page.click("#audience-open")
                page.locator("#audience-dialog").get_by_role(
                    "button", name="Edit", exact=True
                ).click()
                page.fill("#audience-value", "762")
                page.fill("#audience-label", "Community likes")
                page.click("#audience-submit")
                page.wait_for_function(
                    "() => document.getElementById('audience-status').textContent === 'Shared data saved.'"
                )
                for view in views:
                    view.wait_for_function(
                        '(id) => document.querySelector("#"+id+" .audience-value").textContent === "762"',
                        arg=ident,
                    )
                assert (
                    json.loads(
                        (overlay / "layout-hud.json").read_text(encoding="utf-8")
                    )
                    == hud
                )
                if args.shots:
                    page.locator("#audience-dialog").screenshot(
                        path=str(ROOT / "src/docs/wiki/images/cc-audience-widgets.png")
                    )
                page.locator("#audience-dialog").get_by_role(
                    "button", name="Close", exact=True
                ).click()
                page.select_option("#ov-slotpick", ident)
                page.get_by_role(
                    "button", name="Remove from this scene", exact=True
                ).click()
                assert widget.count() == 0
                page.click("#overlay-undo")
                assert widget.count() == 1
                assert m.audience_read_data()["widgets"][0]["manualValue"] == 762
                page.click("#audience-open")
                page.get_by_role("button", name="New widget", exact=True).click()
                page.fill("#audience-label", "Community challenge")
                page.fill("#audience-value", "18")
                page.fill("#audience-unit", "hours")
                page.fill("#audience-target", "24")
                page.select_option("#audience-mode", "net")
                page.click("#audience-submit")
                page.wait_for_function(
                    "() => ovState.audience.widgets.length === 2 && document.getElementById('audience-status').textContent.startsWith('Created')"
                )
                second = page.evaluate("ovState.audience.widgets[1].id")
                row = page.locator("#audience-list p").filter(
                    has_text="Community challenge"
                )
                row.get_by_role(
                    "button", name="Capture baseline / reset", exact=True
                ).click()
                page.wait_for_function(
                    "() => ovState.audience.widgets[1].baseline !== null"
                )
                saved = m.audience_read_data()["widgets"][1]
                assert (
                    saved["baseline"]["value"] == 18
                    and saved["manualValue"] == 18
                    and saved["target"] == 24
                )
                # A newly created draft must not appear in an already airing source.
                assert all(view.locator("#" + second).count() == 0 for view in views)
                page.once("dialog", lambda dialog: dialog.accept())
                row.get_by_role(
                    "button", name="Delete shared widget", exact=True
                ).click()
                page.wait_for_function("() => ovState.audience.widgets.length === 1")
                page.locator("#audience-dialog").get_by_role(
                    "button", name="Close", exact=True
                ).click()
                page.click("#audience-open")
                page.locator("#audience-list p").filter(
                    has_text="Community likes"
                ).get_by_role("button", name="Edit", exact=True).click()

                def switched_after_write(route):
                    response = route.fetch()
                    data = response.json()
                    data["audience"]["active"] = "other"
                    route.fulfill(response=response, json=data)

                page.route("**/api/overlay/slots?page=*", switched_after_write)
                page.click("#audience-submit")
                page.wait_for_function(
                    "() => document.getElementById('audience-status').textContent === 'Active profile changed; reload the builder.'"
                )
                assert page.evaluate("ovState.audience.active") == "demo"
                page.unroute("**/api/overlay/slots?page=*", switched_after_write)
                assert not errors, errors
                browser.close()
                print(
                    "PASS real audience UI: create/place/style/save/shared-live/sample/XSS/remove/Undo"
                )
        finally:
            relay.shutdown()
            relay.server_close()
            server.shutdown()
            server.server_close()
            m._env_base, m._runtime_base_dir = original


if __name__ == "__main__":
    main()
