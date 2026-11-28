#!/usr/bin/env python3
"""The splitscreen keeps the driver dashboard (#1365). Run: python3 tests/test_obs_split_dashboard.py"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _scene(collection, name):
    return next(s for s in collection["sources"] if s.get("id") == "scene" and s["name"] == name)


SPLIT = "Driver Dashboard Splitscreen"


def t_the_splitscreen_dashboard_source_hides_the_track_map():
    with open(os.path.join(ROOT, "src", "obs", "GT_Racing_Endurance.json"), encoding="utf-8") as fh:
        c = json.load(fh)
    by_name = {s["name"]: s for s in c["sources"]}
    src, ref = by_name[SPLIT], by_name["Driver Dashboard"]
    assert src["settings"]["url"] == ref["settings"]["url"] + "?map=0", src["settings"]
    assert {k: v for k, v in src["settings"].items() if k != "url"} == \
        {k: v for k, v in ref["settings"].items() if k != "url"}, "the same page size as the Stint scene's"
    assert len({s["uuid"] for s in c["sources"]}) == len(c["sources"]), "source uuids stay unique"
    item = next(i for i in next(s for s in c["sources"] if s.get("id") == "scene" and s["name"] == "Splitscreen")
                ["settings"]["items"] if i["name"] == SPLIT)
    assert item["source_uuid"] == src["uuid"], item
    with open(os.path.join(ROOT, "src", "obs", "drivers.html"), encoding="utf-8") as fh:
        page = fh.read()
    assert 'body[data-no-map] #drivers-map { display: none !important; }' in page, "map=0 hides the map"
    assert 'get("map") === "0"' in page, "the page reads map=0 from its URL"
    assert 'noMap ? {draw() {}, async sync() {}} : TrackMap.create(' in page, \
        "a hidden map fetches no line and draws nothing"


def t_the_splitscreen_shows_the_driver_dashboard_as_the_stint_scene_does():
    with open(os.path.join(ROOT, "src", "obs", "GT_Racing_Endurance.json"), encoding="utf-8") as fh:
        c = json.load(fh)
    stint = _scene(c, "Stint")["settings"]["items"]
    split = _scene(c, "Splitscreen")["settings"]["items"]
    names = [i["name"] for i in split]
    assert SPLIT in names and "Driver Dashboard" not in names, \
        f"the tower stays on air during a swap, from its own source: {names}"
    item = split[names.index(SPLIT)]
    ref = next(i for i in stint if i["name"] == "Driver Dashboard")
    for key in ("pos", "bounds", "bounds_type", "scale", "visible"):
        assert item.get(key) == ref.get(key), f"the same full frame as in the Stint scene: {key} {item.get(key)}"
    def layers(items):          # OBS draws a group as one layer and skips its backup copies
        return [i["name"] for i in items if not i.get("group_item_backup")]
    split_layers, stint_layers = layers(split), layers(stint)
    assert split_layers.index(SPLIT) + 1 == split_layers.index("Split HUD"), \
        f"right under the splitscreen HUD group: {split_layers}"
    assert stint_layers.index("Driver Dashboard") + 1 == stint_layers.index("Stint HUD"), \
        f"as in the Stint scene, under its HUD group: {stint_layers}"
    assert len({i["id"] for i in split}) == len(split), "scene item ids stay unique"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
