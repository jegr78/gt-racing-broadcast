#!/usr/bin/env python3
"""Stdlib structural checks for the Director Panel single-content layout.
Run: python3 tests/test_director_panel.py

No JS runtime here — these assert markup + presence-of-code anchors over the
served HTML string (same pattern as tests/test_cockpit.py). Runtime behavior is
verified via the ui-visual-verification render pass, not here.

The panel used to be a two-tab layout (PROGRAM / SETUP); it is now ONE compact
scrolling view: a full-width program deck, a full-width HUD, two 2-column control
blocks, and the full-width Schedule/Submissions/Substitution at the bottom. These
tests guard that structure and that NO control was dropped in the reflow."""
import os

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
    # The PROGRAM/SETUP tab shell is gone — one content area now.
    h = _html()
    assert 'role="tablist"' not in h
    assert 'id="tabProgram"' not in h and 'id="tabSetup"' not in h
    assert 'id="tabBtnProgram"' not in h and 'id="tabBtnSetup"' not in h
    assert "function setTab(" not in h, "tab-switch JS must be gone"
    assert "TAB_PANELS" not in h and "TAB_BTNS" not in h
    assert 'id="setupBadge"' not in h, "the SETUP tab badge is gone with the tabs"


def t_single_content_layout_classes():
    # The new layout primitives are present: deck + two-column control blocks.
    h = _html()
    assert 'class="deck"' in h, "program deck wrapper"
    assert h.count('class="cols"') == 2, "two 2-column control blocks"
    assert 'class="cues2"' in h, "Cues uses the full-width 2/1 body"
    assert 'class="grp"' in h, "Graphics/Utilities use grouped sub-rows"


def t_top_to_bottom_order():
    # deck(preview -> PGM) -> HUD -> [Feeds | Scn.Vis -> Timer] -> Log -> Cues ->
    # [Graphics(gfx/pre/grid/flag) | Audio -> Utilities(txBar)] -> Schedule ->
    # Submissions -> Substitution. One straight DOM order, no tab wrappers.
    h = _html()
    _order(h,
           'class="deck"',
           'id="previewSec"', 'id="pgmBus"', 'id="txArmed"',
           'id="hudBus"', 'id="setupRow"', 'id="teamRow"', 'id="condRow"',
           'id="feedsBus"', 'id="scnBus"', 'id="timerBus"',
           'id="log"',
           'id="cuesBus"',
           'id="gfxBus"', 'id="gfxPreRaceBus"', 'id="gfxGridTopBus"',
           'id="gfxGridBus"', 'id="flagGfxBus"',
           'id="audio"', 'id="txBar"', 'id="obsRefreshBtn"',
           'id="urlsBox"', 'id="subsBox"', 'id="subSec"')


def t_log_sits_between_feeds_and_cues():
    # The action log moved up: below the Feeds/Timer row, above Cues (near Feeds).
    h = _html()
    _order(h, 'id="timerBus"', 'id="log"', 'id="cuesBus"')
    assert 'id="log"' in h


def t_no_control_dropped():
    # Every JS-populated container / static control that existed under the tabs
    # must still be present after the reflow.
    h = _html()
    for cid in ("previewSec", "pgmAudioBar", "pgmBus", "feedsBus", "feedQuality",
                "scnBus", "gfxBus", "gfxPreRaceBus", "gfxGridTopBus", "gfxGridBus",
                "flagGfxBus", "timerBus", "timerInfo", "audio", "txBar", "txDur",
                "obsRefreshBtn", "setupRow", "teamRow", "condRow", "setupInfo",
                "cuePresets", "cueTarget", "cueLevel", "cueText", "cueSend",
                "cueRecent", "cueBackWrap", "urlsBox", "subsBox", "subSec", "log"):
        assert f'id="{cid}"' in h, f"dropped control: #{cid}"
    # per-feed PAUSE toggles + quality tiers survive
    assert h.count('class="k pvtoggle"') == 3
    # both feeds keep their quality tiers (>=2 buttons; "emergency" also appears in a CSS rule)
    assert h.count('data-tier="robust"') >= 2 and h.count('data-tier="emergency"') >= 2


