#!/usr/bin/env python3
"""Maintainer tool (not shipped): derive the two solo OBS scene collections from the
proven src/obs/GT_Racing_Endurance.json so they stay OBS-valid and regenerable.

Run: python3 tools/derive-solo-templates.py   (rewrites src/obs/GT_Racing_Solo_*.json)

Strategy: deep-copy real nodes from the endurance collection and mutate minimally, so
every OBS-required field shape is inherited from a proven-importable file. Scaffold
dicts are never hand-authored, because a missing key makes OBS refuse the import.

Result per file: the A/B ping-pong is gone, with Feed A/B and the Stint/Splitscreen
scenes dropped. A "Program" scene keeps Feed POV plus the HUD/graphics overlays and adds
two device inputs, "Solo Capture Device" as a full-frame background and "Solo Webcam
Device" as a bottom-left PiP, each wrapped in its own scene on the Discord "scene wraps
one source" model. The device leaf sources carry the tokens __RACECAST_CAPTURE__ and
__RACECAST_WEBCAM__; the committed form is the macOS av_capture_input source, and
setup-assets.py localizes the source type and device settings per OS at import time.
"""
import copy
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OBS = os.path.join(ROOT, "src", "obs")

# Fixed UUIDs, never uuid4(), so re-runs do not churn the committed JSON.
U = {
    "cap_src": "aaaaaaa4-0000-4000-8000-000000000004",
    "cam_src": "aaaaaaa5-0000-4000-8000-000000000005",
    "cap_scene": "bbbbbbb4-0000-4000-8000-000000000004",
    "cam_scene": "bbbbbbb5-0000-4000-8000-000000000005",
    "program": "ccccccc0-0000-4000-8000-000000000000",
    "mic_src": "aaaaaaa6-0000-4000-8000-000000000006",
    "mic_scene": "bbbbbbb6-0000-4000-8000-000000000006",
    "tyres_src": "aaaaaaa7-0000-4000-8000-000000000007",
    "tyres_scene": "bbbbbbb7-0000-4000-8000-000000000007",
}

DROP_SCENES = {"Stint", "Splitscreen"}
# The endurance commentary mic (#593) is dropped with the feeds and rebuilt below in
# the solo shape: hot, wrapped, wired into more scenes.
DROP_SOURCES = {"Feed A", "Feed B", "Commentary Mic Device"}

# Committed device tokens (localized per OS by setup-assets.localize_device_sources).
CAPTURE_TOKEN = "__RACECAST_CAPTURE__"
WEBCAM_TOKEN = "__RACECAST_WEBCAM__"
MIC_TOKEN = "__RACECAST_MIC__"
TYRES_TOKEN = "__RACECAST_TYRES_CAPTURE__"

# Isolates GT7's bottom-left tyre/fuel/sprint widget from a full-frame 1920x1080
# capture. Fixed values; the operator fine-tunes in OBS if their capture differs.
TYRES_CROP = {"crop_left": 258, "crop_top": 950, "crop_right": 1336, "crop_bottom": 18}

# Scenes the "Commentary Mic" nested scene is wired into as an item. The rendered
# Intro/Outro clips are excluded because they carry their own audio.
MIC_TARGET_SCENES = ("Program", "Interview", "Standby", "Intermission", "Discord")

# scene_order after derivation (drops Stint/Splitscreen, adds the device scenes).
SCENE_ORDER = ["Program", "Standby", "Intro", "Outro", "Interview", "Discord",
               "Intermission", "Solo Capture", "Solo Webcam", "Commentary Mic"]

START_SCENE = "Standby"

OUTPUTS = ("GT_Racing_Solo_Commentary.json", "GT_Racing_Solo_POV.json")


def _by_name(sources):
    return {s.get("name"): s for s in sources}


def _device_leaf(template_leaf, uuid, name, token, source_id="av_capture_input",
                  settings_key="device"):
    """Clone a proven leaf source and retarget it as a device source carrying the
    given token. Only name/uuid/id/versioned_id/settings are overridden; every other
    OBS-required field is inherited from the template. Defaults match the committed
    macOS video-capture form, and the audio mic leaf overrides source_id/settings_key
    to the macOS coreaudio form."""
    leaf = copy.deepcopy(template_leaf)
    leaf["name"] = name
    leaf["uuid"] = uuid
    leaf["id"] = source_id
    leaf["versioned_id"] = source_id
    leaf["settings"] = {settings_key: token}
    return leaf


def _device_scene(discord_scene, uuid, name, src_uuid, src_name):
    """Clone the Discord scene, the 'scene wraps one source' model, and point its single
    item at the given device leaf, rendered full-frame (bounds_type 2 = SCALE_INNER)."""
    scene = copy.deepcopy(discord_scene)
    scene["name"] = name
    scene["uuid"] = uuid
    item = copy.deepcopy(scene["settings"]["items"][0])
    item["name"] = src_name
    item["source_uuid"] = src_uuid
    item["visible"] = True
    item["locked"] = True
    item["bounds_type"] = 2
    item["pos"] = {"x": 0.0, "y": 0.0}
    item["bounds"] = {"x": 1920.0, "y": 1080.0}
    scene["settings"]["items"] = [item]
    return scene


