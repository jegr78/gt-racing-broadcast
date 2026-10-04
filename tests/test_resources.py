#!/usr/bin/env python3
"""Unit checks for the machine resource reader/sampler (pure, stdlib only)."""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


import sys
sys.path.insert(0, os.path.join(ROOT, "src", "scripts"))
r = _load("resources", ("src", "scripts", "resources.py"))


def t_parse_proc_stat_cpu():
    text = "cpu  100 0 100 700 100 0 100\ncpu0 ...\n"
    busy, total = r.parse_proc_stat_cpu(text)
    # idle = fields[3]+fields[4] = 700+100 = 800; total = 1100; busy = 300
    assert (busy, total) == (300, 1100), (busy, total)
    assert r.parse_proc_stat_cpu("no cpu line") is None


def t_parse_proc_net_dev():
    text = ("Inter-|   Receive ...\n"
            " face |bytes ...\n"
            "    lo: 100 0 0 0 0 0 0 0 100 0 0 0 0 0 0 0\n"
            "  eth0: 1000 0 0 0 0 0 0 0 2000 0 0 0 0 0 0 0\n")
    assert r.parse_proc_net_dev(text) == (1000, 2000)   # lo excluded, rx=field0 tx=field8


def t_parse_netstat_ib():
    text = ("Name  Mtu  Network  Address  Ipkts Ierrs Ibytes Opkts Oerrs Obytes Coll\n"
            "lo0   16384 <Link#1>          10 0 500 10 0 500 0\n"
            "en0   1500  <Link#2>          20 0 3000 20 0 4000 0\n"
            "en0   1500  1.2.3.4  1.2.3.4  20 0 3000 20 0 4000 0\n")   # dup row same iface
    assert r.parse_netstat_ib(text) == (3000, 4000)   # lo0 excluded, en0 counted once


def t_parse_top_cpu():
    text = ("CPU usage: 5.0% user, 5.0% sys, 90.00% idle\n"
            "CPU usage: 12.0% user, 8.0% sys, 80.00% idle\n")
    assert r.parse_top_cpu(text) == 20.0   # last line: 100 - 80


def t_parse_vm_stat():
    text = ("Mach Virtual Memory Statistics: (page size of 4096 bytes)\n"
            "Pages free: 100.\nPages active: 200.\nPages inactive: 50.\n"
            "Pages wired down: 100.\nPages occupied by compressor: 50.\n")
    # used = (active + wired + compressor) * page = (200+100+50)*4096
    assert r.parse_vm_stat(text) == 350 * 4096, r.parse_vm_stat(text)


def _if_table(rows):
    """A MIB_IF_TABLE2 buffer as GetIfTable2 lays it out: (if_type, flags, rx, tx) rows."""
    import ctypes
    table = (r.MIB_IF_ROW2 * len(rows))()
    for row, (if_type, flags, rx, tx) in zip(table, rows, strict=True):
        row.Type, row.InterfaceAndOperStatusFlags = if_type, flags
        row.InOctets, row.OutOctets = rx, tx
    head = ctypes.c_uint32(len(rows))
    return bytes(head) + bytes(4) + bytes(table)


def t_mib_if_row2_layout_matches_windows():
    import ctypes
    assert ctypes.sizeof(r.MIB_IF_ROW2) == 1352, ctypes.sizeof(r.MIB_IF_ROW2)
    assert r.MIB_IF_ROW2.InOctets.offset == 1208, "InOctets must sit at the netioapi.h offset"
    assert r.MIB_IF_ROW2.OutOctets.offset == 1280, "OutOctets must sit at the netioapi.h offset"
    assert r.MIB_IF_ROW2.InterfaceAndOperStatusFlags.offset == 1152
    assert r.MIB_IF_ROW2.Type.offset == 1128


def t_parse_if_table2_counts_hardware_rows_once():
    hw, flt = r.IF_FLAG_HARDWARE, r.IF_FLAG_FILTER
    rows = [(6, hw, 1000, 2000),          # Ethernet NIC
            (6, hw | flt, 1000, 2000),    # WFP/QoS filter stacked on that NIC
            (53, 0, 300, 400),            # tunnel adapter (Tailscale, VPN)
            (24, 0, 50, 50),              # software loopback
            (71, hw, 10, 20)]             # Wi-Fi NIC
    assert r.parse_if_table2(_if_table(rows)) == (1010, 2020), \
        "only hardware rows count; filter, tunnel and loopback rows mirror or skip the line"


def t_parse_if_table2_empty_or_short():
    assert r.parse_if_table2(_if_table([])) == (0, 0)
    assert r.parse_if_table2(b"") is None, "a buffer without a header is unreadable"
    assert r.parse_if_table2(_if_table([(6, 1, 1, 1)])[:100]) is None, \
        "a header claiming more rows than the buffer holds is unreadable"


def t_no_typeperf_under_src():
    hits = []
    for base, _dirs, files in os.walk(os.path.join(ROOT, "src")):
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(base, f)
                with open(path, encoding="utf-8") as fh:
                    if "typeperf" in fh.read():
                        hits.append(os.path.relpath(path, ROOT))
    assert not hits, f"typeperf counter paths are localized and fail on German Windows: {hits}"


