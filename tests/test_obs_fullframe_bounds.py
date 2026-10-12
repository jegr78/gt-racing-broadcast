#!/usr/bin/env python3
"""Every full-frame league asset scales into the canvas (#871).

Graphics and the Intro/Outro/Trailer clips come from the league's Sheet, so their
resolution is not ours to choose. A scene item without a bounding box draws such an
asset at native size: a 1280x720 Standby.png covered only the top-left two thirds of
the 1920x1080 canvas. Run: python3 tests/test_obs_fullframe_bounds.py"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
COLLECTIONS = ("GT_Racing_Endurance.json", "GT_Racing_Solo_Commentary.json",
               "GT_Racing_Solo_POV.json")
OBS_BOUNDS_SCALE_INNER = 2
CANVAS = {"x": 1920.0, "y": 1080.0}


def _load(name):
    with open(os.path.join(ROOT, "src", "obs", name), encoding="utf-8") as fh:
        return json.load(fh)


def _is_fullframe_asset(source):
    """A still graphic or a video clip downloaded into the profile runtime."""
    settings = source.get("settings") or {}
    if source.get("id") == "image_source":
        return settings.get("file", "").startswith("__RACECAST_GRAPHICS__/")
    if source.get("id") == "ffmpeg_source":
        return settings.get("local_file", "").startswith("__RACECAST_MEDIA__/") \
            and settings["local_file"].endswith(".mp4")
    return False


def _fullframe_items(collection):
    by_name = {s.get("name"): s for s in collection["sources"]}
    for scene in collection["sources"]:
        for item in (scene.get("settings") or {}).get("items") or []:
            source = by_name.get(item.get("name"))
            if source and _is_fullframe_asset(source):
                yield scene["name"], item


def t_every_fullframe_asset_scales_into_the_canvas():
    offenders = []
    for name in COLLECTIONS:
        for scene, item in _fullframe_items(_load(name)):
            if (item.get("bounds_type") != OBS_BOUNDS_SCALE_INNER
                    or item.get("bounds") != CANVAS
                    or item.get("pos") != {"x": 0.0, "y": 0.0}):
                offenders.append(f"{name}: {scene} -> {item['name']}")
    assert not offenders, "unbounded full-frame items: " + ", ".join(offenders)


def t_the_check_sees_the_standby_thumbnail_and_the_clips():
    # Guard the guard: the items #871 fixed must be among the ones checked.
    seen = {(scene, item["name"]) for scene, item in
            _fullframe_items(_load("GT_Racing_Endurance.json"))}
    for pair in (("Standby", "Thumbnail"), ("Intro", "Intro Video"),
                 ("Outro", "Outro Video"), ("Trailer", "Trailer Video")):
        assert pair in seen, pair


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
