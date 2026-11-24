"""Driver telemetry probe stage 2 (#1116): the logic behind `tools/probe-stage2.py`.

Maintainer only, stdlib only. Three parts:

- `kit`: one setup sheet per platform from the event's invite link.
- `Monitor`: polls the relay's tailnet routes once a second (`/drivers/data`,
  `/drivers/race`, `/telemetry/probe`), prints one line per sender and appends a
  redacted record per poll to `poll.jsonl` in the results folder.
- `analyse` and `report_md`: the report tables from a results folder.

A record never carries a driver code, the probe key or the invite id; a connection
id is replaced by a short hash (`tag`) and a driver name by a pseudonym (`Pseudonyms`),
so the folder outside `private/` can be attached to an issue.
"""
import hashlib
import json
import math
import os
import time

POLL_S = 1.0
PROBE_EVERY_S = 60.0      # a full probe snapshot (600 samples per sender) this often
PRINT_EVERY_S = 5.0
POLL_FILE = "poll.jsonl"
MARK_FILE = "marks.jsonl"
PROBE_DIR = "probe"
PRIVATE_DIR = "private"
NAMES_FILE = "names.json"
PASS_HZ = 59.0            # stage 1's pass mark for 60 Hz, a proposal
JUMP_M = 300.0            # a distance step larger than a car drives in one poll
ANCHOR_TOLERANCE_M = 150.0
LATEST_KEYS = ("kind", "lap", "race_position", "car_count", "best_ms", "last_ms", "on_track",
               "paused", "loading", "speed_kmh", "fuel_l")
SENDER_KEYS = ("received", "posted", "dropped", "post_failed", "interruptions", "heartbeats",
               "post_ms_p50", "post_ms_p95", "forwarded")
PACKET_KEYS = ("received", "lost", "reordered", "decrypt_failed", "parse_failed")
RACE_KEYS = ("entry", "driver", "distance_m", "laps", "status", "in_box", "pit_stops",
             "gt7_position", "position_mismatch", "last_lap_ms", "finished")


def tag(conn):
    """A connection id as a short stable hash: enough to follow a sender, nothing to replay."""
    if not conn:
        return None
    return hashlib.sha256(str(conn).encode("utf-8")).hexdigest()[:8]


# -- kit ------------------------------------------------------------------------------

PLATFORMS = ("windows", "macos", "linux", "iphone", "android")

_TITLE = {"windows": "Windows PC or laptop", "macos": "Mac", "linux": "Linux PC or Raspberry Pi",
          "iphone": "iPhone or iPad (a-Shell)", "android": "Android phone or tablet (Termux)"}

_PREPARE = {
    "windows": [
        "Install Python 3.8 or newer from python.org (tick \"Add python.exe to PATH\").",
        "Open PowerShell in a folder of your choice.",
        "Download the sender: `curl.exe -o racecast-sender.py \"{script}\"`",
    ],
    "macos": [
        "Python 3.8 or newer: `python3 --version` in Terminal. If it is missing, install it "
        "from python.org.",
        "Open Terminal in a folder of your choice.",
        "Download the sender: `curl -o racecast-sender.py \"{script}\"`",
    ],
    "linux": [
        "Python 3.8 or newer: `python3 --version`.",
        "Download the sender: `curl -o racecast-sender.py \"{script}\"`",
        "With an active firewall, allow the console's packets: `sudo ufw allow 33740/udp`.",
    ],
    "iphone": [
        "Install a-Shell from the App Store and open it. Allow Local Network access when asked.",
        "Download the sender: `curl -o racecast-sender.py \"{script}\"`",
        "Find your PlayStation's IP address: Settings, Network, View Connection Status. "
        "The iPhone cannot find the console by itself, so the command needs it.",
        "Keep a-Shell in front with the screen on and the iPhone charging.",
    ],
    "android": [
        "Install Termux from F-Droid (the Play Store version is outdated) and open it.",
        "`pkg install python`, then `termux-wake-lock` (keeps Termux running with the "
        "screen off; `termux-wake-unlock` releases it).",
        "Download the sender: `curl -o racecast-sender.py \"{script}\"`",
        "Keep the device charging and on Wi-Fi. In the system settings set Termux's battery "
        "use to unrestricted (the name differs per vendor) and write down what you changed.",
        "Note your PlayStation's IP address (Settings, Network, View Connection Status) in "
        "case the sender does not find it.",
    ],
}

_PYTHON = {"windows": "py", "macos": "python3", "linux": "python3", "iphone": "python3",
           "android": "python"}

