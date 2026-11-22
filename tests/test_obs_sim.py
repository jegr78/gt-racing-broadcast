#!/usr/bin/env python3
"""The stateful simulated OBS (#1229). Run: python3 tests/test_obs_sim.py"""
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "event-sim", "fakes"))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import obs_sim  # noqa: E402
import obs_state  # noqa: E402
import obs_ws  # noqa: E402

for _key in [k for k in os.environ if k.startswith("RACECAST_")]:
    del os.environ[_key]
os.environ["RACECAST_SIMULATION"] = "1"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _sim(**kw):
    sim = obs_sim.ObsSim(**kw).start()
    os.environ["RACECAST_SIM_OBS_WS"] = sim.url
    return sim


def _png_size(raw):
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "a PNG signature"
    return struct.unpack(">II", raw[16:24])


def _png_pixel(raw, x, y):
    w, _ = _png_size(raw)
    data, pos = b"", 8
    while pos < len(raw):
        n = struct.unpack(">I", raw[pos:pos + 4])[0]
        if raw[pos + 4:pos + 8] == b"IDAT":
            data += raw[pos + 8:pos + 8 + n]
        pos += 12 + n
    rows = zlib.decompress(data)
    start = y * (w * 3 + 1) + 1 + x * 3
    return tuple(rows[start:start + 3])


def _wait(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


class RawClient:
    """A minimal subscribing obs-websocket client, the way Companion or a dock connects."""

    def __init__(self, port, subs=None):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=3)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(obs_ws.handshake_request("127.0.0.1", port, key))
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += self.sock.recv(4096)
        self.buf = obs_ws.parse_handshake(resp, key)
        assert self.next()["op"] == 0, "Hello first"
        ident = {"rpcVersion": 1}
        if subs is not None:
            ident["eventSubscriptions"] = subs
        self.send({"op": 1, "d": ident})
        assert self.next()["op"] == 2, "Identified after Identify"

    def send(self, obj):
        self.sock.sendall(obs_ws.encode_frame(json.dumps(obj).encode()))

    def next(self):
        while True:
            parsed = obs_ws.decode_frame(self.buf)
            if parsed:
                _, payload, self.buf = parsed
                return json.loads(payload)
            self.buf += self.sock.recv(65536)

    def request(self, rtype, data=None):
        self.send({"op": 6, "d": {"requestType": rtype, "requestId": "x", "requestData": data or {}}})
        seen = []
        while True:
            msg = self.next()
            if msg["op"] == 7:
                return msg["d"], seen
            seen.append(msg["d"]["eventType"])

    def close(self):
        self.sock.close()


def t_seeds_scenes_inputs_and_items_from_the_collection():
    st = obs_state.ObsState(obs_state.DEFAULT_COLLECTION)
    snap = st.snapshot()
    assert snap["collection"] == "GT Racing Endurance", snap["collection"]
    assert snap["scene"] == "Splitscreen", "the collection's program scene is on air at start"
    assert {"Stint", "Standby", "Intermission"} <= set(snap["scenes"]), snap["scene_order"]
    stint = {i["source"]: i["enabled"] for i in snap["scenes"]["Stint"]}
    assert stint["Feed A"] is True and stint["Feed B"] is False, stint
    assert snap["inputs"]["Feed POV"]["muted"] is True, "muted inputs stay muted"
    assert snap["inputs"]["Feed A"]["settings"]["input"] == "http://127.0.0.1:53001"


def _demo_collection():
    with open(os.path.join(ROOT, "profiles", "demo", "profile.env"), encoding="utf-8") as fh:
        return next(line.split("=", 1)[1].strip() for line in fh if line.startswith("OBS_COLLECTION="))


def t_event_start_switches_to_the_demo_collection_name():
    sim = _sim()
    try:
        demo = _demo_collection()
        status, note = obs_ws.get_scene_collection(expected=demo)
        assert status["expected_present"] and not status["match"], (status, note)
        ok, note = obs_ws.set_scene_collection(demo)
        assert ok and note == "", note
        assert obs_ws.get_scene_collection(expected=demo)[0]["match"], "OBS now runs the demo name"
        names = [e["eventType"] for e in sim.state.events_since(0)]
        assert names[-2:] == ["CurrentSceneCollectionChanging", "CurrentSceneCollectionChanged"], names
        assert obs_ws.set_scene_collection("Some League")[0] is False, "an unknown name is refused"
    finally:
        sim.stop()


