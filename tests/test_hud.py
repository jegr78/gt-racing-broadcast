#!/usr/bin/env python3
"""Stdlib unit checks for the HUD additions. Run: python3 tests/test_hud.py"""
import importlib.util, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location(
    "irofeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def t_asset_key_basic():
    assert m.asset_key("GERMANY") == "germany"
    assert m.asset_key("  United Arab Emirates ") == "united-arab-emirates"
    assert m.asset_key("Porsche") == "porsche"
    assert m.asset_key("") == ""
    assert m.asset_key(None) == ""


OVERLAY_CSV = (
    ",Stint,Intro,,,,,,,\n"
    ",,,,,,,,,\n"
    ",,,,,,,,,\n"
    ",Streamer,JeGr,,,,,,,\n"
    ",,,,,,,,,\n"
    ",Session,Warmup,,,,,,,\n"
    ",,,,,,,,,\n"
    ",Round Top,Round 4: Nurburgring 24hrs,,,,,,,\n"
    ",,,,,,,,,\n"
    ",Round Bottom,GERMANY,,,,,,,\n"
    ",,,,,,,,,\n"
    ",Round Flag,,,,,,,,\n"
    ",,,,,,,,,\n"
    ",Teams P1,,,OVO eSports #111,,,,,\n"
    ",,,,,,,,,\n"
    ",Teams P2,,,Feel Good Racing #303,,,,,\n"
    ",,,,,,,,,\n"
    ",Teams P3,,,NWR Motorsport #224,,,,,\n"
    ",,,,,,,,,\n"
    ",Race Control,,,,,,,,\n"
)


def t_parse_overlay_values():
    o = m.parse_overlay(OVERLAY_CSV)
    assert o["stint"] == "Intro", o
    assert o["streamer"] == "JeGr", o
    assert o["session"] == "Warmup", o
    assert o["round_top"] == "Round 4: Nurburgring 24hrs", o
    assert o["country"] == "GERMANY", o
    assert o["teams"] == ["OVO eSports #111", "Feel Good Racing #303",
                          "NWR Motorsport #224"], o
    assert o["race_control"] == "", o


def t_parse_overlay_missing_rows_safe():
    o = m.parse_overlay(",Streamer,JeGr,,,,,,,\n")
    assert o["streamer"] == "JeGr"
    assert o["teams"] == ["", "", ""]
    assert o["country"] == ""


CONFIG_CSV = (
    "Stints,Streamers,Session,Round,Country,Flag,Teams,,Brands,Race Control,Brand Key\n"
    "Stint 1,JeGr,Qualifier,Round 1,UNITED STATES,,OVO eSports #111,,,Formation Lap,Porsche\n"
    "Stint 2,GT45,Race,Round 2,AUSTRALIA,,Feel Good Racing #303,,,Final Lap,Porsche\n"
    "Stint 3,,,,,,NWR Motorsport #224,,,,Ferrari\n"
)


def t_parse_config_roster():
    # Keyed by the VERBATIM label (with #NNN) so same-name cars never collide.
    r = m.parse_config_roster(CONFIG_CSV)
    assert r["OVO eSports #111"] == {"number": "111", "brandKey": "porsche",
                                     "brandName": "Porsche",
                                     "bgColor": "", "textColor": ""}, r
    assert r["Feel Good Racing #303"] == {"number": "303", "brandKey": "porsche",
                                          "brandName": "Porsche",
                                          "bgColor": "", "textColor": ""}, r
    assert r["NWR Motorsport #224"] == {"number": "224", "brandKey": "ferrari",
                                        "brandName": "Ferrari",
                                        "bgColor": "", "textColor": ""}, r


def t_parse_config_roster_missing_team_header_safe():
    assert m.parse_config_roster("a,b,c\n1,2,3\n") == {}


# The sheet's text header is "Brand Name", alongside the image columns "Brand Logo"
# and "Brands", which must not be picked up.
CONFIG_CSV_BRANDNAME = (
    "Teams,Brand Name,Brand Logo,Brands,Race Control\n"
    "OVO eSports #111,Porsche,,,Formation Lap\n"
    "Elite Racing Squad #73,BMW,,,Final Lap\n"
    "Alien Motorsports #999,AMG,,,Warmup\n"
)


def t_parse_config_roster_accepts_brand_name_header():
    r = m.parse_config_roster(CONFIG_CSV_BRANDNAME)
    assert r["OVO eSports #111"] == {"number": "111", "brandKey": "porsche",
                                     "brandName": "Porsche",
                                     "bgColor": "", "textColor": ""}, r
    assert r["Elite Racing Squad #73"] == {"number": "73", "brandKey": "bmw",
                                           "brandName": "BMW",
                                           "bgColor": "", "textColor": ""}, r
    assert r["Alien Motorsports #999"] == {"number": "999", "brandKey": "amg",
                                           "brandName": "AMG",
                                           "bgColor": "", "textColor": ""}, r


# The "Brand Name Override" column wins for the display name when present, but never
# changes the logo mapping: brandKey stays the asset_key of Brand.
CONFIG_CSV_BRAND_OVERRIDE = (
    "Teams,Brand Name,Brand Name Override,Race Control\n"
    "OVO eSports #111,Porsche,Porsche 963,Formation Lap\n"   # override wins for text
    "Elite Racing Squad #73,BMW,,Final Lap\n"                 # blank override -> verbatim
)


def t_parse_config_roster_brand_name_override():
    r = m.parse_config_roster(CONFIG_CSV_BRAND_OVERRIDE)
    # override wins for the display name; brandKey still maps from Brand ("porsche")
    assert r["OVO eSports #111"] == {
        "number": "111", "brandKey": "porsche", "brandName": "Porsche 963",
        "bgColor": "", "textColor": ""}, r
    # empty override falls back to the verbatim brand text
    assert r["Elite Racing Squad #73"] == {
        "number": "73", "brandKey": "bmw", "brandName": "BMW",
        "bgColor": "", "textColor": ""}, r


def t_parse_config_roster_ignores_image_columns():
    # team header present, only image brand columns -> brandKey blank, number from #1
    assert m.parse_config_roster("Teams,Brand Logo,Brands\nX #1,,\n") == {
        "X #1": {"number": "1", "brandKey": "", "brandName": "",
                 "bgColor": "", "textColor": ""}}


def t_build_hud_data():
    overlay = m.parse_overlay(OVERLAY_CSV)
    roster = m.parse_config_roster(CONFIG_CSV)
    d = m.build_hud_data(overlay, roster)
    assert d["stint"] == "Intro"
    assert d["streamer"] == "JeGr"
    assert d["session"] == "Warmup"
    assert d["round"]["top"] == "Round 4: Nurburgring 24hrs"
    assert d["round"]["country"] == "GERMANY"
    assert d["round"]["flagKey"] == "germany"
    assert d["teams"][0] == {"name": "OVO eSports", "number": "111", "brandKey": "porsche",
                             "brandName": "Porsche", "label": "OVO eSports #111",
                             "bgColor": "", "textColor": "", "qualiLap": ""}
    assert d["teams"][2] == {"name": "NWR Motorsport", "number": "224", "brandKey": "ferrari",
                             "brandName": "Ferrari", "label": "NWR Motorsport #224",
                             "bgColor": "", "textColor": "", "qualiLap": ""}
    assert d["raceControl"] == ""


def t_build_hud_data_unknown_brand_blank():
    overlay = m.parse_overlay(",Teams P1,,,Mystery Team #0,,,,,\n")
    d = m.build_hud_data(overlay, {})
    assert d["teams"][0] == {"name": "Mystery Team", "number": "0", "brandKey": "",
                             "brandName": "", "label": "Mystery Team #0",
                             "bgColor": "", "textColor": "", "qualiLap": ""}


def t_hudsource_data_uses_builders():
    import tempfile, os as _os
    d = tempfile.mkdtemp()
    hs = m.HudSource("http://overlay", "http://config",
                     _os.path.join(d, "hud.cache.json"))
    hs._fetch = lambda url, timeout=10: OVERLAY_CSV if url == "http://overlay" else CONFIG_CSV
    assert hs.refresh() is True
    data = hs.data()
    assert data["streamer"] == "JeGr"
    assert data["teams"][0]["brandKey"] == "porsche"


def t_resolve_asset_extension_agnostic():
    import tempfile, os as _os
    ad = tempfile.mkdtemp()
    _os.makedirs(_os.path.join(ad, "brands"))
    _os.makedirs(_os.path.join(ad, "flags"))
    with open(_os.path.join(ad, "brands", "porsche.png"), "w") as fh:
        fh.write("x")
    with open(_os.path.join(ad, "flags", "germany.svg"), "w") as fh:
        fh.write("x")
    path, ctype = m.resolve_asset(ad, "brands", "porsche")
    assert path.endswith("porsche.png") and ctype == "image/png", (path, ctype)
    path, ctype = m.resolve_asset(ad, "flags", "germany")
    assert path.endswith("germany.svg") and ctype == "image/svg+xml", (path, ctype)
    assert m.resolve_asset(ad, "brands", "ferrari") is None
    assert m.resolve_asset(ad, "evil", "porsche") is None
    assert m.resolve_asset(ad, "brands", "../secret") is None


def t_hudsource_keeps_last_good_on_failure():
    import tempfile, os as _os
    d = tempfile.mkdtemp()
    hs = m.HudSource("http://overlay", "http://config",
                     _os.path.join(d, "hud.cache.json"))
    hs._fetch = lambda url, timeout=10: OVERLAY_CSV if url == "http://overlay" else CONFIG_CSV
    hs.refresh()
    def boom(url, timeout=10):
        raise RuntimeError("sheet down")
    hs._fetch = boom
    assert hs.refresh() is False
    assert hs.data()["streamer"] == "JeGr"   # last-good preserved


def t_parse_config_vocab():
    v = m.parse_config_vocab(CONFIG_CSV)
    assert v["stint"] == ["Stint 1", "Stint 2", "Stint 3"], v
    assert v["streamer"] == ["JeGr", "GT45"], v
    assert v["session"] == ["Qualifier", "Race"], v
    assert v["racecontrol"] == ["Formation Lap", "Final Lap"], v


def t_parse_config_vocab_missing_columns_safe():
    v = m.parse_config_vocab("a,b\n1,2\n")
    assert v == {"stint": [], "streamer": [], "session": [], "racecontrol": [], "flag": []}
    assert m.parse_config_vocab("") == {"stint": [], "streamer": [],
                                        "session": [], "racecontrol": [], "flag": []}


def t_parse_config_vocab_dedupes_keeps_order():
    v = m.parse_config_vocab("Streamers\nB\nA\nB\n\nA\n")
    assert v["streamer"] == ["B", "A"], v


FLAG_OVERLAY_CSV = (",Flag,Safety Car,,,,,,,\n")
def t_parse_overlay_flag():
    assert m.parse_overlay(FLAG_OVERLAY_CSV)["flag"] == "Safety Car"

def t_build_hud_data_flag():
    d = m.build_hud_data(m.parse_overlay(FLAG_OVERLAY_CSV), {})
    assert d["flag"] == "Safety Car"

def t_build_hud_data_flag_default_empty():
    d = m.build_hud_data(m.parse_overlay(",Stint,X,,,,,,,\n"), {})
    assert d["flag"] == ""

FLAG_CONFIG_CSV = ("Stints,Flag,Race Control\n"
                   "Stint 1,Yellow Flag,Formation Lap\n"
                   "Stint 2,Safety Car,Final Lap\n")
def t_parse_config_vocab_flag():
    assert m.parse_config_vocab(FLAG_CONFIG_CSV)["flag"] == ["Yellow Flag", "Safety Car"]

def t_hudsource_empty_has_flag():
    assert m.HudSource.EMPTY["flag"] == ""


def t_hudsource_vocab_from_refresh():
    import tempfile, os as _os
    d = tempfile.mkdtemp()
    hs = m.HudSource("http://overlay", "http://config",
                     _os.path.join(d, "hud.cache.json"))
    hs._fetch = lambda url, timeout=10: OVERLAY_CSV if url == "http://overlay" else CONFIG_CSV
    assert hs.vocab() == {"stint": [], "streamer": [], "session": [],
                          "racecontrol": [], "flag": []}   # before any refresh
    hs.refresh()
    assert hs.vocab()["streamer"] == ["JeGr", "GT45"]


def t_hudsource_vocab_preserved_on_failure():
    import tempfile, os as _os
    d = tempfile.mkdtemp()
    hs = m.HudSource("http://overlay", "http://config",
                     _os.path.join(d, "hud.cache.json"))
    hs._fetch = lambda url, timeout=10: OVERLAY_CSV if url == "http://overlay" else CONFIG_CSV
    hs.refresh()
    def boom(url, timeout=10):
        raise RuntimeError("sheet down")
    hs._fetch = boom
    assert hs.refresh() is False
    assert hs.vocab()["streamer"] == ["JeGr", "GT45"]   # last-good preserved


def _hs():
    import tempfile, os as _os
    d = tempfile.mkdtemp()
    hs = m.HudSource("http://overlay", "http://config",
                     _os.path.join(d, "hud.cache.json"))
    hs._fetch = lambda url, timeout=10: OVERLAY_CSV if url == "http://overlay" else CONFIG_CSV
    hs.refresh()
    return hs


def t_override_applies_immediately():
    hs = _hs()
    hs.set_override("raceControl", "Formation Lap", now=1000.0)
    assert hs.data(now=1001.0)["raceControl"] == "Formation Lap"
    assert hs.data(now=1001.0)["streamer"] == "JeGr"   # others untouched
    assert hs.pending(now=1001.0) == {"raceControl"}


def t_override_expires_back_to_sheet_truth():
    hs = _hs()
    hs.set_override("raceControl", "Formation Lap", now=1000.0)
    late = 1000.0 + m.OVERRIDE_TTL + 1
    assert hs.pending(now=late) == set()          # expiry visible without a data() call
    assert hs.data(now=late)["raceControl"] == ""
    assert hs.pending() == set()


def t_override_cleared_when_sheet_confirms():
    hs = _hs()
    hs.set_override("streamer", "GT45", now=1000.0)
    confirmed = OVERLAY_CSV.replace(",Streamer,JeGr,", ",Streamer,GT45,")
    hs._fetch = lambda url, timeout=10: confirmed if url == "http://overlay" else CONFIG_CSV
    hs.refresh()
    assert hs.pending() == set()
    assert hs.data(now=1001.0)["streamer"] == "GT45"


def t_override_survives_unconfirmed_refresh():
    hs = _hs()   # sheet still says JeGr
    hs.set_override("streamer", "GT45", now=1000.0)
    hs.refresh()                                       # poll without the new value yet
    assert hs.data(now=1001.0)["streamer"] == "GT45"   # echo still pending
    assert hs.pending(now=1001.0) == {"streamer"}


def t_split_team_label_trailing_number():
    assert m.split_team_label("OVO eSports #111") == ("OVO eSports", "111")
    assert m.split_team_label("Apex Racing #7") == ("Apex Racing", "7")

def t_split_team_label_no_number():
    assert m.split_team_label("OVO eSports") == ("OVO eSports", "")
    assert m.split_team_label("") == ("", "")

def t_split_team_label_mid_string_hash_kept():
    # only a TRAILING "#<digits>" token is split off; a mid-string # stays in the name
    assert m.split_team_label("Team #1 Racing") == ("Team #1 Racing", "")
    assert m.split_team_label("  Spaced #42  ") == ("Spaced", "42")

def t_split_team_label_no_redos_on_long_spaces():
    # CodeQL py/polynomial-redos (#170): the old `^(.*?)\s*#(\d+)\s*$` put `(.*?)`
    # next to `\s*`, both matching a space, so a long run of spaces with no trailing
    # '#<digits>' backtracked quadratically. A huge label must resolve in linear time.
    import time
    label = "Team" + " " * 60000 + "Racing"       # internal spaces, no trailing #num
    t0 = time.monotonic()
    assert m.split_team_label(label) == (label, "")
    assert time.monotonic() - t0 < 1.0
    # trailing number still peeled even behind a long space run
    assert m.split_team_label("Team" + " " * 60000 + "#7") == ("Team", "7")


ROSTER_CSV_WITH_NUMBER = (
    "Teams,Number,Brand Name\n"
    "OVO eSports,111,Porsche\n"
    "Feel Good,303,BMW\n")

ROSTER_CSV_EMBEDDED_ONLY = (
    "Teams,Brand Name\n"
    "OVO eSports #111,Porsche\n"
    "Apex Racing #7,Audi\n")

ROSTER_CSV_BOTH = (
    "Teams,Number,Brand Name\n"
    "OVO eSports #999,111,Porsche\n")   # embedded #999 must be ignored, column wins

# Two cars of the same team name but different race numbers: each verbatim '#NNN'
# label is its own roster entry. The roster is keyed by the verbatim label, not the
# stripped name, so the dropdown and HUD never collapse two cars into one.
ROSTER_CSV_DUP_NAME = (
    "Teams,Brand Name\n"
    "Scuderia Adriatica Motorsport #14,Ferrari\n"
    "Scuderia Adriatica Motorsport #54,AMG\n")

def t_roster_same_name_different_number_kept_distinct():
    r = m.parse_config_roster(ROSTER_CSV_DUP_NAME)
    assert r["Scuderia Adriatica Motorsport #14"] == {
        "number": "14", "brandKey": "ferrari", "brandName": "Ferrari",
        "bgColor": "", "textColor": ""}, r
    assert r["Scuderia Adriatica Motorsport #54"] == {
        "number": "54", "brandKey": "amg", "brandName": "AMG",
        "bgColor": "", "textColor": ""}, r

def t_team_entry_resolves_per_car_by_verbatim_label():
    # The HUD resolves number and brand for the specific car in the slot, not
    # whichever same-name row won a stripped-key collision. The displayed name is
    # still stripped; the verbatim 'label' rides along for the panel dropdown.
    roster = m.parse_config_roster(ROSTER_CSV_DUP_NAME)
    assert m.team_entry("Scuderia Adriatica Motorsport #14", roster) == {
        "name": "Scuderia Adriatica Motorsport", "number": "14",
        "brandKey": "ferrari", "brandName": "Ferrari",
        "label": "Scuderia Adriatica Motorsport #14",
        "bgColor": "", "textColor": "", "qualiLap": ""}
    assert m.team_entry("Scuderia Adriatica Motorsport #54", roster) == {
        "name": "Scuderia Adriatica Motorsport", "number": "54",
        "brandKey": "amg", "brandName": "AMG",
        "label": "Scuderia Adriatica Motorsport #54",
        "bgColor": "", "textColor": "", "qualiLap": ""}

def t_roster_number_column():
    r = m.parse_config_roster(ROSTER_CSV_WITH_NUMBER)
    assert r == {"OVO eSports": {"number": "111", "brandKey": "porsche", "brandName": "Porsche",
                                 "bgColor": "", "textColor": ""},
                 "Feel Good": {"number": "303", "brandKey": "bmw", "brandName": "BMW",
                              "bgColor": "", "textColor": ""}}, r

def t_roster_embedded_fallback():
    r = m.parse_config_roster(ROSTER_CSV_EMBEDDED_ONLY)
    assert r["OVO eSports #111"] == {"number": "111", "brandKey": "porsche", "brandName": "Porsche",
                                     "bgColor": "", "textColor": ""}
    assert r["Apex Racing #7"] == {"number": "7", "brandKey": "audi", "brandName": "Audi",
                                   "bgColor": "", "textColor": ""}

def t_roster_column_wins_over_embedded():
    # Key is the verbatim label; the Number column still wins for the car number.
    r = m.parse_config_roster(ROSTER_CSV_BOTH)
    assert r == {"OVO eSports #999": {"number": "111", "brandKey": "porsche", "brandName": "Porsche",
                                      "bgColor": "", "textColor": ""}}, r

def t_roster_no_teams_header_is_empty():
    assert m.parse_config_roster("Foo,Bar\n1,2\n") == {}


def t_team_full_labels_keeps_embedded_number():
    # Embedded #NNN (no Number column) -> verbatim label kept under the bare key.
    r = m.parse_team_full_labels(ROSTER_CSV_EMBEDDED_ONLY)
    assert r == {"OVO eSports": "OVO eSports #111", "Apex Racing": "Apex Racing #7"}, r


def t_team_full_labels_bare_when_number_is_separate_column():
    # Bare Teams column + separate Number column -> verbatim label is the bare name.
    r = m.parse_team_full_labels(ROSTER_CSV_WITH_NUMBER)
    assert r == {"OVO eSports": "OVO eSports", "Feel Good": "Feel Good"}, r


def t_team_full_labels_no_teams_header_is_empty():
    assert m.parse_team_full_labels("Foo,Bar\n1,2\n") == {}

def t_build_hud_data_team_number_and_strip():
    # Bare roster key + a numbered slot value -> stripped-name fallback still hits.
    roster = {"OVO eSports": {"number": "111", "brandKey": "porsche", "brandName": "Porsche"}}
    overlay = {"teams": ["OVO eSports #999", "Unknown #5", ""]}
    d = m.build_hud_data(overlay, roster)
    assert d["teams"][0] == {"name": "OVO eSports", "number": "111", "brandKey": "porsche",
                             "brandName": "Porsche", "label": "OVO eSports #999",
                             "bgColor": "", "textColor": "", "qualiLap": ""}
    assert d["teams"][1] == {"name": "Unknown", "number": "5", "brandKey": "",
                             "brandName": "", "label": "Unknown #5",
                             "bgColor": "", "textColor": "", "qualiLap": ""}
    assert d["teams"][2] == {"name": "", "number": "", "brandKey": "", "brandName": "", "label": "",
                             "bgColor": "", "textColor": "", "qualiLap": ""}


def t_parse_config_roster_team_name_header():
    r = m.parse_config_roster("Team Name,Number,Brand\nOVO eSports,111,Porsche\n")
    assert r == {"OVO eSports": {"number": "111", "brandKey": "porsche", "brandName": "Porsche",
                                 "bgColor": "", "textColor": ""}}, r


def t_hudsource_roster_preserved_on_failure():
    import tempfile, os as _os
    d = tempfile.mkdtemp()
    hs = m.HudSource("http://overlay", "http://config", _os.path.join(d, "hud.cache.json"))
    hs._fetch = lambda url, timeout=10: OVERLAY_CSV if url == "http://overlay" else CONFIG_CSV
    assert hs.refresh() is True
    assert hs._roster  # populated
    def boom(url, timeout=10): raise OSError("network down")
    hs._fetch = boom
    assert hs.refresh() is False
    assert hs._roster  # last-good roster preserved, not cleared


def _roster_hud():
    import tempfile, os as _os
    d = tempfile.mkdtemp()
    hs = m.HudSource("http://overlay", "http://config",
                     _os.path.join(d, "hud.cache.json"))
    overlay = (",Teams P1,OVO eSports,,\n,Teams P2,Feel Good,,\n,Teams P3,,,\n")
    config = "Teams,Number,Brand Name\nOVO eSports,111,Porsche\nFeel Good,303,BMW\n"
    hs._fetch = lambda url, timeout=10: overlay if url == "http://overlay" else config
    hs.refresh()
    return hs

def t_hud_roster_names_and_resolve():
    hs = _roster_hud()
    assert hs.roster_names() == ["OVO eSports", "Feel Good"]
    assert hs.resolve_team("OVO eSports") == {"name": "OVO eSports", "number": "111",
        "brandKey": "porsche", "brandName": "Porsche", "label": "OVO eSports",
        "bgColor": "", "textColor": "", "qualiLap": ""}
    # unknown -> stripped name, blank number/logo (embedded #9 used as fallback number)
    assert hs.resolve_team("Ghost #9") == {"name": "Ghost", "number": "9",
        "brandKey": "", "brandName": "", "label": "Ghost #9",
        "bgColor": "", "textColor": "", "qualiLap": ""}

def t_hud_team_override_echo_and_pending():
    hs = _roster_hud()
    entry = hs.resolve_team("Feel Good")
    hs.set_team_override(0, entry, now=1000.0)
    assert hs.data(now=1001.0)["teams"][0] == entry      # optimistic echo into slot 0
    assert hs.team_pending(now=1001.0) == {0}
    assert hs.team_pending(now=1000.0 + m.OVERRIDE_TTL + 1) == set()


def t_hud_team_override_cleared_when_sheet_confirms():
    import tempfile, os as _os
    d = tempfile.mkdtemp()
    hs = m.HudSource("http://overlay", "http://config", _os.path.join(d, "h.json"))
    config = "Teams,Number,Brand Name\nOVO eSports,111,Porsche\nFeel Good,303,BMW\n"
    # sheet starts with P1 = OVO eSports
    state = {"p1": "OVO eSports"}
    def fetch(url, timeout=10):
        if url == "http://overlay":
            return ",Teams P1," + state["p1"] + ",,\n,Teams P2,,,\n,Teams P3,,,\n"
        return config
    hs._fetch = fetch
    assert hs.refresh() is True
    # director optimistically switches P1 to Feel Good
    hs.set_team_override(0, hs.resolve_team("Feel Good"), now=1000.0)
    assert hs.team_pending(now=1001.0) == {0}
    # the sheet has NOT caught up yet -> a refresh keeps the override pending
    assert hs.refresh() is True
    assert hs.team_pending(now=1001.0) == {0}, "override must survive an unconfirmed refresh"
    # now the sheet shows Feel Good in P1 -> the next refresh confirms and clears it
    state["p1"] = "Feel Good"
    assert hs.refresh() is True
    assert hs.team_pending(now=1001.0) == set(), "confirmed override must be pruned"


def t_resolve_brand_override_direct_base():
    import tempfile, os as _os
    bd = tempfile.mkdtemp()
    with open(_os.path.join(bd, "cupra.png"), "w") as fh:
        fh.write("x")
    # brands_dir is the base DIRECTLY (no 'brands' sub-level, unlike resolve_asset)
    path, ctype = m.resolve_brand_override(bd, "cupra")
    assert path.endswith("cupra.png") and ctype == "image/png", (path, ctype)
    assert m.resolve_brand_override(bd, "bmw") is None        # not overridden here
    assert m.resolve_brand_override(bd, "../secret") is None   # traversal rejected
    assert m.resolve_brand_override("", "cupra") is None       # no dir
    assert m.resolve_brand_override(bd, "BadKey") is None       # bad key shape


def t_brand_override_wins_over_base():
    """The exact precedence expression _send_asset uses for sub=='brands'."""
    import tempfile, os as _os
    bd = tempfile.mkdtemp()                       # runtime override dir
    ad = tempfile.mkdtemp()                       # base assets dir (src/assets shape)
    _os.makedirs(_os.path.join(ad, "brands"))
    with open(_os.path.join(bd, "bmw.png"), "w") as fh:
        fh.write("override")
    with open(_os.path.join(ad, "brands", "bmw.png"), "w") as fh:
        fh.write("base")
    hit = m.resolve_brand_override(bd, "bmw") or m.resolve_asset(ad, "brands", "bmw")
    assert hit[0].startswith(_os.path.realpath(bd)), hit          # override path wins
    # a key present only in the base still resolves through the fallback
    with open(_os.path.join(ad, "brands", "audi.png"), "w") as fh:
        fh.write("base")
    hit2 = m.resolve_brand_override(bd, "audi") or m.resolve_asset(ad, "brands", "audi")
    assert hit2[0].endswith("audi.png") and hit2[0].startswith(_os.path.realpath(ad)), hit2


def _get_route(logo_path="", path="/hud"):
    """make_handler over a real ThreadingHTTPServer, wired only with logo_path.
    GETs one path and returns a (status, ctype, body) result."""
    import threading as _t
    import urllib.error
    from urllib.request import urlopen
    from collections import namedtuple

    class _StubFeed:
        def __init__(self, idx):
            self.idx = idx

    class _StubRelay:
        def __init__(self):
            self.feeds = {"A": _StubFeed(0), "B": _StubFeed(1)}

    handler = m.make_handler(_StubRelay(), logo_path=logo_path)
    srv = m.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    _t.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    Result = namedtuple("Result", "status ctype body")
    try:
        with urlopen(base + path, timeout=5) as r:
            return Result(r.status, r.headers.get("Content-Type", ""), r.read())
    except urllib.error.HTTPError as e:
        return Result(e.code, e.headers.get("Content-Type", ""), e.read())
    finally:
        srv.shutdown()


def t_hud_logo_route_serves_image_and_404s():
    import tempfile, os
    # A relay handler built with a real logo file serves it at /hud/logo;
    # built with no logo, /hud/logo is 404.
    with tempfile.TemporaryDirectory() as d:
        png = os.path.join(d, "logo.png")
        with open(png, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")            # minimal PNG signature
        got = _get_route(logo_path=png, path="/hud/logo")
        assert got.status == 200
        assert got.ctype.startswith("image/")
        assert got.body.startswith(b"\x89PNG")
        none = _get_route(logo_path="", path="/hud/logo")
        assert none.status == 404


# Brand tile colours and quali times (#555).

def t_sanitize_css_color_accepts_plausible_values():
    assert m.sanitize_css_color("#C00000") == "#C00000"
    assert m.sanitize_css_color("  #fff ") == "#fff"
    assert m.sanitize_css_color("#12345678") == "#12345678"
    assert m.sanitize_css_color("rgb(200, 0, 0)") == "rgb(200, 0, 0)"
    assert m.sanitize_css_color("rgba(200,0,0,.5)") == "rgba(200,0,0,.5)"
    assert m.sanitize_css_color("white") == "white"


def t_sanitize_css_color_accepts_hsl():
    # The docs promise "any plain CSS colour", so the hsl()/hsla() family must pass
    # too, including the space-separated form and an angle unit on the hue.
    assert m.sanitize_css_color("hsl(0,0%,0%)") == "hsl(0,0%,0%)"
    assert m.sanitize_css_color(" hsl(210 90% 45%) ") == "hsl(210 90% 45%)"
    assert m.sanitize_css_color("hsl(120deg 50% 50%)") == "hsl(120deg 50% 50%)"
    assert m.sanitize_css_color("hsla(0, 0%, 0%, .5)") == "hsla(0, 0%, 0%, .5)"


def t_sanitize_css_color_rejects_everything_else():
    # The gate that keeps a sheet cell from smuggling a resource fetch into the
    # custom property the HUD sets on its slots.
    assert m.sanitize_css_color("url(http://x/y.png)") == ""
    assert m.sanitize_css_color("red; background: url(http://x)") == ""
    assert m.sanitize_css_color("var(--x)") == ""
    assert m.sanitize_css_color("#12345") == ""       # not 3/4/6/8 hex digits
    assert m.sanitize_css_color("") == ""
    assert m.sanitize_css_color(None) == ""
    # An hsl-shaped value may not smuggle anything either: no second declaration,
    # no nested function, no url(). Widening the regex to hsl() must not widen the
    # security property.
    assert m.sanitize_css_color("hsl(0,0%,0%); background: url(http://x)") == ""
    assert m.sanitize_css_color("hsl(0,0%,0%) url(http://x)") == ""
    assert m.sanitize_css_color("hsl(var(--x))") == ""
    assert m.sanitize_css_color('hsl(0,0%,0%)"') == ""


def t_normalize_quali_lap_verbatim_and_fixes():
    assert m.normalize_quali_lap("1:38.973") == "1:38.973"      # verbatim
    assert m.normalize_quali_lap(" 1:38,973 ") == "1:38.973"    # comma -> dot
    assert m.normalize_quali_lap("0:01:38,973") == "1:38.973"   # sheets duration
    assert m.normalize_quali_lap("00:01:38.973") == "1:38.973"
    assert m.normalize_quali_lap("0:1:38.973") == "1:38.973"
    assert m.normalize_quali_lap("98.973") == "98.973"          # no conversion
    assert m.normalize_quali_lap("1:02:03.400") == "1:02:03.400"  # non-zero hour kept
    assert m.normalize_quali_lap("") == ""
    assert m.normalize_quali_lap(None) == ""


QUALI_CSV = (
    "Team,Best Lap\n"
    "Tavernello Racing #6,1:38.973\n"
    # comma decimal, no embedded number; a real Sheets CSV export quotes a cell
    # whose value itself contains a comma.
    "N3XUS Racing,\"1:39,104\"\n"
    "Trrack Design Racing #51,0:01:40.512\n"   # sheets duration formatting
    ",1:00.000\n"                      # no team -> skipped
    "Ghost Racing,\n"                  # no lap -> skipped
)


def t_parse_quali_times_keys_by_verbatim_and_stripped_name():
    # Two keys per row: the verbatim cell, which is the per-car identity, and the
    # stripped team name, so a sheet that writes only the bare name still matches
    # every car of that team.
    q = m.parse_quali_times(QUALI_CSV)
    assert q == {"tavernello-racing-6": "1:38.973",
                 "tavernello-racing": "1:38.973",
                 "n3xus-racing": "1:39.104",
                 "trrack-design-racing-51": "1:40.512",
                 "trrack-design-racing": "1:40.512"}, q


def t_parse_quali_times_tolerates_missing_pieces():
    assert m.parse_quali_times("") == {}
    assert m.parse_quali_times("Team,Something\nX,1:2.3\n") == {}   # no lap header
    assert m.parse_quali_times("Foo,Best Lap\nX,1:2.3\n") == {}     # no team header
    assert m.parse_quali_times("Team,Best Lap\n") == {}             # header only
    # A Configuration CSV pointed at this parser yields nothing rather than
    # garbage, because it has no 'Best Lap' column.
    assert m.parse_quali_times(CONFIG_CSV) == {}


def t_quali_lap_headers_are_narrow():
    # A bare 'Quali'/'Lap' column header is broad enough to match an unrelated
    # column and turn plausible-looking garbage into an on-air lap time, so only
    # the explicit 'Best Lap' spellings (+ 'Quali Time') are accepted.
    assert "quali" not in m.QUALI_LAP_HEADERS
    assert "lap" not in m.QUALI_LAP_HEADERS
    assert "best lap" in m.QUALI_LAP_HEADERS
    assert m.parse_quali_times("Team,Lap\nA Team,1:38.000\n") == {}
    assert m.parse_quali_times("Team,Quali\nA Team,1:38.000\n") == {}
    assert m.parse_quali_times("Team,Quali Time\nA Team,1:38.000\n") == {
        "a-team": "1:38.000"}


def t_parse_quali_times_two_cars_of_one_team_keep_their_own_lap():
    # A team fielding two cars is two entries, like the roster: the stripped name is
    # not a unique identity, so neither car may inherit the other's lap.
    q = m.parse_quali_times("Team,Best Lap\nA Team #14,1:38.100\nA Team #54,1:39.900\n")
    assert q["a-team-14"] == "1:38.100", q
    assert q["a-team-54"] == "1:39.900", q
    assert m.team_entry("A Team #14", {}, q)["qualiLap"] == "1:38.100"
    assert m.team_entry("A Team #54", {}, q)["qualiLap"] == "1:39.900"


def t_parse_quali_times_bare_row_still_matches_every_car():
    # A sheet that writes only the bare team name matches whichever car of that
    # team is on the podium.
    q = m.parse_quali_times("Team,Best Lap\nA Team,1:38.000\n")
    assert m.team_entry("A Team", {}, q)["qualiLap"] == "1:38.000"
    assert m.team_entry("A Team #14", {}, q)["qualiLap"] == "1:38.000"
    assert m.team_entry("A Team #54", {}, q)["qualiLap"] == "1:38.000"


def t_parse_quali_times_duplicate_row_first_wins():
    # The genuine duplicate case: the same car twice -> the first row wins.
    q = m.parse_quali_times("Team,Best Lap\nA Team #14,1:38.100\nA Team #14,1:40.000\n")
    assert m.team_entry("A Team #14", {}, q)["qualiLap"] == "1:38.100", q


def t_parse_quali_times_generic_row_never_shadows_a_specific_one():
    # A bare team row next to a per-car row, in both sheet orders: the per-car row
    # always wins for that car. The lookup tries the verbatim key first, and each
    # key is setdefault'ed so a later generic row cannot overwrite a specific one.
    for text in ("Team,Best Lap\nA Team,1:38.000\nA Team #54,1:39.900\n",
                 "Team,Best Lap\nA Team #54,1:39.900\nA Team,1:38.000\n"):
        q = m.parse_quali_times(text)
        assert m.team_entry("A Team #54", {}, q)["qualiLap"] == "1:39.900", text


CONFIG_CSV_COLORS = (
    "Teams,Number,Brand Name,BG Color,Text Color\n"
    "OVO eSports,111,Porsche,#FFFFFF,#111111\n"
    # rgb(...) has an internal comma, so a real Sheets CSV export quotes the cell.
    "Feel Good,303,BMW,\"rgb(0,80,160)\",white\n"
    "Ghost,7,Audi,url(http://evil/x.png),#00FF00\n"   # rejected -> blank bg
)


def t_parse_config_roster_reads_tile_colors():
    r = m.parse_config_roster(CONFIG_CSV_COLORS)
    assert r["OVO eSports"]["bgColor"] == "#FFFFFF"
    assert r["OVO eSports"]["textColor"] == "#111111"
    assert r["Feel Good"]["bgColor"] == "rgb(0,80,160)"
    assert r["Feel Good"]["textColor"] == "white"
    # implausible value is dropped, the rest of the row survives
    assert r["Ghost"]["bgColor"] == ""
    assert r["Ghost"]["textColor"] == "#00FF00"
    assert r["Ghost"]["brandKey"] == "audi"


def t_parse_config_roster_colors_blank_without_columns():
    # Every existing league has no colour columns -> keys present, values blank.
    r = m.parse_config_roster(CONFIG_CSV)
    assert r["OVO eSports #111"]["bgColor"] == ""
    assert r["OVO eSports #111"]["textColor"] == ""


def t_team_entry_joins_colors_and_quali_lap():
    roster = m.parse_config_roster(CONFIG_CSV_COLORS)
    quali = m.parse_quali_times("Team,Best Lap\nOVO eSports,1:38.973\n")
    e = m.team_entry("OVO eSports", roster, quali)
    assert e == {"name": "OVO eSports", "number": "111", "brandKey": "porsche",
                 "brandName": "Porsche", "label": "OVO eSports",
                 "bgColor": "#FFFFFF", "textColor": "#111111",
                 "qualiLap": "1:38.973"}, e


def t_team_entry_quali_lap_matches_across_number_variants():
    # The slot value carries '#111' and the Quali Times row does not, or the other
    # way round, so both resolve through asset_key of the stripped name.
    roster = m.parse_config_roster(CONFIG_CSV)
    quali = m.parse_quali_times("Team,Best Lap\nOVO eSports,1:38.973\n")
    assert m.team_entry("OVO eSports #111", roster, quali)["qualiLap"] == "1:38.973"
    quali2 = m.parse_quali_times("Team,Best Lap\nOVO eSports #111,1:38.973\n")
    assert m.team_entry("OVO eSports #111", roster, quali2)["qualiLap"] == "1:38.973"


def t_team_entry_without_quali_map_is_blank():
    roster = m.parse_config_roster(CONFIG_CSV)
    e = m.team_entry("OVO eSports #111", roster)          # no quali argument
    assert e["qualiLap"] == "" and e["bgColor"] == "" and e["textColor"] == ""


def t_build_hud_data_carries_colors_and_quali():
    overlay = m.parse_overlay(OVERLAY_CSV)
    roster = m.parse_config_roster(CONFIG_CSV_COLORS)
    quali = m.parse_quali_times("Team,Best Lap\nOVO eSports,1:38.973\n")
    d = m.build_hud_data(overlay, roster, quali)
    # OVERLAY_CSV puts 'OVO eSports #111' in P1; the roster here is keyed bare.
    assert d["teams"][0]["qualiLap"] == "1:38.973"
    assert d["teams"][0]["bgColor"] == "#FFFFFF"
    # a team with no quali row keeps a blank slot (the HUD hides it)
    assert d["teams"][2]["qualiLap"] == ""


def _quali_hud(quali_text=None, quali_boom=False):
    """A HudSource with all three tabs stubbed. quali_boom simulates the tab not
    existing, the state of a league that never created it. Every fetched URL is
    recorded on hs.seen, so a test can assert which tabs a call touched."""
    import tempfile, os as _os
    d = tempfile.mkdtemp()
    hs = m.HudSource("http://overlay", "http://config",
                     _os.path.join(d, "hud.cache.json"), quali_url="http://quali")
    hs.seen = []
    def fetch(url, timeout=10):
        hs.seen.append(url)
        if url == "http://overlay":
            return OVERLAY_CSV
        if url == "http://quali":
            if quali_boom:
                raise RuntimeError("no such sheet tab")
            return quali_text or ""
        return CONFIG_CSV
    hs._fetch = fetch
    return hs


def _muted(fn):
    """Run fn() with the relay logger's warning muted, so a test that provokes the
    quali-fetch warning does not pollute the suite output."""
    orig = m.LOG.warning
    m.LOG.warning = lambda *a, **k: None
    try:
        return fn()
    finally:
        m.LOG.warning = orig


def t_hudsource_reads_quali_times():
    hs = _quali_hud("Team,Best Lap\nOVO eSports,1:38.973\n")
    assert hs.refresh_quali() is True
    assert hs.quali_times() == {"ovo-esports": "1:38.973"}
    assert hs.refresh() is True
    assert hs.data()["teams"][0]["qualiLap"] == "1:38.973"


def t_hudsource_refresh_fetches_exactly_overlay_and_config():
    # The on-air performance guarantee: no quali-tab state, missing, present or
    # unreachable, may add a round trip to the 5 s HUD refresh or to the synchronous
    # panel-push confirm. refresh() touches two tabs, always.
    hs = _quali_hud("Team,Best Lap\nOVO eSports,1:38.973\n")   # tab exists + works
    assert hs.refresh() is True
    assert hs.seen == ["http://overlay", "http://config"], hs.seen
    hs.seen.clear()
    assert hs.refresh() is True                                # steady state
    assert hs.seen == ["http://overlay", "http://config"], hs.seen

    import tempfile, os as _os
    d = tempfile.mkdtemp()
    plain = m.HudSource("http://overlay", "http://config",
                        _os.path.join(d, "hud.cache.json"))    # no quali_url at all
    seen = []
    def fetch(url, timeout=10):
        seen.append(url)
        return OVERLAY_CSV if url == "http://overlay" else CONFIG_CSV
    plain._fetch = fetch
    assert plain.refresh() is True
    assert seen == ["http://overlay", "http://config"], seen


def t_hudsource_refresh_unaffected_by_an_unreachable_quali_tab():
    # A league without the tab: refresh() never touches it, so it cannot fail or
    # stall on it, and refresh_quali() reports the failure without raising.
    hs = _quali_hud(quali_boom=True)
    assert hs.refresh() is True
    assert hs.seen == ["http://overlay", "http://config"], hs.seen
    assert hs.data()["streamer"] == "JeGr"
    assert hs.data()["teams"][0]["qualiLap"] == ""
    assert _muted(hs.refresh_quali) is False
    assert hs.quali_times() == {}


def t_hudsource_refresh_quali_is_a_noop_without_a_url():
    import tempfile, os as _os
    d = tempfile.mkdtemp()
    hs = m.HudSource("http://overlay", "http://config",
                     _os.path.join(d, "hud.cache.json"))     # no quali_url
    seen = []
    hs._fetch = lambda url, timeout=10: seen.append(url) or ""
    assert hs.refresh_quali() is False
    assert seen == [], seen


def t_hudsource_quali_times_preserved_on_overlay_failure():
    hs = _quali_hud("Team,Best Lap\nOVO eSports,1:38.973\n")
    assert hs.refresh_quali() is True
    assert hs.refresh() is True
    def boom(url, timeout=10):
        raise RuntimeError("sheet down")
    hs._fetch = boom
    assert hs.refresh() is False
    assert hs.quali_times() == {"ovo-esports": "1:38.973"}   # last-good kept


def t_hudsource_quali_times_preserved_on_quali_failure():
    # A transient quali fetch failure keeps the last-good map and is never rolled
    # back to empty, so the laps keep showing.
    hs = _quali_hud("Team,Best Lap\nOVO eSports,1:38.973\n")
    assert hs.refresh_quali() is True
    hs._fetch = lambda url, timeout=10: (_ for _ in ()).throw(RuntimeError("blip"))
    assert _muted(hs.refresh_quali) is False
    assert hs.quali_times() == {"ovo-esports": "1:38.973"}


def t_hudsource_refresh_is_total_even_if_the_build_raises():
    # refresh() runs in the HUD poll thread, which calls it bare, so an escaping
    # exception would kill the poll for the rest of the relay run and freeze the
    # on-air overlay with no log. Any failure must become last_error plus False,
    # including one from the data build, which sits after the fetches.
    hs = _quali_hud()
    orig = m.build_hud_data
    m.build_hud_data = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        assert hs.refresh() is False
        assert "boom" in (hs.last_error or ""), hs.last_error
    finally:
        m.build_hud_data = orig


def t_hudsource_empty_and_resolve_team_carry_new_keys():
    # resolve_team feeds the panel's 30 s optimistic echo. If it omitted the new
    # keys, an approved team switch would flash a colourless, quali-less tile.
    keys = {"name", "number", "brandKey", "brandName", "label",
            "bgColor", "textColor", "qualiLap"}
    assert set(m.HudSource.EMPTY["teams"][0]) == keys
    hs = _quali_hud("Team,Best Lap\nFeel Good,1:39.104\n")
    hs.refresh_quali()
    hs.refresh()
    e = hs.resolve_team("Feel Good")
    assert set(e) == keys, e
    assert e["qualiLap"] == "1:39.104", e


def t_resolve_team_gets_the_right_car_of_a_two_car_team():
    # resolve_team delegates to team_entry, so it inherits the per-car lookup and
    # the panel's optimistic echo shows the lap of the car it just put on air.
    hs = _quali_hud("Team,Best Lap\nGhost Racing #14,1:38.100\n"
                    "Ghost Racing #54,1:39.900\n")
    assert hs.refresh_quali() is True
    hs.refresh()
    assert hs.resolve_team("Ghost Racing #14")["qualiLap"] == "1:38.100"
    assert hs.resolve_team("Ghost Racing #54")["qualiLap"] == "1:39.900"


def t_hudsource_team_override_padding_shape():
    # An override on slot 2 with fewer than 3 sheet teams pads the list; the pad
    # entries must carry the same key set as a real one. A live refresh() always
    # yields exactly 3 team slots, because parse_overlay pre-fills ["", "", ""]
    # before reading any row, so the only way data() sees fewer than 3 cached teams
    # is a short on-disk hud.cache.json. Setting _data directly is what exercises
    # the `while len(teams) < 3` pad loop.
    import tempfile, os as _os
    d = tempfile.mkdtemp()
    hs = m.HudSource("http://overlay", "http://config",
                     _os.path.join(d, "hud.cache.json"))
    hs._data = {"stint": "", "streamer": "", "session": "",
                "round": {"top": "", "country": "", "flagKey": ""},
                "teams": [dict(m.EMPTY_TEAM_ENTRY)],   # only ONE cached slot
                "raceControl": "", "flag": ""}
    hs.set_team_override(2, hs.resolve_team("Ghost #9"), now=1000.0)
    teams = hs.data(now=1001.0)["teams"]
    assert len(teams) == 3, teams   # padded up from 1 to 3
    for t in teams:
        assert set(t) == set(m.EMPTY_TEAM_ENTRY), t


def t_hudsource_quali_warning_logs_once_then_resets_on_success():
    # The "log once per relay run" gate: a repeat failure of the same kind stays
    # silent after the first warning, and a success in between clears the gate so a
    # later, different failure warns again.
    import tempfile, os as _os
    d = tempfile.mkdtemp()
    hs = m.HudSource("http://overlay", "http://config",
                     _os.path.join(d, "hud.cache.json"), quali_url="http://quali")
    state = {"boom": True}
    def fetch(url, timeout=10):
        if url == "http://overlay":
            return OVERLAY_CSV
        if url == "http://quali":
            if state["boom"]:
                raise RuntimeError("no such sheet tab")
            return "Team,Best Lap\nOVO eSports,1:38.973\n"
        return CONFIG_CSV
    hs._fetch = fetch

    calls = []
    orig_warning = m.LOG.warning
    m.LOG.warning = lambda *a, **k: calls.append(a)
    try:
        assert hs.refresh_quali() is False
        assert len(calls) == 1, "first failure must warn"
        assert hs.refresh_quali() is False
        assert len(calls) == 1, "a repeat failure of the same kind must stay silent"
        state["boom"] = False
        assert hs.refresh_quali() is True   # success resets the gate
        assert hs.quali_times() == {"ovo-esports": "1:38.973"}
        state["boom"] = True
        assert hs.refresh_quali() is False
        assert len(calls) == 2, "a NEW failure after a success must warn again"
        # The warning must not claim the laps blank, because a transient failure
        # keeps showing the last known times.
        msg = calls[0][0]
        assert "stay blank" not in msg, msg
        assert "last known" in msg, msg
    finally:
        m.LOG.warning = orig_warning


def t_quali_times_tab_is_its_own_tab():
    # Its own sheet tab, never the qualifying schedule tab, which owns 'Qualifying'.
    assert m.DEFAULT_QUALI_TIMES_TAB == "Quali Times"
    assert m.DEFAULT_QUALI_TIMES_TAB != m.DEFAULT_QUALIFYING_TAB


class _CountdownEvent:
    """A stop_evt stand-in for the poll loops: wait() returns False n times, then
    True, so a loop runs exactly n iterations with no sleep."""

    def __init__(self, n):
        self.n = n
        self.waits = []

    def wait(self, timeout=None):
        self.waits.append(timeout)
        self.n -= 1
        return self.n < 0


def t_quali_poll_interval_is_slow():
    # Quali times are entered once between qualifying and the race, so they need no
    # 5 s freshness; the separate thread is what keeps them off the HUD refresh.
    assert m.QUALI_TIMES_POLL_S >= 60


def t_quali_poller_polls_on_its_own_cadence_and_survives_a_raise():
    class _Src:
        def __init__(self):
            self.calls = 0

        def refresh_quali(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("unexpected")
            return True

    src, evt = _Src(), _CountdownEvent(3)
    orig_warning = m.LOG.warning
    m.LOG.warning = lambda *a, **k: None
    try:
        m.quali_poller(src, m.QUALI_TIMES_POLL_S, evt)
    finally:
        m.LOG.warning = orig_warning
    assert src.calls == 3, src.calls          # a raise did not kill the loop
    assert evt.waits == [m.QUALI_TIMES_POLL_S] * 4, evt.waits


def t_poller_survives_a_raising_refresh():
    # The HUD and schedule poll threads call refresh() bare, so a raise would kill
    # the thread silently for the rest of the relay run.
    class _Boom:
        def __init__(self):
            self.calls = 0

        def refresh(self):
            self.calls += 1
            raise RuntimeError("kaboom")

    src, evt = _Boom(), _CountdownEvent(3)
    calls = []
    orig_warning = m.LOG.warning
    m.LOG.warning = lambda *a, **k: calls.append(a)
    try:
        m.poller(src, 5, evt)
    finally:
        m.LOG.warning = orig_warning
    assert src.calls == 3, src.calls
    assert len(calls) == 3, calls              # and it is never silent


def _hud_data_route(mode="race", quali_text=None):
    """GET /hud/data off a real make_handler server with a stubbed relay and a
    stubbed HudSource."""
    import json as _json, threading as _t
    from urllib.request import urlopen

    class _StubRelay:
        def __init__(self):
            self.mode = mode

        def pov_active(self):
            return False

        def pov_name(self):
            return ""

    hs = _quali_hud(quali_text)
    hs.refresh_quali()
    hs.refresh()
    handler = m.make_handler(_StubRelay(), hud_source=hs)
    srv = m.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    _t.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urlopen(f"http://127.0.0.1:{srv.server_address[1]}/hud/data",
                     timeout=5) as r:
            return _json.loads(r.read().decode("utf-8"))
    finally:
        srv.shutdown()


def t_hud_data_route_carries_the_relay_mode():
    # The one line that makes body[data-mode] work; it lives in the route, not in
    # a pure function, so it needs the live-handler check.
    assert _hud_data_route("qualifying")["mode"] == "qualifying"
    assert _hud_data_route("race")["mode"] == "race"


def t_hud_data_route_carries_the_quali_lap():
    d = _hud_data_route("race", "Team,Best Lap\nOVO eSports,1:38.973\n")
    assert d["teams"][0]["qualiLap"] == "1:38.973", d["teams"][0]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