_STOP = {
    "windows": "Press Ctrl+C in the PowerShell window.",
    "macos": "Press Ctrl+C in Terminal.",
    "linux": "Press Ctrl+C.",
    "iphone": "Tap `ctrl` in the bar above the keyboard, then `c`. Closing a-Shell stops it too.",
    "android": "Tap `CTRL` in the extra keys row, then `c`. Then `termux-wake-unlock`.",
}


def kit(invite_url, script_url, platform):
    """The setup sheet for one platform, as Markdown. `invite_url` and `script_url` are
    the event's links: the sheet is for the invited drivers only."""
    if platform not in PLATFORMS:
        raise ValueError(f"platform is one of {', '.join(PLATFORMS)}")
    py = _PYTHON[platform]
    console = " --console <PlayStation IP>" if platform == "iphone" else ""
    cmd = f"{py} racecast-sender.py --url ... --code ...{console}"
    lines = [f"# Telemetry sender: {_TITLE[platform]}", "",
             "The sender runs on a device on the same network as your PlayStation and sends "
             "your car's GT7 telemetry to the broadcast. It sends nothing else.", "",
             "## Before the session", ""]
    steps = [s.format(script=script_url) for s in _PREPARE[platform]]
    steps += [f"Open the invite page {invite_url} , pick your entry (and stint), type your name "
              "and press \"Get my sender command\". Copy the line that starts with "
              f"`python3 racecast-sender.py`{' and use `py` instead of `python3`' if py == 'py' else ''}"
              f"{' and `python` instead of `python3`' if py == 'python' else ''}.",
              "Start a session in GT7 (any track; standing in the pits is fine) and run the "
              "self-check, the copied command with `--check` at the end:\n\n"
              f"  `{cmd} --check`\n\n"
              "It takes 12 seconds, sends no telemetry and ends with `RESULT: OK` or "
              "`RESULT: FAIL`. Send the lines from `checking for 12 s` to `RESULT` to the producer "
              "either way, and leave out the command line: it carries your code."]
    lines += [f"{n}. {s}" for n, s in enumerate(steps, 1)]
    lines += ["", "## For the session", "",
              f"Run the copied command without `--check`:\n\n  `{cmd}`\n",
              "Until Race Control approves you it prints `waiting for approval by Race "
              "Control` and asks again every 10 seconds. Leave it running for the whole "
              "session. Every 10 seconds it prints a status line.", "",
              "## Stop it", "", _STOP[platform], "",
              "## If the check fails", "",
              "- `receive: FAIL`: is GT7 in a session? Add `--console <PlayStation IP>`. Allow "
              "inbound UDP 33740 if a firewall asks or is on.",
              "- `relay: FAIL ... 403` or `410`: register again on the invite page and use the "
              "new command.",
              "- `cannot receive on UDP 33740`: another app on this device receives the "
              "telemetry (a dashboard app, SimHub). Run the sender on another device.",
              "", "The driver guide in the GT Racing Broadcast wiki (Driver Telemetry) covers "
              "SimHub and GT Race Engineer setups.", ""]
    return "\n".join(lines)


# -- monitor --------------------------------------------------------------------------

def compact(t, drivers, race, probe):
    """One redacted poll record from the three route answers (each may be None)."""
    rec = {"t": round(t, 3)}
    if isinstance(drivers, dict):
        rec["conns"] = [{
            "tag": tag(c.get("conn")), "entry": c.get("entry"), "segment": c.get("segment"),
            "name": c.get("name"), "live": c.get("live"), "idle_s": c.get("idle_s"),
            "packets": dict({k: (c.get("packets") or {}).get(k) for k in PACKET_KEYS},
                            duplicates=c.get("duplicates")),
            "sender": {k: v for k, v in (c.get("sender_stats") or {}).items() if k in SENDER_KEYS},
            "latest": {k: (c.get("latest") or {}).get(k) for k in LATEST_KEYS} if c.get("latest") else None,
        } for c in drivers.get("connections") or []]
    if isinstance(race, dict):
        rec["race"] = {
            "state": race.get("state"), "change_window": race.get("change_window"),
            "red_flag": bool(race.get("red_flag")), "lap": race.get("lap"),
            "fastest": {k: (race.get("fastest") or {}).get(k) for k in ("entry", "driver", "ms")}
            if race.get("fastest") else None,
            "entries": [dict({k: e.get(k) for k in RACE_KEYS}, tag=tag(e.get("conn")))
                        for e in race.get("entries") or []]}
    if isinstance(probe, dict) and probe.get("senders") is not None:
        rec["probe"] = {name: {
            "packets": dict({k: (s.get("packets") or {}).get(k) for k in PACKET_KEYS},
                            duplicates=s.get("duplicates")),
            "kinds": s.get("kinds"), "idle_s": s.get("idle_s"),
            "sender": {k: v for k, v in (s.get("sender_stats") or {}).items() if k in SENDER_KEYS},
            "latest": {k: (s.get("samples") or [{}])[-1].get(k) for k in LATEST_KEYS}
            if s.get("samples") else None,
        } for name, s in probe["senders"].items()}
    return rec