def t_extra_collection_names_are_listed():
    st = obs_state.ObsState(obs_state.DEFAULT_COLLECTION, collection_names=["League X"])
    names = st.request("GetSceneCollectionList")["sceneCollections"]
    assert names[0] == "GT Racing Endurance" and "League X" in names, names
    try:
        st.request("SetCurrentSceneCollection", {"sceneCollectionName": "Some League"})
    except obs_state.ObsError as exc:
        assert exc.code == obs_state.NOT_FOUND
    else:
        raise AssertionError("OBS refuses a collection it does not list")


def _transform(st, scene, source):
    item = st.request("GetSceneItemId", {"sceneName": scene, "sourceName": source})["sceneItemId"]
    return st.request("GetSceneItemTransform", {"sceneName": scene, "sceneItemId": item})["sceneItemTransform"]


def t_item_transforms_come_from_the_collection():
    st = obs_state.ObsState(obs_state.DEFAULT_COLLECTION)
    feed_b = _transform(st, "Splitscreen", "Feed B")
    want = {"positionX": 960.0, "positionY": 270.0, "boundsType": "OBS_BOUNDS_SCALE_INNER",
            "boundsWidth": 960.0, "boundsHeight": 540.0, "alignment": 5, "width": 960.0, "height": 540.0}
    assert {k: feed_b[k] for k in want} == want, feed_b
    pov = _transform(st, "Stint", "Feed POV")
    assert (pov["positionX"], pov["positionY"], pov["width"], pov["height"]) == (1496.0, 644.0, 384.0, 216.0), pov
    hud = _transform(st, "Stint", "Stint HUD")
    assert (hud["boundsType"], hud["width"], hud["height"]) == ("OBS_BOUNDS_NONE", 1920.0, 1080.0), hud
    item = st.request("GetSceneItemId", {"sceneName": "Stint", "sceneItemId": 0, "sourceName": "Feed POV"})
    st.request("SetSceneItemTransform", {"sceneName": "Stint", "sceneItemId": item["sceneItemId"],
                                         "sceneItemTransform": {"boundsWidth": 480.0, "width": 1.0,
                                                                "bogus": 1}})
    pov = _transform(st, "Stint", "Feed POV")
    assert (pov["boundsWidth"], pov["width"]) == (480.0, 480.0), "width follows the bounds, never a Set"
    assert "bogus" not in pov, "unknown transform keys are ignored, as in OBS"


def t_unbounded_size_follows_source_scale_and_crop():
    collection = {"name": "C", "sources": [
        {"id": "browser_source", "name": "Page", "settings": {"width": 800, "height": 600}},
        {"id": "scene", "name": "S", "settings": {"items": [
            {"name": "Page", "id": 1, "visible": True, "scale": {"x": 0.5, "y": 2.0},
             "crop_left": 100, "crop_top": 50}]}}]}
    st = obs_state.ObsState(collection)
    tr = _transform(st, "S", "Page")
    assert (tr["sourceWidth"], tr["width"], tr["height"]) == (800.0, 350.0, 1100.0), tr


def t_strict_mode_answers_missing_things_like_obs():
    st = obs_state.ObsState(obs_state.DEFAULT_COLLECTION)
    for rtype, data in (("SetCurrentProgramScene", {"sceneName": "Nope"}),
                        ("GetSceneItemId", {"sceneName": "Stint", "sourceName": "Nope"}),
                        ("GetInputMute", {"inputName": "Nope"})):
        try:
            st.request(rtype, data)
        except obs_state.ObsError as exc:
            assert exc.code == obs_state.NOT_FOUND, (rtype, exc.code)
        else:
            raise AssertionError(f"{rtype} on a missing resource must fail")
    try:
        st.request("NoSuchRequest")
    except obs_state.ObsError as exc:
        assert exc.code == obs_state.UNKNOWN_REQUEST
    else:
        raise AssertionError("an unknown request type must fail in strict mode")
    assert st.requests_since(0)[-1]["code"] == obs_state.UNKNOWN_REQUEST, "the log keeps the error code"