def t_cpu_pct_from_delta():
    assert r.cpu_pct_from_delta((300, 1100), (400, 1300)) == 50.0   # db=100 dt=200
    assert r.cpu_pct_from_delta(None, (1, 2)) is None
    assert r.cpu_pct_from_delta((5, 10), (5, 10)) is None           # dt=0
    assert r.cpu_pct_from_delta((5, 100), (4, 200)) is None         # busy went backwards


def t_rate_from_delta():
    assert r.rate_from_delta(1000, 3000, 2.0) == 1000.0
    assert r.rate_from_delta(1000, 3000, 0) is None
    assert r.rate_from_delta(5000, 1000, 2.0) is None               # counter reset


def _fake_readers(seq):
    """seq: list of dicts per tick with keys cpu/net/mem/disk. Returns a readers map
    whose calls pop the next tick's value."""
    ticks = list(seq)
    state = {"i": 0}

    def nextt():
        t = ticks[min(state["i"], len(ticks) - 1)]
        return t

    def adv():
        state["i"] += 1

    def read_disk():
        val = nextt()["disk"]
        adv()  # noqa: PLE0001 - intentional side effect for state advance
        return val

    return {
        "cpu": lambda: nextt()["cpu"],
        "net": lambda: nextt()["net"],
        "mem": lambda: nextt()["mem"],
        "disk": read_disk,
    }


def t_sampler_counter_deltas():
    readers = _fake_readers([
        {"cpu": ("counter", 300, 1100), "net": ("counter", 1000, 2000),
         "mem": (8 * 1024**3, 16 * 1024**3), "disk": 100 * 1024**3},
        {"cpu": ("counter", 400, 1300), "net": ("counter", 3000, 6000),
         "mem": (8 * 1024**3, 16 * 1024**3), "disk": 100 * 1024**3},
    ])
    s = r.ResourceSampler(readers=readers)
    first = s.sample(now=1000.0)
    assert first["cpu_pct"] is None and first["net_up_bps"] is None   # no prev yet
    assert first["mem_pct"] == 50.0 and first["disk_free"] == 100 * 1024**3
    second = s.sample(now=1002.0)                                     # dt=2s
    assert second["cpu_pct"] == 50.0, second                          # db=100 dt=200
    # rx 1000->3000 over 2s = 1000/s (down); tx 2000->6000 over 2s = 2000/s (up)
    assert second["net_down_bps"] == 1000.0 and second["net_up_bps"] == 2000.0, second


def t_sampler_percent_passthrough():
    readers = _fake_readers([
        {"cpu": ("percent", 42.0, None), "net": None,
         "mem": (4 * 1024**3, 8 * 1024**3), "disk": 50 * 1024**3},
    ])
    snap = r.ResourceSampler(readers=readers).sample(now=1.0)
    assert snap["cpu_pct"] == 42.0


def t_sampler_none_on_reader_failure():
    readers = _fake_readers([{"cpu": None, "net": None, "mem": (None, None), "disk": None}])
    snap = r.ResourceSampler(readers=readers).sample(now=1.0)
    assert snap["cpu_pct"] is None and snap["net_up_bps"] is None
    assert snap["mem_pct"] is None and snap["disk_free"] is None


def t_levels():
    assert (r.cpu_level(10), r.cpu_level(80), r.cpu_level(95)) == ("green", "yellow", "red")
    assert (r.mem_level(50), r.mem_level(85), r.mem_level(95)) == ("green", "yellow", "red")
    assert r.cpu_level(None) is None
    assert r.disk_level(1 * 1024**3) == "red"      # <2 GB
    assert r.disk_level(3 * 1024**3) == "yellow"   # <5 GB
    assert r.disk_level(50 * 1024**3) == "green"


def t_to_health_fields():
    snap = {"cpu_pct": 42.0, "mem_pct": 55.0, "net_up_bps": 2000.0,
            "net_down_bps": 1000.0, "disk_free": 100 * 1024 * 1024}
    f = r.to_health_fields(snap)
    assert f == {"sys_cpu_pct": 42.0, "sys_mem_pct": 55.0,
                 "sys_net_up_kbps": 2.0, "sys_net_down_kbps": 1.0,
                 "sys_disk_free_mb": 100.0}, f
    # None-safe
    empty = r.to_health_fields({"cpu_pct": None, "mem_pct": None, "net_up_bps": None,
                                "net_down_bps": None, "disk_free": None})
    assert set(empty.values()) == {None}


def t_monitor_latest_none_then_sampled():
    calls = {"n": 0}

    class _S:
        def sample(self, now=None):
            calls["n"] += 1
            return {"ts": now, "cpu_pct": 1.0, "mem_used": 1, "mem_total": 2,
                    "mem_pct": 50.0, "net_up_bps": None, "net_down_bps": None, "disk_free": 3}
    m = r.ResourceMonitor(interval=0.02, sampler=_S())
    assert m.latest() is None
    m.start()
    import time as _t
    for _ in range(50):
        if m.latest() is not None:
            break
        _t.sleep(0.02)
    m.stop()
    assert m.latest() is not None and calls["n"] >= 1


def run():
    for name, fn in sorted(globals().items()):
        if name.startswith("t_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASS")


if __name__ == "__main__":
    run()
