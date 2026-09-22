#!/usr/bin/env python3
"""Stdlib unit checks for broadcast Part control (src/scripts/parts.py + relay
PartStore/ProducerSource/apply). Run: python3 tests/test_parts.py"""
import importlib.util, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
import parts as m  # pure module
import producer as pm  # pure Producer-tab classifier (same sys.path as parts)

# The relay module has a hyphenated filename, so it is loaded by path.
_rspec = importlib.util.spec_from_file_location(
    "irofeeds", os.path.join(ROOT, "src", "relay", "racecast-feeds.py"))
R = importlib.util.module_from_spec(_rspec); _rspec.loader.exec_module(R)

ROWS3 = [{"part": "Part 1", "producer": "A", "magicdns": "a", "stream_key": "key1"},
         {"part": "Part 2", "producer": "B", "magicdns": "b", "stream_key": "key2"},
         {"part": "Part 3", "producer": "C", "magicdns": "c", "stream_key": "key3"}]


def t_parts_intent_phrase():
    assert m.parts_intent_phrase("start", "1") == "START PART 1"
    assert m.parts_intent_phrase("end", "3") == "END PART 3"
    assert m.parts_intent_phrase("start", "Q") == "START PART Q"
    assert m.parts_intent_phrase("start", 1) == "START PART 1"  # int back-compat


def t_normalize_intent():
    assert m.normalize_intent("  end   part 2 ") == "END PART 2"
    assert m.normalize_intent("Start Part 1") == "START PART 1"
    assert m.normalize_intent(None) == ""


def t_part_confirm_token():
    assert m.part_confirm_token("Part 1") == "1"
    assert m.part_confirm_token("part 2") == "2"
    assert m.part_confirm_token("Q") == "Q"
    assert m.part_confirm_token("Part Q") == "Q"
    assert m.part_confirm_token("  Part   3 ") == "3"


def t_view_model_qualifying_confirm_phrase():
    # A single Q row, ready to start -> confirm phrase reads START PART Q.
    q = [{"part": "Q", "producer": "A"}]
    vm = m.parts_view_model(q, {"index": 1, "live": False}, stream_active=False)
    assert vm["action"] == "start" and vm["confirm_phrase"] == "START PART Q"
    vm2 = m.parts_view_model(q, {"index": 1, "live": False}, stream_active=True)
    assert vm2["action"] == "end" and vm2["confirm_phrase"] == "END PART Q"


def t_stream_active_param():
    # The client passes the OBS truth it already polled, so /parts/data skips its own
    # obs-ws read. Absent or unknown gives None and the relay reads OBS itself.
    assert m.stream_active_param("1") is True
    assert m.stream_active_param("true") is True
    assert m.stream_active_param("0") is False
    assert m.stream_active_param("false") is False
    assert m.stream_active_param(None) is None
    assert m.stream_active_param("") is None
    assert m.stream_active_param("maybe") is None


def t_view_model_ready_offers_start():
    vm = m.parts_view_model(ROWS3, {"index": 1, "live": False}, stream_active=False)
    assert vm["enabled"] and vm["count"] == 3
    assert vm["action"] == "start" and vm["index"] == 1
    assert vm["confirm_phrase"] == "START PART 1" and vm["complete"] is False
    assert len(vm["parts"]) == 3 and vm["parts"][1]["label"] == "Part 2"


def t_view_model_live_offers_end_from_obs():
    # file says not live, OBS says active -> OBS wins, it is authoritative
    vm = m.parts_view_model(ROWS3, {"index": 1, "live": False}, stream_active=True)
    assert vm["live"] is True and vm["action"] == "end"
    assert vm["confirm_phrase"] == "END PART 1"


def t_view_model_after_end_offers_next():
    vm = m.parts_view_model(ROWS3, {"index": 2, "live": False}, stream_active=False)
    assert vm["action"] == "start" and vm["index"] == 2
    assert vm["confirm_phrase"] == "START PART 2" and vm["next_index"] == 3


def t_view_model_last_part_complete():
    vm = m.parts_view_model(ROWS3, {"index": 4, "live": False}, stream_active=False)
    assert vm["complete"] is True and vm["action"] is None


def t_view_model_no_parts_disabled():
    vm = m.parts_view_model([], {"index": 1, "live": False}, stream_active=False)
    assert vm["enabled"] is False and vm["action"] is None


def t_view_model_falls_back_to_file_live():
    vm = m.parts_view_model(ROWS3, {"index": 2, "live": True}, stream_active=None)
    assert vm["live"] is True and vm["action"] == "end" and vm["index"] == 2


def t_validate_start_ok():
    ok, res = m.validate_start({"index": 1, "intent": "START PART 1"},
                               ROWS3, {"index": 1, "live": False})
    assert ok and res == 1


def t_validate_start_bad_phrase():
    ok, res = m.validate_start({"index": 1, "intent": "go"},
                               ROWS3, {"index": 1, "live": False})
    assert not ok and res[1] == 403


def t_validate_start_wrong_index():
    ok, res = m.validate_start({"index": 2, "intent": "START PART 2"},
                               ROWS3, {"index": 1, "live": False})
    assert not ok and res[1] == 409


def t_validate_start_bad_index_type():
    ok, res = m.validate_start({"index": "x", "intent": "START PART x"},
                               ROWS3, {"index": 1, "live": False})
    assert not ok and res[1] == 400


def t_validate_start_qualifying_token():
    q = [{"part": "Q"}]
    ok, res = m.validate_start({"index": 1, "intent": "START PART Q"},
                               q, {"index": 1, "live": False})
    assert ok and res == 1
    bad, r2 = m.validate_start({"index": 1, "intent": "START PART 1"},
                               q, {"index": 1, "live": False})
    assert not bad and r2[1] == 403          # numeric phrase rejected for a Q part


