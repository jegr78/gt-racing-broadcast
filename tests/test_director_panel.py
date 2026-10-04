#!/usr/bin/env python3
"""Stdlib structural checks for the Director Panel frame.
Run: python3 tests/test_director_panel.py

There is no JS runtime here: these assert markup and presence-of-code anchors over
the served HTML string. Runtime behavior is verified in the render pass.

The panel is a fixed frame (#728): topic navigation, the live column, one workspace
area at a time and the chat rail. These tests guard that structure and that no
control of the old page was dropped."""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PANEL = os.path.join(ROOT, "src", "director", "director-panel.html")


def _html():
    with open(PANEL, encoding="utf-8") as fh:
        return fh.read()


def _order(html, *needles):
    """Assert each needle appears, in strictly increasing position."""
    last = -1
    for n in needles:
        i = html.find(n)
        assert i != -1, f"missing: {n}"
        assert i > last, f"out of order: {n} (at {i}) not after previous (at {last})"
        last = i


def t_tabs_removed():
    # The PROGRAM/SETUP tab shell is gone; there is one content area.
    h = _html()
    assert 'role="tablist"' not in h
    assert 'id="tabProgram"' not in h and 'id="tabSetup"' not in h
    assert 'id="tabBtnProgram"' not in h and 'id="tabBtnSetup"' not in h
    assert "function setTab(" not in h, "tab-switch JS must be gone"
    assert "TAB_PANELS" not in h and "TAB_BTNS" not in h
    assert 'id="setupBadge"' not in h, "the SETUP tab badge is gone with the tabs"





def t_no_control_dropped():
    # Every JS-populated container and static control that existed under the tabs
    # must still be present after the reflow.
    h = _html()
    for cid in ("previewSec", "pgmAudioBar", "pgmBus", "feedsBus", "feedQuality",
                "scnBus", "gfxBus", "gfxPreRaceBus", "gfxGridTopBus", "gfxGridBus",
                "flagGfxBus", "timerBus", "timerInfo", "audio", "txBar", "txDur",
                "obsRefreshBtn", "setupRow", "teamRow", "condRow", "setupInfo",
                "cuePresets", "cueTarget", "cueLevel", "cueText", "cueSend",
                "cueRecent", "cueBackWrap", "urlsBox", "subsBox", "subSec", "log"):
        assert f'id="{cid}"' in h, f"dropped control: #{cid}"
    # per-feed PAUSE toggles and quality tiers survive
    assert h.count('class="k pvtoggle"') == 3
    # both feeds keep their quality tiers; "emergency" also appears in a CSS rule
    assert h.count('data-tier="robust"') >= 2 and h.count('data-tier="emergency"') >= 2



def t_cues_two_column_body():
    # Cues compose and presets sit left, recent and cueback right, inside .cues2.
    h = _html()
    seg = _area(h, "cues")
    assert 'class="cues2"' in seg
    assert seg.count('class="cuescol"') == 2
    _order(seg, 'id="cueTarget"', 'id="cuePresets"', 'id="cueRecent"')


def t_graphics_grouped_into_one_card():
    # Gfx, Pre-Race, Grid and Flag-Gfx are one card with labelled sub-rows.
    h = _html()
    g = h.find('id="gfxBus"')
    end = h.find('</section>', h.find('id="flagGfxBus"'))
    seg = h[h.rfind('<section', 0, g):end]
    assert seg.count('class="grp"') >= 4
    for cid in ("gfxBus", "gfxPreRaceBus", "gfxGridTopBus", "gfxGridBus", "flagGfxBus"):
        assert f'id="{cid}"' in seg, f"#{cid} must live inside the merged Graphics card"




def t_setup_badge_is_safe_noop():
    # updateSetupBadge is kept for its callers, but its #setupBadge target is gone,
    # so it returns early as a safe no-op.
    h = _html()
    assert "function updateSetupBadge(" in h
    assert h.count("updateSetupBadge()") >= 2
    assert 'getElementById("subsCount")' in h
    assert 'getElementById("subSec")' in h


def t_preview_default_shown():
    # A new or unset install shows the preview by default, and an explicit "0" wins.
    h = _html()
    assert 'localStorage.getItem(PV_KEY) || "1"' in h


def t_final_part_confirmation_present():
    h = _html()
    # last-part detection in the modal and the final-confirm copy
    assert "d.index === d.count" in h or "d.index == d.count" in h
    assert "ends the broadcast" in h.lower()
    assert "res.final" in h


