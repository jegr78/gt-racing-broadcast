#!/usr/bin/env python3
"""Content checks for the Intermission control surfaces. Run: python3 tests/test_intermission.py"""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def t_panel_has_intermission_macro():
    html = _read(os.path.join("src", "director", "director-panel.html"))
    assert "INTERMISSION" in html
    assert "Intermission" in html            # scene name in the macro
    assert "Intermission Music" in html      # audio fader input


def t_companion_has_intermission_button():
    raw = _read(os.path.join("src", "companion", "racecast-buttons.companionconfig"))
    json.loads(raw)                           # must stay valid JSON
    assert "Intermission" in raw              # a button/action references the scene


def t_panel_intermission_macro_does_not_mute():
    # The INTERMISSION macro is a pure scene switch: the feeds and Discord are not in
    # the scene, so muting them here would only force a manual reset on the next switch.
    html = _read(os.path.join("src", "director", "director-panel.html"))
    i = html.index('{label:"INTERMISSION"')
    macro = html[i:html.index('}', i) + 1]    # the macro object only (before any trailing comment)
    assert "mute:[]" in macro                 # empty mute list
    for name in ("Feed A", "Feed B", "Discord Audio Capture"):
        assert name not in macro, f"INTERMISSION macro must not touch audio ({name})"


def t_companion_intermission_button_only_switches_scene():
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
                if isinstance(btn, dict) and any(scene_val(a) == "Intermission" for a in downs(btn)
                                                 if isinstance(a, dict)):
                    target = btn
    assert target is not None, "no Companion button switches to the Intermission scene"
    # a pure scene switch must mute no source, so no set_source_mute action
    for a in downs(target):
        if isinstance(a, dict):
            assert (a.get("options") or {}).get("source") is None, \
                "Intermission button must not mute feeds/Discord (pure scene switch)"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
