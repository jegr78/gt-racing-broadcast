#!/usr/bin/env python3
"""Stdlib unit checks for the /console authorization policy (#216 phase 2).
Run: python3 tests/test_console.py"""
import importlib.util, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


cp = _load("console_policy", os.path.join("src", "scripts", "console_policy.py"))


def _cap(segs, method="GET"):
    r = cp.min_capability(segs, method)
    return None if r is None else (r.capability, r.step_up)


def t_driver_requests_are_desk_routes_only():
    for segs in (["drivers", "requests"], ["race-control", "drivers", "requests"]):
        assert _cap(segs) is not None, segs
    assert _cap(["drivers", "requests"]) == (cp.DIRECTOR, False)
    assert _cap(["race-control", "drivers", "requests"]) == (cp.RACE_CONTROL, False)
    for act in ("approve", "correct", "reject"):
        assert _cap(["drivers", "requests", act], "POST") == (cp.DIRECTOR, False), act
        assert _cap(["race-control", "drivers", "requests", act], "POST") == (cp.RACE_CONTROL, False), act
        assert _cap(["drivers", "requests", act]) is None, "GET never decides"
    for segs, cap in ((["drivers", "requests", "reject-pending"], cp.DIRECTOR),
                      (["race-control", "drivers", "requests", "reject-pending"], cp.RACE_CONTROL)):
        assert _cap(segs, "POST") == (cap, False), segs
    assert _cap(["drivers", "requests", "delete"], "POST") is None


def t_any_authenticated_reads():
    for segs in ([], ["status"], ["console"], ["data"], ["program"],
                 ["hud"], ["hud", "data"], ["hud", "override.css"],
                 ["hud", "preview"], ["hud", "assets", "flags", "de.png"],
                 ["preview", "program"], ["preview", "feed", "A"],
                 ["timer", "data"], ["setup", "data"],
                 ["chat", "data"], ["chat", "reload"],
                 ["cockpit"], ["cockpit", "data"], ["cockpit", "program"],
                 ["cockpit", "timer"], ["cockpit", "chat", "data"],
                 ["cockpit", "graphics"], ["cockpit", "graphics", "Standings.png"]):
        assert _cap(segs) == ("any", False), segs


def t_chat_send_is_any_authenticated():
    assert _cap(["chat", "send"], "POST") == ("any", False)


def t_submit_is_commentator():
    assert _cap(["submit"], "POST") == ("commentator", False)
    assert _cap(["cockpit", "submit"], "POST") == ("commentator", False)


def t_director_feed_and_schedule_control():
    for segs in (["next"], ["prev", "A"], ["reload"], ["reload", "A"],
                 ["set", "A", "3"], ["set", "B", "12"]):
        assert _cap(segs) == ("director", False), segs


def t_director_panel_setup_timer_pov_submissions():
    for segs in (["panel"], ["pov", "reload"], ["pov", "set"],
                 ["setup", "set", "stint", "Alice"],
                 ["timer", "start"], ["timer", "stop"], ["timer", "set", "1:00:00"],
                 ["schedule", "set"], ["qualifying", "set"], ["event", "title"],
                 ["submissions"], ["submissions", "approve"], ["submissions", "reject"],
                 ["telemetry", "show"], ["telemetry", "hide"], ["telemetry", "toggle"],
                 ["telemetry", "record", "start"], ["telemetry", "record", "stop"],
                 ["telemetry", "record", "toggle"]):
        assert _cap(segs) == ("director", False), segs


def t_sync_now_routes_are_director_only():
    # SYNC NOW re-pushes a panel save that is held in the relay (spec 2026-10-06).
    for segs in (["schedule", "sync"], ["qualifying", "sync"], ["pov", "sync"]):
        assert _cap(segs) == ("director", False), segs


def t_setup_data_and_timer_data_are_reads_not_director():
    # The read endpoints under setup/timer must stay "any", not escalate to director.
    assert _cap(["setup", "data"]) == ("any", False)
    assert _cap(["timer", "data"]) == ("any", False)


def t_schedule_and_qualifying_data_are_director_reads():
    # These return per-stint stream URLs, so over the Funnel they must not be
    # any-auth, or a commentator could read every feed's stream URL. Director-only,
    # matching their sole consumer, the director panel.
    assert _cap(["schedule", "data"]) == ("director", False)
    assert _cap(["qualifying", "data"]) == ("director", False)


def t_producer_stepup_irreversible_ops():
    for segs in (["set", "stint", "4"],
                 ["takeover", "status"], ["takeover", "chat"], ["takeover", "versions"],
                 ["cockpit", "versions"]):
        assert _cap(segs) == ("producer", True), segs


