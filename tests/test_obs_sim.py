#!/usr/bin/env python3
"""The stateful simulated OBS (#1229). Run: python3 tests/test_obs_sim.py"""
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "event-sim", "fakes"))
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import obs_grab  # noqa: E402
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
    kw.setdefault("grabber", None)
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
            chunk = self.sock.recv(65536)
            assert chunk, "the server closed the session"
            self.buf += chunk

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
    assert [e["eventType"] for e in st.events_since(0)] == ["SceneTransitionStarted"], "OBS reports the scene at the end"
    seq = st.event_seq
    st.request("SetCurrentProgramScene", {"sceneName": "Standby"})
    assert st.event_seq == seq, "the scene it is already fading to starts nothing new"
    clock.now += 0.31
    names = [e["eventType"] for e in st.events_since(0)]
    assert names == ["SceneTransitionStarted", "SceneTransitionEnded", "CurrentProgramSceneChanged"], names
    seq = st.event_seq
    st.request("SetCurrentProgramScene", {"sceneName": "Standby"})
    assert st.event_seq == seq and not st.snapshot()["transition"]["active"], "the program scene again is a no-op"
    st.request("SetCurrentSceneTransition", {"transitionName": "Cut"})
    st.request("SetCurrentProgramScene", {"sceneName": "Stint"})
    assert not st.snapshot()["transition"]["active"], "a cut ends at once"
    assert st.events_since(0)[-1]["eventType"] == "CurrentProgramSceneChanged"


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


def _media(st, name):
    return st.request("GetMediaInputStatus", {"inputName": name})


def t_media_status_follows_obs():
    clock = Clock()
    st = obs_state.ObsState(obs_state.DEFAULT_COLLECTION, clock=clock)
    clock.now += 2
    status = _media(st, "Feed A")
    assert status["mediaState"] == "OBS_MEDIA_STATE_PLAYING" and status["mediaCursor"] == 2000, \
        "the cursor runs from the input's creation, not from the first question"
    assert status["mediaDuration"] == obs_state.LIVE_DURATION, "a live stream has no duration"
    st.freeze_media("Feed A", state="OBS_MEDIA_STATE_ENDED")
    ended = _media(st, "Feed A")
    assert (ended["mediaState"], ended["mediaCursor"], ended["mediaDuration"]) == ("OBS_MEDIA_STATE_ENDED", None, None)
    assert st.snapshot()["inputs"]["Feed A"]["frozen"], "the state shows the freeze"
    st.request("SetInputSettings", {"inputName": "Feed A", "inputSettings": {"input": FEED_A}})
    clock.now += 0.5
    reopened = _media(st, "Feed A")
    assert (reopened["mediaState"], reopened["mediaCursor"]) == ("OBS_MEDIA_STATE_PLAYING", 500), \
        "new settings reopen the media and end the freeze"
    assert not st.snapshot()["inputs"]["Feed A"]["frozen"]
    assert _media(st, "Overlay") == {"mediaState": "OBS_MEDIA_STATE_NONE", "mediaDuration": None,
                                     "mediaCursor": None}, "a non-media input answers like OBS"
    try:
        _media(st, "Stint")
    except obs_state.ObsError as exc:
        assert exc.code == obs_state.INVALID_RESOURCE_TYPE
    else:
        raise AssertionError("a scene is not an input")
    st.request("CreateInput", {"sceneName": "Stint", "inputName": "Clip", "inputKind": "ffmpeg_source",
                               "inputSettings": {"local_file": "clip.mp4", "is_local_file": True}})
    clock.now += 1
    assert _media(st, "Clip")["mediaCursor"] == 1000, "a new input starts at its creation"