def redact_probe(probe):
    """A full probe snapshot without its key."""
    return {k: v for k, v in probe.items() if k != "key"}


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _rate(prev, cur, key, sub):
    """Per-second change of a counter between two poll entries of one sender."""
    if prev is None or cur is None:
        return None
    a, b = _num((prev[1].get(sub) or {}).get(key)), _num((cur[1].get(sub) or {}).get(key))
    dt = cur[0] - prev[0]
    if a is None or b is None or dt <= 0 or b < a:
        return None
    return (b - a) / dt


def live_lines(prev, rec):
    """The monitor's screen: one line per sender of `rec`, rates against `prev`."""
    old = {c["tag"]: (prev["t"], c) for c in (prev or {}).get("conns") or []}
    old.update({"probe:" + n: (prev["t"], s) for n, s in ((prev or {}).get("probe") or {}).items()})
    race = {e["tag"]: e for e in (rec.get("race") or {}).get("entries") or [] if e.get("tag")}
    rows = [(c["tag"], c["name"], c["entry"], c) for c in rec.get("conns") or []]
    rows += [("probe:" + n, n, "(probe)", s) for n, s in (rec.get("probe") or {}).items()]
    head = "%s  race %s%s" % (time.strftime("%H:%M:%S", time.localtime(rec["t"])),
                              (rec.get("race") or {}).get("state"),
                              "  CHANGE WINDOW" if (rec.get("race") or {}).get("change_window") else "")
    out = [head, "%-16s %-20s %6s %6s %5s %5s %4s %9s %5s %4s %8s %-8s" % (
        "name", "entry", "relay/s", "dev/s", "lost", "reord", "dup", "post p50/95", "0x84", "lap", "dist m", "status")]
    for key, name, entry, c in rows:
        now = (rec["t"], c)
        relay_hz, dev_hz = _rate(old.get(key), now, "received", "packets"), _rate(old.get(key), now, "received", "sender")
        sender, latest, pk = c.get("sender") or {}, c.get("latest") or {}, c.get("packets") or {}
        r = race.get(key, {})
        pos = latest.get("race_position")
        out.append("%-16.16s %-20.20s %6s %6s %5s %5s %4s %9s %5s %4s %8s %-8s" % (
            name or "?", entry or "", "-" if relay_hz is None else "%.1f" % relay_hz,
            "-" if dev_hz is None else "%.1f" % dev_hz, pk.get("lost", "-"), pk.get("reordered", "-"),
            "-" if pk.get("duplicates") is None else pk["duplicates"],
            "%s/%s" % (sender.get("post_ms_p50", "-"), sender.get("post_ms_p95", "-")),
            "-" if pos is None else "%s/%s" % (pos, latest.get("car_count")),
            latest.get("lap", "-") if latest else "-",
            "-" if r.get("distance_m") is None else "%.0f" % r["distance_m"], r.get("status", "")))
    return out


class Pseudonyms:
    """Driver names as "Driver N" in the shareable files; the names behind them live in
    `private/names.json`, read again by a restarted watch so the numbers hold."""

    def __init__(self, path):
        self.path = path
        try:
            with open(path, encoding="utf-8") as fh:
                self.real = {str(k): str(v) for k, v in json.load(fh).items()}
        except (OSError, ValueError, AttributeError):
            self.real = {}
        self.alias = {v: k for k, v in self.real.items()}

    def of(self, name):
        if not name:
            return name
        if name not in self.alias:
            alias = f"Driver {len(self.alias) + 1}"
            self.alias[name], self.real[alias] = alias, name
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self.real, fh, indent=1)
        return self.alias[name]

    def apply(self, rec):
        """`rec` with every driver name replaced; the record itself stays as it was."""
        out = json.loads(json.dumps(rec))
        for c in out.get("conns") or []:
            c["name"] = self.of(c.get("name"))
        race = out.get("race") or {}
        for e in race.get("entries") or []:
            e["driver"] = self.of(e.get("driver"))
        if race.get("fastest"):
            race["fastest"]["driver"] = self.of(race["fastest"].get("driver"))
        return out