def t_mode_drives_section_visibility():
    # relayPoll delegates to applyMode(), which toggles the two mutually exclusive
    # mode regions and flips the single switch label.
    h = _html()
    assert "applyMode(" in h, "relayPoll must delegate mode handling to applyMode"
    assert '$("#raceSched").hidden = qualifying' in h
    assert '$("#qualSched").hidden = !qualifying' in h
    assert "switch → QUALIFYING" in h   # race-mode target
    assert "switch → RACE" in h          # qualifying-mode target


def t_single_merged_schedule_section():
    # The standalone Qualifying <details> is gone; there is one merged block.
    h = _html()
    assert 'id="qualBox"' not in h, "qualBox must be merged into the single #urlsBox block"
    assert h.count('id="urlsBox"') == 1


def t_mode_regions_and_switch_present():
    h = _html()
    assert 'id="raceSched"' in h    # race-only region
    assert 'id="qualSched"' in h    # qualifying-only region
    assert 'id="modeSwitch"' in h   # the single mode switch
    assert 'id="modeChip"' in h     # always-visible mode indicator


def t_pov_editor_shared_across_modes():
    # POV works in both modes, so its editor sits after both mode regions and is
    # never nested inside the race-only or qualifying-only region.
    h = _html()
    assert h.index('id="povUrl"') > h.index('id="schedBody"')   # after race region content
    assert h.index('id="povUrl"') > h.index('id="qualRow"')     # after qualifying region content


def t_old_mode_buttons_removed():
    h = _html()
    assert 'id="qualOn"' not in h
    assert 'id="qualOff"' not in h
    assert 'id="qualModeBadge"' not in h


def t_urls_section_honors_hidden_rule():
    # `details.urls{display:block}` is an author rule that overrides the UA
    # `[hidden]{display:none}`, so without an explicit override, setting
    # `#urlsBox`.hidden in qualifying mode leaves the race schedule editor visible
    # and the qualifying feed shown twice. A [hidden] guard must exist.
    h = _html()
    assert "details.urls[hidden]" in h, \
        "details.urls must honor the hidden attribute (else urlsBox stays shown in qualifying mode)"


def t_qualifying_submission_tag_present():
    h = _html()
    # subRow renders a QUALI tag when the pending entry is a qualifying submission
    assert 'QUALI' in h
    assert 'e.mode === "qualifying"' in h


def _func_body(html, name):
    """The source text of a top-level `function <name>(){ ... }`, sliced from its
    declaration to the next top-level `function ` (or EOF). Enough for presence
    checks inside one function without a JS parser."""
    start = html.find("function " + name + "(")
    assert start != -1, "missing function " + name
    nxt = html.find("\nfunction ", start + 1)
    return html[start: nxt if nxt != -1 else len(html)]


def t_program_preview_self_reschedules_no_wedge():
    # The PROGRAM preview uses the self-rescheduling `new Image()` probe, like the
    # cockpit and race-control `pollProgram`, not a `setInterval` re-assigning one
    # reused `<img>`. With a reused img, a poll that outruns the interval has its
    # pending request aborted by the next `img.src` assignment: the browser fires
    # neither onload nor onerror, so the tile sticks on "Program loading" with no
    # recovery. A fresh probe plus setTimeout after resolve cannot overlap. (#520)
    h = _html()
    assert "setInterval(pvSetProgram" not in h, \
        "program preview must not be driven by setInterval on a reused <img> (wedges on a slow poll)"
    body = _func_body(h, "pvSetProgram")
    assert "new Image()" in body, \
        "pvSetProgram must probe with a fresh new Image() each cycle (never abort a pending reused-img load)"
    assert "setTimeout(pvSetProgram" in body, \
        "pvSetProgram must self-reschedule via setTimeout after onload/onerror (auto-recover)"
    assert "clearTimeout" in _func_body(h, "pvStop"), \
        "pvStop must clearTimeout the program-preview poll handle (no leaked poll after HIDE)"


