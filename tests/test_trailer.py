#!/usr/bin/env python3
"""Content checks for the Trailer control surfaces. Run: python3 tests/test_trailer.py"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def t_obs_collection_has_trailer_scene_and_source():
    cfg = json.loads(_read(os.path.join("src", "obs", "GT_Racing_Endurance.json")))
    names = {s.get("name") for s in cfg.get("sources", [])}
    assert "Trailer" in names, "no Trailer scene in the collection"
    assert "Trailer Video" in names, "no Trailer Video source"
    order = {e.get("name") for e in cfg.get("scene_order", [])}
    assert "Trailer" in order, "Trailer missing from scene_order"
    src = next(s for s in cfg["sources"] if s.get("name") == "Trailer Video")
    st = src.get("settings", {})
    assert st.get("local_file") == "__RACECAST_MEDIA__/trailer.mp4", st
    assert st.get("looping") is True and st.get("restart_on_activate") is True, st


def t_panel_has_trailer_macro():
    html = _read(os.path.join("src", "director", "director-panel.html"))
    i = html.index('{label:"TRAILER"')
    macro = html[i:html.index('}', i) + 1]
    assert 'scene:"Trailer"' in macro, macro
    # loop-clip-with-own-audio: mutes the feeds + Discord, like INTRO/OUTRO.
    for name in ("Feed A", "Feed B", "Discord Audio Capture"):
        assert name in macro, f"TRAILER macro must mute {name}"


def t_companion_has_trailer_button():
    cfg = json.loads(_read(os.path.join("src", "companion", "racecast-buttons.companionconfig")))

    def downs(btn):
        try:
            return btn["steps"]["0"]["action_sets"]["down"]
        except (KeyError, TypeError):
            return []

    def scene_val(a):
        return ((a.get("options") or {}).get("scene") or {}).get("value")

    target = None
    for page in cfg.get("pages", {}).values():
        for row in (page.get("controls", {}) or {}).values():
            for btn in (row or {}).values():
                if isinstance(btn, dict) and any(scene_val(a) == "Trailer" for a in downs(btn)
                                                 if isinstance(a, dict)):
                    target = btn
    assert target is not None, "no Companion button switches to the Trailer scene"
    assert (target.get("style") or {}).get("text") == "TRAILER", target.get("style")


def t_companion_red_flag_on_reachable_row():
    # RED FLAG sits at page 1 slot 1/7 because a Stream Deck XL is 8x4, so its old
    # 4/3 slot lived on an unreachable 5th row. TRAILER took the vacated 0/7.
    cfg = json.loads(_read(os.path.join("src", "companion", "racecast-buttons.companionconfig")))
    controls = cfg["pages"]["1"]["controls"]
    assert controls["0"]["7"]["style"]["text"] == "TRAILER", "slot 0/7 should now be TRAILER"
    rf = controls["1"]["7"]
    assert rf["style"]["text"] == "RED\nFLAG", "RED FLAG must sit at 1/7"
    assert set(rf.get("steps", {})) == {"0", "1"}, "RED FLAG must keep its 2-step toggle"
    assert "3" not in controls.get("4", {}), "old 4/3 RED FLAG slot must be freed"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