def t_header_is_sticky():
    # The real page header is sticky now (previously the PGM bus was).
    h = _html()
    hdr = h.find("header{")
    assert hdr != -1
    seg = h[hdr:hdr + 200]
    assert "position:sticky" in seg and "top:0" in seg
    # the old PGM sticky rule is gone
    assert ".pgm{position:sticky" not in h


def t_cues_two_column_body():
    # Cues compose + presets sit left, recent/cueback right, inside .cues2 (2/1).
    h = _html()
    cues = h.find('id="cuesBus"')
    nxt = h.find('class="cols"', cues)          # the following control block
    seg = h[cues:nxt]
    assert 'class="cues2"' in seg
    assert seg.count('class="cuescol"') == 2
    _order(seg, 'id="cueTarget"', 'id="cuePresets"', 'id="cueRecent"')


def t_graphics_grouped_into_one_card():
    # Gfx + Pre-Race + Grid + Flag-Gfx are one card with labelled sub-rows now.
    h = _html()
    g = h.find('id="gfxBus"')
    end = h.find('</section>', h.find('id="flagGfxBus"'))
    seg = h[h.rfind('<section', 0, g):end]
    assert seg.count('class="grp"') >= 4
    for cid in ("gfxBus", "gfxPreRaceBus", "gfxGridTopBus", "gfxGridBus", "flagGfxBus"):
        assert f'id="{cid}"' in seg, f"#{cid} must live inside the merged Graphics card"


def t_utilities_merges_transition_and_obs():
    # Transition (#txBar, id kept for CSS/JS) + OBS refresh live in one card.
    h = _html()
    tx = h.find('id="txBar"')
    end = h.find('</section>', tx)
    seg = h[tx:end]
    assert 'id="obsRefreshBtn"' in seg, "OBS refresh folded into the Utilities card"
    assert 'data-tx="cut"' in seg and 'id="txDur"' in seg


def t_tx_chip_present_and_wired():
    h = _html()
    # chip lives in the deck's PGM section, before Cues
    assert 'id="txArmed"' in h
    pgm = h.find('class="bus pgm"')
    assert pgm != -1 and h.find('id="txArmed"') > pgm
    assert h.find('id="txArmed"') < h.find('id="cuesBus"'), "chip must be in the PGM/deck area"
    # renderTxBar updates the chip text
    assert 'chip.textContent = "TX: " + activeTransition.toUpperCase()' in h
    # clicking the chip scrolls to the Utilities/Transition card (tabs are gone)
    assert 'getElementById("txBar")' in h and "scrollIntoView" in h


def t_setup_badge_is_safe_noop():
    # updateSetupBadge is kept (callers unchanged) but its #setupBadge target is
    # gone, so it returns early — a safe no-op. Callers still fire.
    h = _html()
    assert "function updateSetupBadge(" in h
    assert h.count("updateSetupBadge()") >= 2
    assert 'getElementById("subsCount")' in h
    assert 'getElementById("subSec")' in h


def t_preview_default_shown():
    # New/unset installs show the preview by default (respects an explicit "0").
    h = _html()
    assert 'localStorage.getItem(PV_KEY) || "1"' in h


def t_final_part_confirmation_present():
    h = _html()
    # last-part detection in the modal + the final-confirm copy
    assert "d.index === d.count" in h or "d.index == d.count" in h
    assert "ends the broadcast" in h.lower()
    # the panel reacts to the relay's {final:true} response
    assert "res.final" in h


def t_mode_drives_section_visibility():
    # relayPoll delegates to applyMode(); applyMode toggles the two mode regions
    # and flips the single switch label. The two mode regions are mutually exclusive.
    h = _html()
    assert "applyMode(" in h, "relayPoll must delegate mode handling to applyMode"
    assert '$("#raceSched").hidden = qualifying' in h
    assert '$("#qualSched").hidden = !qualifying' in h
    assert "switch → QUALIFYING" in h   # race-mode target
    assert "switch → RACE" in h          # qualifying-mode target


def t_single_merged_schedule_section():
    # The old standalone Qualifying <details> is gone — one merged block.
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
    # POV must work in BOTH modes → its editor sits AFTER both mode regions
    # (shared), never nested inside the race-only or qualifying-only region.
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
    # `[hidden]{display:none}`, so setting `#urlsBox`.hidden in qualifying mode
    # would NOT hide the race schedule editor without an explicit override —
    # leaving the qualifying feed shown twice. A [hidden] guard must exist.
    h = _html()
    assert "details.urls[hidden]" in h, \
        "details.urls must honor the hidden attribute (else urlsBox stays shown in qualifying mode)"


