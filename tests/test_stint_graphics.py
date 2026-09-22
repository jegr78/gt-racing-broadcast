#!/usr/bin/env python3
"""Structural guard for the 12 added Stint full-page graphics (info + grid).
Run: python3 tests/test_stint_graphics.py

Asserts the three hardcoded surfaces agree on the exact scene/source strings: the
OBS collection, the Director Panel and Companion. A name drift between them fails
silently in production, so it is pinned here."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OBS = os.path.join(ROOT, "src", "obs", "GT_Racing_Endurance.json")
PANEL = os.path.join(ROOT, "src", "director", "director-panel.html")
COMPANION = os.path.join(ROOT, "src", "companion",
                         "racecast-buttons.companionconfig")

NEW_GRAPHICS = [
    "Weekend Info", "Race Info", "Next Event", "Starting Grid",
    "Grid Row 1", "Grid Row 2", "Grid Row 3", "Grid Row 4",
    "Grid Row 5", "Grid Row 6", "Grid Row 7", "Grid Row 8",
]


def _obs():
    with open(OBS, encoding="utf-8") as fh:
        return json.load(fh)


def t_obs_image_sources_present():
    d = _obs()
    by_name = {s["name"]: s for s in d["sources"] if s.get("id") == "image_source"}
    for label in NEW_GRAPHICS:
        assert label in by_name, f"missing image_source: {label}"
        s = by_name[label]
        assert s["settings"]["file"] == f"__RACECAST_GRAPHICS__/{label}.png", label
        assert s["settings"].get("linear_alpha") is True, label


def t_obs_stint_scene_items_present():
    d = _obs()
    stint = next(s for s in d["sources"] if s.get("name") == "Stint"
                 and s.get("id") == "scene")
    items = {i["name"]: i for i in stint["settings"]["items"]}
    src_uuid = {s["name"]: s["uuid"] for s in d["sources"]
                if s.get("id") == "image_source"}
    ids = [i["id"] for i in stint["settings"]["items"]]
    assert len(ids) == len(set(ids)), "duplicate scene-item id in Stint"
    for label in NEW_GRAPHICS:
        assert label in items, f"missing Stint scene-item: {label}"
        it = items[label]
        assert it["visible"] is False, label
        assert it["source_uuid"] == src_uuid[label], label
        assert it["bounds"] == {"x": 1920.0, "y": 1080.0}, label
        assert it["show_transition"]["name"] == f"{label} Show Transition", label
        assert it["hide_transition"]["name"] == f"{label} Hide Transition", label


def t_panel_lists_new_graphics():
    with open(PANEL, encoding="utf-8") as fh:
        html = fh.read()
    assert "graphicsPreRace" in html, "missing CONFIG.graphicsPreRace"
    assert "graphicsGrid" in html, "missing CONFIG.graphicsGrid"
    assert 'id="gfxPreRaceBus"' in html
    assert 'id="gfxGridBus"' in html
    for label in NEW_GRAPHICS:
        assert f'source:"{label}"' in html, f"panel missing source: {label}"


def t_companion_toggles_new_graphics():
    with open(COMPANION, encoding="utf-8") as fh:
        cfg = json.load(fh)
    toggled = set()
    def walk(o):
        if isinstance(o, dict):
            if o.get("definitionId") == "toggle_scene_item":
                opt = o.get("options", {})
                scene = (opt.get("scene") or {}).get("value")
                source = (opt.get("source") or {}).get("value")
                if scene == "Stint" and source:
                    toggled.add(source)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(cfg)
    for label in NEW_GRAPHICS:
        assert label in toggled, f"companion missing toggle for: {label}"
    # Page 1 is 8x4, so a 5th row would be unreachable; the info and grid graphics
    # live on the dedicated graphics page instead.
    assert cfg["pages"]["1"]["gridSize"]["maxRow"] <= 3
    names = [(p.get("name") or "").upper() for p in cfg["pages"].values()]
    assert any("GRAPHIC" in n for n in names), f"no graphics page (pages: {names})"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")
