#!/usr/bin/env python3
"""Machine resource sampling, stdlib only: CPU %, RAM, network throughput, free disk.

The pure parsers and delta math are unit-tested with fixture strings.
`ResourceSampler` owns the previous cumulative counters and computes deltas;
`ResourceMonitor` runs a sampler on a background thread and caches the latest
snapshot. Per-OS reads mirror preflight.py: Linux /proc, Windows ctypes, macOS
subprocess. Nothing raises; an unreadable metric is None.
"""
import ctypes
import re
import shutil
import subprocess
import sys
import threading
import time

try:
    from preflight import no_window_kwargs
except ImportError:                                  # pragma: no cover - path fallback
    def no_window_kwargs(os_name=None):
        import os as _os
        if (os_name or _os.name) == "nt":
            return {"creationflags": 0x08000000}
        return {}

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def parse_proc_stat_cpu(text):
    """First 'cpu ' line of /proc/stat -> (busy, total) jiffies, or None."""
    for line in text.splitlines():
        if line.startswith("cpu "):
            f = [int(x) for x in line.split()[1:]]
            idle = f[3] + (f[4] if len(f) > 4 else 0)     # idle + iowait
            total = sum(f)
            return (total - idle, total)
    return None


def parse_proc_net_dev(text):
    """/proc/net/dev -> (rx_bytes, tx_bytes) summed over non-loopback interfaces."""
    rx = tx = 0
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, _, rest = line.partition(":")
        name = name.strip()
        f = rest.split()
        if name == "lo" or len(f) < 9:
            continue
        rx += int(f[0])
        tx += int(f[8])
    return (rx, tx)


def parse_netstat_ib(text):
    """macOS `netstat -ib` -> (rx_bytes, tx_bytes) over the non-lo0 interfaces, one
    row each."""
    lines = text.splitlines()
    if not lines:
        return (0, 0)
    hdr = lines[0].split()
    try:
        ri, ti = hdr.index("Ibytes"), hdr.index("Obytes")
    except ValueError:
        return (0, 0)
    rx = tx = 0
    seen = set()
    for line in lines[1:]:
        f = line.split()
        if len(f) < len(hdr):
            continue
        name = f[0]
        if name.startswith("lo") or name in seen:
            continue
        seen.add(name)
        try:
            rx += int(f[ri]); tx += int(f[ti])
        except ValueError:
            continue
    return (rx, tx)


def parse_top_cpu(text):
    """macOS `top -l2 -s1 -n0` -> busy % from the LAST 'CPU usage' line (100 - idle)."""
    pct = None
    for line in text.splitlines():
        if "CPU usage" in line:
            m = re.search(r"([\d.]+)%\s*idle", line)
            if m:
                pct = round(100.0 - float(m.group(1)), 1)
    return pct


def parse_vm_stat(text):
    """macOS `vm_stat` -> bytes in use, (active+wired+compressor) * page_size, or
    None."""
    m = re.search(r"page size of (\d+) bytes", text)
    page = int(m.group(1)) if m else 4096

    def pages(label):
        mm = re.search(label + r":\s+(\d+)", text)
        return int(mm.group(1)) if mm else 0
    active = pages(r"Pages active")
    wired = pages(r"Pages wired down")
    comp = pages(r"Pages occupied by compressor")
    if not (active or wired):
        return None
    return (active + wired + comp) * page


