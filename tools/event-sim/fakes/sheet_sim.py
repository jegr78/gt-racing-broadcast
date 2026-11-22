#!/usr/bin/env python3
"""Simulated Google Sheet plus its Apps Script webhook, for the event simulation.

One loopback port serves the tabs the way gviz exports them and runs the v7 script of
the Sheet-Webhook wiki page against them, so a write is visible on the next read:

    GET  /spreadsheets/d/sim/gviz/tq?tqx=out:csv[&headers=1]&sheet=<Tab>
    POST /macros/s/sim/exec?key=<key>     the script's doPost, always HTTP 200
    GET  /_sim/state                      tab names, write and key-read counters
    GET  /_sim/tab?name=<Tab>             rows and header-keyed records of one tab
    GET  /_sim/writes?since=N             script calls after sequence N
    POST /_sim/tab                        {"name", "rows" | "csv"}: seed or replace a tab

`SheetSim.env()` gives RACECAST_SIM_SHEET_URL and RACECAST_SIM_SHEET_PUSH_URL.

    python3 tools/event-sim/fakes/sheet_sim.py --port 0 --tabs DIR --ready-file sheet.json

Maintainer tool, not shipped."""
import argparse
import csv
import datetime
import io
import json
import math
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import loopback_http  # noqa: E402

SHEET_URL_ENV = "RACECAST_SIM_SHEET_URL"            # simulation.SHEET_URL
SHEET_PUSH_URL_ENV = "RACECAST_SIM_SHEET_PUSH_URL"  # simulation.SHEET_PUSH_URL
SHEET_PATH = "/spreadsheets/d/sim"
EXEC_PATH = "/macros/s/sim/exec"
DEFAULT_KEY = "sim"
SCRIPT_VERSION = 7
SETUP_FIELDS = ("Stint", "Streamer", "Session", "Race Control", "Flag")
TIMER_ROWS = {"Race End (UTC)": 1, "Duration": 2, "Visible": 3, "Updated (UTC)": 4,
              "Remaining": 5}
CREW_HEADERS = ["Name", "Commentator", "Director", "Producer", "Race Control", "Discord"]
# Overlay label (column B, as the relay reads it) -> the Setup header its formula mirrors.
OVERLAY_MIRROR = {"Stint": "Stint", "Streamer": "Streamer", "Session": "Session",
                  "Race Control": "Race Control", "Flag": "Flag",
                  "Teams P1": "Team 1", "Teams P2": "Team 2", "Teams P3": "Team 3"}
OVERLAY = "Overlay"
SETUP = "Setup"


class ScriptError(Exception):
    """A value the script throws; doPost answers {"error": String(err)}."""


def js_str(value):
    """String(value) as JavaScript writes it."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)


def js_truthy(value):
    return not (value is None or value is False or value == "" or
                (isinstance(value, (int, float)) and not isinstance(value, bool) and
                 (value == 0 or math.isnan(value))))


def js_number(value):
    """Number(value): NaN for what JavaScript cannot convert."""
    if isinstance(value, bool):
        return float(value)
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0.0
        try:
            return float(text)
        except ValueError:
            return math.nan
    return math.nan


def js_parse_int(value):
    """parseInt(value, 10), or None for NaN."""
    text = js_str(value).strip()
    digits = ""
    for i, ch in enumerate(text):
        if ch.isdigit() or (i == 0 and ch in "+-"):
            digits += ch
        else:
            break
    try:
        return int(digits)
    except ValueError:
        return None


def cell(value):
    """What Sheets shows after setValue: booleans as TRUE/FALSE, null as empty."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return js_str(value)


def parse_csv(text):
    return [list(r) for r in csv.reader(io.StringIO(text))]


def _trimmed(rows):
    """Rows up to the last non-empty one, each padded to the widest non-empty column."""
    rows = [[str(c) for c in r] for r in rows]
    while rows and not any(c.strip() for c in rows[-1]):
        rows.pop()
    width = max((i + 1 for r in rows for i, c in enumerate(r) if c.strip()), default=0)
    return [(r + [""] * width)[:width] for r in rows]