def t_stint_macro_resolves_the_on_air_feed_on_the_relay():
    # One STINT macro (#729): the relay's /obs/stint picks the on-air feed and sets
    # its visibility, audio and the producer's commentary mic. NEXT decides the feed.
    h = _html()
    endurance = _config_block(h, "CONFIG")
    assert 'label:"STINT A"' not in endurance and 'label:"STINT B"' not in endurance
    m = re.search(r'\{label:"STINT",[^}]*\}', endurance)
    assert m and 'scene:"Stint"' in m.group(0) and 'relayStint:"live"' in m.group(0), m
    assert '"Feed A"' not in m.group(0) and '"Feed B"' not in m.group(0), m.group(0)
    # The air light follows the relay's on-air feed.
    src = _func_src(h, "macroAirSources")
    assert 'const feed = m.relayStint === "live" ? liveFeed : m.relayStint;' in src
    assert "liveFeed = live;" in _func_src(h, "renderLive")
    # A relay-resolved step answers with a note when an input is missing, and that
    # note is logged rather than shown only as a red OBS LED.
    assert "function relayStep(what, path, body){" in h
    assert 'relayStep("stint " + m.relayStint, "stint",' in h
    assert 'relayStep("split (on-air)", "split", {})' in h
    assert "const why = d && (d.note || (!d.ok && d.error));" in h   # a bare error too
    assert 'if (why) log(`${what}: ${why}`, d.ok ? "warn" : "err");' in h
    assert "for (const [sc, src] of macroAirSources(m))" in h
    assert "if (air && req.length){" in h


def t_emergency_feed_switch_lives_in_troubleshoot_with_a_confirm():
    h = _html()
    endurance = _config_block(h, "CONFIG")
    for feed in ("A", "B"):
        m = re.search(r'\{label:"FEED ' + feed + r' ON AIR",[^}]*\}', endurance)
        assert m and 'relayStint:"' + feed + '"' in m.group(0), feed
    assert 'id="emergencyBus"' in _area(h, "fault")
    keys = _block(_func_body(h, "buildControls"), "cfg.emergency.forEach", '$("#emergencyBus").appendChild(b);')
    assert "if (!confirm(" in keys and "return;" in keys, "the manual switch must be confirmed"
    assert "emergency: []," in _config_block(h, "CONFIG_SOLO")


def t_emergency_switch_moves_the_relay_too():
    h = _html()
    endurance = _config_block(h, "CONFIG")
    for feed in ("A", "B"):
        m = re.search(r'\{label:"FEED ' + feed + r' ON AIR",[^}]*\}', endurance)
        assert "take:true" in m.group(0), m.group(0)
    assert "m.take ? {feed: m.relayStint, take: true} : {feed: m.relayStint}" in _func_src(h, "runMacro")
    m = re.search(r'\{label:"STINT",[^}]*\}', endurance)
    assert "take" not in m.group(0), "the STINT macro never moves the relay"


def t_next_step_and_schedule_dot_read_handover_next():
    src = _func_src(_html(), "renderLive")
    assert "const ho = d.handover_next;" in src
    assert "const noLink = !qual && !end && ho && !ho.link;" in src
    assert "if (noLink)\n    setNav(\"schedule\", \"warn\"" in src, "only the next stint warns"
    assert "schedRows[of.index].url" not in src, "no client-side guess about the next link"


def t_solo_preview_tiles_follow_the_template():
    h = _html()
    assert 'const SOLO_TILES = {pov: [["capture", "CAPTURE"], ["webcam", "WEBCAM"]],' in h
    assert 'commentary: [["capture", "CAPTURE"], ["webcam", "WEBCAM"], ["tyres", "TYRES"]]};' in h
    src = _func_src(h, "soloLayout")
    assert "pvStopFeed(t); t.remove();" in src, "solo stops polling the A/B tiles it hides"
    assert "applySolo(solo, d.template);" in h and "soloLayout(template);" in _func_src(h, "applySolo")
    assert 'tile.dataset.src ? "/preview/source/" + tile.dataset.src' in _func_src(h, "pvPollFeed")


def t_removed_duplicate_keys():
    h = _html()
    for name in ("CONFIG", "CONFIG_SOLO"):
        cfg = _config_block(h, name)
        assert "STBY COVER" not in cfg, f"{name}: RED FLAG toggles the Standby Cover"
    vis = _config_block(h, "CONFIG")
    assert 'label:"FEED A"' not in vis and 'label:"FEED B"' not in vis, "the emergency switch replaces them"


def _macro_scenes(cfg):
    return set(re.findall(r'label:"[^"]+", scene:"([^"]+)"', cfg)) | \
        set(re.findall(r'soloMacro\("[^"]+",\s*"([^"]+)"', cfg))


def t_every_scene_has_a_macro():
    h = _html()
    for name in ("CONFIG", "CONFIG_SOLO"):
        cfg = _config_block(h, name)
        raw = re.findall(r'"([^"]+)"', re.search(r"\n  scenes: \[([^\]]*)\]", cfg).group(1))
        missing = set(raw) - _macro_scenes(cfg)
        assert not missing, f"{name}: scenes reachable only as a raw key: {missing}"