def t_obs_refuses_stream_settings_while_streaming_and_lists_scenes_bottom_up():
    st = obs_state.ObsState(obs_state.DEFAULT_COLLECTION)
    st.request("StartStream")
    try:
        st.request("SetStreamServiceSettings", obs_ws.stream_service_payload("twitch", "k"))
    except obs_state.ObsError as exc:
        assert exc.code == obs_state.OUTPUT_RUNNING
    else:
        raise AssertionError("OBS keeps the service while streaming")
    scenes = st.request("GetSceneList")["scenes"]
    order = st.snapshot()["scene_order"]
    assert [s["sceneName"] for s in scenes] == order[::-1], "the list starts at the bottom scene"
    assert [s["sceneIndex"] for s in scenes] == list(range(len(order))), "indexes count from the bottom"


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


def _enable(st, scene, source, on=True):
    item = st.request("GetSceneItemId", {"sceneName": scene, "sourceName": source})["sceneItemId"]
    st.request("SetSceneItemEnabled", {"sceneName": scene, "sceneItemId": item, "sceneItemEnabled": on})


def _feeds(st, scene):
    return {f["input"]: (f["occluded"], f["occluded_by"]) for f in st.feeds(scene)}


def t_splitscreen_picture_places_feeds_by_their_transform():
    st = obs_state.ObsState(obs_state.DEFAULT_COLLECTION)
    rects = {f["input"]: f["rect"] for f in st.feeds("Splitscreen")}
    assert rects == {"Feed A": [0.0, 270.0, 960.0, 540.0], "Feed B": [960.0, 270.0, 960.0, 540.0]}, rects
    shot, _ = st.screenshot("Splitscreen", 320)
    assert _png_pixel(shot, 2, 100) == (192, 192, 192), "Feed A's bars start at the left edge"
    assert _png_pixel(shot, 2, 150) != (192, 192, 192), "below the halves is no feed"


def t_a_full_frame_graphic_above_a_feed_hides_it():
    st = obs_state.ObsState(obs_state.DEFAULT_COLLECTION)
    assert _feeds(st, "Stint") == {"Feed A": (False, None)}, "transparent overlays never hide a feed"
    _enable(st, "Stint", "Feed POV")
    _enable(st, "Stint", "Standby Cover")
    assert _feeds(st, "Stint") == {"Feed A": (True, "Standby Cover"), "Feed POV": (True, "Standby Cover")}
    shot, _ = st.screenshot("Stint", 320)
    assert _png_pixel(shot, 2, 100) != (192, 192, 192), "the cover paints over the feed"
    _enable(st, "Stint", "Standby Cover", False)
    _enable(st, "Stint", "Standings")
    assert _feeds(st, "Stint")["Feed A"] == (True, "Standings"), "a full-page graphic hides it too"
    _enable(st, "Stint", "Standings", False)
    _enable(st, "Stint", "Grid Row 1")
    assert _feeds(st, "Stint")["Feed A"] == (False, None), "a grid row paints only its own row"


def t_rect_honours_the_alignment():
    collection = {"name": "C", "sources": [{"id": "image_source", "name": "Img", "settings": {}},
                                           {"id": "scene", "name": "S", "settings": {"items": [
                                               {"name": "Img", "id": 1, "align": 0, "pos": {"x": 960.0, "y": 540.0},
                                                "bounds_type": 2, "bounds": {"x": 100.0, "y": 50.0}},
                                               {"name": "Img", "id": 2, "align": 10, "pos": {"x": 960.0, "y": 540.0},
                                                "bounds_type": 2, "bounds": {"x": 100.0, "y": 50.0}}]}}]}
    st = obs_state.ObsState(collection)
    assert [st.rect(i) for i in st.scenes["S"]] == [[910.0, 515.0, 100.0, 50.0], [860.0, 490.0, 100.0, 50.0]]


def t_only_an_opaque_item_above_and_around_a_feed_hides_it():
    def item(name, n, w):
        return {"name": name, "id": n, "bounds_type": 2, "bounds": {"x": w, "y": w * 9 / 16}}
    collection = {"name": "C", "sources": [
        {"id": "image_source", "name": "Back", "settings": {}},
        {"id": "image_source", "name": "Badge", "settings": {}},
        {"id": "ffmpeg_source", "name": "Cam", "settings": {"input": "http://127.0.0.1:53001"}},
        {"id": "scene", "name": "S", "settings": {"items": [
            item("Back", 1, 1920.0), item("Cam", 2, 1920.0), item("Badge", 3, 400.0)]}}]}
    st = obs_state.ObsState(collection, opaque=["Back", "Badge"])
    assert _feeds(st, "S") == {"Cam": (False, None)}, "neither a backdrop below nor a small badge above hides it"