def t_mode_switch_is_director_no_stepup():
    # Switching race and qualifying is a Director-Panel control that runs auth-free on
    # the tailnet, so over the Funnel it is director tier, not producer plus step-up:
    # the panel's plain relayCall carries no X-Console-Secret and would get a 403.
    assert _cap(["mode", "race"]) == ("director", False)
    assert _cap(["mode", "qualifying"]) == ("director", False)
    assert cp.decide({"director"}, ["mode", "qualifying"]) == cp.ALLOW
    assert cp.decide({"director"}, ["mode", "race"], has_step_up=False) == cp.ALLOW
    assert cp.decide({"commentator"}, ["mode", "race"]) == cp.FORBIDDEN


def t_prod_page_is_producer_view_no_stepup():
    assert _cap(["prod"]) == ("producer", False)


def t_set_stint_not_confused_with_set_feed():
    # set/stint/<n> is producer+stepup; set/<feed>/<n> is director. Ordering matters.
    assert _cap(["set", "stint", "4"]) == ("producer", True)
    assert _cap(["set", "A", "4"]) == ("director", False)


def t_unknown_route_is_none():
    for segs in (["bogus"], ["timer"], ["set"], ["set", "A"],
                 ["cockpit", "nope"], ["mode"]):
        assert cp.min_capability(segs) is None, segs


def t_next_with_feed_is_director():
    assert _cap(["next", "A"]) == ("director", False)


def t_decide_any_allows_even_empty_roles():
    # A valid identity with no roles can still reach read-only monitors.
    assert cp.decide(set(), ["status"]) == cp.ALLOW
    assert cp.decide(set(), ["cockpit", "data"]) == cp.ALLOW


def t_decide_unknown_route_is_not_found():
    assert cp.decide({"director"}, ["bogus"]) == cp.NOT_FOUND


def t_decide_commentator_blocked_from_director_op():
    assert cp.decide({"commentator"}, ["next"]) == cp.FORBIDDEN


def t_decide_director_allowed_director_op():
    assert cp.decide({"director"}, ["next"]) == cp.ALLOW
    assert cp.decide({"commentator", "director"}, ["set", "A", "3"]) == cp.ALLOW


def t_decide_commentator_allowed_submit():
    assert cp.decide({"commentator"}, ["cockpit", "submit"], "POST") == cp.ALLOW
    assert cp.decide({"director"}, ["cockpit", "submit"], "POST") == cp.FORBIDDEN


def t_decide_producer_stepup_enforced():
    # Producer without the second factor is told to step up, not allowed.
    assert cp.decide({"producer"}, ["set", "stint", "4"]) == cp.STEP_UP_REQUIRED
    assert cp.decide({"producer"}, ["set", "stint", "4"], has_step_up=True) == cp.ALLOW


def t_decide_stepup_route_still_requires_the_role_first():
    # A director who is not a producer hitting a producer op is forbidden, step-up or not.
    assert cp.decide({"director"}, ["set", "stint", "4"]) == cp.FORBIDDEN
    assert cp.decide({"director"}, ["set", "stint", "4"], has_step_up=True) == cp.FORBIDDEN


def t_decide_prod_page_needs_producer_no_stepup():
    assert cp.decide({"producer"}, ["prod"]) == cp.ALLOW
    assert cp.decide({"director"}, ["prod"]) == cp.FORBIDDEN


def t_splitscreen_and_overlay_fonts_are_any_reads():
    for segs in (["splitscreen"], ["splitscreen", "data"],
                 ["splitscreen", "override.css"],
                 ["overlay", "fonts", "Inter.woff2"]):
        assert _cap(segs) == ("any", False), segs


def t_cockpit_chat_send_is_any_read():
    # cockpit.html POSTs /cockpit/chat/send; under /console any authenticated subject
    # may reach it, because the cockpit handler forces the identity server-side.
    assert _cap(["cockpit", "chat", "send"], "POST") == ("any", False)


def t_program_audio_endpoints_are_any():
    # Cockpit and Race Control desk stream, funnelled under /console/cockpit/...
    assert cp.min_capability(["cockpit", "program-audio"]) == cp.Requirement(cp.ANY, False)
    # Director Panel stream: tailnet /preview/... and /console/preview/... via the gate
    assert cp.min_capability(["preview", "program-audio"]) == cp.Requirement(cp.ANY, False)


def t_solo_input_previews_are_director_only():
    # A solo webcam or capture can be off program, so only the panel may see it (#730).
    for key in ("capture", "webcam", "tyres"):
        assert cp.min_capability(["preview", "source", key]) == cp.Requirement(cp.DIRECTOR, False), key
    assert cp.min_capability(["preview", "feed", "A"]) == cp.Requirement(cp.ANY, False)


