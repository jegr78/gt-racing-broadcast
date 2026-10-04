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


def _modes(mode, rc=None):
    return {"commentator": mode, "race_control": mode if rc is None else rc}


def _entry(defs, source):
    e = gt.find(defs, source)
    assert e is not None, f"{source} missing from the definitions"
    return e


def t_mode_defaults_to_off():
    for raw in (None, "", "OFF", "nonsense"):
        assert gt.normalize_mode(raw) == "off", f"{raw!r} must read as off"
    assert gt.normalize_mode(" Direct ") == "direct"
    assert gt.normalize_mode("Request") == "request"


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
    assert gt.may_take(flag, RC, _modes("direct"))
    assert not gt.may_take(flag, CREW, _modes("direct")), "a commentator must not set flags"
    standings = _entry(defs, "Standings")
    assert gt.may_take(standings, CREW, _modes("direct"))
    assert gt.may_take(standings, RC, _modes("direct"))


def t_off_mode_allows_nobody():
    defs = gt.definitions(solo=False)
    for source in ("Standings", "Flag Green"):
        assert not gt.may_take(_entry(defs, source), RC | CREW, _modes("off")), source


def t_director_role_alone_does_not_use_crew_takes():
    defs = gt.definitions(solo=False)
    assert not gt.may_take(_entry(defs, "Standings"), {"director"}, _modes("direct"))


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
                        (["cockpit", "graphic-takes"], "POST"),
                        (["cockpit", "graphic-takes", "request"], "POST")):
        for roles in (CREW, RC):
            assert cp.decide(roles, seg, method) == cp.ALLOW, (seg, roles)


def t_policy_keeps_obs_graphics_director_only():
    assert cp.decide({"director"}, ["obs", "graphics"], "GET") == cp.ALLOW
    assert cp.decide(CREW, ["obs", "graphics"], "GET") == cp.FORBIDDEN
    assert cp.decide(RC, ["obs", "graphics"], "GET") == cp.FORBIDDEN
    for seg in (["obs", "graphics", "requests"], ["obs", "graphics", "mode"],
                ["obs", "graphics", "request", "1"]):
        for roles in (CREW, RC):
            assert cp.decide(roles, seg, "POST") == cp.FORBIDDEN, (seg, roles)
        assert cp.decide({"director"}, seg, "POST") == cp.ALLOW, seg



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



def t_mode_is_the_most_permissive_over_the_callers_roles():
    defs = gt.definitions(solo=False)
    standings = _entry(defs, "Standings")
    assert gt.entry_mode(standings, CREW | RC, _modes("off", rc="request")) == "request"
    assert gt.entry_mode(standings, CREW | RC, _modes("direct", rc="request")) == "direct"
    flag = _entry(defs, "Flag Red")
    assert gt.entry_mode(flag, CREW | RC, _modes("direct", rc="off")) == "off", \
        "a commentator's direct mode must not open the flags"


def t_request_mode_offers_requests_not_takes():
    defs = gt.definitions(solo=False)
    standings = _entry(defs, "Standings")
    assert not gt.may_take(standings, CREW, _modes("request"))
    assert gt.may_request(standings, CREW, _modes("request"))
    assert not gt.may_request(standings, CREW, _modes("direct")), "direct mode takes, it does not ask"
    assert not gt.may_request(standings, CREW, _modes("off"))


def t_race_control_flags_go_direct_in_request_mode():
    defs = gt.definitions(solo=False)
    flag = _entry(defs, "Flag Yellow")
    assert gt.may_take(flag, RC, _modes("request"))
    assert not gt.may_request(flag, RC, _modes("request")), "a flag is never queued"


def t_crew_hides_its_own_take_in_request_mode():
    defs = gt.definitions(solo=False)
    standings = _entry(defs, "Standings")
    modes = _modes("request")
    assert gt.may_hide(standings, CREW, modes, crew_current="Standings")
    assert not gt.may_hide(standings, CREW, modes, crew_current=None), \
        "a graphic the director set stays with the director"
    assert not gt.may_hide(standings, CREW, _modes("off"), crew_current="Standings")
    assert gt.may_hide(standings, CREW, _modes("direct"), crew_current=None)


def t_take_modes_start_from_the_league_value():
    modes = gt.TakeModes("request")
    assert modes.snapshot() == {"commentator": "request", "race_control": "request"}
    assert modes.set("commentator", "direct")
    assert modes.snapshot() == {"commentator": "direct", "race_control": "request"}
    assert not modes.set("director", "direct"), "only crew roles have a mode"
    assert not modes.set("race_control", "sometimes"), "an unknown mode is refused"
    assert gt.TakeModes("request").snapshot()["commentator"] == "request", \
        "a new relay starts from the league value again"
    assert gt.TakeModes(None).snapshot() == {"commentator": "off", "race_control": "off"}


def t_requests_for_one_graphic_merge():
    q = gt.Requests()
    first, merged = q.add("Standings", "alice", "Alice", now=100)
    assert not merged
    again, merged = q.add("Standings", "dave", "RC 2", now=110)
    assert merged and again["id"] == first["id"], (first, again)
    assert again["by"] == ["Alice", "RC 2"], again["by"]
    other, merged = q.add("Schedule", "alice", "Alice", now=110)
    assert not merged and other["id"] != first["id"]
    assert [r["source"] for r in q.pending(now=111)] == ["Standings", "Schedule"]


def t_requests_expire_after_a_minute():
    q = gt.Requests()
    q.add("Standings", "alice", "Alice", now=100)
    assert q.pending(now=159), "still open before 60 s"
    assert q.pending(now=161) == [], "expired after 60 s"
    assert q.states_for("alice", now=161) == {"Standings": "expired"}
    assert q.states_for("alice", now=300) == {}, "an old outcome is forgotten"


def t_merged_requests_keep_their_first_deadline():
    q = gt.Requests()
    q.add("Standings", "alice", "Alice", now=100)
    q.add("Standings", "dave", "RC 2", now=150)
    assert q.pending(now=161) == [], "a second ask must not keep a stale request alive"


def t_resolving_a_request_reports_back_to_every_requester():
    q = gt.Requests()
    req, _ = q.add("Standings", "alice", "Alice", now=100)
    q.add("Standings", "dave", "RC 2", now=101)
    done = q.resolve(req["id"], "declined", now=102)
    assert done is not None and done["state"] == "declined"
    assert q.pending(now=103) == []
    for key in ("alice", "dave"):
        assert q.states_for(key, now=103) == {"Standings": "declined"}, key
    assert q.resolve(req["id"], "taken", now=104) is None, "a request resolves once"


def t_dropping_requests_clears_the_queue():
    q = gt.Requests()
    q.add("Standings", "alice", "Alice", now=100)
    q.add("Schedule", "dave", "RC 2", now=100)
    q.drop_pending()
    assert q.pending(now=101) == [] and q.states_for("alice", now=101) == {}


def t_request_chat_line():
    assert gt.request_line("Comms 1", "Standings") == "Comms 1 asked for Standings"
    assert gt.decline_line("Standings") == "Director declined Standings"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