def t_qualifying_submission_tag_present():
    h = _html()
    # subRow renders a QUALI tag when the pending entry is a qualifying submission
    assert 'QUALI' in h
    assert 'e.mode === "qualifying"' in h


def _func_body(html, name):
    """The source text of a top-level `function <name>(){ … }`, sliced from its
    declaration to the next top-level `function ` (or EOF). Enough for presence
    checks inside one function without a JS parser."""
    start = html.find("function " + name + "(")
    assert start != -1, "missing function " + name
    nxt = html.find("\nfunction ", start + 1)
    return html[start: nxt if nxt != -1 else len(html)]


def t_program_preview_self_reschedules_no_wedge():
    # Issue #520: the PROGRAM preview must use the robust self-rescheduling
    # `new Image()` probe (like the cockpit/race-control `pollProgram`), NOT a
    # `setInterval` re-assigning one reused `<img>`. With setInterval + a reused
    # img, a poll that outruns the interval (a slow/failing OBS screenshot vs the
    # 2 s obs-ws timeout) gets its pending request ABORTED by the next `img.src`
    # assignment — the browser fires neither onload nor onerror, so the frame and
    # the error state never land and the tile is stuck on "Program loading …"
    # forever with no recovery. The fresh-probe + setTimeout-after-resolve pattern
    # cannot overlap and auto-recovers once OBS delivers.
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


def t_stint_macros_resolve_on_the_relay_like_companion():
    # One behaviour for the panel and Companion: STINT A/B cut to Stint themselves
    # and take visibility, audio and the producer's commentary mic from the relay
    # (/obs/stint), which knows whether a stint is local. No static feed names and
    # no client-side mic decision may remain in the macro.
    import re
    h = _html()
    for label, feed in (("STINT A", "A"), ("STINT B", "B")):
        m = re.search(r'\{label:"' + label + r'",[^}]*\}', h)
        assert m, label
        macro = m.group(0)
        assert 'scene:"Stint"' in macro and 'relayStint:"' + feed + '"' in macro, macro
        assert '"Feed A"' not in macro and '"Feed B"' not in macro, macro
    # The PGM bus still tells STINT A from STINT B: with no static `show`, the air
    # light and the state read-back take the picked feed from macroAirSources().
    assert 'function macroAirSources(m){' in h
    assert '[[m.scene, "Feed " + m.relayStint]]' in h           # one source of truth
    # A relay-resolved step (SPLIT, STINT) answers with a note when an input is
    # missing (e.g. the commentary mic of an older collection): it is logged, not
    # just a red OBS LED.
    assert "function relayStep(what, path, body){" in h
    assert 'relayStep("stint " + m.relayStint, "stint", {feed: m.relayStint})' in h
    assert 'relayStep("split (on-air)", "split", {})' in h
    assert "const why = d && (d.note || (!d.ok && d.error));" in h   # a bare error too
    assert 'if (why) log(`${what}: ${why}`, d.ok ? "warn" : "err");' in h
    assert "for (const [sc, src] of macroAirSources(m))" in h
    assert "if (air && req.length){" in h
    for gone in ("stintMicIntent", "feedPlatforms", "micFor", "COMMENTARY_MIC"):
        assert gone not in h, gone


def t_feed_reset_is_labelled_as_the_backlog_resolution():
    # #587: the one RESET button per feed doubles as the deliberate backlog fix. It
    # jumps OBS back to live, so it says so and shows the cost from /status each poll;
    # no second button calls the same endpoint under another name.
    h = _html()
    assert '"RESET "+f+"→OBS"' not in h, "old RESET x→OBS label must be gone"
    assert h.count('obsPost("feed-reset"') == 1, "exactly one control calls feed-reset"
    assert "function resetLabel(" in h, "RESET label renderer"
    poll = h[h.index("async function relayPoll("):]
    poll = poll[:poll.index("\n}\n")]
    assert "resetLabel(d);" in poll, "RESET labels re-rendered from every /status poll"
    assert "reset_discards_s" in h, "the label reads the relay's discard figure"
    assert "discards " in h and " s backlog" in h, "the cost is spelled out on the button"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
