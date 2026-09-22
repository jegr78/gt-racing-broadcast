#!/usr/bin/env python3
"""Stdlib unit checks for the director text-cue channel. Run: python3 tests/test_cues.py"""
import importlib.util
import os
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


cu = _load("cue_admin", ("src", "scripts", "cue_admin.py"))


def t_sanitize_cue_basic():
    c = cu.sanitize_cue({"id": 1, "ts": 100.0, "target": "max", "level": "info",
                         "text": "wrap up", "from": "Director", "ack": None})
    assert c == {"id": 1, "ts": 100.0, "target": "max", "level": "info",
                 "text": "wrap up", "from": "Director", "ack": None}


def t_sanitize_cue_caps_and_strips():
    c = cu.sanitize_cue({"id": 2, "ts": 1.0, "target": "all", "level": "critical",
                         "text": "x" * 500, "from": "y" * 80})
    assert len(c["text"]) == cu.MAX_CUE_TEXT
    assert len(c["from"]) == cu.MAX_NAME


def t_sanitize_cue_folds_control_chars():
    c = cu.sanitize_cue({"id": 3, "ts": 1.0, "target": "max", "level": "info",
                         "text": "go\x07 now\nplease"})
    assert c["text"] == "go now please"
    assert c["from"] == "Director"          # blank -> default label


def t_sanitize_cue_rejects_bad():
    for bad in ({"id": 1, "ts": 1.0, "target": "max", "level": "loud", "text": "x"},
                {"id": 1, "ts": 1.0, "target": "", "level": "info", "text": "x"},
                {"id": 1, "ts": 1.0, "target": "max", "level": "info", "text": "   "},
                {"id": 0, "ts": 1.0, "target": "max", "level": "info", "text": "x"},
                {"id": True, "ts": 1.0, "target": "max", "level": "info", "text": "x"},
                {"id": 1, "ts": "x", "target": "max", "level": "info", "text": "x"}):
        assert cu.sanitize_cue(bad) is None, bad


def t_sanitize_cue_ack_shape():
    c = cu.sanitize_cue({"id": 1, "ts": 1.0, "target": "max", "level": "critical",
                         "text": "hot", "ack": {"ts": 9.0, "junk": 1}})
    assert c["ack"] == {"ts": 9.0}
    c2 = cu.sanitize_cue({"id": 1, "ts": 1.0, "target": "max", "level": "critical",
                          "text": "hot", "ack": {"nope": 1}})
    assert c2["ack"] is None


def t_resolve_target_all_and_key():
    norm = lambda s: s.strip().lower().replace(" ", "-")
    assert cu.resolve_target("all", None, norm) == "all"
    assert cu.resolve_target("Max Power", None, norm) == "max-power"
    assert cu.resolve_target("  ", None, norm) is None


def t_resolve_target_on_air():
    norm = lambda s: s.strip().lower()
    assert cu.resolve_target("on-air", "jegr", norm) == "jegr"
    assert cu.resolve_target("on-air", None, norm) is None   # nobody on air


def t_active_cues_info_ttl():
    cues = [{"id": 1, "ts": 100.0, "target": "max", "level": "info",
             "text": "hi", "from": "Director", "ack": None}]
    assert cu.active_cues_for(cues, "max", 120.0, info_ttl=30) == cues   # within TTL
    assert cu.active_cues_for(cues, "max", 130.0, info_ttl=30) == []     # at boundary: expired
    assert cu.active_cues_for(cues, "max", 131.0, info_ttl=30) == []     # expired


def t_active_cues_critical_sticky_until_ack():
    base = {"id": 1, "ts": 1.0, "target": "max", "level": "critical",
            "text": "hot", "from": "Director", "ack": None}
    assert cu.active_cues_for([base], "max", 1e9) == [base]              # sticky forever
    acked = dict(base, ack={"ts": 5.0})
    assert cu.active_cues_for([acked], "max", 1e9) == []                 # acked -> gone


def t_active_cues_target_scope():
    cues = [{"id": 1, "ts": 1.0, "target": "max", "level": "critical", "text": "a",
             "from": "Director", "ack": None},
            {"id": 2, "ts": 1.0, "target": "all", "level": "critical", "text": "b",
             "from": "Director", "ack": None}]
    got = cu.active_cues_for(cues, "ann", 1e9)
    assert [c["id"] for c in got] == [2]          # ann sees only the "all" cue, not max's


