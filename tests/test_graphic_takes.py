#!/usr/bin/env python3
"""Stdlib unit checks for crew graphic takes. Run: python3 tests/test_graphic_takes.py"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


gt = _load("graphic_takes", ("src", "scripts", "graphic_takes.py"))
cp = _load("console_policy", ("src", "scripts", "console_policy.py"))

CREW = {"commentator"}
RC = {"race_control"}


def _entry(defs, source):
    e = gt.find(defs, source)
    assert e is not None, f"{source} missing from the definitions"
    return e


def t_mode_defaults_to_off():
    for raw in (None, "", "OFF", "nonsense", "request"):
        assert gt.normalize_mode(raw) == "off", f"{raw!r} must read as off"
    assert gt.normalize_mode(" Direct ") == "direct"


def t_endurance_definitions_use_the_collection_scenes():
    defs = gt.definitions(solo=False)
    assert _entry(defs, "Standings")["scenes"] == ["Stint"]
    assert _entry(defs, "Post Race Interviews")["scenes"] == ["Interview"]
    assert _entry(defs, "Flag Yellow")["scenes"] == ["Stint", "Splitscreen"]
    assert _entry(defs, "Flag Yellow")["flag"] == "yellow"


def t_solo_definitions_use_program():
    defs = gt.definitions(solo=True)
    assert _entry(defs, "Standings")["scenes"] == ["Program"]
    assert _entry(defs, "Flag Red")["scenes"] == ["Program"]


def t_director_only_sources_are_not_takeable():
    defs = gt.definitions(solo=False)
    for source in ("Standby Cover", "Stint HUD", "Split HUD", "Grid Row 1"):
        assert gt.find(defs, source) is None, f"{source} must stay director-only"


def t_flags_are_race_control_only():
    defs = gt.definitions(solo=False)
    flag = _entry(defs, "Flag Green")
    assert gt.may_take(flag, RC, "direct")
    assert not gt.may_take(flag, CREW, "direct"), "a commentator must not set flags"
    standings = _entry(defs, "Standings")
    assert gt.may_take(standings, CREW, "direct")
    assert gt.may_take(standings, RC, "direct")


def t_off_mode_allows_nobody():
    defs = gt.definitions(solo=False)
    for source in ("Standings", "Flag Green"):
        assert not gt.may_take(_entry(defs, source), RC | CREW, "off"), source


def t_director_role_alone_does_not_use_crew_takes():
    defs = gt.definitions(solo=False)
    assert not gt.may_take(_entry(defs, "Standings"), {"director"}, "direct")


def t_scene_gate():
    defs = gt.definitions(solo=False)
    assert gt.scene_ok(_entry(defs, "Standings"), "Stint")
    assert not gt.scene_ok(_entry(defs, "Standings"), "Splitscreen")
    assert gt.scene_ok(_entry(defs, "Flag Red"), "Splitscreen")
    assert not gt.scene_ok(_entry(defs, "Standings"), None)


def t_take_replaces_the_previous_crew_take():
    defs = gt.definitions(solo=False)
    intents, current = gt.take_intents(_entry(defs, "Standings"), True, "Schedule", defs,
                                       {"Schedule": True})
    assert ("Stint", "Schedule", False) in intents, "the previous crew take must go"
    assert ("Stint", "Standings", True) in intents
    assert current == "Standings"


def t_a_crew_take_the_director_already_hid_is_left_alone():
    # Hidden since by the director: if he shows it again it is his, not the crew's.
    defs = gt.definitions(solo=False)
    intents, _ = gt.take_intents(_entry(defs, "Standings"), True, "Schedule", defs,
                                 {"Schedule": False})
    assert intents == [("Stint", "Standings", True)], intents


def t_take_without_a_previous_crew_take_hides_nothing():
    defs = gt.definitions(solo=False)
    intents, current = gt.take_intents(_entry(defs, "Standings"), True, None, defs, {})
    assert intents == [("Stint", "Standings", True)], intents
    assert current == "Standings"


def t_retaking_a_visible_graphic_changes_nothing():
    defs = gt.definitions(solo=False)
    intents, _ = gt.take_intents(_entry(defs, "Standings"), True, "Standings", defs,
                                 {"Standings": True})
    assert intents == [], intents


def t_hide_clears_the_crew_take_only_for_that_graphic():
    defs = gt.definitions(solo=False)
    intents, current = gt.take_intents(_entry(defs, "Standings"), False, "Standings", defs,
                                       {"Standings": True})
    assert intents == [("Stint", "Standings", False)] and current is None
    intents, current = gt.take_intents(_entry(defs, "Schedule"), False, "Standings", defs,
                                       {"Schedule": True})
    assert intents == [("Stint", "Schedule", False)] and current == "Standings"


def t_hiding_a_hidden_graphic_changes_nothing():
    defs = gt.definitions(solo=False)
    intents, _ = gt.take_intents(_entry(defs, "Standings"), False, None, defs,
                                 {"Standings": False})
    assert intents == [], intents


def _ok(scene, source, enabled):
    return True, ""


def t_crew_takes_track_who_set_what():
    defs = gt.definitions(solo=False)
    st = gt.CrewTakes()
    st.apply(_entry(defs, "Schedule"), True, "Comms 1", defs, {}, _ok)
    intents, failed = st.apply(_entry(defs, "Standings"), True, "RC 2", defs,
                               {"Schedule": True}, _ok)
    assert ("Stint", "Schedule", False) in intents and failed == []
    assert st.snapshot() == ("Standings", {"Standings": "RC 2"}), st.snapshot()
    st.apply(_entry(defs, "Standings"), False, "Comms 1", defs, {"Standings": True}, _ok)
    assert st.snapshot() == (None, {}), st.snapshot()


def t_crew_takes_keep_their_state_when_obs_fails():
    defs = gt.definitions(solo=False)
    st = gt.CrewTakes()
    _intents, failed = st.apply(_entry(defs, "Standings"), True, "Comms 1", defs, {},
                                lambda sc, src, on: (False, "boom"))
    assert failed == ["Standings in Stint: boom"], failed
    assert st.snapshot() == (None, {}), "a failed take must not count as on air"


def t_chat_line_names_the_person_and_the_graphic():
    assert gt.chat_line("RC 2", "Flag Yellow", True) == "RC 2 put Flag Yellow on air"
    assert gt.chat_line("Comms 1", "Standings", False) == "Comms 1 took Standings off air"


def t_policy_lets_any_console_subject_reach_the_take_routes():
    # The handler checks role and mode; the gate only needs an identity.
    for seg, method in ((["cockpit", "graphic-takes"], "GET"),
                        (["cockpit", "graphic-takes"], "POST")):
        for roles in (CREW, RC):
            assert cp.decide(roles, seg, method) == cp.ALLOW, (seg, roles)


def t_policy_keeps_obs_graphics_director_only():
    assert cp.decide({"director"}, ["obs", "graphics"], "GET") == cp.ALLOW
    assert cp.decide(CREW, ["obs", "graphics"], "GET") == cp.FORBIDDEN
    assert cp.decide(RC, ["obs", "graphics"], "GET") == cp.FORBIDDEN



def _collection_items(filename):
    import json
    with open(os.path.join(ROOT, "src", "obs", filename), encoding="utf-8") as fh:
        d = json.load(fh)
    return {sc["name"]: {i["name"] for i in sc["settings"].get("items", [])}
            for sc in d["sources"] if sc.get("id") == "scene"}


def t_panel_catalog_targets_exist_in_their_collections():
    for solo, files in ((False, ["GT_Racing_Endurance.json"]),
                        (True, ["GT_Racing_Solo_POV.json", "GT_Racing_Solo_Commentary.json"])):
        cat = gt.panel_catalog(solo)
        for filename in files:
            scenes = _collection_items(filename)
            for bus in ("graphics", "graphicsPreRace", "graphicsGrid"):
                for item in cat[bus]:
                    assert item["source"] in scenes.get(item["scene"], set()), \
                        f"{filename}: {item['scene']!r} has no {item['source']!r}"


def t_panel_catalog_keeps_the_panel_layout():
    cat = gt.panel_catalog(False)
    assert [i["label"] for i in cat["graphics"]] == [
        "HUD", "HUD", "STANDINGS", "SCHEDULE", "RACE RESULTS", "QUALI RESULTS",
        "RACE WX 1", "RACE WX 2", "QUALI WX", "POST-RACE"], cat["graphics"]
    assert cat["graphics"][1] == {"label": "HUD", "scene": "Splitscreen", "source": "Split HUD"}
    assert [i["label"] for i in cat["graphicsPreRace"]] == ["WEEKEND", "RACE INFO", "NEXT EVENT"]
    grid = cat["graphicsGrid"]
    assert grid[0] == {"label": "STARTING GRID", "scene": "Stint", "source": "Starting Grid",
                       "top": True}, grid[0]
    assert [i["label"] for i in grid[1:]] == [f"GRID R{n}" for n in range(1, 9)]
    solo = gt.panel_catalog(True)
    assert [i["source"] for i in solo["graphics"]][:1] == ["Stint HUD"]
    assert "Split HUD" not in [i["source"] for i in solo["graphics"]], "solo has no Splitscreen"
    assert {i["scene"] for i in solo["graphicsGrid"]} == {"Program"}


def t_panel_catalog_covers_every_companion_graphic():
    # The Companion route (#706) and the panel must offer the same full-screen stills.
    sources = {i["source"] for bus in gt.panel_catalog(False).values() for i in bus}
    missing = set(gt.obs_ws.GRAPHIC_SOURCES) - sources - {"Standby Cover"}
    assert not missing, f"panel lacks {sorted(missing)}"


def t_crew_definitions_are_a_subset_of_the_panel_catalog():
    sources = {i["source"] for bus in gt.panel_catalog(False).values() for i in bus}
    crew = {d["source"] for d in gt.definitions(False) if d["group"] == "editorial"}
    assert crew <= sources, sorted(crew - sources)

if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
