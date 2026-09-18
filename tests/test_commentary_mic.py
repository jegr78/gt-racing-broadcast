#!/usr/bin/env python3
"""Stdlib checks for the endurance commentary mic (#593): tools/add_commentary_mic.py,
the committed collection, its localization and the tokenize-obs round trip.
Run: python3 tests/test_commentary_mic.py"""
import copy, importlib.util, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tool = _load("add_commentary_mic", os.path.join("tools", "add_commentary_mic.py"))
tk = _load("tokenize_obs", os.path.join("tools", "tokenize-obs.py"))
sa = _load("setup_assets", os.path.join("src", "setup-assets.py"))
obs_ws = _load("obs_ws", os.path.join("src", "scripts", "obs_ws.py"))

MIC = "Commentary Mic Device"


def _collection():
    with open(os.path.join(ROOT, "src", "obs", "GT_Racing_Endurance.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _without_mic(d):
    d = copy.deepcopy(d)
    d["sources"] = [s for s in d["sources"] if s.get("name") != MIC]
    for s in d["sources"]:
        if s.get("id") == "scene":
            s["settings"]["items"] = [i for i in s["settings"]["items"] if i.get("name") != MIC]
    return d


def _scene(d, name):
    return next(s for s in d["sources"] if s.get("name") == name and s.get("id") == "scene")


def _items_named(d, name):
    return [(s["name"], i) for s in d["sources"] if s.get("id") == "scene"
            for i in s["settings"]["items"] if i.get("name") == name]


def t_the_relay_and_the_collection_name_the_same_input():
    assert obs_ws.COMMENTARY_MIC_INPUT == tool.MIC_SOURCE == MIC
    assert any(e["name"] == MIC for e in sa.DEVICE_SOURCES)


def t_adds_a_muted_tokenized_mic_to_stint_and_splitscreen():
    d = _without_mic(_collection())
    assert tool.add_commentary_mic(d) is True
    leaf = [s for s in d["sources"] if s.get("name") == MIC]
    assert len(leaf) == 1, leaf
    leaf = leaf[0]
    assert leaf["id"] == leaf["versioned_id"] == "coreaudio_input_capture"
    assert leaf["settings"] == {"device_id": "__RACECAST_MIC__"}
    # Shipped closed: only the relay opens it, and only for a local stint on air.
    assert leaf["muted"] is True
    assert leaf["monitoring_type"] == 0            # no self-monitor, no echo
    where = sorted(scene for scene, _ in _items_named(d, MIC))
    assert where == ["Splitscreen", "Stint"], where


def t_the_mic_is_a_direct_audio_item_not_a_nested_scene():
    # A visible nested scene is one more render layer in Stint, which tips a weak
    # host over the render cliff (PR #559). The leaf input has no video to render.
    d = _without_mic(_collection())
    tool.add_commentary_mic(d)
    assert not any(s.get("name") == "Commentary Mic" for s in d["sources"])
    for scene, item in _items_named(d, MIC):
        assert item["visible"] is True, scene
        assert item["source_uuid"] == tool.MIC_UUID, scene


def t_new_item_ids_are_unique_and_counted():
    d = _without_mic(_collection())
    tool.add_commentary_mic(d)
    for name in ("Stint", "Splitscreen"):
        s = _scene(d, name)
        ids = [i["id"] for i in s["settings"]["items"]]
        assert len(ids) == len(set(ids)), (name, ids)
        mic_id = next(i["id"] for i in s["settings"]["items"] if i["name"] == MIC)
        assert s["settings"]["id_counter"] >= mic_id, name
        assert f"libobs.show_scene_item.{mic_id}" in s["hotkeys"], name


def t_is_idempotent():
    d = _without_mic(_collection())
    tool.add_commentary_mic(d)
    once = copy.deepcopy(d)
    assert tool.add_commentary_mic(d) is False
    assert d == once


def t_committed_collection_carries_the_mic():
    d = _collection()
    assert tool.add_commentary_mic(copy.deepcopy(d)) is False   # already applied
    leaf = next(s for s in d["sources"] if s.get("name") == MIC)
    assert leaf["uuid"] == tool.MIC_UUID and leaf["muted"] is True
    assert leaf["settings"] == {"device_id": "__RACECAST_MIC__"}
    assert sorted(scene for scene, _ in _items_named(d, MIC)) == ["Splitscreen", "Stint"]


def t_windows_localization_targets_wasapi():
    d = _collection()
    unset = sa.localize_device_sources(d, "win32", {"RACECAST_MIC": "{0.0.1.00000000}.{abc}"})
    leaf = next(s for s in d["sources"] if s.get("name") == MIC)
    assert leaf["id"] == "wasapi_input_capture"
    assert leaf["settings"] == {"device_id": "{0.0.1.00000000}.{abc}"}
    assert leaf["muted"] is True                    # localization never opens the mic
    assert MIC not in unset


def t_tokenize_folds_a_localized_mic_back():
    d = _collection()
    sa.localize_device_sources(d, "win32", {"RACECAST_MIC": "{0.0.1.00000000}.{abc}"})
    leaf = next(s for s in d["sources"] if s.get("name") == MIC)
    leaf["muted"] = False                           # exported mid-stint, mic open
    assert tk.canonicalize_commentary_mic(d) is True
    assert leaf["id"] == leaf["versioned_id"] == "coreaudio_input_capture"
    assert leaf["settings"] == {"device_id": "__RACECAST_MIC__"}
    assert leaf["muted"] is True
    assert tk.canonicalize_commentary_mic(d) is False


def t_tokenize_leaves_a_solo_collection_alone():
    with open(os.path.join(ROOT, "src", "obs", "GT_Racing_Solo_Commentary.json"), encoding="utf-8") as fh:
        d = json.load(fh)
    before = copy.deepcopy(d)
    assert tk.canonicalize_commentary_mic(d) is False
    assert d == before                               # the solo mic stays hot


def t_unset_warning_names_the_env_var_of_each_device():
    line = sa.device_unset_warning([MIC, "Solo Webcam Device"], "solo", {})
    assert "RACECAST_MIC" in line and "RACECAST_WEBCAM" in line, line
    assert "RACECAST_CAPTURE" not in line, line


def t_endurance_without_a_capture_card_does_not_warn_about_the_mic():
    # No RACECAST_CAPTURE -> no local stint on this machine -> the mic is never opened.
    assert sa.device_unset_warning([MIC], "endurance", {}) is None
    line = sa.device_unset_warning([MIC], "endurance", {"RACECAST_CAPTURE": "Game Capture HD60 X"})
    assert line and "RACECAST_MIC" in line, line


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
