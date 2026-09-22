#!/usr/bin/env python3
"""Swap the 13 per-cell HUD sheet browser sources for one 'HUD Overlay' source.

Replaces the old chroma-keyed Google-Sheets-editor sources (Stint, Streamer,
Session, Round Track/Flag/Country, Team 1-3 Brand/Name, Race Control) with a
single 'HUD Overlay' browser source pointing at the relay (http://127.0.0.1:8088/hud,
1920x1080), placed inside the existing 'HUD' group just above 'Overlay'. Keeps
'Overlay', the frame, and 'HUD Race Timer', the stagetimer. The Director's single
HUD group toggle is preserved.

Edits all three places OBS keeps this state in sync:
  1) d['sources']                      source definitions
  2) d['groups'][HUD].settings.items   the group's children
  3) each scene's settings.items       the group_item_backup copies

Idempotent: re-running once 'HUD Overlay' exists is a no-op.

Usage: python3 tools/swap-hud-overlay.py <collection.json>
"""
import copy, json, sys

OLD_NAMES = {
    "HUD - Stint", "HUD - Streamer", "HUD - Session",
    "HUD - Round Track", "HUD - Round Flag", "HUD - Round Country",
    "HUD Team 1 Brand", "HUD Team 1 Name",
    "HUD Team 2 Brand", "HUD Team 2 Name",
    "HUD Team 3 Brand", "HUD Team 3 Name",
    "HUD Race Control",
}
NEW_NAME = "HUD Overlay"
NEW_UUID = "0ad0fee0-0000-4000-8000-000000000001"
NEW_URL = "http://127.0.0.1:8088/hud"
ANCHOR = "Overlay"   # insert the new source directly above the frame


def _scene_items(scene):
    return scene.get("settings", {}).get("items", [])


def main(path):
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    sources = d["sources"]

    if any(s.get("name") == NEW_NAME for s in sources):
        print(f"{path}: '{NEW_NAME}' already present, skip")
        return

    group = next((g for g in d.get("groups", []) if g.get("name") == "HUD"), None)
    if group is None:
        sys.exit("ERROR: no 'HUD' group in this collection.")
    gitems = group["settings"]["items"]

    # A stable id for the new child, unique across the group AND every scene that
    # carries it. The group child id and the scene backup id are kept identical.
    scenes_with_hud = [s for s in sources if s.get("id") == "scene"
                       and any(it.get("name") == "HUD" for it in _scene_items(s))]
    max_id = max([it["id"] for it in gitems]
                 + [it["id"] for s in scenes_with_hud for it in _scene_items(s)])
    new_id = max_id + 1

    # 1) source object: clone an old HUD browser source, drop the chroma key,
    #    repoint at the relay. Capture the template before removing the old ones.
    template_src = next(s for s in sources if s.get("name") in OLD_NAMES
                        and s.get("id") == "browser_source")
    new_src = copy.deepcopy(template_src)
    new_src["name"] = NEW_NAME
    new_src["uuid"] = NEW_UUID
    new_src["filters"] = []                       # no chroma key; the page is transparent
    new_src["settings"] = {"url": NEW_URL, "width": 1920, "height": 1080,
                           "restart_when_active": True}   # reload on scene-activate
                                                          # heals a cold start
    sources[:] = [s for s in sources if s.get("name") not in OLD_NAMES]
    sources.append(new_src)

    # 2) group child item: clone the 'Overlay' child, full-canvas 1920x1080 at 0,0.
    ov_child = next(it for it in gitems if it.get("name") == ANCHOR)
    new_child = copy.deepcopy(ov_child)
    new_child["name"] = NEW_NAME
    new_child["source_uuid"] = NEW_UUID
    new_child["id"] = new_id
    new_child["visible"] = True
    new_child["locked"] = False
    gitems[:] = [it for it in gitems if it.get("name") not in OLD_NAMES]
    gi = next(i for i, it in enumerate(gitems) if it.get("name") == ANCHOR)
    gitems.insert(gi + 1, new_child)

    # 3) per-scene group_item_backup copies
    for scene in scenes_with_hud:
        items = _scene_items(scene)
        ov_bak = next(it for it in items if it.get("name") == ANCHOR
                      and it.get("group_item_backup"))
        new_bak = copy.deepcopy(ov_bak)
        new_bak["name"] = NEW_NAME
        new_bak["source_uuid"] = NEW_UUID
        new_bak["id"] = new_id
        new_bak["visible"] = True
        new_bak["locked"] = False
        new_bak["group_item_backup"] = True
        items[:] = [it for it in items if it.get("name") not in OLD_NAMES]
        bi = next(i for i, it in enumerate(items) if it.get("name") == ANCHOR
                  and it.get("group_item_backup"))
        items.insert(bi + 1, new_bak)

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=4)
    print(f"{path}: replaced {len(OLD_NAMES)} sheet sources with '{NEW_NAME}' "
          f"(id {new_id}) in the HUD group of {len(scenes_with_hud)} scene(s).")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: swap-hud-overlay.py <collection.json>")
    main(sys.argv[1])