def t_root_graphics_browser_is_any_authenticated():
    # The tailnet-open /graphics list and file endpoints are also reachable via
    # /console/graphics for any authenticated subject, so the console pages' graphics
    # widget works on the tailnet /panel and under the /console mount alike.
    assert _cap(["graphics"]) == ("any", False)
    assert _cap(["graphics", "Standings.png"]) == ("any", False)
    assert cp.decide(set(), ["graphics"]) == cp.ALLOW           # even with no roles
    assert cp.decide(set(), ["graphics", "Standings.png"]) == cp.ALLOW


def t_obs_routes_require_director():
    for seg in (["obs", "scene"], ["obs", "source"], ["obs", "audio"],
                ["obs", "state"], ["obs", "stream"], ["obs", "rebuild-rearm"]):
        assert cp.min_capability(seg) == cp.Requirement(cp.DIRECTOR, False), seg


def t_buttons_requires_director_no_stepup():
    assert cp.min_capability(["buttons"]) == cp.Requirement(cp.DIRECTOR, False)
    assert cp.min_capability(["buttons", "tablet"]) == cp.Requirement(cp.DIRECTOR, False)
    assert cp.min_capability(["buttons", "trpc"]) == cp.Requirement(cp.DIRECTOR, False)
    assert cp.min_capability(["buttons", "health"]) == cp.Requirement(cp.DIRECTOR, False)


def t_buttons_commentator_forbidden_director_allowed():
    assert cp.decide({cp.COMMENTATOR}, ["buttons", "tablet"]) == cp.FORBIDDEN
    assert cp.decide({cp.DIRECTOR}, ["buttons", "tablet"]) == cp.ALLOW


def t_logo_is_any_authenticated():
    assert cp.min_capability(["logo"]) == cp.Requirement(cp.ANY, False)


def t_race_control_page_and_data_require_race_control():
    # The monitoring desk page and its one data endpoint are gated on the race_control
    # capability with no step-up, like the cockpit and panel pages. (#244)
    assert cp.min_capability(["race-control"]) == cp.Requirement(cp.RACE_CONTROL, False)
    assert cp.min_capability(["race-control", "data"]) == cp.Requirement(cp.RACE_CONTROL, False)


def t_decide_race_control_allowed_commentator_forbidden():
    assert cp.decide({cp.RACE_CONTROL}, ["race-control"]) == cp.ALLOW
    assert cp.decide({cp.RACE_CONTROL}, ["race-control", "data"]) == cp.ALLOW
    # A plain commentator, or any other role, may not reach the desk.
    assert cp.decide({cp.COMMENTATOR}, ["race-control"]) == cp.FORBIDDEN
    assert cp.decide({cp.DIRECTOR}, ["race-control", "data"]) == cp.FORBIDDEN
    # Additive roles: holding race_control alongside another role still allows it.
    assert cp.decide({cp.DIRECTOR, cp.RACE_CONTROL}, ["race-control"]) == cp.ALLOW


def t_race_control_cues_and_presets_require_race_control():
    # RC to commentator notes: the send and preset endpoints sit under the race-control
    # desk, gated on the race_control capability with no step-up. (#376)
    for seg in (["race-control", "cues"], ["race-control", "presets"]):
        assert cp.min_capability(seg) == cp.Requirement(cp.RACE_CONTROL, False), seg
        assert cp.decide({cp.RACE_CONTROL}, seg, "POST", False) == cp.ALLOW, seg
        assert cp.decide({cp.COMMENTATOR}, seg, "POST", False) == cp.FORBIDDEN, seg
        assert cp.decide({cp.DIRECTOR}, seg, "GET", False) == cp.FORBIDDEN, seg


def t_cue_back_send_any_auth_read_director():
    # Commentator to director cue-back: the commentator send is any-auth and
    # identity-scoped, and the director read sits under /cues, so it stays
    # director-gated. (#377)
    assert cp.min_capability(["cockpit", "cue-back"]) == cp.Requirement(cp.ANY, False)
    assert cp.decide({cp.COMMENTATOR}, ["cockpit", "cue-back"], "POST", False) == cp.ALLOW
    assert cp.decide(set(), ["cockpit", "cue-back"], "POST", False) == cp.ALLOW
    assert cp.min_capability(["cues", "back"]) == cp.Requirement(cp.DIRECTOR, False)
    assert cp.decide({cp.DIRECTOR}, ["cues", "back"], "GET", False) == cp.ALLOW
    assert cp.decide({cp.COMMENTATOR}, ["cues", "back"], "GET", False) == cp.FORBIDDEN


