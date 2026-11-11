#!/usr/bin/env python3
"""Race Engineer desk: the connection section names a lossy GT7 network apart from
"no packets" (#1107). The JS checks run under node when it is installed (skipped otherwise).
Run: python3 tests/test_engineer_packet_loss.py"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_engineer_live_trend import _func_src, _html, _run_js  # noqa: E402

_HARNESS = r"""
let now = 0;
const performance = {now: () => now};
const mk = () => ({textContent: '', classList: {toggle() {}}});
const els = {}; const $ = id => els[id] || (els[id] = mk());
const NA = 'N/A';
const num = (v, d = 1) => typeof v === 'number' && Number.isFinite(v) ? v.toFixed(d) : NA;
const state = {stopped: false, connected: true, received: 0, problem: null, snapshot: null};
"""


def _age(snapshot):
    src = (_HARNESS + _func_src(_html(), "age")
           + f"\nstate.snapshot = {json.dumps(snapshot)};\nage();\n"
           + "console.log(JSON.stringify([$('status').textContent, $('receipt').textContent]));")
    out = _run_js(src)
    return json.loads(out) if out is not None else None


def _snap(packet_age, packets=None):
    snap = {"receipt": {"state": "live", "packet_age_s": packet_age, "packet_received_at": 1700000000}}
    if packets is not None:
        snap["packets"] = packets
    return snap


def t_a_lossy_network_is_named_in_the_status_and_the_receipt():
    got = _age(_snap(0.1, {"loss_rate_10s": 0.083, "lossy": True}))
    if got is None:
        return
    status, receipt = got
    assert status == "Lossy network", status
    assert "packets lost: 8.3 % (10 s)" in receipt, receipt


def t_no_packets_stays_stale_not_lossy():
    got = _age(_snap(9.0, {"loss_rate_10s": 0.2, "lossy": True}))
    if got is None:
        return
    assert got[0] == "Telemetry stale", f"no packets is not a lossy network: {got[0]}"


def t_a_clean_or_unknown_network_keeps_the_live_label():
    for packets in ({"loss_rate_10s": 0.0, "lossy": False}, None):
        got = _age(_snap(0.1, packets))
        if got is None:
            return
        assert got[0] == "Telemetry live", (packets, got)
        assert ("packets lost" in got[1]) == (packets is not None), (packets, got[1])


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn(); print("ok", name)
    print("ALL PASS")
