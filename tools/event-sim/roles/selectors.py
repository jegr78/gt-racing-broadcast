"""Every selector the page roles use, in one place: a page that moves a control changes this
file only. `{name}` parts are filled in by the role. `tests/test_event_sim_roles.py` checks
each id, class, label and button text against the shipped files in `SOURCES`."""

PRODUCER = {
    "track": "#evt-track", "track_option": '#evt-track option[value="{track}"]',
    "track_error": "#evt-track-err",
    "formation": "#evt-race-formation", "finish_mode": "#evt-race-mode", "laps": "#evt-race-laps",
    "race_error": "#evt-race-err",
    "stint": "#stint", "qualifying": "#event-qualifying",
    "start": 'button:has-text("Start event")', "stop": 'button:has-text("Stop event")',
    "confirm": "#cmodal-ok", "confirm_text": "#cmodal-body",
    "job": "#jobchip", "job_log": "#joblog", "summary": "#home-summary", "relay": "#h-relay",
    "view": '.navitem[data-nav="{view}"]',
    "relay_restart": """[data-view="services"] button[onclick="op('relay-restart', true)"]""",
    "invite": "#evt-invite-text", "timing": "#evt-timing-text",
}

DIRECTOR = {
    "area": '.navbtn[data-area="{area}"]',
    "approve": '#hoUpcoming .hrow:has-text("{stint}") button:has-text("APPROVE")',
    "handover_row": '#hoUpcoming .hrow:has-text("{stint}")',
    "feed_arm": '#feedQuality .armbtn[data-feed="{feed}"][data-on="1"]', "feed_state": "#st{feed}",
    "arm": "#armBtn", "next": "#nextBtn", "next_ready": "#nextBtn.ready", "next_sub": "#nextSub",
    "on_air": "#liveOnAir", "next_step": "#nextStepText",
    "scene": '#pgmBus button.k:has-text("{scene}")',
    "red_flag": "#pgmBus button.redflag", "live": "#liveCol",
    "desk_tab": '#deskTabs button[data-sub="{sub}"]', "links": "#driverBox .mono.grow",
    "dashboard": '#driverBox button[data-label="Show dashboard"]',   # data-label keeps the first text
    "cue_target": "#cueTarget", "cue_level": "#cueLevel", "cue_text": "#cueText", "cue_send": "#cueSend",
    "chat_name": "#chatName", "chat_text": "#chatInput", "chat_send": "#chatSendBtn", "chat_log": "#chatLog",
    "graphic": ':is(#gfxBus, #gfxPreRaceBus, #gfxGridTopBus, #gfxGridBus) button.k:has-text("{name}")',
    "graphic_in": ':is(#gfxBus, #gfxPreRaceBus, #gfxGridTopBus, #gfxGridBus) '
                  'button.k:has(.tag:text-is("{scene}")):has-text("{name}")',
    "pov": '#scnVisBus button.k:has-text("POV")',
    "hud_field": 'select[data-setup="{field}"]',
    "mode_switch": "#modeSwitch", "mode_chip": "#modeChip",
    "part_button": "#partActionBtn", "part_status": "#partStatus", "part_phrase": "#partModalPhrase",
    "part_input": "#partModalInput", "part_confirm": "#partModalConfirm",
}

RACE_CONTROL = {
    "area": '.navbtn[data-area="{area}"]', "live": "#liveCol",
    "waiting": ".dd .req.waiting",
    "waiting_name": ".dd .req.waiting strong",
    "request": '.dd .req.waiting:has(strong[title="{name}"])',
    "request_entry": '.dd .req.waiting select[aria-label="Entry of {name}"]',
    "request_approve": '.dd .req.waiting:has(strong[title="{name}"]) button[data-label="Approve"]',
    "button": '.dd button[data-label="{label}"]',
    "select": '.dd select[aria-label="{label}"]',
    "input": '.dd input[aria-label="{label}"]',
    "status": '.dd .msg.cur[role="status"]',
    "lap_line": ".dd .lapline", "links": ".dd .mono.grow",
    "note_target": "#rcTarget", "note_text": "#rcText", "note_send": "#rcSend", "note_message": "#rcMsg",
}

COMMENTATOR = {
    "tally": "#tally", "card": "#submitCard", "stint": "#subStint", "url": "#subUrl",
    "submit": "#subBtn", "message": "#subMsg", "pending": "#subPending",
    "chat_text": "#chatin", "chat_send": "#chatsend", "chat_log": "#chatlog",
}

VIEWER = {"rows": "#timing tbody tr", "session": "#session", "state": "#state",
             "fastest": "#fastest"}

# The driver desk's button and field labels (src/console/driver-desk.js), by what they do.
DESK = {
    "final_lap": "Final lap",
    "finish": "Finish",
    "red_flag": "RED FLAG",
    "release": "Restart released",
    "retire_entry": "Entry to retire",
    "retire": "Retire (DNF)",
    "rejoin_entry": "Team to rejoin",
    "rejoin": "Mark rejoin",
    "estimate_entry": "Quiet entry to estimate",
    "estimate_side": "Behind or ahead of the reference",
    "estimate_ref": "Reference entry",
    "estimate_seconds": "Seconds from the reference",
    "estimate": "Set estimate",
    "correct_entry": "Entry to correct",
    "correct_metres": "Distance correction in metres",
    "correct_pits": "Pit stop correction",
    "correct_reason": "Reason",
    "correct": "Apply correction",
    "quali_entry": "Qualifying entry",
    "quali_time": "Qualifying time",
    "set_time": "Set time",
    "add_lap": "Add lap",
    "approve": "Approve",
    "dashboard_show": "Show dashboard",
    "dashboard_hide": "Hide dashboard",
    "confirm_prefix": "Confirm ",
    "side_ahead": "ahead of",
    "side_behind": "behind",
}

# The live-column controls `fit` waits for and fails without, by label (a label may run on).
LIVE_REQUIRED = {"DIRECTOR": ("NEXT", "CUT", "FADE", "STINGER"),
                 "RACE_CONTROL": (DESK["red_flag"], DESK["final_lap"], DESK["finish"])}

# The link texts a screenshot of any page blacks out: the driver invite and the timing link.
LINKS = ("#evt-invite-text", "#evt-timing-text", ".dd .mono.grow")

# Visible once the page works.
READY = {"PRODUCER": "#evt-track", "DIRECTOR": "#nextBtn", "RACE_CONTROL": "#liveCol",
         "COMMENTATOR": "#tally", "VIEWER": "#timing"}

SOURCES = {
    "PRODUCER": ("src/ui/control-center.html",),
    "DIRECTOR": ("src/director/director-panel.html", "src/console/driver-desk.js"),
    "RACE_CONTROL": ("src/racecontrol/race-control.html", "src/console/driver-desk.js"),
    "COMMENTATOR": ("src/cockpit/cockpit.html",),
    "VIEWER": ("src/console/timing.html",),
}