def t_lenient_mode_keeps_the_screenshot_stand_in_answers():
    st = obs_state.ObsState(None, scene="Race", strict=False)
    st.set_image(b"\xff\xd8fixed")
    assert st.request("GetSceneItemId", {"sceneName": "Stint", "sourceName": "Feed A"}) == {"sceneItemId": 1}
    assert st.request("GetSceneItemEnabled", {"sceneName": "Stint", "sceneItemId": 1})["sceneItemEnabled"] is True
    assert st.request("GetInputList", {}) == {"inputs": []}, "no feed inputs: covers report no-feed"
    assert st.request("Whatever") == {}, "unknown requests succeed"
    st.request("StartStream")
    assert st.request("GetStreamStatus")["outputActive"] is False, "lenient answers stay fixed"
    assert st.request("GetSceneCollectionList") == {}, "no collection name to compare against"
    assert st.request("GetCurrentProgramScene")["currentProgramSceneName"] == "Race"
    shot = st.request("GetSourceScreenshot", {"sourceName": "Feed A", "imageFormat": "jpg"})
    assert obs_ws.parse_screenshot_data_uri(shot["imageData"]) == b"\xff\xd8fixed", "the fixed image wins"


def t_transition_runs_for_its_duration_then_ends():
    clock = Clock()
    st = obs_state.ObsState(obs_state.DEFAULT_COLLECTION, clock=clock)
    st.request("SetCurrentProgramScene", {"sceneName": "Standby"})
    snap = st.snapshot()
    assert snap["scene"] == "Standby" and snap["transition"]["active"], snap["transition"]
    clock.now += 0.31
    names = [e["eventType"] for e in st.events_since(0)]
    assert names == ["SceneTransitionStarted", "CurrentProgramSceneChanged", "SceneTransitionEnded"], names
    st.request("SetCurrentSceneTransition", {"transitionName": "Cut"})
    st.request("SetCurrentProgramScene", {"sceneName": "Stint"})
    assert not st.snapshot()["transition"]["active"], "a cut ends at once"


def t_studio_mode_preview_and_transition():
    st = obs_state.ObsState(obs_state.DEFAULT_COLLECTION)
    try:
        st.request("SetCurrentPreviewScene", {"sceneName": "Stint"})
    except obs_state.ObsError as exc:
        assert exc.code == obs_state.STUDIO_MODE_OFF
    else:
        raise AssertionError("preview needs studio mode")
    st.request("SetStudioModeEnabled", {"studioModeEnabled": True})
    st.request("SetCurrentPreviewScene", {"sceneName": "Stint"})
    st.request("TriggerStudioModeTransition")
    snap = st.snapshot()
    assert (snap["scene"], snap["preview"]) == ("Stint", "Splitscreen"), (snap["scene"], snap["preview"])


def t_outputs_keep_state_and_refuse_double_starts():
    clock = Clock()
    st = obs_state.ObsState(obs_state.DEFAULT_COLLECTION, clock=clock)
    st.request("StartStream")
    try:
        st.request("StartStream")
    except obs_state.ObsError as exc:
        assert exc.code == obs_state.OUTPUT_RUNNING
    else:
        raise AssertionError("a second StartStream must fail")
    clock.now += 2
    status = st.request("GetStreamStatus")
    assert status["outputActive"] and status["outputDuration"] == 2000, status
    st.request("StartRecord")
    st.request("PauseRecord")
    clock.now += 5
    st.request("ResumeRecord")
    clock.now += 1
    assert st.request("GetRecordStatus")["outputDuration"] == 1000, "paused time does not count"
    path = st.request("StopRecord")["outputPath"]
    assert path and st.snapshot()["record"] == {"active": False, "paused": False, "duration_ms": 0,
                                               "path": path, "count": 1}
    states = [e["eventData"]["outputState"] for e in st.events_since(0) if e["eventType"] == "StreamStateChanged"]
    assert states == [obs_state.STARTING, obs_state.STARTED], states


def t_stream_key_never_reaches_state_or_log():
    st = obs_state.ObsState(obs_state.DEFAULT_COLLECTION)
    st.request("SetStreamServiceSettings", obs_ws.stream_service_payload("twitch", "live_abc"))
    assert st.snapshot()["stream"]["key_set"] is True
    dumped = json.dumps([st.snapshot(), st.requests_since(0)])
    assert "live_abc" not in dumped, "the stream key stays out of checks and reports"
    assert st.request("GetStreamServiceSettings")["streamServiceSettings"]["key"] == "live_abc"


