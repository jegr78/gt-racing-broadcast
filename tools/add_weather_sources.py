#!/usr/bin/env python3
"""Add the three weather graphic sources + hidden Stint items to an OBS collection.

Idempotent: deep-copies the 'Standings' image source and its full-screen Stint scene
item as templates, so the result always matches OBS's schema. Re-running is a no-op once
the sources exist. Files are tokenised as __RACECAST_GRAPHICS__/<name>.png and resolved
by setup-assets.py.

Usage: python3 tools/add_weather_sources.py <collection.json>
"""
import copy, json, sys

WEATHER = [
    ("Race Weather 1", "c1c1c1c1-0000-4000-8000-000000000001"),
    ("Race Weather 2", "c2c2c2c2-0000-4000-8000-000000000002"),
    ("Quali Weather",  "c3c3c3c3-0000-4000-8000-000000000003"),
]


def add_weather_sources(d):
    """Mutate the collection in place. Return the list of names added."""
    srcs = d["sources"]
    stint = next(s for s in srcs if s.get("id") == "scene" and s.get("name") == "Stint")
    items = stint["settings"]["items"]
    tmpl_src = next(s for s in srcs if s.get("name") == "Standings")
    tmpl_item = next(it for it in items if it.get("name") == "Standings")
    added = []
    for name, uuid in WEATHER:
        if any(s.get("name") == name for s in srcs):
            continue
        src = copy.deepcopy(tmpl_src)
        src["name"] = name
        src["uuid"] = uuid
        src["settings"] = dict(src.get("settings", {}))
        src["settings"]["file"] = f"__RACECAST_GRAPHICS__/{name}.png"
        srcs.append(src)

        item = copy.deepcopy(tmpl_item)
        item["name"] = name
        item["source_uuid"] = uuid
        item["visible"] = False
        item["locked"] = False
        item["id"] = max(it.get("id", 0) for it in items) + 1
        item["pos"] = {"x": 0.0, "y": 0.0}
        item["scale"] = {"x": 1.0, "y": 1.0}
        item["bounds_type"] = 2                  # OBS_BOUNDS_SCALE_INNER = "fit"
        item["bounds"] = {"x": 1920.0, "y": 1080.0}
        items.append(item)
        added.append(name)
    return added


def main(path):
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    added = add_weather_sources(d)
    if not added:
        print(f"{path}: weather sources already present, skip"); return
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=4)
    print(f"{path}: added {', '.join(added)} (hidden full-screen Stint items)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: add_weather_sources.py <collection.json>")
    main(sys.argv[1])
