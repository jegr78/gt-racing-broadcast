#!/usr/bin/env python3
"""Stdlib checks for the platform-dependent Discord audio source transforms.
Run: python3 tests/test_discord_audio.py"""
import copy, importlib.util, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, *rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sa = _load("setup_assets", "src", "setup-assets.py")
tk = _load("tokenize_obs", "tools", "tokenize-obs.py")

CANONICAL_SETTINGS = {"type": 1, "application": "com.hnc.Discord"}


def coll(src_id="sck_audio_capture", settings=None):
    return {"sources": [
        {"name": "Discord Audio Capture", "uuid": sa.DISCORD_AUDIO_UUID,
         "id": src_id, "versioned_id": src_id,
         "settings": dict(CANONICAL_SETTINGS if settings is None else settings)},
        {"name": "Feed A", "uuid": "feed-a", "id": "ffmpeg_source",
         "settings": {"input": "http://127.0.0.1:53001"}},
    ]}


def t_variant_per_platform():
    assert sa.discord_variant("darwin")[0] == "sck_audio_capture"
    assert sa.discord_variant("win32")[0] == "wasapi_process_output_capture"
    assert sa.discord_variant("linux")[0] == "pipewire_audio_application_capture"
    assert sa.discord_variant("sunos5") is None


def t_localize_windows_swaps_id_and_settings():
    c = coll()
    assert sa.localize_discord_audio(c, "win32") == "wasapi_process_output_capture"
    s = c["sources"][0]
    assert s["id"] == s["versioned_id"] == "wasapi_process_output_capture"
    assert s["settings"] == {"window": "Discord:Chrome_WidgetWin_1:Discord.exe",
                             "priority": 2}   # 2 = WINDOW_PRIORITY_EXE
    assert c["sources"][1]["settings"]["input"] == "http://127.0.0.1:53001"


def t_localize_linux_uses_pipewire_plugin():
    c = coll()
    assert sa.localize_discord_audio(c, "linux") == "pipewire_audio_application_capture"
    # "MatchPriorty" (sic) is the plugin's actual settings key; 0 = binary name.
    assert c["sources"][0]["settings"] == {"TargetName": "Discord", "MatchPriorty": 0}


def t_variant_linux_web_targets_browser():
    src_id, settings = sa.discord_variant("linux", web=True, browser="Firefox")
    assert src_id == "pipewire_audio_application_capture"
    assert settings == {"TargetName": "Firefox", "MatchPriorty": 0}
    # web flag only affects Linux; macOS/Windows ignore it.
    assert sa.discord_variant("darwin", web=True)[0] == "sck_audio_capture"
    assert sa.discord_variant("win32", web=True)[0] == "wasapi_process_output_capture"


def t_localize_linux_web_swaps_targetname():
    c = coll()
    assert sa.localize_discord_audio(c, "linux", web=True, browser="Chromium") \
        == "pipewire_audio_application_capture"
    s = c["sources"][0]
    assert s["id"] == s["versioned_id"] == "pipewire_audio_application_capture"
    assert s["settings"] == {"TargetName": "Chromium", "MatchPriorty": 0}


def t_localize_linux_web_default_off_is_native():
    # web defaults False -> the existing native behaviour is unchanged.
    c = coll()
    sa.localize_discord_audio(c, "linux")
    assert c["sources"][0]["settings"] == {"TargetName": "Discord", "MatchPriorty": 0}


def t_localize_idempotent_and_darwin_noop():
    c = coll()
    sa.localize_discord_audio(c, "win32")
    once = copy.deepcopy(c)
    sa.localize_discord_audio(c, "win32")
    assert c == once
    d = coll()
    sa.localize_discord_audio(d, "darwin")
    assert d["sources"][0]["id"] == "sck_audio_capture"
    assert d["sources"][0]["settings"] == CANONICAL_SETTINGS


def t_localize_missing_source_or_unknown_platform():
    c = {"sources": [{"name": "x", "uuid": "other", "id": "scene", "settings": {}}]}
    before = copy.deepcopy(c)
    assert sa.localize_discord_audio(c, "win32") is None
    assert c == before
    d = coll()
    before = copy.deepcopy(d)
    assert sa.localize_discord_audio(d, "sunos5") is None
    assert d == before


def t_tokenize_folds_any_variant_back():
    c = coll()
    sa.localize_discord_audio(c, "win32")
    assert tk.canonicalize_discord_audio(c) is True
    s = c["sources"][0]
    assert s["id"] == s["versioned_id"] == "sck_audio_capture"
    assert s["settings"] == CANONICAL_SETTINGS
    assert tk.canonicalize_discord_audio(c) is False   # canonical input: no-op


def t_committed_template_carries_the_source():
    # Guards against the uuid drifting when scenes are re-exported.
    path = os.path.join(ROOT, "src", "obs", "GT_Racing_Endurance.json")
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    hits = [s for s in d.get("sources", []) if s.get("uuid") == sa.DISCORD_AUDIO_UUID]
    assert len(hits) == 1 and hits[0]["id"] == "sck_audio_capture"


