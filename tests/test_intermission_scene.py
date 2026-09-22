#!/usr/bin/env python3
"""Stdlib checks for tools/add_intermission_scene.py. Run: python3 tests/test_intermission_scene.py"""
import copy, importlib.util, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tool = _load("add_intermission_scene", os.path.join("tools", "add_intermission_scene.py"))


def _collection():
    with open(os.path.join(ROOT, "src", "obs", "GT_Racing_Endurance.json"), encoding="utf-8") as fh:
        return json.load(fh)


# Sources the tool creates. The background image source is "Intermission
# Background", NOT "Intermission", because OBS source and scene names share one
# global namespace: naming the image "Intermission" collides with the scene and
# makes SetCurrentProgramScene("Intermission") resolve to the image (error 602).
_INTERMISSION_SOURCES = ("Intermission Background", "Intermission Chat", "Intermission Music")


def t_adds_scene_and_three_sources():
    d = copy.deepcopy(_collection())
    # start from a collection WITHOUT the scene to prove the tool creates it
    d["sources"] = [s for s in d["sources"]
                    if s.get("name") not in ("Intermission",) + _INTERMISSION_SOURCES]
    changed = tool.add_intermission_scene(d)
    assert changed is True
    names = {s.get("name") for s in d["sources"]}
    assert set(_INTERMISSION_SOURCES) <= names
    scene = next(s for s in d["sources"] if s.get("name") == "Intermission" and s.get("id") == "scene")
    item_names = {it.get("name") for it in scene["settings"]["items"]}
    assert set(_INTERMISSION_SOURCES) <= item_names


def t_no_duplicate_source_names():
    # OBS requires globally-unique source/scene names; a collision breaks the
    # name->object lookup (SetCurrentProgramScene 602). Guard the whole template.
    names = [s.get("name") for s in _collection()["sources"]]
    dups = sorted({n for n in names if names.count(n) > 1})
    assert not dups, f"duplicate source names in GT_Racing_Endurance.json: {dups}"


def t_no_duplicate_scene_item_ids():
    # OBS requires per-scene-unique scene-item ids (the item `id` field == the
    # runtime sceneItemId). A collision makes SetSceneItemEnabled(id) toggle
    # whichever item OBS resolves, so two GFX/visibility toggles cross-fire.
    # (#478)
    offenders = {}
    for src in _collection()["sources"]:
        if src.get("id") != "scene":
            continue
        by_id = {}
        for it in src.get("settings", {}).get("items", []):
            by_id.setdefault(it.get("id"), []).append(it.get("name"))
        dups = {i: names for i, names in by_id.items() if len(names) > 1}
        if dups:
            offenders[src.get("name")] = dups
    assert not offenders, f"duplicate sceneItemIds per scene in GT_Racing_Endurance.json: {offenders}"


def t_tokens_and_url_are_correct():
    d = copy.deepcopy(_collection())
    d["sources"] = [s for s in d["sources"]
                    if s.get("name") not in ("Intermission",) + _INTERMISSION_SOURCES]
    tool.add_intermission_scene(d)
    img = next(s for s in d["sources"]
               if s.get("name") == "Intermission Background" and s.get("id") != "scene")
    assert img["settings"]["file"] == "__RACECAST_GRAPHICS__/Intermission.png"
    music = next(s for s in d["sources"] if s.get("name") == "Intermission Music")
    assert music["settings"]["local_file"] == "__RACECAST_MEDIA__/intermission.mp3"
    assert music["settings"].get("is_local_file") is True
    assert music["settings"].get("looping") is True
    chat = next(s for s in d["sources"] if s.get("name") == "Intermission Chat")
    assert chat["settings"]["url"] == "http://127.0.0.1:8088/intermission"


def t_idempotent():
    d = copy.deepcopy(_collection())
    tool.add_intermission_scene(d)
    assert tool.add_intermission_scene(d) is False


def t_committed_collection_already_has_scene():
    # The shipped template must carry the scene, not just the tool's output.
    names = {s.get("name") for s in _collection()["sources"]}
    assert ({"Intermission"} | set(_INTERMISSION_SOURCES)) <= names


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
