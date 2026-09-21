#!/usr/bin/env python3
"""Machine resource sampling — CPU %, RAM, network throughput, free disk (stdlib only).

Pure parsers + delta math are unit-tested with fixture strings; `ResourceSampler` owns the
previous cumulative counters and computes deltas; `ResourceMonitor` runs a sampler on a
background thread and caches the latest snapshot. Per-OS reads mirror preflight.py (Linux
/proc, Windows ctypes, macOS subprocess). Never raises — an unreadable metric is None.
"""
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


# ---------- pure parsers ----------

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
    """macOS `netstat -ib` -> (rx_bytes, tx_bytes) over non-lo0 ifaces (one row per iface)."""
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
    """macOS `vm_stat` -> bytes in use ((active+wired+compressor) * page_size), or None."""
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


def parse_typeperf_net(text):
    """Windows `typeperf` CSV -> (up_bps, down_bps) from the last data row, or None."""
    import csv
    import io
    hdr = None
    data = None
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        if any("Bytes" in c for c in row):
            hdr = row
        elif hdr and len(row) == len(hdr):
            data = row
    if not hdr or not data:
        return None
    up = down = 0.0
    got = False
    for i, col in enumerate(hdr):
        try:
            val = float(data[i])
        except (ValueError, IndexError):
            continue
        if "Received" in col:
            down += val; got = True
        elif "Sent" in col:
            up += val; got = True
    return (up, down) if got else None


# ---------- pure delta math ----------

def cpu_pct_from_delta(prev, cur):
    """(busy,total) pairs -> percent, or None (no prev / non-positive dt / counter reset)."""
    if not prev or not cur:
        return None
    db = cur[0] - prev[0]
    dt = cur[1] - prev[1]
    if dt <= 0 or db < 0:
        return None
    return round(min(100.0, max(0.0, db / dt * 100.0)), 1)


def rate_from_delta(prev, cur, dt):
    """Cumulative byte counters -> bytes/sec, or None (no prev / dt<=0 / reset)."""
    if prev is None or cur is None or dt <= 0 or cur < prev:
        return None
    return (cur - prev) / dt


# ---------- color levels ----------

def cpu_level(pct):
    if pct is None:
        return None
    return "red" if pct >= 90 else "yellow" if pct >= 75 else "green"


def mem_level(pct):
    if pct is None:
        return None
    return "red" if pct >= 92 else "yellow" if pct >= 80 else "green"


def disk_level(free_bytes):
    """Mirrors preflight.classify_disk thresholds (2 GB / 5 GB)."""
    if free_bytes is None:
        return None
    gb = free_bytes / (1024 ** 3)
    return "red" if gb < 2 else "yellow" if gb < 5 else "green"


# ---------- per-OS real readers (OS-gated; not unit-tested with real calls) ----------

def _read_cpu():
    """-> ('counter', busy, total) | ('percent', pct, None) | None.

    All tuple returns are length 3 (the 'percent' variant pads a None) so the
    shape is uniform — CodeQL's py/mixed-tuple-returns; the consumer (_cpu)
    keys off element [0] and never reads the pad."""
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


def _read_net():
    """-> ('counter', rx, tx) | ('rate', up_bps, down_bps) | None."""
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
            out = subprocess.run(
                ["typeperf", r"\Network Interface(*)\Bytes Received/sec",
                 r"\Network Interface(*)\Bytes Sent/sec", "-sc", "1"],
                capture_output=True, text=True, errors="replace", timeout=10, **no_window_kwargs()).stdout
            got = parse_typeperf_net(out)
            return ("rate", got[0], got[1]) if got else None
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


# ---------- sampler + monitor ----------

class ResourceSampler:
    """Owns the previous cumulative CPU + net counters; each sample() computes deltas
    since the last call. Never raises — a failed metric is None. Inject `readers` (the
    default = the real per-OS readers) to unit-test the delta logic without OS calls."""

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
        if reading[0] == "rate":
            return (reading[1], reading[2])           # (up_bps, down_bps)
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


def to_health_fields(snap):
    """Map a ResourceSampler snapshot to the health_store sys_* columns (%, kbps, MB).
    None-safe."""
    def kbps(bps):
        return round(bps / 1000.0, 1) if bps is not None else None

    def mb(b):
        return round(b / (1024 * 1024), 1) if b is not None else None
    return {"sys_cpu_pct": snap.get("cpu_pct"),
            "sys_mem_pct": snap.get("mem_pct"),
            "sys_net_up_kbps": kbps(snap.get("net_up_bps")),
            "sys_net_down_kbps": kbps(snap.get("net_down_bps")),
            "sys_disk_free_mb": mb(snap.get("disk_free"))}