SOLO_AUDIO_TABLE = {   # macro: (unmuted, muted) of Game, Mic and Discord, from the spec
    "PROGRAM": ({"SOLO_GAME", "SOLO_MIC"}, {"SOLO_DISCORD"}),
    "INTERVIEW": ({"SOLO_MIC", "SOLO_DISCORD"}, {"SOLO_GAME"}),
    "STANDBY": ({"SOLO_MIC"}, {"SOLO_GAME", "SOLO_DISCORD"}),
    "INTERMISSION": ({"SOLO_MIC"}, {"SOLO_GAME", "SOLO_DISCORD"}),
    "INTRO": (set(), {"SOLO_GAME", "SOLO_MIC", "SOLO_DISCORD"}),
    "OUTRO": (set(), {"SOLO_GAME", "SOLO_MIC", "SOLO_DISCORD"}),
    "TRAILER": (set(), {"SOLO_GAME", "SOLO_MIC", "SOLO_DISCORD"}),
    "DISCORD": ({"SOLO_MIC", "SOLO_DISCORD"}, {"SOLO_GAME"}),
    "WEBCAM": ({"SOLO_GAME", "SOLO_MIC"}, {"SOLO_DISCORD"}),
    "CAPTURE": ({"SOLO_GAME", "SOLO_MIC"}, {"SOLO_DISCORD"}),
}


def t_solo_macros_follow_the_spec_audio_table():
    solo = _config_block(_html(), "CONFIG_SOLO")
    rows = re.findall(r'soloMacro\("([^"]+)",\s*"[^"]+",\s*\[([^\]]*)\],\s*\[([^\]]*)\]\)', solo)
    got = {label: ({x.strip() for x in on.split(",") if x.strip()},
                   {x.strip() for x in off.split(",") if x.strip()}) for label, on, off in rows}
    assert got == SOLO_AUDIO_TABLE, got


def t_solo_audio_inputs_exist_in_both_solo_collections():
    h = _html()
    names = dict(re.findall(r'(SOLO_GAME|SOLO_MIC|SOLO_DISCORD) = "([^"]+)"', h))
    assert set(names) == {"SOLO_GAME", "SOLO_MIC", "SOLO_DISCORD"}, names
    for filename in ("GT_Racing_Solo_POV.json", "GT_Racing_Solo_Commentary.json"):
        with open(os.path.join(ROOT, "src", "obs", filename), encoding="utf-8") as fh:
            sources = {s["name"] for s in json.load(fh)["sources"]}
        for const, name in names.items():
            assert name in sources, f"{filename}: no input {name!r} for {const}"


def t_endurance_macro_inputs_exist():
    cfg = _config_block(_html(), "CONFIG")
    with open(os.path.join(ROOT, "src", "obs", "GT_Racing_Endurance.json"), encoding="utf-8") as fh:
        sources = {s["name"] for s in json.load(fh)["sources"]}
    for group in re.findall(r"(?:un)?mute:\[([^\]]*)\]", cfg):
        for name in re.findall(r'"([^"]+)"', group):
            assert name in sources, f"macro input {name!r} is not in the endurance collection"
def t_feed_reset_is_labelled_as_the_backlog_resolution():
    # The one RESET button per feed doubles as the deliberate backlog fix. It jumps
    # OBS back to live, so it says so and shows the cost from /status on each poll,
    # and no second button calls the same endpoint under another name. (#587)
    h = _html()
    assert '"RESET "+f+"→OBS"' not in h, "old RESET x→OBS label must be gone"
    assert h.count('obsPost("feed-reset"') == 1, "exactly one control calls feed-reset"
    assert "function resetLabel(" in h, "RESET label renderer"
    poll = h[h.index("async function relayPoll("):]
    poll = poll[:poll.index("\n}\n")]
    assert "resetLabel(d);" in poll, "RESET labels re-rendered from every /status poll"
    assert "reset_discards_s" in h, "the label reads the relay's discard figure"
    assert "Math.floor(v)" in h, "floored: the button never overstates the cost"
    assert '"discards " + k.discards + " s backlog"' in h, "the cost is spelled out on the button"


