#!/usr/bin/env python3
"""Stdlib unit checks for tools/add_standby_cover.py. Run: python3 tests/test_standby.py"""
import importlib.util, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "add_standby_cover", os.path.join(ROOT, "tools", "add_standby_cover.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def _collection():
    """Minimal Stint collection mirroring the real structure (incl. a group backup)."""
    return {"sources": [
        {"id": "image_source", "name": "Thumbnail",
         "uuid": "aecf782b-9f16-4c9d-ad0d-0a78a78cbcc3",
         "settings": {"file": "__RACECAST_GRAPHICS__/Standby.png"}},
        {"id": "image_source", "name": "Standings",
         "uuid": "dddddd01-0000-4000-8000-000000000001",
         "settings": {"file": "__RACECAST_GRAPHICS__/Standings.png"}},
        {"id": "scene", "name": "Stint", "uuid": "11111111-1111-4111-8111-111111111111",
         "settings": {"items": [
            {"name": "Discord", "source_uuid": "disc", "id": 23,
             "visible": True, "group_item_backup": False},
            {"name": "Feed A", "source_uuid": "a", "id": 1,
             "visible": True, "group_item_backup": False},
            {"name": "Feed B", "source_uuid": "b", "id": 2,
             "visible": False, "group_item_backup": False},
            {"name": "Overlay", "source_uuid": "ov", "id": 3,
             "visible": True, "group_item_backup": True},
            {"name": "HUD", "source_uuid": "hud", "id": 24,
             "visible": True, "group_item_backup": False},
            {"name": "Standings", "source_uuid": "dddddd01-0000-4000-8000-000000000001",
             "id": 18, "visible": False, "group_item_backup": False,
             "pos": {"x": 0.0, "y": 0.0}, "scale": {"x": 1.0, "y": 1.0},
             "bounds_type": 2, "bounds": {"x": 1920.0, "y": 1080.0}},
            {"name": "Feed POV", "source_uuid": "aaaaaaa3-0000-4000-8000-000000000003",
             "id": 25, "visible": False, "group_item_backup": False,
             "pos": {"x": 1496.0, "y": 644.0}, "scale": {"x": 1.0, "y": 1.0},
             "bounds_type": 2, "bounds": {"x": 384.0, "y": 216.0}},
         ]}},
    ]}


def _stint_items(d):
    s = next(x for x in d["sources"] if x.get("id") == "scene" and x.get("name") == "Stint")
    return s["settings"]["items"]


def _nb_names(items):
    """Non-backup item names in z-order (back -> front)."""
    return [it["name"] for it in items if not it.get("group_item_backup")]


def t_adds_source():
    d = _collection()
    assert m.add_standby_cover(d) is True
    src = next(s for s in d["sources"] if s.get("name") == "Standby Cover")
    assert src["id"] == "image_source"
    assert src["uuid"] == m.COVER_UUID
    assert src["settings"]["file"] == m.COVER_FILE
    assert m.COVER_FILE == "__RACECAST_GRAPHICS__/Standby Cover.png", \
        "dedicated graphic, NOT the Standby scene's thumbnail (Standby.png)"


def t_cover_item_hidden_fullscreen():
    d = _collection(); m.add_standby_cover(d)
    cov = next(it for it in _stint_items(d) if it["name"] == "Standby Cover")
    assert cov["visible"] is False
    assert cov["locked"] is False
    assert cov["source_uuid"] == m.COVER_UUID
    assert cov["pos"] == {"x": 0.0, "y": 0.0}
    assert cov["bounds_type"] == 2
    assert cov["bounds"] == {"x": 1920.0, "y": 1080.0}


def t_zorder_below_hud():
    d = _collection(); m.add_standby_cover(d)
    names = _nb_names(_stint_items(d))
    # Feed POV then Standby Cover sit right after Feed B and before the HUD group.
    i = names.index("Feed B")
    assert names[i + 1] == "Feed POV"
    assert names[i + 2] == "Standby Cover"
    assert names[i + 3] == "HUD"


def t_pov_moved_below_hud():
    d = _collection(); m.add_standby_cover(d)
    names = _nb_names(_stint_items(d))
    assert names.index("Feed POV") < names.index("HUD")
    assert names.index("Standby Cover") < names.index("HUD")


def t_unique_ids():
    d = _collection(); m.add_standby_cover(d)
    ids = [it["id"] for it in _stint_items(d)]
    assert len(ids) == len(set(ids)), ids


def t_idempotent():
    d = _collection()
    assert m.add_standby_cover(d) is True
    n1 = len(_stint_items(d))
    assert m.add_standby_cover(d) is False           # second run is a no-op
    assert len(_stint_items(d)) == n1
    assert sum(s.get("name") == "Standby Cover" for s in d["sources"]) == 1


def t_no_pov_fallback():
    d = _collection()
    items = _stint_items(d)
    items[:] = [it for it in items if it["name"] != "Feed POV"]   # --no-pov collection
    assert m.add_standby_cover(d) is True
    names = _nb_names(_stint_items(d))
    i = names.index("Feed B")
    assert names[i + 1] == "Standby Cover"           # cover lands right after Feed B
    assert names[i + 2] == "HUD"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
