#!/usr/bin/env python3
"""Stdlib unit checks for the /console authorization policy (#216 phase 2).
Run: python3 tests/test_console.py"""
import importlib.util, os

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
                 ["submissions"], ["submissions", "approve"], ["submissions", "reject"]):
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
    # An authenticated subject with no resolved role still reaches an any-auth route.
    assert cp.decide(set(), ["health-monitor"]) == cp.ALLOW


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
    # and it is not a recognized POST or write route
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

if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