def t_policy_cockpit_rc_notes_any_auth():
    # A commentator reads their RC notes via the identity-scoped cockpit endpoint, and
    # any authenticated subject may reach it because the read is target-scoped.
    seg = ["cockpit", "rc-notes"]
    assert cp.min_capability(seg) == cp.Requirement(cp.ANY, False)
    assert cp.decide({cp.COMMENTATOR}, seg, "GET", False) == cp.ALLOW
    assert cp.decide(set(), seg, "GET", False) == cp.ALLOW


def t_policy_cues_director_only():
    # A director may send and read cues; a bare commentator may not.
    for seg in (["cues", "send"], ["cues", "data"], ["cues", "presets"], ["cues", "reload"]):
        assert cp.decide({"director"}, seg, "POST", False) == cp.ALLOW, seg
        assert cp.decide({"commentator"}, seg, "POST", False) == cp.FORBIDDEN, seg


def t_policy_cockpit_cues_any_auth():
    # Any authenticated subject may read their cues and ack one.
    for seg in (["cockpit", "cues"], ["cockpit", "cues", "ack"]):
        assert cp.decide({"commentator"}, seg, "POST", False) == cp.ALLOW, seg
        assert cp.decide(set(), seg, "GET", False) == cp.ALLOW, seg


def t_policy_takeover_cues_producer_stepup():
    seg = ["takeover", "cues"]
    assert cp.decide({"producer"}, seg, "GET", True) == cp.ALLOW
    assert cp.decide({"producer"}, seg, "GET", False) == cp.STEP_UP_REQUIRED
    assert cp.decide({"director"}, seg, "GET", True) == cp.FORBIDDEN


def t_health_monitor_page_and_data_are_any_authenticated():
    assert cp.min_capability(["health-monitor"]) == cp.Requirement(cp.ANY, False)
    assert cp.min_capability(["health-monitor", "data"]) == cp.Requirement(cp.ANY, False)
    assert cp.min_capability(["health-monitor", "assets", "uPlot.min.css"]) == cp.Requirement(cp.ANY, False)


def t_decide_health_monitor_allows_any_role():
    for role in (cp.COMMENTATOR, cp.DIRECTOR, cp.PRODUCER, cp.RACE_CONTROL):
        assert cp.decide({role}, ["health-monitor"]) == cp.ALLOW
        assert cp.decide({role}, ["health-monitor", "data"]) == cp.ALLOW
    assert cp.decide(set(), ["health-monitor"]) == cp.ALLOW, \
        "an authenticated subject with no resolved role still reaches an any-auth route"


def t_takeover_health_is_producer_step_up():
    assert cp.min_capability(["takeover", "health"]) == cp.Requirement(cp.PRODUCER, True)
    assert cp.decide({cp.PRODUCER}, ["takeover", "health"], has_step_up=False) == cp.STEP_UP_REQUIRED
    assert cp.decide({cp.PRODUCER}, ["takeover", "health"], has_step_up=True) == cp.ALLOW


def t_substitution_is_director_gated():
    assert cp.min_capability(["substitution", "latest"]) == cp.Requirement(cp.DIRECTOR, False)
    assert cp.min_capability(["substitution", "note"], "POST") == cp.Requirement(cp.DIRECTOR, False)


def t_event_notes_any_authenticated():
    # Any authenticated subject, even a role-less one, may read the notes
    assert cp.decide(set(), ["event-notes", "data"], "GET") == cp.ALLOW
    assert cp.decide({"commentator"}, ["event-notes", "data"], "GET") == cp.ALLOW
    # And it is not a recognized POST or write route.
    assert cp.decide({"director"}, ["event-notes", "send"], "GET") == cp.NOT_FOUND


def t_parts_requires_director():
    assert cp.min_capability(["parts", "data"]) == cp.Requirement(cp.DIRECTOR, False)
    assert cp.min_capability(["parts", "start"]) == cp.Requirement(cp.DIRECTOR, False)
    assert cp.min_capability(["parts", "end"]) == cp.Requirement(cp.DIRECTOR, False)


def t_resync_stint_is_director_no_stepup():
    req = cp.min_capability(["resync", "stint", "4"], "GET")
    assert req == cp.Requirement(cp.DIRECTOR, False)


def t_feed_arm_is_director_no_stepup():
    for act in ("activate", "deactivate"):
        assert cp.min_capability(["feed", "A", act]) == cp.Requirement(cp.DIRECTOR, False), act
        assert cp.min_capability(["feed", "B", act]) == cp.Requirement(cp.DIRECTOR, False), act


def t_feed_quality_is_director_no_stepup():
    # Manual quality-profile control mirrors feed arm and disarm: director tier, no
    # step-up, so a plain director token can pin a feed's quality. (#493)
    assert cp.min_capability(["feed", "A", "quality"], "POST") == cp.Requirement(cp.DIRECTOR, False)
    assert cp.min_capability(["feed", "B", "quality"], "POST") == cp.Requirement(cp.DIRECTOR, False)



