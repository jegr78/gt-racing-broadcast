#!/usr/bin/env python3
"""Real CLI jobs and replay HTTP with synthetic Control Center browser controls."""
import asyncio
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "src/scripts"), str(ROOT / "src/ui")]
from engineer_ui_fixture import Fixture, gate
import telemetry_replay
import ui_ops
import ui_jobs
import ui_server


async def main():
    from playwright.async_api import async_playwright
    with tempfile.TemporaryDirectory(prefix="racecast-replay-browser-") as temp:
        fixture = Fixture(temp)
        fixture.access().update(enabled=True, expected=0)
        relay = fixture.console(with_live=True, solo=True)
        fixture.telemetry.replay = telemetry_replay.Controller(fixture.telemetry, fixture.root / fixture.profile / "telemetry-recordings")
        fixture.live._clock = time.monotonic
        Path(fixture.source.path).write_bytes(Path(fixture.live_path).read_bytes())
        from test_gt7_laps import _index, FakeTracks, FakeCars
        index = _index(fixture.source.path)
        fixture.telemetry.reload_data(FakeCars(), FakeTracks())
        context = fixture.context()
        recording = dict(name=fixture.source.name, rec=index['rec'], indexed=True, recording=False,
                         laps=len(index['laps']), duration_s=index['end_ts']-index['start_ts'],
                         start_ts=index['start_ts'], started='2023-11-14T22:13:20+00:00',
                         size=Path(fixture.source.path).stat().st_size, track=index['track'])
        context['telemetry_recordings'] = lambda: dict(ok=True,recordings=[recording])
        context['telemetry_laps'] = lambda *args,**kw: dict(ok=True,recording=recording,laps=index['laps'],
            best_sectors=index.get('best_sectors',[]),theoretical_best=index.get('theoretical_best'),unindexed=0,sector_m=200)
        context['telemetry_lap'] = lambda name,session,lap,**kw: dict(ok=True,
            lap=next(r for r in index['laps'] if r['session']==int(session) and r['lap']==int(lap)),sector_m=200)
        read_access = context['engineer_read']
        context['engineer_read'] = lambda payload: dict(read_access(payload),analysis_available=True)
        context["ops"], context["build_argv"] = ui_ops.OPS, ui_ops.build_argv
        # Real CLI handlers in isolated children, with only their local profile/port seams set.
        code = ("import sys;sys.path.insert(0,sys.argv[1]);import racecast as rc;"
                "rc.RELAY_PORT=int(sys.argv[2]);rc._foreign_relay_profile=lambda:'';"
                "rc._telemetry_rec_dir=lambda:sys.argv[3];"
                "rc.telemetry_import_cmd(sys.argv[6:]) if sys.argv[5]=='import' else rc.telemetry_replay_cmd(sys.argv[6:])")
        context["jobs"] = ui_jobs.JobManager(lambda args: [sys.executable, "-c", code, str(ROOT / "src"),
            str(relay.server_port), str(fixture.root / fixture.profile / "telemetry-recordings"), *args])
        ui = gate.m.ThreadingHTTPServer(("127.0.0.1", 0), ui_server.make_handler(context))
        threading.Thread(target=ui.serve_forever, daemon=True).start()
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch()
                page = await browser.new_page(viewport={"width":1440,"height":1050})
                errors = []
                page.on("pageerror", lambda exc: errors.append(str(exc)))
                await page.goto(f"http://127.0.0.1:{ui.server_port}/")
                await page.evaluate("() => showView('telemetry')")
                await page.locator("#tm-replay:not([disabled])").wait_for()
                await page.locator("#tm-replay-from").fill("1")
                await page.locator("#tm-replay").click()
                try:
                    await page.wait_for_function("() => document.getElementById('tm-replay-status').textContent.includes('Replay ·')",timeout=8000)
                except Exception:
                    print("UI error",await page.locator("#tm-err").inner_text())
                    print("Jobs",[(j.op,j.exit_code,j.lines) for j in context["jobs"].jobs.values()])
                    raise
                assert fixture.telemetry.replay.status()["active"]
                if "--shots" in sys.argv:
                    await page.screenshot(path=str(ROOT / "src/docs/wiki/images/cc-telemetry.png"))
                token = gate.m.console_auth.mint_token(gate.SECRET, "engineer")
                desk = await browser.new_page(viewport={"width":1440,"height":1050})
                await desk.goto(f"http://127.0.0.1:{relay.server_port}/console/race-engineer?t={token}")
                await desk.wait_for_function("() => document.getElementById('status').textContent.includes('Replay ·')")
                assert fixture.source.name not in await desk.locator("#status").inner_text()
                if "--shots" in sys.argv:
                    await desk.screenshot(path=str(ROOT / "src/docs/wiki/images/console-race-engineer-replay.png"), full_page=True)
                panel = await browser.new_page(viewport={"width":1440,"height":1400})
                await panel.goto(f"http://127.0.0.1:{relay.server_port}/panel")
                await panel.locator("#stReplay:not([hidden])").wait_for()
                await panel.wait_for_function("() => teleMarkerBtn && !teleMarkerBtn.hidden")
                assert await panel.locator("#stReplay").inner_text() == "REPLAY"
                hud = await browser.new_page(viewport={"width":1920,"height":1080})
                await hud.goto(f"http://127.0.0.1:{relay.server_port}/hud")
                await hud.locator("#tele-replay:not([hidden])").wait_for()
                await hud.add_style_tag(content="body {background:#152131}")
                if "--shots" in sys.argv:
                    await panel.screenshot(path=str(ROOT / "src/docs/wiki/images/director-panel-solo.png"), full_page=True)
                    await hud.screenshot(path=str(ROOT / "src/docs/wiki/images/hud-telemetry-replay.png"))
                await panel.evaluate("() => teleMarkerBtn.click()")
                await hud.wait_for_function("() => document.getElementById('tele-replay').hidden")
                assert not fixture.telemetry._replay_marker
                await panel.evaluate("() => teleMarkerBtn.click()")
                await hud.wait_for_function("() => !document.getElementById('tele-replay').hidden")
                await page.locator("#tm-replay-stop").click()
                await page.wait_for_function("() => document.getElementById('tm-replay-status').textContent.includes('Live UDP')")
                await desk.wait_for_function("() => !EngineerDesk.state.snapshot.replay.active")
                assert not fixture.telemetry.replay.status()["active"]
                fixture.relay.obs_stats = {"stream_active": True}
                await page.locator("#tm-replay").click()
                await page.get_by_role("button",name="Start replay",exact=True).wait_for()
                assert not fixture.telemetry.replay.status()["active"], "On-air replay started before confirmation"
                await page.get_by_role("button",name="Start replay",exact=True).click()
                await page.wait_for_function("() => document.getElementById('tm-replay-status').textContent.includes('Replay ·')")
                assert fixture.telemetry.replay.status()["active"]
                await page.locator("#tm-replay-stop").click()
                await page.wait_for_function("() => document.getElementById('tm-replay-status').textContent.includes('Live UDP')")
                imported = Path(temp) / "imported.gt7rec"
                imported.write_bytes(Path(fixture.live_path).read_bytes())
                imported.with_suffix(".laps.json").write_text('{"version":8}',encoding="utf8")
                page.once("dialog", lambda dialog: dialog.accept(str(imported)))
                await page.get_by_role("button",name="Import recording",exact=True).click()
                target = fixture.root / fixture.profile / "telemetry-recordings" / imported.name
                deadline = time.monotonic() + 10
                while not target.with_suffix(".laps.json").exists() and time.monotonic() < deadline:
                    await asyncio.sleep(.05)
                assert target.read_bytes() == imported.read_bytes()
                assert target.with_suffix(".laps.json").read_bytes() == imported.with_suffix(".laps.json").read_bytes()
                assert not errors, errors
                await browser.close()
                print("PASS Control Center real CLI replay jobs, Engineer label and stop")
        finally:
            fixture.telemetry.replay.stop()
            ui.shutdown(); ui.server_close()
            relay.shutdown(); relay.server_close()
            fixture.live.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