class Sheet:
    """The tabs, the script's Script Properties (`stream_keys`) and the write log.
    `tabs` maps a tab name to rows (lists of strings) or CSV text; None drops it."""

    def __init__(self, tabs=None, stream_keys=None, clock=time.time, key=DEFAULT_KEY):
        self.lock = threading.RLock()
        self.clock = clock
        self.key = key
        self.stream_keys = dict(stream_keys or {})
        self._tabs = {}
        self._writes = []
        self.write_seq = 0
        self.key_reads = 0
        for name, rows in (tabs or {}).items():
            self.set_tab(name, rows)

    def set_tab(self, name, rows):
        with self.lock:
            if rows is None:
                self._tabs.pop(name, None)
            else:
                self._tabs[name] = [list(map(str, r)) for r in
                                    (parse_csv(rows) if isinstance(rows, str) else rows)]

    def tab_names(self):
        with self.lock:
            names = set(self._tabs)
            if SETUP in names:
                names.add(OVERLAY)
            return sorted(names)

    def tab(self, name):
        """The rows a read sees (Overlay with its Setup mirror applied), or None."""
        with self.lock:
            if name == OVERLAY:
                return self._overlay()
            rows = self._tabs.get(name)
            return None if rows is None else [list(r) for r in rows]

    def records(self, name):
        """Header-keyed dicts of every row below row 1: records(tab)[i] is physical row i+2."""
        rows = self.tab(name) or []
        if not rows:
            return []
        header = rows[0]
        return [{h: (r[i] if i < len(r) else "") for i, h in enumerate(header) if h}
                for r in rows[1:]]

    def csv(self, name):
        """The gviz CSV export of a tab: every cell quoted, "" for an unknown tab."""
        rows = _trimmed(self.tab(name) or [])
        out = io.StringIO()
        csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator="\n").writerows(rows)
        return out.getvalue()

    def writes(self, since=0):
        with self.lock:
            return [json.loads(json.dumps(w)) for w in self._writes if w["seq"] > since]

    def snapshot(self):
        with self.lock:
            return {"tabs": self.tab_names(), "write_seq": self.write_seq,
                    "key_reads": self.key_reads}

    def _overlay(self):
        setup = self._tabs.get(SETUP)
        rows = self._tabs.get(OVERLAY)
        if setup is None:
            return None if rows is None else [list(r) for r in rows]
        if rows is None:
            rows = [["", label, ""] for label in OVERLAY_MIRROR]
        out = []
        for r in rows:
            label = r[1].strip() if len(r) > 1 else ""
            source = next((h for lab, h in OVERLAY_MIRROR.items() if lab.lower() == label.lower()), None)
            hit = self._below(setup, source) if source else None
            out.append(list(r) if hit is None else
                       ["", r[1], setup[hit[0]][hit[1]] if hit[0] < len(setup) and
                        hit[1] < len(setup[hit[0]]) else ""])
        return out

    @staticmethod
    def _below(grid, header):
        """(row, col) of the cell below the first cell reading `header`, or None."""
        for r, row in enumerate(grid):
            for c, value in enumerate(row):
                if value.strip().lower() == header.lower():
                    return r + 1, c
        return None

    # -- the Apps Script ------------------------------------------------------
    def do_post(self, key, body):
        """The v7 doPost for one request: its JSON answer as a dict."""
        with self.lock:
            if key != self.key:
                return self._log(None, {"error": "bad key"})
            try:
                p = json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                return self._log(None, {"error": f"SyntaxError: {exc}"})
            if not isinstance(p, dict):
                return self._log(p, {"error": "TypeError: the payload is not an object"})
            action = p.get("action") if js_truthy(p.get("action")) else "timer"
            if action == "get_stream_key":
                self.key_reads += 1
                ref = js_str(p.get("ref") or "")
                found = self.stream_keys.get(ref)
                return ({"ok": True, "action": action, "key": found} if found else
                        {"ok": False, "action": action, "error": f"no key for ref '{ref}'"})
            handler = {"timer": self._write_timer, "setup": self._write_setup,
                       "schedule": self._write_schedule, "pov": self._write_pov,
                       "teams": self._write_teams, "crew": self._write_crew}.get(action)
            if handler is None:
                return self._log(p, {"error": f"unknown action: {js_str(action)}"})
            try:
                handler(p)
            except ScriptError as exc:
                return self._log(p, {"error": str(exc)})
            return self._log(p, {"ok": True, "action": action, "v": SCRIPT_VERSION})

    def _log(self, payload, response):
        self.write_seq += 1
        action = payload.get("action") if isinstance(payload, dict) else None
        self._writes.append({"seq": self.write_seq, "time": self.clock(),
                             "action": action if js_truthy(action) else
                             ("timer" if isinstance(payload, dict) else None),
                             "payload": payload, "response": response,
                             "ok": response.get("ok") is True})
        return response

    def _sheet(self, name):
        rows = self._tabs.get(name)
        if rows is None:
            raise ScriptError(f"tab not found: {name}")
        return rows

    @staticmethod
    def _set(rows, row, col, value):
        """setValue on 1-based (row, col), growing the grid as Sheets does."""
        while len(rows) < row:
            rows.append([])
        line = rows[row - 1]
        while len(line) < col:
            line.append("")
        line[col - 1] = cell(value)

    @staticmethod
    def _last_row(rows):
        return max((i + 1 for i, r in enumerate(rows) if any(c != "" for c in r)), default=0)

    @staticmethod
    def _last_col(rows):
        return max((i + 1 for r in rows for i, c in enumerate(r) if c != ""), default=0)

    @staticmethod
    def _col_of(header, name):
        for c, value in enumerate(header):
            if value.strip().lower() == name:
                return c + 1
        return 0

    def _write_timer(self, p):
        rows = self._tabs.setdefault("Timer", [])

        def write(label, value):
            self._set(rows, TIMER_ROWS[label], 1, label)
            self._set(rows, TIMER_ROWS[label], 2, value)
        for field, label in (("end", "Race End (UTC)"), ("duration", "Duration"),
                             ("visible", "Visible"), ("remaining", "Remaining")):
            if field in p:
                write(label, p[field])
        now = datetime.datetime.fromtimestamp(self.clock(), datetime.timezone.utc)
        write("Updated (UTC)", now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z")

    def _write_setup(self, p):
        rows = self._sheet(SETUP)
        fields = p.get("fields") if isinstance(p.get("fields"), dict) else {}
        targets = {}
        for name in fields:
            if name not in SETUP_FIELDS:
                raise ScriptError(f"unknown setup field: {name}")
            hit = self._below(rows, name)
            if hit is None:
                raise ScriptError(f"header not found in Setup tab: {name}")
            targets[name] = hit
        for name, (r, c) in targets.items():
            self._set(rows, r + 1, c + 1, fields[name])

    def _write_schedule(self, p):
        rows = self._sheet("Qualifying" if p.get("tab") == "Qualifying" else "Schedule")
        row = js_number(p.get("row"))
        if math.isnan(row) or not row or row < 1 or row > self._last_row(rows) + 1 or \
                not row.is_integer():
            raise ScriptError(f"row out of range: {js_str(p.get('row')) if 'row' in p else 'undefined'}")
        row = int(row)
        header = rows[0][:self._last_col(rows)] if rows else []
        url_col = self._col_of(header, "url") or 1
        name_col = self._col_of(header, "streamer") or 2
        stint_col = self._col_of(header, "stint")
        if "url" in p:
            self._set(rows, row, url_col, p["url"])
        if "name" in p:
            self._set(rows, row, name_col, p["name"])
        if "stint" in p and stint_col:
            self._set(rows, row, stint_col, p["stint"])

    def _write_pov(self, p):
        rows = self._sheet("POV")
        header = rows[0] if rows else []
        if "url" in p:
            self._set(rows, 2, self._col_of(header, "url") or 1, p["url"] if js_truthy(p["url"]) else "")
        if "name" in p:
            self._set(rows, 2, self._col_of(header, "name") or 2, p["name"] if js_truthy(p["name"]) else "")

    def _write_teams(self, p):
        slot = js_number(p.get("slot"))
        if not (slot >= 1 and slot <= 3):
            raise ScriptError(f"slot out of range: {js_str(p.get('slot')) if 'slot' in p else 'undefined'}")
        rows = self._sheet(SETUP)
        hit = self._below(rows, f"team {js_str(slot)}")
        if hit is None:
            raise ScriptError(f"header not found in Setup tab: Team {js_str(slot)}")
        self._set(rows, hit[0] + 1, hit[1] + 1, p.get("name") if js_truthy(p.get("name")) else "")

    def _write_crew(self, p):
        rows = self._tabs.setdefault("Crew", [])
        if self._last_row(rows) < 1:
            rows[:] = [list(CREW_HEADERS)]
        row = js_parse_int(p.get("row")) if "row" in p else None
        if row is None or row < 1:
            raise ScriptError("crew: row must be >= 1")
        target = row + 1
        if p.get("delete") is True:
            if target <= self._last_row(rows):
                del rows[target - 1]
            return
        name = js_str(p.get("name") if js_truthy(p.get("name")) else "").strip()
        if not name:
            raise ScriptError("crew: name is required")
        header = rows[0][:max(1, self._last_col(rows[:1]))]

        def col_of(label):
            found = self._col_of(header, label.lower())
            if found:
                return found
            header.append(label)
            self._set(rows, 1, len(header), label)
            return len(header)
        self._set(rows, target, col_of("Name"), name)
        for label, field in (("Commentator", "commentator"), ("Director", "director"),
                             ("Producer", "producer"), ("Race Control", "race_control")):
            self._set(rows, target, col_of(label), js_truthy(p.get(field)))
        if "race_engineer" in p:
            self._set(rows, target, col_of("Race Engineer"), js_truthy(p["race_engineer"]))
        self._set(rows, target, col_of("Discord"),
                  js_str(p.get("discord") if js_truthy(p.get("discord")) else "").strip())


class SheetSim(loopback_http.LoopbackServer):
    """A running simulated Sheet. `tabs` and `stream_keys` seed `self.sheet`; `key` is
    the script's KEY, carried in the push URL."""

    def __init__(self, tabs=None, stream_keys=None, key=DEFAULT_KEY, port=0, clock=time.time):
        super().__init__(port)
        self.sheet = Sheet(tabs, stream_keys=stream_keys, clock=clock, key=key)
        self.set_tab, self.tab, self.records = self.sheet.set_tab, self.sheet.tab, self.sheet.records
        self.writes = self.sheet.writes

    @property
    def url(self):
        return self.http_url + SHEET_PATH

    @property
    def push_url(self):
        return f"{self.http_url}{EXEC_PATH}?key={self.sheet.key}"

    def env(self):
        """The simulation-mode environment that points racecast at this Sheet."""
        return {SHEET_URL_ENV: self.url, SHEET_PUSH_URL_ENV: self.push_url}

    def route(self, method, path, query, headers, body):
        reply = loopback_http.json_reply
        first = {k: v[0] for k, v in query.items()}
        if method == "GET" and path == SHEET_PATH + "/gviz/tq":
            if first.get("tqx") != "out:csv" or "sheet" not in first:
                return reply({"error": "only tqx=out:csv with a sheet name is served"}, 400)
            return 200, "text/csv; charset=utf-8", self.sheet.csv(first["sheet"]).encode("utf-8")
        if method == "POST" and path == EXEC_PATH:
            return reply(self.sheet.do_post(first.get("key", ""), body))
        if method == "GET" and path == "/_sim/state":
            return reply(self.sheet.snapshot())
        if method == "GET" and path == "/_sim/writes":
            since = int(first.get("since") or 0)
            return reply({"writes": self.sheet.writes(since), "seq": self.sheet.write_seq})
        if method == "GET" and path == "/_sim/tab":
            name = first.get("name", "")
            return reply({"name": name, "rows": self.sheet.tab(name),
                          "records": self.sheet.records(name)})
        if method == "POST" and path == "/_sim/tab":
            data = json.loads(body or b"{}")
            if not isinstance(data, dict) or not isinstance(data.get("name"), str):
                return reply({"error": "name must be a string"}, 400)
            rows = data.get("csv") if isinstance(data.get("csv"), str) else data.get("rows")
            if rows is not None and not isinstance(rows, str) and not (
                    isinstance(rows, list) and all(isinstance(r, list) for r in rows)):
                return reply({"error": "rows must be a list of lists"}, 400)
            self.sheet.set_tab(data["name"], rows)
            return reply({"name": data["name"], "rows": self.sheet.tab(data["name"])})
        return reply({"error": "not found"}, 404)


def load_tabs(directory):
    """{tab: CSV text} from every <Tab>.csv in `directory`."""
    tabs = {}
    for entry in sorted(os.listdir(directory)):
        if entry.lower().endswith(".csv"):
            with open(os.path.join(directory, entry), encoding="utf-8", newline="") as fh:
                tabs[entry[:-4]] = fh.read()
    return tabs


def build_parser():
    ap = argparse.ArgumentParser(description="Simulated Google Sheet plus its Apps Script webhook.")
    ap.add_argument("--port", type=int, default=0, help="listen port, 0 picks a free one (default)")
    ap.add_argument("--tabs", help="directory of <Tab>.csv files to seed the tabs")
    ap.add_argument("--key", default=DEFAULT_KEY, help="the script's KEY in the push URL (default sim)")
    ap.add_argument("--ready-file", help="write {port, url, push_url, http_url, env} here once listening")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    sim = SheetSim(load_tabs(args.tabs) if args.tabs else None, key=args.key, port=args.port).start()
    if args.ready_file:
        loopback_http.write_ready_file(args.ready_file, {
            "port": sim.port, "url": sim.url, "push_url": sim.push_url,
            "http_url": sim.http_url, "env": sim.env()})
    print(f"sheet-sim: tabs {', '.join(sim.sheet.tab_names()) or '(none)'} on {sim.url}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass  # Ctrl+C ends the run
    finally:
        sim.stop()


fetch = loopback_http.fetch

if __name__ == "__main__":
    main()