def _nested_scene_item(template_item, name, src_uuid, item_id):
    """Clone a proven audio-style scene item, bounds_type 0 with no visual footprint,
    the shape already used to reference the "Discord" scene from other scenes, and
    retarget it at a different nested scene. Wires the "Commentary Mic" scene into the
    five target scenes."""
    it = copy.deepcopy(template_item)
    it["name"] = name
    it["source_uuid"] = src_uuid
    it["id"] = item_id
    # An item that never renders has no use for the template's show/hide transition.
    it["show_transition"] = {"duration": 0}
    it["hide_transition"] = {"duration": 0}
    return it


def _add_mic_reference(scene, mic_ref_template, mic_scene_uuid):
    """Deep-copy `scene` and append a "Commentary Mic" nested-scene item, the same
    pattern other scenes already use to reference "Discord"."""
    scene = copy.deepcopy(scene)
    next_id = int(scene["settings"].get("id_counter", 0)) + 1
    item = _nested_scene_item(mic_ref_template, "Commentary Mic", mic_scene_uuid, next_id)
    scene["settings"]["items"].append(item)
    scene["settings"]["id_counter"] = next_id
    return scene


def _program_item(template_item, name, src_uuid, pos, bounds, item_id):
    """Clone a proven Stint scene item (inherits every transform key) and retarget it."""
    it = copy.deepcopy(template_item)
    it["name"] = name
    it["source_uuid"] = src_uuid
    it["visible"] = True
    it["locked"] = True
    it["bounds_type"] = 2
    it["pos"] = {"x": float(pos[0]), "y": float(pos[1])}
    it["bounds"] = {"x": float(bounds[0]), "y": float(bounds[1])}
    it["id"] = item_id
    return it