class MIB_IF_ROW2(ctypes.Structure):
    """netioapi.h MIB_IF_ROW2 in fixed-width types, so the layout (1352 bytes) is the
    same on every OS and testable off Windows. WCHAR arrays are uint16."""
    _fields_ = [("InterfaceLuid", ctypes.c_uint64),
                ("InterfaceIndex", ctypes.c_uint32),
                ("InterfaceGuid", ctypes.c_uint8 * 16),
                ("Alias", ctypes.c_uint16 * 257),
                ("Description", ctypes.c_uint16 * 257),
                ("PhysicalAddressLength", ctypes.c_uint32),
                ("PhysicalAddress", ctypes.c_uint8 * 32),
                ("PermanentPhysicalAddress", ctypes.c_uint8 * 32),
                ("Mtu", ctypes.c_uint32),
                ("Type", ctypes.c_uint32),
                ("TunnelType", ctypes.c_uint32),
                ("MediaType", ctypes.c_uint32),
                ("PhysicalMediumType", ctypes.c_uint32),
                ("AccessType", ctypes.c_uint32),
                ("DirectionType", ctypes.c_uint32),
                ("InterfaceAndOperStatusFlags", ctypes.c_uint8),
                ("OperStatus", ctypes.c_uint32),
                ("AdminStatus", ctypes.c_uint32),
                ("MediaConnectState", ctypes.c_uint32),
                ("NetworkGuid", ctypes.c_uint8 * 16),
                ("ConnectionType", ctypes.c_uint32),
                ("TransmitLinkSpeed", ctypes.c_uint64),
                ("ReceiveLinkSpeed", ctypes.c_uint64),
                ("InOctets", ctypes.c_uint64),
                ("InUcastPkts", ctypes.c_uint64),
                ("InNUcastPkts", ctypes.c_uint64),
                ("InDiscards", ctypes.c_uint64),
                ("InErrors", ctypes.c_uint64),
                ("InUnknownProtos", ctypes.c_uint64),
                ("InUcastOctets", ctypes.c_uint64),
                ("InMulticastOctets", ctypes.c_uint64),
                ("InBroadcastOctets", ctypes.c_uint64),
                ("OutOctets", ctypes.c_uint64),
                ("OutUcastPkts", ctypes.c_uint64),
                ("OutNUcastPkts", ctypes.c_uint64),
                ("OutDiscards", ctypes.c_uint64),
                ("OutErrors", ctypes.c_uint64),
                ("OutUcastOctets", ctypes.c_uint64),
                ("OutMulticastOctets", ctypes.c_uint64),
                ("OutBroadcastOctets", ctypes.c_uint64),
                ("OutQLen", ctypes.c_uint64)]


IF_FLAG_HARDWARE = 0x01   # InterfaceAndOperStatusFlags.HardwareInterface
IF_FLAG_FILTER = 0x02     # InterfaceAndOperStatusFlags.FilterInterface
_IF_TABLE2_ROWS_AT = 8    # ULONG NumEntries, padded to the rows' 8-byte alignment


def parse_if_table2(buf):
    """A GetIfTable2 MIB_IF_TABLE2 buffer -> (rx_bytes, tx_bytes) over the hardware
    NICs, or None if the buffer is truncated.

    Filter drivers stacked on a NIC (WFP, QoS) repeat its counters, and tunnel
    adapters (Tailscale, VPN) carry traffic the NIC already counts, so only
    hardware rows that are not filters count. No localized counter names are
    involved, unlike perf-counter paths."""
    if len(buf) < _IF_TABLE2_ROWS_AT:
        return None
    n = int.from_bytes(buf[:4], "little")
    if len(buf) < _IF_TABLE2_ROWS_AT + n * ctypes.sizeof(MIB_IF_ROW2):
        return None
    rows = (MIB_IF_ROW2 * n).from_buffer_copy(buf, _IF_TABLE2_ROWS_AT)
    rx = tx = 0
    for row in rows:
        flags = row.InterfaceAndOperStatusFlags
        if flags & IF_FLAG_HARDWARE and not flags & IF_FLAG_FILTER:
            rx += row.InOctets
            tx += row.OutOctets
    return (rx, tx)


def cpu_pct_from_delta(prev, cur):
    """(busy,total) pairs -> percent. None without a previous pair, on a
    non-positive dt, or after a counter reset."""
    if not prev or not cur:
        return None
    db = cur[0] - prev[0]
    dt = cur[1] - prev[1]
    if dt <= 0 or db < 0:
        return None
    return round(min(100.0, max(0.0, db / dt * 100.0)), 1)


def rate_from_delta(prev, cur, dt):
    """Cumulative byte counters -> bytes/sec. None without a previous value, on
    dt <= 0, or after a counter reset."""
    if prev is None or cur is None or dt <= 0 or cur < prev:
        return None
    return (cur - prev) / dt


def cpu_level(pct):
    if pct is None:
        return None
    return "red" if pct >= 90 else "yellow" if pct >= 75 else "green"


def mem_level(pct):
    if pct is None:
        return None
    return "red" if pct >= 92 else "yellow" if pct >= 80 else "green"


def disk_level(free_bytes):
    """Uses preflight.classify_disk's thresholds of 2 GB and 5 GB."""
    if free_bytes is None:
        return None
    gb = free_bytes / (1024 ** 3)
    return "red" if gb < 2 else "yellow" if gb < 5 else "green"