def t_input_settings_merge_unless_overlay_is_off():
    st = obs_state.ObsState(obs_state.DEFAULT_COLLECTION)
    st.request("SetInputSettings", {"inputName": "Feed A", "inputSettings": {"input": "http://127.0.0.1:53009"}})
    merged = st.snapshot()["inputs"]["Feed A"]["settings"]
    assert merged["input"] == "http://127.0.0.1:53009" and merged["is_local_file"] is False, "overlay merges"
    st.request("SetInputSettings", {"inputName": "Feed A", "inputSettings": {"x": 1}, "overlay": False})
    assert st.snapshot()["inputs"]["Feed A"]["settings"] == {"x": 1}, "overlay off replaces"


def t_media_cursor_advances_until_frozen():
    clock = Clock()
    st = obs_state.ObsState(obs_state.DEFAULT_COLLECTION, clock=clock)
    first = st.request("GetMediaInputStatus", {"inputName": "Feed A"})["mediaCursor"]
    clock.now += 1.5
    second = st.request("GetMediaInputStatus", {"inputName": "Feed A"})["mediaCursor"]
    assert second - first == 1500, (first, second)
    st.freeze_media("Feed A")
    clock.now += 3
    assert st.request("GetMediaInputStatus", {"inputName": "Feed A"})["mediaCursor"] == second
    st.freeze_media("Feed A", False)
    clock.now += 1
    assert st.request("GetMediaInputStatus", {"inputName": "Feed A"})["mediaCursor"] == second + 1000


def t_screenshot_shows_scene_and_visible_feeds():
    st = obs_state.ObsState(obs_state.DEFAULT_COLLECTION)
    assert [f["input"] for f in st.feeds("Stint")] == ["Feed A"]
    assert [f["port"] for f in st.feeds("Splitscreen")] == [53001, 53002]
    assert st.feeds("Standby") == []
    stint, _ = st.screenshot("Stint", 320)
    assert _png_size(stint) == (320, 180)
    assert _png_pixel(stint, 2, 100) == (192, 192, 192), "a feed shows colour bars"
    standby, _ = st.screenshot("Standby", 320)
    assert _png_pixel(standby, 2, 100) != (192, 192, 192), "a scene without a feed has no bars"
    item = st.request("GetSceneItemId", {"sceneName": "Stint", "sourceName": "Feed B"})["sceneItemId"]
    st.request("SetSceneItemEnabled", {"sceneName": "Stint", "sceneItemId": item, "sceneItemEnabled": True})
    assert st.screenshot("Stint", 320)[0] != stint, "a second visible feed changes the picture"
    plain = st.screenshot("Splitscreen", 320)[0]
    st.set_labels({"Feed A": "Stint 1 Comms 1"})
    assert st.screenshot("Splitscreen", 320)[0] != plain, "a label changes the feed's pattern"
    st.set_labels({"Feed A": None})
    assert st.screenshot("Splitscreen", 320)[0] == plain, "the same state renders the same bytes"


def t_relay_helpers_drive_the_sim_over_the_wire():
    sim = _sim()
    try:
        applied, note = obs_ws.reflect_feed_state("B", True, transition="fade", duration_ms=500)
        assert note == "", note
        snap = obs_sim.fetch(sim.http_url)
        stint = {i["source"]: i["enabled"] for i in snap["scenes"]["Stint"]}
        assert snap["scene"] == "Stint" and stint["Feed B"] and not stint["Feed A"], (snap["scene"], stint)
        assert snap["inputs"]["Feed A"]["muted"] and not snap["inputs"]["Feed B"]["muted"]
        assert (snap["transition"]["name"], snap["transition"]["duration"]) == ("Fade", 500)
        assert _wait(lambda: not obs_sim.fetch(sim.http_url)["transition"]["active"]), "the fade ends"
        ok, note = obs_ws.set_stream(True)
        assert ok and obs_sim.fetch(sim.http_url)["stream"]["active"], note
        data, note = obs_ws.get_program_screenshot(width=320)
        assert _png_size(data) == (320, 180), note
        _, note = obs_ws.set_scene_item_enabled("Stint", "Nope", True)
        assert note, "a missing scene item reaches the caller as an error"
    finally:
        sim.stop()


