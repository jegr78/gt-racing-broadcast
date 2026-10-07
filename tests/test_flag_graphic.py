#!/usr/bin/env python3
"""Stdlib unit checks for flag-status graphics. Run: python3 tests/test_flag_graphic.py"""
import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
fg = _load("flag_graphic", ("src", "scripts", "flag_graphic.py"))
import placeholders  # noqa: E402


def t_sources_are_the_catalog_flags():
    assert list(fg.FLAG_GRAPHIC_SOURCES) == [
        "green", "yellow", "red", "safety-car", "virtual-safety-car", "checkered"]
    assert fg.FLAG_GRAPHIC_SOURCES["checkered"] == "Flag Checkered"
    assert fg.FLAG_GRAPHIC_SOURCES["green"] == "Flag Green"
    assert fg.FLAG_GRAPHIC_SOURCES["virtual-safety-car"] == "Flag Virtual Safety Car"
    assert fg.FLAG_GRAPHIC_SCENES == ("Stint", "Splitscreen")
    assert fg.FLAG_GRAPHIC_SCENES_SOLO == ("Program",)


def t_scenes_for_mode():
    assert fg.flag_graphic_scenes(solo=False) == ("Stint", "Splitscreen")
    assert fg.flag_graphic_scenes(solo=True) == ("Program",)


def _scenes_with_all_flags(collection):
    with open(os.path.join(ROOT, "src", "obs", collection), encoding="utf-8") as fh:
        data = json.load(fh)
    flags = set(fg.FLAG_GRAPHIC_SOURCES.values())
    return {s["name"] for s in data["sources"] if s.get("id") == "scene"
            and flags <= {i["name"] for i in s["settings"].get("items", [])}}


def t_mode_scenes_carry_all_flag_items_in_the_shipped_collections():
    for collection, solo in (("GT_Racing_Endurance.json", False),
                             ("GT_Racing_Solo_POV.json", True),
                             ("GT_Racing_Solo_Commentary.json", True)):
        have = _scenes_with_all_flags(collection)
        want = set(fg.flag_graphic_scenes(solo))
        assert want <= have, f"{collection}: flag scenes {sorted(want)} not all in {sorted(have)}"


def t_normalize_canonical_aliases_and_clear():
    assert fg.normalize_flag_value("green") == "green"
    assert fg.normalize_flag_value("GREEN") == "green"
    assert fg.normalize_flag_value(" Safety Car ") == "safety-car"
    assert fg.normalize_flag_value("sc") == "safety-car"
    assert fg.normalize_flag_value("vsc") == "virtual-safety-car"
    assert fg.normalize_flag_value("") == ""
    assert fg.normalize_flag_value(None) == ""
    assert fg.normalize_flag_value("purple") is None
    assert fg.normalize_flag_value("Chequered") == "checkered"
    assert fg.normalize_flag_value("Checkered Flag") == "checkered"


def t_intents_show_one_hide_rest_in_both_scenes():
    intents = fg.flag_graphic_intents("yellow")
    assert len(intents) == 2 * len(fg.FLAG_GRAPHIC_SOURCES), "every flag in both scenes"
    on = [(sc, src) for (sc, src, en) in intents if en]
    assert on == [("Stint", "Flag Yellow"), ("Splitscreen", "Flag Yellow")]
    # everything else hidden
    assert all(not en for (sc, src, en) in intents if src != "Flag Yellow")


def t_intents_follow_the_given_scenes():
    intents = fg.flag_graphic_intents("red", scenes=fg.FLAG_GRAPHIC_SCENES_SOLO)
    assert {sc for (sc, _src, _en) in intents} == {"Program"}, intents
    assert [(sc, src) for (sc, src, en) in intents if en] == [("Program", "Flag Red")], intents


def t_solo_store_applies_only_to_program():
    with tempfile.TemporaryDirectory() as d:
        obs = _FakeObs()
        st = fg.FlagGraphicStore(os.path.join(d, "flag-graphic.json"), apply_fn=obs.apply,
                                 scenes=fg.flag_graphic_scenes(solo=True))
        st.set("green")
        assert {sc for (sc, _src, _en) in obs.calls} == {"Program"}, obs.calls
        assert ("Program", "Flag Green", True) in obs.calls, obs.calls


def t_intents_clear_hides_all():
    for active in ("", None, "bogus"):
        assert all(not en for (_sc, _src, en) in fg.flag_graphic_intents(active))


class _FakeObs:
    """Records (scene, source, enabled) calls; mimics set_scene_item_enabled."""
    def __init__(self, reachable=True):
        self.calls = []
        self.reachable = reachable
    def apply(self, scene, source, enabled):
        self.calls.append((scene, source, enabled))
        return (True, "") if self.reachable else (False, "obs unavailable")


def t_store_set_persists_and_applies_one_visible():
    with tempfile.TemporaryDirectory() as d:
        obs = _FakeObs()
        st = fg.FlagGraphicStore(os.path.join(d, "flag-graphic.json"), apply_fn=obs.apply)
        res = st.set("vsc")
        assert res == {"ok": True, "active": "virtual-safety-car"}, res
        assert st.get() == "virtual-safety-car"
        on = [(sc, src) for (sc, src, en) in obs.calls if en]
        assert on == [("Stint", "Flag Virtual Safety Car"),
                      ("Splitscreen", "Flag Virtual Safety Car")]
        # persisted
        with open(os.path.join(d, "flag-graphic.json")) as fh:
            assert json.load(fh) == {"active": "virtual-safety-car"}