def t_prune_drops_stale_keeps_active():
    cues = [{"id": 1, "ts": 1.0, "target": "max", "level": "info", "text": "old",
             "from": "Director", "ack": None},                      # expired info
            {"id": 2, "ts": 1.0, "target": "max", "level": "critical", "text": "ack'd",
             "from": "Director", "ack": {"ts": 2.0}},                # acked critical
            {"id": 3, "ts": 1.0, "target": "max", "level": "critical", "text": "live",
             "from": "Director", "ack": None}]                      # still active
    kept = cu.prune(cues, now=1000.0, info_ttl=30)
    assert [c["id"] for c in kept] == [3]


def t_validate_payload_sorts_and_caps():
    payload = {"cues": [
        {"id": 2, "ts": 2.0, "target": "max", "level": "info", "text": "b"},
        {"id": 1, "ts": 1.0, "target": "max", "level": "info", "text": "a"},
        {"id": 3, "ts": 3.0, "target": "max", "level": "bogus", "text": "drop me"}]}
    clean = cu.validate_payload(payload)
    assert [c["id"] for c in clean] == [1, 2]        # sorted by id, bad entry dropped


def t_validate_payload_rejects_shape():
    for bad in ({}, {"cues": "nope"}, []):
        try:
            cu.validate_payload(bad); raise AssertionError(bad)
        except ValueError:
            pass


def t_write_load_round_trip():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "sub", "cues.json")
        cu.write_cues(path, [{"id": 1, "ts": 1.0, "target": "max", "level": "info",
                              "text": "hi", "from": "Director", "ack": None}])
        assert cu.load_cues(path) == [{"id": 1, "ts": 1.0, "target": "max",
                                       "level": "info", "text": "hi",
                                       "from": "Director", "ack": None}]


def t_load_missing_is_empty():
    with tempfile.TemporaryDirectory() as d:
        assert cu.load_cues(os.path.join(d, "nope.json")) == []


def t_apply_pulled_prunes_and_writes():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "cues.json")
        payload = {"cues": [
            {"id": 1, "ts": 1.0, "target": "max", "level": "info", "text": "stale"},
            {"id": 2, "ts": 1.0, "target": "max", "level": "critical", "text": "live"}]}
        n = cu.apply_pulled(path, payload, now=1000.0, info_ttl=30)
        assert n == 1                                  # the expired info pruned out
        assert [c["id"] for c in cu.load_cues(path)] == [2]


_relay = _load("irofeeds", ("src", "relay", "racecast-feeds.py"))


def t_parse_cue_presets_by_header():
    csv_text = ("Stints,Streamers,Cue Preset\n"
                "Stint 1,JeGr,Wrap up\n"
                "Stint 2,Ann,Throw to pit\n"
                ",,Wrap up\n")                 # duplicate dropped, blanks skipped
    assert _relay.parse_cue_presets(csv_text) == ["Wrap up", "Throw to pit"]


def t_parse_cue_presets_absent_column():
    assert _relay.parse_cue_presets("Stints,Streamers\nStint 1,JeGr\n") == []


def t_cuestore_add_list_ack_round_trip():
    with tempfile.TemporaryDirectory() as d:
        store = _relay.CueStore(os.path.join(d, "cues.json"))
        r = store.add(target="max", level="critical", text="hot", now=100.0)
        assert r["ok"] and r["cue"]["id"] == 1 and r["cue"]["from"] == "Director"
        # a foreign commentator cannot ack max's cue
        assert "error" in store.ack(1, "ann", now=101.0)
        assert store.list()[0]["ack"] is None
        # the addressee can
        assert store.ack(1, "max", now=102.0)["ok"] is True
        assert store.list()[0]["ack"] == {"ts": 102.0}


def t_cuestore_rejects_bad_level():
    with tempfile.TemporaryDirectory() as d:
        store = _relay.CueStore(os.path.join(d, "cues.json"))
        assert "error" in store.add(target="max", level="loud", text="x")


# Race Control -> commentator notes. (#376)
# RC notes ride the same cue store but carry origin="race_control"; a director cue
# keeps the exact 7-key shape (no origin key) so the on-disk shape is unchanged.

