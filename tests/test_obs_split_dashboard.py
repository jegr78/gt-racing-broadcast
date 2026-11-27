#!/usr/bin/env python3
"""The splitscreen keeps the driver dashboard (#1365). Run: python3 tests/test_obs_split_dashboard.py"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _scene(collection, name):
    return next(s for s in collection["sources"] if s.get("id") == "scene" and s["name"] == name)


def t_the_splitscreen_shows_the_driver_dashboard_as_the_stint_scene_does():
    with open(os.path.join(ROOT, "src", "obs", "GT_Racing_Endurance.json"), encoding="utf-8") as fh:
        c = json.load(fh)
    stint = _scene(c, "Stint")["settings"]["items"]
    split = _scene(c, "Splitscreen")["settings"]["items"]
    names = [i["name"] for i in split]
    assert "Driver Dashboard" in names, f"the tower and the track map stay on air during a swap: {names}"
    item = split[names.index("Driver Dashboard")]
    ref = next(i for i in stint if i["name"] == "Driver Dashboard")
    for key in ("pos", "bounds", "bounds_type", "scale", "visible"):
        assert item.get(key) == ref.get(key), f"the same full frame as in the Stint scene: {key} {item.get(key)}"
    def layers(items):          # OBS draws a group as one layer and skips its backup copies
        return [i["name"] for i in items if not i.get("group_item_backup")]
    split_layers, stint_layers = layers(split), layers(stint)
    assert split_layers.index("Driver Dashboard") + 1 == split_layers.index("Split HUD"), \
        f"right under the splitscreen HUD group: {split_layers}"
    assert stint_layers.index("Driver Dashboard") + 1 == stint_layers.index("Stint HUD"), \
        f"as in the Stint scene, under its HUD group: {stint_layers}"
    assert len({i["id"] for i in split}) == len(split), "scene item ids stay unique"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
