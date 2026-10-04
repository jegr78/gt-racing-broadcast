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
    intents, current = gt.take_intents(_entry(defs, "Standings"), True, "Schedule", defs)
    assert ("Stint", "Schedule", False) in intents, "the previous crew take must go"
    assert ("Stint", "Standings", True) in intents
    assert current == "Standings"


def t_take_without_a_previous_crew_take_hides_nothing():
    defs = gt.definitions(solo=False)
    intents, current = gt.take_intents(_entry(defs, "Standings"), True, None, defs)
    assert intents == [("Stint", "Standings", True)], intents
    assert current == "Standings"


def t_retaking_the_same_graphic_does_not_hide_it():
    defs = gt.definitions(solo=False)
    intents, _ = gt.take_intents(_entry(defs, "Standings"), True, "Standings", defs)
    assert intents == [("Stint", "Standings", True)], intents


def t_hide_clears_the_crew_take_only_for_that_graphic():
    defs = gt.definitions(solo=False)
    intents, current = gt.take_intents(_entry(defs, "Standings"), False, "Standings", defs)
    assert intents == [("Stint", "Standings", False)] and current is None
    intents, current = gt.take_intents(_entry(defs, "Schedule"), False, "Standings", defs)
    assert intents == [("Stint", "Schedule", False)] and current == "Standings"


def t_crew_takes_track_who_set_what():
    defs = gt.definitions(solo=False)
    st = gt.CrewTakes()
    st.apply(_entry(defs, "Schedule"), True, "Comms 1", defs)
    intents = st.apply(_entry(defs, "Standings"), True, "RC 2", defs)
    assert ("Stint", "Schedule", False) in intents
    assert st.snapshot() == ("Standings", {"Standings": "RC 2"}), st.snapshot()
    st.apply(_entry(defs, "Standings"), False, "Comms 1", defs)
    assert st.snapshot() == (None, {}), st.snapshot()


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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
