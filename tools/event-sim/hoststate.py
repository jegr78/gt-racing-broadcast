"""The machine's and the run's own cgroup state, for timing and failure diagnostics.

`sample()` reads the load average, the host's pressure stall information and the cgroup
this process runs in (on local Linux the E2E unit): its cpu.stat, memory and pressure.
`delta(before, after)` says how much CPU the unit got between two samples and how long
its tasks waited. Linux only: elsewhere every value is None."""
import os
import time

PROC = "/proc"
CGROUP = "/sys/fs/cgroup"
RESOURCES = ("cpu", "memory", "io")
DELTA_KEYS = ("seconds", "cpu_s", "throttled_s", "nr_throttled", "unit_cpu_stall_s",
              "unit_memory_stall_s", "unit_io_stall_s", "host_cpu_stall_s", "cpu_stall_share",
              "oom", "oom_kill", "high", "max", "memory_peak_mb")


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _int(path):
    text = _read(path)
    try:
        return int(text.strip()) if text is not None else None
    except ValueError:
        return None      # "max"


def _pairs(path):
    """A flat keyed file (cpu.stat, memory.events) as {key: int}."""
    text = _read(path)
    if text is None:
        return None
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("-").isdigit():
            out[parts[0]] = int(parts[1])
    return out


def _psi(path):
    """A pressure file as {some: {avg10, avg60, avg300, total}, full: ...}."""
    text = _read(path)
    if text is None:
        return None
    out = {}
    for line in text.splitlines():
        kind, *fields = line.split()
        values = {}
        for f in fields:
            key, _, value = f.partition("=")
            try:
                values[key] = int(value) if key == "total" else float(value)
            except ValueError:
                continue
        out[kind] = values
    return out


def _unit(proc, cgroup_root):
    text = _read(os.path.join(proc, "self", "cgroup"))
    rel = next((line[3:] for line in (text or "").splitlines() if line.startswith("0::")), None)
    if rel is None or ".." in rel.split("/"):
        return None
    base = os.path.join(cgroup_root, rel.lstrip("/"))
    if not os.path.isdir(base):
        return None
    return {"path": rel, "cpu": _pairs(os.path.join(base, "cpu.stat")),
            "memory_current": _int(os.path.join(base, "memory.current")),
            "memory_peak": _int(os.path.join(base, "memory.peak")),
            "memory_max": _int(os.path.join(base, "memory.max")),
            "memory_events": _pairs(os.path.join(base, "memory.events")),
            "pids_current": _int(os.path.join(base, "pids.current")),
            "pressure": {r: _psi(os.path.join(base, f"{r}.pressure")) for r in RESOURCES}}


def _label(cmdline):
    """A short name for a process: its program, and Chromium's --type or a Python script."""
    args = [a for a in cmdline.split("\0") if a]
    if len(args) == 1:
        args = args[0].split()      # Chromium rewrites its command line into one string
    if not args:
        return None
    name = os.path.basename(args[0])
    extra = next((a[len("--type="):] if a.startswith("--type=") else os.path.basename(a)
                  for a in args[1:] if a.startswith("--type=") or a.endswith(".py")), "")
    return f"{name} {extra}".strip()


def processes(proc=PROC, cgroup_root=CGROUP, ticks=None):
    """The processes of this run's cgroup, most CPU first: [{pid, name, cpu_s}]
    (user and system time so far). None outside Linux."""
    unit = _unit(proc, cgroup_root)
    pids = _read(os.path.join(cgroup_root, unit["path"].lstrip("/"), "cgroup.procs")) if unit else None
    if pids is None:
        return None
    ticks = ticks or os.sysconf("SC_CLK_TCK")
    out = []
    for pid in pids.split():
        stat = _read(os.path.join(proc, pid, "stat"))
        if not stat or ")" not in stat:
            continue        # it ended meanwhile
        fields = stat.rsplit(")", 1)[1].split()
        try:
            cpu = (int(fields[11]) + int(fields[12])) / ticks
        except (IndexError, ValueError):
            continue
        name = _label(_read(os.path.join(proc, pid, "cmdline")) or "") or stat.split("(", 1)[1].rsplit(")", 1)[0]
        out.append({"pid": int(pid), "name": name, "cpu_s": round(cpu, 2)})
    return sorted(out, key=lambda p: -p["cpu_s"])


def cpu_delta(before, after, top=10):
    """Each process's CPU seconds between two `processes()` readings, most first. A
    process that started meanwhile counts from zero; None without both readings."""
    if before is None or after is None:
        return None
    was = {p["pid"]: p["cpu_s"] for p in before}
    out = [{"pid": p["pid"], "name": p["name"], "cpu_s": round(p["cpu_s"] - was.get(p["pid"], 0.0), 2)}
           for p in after]
    return sorted(out, key=lambda p: -p["cpu_s"])[:top]


def sample(proc=PROC, cgroup_root=CGROUP, clock=time.monotonic):
    """{t, loadavg, pressure, unit}; a value the machine does not offer is None."""
    load = _read(os.path.join(proc, "loadavg"))
    try:
        loadavg = [float(x) for x in load.split()[:3]] if load else None
    except ValueError:
        loadavg = None
    return {"t": clock(), "loadavg": loadavg,
            "pressure": {r: _psi(os.path.join(proc, "pressure", r)) for r in RESOURCES},
            "unit": _unit(proc, cgroup_root)}


def _get(data, *path):
    for key in path:
        if not isinstance(data, dict) or data.get(key) is None:
            return None
        data = data[key]
    return data


def _diff(before, after, *path, scale=1):
    a, b = _get(after, *path), _get(before, *path)
    if a is None or b is None:
        return None
    return round((a - b) / scale, 3) if scale != 1 else a - b


def delta(before, after):
    """What happened between two samples: the unit's CPU time, throttling and stall
    seconds, the host's CPU stall seconds, new OOM, memory.high and memory.max events
    (the unit at its memory cap reclaims and stalls) and its memory peak so far."""
    stall = _diff(before, after, "unit", "pressure", "cpu", "some", "total", scale=1e6)
    linux = all(s.get("unit") is not None or s.get("loadavg") is not None for s in (before, after))
    seconds = round(after["t"] - before["t"], 3) if linux else None
    out = dict.fromkeys(DELTA_KEYS)
    out.update({
        "seconds": seconds,
        "cpu_s": _diff(before, after, "unit", "cpu", "usage_usec", scale=1e6),
        "throttled_s": _diff(before, after, "unit", "cpu", "throttled_usec", scale=1e6),
        "nr_throttled": _diff(before, after, "unit", "cpu", "nr_throttled"),
        "unit_cpu_stall_s": stall,
        "unit_memory_stall_s": _diff(before, after, "unit", "pressure", "memory", "some", "total", scale=1e6),
        "unit_io_stall_s": _diff(before, after, "unit", "pressure", "io", "some", "total", scale=1e6),
        "host_cpu_stall_s": _diff(before, after, "pressure", "cpu", "some", "total", scale=1e6),
        "oom": _diff(before, after, "unit", "memory_events", "oom"),
        "oom_kill": _diff(before, after, "unit", "memory_events", "oom_kill"),
        "high": _diff(before, after, "unit", "memory_events", "high"),
        "max": _diff(before, after, "unit", "memory_events", "max"),
        "memory_peak_mb": None if _get(after, "unit", "memory_peak") is None
        else round(after["unit"]["memory_peak"] / 1048576),
    })
    if stall is not None and seconds:
        out["cpu_stall_share"] = round(stall / seconds, 3)
    return out