def t_solo_telemetry_toggle():
    # Solo POV: a TELEMETRY key on the SCN-VIS bus toggles the HUD telemetry block
    # through the relay (no OBS item behind it) and lights from /status.
    html = _html()
    solo = _config_block(html, "CONFIG_SOLO")
    solo = solo[solo.index("vis:"):solo.index("audio:")]
    assert 'label:"TELEMETRY"' in solo and 'relay:"telemetry"' in solo
    assert 'relayCall(item.relay + "/toggle")' in html
    assert "teleVisBtn.classList.toggle(\"on\", !!(d.telemetry && d.telemetry.visible))" in html
    assert "teleVisBtn.hidden = !d.telemetry" in html


def t_solo_status_strip_names_the_car():
    """The status strip shows the GT7 car from /status (telemetry.car), as text so a
    car name can never inject markup, and hides the pill without one. (#713)"""
    html = _html()
    assert '<span class="st" id="stCar" hidden>CAR <b></b></span>' in html
    poll = html[html.index("async function relayPoll"):]
    poll = poll[:poll.index("}catch(e){")]
    assert "const car = d.telemetry && d.telemetry.car;" in poll
    assert '$("#stCar").hidden = !car;' in poll
    assert '$("#stCar b").textContent = carLabel(car);' in poll
    assert "function carLabel(car)" in html



def _config_block(html, name):
    """The source text of `const <name> = {...};`, up to the closing `};`."""
    start = html.index("const " + name + " = {")
    return html[start: html.index("\n};", start)]


def _collection_scenes(filename):
    """{scene name: set of its item names} of an OBS collection under src/obs/."""
    with open(os.path.join(ROOT, "src", "obs", filename), encoding="utf-8") as fh:
        d = json.load(fh)
    return {s["name"]: {i["name"] for i in s["settings"].get("items", [])}
            for s in d["sources"] if s.get("id") == "scene"}


def _assert_config_matches(cfg, filename):
    scenes = _collection_scenes(filename)
    listed = re.search(r"\n  scenes: \[([^\]]*)\]", cfg)
    assert listed, "config has no scenes list"
    for name in re.findall(r'"([^"]+)"', listed.group(1)):
        assert name in scenes, f"{filename}: scene key {name!r} has no scene"
    # Every OBS toggle and red-flag target; relay-driven items name no OBS item.
    pairs = re.findall(r'\{[^{}]*?scene:"([^"]+)",\s*source:"([^"]+)"[^{}]*\}', cfg)
    assert len(pairs) == cfg.count('source:"'), \
        "an OBS target is not written as {..., scene:..., source:...} and escapes this check"
    for scene, source in pairs:
        assert scene in scenes, f"{filename}: {source!r} targets missing scene {scene!r}"
        assert source in scenes[scene], \
            f"{filename}: scene {scene!r} has no item {source!r}"


def t_solo_config_targets_exist_in_both_solo_collections():
    solo = _config_block(_html(), "CONFIG_SOLO")
    for filename in ("GT_Racing_Solo_POV.json", "GT_Racing_Solo_Commentary.json"):
        _assert_config_matches(solo, filename)


def t_solo_scene_keys_cover_trailer_webcam_and_capture():
    solo = _config_block(_html(), "CONFIG_SOLO")
    listed = re.search(r"\n  scenes: \[([^\]]*)\]", solo).group(1)
    assert re.findall(r'"([^"]+)"', listed) == [
        "Program", "Interview", "Standby", "Intermission", "Intro", "Outro", "Trailer",
        "Discord", "Solo Webcam", "Solo Capture"], listed


def t_endurance_config_targets_exist_in_endurance_collection():
    _assert_config_matches(_config_block(_html(), "CONFIG"), "GT_Racing_Endurance.json")


def t_graphic_buses_come_from_the_relay_catalog():
    h = _html()
    assert "buildGraphicBuses();" in _func_body(h, "buildControls"), \
        "a rebuild of the other buses must rebuild the graphic keys too"
    body = _func_body(h, "buildGraphicBuses")
    for bus in ("gfxBus", "gfxPreRaceBus", "gfxGridTopBus", "gfxGridBus"):
        assert f'"#{bus}"' in body, f"{bus} is not built from the catalog"
    assert "Waiting for the relay" in body, "an unloaded catalog must say why the buses are empty"
    for name in ("CONFIG", "CONFIG_SOLO"):
        cfg = _config_block(h, name)
        for key in ("graphics:", "graphicsPreRace:", "graphicsGrid:"):
            assert key not in cfg, f"{name} still lists {key} next to the relay catalog"
    assert 'fetch("/obs/graphics"' in _func_body(h, "loadGraphicCatalog")