def t_events_reach_subscribers_only():
    sim = _sim()
    quiet = RawClient(sim.port, subs=0)
    loud = RawClient(sim.port)
    try:
        resp, seen = quiet.request("SetCurrentProgramScene", {"sceneName": "Standby"})
        assert resp["requestStatus"]["result"] and seen == [], (resp, seen)
        names = [loud.next()["d"]["eventType"] for _ in range(2)]
        assert names == ["SceneTransitionStarted", "CurrentProgramSceneChanged"], names
        resp, seen = quiet.request("GetCurrentProgramScene")
        assert seen == [] and resp["responseData"]["currentProgramSceneName"] == "Standby", seen
        quiet.send({"op": 3, "d": {"eventSubscriptions": obs_state.OUTPUTS}})
        assert quiet.next()["op"] == 2, "Reidentify answers Identified"
        _, seen = quiet.request("StartRecord")
        assert seen == ["RecordStateChanged", "RecordStateChanged"], seen
        resp, seen = loud.request("SetSceneItemTransform", {"sceneName": "Stint", "sceneItemId": 1,
                                                            "sceneItemTransform": {"positionX": 5.0}})
        assert resp["requestStatus"]["result"], resp
        assert "SceneItemTransformChanged" not in seen, "high-volume events need their own bit"
    finally:
        quiet.close()
        loud.close()
        sim.stop()


def t_http_endpoint_reads_and_pokes_state():
    sim = _sim()
    try:
        out = obs_sim.fetch(sim.http_url, "/request", {"requestType": "SetCurrentProgramScene",
                                                       "requestData": {"sceneName": "Intermission"}})
        assert out["ok"], out
        assert obs_sim.fetch(sim.http_url)["scene"] == "Intermission"
        bad = obs_sim.fetch(sim.http_url, "/request", {"requestType": "SetCurrentProgramScene",
                                                       "requestData": {"sceneName": "Nope"}})
        assert bad == {"ok": False, "code": obs_state.NOT_FOUND, "comment": bad["comment"]}, bad
        reqs = obs_sim.fetch(sim.http_url, "/requests?since=0")["requests"]
        assert [r["origin"] for r in reqs] == ["http", "http"], reqs
        obs_sim.fetch(sim.http_url, "/request", {"requestType": "GetStats"})
        snap = obs_sim.fetch(sim.http_url)
        assert snap["seq"]["requests"] == 2 and snap["reads"] == {"GetStats": 1}, "reads are counted, not logged"
        events = obs_sim.fetch(sim.http_url, "/events?since=0")
        assert "CurrentProgramSceneChanged" in [e["eventType"] for e in events["events"]]
        assert obs_sim.fetch(sim.http_url, f"/events?since={events['seq']}")["events"] == []
        assert obs_sim.fetch(sim.http_url, "/labels", {"Feed A": "Comms 1"})["labels"] == {"Feed A": "Comms 1"}
        assert obs_sim.fetch(sim.http_url, "/freeze", {"input": "Feed A"})["frozen"] == ["Feed A"]
        status, ctype, raw = sim.route("GET", "/program.png?width=200")
        assert status.startswith("200") and ctype == "image/png" and _png_size(raw)[0] == 200
        assert sim.route("GET", "/nope")[0].startswith("404")
        assert sim.env() == {"RACECAST_SIM_OBS_WS": sim.url}
    finally:
        sim.stop()


def t_fetch_refuses_a_non_loopback_host():
    try:
        obs_sim.fetch("http://192.0.2.1:4466")
    except ValueError:
        return
    raise AssertionError("checks only ever read a loopback obs-sim")


def t_obs_sim_py_still_serves_a_fixed_image():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    image = os.path.join(ROOT, "src", "assets", "brands", "audi.png")
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "obs-sim.py"), "--image", image,
                             "--port", str(port)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        assert "serving" in proc.stdout.readline().decode("utf-8", "replace"), "the banner names the image"
        os.environ["RACECAST_SIM_OBS_WS"] = f"ws://127.0.0.1:{port}"
        data, note = obs_ws.get_program_screenshot()
        with open(image, "rb") as fh:
            assert data == fh.read(), note
        assert obs_ws.get_current_program_scene()[0] == "Race"
        assert obs_ws.read_obs_state([("Stint", "Feed A")], ["Feed A"])[0]["sources"][0]["enabled"] is True
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        proc.stdout.close()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