# The per-OS readers below are OS-gated, so their real calls are not unit-tested.

def _read_cpu():
    """-> ('counter', busy, total) | ('percent', pct, None) | None.

    Every tuple return has length 3, the 'percent' variant padding with a None, so
    the shape stays uniform for CodeQL's py/mixed-tuple-returns. The consumer keys
    off element [0] and never reads the pad."""
    try:
        if IS_LINUX:
            with open("/proc/stat") as fh:
                got = parse_proc_stat_cpu(fh.read())
            return ("counter", got[0], got[1]) if got else None
        if IS_WIN:
            import ctypes
            idle, kern, user = (ctypes.c_ulonglong(), ctypes.c_ulonglong(),
                                ctypes.c_ulonglong())
            if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle),
                                                         ctypes.byref(kern),
                                                         ctypes.byref(user)):
                return None
            total = kern.value + user.value        # kernel time includes idle
            return ("counter", total - idle.value, total)
        if IS_MAC:
            out = subprocess.run(["top", "-l", "2", "-s", "1", "-n", "0"],
                                 capture_output=True, text=True, errors="replace", timeout=6,
                                 **no_window_kwargs()).stdout
            pct = parse_top_cpu(out)
            return ("percent", pct, None) if pct is not None else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def _read_if_table2():
    """Windows: cumulative (rx, tx) bytes over the hardware NICs via iphlpapi, or None."""
    iphlpapi = ctypes.windll.iphlpapi
    table = ctypes.c_void_p()
    if iphlpapi.GetIfTable2(ctypes.byref(table)) != 0:
        return None
    try:
        n = ctypes.c_uint32.from_address(table.value).value
        size = _IF_TABLE2_ROWS_AT + n * ctypes.sizeof(MIB_IF_ROW2)
        return parse_if_table2(ctypes.string_at(table.value, size))
    finally:
        iphlpapi.FreeMibTable(table)


def _read_net():
    """-> ('counter', rx, tx) | None."""
    try:
        if IS_LINUX:
            with open("/proc/net/dev") as fh:
                rx, tx = parse_proc_net_dev(fh.read())
            return ("counter", rx, tx)
        if IS_MAC:
            out = subprocess.run(["netstat", "-ib"], capture_output=True, text=True, errors="replace",
                                 timeout=6, **no_window_kwargs()).stdout
            rx, tx = parse_netstat_ib(out)
            return ("counter", rx, tx)
        if IS_WIN:
            got = _read_if_table2()
            return ("counter", got[0], got[1]) if got else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def _read_mem():
    """-> (used_bytes, total_bytes); either may be None."""
    try:
        if IS_LINUX:
            total = avail = None
            with open("/proc/meminfo") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        total = int(line.split()[1]) * 1024
                    elif line.startswith("MemAvailable:"):
                        avail = int(line.split()[1]) * 1024
            used = (total - avail) if (total is not None and avail is not None) else None
            return (used, total)
        if IS_WIN:
            import ctypes

            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = _MS()
            st.dwLength = ctypes.sizeof(_MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return (st.ullTotalPhys - st.ullAvailPhys, st.ullTotalPhys)
        if IS_MAC:
            total = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"],
                                                **no_window_kwargs()).strip())
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, errors="replace", timeout=6,
                                 **no_window_kwargs()).stdout
            return (parse_vm_stat(out), total)
    except (OSError, ValueError, subprocess.SubprocessError):
        return (None, None)
    return (None, None)


def _read_disk_free(path="."):
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def _default_readers():
    return {"cpu": _read_cpu, "net": _read_net, "mem": _read_mem, "disk": _read_disk_free}


