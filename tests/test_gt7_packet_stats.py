#!/usr/bin/env python3
"""GT7 packet loss and decode failure counters (#1107).
Run: python3 tests/test_gt7_packet_stats.py"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "scripts"))
import gt7_packet_stats as ps  # noqa: E402

DT = 1 / 60


def feed(stats, ids, start=100.0):
    t = start
    for pid in ids:
        stats.on_packet(pid, t)
        t += DT
    return t


def t_a_clean_sequence_loses_nothing():
    s = ps.PacketStats()
    t = feed(s, range(1000, 1600))
    snap = s.snapshot(t)
    assert (snap["received"], snap["lost"], snap["reordered"]) == (600, 0, 0), snap
    assert snap["loss_rate_10s"] == 0.0 and snap["lossy"] is False, snap


def t_a_gap_counts_the_missing_ids():
    s = ps.PacketStats()
    t = feed(s, [1, 2, 3, 7, 8])
    snap = s.snapshot(t)
    assert snap["received"] == 5 and snap["lost"] == 3, snap
    assert snap["loss_rate_10s"] == round(3 / 8, 4), snap


def t_a_late_packet_is_out_of_order_not_lost():
    s = ps.PacketStats()
    t = feed(s, [1, 2, 4, 3, 5])
    snap = s.snapshot(t)
    assert (snap["lost"], snap["reordered"], snap["received"]) == (0, 1, 5), snap


def t_a_repeated_packet_is_counted_once_as_reordered():
    s = ps.PacketStats()
    t = feed(s, [1, 2, 2, 3])
    snap = s.snapshot(t)
    assert (snap["lost"], snap["reordered"], snap["received"]) == (0, 1, 4), snap


def t_an_id_reset_starts_a_new_stream_instead_of_a_huge_gap():
    s = ps.PacketStats()
    t = feed(s, [500000, 500001, 500003])
    assert s.snapshot(t)["lost"] == 1
    t = feed(s, [1, 2, 3], t)
    snap = s.snapshot(t)
    assert snap["stream"] == 2 and (snap["received"], snap["lost"]) == (3, 0), snap
    assert snap["previous"] == {"received": 3, "lost": 1, "reordered": 0,
                                "decrypt_failed": 0, "parse_failed": 0}, snap


def t_a_long_silence_starts_a_new_stream():
    s = ps.PacketStats()
    t = feed(s, [1, 2, 3])
    t = feed(s, [10, 11], t + ps.STREAM_IDLE_S + 1)
    snap = s.snapshot(t)
    assert snap["stream"] == 2 and snap["lost"] == 0, \
        f"a console that stopped sending is no lossy network: {snap}"


def t_decode_failures_are_counted_separately():
    s = ps.PacketStats()
    t = feed(s, [1, 2])
    s.on_decrypt_failure(t)
    s.on_parse_failure(t)
    s.on_decrypt_failure(t)
    snap = s.snapshot(t)
    assert (snap["decrypt_failed"], snap["parse_failed"], snap["lost"]) == (2, 1, 0), snap


def t_a_failure_before_the_first_packet_stays_in_its_stream():
    s = ps.PacketStats()
    s.on_decrypt_failure(100.0)
    t = feed(s, [1, 2, 3], 100.1)
    snap = s.snapshot(t)
    assert (snap["stream"], snap["decrypt_failed"], snap["received"]) == (1, 1, 3), snap


def t_the_rate_covers_only_the_last_ten_seconds():
    s = ps.PacketStats()
    ids = [i for i in range(1, 601) if i % 10]          # 10 % lost
    t = feed(s, ids)
    assert s.snapshot(t)["loss_rate_10s"] > 0.09
    t = feed(s, range(601, 601 + 12 * 60), t)            # 12 s clean
    snap = s.snapshot(t)
    assert snap["loss_rate_10s"] == 0.0 and snap["lost"] == 60, snap


def t_lossy_needs_a_sustained_rate_while_packets_arrive():
    s = ps.PacketStats()
    pid, t = 1, 100.0
    while t < 100.0 + ps.LOSSY_HOLD_S - 2:
        s.on_packet(pid, t)
        pid += 1 if pid % 10 else 2                       # about 9 % lost
        t += DT
    assert s.snapshot(t)["lossy"] is False, "a short burst is not yet a lossy network"
    while t < 100.0 + ps.LOSSY_HOLD_S + ps.WINDOW_S + 2:
        s.on_packet(pid, t)
        pid += 1 if pid % 10 else 2
        t += DT
    assert s.snapshot(t)["lossy"] is True
    assert s.snapshot(t + ps.STALE_S + 1)["lossy"] is False, "no packets is not a lossy network"
    assert s.snapshot(t + ps.STALE_S + 1)["loss_rate_10s"] is not None


def t_an_empty_window_has_no_rate():
    s = ps.PacketStats()
    assert s.snapshot(0.0)["loss_rate_10s"] is None
    t = feed(s, [1, 2])
    assert s.snapshot(t + ps.WINDOW_S + 2)["loss_rate_10s"] is None


def t_gap_helper_matches_the_counter_rules():
    assert ps.gap(None, 5) == 0
    assert ps.gap(5, 6) == 0 and ps.gap(5, 9) == 3
    assert ps.gap(5, 5) == 0 and ps.gap(9, 5) == 0
    assert ps.gap(5, 5 + ps.MAX_GAP + 2) == 0, "a jump past MAX_GAP is a new stream"


def t_packet_id_reads_offset_0x70():
    plain = bytearray(0x128)
    plain[0x70:0x74] = (123456).to_bytes(4, "little", signed=True)
    assert ps.packet_id(bytes(plain)) == 123456
    assert ps.packet_id(b"short") is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