def t_apply_collection_name_sets_top_level_name():
    c = {"name": "GT Racing Endurance", "sources": []}
    out = sa.apply_collection_name(c, "ERF Endurance")
    assert out["name"] == "ERF Endurance"


def t_apply_collection_name_noop_on_blank():
    c = {"name": "GT Racing Endurance", "sources": []}
    out = sa.apply_collection_name(c, "")
    assert out["name"] == "GT Racing Endurance"
    out2 = sa.apply_collection_name(c, None)
    assert out2["name"] == "GT Racing Endurance"


def t_canonicalize_name_resets_to_constant():
    d = {"name": "ERF Endurance", "sources": []}
    out = tk.canonicalize_name(d)
    assert out["name"] == tk.CANONICAL_COLLECTION_NAME
    assert tk.CANONICAL_COLLECTION_NAME == "GT Racing Endurance"


def _coll_with_pov(pos=(1496.0, 644.0), bounds=(384.0, 216.0)):
    # Scenes are stored as `sources` entries with id "scene"; their items
    # (carrying pos/bounds) live in settings.items, mirroring GT_Racing_Endurance.json.
    return {"sources": [
        {"name": "Stint", "id": "scene", "settings": {"items": [
            {"name": "Feed POV",
             "pos": {"x": pos[0], "y": pos[1]},
             "bounds": {"x": bounds[0], "y": bounds[1]}},
        ]}},
    ]}


def _pov_item(coll):
    return coll["sources"][0]["settings"]["items"][0]


def t_pov_source_name_matches_overlay_build():
    assert sa.POV_SOURCE_NAME == "Feed POV"


def t_apply_pov_transform_full():
    coll = _coll_with_pov()
    sa.apply_pov_transform(coll, {"left": 1516, "top": 600,
                                  "width": 384, "height": 216})
    it = _pov_item(coll)
    assert it["pos"] == {"x": 1516, "y": 600}
    assert it["bounds"] == {"x": 384, "y": 216}


def t_apply_pov_transform_partial_keeps_existing():
    coll = _coll_with_pov()
    sa.apply_pov_transform(coll, {"left": 1516, "top": 600})   # no width/height
    it = _pov_item(coll)
    assert it["pos"] == {"x": 1516, "y": 600}
    assert it["bounds"] == {"x": 384.0, "y": 216.0}            # untouched base


def t_apply_pov_transform_empty_is_noop():
    coll = _coll_with_pov()
    sa.apply_pov_transform(coll, {})
    assert _pov_item(coll)["pos"] == {"x": 1496.0, "y": 644.0}


def t_apply_pov_transform_no_pov_item_is_noop():
    coll = {"sources": [{"name": "Feed A", "id": "ffmpeg_source",
                         "settings": {"input": "http://127.0.0.1:53001"}}]}
    sa.apply_pov_transform(coll, {"left": 1516})              # must not raise
    assert coll["sources"][0]["name"] == "Feed A"


def _coll_with_webcam(pos=(14.0, 695.0), bounds=(336.0, 189.0)):
    # Mirrors _coll_with_pov, but for the solo-mode "Solo Webcam" device item
    # in scene "Program". See GT_Racing_Solo_POV.json and GT_Racing_Solo_Commentary.json.
    return {"sources": [
        {"name": "Program", "id": "scene", "settings": {"items": [
            {"name": "Solo Webcam",
             "pos": {"x": pos[0], "y": pos[1]},
             "bounds": {"x": bounds[0], "y": bounds[1]}},
        ]}},
    ]}


def _webcam_item(coll):
    return coll["sources"][0]["settings"]["items"][0]


def t_apply_box_transform_webcam_full():
    coll = _coll_with_webcam()
    sa.apply_box_transform(coll, "Solo Webcam",
                            {"left": 20, "top": 700, "width": 400, "height": 225})
    it = _webcam_item(coll)
    assert it["pos"] == {"x": 20, "y": 700}
    assert it["bounds"] == {"x": 400, "y": 225}


def t_apply_box_transform_webcam_empty_is_noop():
    coll = _coll_with_webcam()
    sa.apply_box_transform(coll, "Solo Webcam", {})
    assert _webcam_item(coll)["pos"] == {"x": 14.0, "y": 695.0}