def t_a_feed_cover_hides_only_its_own_half():
    sim = _sim()
    try:
        report, note = obs_ws.ensure_cover_items("Feed A", "Feed A Cover", "cover.png")
        assert report.get("Splitscreen") == "created", (report, note)
        obs_ws.set_scene_item_enabled("Splitscreen", "Feed A Cover", True)
        assert _feeds(sim.state, "Splitscreen") == {"Feed A": (True, "Feed A Cover"), "Feed B": (False, None)}
    finally:
        sim.stop()


STUB = """import sys, time
args = sys.argv[1:]
with open(args[0], "a") as fh:
    fh.write(args[args.index("-i") + 1] + "\\n")
mode = args[1]
if mode.startswith("sleep"):
    time.sleep(float(mode[5:] or 30))
    mode = "ok"
if mode not in ("ok", "short"):
    sys.exit(1)
w, h = (int(v) for v in args[args.index("-vf") + 1].split("=")[1].split(":"))
sys.stdout.buffer.write(bytes((10, 200, 30)) * (w * h - (mode == "short")))
"""
FEED_A, FEED_B = "http://127.0.0.1:53001", "http://127.0.0.1:53002"


class Skip(Exception):
    pass


def _stub(tmp, mode):
    path = os.path.join(tmp, "ffmpeg_stub.py")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(STUB)
    calls = os.path.join(tmp, "calls.txt")
    return [sys.executable, path, calls, mode], calls


