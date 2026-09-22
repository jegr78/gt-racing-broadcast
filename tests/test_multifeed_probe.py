#!/usr/bin/env python3
"""Unit checks for the pure helpers of tools/multifeed-429-probe.py (#505).

Runnable script, stdlib only. Only the pure helpers are exercised. The relay module
is lazy-loaded in the probe's run path, so importing the tool here is cheap and does
not pull in racecast-feeds.py."""
import importlib.util
import os
import random
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)


def _load_probe():
    path = os.path.join(_ROOT, "tools", "multifeed-429-probe.py")
    spec = importlib.util.spec_from_file_location("multifeed_probe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


m = _load_probe()


def t_activation_delays_burst_all_zero():
    d = m.activation_delays(3, "burst", 5, 2, random.Random(1))
    assert d == [0.0, 0.0, 0.0], d


def t_activation_delays_staggered_monotone_first_zero():
    d = m.activation_delays(3, "staggered", 5, 0, random.Random(1))   # no jitter -> exact
    assert d == [0.0, 5.0, 10.0], d


def t_activation_delays_staggered_jitter_bounded_and_nonneg():
    d = m.activation_delays(4, "staggered", 5, 2, random.Random(7))
    assert d[0] == 0.0
    for i in range(1, 4):
        assert abs(d[i] - i * 5) <= 2 + 1e-9, d          # within jitter band
        assert d[i] >= 0.0


def t_backoff_delay_fixed_is_constant():
    for a in (1, 2, 5, 10):
        assert m.backoff_delay(a, "fixed", 15, 120, 5, random.Random(1)) == 15.0


def t_backoff_delay_exponential_capped():
    r = random.Random(1)
    # base 5, no jitter (pass 0): 5,10,20,40,80,120(cap),120(cap)
    vals = [m.backoff_delay(a, "backoff", 5, 120, 0, r) for a in range(1, 8)]
    assert vals == [5.0, 10.0, 20.0, 40.0, 80.0, 120.0, 120.0], vals


def t_backoff_delay_jitter_within_band():
    r = random.Random(3)
    v = m.backoff_delay(1, "backoff", 5, 120, 4, r)
    assert 5.0 <= v < 5.0 + 4.0, v


def t_classify_pull_line_429_wins():
    assert m.classify_pull_line("HTTP error 429 Too Many Requests") == "throttle_429"
    assert m.classify_pull_line("got 429 while reloading") == "throttle_429"


def t_classify_pull_line_403_and_reload_and_none():
    assert m.classify_pull_line("Server returned 403 Forbidden") == "http_403"
    assert m.classify_pull_line("Failed to reload playlist") == "reload_error"
    assert m.classify_pull_line("Unable to open URL: ...") == "reload_error"
    assert m.classify_pull_line("[cli][info] Opening stream: 1080p (hls)") is None


def t_relay_running_true_when_pid_alive():
    d = tempfile.mkdtemp()
    pidf = os.path.join(d, "relay.pid")
    with open(pidf, "w", encoding="utf-8") as fh:
        fh.write("4242\n")
    assert m.relay_running(pidf, lambda pid: pid == 4242) is True
    assert m.relay_running(pidf, lambda pid: False) is False        # pid dead


def t_relay_running_false_when_absent_or_garbage():
    d = tempfile.mkdtemp()
    assert m.relay_running(os.path.join(d, "nope.pid"), lambda pid: True) is False
    bad = os.path.join(d, "bad.pid")
    with open(bad, "w", encoding="utf-8") as fh:
        fh.write("not-a-pid\n")
    assert m.relay_running(bad, lambda pid: True) is False


def t_read_url_list_skips_comments_and_blanks():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "urls.txt")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("# a comment\n\nhttps://youtu.be/A\n  https://youtu.be/B  \n# tail\n")
    assert m.read_url_list(p) == ["https://youtu.be/A", "https://youtu.be/B"]


def t_apply_live_edge_overrides_existing():
    cmd = ["streamlink", "--ringbuffer-size", "64M", "--hls-live-edge", "4", "--", "URL", "best"]
    out = m.apply_live_edge(cmd, 6)
    assert out[out.index("--hls-live-edge") + 1] == "6"
    assert cmd[cmd.index("--hls-live-edge") + 1] == "4"              # original untouched (copy)


def t_apply_live_edge_appends_when_absent_before_dashdash():
    cmd = ["streamlink", "--twitch-low-latency", "--", "URL", "best"]
    out = m.apply_live_edge(cmd, 3)
    assert "--hls-live-edge" in out
    i = out.index("--hls-live-edge")
    assert out[i + 1] == "3"
    assert out.index("--") > i                                       # inserted before the `--`


def t_apply_live_edge_none_is_noop_copy():
    cmd = ["streamlink", "--", "URL", "best"]
    out = m.apply_live_edge(cmd, None)
    assert out == cmd and out is not cmd


def t_summarize_cells_rows_only_for_cell_records():
    records = [
        {"kind": "pull", "idx": 0},                                  # ignored
        {"kind": "cell", "cell_id": "yt-2-burst", "platform": "youtube", "n": 2,
         "activation": "burst", "retry_mode": "fixed", "threw_429": True,
         "time_to_first_429_s": 118.0, "sustained_s": 118.0, "recovery_s": None,
         "agg_mbps": 9.4},
        {"kind": "cell", "cell_id": "tw-2-burst", "platform": "twitch", "n": 2,
         "activation": "burst", "retry_mode": "fixed", "threw_429": False,
         "time_to_first_429_s": None, "sustained_s": 1200.0, "recovery_s": None,
         "agg_mbps": 11.0},
    ]
    header, sep, rows = m.summarize_cells(records)
    assert header.startswith("| cell |")
    assert "|---" in sep
    assert len(rows) == 2                                            # the pull record is skipped
    # rows are sorted by cell_id: "tw-2-burst" < "yt-2-burst"
    assert "tw-2-burst" in rows[0] and "no" in rows[0] and "1200" in rows[0]
    assert "yt-2-burst" in rows[1] and "yes" in rows[1] and "118" in rows[1]
    assert "—" in rows[1]                                            # recovery None -> em-dash


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
    sys.exit(0)