def t_apply_box_transform_webcam_scene_scoped_program_only():
    # The webcam bake must reposition the 'Solo Webcam' item ONLY where it is
    # embedded in 'Program', never a same-named item in the standalone fullscreen
    # 'Solo Webcam' scene. A decoy there must stay untouched.
    coll = {"sources": [
        {"name": "Solo Webcam", "id": "scene", "settings": {"items": [
            {"name": "Solo Webcam",                       # decoy in its own scene
             "pos": {"x": 0.0, "y": 0.0},
             "bounds": {"x": 1920.0, "y": 1080.0}},
        ]}},
        {"name": "Program", "id": "scene", "settings": {"items": [
            {"name": "Solo Webcam",                       # the embedded instance
             "pos": {"x": 24.0, "y": 776.0},
             "bounds": {"x": 384.0, "y": 280.0}},
        ]}},
    ]}
    sa.apply_box_transform(coll, "Solo Webcam",
                            {"left": 20, "top": 700, "width": 400, "height": 225},
                            scene="Program")
    own = coll["sources"][0]["settings"]["items"][0]
    prog = coll["sources"][1]["settings"]["items"][0]
    assert prog["pos"] == {"x": 20, "y": 700}             # Program: repositioned
    assert prog["bounds"] == {"x": 400, "y": 225}
    assert own["pos"] == {"x": 0.0, "y": 0.0}             # own scene: untouched
    assert own["bounds"] == {"x": 1920.0, "y": 1080.0}


def t_apply_box_transform_tyres_capture_scene_scoped_program_only():
    # Mirrors t_apply_box_transform_webcam_scene_scoped_program_only: the
    # tyres/fuel capture bake must reposition the 'Solo Tyres/Fuel Capture' item
    # ONLY where it is embedded in 'Program'. A same-named decoy in another scene
    # must stay untouched.
    coll = {"sources": [
        {"name": "Other Scene", "id": "scene", "settings": {"items": [
            {"name": "Solo Tyres/Fuel Capture",           # decoy in another scene
             "pos": {"x": 0.0, "y": 0.0},
             "bounds": {"x": 1920.0, "y": 1080.0}},
        ]}},
        {"name": "Program", "id": "scene", "settings": {"items": [
            {"name": "Solo Tyres/Fuel Capture",           # the embedded instance
             "pos": {"x": 7.0, "y": 926.0},
             "bounds": {"x": 245.0, "y": 84.0}},
        ]}},
    ]}
    sa.apply_box_transform(coll, "Solo Tyres/Fuel Capture",
                            {"left": 20, "top": 900, "width": 260, "height": 90},
                            scene="Program")
    decoy = coll["sources"][0]["settings"]["items"][0]
    prog = coll["sources"][1]["settings"]["items"][0]
    assert prog["pos"] == {"x": 20, "y": 900}              # Program: repositioned
    assert prog["bounds"] == {"x": 260, "y": 90}
    assert decoy["pos"] == {"x": 0.0, "y": 0.0}            # other scene: untouched
    assert decoy["bounds"] == {"x": 1920.0, "y": 1080.0}


def t_localize_tyres_capture_windows():
    coll = {"sources": [
        {"name": "Solo Tyres Capture Device", "id": "av_capture_input",
         "settings": {"device": "__RACECAST_TYRES_CAPTURE__"}},
    ]}
    unset = sa.localize_device_sources(coll, "win32", {"RACECAST_TYRES_CAPTURE": "Elgato HD60 X:\\\\?\\usb#x"})
    src = coll["sources"][0]
    assert src["id"] == "dshow_input"
    assert src["settings"] == {"video_device_id": "Elgato HD60 X:\\\\?\\usb#x"}
    assert "Solo Tyres Capture Device" not in unset


def t_apply_box_transform_pov_still_works_via_generic():
    # #pov -> "Feed POV" must keep working identically through the generalized
    # apply_box_transform (apply_pov_transform is now a thin wrapper over it).
    coll = _coll_with_pov()
    sa.apply_box_transform(coll, sa.POV_SOURCE_NAME,
                            {"left": 1516, "top": 600, "width": 384, "height": 216})
    it = _pov_item(coll)
    assert it["pos"] == {"x": 1516, "y": 600}
    assert it["bounds"] == {"x": 384, "y": 216}


def t_commentary_has_tyres_capture_structure():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "src/obs/GT_Racing_Solo_Commentary.json"), encoding="utf-8") as fh:
        d = json.load(fh)
    by_name = {s.get("name"): s for s in d["sources"]}
    assert by_name["Solo Tyres Capture Device"]["settings"]["device"] == "__RACECAST_TYRES_CAPTURE__"
    assert by_name["Solo Tyres Capture Device"].get("muted") is True
    prog = by_name["Program"]["settings"]["items"]
    tyres = [it for it in prog if it.get("name") == "Solo Tyres/Fuel Capture"]
    assert len(tyres) == 1
    it = tyres[0]
    assert (it["crop_left"], it["crop_top"], it["crop_right"], it["crop_bottom"]) == (258, 950, 1336, 18)
    assert it["bounds_type"] == 2
    # The tyres item takes a genuinely-free scene-item id (not a colliding one).
    assert [i.get("id") for i in prog].count(it["id"]) == 1, "tyres item id collides"
    # POV collection must NOT gain the tyres source (Commentary-only)
    with open(os.path.join(root, "src/obs/GT_Racing_Solo_POV.json"), encoding="utf-8") as fh:
        pov = json.load(fh)
    assert "Solo Tyres Capture Device" not in {s.get("name") for s in pov["sources"]}


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
