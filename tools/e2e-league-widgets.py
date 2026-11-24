#!/usr/bin/env python3
"""Exercise the league service widgets in a real browser against the simulated service.

Maintainer and CI tool, not shipped. A synthetic profile loads the simulated league
event; the Control Center builder places both widgets with their sample values and saves
them; a relay handler sends the live state to the simulated service; the Director Panel
shows a widget and the overlay page renders its value as text; a dead service hides it
after the 30 s hold and the Director Panel names the reason. --shots refreshes
cc-league-widgets.png and director-panel-league.png.

Run with the Playwright Python environment (see tools/CLAUDE.md).
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools" / "event-sim" / "fakes"))
import e2e_safety  # noqa: E402
import test_console_gate as gate  # noqa: E402
import test_racecast as cli  # noqa: E402
import test_ui_server as ui  # noqa: E402
import league_sim  # noqa: E402
import league_result  # noqa: E402
import league_service  # noqa: E402
import league_widgets  # noqa: E402
from test_league_result import _Source  # noqa: E402

KEY = "e2e-league-widgets-key"
HOSTILE = '<img src=x onerror="window.__pwned=1">'
SCENES = (("hud", 1460, "/hud"), ("intermission", 300, "/intermission"), ("splitscreen", 700, "/splitscreen/hud"))
SNAP = {"mode": "race", "state": "racing", "lap": 12, "entries": [
    {"pos": 1, "entry": "e1", "driver": "Ann Weber", "team": "Red Bulls", "laps": 12},
    {"pos": 2, "entry": "e2", "driver": "Bo Lind", "team": "Blue Sharks", "laps": 12},
    {"pos": 3, "entry": "e3", "driver": "Cy Moreau", "team": "Red Bulls", "laps": 12},
    {"pos": 4, "entry": "e4", "driver": "Dee Okafor", "team": "Blue Sharks", "laps": 11}]}


def event():
    doc = league_sim.default_events()[league_sim.DEFAULT_REF]
    doc["widgets"][0]["sample"] = {"values": [{"label": "Red Bulls", "value": "0"},
                                              {"label": "Blue Sharks", "value": "0"}]}
    doc["widgets"][1]["sample"] = {"value_title": "Pts", "rows": [
        {"rank": 1, "name": "Ann Weber", "value": "25"}, {"rank": 2, "name": "Bo Lind", "value": "18"}]}
    return {league_sim.DEFAULT_REF: doc}


def builder(page, overlay, shots):
    """Place both widgets in the HUD through the real dialog and save."""
    page.wait_for_function("() => ovState.layout && ovState.slots.length > 0")
    page.evaluate("openBuilderModal()")
    assert not page.evaluate("ovState.slots.some(s => s.id.startsWith('league-widget-'))"), \
        "an unplaced widget is no slot on the canvas"
    page.click("#league-open")
    dialog = page.locator("#league-dialog")
    for _ in range(2):
        dialog.get_by_role("button", name="Place in this scene").first.click()
    dialog.get_by_role("button", name="Close", exact=True).click()
    score = page.locator("#ov-stage").locator("#league-widget-match-score")
    table = page.locator("#ov-stage").locator("#league-widget-standings")
    assert score.locator(".lw-label").nth(1).text_content() == "Blue Sharks"
    assert table.locator(".lw-name").first.text_content() == "Ann Weber"
    page.select_option("#ov-slotpick", "league-widget-standings")
    for prop, value in (("left", 1460), ("top", 330)):
        page.evaluate("([p, v]) => { const f = ovState.fields[p]; f.value = v; f.dispatchEvent(new Event('input'));"
                      " f.dispatchEvent(new Event('change')); }", [prop, value])
    page.select_option("#ov-slotpick", "league-widget-match-score")
    page.evaluate("() => { const f = ovState.fields.left; f.value = 1460; f.dispatchEvent(new Event('input'));"
                  " f.dispatchEvent(new Event('change')); }")
    page.evaluate("saveOverlay()")
    page.wait_for_function("() => document.getElementById('overlay-save').textContent.includes('Saved')")
    css = (overlay / "hud.css").read_text(encoding="utf-8")
    assert "#league-widget-standings { left: 1460px; top: 330px; width: 420px; height: 330px; font-size: 24px;" in css, css
    if shots:
        page.locator("#ov-modal .ovmodal-card").screenshot(path=str(ROOT / "src/docs/wiki/images/cc-league-widgets.png"))
    for scene, left, _path in SCENES[1:]:
        page.select_option("#ov-scene", scene)
        page.wait_for_function("(s) => ovState.page === s", arg=scene)
        placed = page.evaluate("ovState.slots.some(s => s.id === 'league-widget-match-score')")
        assert placed == (scene == "splitscreen"), f"{scene}: a new Split layout starts from the HUD's, {placed}"
        if not placed:
            page.click("#league-open")
            dialog.get_by_role("button", name="Place in this scene").first.click()
            dialog.get_by_role("button", name="Close", exact=True).click()
        page.select_option("#ov-slotpick", "league-widget-match-score")
        page.evaluate("(v) => { const f = ovState.fields.left; f.value = v; f.dispatchEvent(new Event('input'));"
                      " f.dispatchEvent(new Event('change')); }", left)
        page.evaluate("saveOverlay()")
        page.wait_for_function("() => document.getElementById('overlay-save').textContent.includes('Saved')")
    page.select_option("#ov-scene", "hud")
    page.wait_for_function("() => ovState.page === 'hud' && ovState.shadow.getElementById('league-widget-match-score')")
    page.evaluate("(label) => RacecastLeague.render(ovState.shadow, {widgets: [{id: 'match-score', form: 'score',"
                  " title: 'Match', holdFor: null, value: {values: [{label, value: '1'}]}}]})", HOSTILE)
    assert score.locator(".lw-label").text_content() == HOSTILE, "the canvas sets a label as text"
    assert score.locator("img").count() == 0 and not page.evaluate("window.__pwned")
    print("  [PASS] the builder places the widgets per scene with their sample values and saves their style")


def live_pages(browser, relay_port, sim, shots):
    """The Director shows a widget, the HUD renders its value, a dead service hides it."""
    panel = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    panel.goto(f"http://127.0.0.1:{relay_port}/panel")
    panel.click('.navbtn[data-area="race"]')
    panel.wait_for_function("() => document.querySelectorAll('#leagueWidgets .lwrow').length === 2")
    rows = panel.locator("#leagueWidgets .lwrow")
    assert rows.nth(0).locator("button").text_content() == "SHOW", "default hidden"
    rows.nth(0).locator("button").click()
    panel.wait_for_function("() => document.querySelector('#leagueWidgets .lwrow button').textContent === 'HIDE'")
    hud = browser.new_page(viewport={"width": 1920, "height": 1080})
    hud.on("pageerror", lambda error: errors.append(str(error)))
    hud.goto(f"http://127.0.0.1:{relay_port}/hud")
    hud.wait_for_function("() => !document.getElementById('league-widget-match-score').hidden")
    labels = hud.locator("#league-widget-match-score .lw-label").all_text_contents()
    assert labels == ["Red Bulls", "Blue Sharks"], labels
    assert hud.locator("#league-widget-standings").get_attribute("hidden") is not None, "the hidden one stays hidden"
    sim.answer("live", body={"v": 1, "widgets": {"match-score": {"values": [
        {"label": HOSTILE, "value": "7"}]}}})
    hud.wait_for_function("() => document.querySelector('#league-widget-match-score .lw-value').textContent === '7'")
    assert hud.locator("#league-widget-match-score .lw-label").text_content() == HOSTILE
    assert hud.locator("#league-widget-match-score img").count() == 0 and not hud.evaluate("window.__pwned")
    sim.clear_answers()
    for _scene, left, path in SCENES[1:]:
        view = browser.new_page(viewport={"width": 1920, "height": 1080})
        view.on("pageerror", lambda error: errors.append(str(error)))
        view.goto(f"http://127.0.0.1:{relay_port}{path}")
        view.wait_for_function("() => !document.getElementById('league-widget-match-score').hidden")
        box = view.locator("#league-widget-match-score").bounding_box()
        assert box["x"] == left, f"{path}: each scene keeps its own placement: {box}"
        placed = 2 if path == "/splitscreen/hud" else 1      # the Split layout started from the HUD's
        assert view.locator("[data-league-widget]").count() == placed, f"{path}: only the placed widgets"
        view.close()
    panel.wait_for_function("() => document.querySelector('#leagueWidgets .lwrow .lwstate').textContent.startsWith('On air')")
    if shots:
        panel.locator("#leagueSec").screenshot(path=str(ROOT / "src/docs/wiki/images/director-panel-league.png"))
    print("  [PASS] the Director shows a widget and the HUD renders the service's text as text")
    sim.fail("service", status=503)
    hud.wait_for_function("() => document.getElementById('league-widget-match-score').hidden", timeout=45000)
    panel.wait_for_function("() => document.querySelector('#leagueWidgets .lwrow .lwstate').textContent"
                            ".includes('no good answer from the league service')", timeout=10000)
    reason = panel.locator("#leagueWidgets .lwrow .lwstate").first.text_content()
    assert "HTTP 503" in reason, reason
    assert not errors, errors
    print(f"  [PASS] 30 s after the last good answer the widget hides: {reason}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", action="store_true")
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright
    m = cli.m
    with tempfile.TemporaryDirectory(prefix="league-widgets-") as td, \
            league_sim.LeagueSim(key=KEY, events=event()) as sim:
        cli._mk_active_profile(td)
        profile = Path(td) / "profiles/demo"
        (profile / "profile.env").write_text("NAME=Demo Racing\nKIND=endurance\n", encoding="utf-8")
        overlay = profile / "overlay"
        runtime = Path(td) / "runtime/demo"
        runtime.mkdir(parents=True)
        cfg, problem = league_service.settings(sim.url, KEY, sim.env())
        assert cfg is not None, problem
        loaded = league_service.fetch_event(cfg, league_sim.DEFAULT_REF)
        league_service.store(league_service.path_in(str(runtime)), league_sim.DEFAULT_REF, loaded)
        league_widgets.remember(league_widgets.path_in(str(profile)), loaded["widgets"])
        ctx = ui._ctx()
        ctx["active_profile"] = m._active_profile_name
        ctx.update(version="dev build",
                   status=lambda: {"relay": {"alive": False}, "streams": [], "companion": {"running": False}},
                   profiles=lambda: {"ok": True, "active": "demo", "profiles": [
                       {"name": "demo", "display": "Demo Racing", "kind": "endurance"}]},
                   update_check=lambda force=False: {"ok": True, "note": "development build"},
                   overlay_asset_serve=m.overlay_asset_serve, overlay_font_serve=m.overlay_font_serve,
                   overlay_slots=m.overlay_slots_data, overlay_layout_read=m.overlay_layout_read_data,
                   overlay_layout_write=m.overlay_layout_write_data, overlay_fonts=m.overlay_fonts_list_data,
                   overlay_bg=m.overlay_bg_path, audience_read=m.audience_read_data,
                   audience_write=m.audience_write_data)
        server, port = ui._serve(ctx)
        live = league_widgets.Live(lambda: (cfg, ""), lambda: SNAP, league_service.path_in(str(runtime)),
                                   overlay_dir=str(overlay))
        exporter = league_result.Exporter(str(runtime), lambda: (cfg, ""), _Source())
        stop = threading.Event()
        threading.Thread(target=live.run, args=(stop,), daemon=True).start()
        original_handler = gate.m.make_handler

        def handler(*a, **kw):
            kw.update(overlay_dir=str(overlay), hud_source=SimpleNamespace(data=lambda: {}),
                      hud_path=str(ROOT / "src/obs/hud.html"), intermission_path=str(ROOT / "src/obs/intermission.html"),
                      league_live=live, league_exporter=exporter)
            return original_handler(*a, **kw)

        with patch.object(gate.m, "make_handler", side_effect=handler):
            relay = gate._serve()
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(args=["--renderer-process-limit=2", "--num-raster-threads=1"])
                try:
                    page = browser.new_page(viewport={"width": 1600, "height": 1100})
                    page.goto(f"http://127.0.0.1:{port}/#profile")
                    builder(page, overlay, args.shots)
                    live_pages(browser, relay.server_address[1], sim, args.shots)
                finally:
                    browser.close()
            calls = sim.calls(call="live")
            assert calls and all(c["body"]["ref"] == league_sim.DEFAULT_REF for c in calls)
            assert KEY not in json.dumps(live.director())
        finally:
            stop.set()
            live.client.wake()
            relay.shutdown()
            server.shutdown()
    print("ALL PASS")


if __name__ == "__main__":
    contained = e2e_safety.enter(__file__, sys.argv[1:])
    if contained is not None:
        raise SystemExit(contained)
    main()
