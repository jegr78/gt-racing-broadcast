#!/usr/bin/env python3
"""Add the 'Feed POV' PiP source to an OBS scene-collection JSON (idempotent).

It deep-copies the existing 'Feed A' source object and its Stint scene item as
templates, then overrides only the POV-specific fields, so the result always
matches OBS's schema. Re-running is a no-op once 'Feed POV' exists.

Usage: python3 tools/add_pov_source.py <collection.json>
"""
import copy, json, sys

POV_NAME  = "Feed POV"
POV_UUID  = "aaaaaaa3-0000-4000-8000-000000000003"   # A=…001, B=…002, POV=…003
POV_INPUT = "http://127.0.0.1:53003"
POS    = {"x": 1496.0, "y": 644.0}   # bottom-right, above the HUD lower-third
BOUNDS = {"x": 384.0,  "y": 216.0}   # 1/5 size, 16:9


def main(path):
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    srcs = d["sources"]
    if any(s.get("name") == POV_NAME for s in srcs):
        print(f"{path}: '{POV_NAME}' already present, skip"); return
    feedA = next(s for s in srcs if s.get("name") == "Feed A")
    stint = next(s for s in srcs if s.get("id") == "scene" and s.get("name") == "Stint")

    src = copy.deepcopy(feedA)
    src["name"] = POV_NAME
    src["uuid"] = POV_UUID
    src["muted"] = True
    src["settings"] = dict(src.get("settings", {}))
    src["settings"]["input"] = POV_INPUT
    src["settings"]["close_when_inactive"] = True        # no decode while hidden
    src["settings"]["restart_on_activate"] = True        # grab the port on SHOW
    src["settings"]["reconnect_delay_sec"] = 10
    srcs.append(src)

    # Items are ordered back to front, so appending puts POV front-most.
    items = stint["settings"]["items"]
    tmpl = next(it for it in items if it.get("name") == "Feed A")
    item = copy.deepcopy(tmpl)
    item["name"] = POV_NAME
    item["source_uuid"] = POV_UUID
    item["visible"] = False
    item["locked"] = False
    item["id"] = max(it.get("id", 0) for it in items) + 1
    item["pos"] = dict(POS)
    item["scale"] = {"x": 1.0, "y": 1.0}
    item["bounds_type"] = 2                               # OBS_BOUNDS_SCALE_INNER = "fit"
    item["bounds"] = dict(BOUNDS)
    items.append(item)

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=4)
    print(f"{path}: added '{POV_NAME}' (hidden, "
          f"{int(BOUNDS['x'])}x{int(BOUNDS['y'])} @ {int(POS['x'])},{int(POS['y'])})")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: add_pov_source.py <collection.json>")
    main(sys.argv[1])
