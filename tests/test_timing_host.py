#!/usr/bin/env python3
"""The timing host publish protocol helpers (#1161).
Run: python3 tests/test_timing_host.py"""
import json, os, re, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "scripts"))
import timing_host as th  # noqa: E402

LINK = "abcDEF123_-xyzABC456789"


def t_state_frame_embeds_body():
    frame = json.loads(th.state_frame(3, 1.5, b'{"v":1}', None))
    assert frame == {"t": "state", "seq": 3, "age_s": 1.5, "data": {"v": 1}, "fallback": None}, frame


def t_state_frame_carries_fallback():
    url = "https://host.example.ts.net/console/timing?l=" + LINK
    assert json.loads(th.state_frame(1, 0.0, b"{}", url))["fallback"] == url


def t_end_and_moved_frames():
    assert json.loads(th.end_frame()) == {"t": "end"}
    assert json.loads(th.moved_frame("https://x/y")) == {"t": "moved", "to": "https://x/y"}


def t_parse_host_frame_reads_ack():
    ack = th.parse_host_frame('{"t":"ack","seq":5,"viewers":12,"conns_today":40}')
    assert ack == {"t": "ack", "seq": 5, "viewers": 12, "conns_today": 40}, ack


def t_parse_host_frame_rejects_unknown():
    for text in ('{"t":"x"}', "not json", "[]", '{"t":"ack","seq":"5","viewers":1,"conns_today":1}',
                 '{"t":"ack","seq":true,"viewers":1,"conns_today":1}'):
        assert th.parse_host_frame(text) is None, text


def t_script_name_is_valid():
    for profile in ("ERF Endurance_2", "demo", "x" * 200, "Ünïcode league"):
        name = th.script_name(profile)
        assert re.fullmatch(r"racecast-timing-[a-z0-9-]{1,47}", name), (profile, name)
        assert not name.endswith("-"), name


def t_script_name_differs_per_profile():
    assert th.script_name("league-a") != th.script_name("league-b")


def t_urls():
    host = "https://racecast-timing-demo.example.workers.dev"
    assert th.publish_url(host, LINK) == \
        f"wss://racecast-timing-demo.example.workers.dev/t/{LINK}/publish?v=1"
    assert th.viewer_url(host + "/", LINK) == f"{host}/t/{LINK}"


def t_link_id_re_matches_generated_ids():
    import secrets
    for _ in range(50):
        assert th.LINK_ID_RE.fullmatch(secrets.token_urlsafe(18))
    assert not th.LINK_ID_RE.fullmatch("../x")
    assert not th.LINK_ID_RE.fullmatch("short")


def t_read_settings_needs_url_and_key():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "profile.env")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("NAME=Demo\nTIMING_HOST_URL=https://h.example.workers.dev\n")
        assert th.read_settings(path) is None
        with open(path, "a", encoding="utf-8") as fh:
            fh.write('TIMING_HOST_PUBLISH_KEY="k1"\n')
        assert th.read_settings(path) == th.Settings("https://h.example.workers.dev", "k1")
        assert th.read_settings(os.path.join(td, "missing.env")) is None
        assert th.read_settings("") is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