def _calls(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return fh.read().split()


def _grabber(cmd, **kw):
    kw.setdefault("size", (4, 2))
    return obs_grab.FeedGrabber(cmd, **kw)


def t_grabber_reads_only_the_relay_feeds():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        cmd, calls = _stub(tmp, "ok")
        grab = _grabber(cmd, interval=60)
        refused = ["http://192.0.2.7:53001", "http://127.0.0.1:8088", "http://127.0.0.1:53001/timer/reset",
                   "http://127.0.0.1:53001/?x=1", "file://localhost/etc/passwd", "tcp://127.0.0.1:53001",
                   "http://user@127.0.0.1:53001"]
        got = grab.frames([FEED_A] + refused)
        assert got[FEED_A] == (bytes((10, 200, 30)) * 8, 4, 2), got[FEED_A]
        assert all(got[u] is None for u in refused), got
        assert _calls(calls) == [FEED_A], "only the feed itself is ever read"
        assert grab.frames([FEED_A])[FEED_A] is not None and grab.reads == 1, "one read per interval"
        argv = grab.argv(FEED_A)
        for flag, value in (("-protocol_whitelist", "http,tcp,udp"), ("-reconnect", "0"),
                            ("-rw_timeout", "4000000"), ("-f", "mpegts")):
            assert argv[argv.index(flag) + 1] == value, (flag, argv)
        assert argv.index("-f") < argv.index("-i"), "the demuxer is forced before the input"
    with tempfile.TemporaryDirectory() as tmp:
        cmd, _ = _stub(tmp, "ok")
        grab = _grabber(cmd, ports=[9000])
        assert grab.frames(["http://127.0.0.1:9000/"])["http://127.0.0.1:9000/"], "configured ports"
        assert grab.frames([FEED_A])[FEED_A] is None, "a configured set replaces the default"
        many = _grabber(cmd, ports=range(9000, 9040), wait=0)
        many.frames([f"http://127.0.0.1:{p}" for p in range(9000, 9040)])
        assert len(many.feeds) <= obs_grab.MAX_FEEDS, "the feed cache is bounded"
        many.close()
    for mode in ("fail", "short"):
        with tempfile.TemporaryDirectory() as tmp:
            cmd, _ = _stub(tmp, mode)
            assert _grabber(cmd).frames([FEED_A])[FEED_A] is None, mode
    missing = _grabber([os.path.join(ROOT, "no-such-ffmpeg")])
    assert missing.frames([FEED_A])[FEED_A] is None, "no ffmpeg"
    assert isinstance(obs_sim.ObsSim().state.grabber, obs_grab.FeedGrabber), "ffmpeg by default"


def t_grabs_run_in_parallel_in_the_background_and_die_with_the_sim():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        cmd, calls = _stub(tmp, "sleep1")
        grab = _grabber(cmd, wait=3, interval=0.2)
        started = time.monotonic()
        got = grab.frames([FEED_A, FEED_B])
        assert got[FEED_A] and got[FEED_B] and time.monotonic() - started < 1.8, "both feeds at once"
        time.sleep(0.3)
        started = time.monotonic()
        assert grab.frames([FEED_A])[FEED_A], "the last frame while a new read runs"
        assert time.monotonic() - started < 0.5, "a refresh never blocks the screenshot"
        assert _wait(lambda: len(_calls(calls)) == 3) and grab.frames([FEED_A])[FEED_A]
        _wait(lambda: not grab.procs)
    with tempfile.TemporaryDirectory() as tmp:
        cmd, _ = _stub(tmp, "sleep30")
        grab = _grabber(cmd, wait=0.2, timeout=60)
        assert grab.frames([FEED_A])[FEED_A] is None, "the first frame is waited for only briefly"
        assert _wait(lambda: len(grab.procs) == 1)
        proc = next(iter(grab.procs))
        grab.close()
        assert _wait(lambda: proc.poll() is not None), "close kills a running ffmpeg"
        assert grab.frames([FEED_B])[FEED_B] is None and grab.reads == 1, "no reads after close"
    with tempfile.TemporaryDirectory() as tmp:
        cmd, _ = _stub(tmp, "sleep30")
        grab = _grabber(cmd, wait=0.1, timeout=0.5)
        grab.frames([FEED_A])
        assert _wait(lambda: grab.feeds[FEED_A]["busy"] is None, 3), "a stalled feed is cut off"


def t_a_stale_frame_is_dropped():
    import tempfile
    clock = Clock()
    with tempfile.TemporaryDirectory() as tmp:
        cmd, _ = _stub(tmp, "ok")
        grab = _grabber(cmd, interval=1, max_age=10, clock=clock)
        assert grab.frames([FEED_A])[FEED_A], "the first frame"
        grab.cmd = [os.path.join(ROOT, "no-such-ffmpeg")]
        clock.now += 5
        assert grab.frames([FEED_A])[FEED_A], "a recent frame stands in while reads fail"
        clock.now += 6
        assert grab.frames([FEED_A])[FEED_A] is None, "a frame older than max_age is not shown"


def t_real_ffmpeg_grabs_a_frame_over_http():
    import http.server
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "red.ts")
        try:
            made = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                                   "color=c=red:s=64x36:d=1", "-c:v", "mpeg2video", "-f", "mpegts", clip],
                                  capture_output=True, timeout=60).returncode == 0
        except (OSError, subprocess.SubprocessError):
            made = False
        if not made:
            assert not os.environ.get("OBS_SIM_REQUIRE_FFMPEG"), "this CI job installs ffmpeg"
            raise Skip("no working ffmpeg on PATH")
        with open(clip, "rb") as fh:
            payload = fh.read()

        class Feed(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass  # quiet test output

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Feed)
        server.daemon_threads = False
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            port = server.server_address[1]
            grab = obs_grab.FeedGrabber(ports=[port], size=(8, 4), wait=10, timeout=10)
            frame = grab.frames([f"http://127.0.0.1:{port}/"])[f"http://127.0.0.1:{port}/"]
            grab.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        assert frame and len(frame[0]) == 8 * 4 * 3, "one rgb24 frame of the asked size"
        r, g, b = frame[0][0], frame[0][1], frame[0][2]
        assert r > 180 and g < 70 and b < 70, (r, g, b)