def t_graphic_requests_sit_in_the_live_column():
    h = _html()
    live = h[h.index('<section id="liveCol"'):h.index('<main id="workspace"')]
    assert 'id="gfxReqList"' in live, "requests must stay visible while another topic is open"
    body = _func_body(h, "renderRequests")
    assert "innerHTML" not in body and "textContent" in body, \
        "requester names come from the Sheet and must not reach innerHTML"
    for role in ("commentator", "race_control"):
        assert f'class="takemode" data-role="{role}"' in h, f"no take mode keys for {role}"


AREAS = ("handover", "graphics", "hud", "cues", "audio",
         "broadcast", "schedule", "setup", "fault")

# Every id of the page before the frame (#728). Only the three layout wrappers the
# frame replaced may go; a missing id here is a control the reshuffle dropped.
OLD_IDS = (
    "audio", "backConsole", "banners", "bchatBox", "bchatCompose", "bchatLog",
    "bchatRefresh", "brandSub", "brandSubEdit", "brandSubInput", "brandSubText", "chatBox",
    "chatInput", "chatLog", "chatName", "chatSendBtn", "chatUnread", "condRow",
    "cueBackList", "cueBackWrap", "cueHint", "cueLevel", "cuePresets", "cueRecent",
    "cueSend", "cueTarget", "cueText", "cuesBus", "feedHealth", "feedQuality", "feedsBus",
    "feedsSec", "flagGfxBus", "gfxBrowseBox", "gfxBus", "gfxGridBus", "gfxGridTopBus",
    "gfxList", "gfxPreRaceBus", "gfxRefresh", "hudBus", "ledObs", "ledRelay", "log",
    "modeChip", "modeSwitch", "notesBody", "notesBtn", "notesModal", "obsRefreshBtn",
    "obsStreamBtn", "partActionBtn", "partControl", "partModal", "partModalBody",
    "partModalConfirm", "partModalInput", "partModalPhrase", "partModalTitle", "partStatus",
    "pgmAudio", "pgmAudioBar", "pgmAudioBtn", "pgmAudioVol", "pgmBus", "povActionsBus",
    "povName", "povSave", "povUrl", "previewSec", "pvBody", "pvProgram", "pvProgramFrame",
    "pvProgramLabel", "pvToggle", "qualClear", "qualInfo", "qualLive", "qualNm", "qualRow",
    "qualSave", "qualSched", "qualSt", "qualUrl", "raceSched", "rebuildRearm", "schedAdd",
    "schedBody", "scnBus", "scnVisBus", "setupInfo", "setupRow", "stA", "stAir", "stB",
    "stBehind", "stCar", "stHealth", "stPov", "stTimer", "subInfo", "subReason", "subSave",
    "subSec", "subsBody", "subsBox", "subsCount", "subsEmpty", "teamRow", "timerBus",
    "timerInfo", "toasts", "top3Apply", "top3BatchTgl", "txBar", "txDur", "urlsBox")


def _block(html, start, end_marker):
    i = html.index(start)
    return html[i:html.index(end_marker, i)]


def _area(html, name):
    """The markup of one workspace area, up to the next area or the action log."""
    i = html.index(f'<div class="area" data-area="{name}">')
    ends = [j for j in (html.find('<div class="area"', i + 1), html.find('<div id="log">', i))
            if j != -1]
    return html[i:min(ends)]


def _func_src(html, name):
    """One top-level function: up to its closing brace or the next declaration."""
    i = html.index(f"function {name}(")
    ends = [j for j in (html.find(m, i + 1) for m in ("\n}\n", "\nfunction ", "\nasync function "))
            if j != -1]
    return html[i:min(ends)]


def t_old_controls_survive_the_frame():
    h = _html()
    missing = [i for i in OLD_IDS if f'id="{i}"' not in h]
    assert not missing, f"controls dropped by the frame: {missing}"
    for gone in ("ctlCols", "feedsCol", "txArmed"):
        assert f'id="{gone}"' not in h, f"#{gone} belongs to the old layout"


def t_frame_columns_in_order():
    _order(_html(), '<div class="frame">', '<nav id="areaNav"', '<section id="liveCol"',
           '<main id="workspace">', '<aside class="rail" id="chatRail"')