def derive(with_tyres=False):
    """Build the solo collection. `with_tyres=True`, Commentary only, adds the
    'Solo Tyres/Fuel Capture' source cropped to GT7's bottom-left tyre/fuel widget.
    POV omits it, because the driver's own feed already shows it."""
    with open(os.path.join(OBS, "GT_Racing_Endurance.json"), encoding="utf-8") as fh:
        col = json.load(fh)

    by = _by_name(col["sources"])
    stint = by["Stint"]
    discord_scene = by["Discord"]
    pov_leaf = by["Feed POV"]

    program = copy.deepcopy(stint)
    program["name"] = "Program"
    program["uuid"] = U["program"]

    items = program["settings"]["items"]
    # Feed POV and the HUD/overlays/graphics/flags/Discord/Standby items stay.
    items = [it for it in items if it.get("name") not in DROP_SOURCES]
    # The dropped endurance mic item's show/hide hotkeys go with it (#593).
    for it in stint["settings"]["items"]:
        if it.get("name") == "Commentary Mic Device":
            for verb in ("show", "hide"):
                program.get("hotkeys", {}).pop(f"libobs.{verb}_scene_item.{it['id']}", None)

    # The Feed POV item is the cleanest transform template for the two new PiP items.
    pov_item = next(it for it in items if it.get("name") == "Feed POV")
    # The "Discord" nested-scene reference is the template for the new "Commentary
    # Mic" one: same bounds_type-0 shape.
    discord_ref_item = next(it for it in items if it.get("name") == "Discord")
    # The endurance Stint scene hides Discord, but solo has no such scene split and the
    # commentator's Discord audio belongs on air in Program, so re-assert visibility.
    discord_ref_item["visible"] = True

    # Allocated just ABOVE the highest inherited id, so later growth in the base Stint
    # scene can never collide with the solo additions.
    base_max = max((int(it["id"]) for it in items
                    if isinstance(it.get("id"), int)), default=0)
    cap_id, cam_id, tyres_id, mic_id = (base_max + 1, base_max + 2,
                                        base_max + 3, base_max + 4)
    # Full-frame background at the BOTTOM of the z-order, so it renders first.
    cap_item = _program_item(pov_item, "Solo Capture", U["cap_scene"],
                             (0, 0), (1920, 1080), item_id=cap_id)
    # Bottom-left PiP, inserted right after Feed POV.
    cam_item = _program_item(pov_item, "Solo Webcam", U["cam_scene"],
                             (24, 776), (384, 280), item_id=cam_id)
    # Commentary only: the same PiP transform template as the webcam, plus the crop.
    tyres_item = None
    if with_tyres:
        tyres_item = _program_item(pov_item, "Solo Tyres/Fuel Capture", U["tyres_scene"],
                                   (7, 926), (245, 84), item_id=tyres_id)
        tyres_item.update(TYRES_CROP)

    new_items = [cap_item]
    for it in items:
        new_items.append(it)
        if it.get("name") == "Feed POV":
            new_items.append(cam_item)
            if tyres_item is not None:
                new_items.append(tyres_item)
    # Audible in Program via a nested-scene reference with no visual footprint.
    mic_item_program = _nested_scene_item(discord_ref_item, "Commentary Mic",
                                          U["mic_scene"], item_id=mic_id)
    new_items.append(mic_item_program)
    program["settings"]["items"] = new_items
    program["settings"]["id_counter"] = max(
        int(program["settings"].get("id_counter", 0)), mic_id)

    # Each leaf is named distinctly from its wrapping scene, mirroring the Discord
    # precedent, so setup-assets' by-name lookup in localize_device_sources can never
    # collide a device leaf with its wrapping scene.
    cap_src = _device_leaf(pov_leaf, U["cap_src"], "Solo Capture Device", CAPTURE_TOKEN)
    cam_src = _device_leaf(pov_leaf, U["cam_src"], "Solo Webcam Device", WEBCAM_TOKEN)
    cap_scene = _device_scene(discord_scene, U["cap_scene"], "Solo Capture",
                              U["cap_src"], "Solo Capture Device")
    cam_scene = _device_scene(discord_scene, U["cam_scene"], "Solo Webcam",
                              U["cam_src"], "Solo Webcam Device")
    # Commentary only. setup-assets folds the leaf into Solo Capture Device when one
    # card carries both (#597). It inherits muted=True from the Feed POV template, since
    # the game audio already comes from Solo Capture. In the folded one-card case the
    # capture leaf sits in two nested scenes, and OBS >= 32.0 mixes a source that appears
    # twice in the audio tree only once.
    tyres_src = tyres_scene = None
    if with_tyres:
        tyres_src = _device_leaf(pov_leaf, U["tyres_src"], "Solo Tyres Capture Device",
                                 TYRES_TOKEN)
        tyres_scene = _device_scene(discord_scene, U["tyres_scene"], "Solo Tyres/Fuel Capture",
                                    U["tyres_src"], "Solo Tyres Capture Device")
    # The macOS coreaudio_input_capture form, wrapped in a scene cloned from Discord.
    mic_src = _device_leaf(pov_leaf, U["mic_src"], "Commentary Mic Device", MIC_TOKEN,
                           source_id="coreaudio_input_capture", settings_key="device_id")
    # The PRIMARY audio of a solo commentary broadcast, so it ships HOT, unlike the
    # muted-by-default capture and webcam leaves.
    mic_src["muted"] = False
    mic_scene = _device_scene(discord_scene, U["mic_scene"], "Commentary Mic",
                              U["mic_src"], "Commentary Mic Device")

    # Program already got its reference above, inline with the rest of its items.
    other_targets = [n for n in MIC_TARGET_SCENES if n != "Program"]
    mic_targets = {name: _add_mic_reference(by[name], discord_ref_item, U["mic_scene"])
                   for name in other_targets}

    # Remove the endurance-only scenes and sources, substitute the mic-wired scenes,
    # then append the solo additions.
    kept = []
    for s in col["sources"]:
        name = s.get("name")
        if name in (DROP_SCENES | DROP_SOURCES):
            continue
        kept.append(mic_targets[name] if name in mic_targets else s)
    additions = [cap_scene, cam_scene, mic_scene, cap_src, cam_src, mic_src]
    if with_tyres:
        additions += [tyres_scene, tyres_src]
    additions.append(program)
    kept.extend(additions)
    col["sources"] = kept

    scene_order = list(SCENE_ORDER)
    if with_tyres:
        scene_order.append("Solo Tyres/Fuel Capture")
    col["scene_order"] = [{"name": n} for n in scene_order]
    # current_scene / current_program_scene are plain strings in this collection.
    col["current_scene"] = START_SCENE
    col["current_program_scene"] = START_SCENE
    # setup-assets overrides this with the per-league name at localize time, but the
    # committed artifact must not carry the inherited endurance name.
    col["name"] = "GT Racing Solo"

    # The Splitscreen scene was dropped above, leaving its "Split HUD" group and
    # "Splitscreen Labels" leaf referenced by nothing (#304).
    col["sources"] = [s for s in col["sources"] if s.get("name") != "Splitscreen Labels"]
    if col.get("groups"):
        col["groups"] = [g for g in col["groups"] if g.get("name") != "Split HUD"]

    # The game, Discord and media must be audible AND streamed (monitoring_type 2 =
    # MONITOR_AND_OUTPUT), so the commentator hears the race in-headset and it lands in
    # the stream mix. The mic stays output-only at monitoring_type 0 to avoid an echo,
    # and the webcam and tyres captures stay muted. Applies to both solo outputs.
    cap_src["muted"] = False
    cap_src["monitoring_type"] = 2
    by_final = _by_name(col["sources"])
    for nm in ("Discord Audio Capture", "Intro Video", "Outro Video", "Intermission Music"):
        s = by_final.get(nm)
        if s is not None:
            s["monitoring_type"] = 2
    return col


def main():
    # Commentary gets the tyres/fuel second-capture; POV omits it.
    per_file = {"GT_Racing_Solo_Commentary.json": derive(with_tyres=True),
                "GT_Racing_Solo_POV.json": derive(with_tyres=False)}
    for fn in OUTPUTS:
        path = os.path.join(OBS, fn)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(per_file[fn], fh, ensure_ascii=False, indent=4)
            fh.write("\n")
        print("wrote", path)


if __name__ == "__main__":
    main()
