#!/usr/bin/env python3
"""Add the 'Standby Cover' incident hold graphic to an OBS scene collection (idempotent).

It deep-copies the existing 'Thumbnail' image source, pointing at the dedicated
'Standby Cover.png' graphic rather than the Standby scene's 'Standby.png', and the
'Standings' full-screen scene item as templates, so the result always matches OBS's
schema. The cover sits BELOW the HUD group, so the Race Control banner and timer stay
visible; the existing 'Feed POV' item is moved below the HUD too, directly after Feed B,
so the cover hides the POV PiP as well. Re-running is a no-op once 'Standby Cover'
exists.

Usage: python3 tools/add_standby_cover.py <collection.json>
"""
import copy, json, sys

COVER_NAME = "Standby Cover"
COVER_UUID = "bbbbbbb1-0000-4000-8000-000000000001"   # A/B/POV use aaaaaaaN-; cover bbbbbbb1-
COVER_FILE = "__RACECAST_GRAPHICS__/Standby Cover.png"
POV_NAME   = "Feed POV"


def _feedb_index(items):
    """Index of the non-backup 'Feed B' scene item."""
    return next(i for i, it in enumerate(items)
                if it.get("name") == "Feed B" and not it.get("group_item_backup"))


def add_standby_cover(d):
    """Mutate the collection dict in place. Return True if changed, False if already present."""
    srcs = d["sources"]
    if any(s.get("name") == COVER_NAME for s in srcs):
        return False
    thumb = next(s for s in srcs if s.get("name") == "Thumbnail")
    stint = next(s for s in srcs if s.get("id") == "scene" and s.get("name") == "Stint")
    items = stint["settings"]["items"]

    src = copy.deepcopy(thumb)
    src["name"] = COVER_NAME
    src["uuid"] = COVER_UUID
    src["settings"] = dict(src.get("settings", {}))
    src["settings"]["file"] = COVER_FILE
    srcs.append(src)

    # A present 'Feed POV' moves to sit directly after Feed B.
    insert_at = _feedb_index(items) + 1
    pov = next((it for it in items if it.get("name") == POV_NAME
                and not it.get("group_item_backup")), None)
    if pov is not None:
        items.remove(pov)
        insert_at = _feedb_index(items) + 1       # recompute after removal
        items.insert(insert_at, pov)
        insert_at += 1                            # cover goes after the moved POV

    # The cover's Stint item copies the 'Standings' full-screen insert.
    tmpl = next(it for it in items if it.get("name") == "Standings")
    item = copy.deepcopy(tmpl)
    item["name"] = COVER_NAME
    item["source_uuid"] = COVER_UUID
    item["visible"] = False
    item["locked"] = False
    item["id"] = max(it.get("id", 0) for it in items) + 1
    item["pos"] = {"x": 0.0, "y": 0.0}
    item["scale"] = {"x": 1.0, "y": 1.0}
    item["bounds_type"] = 2                        # OBS_BOUNDS_SCALE_INNER = "fit"
    item["bounds"] = {"x": 1920.0, "y": 1080.0}
    items.insert(insert_at, item)
    return True


def main(path):
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    if not add_standby_cover(d):
        print(f"{path}: '{COVER_NAME}' already present, skip"); return
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=4)
    print(f"{path}: added '{COVER_NAME}' (hidden full-screen, below HUD) "
          f"and moved '{POV_NAME}' below the HUD group")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: add_standby_cover.py <collection.json>")
    main(sys.argv[1])