class ResourceSampler:
    """Owns the previous cumulative CPU and net counters; each sample() computes
    deltas since the last call. Nothing raises: a failed metric is None. Inject
    `readers`, which defaults to the real per-OS readers, to unit-test the delta
    logic without OS calls."""

    def __init__(self, readers=None):
        self.readers = readers or _default_readers()
        self._prev_cpu = None      # (busy, total)
        self._prev_net = None      # (rx, tx)
        self._prev_ts = None

    def _cpu(self, reading):
        if not reading:
            return None
        if reading[0] == "percent":
            return reading[1]
        if reading[0] == "counter":
            cur = (reading[1], reading[2])
            pct = cpu_pct_from_delta(self._prev_cpu, cur)
            self._prev_cpu = cur
            return pct
        return None

    def _net(self, reading, now):
        if not reading:
            return (None, None)
        if reading[0] == "counter":
            cur = (reading[1], reading[2])            # (rx, tx)
            dt = (now - self._prev_ts) if self._prev_ts is not None else 0
            prev = self._prev_net
            down = rate_from_delta(prev[0] if prev else None, cur[0], dt)
            up = rate_from_delta(prev[1] if prev else None, cur[1], dt)
            self._prev_net = cur
            return (up, down)
        return (None, None)

    def sample(self, now=None):
        now = time.time() if now is None else now
        cpu_pct = None
        net_up = net_down = None
        mem_used = mem_total = None
        disk_free = None
        try:
            cpu_pct = self._cpu(self.readers["cpu"]())
        except Exception:  # noqa: BLE001 - never raise
            cpu_pct = None
        try:
            net_up, net_down = self._net(self.readers["net"](), now)
        except Exception:  # noqa: BLE001
            net_up = net_down = None
        try:
            mem_used, mem_total = self.readers["mem"]()
        except Exception:  # noqa: BLE001
            mem_used = mem_total = None
        try:
            disk_free = self.readers["disk"]()
        except Exception:  # noqa: BLE001
            disk_free = None
        mem_pct = (round(mem_used / mem_total * 100, 1)
                   if (mem_used and mem_total) else None)
        self._prev_ts = now
        return {"ts": now, "cpu_pct": cpu_pct,
                "mem_used": mem_used, "mem_total": mem_total, "mem_pct": mem_pct,
                "net_up_bps": net_up, "net_down_bps": net_down, "disk_free": disk_free}


class ResourceMonitor:
    """Runs a ResourceSampler on a daemon thread, caching the latest snapshot under a
    lock. `.latest()` is None until the first tick completes."""

    def __init__(self, interval=2.0, sampler=None):
        self.interval = interval
        self.sampler = sampler or ResourceSampler()
        self._latest = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def _run(self):
        while not self._stop.is_set():
            try:
                snap = self.sampler.sample()
                with self._lock:
                    self._latest = snap
            except Exception:  # noqa: BLE001 - keep the thread alive
                pass
            self._stop.wait(self.interval)

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def latest(self):
        with self._lock:
            return dict(self._latest) if self._latest else None

    def stop(self):
        self._stop.set()


class NetDownFloor:
    """Samples the host's download rate every `interval` seconds on its own thread and
    keeps the smallest rate seen since the last take(). A 30 s mean hides a 5 s dip;
    the floor shows it. Inject `reader` and `clock` to drive tick() in tests."""

    def __init__(self, interval=2.0, reader=None, clock=time.monotonic):
        self.interval = interval
        self.reader = reader or _read_net
        self.clock = clock
        self._prev = None          # (rx, t) of the last good reading
        self._floor = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def tick(self):
        try:
            reading = self.reader()
        except Exception:  # noqa: BLE001 - never raise
            reading = None
        now = self.clock()
        if not reading or reading[0] != "counter":
            self._prev = None
            return
        prev, self._prev = self._prev, (reading[1], now)
        if prev is None:
            return
        down = rate_from_delta(prev[0], reading[1], now - prev[1])
        if down is None:
            return
        with self._lock:
            if self._floor is None or down < self._floor:
                self._floor = down

    def take(self):
        """The smallest download rate (bytes/s) since the last call, or None."""
        with self._lock:
            floor, self._floor = self._floor, None
        return floor

    def _run(self):
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.interval)

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()


def to_health_fields(snap):
    """Map a ResourceSampler snapshot to the health_store sys_* columns, in percent,
    kbps and MB. None-safe."""
    def kbps(bps):
        return round(bps / 1000.0, 1) if bps is not None else None

    def mb(b):
        return round(b / (1024 * 1024), 1) if b is not None else None
    return {"sys_cpu_pct": snap.get("cpu_pct"),
            "sys_mem_pct": snap.get("mem_pct"),
            "sys_net_up_kbps": kbps(snap.get("net_up_bps")),
            "sys_net_down_kbps": kbps(snap.get("net_down_bps")),
            "sys_net_down_min_kbps": kbps(snap.get("net_down_min_bps")),
            "sys_disk_free_mb": mb(snap.get("disk_free"))}