class FakeGrabber:
    def __init__(self, live, block=None):
        self.live, self.block, self.asked = live, block, []

    def frames(self, urls):
        self.asked.append(sorted(urls))
        if self.block is not None:
            self.block.wait(5)
        return {u: (bytes((10, 200, 30)) * 16, 4, 4) if u in self.live else None for u in urls}


def t_screenshot_composites_grabbed_frames_and_reports_the_source():
    grabber = FakeGrabber({FEED_A})
    st = obs_state.ObsState(obs_state.DEFAULT_COLLECTION, grabber=grabber)
    assert [f["source"] for f in st.snapshot()["program"]["feeds"]] == [None, None], "nothing rendered yet"
    shot, _ = st.screenshot("Splitscreen", 320)
    assert _png_pixel(shot, 2, 100) == (10, 200, 30), "Feed A shows its grabbed frame, scaled to its half"
    assert _png_pixel(shot, 158, 100) == (10, 200, 30), "the frame fills the whole half"
    assert _png_pixel(shot, 162, 100) == (192, 192, 192), "Feed B falls back to the pattern"
    sources = {f["input"]: f["source"] for f in st.snapshot()["program"]["feeds"]}
    assert sources == {"Feed A": "frame", "Feed B": "synthetic"}, sources
    _enable(st, "Stint", "Standby Cover")
    st.screenshot("Stint", 320)
    assert grabber.asked[-1] == [], "a hidden feed is not grabbed"
    assert {f["input"]: f["source"] for f in st.feeds("Stint")} == {"Feed A": None}


def t_a_slow_grab_never_blocks_obs():
    release = threading.Event()
    sim = _sim(grabber=FakeGrabber({FEED_A}, block=release))
    shooter = RawClient(sim.port)
    other = RawClient(sim.port)
    try:
        results = []
        worker = threading.Thread(target=lambda: results.append(
            shooter.request("GetSourceScreenshot", {"sourceName": "Splitscreen", "imageFormat": "png"})))
        worker.start()
        assert _wait(lambda: sim.state.grabber.asked), "the screenshot is grabbing"
        started = time.monotonic()
        resp, _ = other.request("SetCurrentProgramScene", {"sceneName": "Standby"})
        assert resp["requestStatus"]["result"] and obs_sim.fetch(sim.http_url)["scene"] == "Standby"
        assert time.monotonic() - started < 1.0, "requests and /state run while a grab waits"
        release.set()
        worker.join(5)
        assert results and results[0][0]["requestStatus"]["result"], results
    finally:
        release.set()
        shooter.close()
        other.close()
        sim.stop()



def _jpeg_size(raw):
    assert raw[:2] == b"\xff\xd8" and raw[-2:] == b"\xff\xd9", "a JPEG with SOI and EOI"
    pos = 2
    while pos < len(raw):
        marker, n = raw[pos + 1], struct.unpack(">H", raw[pos + 2:pos + 4])[0]
        if marker == 0xC0:
            h, w = struct.unpack(">HH", raw[pos + 5:pos + 9])
            return w, h
        pos += 2 + n
    raise AssertionError("no baseline SOF0")


def _shot(st, **data):
    data.setdefault("sourceName", "Splitscreen")
    return st.request("GetSourceScreenshot", data)["imageData"]