def t_feed_quality_get_form_not_funnelled():
    # The GET path form /feed/<A|B>/quality/<tier> is a Companion loopback route and
    # deliberately not a /console route, so min_capability returns None and it is not
    # reachable over the Funnel; there feed control stays the director-gated POST. (#493)
    assert cp.min_capability(["feed", "A", "quality", "robust"], "GET") is None
    assert cp.min_capability(["feed", "B", "quality", "auto"], "GET") is None

def t_driver_telemetry_controls_for_directors_and_race_control():
    for path, method in ((["drivers", "race"], "GET"), (["drivers", "race", "red-flag"], "POST"),
                         (["drivers", "readiness"], "GET"), (["drivers", "reassign"], "POST")):
        assert cp.decide({"director"}, path, method) == cp.ALLOW, path
        assert cp.decide({"race_control"}, path, method) == cp.FORBIDDEN, path
        assert cp.decide({"race_control"}, ["race-control"] + path, method) == cp.ALLOW, path
        assert cp.decide({"commentator"}, ["race-control"] + path, method) == cp.FORBIDDEN, path
    for tailnet_only, method in ((["drivers", "data"], "GET"), (["drivers", "race", "reset"], "POST"),
                                 (["drivers", "race"], "POST"), (["drivers", "readiness"], "POST")):
        assert cp.decide({"producer", "director"}, tailnet_only, method) == cp.NOT_FOUND, tailnet_only
        assert cp.decide({"producer", "race_control"}, ["race-control"] + tailnet_only, method) \
            == cp.NOT_FOUND, tailnet_only
    assert cp.decide({"race_control"}, ["race-control", "red-flag"], "POST") == cp.ALLOW
    assert cp.decide({"director"}, ["race-control", "red-flag"], "POST") == cp.FORBIDDEN


def t_class_lap_counts_are_a_desk_route():
    path = ["drivers", "race", "class-laps"]
    assert cp.decide({"director"}, path, "POST") == cp.ALLOW
    assert cp.decide({"race_control"}, ["race-control"] + path, "POST") == cp.ALLOW
    assert cp.decide({"commentator"}, path, "POST") == cp.FORBIDDEN
    assert cp.decide({"commentator"}, ["race-control"] + path, "POST") == cp.FORBIDDEN


def t_recording_routes_are_desk_routes():
    for path, method in ((["drivers", "race", "recording"], "GET"),
                         (["drivers", "race", "recording", "at"], "GET"),
                         (["drivers", "race", "recording", "csv"], "GET"),
                         (["drivers", "race", "recording"], "POST"),
                         (["drivers", "race", "recording", "event"], "POST"),
                         (["drivers", "snapshot"], "GET")):
        assert cp.decide({"director"}, path, method) == cp.ALLOW, path
        assert cp.decide({"race_control"}, ["race-control"] + path, method) == cp.ALLOW, path
        assert cp.decide({"commentator"}, path, method) == cp.FORBIDDEN, path
        assert cp.decide({"commentator"}, ["race-control"] + path, method) == cp.FORBIDDEN, path
    assert cp.decide({"director"}, ["drivers", "race", "recording", "event"], "GET") == cp.NOT_FOUND
    assert cp.decide({"director"}, ["drivers", "race", "recording", "at"], "POST") == cp.NOT_FOUND
    assert cp.decide({"director"}, ["drivers", "snapshot"], "POST") == cp.NOT_FOUND


def _desk_fn(name, call):
    """Runs one top-level function of driver-desk.js under node; None without node."""
    import sys
    sys.path.insert(0, HERE)
    import test_driver_tower as tower
    desk = os.path.join(ROOT, "src", "console", "driver-desk.js")
    src = "\n".join(tower._function(desk, n) for n in name.split(","))
    return tower._run(src + "\n;console.log(JSON.stringify(" + call + "));")


def t_the_desk_lists_waiting_requests_first_and_hides_rejected_ones():
    reqs = [{"conn": "a", "status": "approved", "ts": 1}, {"conn": "b", "status": "rejected", "ts": 2},
            {"conn": "c", "status": "pending", "ts": 4}, {"conn": "d", "status": "pending", "ts": 3}]
    got = _desk_fn("requestRows", "requestRows(" + json.dumps(reqs) + ").map(function (r) { return r.conn; })")
    if got is None:
        return
    assert got == ["d", "c", "a"], got