def t_sanitize_cue_origin_director_default_absent():
    c = cu.sanitize_cue({"id": 1, "ts": 1.0, "target": "max", "level": "info", "text": "x"})
    assert "origin" not in c                       # director cue: shape unchanged
    bad = cu.sanitize_cue({"id": 1, "ts": 1.0, "target": "max", "level": "info",
                           "text": "x", "origin": "bogus"})
    assert "origin" not in bad                      # unknown origin -> dropped


def t_sanitize_cue_origin_race_control_preserved():
    c = cu.sanitize_cue({"id": 5, "ts": 1.0, "target": "max", "level": "info",
                         "text": "team 5 retired", "origin": "race_control"})
    assert c["origin"] == "race_control"


def t_active_cues_excludes_race_control():
    # An RC note must NEVER surface as a director toast/banner.
    cues = [{"id": 1, "ts": 1.0, "target": "max", "level": "info", "text": "rc",
             "from": "Race Control", "ack": None, "origin": "race_control"},
            {"id": 2, "ts": 1.0, "target": "max", "level": "info", "text": "dir",
             "from": "Director", "ack": None}]
    got = cu.active_cues_for(cues, "max", 1.0, info_ttl=30)
    assert [c["id"] for c in got] == [2]


def t_prune_keeps_race_control_past_ttl():
    cues = [{"id": 1, "ts": 1.0, "target": "max", "level": "info", "text": "old rc",
             "from": "Race Control", "ack": None, "origin": "race_control"}]
    kept = cu.prune(cues, now=1e9, info_ttl=30)        # long past the info TTL
    assert [c["id"] for c in kept] == [1]               # RC note survives


def t_prune_caps_race_control_notes():
    n = cu.RC_NOTE_KEEP + 4
    cues = [{"id": i, "ts": 1.0, "target": "max", "level": "info", "text": str(i),
             "from": "Race Control", "ack": None, "origin": "race_control"}
            for i in range(1, n + 1)]
    kept = cu.prune(cues, now=1e9, info_ttl=30)
    assert len(kept) == cu.RC_NOTE_KEEP
    assert kept[-1]["id"] == n                          # newest retained, order preserved


def t_race_control_notes_for_target_scope_and_limit():
    cues = [{"id": 1, "ts": 1.0, "target": "max", "level": "info", "text": "a",
             "from": "Race Control", "ack": None, "origin": "race_control"},
            {"id": 2, "ts": 2.0, "target": "all", "level": "info", "text": "b",
             "from": "Race Control", "ack": None, "origin": "race_control"},
            {"id": 3, "ts": 3.0, "target": "ann", "level": "info", "text": "c",
             "from": "Race Control", "ack": None, "origin": "race_control"},
            {"id": 4, "ts": 4.0, "target": "max", "level": "info", "text": "d",
             "from": "Director", "ack": None}]      # director cue, not an RC note
    got = cu.race_control_notes_for(cues, "max")
    assert [c["id"] for c in got] == [1, 2]         # max + all, not ann, not director


def t_race_control_notes_for_show_cap():
    cues = [{"id": i, "ts": float(i), "target": "all", "level": "info", "text": str(i),
             "from": "Race Control", "ack": None, "origin": "race_control"}
            for i in range(1, 12)]
    got = cu.race_control_notes_for(cues, "max")
    assert len(got) == cu.RC_NOTE_SHOW
    assert got[-1]["id"] == 11                      # most-recent window


def t_cuestore_race_control_note_round_trip():
    with tempfile.TemporaryDirectory() as d:
        store = _relay.CueStore(os.path.join(d, "cues.json"))
        r = store.add(target="max", level="info", text="team DC — rejoin next stint",
                      from_name=cu.RACE_CONTROL_FROM, origin="race_control", now=100.0)
        assert r["ok"] and r["cue"]["origin"] == "race_control"
        assert r["cue"]["from"] == "Race Control"
        # never a director toast, always an RC note:
        assert cu.active_cues_for(store.list(), "max", 100.0) == []
        assert [c["id"] for c in cu.race_control_notes_for(store.list(), "max")] == [1]


