#!/usr/bin/env python3
"""Add the commentary microphone to the endurance OBS scene collection (idempotent, #593).

A local stint (#592) carries the producer's own commentary: the capture card's game
audio rides in the feed, the microphone is this separate OBS input. It is added as
"Commentary Mic Device", the same leaf name, uuid, token and macOS form the solo
collections use, so setup-assets.localize_device_sources fills it from RACECAST_MIC,
and placed as a visible item in Stint and Splitscreen, the two scenes a local stint
can be on air in.

Two deliberate differences from the solo collections:
- It ships MUTED. The relay opens it only while a local stint is on air
  (obs_ws.feed_audio_plan); every other state keeps it closed.
- It is a direct audio item, not the "Commentary Mic" wrapper scene. A visible
  nested scene is one more render layer in Stint, and a third layer tips a weak
  host over the render cliff (#559). An audio input has nothing to render.

Deep-copies proven nodes as schema-correct templates (the Feed POV leaf, the Discord
item in Stint). Re-running is a no-op once the leaf exists.

Usage: python3 tools/add_commentary_mic.py <collection.json>
"""
import copy, json, sys

MIC_SOURCE = "Commentary Mic Device"
MIC_UUID = "aaaaaaa6-0000-4000-8000-000000000006"   # = derive-solo-templates U["mic_src"]
MIC_TOKEN = "__RACECAST_MIC__"
TARGET_SCENES = ("Stint", "Splitscreen")


def _scene(srcs, name):
    return next(s for s in srcs if s.get("name") == name and s.get("id") == "scene")


def add_commentary_mic(d):
    """Mutate the collection dict in place. Return True if changed, False if already present."""
    srcs = d["sources"]
    if any(s.get("name") == MIC_SOURCE for s in srcs):
        return False

    # The leaf is the macOS coreaudio form the solo collections commit.
    leaf = copy.deepcopy(next(s for s in srcs if s.get("name") == "Feed POV"))
    leaf["name"] = MIC_SOURCE
    leaf["uuid"] = MIC_UUID
    leaf["id"] = leaf["versioned_id"] = "coreaudio_input_capture"
    leaf["settings"] = {"device_id": MIC_TOKEN}
    leaf["muted"] = True
    leaf["monitoring_type"] = 0          # output only: no self-monitor, no echo
    srcs.append(leaf)

    # One audio item per target scene, cloned from its Discord item.
    for name in TARGET_SCENES:
        scene = _scene(srcs, name)
        st = scene["settings"]
        tmpl = next(i for i in st["items"] if i.get("name") == "Discord")
        # id_counter lags the highest item id in this collection; take the larger.
        item_id = max([int(st.get("id_counter", 0))] + [int(i["id"]) for i in st["items"]]) + 1
        item = copy.deepcopy(tmpl)
        item["name"] = MIC_SOURCE
        item["source_uuid"] = MIC_UUID
        item["id"] = item_id
        item["visible"] = True           # audibility is the relay's mute, not visibility
        item["locked"] = True
        item["show_transition"] = {"duration": 0}
        item["hide_transition"] = {"duration": 0}
        st["items"].append(item)
        st["id_counter"] = item_id
        hk = scene.setdefault("hotkeys", {})
        hk[f"libobs.show_scene_item.{item_id}"] = []
        hk[f"libobs.hide_scene_item.{item_id}"] = []
    return True


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip().splitlines()[-1])
    path = sys.argv[1]
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    if not add_commentary_mic(d):
        print(f"{MIC_SOURCE} already present in {path}")
        return
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=4)
    print(f"added {MIC_SOURCE} to {', '.join(TARGET_SCENES)} in {path}")


if __name__ == "__main__":
    main()