def t_the_desk_marks_requests_with_the_same_name_entry_and_stint():
    reqs = [{"conn": "a", "name": "Max", "entry": "A", "segment": "S1", "status": "pending"},
            {"conn": "b", "name": "Max", "entry": "A", "segment": "S1", "status": "pending"},
            {"conn": "c", "name": "Max", "entry": "A", "segment": "S2", "status": "approved"},
            {"conn": "d", "name": "Ann", "entry": "A", "segment": "S2", "status": "pending"},
            {"conn": "e", "name": "Ann", "entry": "A", "segment": "S2", "status": "rejected"}]
    got = _desk_fn("duplicates", "Object.keys(duplicates(" + json.dumps(reqs) + ")).sort()")
    if got is None:
        return
    assert got == ["a", "b"], got


def t_the_desk_marks_pending_and_approved_senders_apart():
    senders = [{"name": "Max", "live": True, "connected": True, "approval": "approved"},
               {"name": "Ann", "live": False, "connected": False, "approval": "pending"}]
    got = _desk_fn("fitName,readyPills", "[readyPills(" + json.dumps(senders) + "), readyPills(" +
                   json.dumps(senders[1:]) + "), readyPills([{name: 'Lea', live: false, connected: false, "
                   "approval: 'approved'}]), readyPills([{name: 'Ole', live: false, connected: true, "
                   "approval: 'approved'}]), readyPills([])]")
    if got is None:
        return
    assert got == [[["Max", "ok"], ["Ann: pending", "amb"]], [["Ann: pending", "amb"]],
                   [["approved, no data", "amb"]], [["quiet", "amb"]], [["no sender", "bad"]]], got
    got = _desk_fn("senderState", "[" + ",".join("senderState(" + json.dumps(x) + ")" for x in (
        {"live": True, "connected": True, "approval": "approved"},
        {"live": False, "connected": True, "approval": "approved"},
        {"live": False, "connected": False, "approval": "pending"},
        {"live": False, "connected": False, "approval": "approved"})) + "]")
    assert got == [["live", "ok"], ["quiet", "bad"], ["pending", "amb"], ["approved, no data", "amb"]], got


def t_the_desk_sends_the_corrected_request():
    rec = {"conn": "c1", "entry": "Team A #1", "segment": "Stint 1", "name": "Max"}
    got = _desk_fn("requestBody", "[requestBody(" + json.dumps(rec) + ", {name: 'Max P', entry: 'Team B #2'}, true),"
                   " requestBody(" + json.dumps(rec) + ", null, false)]")
    if got is None:
        return
    assert got == [{"conn": "c1", "entry": "Team B #2", "segment": "Stint 1", "name": "Max P"},
                   {"conn": "c1", "entry": "Team A #1", "name": "Max"}], got
    with open(os.path.join(ROOT, "src", "console", "driver-desk.js"), encoding="utf-8") as fh:
        desk = fh.read()
    for route in ('"/drivers/requests/approve"', '"/drivers/requests/correct"', '"/drivers/requests/reject"',
                  '"/drivers/requests/reject-pending"',
                  '"/drivers/requests"'):
        assert route in desk, route
    for call in ('decide(pend ? "/drivers/requests/approve"', 'decide("/drivers/requests/reject"',
                 'decide("/drivers/requests/reject-pending"'):
        assert call in desk, f"a decision drops the focus so its row re-renders: {call}"
    assert "document.activeElement.blur()" in desk


def t_the_desk_offers_a_lap_count_per_slower_class():
    with open(os.path.join(ROOT, "src", "console", "driver-desk.js"), encoding="utf-8") as fh:
        desk = fh.read()
    assert '"/drivers/race/class-laps", {class: ' in desk, "the inputs post to the class lap route"
    got = _desk_fn("lapClasses", '[lapClasses(["GT3", "GT4", "GT2", ""]), lapClasses(["", "GT3", "GT4"]), lapClasses([])]')
    if got is None:
        return
    assert got == [["GT4", "GT2"], ["GT4"], []], \
        f"one input per named class after the first, which runs on the Schedule laps, none for the unclassed: {got}"


def t_the_desk_estimates_a_quiet_car_behind_or_ahead_of_its_reference():
    with open(os.path.join(ROOT, "src", "console", "driver-desk.js"), encoding="utf-8") as fh:
        desk = fh.read()
    assert 'opt("behind", "behind"' in desk and 'opt("ahead", "ahead of"' in desk, \
        "the estimate row offers behind (the default) and ahead of"
    assert '"/drivers/race/estimate", estimateBody(' in desk, "the row posts the direction it shows"
    got = _desk_fn("estimateBody,estimateText",
                   '[estimateBody("A", "B", 1.5, "behind"), estimateBody("A", "B", 2, "ahead"), '
                   'estimateText({ref: "B", seconds: 1.5, ahead: false}), '
                   'estimateText({ref: "B", seconds: 2, ahead: true})]')
    if got is None:
        return
    assert got == [{"entry": "A", "ref": "B", "seconds": 1.5, "ahead": False},
                   {"entry": "A", "ref": "B", "seconds": 2, "ahead": True},
                   "1.5 s behind B", "2 s ahead of B"], \
        f"the route gets an explicit direction and the desk names it: {got}"