def t_nav_and_areas_match_the_spec_topics():
    h = _html()
    nav = re.findall(r'class="navbtn" data-area="([a-z]+)"', h)
    assert tuple(nav) == AREAS, nav
    areas = re.findall(r'<div class="area" data-area="([a-z]+)">', h)
    assert tuple(areas) == AREAS, areas
    assert '<span class="navhead">On air</span>' in h and '<span class="navhead">Operations</span>' in h


def t_cards_sit_in_their_area():
    h = _html()
    homes = {
        "handover": ("handoverSec", "hoOnAir", "hoNext", "hoUpcoming"),
        "graphics": ("gfxBus", "gfxPreRaceBus", "gfxGridTopBus", "gfxGridBus", "flagGfxBus"),
        "hud": ("hudBus", "setupRow", "teamRow", "condRow", "timerBus"),
        "cues": ("cuesBus", "cueText", "cueRecent"),
        "audio": ("audio",),
        "broadcast": ("streamSec", "obsStreamBtn", "partControl", "subSec"),
        "schedule": ("urlsBox", "subsBox"),
        "setup": ("gfxBrowseBox",),
        "fault": ("feedsSec", "feedsBus", "feedQuality", "emergencySec", "emergencyBus", "scnVisSec", "obsToolsSec", "obsRefreshBtn"),
    }
    for area, ids in homes.items():
        seg = _area(h, area)
        for cid in ids:
            assert f'id="{cid}"' in seg, f"#{cid} is not in the {area} area"


def t_live_column_holds_what_acts_on_air():
    h = _html()
    live = _block(h, '<section id="liveCol"', '<main id="workspace">')
    _order(live, 'id="previewSec"', 'id="liveOnAir"', 'id="armBtn"', 'id="nextBtn"',
           'id="pgmBus"', 'id="overlaySec"', 'id="scnVisBus"', 'id="txBar"')
    for cid in ("obsRefreshBtn", "obsStreamBtn", "partControl"):
        assert f'id="{cid}"' not in live, f"#{cid} is not a live control"
    assert 'data-tx="cut"' in live and 'id="txDur"' in live
    assert '<div class="liveair" id="liveOnAir">' in live and "  .onair{" not in h, \
        "the on-air card must not reuse .onair, which styles the on-air preview tile"


def t_chat_rail_holds_both_chats_as_sections():
    h = _html()
    rail = _block(h, '<aside class="rail" id="chatRail"', "</aside>")
    _order(rail, 'id="chatBox"', 'id="railSplit"', 'id="bchatBox"')
    assert "<details" not in rail, "a <details> passes no height to its children"
    assert '$("#chatBox").open' not in h, "chat visibility goes through chatVisible()"
    assert h.count("chatVisible()") >= 2
    assert '<button type="button" id="chatDrawerBtn"' in h and 'id="drawerUnread"' in h
    assert '$("#drawerUnread")' in _func_src(h, "chatUpdateBadge"), "the drawer button shows unread too"
    assert 'e.key === "Escape" && document.body.classList.contains("chats-open")) setDrawer(false);' in h, \
        "the open drawer covers its own button, so Escape must close it"
    assert '!e.target.closest("#chatRail, #chatDrawerBtn")' in h, "a click beside the drawer closes it"


def t_rail_split_is_draggable_and_remembered():
    h = _html()
    assert "grid-template-rows:minmax(140px,var(--crew,58fr)) 12px minmax(140px,var(--bcast,42fr))" in h
    assert 'role="separator"' in h and 'tabindex="0"' in _block(h, 'id="railSplit"', "</div>")
    src = _func_src(h, "applySplit")
    assert "Math.min(0.8, Math.max(0.2, r))" in src, "the split is clamped"
    assert 'SPLIT_KEY = "rc_chat_split"' in h


def t_area_and_nav_state_are_remembered_per_browser():
    h = _html()
    assert 'AREA_KEY = "rc_area"' in h and 'NAV_KEY = "rc_nav_collapsed"' in h
    store, recall = _func_src(h, "store"), _func_src(h, "recall")
    assert "try{" in store and "try{" in recall, "storage access must survive private mode"
    assert "store(AREA_KEY, name)" in _func_src(h, "showArea")


def t_area_falls_back_when_hidden():
    h = _html()
    assert "if (!AREAS.includes(name)) return false;" in _func_src(h, "areaAvailable"), \
        "a stored area name must be checked before it reaches a selector"
    src = _func_src(h, "showArea")
    assert 'if (!areaAvailable(name)) name = document.body.classList.contains("solo") ? "graphics" : "handover";' in src