def t_screenshots_honour_format_size_and_quality():
    st = obs_state.ObsState(obs_state.DEFAULT_COLLECTION)
    uri = _shot(st, imageFormat="jpg", imageWidth=320)
    assert uri.startswith("data:image/jpg;base64,"), "OBS echoes the requested format"
    raw = obs_ws.parse_screenshot_data_uri(uri)
    assert _jpeg_size(raw) == (320, 180), "a real JPEG of the asked size"
    uri = _shot(st, imageFormat="jpeg", imageHeight=90)
    assert uri.startswith("data:image/jpeg;base64,") and _jpeg_size(obs_ws.parse_screenshot_data_uri(uri)) == (160, 90)
    low = obs_ws.parse_screenshot_data_uri(_shot(st, imageFormat="jpg", imageWidth=320, imageCompressionQuality=10))
    assert len(low) < len(raw), "a lower quality gives a smaller file"
    png = obs_ws.parse_screenshot_data_uri(_shot(st, imageFormat="png", imageWidth=100))
    assert _png_size(png) == (100, 56)
    for bad, code in (({"imageFormat": "webp"}, 400), ({"imageFormat": "png", "imageWidth": 4097}, 402),
                      ({"imageFormat": "png", "imageCompressionQuality": 101}, 402)):
        try:
            _shot(st, **bad)
        except obs_state.ObsError as exc:
            assert exc.code == code, (bad, exc.code)
        else:
            raise AssertionError(f"OBS refuses {bad}")


ZIGZAG = (0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5, 12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6,
          7, 14, 21, 28, 35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51, 58, 59, 52, 45, 38, 31,
          39, 46, 53, 60, 61, 54, 47, 55, 62, 63)