DESK_RACE = {"entries": [{"entry": "A", "class": "GT3", "class_pos": 1}, {"entry": "B", "class": "GT4", "class_pos": 1},
                         {"entry": "C", "class": "GT3", "class_pos": 2}, {"entry": "D", "class": "", "class_pos": 1}],
             "classes": ["GT3", "GT4", ""], "class_lap": {"GT3": 7, "GT4": 6, "": 6},
             "class_laps": {"GT4": 18}, "lap": {"leader": 7, "count": 20},
             "class_colors": {"GT3": "#e10600", "GT4": "red;background:url(x)", "": ""},
             "class_counts": {"GT3": True, "GT4": True, "": True},
             "class_count": {"GT3": 20, "GT4": 18, "": None}}


def t_the_desk_groups_the_race_by_class():
    got = _desk_fn("classGroups", "classGroups(" + json.dumps(DESK_RACE) + ").map(g => "
                   "[g.name, g.lap, g.laps, g.rows.map(e => e.entry), g.color])")
    if got is None:
        return
    assert [g[:4] for g in got] == [["GT3", 7, 20, ["A", "C"]], ["GT4", 6, 18, ["B"]], ["", 6, None, ["D"]]], \
        f"every class under its header, the first with the race's lap count: {got}"
    assert [g[4] for g in got] == ["#e10600", "", ""], f"a header takes its class colour, never a non-colour: {got}"


def t_the_desk_renders_one_class_as_today():
    race = {"entries": [{"entry": "A"}, {"entry": "B"}], "classes": [], "class_lap": {}, "class_laps": {},
            "lap": {"leader": 3, "count": 20}}
    got = _desk_fn("classGroups", "classGroups(" + json.dumps(race) + ")")
    if got is None:
        return
    assert got == [{"name": None, "rows": [{"entry": "A"}, {"entry": "B"}]}], \
        f"outside class mode one group without a header, in overall order: {got}"


def t_the_desk_fastest_lap_form_takes_a_class():
    r = {"fastest": {"entry": "A", "ms": 60000}, "classes": ["GT3", "GT4", ""],
         "class_fastest": {"GT3": {"entry": "A", "ms": 60000}, "GT4": None, "": None}}
    got = _desk_fn("scopedFastest,fastestBody,flClasses",
                   "[scopedFastest(R, ''), scopedFastest(R, 'GT3'), scopedFastest(R, 'GT4'), scopedFastest(R, 'X'),"
                   " fastestBody('', {ms: 1}), fastestBody('GT4', {ms: 1}), flClasses(R), flClasses({classes: []})]"
                   .replace("R", json.dumps(r)))
    if got is None:
        return
    assert got[:4] == [{"entry": "A", "ms": 60000}, {"entry": "A", "ms": 60000}, None, None], \
        f"overall reads the race's lap, a class only its own: {got[:4]}"
    assert got[4:6] == [{"ms": 1}, {"class": "GT4", "ms": 1}], f"only a class scope sends the class: {got[4:6]}"
    assert got[6:] == [["GT3", "GT4"], []], f"the named classes, none outside class mode: {got[6:]}"


def t_the_desk_fastest_lap_entries_follow_the_class():
    r = {"entries": [{"entry": "A", "class": "GT3"}, {"entry": "B", "class": "GT4"}, {"entry": "C", "class": "GT3"}]}
    got = _desk_fn("flEntries", "[flEntries(R, ''), flEntries(R, 'GT3'), flEntries(R, 'GT4')]".replace("R", json.dumps(r)))
    if got is None:
        return
    assert got == [["A", "B", "C"], ["A", "C"], ["B"]], f"a class picks from its own entries, Overall from all: {got}"


def t_the_desk_class_header_count_follows_the_overall_rule():
    race = dict(DESK_RACE, class_count={"GT3": 20, "GT4": None, "": None})
    got = _desk_fn("classGroups", "classGroups(" + json.dumps(race) + ").map(g => g.laps)")
    if got is None:
        return
    assert got == [20, None, None], f"the header shows the relay's class count in race laps, none where it is off: {got}"


def t_the_desk_lap_line_shows_the_race_lap_and_the_lobby_lap():
    got = _desk_fn("lapText", '[lapText({lap: {leader: 4, count: 19}, lobby_lap: 5}), lapText({lap: {leader: 4, count: null}}),'
                              ' lapText({lap: {}, lobby_lap: null})]')
    if got is None:
        return
    assert got == ["Race lap 4 / 19 \u00b7 Lobby lap 5", "Race lap 4", ""], \
        f"the race lap against the count, then GT7's lap of the lobby on air: {got}"