def t_store_reload_from_file_and_reassert():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "flag-graphic.json")
        with open(path, "w") as fh:
            json.dump({"active": "red"}, fh)
        obs = _FakeObs()
        st = fg.FlagGraphicStore(path, apply_fn=obs.apply)
        assert st.get() == "red"               # loaded
        assert obs.calls == []                  # construction does NOT apply
        st.reassert()
        on = [(sc, src) for (sc, src, en) in obs.calls if en]
        assert on == [("Stint", "Flag Red"), ("Splitscreen", "Flag Red")]


def t_store_clear_hides_all_and_persists_empty():
    with tempfile.TemporaryDirectory() as d:
        obs = _FakeObs()
        st = fg.FlagGraphicStore(os.path.join(d, "flag-graphic.json"), apply_fn=obs.apply)
        st.set("green"); obs.calls.clear()
        assert st.clear() == {"ok": True, "active": ""}
        assert st.get() == ""
        assert all(not en for (_sc, _src, en) in obs.calls)


def t_store_unknown_value_is_error_no_change():
    with tempfile.TemporaryDirectory() as d:
        obs = _FakeObs()
        st = fg.FlagGraphicStore(os.path.join(d, "flag-graphic.json"), apply_fn=obs.apply)
        st.set("green"); obs.calls.clear()
        res = st.set("purple")
        assert "error" in res
        assert st.get() == "green"             # unchanged
        assert obs.calls == []                  # not applied


def t_store_corrupt_file_defaults_to_empty():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "flag-graphic.json")
        with open(path, "w") as fh:
            fh.write("{not json")
        st = fg.FlagGraphicStore(path, apply_fn=_FakeObs().apply)
        assert st.get() == ""


def t_obs_unreachable_is_ok_not_crash():
    with tempfile.TemporaryDirectory() as d:
        obs = _FakeObs(reachable=False)
        st = fg.FlagGraphicStore(os.path.join(d, "flag-graphic.json"), apply_fn=obs.apply)
        res = st.set("yellow")                  # apply_fn returns (False, note)
        assert res == {"ok": True, "active": "yellow"}, res   # state still set + persisted
        assert st.get() == "yellow"


def _graphics(d, real=(), placeholder=()):
    """A graphics dir with real PNGs for *real* and the placeholder for *placeholder*."""
    with open(placeholders.graphic_placeholder_path(), "rb") as fh:
        blank = fh.read()
    for key in real:
        with open(os.path.join(d, fg.FLAG_GRAPHIC_SOURCES[key] + ".png"), "wb") as fh:
            fh.write(b"\x89PNG real " + key.encode())
    for key in placeholder:
        with open(os.path.join(d, fg.FLAG_GRAPHIC_SOURCES[key] + ".png"), "wb") as fh:
            fh.write(blank)
    return d


def t_available_flags_are_the_linked_sheet_assets():
    with tempfile.TemporaryDirectory() as d:
        _graphics(d, real=("checkered", "green"), placeholder=("red",))
        with open(os.path.join(d, "manifest.json"), "w") as fh:
            json.dump({"internal": ["Flag Green"]}, fh)
        assert fg.available_flags(d) == ["green", "checkered"], \
            "only linked flags count, in catalog order, internal ones included"


def t_without_a_graphics_dir_every_flag_is_available():
    assert fg.available_flags(None) == list(fg.FLAG_GRAPHIC_SOURCES)
    assert fg.available_flags(os.path.join(tempfile.gettempdir(), "no-such-dir")) == []


def t_store_refuses_a_flag_the_sheet_does_not_link():
    with tempfile.TemporaryDirectory() as d:
        _graphics(d, real=("yellow",), placeholder=("red",))
        obs = _FakeObs()
        st = fg.FlagGraphicStore(os.path.join(d, "flag-graphic.json"), apply_fn=obs.apply,
                                 graphics_dir=d)
        assert st.available() == ["yellow"]
        res = st.set("red")
        assert "error" in res and st.get() == "" and obs.calls == [], res
        assert st.set("yellow") == {"ok": True, "active": "yellow"}
        assert st.clear() == {"ok": True, "active": ""}, "clear always works"
        assert st.data()["available"] == [{"key": "yellow", "source": "Flag Yellow"}]


cp = _load("console_policy", ("src", "scripts", "console_policy.py"))


def t_console_policy_gates_obs_flag_as_director():
    # The flag-graphic routes live under /obs, which console_policy maps to
    # DIRECTOR, so the Funnel /console/panel controls reach them and commentators
    # do not.
    for seg in (["obs", "flag", "data"], ["obs", "flag", "set", "green"],
                ["obs", "flag", "clear"]):
        assert cp.decide({"director"}, seg, "GET") == cp.ALLOW, seg
        assert cp.decide({"commentator"}, seg, "GET") == cp.FORBIDDEN, seg


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