def t_validate_end_ok():
    ok, res = m.validate_end({"intent": "END PART 2"}, ROWS3, {"index": 2, "live": True})
    assert ok and res == 2


def t_validate_end_bad_phrase():
    ok, res = m.validate_end({"intent": "nope"}, ROWS3, {"index": 2, "live": True})
    assert not ok and res[1] == 403


def t_validate_end_qualifying_token():
    q = [{"part": "Q"}]
    ok, res = m.validate_end({"intent": "END PART Q"}, q, {"index": 1, "live": True})
    assert ok and res == 1


def t_partstore_default_and_transitions():
    d = tempfile.mkdtemp()
    ps = R.PartStore(os.path.join(d, "part.json"))
    assert ps.get() == {"index": 1, "live": False}
    assert ps.mark_live(1) == {"index": 1, "live": True}
    assert ps.end() == {"index": 2, "live": False}
    # persisted -> a fresh store reloads the advanced pointer
    ps2 = R.PartStore(os.path.join(d, "part.json"))
    assert ps2.get() == {"index": 2, "live": False}


def t_partstore_ignores_corrupt_file():
    d = tempfile.mkdtemp(); p = os.path.join(d, "part.json")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    assert R.PartStore(p).get() == {"index": 1, "live": False}


def t_partstore_type_checks_load():
    d = tempfile.mkdtemp(); p = os.path.join(d, "part.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"index": "x", "live": "yes"}, fh)
    assert R.PartStore(p).get() == {"index": 1, "live": False}


def t_partstore_reset():
    d = tempfile.mkdtemp()
    ps = R.PartStore(os.path.join(d, "part.json"))
    ps.mark_live(3)
    assert ps.get() == {"index": 3, "live": True}
    assert ps.reset() == {"index": 1, "live": False}
    # persisted -> a fresh store reloads the reset pointer
    assert R.PartStore(os.path.join(d, "part.json")).get() == {"index": 1, "live": False}


def t_apply_stream_service_for_ref_happy():
    calls, seen = {}, {}
    def fetch(u):
        return "Platform,Channel\nyoutube,@x\n"
    def post(u, o):
        calls["ref"] = o["ref"]
        return b'{"ok":true,"action":"get_stream_key","key":"SECRET"}'
    def set_service(platform, key):
        seen["p"] = platform; seen["k"] = key; return True, "ok"
    ok, note = R.apply_stream_service_for_ref("key2", "http://chan", "http://push",
                                              set_service, fetch=fetch, post=post)
    assert ok and calls["ref"] == "key2" and seen["p"] == "youtube" and seen["k"] == "SECRET"
    assert "SECRET" not in note   # key never leaks into the note


def t_apply_stream_service_for_ref_webhook_error():
    def fetch(u):
        return "Platform,Channel\nyoutube,@x\n"
    def post(u, o):
        return b'{"ok":false,"error":"bad ref"}'
    ok, note = R.apply_stream_service_for_ref("keyX", "c", "p",
                                              lambda a, b: (True, ""),
                                              fetch=fetch, post=post)
    assert not ok and note == "bad ref"


def t_apply_stream_service_for_ref_no_push_url():
    ok, note = R.apply_stream_service_for_ref("k", "c", "",
                                              lambda a, b: (True, ""))
    assert not ok and "SHEET_PUSH_URL" in note


def t_producer_source_empty_when_no_url():
    assert R.ProducerSource(None).get() == []


def t_producer_source_parses_on_refresh():
    ps = R.ProducerSource("http://x")
    ps._fetch_text = lambda timeout=15: (
        "Part,Producer,MagicDNS,Stream Key\nPart 1,A,a,key1\nPart 2,B,b,key2\n")
    assert ps.refresh() is True
    assert ps.get() == [
        {"part": "Part 1", "producer": "A", "magicdns": "a", "stream_key": "key1"},
        {"part": "Part 2", "producer": "B", "magicdns": "b", "stream_key": "key2"}]


def t_last_part_condition():
    # The relay's /parts/end branch detects "this was the last part" from live plus
    # index == count, which decides whether to spawn `event stop`.
    rows = [{"part": "P1"}, {"part": "P2"}]
    vm = m.parts_view_model(rows, {"index": 2, "live": True}, stream_active=True)
    assert vm["live"] and vm["index"] == vm["count"]      # last part is live
    vm1 = m.parts_view_model(rows, {"index": 1, "live": True}, stream_active=True)
    assert not (vm1["index"] == vm1["count"])             # not the last part


def t_part_kind_classifies_q_vs_numeric():
    assert pm.part_kind("Q") == "qualifying"
    assert pm.part_kind("q") == "qualifying"
    assert pm.part_kind(" Qualifying ") == "qualifying"
    assert pm.part_kind("Q1") == "qualifying"
    assert pm.part_kind("Part 1") == "race"
    assert pm.part_kind("1") == "race"
    assert pm.part_kind("") == "race"
    assert pm.part_kind(None) == "race"
    assert pm.part_kind("Part Q1") == "race"  # contains but doesn't START with Q


def t_active_producer_rows_filters_by_mode():
    rows = [{"part": "Part 1"}, {"part": "Q"}, {"part": "Part 2"}]
    race = pm.active_producer_rows(rows, "race")
    assert [r["part"] for r in race] == ["Part 1", "Part 2"]
    qual = pm.active_producer_rows(rows, "qualifying")
    assert [r["part"] for r in qual] == ["Q"]
    # unknown mode -> race subset; empty or None -> []
    assert [r["part"] for r in pm.active_producer_rows(rows, "x")] == ["Part 1", "Part 2"]
    assert pm.active_producer_rows([], "race") == []
    assert pm.active_producer_rows(None, "qualifying") == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