def t_the_desk_names_a_qualifying_segment_once():
    got = _desk_fn("lapText", '["Qualifying", "qualifying 2", "Q1", ""].map(function (s) {'
                              ' return lapText({mode: "qualifying", segment: s}); })')
    if got is None:
        return
    assert got == ["Qualifying", "qualifying 2", "Qualifying \u00b7 Q1", "Qualifying"], \
        f"a segment that names qualifying itself shows alone: {got}"


def t_the_desk_shows_qualifying_per_class_with_every_column():
    rows = [{"entry": "A", "pos": 1, "best_ms": 90000, "gap_ms": 0, "interval_ms": None, "last_lap_ms": 90000,
             "class": "GT3", "class_pos": 1, "class_gap_ms": 0, "class_interval_ms": None},
            {"entry": "B", "pos": 2, "best_ms": 95000, "gap_ms": 5000, "interval_ms": 5000, "last_lap_ms": 96000,
             "class": "GT4", "class_pos": 1, "class_gap_ms": 0, "class_interval_ms": None},
            {"entry": "C", "pos": 3, "best_ms": 95400, "gap_ms": 5400, "interval_ms": 400, "last_lap_ms": 95400,
             "class": "GT4", "class_pos": 2, "class_gap_ms": 400, "class_interval_ms": 400}]
    r = {"mode": "qualifying", "segment": "Q1", "qualifying": rows,
         "quali_classes": [{"name": "GT3", "color": "#f00"}, {"name": "GT4", "color": "red;x"}]}
    got = _desk_fn("lapTime,plusMs,lapText,qualiGroups,qualiCells",
                   f"[lapText({json.dumps(r)}), qualiGroups({json.dumps(r)}).map(function (g) {{"
                   "return [g.name, g.color, g.rows.map(function (e, k) { return qualiCells(e, true, k); })]; }), "
                   f"qualiCells({json.dumps(rows[2])}, false, 2)]")
    if got is None:
        return
    line, groups, flat = got
    assert line == "Qualifying \u00b7 Q1", f"the lap line names the segment: {line}"
    assert [(g[0], g[1]) for g in groups] == [("GT3", "#f00"), ("GT4", "")], f"classes, a bad colour dropped: {groups}"
    gt4 = groups[1][2]
    assert [(c["pos"], c["best"], c["gap"], c["interval"], c["last"], c["mark"]) for c in gt4] == [
        ("1", "1:35.000", "Pole", "", "1:36.000", ""), ("2", "1:35.400", "+0.400", "+0.400", "1:35.400", "pb")], \
        f"positions, gaps and intervals within the class, a personal best marked: {gt4}"
    assert groups[0][2][0]["mark"] == "fl" and groups[0][2][0]["bestFl"], "the class's fastest lap is purple"
    assert (flat["pos"], flat["gap"], flat["interval"]) == ("3", "+5.400", "+0.400"), f"overall without classes: {flat}"


def t_the_desk_class_gap_reads_laps_down_in_the_class():
    got = _desk_fn("secs,lappedText,gapCell",
                   "[gapCell({lapped: 1, class_lapped: 0, class_gap_s: 2.5, gap_time_s: 90}, true, 4, 1),"
                   " gapCell({lapped: 0, class_lapped: 1, class_gap_s: null}, true, 4, 1),"
                   " gapCell({lapped: 2, class_lapped: 1, class_gap_s: null, class_gap_time_s: 158.2, gap_time_s: 300},"
                   " true, 4, 1),"
                   " gapCell({lapped: 1, gap_time_s: 90}, false, 4, 1)]")
    if got is None:
        return
    assert got[0] == {"text": "2.5", "lap": False}, f"a car lapped overall but not in its class: {got[0]}"
    assert got[1] == {"text": "+1\u00a0L", "lap": True}, f"a car lapped in its class without a time: {got[1]}"
    assert got[2] == {"text": "+1\u00a0L\u00a0\u00b7\u00a02:38.2", "lap": True}, \
        f"a car lapped in its class shows the exact time to its class leader: {got[2]}"
    assert got[3] == {"text": "+1\u00a0L\u00a0\u00b7\u00a01:30.0", "lap": True}, f"overall as before: {got[3]}"


def t_the_desk_class_interval_keeps_the_exact_time():
    got = _desk_fn("intervalTime",
                   "[intervalTime({class_interval_s: null, class_interval_time_s: 153.7, interval_time_s: 9}, true),"
                   " intervalTime({class_interval_s: 1.5, interval_time_s: 9}, false)]")
    if got is None:
        return
    assert got == [153.7, 9], f"grouped rows read the exact class interval, a lap down too: {got}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