def t_next_moved_into_the_live_column():
    h = _html()
    assert 'FEED_ACTIONS.filter(([label]) => label !== "NEXT")' in h or \
           'FEED_ACTIONS.filter(([label])=>label !== "NEXT")' in h, "NEXT left the feeds bus"
    handler = _block(h, '$("#nextBtn").addEventListener("click"', "});")
    assert 'relayCall(`next?transition=${activeTransition}&duration=' in handler, \
        "the handover cut uses the transition armed in the live column"
    assert "setTimeout(() => { b.disabled = false; }, 3000)" in handler, "double-press guard"


def t_arm_targets_the_off_air_feed_only_in_manual_mode():
    src = _func_src(_html(), "renderLive")
    assert 'd.feeds.A.index <= d.feeds.B.index ? "A" : "B"' in src, "live feed = lower index"
    assert "arm.hidden = !manual || qual || end;" in src
    assert "arm.dataset.feed = off;" in src
    assert '$("#nextBtn").classList.toggle("ready", serving && !end);' in src


def t_handover_matches_a_submission_by_sheet_row():
    src = _func_src(_html(), "renderHandover")
    assert 'e.target_line === r.sheetRow' in src, "stint labels can repeat; the sheet row cannot"
    assert "const pend = subs.length === 1 ? subs[0] : null;" in src, \
        "two submissions for one row must not be approvable with one click"
    assert "st.textContent = pend ? pend.proposed_url" in src, "the director sees the link before approving"


def t_handover_renders_text_only():
    h = _html()
    for fn in ("renderHandover", "hoCard", "renderLive", "setNav"):
        assert "innerHTML" not in _func_src(h, fn), f"{fn} must not build markup from relay data"


def t_live_column_follows_every_status_poll():
    poll = _func_src(_html(), "relayPoll")
    assert "renderLive(d);" in poll and "renderLive(null);" in poll


def t_solo_hides_handover_and_the_feed_switch():
    h = _html()
    hide = h[h.index("Kind-conditional cut (#307)"):]
    hide = hide[:hide.index("{ display: none !important; }")]
    for sel in ("body.solo #handoverCtl", "body.solo #nextStep", "body.solo #feedsSec"):
        assert sel in hide, f"solo must hide {sel}"
    assert "body.solo #emergencySec" in hide, "solo has no feed pair to switch"
    assert "scnVisSec" not in _func_src(h, "soloLayout"), "solo scenes are macros, raw keys stay in Troubleshoot"
    assert "soloLayout(template);" in _func_src(h, "applySolo")


def t_frame_fills_the_window_without_banners():
    h = _html()
    assert "#banners:empty{display:none}" in h
    assert "grid-row:3}" in _block(h, "  .frame{display:grid", "\n  .frame>"), \
        "with no banner the frame would fall into the auto row and leave the 1fr row empty"


def t_keyboard_shortcuts_are_off_by_default_and_shown_in_the_header():
    h = _html()
    assert '<button type="button" id="kbdBtn" class="kbdbtn" aria-pressed="false"' in h
    assert 'KEYS_KEY = "rc_keys"' in h
    assert _func_src(h, "keysOn").count('recall(KEYS_KEY) === "1"') == 1, "on only after an explicit opt-in"


def t_keyboard_shortcuts_cover_next_and_the_scene_macros_only():
    src = _func_src(_html(), "kbdTarget")
    assert 'if (key === "n")' in src
    assert "/^[1-9]$/.test(key)" in src
    assert 'filter(b => b._m)' in src, "RED FLAG and other toggles get no key"


def t_keyboard_shortcut_needs_a_confirming_second_press():
    h = _html()
    src = _block(h, 'document.addEventListener("keydown", e => {\n  if (!keysOn()', "\n});")
    assert "if (!keysOn() || e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;" in src
    assert 'closest("input, textarea, select, [contenteditable]")' in src, "never while typing"
    assert "kbdArmed.key === key && now - kbdArmed.at <= KEY_CONFIRM_MS" in src
    # The first press only arms; the click happens solely on the confirmed branch.
    assert src.count("btn.click()") == 1
    confirmed = src[src.index("kbdArmed.key === key"):src.index("btn.click()")]
    assert "kbdArm(" not in confirmed


def t_breakpoints():
    h = _html()
    assert 'matchMedia("(min-width:900px) and (max-width:1599px)")' in h, "nav collapses below 1600"
    assert "@media(max-width:1279px)" in h, "chats become a drawer below 1280"
    assert "@media(max-width:899px)" in h, "one column below 900"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