def _decode_fixed_jpeg(raw):
    """Decode obs_render's baseline 4:2:0 JPEG (fixed-length codes) to RGB pixels."""
    import math
    pos, qt = 2, {}
    while True:
        marker, n = raw[pos + 1], struct.unpack(">H", raw[pos + 2:pos + 4])[0]
        body = raw[pos + 4:pos + 2 + n]
        if marker == 0xDB:
            for t in range(len(body) // 65):
                qt[body[t * 65]] = body[t * 65 + 1:t * 65 + 65]
        elif marker == 0xC0:
            h, w = struct.unpack(">HH", body[1:5])
        elif marker == 0xDA:
            data = raw[pos + 2 + n:-2].replace(b"\xff\x00", b"\xff")
            break
        pos += 2 + n
    bits = "".join(f"{byte:08b}" for byte in data)
    cur = [0]

    def take(k):
        v = int(bits[cur[0]:cur[0] + k] or "0", 2)
        cur[0] += k
        return v

    def signed(v, size):
        return v if size == 0 or v >= 1 << (size - 1) else v - (1 << size) + 1

    cos = [[math.cos((2 * x + 1) * u * math.pi / 16) * (math.sqrt(0.5) if u == 0 else 1) for u in range(8)]
           for x in range(8)]

    def block(q, prev):
        coef = [0] * 64
        size = take(4)
        coef[0] = prev + signed(take(size), size)
        k = 1
        while k < 64:
            sym = take(8)
            symbols = [0x00, 0xF0] + [(r << 4) | s for r in range(16) for s in range(1, 11)]
            rs = symbols[sym]
            if rs == 0x00:
                break
            if rs == 0xF0:
                k += 16
                continue
            k += rs >> 4
            coef[k] = signed(take(rs & 15), rs & 15)
            k += 1
        nat = [0.0] * 64
        for i in range(64):
            nat[ZIGZAG[i]] = coef[i] * q[i]
        out = [sum(cos[x][u] * cos[y][v] * nat[v * 8 + u] for u in range(8) for v in range(8)) / 4 + 128
               for y in range(8) for x in range(8)]
        return out, coef[0]

    ys, cb, cr = [[0.0] * (w * h) for _ in range(3)]
    dc = [0, 0, 0]
    for my in range((h + 15) // 16):
        for mx in range((w + 15) // 16):
            for by, bx in ((0, 0), (0, 8), (8, 0), (8, 8)):
                pix, dc[0] = block(qt[0], dc[0])
                for i, v in enumerate(pix):
                    y, x = my * 16 + by + i // 8, mx * 16 + bx + i % 8
                    if y < h and x < w:
                        ys[y * w + x] = v
            for plane, n in ((cb, 1), (cr, 2)):
                pix, dc[n] = block(qt[1], dc[n])
                for i, v in enumerate(pix):
                    for dy in (0, 1):
                        for dx in (0, 1):
                            y, x = my * 16 + 2 * (i // 8) + dy, mx * 16 + 2 * (i % 8) + dx
                            if y < h and x < w:
                                plane[y * w + x] = v - 128
    return w, h, [(ys[i] + 1.402 * cr[i], ys[i] - 0.344136 * cb[i] - 0.714136 * cr[i], ys[i] + 1.772 * cb[i])
                  for i in range(w * h)]


def t_jpeg_encoder_round_trips():
    import obs_render
    w, h = 40, 24
    colours = [(200, 30, 30), (30, 200, 30), (30, 30, 200), (230, 230, 230)]
    px = bytes(c for y in range(h) for x in range(w) for c in colours[(y >= h // 2) * 2 + (x >= w // 2)])
    dw, dh, pixels = _decode_fixed_jpeg(obs_render.jpeg(w, h, px, 95))
    assert (dw, dh) == (w, h)
    worst = max(abs(pixels[y * w + x][c] - px[(y * w + x) * 3 + c])
                for y in range(h) for x in range(w) for c in range(3)
                if x % (w // 2) not in (0, w // 2 - 1) and y % (h // 2) not in (0, h // 2 - 1))
    assert worst < 30, f"decoded pixels match the source, worst channel error {worst}"


def t_jpeg_stuffs_ff_bytes_and_averages_chroma():
    import obs_render
    bits = obs_render._Bits()
    bits.put(0xFF, 8)
    assert bytes(bits.out) == b"\xff\x00", "a 0xFF byte in the scan is followed by 0x00"
    rows = [(200, 30, 30) if y % 2 == 0 else (30, 30, 200) for y in range(16)]
    px = bytes(c for y in range(16) for _ in range(16) for c in rows[y])
    _, _, pixels = _decode_fixed_jpeg(obs_render.jpeg(16, 16, px, 95))
    drift = sum(r - b for r, _, b in pixels) / len(pixels)
    assert abs(drift) < 20, f"chroma is the mean of each 2x2 cell, red minus blue drifts by {drift}"


def t_relay_program_monitor_gets_a_jpeg():
    sim = _sim()
    try:
        data, note = obs_ws.get_program_screenshot(width=640)
        assert _jpeg_size(data) == (640, 360), note
    finally:
        sim.stop()


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
        assert _jpeg_size(data) == (320, 180), note
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
        names = [loud.next()["d"]["eventType"] for _ in range(3)]
        assert names == ["SceneTransitionStarted", "SceneTransitionEnded", "CurrentProgramSceneChanged"], names
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
        assert _wait(lambda: "CurrentProgramSceneChanged" in [
            e["eventType"] for e in obs_sim.fetch(sim.http_url, "/events?since=0")["events"]]), "after the fade"
        events = obs_sim.fetch(sim.http_url, "/events?since=0")
        assert obs_sim.fetch(sim.http_url, f"/events?since={events['seq']}")["events"] == []
        assert obs_sim.fetch(sim.http_url, "/labels", {"Feed A": "Comms 1"})["labels"] == {"Feed A": "Comms 1"}
        assert obs_sim.fetch(sim.http_url, "/freeze", {"input": "Feed A"})["frozen"] == ["Feed A"]
        status, ctype, raw = sim.route("GET", "/program.png?width=200")
        assert status.startswith("200") and ctype == "image/png" and _png_size(raw)[0] == 200
        assert sim.route("GET", "/nope")[0].startswith("404")
        assert sim.env() == {"RACECAST_SIM_OBS_WS": sim.url}
    finally:
        sim.stop()


def _raw_http(port, request):
    with socket.create_connection(("127.0.0.1", port), timeout=3) as c:
        c.sendall(request.encode())
        data = b""
        while True:
            chunk = c.recv(65536)
            if not chunk:
                return data.decode("iso-8859-1")
            data += chunk


def t_a_browser_page_cannot_reach_the_endpoint():
    sim = _sim()
    try:
        port = sim.port
        body = json.dumps({"requestType": "SetCurrentProgramScene", "requestData": {"sceneName": "Standby"}})
        post = ("POST /request HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n{extra}"
                "Content-Length: {n}\r\n\r\n{body}")
        plain = post.format(port=port, extra="Content-Type: text/plain\r\n", n=len(body), body=body)
        assert _raw_http(port, plain).startswith("HTTP/1.1 415"), "a form post is refused"
        origin = post.format(port=port, n=len(body), body=body,
                             extra="Content-Type: application/json\r\nOrigin: http://evil.example\r\n")
        assert _raw_http(port, origin).startswith("HTTP/1.1 403"), "any Origin is refused"
        rebound = post.format(port=port, n=len(body), body=body,
                              extra="Content-Type: application/json\r\n").replace(
            f"Host: 127.0.0.1:{port}", f"Host: evil.example:{port}")
        assert _raw_http(port, rebound).startswith("HTTP/1.1 403"), "a rebound Host is refused"
        img = "GET /program.png HTTP/1.1\r\nHost: attacker.example\r\n\r\n"
        assert _raw_http(port, img).startswith("HTTP/1.1 403"), "a foreign Host never renders"
        assert sim.state.snapshot()["scene"] == "Splitscreen", "nothing reached the state"
        local = post.format(port=port, n=len(body), body=body,
                            extra="Content-Type: application/json; charset=utf-8\r\n").replace(
            f"Host: 127.0.0.1:{port}", f"Host: localhost:{port}")
        assert _raw_http(port, local).startswith("HTTP/1.1 200"), "a local tool still gets through"
        key = base64.b64encode(os.urandom(16)).decode()
        upgrade = obs_ws.handshake_request("127.0.0.1", port, key, headers={"Origin": "http://evil.example"})
        with socket.create_connection(("127.0.0.1", port), timeout=3) as c:
            c.sendall(upgrade)
            assert c.recv(4096).startswith(b"HTTP/1.1 403"), "a browser WebSocket is refused"
    finally:
        sim.stop()


def t_a_failing_handler_answers_400_and_keeps_the_session():
    sim = _sim()
    client = RawClient(sim.port)
    try:
        resp, _ = client.request("SetInputVolume", {"inputName": "Feed A", "inputVolumeDb": "loud"})
        assert resp["requestStatus"]["code"] == 400 and not resp["requestStatus"]["result"], resp
        resp, _ = client.request("GetCurrentProgramScene")
        assert resp["requestStatus"]["result"], "the session survives"
        status, _, payload = sim.route("POST", "/freeze", b'{"input": "Feed A", "frozen": {"a": 1}}')
        assert status.startswith("200") or status.startswith("400"), status
        status, _, payload = sim.route("POST", "/labels", b'{"Feed A": {"x": [1]}}')
        assert status.startswith("200"), (status, payload)
        status, _, payload = sim.route("GET", "/events?since=x")
        assert status.startswith("200"), status
        status, ctype, payload = sim.route("POST", "/request", b'{"requestType": 5, "requestData": []}')
        assert status.startswith("400") and json.loads(payload)["code"] == 400, (status, payload)
    finally:
        client.close()
        sim.stop()


def t_a_client_that_stops_reading_is_dropped():
    old = obs_sim._Client.QUEUE_MAX
    obs_sim._Client.QUEUE_MAX = 4
    sim = _sim()
    try:
        slow = RawClient(sim.port)
        slow.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        big = {"blob": "x" * 65536}
        for _ in range(400):
            sim.state.request("SetInputSettings", {"inputName": "Feed A", "inputSettings": big})
            if len(sim.conns) == 0:
                break
        assert _wait(lambda: len(sim.conns) == 0), "a client that never reads is closed"
        slow.close()
    finally:
        obs_sim._Client.QUEUE_MAX = old
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
            try:
                fn()
            except Skip as why:
                print("SKIP", name, "-", why)
                continue
            print("ok", name)
    print("ALL PASS")