def t_parse_rc_note_presets_by_header():
    csv_text = ("Stints,Streamers,RC Note\n"
                "Stint 1,JeGr,Jump to leader\n"
                "Stint 2,Ann,Team DC — rejoin next stint\n"
                ",,Jump to leader\n")          # duplicate dropped, blanks skipped
    assert _relay.parse_rc_note_presets(csv_text) == ["Jump to leader",
                                                       "Team DC — rejoin next stint"]


def t_parse_rc_note_presets_absent_column():
    assert _relay.parse_rc_note_presets("Stints,Streamers\nStint 1,JeGr\n") == []


# Commentator -> director cue-back. (#377)
# The reverse direction: origin="commentator", shown only on the Director Panel.

def t_active_cues_excludes_every_non_director_origin():
    # Director toasts/banners show ONLY plain director cues (no origin key).
    cues = [{"id": 1, "ts": 1.0, "target": "max", "level": "info", "text": "rc",
             "from": "Race Control", "ack": None, "origin": "race_control"},
            {"id": 2, "ts": 1.0, "target": "director", "level": "info", "text": "ready",
             "from": "Max", "ack": None, "origin": "commentator"},
            {"id": 3, "ts": 1.0, "target": "max", "level": "info", "text": "dir",
             "from": "Director", "ack": None}]
    assert [c["id"] for c in cu.active_cues_for(cues, "max", 1.0, info_ttl=30)] == [3]


def t_cue_backs_selector():
    cues = [{"id": 1, "ts": 1.0, "target": "director", "level": "info", "text": "ready",
             "from": "Max", "ack": None, "origin": "commentator"},
            {"id": 2, "ts": 2.0, "target": "max", "level": "info", "text": "go",
             "from": "Director", "ack": None},                          # director cue
            {"id": 3, "ts": 3.0, "target": "all", "level": "info", "text": "rc",
             "from": "Race Control", "ack": None, "origin": "race_control"},
            {"id": 4, "ts": 4.0, "target": "director", "level": "info", "text": "need 2 min",
             "from": "Ann", "ack": None, "origin": "commentator"}]
    got = cu.cue_backs(cues)
    assert [(c["id"], c["from"]) for c in got] == [(1, "Max"), (4, "Ann")]


def t_cue_backs_show_cap():
    cues = [{"id": i, "ts": float(i), "target": "director", "level": "info",
             "text": str(i), "from": "Max", "ack": None, "origin": "commentator"}
            for i in range(1, cu.CUE_BACK_SHOW + 6)]
    got = cu.cue_backs(cues)
    assert len(got) == cu.CUE_BACK_SHOW
    assert got[-1]["id"] == cu.CUE_BACK_SHOW + 5      # most-recent window


def t_prune_keeps_cue_backs_in_a_separate_window():
    # A flood of RC notes must NOT evict cue-backs (independent per-origin windows).
    rc = [{"id": i, "ts": 1.0, "target": "all", "level": "info", "text": "rc",
           "from": "Race Control", "ack": None, "origin": "race_control"}
          for i in range(1, cu.RC_NOTE_KEEP + 5)]
    backs = [{"id": 1000 + i, "ts": 1.0, "target": "director", "level": "info",
              "text": "cb", "from": "Max", "ack": None, "origin": "commentator"}
             for i in range(3)]
    kept = cu.prune(rc + backs, now=1e9, info_ttl=30)
    kept_backs = [c for c in kept if c.get("origin") == "commentator"]
    kept_rc = [c for c in kept if c.get("origin") == "race_control"]
    assert len(kept_backs) == 3                       # all cue-backs survive
    assert len(kept_rc) == cu.RC_NOTE_KEEP            # RC flood capped, not the backs


def t_cuestore_cue_back_round_trip():
    with tempfile.TemporaryDirectory() as d:
        store = _relay.CueStore(os.path.join(d, "cues.json"))
        r = store.add(target="director", level="info", text="ready",
                      from_name="Max Power", origin="commentator", now=100.0)
        assert r["ok"] and r["cue"]["origin"] == "commentator"
        assert r["cue"]["from"] == "Max Power"
        assert [c["id"] for c in cu.cue_backs(store.list())] == [1]
        # Never a director toast and never an RC note:
        assert cu.active_cues_for(store.list(), "max", 100.0) == []
        assert cu.race_control_notes_for(store.list(), "max") == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