class Monitor:
    """Polls the relay and writes the results folder. `fetch(path)` returns the decoded
    JSON of a GET or None; `clock` is wall time."""

    def __init__(self, out_dir, fetch, clock=time.time, say=print):
        self.out, self.fetch, self.clock, self.say = out_dir, fetch, clock, say
        os.makedirs(os.path.join(out_dir, PROBE_DIR), exist_ok=True)
        self.names = Pseudonyms(os.path.join(out_dir, PRIVATE_DIR, NAMES_FILE))
        self.prev = None
        self._probe_at = self._print_at = None
        self.polls = 0

    def poll(self):
        t = self.clock()
        probe = self.fetch("/telemetry/probe")
        rec = compact(t, self.fetch("/drivers/data"), self.fetch("/drivers/race"), probe)
        with open(os.path.join(self.out, POLL_FILE), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(self.names.apply(rec), separators=(",", ":")) + "\n")
        if isinstance(probe, dict) and probe.get("senders") and (
                self._probe_at is None or t - self._probe_at >= PROBE_EVERY_S):
            self._probe_at = t
            self.snapshot_probe(probe, time.strftime("%H%M%S", time.localtime(t)))
        if self._print_at is None or t - self._print_at >= PRINT_EVERY_S:
            self._print_at = t
            for line in live_lines(self.prev, rec):
                self.say(line)
        self.prev = rec
        self.polls += 1
        return rec

    def snapshot_probe(self, probe, name):
        path = os.path.join(self.out, PROBE_DIR, f"probe-{name}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(redact_probe(probe), fh)
        return path

    def run(self, stop, interval=POLL_S, sleep=time.sleep):
        while not stop():
            started = self.clock()
            self.poll()
            sleep(max(0.0, interval - (self.clock() - started)))


def mark(out_dir, text, clock=time.time):
    os.makedirs(out_dir, exist_ok=True)
    rec = {"t": round(clock(), 3), "text": str(text)}
    with open(os.path.join(out_dir, MARK_FILE), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    return rec


# -- analysis -------------------------------------------------------------------------

def _read_jsonl(path):
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue  # a line cut by a hard stop
    except OSError:
        return []
    return out


def load(out_dir):
    return _read_jsonl(os.path.join(out_dir, POLL_FILE)), _read_jsonl(os.path.join(out_dir, MARK_FILE))


def _block_at(marks, t):
    text = None
    for m in marks:
        if m["t"] <= t and is_block(m):
            text = m["text"]
    return text


def _senders(polls):
    """{key: {"name", "entry", "kind": "driver"|"probe", "series": [(t, entry_dict)]}}."""
    out = {}
    for rec in polls:
        for c in rec.get("conns") or []:
            s = out.setdefault(c["tag"], {"name": c.get("name"), "entry": c.get("entry"),
                                          "segment": c.get("segment"), "kind": "driver", "series": []})
            s["series"].append((rec["t"], c))
        for name, p in (rec.get("probe") or {}).items():
            s = out.setdefault("probe:" + name, {"name": name, "entry": None, "segment": None,
                                                 "kind": "probe", "series": []})
            s["series"].append((rec["t"], p))
    return out


def _active(series):
    """`series` up to its last poll in which a packet counter rose: the relay lists a
    connection that stopped sending for minutes, and that tail is no measurement."""
    end = 0
    for i in range(1, len(series)):
        a, b = series[i - 1][1], series[i][1]
        if any((_num((b.get(sub) or {}).get("received")) or 0) > (_num((a.get(sub) or {}).get("received")) or 0)
               for sub in ("packets", "sender")):
            end = i
    return series[:end + 1]


def _rise(series, sub, key):
    """How much a counter rose over `series`; a drop is a restarted sender, which counts
    from zero again. None when the counter never appears."""
    vals = [_num((c.get(sub) or {}).get(key)) for _t, c in series]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return sum(b - a if b >= a else b for a, b in zip(vals, vals[1:], strict=False))


def _sending(series, sub, key="received"):
    """(packets, seconds) over the poll intervals in which the counter rose: idle time
    before, between and after sending dilutes no rate. A drop is a restarted sender."""
    n = dt = 0.0
    seen = False
    for (t0, a), (t1, b) in zip(series, series[1:], strict=False):
        x, y = _num((a.get(sub) or {}).get(key)), _num((b.get(sub) or {}).get(key))
        if x is None or y is None:
            continue
        seen = True
        rise = y - x if y >= x else y
        if rise > 0:
            n, dt = n + rise, dt + (t1 - t0)
    return (n, dt) if seen else (None, 0.0)


def _hz(series, sub):
    n, dt = _sending(series, sub)
    return None if n is None or dt <= 0 else round(n / dt, 1)


def _spread(values):
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    return {"min": round(vals[0], 1), "p50": round(vals[len(vals) // 2], 1), "max": round(vals[-1], 1)}


def sender_summary(polls):
    rows = []
    for key, s in _senders(polls).items():
        active = _active(s["series"])
        first, last = active[0], active[-1]
        span = last[0] - first[0]

        def pk(k, active=active):
            return _rise(active, "packets", k)

        def sd(k, active=active):
            return _rise(active, "sender", k)
        rx, lost = pk("received"), pk("lost")
        _n, sent_s = _sending(active, "packets")
        rows.append({
            "key": key, "name": s["name"], "entry": s["entry"], "segment": s["segment"], "kind": s["kind"],
            "seconds": round(span, 1), "polls": len(active), "idle_s": round(span - sent_s, 1),
            "relay_hz": _hz(active, "packets"), "device_hz": _hz(active, "sender"),
            "lost": lost, "lost_pct": None if not rx or lost is None else round(100.0 * lost / (rx + lost), 3),
            "reordered": pk("reordered"), "duplicates": pk("duplicates"), "decrypt_failed": pk("decrypt_failed"),
            "interruptions": sd("interruptions"), "post_failed": sd("post_failed"), "dropped": sd("dropped"),
            "post_ms_p50": _spread(p[1].get("sender", {}).get("post_ms_p50") for p in active),
            "post_ms_p95": _spread(p[1].get("sender", {}).get("post_ms_p95") for p in active),
            "max_idle_s": max((_num(p[1].get("idle_s")) or 0.0 for p in active), default=None),
            "kinds": last[1].get("kinds")})
    return sorted(rows, key=lambda r: (r["kind"], r["entry"] or "", r["name"] or ""))


def hz_by_minute(polls, marks=()):
    """Per sender, one row per wall-clock minute since its first poll: device and relay Hz
    over the seconds it sent, and `idle_s`, the seconds of that minute it did not."""
    out = {}
    for key, s in _senders(polls).items():
        rows, series = [], _active(s["series"])
        if not series:
            continue
        t0 = series[0][0]
        buckets = {}
        for t, c in series:
            buckets.setdefault(int((t - t0) // 60), []).append((t, c))
        for minute in sorted(buckets):
            pts = buckets[minute]
            prev = [p for p in series if p[0] < pts[0][0]][-1:]
            span = prev + pts
            _n, sent_s = _sending(span, "packets")
            rows.append({"minute": minute, "block": _block_at(marks, pts[0][0]),
                         "device_hz": _hz(span, "sender"), "relay_hz": _hz(span, "packets"),
                         "idle_s": round(span[-1][0] - span[0][0] - sent_s, 1),
                         "interruptions": _rise(span, "sender", "interruptions")})
        out[key] = {"name": s["name"], "entry": s["entry"], "rows": rows}
    return out


def _rnd(v):
    return None if v is None else round(v, 1)


def positions(polls):
    """0x84 against the relay's order, per entry: how often GT7 named a position at all,
    and how often the order of those positions among the entries that carried one
    matched the relay's order of the same entries (cars without a sender sit between
    them in GT7's count). `relay_mismatch` counts the relay's own mismatch flag."""
    stats = {}
    for rec in polls:
        race = rec.get("race") or {}
        ranked = [e for e in race.get("entries") or [] if e.get("distance_m") is not None
                  and e.get("status") in ("racing", "box", "red_flag")]
        named = [e for e in ranked if e.get("gt7_position") is not None]
        by_gt7 = sorted(named, key=lambda e: e["gt7_position"])
        for e in ranked:
            st = stats.setdefault(e["entry"], {"entry": e["entry"], "polls": 0, "with_0x84": 0,
                                               "agree": 0, "relay_mismatch": 0, "values": set()})
            st["polls"] += 1
            if e.get("gt7_position") is not None:
                st["with_0x84"] += 1
                st["values"].add(e["gt7_position"])
                st["agree"] += named.index(e) == by_gt7.index(e)
            st["relay_mismatch"] += bool(e.get("position_mismatch"))
    rows = []
    for st in stats.values():
        rows.append(dict(st, values=sorted(st["values"]),
                         agree_pct=None if not st["with_0x84"] else round(100.0 * st["agree"] / st["with_0x84"], 1)))
    return sorted(rows, key=lambda r: r["entry"])


def car_counts(polls):
    """Per sender: the 0x84 car counts and positions its packets carried."""
    out = {}
    for key, s in _senders(polls).items():
        seen = {"positions": set(), "car_counts": set(), "samples": 0, "with_position": 0}
        for _t, c in s["series"]:
            latest = c.get("latest") or {}
            if not latest:
                continue
            seen["samples"] += 1
            if latest.get("race_position") is not None:
                seen["with_position"] += 1
                seen["positions"].add(latest["race_position"])
            if latest.get("car_count") is not None:
                seen["car_counts"].add(latest["car_count"])
        out[key] = {"name": s["name"], "samples": seen["samples"], "with_position": seen["with_position"],
                    "positions": sorted(seen["positions"]), "car_counts": sorted(seen["car_counts"])}
    return out


def changes(polls, marks=()):
    """The driver change timeline: change windows and every entry whose active
    connection changed, with its distance and laps right before and after. A change in
    the poll that shows the window closed counts inside it: the change closes it."""
    resets = sorted(m["t"] for m in marks if m.get("text") == RESET_MARK)
    events, last, window, prev_t = [], {}, False, None
    for rec in polls:
        if prev_t is not None and any(prev_t < r <= rec["t"] for r in resets):
            last = {}
        prev_t = rec["t"]
        race = rec.get("race") or {}
        now = bool(race.get("change_window"))
        if now and not window:
            events.append({"t": rec["t"], "kind": "window_open", "block": _block_at(marks, rec["t"])})
        for e in race.get("entries") or []:
            prev = last.get(e["entry"])
            if prev is not None and e.get("tag") and prev.get("tag") and e["tag"] != prev["tag"]:
                before, after = prev.get("distance_m"), e.get("distance_m")
                carried = None if before is None or after is None else \
                    -ANCHOR_TOLERANCE_M <= after - before <= ANCHOR_TOLERANCE_M
                events.append({"t": rec["t"], "kind": "change", "entry": e["entry"],
                               "block": _block_at(marks, rec["t"]),
                               "from": prev.get("driver"), "to": e.get("driver"),
                               "old_status": prev.get("status"), "old_in_box": prev.get("in_box"),
                               "distance_before": before, "distance_after": after,
                               "laps_before": prev.get("laps"), "laps_after": e.get("laps"),
                               "in_window": window or now, "anchor_carried": carried})
            last[e["entry"]] = e
        if window and not now:
            events.append({"t": rec["t"], "kind": "window_closed", "block": _block_at(marks, rec["t"])})
        window = now
    return events


RESET_MARK = "race reset"


def jumps(polls, marks=()):
    """Per entry: a lap count that stepped back and distance steps past JUMP_M, for one
    connection and never across a `race reset` mark, which starts the race over."""
    resets = [m["t"] for m in marks if m.get("text") == RESET_MARK]
    out, last = [], {}
    for rec in polls:
        for e in (rec.get("race") or {}).get("entries") or []:
            prev = last.get(e["entry"])
            last[e["entry"]] = (rec["t"], e)
            if prev is None or prev[1].get("tag") != e.get("tag"):
                continue
            (t0, p) = prev
            if any(t0 < r <= rec["t"] for r in resets):
                continue
            if _num(p.get("laps")) is not None and _num(e.get("laps")) is not None and e["laps"] < p["laps"]:
                out.append({"t": rec["t"], "entry": e["entry"], "kind": "laps_back",
                            "before": p["laps"], "after": e["laps"]})
            a, b = _num(p.get("distance_m")), _num(e.get("distance_m"))
            if a is not None and b is not None and (b - a > JUMP_M * max(1.0, rec["t"] - t0) or a - b > 50.0):
                out.append({"t": rec["t"], "entry": e["entry"], "kind": "distance_jump",
                            "before": a, "after": b, "status": e.get("status")})
    return out


def best_laps(polls, marks=()):
    """Per sender: every change of 0x78 best_ms with the lap and last_ms at that moment,
    and the best a new session starts with (the lap counter stepped back), changed or
    not: GT7 carrying a qualifying best into the race shows only there. `before_a_lap`
    marks a best that appeared before this stream's first lap time."""
    out = {}
    for key, s in _senders(polls).items():
        rows, best, lap = [], None, None
        for t, c in s["series"]:
            latest = c.get("latest") or {}
            if not latest:
                continue
            b = latest.get("best_ms")
            b = b if isinstance(b, int) and b > 0 else None
            lm = latest.get("last_ms")
            lm = lm if isinstance(lm, int) and lm > 0 else None
            now = _num(latest.get("lap"))
            new_session = lap is not None and now is not None and now < lap
            if b != best or new_session:
                rows.append({"t": t, "block": _block_at(marks, t), "lap": latest.get("lap"),
                             "event": "session start" if new_session else "best changed",
                             "best_ms": b, "last_ms": lm, "before_a_lap": b is not None and lm is None})
                best = b
            lap = now if now is not None else lap
        out[key] = {"name": s["name"], "rows": rows}
    return out


def is_block(mark):
    """A mark whose text starts with "block" opens a block; any other is an observation."""
    return str(mark.get("text", "")).lower().startswith("block")


def blocks(polls, marks=()):
    """The polls of each block, from its mark to the next block mark."""
    starts = [m for m in sorted(marks, key=lambda m: m["t"]) if is_block(m)]
    out = []
    for i, m in enumerate(starts):
        end = starts[i + 1]["t"] if i + 1 < len(starts) else math.inf
        part = [p for p in polls if m["t"] <= p["t"] < end]
        if part:
            out.append({"block": m["text"], "start": part[0]["t"], "end": part[-1]["t"], "polls": part})
    return out


def analyse(polls, marks=()):
    marks = sorted(marks, key=lambda m: m["t"])
    final = (polls[-1].get("race") if polls else None) or {}
    return {"start": polls[0]["t"] if polls else None, "end": polls[-1]["t"] if polls else None,
            "polls": len(polls), "marks": list(marks),
            "senders": sender_summary(polls), "hz": hz_by_minute(polls, marks),
            "positions": positions(polls), "car_counts": car_counts(polls),
            "changes": changes(polls, marks), "jumps": jumps(polls, marks), "best_laps": best_laps(polls, marks),
            "fastest": final.get("fastest"), "final_state": final.get("state"),
            "blocks": [{"block": b["block"], "start": b["start"], "end": b["end"], "polls": len(b["polls"]),
                        "senders": sender_summary(b["polls"]), "positions": positions(b["polls"]),
                        "car_counts": car_counts(b["polls"])} for b in blocks(polls, marks)]}


def _clock(t):
    return "-" if t is None else time.strftime("%H:%M:%S", time.localtime(t))


def _cell(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return "%.1f" % v if not math.isnan(v) else "-"
    if isinstance(v, dict):
        return "/".join(str(v[k]) for k in ("min", "p50", "max") if k in v) or json.dumps(v)
    if isinstance(v, (list, tuple, set)):
        return ", ".join(str(x) for x in v) or "-"
    return str(v).replace("|", "/")


def _table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return out + [""]


def _senders_table(rows):
    return _table(["name", "entry", "kind", "seconds", "device Hz", "relay Hz", "lost %", "reordered",
                   "duplicates", "decrypt failed", "interruptions", "post failed", "dropped", "post p50 ms",
                   "post p95 ms", "max idle s"],
                  [(r["name"], r["entry"], r["kind"], r["seconds"], r["device_hz"], r["relay_hz"], r["lost_pct"],
                    r["reordered"], r["duplicates"], r["decrypt_failed"], r["interruptions"], r["post_failed"],
                    r["dropped"], r["post_ms_p50"], r["post_ms_p95"], r["max_idle_s"]) for r in rows])


def _positions_tables(positions, car_counts):
    out = _table(["entry", "polls", "with 0x84", "agree", "agree %", "relay mismatch", "positions seen"],
                 [(r["entry"], r["polls"], r["with_0x84"], r["agree"], r["agree_pct"], r["relay_mismatch"],
                   r["values"])
                  for r in positions])
    return out + ["0x84 per sender:", ""] + _table(["name", "samples", "with position", "positions", "car counts"],
                        [(c["name"], c["samples"], c["with_position"], c["positions"], c["car_counts"])
                         for c in car_counts.values() if c["samples"]])


def report_md(a):
    """The report tables for the stage 2 report (see the runbook's report template)."""
    L = ["# Probe stage 2: measured tables", "",
         f"Session {_clock(a['start'])} to {_clock(a['end'])}, {a['polls']} polls. "
         f"Final race state: {a['final_state'] or '-'}.", ""]
    L += ["## Timeline (marks)", ""] + _table(["time", "mark"], [(_clock(m["t"]), m["text"]) for m in a["marks"]])
    L += ["## Senders", "",
          "Hz over the time each sender sent; `device` is what the device received, `relay` what "
          "reached the relay; `duplicates` are packets a sender posted twice. Post latency "
          "min/median/max of the sender's reported p50 and p95. The whole session first, then "
          "per block (a mark starting with \"block\").", ""]
    L += _senders_table(a["senders"])
    for b in a.get("blocks") or []:
        L += [f"### {b['block']}", "", f"{_clock(b['start'])} to {_clock(b['end'])}, {b['polls']} polls.", ""]
        L += _senders_table(b["senders"])
    L += ["## Hz per minute", "", "Rates over the seconds a sender sent. A minute with seconds "
          "without packets is marked `idle` and judged by its idle time, not its rate; a minute "
          f"without a gap below {PASS_HZ:.0f} Hz on the device is marked `low` (stage 1's pass "
          "mark, a proposal).", ""]
    for h in a["hz"].values():
        def mark(r):
            if r["idle_s"]:
                return "idle"
            return "low" if r["device_hz"] is not None and r["device_hz"] < PASS_HZ else ""
        clean = [r for r in h["rows"] if not r["idle_s"]]
        low = sum(1 for r in clean if mark(r) == "low")
        L += [f"#### {h['name']} ({h['entry'] or 'probe'}), {low} low minutes, "
              f"minutes without a gap: {len(clean)}", ""]
        L += _table(["minute", "block", "device Hz", "relay Hz", "idle s", "interruptions", ""],
                    [(r["minute"], r["block"], r["device_hz"], r["relay_hz"], r["idle_s"], r["interruptions"],
                      mark(r)) for r in h["rows"]])
    L += ["## 0x84 race position against the relay's order", "",
          "Per entry over the polls in which it was ranked: how often its packets named a "
          "position, how often the order of the named positions matched the relay's order of "
          "the same entries, and how often the relay itself marked a mismatch; then per sender "
          "the positions and car counts its packets carried. Per block when the session has "
          "block marks.", ""]
    for b in a.get("blocks") or [{"block": "", "positions": a["positions"], "car_counts": a["car_counts"]}]:
        if b["block"]:
            L += [f"### {b['block']}", ""]
        L += _positions_tables(b["positions"], b["car_counts"])
    L += ["## Driver changes", "",
          f"An anchor counts as carried when the distance after the change is within "
          f"{ANCHOR_TOLERANCE_M:.0f} m of the distance before it.", ""]
    L += _table(["time", "block", "event", "entry", "from", "to", "old status", "in window",
                 "distance before", "after", "laps before", "after", "anchor carried"],
                [(_clock(e["t"]), e.get("block"), e["kind"], e.get("entry"), e.get("from"), e.get("to"),
                  e.get("old_status"), e.get("in_window"), e.get("distance_before"), e.get("distance_after"),
                  e.get("laps_before"), e.get("laps_after"), e.get("anchor_carried")) for e in a["changes"]])
    L += ["## Lap count and distance", "", "A lap count that stepped back, or a distance step "
          f"larger than {JUMP_M:.0f} m per second forward or 50 m back, for one connection.", ""]
    L += _table(["time", "entry", "kind", "before", "after", "status"],
                [(_clock(j["t"]), j["entry"], j["kind"], j["before"], j["after"], j.get("status"))
                 for j in a["jumps"]]) if a["jumps"] else ["None.", ""]
    L += ["## Best lap (0x78)", "", "`before a lap` marks a best that appeared before the sender's "
          "first lap time in that stream: a best carried over from qualifying or an earlier lobby.", ""]
    for b in a["best_laps"].values():
        if not b["rows"]:
            continue
        L += [f"### {b['name']}", ""]
        L += _table(["time", "block", "event", "lap", "best ms", "last ms", "before a lap"],
                    [(_clock(r["t"]), r["block"], r["event"], r["lap"], r["best_ms"], r["last_ms"],
                      r["before_a_lap"]) for r in b["rows"]])
    fl = a.get("fastest") or {}
    L += ["## Fastest race lap", "", f"{fl.get('entry') or '-'}, {fl.get('driver') or '-'}, "
          f"{fl.get('ms') or '-'} ms", ""]
    return "\n".join(L)


def facts(a):
    """The numbers a rehearsal asserts on."""
    ch = [e for e in a["changes"] if e["kind"] == "change"]
    return {"polls": a["polls"], "drivers": sum(1 for s in a["senders"] if s["kind"] == "driver"),
            "probe_senders": sum(1 for s in a["senders"] if s["kind"] == "probe"),
            "changes": len(ch), "anchors_carried": sum(1 for e in ch if e["anchor_carried"]),
            "changes_in_window": sum(1 for e in ch if e["in_window"]),
            "windows": sum(1 for e in a["changes"] if e["kind"] == "window_open"),
            "with_0x84": sum(r["with_0x84"] for r in a["positions"]),
            "laps_back": sum(1 for j in a["jumps"] if j["kind"] == "laps_back"),
            "marks": len(a["marks"]), "blocks": len(a.get("blocks") or []),
            "session_starts": sum(1 for b in a["best_laps"].values() for r in b["rows"]
                                  if r["event"] == "session start")}
